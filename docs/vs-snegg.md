# Relative to snegg

[snegg](https://gitlab.freedesktop.org/whot/snegg) is the reference Python
binding for libei, written by libei's own author (Peter Hutterer), and it's
where this project's overall shape comes from: the module split
(`ei`/`eis`/`oeffis`) and general approach (thin ctypes wrapper, refcounted
Python objects mirroring the C objects) were both adopted from it. This
project isn't a fork -- the code here was written from scratch, with a
different set of priorities (see below) -- but the design debt to snegg is
real and worth naming.

snegg itself describes its own goal as "rapid prototyping" and its API as
explicitly not stable, so the gaps noted here are less "bugs in a finished
library" and more differences in what each project set out to be.

## Two issues found while cross-checking against libei

Found by driving snegg against a real, installed `libei` 1.5.0 on Fedora 44
and cross-checking bound signatures against the upstream libei source
headers (`src/libei.h`, `src/libeis.h`). Recorded here because they're the
concrete reason this project's fd-backend path and `_as_parameter_`
mechanism ended up shaped the way they did.

1. **The fd-backend path.** `eis_backend_fd_add_client()` takes only the
   `struct eis *` context and *returns* a new fd for one client connection
   -- it doesn't take one. snegg's `add_client_fd()` wrapper is written
   around the opposite assumption (takes an `fd: IO` argument, and doesn't
   use the function's return value). Here, `Eis.create_for_fd()` takes no
   fd argument and `Eis.add_client()` returns the new fd, matching the
   real API -- exercised end-to-end in `tests/test_integration_socketpair.py`.

2. **Pointer unwrapping at call sites.** snegg's `CObjectWrapper` has no
   `_as_parameter_` defined, so a ctypes call site needs to remember to
   pass `obj._cobject` rather than `obj` itself -- easy to miss, and
   nothing catches it if you do. This project's `CObject` defines
   `_as_parameter_`, so a wrapper instance can be passed directly to any
   bound function expecting the underlying pointer, and that class of
   mistake isn't available to make.

## Other differences (design choices, not corrections)

- **Lazy library loading.** snegg's `clibwrapper` class decorator calls
  `ctypes.CDLL(soname)` at class-decoration time -- i.e. at import. `import
  snegg.ei` requires libei to already be installed, even for code that only
  wants an enum value. Here, `_capi/loader.py` defers the `dlopen` to first
  actual use, so importing this package is always safe; `ei.is_available()`
  / `eis.is_available()` / `oeffis.is_available()` let callers check before
  depending on it. This follows from a specific goal for this project --
  being safe to import as an optional backend in a larger tool -- that
  snegg, built for prototyping against a known environment, didn't need.

- **Hand-declared ctypes signatures, not a regex C-declaration parser.**
  snegg parses C declaration strings (`"int ei_foo(struct ei *ei, int
  x);"`) into ctypes argtypes/restype at runtime via a small regex-based
  parser (`c/__init__.py`) -- a neat trick that keeps declarations
  readable as plain C. This project spells out `(c_void_p, c_int)` tuples
  explicitly instead, on the theory that a wrong argument count or type is
  then a visible diff rather than something to notice by running it. Every
  signature in `_capi/libei.py`, `_capi/libeis.py` and `_capi/liboeffis.py`
  here was cross-checked against the real libei source headers before
  being trusted.

- **Test coverage.** snegg ships one test file covering socket
  connect/disconnect -- reasonable for a prototyping tool. This project
  splits tests into fast unit tests (mocked, run everywhere) and a real
  integration suite (`pytest -m integration`, auto-skipped if libei isn't
  installed) that drives a full seat/device negotiation and a
  pointer-motion round-trip against the actual library, since being
  embedded as a dependency in other tools calls for a higher coverage bar
  than a prototyping aid does.

- **Deliberately partial C-API coverage.** 250 of the 302 functions the
  three libraries export (1.6.0) are bound here -- snegg's own coverage
  hasn't been counted, so this is a statement about this project, not a
  comparison. What is left out is redundant under a Python wrapper:
  `*_get_user_data()`/`*_set_user_data()` (state lives on the wrapper
  object), the `*_ref()`/`*_unref()` pairs `CObject` owns, the
  logging-context accessors, `*_event_type_to_string()`, the
  NUL-terminated `*_device_text_utf8()` (the `_with_length` form is bound
  instead, so a str containing a NUL isn't silently truncated), and the
  `*_get_context()` accessors, which have nothing to return: a context is
  only ever created by its own `create_for_*()`, never wrapped from a raw
  pointer. The README's "What's implemented" section says the same in
  user-facing terms, so a caller finds out from the docs rather than from
  an `AttributeError`.

- **Accessors check the event type.** libei's data accessors don't report a
  mismatch to the caller: `ei_event_keyboard_get_key()` on a pointer event
  returns 0, logging an internal "Bug:" line for some accessors and nothing
  at all for others. `Event.key_event` and friends raise `TypeError` naming
  both types instead, which is why `TOUCH_UP` grew its own `touch_up_event`
  accessor -- it carries no coordinates, so sharing `touch_event` would have
  meant reading two meaningless zeros.

## What's unverified

The portal (`oeffis`) path has no automated live-integration test in either
project -- it needs an interactive consent dialog, which nothing here drives
programmatically. It was manually verified working on 2026-08-25 on GNOME
50.4 (`busctl monitor` trace: `CreateSession -> SelectDevices -> Start ->
ConnectToEIS`, 3/3 attempts, a few seconds each), after an earlier attempt on
GNOME 44 saw it hang. See the README's Troubleshooting section for the likely
explanation. Manual verification isn't the same guarantee automated coverage
would be, so treat `oeffis` as the least *automatically* verified part of
this package -- see the note in `tests/test_oeffis.py`.

## Two things found while building the integration test

Both found by actually running against real, installed `libei`/`libeis`
1.5.0 on Fedora 44 -- not from reading headers, and both are gaps in this
project's own first draft rather than anything inherited from snegg.

1. **`EventType` needs to be complete, not just "the events a basic
   pointer/keyboard client cares about."** The first version of this
   package's `ei.EventType` / `eis.EventType` omitted `PONG = 90` and
   `SYNC = 91`. A live GNOME/Mutter session sends an unsolicited `SYNC`
   event during ordinary connection setup -- nothing in this package's
   code calls `ei_ping()` to request it -- which crashed `Event.event_type`
   with `ValueError: 91 is not a valid EventType` on the very first real
   end-to-end test run. libei's own header comment on the enum says
   outright that it "is not exhaustive, future versions of this library
   may add new event types" and that unknown events must still be released
   with `ei_event_unref()`. Both enums here were rebuilt to cover every
   type upstream defines -- including the text types, and the
   gesture/stylus ones that exist on libei's `main` branch but in no
   released version (1.6.0's `enum ei_event_type` stops at
   `EI_EVENT_TEXT_UTF8`; the values here match `main` exactly, checked
   2026-08-27). Data getters exist for the text types only, since nothing
   released exports the gesture/stylus accessors to verify against. And
   `Event.event_type` now returns a plain `int` instead of raising for any
   value still outside the table, honoring that documented contract
   instead of assuming the table is complete.

2. **Dropping the last Python reference to a `Sender` does not reliably
   close its fd.** `ei_setup_backend_fd()`'s doc comment says the function
   "takes ownership of the file descriptor, and will close it when tearing
   down." In practice: dropping the last reference (via `CObject`'s
   weakref-finalizer, which calls `ei_unref()`) and checking with
   `os.fstat()` immediately after showed the fd still open, and it stayed
   open even after 15s of unconditionally polling `server.dispatch()` on
   the EIS side. This looks like an internal libei refcounting detail --
   plausibly an extra reference the context holds on itself via its own
   event-loop registration, which a single external `ei_unref()` doesn't
   release -- rather than a bug in this package's bindings.
   `test_integration_socketpair.py` only asserts the connect/negotiate/
   inject path, which is fully verified working; it does not assert
   disconnect-via-GC.
