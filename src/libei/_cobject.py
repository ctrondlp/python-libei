"""Base class for Python wrappers around refcounted opaque C pointers."""

from __future__ import annotations

import threading
import weakref
from typing import Any, ClassVar, TypeVar

T = TypeVar("T", bound="CObject")


class CObject:
    """Base class for a Python wrapper around a refcounted C pointer.

    Subclasses pick up three things:

    * ``_as_parameter_``, which ctypes consults automatically, so a
      wrapper instance can be passed straight to any bound C function
      expecting the underlying pointer.
    * Automatic ref/unref, driven by ``_ref_func``/``_unref_func`` (each
      a ``staticmethod``-wrapped bound C function, or ``None``).
    * An identity cache, so the same pointer yields the same Python
      object for as long as that object stays alive. Use :meth:`wrap`
      for a borrowed pointer and :meth:`adopt` for an owned one. The
      cache is shared by a class and all its subclasses, so one pointer
      has one wrapper no matter which class in the hierarchy wraps it --
      see :meth:`__init_subclass__` for why that matters.

    Subclasses that define ``__init__`` must accept and forward the
    keyword-only ``_adopt`` flag, or :meth:`wrap`/:meth:`adopt` will fail
    on them.

    .. note::
       The identity cache is keyed by raw address, and C libraries reuse
       addresses. If the library frees an object and allocates a new one
       at the same address while a wrapper for the old one is still
       alive, :meth:`wrap` returns the stale wrapper. :meth:`release`
       closes this for the short-lived objects where it actually bites
       (events, allocated and freed in a tight cycle); for longer-lived
       objects such as seats and devices, hold the wrapper for as long as
       you hold the C object and the question doesn't arise.
    """

    # These must be wrapped in staticmethod(). A plain function stored as
    # a class attribute is a descriptor: `self._ref_func` would hand back
    # a *bound* method, passing `self` to the C call as an extra leading
    # argument, and giving weakref.finalize a strong reference to `self`
    # that stops the object ever being collected.
    _ref_func: ClassVar[staticmethod[[int], Any] | None] = None
    _unref_func: ClassVar[staticmethod[[int], Any] | None] = None
    # False on classes that the library only ever hands out as brand-new,
    # freshly-allocated roots (ei.Context/Sender/Receiver, eis.Eis) rather
    # than as a sub-object reachable from some other wrapped object. There
    # is no legitimate pointer to wrap() or adopt() for those -- callers
    # get them from create_for_fd(), never from a getter -- so wrap()ing an
    # arbitrary int would construct a real wrapper around it and hand that
    # pointer straight to a C call (e.g. Context.__init__'s
    # log_set_handler()) with no way for this package or ctypes to tell a
    # garbage address from a real `struct ei *`. Blocking it here turns
    # that from a process-killing segfault into a catchable TypeError.
    _wrappable: ClassVar[bool] = True
    _instances: ClassVar[weakref.WeakValueDictionary[int, CObject]]
    # Reentrant: _get_or_create holds this while running __init__, which
    # registers the new object under the same lock, and which for some
    # subclasses also makes C calls that can re-enter Python.
    _instances_lock: ClassVar[threading.RLock]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # One cache per wrapper *hierarchy*, not per class. Only a root
        # wrapper class -- one whose only CObject ancestor is CObject
        # itself -- gets a fresh cache; deeper subclasses share their
        # root's.
        #
        # This is load-bearing for the unref-only classes. ei.Sender and
        # ei.Receiver subclass ei.Context, which has an _unref_func and no
        # _ref_func. With a cache per class, Sender.create_for_fd() would
        # register its wrapper under Sender, and a later Context.wrap() on
        # the same pointer would miss it, build a *second* wrapper with a
        # *second* finalizer, and take no ref -- two ei_unref() calls for
        # one reference, i.e. a use-after-free. (Context/Eis now also set
        # _wrappable = False, which closes this specific path a second,
        # earlier way -- Context.wrap() never gets far enough to look at
        # the cache at all. The shared cache stays as the general-purpose
        # invariant for any other unref-only hierarchy shaped like this
        # one, wrappable or not.)
        #
        # Sharing the cache also gives _get_or_create's isinstance() check
        # something real to do: Context.wrap() on a cached Sender now
        # returns that Sender, while Sender.wrap() on a cached plain
        # Context raises instead of silently double-wrapping.
        root = next(
            (b for b in cls.__mro__[1:] if b is not CObject and issubclass(b, CObject)),
            None,
        )
        if root is None:
            cls._instances = weakref.WeakValueDictionary()
            cls._instances_lock = threading.RLock()

    def __init__(self, pointer: int, *, _adopt: bool = False) -> None:
        if not pointer:
            raise ValueError(f"{type(self).__name__} cannot wrap a NULL pointer")
        self._pointer = pointer
        # Kept even after release() zeroes _pointer, so __hash__ stays
        # stable for the object's whole lifetime -- a hash that changes
        # would silently lose the object from any set or dict holding it.
        self._hash_key = pointer
        self._finalizer: Any = None  # weakref.finalize's generics need a ParamSpec
        if self._ref_func is not None and not _adopt:
            self._ref_func(pointer)
        if self._unref_func is not None:
            self._finalizer = weakref.finalize(self, self._unref_func, pointer)
        # Self-register, so a wrapper built by direct construction (as the
        # create_for_*() constructors do) is still found by a later
        # wrap()/adopt(). Without this, an unref-only class -- one with an
        # _unref_func but no _ref_func, such as Context/Eis/Event/Touch --
        # would get a *second* wrapper with a *second* finalizer but no
        # extra reference, and the C object would be unref'd twice.
        with type(self)._instances_lock:
            type(self)._instances[pointer] = self

    @property
    def _as_parameter_(self) -> int:
        if self._pointer == 0:
            raise RuntimeError(
                f"{type(self).__name__} has already been released; "
                "the underlying C object no longer exists"
            )
        return self._pointer

    def release(self) -> None:
        """Drop this object's C reference now rather than at GC time.

        Also evicts the wrapper from the identity cache and invalidates
        it, so any later use raises rather than reading through a pointer
        the C library may already have reused. Idempotent, and optional --
        skipping it just leaves the unref to the garbage collector.

        Worth calling explicitly wherever the *timing* of the unref is
        load-bearing. libei sends a SYNC event's pong reply exactly when
        that event is unref'd, so a caller waiting on the reply can stall
        indefinitely if the unref is left to the GC; the ``events``
        generators in :mod:`libei.ei` and :mod:`libei.eis` call this after
        each event for that reason.
        """
        # Invalidate and evict under one lock hold. Zeroing _pointer first
        # and locking afterwards leaves a window where a concurrent
        # _get_or_create can find this object still cached and hand back a
        # wrapper that is already invalid.
        with type(self)._instances_lock:
            pointer = self._pointer
            if pointer == 0:
                return
            self._pointer = 0
            if type(self)._instances.get(pointer) is self:
                type(self)._instances.pop(pointer, None)
        if self._finalizer is not None:
            self._finalizer()

    @classmethod
    def _get_or_create(cls: type[T], pointer: int | None, *, adopt: bool) -> T | None:
        if not pointer:
            return None
        if not cls._wrappable:
            # Checked before the lock, and well before the pointer ever
            # reaches a C call: cls(pointer, ...) below is what would
            # dereference it.
            raise TypeError(
                f"{cls.__name__} cannot be constructed from a raw pointer via "
                "wrap()/adopt() -- use its own create_for_*() classmethod instead"
            )
        # Locked so two threads racing to wrap the same new pointer can't
        # both pass the "not cached yet" check and construct (and ref)
        # two separate wrappers for it.
        with cls._instances_lock:
            existing = cls._instances.get(pointer)
            if existing is not None:
                if not isinstance(existing, cls):
                    # A real exception, not `assert`: this guards against
                    # a genuine cross-class pointer collision (or a bug
                    # letting one through), and assertions disappear
                    # under `python -O` -- silently returning the wrong
                    # wrapper type is worse than the check never running.
                    #
                    # ei.py/eis.py do use bare `assert x is not None` after
                    # wrap(), which is a different job: narrowing wrap()'s
                    # `T | None` for mypy at a getter the C API documents as
                    # never returning NULL. Losing one of those under -O
                    # costs an AttributeError on None; losing this one costs
                    # a wrapper of the wrong type over live memory.
                    raise TypeError(
                        f"pointer {pointer:#x} already wrapped as "
                        f"{type(existing).__name__}, not {cls.__name__}"
                    )
                return existing
            # __init__ self-registers, so no insert is needed here.
            return cls(pointer, _adopt=adopt)

    @classmethod
    def wrap(cls: type[T], pointer: int | None) -> T | None:
        """Return the cached wrapper for a *borrowed* pointer, or a new one.

        Use this for getters (e.g. ``event.device``), which hand back a
        reference the callee still owns -- an extra ref is taken to keep
        the C object alive for as long as this Python wrapper is.

        Returns ``None`` for a NULL pointer, matching the C API's own
        "may return NULL" contract instead of raising.

        Raises ``TypeError`` immediately, without touching the pointer, if
        ``cls`` has ``_wrappable = False`` (``ei.Context``/``Sender``/
        ``Receiver``, ``eis.Eis``) -- those are only ever handed out
        freshly-created by their own ``create_for_*()``, never as a
        sub-object, so there is no valid pointer to pass here.
        """
        return cls._get_or_create(pointer, adopt=False)

    @classmethod
    def adopt(cls: type[T], pointer: int | None) -> T | None:
        """Return the cached wrapper for an *owned* pointer, or a new one.

        Use this for constructor-style functions (e.g.
        ``eis_seat_new_device``) whose docs say the caller already owns
        the returned reference. Unlike :meth:`wrap`, this does not take an
        extra ref -- doing so would leave the object's refcount one higher
        than this wrapper's single finalizer unref ever brings it back
        down to, leaking it for the life of the process.

        Raises ``TypeError`` immediately, without touching the pointer, if
        ``cls`` has ``_wrappable = False`` -- see :meth:`wrap`.
        """
        return cls._get_or_create(pointer, adopt=True)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CObject):
            return NotImplemented
        if type(self) is not type(other):
            return False
        # A released wrapper no longer stands for a live C object, so it
        # is only equal to itself. Comparing zeroed pointers instead would
        # make every released wrapper of a given type compare equal to
        # every other one, however unrelated the objects they once wrapped.
        if self._pointer == 0 or other._pointer == 0:
            return self is other
        return self._pointer == other._pointer

    def __hash__(self) -> int:
        # Deliberately keyed on _hash_key, not _pointer: release() zeroes
        # _pointer, and an object whose hash changes mid-life vanishes
        # from any set or dict it was placed in. Two wrappers can share a
        # hash without being equal -- __eq__ above settles that.
        return hash((type(self), self._hash_key))
