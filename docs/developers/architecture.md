# Architecture

How the package is put together. Reading order matters here — each layer only
makes sense once the one below it does.

## Four layers, bottom up

| Layer | What it does |
|-------|--------------|
| [`_capi/loader.py`](../../src/libei/_capi/loader.py) | `LazyLibrary`: `dlopen`s a `.so` on first *call*, not at import, so this package imports fine with no native libraries installed |
| [`_capi/libei.py`](../../src/libei/_capi/libei.py), `libeis.py`, `liboeffis.py` | One line per C function, with hand-written ctypes signatures. Nothing else — no logic |
| [`_cobject.py`](../../src/libei/_cobject.py) | `CObject`: pointer ownership, refcounting, and the identity cache that every wrapper class inherits |
| [`ei.py`](../../src/libei/ei.py), [`eis.py`](../../src/libei/eis.py), [`oeffis.py`](../../src/libei/oeffis.py) | The public API: Python classes, enums and dataclasses over the raw calls |

[`portal.py`](../../src/libei/portal.py) sits outside this stack entirely —
there is no C library behind it, so no `_capi` binding and no `CObject`. It
talks D-Bus directly through PyGObject (`Gio`/`GLib`, imported lazily the same
way the C libraries are loaded lazily) and only ever produces a plain fd,
which is where it hands off to `ei.Sender.create_for_fd()`.

## Read `_cobject.py` first

It is the smallest file with the most consequence: get `wrap()` vs `adopt()`,
the `staticmethod()` wrapping of `_ref_func`/`_unref_func`, or the
`_wrappable` flag wrong and the failure is a use-after-free or a segfault
rather than a traceback. Every non-obvious line there carries a comment
explaining what breaks without it.

## Two conventions

- **`_capi` names drop the C prefix.** `ei_unref()` is `_capi.libei.unref()`,
  `eis_device_configure_name()` is `_capi.libeis.device_configure_name()`. The
  module says which library it is, so repeating it in every name would only
  add noise.
- **Wrapper instances are passed straight to C calls.** `CObject` defines
  `_as_parameter_`, which ctypes consults automatically, so
  `_capi.libei.device_frame(self, timestamp)` works without unwrapping a
  pointer out of `self` by hand.

## What is bound, and what is deliberately not

The ctypes layer binds 250 of the 302 functions the three libraries export as
of 1.6.0 (libei 109/132, libeis 131/158, liboeffis 10/12). What is left out is
deliberate:

- `*_get_user_data()` / `*_set_user_data()` — the Python wrapper object is
  where you keep state.
- The `*_ref()` / `*_unref()` pairs — `CObject` handles those for you.
- The logging-context accessors and `*_event_type_to_string()`.
- The NUL-terminated `*_device_text_utf8()` — the `_with_length` form is bound
  instead, so text containing a NUL is not truncated.
- `ei_new()` — superseded by `ei_new_sender()` / `ei_new_receiver()`.
- `*_clock_set_now_func()`.
- The `*_get_context()` accessors, which have nothing to hand back: a context
  is only ever created by its own `create_for_*()`, never wrapped from a raw
  pointer.

The 22 gesture/stylus accessor functions that libei's `main` branch adds are
also unbound. Nothing that ships today exports them, so nothing here could be
verified against a real library — which is the bar every other binding in this
package was held to.

## Design lineage

Written from scratch, taking its overall shape from
[snegg](https://gitlab.freedesktop.org/whot/snegg) (the reference bindings by
libei's own author), with different priorities suited to being embedded as a
dependency rather than used for prototyping. [`../vs-snegg.md`](../vs-snegg.md)
covers the specifics, including two signature issues found by cross-checking
against the real libei source.
