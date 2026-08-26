"""Unit tests for libei.oeffis's event-state machine.

All calls monkeypatched -- there is deliberately no live-portal integration
test here, since driving a real xdg-desktop-portal RemoteDesktop session
needs an interactive consent dialog nothing here can click through. Manually
verified working against a real portal on 2026-08-25 (see the README's
Troubleshooting section and docs/vs-snegg.md), but manual verification
isn't automated coverage -- treat the portal path as the least
*automatically* verified part of this package. The state machine tested
here is what *is* verifiable without a live desktop session.
"""

from __future__ import annotations

import gc
import os

import pytest

from libei import _capi, oeffis


@pytest.fixture(autouse=True)
def _no_real_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_capi.liboeffis, "new", lambda userdata: 0x1)
    monkeypatch.setattr(_capi.liboeffis, "unref", lambda p: None)
    monkeypatch.setattr(_capi.liboeffis, "get_fd", lambda p: -1)


def make_session(monkeypatch: pytest.MonkeyPatch, events: list[int]) -> oeffis.Oeffis:
    queue = list(events) + [0]  # NONE terminates each dispatch() call

    def fake_get_event(pointer: int) -> int:
        return queue.pop(0) if queue else 0

    monkeypatch.setattr(_capi.liboeffis, "dispatch", lambda p: None)
    monkeypatch.setattr(_capi.liboeffis, "get_event", fake_get_event)
    monkeypatch.setattr(_capi.liboeffis, "get_eis_fd", lambda p: 99)
    monkeypatch.setattr(_capi.liboeffis, "get_error_message", lambda p: b"boom")
    return oeffis.Oeffis()


def test_dispatch_returns_false_when_no_event_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session(monkeypatch, events=[])
    assert session.dispatch() is False


def test_dispatch_returns_true_and_exposes_eis_fd_on_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session(monkeypatch, events=[oeffis._EventType.CONNECTED_TO_EIS])
    assert session.dispatch() is True
    assert session.eis_fd == 99


def test_eis_fd_raises_before_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session(monkeypatch, events=[])
    session.dispatch()
    with pytest.raises(oeffis.DisconnectedError):
        _ = session.eis_fd


def test_dispatch_raises_disconnected_error_on_disconnect_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session(monkeypatch, events=[oeffis._EventType.DISCONNECTED])
    with pytest.raises(oeffis.DisconnectedError, match="boom"):
        session.dispatch()


def test_dispatch_raises_session_closed_error_on_closed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session(monkeypatch, events=[oeffis._EventType.CLOSED])
    with pytest.raises(oeffis.SessionClosedError):
        session.dispatch()


def test_dispatch_keeps_raising_after_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session(monkeypatch, events=[oeffis._EventType.DISCONNECTED])
    with pytest.raises(oeffis.DisconnectedError):
        session.dispatch()
    # A second call must not silently return False -- the session is
    # permanently dead once disconnected.
    with pytest.raises(oeffis.DisconnectedError):
        session.dispatch()


def test_session_closed_error_has_a_fixed_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session(monkeypatch, events=[oeffis._EventType.CLOSED])
    with pytest.raises(oeffis.SessionClosedError, match="Session closed"):
        session.dispatch()


def test_del_closes_eis_fd_if_never_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    # oeffis_get_eis_fd() docs: the caller owns the dup()'d fd it returns.
    # If a session connects but its `eis_fd` property is never read (so it
    # never gets handed to a Sender, which would otherwise own and close
    # it), nothing else will ever close it -- __del__ must, or it leaks.
    # Uses a real pipe fd, not a dummy number, so os.fstat() can actually
    # prove whether it got closed.
    read_fd, write_fd = os.pipe()
    queue = [oeffis._EventType.CONNECTED_TO_EIS, 0]

    def fake_get_event(pointer: int) -> int:
        return queue.pop(0)

    monkeypatch.setattr(_capi.liboeffis, "dispatch", lambda p: None)
    monkeypatch.setattr(_capi.liboeffis, "get_event", fake_get_event)
    monkeypatch.setattr(_capi.liboeffis, "get_eis_fd", lambda p: read_fd)

    try:
        session = oeffis.Oeffis()
        assert session.dispatch() is True
        # Deliberately never read session.eis_fd.

        del session
        gc.collect()

        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass  # expected: __del__ should have already closed this
        os.close(write_fd)


def test_del_does_not_close_eis_fd_once_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The mirror image of the test above: once `eis_fd` has been read (the
    # hand-off point -- typically straight into Sender.create_for_fd(),
    # which takes ownership and closes it itself), __del__ must NOT also
    # close it, or that would race/double-close against whatever now owns
    # it.
    read_fd, write_fd = os.pipe()
    queue = [oeffis._EventType.CONNECTED_TO_EIS, 0]

    def fake_get_event(pointer: int) -> int:
        return queue.pop(0)

    monkeypatch.setattr(_capi.liboeffis, "dispatch", lambda p: None)
    monkeypatch.setattr(_capi.liboeffis, "get_event", fake_get_event)
    monkeypatch.setattr(_capi.liboeffis, "get_eis_fd", lambda p: read_fd)

    try:
        session = oeffis.Oeffis()
        assert session.dispatch() is True
        claimed_fd = session.eis_fd
        assert claimed_fd == read_fd

        del session
        gc.collect()

        os.fstat(read_fd)  # still open -- proves __del__ left it alone
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_dispatch_ignores_an_unknown_event_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same contract as ei/eis EventType: liboeffis may grow event types,
    # and one this table doesn't know must not crash dispatch(). The
    # unknown value is skipped and draining continues, so the CLOSED
    # behind it is still seen.
    queue = [12345, oeffis._EventType.CLOSED, 0]

    def fake_get_event(pointer: int) -> int:
        return queue.pop(0)

    monkeypatch.setattr(_capi.liboeffis, "dispatch", lambda p: None)
    monkeypatch.setattr(_capi.liboeffis, "get_event", fake_get_event)
    monkeypatch.setattr(_capi.liboeffis, "get_error_message", lambda p: b"")

    session = oeffis.Oeffis()
    with pytest.raises(oeffis.SessionClosedError):
        session.dispatch()


def test_dispatch_raises_when_get_eis_fd_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # liboeffis documents "-1 on failure or before the fd was retrieved".
    # Accepting that as a live fd would hand -1 to ei_setup_backend_fd()
    # and surface the failure far from its cause.
    queue = [oeffis._EventType.CONNECTED_TO_EIS, 0]

    def fake_get_event(pointer: int) -> int:
        return queue.pop(0)

    monkeypatch.setattr(_capi.liboeffis, "dispatch", lambda p: None)
    monkeypatch.setattr(_capi.liboeffis, "get_event", fake_get_event)
    monkeypatch.setattr(_capi.liboeffis, "get_eis_fd", lambda p: -1)
    monkeypatch.setattr(_capi.liboeffis, "get_error_message", lambda p: b"nope")

    session = oeffis.Oeffis()
    with pytest.raises(oeffis.DisconnectedError):
        session.dispatch()

    # And the session is left dead, not half-connected claiming success.
    with pytest.raises(oeffis.DisconnectedError):
        _ = session.eis_fd
