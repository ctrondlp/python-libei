# Changelog

All notable changes to python-libei are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html) — with the usual
0.x caveat that the API may still change between minor versions.

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
