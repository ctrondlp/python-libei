"""Unit tests for libei.ei's Python object model.

Every native call is monkeypatched to a fake at the ``_capi.libei`` function
level, so these test the wrapper logic (property mapping, dataclass
construction, method chaining, bitmask math) in complete isolation from the
real library -- they run the same with or without libei installed.
"""

from __future__ import annotations

import gc
import os
from ctypes import c_int

import pytest

from libei import _capi, ei


@pytest.fixture(autouse=True)
def _isolated_instance_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each CObject subclass keeps a class-level weak-value cache keyed by
    # pointer value; tests reuse small fake pointer values like 0x1, so
    # without isolation a leftover entry from one test could leak into the
    # next and return the wrong wrapper.
    #
    # ref/unref are also stubbed out here: these tests wrap fake pointers
    # like 0x1 that were never returned by the real library. If libei
    # happens to be installed on the machine running the tests, a real
    # weakref-triggered ei_*_unref() call on such a pointer at GC time would
    # dereference garbage -- a real crash risk, not just a wrong result.
    for cls in (
        ei.Context,
        ei.Event,
        ei.Seat,
        ei.Device,
        ei.Region,
        ei.Keymap,
        ei.Touch,
    ):
        monkeypatch.setattr(cls, "_instances", type(cls._instances)())
        # staticmethod() matters, not just style: a plain function stored
        # as a class attribute is a descriptor, so `self._ref_func` would
        # bind it and silently pass `self` as an extra argument -- which is
        # exactly the class of bug this project exists to avoid. Skipping
        # the wrapper here reintroduces it in the test fixture itself.
        if cls._ref_func is not None:
            monkeypatch.setattr(cls, "_ref_func", staticmethod(lambda p: None))
        if cls._unref_func is not None:
            monkeypatch.setattr(cls, "_unref_func", staticmethod(lambda p: None))


def test_event_type_enum_matches_protocol_values() -> None:
    # These integers are the actual wire values libei uses; a typo here
    # would silently misclassify every event of that type.
    assert ei.EventType.CONNECT.value == 1
    assert ei.EventType.DEVICE_ADDED.value == 5
    assert ei.EventType.KEYBOARD_MODIFIERS.value == 9
    assert ei.EventType.FRAME.value == 100
    assert ei.EventType.POINTER_MOTION.value == 300
    assert ei.EventType.POINTER_MOTION_ABSOLUTE.value == 400
    assert ei.EventType.BUTTON_BUTTON.value == 500
    assert ei.EventType.KEYBOARD_KEY.value == 700
    assert ei.EventType.TOUCH_DOWN.value == 800
    # PONG=90/SYNC=91 sit between KEYBOARD_MODIFIERS and FRAME in the real
    # protocol; a live GNOME/Mutter session sent a SYNC event during normal
    # connection setup even though nothing called ei_ping() explicitly,
    # which is what caught this table being incomplete in the first place.
    assert ei.EventType.PONG.value == 90
    assert ei.EventType.SYNC.value == 91


def test_event_type_handles_a_value_outside_the_known_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # libei's own docs: "this enum is not exhaustive, future versions of
    # this library may add new event types" -- a value this table doesn't
    # know about must not crash the caller, per that documented contract.
    monkeypatch.setattr(_capi.libei, "event_get_type", lambda p: 123456)
    event = ei.Event.wrap(0x1)
    assert event is not None
    assert event.event_type == 123456
    assert not isinstance(event.event_type, ei.EventType)


def test_device_capability_flags_are_distinct_bits() -> None:
    flags = list(ei.DeviceCapability)
    values = [f.value for f in flags]
    assert len(values) == len(set(values)), "capability flags must not overlap"
    combined = 0
    for v in values:
        combined |= v
    assert combined == sum(values), "flags must each be a single bit"


def test_event_get_type_wraps_result_in_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_capi.libei, "event_get_type", lambda p: 300)
    event = ei.Event.wrap(0x1)
    assert event is not None
    assert event.event_type is ei.EventType.POINTER_MOTION


def test_pointer_event_reads_dx_dy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_capi.libei, "event_pointer_get_dx", lambda p: 1.5)
    monkeypatch.setattr(_capi.libei, "event_pointer_get_dy", lambda p: -2.5)
    event = ei.Event.wrap(0x1)
    assert event is not None
    assert event.pointer_event == ei.PointerEvent(dx=1.5, dy=-2.5)


def test_key_event_reads_key_and_press_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_capi.libei, "event_keyboard_get_key", lambda p: 30)
    monkeypatch.setattr(_capi.libei, "event_keyboard_get_key_is_press", lambda p: 1)
    event = ei.Event.wrap(0x1)
    assert event is not None
    assert event.key_event == ei.KeyEvent(key=30, is_press=True)


def test_device_capabilities_filters_by_has_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    granted = {ei.DeviceCapability.POINTER, ei.DeviceCapability.BUTTON}
    monkeypatch.setattr(
        _capi.libei,
        "device_has_capability",
        lambda p, cap: ei.DeviceCapability(cap) in granted,
    )
    device = ei.Device.wrap(0x1)
    assert device is not None
    assert set(device.capabilities) == granted


def test_device_method_chain_returns_self(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        _capi.libei,
        "device_start_emulating",
        lambda p, seq: calls.append(("start_emulating", seq)),
    )
    monkeypatch.setattr(
        _capi.libei,
        "device_pointer_motion",
        lambda p, dx, dy: calls.append(("pointer_motion", dx, dy)),
    )
    monkeypatch.setattr(
        _capi.libei, "device_frame", lambda p, ts: calls.append(("frame", ts))
    )
    monkeypatch.setattr(
        _capi.libei, "device_stop_emulating", lambda p: calls.append(("stop",))
    )

    device = ei.Device.wrap(0x1)
    assert device is not None
    result = (
        device.start_emulating(sequence=42)
        .pointer_motion(3, 4)
        .frame(timestamp=100)
        .stop_emulating()
    )

    assert result is device
    assert calls == [
        ("start_emulating", 42),
        ("pointer_motion", 3, 4),
        ("frame", 100),
        ("stop",),
    ]


def test_seat_bind_passes_one_capability_per_vararg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ei_seat_bind_capabilities() is variadic: the C side reads one
    # capability at a time with va_arg and switches on each *exact* value,
    # sentinel-terminated with 0. An earlier version of this binding OR'd
    # the capabilities into a single mask and passed that as one vararg --
    # POINTER|KEYBOARD matches no case in the C switch, so it silently
    # bound nothing at all. The real call passes ctypes.c_int instances
    # (see ei.py's Seat.bind); a plain-function mock bypasses ctypes
    # marshalling entirely, so it receives those c_int objects as-is --
    # unwrap with `.value`, not `int(...)` (which goes through c_int's
    # buffer protocol instead and raises a confusing ValueError).
    captured_args: tuple[c_int, ...] = ()

    def fake_bind(pointer: int, *args: c_int) -> None:
        nonlocal captured_args
        captured_args = args

    monkeypatch.setattr(_capi.libei, "seat_bind_capabilities", fake_bind)

    seat = ei.Seat.wrap(0x1)
    assert seat is not None
    seat.bind((ei.DeviceCapability.POINTER, ei.DeviceCapability.KEYBOARD))

    values = [arg.value for arg in captured_args]
    assert values == [
        ei.DeviceCapability.POINTER,
        ei.DeviceCapability.KEYBOARD,
        0,
    ]


def test_context_events_stops_at_null_and_wraps_pointers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = [0x10, 0x20, 0]

    def fake_get_event(pointer: int) -> int:
        return queue.pop(0)

    monkeypatch.setattr(_capi.libei, "get_event", fake_get_event)
    monkeypatch.setattr(_capi.libei, "event_unref", lambda p: None)
    monkeypatch.setattr(_capi.libei, "log_set_handler", lambda p, h: None)
    monkeypatch.setattr(_capi.libei, "log_set_priority", lambda p, prio: None)

    ctx = ei.Context(0x1)
    # Read _as_parameter_ inside the comprehension body, i.e. before the
    # generator resumes and releases that event -- events.events releases
    # each event right after yielding it (see the next test), so
    # collecting events into a list first and inspecting them afterward
    # would be reading already-released objects.
    pointers_seen = [e._as_parameter_ for e in ctx.events]
    assert pointers_seen == [0x10, 0x20]


def test_context_events_releases_each_event_after_yielding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = [0x10, 0]
    unreffed: list[int] = []

    def fake_get_event(pointer: int) -> int:
        return queue.pop(0)

    monkeypatch.setattr(_capi.libei, "get_event", fake_get_event)
    # Event._unref_func is captured once, at Event.__init__ time, as
    # whatever the class attribute currently is -- not a live lookup of
    # _capi.libei.event_unref on every call. Patching that module
    # attribute after the fact (as the autouse fixture above already does,
    # for safety) wouldn't affect it; patch the class attribute directly
    # to observe what release() actually calls.
    monkeypatch.setattr(ei.Event, "_unref_func", staticmethod(unreffed.append))
    monkeypatch.setattr(_capi.libei, "log_set_handler", lambda p, h: None)
    monkeypatch.setattr(_capi.libei, "log_set_priority", lambda p, prio: None)

    ctx = ei.Context(0x1)
    events = list(ctx.events)

    assert unreffed == [0x10]
    with pytest.raises(RuntimeError, match="already been released"):
        _ = events[0]._as_parameter_


def test_context_events_releases_on_early_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # try/finally around the yield: breaking out of a `for` loop throws
    # GeneratorExit in at the yield point, which would skip a bare
    # `event.release()` placed after it.
    queue = [0x10, 0x20, 0]
    unreffed: list[int] = []

    def fake_get_event(pointer: int) -> int:
        return queue.pop(0)

    monkeypatch.setattr(_capi.libei, "get_event", fake_get_event)
    monkeypatch.setattr(ei.Event, "_unref_func", staticmethod(unreffed.append))
    monkeypatch.setattr(_capi.libei, "log_set_handler", lambda p, h: None)
    monkeypatch.setattr(_capi.libei, "log_set_priority", lambda p, prio: None)

    ctx = ei.Context(0x1)
    for _event in ctx.events:
        break

    assert unreffed == [0x10]


def test_keymap_fd_dups_so_it_can_be_closed_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ei_keymap_get_fd() is a plain field read -- the keymap still owns
    # that fd and will close it itself. Keymap.fd must hand out a
    # duplicate each time, not the original, or closing one returned file
    # object (or reading the property twice) would close a fd libei still
    # needs, or later close an unrelated, since-recycled fd number.
    real_fd, write_fd = os.pipe()
    try:
        monkeypatch.setattr(_capi.libei, "keymap_get_fd", lambda p: real_fd)

        keymap = ei.Keymap.wrap(0x1)
        assert keymap is not None

        f1 = keymap.fd
        f2 = keymap.fd
        try:
            assert f1.fileno() != real_fd
            assert f2.fileno() != real_fd
            assert f1.fileno() != f2.fileno()

            f1.close()
            os.fstat(real_fd)  # still open: closing the dup didn't touch it
            f2.close()
            os.fstat(real_fd)  # still open after closing the second dup too
        finally:
            for f in (f1, f2):
                try:
                    f.close()
                except OSError:
                    pass
    finally:
        os.close(real_fd)
        os.close(write_fd)


def test_device_start_emulating_default_sequence_increases_monotonically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # libei.h: the sequence "must go up by at least 1 on each call".
    # Absolute values aren't part of the contract (the counter is
    # process-wide, so other tests advance it) -- only that it climbs.
    sequences: list[int] = []
    monkeypatch.setattr(
        _capi.libei,
        "device_start_emulating",
        lambda p, seq: sequences.append(seq),
    )
    monkeypatch.setattr(_capi.libei, "device_stop_emulating", lambda p: None)

    device = ei.Device.wrap(0x1)
    assert device is not None
    device.start_emulating()
    device.stop_emulating()
    device.start_emulating()
    device.start_emulating()

    assert all(b > a for a, b in zip(sequences, sequences[1:], strict=False)), sequences


def test_device_start_emulating_survives_wrapper_garbage_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The counter used to live on the Python wrapper, so dropping the last
    # reference and re-wrapping the *same* C pointer restarted it at 1 and
    # replayed sequence numbers for a device that was still very much
    # alive. A process-wide counter has no such lifetime coupling.
    sequences: list[int] = []
    monkeypatch.setattr(
        _capi.libei,
        "device_start_emulating",
        lambda p, seq: sequences.append(seq),
    )

    device = ei.Device.wrap(0x1)
    assert device is not None
    device.start_emulating()
    device.start_emulating()

    del device
    gc.collect()

    revived = ei.Device.wrap(0x1)
    assert revived is not None
    revived.start_emulating()

    assert all(b > a for a, b in zip(sequences, sequences[1:], strict=False)), sequences


def test_device_start_emulating_explicit_sequence_is_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequences: list[int] = []
    monkeypatch.setattr(
        _capi.libei,
        "device_start_emulating",
        lambda p, seq: sequences.append(seq),
    )

    device = ei.Device.wrap(0x1)
    assert device is not None
    device.start_emulating(sequence=100)

    assert sequences == [100]


def test_device_start_emulating_never_repeats_across_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One shared counter, so no two start_emulating() calls anywhere in
    # the process can collide -- which satisfies the per-device
    # requirement for free.
    calls: list[int] = []
    monkeypatch.setattr(
        _capi.libei,
        "device_start_emulating",
        lambda p, seq: calls.append(seq),
    )

    device_a = ei.Device.wrap(0x1)
    device_b = ei.Device.wrap(0x2)
    assert device_a is not None
    assert device_b is not None

    device_a.start_emulating()
    device_b.start_emulating()
    device_a.start_emulating()

    assert len(set(calls)) == len(calls), calls


def test_set_fd_dups_a_file_object_before_handing_it_to_libei(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ei_setup_backend_fd() takes ownership of whatever fd it's given. A
    # Python file object still believes it owns its own fd and will close
    # it later -- handing over the original (rather than a dup) means that
    # close() would fight libei for the same fd, and could end up closing
    # a completely unrelated, since-recycled fd number.
    real_fd, write_fd = os.pipe()
    f = os.fdopen(real_fd, "rb")
    captured: dict[str, int] = {}
    try:
        monkeypatch.setattr(_capi.libei, "log_set_handler", lambda p, h: None)
        monkeypatch.setattr(_capi.libei, "log_set_priority", lambda p, prio: None)

        def fake_setup(pointer: int, fd: int) -> int:
            captured["fd"] = fd
            return 0

        monkeypatch.setattr(_capi.libei, "setup_backend_fd", fake_setup)

        ctx = ei.Context(0x1)
        ctx.set_fd(f)

        assert captured["fd"] != f.fileno()
        os.fstat(f.fileno())  # f's own fd is untouched, safe to close normally
        os.close(captured["fd"])
    finally:
        f.close()
        os.close(write_fd)


def test_set_fd_passes_a_raw_int_through_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A plain int is assumed to already be a fd the caller is handing off
    # (matching what eis.Eis.add_client()/oeffis.eis_fd return) -- no dup.
    captured: dict[str, int] = {}
    monkeypatch.setattr(_capi.libei, "log_set_handler", lambda p, h: None)
    monkeypatch.setattr(_capi.libei, "log_set_priority", lambda p, prio: None)

    def fake_setup(pointer: int, fd: int) -> int:
        captured["fd"] = fd
        return 0

    monkeypatch.setattr(_capi.libei, "setup_backend_fd", fake_setup)

    ctx = ei.Context(0x1)
    ctx.set_fd(42)

    assert captured["fd"] == 42


def test_keymap_fd_reports_a_useful_error_when_the_fd_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # os.dup(-1) raises a bare EBADF that names nothing; callers could not
    # tell which object failed or why.
    monkeypatch.setattr(_capi.libei, "keymap_get_fd", lambda p: -1)
    keymap = ei.Keymap.wrap(0x1)
    assert keymap is not None
    with pytest.raises(ei.Error, match="keymap_get_fd"):
        _ = keymap.fd


def test_seat_bind_rejects_an_empty_capability_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty tuple sends only the sentinel, so libei binds nothing and
    # the caller waits forever for a DEVICE_ADDED that cannot arrive --
    # the same silent hang as the OR'd-mask bug.
    called: list[object] = []
    monkeypatch.setattr(
        _capi.libei, "seat_bind_capabilities", lambda p, *a: called.append(a)
    )
    monkeypatch.setattr(
        _capi.libei, "seat_unbind_capabilities", lambda p, *a: called.append(a)
    )

    seat = ei.Seat.wrap(0x1)
    assert seat is not None

    with pytest.raises(ValueError, match="at least one capability"):
        seat.bind(())
    with pytest.raises(ValueError, match="at least one capability"):
        seat.unbind(())

    assert not called, "nothing should reach the C library"


def test_context_fd_is_not_memoized(monkeypatch: pytest.MonkeyPatch) -> None:
    # fd used to be cached on first read. A read taken before the backend
    # was set up -- reachable now that Context can be obtained via wrap()
    # -- would pin libei's pre-setup -1 for the object's whole life.
    values = iter([-1, 7, 7])
    monkeypatch.setattr(_capi.libei, "get_fd", lambda p: next(values))
    monkeypatch.setattr(_capi.libei, "log_set_handler", lambda p, h: None)
    monkeypatch.setattr(_capi.libei, "log_set_priority", lambda p, prio: None)

    ctx = ei.Context(0x1)
    assert ctx.fd == -1  # before setup
    assert ctx.fd == 7  # after setup, the new value is visible
