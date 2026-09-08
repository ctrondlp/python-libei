# Troubleshooting

Nearly every failure here is silent. libei's design is to drop what it cannot
deliver rather than raise, so "nothing happened" is the symptom for a dozen
different causes — which is why this page is a checklist rather than a list of
error messages.

## When nothing happens: the checklist

Work down it in order. The first three account for most cases.

1. **Did you call `frame()`?** Events queue until a frame commits them.
   Without one, nothing is ever sent. No exception, no warning.

2. **Did you wait for `DEVICE_RESUMED`, not `DEVICE_ADDED`?** A device arrives
   paused. libei calls sending events before it resumes "a client bug", and
   handles it by discarding them.

3. **Did you call `dispatch()` before reading `events`?** `events` drains only
   what is already queued; it yields nothing until `dispatch()` has read from
   the socket. A loop that reads `events` without dispatching spins forever on
   an empty list.

4. **Does the device actually have the capability you are using?** Sending an
   event a device lacks the capability for is *silently ignored*. This is the
   one that looks most like a broken library. Check
   `device.capabilities`.

5. **Did you take the wrong device?** A seat can resume several. On GNOME,
   binding both `POINTER` and `POINTER_ABSOLUTE` gives you two devices,
   relative first — so "first resumed wins" hands you the relative one, and
   `pointer_motion_absolute()` on it does nothing at all. Select on
   `device.capabilities` instead;
   [recipes.md](recipes.md#picking-the-right-device-when-several-resume) has
   the loop.

6. **Does the seat offer the capability you bound?** Capabilities are
   per-seat, and a seat only offers some. Check `seat.capabilities` before
   binding. If the loop hangs waiting for a device, this is usually why.

7. **Did you bind an empty set?** Binding no capabilities sends nothing, so
   the device you are waiting for never arrives. This one *does* raise —
   `ValueError` — rather than hanging.

8. **Are you binding `GESTURES` or `STYLUS`?** They are in no released libei.
   The values here match upstream `main` and are ready for whatever release
   adds them, but 1.6.0's capability enum stops at `TEXT`. Binding them
   against a shipping library silently does nothing — no error, no device, no
   events.

9. **Is your libei new enough for the call?** Nothing is resolved until it is
   called, so a build without a symbol costs you only that call: it raises
   `LibraryNotFoundError` naming the missing symbol. `Region.mapping_id` needs
   1.1, ping/pong and touch cancellation 1.4, `eis.Client.pid` 1.5, and
   `text_utf8()`/`request_device()`/`set_flag()` 1.6.

10. **Turn on logging and look.** This is how you see libei's own complaints,
    which are otherwise invisible:

    ```python
    import logging
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("libei").setLevel(logging.DEBUG)
    ```

    `WARNING` gets you just the complaints (`device is not an absolute
    pointer` among them); `DEBUG` is a full protocol trace, a few hundred
    lines for one connect and one motion, and it is the right tool when a
    negotiation stalls rather than fails.

## `LibraryNotFoundError`

The native library is not installed, or is too old to export a function this
package binds. Check with `ei.is_available()`; install with
`sudo dnf install libei libeis liboeffis` on Fedora.

Because loading is lazy, this is raised at the first *call*, not at import —
so an application can import this package, discover the libraries are absent,
and fall back to something else without ever handling an ImportError.

## The portal dialog appears, I approve it, and nothing happens

Earlier testing on GNOME 44 saw the round trip hang indefinitely even after
clicking through the dialog, and it went unroot-caused for a while.

Revisited 2026-08-25 on GNOME 50.4 with a `busctl monitor` trace on the real
portal: `CreateSession → SelectDevices → Start → ConnectToEIS` completed
cleanly in a few seconds, 3/3 consecutive attempts, with `Start()`'s
`Response` signal arriving only after a multi-second gap consistent with a
real dialog being answered. The code was correctly waiting the whole time —
`dispatch()` returning `False` just means no `Response` has arrived yet.

The likely explanation for the earlier hangs: `RemoteDesktop.Start()` can
involve more than one prompt (an access-request dialog, then a device-sharing
confirmation), and dismissing or missing one leaves `Start()` never returning
— indistinguishable from a hang on the caller's side. Not independently
confirmed by watching the dialogs themselves, only inferred from this trace
plus which step it stalled at previously. **If you hit this, check whether a
second prompt is waiting before assuming it is this library.**

`libei.eis` remains the right fallback for anything that does not need the
portal at all, tests especially.

## The consent dialog appears on every single run

Expected with `libei.oeffis`, which cannot do otherwise. Use `libei.portal`
with `persist_mode` and a saved `restore_token` —
[recipes.md](recipes.md#avoiding-the-consent-dialog-on-every-run).

## `TypeError` reading an event accessor

You read the accessor for a different event type than the one you have —
`event.key_event` on a `POINTER_MOTION` event, say. The message names both
types.

This is deliberate. libei itself would have returned `KeyEvent(key=0,
is_press=False)` — a real-looking value — while logging a `Bug:` line the
caller never sees. Branch on `event_type` first. Note `TOUCH_UP` has its own
`touch_up_event`, since it carries no coordinates.

## `RuntimeError` using an event

You kept an event past its loop iteration. Each one is released as soon as the
loop moves on. Objects pulled *off* an event (`event.device`, `event.seat`)
are safe to keep — copy out `event.pointer_event` and friends rather than the
event itself.

## The typed characters are wrong

`keyboard_key()` takes a key *position*, not a character. `KEY_A = 30` is "the
key where A sits on US QWERTY", and under another layout the compositor turns
that into something else — this package does no keysym mapping.

Either resolve characters through the keymap the compositor handed you, or use
`text_utf8()` on libei 1.6, which hands the compositor characters and lets it
work out the keys. Both routes are in
[recipes.md](recipes.md#keyboard-positions-not-characters).

## A long-running process accumulates portal sessions

The portal session lives in xdg-desktop-portal and outlives the Python object
unless `Session.Close()` is called. `Gio.bus_get_sync()` hands back GLib's
*shared* connection, so dropping the session tears nothing down.

Use `libei.portal.RemoteDesktopSession.negotiate()` as a context manager, or
call `session.close()` yourself.
