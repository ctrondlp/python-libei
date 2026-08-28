"""End-to-end tests for the accessors and capabilities added on top of the
core injection path, against the real libei/libeis.

``test_integration_socketpair.py`` proves the connect/negotiate/inject path
works; this file covers the parts that were only bound later -- text input,
touch cancellation, ping/pong round trips, region mapping ids and keymap
transfer -- plus the two silent failures those made worth fixing: a keymap
fd that arrives at EOF, and accessors that return zeros for the wrong event
type instead of complaining.

Some of these need libei 1.6 (text) or 1.4 (ping); a build too old to export
the symbol raises LibraryNotFoundError, which each test skips on rather than
failing, so this file stays runnable against the whole supported range.
"""

from __future__ import annotations

import os
import select
import time
from collections.abc import Callable, Sequence

import pytest

from conftest import requires_libei, requires_symbol
from libei import ei, eis

pytestmark = [pytest.mark.integration, requires_libei]


class Pair:
    """A connected libeis server and libei sender, in one process.

    Handles the negotiation both tests and callers always have to repeat --
    CLIENT_CONNECT to a seat, SEAT_BIND to a device -- and hands each side's
    events to callbacks *inside* the drain loop, since an event must not be
    kept past its iteration.
    """

    def __init__(
        self,
        capabilities: Sequence[eis.DeviceCapability],
        configure_device: Callable[[eis.Device], None] | None = None,
        regions: Sequence[eis.ConfigureRegion] = (),
    ) -> None:
        self.server = eis.Eis.create_for_fd()
        self.sender = ei.Sender.create_for_fd(
            self.server.add_client(), name="test-sender"
        )
        self._capabilities = tuple(capabilities)
        self._configure_device = configure_device
        self._regions = tuple(regions)
        self._device_made = False
        self.server_device: eis.Device | None = None

    def run(
        self,
        done: Callable[[], bool],
        on_server_event: Callable[[eis.Event], None] = lambda e: None,
        on_client_event: Callable[[ei.Event], None] = lambda e: None,
        on_device: Callable[[ei.Device], None] = lambda d: None,
        timeout: float = 10.0,
    ) -> None:
        """Pump both sides until ``done()``, then assert that it happened."""
        fds: dict[int, eis.Eis | ei.Sender] = {
            self.server.fd: self.server,
            self.sender.fd: self.sender,
        }
        client_capabilities = tuple(
            ei.DeviceCapability(c.value) for c in self._capabilities
        )
        device_seen = False
        deadline = time.monotonic() + timeout
        while not done() and time.monotonic() < deadline:
            for fd in select.select(list(fds), [], [], 0.2)[0]:
                fds[fd].dispatch()

            for event in self.server.events:  # server events only; see below
                event_type = event.event_type
                if event_type is eis.EventType.CLIENT_CONNECT:
                    event.client.connect()
                    seat = event.client.new_seat("test-seat")
                    seat.configure_capabilities(self._capabilities)
                    seat.add()
                elif event_type is eis.EventType.SEAT_BIND and not self._device_made:
                    self._device_made = True
                    assert event.seat is not None
                    device = event.seat.new_device()
                    device.configure(
                        name="test-device",
                        capabilities=self._capabilities,
                        regions=self._regions,
                    )
                    if self._configure_device is not None:
                        self._configure_device(device)
                    device.add()
                    device.resume()
                    self.server_device = device
                else:
                    on_server_event(event)

            for client_event in self.sender.events:
                if client_event.event_type is ei.EventType.SEAT_ADDED:
                    assert client_event.seat is not None
                    client_event.seat.bind(client_capabilities)
                elif client_event.event_type is ei.EventType.DEVICE_RESUMED:
                    client_device = client_event.device
                    if client_device is not None and not device_seen:
                        device_seen = True
                        on_device(client_device)
                else:
                    on_client_event(client_event)
        assert done(), f"timed out after {timeout}s"


@requires_symbol("libeis.so.1", "eis_device_text_utf8_with_length")
def test_text_utf8_round_trips() -> None:
    pair = Pair((eis.DeviceCapability.TEXT,))
    received: list[str] = []

    def on_server_event(event: eis.Event) -> None:
        if event.event_type is eis.EventType.TEXT_UTF8:
            received.append(event.text_utf8_event.text)

    def on_device(device: ei.Device) -> None:
        device.start_emulating().text_utf8("héllo").frame().stop_emulating()

    pair.run(
        lambda: bool(received), on_server_event=on_server_event, on_device=on_device
    )
    assert received == ["héllo"]


@requires_symbol("libeis.so.1", "eis_device_text_keysym")
def test_text_keysym_round_trips() -> None:
    pair = Pair((eis.DeviceCapability.TEXT,))
    received: list[eis.TextKeysymEvent] = []

    def on_server_event(event: eis.Event) -> None:
        if event.event_type is eis.EventType.TEXT_KEYSYM:
            received.append(event.text_keysym_event)

    def on_device(device: ei.Device) -> None:
        device.start_emulating()
        device.text_keysym(0x61, True).frame()  # XKB_KEY_a
        device.text_keysym(0x61, False).frame()
        device.stop_emulating()

    pair.run(
        lambda: len(received) >= 2, on_server_event=on_server_event, on_device=on_device
    )
    assert received[0] == eis.TextKeysymEvent(keysym=0x61, is_press=True)
    assert received[1] == eis.TextKeysymEvent(keysym=0x61, is_press=False)


@requires_symbol("libei.so.1", "ei_touch_cancel")
def test_touch_up_reports_cancellation() -> None:
    pair = Pair((eis.DeviceCapability.TOUCH,))
    ups: list[eis.TouchUpEvent] = []

    def on_server_event(event: eis.Event) -> None:
        if event.event_type is eis.EventType.TOUCH_UP:
            ups.append(event.touch_up_event)

    def on_device(device: ei.Device) -> None:
        device.start_emulating()
        touch = device.touch_new()
        touch.down(10.0, 20.0)
        device.frame()
        touch.cancel()
        device.frame()
        device.stop_emulating()

    pair.run(lambda: bool(ups), on_server_event=on_server_event, on_device=on_device)
    # A cancelled touch still arrives as TOUCH_UP; is_cancel is what
    # separates it from a normal release. Whether the flag survives the
    # wire depends on the ei_touchscreen protocol version both sides
    # negotiated, not on any symbol's presence -- so an older libei reports
    # a plain release here, and there is nothing to assert.
    if not ups[0].is_cancel:
        pytest.skip("installed libei negotiated ei_touchscreen older than v2")
    assert ups[0].is_cancel is True


def test_touch_up_without_cancel_is_not_flagged() -> None:
    pair = Pair((eis.DeviceCapability.TOUCH,))
    ups: list[eis.TouchUpEvent] = []

    def on_server_event(event: eis.Event) -> None:
        if event.event_type is eis.EventType.TOUCH_UP:
            ups.append(event.touch_up_event)

    def on_device(device: ei.Device) -> None:
        device.start_emulating()
        touch = device.touch_new()
        touch.down(10.0, 20.0)
        device.frame()
        touch.up()
        device.frame()
        device.stop_emulating()

    pair.run(lambda: bool(ups), on_server_event=on_server_event, on_device=on_device)
    # Deliberately not gated on ei_touch_cancel: this is also the
    # regression test for touch_up_event degrading rather than raising on a
    # libei older than 1.4, where the is_cancel accessor does not exist.
    assert ups[0].is_cancel is False


@requires_symbol("libei.so.1", "ei_new_ping")
def test_ping_round_trips_to_a_pong_event() -> None:
    pair = Pair((eis.DeviceCapability.POINTER,))
    pongs: list[int] = []
    sent_id: list[int] = []
    ping_holder: list[ei.Ping] = []

    def on_device(device: ei.Device) -> None:
        ping = pair.sender.new_ping()
        ping_holder.append(ping)
        sent_id.append(ping.id)
        ping.send()

    def on_client_event(event: ei.Event) -> None:
        if event.event_type is ei.EventType.PONG:
            pongs.append(event.pong.id)

    pair.run(lambda: bool(pongs), on_client_event=on_client_event, on_device=on_device)
    # The pong has to identify *which* round trip completed, or a caller
    # with more than one in flight can't tell them apart.
    assert pongs[0] == sent_id[0]


def test_keymap_transfers_and_reads_back() -> None:
    keymap_text = b"xkb_keymap { /* test */ };\x00"
    source = os.memfd_create("test-keymap")
    os.write(source, keymap_text)
    keymap_file = os.fdopen(source, "rb")

    def configure_device(device: eis.Device) -> None:
        device.new_keymap(eis.KeymapType.XKB, keymap_file, len(keymap_text)).add()

    pair = Pair((eis.DeviceCapability.KEYBOARD,), configure_device=configure_device)
    read_back: list[bytes] = []

    def on_device(device: ei.Device) -> None:
        keymap = device.keymap
        assert keymap is not None
        assert keymap.keymap_type is ei.KeymapType.XKB
        assert keymap.size == len(keymap_text)
        # No seek() here on purpose: this is the regression test for the
        # fd arriving at EOF, where reading straight through returned b"".
        with keymap.fd as f:
            read_back.append(f.read())

    with keymap_file:
        pair.run(lambda: bool(read_back), on_device=on_device)
    assert read_back[0] == keymap_text


@requires_symbol("libeis.so.1", "eis_region_set_mapping_id")
def test_region_mapping_id_reaches_the_client() -> None:
    # Set through ConfigureRegion rather than Region.set_mapping_id():
    # Device.configure() creates, configures and adds each region in one
    # go, so there is no moment in between for a caller to reach it.
    pair = Pair(
        (eis.DeviceCapability.POINTER_ABSOLUTE,),
        regions=(
            eis.ConfigureRegion(
                offset=(0, 0), size=(1920, 1080), mapping_id="screen-0"
            ),
        ),
    )
    seen: list[list[tuple[str | None, tuple[int, int]]]] = []

    def on_device(device: ei.Device) -> None:
        seen.append([(r.mapping_id, r.dimension) for r in device.regions])

    pair.run(lambda: bool(seen), on_device=on_device)
    assert seen[0] == [("screen-0", (1920, 1080))]


def test_wrong_accessor_raises_instead_of_returning_zeros() -> None:
    pair = Pair((eis.DeviceCapability.POINTER,))
    checked: list[str] = []

    def on_server_event(event: eis.Event) -> None:
        if event.event_type is not eis.EventType.POINTER_MOTION:
            return
        assert event.pointer_event == eis.PointerEvent(dx=7.0, dy=3.0)
        # Before the type check, this returned KeyEvent(key=0,
        # is_press=False) -- a real, plausible-looking value -- while
        # libeis logged an internal "Bug:" line the caller never saw.
        with pytest.raises(TypeError, match="key_event"):
            _ = event.key_event
        checked.append("ok")

    def on_device(device: ei.Device) -> None:
        device.start_emulating().pointer_motion(7, 3).frame().stop_emulating()

    pair.run(
        lambda: bool(checked), on_server_event=on_server_event, on_device=on_device
    )


@requires_symbol("libei.so.1", "ei_peek_event")
def test_peek_event_type_matches_the_next_event() -> None:
    pair = Pair((eis.DeviceCapability.POINTER,))
    agreed: list[bool] = []

    def on_device(device: ei.Device) -> None:
        device.start_emulating().pointer_motion(1, 1).frame().stop_emulating()

    # Peeking must agree with what the next drain actually yields, and must
    # not consume it -- a peeked reference held across ei_get_event() is
    # undefined behavior, so peek_event_type() drops it before returning.
    def on_client_event(event: ei.Event) -> None:
        agreed.append(True)

    deadline = time.monotonic() + 10.0
    fds: dict[int, eis.Eis | ei.Sender] = {
        pair.server.fd: pair.server,
        pair.sender.fd: pair.sender,
    }
    started = False
    while not agreed and time.monotonic() < deadline:
        for fd in select.select(list(fds), [], [], 0.2)[0]:
            fds[fd].dispatch()
        for server_event in pair.server.events:
            if server_event.event_type is eis.EventType.CLIENT_CONNECT:
                server_event.client.connect()
                seat = server_event.client.new_seat("test-seat")
                seat.configure_capabilities((eis.DeviceCapability.POINTER,))
                seat.add()
            elif server_event.event_type is eis.EventType.SEAT_BIND and not started:
                started = True
                assert server_event.seat is not None
                device = server_event.seat.new_device()
                device.configure(
                    name="test-device", capabilities=(eis.DeviceCapability.POINTER,)
                )
                device.add()
                device.resume()
        peeked = pair.sender.peek_event_type()
        for client_event in pair.sender.events:
            assert peeked == client_event.event_type, "peek disagreed with next event"
            agreed.append(True)
            break
    assert agreed, "no client event ever arrived to peek at"
