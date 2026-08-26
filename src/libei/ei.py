"""Pythonic wrapper around libei -- the EI *client* library.

An EI client is either a :class:`Sender` (injects input -- what a remote-
control or automation client wants) or a :class:`Receiver` (consumes input
-- what a compositor implementation wants). Both are :class:`Context`
subclasses.

Typical sender usage. Note the ``dispatch()`` call: :attr:`Context.events`
drains only what is already queued, so it yields nothing until
``dispatch()`` has read from the connection::

    ctx = Sender.create_for_fd(eis_fd, name="my-app")

    device = None
    while device is None:
        ctx.dispatch()
        for event in ctx.events:
            if event.event_type is EventType.SEAT_ADDED:
                event.seat.bind((DeviceCapability.POINTER,))
            elif event.event_type is EventType.DEVICE_RESUMED:
                device = event.device

    device.start_emulating().pointer_motion(5, 0).frame().stop_emulating()

Wait for ``DEVICE_RESUMED``, not ``DEVICE_ADDED``: a device arrives paused,
and libei calls sending events before it resumes "a client bug".

A real client should ``select()`` on :attr:`Context.fd` rather than
spinning, and give up after a timeout; see the README for that form.
"""

from __future__ import annotations

import dataclasses
import enum
import itertools
import logging
import os
from collections.abc import Iterator
from ctypes import c_int, c_void_p
from pathlib import Path
from typing import IO

from . import _capi
from ._capi.libei import log_handler_t
from ._cobject import CObject

logger = logging.getLogger("libei.ei")

# Process-wide rather than per-Device, deliberately. libei requires the
# emulation sequence to increase on every ei_device_start_emulating() call
# for a given device; a counter stored on the Python wrapper would restart
# at 1 whenever that wrapper was garbage-collected and later rebuilt from
# the same C pointer, repeating sequence numbers for a device that is very
# much still alive. Sharing one counter across all devices trivially
# satisfies the per-device requirement and has no such lifetime coupling.
# itertools.count().__next__ is atomic under CPython, so no lock is needed.
_emulating_sequence = itertools.count(1)


def _next_emulating_sequence() -> int:
    # Masked into uint32 to match the C parameter. libei asks callers to
    # keep wraparound detection "reasonable"; skipping 0 keeps the value
    # away from anything that might read as unset.
    return (next(_emulating_sequence) % 0xFFFFFFFF) + 1


def is_available() -> bool:
    """Whether libei.so.1 can be loaded on this system."""
    return _capi.libei.lib.is_available()


class Error(Exception):
    def __init__(self, message: str, errno: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errno = errno


class EventType(enum.IntEnum):
    """Mirrors ``enum ei_event_type`` from libei.h.

    libei's own docs say this enum "is not exhaustive, future versions of
    this library may add new event types" and that unknown events must
    still be released with ``ei_event_unref()``. :attr:`Event.event_type`
    honors that: a value not listed here is returned as a plain ``int``
    rather than raising.
    """

    CONNECT = 1
    DISCONNECT = 2
    SEAT_ADDED = 3
    SEAT_REMOVED = 4
    DEVICE_ADDED = 5
    DEVICE_REMOVED = 6
    DEVICE_PAUSED = 7
    DEVICE_RESUMED = 8
    KEYBOARD_MODIFIERS = 9
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
    # Event *types* below are recognized for completeness/classification,
    # but this package does not (yet) implement data getters for them --
    # see docs/vs-snegg.md. A caller receiving one of these can still
    # identify it and unref it; reading e.g. text_event would be an
    # AttributeError.
    TEXT_KEYSYM = 900
    TEXT_UTF8 = 901
    SWIPE_BEGIN = 1000
    SWIPE_UPDATE = 1001
    SWIPE_END = 1002
    SWIPE_ABORTED = 1003
    PINCH_BEGIN = 1010
    PINCH_UPDATE = 1011
    PINCH_END = 1012
    PINCH_ABORTED = 1013
    HOLD_BEGIN = 1020
    HOLD_END = 1021
    HOLD_ABORTED = 1022
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
class XkbModifiersEvent:
    depressed: int
    latched: int
    locked: int
    group: int


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


class Region(CObject):
    _ref_func = staticmethod(_capi.libei.region_ref)
    _unref_func = staticmethod(_capi.libei.region_unref)

    def __repr__(self) -> str:
        w, h = self.dimension
        x, y = self.position
        return f"<Region {w}x{h}+{x}+{y}>"

    @property
    def position(self) -> tuple[int, int]:
        """Top-left corner of the region, in logical pixels."""
        return (
            _capi.libei.region_get_x(self),
            _capi.libei.region_get_y(self),
        )

    @property
    def dimension(self) -> tuple[int, int]:
        """Width and height of the region, in logical pixels."""
        return (
            _capi.libei.region_get_width(self),
            _capi.libei.region_get_height(self),
        )

    @property
    def physical_scale(self) -> float:
        """Scale between logical pixels and this region's physical size."""
        return _capi.libei.region_get_physical_scale(self)

    def contains(self, x: float, y: float) -> bool:
        """Whether the given logical-pixel point falls inside this region."""
        return bool(_capi.libei.region_contains(self, x, y))


class Keymap(CObject):
    _ref_func = staticmethod(_capi.libei.keymap_ref)
    _unref_func = staticmethod(_capi.libei.keymap_unref)

    @property
    def keymap_type(self) -> KeymapType:
        """Keymap format; currently always XKB."""
        return KeymapType(_capi.libei.keymap_get_type(self))

    @property
    def size(self) -> int:
        """Size of the keymap data, in bytes."""
        return _capi.libei.keymap_get_size(self)

    @property
    def fd(self) -> IO[bytes]:
        """Memmap-able file descriptor holding the keymap data.

        A fresh duplicate on each read, which the caller owns and should
        close; the keymap keeps its own.
        """
        # ei_keymap_get_fd() is a plain field read; the keymap still owns
        # that fd. os.fdopen() would make the returned file object close
        # it, so duplicate it and hand out the copy.
        raw_fd = _capi.libei.keymap_get_fd(self)
        if raw_fd < 0:
            # Without this, os.dup(-1) surfaces as a bare EBADF that says
            # nothing about which object failed.
            raise Error("ei_keymap_get_fd() reported no usable file descriptor")
        return os.fdopen(os.dup(raw_fd), "rb")

    @property
    def device(self) -> Device:
        """The device this keymap belongs to."""
        device = Device.wrap(_capi.libei.keymap_get_device(self))
        assert device is not None
        return device


class Touch(CObject):
    _unref_func = staticmethod(_capi.libei.touch_unref)

    @property
    def device(self) -> Device:
        """The device this touch belongs to."""
        device = Device.wrap(_capi.libei.touch_get_device(self))
        assert device is not None
        return device

    def down(self, x: float, y: float) -> Touch:
        """Begin the touch at the given point."""
        _capi.libei.touch_down(self, x, y)
        return self

    def motion(self, x: float, y: float) -> Touch:
        """Move the in-progress touch to the given point."""
        _capi.libei.touch_motion(self, x, y)
        return self

    def up(self) -> Touch:
        """End the touch."""
        _capi.libei.touch_up(self)
        return self


class Device(CObject):
    _ref_func = staticmethod(_capi.libei.device_ref)
    _unref_func = staticmethod(_capi.libei.device_unref)

    def __repr__(self) -> str:
        caps = "|".join(c.name or str(c.value) for c in self.capabilities)
        return f"<Device {self.name!r} {self.device_type.name} {caps}>"

    @property
    def device_type(self) -> DeviceType:
        """Whether the device is virtual or represents real hardware."""
        return DeviceType(_capi.libei.device_get_type(self))

    @property
    def name(self) -> str:
        """The device name assigned by the server."""
        return _capi.libei.device_get_name(self).decode("utf-8")

    @property
    def width(self) -> int:
        """Device width in logical pixels; 0 if unsized."""
        return _capi.libei.device_get_width(self)

    @property
    def height(self) -> int:
        """Device height in logical pixels; 0 if unsized."""
        return _capi.libei.device_get_height(self)

    @property
    def capabilities(self) -> tuple[DeviceCapability, ...]:
        """The capabilities this object actually has."""
        return tuple(
            c for c in DeviceCapability if _capi.libei.device_has_capability(self, c)
        )

    @property
    def regions(self) -> tuple[Region, ...]:
        """The device's regions, in index order."""
        regions = []
        index = 0
        while True:
            pointer = _capi.libei.device_get_region(self, index)
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
        seat = Seat.wrap(_capi.libei.device_get_seat(self))
        assert seat is not None
        return seat

    @property
    def keymap(self) -> Keymap | None:
        """The device's keymap, or None if it has no keyboard capability."""
        return Keymap.wrap(_capi.libei.device_keyboard_get_keymap(self))

    def close(self) -> None:
        """Ask the server to remove this device."""
        _capi.libei.device_close(self)

    def start_emulating(self, sequence: int | None = None) -> Device:
        """Begin an emulation transaction; pair with :meth:`stop_emulating`.

        ``sequence`` identifies the transaction and, per libei, "must go up
        by at least 1 on each call". The default draws from a process-wide
        counter that satisfies that for every device.
        """
        if sequence is None:
            sequence = _next_emulating_sequence()
        _capi.libei.device_start_emulating(self, sequence)
        return self

    def stop_emulating(self) -> Device:
        """End the transaction opened by :meth:`start_emulating`."""
        _capi.libei.device_stop_emulating(self)
        return self

    def frame(self, timestamp: int | None = None) -> Device:
        """Commit the events queued since the last frame as one logical
        hardware event. ``timestamp`` defaults to the context's current
        time."""
        if timestamp is None:
            timestamp = _capi.libei.now(_capi.libei.device_get_context(self))
        _capi.libei.device_frame(self, timestamp)
        return self

    def pointer_motion(self, dx: float, dy: float) -> Device:
        """Queue a relative pointer motion, in logical pixels."""
        _capi.libei.device_pointer_motion(self, dx, dy)
        return self

    def pointer_motion_absolute(self, x: float, y: float) -> Device:
        """Queue an absolute pointer motion, in the device's region."""
        _capi.libei.device_pointer_motion_absolute(self, x, y)
        return self

    def button(self, button: int, is_press: bool) -> Device:
        """Queue a button press or release. ``button`` is a Linux
        ``BTN_*`` code (e.g. ``0x110`` for ``BTN_LEFT``)."""
        _capi.libei.device_button_button(self, button, is_press)
        return self

    def keyboard_key(self, key: int, is_press: bool) -> Device:
        """Queue a key press or release, by Linux ``KEY_*`` keycode."""
        _capi.libei.device_keyboard_key(self, key, is_press)
        return self

    def scroll_delta(self, dx: float, dy: float) -> Device:
        """Queue a smooth scroll, in logical pixels."""
        _capi.libei.device_scroll_delta(self, dx, dy)
        return self

    def scroll_discrete(self, dx: int, dy: int) -> Device:
        """Queue a discrete (detent) scroll; one detent is 120."""
        _capi.libei.device_scroll_discrete(self, dx, dy)
        return self

    def scroll_stop(self, stop_x: bool, stop_y: bool) -> Device:
        """Signal that scrolling has stopped on the given axes."""
        _capi.libei.device_scroll_stop(self, stop_x, stop_y)
        return self

    def scroll_cancel(self, cancel_x: bool, cancel_y: bool) -> Device:
        """Signal that scroll kinetics are cancelled on the given axes."""
        _capi.libei.device_scroll_cancel(self, cancel_x, cancel_y)
        return self

    def touch_new(self) -> Touch:
        """Start a new touch on a device with the TOUCH capability."""
        pointer = _capi.libei.device_touch_new(self)
        if not pointer:
            raise Error("ei_device_touch_new() returned NULL")
        touch = Touch.adopt(pointer)
        assert touch is not None
        return touch


class Seat(CObject):
    _ref_func = staticmethod(_capi.libei.seat_ref)
    _unref_func = staticmethod(_capi.libei.seat_unref)

    def __repr__(self) -> str:
        caps = "|".join(c.name or str(c.value) for c in self.capabilities)
        return f"<Seat {self.name!r} {caps}>"

    @property
    def name(self) -> str:
        """The seat name assigned by the server."""
        return _capi.libei.seat_get_name(self).decode("utf-8")

    @property
    def capabilities(self) -> tuple[DeviceCapability, ...]:
        """The capabilities this object actually has."""
        return tuple(
            c for c in DeviceCapability if _capi.libei.seat_has_capability(self, c)
        )

    def bind(self, capabilities: tuple[DeviceCapability, ...]) -> None:
        """Request these capabilities from the seat.

        The server responds by adding matching devices, surfacing as
        DEVICE_ADDED events. Raises :class:`ValueError` if given no
        capabilities: that would send nothing, and the caller would wait
        for devices that are never coming.
        """
        if not capabilities:
            raise ValueError(
                "bind() needs at least one capability; binding an empty set "
                "sends nothing and no DEVICE_ADDED event will ever arrive"
            )
        # ei_seat_bind_capabilities is variadic, one *individual* capability
        # value per vararg, sentinel-terminated -- the C side reads them
        # with va_arg and switches on each exact value. Passing a single
        # OR'd mask (e.g. POINTER|KEYBOARD) matches no case, silently binds
        # nothing, and the caller hangs waiting for DEVICE_ADDED.
        _capi.libei.seat_bind_capabilities(
            self, *(c_int(c) for c in capabilities), c_int(0)
        )

    def unbind(self, capabilities: tuple[DeviceCapability, ...]) -> None:
        """Release previously bound capabilities on this seat.

        Raises :class:`ValueError` if given no capabilities, for the same
        reason as :meth:`bind`.
        """
        if not capabilities:
            raise ValueError("unbind() needs at least one capability")
        _capi.libei.seat_unbind_capabilities(
            self, *(c_int(c) for c in capabilities), c_int(0)
        )


class Event(CObject):
    _unref_func = staticmethod(_capi.libei.event_unref)

    def __repr__(self) -> str:
        event_type = self.event_type
        label = event_type.name if isinstance(event_type, EventType) else event_type
        return f"<Event {label}>"

    @property
    def event_type(self) -> EventType | int:
        """The event's type, or a raw int for a value newer than this
        package's :class:`EventType` table -- see its docstring."""
        raw = _capi.libei.event_get_type(self)
        try:
            return EventType(raw)
        except ValueError:
            return raw

    @property
    def time(self) -> int:
        """Event timestamp in microseconds, in the context's clock domain."""
        return _capi.libei.event_get_time(self)

    @property
    def device(self) -> Device | None:
        """The device this event concerns, or None if it has none."""
        return Device.wrap(_capi.libei.event_get_device(self))

    @property
    def seat(self) -> Seat | None:
        """The seat this event concerns, or None if it has none.

        Connect/disconnect events carry no seat.
        """
        return Seat.wrap(_capi.libei.event_get_seat(self))

    @property
    def emulating_sequence(self) -> int:
        """Sequence number of the start_emulating transaction."""
        return _capi.libei.event_emulating_get_sequence(self)

    @property
    def keyboard_xkb_modifiers(self) -> XkbModifiersEvent:
        """XKB modifier state for a KEYBOARD_MODIFIERS event."""
        return XkbModifiersEvent(
            depressed=_capi.libei.event_keyboard_get_xkb_mods_depressed(self),
            latched=_capi.libei.event_keyboard_get_xkb_mods_latched(self),
            locked=_capi.libei.event_keyboard_get_xkb_mods_locked(self),
            group=_capi.libei.event_keyboard_get_xkb_group(self),
        )

    @property
    def key_event(self) -> KeyEvent:
        """Key code and press/release state for a KEYBOARD_KEY event."""
        return KeyEvent(
            key=_capi.libei.event_keyboard_get_key(self),
            is_press=bool(_capi.libei.event_keyboard_get_key_is_press(self)),
        )

    @property
    def button_event(self) -> ButtonEvent:
        """Button code and press/release state for a BUTTON_BUTTON event."""
        return ButtonEvent(
            button=_capi.libei.event_button_get_button(self),
            is_press=bool(_capi.libei.event_button_get_is_press(self)),
        )

    @property
    def pointer_event(self) -> PointerEvent:
        """Relative motion deltas for a POINTER_MOTION event."""
        return PointerEvent(
            dx=_capi.libei.event_pointer_get_dx(self),
            dy=_capi.libei.event_pointer_get_dy(self),
        )

    @property
    def pointer_absolute_event(self) -> PointerAbsoluteEvent:
        """Absolute position for a POINTER_MOTION_ABSOLUTE event."""
        return PointerAbsoluteEvent(
            x=_capi.libei.event_pointer_get_absolute_x(self),
            y=_capi.libei.event_pointer_get_absolute_y(self),
        )

    @property
    def scroll_event(self) -> ScrollEvent:
        """Smooth scroll deltas for a SCROLL_DELTA event."""
        return ScrollEvent(
            dx=_capi.libei.event_scroll_get_dx(self),
            dy=_capi.libei.event_scroll_get_dy(self),
        )

    @property
    def scroll_discrete_event(self) -> ScrollDiscreteEvent:
        """Detent deltas for a SCROLL_DISCRETE event (120 per detent)."""
        return ScrollDiscreteEvent(
            dx=_capi.libei.event_scroll_get_discrete_dx(self),
            dy=_capi.libei.event_scroll_get_discrete_dy(self),
        )

    @property
    def scroll_stop_event(self) -> ScrollStopEvent:
        """Which axes stopped, for a SCROLL_STOP/SCROLL_CANCEL event."""
        return ScrollStopEvent(
            stop_x=bool(_capi.libei.event_scroll_get_stop_x(self)),
            stop_y=bool(_capi.libei.event_scroll_get_stop_y(self)),
        )

    @property
    def touch_event(self) -> TouchEvent:
        """Touch id and position for a TOUCH_* event."""
        return TouchEvent(
            touchid=_capi.libei.event_touch_get_id(self),
            x=_capi.libei.event_touch_get_x(self),
            y=_capi.libei.event_touch_get_y(self),
        )


def _log_callback(_ei: int, priority: int, message: bytes, _context: int) -> None:
    # Look up the raw int, not _LogPriority(priority): constructing the
    # enum from an unrecognized value raises ValueError immediately, which
    # would happen *before* .get()'s default ever gets a chance to apply
    # -- and inside a ctypes callback, that exception is silently dropped
    # (printed to stderr) rather than propagated, so the log line is just
    # lost instead of falling back to DEBUG. Keyed by .value (plain int)
    # rather than the enum members themselves so mypy accepts a plain-int
    # lookup key too.
    level = {
        _LogPriority.DEBUG.value: logging.DEBUG,
        _LogPriority.INFO.value: logging.INFO,
        _LogPriority.WARNING.value: logging.WARNING,
        _LogPriority.ERROR.value: logging.ERROR,
    }.get(priority, logging.DEBUG)
    logger.log(level, message.decode("utf-8", errors="replace"))


# Kept as a module-level reference: ctypes does not keep a CFUNCTYPE callback
# alive on the C side, so letting this get garbage-collected would leave
# libei holding a dangling function pointer.
_log_handler = log_handler_t(_log_callback)


class Context(CObject):
    _unref_func = staticmethod(_capi.libei.unref)
    # Only ever created fresh via _new() inside create_for_fd()/
    # create_for_socket(), never handed out as a sub-object -- so wrap()/
    # adopt() on this class (and Sender/Receiver below) have no legitimate
    # caller. Blocking them stops a garbage pointer from ever reaching
    # __init__'s log_set_handler()/log_set_priority() calls below, which
    # would otherwise dereference it as a real `struct ei *` and segfault.
    _wrappable = False

    def __init__(self, pointer: int, *, _adopt: bool = False) -> None:
        # _adopt is accepted and forwarded for signature consistency with
        # CObject, but with _wrappable = False, _get_or_create() never
        # actually reaches this constructor -- Context (and Sender/
        # Receiver) are always built directly via cls(cls._new()) in
        # create_for_fd()/create_for_socket().
        super().__init__(pointer, _adopt=_adopt)
        self._name: str | None = None
        _capi.libei.log_set_handler(self, _log_handler)
        _capi.libei.log_set_priority(self, _LogPriority.DEBUG)

    def set_name(self, name: str) -> Context:
        """Set the client name announced to the server. Call before connecting."""
        self._name = name
        _capi.libei.configure_name(self, name.encode("utf-8"))
        return self

    @property
    def name(self) -> str | None:
        """The client name set via :meth:`set_name`, if any."""
        return self._name

    @property
    def fd(self) -> int:
        """File descriptor to poll; readable when :meth:`dispatch` has work.

        Only valid once a backend is set up (which the ``create_for_*``
        constructors do before returning); before that libei reports -1.
        """
        # Deliberately not memoized. ei_get_fd() is a plain field read, and
        # caching it meant a read taken before set_fd()/set_socket() -- now
        # reachable, since Context can be obtained via wrap() -- would pin
        # the pre-setup -1 for the object's whole life.
        return _capi.libei.get_fd(self)

    @property
    def events(self) -> Iterator[Event]:
        """Drain currently-queued events.

        Each event is released (unref'd) as soon as this generator resumes
        after yielding it -- do not hold a reference past the loop
        iteration that receives it. This matters beyond just memory: a
        SYNC event's pong reply is sent by libei precisely when the event
        is unref'd, so leaving that to Python's own GC timing (which, for
        a bare ``for event in ctx.events:`` loop, may not happen until the
        loop variable is next reassigned -- possibly never, if that event
        turns out to be the last one in a batch) can silently stall a
        caller waiting on that reply.
        """
        while True:
            pointer = _capi.libei.get_event(self)
            if not pointer:
                break
            event = Event.wrap(pointer)
            assert event is not None
            # try/finally, not a bare call after yield: breaking out of a
            # `for event in ctx.events:` loop (or an exception propagating
            # through it) throws GeneratorExit in at the yield and unwinds
            # this frame immediately -- release() right after wouldn't run.
            try:
                yield event
            finally:
                event.release()

    @property
    def now(self) -> int:
        """The context's current time, in microseconds."""
        return _capi.libei.now(self)

    def set_fd(self, fd: IO[bytes] | int) -> Context:
        """Use an already-connected socket as the transport.

        libei takes ownership of a raw int fd and closes it itself; a file
        object is duplicated first, so the caller's own object stays valid."""
        # ei_setup_backend_fd() takes ownership of the fd and will close it
        # itself. A raw int is assumed to already be one the caller is
        # handing off (matching what eis.Eis.add_client()/oeffis.eis_fd
        # return); a file object still thinks it owns its own fd and would
        # close it again later -- possibly a *different*, since-reused fd
        # number by then -- so duplicate it rather than handing over the
        # original.
        raw_fd = fd if isinstance(fd, int) else os.dup(fd.fileno())
        err = _capi.libei.setup_backend_fd(self, raw_fd)
        if err < 0:
            raise Error(os.strerror(-err), -err)
        return self

    def set_socket(self, path: Path | None) -> Context:
        """Connect to an EIS socket by path.

        ``None`` uses ``$LIBEI_SOCKET``; a relative path is resolved
        against ``$XDG_RUNTIME_DIR``."""
        encoded = os.fspath(path).encode("utf-8") if path else None
        err = _capi.libei.setup_backend_socket(self, encoded)
        if err < 0:
            raise Error(os.strerror(-err), -err)
        return self

    def dispatch(self) -> None:
        """Read from the connection and queue any events that arrive.

        Call this before iterating :attr:`events`, which only drains what
        is already queued."""
        _capi.libei.dispatch(self)


class Sender(Context):
    """An EI client that injects input -- e.g. remote-control automation."""

    @classmethod
    def _new(cls) -> int:
        pointer = _capi.libei.new_sender(c_void_p(None))
        if not pointer:
            raise Error("ei_new_sender() returned NULL")
        return pointer

    @classmethod
    def create_for_fd(cls, fd: IO[bytes] | int, name: str | None = None) -> Sender:
        """Create a context speaking EI over an already-connected fd."""
        return cls(cls._new()).set_name(name or "unnamed").set_fd(fd)  # type: ignore[return-value]

    @classmethod
    def create_for_socket(
        cls, path: Path | None = None, name: str | None = None
    ) -> Sender:
        """Create a context connecting to an EIS socket by path."""
        return cls(cls._new()).set_name(name or "unnamed").set_socket(path)  # type: ignore[return-value]


class Receiver(Context):
    """An EI client that consumes input -- e.g. a compositor-side test."""

    @classmethod
    def _new(cls) -> int:
        pointer = _capi.libei.new_receiver(c_void_p(None))
        if not pointer:
            raise Error("ei_new_receiver() returned NULL")
        return pointer

    @classmethod
    def create_for_fd(cls, fd: IO[bytes] | int, name: str | None = None) -> Receiver:
        """Create a context speaking EI over an already-connected fd."""
        return cls(cls._new()).set_name(name or "unnamed").set_fd(fd)  # type: ignore[return-value]

    @classmethod
    def create_for_socket(
        cls, path: Path | None = None, name: str | None = None
    ) -> Receiver:
        """Create a context connecting to an EIS socket by path."""
        return cls(cls._new()).set_name(name or "unnamed").set_socket(path)  # type: ignore[return-value]


__all__ = [
    "ButtonEvent",
    "Context",
    "Device",
    "DeviceCapability",
    "DeviceType",
    "Error",
    "Event",
    "EventType",
    "Keymap",
    "KeymapType",
    "PointerAbsoluteEvent",
    "PointerEvent",
    "Receiver",
    "Region",
    "ScrollDiscreteEvent",
    "ScrollEvent",
    "ScrollStopEvent",
    "Seat",
    "Sender",
    "Touch",
    "TouchEvent",
    "XkbModifiersEvent",
    "is_available",
]
