"""Runs the examples from the README and module docstrings for real.

Both had drifted into shapes that raise as written -- an ``events`` loop
with no ``dispatch()`` call drains an empty queue, so a variable the
example goes on to use is never assigned. Nothing caught it, because
example code in a docstring is inert. These tests execute the real text
extracted from the real files, so it can't silently rot again.

Checks that need no native library (and so still run where libei is
absent) live in ``test_documentation_shape.py``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from conftest import requires_libei
from libei import ei, eis

pytestmark = [pytest.mark.integration, requires_libei]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _docstring_example(module_doc: str) -> str:
    """Pull the ``::``-introduced literal block out of a module docstring."""
    _, _, after = module_doc.partition("::\n")
    lines: list[str] = []
    for line in after.splitlines():
        if line.strip() and not line.startswith("    "):
            break
        lines.append(line)
    return textwrap.dedent("\n".join(lines))


def test_ei_module_docstring_example_runs() -> None:
    source = _docstring_example(ei.__doc__ or "")
    assert "dispatch()" in source, (
        "the docstring example must pump dispatch(); without it "
        "Context.events yields nothing and the example cannot work"
    )

    server = eis.Eis.create_for_fd()
    eis_fd = server.add_client()

    # The example is written against bare names, as a reader would have
    # after `from libei.ei import ...`.
    namespace: dict[str, object] = {
        "Sender": ei.Sender,
        "EventType": ei.EventType,
        "DeviceCapability": ei.DeviceCapability,
        "eis_fd": eis_fd,
    }

    # Drive the server side far enough that the client's loop can finish,
    # by stepping it between the example's own dispatch() calls.
    original_dispatch = ei.Context.dispatch
    state = {"configured": False}

    def dispatch_and_step_server(self: ei.Context) -> None:
        original_dispatch(self)
        server.dispatch()
        for event in server.events:
            if event.event_type is eis.EventType.CLIENT_CONNECT:
                event.client.connect()
                seat = event.client.new_seat("example-seat")
                seat.configure_capabilities((eis.DeviceCapability.POINTER,))
                seat.add()
            elif (
                event.event_type is eis.EventType.SEAT_BIND and not state["configured"]
            ):
                state["configured"] = True
                assert event.seat is not None
                device = event.seat.new_device()
                device.configure(
                    name="example-pointer",
                    capabilities=(eis.DeviceCapability.POINTER,),
                )
                device.add()
                device.resume()
        original_dispatch(self)

    ei.Context.dispatch = dispatch_and_step_server  # type: ignore[method-assign]
    try:
        exec(compile(source, "<ei module docstring>", "exec"), namespace)
    finally:
        ei.Context.dispatch = original_dispatch  # type: ignore[method-assign]

    assert namespace["device"] is not None


@pytest.mark.parametrize("module", [ei, eis, "oeffis"])
def test_public_api_is_documented(module: object) -> None:
    # The package ships py.typed and is meant to be consumed as a
    # dependency, so every public callable needs at least a one-line
    # docstring. This started at 3/64 and 6/74.
    import ast
    import importlib

    if isinstance(module, str):
        module = importlib.import_module(f"libei.{module}")

    source = Path(module.__file__).read_text()  # type: ignore[attr-defined]
    tree = ast.parse(source)

    undocumented = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and not node.name.startswith("_")
        and not ast.get_docstring(node)
    ]
    assert not undocumented, (
        f"{module.__name__} has undocumented public callables: "  # type: ignore[attr-defined]
        f"{sorted(set(undocumented))}"
    )
