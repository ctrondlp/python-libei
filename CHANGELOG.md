# Changelog

All notable changes to python-libei are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html) — with the usual
0.x caveat that the API may still change between minor versions.

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
