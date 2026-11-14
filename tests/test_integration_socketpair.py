"""End-to-end tests against the real libei/libeis, entirely in-process.

Most of these use the fd backend on both sides (``Eis.create_for_fd()`` +
``add_client()`` on the server, ``ei.Sender.create_for_fd()`` on the client)
-- the same mechanism xdg-desktop-portal's RemoteDesktop implementation uses
to hand a client its EI fd, but without needing a live
portal/compositor/user-consent dialog. It's what makes this package's core
injection path testable in CI rather than only against a live desktop session
-- see ``_capi/libeis.py``'s module docstring and docs/vs-snegg.md for how
this path's real signatures were worked out.

``test_socket_backend_round_trips_and_reports_client_pid`` is the exception:
it drives the *socket* backend (``create_for_socket()`` on both sides), the
transport no fd-backend test can reach, and reads a client's pid off the live
peer credential -- which only the socket backend can answer.
"""

from __future__ import annotations

import os
import select
import shutil
import tempfile
import time
from pathlib import Path

import pytest

from conftest import has_symbol, requires_libei
from libei import ei, eis

pytestmark = [pytest.mark.integration, requires_libei]

# What the client sends as its start_emulating sequence, and what the server
# should read back for it. They are the same number only on libei 1.6+:
# below that, libeis built the start_emulating event and never copied the
# sequence into it, so the getter returned a zeroed struct field for every
# client no matter what was sent. Upstream added the missing assignment in
# libei commit 98cd152, released in 1.6.0 and in no earlier tag -- measured
# 0 on 1.2.1, 1.3.901 and 1.5.0, 4242 on 1.6.0.
#
# `requires_symbol` cannot express this, which is why `has_symbol` exists:
# every version from the 1.0.0 floor up exports
# `eis_event_emulating_get_sequence`, the old ones just answer wrongly.
# `eis_set_flag` is 1.6-only and stands in for the version -- and it is a
# libeis symbol, so the library being asked is the one that answers.
#
# Decided here at import rather than in the test body both because that is
# where the suite's other version gates live, and because a branch down in
# that already-long negotiation costs more than it is worth.
_SENT_EMULATING_SEQUENCE = 4242
_READ_EMULATING_SEQUENCE = (
    _SENT_EMULATING_SEQUENCE if has_symbol("libeis.so.1", "eis_set_flag") else 0
)

# eis_backend_socket_get_client_pid is libei 1.5+; the socket transport itself
# is on the 1.0.0 floor. So test_socket_backend_round_trips_and_reports_client_pid
# runs on every version and only the pid read is gated -- the same has_symbol
# branch as above, rather than a mark that would skip the whole test on an older
# library that can still prove the transport.
_HAS_CLIENT_PID = has_symbol("libeis.so.1", "eis_backend_socket_get_client_pid")


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


def test_the_accessors_no_other_test_reaches_round_trip() -> None:
    """One negotiation that drives every event accessor the suite had missed.

    Found by asking which public names never appear in any test file: the
    scroll, button, absolute-motion and emulating-sequence accessors on
    ``eis.Event``, ``ei.Event.keyboard_xkb_modifiers``, and
    ``eis.Device.device_type``/``height`` were all unexercised. Each is a
    ctypes binding to a separate C symbol, so an unexercised one is exactly
    where a wrong symbol name or a wrong ``restype`` hides -- the kind of
    mistake that cannot be caught by reading, only by letting the library
    answer.

    Driven in one negotiation rather than one test apiece because the
    expensive part is the handshake, not the assertions, and because the
    values then have to survive the *same* frame: a wrong binding that
    happened to return a plausible number in isolation still has to agree
    with the five others here.

    The modifier half goes the other way round from the rest. The server
    *sends* xkb modifiers (``eis.Device.keyboard_xkb_modifiers``) and the
    client *receives* them (``ei.Event.keyboard_xkb_modifiers``) -- two
    different members that share a name, which is the protocol's shape and
    not a duplication.
    """
    server = eis.Eis.create_for_fd()
    client_fd = server.add_client()
    sender = ei.Sender.create_for_fd(client_fd, name="accessor-probe")

    caps = (
        eis.DeviceCapability.POINTER,
        eis.DeviceCapability.POINTER_ABSOLUTE,
        eis.DeviceCapability.BUTTON,
        eis.DeviceCapability.SCROLL,
        eis.DeviceCapability.KEYBOARD,
    )
    client_caps = (
        ei.DeviceCapability.POINTER,
        ei.DeviceCapability.POINTER_ABSOLUTE,
        ei.DeviceCapability.BUTTON,
        ei.DeviceCapability.SCROLL,
        ei.DeviceCapability.KEYBOARD,
    )

    seen: dict[str, object] = {}
    wanted = {
        "button",
        "scroll",
        "scroll_discrete",
        "absolute",
        "sequence",
        "modifiers",
    }
    device_created = False
    emulated = False

    deadline = time.monotonic() + 30.0
    fds: dict[int, eis.Eis | ei.Sender] = {server.fd: server, sender.fd: sender}

    while not wanted <= seen.keys() and time.monotonic() < deadline:
        ready, _, _ = select.select(list(fds), [], [], 0.2)
        for fd in ready:
            fds[fd].dispatch()

        for server_event in server.events:
            kind = server_event.event_type
            if kind is eis.EventType.CLIENT_CONNECT:
                server_event.client.connect()
                seat = server_event.client.new_seat("accessor-seat")
                seat.configure_capabilities(caps)
                seat.add()
            elif kind is eis.EventType.SEAT_BIND and not device_created:
                device_created = True
                assert server_event.seat is not None
                server_device = server_event.seat.new_device()
                # The region is not decoration: absolute motion is defined
                # *in the device's region*, so a device configured with
                # POINTER_ABSOLUTE and no region has nowhere to put the
                # point and libeis drops the event. Local libei 1.6 happened
                # to deliver it anyway; the libei the CI runners install did
                # not, and the stricter behaviour is the correct one to write
                # against. (100, 200) sits inside this rectangle, and
                # `mapping_id` is deliberately left unset -- it needs libei
                # 1.1, which the declared-floor job does not have.
                # No `size`: libeis refuses one on a VIRTUAL device
                # ("Device type physical required for size"), and the region
                # is what an absolute device actually needs.
                server_device.configure(
                    name="accessor-device",
                    capabilities=caps,
                    regions=(eis.ConfigureRegion(offset=(0, 0), size=(1920, 1080)),),
                )
                server_device.add()
                server_device.resume()
                seen["device_type"] = server_device.device_type
                seen["height"] = server_device.height
                # The server-to-client direction, which has no event on this
                # side to observe -- the assertion for it is on the client.
                server_device.keyboard_xkb_modifiers(4, 0, 2, 0)
            elif kind is eis.EventType.BUTTON_BUTTON:
                seen["button"] = server_event.button_event
            elif kind is eis.EventType.SCROLL_DELTA:
                seen["scroll"] = server_event.scroll_event
            elif kind is eis.EventType.SCROLL_DISCRETE:
                seen["scroll_discrete"] = server_event.scroll_discrete_event
            elif kind is eis.EventType.POINTER_MOTION_ABSOLUTE:
                seen["absolute"] = server_event.pointer_absolute_event
            elif kind is eis.EventType.DEVICE_START_EMULATING:
                seen["sequence"] = server_event.emulating_sequence

        for sender_event in sender.events:
            kind = sender_event.event_type
            if kind is ei.EventType.SEAT_ADDED and sender_event.seat is not None:
                sender_event.seat.bind(client_caps)
            elif kind is ei.EventType.KEYBOARD_MODIFIERS:
                seen["modifiers"] = sender_event.keyboard_xkb_modifiers
            elif (
                kind is ei.EventType.DEVICE_ADDED
                and sender_event.device is not None
                and not emulated
            ):
                emulated = True
                client_device = sender_event.device
                client_device.start_emulating(sequence=_SENT_EMULATING_SEQUENCE)
                client_device.button(272, True)
                client_device.scroll_delta(3.5, -2.0)
                client_device.scroll_discrete(120, -240)
                client_device.pointer_motion_absolute(100.0, 200.0)
                client_device.frame()
                client_device.stop_emulating()

    missing = sorted(wanted - seen.keys())
    assert not missing, f"never observed: {missing}"

    assert seen["button"] == eis.ButtonEvent(button=272, is_press=True)
    assert seen["scroll"] == eis.ScrollEvent(dx=3.5, dy=-2.0)
    assert seen["scroll_discrete"] == eis.ScrollDiscreteEvent(dx=120, dy=-240)
    assert seen["absolute"] == eis.PointerAbsoluteEvent(x=100.0, y=200.0)
    # The read itself stays unconditional -- calling the getter is what
    # proves its symbol name and restype, which is this test's whole point.
    # Only the expected value is version-dependent; see
    # `_READ_EMULATING_SEQUENCE` above for why. Asserting the 0 below 1.6
    # rather than skipping is deliberate: it is what
    # `eis.Event.emulating_sequence` documents, so an old library keeps
    # that claim under test instead of leaving it unchallenged.
    assert seen["sequence"] == _READ_EMULATING_SEQUENCE
    assert seen["modifiers"] == ei.XkbModifiersEvent(
        depressed=4, latched=0, locked=2, group=0
    )
    # `height` is 0 here, and exactly 0 is the right assertion rather than a
    # type check: it reports a *configured size*, which libeis accepts only
    # on a PHYSICAL device, so a VIRTUAL one like this is unsized by
    # definition. The region above does not change it -- a region is where
    # the device maps to, not how big the device is. device_type is whatever
    # libeis assigns a device this package created, so only its being a real
    # member of the enum is claimed.
    assert seen["device_type"] in tuple(eis.DeviceType)
    assert seen["height"] == 0


def test_seat_bind_reports_the_capabilities_the_client_bound() -> None:
    """``eis.Event.seat_capabilities``, read off a real SEAT_BIND event.

    The binding behind it -- ``eis_event_seat_has_capability`` -- is a
    per-capability probe no other test calls at the value level, and the
    ``SEAT_BIND`` accessor the suite's earlier round-trip pass missed. A wrong
    symbol name or ``restype`` on it would return plausible booleans in
    isolation; this catches that by having the server offer exactly three
    capabilities, the client bind exactly those three, and the ``SEAT_BIND``
    event name all three and nothing else. The offer and the bind each pass
    the three in a *different* non-declaration order, so the asserted result
    -- (POINTER, SCROLL, BUTTON), ``DeviceCapability`` declaration order,
    which is how the accessor assembles the tuple -- can match neither of
    them by accident.
    """
    server = eis.Eis.create_for_fd()
    client_fd = server.add_client()
    sender = ei.Sender.create_for_fd(client_fd, name="seatbind-probe")

    offered = (
        eis.DeviceCapability.SCROLL,
        eis.DeviceCapability.BUTTON,
        eis.DeviceCapability.POINTER,
    )
    requested = (
        ei.DeviceCapability.BUTTON,
        ei.DeviceCapability.POINTER,
        ei.DeviceCapability.SCROLL,
    )
    bound: tuple[eis.DeviceCapability, ...] | None = None

    deadline = time.monotonic() + 10.0
    fds: dict[int, eis.Eis | ei.Sender] = {server.fd: server, sender.fd: sender}
    while bound is None and time.monotonic() < deadline:
        ready, _, _ = select.select(list(fds), [], [], 0.2)
        for fd in ready:
            fds[fd].dispatch()
        for server_event in server.events:
            if server_event.event_type is eis.EventType.CLIENT_CONNECT:
                server_event.client.connect()
                seat = server_event.client.new_seat("seatbind-seat")
                seat.configure_capabilities(offered)
                seat.add()
            elif server_event.event_type is eis.EventType.SEAT_BIND:
                bound = server_event.seat_capabilities
        for sender_event in sender.events:
            if (
                sender_event.event_type is ei.EventType.SEAT_ADDED
                and sender_event.seat is not None
            ):
                sender_event.seat.bind(requested)

    assert bound is not None, "server never saw a SEAT_BIND event"
    assert bound == (
        eis.DeviceCapability.POINTER,
        eis.DeviceCapability.SCROLL,
        eis.DeviceCapability.BUTTON,
    )


def test_scroll_stop_and_cancel_round_trip() -> None:
    """``scroll_stop``/``scroll_cancel`` send, and the event read on the server.

    Four C symbols no other test exercises at the value level: the client's
    ``ei.Device.scroll_stop()``/``scroll_cancel()`` send path
    (``ei_device_scroll_stop`` / ``ei_device_scroll_cancel``) and the server's
    read of the resulting events (``eis_event_scroll_get_stop_x`` /
    ``eis_event_scroll_get_stop_y``, which ``scroll_stop_event`` accepts for
    both SCROLL_STOP and SCROLL_CANCEL). The two axes are set asymmetrically --
    stop on y only, cancel on x only -- so a getter wired to the wrong axis
    symbol, or an accessor that read a SCROLL_CANCEL as a stop, would disagree
    with the value sent.
    """
    server = eis.Eis.create_for_fd()
    client_fd = server.add_client()
    sender = ei.Sender.create_for_fd(client_fd, name="scrollstop-probe")

    scroll_caps = (eis.DeviceCapability.POINTER, eis.DeviceCapability.SCROLL)
    client_scroll_caps = (ei.DeviceCapability.POINTER, ei.DeviceCapability.SCROLL)
    device_created = False
    emitted = False
    stop_event: eis.ScrollStopEvent | None = None
    cancel_event: eis.ScrollStopEvent | None = None

    deadline = time.monotonic() + 15.0
    fds: dict[int, eis.Eis | ei.Sender] = {server.fd: server, sender.fd: sender}
    while (stop_event is None or cancel_event is None) and time.monotonic() < deadline:
        ready, _, _ = select.select(list(fds), [], [], 0.2)
        for fd in ready:
            fds[fd].dispatch()
        for server_event in server.events:
            kind = server_event.event_type
            if kind is eis.EventType.CLIENT_CONNECT:
                server_event.client.connect()
                seat = server_event.client.new_seat("scrollstop-seat")
                seat.configure_capabilities(scroll_caps)
                seat.add()
            elif kind is eis.EventType.SEAT_BIND and not device_created:
                device_created = True
                assert server_event.seat is not None
                device = server_event.seat.new_device()
                device.configure(name="scrollstop-device", capabilities=scroll_caps)
                device.add()
                device.resume()
            elif kind is eis.EventType.SCROLL_STOP:
                stop_event = server_event.scroll_stop_event
            elif kind is eis.EventType.SCROLL_CANCEL:
                cancel_event = server_event.scroll_stop_event
        for sender_event in sender.events:
            kind = sender_event.event_type
            if kind is ei.EventType.SEAT_ADDED and sender_event.seat is not None:
                sender_event.seat.bind(client_scroll_caps)
            elif (
                # DEVICE_RESUMED, not DEVICE_ADDED: a device arrives paused,
                # and ei_device_start_emulating() silently returns unless the
                # device's state is RESUMED. Both events are in flight here,
                # so keying off ADDED happens to work whenever one dispatch
                # batch carries both -- and sends nothing at all the moment
                # they split across batches.
                kind is ei.EventType.DEVICE_RESUMED
                and sender_event.device is not None
                and ei.DeviceCapability.SCROLL in sender_event.device.capabilities
                and not emitted
            ):
                emitted = True
                client_device = sender_event.device
                # scroll_stop/scroll_cancel describe the *end* of a scroll, so
                # each follows a scroll_delta on the same axis within its own
                # frame -- the shape libei's header documents.
                client_device.start_emulating()
                client_device.scroll_delta(0.0, 5.0)
                client_device.scroll_stop(False, True)
                client_device.frame()
                client_device.scroll_delta(5.0, 0.0)
                client_device.scroll_cancel(True, False)
                client_device.frame()
                client_device.stop_emulating()

    assert stop_event is not None, "server never received a SCROLL_STOP event"
    assert cancel_event is not None, "server never received a SCROLL_CANCEL event"
    assert stop_event == eis.ScrollStopEvent(stop_x=False, stop_y=True)
    assert cancel_event == eis.ScrollStopEvent(stop_x=True, stop_y=False)


def test_socket_backend_round_trips_and_reports_client_pid() -> None:
    """The socket backend -- the transport the fd-backend tests never take.

    ``eis.Eis.create_for_socket()`` + ``ei.Sender.create_for_socket()`` drive
    ``eis_setup_backend_socket`` and ``ei_setup_backend_socket``, which no other
    integration test reaches. A pointer motion over the socket proves it
    carries real input, and -- on libei 1.5+, which added the symbol --
    ``eis.Client.pid`` reads ``eis_backend_socket_get_client_pid`` off the live
    ``SO_PEERCRED`` credential. Client and server share this process, so the
    reported pid must be exactly ``os.getpid()``; the fd backend has no peer
    socket to ask and could not test this path at all.
    """
    tmpdir = tempfile.mkdtemp(prefix="eis")

    device_created = False
    client_pid: int | None = None
    received_motion: eis.PointerEvent | None = None

    try:
        # A short dir under the system temp root keeps the bind path well
        # under the AF_UNIX sun_path limit (108 bytes), which a pytest
        # tmp_path can approach. Both create_for_socket() calls belong inside
        # the try: either can fail -- a bind refused, a connect refused -- and
        # by then libeis may already have put the socket and its .lock in the
        # dir, so a raise out here would leak the whole thing.
        socket_path = Path(tmpdir, "s")
        server = eis.Eis.create_for_socket(socket_path)
        sender = ei.Sender.create_for_socket(socket_path, name="socket-probe")

        deadline = time.monotonic() + 15.0
        fds: dict[int, eis.Eis | ei.Sender] = {server.fd: server, sender.fd: sender}
        while received_motion is None and time.monotonic() < deadline:
            ready, _, _ = select.select(list(fds), [], [], 0.2)
            for fd in ready:
                fds[fd].dispatch()
            for server_event in server.events:
                kind = server_event.event_type
                if kind is eis.EventType.CLIENT_CONNECT:
                    if _HAS_CLIENT_PID:
                        client_pid = server_event.client.pid
                    server_event.client.connect()
                    seat = server_event.client.new_seat("socket-seat")
                    seat.configure_capabilities((eis.DeviceCapability.POINTER,))
                    seat.add()
                elif kind is eis.EventType.SEAT_BIND and not device_created:
                    device_created = True
                    assert server_event.seat is not None
                    device = server_event.seat.new_device()
                    device.configure(
                        name="socket-pointer",
                        capabilities=(eis.DeviceCapability.POINTER,),
                    )
                    device.add()
                    device.resume()
                elif kind is eis.EventType.POINTER_MOTION:
                    received_motion = server_event.pointer_event
            for sender_event in sender.events:
                kind = sender_event.event_type
                if kind is ei.EventType.SEAT_ADDED and sender_event.seat is not None:
                    sender_event.seat.bind((ei.DeviceCapability.POINTER,))
                elif (
                    # DEVICE_RESUMED, not DEVICE_ADDED -- see the same branch
                    # in test_scroll_stop_and_cancel_round_trip:
                    # start_emulating() no-ops on a device that is still
                    # paused, which is how every device first arrives.
                    kind is ei.EventType.DEVICE_RESUMED
                    and sender_event.device is not None
                    and ei.DeviceCapability.POINTER in sender_event.device.capabilities
                ):
                    sender_event.device.start_emulating().pointer_motion(
                        9, 4
                    ).frame().stop_emulating()
    finally:
        # rmtree, not rmdir: libeis leaves both the socket and a sibling
        # ``.lock`` file behind, and unlinking an open AF_UNIX socket is fine
        # on Linux.
        shutil.rmtree(tmpdir, ignore_errors=True)

    assert received_motion is not None, "no pointer motion arrived over the socket"
    assert received_motion == eis.PointerEvent(dx=9, dy=4)
    if _HAS_CLIENT_PID:
        assert client_pid == os.getpid()
