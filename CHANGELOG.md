# Changelog

All notable changes to python-libei are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html) — with the usual
0.x caveat that the API may still change between minor versions.

## [Unreleased]

## [0.6.1] - 2026-10-01

### Changed

- **The package metadata and README now say `libei.portal` captures input, and
  the README counts its modules right.** `InputCaptureSession` -- receiving the
  user's real pointer and keyboard through the InputCapture portal -- was in the
  module docstring and nowhere a reader looks: not the PyPI description, not the
  keywords, and not the README's table of what to use for what. The description
  now names it, `input-capture` and `eis` join the keywords, and the table has a
  row that says, as `docs/developers/verification.md` does, that only its
  negotiation half has been run against a real desktop. Also the `POSIX`
  operating-system classifier the other two packages carry, a `documentation` URL
  and a link to pyguitest, its main consumer. The README said "Five modules"
  where there are four (`ei`, `eis`, `oeffis`, `portal`; the table has five rows).
  `InputCaptureSession`'s docstring said "Never live-tested", which the
  verification log contradicts for its negotiation half; it now says half.

### Fixed

- **Using a released event raised `ctypes.ArgumentError`, not the documented
  `RuntimeError`.** Each event is released as the loop moves on, and the README
  and `docs/recipes.md` promise a `RuntimeError` if one is used afterwards. What
  a caller actually got, from a real call, was
  `ArgumentError: argument 1: RuntimeError: Event has already been released` --
  ctypes wraps anything raised while converting an argument and keeps only its
  text, so `except RuntimeError` missed it. The unit test passed because it read
  `_as_parameter_` directly and never crossed ctypes; found by probing the real
  library for the README's claims. `LazyLibrary` now re-raises the original
  `RuntimeError` when an argument is a released object, and an ordinary bad
  argument is still ctypes' own error.

- **Six bindings named no libei version, so an old library read as a mystery.**
  `ei_touch_cancel`, `ei_event_touch_get_is_cancel`, `ei_event_pong_get_ping` and
  their libeis counterparts arrived in libei 1.4, and
  `eis_backend_socket_get_client_pid` in 1.5; none carried the `# libei X.Y+`
  marker the other gated declarations do. Found by checking every declaration
  against the headers of 1.0.0, 1.2.1, 1.4.0, 1.5.0 and 1.6.0. Behaviour was
  already right -- the README's version table had them correctly -- so this is
  the source catching up with it.

- **The docs said forgetting `frame()` means nothing happens, silently. It is
  only true without `stop_emulating()`.** Measured against libei 1.2.1 and 1.6.0:
  a motion queued and then followed by `stop_emulating()` is delivered, in a
  `FRAME` libei adds itself, and logged as an error --
  `Bug: ei_device_stop_emulating: missing call to ei_device_frame()`. Only a
  queue that is never stopped is dropped with no log line at all. The README,
  getting-started and troubleshooting now say so. The same probing corrected
  two more claims: an event sent to a device lacking the capability is dropped
  with a `Bug: ... device is not a keyboard` error log rather than "silently",
  and the wrong-accessor read libei answers with zeros is likewise logged at
  error level rather than "a line the caller never sees". The README's list of
  versions the suite has been run against named 1.5.0, which
  `docs/developers/verification.md` has no record of, and contradicted its own
  Status section on CI's 1.2.1; both now agree.

- **The test suite ran nothing at all off Linux and FreeBSD.** `tests/test_loader.py`
  raised at module level when `ctypes.util.find_library("c")` found no C library,
  which pytest reports as a *collection* error -- so on Windows `pytest -q -rs`
  stopped before any of the other 200-odd tests, including every pure-Python one.
  The four loader tests that need a real library now skip there instead, and the
  three tests that build a keymap with `os.memfd_create` skip where it does not
  exist. On Windows the suite now reports 212 passed, 18 skipped; on Linux nothing
  changes -- 230 passed against libei 1.6.0.
- **The count of unbound gesture/stylus functions was stale.** The README and
  `docs/developers/architecture.md` said libei's `main` adds 22; counted against
  1.6.0's headers on 2026-09-30 it is 46 in libei and 51 in libeis, now that the
  stylus protocol has landed there too. Still none of it is in a release, so still
  none of it is bound.

### Tests

- **The bindings are checked against the real headers and libraries.**
  `tests/test_abi.py` compares every declared function's argument count and
  argument and return classes (pointer, 4- or 8-byte int, double, bool, void)
  with the upstream prototype, every Python enum member with its C enumerator
  value, and every declaration with the installed library's exports, where a
  missing one must carry a version marker. There are no `ctypes.Structure`
  bindings, so there is no layout to check. It needs the headers, via
  `LIBEI_SOURCE_DIR`, and otherwise skips; see CONTRIBUTING.md. Clean against
  the public headers of 1.0.0, 1.2.1, 1.4.0, 1.5.0 and 1.6.0, and against the
  installed 1.6.0 (Fedora 45) and 1.2.1 (Ubuntu 24.04) libraries.
  `LazyLibrary` now records what is declared on it (`declared`, `soname`) for this.
- **Every event accessor against every event type.**
  `tests/test_event_accessors.py` reads the accessor list from the source, so a
  new one is covered automatically, and checks that each raises `TypeError`
  before any C accessor is called for every wrong type, on both the client and
  server side, and that an accessor added without a guard fails. Previously one
  wrong type per library was pinned.
- **Forgetting `frame()` and using a released event, against the real library.**
  `tests/test_integration_frame.py` pins the four behaviours above and
  `tests/test_integration_lifetime.py` the released-event error; the loader tests
  gain missing-symbol behaviour (named, repeatable, the rest still working) and
  one-open-under-concurrent-first-calls.

## [0.6.0] - 2026-09-22

### Added

- **`LibraryNotFoundError` is now part of `libei.ei`'s, `libei.eis`'s and
  `libei.oeffis`'s public surface.** It has always been raised from all
  three -- `_capi/loader.py` raises it the moment a `dlopen` fails, or an
  installed library turns out too old to export a bound function -- and
  `docs/troubleshooting.md` had been telling readers to write
  `from libei.ei import Error, LibraryNotFoundError` since its exceptions
  table went in. It was in no module's `__all__`, alone among the eight
  classes that table names: catching `ei.LibraryNotFoundError` worked, but
  `from libei.ei import *`, `pydoc`, an IDE's completion, and anything else
  reading a module's declared surface did not see it, and the only
  obviously public place to import it from was the private
  `libei._capi.loader` it is defined in. Two of the three modules come from
  the table; `libei.oeffis` was found while fixing those, `Oeffis.create()`
  on a machine with no liboeffis being the same failure one module over.
  Nothing moved to the package root either way: `libei/__init__.py` still
  carries only `__version__`, as that page says.

- **`docs/troubleshooting.md` now names the exceptions, having been the page
  for everything that *doesn't* raise.** Eight classes are raised from real
  paths and were named in no user-facing text: `ei.Error` and `eis.Error` (22
  raise sites between them, two classes sharing no ancestor, carrying
  `.errno` where libei reported one), `portal.PortalError` (18 sites,
  the "session is closed" path among them) with its three subclasses, and
  `oeffis.DisconnectedError`/`SessionClosedError` from a session that ends
  under the caller. Only the three portal subclasses appeared anywhere, and
  only inside one sentence of `recipes.md`; the two `Error` classes and both
  oeffis ones appeared on no page at all — so the names a caller writes in an
  `except` clause were exactly the names the docs did not carry. The new
  section gives what each is raised for and what nests under what:
  `except PortalError` covers its three, `except DisconnectedError` covers
  `SessionClosedError`, and `ei.Error`/`eis.Error` cover nothing but
  themselves.

- **`docs/recipes.md` pairs every event type with the getter that reads it.**
  The page listed twelve getters in a sentence and never said which event each
  one belongs to — the pairing the `TypeError` beside them exists to enforce,
  and the reason `keyboard_xkb_modifiers` reads oddly, being the only getter
  not named after the class it unwraps. Two more were missing from the
  sentence entirely: `pong`, which is used two paragraphs below it, and
  `emulating_sequence`. All fourteen are now a table with the fields each
  returns and the libei version where one is needed, followed by the
  connection and lifecycle events, which have no getter and are read through
  `event_type`.

- **CI, PyPI and license badges on the README.** The three things a reader
  checks before installing anything -- whether the suite is green on `main`,
  what the current release is, and under what license -- each took a click
  through to somewhere else. The PyPI badge is also the one version statement
  in the repository that reads what is actually *published*: the prose lines
  that `tests/test_documentation_shape.py` guards are held to this checkout's
  `libei.__version__`, which says nothing about what `pip install` would
  fetch.

- **`.coderabbit.yaml`**, turning the Docstring Coverage pre-merge check off
  and telling the reviewer not to ask for docstrings under `tests/`. That
  check scores a whole diff against a single percentage threshold and its
  schema offers no per-path option, so it cannot see that
  `[tool.ruff.lint.per-file-ignores]` ignores `D100`-`D104` and `D107` under
  `tests/*` deliberately -- matching both sibling repos, on the grounds that
  each test is named for the behaviour it pins -- and so it objected on every
  PR that adds tests, which is most of them. Coverage of `src/` is untouched:
  ruff's pydocstyle rules enforce it in `scripts/pre-commit-test.sh`, and
  those *are* path-aware. The same exclusion is repeated as a
  `path_instructions` entry, which is where it can be path-scoped, so it
  holds whether or not the check is ever switched back on.

### Changed

- **The docstring-completeness check now runs everywhere, and covers
  `libei.portal`.** It lived in `tests/test_documented_examples.py`, whose
  module-level `integration` mark skips the file where the native libraries
  are absent -- so a check that reads source with `ast` and needs nothing
  installed was skipping on exactly the machines where it was cheapest to
  run. It now sits in `tests/test_documentation_shape.py`, beside the other
  checks that need no native library, takes in classes as well as functions,
  and includes `libei.portal`, which was never checked. Nothing had to be
  written to make it pass: every public definition in all four modules
  already carries a docstring, so this is a guard rather than a cleanup.

- **A new check ties the exceptions table to the modules it names.**
  `tests/test_documentation_shape.py` now reads the table in
  `docs/troubleshooting.md` and asserts that every class it pairs with a
  module is both reachable from that module and listed in its `__all__` --
  and that a class shared between modules is one class in all of them, which
  is what makes one `except` clause cover every module that raises it. This
  is the check that would have caught `LibraryNotFoundError` when the table
  itself was written.

- **The documentation guards reach the files that were drifting.**
  `tests/test_documentation_shape.py` compared the README's version line
  against `libei.__version__` but not the developers page's, which is how
  0.5.1 sat there through a release; both pages are compared now. The
  module-docstring example check covered the two modules that used `::` and
  read nothing at all from the two that did not -- both are covered now, all
  four public modules. A new check reads every docstring in those modules
  for the two defects below, neither of which is visible anywhere but in the
  rendered text: a role split across a line break, and an unpaired backtick.
  And `tests/test_portal.py` now asserts what `verification.md` claimed it
  covered -- that `CreateSession` carries a `session_handle_token`, whose
  absence crashes xdg-desktop-portal 1.22.1 outright.

- **39 docstrings on the dunders and private helpers that had none.** No check
  asked for them: ruff's pydocstyle rules treat an underscore-prefixed name as
  non-public, and this repo's config ignores `D105` outright -- so `__init__`,
  `__eq__`, `__hash__`, `__repr__`, `__del__`, `__enter__`/`__exit__`,
  `__init_subclass__`, `_cobject.CObject._get_or_create()`,
  `LazyLibrary._ensure_loaded()` and both `_log_callback`s went undocumented
  while being the definitions whose behaviour is least guessable from the
  signature: pointer adoption and the per-hierarchy identity cache, a failed
  `dlopen` cached rather than retried, a C function resolved on first call
  rather than at bind time. `help()` and an IDE's hover show all of them, and
  neither cares that a name starts with an underscore.

### Fixed

- **The README's link to the developer pages landed on a file listing.**
  `docs/developers/` holds `architecture.md` and `verification.md` and has no
  README of its own, so the last entry of the documentation index sent a
  reader to a directory listing rather than to anything written -- the same
  defect the sibling recorder repo fixed in its own README, found by holding
  this one to that fix. The entry now names both pages, and
  `tests/test_documentation_shape.py` requires a linked directory to *have* a
  README rather than merely to exist, which is the distinction `exists()`
  cannot draw.

- **Three public names were documented nowhere.** `eis.Flag`,
  `eis.ConfigureRegion` and `portal.Activation` are in their modules' `__all__`
  -- the declared surface, the thing `from libei.eis import *`, `pydoc` and an
  IDE's completion read -- and appeared on no page of the README or `docs/`:
  the only way to learn they existed was to open the source that defines them.
  Each is now named where its subject is described: `Flag` and
  `ConfigureRegion` beside `Eis.set_flag()` and `Device.configure()` in the
  README's tour of the modules, with the region argument written out in the
  own-EIS-server recipe, and `Activation` in the sentence saying what `portal`
  is smaller than `ei` by. The guard runs the direction the existing checks
  never did -- every one of them went from a page to the source, and none from
  the declared surface back to the pages -- so a public name that ships with
  prose nowhere is a failing test rather than a discovery.

- **`scripts/pre-commit-test.sh` failed its `tests` check on any interpreter
  without the package installed.** The script runs `python -m pytest -q -rs`
  with no `PYTHONPATH`, deliberately: CI's install step is one of the two
  things it leaves to the runner. But `[tool.pytest.ini_options]` here had
  only `testpaths`, so nothing put `src` on the path, and `tests/conftest.py`'s
  `from libei import ei` died at collection -- reporting an ImportError for
  the whole suite where pyguitest and pyguitest-recorder both set
  `pythonpath = ["src"]` and just run. The other four checks passed, which is
  what made it look like a test failure rather than a path one. Same setting
  added here; a bare checkout now runs the suite with nothing installed, which
  is also what makes a copied tree testable anywhere.

- **Four documentation defects, each invisible from the page it is on.**
  `docs/developers/verification.md` opened with "the package is beta
  (`0.5.1`)" a release after 0.5.2 shipped, and nothing compared that prose
  to `pyproject.toml` the way the README's own version line is compared. The
  same page and `docs/vs-snegg.md` both pointed a reader at "the README's
  Troubleshooting section", which has never existed -- the GNOME 44
  explanation they mean is in `docs/troubleshooting.md`, and both links now
  go there. And `InputCaptureSession`'s own docstring broke a role across a
  line, so `:meth:`wait_for_activation`` rendered as the literal text
  ":meth:" and a name rather than as a link. The README's module table also
  promised that *each* module carries an `Error` exception and an
  `EventType`/`DeviceCapability` enum, which holds for `ei` and `eis` only:
  `oeffis` and `portal` raise their own classes and carry `DeviceType`
  instead, so a reader writing `except oeffis.Error` was chasing a class
  that does not exist. All four are corrected.

- **The two module docstrings that carry a usage example now mark it as
  one.** `oeffis` and `portal` wrote their examples as plain indented
  blocks, where `ei` and `eis` use the `::` a reST literal block needs --
  which is also why the example check had never read either of them, since
  the extractor looks for that marker. The blocks themselves were already
  valid Python and unaffected.

## [0.5.2] - 2026-09-12

### Added

- **`docs/install.md`**, a page for the half `pip` cannot do: the native
  `libei`/`libeis`/`liboeffis` libraries, which distribution provides them
  under which name, and the optional `[portal]` extra. The same information
  was spread across two README sections, and `troubleshooting.md` carried a
  Fedora-only `dnf` line with nothing to say for anyone else. Package names
  are listed only for the families they have actually been checked against —
  Fedora, Debian/Ubuntu (both exercised by this repo's own CI) and FreeBSD —
  with the rest pointed at the name to search for rather than a guess, which
  is the standard `hints.py` in pyguitest holds itself to as well.

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

- **CI now runs `ruff format --check`**, which this repo had never asked for,
  though pyguitest and pyguitest-recorder both gate on it. The tree was
  already formatted, so this is a guard rather than a cleanup — and it is the
  one repo of the three where formatting could previously have drifted in
  unnoticed.

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
  developer to click through the consent dialog *and* accept that their
  pointer will be exclusively diverted from their own desktop for the
  length of the test, not something to trigger without asking first.
  Designed against the shipped portal spec
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
  including on `KeyboardInterrupt`: `Start` blocks on a user answering a
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
