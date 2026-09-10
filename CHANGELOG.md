# Changelog

All notable changes to python-libei are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html) — with the usual
0.x caveat that the API may still change between minor versions.

## [Unreleased]

### Changed

- **Ruff now enforces pydocstyle plus the same complexity/simplification/
  argument rules pyguitest and pyguitest-recorder already gate on** (`D`,
  `C4`, `SIM`, `RET`, `ARG`, `C901`, ceiling 15) -- this repo's config had
  never turned them on. Closing the gap surfaced two real things: eight
  `try`/`except OSError: pass` blocks (tests plus two `__del__` methods in
  `portal.py`) rewritten as `contextlib.suppress(OSError)`, and ~40 missing
  docstrings, mostly one-line additions to the per-event dataclasses in
  `ei.py`/`eis.py` naming the numbering scheme a field uses (e.g.
  `KeyEvent.key` is a Linux `KEY_*` code, matching `Device.keyboard_key()`)
  since that wasn't stated on the class itself.

### Fixed

- **`InputCaptureSession.wait_for_activation()`/`wait_for_deactivation()`
  now actually receive their signals.** Both subscribed to `Activated`/
  `Deactivated` with the *session handle* as the D-Bus object path, but the
  portal emits these on its own object (`/org/freedesktop/portal/desktop`),
  identifying the session by the signal's first argument instead. The
  subscription therefore matched nothing and the wait always ran to its
  timeout -- indistinguishable, from the caller's side, from a compositor
  that never activates capture, which is exactly how it was misdiagnosed:
  as a Mutter bug, across two GNOME versions, a standalone C reproducer and
  an upstream bug report, until an xdg-desktop-portal developer pointed out
  the mistake. Now subscribes on the portal object and filters on the
  payload's session handle, so a second concurrent session's signals are
  ignored rather than answered.

- **`_wait_for_signal` (used by `InputCaptureSession.wait_for_activation`/
  `wait_for_deactivation`) no longer double-removes its own GLib timeout
  source on timeout**, which logged a real GLib warning ("Source ID N was
  not found when attempting to remove it") on every timed-out wait.
  `GLib.timeout_add`'s callback returns `False`, which already deregisters
  the source; the cleanup `finally` block called `GLib.source_remove` on it
  again unconditionally. Caught live on the GNOME 50 box (this session's
  first real timeout run against a genuine GLib main loop) -- the unit
  tests' fake `GLib.source_remove` doesn't reproduce the warning, so it was
  invisible to the suite.

- **`_request` had the identical double-remove bug as its sibling above,
  never fixed alongside it.** `_request` backs every Request-returning
  RemoteDesktop/InputCapture call (`CreateSession`, `SelectDevices`,
  `Start`, `GetZones`, `SetPointerBarriers`) -- far more heavily used than
  `_wait_for_signal` -- so this covers the single most realistic timeout
  scenario in the module: a consent dialog nobody answers. Found by a
  self-review sweep after the `_wait_for_signal` fix, not live; fixed the
  same way, with a test mirroring `test_inputcapture.py`'s existing one for
  `_wait_for_signal`.

## [0.5.1] - 2026-09-08

### Fixed

- **`InputCaptureSession.negotiate()` now works against pre-v2 portals**, via
  the deprecated v1 `CreateSession`. Only `CreateSession2` and `Start` are
  version 2 additions to the interface -- `GetZones`, `SetPointerBarriers`,
  `Enable`, `Disable`, `Release` and `ConnectToEIS` are all v1 originals -- so
  just session creation forks and everything after negotiating is unchanged.
  On v1 a single `CreateSession` carries `capabilities` and raises the consent
  dialog itself; there is no `Start` to call.

  This turns out to matter on shipping systems, not just old ones:
  **xdg-desktop-portal-gnome 50 reports InputCapture version 0** -- it
  registers the complete impl interface (including `ConnectToEIS`, the
  `Activated`/`Deactivated` signals and `SupportedCapabilities = 15`) and
  simply never sets the `version` property, and the frontend derives its own
  version from the impl's. Previously that raised
  `PortalVersionError: InputCapture version 0 is too old for CreateSession2`
  and there was no way through. Live-validated on Fedora 44 / GNOME Shell 50.0
  (xdg-desktop-portal 1.21.1): negotiation returns a real EIS fd and `zones()`
  answers `(0, [(1920, 1080, 0, 0)])`.

  Note that such a portal's introspection XML advertises `CreateSession2`
  anyway -- that XML is static and says nothing about what the frontend will
  dispatch, which is why the `version` property is read first and believed.
  Calling `CreateSession2` there fails with `UnknownMethod`.

  The one thing v1 cannot do is persist: `persist_mode` and `restore_token`
  were added to `Start`, which does not exist. Passing either against a pre-v2
  portal now raises `PortalVersionError` explaining that, rather than being
  silently ignored and handing back a `restore_token` of `None`.

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
