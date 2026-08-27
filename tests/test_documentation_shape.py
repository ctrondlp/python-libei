"""Checks on the documentation that need no native library.

``test_documented_examples.py`` executes the examples for real, which means
it skips itself wherever libei isn't installed -- including CI sandboxes.
The properties asserted here are the ones cheap enough to check as text, so
that documentation defects still get caught in that environment.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

from libei import ei, eis

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_README = _PROJECT_ROOT / "README.md"


def _readme_python_blocks() -> list[str]:
    return re.findall(r"```python\n(.*?)```", _README.read_text(), re.DOTALL)


def _docstring_example(module_doc: str) -> str:
    """Pull the ``::``-introduced literal block out of a module docstring."""
    _, _, after = module_doc.partition("::\n")
    lines: list[str] = []
    for line in after.splitlines():
        if line.strip() and not line.startswith("    "):
            break
        lines.append(line)
    return textwrap.dedent("\n".join(lines))


def test_readme_has_python_examples() -> None:
    assert _readme_python_blocks(), "README has no ```python examples to check"


def test_readme_examples_are_valid_python() -> None:
    # They can't all *run* (they reference a live connection), but an
    # example that doesn't even parse is never worth shipping.
    for block in _readme_python_blocks():
        try:
            compile(block, "<README>", "exec")
        except SyntaxError as exc:
            pytest.fail(f"README example is not valid Python ({exc}):\n{block}")


@pytest.mark.parametrize("module", [ei, eis])
def test_module_docstring_examples_are_valid_python(module: object) -> None:
    source = _docstring_example(module.__doc__ or "")
    assert source.strip(), f"{module.__name__} docstring has no example"  # type: ignore[attr-defined]
    try:
        compile(source, f"<{module.__name__} docstring>", "exec")  # type: ignore[attr-defined]
    except SyntaxError as exc:
        pytest.fail(f"docstring example is not valid Python ({exc}):\n{source}")


def _emulating_examples() -> list[str]:
    """Examples that both react to events and start emulating input."""
    candidates = [*_readme_python_blocks(), _docstring_example(ei.__doc__ or "")]
    return [c for c in candidates if ".events" in c and "start_emulating" in c]


def test_examples_wait_for_device_resumed_before_emulating() -> None:
    # libei's own header: a device arrives paused, and "sender clients must
    # wait until EI_EVENT_DEVICE_RESUMED before sending events" -- sending
    # on DEVICE_ADDED is documented as a client bug. An example that gets
    # this wrong happens to work against a test server that resumes
    # immediately (as tests/test_integration_socketpair.py does) and then
    # misbehaves against a real compositor, which is the worst way for a
    # documentation defect to fail.
    examples = _emulating_examples()
    assert examples, "expected examples that negotiate a device and emulate"
    for block in examples:
        assert "DEVICE_RESUMED" in block, (
            "example emulates input without waiting for DEVICE_RESUMED:\n" + block
        )


def test_examples_pump_dispatch_before_draining_events() -> None:
    # Context.events drains only what is already queued, so an events loop
    # with no dispatch() spins on an empty queue forever.
    blocks = [b for b in _readme_python_blocks() if ".events" in b]
    assert blocks, "expected README examples that iterate .events"
    for block in blocks:
        assert "dispatch()" in block, (
            "example iterates .events without dispatch(); "
            f"it cannot work as written:\n{block}"
        )


def test_readme_install_command_is_not_a_broken_pypi_reference() -> None:
    # The package is not on PyPI; `pip install python-libei` fails.
    assert "pip install python-libei" not in _README.read_text()


def test_readme_only_references_real_public_api() -> None:
    # Catches an example drifting to a method that was renamed or never
    # existed -- the failure mode a reader hits first and can't debug.
    modules = {"ei": ei, "eis": eis}
    known = {
        name: {a for a in dir(mod) if not a.startswith("_")}
        for name, mod in modules.items()
    }
    for block in _readme_python_blocks():
        for mod_name, attr in re.findall(r"\b(ei|eis)\.([A-Za-z_]\w*)", block):
            assert attr in known[mod_name], (
                f"README references {mod_name}.{attr}, which does not exist"
            )


def test_documented_event_types_exist() -> None:
    # EventType members named in the README must really be in the enum.
    for block in _readme_python_blocks():
        for mod_name, member in re.findall(r"\b(ei|eis)\.EventType\.([A-Z_]+)", block):
            enum_cls = (ei if mod_name == "ei" else eis).EventType
            assert member in enum_cls.__members__, (
                f"README references {mod_name}.EventType.{member}, which does not exist"
            )
    for member in re.findall(r"\bEventType\.([A-Z_]+)", ei.__doc__ or ""):
        assert member in ei.EventType.__members__, (
            f"ei docstring references EventType.{member}, which does not exist"
        )


def test_documented_capabilities_exist() -> None:
    for block in _readme_python_blocks():
        for mod_name, member in re.findall(
            r"\b(ei|eis)\.DeviceCapability\.([A-Z_]+)", block
        ):
            enum_cls = (ei if mod_name == "ei" else eis).DeviceCapability
            assert member in enum_cls.__members__, (
                f"README references {mod_name}.DeviceCapability.{member}, "
                "which does not exist"
            )


def test_readme_device_methods_exist() -> None:
    # The "Sending input" cookbook is the part a reader copies verbatim.
    for method in (
        "start_emulating",
        "stop_emulating",
        "frame",
        "pointer_motion",
        "pointer_motion_absolute",
        "button",
        "keyboard_key",
        "scroll_delta",
        "scroll_discrete",
        "touch_new",
        "regions",
        "keymap",
        "text_utf8",
        "text_keysym",
        "region_at",
    ):
        assert hasattr(ei.Device, method), f"ei.Device.{method} is documented but gone"
    for method in ("down", "motion", "up", "cancel"):
        assert hasattr(ei.Touch, method), f"ei.Touch.{method} is documented but gone"


def test_readme_connection_and_region_api_exists() -> None:
    # Same guard for the parts of the README outside the input cookbook:
    # the Connection lifecycle section, the keymap walkthrough and the
    # region helpers all name methods a reader will copy.
    for cls, methods in (
        (ei.Context, ("new_ping", "disconnect", "peek_event_type", "is_sender")),
        (ei.Seat, ("bind", "unbind", "request_device", "capabilities")),
        (ei.Region, ("mapping_id", "convert_point", "contains")),
        (ei.Keymap, ("fd", "size", "keymap_type")),
        (ei.Ping, ("id", "send")),
        (ei.Event, ("touch_up_event", "text_utf8_event", "text_keysym_event", "pong")),
    ):
        for method in methods:
            assert hasattr(cls, method), (
                f"ei.{cls.__name__}.{method} is documented but gone"
            )
    # eis mirrors the client side; the EIS-server section documents these.
    for method in ("set_flag", "peek_event_type", "add_client"):
        assert hasattr(eis.Eis, method), f"eis.Eis.{method} is documented but gone"
    assert hasattr(eis.ConfigureRegion, "mapping_id")


def test_readme_version_gated_features_are_named_with_their_versions() -> None:
    # Every feature that needs a libei newer than the 1.0.0 floor has to
    # say so, or a reader on an older build gets a LibraryNotFoundError
    # with no way to know it was expected.
    readme = _README.read_text()
    for feature, version in (
        ("text_utf8", "1.6"),
        ("request_device", "1.6"),
        ("new_ping", "1.4"),
        ("disconnect", "1.4"),
        ("convert_point", "1.1"),
    ):
        assert feature in readme, f"{feature} is no longer documented"
        assert version in readme, f"README no longer states the {version} requirement"


def test_readme_input_codes_match_linux_headers() -> None:
    # BTN_LEFT/KEY_A are spelled out as literals in the README, so they
    # can't be checked by import -- pin them against the kernel's values.
    readme = _README.read_text()
    for name, value in (("BTN_LEFT", "0x110"), ("KEY_A", "30")):
        if name in readme:
            assert f"{name} = {value}" in readme, (
                f"README defines {name} with a value other than {value}"
            )


def test_ast_of_readme_examples_has_no_bare_event_retention() -> None:
    # Events are released when the loop moves on. Assigning the loop
    # variable itself to something outer (`kept = event`) is the mistake
    # the "Things that will bite you" section warns about, so the README
    # must not demonstrate it.
    for block in _readme_python_blocks():
        try:
            tree = ast.parse(block)
        except SyntaxError:
            continue  # reported by test_readme_examples_are_valid_python
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                loop_var = node.target.id
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Assign) and isinstance(
                        inner.value, ast.Name
                    ):
                        assert inner.value.id != loop_var, (
                            f"README example stores the event loop variable "
                            f"{loop_var!r} past its iteration"
                        )
