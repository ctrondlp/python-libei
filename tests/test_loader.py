"""Unit tests for the deferred-loading mechanism itself.

Uses the C library as a stand-in shared library (present on every POSIX
system) so these run without libei installed at all -- they're testing the
loader, not libei. The soname isn't portable -- glibc is libc.so.6, FreeBSD
is libc.so.7 -- so it's resolved with ctypes.util.find_library rather than
hardcoded; a hardcoded libc.so.6 here once made every test below fail on
FreeBSD, not because LazyLibrary was wrong but because the soname it was
asked to load doesn't exist there.
"""

from __future__ import annotations

import sys
from ctypes import ArgumentError, c_char_p, c_int, c_void_p, util

import pytest

from libei._capi.loader import LazyLibrary, LibraryNotFoundError

_found_libc = util.find_library("c")
_LIBC: str = _found_libc or ""
# A skip, not a module-level raise: raising here was a *collection* error, so
# on a machine with no findable C library (Windows) pytest ran none of the
# suite's other tests either. Only the tests that load a real library need
# one; the loader-defers tests below run everywhere.
_needs_libc = pytest.mark.skipif(
    _found_libc is None, reason="needs a real C library as a stand-in (POSIX only)"
)


def test_import_does_not_touch_the_filesystem() -> None:
    # Constructing a LazyLibrary and declaring functions on it must not
    # dlopen anything -- that's the whole point of deferring the load.
    lib = LazyLibrary("this-library-definitely-does-not-exist.so.999")
    lib.function("abs", (c_int,), c_int)  # declaring, not calling
    # no exception means we got this far without ever opening the library


def test_missing_library_raises_only_when_called() -> None:
    lib = LazyLibrary("this-library-definitely-does-not-exist.so.999")
    abs_ = lib.function("abs", (c_int,), c_int)
    with pytest.raises(LibraryNotFoundError):
        abs_(-1)


def test_missing_library_is_not_available() -> None:
    lib = LazyLibrary("this-library-definitely-does-not-exist.so.999")
    assert lib.is_available() is False


@_needs_libc
def test_real_library_is_available() -> None:
    lib = LazyLibrary(_LIBC)
    assert lib.is_available() is True


@_needs_libc
def test_real_function_call_round_trips() -> None:
    lib = LazyLibrary(_LIBC)
    abs_ = lib.function("abs", (c_int,), c_int)
    assert abs_(-7) == 7
    assert abs_(7) == 7


def test_function_result_is_cached_across_calls() -> None:
    # Not observable from the return value alone, so check indirectly:
    # calling twice must not re-resolve/re-raise differently once the
    # library is known-missing.
    lib = LazyLibrary("this-library-definitely-does-not-exist.so.999")
    abs_ = lib.function("abs", (c_int,), c_int)
    with pytest.raises(LibraryNotFoundError):
        abs_(-1)
    with pytest.raises(LibraryNotFoundError):
        abs_(-1)


@_needs_libc
def test_missing_symbol_raises_library_not_found_error() -> None:
    lib = LazyLibrary(_LIBC)
    nonexistent = lib.function("this_symbol_does_not_exist_in_libc", (c_char_p,), c_int)
    with pytest.raises(LibraryNotFoundError, match="does not export"):
        nonexistent(b"x")


@_needs_libc
def test_two_lazy_libraries_are_independent() -> None:
    good = LazyLibrary(_LIBC)
    bad = LazyLibrary("this-library-definitely-does-not-exist.so.999")
    assert good.is_available() is True
    assert bad.is_available() is False


@_needs_libc
def test_a_missing_symbol_names_itself_and_leaves_the_rest_working() -> None:
    # A library that loads but lacks a newer symbol (an old libei asked for a
    # 1.6 function) must fail that one call, by name, and nothing else: the
    # older functions keep working before and after, and asking again gives
    # the same answer rather than a half-initialised state.
    lib = LazyLibrary(_LIBC)
    present = lib.function("abs", (c_int,), c_int)
    absent = lib.function("symbol_added_in_a_newer_release", (c_int,), c_int)

    assert present(-3) == 3
    for _ in range(3):
        with pytest.raises(LibraryNotFoundError) as caught:
            absent(1)
        assert "symbol_added_in_a_newer_release" in str(caught.value)
        assert present(-4) == 4
        assert lib.is_available() is True


@_needs_libc
def test_a_missing_symbol_is_not_resolved_until_it_is_called() -> None:
    lib = LazyLibrary(_LIBC)
    absent = lib.function("symbol_added_in_a_newer_release", (c_int,), c_int)
    # Declaring it, and calling something else, cost nothing and raised nothing.
    assert lib.function("abs", (c_int,), c_int)(-1) == 1
    assert "symbol_added_in_a_newer_release" in lib.declared
    with pytest.raises(LibraryNotFoundError):
        absent(0)


def test_declarations_are_recorded_without_opening_the_library() -> None:
    lib = LazyLibrary("this-library-definitely-does-not-exist.so.999")
    lib.function("abs", (c_int,), c_int)
    lib.function("puts", (c_char_p,), None)
    assert lib.declared == {"abs": ((c_int,), c_int), "puts": ((c_char_p,), None)}
    assert lib.soname == "this-library-definitely-does-not-exist.so.999"
    # Only a call may fail; reading the registry must not have tried to load.
    assert lib._lib is None and lib._load_error is None


@_needs_libc
def test_concurrent_first_calls_open_the_library_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The double-checked lock in _ensure_loaded is there for exactly this.
    import ctypes
    import threading

    opened: list[str] = []
    real = ctypes.CDLL

    def counting(name: str, *args: object, **kwargs: object) -> ctypes.CDLL:
        opened.append(name)
        return real(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ctypes, "CDLL", counting)
    lib = LazyLibrary(_LIBC)
    abs_ = lib.function("abs", (c_int,), c_int)
    start = threading.Barrier(8)
    results: list[int] = []

    def worker() -> None:
        start.wait()
        results.append(abs_(-5))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [5] * 8
    assert opened == [_LIBC]


class _Released:
    """What a released CObject looks like to ctypes."""

    @property
    def _as_parameter_(self) -> int:
        raise RuntimeError("Thing has already been released")


def _stand_in_library() -> str | None:
    # msvcrt on Windows, where find_library("c") finds nothing: this test needs
    # only *a* C function taking an int, and it is the one about ctypes itself.
    return "msvcrt" if sys.platform == "win32" else _found_libc


def test_a_released_object_raises_runtime_error_not_ctypes_argument_error() -> None:
    # ctypes wraps whatever _as_parameter_ raises in an ArgumentError that keeps
    # only the message. The mocked suites never cross ctypes, so they passed
    # while a real call with a released Event raised ArgumentError -- and the
    # documented `except RuntimeError` missed it.
    soname = _stand_in_library()
    if soname is None:
        pytest.skip("needs a real C library as a stand-in")
    lib = LazyLibrary(soname)
    abs_ = lib.function("abs", (c_void_p,), c_int)
    with pytest.raises(RuntimeError, match="already been released"):
        abs_(_Released())
    # An ordinary bad argument is still ctypes' own error, untouched.
    with pytest.raises(ArgumentError):
        abs_(object())
    # And a good call is unaffected.
    assert abs_(0) == 0
