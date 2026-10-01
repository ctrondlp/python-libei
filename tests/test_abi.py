"""The ctypes bindings checked against the real library and the real headers.

Everything here binds C functions by hand, with no compiler in the loop, so a
wrong argument count, a wrong width or a wrong enum value is invisible until
the one call that crosses it -- and then it is a corrupted argument or a
process-level crash rather than a Python exception. The mocked suites cannot
see any of it, because a mock agrees with whatever the binding says.

There are no ``ctypes.Structure`` bindings in this package (every object is an
opaque pointer, and values cross as scalars), so the layout risk is narrower
than "struct offsets": it is **names, argument types and enum values**. Those
are what is checked, two ways:

* against the installed shared library -- every declared function must be
  exported, unless its declaration is marked as needing a newer libei than the
  one installed;
* against the upstream public headers -- every declared function's argument
  count and each argument's and the return's size class (pointer, 4-byte int,
  8-byte int, double, bool, void) must match the prototype, and every Python
  enum member's value must match the C enumerator of the same name.

The header half needs the headers, which distributions ship only in a -dev
package and which differ by release. Point ``LIBEI_SOURCE_DIR`` at an upstream
checkout or tarball (the directory holding ``meson.build`` and ``src/``) to run
it; with nothing set it looks for an installed ``libei-1.0`` include directory
and otherwise skips. Running it once per libei release is the version matrix:

    for v in 1.0.0 1.2.1 1.4.0 1.6.0; do
        LIBEI_SOURCE_DIR=libei-$v pytest tests/test_abi.py
    done
"""

from __future__ import annotations

import ctypes
import enum
import os
import re
import sys
from pathlib import Path

import pytest

from libei import ei, eis, oeffis
from libei._capi import libei as c_libei
from libei._capi import libeis as c_libeis
from libei._capi import liboeffis as c_liboeffis

_LIBRARIES = {
    "ei_": c_libei.lib,
    "eis_": c_libeis.lib,
    "oeffis_": c_liboeffis.lib,
}
_CAPI_SOURCES = {
    "ei_": Path(c_libei.__file__),
    "eis_": Path(c_libeis.__file__),
    "oeffis_": Path(c_liboeffis.__file__),
}
_HEADER_FILES = {"ei_": "libei.h", "eis_": "libeis.h", "oeffis_": "liboeffis.h"}


# -- which declarations are marked as needing a newer libei ------------------


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _marked_versions(prefix: str) -> dict[str, tuple[int, ...]]:
    """C name -> the libei version its declaration's comment says it needs.

    Read from the binding modules' own ``# libei 1.4+`` comments, on the
    ``lib.function(`` line, so the marker and the declaration cannot drift
    apart: a declaration with no marker is one every supported libei exports.
    """
    marked: dict[str, tuple[int, ...]] = {}
    lines = _CAPI_SOURCES[prefix].read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if "lib.function(" not in line:
            continue
        found = re.search(r"#\s*libei\s+(\d+\.\d+(?:\.\d+)?)\+", line)
        if not found:
            continue
        window = " ".join(lines[index : index + 2])
        name = re.search(r'"((?:ei|eis|oeffis)_\w+)"', window)
        assert name, f"no C name near {line!r}"
        marked[name.group(1)] = _version(found.group(1))
    return marked


def _declared(prefix: str) -> dict[str, tuple[tuple[type, ...], type | None]]:
    # Importing the binding modules declares everything; nothing is opened.
    return _LIBRARIES[prefix].declared


# -- the installed library ---------------------------------------------------


def _installed_version(soname: str) -> tuple[int, ...] | None:
    """The version in the loaded library's file name (``libei.so.1.6.0``)."""
    try:
        maps = Path("/proc/self/maps").read_text()
    except OSError:
        return None
    stem = soname.split(".so")[0]
    for line in maps.splitlines():
        found = re.search(rf"/{re.escape(stem)}\.so\.(\d+\.\d+\.\d+)$", line)
        if found:
            return _version(found.group(1))
    return None


@pytest.mark.parametrize("prefix", sorted(_LIBRARIES))
def test_every_declared_function_is_exported_by_the_installed_library(
    prefix: str,
) -> None:
    lib = _LIBRARIES[prefix]
    if not lib.is_available():
        pytest.skip(f"{lib.soname} is not installed")
    handle = lib._ensure_loaded()
    marked = _marked_versions(prefix)
    installed = _installed_version(lib.soname)

    missing = sorted(n for n in _declared(prefix) if not hasattr(handle, n))
    unmarked = [n for n in missing if n not in marked]
    assert not unmarked, (
        f"{lib.soname} does not export {unmarked}, and none of them is marked as "
        "needing a newer libei -- a misspelt name, or a missing version marker"
    )
    if installed is not None:
        # The other direction: a marker that says 1.4+ on a 1.6 library is a
        # promise the library has to keep.
        overdue = [n for n in missing if marked[n] <= installed]
        assert not overdue, (
            f"{overdue} are marked as needing libei <= {installed} "
            "yet this library lacks them"
        )


def test_the_marked_functions_really_are_absent_from_a_library_that_predates_them() -> (
    None
):
    # Pins the marker comments to reality in the direction that matters on an
    # old library: where libei is older than a marker, the function is missing
    # and the binding says so by name rather than crashing.
    if not c_libei.lib.is_available():
        pytest.skip("libei is not installed")
    installed = _installed_version(c_libei.lib.soname)
    if installed is None:
        pytest.skip("cannot read the installed libei version here")
    handle = c_libei.lib._ensure_loaded()
    for name, needs in _marked_versions("ei_").items():
        if needs > installed:
            assert not hasattr(handle, name), f"{name} is present on libei {installed}"


# -- the upstream headers ----------------------------------------------------


def _header_dir() -> tuple[Path, tuple[int, ...] | None] | None:
    configured = os.environ.get("LIBEI_SOURCE_DIR")
    if configured:
        root = Path(configured)
        meson = root / "meson.build"
        version = None
        if meson.is_file():
            found = re.search(r"version:\s*'(\d+\.\d+\.\d+)'", meson.read_text())
            version = _version(found.group(1)) if found else None
        return root / "src", version
    for candidate in ("/usr/include/libei-1.0", "/usr/local/include/libei-1.0"):
        if (Path(candidate) / "libei.h").is_file():
            return Path(candidate), None
    return None


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


_PROTOTYPE = re.compile(
    r"(?m)^[ \t]*((?:const\s+|struct\s+|enum\s+|unsigned\s+)*[A-Za-z_]\w*[\s\*]*?)"
    r"[ \t]*\n?[ \t]*\b((?:ei|eis|oeffis)_\w+)\s*\(([^()]*)\)\s*"
    r"(?:__attribute__\(\([^;]*?\)\)|_\w+_(?:\([^)]*\))?)?\s*;"
)


def _size_class(ctype: str) -> str:
    """Collapse a C type to what the calling convention sees."""
    ctype = " ".join(ctype.replace("const", " ").split())
    if "*" in ctype:
        return "ptr"
    if ctype == "void":
        return "void"
    if ctype == "bool":
        return "bool"
    if ctype == "double":
        return "f64"
    if ctype in ("uint64_t", "int64_t"):
        return "i8"
    if ctype in ("size_t", "ssize_t", "uintptr_t"):
        # Pointer-sized, not fixed-width: 4 bytes on a 32-bit build.
        return {4: "i4", 8: "i8"}[ctypes.sizeof(ctypes.c_size_t)]
    if ctype.startswith("enum ") or ctype in (
        "int",
        "uint32_t",
        "int32_t",
        "unsigned",
        "unsigned int",
        "pid_t",
        "uid_t",
        "gid_t",
    ):
        return "i4"
    if ctype.startswith(("ei_", "eis_", "oeffis_")):
        # A typedef'd callback (ei_log_handler, ei_clock_now_func).
        return "ptr"
    raise AssertionError(f"unclassified C type {ctype!r}")


def _ctypes_class(ctype: type | None) -> str:
    if ctype is None:
        return "void"
    if ctype in (ctypes.c_void_p, ctypes.c_char_p) or isinstance(
        ctype, type(ctypes.POINTER(ctypes.c_int))
    ):
        return "ptr"
    if isinstance(ctype, type) and issubclass(ctype, ctypes._CFuncPtr):  # type: ignore[attr-defined]
        return "ptr"
    if ctype is ctypes.c_bool:
        return "bool"
    if ctype is ctypes.c_double:
        return "f64"
    size = ctypes.sizeof(ctype)  # type: ignore[arg-type]
    return {4: "i4", 8: "i8"}[size]


def _prototypes(prefix: str, directory: Path) -> dict[str, tuple[str, list[str], bool]]:
    """C name -> (return class, fixed argument classes, is variadic)."""
    text = _strip_comments(_header_text(directory, prefix))
    found: dict[str, tuple[str, list[str], bool]] = {}
    for match in _PROTOTYPE.finditer(text):
        returns, name, params = match.group(1), match.group(2), match.group(3)
        if not name.startswith(prefix):
            continue
        params = " ".join(params.split())
        variadic = params.endswith("...")
        pieces = [p.strip() for p in params.split(",") if p.strip() not in ("", "...")]
        if pieces == ["void"]:
            pieces = []
        classes = []
        for piece in pieces:
            if "*" in piece:
                classes.append("ptr")
                continue
            tokens = piece.replace("const", " ").split()
            # `enum tag` alone is an unnamed parameter; `enum tag name` or
            # `uint32_t name` carries one, which is not part of the type.
            unnamed = tokens[0] in ("enum", "struct") and len(tokens) == 2
            if len(tokens) >= 2 and not unnamed:
                tokens = tokens[:-1]
            classes.append(_size_class(" ".join(tokens)))
        found[name] = (_size_class(returns), classes, variadic)
    return found


def _header_text(directory: Path, prefix: str) -> str:
    """One public header's text, skipping where this install does not ship it.

    Distributions split them: Ubuntu's libei-dev has libei.h and libeis.h, and
    liboeffis.h arrives with liboeffis-dev.
    """
    path = directory / _HEADER_FILES[prefix]
    if not path.is_file():
        pytest.skip(f"{path} is not installed")
    return path.read_text()


@pytest.fixture(scope="module")
def headers() -> tuple[Path, tuple[int, ...] | None]:
    located = _header_dir()
    if located is None:
        pytest.skip(
            "no libei headers: set LIBEI_SOURCE_DIR to an upstream checkout "
            "or install libei-devel"
        )
    return located


@pytest.mark.parametrize("prefix", sorted(_LIBRARIES))
def test_every_declared_function_matches_its_upstream_prototype(
    prefix: str, headers: tuple[Path, tuple[int, ...] | None]
) -> None:
    directory, version = headers
    prototypes = _prototypes(prefix, directory)
    assert len(prototypes) > 5, f"parsed almost nothing from {directory}"
    marked = _marked_versions(prefix)

    problems: list[str] = []
    for name, (argtypes, restype) in _declared(prefix).items():
        if name not in prototypes:
            # Legitimate only where the header predates the function -- or is of
            # a version nothing here can read (an installed -dev package).
            if name in marked and (version is None or marked[name] > version):
                continue
            problems.append(f"{name}: no prototype in {_HEADER_FILES[prefix]}")
            continue
        returns, params, variadic = prototypes[name]
        declared_args = [_ctypes_class(a) for a in argtypes]
        if variadic:
            # Only the fixed prefix is declared: the trailing arguments go
            # through ctypes' default conversion.
            params = params[: len(declared_args)]
        if declared_args != params:
            problems.append(f"{name}: arguments {declared_args}, header says {params}")
        if _ctypes_class(restype) != returns:
            problems.append(
                f"{name}: returns {_ctypes_class(restype)}, header says {returns}"
            )
    assert not problems, "\n".join(problems)


# -- enums -------------------------------------------------------------------

# Python enum -> (C enum tag, prefix to strip from its enumerators).
_ENUMS: dict[str, tuple[type[enum.IntEnum] | type[enum.IntFlag], str, str, str]] = {
    "ei.EventType": (ei.EventType, "ei_", "ei_event_type", "EI_EVENT_"),
    "ei.DeviceCapability": (
        ei.DeviceCapability,
        "ei_",
        "ei_device_capability",
        "EI_DEVICE_CAP_",
    ),
    "ei.DeviceType": (ei.DeviceType, "ei_", "ei_device_type", "EI_DEVICE_TYPE_"),
    "ei.KeymapType": (ei.KeymapType, "ei_", "ei_keymap_type", "EI_KEYMAP_TYPE_"),
    "ei._LogPriority": (ei._LogPriority, "ei_", "ei_log_priority", "EI_LOG_PRIORITY_"),
    "eis.EventType": (eis.EventType, "eis_", "eis_event_type", "EIS_EVENT_"),
    "eis.DeviceCapability": (
        eis.DeviceCapability,
        "eis_",
        "eis_device_capability",
        "EIS_DEVICE_CAP_",
    ),
    "eis.DeviceType": (eis.DeviceType, "eis_", "eis_device_type", "EIS_DEVICE_TYPE_"),
    "eis.KeymapType": (eis.KeymapType, "eis_", "eis_keymap_type", "EIS_KEYMAP_TYPE_"),
    "eis._LogPriority": (
        eis._LogPriority,
        "eis_",
        "eis_log_priority",
        "EIS_LOG_PRIORITY_",
    ),
    "eis.Flag": (eis.Flag, "eis_", "eis_flag", "EIS_FLAG_"),
    "oeffis.DeviceType": (
        oeffis.DeviceType,
        "oeffis_",
        "oeffis_device",
        "OEFFIS_DEVICE_",
    ),
    "oeffis._EventType": (
        oeffis._EventType,
        "oeffis_",
        "oeffis_event_type",
        "OEFFIS_EVENT_",
    ),
}

# Members the Python enums carry that no released header has yet, on purpose
# (see the comments on each enum): they track upstream's main branch.
_AHEAD_OF_RELEASES = {
    "ei.EventType": {
        "SWIPE_BEGIN",
        "SWIPE_UPDATE",
        "SWIPE_END",
        "SWIPE_ABORTED",
        "PINCH_BEGIN",
        "PINCH_UPDATE",
        "PINCH_END",
        "PINCH_ABORTED",
        "HOLD_BEGIN",
        "HOLD_END",
        "HOLD_ABORTED",
        "STYLUS_PROXIMITY_IN",
        "STYLUS_PROXIMITY_OUT",
        "STYLUS_ERASE_START",
        "STYLUS_ERASE_STOP",
        "STYLUS_TIP_DOWN",
        "STYLUS_TIP_UP",
        "STYLUS_AXIS",
    },
    "ei.DeviceCapability": {"GESTURES", "STYLUS"},
}


def _c_enums(text: str) -> dict[str, dict[str, int]]:
    """Every ``enum tag { A = 1, B, ... }`` in the header, evaluated."""
    text = _strip_comments(text)
    result: dict[str, dict[str, int]] = {}
    for match in re.finditer(r"enum\s+(\w+)\s*\{([^}]*)\}", text):
        tag, body = match.group(1), match.group(2)
        values: dict[str, int] = {}
        next_value = 0
        for item in body.split(","):
            item = item.strip()
            if not item:
                continue
            if "=" in item:
                name, expression = (part.strip() for part in item.split("=", 1))
                next_value = eval(  # noqa: S307 - header constants only
                    expression, {"__builtins__": {}}, dict(values)
                )
            else:
                name = item
            values[name] = next_value
            next_value += 1
        result[tag] = values
    return result


@pytest.mark.parametrize("label", sorted(_ENUMS))
def test_every_enum_member_has_the_value_the_header_gives_it(
    label: str, headers: tuple[Path, tuple[int, ...] | None]
) -> None:
    directory, _ = headers
    python_enum, file_prefix, tag, prefix = _ENUMS[label]
    c_values = _c_enums(_header_text(directory, file_prefix)).get(tag)
    if c_values is None:
        pytest.skip(f"this libei's header has no enum {tag}")

    wrong, absent = [], []
    for member in python_enum.__members__.values():
        name = str(member.name)
        if name in _AHEAD_OF_RELEASES.get(label, set()):
            assert prefix + name not in c_values, (
                f"{name} is now in a released header: it no longer belongs in the "
                "ahead-of-releases list"
            )
            continue
        if prefix + name not in c_values:
            absent.append(name)
        elif c_values[prefix + name] != member.value:
            wrong.append(
                f"{name}: {member.value}, header says {c_values[prefix + name]}"
            )
    # A member the header does not have is only legitimate where the header is
    # older than the member -- the test cannot tell which, so it reports them
    # rather than asserting, and the wrong values are what fail.
    assert not wrong, "\n".join(wrong)
    assert len(absent) < len(python_enum.__members__), (
        f"none of {label}'s members were found under {prefix}: wrong prefix?"
    )


@pytest.mark.parametrize("label", sorted(_ENUMS))
def test_every_header_enumerator_is_covered_by_the_python_enum(
    label: str, headers: tuple[Path, tuple[int, ...] | None]
) -> None:
    """A value libei can send must not arrive as a bare, unnamed int."""
    directory, _ = headers
    python_enum, file_prefix, tag, prefix = _ENUMS[label]
    c_values = _c_enums(_header_text(directory, file_prefix)).get(tag)
    if c_values is None:
        pytest.skip(f"this libei's header has no enum {tag}")
    known = set(python_enum.__members__)
    uncovered = sorted(
        name[len(prefix) :]
        for name in c_values
        if name.startswith(prefix) and name[len(prefix) :] not in known
    )
    assert not uncovered, f"{label} lacks {uncovered}"


def test_the_header_parser_reads_a_prototype_and_an_enum() -> None:
    # The parser is the instrument the two tests above rely on; if it silently
    # returns nothing they pass vacuously, so it is pinned on a known text.
    sample = """
    /** doc */
    struct ei *
    ei_new_sender(void *user_data);
    void
    ei_seat_bind_capabilities(struct ei_seat *seat, ...) __attribute__((sentinel));
    uint64_t
    ei_event_get_time(struct ei_event *event);
    enum e { E_A = 1, E_B = (1 << 3), E_C };
    """
    text = _strip_comments(sample)
    names = {m.group(2): m for m in _PROTOTYPE.finditer(text)}
    assert set(names) == {
        "ei_new_sender",
        "ei_seat_bind_capabilities",
        "ei_event_get_time",
    }
    assert _c_enums(sample)["e"] == {"E_A": 1, "E_B": 8, "E_C": 9}
    assert _size_class("uint64_t") == "i8"
    assert _size_class("const char *") == "ptr"
    assert _size_class("enum ei_event_type") == "i4"
    assert sys.version_info >= (3, 9)
