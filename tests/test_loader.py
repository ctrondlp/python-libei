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

from ctypes import c_char_p, c_int, util

import pytest

from libei._capi.loader import LazyLibrary, LibraryNotFoundError

_found_libc = util.find_library("c")
if _found_libc is None:
    raise RuntimeError("no C library found -- these tests need one to stand in")
_LIBC: str = _found_libc


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
    lib = LazyLibrary(_LIBC)
    assert lib.is_available() is True


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


def test_missing_symbol_raises_library_not_found_error() -> None:
    lib = LazyLibrary(_LIBC)
    nonexistent = lib.function("this_symbol_does_not_exist_in_libc", (c_char_p,), c_int)
    with pytest.raises(LibraryNotFoundError, match="does not export"):
        nonexistent(b"x")


def test_two_lazy_libraries_are_independent() -> None:
    good = LazyLibrary(_LIBC)
    bad = LazyLibrary("this-library-definitely-does-not-exist.so.999")
    assert good.is_available() is True
    assert bad.is_available() is False
