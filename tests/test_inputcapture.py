"""Unit tests for libei.portal's InputCaptureSession.

All D-Bus calls are faked, for the same reason test_portal.py's
RemoteDesktopSession tests are: nothing here can click through a real
consent dialog, and unlike RemoteDesktop, InputCapture additionally cannot
be triggered at all without a user physically crossing a pointer barrier
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

import contextlib
import os
import sys
from typing import Any
from unittest import mock

import pytest

from libei import portal
from test_portal import (
    FakeGLibError,
    FakeMainLoop,
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
    no Request/Response), and `CreateSession` (v1)/`Start`/`GetZones`/
    `SetPointerBarriers` (Request-shaped, exactly like RemoteDesktop's -- the
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

    Both fire on the **portal's** object path, carrying the session handle
    as the signal's first argument, because that is what a real portal
    does -- see `_wait_for_signal`'s docstring for what modelling this
    wrongly hid. `signal_session_handle` overrides that first argument
    alone, so a test can deliver a *different* session's activation to a
    subscriber and prove it is ignored.
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
        signal_session_handle: str | None = None,
        pending_closed: bool = False,
        pending_activated_raw: tuple[Any, ...] | None = None,
        pending_deactivated_raw: tuple[Any, ...] | None = None,
    ) -> None:
        self.session_handle = session_handle
        self.signal_session_handle = signal_session_handle or session_handle
        self.responses = responses or {
            "Start": (0, {"capabilities": 3}),
            # v1 CreateSession is Request-shaped too, and answers with the
            # handle CreateSession2 returns directly.
            "CreateSession": (0, {"session_handle": session_handle}),
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
        self._pending_closed = pending_closed
        # Delivered VERBATIM as the signal's whole parameter tuple, unlike
        # pending_activated, which is an options dict the fake wraps in the
        # correct (session_handle, options) shape -- this is how a test
        # hands the waiter a payload a spec-conforming portal cannot send.
        self._pending_activated_raw = pending_activated_raw
        self._pending_deactivated_raw = pending_deactivated_raw
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
        elif signal == "Activated" and self._pending_activated_raw is not None:
            raw = self._pending_activated_raw
            self._pending_activated_raw = None
            callback(None, None, portal._OBJECT_PATH, None, "Activated", FakeReply(raw))
        elif signal == "Activated" and self._pending_activated is not None:
            payload = self._pending_activated
            self._pending_activated = None
            self._emit_session_signal(callback, "Activated", payload)
        elif signal == "Deactivated" and self._pending_deactivated_raw is not None:
            raw = self._pending_deactivated_raw
            self._pending_deactivated_raw = None
            callback(
                None, None, portal._OBJECT_PATH, None, "Deactivated", FakeReply(raw)
            )
        elif signal == "Deactivated" and self._pending_deactivated is not None:
            payload = self._pending_deactivated
            self._pending_deactivated = None
            self._emit_session_signal(callback, "Deactivated", payload)
        elif signal == "Closed" and self._pending_closed:
            # Closed fires on the session object's own path, not the
            # portal's -- the opposite of Activated/Deactivated above -- so
            # it is only delivered to a subscription made there.
            if path == self.session_handle:
                self._pending_closed = False
                callback(None, None, path, None, "Closed", FakeReply(({},)))
        return self._next_subscription_id

    def _emit_session_signal(
        self, callback: Any, signal: str, payload: dict[str, Any]
    ) -> None:
        callback(
            None,
            None,
            portal._OBJECT_PATH,
            None,
            signal,
            FakeReply((self.signal_session_handle, payload)),
        )

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


@pytest.mark.parametrize("version", [1, 0])
def test_pre_v2_portal_negotiates_through_v1_create_session(version: int) -> None:
    """v1 has no CreateSession2 and no Start -- one call does both jobs.

    version 0 takes the same path: xdg-desktop-portal-gnome 50 registers the
    whole impl interface and never sets the property, so 0 reaches us from a
    portal that speaks v1 perfectly well.
    """
    connection = FakeInputCaptureConnection(version=version)
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
    assert [c[0] for c in connection.calls] == [
        "Get",
        "CreateSession",
        "ConnectToEIS",
    ]
    assert session.session_handle == connection.session_handle
    assert session.eis_fd == connection.fd_responses["ConnectToEIS"]
    # v1 can never issue one: restore_token is a v2 addition to Start.
    assert session.restore_token is None
    # capabilities ride on CreateSession here, not on Start.
    options = next(v for v in connection.calls[1][1] if isinstance(v, dict))
    assert options["capabilities"].value == int(portal._ALL_DEVICE_TYPES)


def test_v1_portal_refuses_persistence_rather_than_ignoring_it() -> None:
    """persist_mode/restore_token are v2 additions to a method v1 lacks."""
    connection = FakeInputCaptureConnection(version=1)
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalVersionError, match="cannot persist"):
            portal.InputCaptureSession.negotiate(
                connection=connection, persist_mode=portal.PersistMode.UNTIL_REVOKED
            )
    # Refused before creating anything that would need cleaning up.
    assert connection.calls == [
        ("Get", ("org.freedesktop.portal.InputCapture", "version"))
    ]


def test_declined_v1_create_session_raises_denied_error() -> None:
    """v1 raises its consent dialog in CreateSession, so that is the step."""
    connection = FakeInputCaptureConnection(
        version=1,
        responses={"CreateSession": (1, {})},  # 1 == user cancelled
    )
    with install_fake_gi(connection):
        with pytest.raises(portal.PortalDeniedError) as excinfo:
            portal.InputCaptureSession.negotiate(connection=connection)
    assert excinfo.value.step == "CreateSession"
    # No session handle came back, so there is nothing to Close.
    assert [c[0] for c in connection.calls] == ["Get", "CreateSession"]


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
    # Start blocks on a user answering the dialog; Ctrl-C during that wait
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


def test_partially_refused_barriers_are_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A partial refusal is returned but nothing forces a caller to look at
    # it, and the symptom -- one edge that never triggers -- is silent. The
    # log line is what makes it visible without reading the return value.
    connection = FakeInputCaptureConnection(
        responses={"SetPointerBarriers": (0, {"failed_barriers": [2]})}
    )
    with install_fake_gi(connection), caplog.at_level("DEBUG", logger="libei.portal"):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.set_pointer_barriers(
            [(1, 0, 0, 1919, 0), (2, 1920, 0, 1920, 1079)], zone_set=7
        )
    assert "refused: [2]" in caplog.text


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


def test_a_malformed_activated_payload_raises_a_typed_error() -> None:
    # An empty payload cannot even be matched to a session, so it is broken
    # for every possible waiter. Before the shape checks this was an
    # IndexError *inside* the GLib callback -- which PyGI prints and
    # swallows, so the wait hung (timeout=None) or timed out, both
    # indistinguishable from a compositor that never fired.
    connection = FakeInputCaptureConnection(pending_activated_raw=())
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError) as excinfo:
            session.wait_for_activation(timeout=1.0)
    # `.step` is the attribute every sibling portal error raised from the
    # same call site carries, so a caller that catches PortalError and reads
    # `.step` works for this one too (PortalTimeoutError/SessionClosedError).
    assert excinfo.value.step == "Activated"
    assert excinfo.value.payload == ()
    # A shape fault, so no value-level detail.
    assert excinfo.value.detail is None


def test_a_malformed_deactivated_payload_raises_a_typed_error() -> None:
    # Same shape check, the other signal: wait_for_deactivation goes through
    # the identical _wait_for_signal, and nothing else exercises its
    # malformed branch (the timeout/closed paths test both signals, so this
    # one should too).
    connection = FakeInputCaptureConnection(pending_deactivated_raw=("/session/1",))
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError) as excinfo:
            session.wait_for_deactivation(timeout=1.0)
    assert excinfo.value.step == "Deactivated"
    assert excinfo.value.payload == ("/session/1",)


def test_a_non_integer_activation_id_raises_a_typed_error() -> None:
    # Shape-valid payload (a dict), but activation_id is not the `u` the
    # spec promises -- this passes _wait_for_signal's shape check and used
    # to crash the caller with ValueError on int(). It is now the same
    # typed error as a shape violation, and carries a value-level detail so
    # its message does not misdiagnose a correct shape.
    connection = FakeInputCaptureConnection(
        pending_activated={"activation_id": "not-a-number"}
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError) as excinfo:
            session.wait_for_activation(timeout=1.0)
    assert excinfo.value.step == "Activated"
    assert excinfo.value.detail is not None
    assert "shape" not in str(excinfo.value)


def test_a_non_iterable_cursor_position_raises_a_typed_error() -> None:
    # cursor_position is `(dd)`; a scalar here would crash the caller with
    # TypeError unpacking it. Guarded into PortalProtocolError too.
    connection = FakeInputCaptureConnection(
        pending_activated={"activation_id": 1, "cursor_position": 5}
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError):
            session.wait_for_activation(timeout=1.0)


def test_a_wrong_arity_cursor_position_raises_a_typed_error() -> None:
    # (dd) is exactly two: tuple("ab") would not raise, so the old guard let
    # a wrong-arity or stringly-typed cursor through as a bad Activation.
    # The explicit two-element unpack rejects it.
    for bad in ("ab", (1.0,), (1.0, 2.0, 3.0)):
        connection = FakeInputCaptureConnection(
            pending_activated={"activation_id": 1, "cursor_position": bad}
        )
        with install_fake_gi(connection):
            session = portal.InputCaptureSession.negotiate(connection=connection)
            with pytest.raises(portal.PortalProtocolError):
                session.wait_for_activation(timeout=1.0)


def test_a_non_numeric_cursor_component_raises_a_typed_error() -> None:
    connection = FakeInputCaptureConnection(
        pending_activated={"activation_id": 1, "cursor_position": ("x", "y")}
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError):
            session.wait_for_activation(timeout=1.0)


def test_a_non_integer_barrier_id_raises_a_typed_error() -> None:
    # barrier_id is a `u` the caller compares against the ids it armed; a
    # string here used to pass straight through into a type-violating
    # Activation that mismatched silently or crashed on arithmetic later.
    connection = FakeInputCaptureConnection(
        pending_activated={"activation_id": 1, "barrier_id": "b-3"}
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError):
            session.wait_for_activation(timeout=1.0)


def test_an_infinite_activation_id_raises_a_typed_error() -> None:
    # int(float('inf')) raises OverflowError, which the old (ValueError,
    # TypeError) guard missed -- it would have escaped a caller's
    # `except PortalError`.
    connection = FakeInputCaptureConnection(
        pending_activated={"activation_id": float("inf")}
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError):
            session.wait_for_activation(timeout=1.0)


def test_a_non_integer_deactivation_id_raises_a_typed_error() -> None:
    connection = FakeInputCaptureConnection(
        pending_deactivated={"activation_id": "not-a-number"}
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError) as excinfo:
            session.wait_for_deactivation(timeout=1.0)
    assert excinfo.value.step == "Deactivated"


def test_an_options_payload_that_is_not_a_dict_raises_a_typed_error() -> None:
    # The right session's signal, but options is not the a{sv} the spec
    # promises -- before the shape checks this surfaced wherever the caller
    # happened to poke the payload (an AttributeError on .get here).
    connection = FakeInputCaptureConnection(pending_activated_raw=("/session/1", 42))
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError) as excinfo:
            session.wait_for_activation(timeout=1.0)
    assert "Activated" in str(excinfo.value)
    assert excinfo.value.payload == ("/session/1", 42)


def test_a_non_string_session_handle_raises_a_typed_error() -> None:
    connection = FakeInputCaptureConnection(pending_activated_raw=(7, {}))
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError):
            session.wait_for_activation(timeout=1.0)


def test_a_payload_with_an_extra_trailing_field_raises_a_typed_error() -> None:
    # The right session and a valid options dict, but a third field the
    # spec's (o, a{sv}) shape has no room for -- pins the `len(args) != 2`
    # clause on its own, which the not-a-dict case above does not reach.
    connection = FakeInputCaptureConnection(
        pending_activated_raw=("/session/1", {}, "extra")
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalProtocolError) as excinfo:
            session.wait_for_activation(timeout=1.0)
    assert excinfo.value.payload == ("/session/1", {}, "extra")


def test_wait_for_activation_times_out_with_no_signal() -> None:
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError) as excinfo:
            session.wait_for_activation(timeout=0.01)
    assert excinfo.value.step == "Activated"
    assert excinfo.value.timeout == 0.01


def test_wait_for_activation_does_not_double_remove_the_timeout_source_on_timeout() -> (
    None
):
    # Live-caught 2026-09-08: GLib.timeout_add's callback returning False
    # (as on_timeout does) already deregisters the source, so calling
    # GLib.source_remove on it again logs a real GLib warning ("Source ID N
    # was not found when attempting to remove it"). The fake source_remove
    # in install_fake_gi doesn't model that -- it just clears
    # pending_timeout either way -- so only asserting the call itself was
    # never made catches this; a fake that raised on double-removal would
    # have caught it long ago, but reproducing that faithfully needs real
    # GLib, which nothing here has.
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        glib = sys.modules["gi.repository.GLib"]
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with mock.patch.object(glib, "source_remove") as source_remove:
            with pytest.raises(portal.PortalTimeoutError):
                session.wait_for_activation(timeout=0.01)
    source_remove.assert_not_called()


def test_a_signal_and_the_deadline_in_one_poll_do_not_double_remove_the_source() -> (
    None
):
    # The harder half of the same bug: GLib dispatches every source ready in
    # one iteration, so a real Activated can land in the *same poll* as the
    # timeout deadline. on_signal records the activation and quits; on_timeout
    # still runs, sees the wait already settled, and returns False -- which
    # GLib treats as "remove this source". The finally block must not remove
    # it a second time. Keyed on `timed_out` (which this early return leaves
    # False) rather than on "did on_timeout fire", it did -- tripping the
    # "Source ID N was not found" critical that is fatal under
    # G_DEBUG=fatal-criticals. The fake delivers the signal in the same run()
    # as the timeout via inject_before_timeout to model the race.
    connection = FakeInputCaptureConnection()  # nothing fires on subscribe

    def deliver_activation() -> None:
        key = (portal._INPUT_CAPTURE, "Activated", portal._OBJECT_PATH)
        connection._subscriptions[key](
            None,
            None,
            portal._OBJECT_PATH,
            None,
            "Activated",
            FakeReply((connection.session_handle, {"activation_id": 7})),
        )

    with install_fake_gi(connection):
        glib = sys.modules["gi.repository.GLib"]
        session = portal.InputCaptureSession.negotiate(connection=connection)
        FakeMainLoop.inject_before_timeout = deliver_activation
        with mock.patch.object(glib, "source_remove") as source_remove:
            activation = session.wait_for_activation(timeout=5.0)
    # The delivered signal wins -- not a timeout -- and the source the firing
    # on_timeout already removed is not removed again.
    assert activation.activation_id == 7
    source_remove.assert_not_called()


def test_wait_for_activation_subscribes_on_the_portal_not_the_session() -> None:
    # The bug that made this whole feature look like a compositor gap: the
    # portal emits Activated on its own object, never on the session's, so
    # a subscription filtered to the session handle hears nothing at all --
    # identical, from the caller's side, to a compositor that never fires.
    # See _wait_for_signal's docstring.
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError):
            session.wait_for_activation(timeout=0.01)
    subscribed = {
        key[2] for key in connection.signal_subscriptions if key[1] == "Activated"
    }
    assert subscribed == {portal._OBJECT_PATH}
    assert connection.session_handle not in subscribed


def test_wait_for_deactivation_subscribes_on_the_portal_not_the_session() -> None:
    # Same fix, second call site -- the original bug was in both.
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError):
            session.wait_for_deactivation(timeout=0.01)
    subscribed = {
        key[2] for key in connection.signal_subscriptions if key[1] == "Deactivated"
    }
    assert subscribed == {portal._OBJECT_PATH}


def test_wait_for_activation_ignores_another_sessions_activation() -> None:
    # Subscribing on the shared portal object means hearing every session's
    # signals, so the session handle in the payload is now what tells them
    # apart. A foreign one must leave the wait running -- here, until it
    # times out.
    connection = FakeInputCaptureConnection(
        pending_activated={"activation_id": 5, "cursor_position": (1.0, 2.0)},
        signal_session_handle="/session/someone-else",
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError):
            session.wait_for_activation(timeout=0.01)


def test_ignoring_another_sessions_activation_says_so_at_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The point of the log line, not incidental coverage: dropping a signal
    # here is invisible from outside and looks identical to a compositor
    # that never fired one -- which is the mistake this whole code path is
    # being fixed for. A silent filter would just move that trap.
    connection = FakeInputCaptureConnection(
        pending_activated={"activation_id": 5},
        signal_session_handle="/session/someone-else",
    )
    with install_fake_gi(connection), caplog.at_level("DEBUG", logger="libei.portal"):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError):
            session.wait_for_activation(timeout=0.01)
    assert "/session/someone-else" in caplog.text
    assert connection.session_handle in caplog.text


def test_wait_for_deactivation_ignores_another_sessions_deactivation() -> None:
    connection = FakeInputCaptureConnection(
        pending_deactivated={"activation_id": 5},
        signal_session_handle="/session/someone-else",
    )
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError):
            session.wait_for_deactivation(timeout=0.01)


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
        with contextlib.suppress(OSError):
            os.close(read_fd)
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


# -- the session Closed signal ------------------------------------------------


def test_a_closed_session_ends_wait_for_activation_rather_than_hanging() -> None:
    # The compositor-disconnect case: Session.Closed fires when the portal
    # or compositor ends the session from its side. Before the Closed
    # subscription existed, a wait with timeout=None on such a session
    # blocked forever -- indistinguishable from a barrier nobody had
    # crossed yet.
    connection = FakeInputCaptureConnection(pending_closed=True)
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalSessionClosedError) as excinfo:
            session.wait_for_activation(timeout=None)
    assert excinfo.value.step == "Activated"


def test_a_closed_session_ends_wait_for_deactivation_too() -> None:
    connection = FakeInputCaptureConnection(pending_closed=True)
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalSessionClosedError) as excinfo:
            session.wait_for_deactivation(timeout=None)
    assert excinfo.value.step == "Deactivated"


def test_closed_is_subscribed_on_the_session_path_not_the_portal() -> None:
    # The mirror image of the Activated-path bug: Closed is the one signal
    # that really does fire on the session object's own path, so the
    # subscription has to go there.
    connection = FakeInputCaptureConnection()
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalTimeoutError):
            session.wait_for_activation(timeout=0.01)
    subscribed = {
        key[2] for key in connection.signal_subscriptions if key[1] == "Closed"
    }
    assert subscribed == {connection.session_handle}


def test_every_wait_subscription_is_unsubscribed_again() -> None:
    # Both the signal's own subscription and the Closed one: a leak here
    # means a long-running capture loop accumulates one dead subscription
    # per activation cycle.
    connection = FakeInputCaptureConnection(pending_activated={"activation_id": 1})
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        session.wait_for_activation(timeout=1.0)
    live = [k for k in connection._subscriptions if k[1] in ("Activated", "Closed")]
    assert live == []


def test_a_session_closed_mid_wait_still_cleans_up_its_subscriptions() -> None:
    connection = FakeInputCaptureConnection(pending_closed=True)
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        with pytest.raises(portal.PortalSessionClosedError):
            session.wait_for_activation(timeout=None)
    live = [k for k in connection._subscriptions if k[1] in ("Activated", "Closed")]
    assert live == []


def test_repeated_activation_cycles_each_get_their_own_signal() -> None:
    # A capture loop waits, releases, waits again -- each wait is its own
    # subscription, delivered its own payload, with nothing carried over.
    connection = FakeInputCaptureConnection(pending_activated={"activation_id": 1})
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        first = session.wait_for_activation(timeout=1.0)
        connection._pending_activated = {"activation_id": 2}
        second = session.wait_for_activation(timeout=1.0)
    assert (first.activation_id, second.activation_id) == (1, 2)


def test_an_activation_payload_without_an_id_defaults_to_zero() -> None:
    # The spec says activation_id is always sent; a portal that omits it
    # anyway gets the same 0 a compositor that "could not determine" sends,
    # rather than a KeyError mid-wait.
    connection = FakeInputCaptureConnection(pending_activated={})
    with install_fake_gi(connection):
        session = portal.InputCaptureSession.negotiate(connection=connection)
        activation = session.wait_for_activation(timeout=1.0)
    assert activation == portal.Activation(
        activation_id=0, cursor_position=None, barrier_id=None
    )
