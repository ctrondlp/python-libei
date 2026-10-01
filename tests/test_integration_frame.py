"""What the real libei does with input that is queued but never framed.

The docs used to say that forgetting ``frame()`` means nothing happens, with no
exception and no warning. That is a claim about libei, so it is checked against
libei rather than repeated -- and it was only half true. Measured on libei 1.2.1
and 1.6.0 alike:

=============================  ================================================
What the client does           What the server sees
=============================  ================================================
motion, frame, stop            the motion
motion, stop (no frame)        the motion, in a FRAME libei adds itself, and an
                               ERROR log: "Bug: ei_device_stop_emulating:
                               missing call to ei_device_frame()"
motion, no frame, no stop      nothing at all, and no log line
motion, not emulating          nothing, and a "Bug: ... device is not emulating"
=============================  ================================================

The framed control proves the harness can see a motion at all, so an empty
result in the others means something.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import pytest

from conftest import requires_libei
from libei import ei, eis
from test_integration_extras import Pair

pytestmark = [pytest.mark.integration, requires_libei]

_SETTLE = 1.0
"""Seconds to keep pumping after the client is done, so a late event would show."""


def _run(send: Callable[[ei.Device], object]) -> list[eis.EventType | int]:
    pair = Pair((eis.DeviceCapability.POINTER,))
    arrived: list[eis.EventType | int] = []
    sent_at: list[float] = []

    def on_server_event(event: eis.Event) -> None:
        arrived.append(event.event_type)

    def on_device(device: ei.Device) -> None:
        send(device)
        sent_at.append(time.monotonic())

    pair.run(
        lambda: bool(sent_at) and time.monotonic() - sent_at[0] > _SETTLE,
        on_server_event=on_server_event,
        on_device=on_device,
    )
    return arrived


def _bug_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "Bug:" in r.getMessage()]


def test_a_framed_motion_reaches_the_server(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    arrived = _run(
        lambda d: d.start_emulating().pointer_motion(7, 3).frame().stop_emulating()
    )
    assert eis.EventType.POINTER_MOTION in arrived
    assert _bug_lines(caplog) == []


def test_stopping_without_a_frame_is_framed_for_you_and_logged_as_a_bug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    arrived = _run(lambda d: d.start_emulating().pointer_motion(7, 3).stop_emulating())
    assert eis.EventType.POINTER_MOTION in arrived
    assert eis.EventType.FRAME in arrived
    bugs = _bug_lines(caplog)
    assert any("missing call to ei_device_frame" in line for line in bugs), bugs
    # At ERROR, so Python's default last-resort handler prints it to stderr.
    assert any(
        r.levelno >= logging.ERROR and "frame" in r.getMessage() for r in caplog.records
    )


def test_a_motion_never_framed_or_stopped_is_dropped_silently(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    arrived = _run(lambda d: d.start_emulating().pointer_motion(7, 3))
    assert eis.EventType.POINTER_MOTION not in arrived
    assert _bug_lines(caplog) == [], "libei now warns about this case: update the docs"


def test_a_motion_sent_while_not_emulating_is_dropped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    arrived = _run(lambda d: d.pointer_motion(7, 3).frame())
    assert eis.EventType.POINTER_MOTION not in arrived
    assert any("not emulating" in line for line in _bug_lines(caplog))
