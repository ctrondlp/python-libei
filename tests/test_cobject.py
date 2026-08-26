"""Unit tests for the CObject refcounting/identity-cache base class.

Pure Python logic -- no native library involved. Uses fake ref/unref
counters to verify the contract independently of any real C API.
"""

from __future__ import annotations

import gc
import threading
import time

import pytest

from libei._cobject import CObject


def make_tracked_class(*, with_ref: bool = True) -> type[CObject]:
    calls: list[tuple[str, int]] = []

    class Tracked(CObject):
        pass

    # staticmethod() matters here, not just for mypy: without it, a plain
    # function stored as a class attribute is a descriptor, so accessing it
    # via `self._ref_func` would bind it, silently passing `self` as an
    # extra leading argument -- these tests would fail with a hard
    # TypeError from the lambda's arity mismatch if that regressed.
    if with_ref:
        Tracked._ref_func = staticmethod(lambda p: calls.append(("ref", p)))
    Tracked._unref_func = staticmethod(lambda p: calls.append(("unref", p)))
    Tracked.calls = calls  # type: ignore[attr-defined]
    return Tracked


def test_wrap_null_pointer_returns_none() -> None:
    Tracked = make_tracked_class()
    assert Tracked.wrap(0) is None
    assert Tracked.wrap(None) is None


def test_direct_construction_rejects_null_pointer() -> None:
    Tracked = make_tracked_class()
    with pytest.raises(ValueError):
        Tracked(0)


def test_wrap_calls_ref_on_construction() -> None:
    Tracked = make_tracked_class()
    Tracked.wrap(0x1234)
    assert ("ref", 0x1234) in Tracked.calls  # type: ignore[attr-defined]


def test_wrap_same_pointer_returns_same_object() -> None:
    Tracked = make_tracked_class()
    a = Tracked.wrap(0x1234)
    b = Tracked.wrap(0x1234)
    assert a is b
    # ref must only be called once -- wrap() found the cached object the
    # second time, it didn't construct (and thus ref) a new one.
    ref_calls = [c for c in Tracked.calls if c == ("ref", 0x1234)]  # type: ignore[attr-defined]
    assert len(ref_calls) == 1


def test_different_pointers_are_different_objects() -> None:
    Tracked = make_tracked_class()
    a = Tracked.wrap(0x1111)
    b = Tracked.wrap(0x2222)
    assert a is not b
    assert a != b


def test_as_parameter_returns_the_raw_pointer() -> None:
    Tracked = make_tracked_class()
    obj = Tracked.wrap(0xABCD)
    assert obj is not None
    assert obj._as_parameter_ == 0xABCD


def test_unref_called_when_last_reference_dropped() -> None:
    Tracked = make_tracked_class()
    obj = Tracked.wrap(0x5555)
    del obj
    gc.collect()
    assert ("unref", 0x5555) in Tracked.calls  # type: ignore[attr-defined]


def test_cache_does_not_keep_object_alive() -> None:
    Tracked = make_tracked_class()
    obj = Tracked.wrap(0x9999)
    obj_id = id(obj)
    del obj
    gc.collect()
    # A fresh wrap() after the original was collected must build a new
    # object, not resurrect a stale entry from the weak cache.
    obj2 = Tracked.wrap(0x9999)
    assert id(obj2) != obj_id


def test_wrap_without_ref_func_still_tracks_unref() -> None:
    # Some C types (e.g. one-shot Event objects) are unref-only: no ref()
    # call on construction, since the caller already owns a reference.
    Tracked = make_tracked_class(with_ref=False)
    obj = Tracked.wrap(0x42)
    assert obj is not None
    assert not any(c[0] == "ref" for c in Tracked.calls)  # type: ignore[attr-defined]
    del obj
    gc.collect()
    assert ("unref", 0x42) in Tracked.calls  # type: ignore[attr-defined]


def test_two_subclasses_have_independent_instance_caches() -> None:
    A = make_tracked_class()
    B = make_tracked_class()
    a = A.wrap(0x100)
    b = B.wrap(0x100)  # same pointer value, different Python type
    assert a is not b
    assert type(a) is not type(b)


def test_wrap_rejects_pointer_already_owned_by_another_class() -> None:
    A = make_tracked_class()

    class NotA(CObject):
        pass

    NotA._instances = A._instances  # force a collision for this test only
    a = A.wrap(0x777)
    assert a is not None
    with pytest.raises(TypeError):
        NotA.wrap(0x777)


def test_wrap_is_thread_safe_against_concurrent_construction() -> None:
    # _get_or_create's check-then-insert used to run outside any lock: two
    # threads racing to wrap() the same brand-new pointer could both see
    # "not cached yet" and each construct (and ref) their own wrapper,
    # with only one surviving in the cache -- silently double-refing the
    # underlying object with no way to tell from the Python side.
    #
    # _ref_func runs inside __init__, which _get_or_create now calls with
    # its lock held -- sleeping there holds that lock for long enough that
    # every other thread launched below is virtually guaranteed to have
    # reached (and blocked on) it first, reliably forcing the contention
    # this test exists to catch a regression in.
    Tracked = make_tracked_class()
    thread_count = 8
    ref_calls: list[int] = []

    def slow_ref(pointer: int) -> None:
        time.sleep(0.05)
        ref_calls.append(pointer)

    Tracked._ref_func = staticmethod(slow_ref)

    results: list[CObject | None] = [None] * thread_count

    def worker(index: int) -> None:
        results[index] = Tracked.wrap(0x1234)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    assert len(ref_calls) == 1, f"expected exactly one ref() call, got {ref_calls}"
    assert all(r is results[0] for r in results), (
        "every thread must get the same wrapper"
    )


def test_hash_is_stable_across_release() -> None:
    # release() zeroes _pointer. If __hash__ read that, an object's hash
    # would change mid-life and it would vanish from any set or dict
    # holding it -- and events are released on every loop iteration.
    Tracked = make_tracked_class()
    obj = Tracked.wrap(0x1234)
    assert obj is not None
    container = {obj}
    before = hash(obj)

    obj.release()

    assert hash(obj) == before
    assert obj in container


def test_released_objects_are_not_equal_to_each_other() -> None:
    # Comparing zeroed pointers would make every released wrapper of a
    # given type equal to every other one, however unrelated.
    Tracked = make_tracked_class()
    a = Tracked.wrap(0xAAAA)
    b = Tracked.wrap(0xBBBB)
    assert a is not None and b is not None

    a.release()
    b.release()

    assert a != b
    assert a == a  # but still equal to itself


def test_released_object_is_not_equal_to_a_live_one_at_the_same_address() -> None:
    Tracked = make_tracked_class()
    first = Tracked.wrap(0x4242)
    assert first is not None
    first.release()  # evicts it from the cache

    # The C library reuses the address for a different object.
    second = Tracked.wrap(0x4242)
    assert second is not None

    assert second is not first
    assert first != second


def test_live_objects_with_the_same_pointer_are_equal() -> None:
    Tracked = make_tracked_class()
    a = Tracked.wrap(0x1234)
    b = Tracked.wrap(0x1234)
    assert a == b
    assert hash(a) == hash(b)


def test_subclass_init_must_accept_the_adopt_flag() -> None:
    # _get_or_create always passes _adopt=..., so any subclass that
    # defines __init__ without accepting it breaks wrap()/adopt() with a
    # TypeError. Guarding every concrete subclass in the package here is
    # cheaper than discovering it at a call site.
    import inspect

    from libei import ei, eis

    concrete = [
        ei.Context,
        ei.Sender,
        ei.Receiver,
        ei.Device,
        ei.Seat,
        ei.Event,
        ei.Region,
        ei.Keymap,
        ei.Touch,
        eis.Eis,
        eis.Device,
        eis.Seat,
        eis.Client,
        eis.Event,
        eis.Region,
        eis.Keymap,
        eis.Touch,
    ]
    missing = [
        f"{cls.__module__}.{cls.__qualname__}"
        for cls in concrete
        if "_adopt" not in inspect.signature(cls.__init__).parameters
    ]
    assert not missing, f"these do not accept/forward _adopt: {missing}"


def test_directly_constructed_object_is_found_by_wrap() -> None:
    # The create_for_*() constructors build wrappers with cls(ptr), not
    # through _get_or_create. If those never landed in the cache, a later
    # wrap() would build a *second* wrapper -- see the next test for why
    # that corrupts memory for unref-only classes.
    Tracked = make_tracked_class()
    direct = Tracked(0x7000)
    assert Tracked.wrap(0x7000) is direct


def test_unref_only_class_is_never_unrefd_twice() -> None:
    # Context/Eis/Event/Touch have an _unref_func but no _ref_func: each
    # wrapper installs a finalizer without taking a reference. Two
    # wrappers for one pointer therefore means two unrefs for one ref --
    # a use-after-free. The identity cache is what prevents it, so a
    # wrapper built outside the cache must still register itself.
    unrefs: list[int] = []

    class UnrefOnly(CObject):
        _unref_func = staticmethod(unrefs.append)

    direct = UnrefOnly(0x7100)
    same = UnrefOnly.wrap(0x7100)
    assert same is direct

    del direct, same
    gc.collect()

    assert unrefs == [0x7100], f"expected exactly one unref, got {unrefs}"


def test_release_invalidates_and_evicts_atomically() -> None:
    # release() must zero _pointer and drop the cache entry under one lock
    # hold. Doing the zeroing first and locking afterwards leaves a window
    # where another thread finds the object still cached but already dead.
    Tracked = make_tracked_class()
    obj = Tracked.wrap(0x7200)
    assert obj is not None

    entered = threading.Event()

    with Tracked._instances_lock:
        releaser = threading.Thread(target=obj.release)
        releaser.start()
        entered.wait(0.2)  # give it time to block on the lock

        # While we hold the lock, release() cannot have progressed, so the
        # cached object must still be usable. If invalidation happened
        # before the lock, this is where we would see a zeroed pointer.
        cached = Tracked._instances.get(0x7200)
        if cached is not None:
            assert cached._as_parameter_ == 0x7200

    releaser.join(timeout=2)
    assert Tracked._instances.get(0x7200) is None
    with pytest.raises(RuntimeError):
        _ = obj._as_parameter_


def test_instances_lock_is_reentrant() -> None:
    # _get_or_create holds the lock while running __init__, which
    # self-registers under the same lock -- and for some subclasses also
    # makes C calls that can re-enter Python. A plain Lock deadlocks.
    #
    # Probed with a timeout rather than by simply nesting `with` blocks:
    # against a non-reentrant Lock the naive form hangs forever, which
    # wedges the whole suite instead of reporting a failure.
    Tracked = make_tracked_class()
    assert Tracked._instances_lock.acquire(timeout=1)
    try:
        acquired_again = Tracked._instances_lock.acquire(timeout=1)
        assert acquired_again, "_instances_lock is not reentrant; it would deadlock"
        Tracked._instances_lock.release()
    finally:
        Tracked._instances_lock.release()


def test_construction_can_re_enter_the_cache() -> None:
    # The concrete reason the lock must be reentrant: __init__ registers
    # into the cache while _get_or_create already holds the lock. Run in a
    # worker with a join timeout so a regression fails the test instead of
    # hanging the suite.
    reentered: list[object] = []

    class Reentrant(CObject):
        _unref_func = staticmethod(lambda p: None)

        def __init__(self, pointer: int, *, _adopt: bool = False) -> None:
            super().__init__(pointer, _adopt=_adopt)
            # Re-entering the cache from inside construction, as a
            # subclass whose __init__ triggers a callback might.
            reentered.append(Reentrant.wrap(pointer))

    result: list[object] = []
    worker = threading.Thread(target=lambda: result.append(Reentrant.wrap(0x7300)))
    worker.daemon = True
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive(), "construction deadlocked re-entering the cache"
    assert result and result[0] is not None
    assert reentered == [result[0]]


def test_subclass_shares_its_root_identity_cache() -> None:
    # A cache per class rather than per hierarchy is a use-after-free for
    # the unref-only classes: ei.Sender/Receiver subclass ei.Context,
    # which unrefs but never refs. A directly-constructed Sender would
    # register only under Sender, so Context.wrap() on the same pointer
    # would miss it, build a second wrapper with a second finalizer, and
    # take no ref -- two unrefs for one reference.
    Base = make_tracked_class(with_ref=False)

    class Sub(Base):  # type: ignore[valid-type,misc]
        pass

    assert Sub._instances is Base._instances
    assert Sub._instances_lock is Base._instances_lock

    obj = Sub(0x9100)
    assert Base.wrap(0x9100) is obj

    del obj
    gc.collect()
    unrefs = [c for c in Base.calls if c == ("unref", 0x9100)]  # type: ignore[attr-defined]
    assert unrefs == [("unref", 0x9100)], f"expected exactly one unref, got {unrefs}"


def test_wrapping_as_a_sibling_subclass_raises() -> None:
    # The flip side of sharing: asking for a *narrower* type than the
    # cached wrapper must raise rather than quietly minting a second
    # wrapper for the same pointer.
    Base = make_tracked_class(with_ref=False)

    class Sub(Base):  # type: ignore[valid-type,misc]
        pass

    base_obj = Base.wrap(0x9200)
    assert base_obj is not None
    with pytest.raises(TypeError, match="already wrapped as"):
        Sub.wrap(0x9200)


def test_context_subclasses_share_one_cache() -> None:
    # The real classes this exists for, not just a stand-in.
    from libei import ei

    assert ei.Sender._instances is ei.Context._instances
    assert ei.Receiver._instances is ei.Context._instances
    # Separate hierarchies stay separate: an ei pointer and an eis pointer
    # with the same numeric value must not collide.
    from libei import eis

    assert ei.Region._instances is not eis.Region._instances


def test_wrap_on_non_wrappable_class_raises_without_touching_pointer() -> None:
    # Context/Eis set _wrappable = False because their real __init__ makes
    # unconditional C calls (log_set_handler/log_set_priority) that would
    # dereference whatever pointer is handed in. The regression this guards
    # against: wrap()/adopt() must reject a non-wrappable class *before*
    # ever constructing an instance, not just before caching one -- proven
    # here by asserting _ref_func/_unref_func, which only fire from inside
    # __init__/finalization, were never called at all.
    Tracked = make_tracked_class()
    Tracked._wrappable = False

    with pytest.raises(TypeError, match="cannot be constructed from a raw pointer"):
        Tracked.wrap(0x1234)
    with pytest.raises(TypeError, match="cannot be constructed from a raw pointer"):
        Tracked.adopt(0x1234)

    assert Tracked.calls == []  # type: ignore[attr-defined]


def test_wrap_on_non_wrappable_class_still_returns_none_for_null() -> None:
    # The NULL short-circuit in _get_or_create runs before the _wrappable
    # check, so it must keep its existing "returns None" contract rather
    # than start raising for a pointer that was never going to be
    # constructed anyway.
    Tracked = make_tracked_class()
    Tracked._wrappable = False

    assert Tracked.wrap(0) is None
    assert Tracked.adopt(None) is None


def test_non_wrappable_subclasses_inherit_the_restriction() -> None:
    # ei.Sender/Receiver don't set _wrappable themselves -- they inherit
    # False from ei.Context. Prove ordinary attribute inheritance actually
    # carries it, with a stand-in rather than the real classes.
    Base = make_tracked_class(with_ref=False)
    Base._wrappable = False

    class Sub(Base):  # type: ignore[valid-type,misc]
        pass

    with pytest.raises(TypeError, match="cannot be constructed from a raw pointer"):
        Sub.wrap(0x1234)
    assert Base.calls == []  # type: ignore[attr-defined]


def test_context_and_eis_are_not_wrappable() -> None:
    # The real bug this exists for: Context.wrap()/Eis.wrap() (and by
    # inheritance Sender/Receiver) used to construct successfully from any
    # int once __init__ started accepting _adopt, which meant a garbage
    # pointer reached log_set_handler()/log_set_priority() and could
    # segfault the process. Now safe to call directly with a bogus pointer
    # -- construction never happens.
    from libei import ei, eis

    for cls in (ei.Context, ei.Sender, ei.Receiver, eis.Eis):
        with pytest.raises(TypeError, match="cannot be constructed from a raw pointer"):
            cls.wrap(0x1234)
        with pytest.raises(TypeError, match="cannot be constructed from a raw pointer"):
            cls.adopt(0x1234)
