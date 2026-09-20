"""Checks on the documentation that need no native library.

``test_documented_examples.py`` executes the examples for real, which means
it skips itself wherever libei isn't installed -- including CI sandboxes.
The properties asserted here are the ones cheap enough to check as text, so
that documentation defects still get caught in that environment.
"""

from __future__ import annotations

import ast
import importlib
import re
import textwrap
from pathlib import Path

import pytest

from libei import ei, eis

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_README = _PROJECT_ROOT / "README.md"
_TROUBLESHOOTING = _PROJECT_ROOT / "docs" / "troubleshooting.md"

# The modules a reader can import a public name from -- what the reference
# pages name, and whose docstrings carry the examples worth checking.
_PUBLIC_MODULES = ("libei.ei", "libei.eis", "libei.oeffis", "libei.portal")

_EXCEPTION_HEADING = "## When it does raise: which name you are catching"


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


@pytest.mark.parametrize("module_name", _PUBLIC_MODULES)
def test_module_docstring_examples_are_valid_python(module_name: str) -> None:
    # Every module with an example has to write it as the ``::`` literal
    # block the extractor reads, and the block has to parse. oeffis and
    # portal wrote theirs as a plain indented block, so neither example was
    # ever extracted here -- or evaluated as reST, where a literal block
    # needs the marker.
    module = importlib.import_module(module_name)
    source = _docstring_example(module.__doc__ or "")
    assert source.strip(), f"{module_name} docstring has no ``::`` example"
    try:
        compile(source, f"<{module_name} docstring>", "exec")
    except SyntaxError as exc:
        pytest.fail(
            f"{module_name} docstring example is not valid Python ({exc}):\n{source}"
        )


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


def test_readme_documents_the_pypi_install() -> None:
    # The package is on PyPI as of 0.1.0, so the plain install has to be
    # the one a reader sees first. This guard used to assert the opposite;
    # it is inverted rather than deleted so that dropping the PyPI
    # instruction is a test failure, not a silent regression to a checkout.
    readme = _README.read_text()
    assert "pip install python-libei" in readme
    assert readme.index("pip install python-libei") < readme.index(
        "git clone https://github.com/ctrondlp/python-libei.git"
    ), "the checkout install is documented before the PyPI one"


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


def test_version_is_declared_identically_in_both_places() -> None:
    # The version lives in pyproject.toml (what pip and the built artifacts
    # use) and in libei.__version__ (what a caller introspects). Nothing
    # keeps them in step automatically, so a bump that touches one and not
    # the other ships a package whose own reported version is a lie.
    import libei

    pyproject = (_PROJECT_ROOT / "pyproject.toml").read_text()
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match, "pyproject.toml has no version"
    assert libei.__version__ == match.group(1), (
        f"libei.__version__ is {libei.__version__!r} but pyproject.toml "
        f"says {match.group(1)!r}"
    )


_DOCS_ROOT = _PROJECT_ROOT / "docs"
_INLINE_LINK = re.compile(r"\]\(([^)\s]+)\)")
_CONTENTS_ENTRY = re.compile(r"^- \[[^\]]+\]\(#([^)]+)\)$", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6} (.+)$", re.MULTILINE)


def _documentation_pages() -> list[Path]:
    """The README plus every page under docs/, which is the user set."""
    return [_README, *sorted(_DOCS_ROOT.rglob("*.md"))]


def _slug(heading: str) -> str:
    """The anchor a heading gets, insensitive to how a slugger trims.

    Runs of dashes are collapsed and both ends stripped, so a heading naming a
    ``--flag`` matches a contents entry written with one leading dash as well as
    the two a strict slugger would produce. What is asked is "is there a section
    for this", not "is the anchor byte-exact".
    """
    text = re.sub(r"[^a-z0-9 \-_]", "", heading.strip().lower())
    return re.sub(r"-+", "-", text.replace(" ", "-")).strip("-")


def test_relative_links_point_at_something_that_exists() -> None:
    # A page linked from another page is the only way most readers find it, so
    # a move that updates the file but not the links makes it unreachable
    # without breaking anything a reader can see.
    pages = _documentation_pages()
    assert len(pages) > 5, "the docs set looks empty; check the glob"
    for page in pages:
        for target in _INLINE_LINK.findall(page.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path = target.split("#", 1)[0]
            if not path:
                continue
            assert (page.parent / path).exists(), (
                f"{page.name} links to {path}, which does not exist"
            )


def test_each_pages_own_contents_resolves() -> None:
    # The recipes and troubleshooting pages open with a catalogue of their own
    # sections. A section renamed without the catalogue moving leaves a link
    # that silently goes nowhere -- the page still renders, which is why
    # nothing catches it.
    for page in _documentation_pages():
        text = page.read_text()
        headings = {_slug(heading) for heading in _HEADING.findall(text)}
        for anchor in _CONTENTS_ENTRY.findall(text):
            assert _slug(anchor) in headings, (
                f"{page.name} lists #{anchor} in its contents with no such heading"
            )


@pytest.mark.parametrize("page", ["README.md", "docs/developers/verification.md"])
def test_status_pages_name_the_current_version(page: str) -> None:
    # Both state the version in prose, and nothing keeps them in step with
    # pyproject.toml -- so a bump that touches one and not the other ships a
    # page describing a release that no longer exists. The developers page
    # sat a whole release behind exactly that way: 0.5.1 while
    # pyproject.toml, libei.__version__ and the README all said 0.5.2.
    import libei

    assert f"`{libei.__version__}`" in (_PROJECT_ROOT / page).read_text(), (
        f"{page} does not mention the current version {libei.__version__}"
    )


def _documented_exceptions() -> list[tuple[list[str], list[str]]]:
    """The (modules, classes) of each row of the exception table.

    Only the table under :data:`_EXCEPTION_HEADING` is read, so a table
    added to that page elsewhere cannot quietly become part of this check.
    """
    _, _, after = _TROUBLESHOOTING.read_text().partition(_EXCEPTION_HEADING)
    section = after.split("\n## ", 1)[0]
    rows: list[tuple[list[str], list[str]]] = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("Module", "") or cells[0].strip("- ") == "":
            continue
        rows.append(
            (
                [name.strip(" `") for name in cells[0].split(",")],
                [name.strip(" `") for name in cells[1].split(",")],
            )
        )
    return rows


def test_documented_exceptions_are_part_of_the_public_api() -> None:
    # The page says which module to import each exception from, so each has
    # to be part of that module's declared surface rather than an attribute
    # it happens to carry. LibraryNotFoundError was importable from
    # libei.ei and libei.eis but listed in neither __all__ -- which left the
    # private libei._capi.loader as the only place it was obviously public.
    rows = _documented_exceptions()
    assert rows, "no exception table found in docs/troubleshooting.md"
    for modules, classes in rows:
        for class_name in classes:
            found: dict[str, object] = {}
            for module_name in modules:
                module = importlib.import_module(module_name)
                assert hasattr(module, class_name), f"{module_name} has no {class_name}"
                assert class_name in module.__all__, (
                    f"{module_name}.{class_name} is documented as importable from "
                    "there, but is missing from that module's __all__"
                )
                found[module_name] = getattr(module, class_name)
            # A row may name two modules for one class, and Error is a
            # separate class on each side -- so a shared row is the only
            # thing that has to resolve to one object.
            assert len({id(cls) for cls in found.values()}) == 1, (
                f"the row for {class_name} names {modules}, which do not agree "
                "on what that class is"
            )


@pytest.mark.parametrize("module_name", _PUBLIC_MODULES)
def test_public_api_is_documented(module_name: str) -> None:
    # The package ships py.typed and is meant to be consumed as a
    # dependency, so every public callable needs at least a one-line
    # docstring. This started at 3/64 and 6/74. It reads source text and
    # needs nothing installed, so it belongs here rather than in
    # test_documented_examples.py, whose module-level `integration` mark
    # skipped it on exactly the machines where it was cheapest to run.
    module = importlib.import_module(module_name)
    source_path = module.__file__
    assert source_path is not None, f"{module_name} has no source file to read"
    tree = ast.parse(Path(source_path).read_text())
    undocumented = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
        and not ast.get_docstring(node)
    ]
    assert not undocumented, (
        f"{module_name} has undocumented public definitions: "
        f"{sorted(set(undocumented))}"
    )


# A role whose name starts on the next line renders as literal text wherever
# the docstring is read, and so does everything after an unpaired backtick.
_SPLIT_ROLE = re.compile(r":(class|meth|attr|func|mod|data|exc):`[^`\n]*\n")


def _docstrings(module_name: str) -> list[tuple[str, str]]:
    """The (where, docstring) of a module and every definition inside it."""
    module = importlib.import_module(module_name)
    source_path = module.__file__
    assert source_path is not None, f"{module_name} has no source file to read"
    tree = ast.parse(Path(source_path).read_text())
    found = [("<module>", ast.get_docstring(tree) or "")]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((node.name, ast.get_docstring(node) or ""))
    return found


@pytest.mark.parametrize("module_name", _PUBLIC_MODULES)
def test_docstrings_have_no_broken_markup(module_name: str) -> None:
    # Neither defect shows up anywhere except in the rendered text: portal.py
    # read ":meth:`enable` and :meth:`" and broke the line before
    # "wait_for_activation`", so the second name never became a link, and a
    # docstring with an unpaired backtick silently swallows the rest of it.
    for where, doc in _docstrings(module_name):
        if _SPLIT_ROLE.search(doc) is not None:
            pytest.fail(f"{module_name}:{where} breaks a role across a line break")
        assert doc.count("`") % 2 == 0, (
            f"{module_name}:{where} has an unpaired backtick"
        )
