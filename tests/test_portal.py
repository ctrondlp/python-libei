"""Unit tests for libei.portal's RemoteDesktop negotiation.

All D-Bus calls are faked -- there is deliberately no live-portal
integration test here, for the same reason test_oeffis.py has none: driving
a real xdg-desktop-portal RemoteDesktop session needs an interactive
consent dialog nothing here can click through. Manually verified working
against a real portal (see the module docstring), but manual verification
isn't automated coverage -- treat this as the least *automatically* verified
part of this package, same caveat as libei.oeffis.

FakeConnection deliberately fires the Response signal from inside
call_sync() itself, simulating a response that arrives synchronously (no
consent dialog involved, e.g. SelectDevices). That is what proves the
raceless subscribe-before-call pattern in _request() actually works: a
naive "call first, subscribe after" implementation would deadlock against
this fake, since the response would already be gone by the time it
subscribed.
"""

from __future__ import annotations

import os
import sys
import time
import types
from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest

from libei import portal


class FakeVariant:
    def __init__(self, signature: str, value: Any) -> None:
        self.signature = signature
        self.value = value


class FakeReply:
    def __init__(self, value: Any) -> None:
        self._value = value

    def unpack(self) -> Any:
        return self._value


class FakeUnixFDList:
    def __init__(self, fd: int) -> None:
        self._fd = fd

    def get(self, _index: int) -> int:
        return self._fd


class FakeGLibError(Exception):
    """Stands in for `GLib.Error`, which every GDBus failure arrives as.

    `domain` and `code` are what portal._is_reply_timeout reads to tell
    GDBus's own reply timeout from every other D-Bus failure. They default
    to values that match nothing, so a bare FakeGLibError still stands for
    "some other failure" as it did before.
    """

    def __init__(self, message: str = "", domain: str = "", code: int = 0) -> None:
        super().__init__(message)
        self.domain = domain
        self.code = code


_OPEN_PIPES: list[tuple[int, int]] = []


def _fresh_fd() -> int:
    """A real, closeable fd for the fake `ConnectToEIS` to hand back.

    It has to be real. `close()` calls `os.close()` on whatever it was
    given, so a fabricated number (this fake used a hardcoded 12 first)
    either raises EBADF or -- far worse, and how it went unnoticed ---
    silently closes one of the *test runner's* descriptors, because under
    pytest that number is usually a live fd. The tests passed by corrupting
    the process running them.
    """
    read_fd, write_fd = os.pipe()
    _OPEN_PIPES.append((read_fd, write_fd))
    return read_fd


@pytest.fixture(autouse=True)
def _close_fake_fds() -> Iterator[None]:
    yield
    while _OPEN_PIPES:
        for fd in _OPEN_PIPES.pop():
            try:
                os.close(fd)
            except OSError:
                pass  # already closed by the code under test, which is the point


class FakeMainLoop:
    """quit() is normally called synchronously, before run() is reached.

    `pending_timeout` is the escape hatch for the timeout tests: a loop
    whose quit() was never called fires whatever `GLib.timeout_add`
    registered, standing in for real GLib firing the source once the wall
    clock passes it. Without that, a fake that simply raised on an unquit
    run() could not distinguish "hung" from "timed out".
    """

    pending_timeout: Any = None
    # What GLib.timeout_add was asked to wait, so a test can check the
    # Response leg got only the time the call leg left of the deadline.
    pending_timeout_msec: int | None = None

    def __init__(self) -> None:
        self._quit = False

    def run(self) -> None:
        if self._quit:
            return
        callback = FakeMainLoop.pending_timeout
        if callback is not None:
            FakeMainLoop.pending_timeout = None
            callback()
            return
        raise AssertionError("run() called before a synchronous quit()")

    def quit(self) -> None:
        self._quit = True


class FakeConnection:
    """Stands in for a Gio.DBusConnection bound to the session bus.

    signal_subscribe happens *before* call_sync in the raceless pattern
    _request() uses -- so call_sync itself computes the request path from
    the handle_token in its own parameters and fires whichever subscription
    is already registered for it, rather than the other way around.
    """

    def __init__(
        self,
        responses: dict[str, tuple[int, dict[str, Any]]] | None = None,
        version: int = 2,
        fd_responses: dict[str, int] | None = None,
        unique_name: str = ":1.99",
        handle_path: str | None = None,
    ) -> None:
        self.handle_path = handle_path
        self.responses = responses or {
            "CreateSession": (0, {"session_handle": "/session/1"}),
            "SelectDevices": (0, {}),
            "Start": (0, {"devices": 2}),
        }
        self.version = version
        self.fd_responses = fd_responses or {"ConnectToEIS": _fresh_fd()}
        self.calls: list[tuple[str, Any]] = []
        # Kept separate from `calls`, whose two-tuple shape several tests
        # compare against exactly: (method, bus name, object path, timeout).
        self.call_targets: list[tuple[str, Any, Any, Any]] = []
        self._unique_name = unique_name
        self._subscriptions: dict[str, Any] = {}
        self._pending_reply: tuple[str, int, dict[str, Any]] | None = None
        self._subscription_paths: dict[int, str] = {}
        self._next_subscription_id = 0

    def get_unique_name(self) -> str:
        return self._unique_name

    def _escaped_sender(self) -> str:
        return self._unique_name[1:].replace(".", "_")

    def call_sync(
        self,
        bus_name: Any,
        object_path: Any,
        interface: Any,
        method: str,
        parameters: FakeVariant | None,
        reply_type: Any,
        flags: Any,
        timeout: Any,
        cancellable: Any,
    ) -> FakeReply:
        # `parameters` is None for a method that takes no arguments, which
        # is exactly what real Gio expects and what Session.Close() sends.
        self.calls.append((method, None if parameters is None else parameters.value))
        self.call_targets.append((method, bus_name, object_path, timeout))
        if parameters is None:
            return FakeReply(())
        if method == "Get":
            return FakeReply((self.version,))
        *_leading, options = parameters.value
        token = options["handle_token"].value
        path = (
            f"/org/freedesktop/portal/desktop/request/{self._escaped_sender()}/{token}"
        )
        code, results = self.responses.get(method, (0, {}))
        # `handle_path` lets a test make the portal answer on a path other
        # than the one derived from handle_token -- which the spec says it
        # should not do, but real portals are free to, so the caller has to
        # listen on the returned handle too.
        answer_on = self.handle_path or path
        callback = self._subscriptions.get(answer_on)
        if callback is not None:
            callback(
                None, None, answer_on, None, "Response", FakeReply((code, results))
            )
        else:
            # Nothing is watching that path yet: _request() subscribes to
            # the returned handle only after this call returns. Stash the
            # reply so that subscription collects it, which is the order a
            # real portal's asynchronous Response arrives in anyway.
            self._pending_reply = (answer_on, code, results)
        # A Request-returning method replies with its request object path.
        return FakeReply((answer_on,))

    def signal_subscribe(
        self,
        bus_name: Any,
        iface: Any,
        signal: Any,
        path: str,
        arg0: Any,
        flags: Any,
        callback: Any,
        user_data: Any,
    ) -> int:
        self._subscriptions[path] = callback
        self._next_subscription_id += 1
        self._subscription_paths[self._next_subscription_id] = path
        pending = self._pending_reply
        if pending is not None and pending[0] == path:
            self._pending_reply = None
            _path, code, results = pending
            callback(None, None, path, None, "Response", FakeReply((code, results)))
        return self._next_subscription_id

    def signal_unsubscribe(self, subscription_id: int) -> None:
        # Really removes it, as GLib does. A no-op here left each request's
        # callback registered for the next one, which then fired a stale
        # closure bound to an already-finished loop -- and the live request
        # waited out its whole timeout for a reply that had been handed to
        # its predecessor.
        path = self._subscription_paths.pop(subscription_id, None)
        if path is not None:
            self._subscriptions.pop(path, None)

    def call_with_unix_fd_list_sync(
        self,
        bus_name: Any,
        object_path: Any,
        interface: Any,
        method: str,
        parameters: FakeVariant,
        reply_type: Any,
        flags: Any,
        timeout: Any,
        fd_list: Any,
        cancellable: Any,
    ) -> tuple[FakeReply, FakeUnixFDList]:
        self.calls.append((method, parameters.value))
        self.call_targets.append((method, bus_name, object_path, timeout))
        return FakeReply((0,)), FakeUnixFDList(self.fd_responses[method])


def install_fake_gi(connection: FakeConnection | None = None) -> Any:
    gi = types.ModuleType("gi")
    gi.require_version = lambda *a, **kw: None  # type: ignore[attr-defined]
    repository = types.ModuleType("gi.repository")

    Gio = types.ModuleType("gi.repository.Gio")
    Gio.BusType = types.SimpleNamespace(SESSION=1)  # type: ignore[attr-defined]
    Gio.DBusCallFlags = types.SimpleNamespace(NONE=0)  # type: ignore[attr-defined]
    Gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)  # type: ignore[attr-defined]
    # 24 is G_IO_ERROR_TIMED_OUT, the code a real call_sync() raises in the
    # g-io-error-quark domain when its reply never comes.
    Gio.IOErrorEnum = types.SimpleNamespace(TIMED_OUT=24)  # type: ignore[attr-defined]
    Gio.bus_get_sync = (  # type: ignore[attr-defined]
        lambda *a, **kw: connection or FakeConnection()
    )

    GLib = types.ModuleType("gi.repository.GLib")
    GLib.Variant = FakeVariant  # type: ignore[attr-defined]
    GLib.MainLoop = FakeMainLoop  # type: ignore[attr-defined]
    GLib.Error = FakeGLibError  # type: ignore[attr-defined]
    GLib.VariantType = types.SimpleNamespace(  # type: ignore[attr-defined]
        new=lambda sig: sig
    )

    # timeout_add stashes the callback rather than scheduling it; only the
    # timeout tests, which never quit() the loop, actually let it fire.
    def timeout_add(ms: int, callback: Any) -> int:
        FakeMainLoop.pending_timeout = callback
        FakeMainLoop.pending_timeout_msec = ms
        return 1

    def source_remove(_id: int) -> None:
        # Mirrors real GLib: a removed source cannot fire afterwards. Since
        # _request() always removes in a finally, this also stops a stashed
        # callback leaking from one test into the next.
        FakeMainLoop.pending_timeout = None

    GLib.timeout_add = timeout_add  # type: ignore[attr-defined]
    GLib.source_remove = source_remove  # type: ignore[attr-defined]

    repository.Gio = Gio  # type: ignore[attr-defined]
    repository.GLib = GLib  # type: ignore[attr-defined]
    gi.repository = repository  # type: ignore[attr-defined]
    return mock.patch.dict(
        sys.modules,
        {
            "gi": gi,
            "gi.repository": repository,
            "gi.repository.Gio": Gio,
            "gi.repository.GLib": GLib,
        },
    )


def test_is_available_true_when_pygobject_imports() -> None:
    with install_fake_gi():
        assert portal.is_available() is True


def test_is_available_false_when_pygobject_is_missing() -> None:
    with mock.patch.dict(sys.modules, {"gi": None}):
        assert portal.is_available() is False


def test_negotiate_without_pygobject_raises_portal_error() -> None:
    with mock.patch.dict(sys.modules, {"gi": None}):
        with pytest.raises(portal.PortalError, match="PyGObject"):
            portal.RemoteDesktopSession.negotiate()


def test_successful_negotiation_calls_every_step_in_order() -> None:
    connection = FakeConnection()
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(connection=connection)
    methods = [c[0] for c in connection.calls]
    assert methods == ["Get", "CreateSession", "SelectDevices", "Start", "ConnectToEIS"]
    assert session.eis_fd == connection.fd_responses["ConnectToEIS"]


def test_old_remote_desktop_version_is_refused() -> None:
    connection = FakeConnection(version=1)
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalVersionError):
            portal.RemoteDesktopSession.negotiate(connection=connection)
    # Refused before ever calling CreateSession.
    assert connection.calls == [
        ("Get", ("org.freedesktop.portal.RemoteDesktop", "version"))
    ]


def test_declined_create_session_raises_portal_denied_error() -> None:
    connection = FakeConnection(responses={"CreateSession": (1, {})})
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalDeniedError) as excinfo:
            portal.RemoteDesktopSession.negotiate(connection=connection)
    assert excinfo.value.step == "CreateSession"


def test_declined_start_raises_portal_denied_error() -> None:
    connection = FakeConnection(
        responses={
            "CreateSession": (0, {"session_handle": "/session/1"}),
            "SelectDevices": (0, {}),
            "Start": (1, {}),  # 1 == user cancelled, per the portal spec
        }
    )
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalDeniedError) as excinfo:
            portal.RemoteDesktopSession.negotiate(connection=connection)
    assert excinfo.value.step == "Start"


def test_no_persist_options_are_sent_by_default() -> None:
    connection = FakeConnection()
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(connection=connection)
    select = next(c for c in connection.calls if c[0] == "SelectDevices")
    assert "persist_mode" not in select[1][-1]
    assert "restore_token" not in select[1][-1]
    assert session.restore_token is None


def test_persist_mode_and_restore_token_round_trip() -> None:
    connection = FakeConnection(
        responses={
            "CreateSession": (0, {"session_handle": "/session/1"}),
            "SelectDevices": (0, {}),
            "Start": (0, {"devices": 2, "restore_token": "tok-next"}),
        }
    )
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(
            connection=connection,
            restore_token="tok-old",
            persist_mode=portal.PersistMode.UNTIL_REVOKED,
        )
    select = next(c for c in connection.calls if c[0] == "SelectDevices")
    assert select[1][-1]["persist_mode"].value == 2
    assert select[1][-1]["restore_token"].value == "tok-old"
    # Single-use: the fresh token replaces the one that was presented.
    assert session.restore_token == "tok-next"


def test_devices_bitmask_is_forwarded_to_select_devices() -> None:
    connection = FakeConnection()
    with install_fake_gi(connection):
        portal.RemoteDesktopSession.negotiate(
            connection=connection,
            devices=portal.DeviceType.POINTER,
        )
    select = next(c for c in connection.calls if c[0] == "SelectDevices")
    assert select[1][-1]["types"].value == int(portal.DeviceType.POINTER)


def test_no_screencast_source_is_ever_requested() -> None:
    connection = FakeConnection()
    with install_fake_gi(connection):
        portal.RemoteDesktopSession.negotiate(connection=connection)
    methods = [c[0] for c in connection.calls]
    assert "SelectSources" not in methods
    assert "OpenPipeWireRemote" not in methods


def test_a_response_on_the_returned_handle_is_still_seen() -> None:
    # The spec says the handle a call returns matches the path derived from
    # our own handle_token, but a portal is free to hand back something
    # else. Listening only on the derived path means such a Response is
    # never seen and the request burns its whole timeout for nothing.
    connection = FakeConnection(
        handle_path="/org/freedesktop/portal/desktop/request/x/y"
    )
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(
            connection=connection, timeout=5
        )
    methods = [c[0] for c in connection.calls]
    assert methods == ["Get", "CreateSession", "SelectDevices", "Start", "ConnectToEIS"]
    assert session.eis_fd == connection.fd_responses["ConnectToEIS"]


def test_a_synchronous_response_does_not_deadlock_the_loop() -> None:
    # quit() on a loop that is not running yet does not stop the later
    # run(). A Response delivered during call_sync -- which is exactly what
    # this fake does -- would therefore block forever on a reply already in
    # hand, were the loop not guarded by `if not result`. The fake's run()
    # raises rather than blocking, so an unguarded loop fails loudly here.
    connection = FakeConnection()
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(connection=connection)
    assert session.restore_token is None


def test_default_devices_asks_for_every_type_not_zero() -> None:
    # ALL_DEVICES is liboeffis's sentinel and is literally 0, which the
    # portal reads as *no* device types -- a session that negotiates fine
    # and then never resumes a device. It must be translated on the way out.
    connection = FakeConnection()
    with install_fake_gi(connection):
        portal.RemoteDesktopSession.negotiate(connection=connection)
    select = next(c for c in connection.calls if c[0] == "SelectDevices")
    types = select[1][-1]["types"].value
    assert types != 0
    assert types == (
        portal.DeviceType.KEYBOARD
        | portal.DeviceType.POINTER
        | portal.DeviceType.TOUCHSCREEN
    )


def test_explicit_all_devices_is_translated_too() -> None:
    connection = FakeConnection()
    with install_fake_gi(connection):
        portal.RemoteDesktopSession.negotiate(
            connection=connection, devices=portal.DeviceType.ALL_DEVICES
        )
    select = next(c for c in connection.calls if c[0] == "SelectDevices")
    assert select[1][-1]["types"].value != 0


def test_restore_token_without_persist_mode_is_refused() -> None:
    # The portal consumes a restore token on use and only mints a new one
    # when persistence was asked for, so this combination would spend the
    # caller's saved token and hand back None -- silently ending the
    # persistence they plainly meant to keep.
    connection = FakeConnection()
    with install_fake_gi(connection):
        with pytest.raises(ValueError, match="persist_mode"):
            portal.RemoteDesktopSession.negotiate(
                connection=connection, restore_token="tok-old"
            )
    # Refused before touching the bus at all.
    assert connection.calls == []


def test_dbus_failure_is_wrapped_in_portal_error() -> None:
    # No session bus, or no portal implementation behind the name, both
    # arrive as GLib.Error -- which must not escape `except PortalError`.
    class ExplodingConnection(FakeConnection):
        def call_sync(self, *a: Any, **kw: Any) -> FakeReply:
            raise FakeGLibError("org.freedesktop.DBus.Error.ServiceUnknown")

    connection = ExplodingConnection()
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalError, match="D-Bus"):
            portal.RemoteDesktopSession.negotiate(connection=connection)


def test_request_that_never_answers_times_out() -> None:
    # A portal that accepts the call and then dies sends no Response and no
    # error. Without a bounded loop the calling thread waits forever, with
    # nothing to poll -- the one thing Oeffis's pollable fd would avoid.
    class SilentConnection(FakeConnection):
        def call_sync(
            self,
            bus_name: Any,
            object_path: Any,
            interface: Any,
            method: str,
            parameters: FakeVariant | None,
            reply_type: Any,
            flags: Any,
            timeout: Any,
            cancellable: Any,
        ) -> FakeReply:
            self.calls.append(
                (method, None if parameters is None else parameters.value)
            )
            if method == "Get":
                return FakeReply((self.version,))
            return FakeReply(())  # accepted, but no Response ever fires

    connection = SilentConnection()
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalTimeoutError) as excinfo:
            portal.RemoteDesktopSession.negotiate(connection=connection, timeout=0.01)
    assert excinfo.value.step == "CreateSession"
    assert excinfo.value.timeout == 0.01


def test_close_closes_an_unclaimed_eis_fd() -> None:
    # The fd arrives dup'd and owned. If nobody ever reads `eis_fd` (so it
    # was never handed to a Sender, which would close it itself), nothing
    # else will -- close() must, or a reconnect loop leaks to EMFILE.
    read_fd, write_fd = os.pipe()
    connection = FakeConnection(fd_responses={"ConnectToEIS": read_fd})
    try:
        with install_fake_gi(connection):
            session = portal.RemoteDesktopSession.negotiate(connection=connection)
            session.close()
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass  # expected: close() should already have closed it
        os.close(write_fd)


def test_close_leaves_a_claimed_eis_fd_alone() -> None:
    # The mirror image: once `eis_fd` has been read, ownership passed to the
    # caller (typically straight into Sender.create_for_fd, which closes it
    # itself), so close() must not double-close it.
    read_fd, write_fd = os.pipe()
    connection = FakeConnection(fd_responses={"ConnectToEIS": read_fd})
    try:
        with install_fake_gi(connection):
            session = portal.RemoteDesktopSession.negotiate(connection=connection)
            assert session.eis_fd == read_fd
            session.close()
        os.fstat(read_fd)  # still open -- proves close() left it alone
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_close_ends_the_portal_session() -> None:
    # The portal session lives in xdg-desktop-portal and outlives this
    # object; without Session.Close() a long-running process accumulates
    # live sessions. The shared bus connection does not clean them up.
    connection = FakeConnection()
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(connection=connection)
        session.close()
    assert ("Close", None) in [(c[0], c[1]) for c in connection.calls]


def test_close_is_idempotent() -> None:
    connection = FakeConnection()
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(connection=connection)
        session.close()
        session.close()
    closes = [c for c in connection.calls if c[0] == "Close"]
    assert len(closes) == 1


def test_context_manager_closes_on_exit() -> None:
    connection = FakeConnection()
    with install_fake_gi(connection):
        with portal.RemoteDesktopSession.negotiate(connection=connection) as session:
            assert session.restore_token is None
        methods = [c[0] for c in connection.calls]
    assert "Close" in methods


def test_eis_fd_after_close_raises() -> None:
    connection = FakeConnection()
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(connection=connection)
        session.close()
        with pytest.raises(portal.PortalError, match="closed"):
            _ = session.eis_fd


def test_a_failed_session_close_does_not_propagate() -> None:
    # close() runs during cleanup, often on a session the portal has already
    # dropped. There is nothing a caller could do with the failure.
    class CloseFailsConnection(FakeConnection):
        def call_sync(
            self,
            bus_name: Any,
            object_path: Any,
            interface: Any,
            method: str,
            parameters: Any,
            reply_type: Any,
            flags: Any,
            timeout: Any,
            cancellable: Any,
        ) -> FakeReply:
            if method == "Close":
                raise FakeGLibError("session already gone")
            return super().call_sync(
                bus_name,
                object_path,
                interface,
                method,
                parameters,
                reply_type,
                flags,
                timeout,
                cancellable,
            )

    connection = CloseFailsConnection()
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(connection=connection)
        session.close()  # must not raise


def _closed_sessions(connection: FakeConnection) -> list[Any]:
    """Object paths ``Session.Close()`` was called on, in order."""
    return [call[2] for call in connection.call_targets if call[0] == "Close"]


class _SilentAfterCreateConnection(FakeConnection):
    """Answers CreateSession, then accepts Start and never responds.

    Stands in for a portal that dies (or a compositor whose dialog never
    returns) after a session already exists -- the case where a caller is
    left holding nothing at all, since `negotiate` raises rather than
    returning the object whose `close()` would clean up.
    """

    def call_sync(
        self,
        bus_name: Any,
        object_path: Any,
        interface: Any,
        method: str,
        parameters: FakeVariant | None,
        reply_type: Any,
        flags: Any,
        timeout: Any,
        cancellable: Any,
    ) -> FakeReply:
        if method == "Start":
            self.calls.append((method, parameters.value if parameters else None))
            self.call_targets.append((method, bus_name, object_path, timeout))
            return FakeReply(())  # accepted, but no Response ever fires
        return super().call_sync(
            bus_name,
            object_path,
            interface,
            method,
            parameters,
            reply_type,
            flags,
            timeout,
            cancellable,
        )


def test_a_declined_select_devices_closes_the_portal_session() -> None:
    # CreateSession has already created a session inside xdg-desktop-portal
    # by this point, and nothing else will ever close it: negotiate() raises
    # instead of returning the object whose close() would, and the session
    # outlives the failure on the shared session-bus connection.
    connection = FakeConnection(
        responses={
            "CreateSession": (0, {"session_handle": "/session/1"}),
            "SelectDevices": (1, {}),
        }
    )
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalDeniedError):
            portal.RemoteDesktopSession.negotiate(connection=connection)
    assert _closed_sessions(connection) == ["/session/1"]


def test_a_declined_start_closes_the_portal_session() -> None:
    # The most likely failure of the lot: the user says no to the consent
    # dialog. An approved-then-declined session left open is a grant the
    # portal keeps listing for a process that gave up on it.
    connection = FakeConnection(
        responses={
            "CreateSession": (0, {"session_handle": "/session/1"}),
            "SelectDevices": (0, {}),
            "Start": (1, {}),
        }
    )
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalDeniedError):
            portal.RemoteDesktopSession.negotiate(connection=connection)
    assert _closed_sessions(connection) == ["/session/1"]


def test_a_timed_out_request_closes_the_portal_session() -> None:
    connection = _SilentAfterCreateConnection()
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalTimeoutError) as excinfo:
            portal.RemoteDesktopSession.negotiate(connection=connection, timeout=0.01)
    assert excinfo.value.step == "Start"
    assert _closed_sessions(connection) == ["/session/1"]


def test_a_failed_connect_to_eis_closes_the_portal_session() -> None:
    # The last step, and the one with no Request of its own: a session that
    # was fully approved and then failed to hand back an EIS fd is still a
    # session, and still has to be closed.
    class NoEisConnection(FakeConnection):
        def call_with_unix_fd_list_sync(
            self, *args: Any, **kwargs: Any
        ) -> tuple[FakeReply, FakeUnixFDList]:
            raise FakeGLibError("org.freedesktop.DBus.Error.Failed")

    connection = NoEisConnection()
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalError, match="D-Bus"):
            portal.RemoteDesktopSession.negotiate(connection=connection)
    assert _closed_sessions(connection) == ["/session/1"]


def test_an_interrupted_consent_dialog_closes_the_portal_session() -> None:
    # Why the cleanup catches BaseException: Start blocks on a human, so
    # Ctrl-C during that wait is a routine way out of negotiate() -- and it
    # strands an approved session exactly as a decline does.
    class InterruptedConnection(FakeConnection):
        def call_sync(
            self,
            bus_name: Any,
            object_path: Any,
            interface: Any,
            method: str,
            parameters: FakeVariant | None,
            reply_type: Any,
            flags: Any,
            timeout: Any,
            cancellable: Any,
        ) -> FakeReply:
            if method == "Start":
                raise KeyboardInterrupt
            return super().call_sync(
                bus_name,
                object_path,
                interface,
                method,
                parameters,
                reply_type,
                flags,
                timeout,
                cancellable,
            )

    connection = InterruptedConnection()
    with install_fake_gi(connection):
        with pytest.raises(KeyboardInterrupt):
            portal.RemoteDesktopSession.negotiate(connection=connection)
    assert _closed_sessions(connection) == ["/session/1"]


def test_a_successful_negotiation_closes_nothing() -> None:
    # The other half of the cleanup: a session that negotiated fine belongs
    # to the caller until they close it.
    connection = FakeConnection()
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(connection=connection)
        assert _closed_sessions(connection) == []
        session.close()
    assert _closed_sessions(connection) == ["/session/1"]


def test_a_missing_session_handle_raises_portal_error() -> None:
    # Approved, but with nothing to address the rest of the sequence to.
    # A direct index would raise KeyError straight past `except PortalError`.
    connection = FakeConnection(responses={"CreateSession": (0, {})})
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalError, match="session_handle"):
            portal.RemoteDesktopSession.negotiate(connection=connection)
    # And nothing to close: no handle means no session to end.
    assert _closed_sessions(connection) == []


def test_the_session_is_closed_on_the_bus_name_it_was_negotiated_on() -> None:
    # A session created on an alternate portal name has to be closed on
    # that same name; sending Session.Close() to the default bus name
    # reaches a portal that never heard of this session.
    connection = FakeConnection()
    with install_fake_gi(connection):
        session = portal.RemoteDesktopSession.negotiate(
            connection=connection, busname="org.example.Portal"
        )
        session.close()
    closes = [call for call in connection.call_targets if call[0] == "Close"]
    assert [call[1] for call in closes] == ["org.example.Portal"]


def test_a_failed_negotiation_closes_on_the_bus_name_it_used() -> None:
    connection = FakeConnection(
        responses={
            "CreateSession": (0, {"session_handle": "/session/1"}),
            "SelectDevices": (1, {}),
        }
    )
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalDeniedError):
            portal.RemoteDesktopSession.negotiate(
                connection=connection, busname="org.example.Portal"
            )
    closes = [call for call in connection.call_targets if call[0] == "Close"]
    assert [call[1] for call in closes] == ["org.example.Portal"]


def test_every_negotiation_call_is_bounded_by_the_callers_timeout() -> None:
    # GDBus's -1 is not "no timeout" (that is G_MAXINT) but GIO's own 25s
    # default -- a number this module never chose and a caller cannot see.
    # Every leg carries the timeout the caller asked for instead, including
    # ConnectToEIS, which returns no Request and so has no Response leg of
    # its own for the nested loop to bound.
    connection = FakeConnection()
    with install_fake_gi(connection):
        portal.RemoteDesktopSession.negotiate(connection=connection, timeout=12.0)
    timeouts = {call[0]: call[3] for call in connection.call_targets}
    assert timeouts["Get"] == 12_000
    assert timeouts["ConnectToEIS"] == 12_000
    # The Request-returning legs draw on a shared deadline, so they get at
    # most the full timeout, never more, and never GIO's default.
    for method in ("CreateSession", "SelectDevices", "Start"):
        assert 0 < timeouts[method] <= 12_000


def test_the_response_wait_gets_only_what_the_call_leg_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One deadline covers the whole round trip. Bounding each leg by the
    # full timeout separately would let a request that spent 4s getting its
    # handle wait another 10s for the Response -- 14 seconds, asked for 10.
    class Clock:
        def __init__(self) -> None:
            self.now = 100.0

        def __call__(self) -> float:
            return self.now

    clock = Clock()

    class SlowCallConnection(_SilentAfterCreateConnection):
        """Takes 4s to answer Start, then never sends a Response."""

        def call_sync(self, *args: Any, **kwargs: Any) -> FakeReply:
            reply = super().call_sync(*args, **kwargs)
            if args[3] == "Start":
                clock.now += 4.0
            return reply

    # portal.py reads the stdlib clock directly, so this is the one to
    # replace -- and replacing it beats sleeping for the real 4 seconds.
    monkeypatch.setattr(time, "monotonic", clock)
    connection = SlowCallConnection()
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalTimeoutError):
            portal.RemoteDesktopSession.negotiate(connection=connection, timeout=10.0)
    assert FakeMainLoop.pending_timeout_msec == 6_000


def test_a_dbus_reply_timeout_raises_portal_timeout_error() -> None:
    # GDBus reports its own reply timeout as G_IO_ERROR_TIMED_OUT in the
    # g-io-error-quark domain -- not as an org.freedesktop.DBus.Error.*
    # code -- and it means what the nested loop's timeout means: nobody
    # answered in time. So it raises what a caller catches for that.
    class TimingOutConnection(FakeConnection):
        def call_sync(self, *args: Any, **kwargs: Any) -> FakeReply:
            raise FakeGLibError(
                "Timeout was reached", domain="g-io-error-quark", code=24
            )

    connection = TimingOutConnection()
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalTimeoutError) as excinfo:
            portal.RemoteDesktopSession.negotiate(connection=connection, timeout=30.0)
    assert excinfo.value.step == "Get"
