"""Every event accessor against every event type, on both sides of the protocol.

libei's own accessors do not report a type mismatch: reading ``key_event`` off a
pointer event returns ``KeyEvent(key=0, is_press=False)``, which is
indistinguishable from a real key 0, and some accessors make libei log an
internal "Bug:" line while others say nothing. ``Event._require`` turns that
into a ``TypeError`` before the C call. The existing tests pin one wrong type
per library; this pins all of them, and -- because the accessor list is read
from the source rather than typed out here -- pins any accessor added later,
including one added *without* the guard.

The C accessors are replaced with recorders, so nothing here needs libei
installed and a guard that let a wrong type through would show up as a C call
that should never have happened.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any

import pytest

from libei import _capi, ei, eis


@pytest.fixture(autouse=True)
def _no_native_refcounting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake pointers must never reach a real ``*_unref`` at garbage collection.

    Same isolation as test_ei_objects.py: these tests wrap pointers like 0x1
    that no library returned, so where libei is installed a finalizer calling
    the real unref would dereference garbage. Every CObject subclass on both
    sides gets its ref/unref stubbed and a fresh instance cache.
    """
    from libei._cobject import CObject

    for module in (ei, eis):
        for cls in vars(module).values():
            if (
                isinstance(cls, type)
                and issubclass(cls, CObject)
                and cls is not CObject
            ):
                monkeypatch.setattr(cls, "_instances", type(cls._instances)())
                if cls._ref_func is not None:
                    monkeypatch.setattr(cls, "_ref_func", staticmethod(lambda p: None))
                if cls._unref_func is not None:
                    monkeypatch.setattr(
                        cls, "_unref_func", staticmethod(lambda p: None)
                    )


# Properties on Event that read no type-specific payload, so need no guard.
_UNGUARDED = {"event_type", "time", "device", "seat", "client"}

_SIDES: dict[str, Any] = {
    "ei": (ei, _capi.libei),
    "eis": (eis, _capi.libeis),
}


def _accessors(module: Any) -> dict[str, tuple[str, ...]]:
    """Property name -> the event types its ``_require`` call allows."""
    found: dict[str, tuple[str, ...]] = {}
    for name, member in inspect.getmembers(module.Event):
        if name.startswith("_") or not isinstance(member, property):
            continue
        if name in _UNGUARDED:
            continue
        source = inspect.getsource(member.fget)  # type: ignore[arg-type]
        guard = re.search(r"self\._require\(\s*\"[\w]+\"\s*,([^)]*)\)", source)
        found[name] = (
            tuple(re.findall(r"EventType\.(\w+)", guard.group(1))) if guard else ()
        )
    return found


@pytest.mark.parametrize("side", sorted(_SIDES))
def test_every_payload_accessor_is_guarded(side: str) -> None:
    module, _ = _SIDES[side]
    unguarded = sorted(n for n, valid in _accessors(module).items() if not valid)
    assert not unguarded, (
        f"{side}.Event.{unguarded} read a C payload with no _require() guard: the "
        "wrong event type would return plausible zeros instead of raising"
    )


@pytest.mark.parametrize("side", sorted(_SIDES))
def test_every_guard_names_event_types_that_exist(side: str) -> None:
    module, _ = _SIDES[side]
    for name, valid in _accessors(module).items():
        for type_name in valid:
            assert hasattr(module.EventType, type_name), f"{side}.Event.{name}"


def _patch_c(
    monkeypatch: pytest.MonkeyPatch, c_module: Any, event_type: int
) -> list[str]:
    """Replace every bound C function; return the list its calls land in."""
    calls: list[str] = []

    def recorder(name: str) -> Callable[..., int]:
        def call(*_args: Any) -> int:
            calls.append(name)
            return 0

        # The C name, so a later patch of the same module still finds it.
        call.__name__ = name
        return call

    for name in c_module.lib.declared:
        python_name = next(
            (
                attribute
                for attribute, value in vars(c_module).items()
                if getattr(value, "__name__", None) == name
            ),
            None,
        )
        if python_name is not None:
            monkeypatch.setattr(c_module, python_name, recorder(name))
    monkeypatch.setattr(c_module, "event_get_type", lambda _pointer: event_type)
    return calls


@pytest.mark.parametrize("side", sorted(_SIDES))
def test_a_wrong_event_type_raises_before_any_c_accessor_is_called(
    side: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, c_module = _SIDES[side]
    accessors = _accessors(module)
    assert len(accessors) >= 8, "found almost no accessors: the discovery broke"

    wrong_combinations = 0
    for event_type in [*module.EventType, 0, 123456]:
        value = int(event_type)
        calls = _patch_c(monkeypatch, c_module, value)
        event = module.Event.wrap(0x1)
        assert event is not None
        name_of = (
            event_type.name if isinstance(event_type, module.EventType) else str(value)
        )
        for accessor, valid in accessors.items():
            if name_of in valid:
                continue
            calls.clear()
            with pytest.raises(TypeError, match=f"Event.{accessor} is only valid for"):
                getattr(event, accessor)
            assert calls == [], (
                f"{side}.Event.{accessor} reached C ({calls}) for a {name_of} event"
            )
            wrong_combinations += 1
    assert wrong_combinations > 100


@pytest.mark.parametrize("side", sorted(_SIDES))
def test_a_right_event_type_passes_the_guard(
    side: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, c_module = _SIDES[side]
    for accessor, valid in _accessors(module).items():
        for type_name in valid:
            calls = _patch_c(monkeypatch, c_module, int(module.EventType[type_name]))
            event = module.Event.wrap(0x1)
            assert event is not None
            try:
                getattr(event, accessor)
            except TypeError as exc:
                assert "is only valid for" not in str(exc), (
                    f"{side}.Event.{accessor} refused its own type {type_name}"
                )
            except Exception:  # noqa: BLE001 - what the fake C values make of it
                pass
            assert calls, f"{side}.Event.{accessor} never reached C for {type_name}"
