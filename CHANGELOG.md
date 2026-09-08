# Changelog

All notable changes to python-libei are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html) — with the usual
0.x caveat that the API may still change between minor versions.

## [Unreleased]

## [0.5.0] - 2026-09-08

### Added

- **`libei.portal.InputCaptureSession`**, the read direction: receiving real
  input from the user's own devices via `org.freedesktop.portal.InputCapture`,
  rather than injecting synthetic input the way `RemoteDesktopSession` and
  every other class in this package does. Mirrors `RemoteDesktopSession`'s
  own architecture closely -- `negotiate()` (`CreateSession2` -> `Start` ->
  `ConnectToEIS`), the same `PersistMode`/restore-token round trip, the same
  ownership rules for `eis_fd` and `close()` -- and reuses its private
  Request/Response plumbing directly rather than duplicating it; `_request()`
  gained an optional `trailing_args` parameter for `SetPointerBarriers`, the
  one Request-returning method in either portal whose `options` argument is
  not last.

  Adds `zones()`, `set_pointer_barriers()`, `enable()`/`disable()`/
  `release()`, and `wait_for_activation()`/`wait_for_deactivation()` --  new
  plumbing this needed and `RemoteDesktopSession` did not: capturing is
  triggered by the compositor deciding a pointer barrier was crossed, not by
  a call this module makes, so waiting for `Activated`/`Deactivated` is an
  ordinary signal subscription rather than the request-that-returns-a-handle
  pattern every other method here uses.

  **Never run against a real portal**, unlike every other class in this
  module -- see the class's own docstring for why: verifying it needs a
  human to click through the consent dialog *and* accept that their pointer
  will be exclusively diverted from their own desktop for the length of the
  test, not something to trigger without asking first. Designed against the
  shipped portal spec
  (`/usr/share/dbus-1/interfaces/org.freedesktop.portal.InputCapture.xml`),
  not just the header, and unit-tested against a fake connection
  reproducing that spec's documented shapes -- see `tests/
  test_inputcapture.py`, including its own note on the one bug this caught:
  an early draft of one test omitted the "force PyGObject import to fail"
  patch its sibling in `test_portal.py` already used, which on a system
  where PyGObject really is installed reached the real session bus instead
  of a fake one and raised a real consent dialog. No pointer barriers had
  been set and `Enable()` was never reached, so nothing was actually
  captured -- but the test now forces the import failure explicitly, the
  way its sibling always did.

### Changed

- **The README no longer asks you to learn libei before you can use
  python-libei.** An external review put it exactly that way, and the shape of
  the file agreed: 812 lines, with the four-layer architecture, the release
  process and a full verification log sitting between a new reader and the
  code they needed. It is now 375 lines and leads with what a caller does.

  Added where they were missing: a **`frame()` callout in the opening lines**,
  since events queueing until a frame commits them is the one concept every
  user must hold and the commonest reason a first attempt appears to do
  nothing; and a **"Which API do I need?" table** — `ei.Sender` vs
  `ei.Receiver` vs `oeffis` vs `portal` vs `eis` — because the package exposes
  five modules and most callers need exactly two.

  **The `What's implemented` table is now two tables.** `GESTURES` and
  `STYLUS` are in no released libei, and a skimming reader could take a single
  table as saying otherwise. They now sit under their own heading that says
  binding them against a shipping library silently does nothing.

- **New `docs/`:** `getting-started.md` (install through a first real pointer
  motion), `recipes.md` (keyboards, touch, absolute positioning, consent
  persistence, receiver mode, EIS server, logging), `troubleshooting.md`, and
  an index. Troubleshooting is deliberately a **10-point "when nothing
  happens" checklist** rather than a list of error messages, because nearly
  every failure mode here — a missing `frame()`, emulating before
  `DEVICE_RESUMED`, an event the device lacks the capability for — is silent
  by design.

- **New `CONTRIBUTING.md`**, holding the setup, checks, old-libei
  reproduction and release process that were living in the README, matching
  the convention of the sibling projects. The architecture explanation moved
  to `docs/developers/architecture.md` and the per-path verification log to
  `docs/developers/verification.md`, with a short trust summary left in the
  README's Status section.

## [0.4.1] - 2026-09-05

### Fixed

- The test suite's own portability: `tests/test_loader.py` used
  `libc.so.6` as its stand-in "always available" shared library -- correct
  on glibc, wrong everywhere else. Confirmed on FreeBSD, whose libc is
  `libc.so.7`, where it made four otherwise-unrelated tests fail for a
  reason that had nothing to do with `LazyLibrary`, which was behaving
  correctly the whole time -- it was accurately reporting that a soname
  which doesn't exist on that platform isn't available. Resolved with
  `ctypes.util.find_library("c")` instead of a hardcoded soname.

- `[tool.mypy]`'s defaults broke on FreeBSD in two independent ways, only
  visible once `mypy` actually ran there rather than just installed.
  `sqlite_cache` (mypy's own default is on) imports `sqlite3`
  unconditionally, which crashes rather than falls back on a Python built
  without `_sqlite3` -- GhostBSD's python3.11 port is one such build. And
  once that was cleared, `os.memfd_create` failed type-checking on FreeBSD
  even though it works correctly there at runtime (confirmed directly:
  `hasattr` is true, and it round-trips real data) -- typeshed's stub for
  it is still gated to `sys.platform == "linux"`, and mypy's `--platform`
  defaults to whatever OS invokes it. `sqlite_cache = false` and
  `platform = "linux"` fix both; the second pins every run to check
  against Linux's stubs regardless of the contributor's own OS, so results
  stop depending on where `mypy` happens to execute.

## [0.4.0] - 2026-09-03

### Changed

- Development status is now Beta rather than Alpha. The API is still not
  frozen — expect renames before 1.0 — but nothing in the public surface
  has moved since 0.2.0, the injection path is exercised end to end against
  the real libraries on every CI run, and the one module that cannot be
  covered that way, `libei.portal`, has now been both hand-verified against
  a real GNOME session and hardened against the failure paths hand
  verification never reaches (see below).

### Fixed

- `libei.portal`: a negotiation that failed after `CreateSession` left the
  portal session it had just created open. Nothing could close it: no
  `RemoteDesktopSession` exists to own it until every step has succeeded,
  and the D-Bus connection it was created on is GLib's *shared*
  session-bus singleton, which outlives the failure rather than dropping
  the session with it — so a process that retried after a declined,
  timed-out or interrupted consent dialog accumulated live sessions inside
  xdg-desktop-portal. `negotiate()` now closes the session on the way out,
  including on `KeyboardInterrupt`: `Start` blocks on a human answering a
  dialog, so Ctrl-C during that wait is a routine exit and strands an
  approved session exactly as a decline does.

- `libei.portal`: the caller's `timeout` now bounds the D-Bus call that
  starts each round trip, not just the wait for the `Response` signal that
  answers it. Those calls were left on GDBus's `-1`, which is not "no
  timeout" (that is `G_MAXINT`) but GIO's own 25-second default — so the
  bound on a round trip was a number this module never chose and a caller
  could not see, and `ConnectToEIS`, which returns no `Request` at all, was
  bounded by nothing else. Both legs now draw on one deadline, so a slow
  first leg cannot double the wait a caller asked for, and GDBus's own
  reply timeout (`G_IO_ERROR_TIMED_OUT`, which is what it raises rather
  than an `org.freedesktop.DBus.Error.*` code) raises `PortalTimeoutError`
  like any other round trip that runs out of time, instead of a generic
  `PortalError`.

- `libei.portal`: `RemoteDesktopSession.close()` sent `Session.Close()` to
  the default portal bus name even when `negotiate(busname=...)` had used
  another one, so such a session was never actually closed. The session now
  remembers the name it was negotiated on, which
  `RemoteDesktopSession.__init__` takes as a new optional `busname`
  argument defaulting to the standard portal name — the one API addition
  in this release, and why it is a minor rather than a patch.

- `libei.portal`: a `CreateSession` that answered "approved" with no
  `session_handle` raised `KeyError` straight past a caller's
  `except PortalError`; it now raises `PortalError` like every other
  malformed reply.

## [0.3.0] - 2026-08-31

### Added

- `libei.portal`: negotiate `org.freedesktop.portal.RemoteDesktop` directly
  over D-Bus (via PyGObject, the new optional `portal` extra) instead of
  through `libei.oeffis`/liboeffis, exposing `persist_mode`/`restore_token`
  support that liboeffis's C API doesn't have —
  `oeffis_create_session()` takes only a device-type bitmask. Upstream's own
  docs say as much: liboeffis is "intentionally kept simple, any more
  complex needs should be handled by an application talking to DBus
  directly." `RemoteDesktopSession.negotiate()` is a synchronous port of a
  request/response sequence (including two hard-won fixes: a
  subscribe-before-call race, and a `session_handle_token` crash workaround
  for xdg-desktop-portal 1.22.1) that was already live-verified as the
  Wayland input backend of a separate GUI-automation project — this is that
  logic upstreamed into the library itself, so other consumers don't have to
  reimplement it. See the README's "Avoiding the consent dialog on every
  run".

  `RemoteDesktopSession` is a context manager and has an explicit `close()`,
  which ends the portal session (`Session.Close()`) and closes the EIS fd if
  it was never claimed. Both matter: `Gio.bus_get_sync()` returns GLib's
  *shared* connection, so dropping the object tears nothing down, and the fd
  arrives dup'd and owned by the receiver. Each portal round trip is bounded
  by a `timeout` (60s default, `PortalTimeoutError` on expiry) so a portal
  that accepts a call and then dies cannot wedge the caller forever. GDBus
  failures — no session bus, no portal implementation — are wrapped in
  `PortalError` rather than escaping as raw `GLib.Error`. Passing
  `restore_token` without a `persist_mode` raises `ValueError`, since the
  portal answers that combination with no token at all and a caller storing
  what came back would write `None` over the one it just spent. And
  `DeviceType.ALL_DEVICES`, which is liboeffis's sentinel and literally `0`,
  is translated to every device type the portal defines — sent raw it would
  mean *no* device types, yielding a session on which no device ever
  appears.

  Two further details the portal makes you get right: the `Response` is
  awaited on the handle the call actually returned as well as on the path
  derived from our own `handle_token`, because the spec says those match
  but a portal is free to hand back something else — watching only the
  derived path means such a reply is never seen. And the main loop only
  runs when no reply is already in hand: `quit()` on a loop that is not
  running yet does not stop a later `run()`, so a Response delivered
  synchronously during the call would otherwise block forever on a result
  already collected.

## [0.2.0]

No changelog entry recorded before this file was created; see git history
and PyPI release notes.

## [0.1.0]

Initial release.
