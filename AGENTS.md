# Agent instructions

*Regarding AI, adopt or get left behind...*

Working conventions for this repo, learned from the maintainer (Dennis K.
Paulsden) over prior sessions. This is about *how* to work here, not *what
the code does* — read the source and `docs/` for that.

## Resolve the issue; a note about it is not a resolution

**The default response to a defect is to fix its cause.** Not to describe
it, not to route around it, not to pin it as a known gap. If you find
yourself reaching for prose where a fix belongs, that is the signal to stop
and go fix it.

None of these count as resolving anything: a paragraph in `CHANGELOG.md` or
a docstring saying the thing is broken; a comment explaining the symptom; a
retry loop, a widened timeout or tolerance; an `except` that turns a failure
into a pass; a skipped or loosened test, or an assertion weakened to match
what the code does today.

**Two shapes this takes here.** A test that fails only against the libei
version CI pins is a *compatibility* bug in this binding — fix the binding,
or guard on the capability rather than on the version, and never skip the
test to make the run green; the older version is as real as the newer one,
and reproducing it locally needs no box (see the extracted-debs recipe). And
a fix must not rest on an unverified premise about what the C library does:
check the behaviour — a `GError` that is or is not set, an fd list that is
or is not empty — against the library itself before building a guard on top
of your reading of it. A guard for a condition that never occurs is worse
than none: it looks like the bug is handled.

**Phrases to treat as alarms in your own output.** "Left open and
characterised", "reported rather than changed", "recorded here rather than
fixed", "awaits a future round", "documented upstream", "out of scope for
this change", "pre-existing, so not mine". When one appears, either fix the
thing or ask.

**Diagnose to the cause, not the symptom.** Keep going until you can say
*why*, then fix that. A nondeterministic result is a race to find, not a
flake to retry around.

**The only acceptable reasons to leave something unfixed**, and both end in
asking rather than writing: the fix is the maintainer's call, because it
changes this binding's public API or how it presents a protocol object; or
you are genuinely blocked — the cause is upstream in libei or in a
compositor, and then the resolution is a reduced reproducer and a report
there, not a note here.

**Documentation's job is to record a resolved thing** — the measurement
that proves the fix.

## Git workflow

- **AI will never commit.** Never run `git commit` (or `git add` in
  service of one), no exceptions. The user commits their own work, always.
  Make changes, verify them, and leave them in the working tree.
- **Never work on `main`.** Every change goes on a feature branch named
  `P<priority>-<short-kebab-description>` (e.g. `P2-fix-inputcapture-
  signal-path`). If no priority number was given, ask for one before
  starting.
- **Any mention of "commit" from the user — "commit this," "one line
  commit for each," anything — means produce the message text for them to
  use, never run the command.** One line, Conventional Commits style
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, …), no body.
- **A multi-repo or multi-file plan approved once is not standing
  approval for the rest of it.** Do one repo (or one file, for something
  like a README pass), stop, report what changed, and ask before doing the
  next. This applies doubly to editing `README.md` specifically — ask
  first even within one approved plan.

## Verifying a change

CI (`.github/workflows/ci.yml`) is the reference for what "green" means
here:

```sh
ruff check src tests
python -m mypy
python -m pytest -q -rs
```

Fix lint/format/type issues right the first time rather than looping
edit-check-edit-check; run the full gate once near the end of a change, not
after every edit.

## Docs and changelog

`CHANGELOG.md`'s `[Unreleased]` section gets an entry for any user-facing
fix or feature, in the same voice as the existing entries (a bold one-line
summary, then the story: what broke, how it was found, what changed).

## This repo's place in the family

python-libei is pyguitest's libei/eiinput dependency — a bug found while
working in pyguitest or pyguitest-recorder that traces back to input
injection or portal negotiation may actually belong here. Check whether a
fix on this side has a consequence for either sibling repo (a floor version
bump, a matching fix) before considering the work finished.
