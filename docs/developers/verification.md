# What has actually been verified

The package is beta (`0.4.1`) and the API is not frozen — expect renames
before 1.0. This page is what that qualifier covers, concretely: which paths
have been driven against real libraries, which have only ever been driven
against fakes, and which libei versions the whole thing has met.

## Driven end to end against the real libraries

- **The injection path** — connect, bind, wait for a device, send events — is
  exercised by `tests/test_integration_socketpair.py`, and is in use as the
  Wayland input backend of a separate GUI-automation project.
- **Text input, touch cancellation, ping/pong, keymap transfer, region mapping
  ids and `peek_event_type()`** are each round-tripped through a real libeis
  server in `tests/test_integration_extras.py`.

## The portal paths, which can only be verified by hand

Both `libei.oeffis` and `libei.portal` need an interactive consent dialog that
nothing here can drive automatically, so their automated coverage stops at a
fake D-Bus connection.

`tests/test_portal.py` covers `libei.portal`'s orchestration against that
fake: raceless subscribe-before-call, the `session_handle_token` crash
workaround, `persist_mode`/`restore_token`, and closing the portal session on
a negotiation that fails part-way.

By hand:

- **`libei.oeffis`**, 2026-08-25. See the README's troubleshooting section for
  what that run established about the GNOME 44 hangs.
- **`libei.portal`**, 2026-09-01, against a real GNOME Wayland session
  (`RemoteDesktop` v2). A first run raised the consent dialog and was approved
  (5.4s); a second replaying the `restore_token` was granted with no dialog at
  all (0.2s); three devices resumed on the returned fd — relative pointer,
  keyboard, absolute pointer, in that order, which is the device race
  `ei`-side callers must handle; and `Session.Close()` was exercised. No input
  was injected — emulation is `libei.ei`'s job.

## Which libei versions this has met

- Verified against **libei 1.6.0** on Fedora 44 / GNOME 50.4.
- Verified against a locally built **1.2.1** — 130 passed, 4 skipped, the 1.4
  and 1.6 features gating themselves out.
- CI repeats the 1.2.1 run on Python 3.10–3.13, so the 1.0.0 core floor is
  exercised on a real old build rather than asserted.
- **1.0–1.1 and 1.3 have never been run against.**

A separate CI job installs the package with no native libraries at all and
imports it, which is the property the lazy loader exists to provide.

## How the version floors were established

By building libei 1.2.1 and diffing its exported symbols against every binding
here — **not** by reading `@since` annotations. Three of those are missing
upstream, and taking their absence to mean 1.0 got touch cancellation wrong by
four releases.

That is why the per-feature table in the README's requirements section can be
trusted: each row is a symbol that was observed present or absent in a real
build, not a documentation claim.
