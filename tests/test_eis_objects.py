"""Unit tests for libei.eis's Python object model.

Focuses on the pieces that don't just mirror libei.ei: the fd-backend
add_client() return-value handling (the exact shape of bug this project
exists to avoid -- see _capi/libeis.py's module docstring) and
Device.configure()'s region/capability setup, which has no equivalent on
the client side.
"""

from __future__ import annotations

import os

import pytest

from libei import _capi, ei, eis


@pytest.fixture(autouse=True)
def _isolated_instance_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    for cls in (
        eis.Eis,
        eis.Event,
        eis.Client,
        eis.Seat,
        eis.Device,
        eis.Region,
        eis.Keymap,
        eis.Touch,
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
    # Eis.__init__ always calls these two on construction. Tests below
    # construct Eis with fake pointers (0x1) that were never returned by
    # the real library -- letting a real, installed libeis dereference one
    # of those as a `struct eis *` would be a genuine crash risk, not just
    # a wrong test result.
    monkeypatch.setattr(_capi.libeis, "log_set_handler", lambda p, h: None)
    monkeypatch.setattr(_capi.libeis, "log_set_priority", lambda p, prio: None)


def test_event_type_enum_matches_protocol_values() -> None:
    assert eis.EventType.CLIENT_CONNECT.value == 1
    assert eis.EventType.SEAT_BIND.value == 3
    assert eis.EventType.FRAME.value == 100
    assert eis.EventType.POINTER_MOTION.value == 300
    assert eis.EventType.KEYBOARD_KEY.value == 700
    assert eis.EventType.PONG.value == 90
    assert eis.EventType.SYNC.value == 91


def test_event_type_handles_a_value_outside_the_known_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_capi.libeis, "event_get_type", lambda p: 123456)
    event = eis.Event.wrap(0x1)
    assert event is not None
    assert event.event_type == 123456
    assert not isinstance(event.event_type, eis.EventType)


def test_add_client_returns_the_new_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    # eis_backend_fd_add_client() *returns* the client fd; it does not
    # take one -- see _capi/libeis.py's module docstring.
    monkeypatch.setattr(_capi.libeis, "backend_fd_add_client", lambda p: 42)
    server = eis.Eis(0x1)
    assert server.add_client() == 42


def test_add_client_raises_on_negative_errno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_capi.libeis, "backend_fd_add_client", lambda p: -1)
    server = eis.Eis(0x1)
    with pytest.raises(eis.Error):
        server.add_client()


def test_create_for_fd_calls_setup_backend_fd_with_only_the_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Eis.create_for_fd() passes `server` itself (relying on CObject's
    # _as_parameter_ to convert it to the raw pointer inside real ctypes
    # marshalling); a plain-function mock bypasses that conversion and
    # receives the object as-is, so check identity rather than the pointer
    # value.
    captured: dict[str, object] = {}

    def fake_setup(pointer: object) -> int:
        captured["pointer"] = pointer
        return 0

    monkeypatch.setattr(_capi.libeis, "new", lambda userdata: 0x1)
    monkeypatch.setattr(_capi.libeis, "setup_backend_fd", fake_setup)

    server = eis.Eis.create_for_fd()

    assert captured["pointer"] is server


def test_create_for_fd_raises_on_setup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_capi.libeis, "new", lambda userdata: 0x1)
    monkeypatch.setattr(_capi.libeis, "setup_backend_fd", lambda p: -13)  # EACCES

    with pytest.raises(eis.Error):
        eis.Eis.create_for_fd()


def test_device_configure_sets_name_type_size_and_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        _capi.libeis,
        "device_configure_name",
        lambda p, name: calls.append(("name", name)),
    )
    monkeypatch.setattr(
        _capi.libeis,
        "device_configure_type",
        lambda p, t: calls.append(("type", t)),
    )
    monkeypatch.setattr(
        _capi.libeis,
        "device_configure_size",
        lambda p, w, h: calls.append(("size", w, h)),
    )
    monkeypatch.setattr(
        _capi.libeis,
        "device_configure_capability",
        lambda p, cap: calls.append(("capability", cap)),
    )

    device = eis.Device.wrap(0x1)
    assert device is not None
    device.configure(
        name="test-pointer",
        size=(1920, 1080),
        capabilities=(eis.DeviceCapability.POINTER,),
    )

    assert ("name", b"test-pointer") in calls
    assert ("type", eis.DeviceType.VIRTUAL) in calls
    assert ("size", 1920, 1080) in calls
    assert ("capability", eis.DeviceCapability.POINTER) in calls


def test_device_configure_adds_a_region(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, ...]] = []
    # configure() always calls device_configure_type(), regardless of which
    # optional parameters the caller passes.
    monkeypatch.setattr(_capi.libeis, "device_configure_type", lambda p, t: None)
    monkeypatch.setattr(_capi.libeis, "device_new_region", lambda p: 0xABC)
    monkeypatch.setattr(
        _capi.libeis,
        "region_set_size",
        lambda p, w, h: calls.append(("size", w, h)),
    )
    monkeypatch.setattr(
        _capi.libeis,
        "region_set_offset",
        lambda p, x, y: calls.append(("offset", x, y)),
    )
    monkeypatch.setattr(
        _capi.libeis,
        "region_set_physical_scale",
        lambda p, s: calls.append(("scale", s)),
    )
    monkeypatch.setattr(_capi.libeis, "region_add", lambda p: calls.append(("add",)))

    device = eis.Device.wrap(0x1)
    assert device is not None
    device.configure(
        regions=(
            eis.ConfigureRegion(offset=(0, 0), size=(800, 600), physical_scale=1.0),
        )
    )

    assert calls == [
        ("size", 800, 600),
        ("offset", 0, 0),
        ("scale", 1.0),
        ("add",),
    ]


def test_seat_new_device_raises_on_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_capi.libeis, "seat_new_device", lambda p: 0)
    seat = eis.Seat.wrap(0x1)
    assert seat is not None
    with pytest.raises(eis.Error):
        seat.new_device()


def test_client_new_seat_encodes_name_and_wraps_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_new_seat(pointer: int, name: bytes) -> int:
        captured["name"] = name
        return 0x99

    monkeypatch.setattr(_capi.libeis, "client_new_seat", fake_new_seat)
    client = eis.Client.wrap(0x1)
    assert client is not None
    seat = client.new_seat("default")

    assert captured["name"] == b"default"
    assert seat is not None
    assert seat._as_parameter_ == 0x99


def test_keymap_fd_dups_so_it_can_be_closed_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # See test_ei_objects.py's equivalent: eis_keymap_get_fd() is a plain
    # field read, not a duped/transferred fd.
    real_fd, write_fd = os.pipe()
    try:
        monkeypatch.setattr(_capi.libeis, "keymap_get_fd", lambda p: real_fd)

        keymap = eis.Keymap.wrap(0x1)
        assert keymap is not None

        f1 = keymap.fd
        f2 = keymap.fd
        try:
            assert f1.fileno() != real_fd
            assert f2.fileno() != real_fd
            assert f1.fileno() != f2.fileno()

            f1.close()
            os.fstat(real_fd)
            f2.close()
            os.fstat(real_fd)
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
    sequences: list[int] = []
    monkeypatch.setattr(
        _capi.libeis,
        "device_start_emulating",
        lambda p, seq: sequences.append(seq),
    )
    monkeypatch.setattr(_capi.libeis, "device_stop_emulating", lambda p: None)

    device = eis.Device.wrap(0x1)
    assert device is not None
    device.start_emulating()
    device.stop_emulating()
    device.start_emulating()
    device.start_emulating()

    # Only the climb is contractual; the counter is process-wide, so
    # absolute values depend on what else has run.
    assert all(b > a for a, b in zip(sequences, sequences[1:], strict=False)), sequences


def test_device_start_emulating_explicit_sequence_is_passed_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sequences: list[int] = []
    monkeypatch.setattr(
        _capi.libeis,
        "device_start_emulating",
        lambda p, seq: sequences.append(seq),
    )

    device = eis.Device.wrap(0x1)
    assert device is not None
    device.start_emulating(sequence=100)

    assert sequences == [100]


def test_device_start_emulating_shares_one_counter_with_the_ei_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Both modules draw from the same process-wide source, so a client and
    # a server in one process can never hand out the same sequence.
    #
    # This test reaches across into ei.Device, which the autouse fixture
    # above doesn't cover -- stub its ref/unref too, or a real installed
    # libei would dereference the fake 0x1 pointer and segfault.
    monkeypatch.setattr(ei.Device, "_instances", type(ei.Device._instances)())
    monkeypatch.setattr(ei.Device, "_ref_func", staticmethod(lambda p: None))
    monkeypatch.setattr(ei.Device, "_unref_func", staticmethod(lambda p: None))

    seen: list[int] = []
    monkeypatch.setattr(
        _capi.libeis, "device_start_emulating", lambda p, seq: seen.append(seq)
    )
    monkeypatch.setattr(
        _capi.libei, "device_start_emulating", lambda p, seq: seen.append(seq)
    )

    eis_device = eis.Device.wrap(0x1)
    ei_device = ei.Device.wrap(0x1)
    assert eis_device is not None and ei_device is not None

    eis_device.start_emulating()
    ei_device.start_emulating()
    eis_device.start_emulating()

    assert len(set(seen)) == len(seen), seen
