# Agent instructions

*Regarding AI, adopt or get left behind...*

Working conventions for this repo, learned from the maintainer (Dennis K.
Paulsden) over prior sessions. This is about *how* to work here, not *what
the code does* — read the source and `docs/` for that.

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
