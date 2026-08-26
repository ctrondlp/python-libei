"""Pythonic wrapper around libeis -- the EIS *server* library.

An EIS context represents the compositor side of the protocol: it accepts
client connections, advertises seats and devices, and receives the events an
:class:`libei.ei.Sender` injects. This is what a compositor implements, and
what a test harness for this package's own ``ei`` module drives instead of
a real compositor.

Typical usage (accepting one client via the fd backend)::

    server = Eis.create_for_fd()
    client_fd = server.add_client()
    sender = ei.Sender.create_for_fd(client_fd, name="some-client")

    for event in server.events:
        if event.event_type is EventType.CLIENT_CONNECT:
            event.client.connect()
            seat = event.client.new_seat("default")
            seat.configure_capabilities([DeviceCapability.POINTER])
            seat.add()
"""

from __future__ import annotations

import dataclasses
import enum
import logging
import os
from collections.abc import Iterator
from ctypes import c_void_p
from pathlib import Path
from typing import IO

from . import _capi
from ._capi.libei import log_handler_t
from ._cobject import CObject
from .ei import _next_emulating_sequence

logger = logging.getLogger("libei.eis")


def is_available() -> bool:
    """Whether libeis.so.1 can be loaded on this system."""
    return _capi.libeis.lib.is_available()


class Error(Exception):
    def __init__(self, message: str, errno: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errno = errno


class EventType(enum.IntEnum):
    """Mirrors ``enum eis_event_type`` from libeis.h.

    Like its ``ei`` counterpart, this enum is not exhaustive by libei's own
    documented contract; :attr:`Event.event_type` returns a plain ``int``
    for a value not listed here rather than raising.
    """

    CLIENT_CONNECT = 1
    CLIENT_DISCONNECT = 2
    SEAT_BIND = 3
    DEVICE_CLOSED = 4
    DEVICE_READY = 5
    SEAT_DEVICE_REQUESTED = 6
    PONG = 90
    SYNC = 91
    FRAME = 100
    DEVICE_START_EMULATING = 200
    DEVICE_STOP_EMULATING = 201
    POINTER_MOTION = 300
    POINTER_MOTION_ABSOLUTE = 400
    BUTTON_BUTTON = 500
    SCROLL_DELTA = 600
    SCROLL_STOP = 601
    SCROLL_CANCEL = 602
    SCROLL_DISCRETE = 603
    KEYBOARD_KEY = 700
    TOUCH_DOWN = 800
    TOUCH_UP = 801
    TOUCH_MOTION = 802
    # Recognized for classification only -- no data getters implemented.
    # See docs/vs-snegg.md.
    TEXT_KEYSYM = 900
    TEXT_UTF8 = 901
    SWIPE_BEGIN = 1000
    SWIPE_UPDATE = 1001
    SWIPE_END = 1002
    PINCH_BEGIN = 1010
    PINCH_UPDATE = 1011
    PINCH_END = 1012
    HOLD_BEGIN = 1020
    HOLD_END = 1021
    STYLUS_BIND_CAPABILITIES = 1100
    STYLUS_PROXIMITY_IN = 1101
    STYLUS_PROXIMITY_OUT = 1102
    STYLUS_ERASE_START = 1103
    STYLUS_ERASE_STOP = 1104
    STYLUS_TIP_DOWN = 1105
    STYLUS_TIP_UP = 1106
    STYLUS_AXIS = 1107


class DeviceCapability(enum.IntFlag):
    POINTER = 1 << 0
    POINTER_ABSOLUTE = 1 << 1
    KEYBOARD = 1 << 2
    TOUCH = 1 << 3
    SCROLL = 1 << 4
    BUTTON = 1 << 5
    TEXT = 1 << 6
    GESTURES = 1 << 7
    STYLUS = 1 << 8


class DeviceType(enum.IntEnum):
    VIRTUAL = 1
    PHYSICAL = 2


class KeymapType(enum.IntEnum):
    XKB = 1


class _LogPriority(enum.IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40


@dataclasses.dataclass(frozen=True, slots=True)
class KeyEvent:
    key: int
    is_press: bool


@dataclasses.dataclass(frozen=True, slots=True)
class ButtonEvent:
    button: int
    is_press: bool


@dataclasses.dataclass(frozen=True, slots=True)
class PointerEvent:
    dx: float
    dy: float


@dataclasses.dataclass(frozen=True, slots=True)
class PointerAbsoluteEvent:
    x: float
    y: float


@dataclasses.dataclass(frozen=True, slots=True)
class ScrollEvent:
    dx: float
    dy: float


@dataclasses.dataclass(frozen=True, slots=True)
class ScrollDiscreteEvent:
    dx: int
    dy: int


@dataclasses.dataclass(frozen=True, slots=True)
class ScrollStopEvent:
    stop_x: bool
    stop_y: bool


@dataclasses.dataclass(frozen=True, slots=True)
class TouchEvent:
    touchid: int
    x: float
    y: float


@dataclasses.dataclass(frozen=True, slots=True)
class ConfigureRegion:
    offset: tuple[int, int]
    size: tuple[int, int]
    physical_scale: float = 1.0


class Region(CObject):
    _ref_func = staticmethod(_capi.libeis.region_ref)
    _unref_func = staticmethod(_capi.libeis.region_unref)

    @property
    def position(self) -> tuple[int, int]:
        """Top-left corner of the region, in logical pixels."""
        return (
            _capi.libeis.region_get_x(self),
            _capi.libeis.region_get_y(self),
        )

    @property
    def dimension(self) -> tuple[int, int]:
        """Width and height of the region, in logical pixels."""
        return (
            _capi.libeis.region_get_width(self),
            _capi.libeis.region_get_height(self),
        )

    @property
    def physical_scale(self) -> float:
        """Scale between logical pixels and this region's physical size."""
        return _capi.libeis.region_get_physical_scale(self)

    def contains(self, x: float, y: float) -> bool:
        """Whether the given logical-pixel point falls inside this region."""
        return bool(_capi.libeis.region_contains(self, x, y))


class Keymap(CObject):
    _ref_func = staticmethod(_capi.libeis.keymap_ref)
    _unref_func = staticmethod(_capi.libeis.keymap_unref)

    @property
    def keymap_type(self) -> KeymapType:
        """Keymap format; currently always XKB."""
        return KeymapType(_capi.libeis.keymap_get_type(self))

    @property
    def size(self) -> int:
        """Size of the keymap data, in bytes."""
        return _capi.libeis.keymap_get_size(self)

    @property
    def fd(self) -> IO[bytes]:
        """Memmap-able file descriptor holding the keymap data.

        A fresh duplicate on each read, which the caller owns and should
        close; the keymap keeps its own.
        """
        # See ei.Keymap.fd: eis_keymap_get_fd() is a plain field read, not
        # a duped/transferred fd -- duplicate it so os.fdopen()'s file
        # object doesn't close a fd the keymap still owns.
        raw_fd = _capi.libeis.keymap_get_fd(self)
        if raw_fd < 0:
            raise Error("eis_keymap_get_fd() reported no usable file descriptor")
        return os.fdopen(os.dup(raw_fd), "rb")

    def add(self) -> None:
        """Publish this object to the client."""
        _capi.libeis.keymap_add(self)


class Touch(CObject):
    # No _ref_func: eis_device_touch_new() is the only function that ever
    # returns a struct eis_touch* (aside from eis_touch_ref/unref
    # themselves), and its docs say the caller already owns that
    # reference -- there's no borrowed-pointer getter elsewhere that would
    # need wrap()'s extra ref. See Device.touch_new(), which uses adopt().
    _unref_func = staticmethod(_capi.libeis.touch_unref)

    @property
    def device(self) -> Device:
        """The device this touch belongs to."""
        device = Device.wrap(_capi.libeis.touch_get_device(self))
        assert device is not None
        return device

    def down(self, x: float, y: float) -> Touch:
        """Begin the touch at the given point."""
        _capi.libeis.touch_down(self, x, y)
        return self

    def motion(self, x: float, y: float) -> Touch:
        """Move the in-progress touch to the given point."""
        _capi.libeis.touch_motion(self, x, y)
        return self

    def up(self) -> Touch:
        """End the touch."""
        _capi.libeis.touch_up(self)
        return self


class Device(CObject):
    _ref_func = staticmethod(_capi.libeis.device_ref)
    _unref_func = staticmethod(_capi.libeis.device_unref)

    def __repr__(self) -> str:
        caps = "|".join(c.name or str(c.value) for c in self.capabilities)
        return f"<Device {self.name!r} {self.device_type.name} {caps}>"

    def configure(
        self,
        name: str | None = None,
        device_type: DeviceType = DeviceType.VIRTUAL,
        size: tuple[int, int] | None = None,
        capabilities: tuple[DeviceCapability, ...] = (),
        regions: tuple[ConfigureRegion, ...] = (),
    ) -> Device:
        """Set the device's properties. Call before :meth:`add`."""
        if name is not None:
            _capi.libeis.device_configure_name(self, name.encode("utf-8"))
        _capi.libeis.device_configure_type(self, device_type)
        if size is not None:
            _capi.libeis.device_configure_size(self, size[0], size[1])
        for cap in capabilities:
            _capi.libeis.device_configure_capability(self, cap)
        for region in regions:
            pointer = _capi.libeis.device_new_region(self)
            if not pointer:
                raise Error("eis_device_new_region() returned NULL")
            # eis_device_new_region() returns an owned reference (initial
            # refcount 1); eis_region_add() registers it with the device
            # but doesn't consume that reference. Adopt it so we can
            # release it explicitly once added -- the raw pointer used to
            # be discarded here with nothing ever unref'ing it, leaking
            # one region every call.
            region_obj = Region.adopt(pointer)
            assert region_obj is not None
            _capi.libeis.region_set_size(region_obj, *region.size)
            _capi.libeis.region_set_offset(region_obj, *region.offset)
            _capi.libeis.region_set_physical_scale(region_obj, region.physical_scale)
            _capi.libeis.region_add(region_obj)
            region_obj.release()
        return self

    def new_keymap(self, keymap_type: KeymapType, fd: IO[bytes], size: int) -> Keymap:
        """Attach an XKB keymap to this keyboard-capable device."""
        pointer = _capi.libeis.device_new_keymap(self, keymap_type, fd.fileno(), size)
        if not pointer:
            raise Error("eis_device_new_keymap() returned NULL")
        keymap = Keymap.adopt(pointer)
        assert keymap is not None
        return keymap

    @property
    def device_type(self) -> DeviceType:
        """Whether the device is virtual or represents real hardware."""
        return DeviceType(_capi.libeis.device_get_type(self))

    @property
    def name(self) -> str:
        """The device name set via :meth:`configure`."""
        return _capi.libeis.device_get_name(self).decode("utf-8")

    @property
    def width(self) -> int:
        """Device width in logical pixels; 0 if unsized."""
        return _capi.libeis.device_get_width(self)

    @property
    def height(self) -> int:
        """Device height in logical pixels; 0 if unsized."""
        return _capi.libeis.device_get_height(self)

    @property
    def capabilities(self) -> tuple[DeviceCapability, ...]:
        """The capabilities this object actually has."""
        return tuple(
            c for c in DeviceCapability if _capi.libeis.device_has_capability(self, c)
        )

    @property
    def regions(self) -> tuple[Region, ...]:
        """The device's regions, in index order."""
        regions = []
        index = 0
        while True:
            pointer = _capi.libeis.device_get_region(self, index)
            if not pointer:
                break
            region = Region.wrap(pointer)
            assert region is not None
            regions.append(region)
            index += 1
        return tuple(regions)

    @property
    def seat(self) -> Seat:
        """The seat this device belongs to."""
        seat = Seat.wrap(_capi.libeis.device_get_seat(self))
        assert seat is not None
        return seat

    @property
    def keymap(self) -> Keymap | None:
        """The device's keymap, or None if it has no keyboard capability."""
        return Keymap.wrap(_capi.libeis.device_keyboard_get_keymap(self))

    def add(self) -> Device:
        """Publish this object to the client."""
        _capi.libeis.device_add(self)
        return self

    def remove(self) -> Device:
        """Withdraw this object from the client."""
        _capi.libeis.device_remove(self)
        return self

    def pause(self) -> Device:
        """Suspend the device; the client may not send events while paused."""
        _capi.libeis.device_pause(self)
        return self

    def resume(self) -> Device:
        """Resume a paused device, allowing the client to send events again."""
        _capi.libeis.device_resume(self)
        return self

    def keyboard_xkb_modifiers(
        self, depressed: int, latched: int, locked: int, group: int
    ) -> Device:
        """Notify the client of the current XKB modifier state.

        Call this whenever the modifier state or effective group changes,
        for every affected keyboard device.
        """
        _capi.libeis.device_keyboard_send_xkb_modifiers(
            self, depressed, latched, locked, group
        )
        return self

    def start_emulating(self, sequence: int | None = None) -> Device:
        """Begin an emulation transaction; pair with :meth:`stop_emulating`.

        ``sequence`` must go up by at least 1 on each call; the default
        draws from the same process-wide counter as
        :meth:`libei.ei.Device.start_emulating`.
        """
        if sequence is None:
            sequence = _next_emulating_sequence()
        _capi.libeis.device_start_emulating(self, sequence)
        return self

    def stop_emulating(self) -> Device:
        """End the transaction opened by :meth:`start_emulating`."""
        _capi.libeis.device_stop_emulating(self)
        return self

    def frame(self, timestamp: int | None = None) -> Device:
        """Commit the events queued since the last frame as one logical
        hardware event. ``timestamp`` defaults to the context's current time."""
        if timestamp is None:
            timestamp = _capi.libeis.now(_capi.libeis.device_get_context(self))
        _capi.libeis.device_frame(self, timestamp)
        return self

    def pointer_motion(self, dx: float, dy: float) -> Device:
        """Send a relative pointer motion, in logical pixels."""
        _capi.libeis.device_pointer_motion(self, dx, dy)
        return self

    def pointer_motion_absolute(self, x: float, y: float) -> Device:
        """Send an absolute pointer motion, in the device's region."""
        _capi.libeis.device_pointer_motion_absolute(self, x, y)
        return self

    def button(self, button: int, is_press: bool) -> Device:
        """Send a button press or release, by Linux ``BTN_*`` code."""
        _capi.libeis.device_button_button(self, button, is_press)
        return self

    def keyboard_key(self, key: int, is_press: bool) -> Device:
        """Send a key press or release, by Linux ``KEY_*`` keycode."""
        _capi.libeis.device_keyboard_key(self, key, is_press)
        return self

    def scroll_delta(self, dx: float, dy: float) -> Device:
        """Send a smooth scroll, in logical pixels."""
        _capi.libeis.device_scroll_delta(self, dx, dy)
        return self

    def scroll_discrete(self, dx: int, dy: int) -> Device:
        """Send a discrete (detent) scroll; one detent is 120."""
        _capi.libeis.device_scroll_discrete(self, dx, dy)
        return self

    def scroll_stop(self, stop_x: bool, stop_y: bool) -> Device:
        """Signal that scrolling has stopped on the given axes."""
        _capi.libeis.device_scroll_stop(self, stop_x, stop_y)
        return self

    def scroll_cancel(self, cancel_x: bool, cancel_y: bool) -> Device:
        """Signal that scroll kinetics are cancelled on the given axes."""
        _capi.libeis.device_scroll_cancel(self, cancel_x, cancel_y)
        return self

    def touch_new(self) -> Touch:
        """Start a new touch on a device with the TOUCH capability."""
        pointer = _capi.libeis.device_touch_new(self)
        if not pointer:
            raise Error("eis_device_touch_new() returned NULL")
        touch = Touch.adopt(pointer)
        assert touch is not None
        return touch


class Seat(CObject):
    _ref_func = staticmethod(_capi.libeis.seat_ref)
    _unref_func = staticmethod(_capi.libeis.seat_unref)

    def __repr__(self) -> str:
        caps = "|".join(c.name or str(c.value) for c in self.capabilities)
        return f"<Seat {self.name!r} {caps}>"

    @property
    def name(self) -> str:
        """The seat name this server gave the seat."""
        return _capi.libeis.seat_get_name(self).decode("utf-8")

    @property
    def client(self) -> Client:
        """The client this object belongs to."""
        client = Client.wrap(_capi.libeis.seat_get_client(self))
        assert client is not None
        return client

    @property
    def capabilities(self) -> tuple[DeviceCapability, ...]:
        """The capabilities this object actually has."""
        return tuple(
            c for c in DeviceCapability if _capi.libeis.seat_has_capability(self, c)
        )

    def configure_capabilities(
        self, capabilities: tuple[DeviceCapability, ...]
    ) -> Seat:
        """Declare which capabilities this seat offers. Call before add()."""
        for cap in capabilities:
            _capi.libeis.seat_configure_capability(self, cap)
        return self

    def add(self) -> Seat:
        """Publish this object to the client."""
        _capi.libeis.seat_add(self)
        return self

    def remove(self) -> Seat:
        """Withdraw this object from the client."""
        _capi.libeis.seat_remove(self)
        return self

    def new_device(self) -> Device:
        """Create a device on this seat. Configure it, then add()."""
        pointer = _capi.libeis.seat_new_device(self)
        if not pointer:
            raise Error("eis_seat_new_device() returned NULL")
        device = Device.adopt(pointer)
        assert device is not None
        return device


class Client(CObject):
    _ref_func = staticmethod(_capi.libeis.client_ref)
    _unref_func = staticmethod(_capi.libeis.client_unref)

    def __repr__(self) -> str:
        return f"<Client {self.name!r} sender={self.is_sender}>"

    @property
    def is_sender(self) -> bool:
        """Whether the client sends events (rather than receiving them)."""
        return bool(_capi.libeis.client_is_sender(self))

    @property
    def name(self) -> str:
        """The name the client announced for itself."""
        return _capi.libeis.client_get_name(self).decode("utf-8")

    def connect(self) -> None:
        """Accept this client's connection."""
        _capi.libeis.client_connect(self)

    def disconnect(self) -> None:
        """Disconnect this client."""
        _capi.libeis.client_disconnect(self)

    def new_seat(self, name: str) -> Seat:
        """Create a seat to offer this client. Configure it, then add()."""
        pointer = _capi.libeis.client_new_seat(self, name.encode("utf-8"))
        if not pointer:
            raise Error("eis_client_new_seat() returned NULL")
        seat = Seat.adopt(pointer)
        assert seat is not None
        return seat


class Event(CObject):
    _unref_func = staticmethod(_capi.libeis.event_unref)

    def __repr__(self) -> str:
        event_type = self.event_type
        label = event_type.name if isinstance(event_type, EventType) else event_type
        return f"<Event {label}>"

    @property
    def event_type(self) -> EventType | int:
        """The event's type, or a raw int for a value newer than this
        package's :class:`EventType` table -- see its docstring."""
        raw = _capi.libeis.event_get_type(self)
        try:
            return EventType(raw)
        except ValueError:
            return raw

    @property
    def time(self) -> int:
        """Event timestamp in microseconds, in the context's clock domain."""
        return _capi.libeis.event_get_time(self)

    @property
    def client(self) -> Client:
        """The client this object belongs to."""
        client = Client.wrap(_capi.libeis.event_get_client(self))
        assert client is not None
        return client

    @property
    def device(self) -> Device | None:
        """The device this event concerns, or None if it has none."""
        return Device.wrap(_capi.libeis.event_get_device(self))

    @property
    def seat(self) -> Seat | None:
        """The seat this event concerns, or None if it has none.

        Connect/disconnect events carry no seat.
        """
        return Seat.wrap(_capi.libeis.event_get_seat(self))

    @property
    def seat_capabilities(self) -> tuple[DeviceCapability, ...]:
        """Capabilities the client requested, for a SEAT_BIND event."""
        return tuple(
            c
            for c in DeviceCapability
            if _capi.libeis.event_seat_has_capability(self, c)
        )

    @property
    def emulating_sequence(self) -> int:
        """Sequence number of the start_emulating transaction."""
        return _capi.libeis.event_emulating_get_sequence(self)

    @property
    def key_event(self) -> KeyEvent:
        """Key code and press/release state for a KEYBOARD_KEY event."""
        return KeyEvent(
            key=_capi.libeis.event_keyboard_get_key(self),
            is_press=bool(_capi.libeis.event_keyboard_get_key_is_press(self)),
        )

    @property
    def button_event(self) -> ButtonEvent:
        """Button code and press/release state for a BUTTON_BUTTON event."""
        return ButtonEvent(
            button=_capi.libeis.event_button_get_button(self),
            is_press=bool(_capi.libeis.event_button_get_is_press(self)),
        )

    @property
    def pointer_event(self) -> PointerEvent:
        """Relative motion deltas for a POINTER_MOTION event."""
        return PointerEvent(
            dx=_capi.libeis.event_pointer_get_dx(self),
            dy=_capi.libeis.event_pointer_get_dy(self),
        )

    @property
    def pointer_absolute_event(self) -> PointerAbsoluteEvent:
        """Absolute position for a POINTER_MOTION_ABSOLUTE event."""
        return PointerAbsoluteEvent(
            x=_capi.libeis.event_pointer_get_absolute_x(self),
            y=_capi.libeis.event_pointer_get_absolute_y(self),
        )

    @property
    def scroll_event(self) -> ScrollEvent:
        """Smooth scroll deltas for a SCROLL_DELTA event."""
        return ScrollEvent(
            dx=_capi.libeis.event_scroll_get_dx(self),
            dy=_capi.libeis.event_scroll_get_dy(self),
        )

    @property
    def scroll_discrete_event(self) -> ScrollDiscreteEvent:
        """Detent deltas for a SCROLL_DISCRETE event (120 per detent)."""
        return ScrollDiscreteEvent(
            dx=_capi.libeis.event_scroll_get_discrete_dx(self),
            dy=_capi.libeis.event_scroll_get_discrete_dy(self),
        )

    @property
    def scroll_stop_event(self) -> ScrollStopEvent:
        """Which axes stopped, for a SCROLL_STOP/SCROLL_CANCEL event."""
        return ScrollStopEvent(
            stop_x=bool(_capi.libeis.event_scroll_get_stop_x(self)),
            stop_y=bool(_capi.libeis.event_scroll_get_stop_y(self)),
        )

    @property
    def touch_event(self) -> TouchEvent:
        """Touch id and position for a TOUCH_* event."""
        return TouchEvent(
            touchid=_capi.libeis.event_touch_get_id(self),
            x=_capi.libeis.event_touch_get_x(self),
            y=_capi.libeis.event_touch_get_y(self),
        )


def _log_callback(_eis: int, priority: int, message: bytes, _context: int) -> None:
    # See ei.py's _log_callback: look up the raw int, not
    # _LogPriority(priority), which would raise ValueError before .get()'s
    # default could apply -- silently, since this runs inside a ctypes
    # callback.
    level = {
        _LogPriority.DEBUG.value: logging.DEBUG,
        _LogPriority.INFO.value: logging.INFO,
        _LogPriority.WARNING.value: logging.WARNING,
        _LogPriority.ERROR.value: logging.ERROR,
    }.get(priority, logging.DEBUG)
    logger.log(level, message.decode("utf-8", errors="replace"))


_log_handler = log_handler_t(_log_callback)


class Eis(CObject):
    """An EIS server context, accepting one or more client connections."""

    _unref_func = staticmethod(_capi.libeis.unref)
    # Only ever created fresh via _new() inside create_for_fd(), never
    # handed out as a sub-object -- so wrap()/adopt() on this class have no
    # legitimate caller. Blocking them stops a garbage pointer from ever
    # reaching __init__'s log_set_handler()/log_set_priority() calls below,
    # which would otherwise dereference it as a real `struct eis *` and
    # segfault.
    _wrappable = False

    def __init__(self, pointer: int, *, _adopt: bool = False) -> None:
        # _adopt is accepted and forwarded for signature consistency with
        # CObject, but with _wrappable = False, _get_or_create() never
        # actually reaches this constructor -- Eis is always built directly
        # via cls(cls._new()) in create_for_fd().
        super().__init__(pointer, _adopt=_adopt)
        _capi.libeis.log_set_handler(self, _log_handler)
        _capi.libeis.log_set_priority(self, _LogPriority.DEBUG)

    @property
    def fd(self) -> int:
        """File descriptor to poll; readable when :meth:`dispatch` has work."""
        return _capi.libeis.get_fd(self)

    @property
    def events(self) -> Iterator[Event]:
        """Drain currently-queued events.

        Each event is released (unref'd) as soon as this generator resumes
        after yielding it -- see ``ei.Context.events`` for why that timing
        matters (a SYNC event's pong reply is sent precisely on unref, so
        leaving that to Python's own GC timing can silently stall a
        caller).
        """
        while True:
            pointer = _capi.libeis.get_event(self)
            if not pointer:
                break
            event = Event.wrap(pointer)
            assert event is not None
            # See ei.Context.events: try/finally so release() still runs
            # if the caller breaks out of the loop (GeneratorExit at the
            # yield would otherwise skip a bare call placed after it).
            try:
                yield event
            finally:
                event.release()

    @property
    def now(self) -> int:
        """The context's current time, in microseconds."""
        return _capi.libeis.now(self)

    def dispatch(self) -> None:
        """Read from the connection and queue any events that arrive.

        Call this before iterating :attr:`events`, which only drains what
        is already queued."""
        _capi.libeis.dispatch(self)

    def add_client(self) -> int:
        """Mint a new, private fd for one client connection.

        Hand the returned fd to a client's
        :meth:`libei.ei.Sender.create_for_fd` or
        :meth:`libei.ei.Receiver.create_for_fd` -- e.g. across an
        ``os.pipe()``/subprocess boundary, or directly in-process for a
        test. Only valid on a server created with :meth:`create_for_fd`.
        """
        fd = _capi.libeis.backend_fd_add_client(self)
        if fd < 0:
            raise Error(os.strerror(-fd), -fd)
        return fd

    @classmethod
    def _new(cls) -> int:
        pointer = _capi.libeis.new(c_void_p(None))
        if not pointer:
            raise Error("eis_new() returned NULL")
        return pointer

    @classmethod
    def create_for_fd(cls) -> Eis:
        """Create a server using the fd backend -- the one real compositors
        use, since it keeps each client's fd private rather than exposing a
        connectable socket path. Call :meth:`add_client` once per
        connection you want to accept."""
        server = cls(cls._new())
        err = _capi.libeis.setup_backend_fd(server)
        if err < 0:
            raise Error(os.strerror(-err), -err)
        return server

    @classmethod
    def create_for_socket(cls, path: Path) -> Eis:
        """Create a server listening on a Unix socket, as a compositor
        would (this is the path a real ``ei_setup_backend_socket()`` client
        connects to)."""
        server = cls(cls._new())
        err = _capi.libeis.setup_backend_socket(server, os.fspath(path).encode("utf-8"))
        if err < 0:
            raise Error(os.strerror(-err), -err)
        return server


__all__ = [
    "ButtonEvent",
    "Client",
    "ConfigureRegion",
    "Device",
    "DeviceCapability",
    "DeviceType",
    "Eis",
    "Error",
    "Event",
    "EventType",
    "Keymap",
    "KeymapType",
    "PointerAbsoluteEvent",
    "PointerEvent",
    "Region",
    "ScrollDiscreteEvent",
    "ScrollEvent",
    "ScrollStopEvent",
    "Seat",
    "Touch",
    "TouchEvent",
    "is_available",
]
