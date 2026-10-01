"""An event used after its loop iteration, through the real library.

Each event is released as the loop moves on, and the docs promise a
``RuntimeError`` afterwards. test_ei_objects.py checks ``_as_parameter_`` in
isolation, which is not the path a caller takes: a real call converts its
arguments inside ctypes, and ctypes re-raises whatever that conversion raised as
``ctypes.ArgumentError`` carrying only the text. So the promise is checked here,
with a real event and a real C call.
"""

from __future__ import annotations

import select

import pytest

from conftest import requires_libei
from libei import ei, eis
from test_integration_extras import Pair

pytestmark = [pytest.mark.integration, requires_libei]


def test_a_released_event_raises_runtime_error_from_a_real_call() -> None:
    pair = Pair((eis.DeviceCapability.POINTER,))
    kept: list[eis.Event] = []

    def on_server_event(event: eis.Event) -> None:
        if event.event_type is eis.EventType.POINTER_MOTION:
            kept.append(event)

    def on_device(device: ei.Device) -> None:
        device.start_emulating().pointer_motion(7, 3).frame().stop_emulating()

    pair.run(lambda: bool(kept), on_server_event=on_server_event, on_device=on_device)
    # The loop has moved on, so the event has been released.
    if select.select([pair.server.fd], [], [], 0.2)[0]:
        pair.server.dispatch()
    list(pair.server.events)

    def event_type(event: eis.Event) -> object:
        return event.event_type

    def pointer_event(event: eis.Event) -> object:
        return event.pointer_event

    for read in (event_type, pointer_event):
        with pytest.raises(RuntimeError, match="already been released"):
            read(kept[0])
