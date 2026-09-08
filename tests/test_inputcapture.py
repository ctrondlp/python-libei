"""Unit tests for libei.portal's InputCaptureSession.

All D-Bus calls are faked, for the same reason test_portal.py's
RemoteDesktopSession tests are: nothing here can click through a real
consent dialog, and unlike RemoteDesktop, InputCapture additionally cannot
be triggered at all without a human physically crossing a pointer barrier
-- there is no synthetic way to fire Activated for real, which is exactly
why negotiate() and the barrier/activation methods are unit-tested here but
have never been run against a live portal. See InputCaptureSession's own
class docstring.

Reuses test_portal.py's generic fakes (FakeVariant, FakeReply,
FakeUnixFDList, FakeGLibError, FakeMainLoop, install_fake_gi, _fresh_fd)
rather than duplicating them -- none of those are RemoteDesktop-specific.
FakeConnection is not reused: its call_sync assumes every non-Get call is
Request-shaped with options as the last positional argument, which
CreateSession2 (a plain call) and SetPointerBarriers (options is not last)
both break. FakeInputCaptureConnection below models InputCapture's actual
shapes instead.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest import mock

import pytest

from libei import portal
from test_portal import (
    FakeGLibError,
    FakeReply,
    FakeUnixFDList,
    FakeVariant,
    _fresh_fd,
    install_fake_gi,
)

__all__: list[str] = []


class FakeInputCaptureConnection:
    """Stands in for a Gio.DBusConnection bound to the session bus.

    Three call shapes, told apart by method name: `Get` (the version
    property), `CreateSession2`/`Enable`/`Disable`/`Release` (plain calls,
    no Request/Response), and `Start`/`GetZones`/`SetPointerBarriers`
    (Request-shaped, exactly like RemoteDesktop's own calls -- the
    `handle_token` lives in whichever positional argument is a dict, found
    by type rather than by a fixed index, since SetPointerBarriers's
    options argument is not last).

    `Activated`/`Deactivated` are ordinary signals, not Request/Response --
    nothing calls them into being. `pending_activated`/`pending_deactivated`
    simulate the compositor already having decided to fire one: delivered
    the moment something subscribes to the matching (interface, signal,
    path), the same "fires on subscribe" trick test_portal.py's own
    FakeConnection uses to prove the raceless subscribe-before-call pattern
    -- here there is no call to race against, only illustrating that a
    subscription already in place when the signal fires is what receives
    it. A test that wants the *timeout* path instead simply leaves both at
    their default of None; FakeMainLoop.pending_timeout picks up from there
    exactly as it does for every other timeout test in this package.
    """

    def __init__(
        self,
        responses: dict[str, tuple[int, dict[str, Any]]] | None = None,
        version: int = 2,
        fd_responses: dict[str, int] | None = None,
        unique_name: str = ":1.99",
        session_handle: str = "/session/1",
        pending_activated: dict[str, Any] | None = None,
        pending_deactivated: dict[str, Any] | None = None,
    ) -> None:
        self.session_handle = session_handle
        self.responses = responses or {
            "Start": (0, {"capabilities": 3}),
            "GetZones": (0, {"zone_set": 1, "zones": [(1920, 1080, 0, 0)]}),
            "SetPointerBarriers": (0, {"failed_barriers": []}),
        }
        self.version = version
        self.fd_responses = fd_responses or {"ConnectToEIS": _fresh_fd()}
        self.calls: list[tuple[str, Any]] = []
        self.call_targets: list[tuple[str, Any, Any, Any]] = []
        self._unique_name = unique_name
        self._subscriptions: dict[tuple[str, str, str], Any] = {}
        self._subscription_paths: dict[int, tuple[str, str, str]] = {}
        self._next_subscription_id = 0
        self._pending_reply: tuple[str, int, dict[str, Any]] | None = None
        self._pending_activated = pending_activated
        self._pending_deactivated = pending_deactivated
        # A permanent log, unlike _subscriptions: signal_unsubscribe removes
        # an entry from that (correctly -- see its own docstring), so a test
        # checking *what was subscribed to* after the fact needs a record
        # that survives cleanup.
        self.signal_subscriptions: list[tuple[str, str, str]] = []

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
        self.calls.append((method, None if parameters is None else parameters.value))
        self.call_targets.append((method, bus_name, object_path, timeout))
        if parameters is None:
            return FakeReply(())  # Session.Close(), which takes no arguments
        if method == "Get":
            return FakeReply((self.version,))
        if method == "CreateSession2":
            return FakeReply(({"session_handle": self.session_handle},))
        if method in ("Enable", "Disable", "Release"):
            return FakeReply(())
        # Request-shaped: Start, GetZones, SetPointerBarriers. The options
        # dict is found by type, not by position -- see the class docstring.
        options = next(v for v in parameters.value if isinstance(v, dict))
        token = options["handle_token"].value
        sender = self._escaped_sender()
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        code, results = self.responses.get(method, (0, {}))
        key = (portal._REQUEST_INTERFACE, "Response", path)
        callback = self._subscriptions.get(key)
        if callback is not None:
            callback(None, None, path, None, "Response", FakeReply((code, results)))
        else:
            self._pending_reply = (path, code, results)
        return FakeReply((path,))

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
        key = (iface, signal, path)
        self.signal_subscriptions.append(key)
        self._subscriptions[key] = callback
        self._next_subscription_id += 1
        self._subscription_paths[self._next_subscription_id] = key
        if iface == portal._REQUEST_INTERFACE:
            pending = self._pending_reply
            if pending is not None and pending[0] == path:
                self._pending_reply = None
                _path, code, results = pending
                callback(None, None, path, None, "Response", FakeReply((code, results)))
        elif signal == "Activated" and self._pending_activated is not None:
            payload = self._pending_activated
            self._pending_activated = None
            callback(None, None, path, None, "Activated", FakeReply((path, payload)))
        elif signal == "Deactivated" and self._pending_deactivated is not None:
            payload = self._pending_deactivated
            self._pending_deactivated = None
            callback(None, None, path, None, "Deactivated", FakeReply((path, payload)))
        return self._next_subscription_id

    def signal_unsubscribe(self, subscription_id: int) -> None:
        key = self._subscription_paths.pop(subscription_id, None)
        if key is not None:
            self._subscriptions.pop(key, None)

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


# -- negotiate() -------------------------------------------------------------


def test_successful_negotiation_calls_every_step_in_order() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
    methods = [c[0] for c in connection.calls]
    assert methods == ["Get", "CreateSession2", "Start", "ConnectToEIS"]
    assert session.eis_fd == connection.fd_responses["ConnectToEIS"]
    assert session.session_handle == connection.session_handle


def test_old_input_capture_version_is_refused() -> None:
    connection = FakeInputCaptureConnection(version=1)
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalVersionError):
            portal.InputCaptureSession.negotiate(connection=connection)
    # Refused before ever calling CreateSession2.
    assert connection.calls == [
        ("Get", ("org.freedesktop.portal.InputCapture", "version"))
    ]


def test_a_missing_session_handle_raises_portal_error() -> None:
    class NoHandleConnection(FakeInputCaptureConnection):
        def call_sync(self, *args: Any, **kwargs: Any) -> FakeReply:
            if args[3] == "CreateSession2":
                return FakeReply(({},))  # approved, but nothing to address
            return super().call_sync(*args, **kwargs)

    connection = NoHandleConnection()
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalError, match="session_handle"):
            portal.InputCaptureSession.negotiate(connection=connection)


def test_declined_start_raises_portal_denied_error() -> None:
    connection = FakeInputCaptureConnection(
        responses={"Start": (1, {})}  # 1 == user cancelled, per the portal spec
    )
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalDeniedError) as excinfo:
            portal.InputCaptureSession.negotiate(connection=connection)
    assert excinfo.value.step == "Start"


def test_declined_start_closes_the_portal_session() -> None:
    connection = FakeInputCaptureConnection(responses={"Start": (1, {})})
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalDeniedError):
            portal.InputCaptureSession.negotiate(connection=connection)
    assert ("Close", None) in [(c[0], c[1]) for c in connection.calls]


def test_capabilities_bitmask_is_forwarded_to_start() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        portal.InputCaptureSession.negotiate(
            connection=connection, capabilities=portal.DeviceType.POINTER
        )
    start = next(c for c in connection.calls if c[0] == "Start")
    _session_handle, _parent, options = start[1]
    assert options["capabilities"].value == int(portal.DeviceType.POINTER)


def test_default_capabilities_asks_for_every_type_not_zero() -> None:
    # DeviceType.ALL_DEVICES is 0, which the portal would read as "no
    # capabilities" rather than "everything" -- see _ALL_DEVICE_TYPES.
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        portal.InputCaptureSession.negotiate(connection=connection)
    start = next(c for c in connection.calls if c[0] == "Start")
    _session_handle, _parent, options = start[1]
    assert options["capabilities"].value == int(portal._ALL_DEVICE_TYPES)
    assert options["capabilities"].value != 0


def test_no_persist_options_are_sent_by_default() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
    start = next(c for c in connection.calls if c[0] == "Start")
    _session_handle, _parent, options = start[1]
    assert "persist_mode" not in options
    assert "restore_token" not in options
    assert session.restore_token is None


def test_persist_mode_and_restore_token_round_trip() -> None:
    connection = FakeInputCaptureConnection(
        responses={"Start": (0, {"capabilities": 3, "restore_token": "tok-next"})}
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(
            connection=connection,
            restore_token="tok-old",
            persist_mode=portal.PersistMode.UNTIL_REVOKED,
        )
    start = next(c for c in connection.calls if c[0] == "Start")
    _session_handle, _parent, options = start[1]
    assert options["persist_mode"].value == 2
    assert options["restore_token"].value == "tok-old"
    assert session.restore_token == "tok-next"


def test_restore_token_without_persist_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="persist_mode"):
        portal.InputCaptureSession.negotiate(
            connection=FakeInputCaptureConnection(), restore_token="tok"
        )


def test_negotiate_without_pygobject_raises_portal_error() -> None:
    # Must force the import to fail, not merely omit install_fake_gi(): on
    # any system where PyGObject really is installed (this one included),
    # omitting it does not simulate "not installed" -- it reaches the real
    # session bus and the real portal instead. Confirmed the hard way: an
    # earlier, broken version of this test that skipped this patch raised a
    # real consent dialog on the developer's own desktop. No barriers were
    # ever set and Enable() was never reached, so nothing was actually
    # captured, but the lesson stands regardless.
    with mock.patch.dict(sys.modules, {"gi": None}):
        with pytest.raises(portal.PortalError, match="PyGObject"):
            portal.InputCaptureSession.negotiate()


def test_an_interrupted_consent_dialog_closes_the_portal_session() -> None:
    # Start blocks on a human answering the dialog; Ctrl-C during that wait
    # is a routine way out, and it strands an approved session exactly like
    # a decline does unless this closes it on the way out too.
    class RaisingConnection(FakeInputCaptureConnection):
        def call_sync(self, *args: Any, **kwargs: Any) -> FakeReply:
            method = args[3]
            if method == "Start":
                raise KeyboardInterrupt
            return super().call_sync(*args, **kwargs)

    connection = RaisingConnection()
    with install_fake_gi(connection):
        with pytest.raises(KeyboardInterrupt):
            portal.InputCaptureSession.negotiate(connection=connection)
    assert ("Close", None) in [(c[0], c[1]) for c in connection.calls]


# -- zones() / set_pointer_barriers() ----------------------------------------


def test_zones_returns_width_height_x_y_in_wire_order() -> None:
    connection = FakeInputCaptureConnection(
        responses={
            "GetZones": (
                0,
                {"zone_set": 7, "zones": [(1920, 1080, 0, 0), (1280, 1024, 1920, 0)]},
            )
        }
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        zone_set, zones = session.zones()
    assert zone_set == 7
    assert zones == [(1920, 1080, 0, 0), (1280, 1024, 1920, 0)]


def test_a_declined_get_zones_raises_portal_denied_error() -> None:
    connection = FakeInputCaptureConnection(responses={"GetZones": (1, {})})
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalDeniedError) as excinfo:
            session.zones()
    assert excinfo.value.step == "GetZones"


def test_set_pointer_barriers_sends_barrier_id_and_position() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.set_pointer_barriers([(1, 0, 0, 1919, 0)], zone_set=7)
    call = next(c for c in connection.calls if c[0] == "SetPointerBarriers")
    session_handle, _options, barriers, zone_set = call[1]
    assert session_handle == connection.session_handle
    assert zone_set == 7
    (barrier,) = barriers
    assert barrier["barrier_id"].value == 1
    assert barrier["position"].value == (0, 0, 1919, 0)


def test_set_pointer_barriers_reports_the_failed_ones() -> None:
    connection = FakeInputCaptureConnection(
        responses={"SetPointerBarriers": (0, {"failed_barriers": [2]})}
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        failed = session.set_pointer_barriers(
            [(1, 0, 0, 1919, 0), (2, 1920, 0, 1920, 1079)], zone_set=7
        )
    assert failed == [2]


def test_an_empty_barrier_list_clears_every_barrier() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.set_pointer_barriers([], zone_set=7)
    call = next(c for c in connection.calls if c[0] == "SetPointerBarriers")
    _session_handle, _options, barriers, _zone_set = call[1]
    assert barriers == []


# -- enable() / disable() / release() ----------------------------------------


def test_enable_and_disable_are_plain_calls() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.enable()
        session.disable()
    methods = [c[0] for c in connection.calls]
    assert methods[-2:] == ["Enable", "Disable"]
    enable_call = next(c for c in connection.calls if c[0] == "Enable")
    assert enable_call[1] == (connection.session_handle, {})


def test_release_sends_the_activation_id() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.release(activation_id=5)
    call = next(c for c in connection.calls if c[0] == "Release")
    session_handle, options = call[1]
    assert session_handle == connection.session_handle
    assert options["activation_id"].value == 5
    assert "cursor_position" not in options


def test_release_forwards_an_optional_cursor_position() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.release(activation_id=5, cursor_position=(12.5, 34.5))
    call = next(c for c in connection.calls if c[0] == "Release")
    _session_handle, options = call[1]
    assert options["cursor_position"].value == (12.5, 34.5)


# -- wait_for_activation() / wait_for_deactivation() -------------------------


def test_wait_for_activation_returns_the_full_payload() -> None:
    connection = FakeInputCaptureConnection(
        pending_activated={
            "activation_id": 5,
            "cursor_position": (100.5, 200.25),
            "barrier_id": 2,
        }
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        activation = session.wait_for_activation(timeout=1.0)
    assert activation == portal.Activation(
        activation_id=5, cursor_position=(100.5, 200.25), barrier_id=2
    )


def test_wait_for_activation_tolerates_a_missing_barrier_id() -> None:
    # "If the id is missing, the input capture was not triggered by a
    # pointer barrier" -- per the portal spec.
    connection = FakeInputCaptureConnection(
        pending_activated={"activation_id": 9, "cursor_position": (1.0, 2.0)}
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        activation = session.wait_for_activation(timeout=1.0)
    assert activation.barrier_id is None


def test_wait_for_activation_times_out_with_no_signal() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError) as excinfo:
            session.wait_for_activation(timeout=0.01)
    assert excinfo.value.step == "Activated"
    assert excinfo.value.timeout == 0.01


def test_wait_for_activation_subscribes_on_this_sessions_own_path() -> None:
    # A caller managing several sessions on one MainContext must only ever
    # hear about its own session's activation, not another's.
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError):
            session.wait_for_activation(timeout=0.01)
    subscribed_paths = {key[2] for key in connection.signal_subscriptions}
    assert connection.session_handle in subscribed_paths


def test_wait_for_deactivation_returns_the_activation_id() -> None:
    connection = FakeInputCaptureConnection(pending_deactivated={"activation_id": 5})
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        assert session.wait_for_deactivation(timeout=1.0) == 5


def test_wait_for_deactivation_times_out_with_no_signal() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError) as excinfo:
            session.wait_for_deactivation(timeout=0.01)
    assert excinfo.value.step == "Deactivated"


# -- close() / eis_fd / session_handle ----------------------------------------


def test_close_closes_an_unclaimed_eis_fd() -> None:
    read_fd, write_fd = os.pipe()
    connection = FakeInputCaptureConnection(fd_responses={"ConnectToEIS": read_fd})
    try:
        with install_fake_gi(connection):
            session = portal.InputCaptureSession.negotiate(connection=connection)
            session.close()
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
        os.close(write_fd)


def test_close_leaves_a_claimed_eis_fd_alone() -> None:
    read_fd, write_fd = os.pipe()
    connection = FakeInputCaptureConnection(fd_responses={"ConnectToEIS": read_fd})
    try:
        with install_fake_gi(connection):
            session = portal.InputCaptureSession.negotiate(connection=connection)
            assert session.eis_fd == read_fd
            session.close()
        os.fstat(read_fd)  # still open -- proves close() left it alone
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_close_ends_the_portal_session() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.close()
    assert ("Close", None) in [(c[0], c[1]) for c in connection.calls]


def test_close_is_idempotent() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.close()
        session.close()
    closes = [c for c in connection.calls if c[0] == "Close"]
    assert len(closes) == 1


def test_context_manager_closes_on_exit() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        with portal.InputCaptureSession.negotiate(connection=connection) as session:
            assert session.restore_token is None
        methods = [c[0] for c in connection.calls]
    assert "Close" in methods


def test_eis_fd_after_close_raises() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.close()
        with pytest.raises(portal.PortalError, match="closed"):
            _ = session.eis_fd


def test_session_handle_after_close_raises() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.close()
        with pytest.raises(portal.PortalError, match="closed"):
            _ = session.session_handle


def test_a_failed_session_close_does_not_propagate() -> None:
    class CloseFailsConnection(FakeInputCaptureConnection):
        def call_sync(self, *args: Any, **kwargs: Any) -> FakeReply:
            method = args[3]
            if method == "Close":
                raise FakeGLibError("the portal is gone")
            return super().call_sync(*args, **kwargs)

    connection = CloseFailsConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.close()  # must not raise
