"""Unit tests for the deferred-loading mechanism itself.

Uses libc.so.6 as a stand-in shared library (present on every Linux system)
so these run without libei installed at all -- they're testing the loader,
not libei.
"""

from __future__ import annotations

from ctypes import c_char_p, c_int

import pytest

from libei._capi.loader import LazyLibrary, LibraryNotFoundError


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


def test_real_library_is_available() -> None:
    lib = LazyLibrary("libc.so.6")
    assert lib.is_available() is True


def test_real_function_call_round_trips() -> None:
    lib = LazyLibrary("libc.so.6")
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


def test_missing_symbol_raises_library_not_found_error() -> None:
    lib = LazyLibrary("libc.so.6")
    nonexistent = lib.function("this_symbol_does_not_exist_in_libc", (c_char_p,), c_int)
    with pytest.raises(LibraryNotFoundError, match="does not export"):
        nonexistent(b"x")


def test_two_lazy_libraries_are_independent() -> None:
    good = LazyLibrary("libc.so.6")
    bad = LazyLibrary("this-library-definitely-does-not-exist.so.999")
    assert good.is_available() is True
    assert bad.is_available() is False
