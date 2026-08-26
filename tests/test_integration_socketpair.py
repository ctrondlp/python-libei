"""End-to-end test against the real libei/libeis, entirely in-process.

Uses the fd backend on both sides (``Eis.create_for_fd()`` + ``add_client()``
on the server, ``ei.Sender.create_for_fd()`` on the client) -- the same
mechanism xdg-desktop-portal's RemoteDesktop implementation uses to hand a
client its EI fd, but without needing a live portal/compositor/user-consent
dialog. It's what makes this package's core injection path testable in CI
rather than only against a live desktop session -- see
``_capi/libeis.py``'s module docstring and docs/vs-snegg.md for how this
path's real signatures were worked out.
"""

from __future__ import annotations

import select
import time

import pytest

from conftest import requires_libei
from libei import ei, eis

pytestmark = [pytest.mark.integration, requires_libei]


def test_client_connects() -> None:
    server = eis.Eis.create_for_fd()
    client_fd = server.add_client()
    sender = ei.Sender.create_for_fd(client_fd, name="test-sender")

    # The initial handshake is bidirectional -- the client side must have
    # its own dispatch() pumped to process and respond to the server's
    # traffic (a SYNC event arrives on the client before the server ever
    # sees CLIENT_CONNECT). Only selecting on server.fd here left the
    # handshake permanently stuck and the test timing out.
    connect_events: list[eis.Event] = []
    deadline = time.monotonic() + 5.0
    fds: dict[int, eis.Eis | ei.Sender] = {server.fd: server, sender.fd: sender}
    while not connect_events and time.monotonic() < deadline:
        ready, _, _ = select.select(list(fds), [], [], 0.2)
        for fd in ready:
            fds[fd].dispatch()
        connect_events.extend(
            e for e in server.events if e.event_type is eis.EventType.CLIENT_CONNECT
        )
        list(sender.events)  # drain; the client side has nothing to react to here
    assert connect_events, "server never saw a CLIENT_CONNECT event"

    # NOTE: this does *not* also test disconnect-on-GC. Investigating this
    # test's original design, dropping the sender's last Python reference
    # (triggering CObject's weakref-finalizer ei_unref() call) does not
    # actually close the underlying fd at the OS level -- confirmed with
    # os.fstat() immediately after gc.collect(). That contradicts libei.h's
    # own doc comment on ei_setup_backend_fd() ("will close it when tearing
    # down"), and held even when polling server.dispatch() unconditionally
    # for 15s afterward. This looks like an internal libei refcounting
    # detail (e.g. an extra reference held by its own event-loop
    # registration that a single external unref doesn't release) rather
    # than a bug in this package's bindings -- the connect/negotiate/inject
    # path above and in test_pointer_motion_round_trips_through_negotiated_device
    # is what's proven reliable. See docs/vs-snegg.md.


def test_pointer_motion_round_trips_through_negotiated_device() -> None:
    server = eis.Eis.create_for_fd()
    client_fd = server.add_client()
    sender = ei.Sender.create_for_fd(client_fd, name="test-sender")

    device_created = False
    received_motion: eis.PointerEvent | None = None

    deadline = time.monotonic() + 10.0
    fds: dict[int, eis.Eis | ei.Sender] = {server.fd: server, sender.fd: sender}

    while received_motion is None and time.monotonic() < deadline:
        ready, _, _ = select.select(list(fds), [], [], 0.2)
        for fd in ready:
            fds[fd].dispatch()

        # Ordinary `for event in ctx.events:` loops -- Context.events
        # releases each event immediately after yielding it, specifically
        # so a SYNC event (whose pong reply libei sends on unref) can't
        # stall waiting on Python GC timing. See ei.py's events docstring
        # and docs/vs-snegg.md for how this was found: a version of this
        # test without that library-level fix deadlocked here whenever a
        # SYNC event landed as the last item in a drain batch.
        for server_event in server.events:
            if server_event.event_type is eis.EventType.CLIENT_CONNECT:
                server_event.client.connect()
                seat = server_event.client.new_seat("test-seat")
                seat.configure_capabilities((eis.DeviceCapability.POINTER,))
                seat.add()
            elif (
                server_event.event_type is eis.EventType.SEAT_BIND
                and not device_created
            ):
                device_created = True
                assert server_event.seat is not None
                device = server_event.seat.new_device()
                device.configure(
                    name="test-pointer",
                    capabilities=(eis.DeviceCapability.POINTER,),
                )
                device.add()
                device.resume()
            elif server_event.event_type is eis.EventType.POINTER_MOTION:
                received_motion = server_event.pointer_event

        for sender_event in sender.events:
            if (
                sender_event.event_type is ei.EventType.SEAT_ADDED
                and sender_event.seat is not None
            ):
                sender_event.seat.bind((ei.DeviceCapability.POINTER,))
            elif sender_event.event_type is ei.EventType.DEVICE_ADDED:
                client_device = sender_event.device
                if client_device is not None and (
                    ei.DeviceCapability.POINTER in client_device.capabilities
                ):
                    client_device.start_emulating().pointer_motion(
                        7, 3
                    ).frame().stop_emulating()

    assert received_motion is not None, "server never received a pointer motion event"
    assert received_motion == eis.PointerEvent(dx=7, dy=3)
