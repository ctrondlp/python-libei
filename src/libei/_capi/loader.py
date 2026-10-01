"""Deferred loading of the native libei/libeis/liboeffis shared libraries.

``ctypes.CDLL(soname)`` raises ``OSError`` immediately if the library isn't
installed. Binding that call to module import (as a naive ctypes wrapper
would) means ``import libei.ei`` fails hard on any system that hasn't
installed libei -- even for code that only wants to construct dataclasses or
introspect enums. :class:`LazyLibrary` defers the ``dlopen`` until the first
call actually made through it, so importing this package is always safe and
callers can check :meth:`LazyLibrary.is_available` before depending on it.
"""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable, Sequence
from typing import Any


class LibraryNotFoundError(RuntimeError):
    """The native shared library could not be loaded."""


class LazyLibrary:
    """A ctypes.CDLL that only opens the library on first real use."""

    def __init__(self, soname: str) -> None:
        """Remember the soname; nothing is opened until first use."""
        self._soname = soname
        self._lib: ctypes.CDLL | None = None
        self._load_error: OSError | None = None
        self._lock = threading.Lock()
        self._declared: dict[str, tuple[tuple[type, ...], type | None]] = {}

    def _ensure_loaded(self) -> ctypes.CDLL:
        """Return the opened library, opening it on first call.

        Raises LibraryNotFoundError on every later call too after a failure:
        the failed load is cached rather than retried.
        """
        # Double-checked: the unlocked read is the fast path taken by every
        # call after the first, and the repeated check inside the lock is
        # what makes it safe -- two threads can both fall through the first
        # check, and the second one must not dlopen() again after the first
        # has finished. The repetition is deliberate, not redundant.
        if self._lib is not None:
            return self._lib
        with self._lock:
            if self._lib is not None:
                return self._lib
            # A failed load is cached and re-raised rather than retried:
            # a missing library does not appear mid-process, and retrying
            # would pay the dlopen() cost on every call for a caller that
            # ignores the exception in a loop.
            if self._load_error is not None:
                raise LibraryNotFoundError(
                    f"{self._soname} is not available"
                ) from self._load_error
            try:
                self._lib = ctypes.CDLL(self._soname, use_errno=True)
            except OSError as exc:
                self._load_error = exc
                raise LibraryNotFoundError(f"{self._soname} is not available") from exc
            return self._lib

    def is_available(self) -> bool:
        """Whether the library can be loaded on this system.

        Attempts the load if it hasn't been tried yet, then caches the
        result -- callers can use this to skip integration tests or fall
        back to another backend without triggering an exception.
        """
        try:
            self._ensure_loaded()
        except LibraryNotFoundError:
            return False
        return True

    @property
    def soname(self) -> str:
        """The shared library this binds, as it is passed to ``dlopen``."""
        return self._soname

    @property
    def declared(self) -> dict[str, tuple[tuple[type, ...], type | None]]:
        """Every function bound so far: C name -> (argtypes, restype).

        Read by the ABI tests, which compare it with the library's exports and
        with the upstream headers. Declaring a binding records it here and does
        nothing else -- the library is still not opened until a call.
        """
        return dict(self._declared)

    def function(
        self,
        name: str,
        argtypes: Sequence[type],
        restype: type | None,
    ) -> Callable[..., Any]:
        """Bind a single C function, resolved and typed on first call.

        ``argtypes``/``restype`` are applied the first time the function is
        actually invoked, not when this method runs -- so declaring a full
        set of bindings at module scope never touches the filesystem.
        """
        # A one-slot dict used as a mutable cell. `call` below has to write
        # the resolved function back somewhere the *next* call can see, and
        # a plain `bound = ...` inside `call` would just create a local. A
        # `nonlocal` on a variable declared here would work equally well;
        # the dict is chosen only because "absent from the dict" already
        # means "not resolved yet", with no None sentinel to confuse with a
        # legitimately-None value.
        self._declared[name] = (tuple(argtypes), restype)
        cache: dict[str, Any] = {}

        def call(*args: Any) -> Any:
            """Resolve the C function on first call, then pass straight through."""
            # Resolution happens here, on first call, not at bind time --
            # that is the whole point of this module (see its docstring).
            bound = cache.get("f")
            if bound is None:
                lib = self._ensure_loaded()
                try:
                    bound = getattr(lib, name)
                except AttributeError as exc:
                    raise LibraryNotFoundError(
                        f"{self._soname} does not export {name!r} "
                        "(installed version may be too old)"
                    ) from exc
                bound.argtypes = list(argtypes)
                bound.restype = restype
                cache["f"] = bound
            try:
                return bound(*args)
            except ctypes.ArgumentError:
                # ctypes turns *any* exception raised while converting an
                # argument into an ArgumentError carrying only its text -- no
                # __cause__, no __context__. For a released object that loses
                # the RuntimeError its ``_as_parameter_`` raised, so a caller
                # catching RuntimeError (as documented) would miss it. Ask the
                # arguments again and re-raise the real one.
                for arg in args:
                    try:
                        arg._as_parameter_  # noqa: B018 - evaluated for its error
                    except RuntimeError as released:
                        raise released from None
                    except AttributeError:
                        continue
                raise

        call.__name__ = name
        return call
