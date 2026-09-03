"""Negotiate an EIS connection by driving ``org.freedesktop.portal.RemoteDesktop``
directly over D-Bus, rather than through :mod:`libei.oeffis`.

:mod:`libei.oeffis` wraps liboeffis, whose C API
(``oeffis_create_session()``) takes only a device-type bitmask -- it exposes
neither ``persist_mode`` nor the ``restore_token`` a caller needs to avoid
re-prompting the user on every run. Upstream's own documentation is explicit
about why: liboeffis is "intentionally kept simple, any more complex needs
should be handled by an application talking to DBus directly"
(https://libinput.pages.freedesktop.org/libei/api/group__liboeffis.html).
This module is that: the ``CreateSession`` -> ``SelectDevices`` -> ``Start``
-> ``ConnectToEIS`` sequence driven directly, with ``persist_mode`` and
``restore_token`` exposed as real parameters.

    with RemoteDesktopSession.negotiate(
        devices=DeviceType.POINTER | DeviceType.KEYBOARD,
        persist_mode=PersistMode.UNTIL_REVOKED,
        restore_token=saved_token,  # None on the first run
    ) as session:
        save_somewhere(session.restore_token)  # for next time
        sender = ei.Sender.create_for_fd(session.eis_fd, name="my-app")
        ...  # inject input for as long as the session is needed

Three things worth knowing before building on this:

* **Blocking, not event-driven.** Unlike :class:`libei.oeffis.Oeffis`
  (poll ``fd``, call ``dispatch()`` until it returns ``True``),
  :meth:`RemoteDesktopSession.negotiate` runs its own nested
  ``GLib.MainLoop`` per D-Bus round trip and returns only once the whole
  sequence has resolved, or raises. liboeffis is itself event-driven, which
  is why ``Oeffis`` is; a caller driving GDBus directly already has
  ``GLib.MainLoop`` available to it, and there is no equivalent requirement
  here to expose an async surface -- so this doesn't. Each round trip is
  bounded by ``timeout`` (:class:`PortalTimeoutError` when it expires),
  since a blocking call with no escape hatch is the one thing ``Oeffis``'s
  pollable fd would otherwise buy you.
* **Close it when done.** The portal session outlives this object unless
  ``Session.Close()`` is called, and the EIS fd is owned by whoever
  received it. :meth:`RemoteDesktopSession.close` (and the context-manager
  form above) does both; see that method for what it does and does not
  clean up.
* **Least automatically verified path in this package**, same caveat
  :mod:`libei.oeffis` carries: nothing in CI can click through a real
  consent dialog, so ``tests/test_portal.py`` exercises the
  request/response orchestration against a fake D-Bus connection only.

  Verified by hand 2026-09-01 against a real GNOME Wayland session
  (xdg-desktop-portal, ``RemoteDesktop`` v2), end to end: a first run
  raised the consent dialog and was approved with "Remember" checked
  (5.4s), and a second run replaying the ``restore_token`` was granted
  with no dialog at all (0.2s) -- which is the whole point of
  ``persist_mode``, and is also what proves the first run was a genuine
  first-time authorisation rather than a pre-existing grant. Three devices
  resumed on the returned fd: relative pointer, keyboard, then absolute
  pointer -- in that order, which is exactly the device race ``ei``-side
  callers have to handle. ``Session.Close()`` was exercised too. No input
  was injected: emulation is ``libei.ei``'s job and is not what this
  module does.
"""

from __future__ import annotations

import enum
import logging
import os
import time
import uuid
from typing import Any

from .oeffis import DeviceType

logger = logging.getLogger("libei.portal")

__all__ = [
    "DeviceType",
    "PersistMode",
    "PortalError",
    "PortalVersionError",
    "PortalDeniedError",
    "PortalTimeoutError",
    "RemoteDesktopSession",
    "is_available",
]

_BUS_NAME = "org.freedesktop.portal.Desktop"
_OBJECT_PATH = "/org/freedesktop/portal/desktop"
_REMOTE_DESKTOP = "org.freedesktop.portal.RemoteDesktop"
_REQUEST_INTERFACE = "org.freedesktop.portal.Request"
_SESSION_INTERFACE = "org.freedesktop.portal.Session"

_MIN_REMOTE_DESKTOP_VERSION = 2  # ConnectToEIS needs v2+

_DEFAULT_TIMEOUT = 60.0
"""Seconds to wait for one portal round trip. Generous, because a human has
to see and answer the consent dialog `Start` raises -- but bounded, because
the alternative is a caller wedged forever if the portal dies after
accepting the call and before sending its `Response`."""

_GIO_DEFAULT_TIMEOUT_MSEC = 25_000
"""What GDBus's ``-1`` reply timeout actually is.

``call_sync(..., -1, ...)`` does not mean "wait forever" -- that is
``G_MAXINT`` -- it means GIO's own default, which is 25 seconds. Only used
to report the right number when a call left on that default runs out of
time; see `_call_sync`."""

_G_IO_ERROR_DOMAIN = "g-io-error-quark"
"""The GLib error domain GDBus reports its own reply timeout in.

Not one of the ``org.freedesktop.DBus.Error.*`` codes, as one might expect:
a ``call_sync()`` whose reply never arrives raises ``G_IO_ERROR_TIMED_OUT``
in this domain with the message "Timeout was reached" (checked against a
service that accepts a call and then deliberately never answers). See
`_is_reply_timeout`."""

_ALL_DEVICE_TYPES = DeviceType.KEYBOARD | DeviceType.POINTER | DeviceType.TOUCHSCREEN
"""Every bit the RemoteDesktop `types` bitmask defines.

`DeviceType.ALL_DEVICES` is liboeffis's own sentinel and is literally 0,
which the portal reads as *no* device types rather than all of them -- a
session that negotiates fine and then never resumes a single device. The
sentinel is translated to this before it reaches `SelectDevices`."""


class PersistMode(enum.IntEnum):
    """``SelectDevices``'s ``persist_mode`` option, per the RemoteDesktop XML."""

    NONE = 0
    WHILE_RUNNING = 1
    UNTIL_REVOKED = 2


class PortalError(Exception):
    """Base class for this module's failures."""


class PortalVersionError(PortalError):
    """The compositor's RemoteDesktop portal is too old for ConnectToEIS."""


class PortalTimeoutError(PortalError):
    """A portal request did not answer within the timeout.

    Distinct from a decline: the portal accepted the call and then never
    sent its ``Response`` signal. Most often the consent dialog is simply
    still waiting for a human, so raise the timeout rather than treating
    this as a failure if that is expected.
    """

    def __init__(self, step: str, timeout: float) -> None:
        super().__init__(f"{step} did not answer within {timeout:g}s")
        self.step = step
        self.timeout = timeout


class PortalDeniedError(PortalError):
    """``CreateSession``, ``SelectDevices`` or ``Start`` was not approved.

    Covers both an explicit user decline and any other non-zero portal
    response code -- the portal spec does not guarantee a code means
    "the user said no" versus some other failure, so this does not either.
    """

    def __init__(self, step: str, message: str | None = None) -> None:
        super().__init__(message or f"{step} was not approved")
        self.step = step
        self.message = message


def _gio() -> tuple[Any, Any] | None:
    """Import Gio and GLib, or return None.

    Deferred so importing this module never requires PyGObject -- the same
    "zero hard dependencies, probed at runtime" rule the rest of this
    package follows. See is_available().
    """
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except Exception:
        return None
    return Gio, GLib


def is_available() -> bool:
    """Whether PyGObject (Gio) can be imported on this system.

    Does not check for a running session bus or a portal implementation --
    only whether the Python side this module needs is installed. A missing
    session bus or portal surfaces as a `PortalError` from `negotiate()`.
    """
    return _gio() is not None


def _glib_error(GLib: Any) -> Any:
    """``GLib.Error``, or a tuple that catches nothing where it is absent.

    Every GDBus failure -- no session bus, no portal implementation behind
    the name, a method that returns a D-Bus error -- arrives as this one
    exception type. It is looked up rather than imported so that a test
    double standing in for ``GLib`` need not define it: `except ()` catches
    nothing, which is the right behaviour when there is no real GLib whose
    errors could be raised in the first place.
    """
    return getattr(GLib, "Error", ())


def _is_reply_timeout(Gio: Any, exc: Exception) -> bool:
    """Whether a GDBus failure is its own reply timeout expiring.

    Worth telling apart from every other ``GLib.Error``, because it means
    exactly what the nested loop's own timeout means -- nobody answered in
    the time allowed -- and so should raise the `PortalTimeoutError` a
    caller is documented to catch for that, not a generic `PortalError`.

    Defensive throughout: a test double standing in for ``Gio`` need not
    define ``IOErrorEnum``, and one standing in for ``GLib.Error`` need not
    carry a domain or a code. An attribute that isn't there just means
    "not a timeout", which is the safe answer -- the failure still reaches
    the caller, only as the more general error.
    """
    timed_out = getattr(getattr(Gio, "IOErrorEnum", None), "TIMED_OUT", None)
    if timed_out is None:
        return False
    return (
        getattr(exc, "domain", None) == _G_IO_ERROR_DOMAIN
        and getattr(exc, "code", None) == timed_out
    )


def _msec_until(deadline: float) -> int:
    """Milliseconds left before ``deadline``, never negative.

    Both legs of a round trip -- the call that returns the request handle,
    then the wait for its ``Response`` signal -- draw down the same
    deadline, so ``timeout`` bounds the round trip rather than each half of
    it separately, which would let a slow first leg quietly double the wait
    a caller asked for. Zero is a legitimate answer: GLib fires a 0ms
    timeout on the next main-loop iteration, which is the right thing for a
    deadline that has already passed.
    """
    return max(0, int((deadline - time.monotonic()) * 1000))


def _call_sync(
    connection: Any,
    Gio: Any,
    GLib: Any,
    busname: str,
    object_path: str,
    interface: str,
    method: str,
    parameters: Any,
    reply_type: Any,
    timeout_msec: int = -1,
) -> Any:
    """``call_sync``, with GDBus failures translated to `PortalError`.

    Without this a `GLib.Error` propagates raw, so the no-session-bus and
    no-portal-backend cases -- exactly the ones `is_available()` documents
    as surfacing here, since it deliberately checks neither -- escape a
    caller's `except PortalError`.

    ``timeout_msec`` is GDBus's own reply timeout, and defaults to its
    ``-1`` -- GIO's 25 seconds (see `_GIO_DEFAULT_TIMEOUT_MSEC`), which is
    what `RemoteDesktopSession.close` leaves it at, having no caller-facing
    timeout to honour. Anything reached from `negotiate` passes the
    caller's own ``timeout`` instead, so that the bound on a round trip is
    the documented one rather than a number this module never chose.
    """
    try:
        return connection.call_sync(
            busname,
            object_path,
            interface,
            method,
            parameters,
            reply_type,
            Gio.DBusCallFlags.NONE,
            timeout_msec,
            None,
        )
    except _glib_error(GLib) as exc:
        if _is_reply_timeout(Gio, exc):
            expired = timeout_msec if timeout_msec >= 0 else _GIO_DEFAULT_TIMEOUT_MSEC
            raise PortalTimeoutError(method, expired / 1000) from exc
        raise PortalError(f"{method} failed on the D-Bus: {exc}") from exc


def _close_session(
    connection: Any,
    Gio: Any,
    GLib: Any,
    busname: str,
    session_handle: str,
) -> None:
    """Call ``Session.Close()``, logging rather than raising on failure.

    Shared by `RemoteDesktopSession.close` and `negotiate`'s failure path,
    which need the same thing of it: the session may already be gone (the
    portal restarted, the user revoked the grant), and neither a caller
    tidying up nor an exception already unwinding is helped by a second
    failure thrown over the top of the first.
    """
    try:
        _call_sync(
            connection,
            Gio,
            GLib,
            busname,
            session_handle,
            _SESSION_INTERFACE,
            "Close",
            None,
            None,
        )
    except PortalError as exc:
        logger.debug("closing the portal session failed: %s", exc)


def _returned_handle(reply: Any) -> str | None:
    """The request object path a Request-returning call replied with.

    Every such portal method answers ``(o)``, but this stays defensive and
    returns ``None`` on anything else: the value is only ever used as a
    *second* path to listen on alongside the one derived from our own
    handle_token, so a reply shaped unexpectedly is a reason to fall back to
    that derived path, never to fail the negotiation outright.
    """
    try:
        unpacked = reply.unpack()
    except Exception:
        return None
    if isinstance(unpacked, tuple) and len(unpacked) == 1:
        handle = unpacked[0]
        if isinstance(handle, str):
            return handle
    return None


def _request(
    connection: Any,
    Gio: Any,
    GLib: Any,
    busname: str,
    interface: str,
    method: str,
    signature: str,
    leading_args: tuple[Any, ...],
    options: dict[str, Any],
    timeout: float,
) -> tuple[int, Any]:
    """Call a Request-returning portal method, racelessly.

    Subscribing to the ``Response`` signal only *after* the call that
    returns its request handle is a real race, not a hypothetical one: a
    fast, non-interactive response (no consent dialog involved, e.g.
    ``SelectDevices``) can arrive and be delivered before the subscription
    is registered, hanging forever on a signal that already came and went.
    Reproduced live (intermittent hangs at both ``SelectDevices`` and
    ``SelectSources`` in the code this was ported from). Fixed by choosing
    the ``handle_token`` ourselves, computing the resulting request object
    path up front, and subscribing to that exact path *before* making the
    call at all -- the pattern xdg-desktop-portal's own documentation
    describes.

    Raises :class:`PortalTimeoutError` if the whole round trip -- the call
    that returns the request handle, then the ``Response`` that answers it
    -- exceeds ``timeout``. The nested loop is otherwise unbounded, and a
    portal that dies after accepting the call sends no ``Response`` and no
    error, leaving the caller wedged with nothing to poll and no way out.
    Both legs share one deadline (see `_msec_until`), since a caller asking
    for 60 seconds means the answer arrives inside 60 seconds, not inside
    however many 60-second waits the sequence happens to be built from.
    """
    deadline = time.monotonic() + timeout
    unique_name = connection.get_unique_name()
    escaped_sender = unique_name[1:].replace(".", "_")
    token = uuid.uuid4().hex
    options = dict(options)
    options["handle_token"] = GLib.Variant("s", token)
    expected_path = f"/org/freedesktop/portal/desktop/request/{escaped_sender}/{token}"

    loop = GLib.MainLoop()
    result: dict[str, Any] = {}
    subscriptions: list[Any] = []
    # Set inside the timeout callback rather than inferred from an empty
    # `result` afterwards: a Response carrying no results is legitimate
    # (SelectDevices answers with an empty dict), so "did the loop end
    # because it timed out" has to be recorded, not deduced.
    timed_out = False

    def on_response(
        _conn: Any,
        _sender: Any,
        _path: Any,
        _iface: Any,
        _signal: Any,
        params: Any,
        *_a: Any,
    ) -> None:
        if result:  # both subscriptions may fire; the first reply wins
            return
        result["code"], result["results"] = params.unpack()
        loop.quit()

    def on_timeout() -> bool:
        nonlocal timed_out
        timed_out = True
        loop.quit()
        return False  # one-shot; GLib removes the source when this is False

    def subscribe(path: str) -> None:
        subscriptions.append(
            connection.signal_subscribe(
                busname,
                _REQUEST_INTERFACE,
                "Response",
                path,
                None,
                Gio.DBusSignalFlags.NONE,
                on_response,
                None,
            )
        )

    subscribe(expected_path)
    try:
        parameters = GLib.Variant(signature, (*leading_args, options))
        reply = _call_sync(
            connection,
            Gio,
            GLib,
            busname,
            _OBJECT_PATH,
            interface,
            method,
            parameters,
            None,
            _msec_until(deadline),
        )
        # The spec says the handle the call returns matches the path derived
        # from our own handle_token, but a portal is free to hand back
        # something else -- and some do. Listening on both is strictly safer
        # than trusting either alone: watching only the derived path means a
        # Response delivered to the returned handle is never seen, and the
        # wait below then runs out the full timeout for no reason.
        handle = _returned_handle(reply)
        if handle is not None and handle != expected_path:
            subscribe(handle)
        # `if not result` because a synchronous answer (a fast
        # non-interactive Response, or a test double) can arrive during the
        # call above, before run() is reached -- and quit() on a loop that
        # is not running yet does not stop the later run(), so running it
        # then would block with the reply already delivered.
        if not result:
            timeout_source = GLib.timeout_add(_msec_until(deadline), on_timeout)
            try:
                loop.run()
            finally:
                # Removing an already-fired one-shot source is harmless
                # (GLib warns at most); leaking a live one holds a reference
                # to this closure and fires it into a dead loop later.
                GLib.source_remove(timeout_source)
    finally:
        for subscription in subscriptions:
            connection.signal_unsubscribe(subscription)
    if timed_out:
        raise PortalTimeoutError(method, timeout)
    return result["code"], result["results"]


def _call_for_fd(
    connection: Any,
    Gio: Any,
    GLib: Any,
    busname: str,
    interface: str,
    method: str,
    session_handle: str,
    timeout: float,
) -> int:
    """Call a method that returns a fd via a GUnixFDList index.

    The fd that comes back is *owned* -- `g_unix_fd_list_get()` dups it --
    so whoever receives it has to close it. See
    :meth:`RemoteDesktopSession.close`.

    ``ConnectToEIS`` returns no `Request`, so there is no ``Response`` to
    wait on and nothing here beyond the call itself -- but it is a call to
    the same portal that just made the caller wait on a consent dialog, so
    it is bounded by the same ``timeout`` rather than by GIO's default.
    """
    try:
        reply, fd_list = connection.call_with_unix_fd_list_sync(
            busname,
            _OBJECT_PATH,
            interface,
            method,
            GLib.Variant("(oa{sv})", (session_handle, {})),
            GLib.VariantType.new("(h)"),
            Gio.DBusCallFlags.NONE,
            int(timeout * 1000),
            None,
            None,
        )
    except _glib_error(GLib) as exc:
        if _is_reply_timeout(Gio, exc):
            raise PortalTimeoutError(method, timeout) from exc
        raise PortalError(f"{method} failed on the D-Bus: {exc}") from exc
    (handle_index,) = reply.unpack()
    return fd_list.get(handle_index)


def _remote_desktop_version(
    connection: Any, Gio: Any, GLib: Any, busname: str, timeout: float
) -> int:
    """Read the RemoteDesktop portal's ``version`` property."""
    reply = _call_sync(
        connection,
        Gio,
        GLib,
        busname,
        _OBJECT_PATH,
        "org.freedesktop.DBus.Properties",
        "Get",
        GLib.Variant("(ss)", (_REMOTE_DESKTOP, "version")),
        None,
        int(timeout * 1000),
    )
    (version,) = reply.unpack()
    return int(version)


class RemoteDesktopSession:
    """A negotiated ``org.freedesktop.portal.RemoteDesktop`` session.

    Two things here need releasing, and neither happens on its own when
    this object is dropped:

    * **The portal session.** It lives in xdg-desktop-portal, not in this
      process, and persists until ``Session.Close()`` is called or the D-Bus
      connection that created it drops. That connection is *not* owned here
      -- ``Gio.bus_get_sync()`` hands back GLib's shared session-bus
      singleton, which outlives any one session -- so a long-running process
      that negotiates repeatedly accumulates live portal sessions until it
      exits. :meth:`close` is what ends one.
    * **The EIS fd**, which arrives dup'd and owned. Reading :attr:`eis_fd`
      hands that ownership on (typically straight to
      :meth:`libei.ei.Sender.create_for_fd`, which closes it itself); if it
      is never read, :meth:`close` closes it rather than leaking it.

    Use it as a context manager, or call :meth:`close` when done.
    """

    def __init__(
        self,
        connection: Any,
        eis_fd: int,
        restore_token: str | None,
        session_handle: str | None = None,
        busname: str = _BUS_NAME,
    ) -> None:
        self._connection = connection
        self._eis_fd: int | None = eis_fd
        self._session_handle = session_handle
        # Whichever bus name negotiate() used, not the default: a session
        # created on an alternate portal name has to be closed on that same
        # name, or Session.Close() goes to a portal that never heard of it.
        self._busname = busname
        # Mirrors libei.oeffis.Oeffis's ownership rule: reading `eis_fd`
        # hands the fd to the caller, so close() must not also close it once
        # that has happened -- but nothing else will ever close it if the
        # session dies before anyone reads it, so close() must in that case.
        self._eis_fd_claimed = False
        self._closed = False
        self.restore_token = restore_token
        """The token to pass as ``restore_token=`` on the next call to
        avoid re-prompting, or ``None`` if the portal issued none -- either
        because no ``persist_mode`` was requested, or the portal declined
        to grant persistence.

        Save whatever comes back on *every* run rather than only the first:
        the portal is free to hand back a different token each time, and a
        caller that keeps only the original would eventually present a stale
        one. (Observed 2026-09-01 on GNOME: the same token comes back on
        each restore. That is this portal's behaviour, not a guarantee --
        the interface permits a new one.)

        Nothing is written to disk here: a token is a standing grant of
        input injection, so storing it is the caller's decision."""

    @property
    def eis_fd(self) -> int:
        """The fd to pass to :meth:`libei.ei.Sender.create_for_fd`.

        Reading this transfers ownership of the fd to the caller -- after
        that, closing it is the caller's job (or, far more usually, the
        `Sender`'s, which takes ownership and closes it itself).
        """
        if self._eis_fd is None:
            raise PortalError("the session is closed; its EIS fd is gone")
        self._eis_fd_claimed = True
        return self._eis_fd

    def close(self) -> None:
        """End the portal session, and close the EIS fd if unclaimed.

        Idempotent. ``Session.Close()`` failures are logged and swallowed:
        the session may already be gone (the portal restarted, the user
        revoked the grant), and there is nothing a caller could usefully do
        about it during cleanup either way.

        Note that this does *not* disturb an `ei.Sender` already built on
        the fd -- closing the portal session is what tears the EIS
        connection down, so do it when finished injecting, not before.
        """
        if self._closed:
            return
        self._closed = True
        if self._eis_fd is not None and not self._eis_fd_claimed:
            try:
                os.close(self._eis_fd)
            except OSError as exc:
                # Nothing a caller could do about a failed close during
                # cleanup, and raising here would mask whatever exception
                # was already unwinding through a `with` block.
                logger.debug("closing the EIS fd failed: %s", exc)
        self._eis_fd = None
        if self._session_handle is None or self._connection is None:
            return
        gio_modules = _gio()
        if gio_modules is None:  # pragma: no cover - unreachable once negotiated
            return
        Gio, GLib = gio_modules
        try:
            _close_session(
                self._connection,
                Gio,
                GLib,
                self._busname,
                self._session_handle,
            )
        finally:
            self._session_handle = None
            self._connection = None

    def __enter__(self) -> RemoteDesktopSession:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def __del__(self) -> None:
        # Deliberately only the fd, not the D-Bus half of close(): __del__
        # can run during interpreter shutdown, where a synchronous D-Bus
        # round trip may hang or fail in ways nothing can report. Closing an
        # unclaimed fd is the part that is always safe and always necessary
        # -- nothing else will ever close it. getattr() with defaults
        # because __init__ can raise before these exist, and a bare
        # attribute access would then raise AttributeError inside __del__,
        # which Python only prints to stderr.
        if getattr(self, "_eis_fd_claimed", True):
            return
        eis_fd = getattr(self, "_eis_fd", None)
        if eis_fd is not None:
            try:
                os.close(eis_fd)
            except OSError:
                pass  # an exception here is only printed to stderr anyway

    @classmethod
    def negotiate(
        cls,
        *,
        connection: Any = None,
        devices: DeviceType = DeviceType.ALL_DEVICES,
        persist_mode: PersistMode = PersistMode.NONE,
        restore_token: str | None = None,
        busname: str = _BUS_NAME,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> RemoteDesktopSession:
        """Negotiate a RemoteDesktop portal session and connect it to EIS.

        Blocks until the whole ``CreateSession`` -> ``SelectDevices`` ->
        ``Start`` -> ``ConnectToEIS`` sequence resolves, prompting the user
        for consent along the way unless ``restore_token`` lets the portal
        skip that. Raises :class:`PortalVersionError` if the compositor's
        RemoteDesktop portal is too old for ``ConnectToEIS`` (needs v2+),
        :class:`PortalDeniedError` if any step is declined, and
        :class:`PortalTimeoutError` if any one round trip exceeds
        ``timeout`` seconds (60 by default -- generous, since ``Start``
        waits on a human answering a dialog). Any of those raised after
        ``CreateSession`` has succeeded closes the session it created on
        the way out; nothing else could, since no `RemoteDesktopSession`
        exists yet to own it and the portal would keep it alive for the
        life of the bus connection.

        ``devices`` selects what to ask for. ``DeviceType.ALL_DEVICES`` is
        liboeffis's sentinel for "everything" and is literally ``0``, which
        the portal would read as *nothing*; it is translated here to every
        type the portal defines.

        ``persist_mode`` and ``restore_token`` are how a caller avoids
        being prompted on every launch: ask for persistence, read
        :attr:`restore_token` afterwards, store it, and hand it back next
        time. Passing ``restore_token`` *without* a ``persist_mode`` raises
        :class:`ValueError`: the portal answers such a request with no
        token at all, so a caller following the store-what-comes-back rule
        would write ``None`` over the token it just spent -- silently
        ending the persistence it plainly meant to keep.

        ``connection`` can be injected (a `Gio.DBusConnection`, or a
        test double matching its subset of methods this module calls) to
        reuse an existing bus connection, or to test this against a fake
        one without a real portal -- see ``tests/test_portal.py``. Left as
        ``None``, this opens a new session-bus connection itself.

        No ScreenCast source is ever requested here: an absolute-pointer
        EIS device carries its own region, and asking for ScreenCast too
        would make the user grant screen-recording permission for nothing
        an EIS-only caller needs.
        """
        if restore_token is not None and persist_mode == PersistMode.NONE:
            raise ValueError(
                "restore_token was given with persist_mode=NONE: the portal "
                "consumes a restore token on use and only issues a new one "
                "when persistence is requested, so this would spend the "
                "saved token and hand back None. Pass a persist_mode too."
            )

        gio_modules = _gio()
        if gio_modules is None:
            raise PortalError(
                "PyGObject is not installed; libei.portal needs it to "
                "negotiate a RemoteDesktop portal session "
                "(pip install 'python-libei[portal]')"
            )
        Gio, GLib = gio_modules

        if connection is None:
            try:
                connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except _glib_error(GLib) as exc:
                raise PortalError(f"cannot reach the session bus: {exc}") from exc

        version = _remote_desktop_version(connection, Gio, GLib, busname, timeout)
        if version < _MIN_REMOTE_DESKTOP_VERSION:
            raise PortalVersionError(
                f"RemoteDesktop version {version} is too old for "
                f"ConnectToEIS (need {_MIN_REMOTE_DESKTOP_VERSION}+)"
            )

        # session_handle_token is a *different* token from the handle_token
        # _request() injects itself: omitting it crashes xdg-desktop-portal
        # 1.22.1 outright (SIGABRT, "assertion failed:
        # (session->token != NULL)") -- not optional.
        code, results = _request(
            connection,
            Gio,
            GLib,
            busname,
            _REMOTE_DESKTOP,
            "CreateSession",
            "(a{sv})",
            (),
            {"session_handle_token": GLib.Variant("s", uuid.uuid4().hex)},
            timeout,
        )
        if code != 0:
            raise PortalDeniedError("CreateSession")
        session_handle = results.get("session_handle")
        if not isinstance(session_handle, str):
            # Approved, but with no handle to address the rest of the
            # sequence to. `results` is portal-supplied data like any other
            # reply, so a malformed one fails the way the rest of this
            # module's do -- rather than as the KeyError a direct index
            # would raise straight past a caller's `except PortalError`.
            raise PortalError("CreateSession returned no session_handle")

        # Past this point a session exists inside xdg-desktop-portal, and
        # nothing else can close it: no RemoteDesktopSession owns it yet,
        # and the connection it was created on is GLib's shared session-bus
        # singleton, which outlives this failure rather than taking the
        # session down with it. So every exit from here closes it.
        try:
            return cls._connect_devices(
                connection,
                Gio,
                GLib,
                busname,
                session_handle,
                devices=devices,
                persist_mode=persist_mode,
                restore_token=restore_token,
                timeout=timeout,
            )
        except BaseException:
            # BaseException, not Exception: `Start` blocks on a human
            # answering a consent dialog, so Ctrl-C during that wait is a
            # routine way out of this function -- and it strands an
            # approved session exactly as a decline does.
            _close_session(connection, Gio, GLib, busname, session_handle)
            raise

    @classmethod
    def _connect_devices(
        cls,
        connection: Any,
        Gio: Any,
        GLib: Any,
        busname: str,
        session_handle: str,
        *,
        devices: DeviceType,
        persist_mode: PersistMode,
        restore_token: str | None,
        timeout: float,
    ) -> RemoteDesktopSession:
        """``SelectDevices`` -> ``Start`` -> ``ConnectToEIS``, given a session.

        Split out of :meth:`negotiate` only so that the caller can wrap the
        whole of it in one ``try`` -- every step here can fail, and every
        one of those failures leaves the same session behind to be closed.
        """
        # ALL_DEVICES is 0, which SelectDevices reads as "no device types"
        # rather than "every device type" -- see _ALL_DEVICE_TYPES.
        types = _ALL_DEVICE_TYPES if devices == DeviceType.ALL_DEVICES else devices
        options: dict[str, Any] = {"types": GLib.Variant("u", int(types))}
        if persist_mode != PersistMode.NONE:
            options["persist_mode"] = GLib.Variant("u", int(persist_mode))
        if restore_token is not None:
            options["restore_token"] = GLib.Variant("s", restore_token)
        code, _results = _request(
            connection,
            Gio,
            GLib,
            busname,
            _REMOTE_DESKTOP,
            "SelectDevices",
            "(oa{sv})",
            (session_handle,),
            options,
            timeout,
        )
        if code != 0:
            raise PortalDeniedError("SelectDevices")

        code, results = _request(
            connection,
            Gio,
            GLib,
            busname,
            _REMOTE_DESKTOP,
            "Start",
            "(osa{sv})",
            (session_handle, ""),
            {},
            timeout,
        )
        if code != 0:
            raise PortalDeniedError(
                "Start", "the user declined the remote-control consent dialog"
            )
        new_restore_token = results.get("restore_token")

        eis_fd = _call_for_fd(
            connection,
            Gio,
            GLib,
            busname,
            _REMOTE_DESKTOP,
            "ConnectToEIS",
            session_handle,
            timeout,
        )
        return cls(
            connection,
            eis_fd,
            new_restore_token,
            session_handle,
            busname,
        )
