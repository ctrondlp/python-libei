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
    """Stands in for `GLib.Error`, which every GDBus failure arrives as."""


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
        return FakeReply((0,)), FakeUnixFDList(self.fd_responses[method])


def install_fake_gi(connection: FakeConnection | None = None) -> Any:
    gi = types.ModuleType("gi")
    gi.require_version = lambda *a, **kw: None  # type: ignore[attr-defined]
    repository = types.ModuleType("gi.repository")

    Gio = types.ModuleType("gi.repository.Gio")
    Gio.BusType = types.SimpleNamespace(SESSION=1)  # type: ignore[attr-defined]
    Gio.DBusCallFlags = types.SimpleNamespace(NONE=0)  # type: ignore[attr-defined]
    Gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)  # type: ignore[attr-defined]
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
    def timeout_add(_ms: int, callback: Any) -> int:
        FakeMainLoop.pending_timeout = callback
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
