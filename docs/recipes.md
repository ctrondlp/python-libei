# Recipes

Task-shaped answers. For a first program see
[getting-started.md](getting-started.md); when something silently does nothing,
[troubleshooting.md](troubleshooting.md).

- [Avoiding the consent dialog on every run](#avoiding-the-consent-dialog-on-every-run)
- [Pointer, buttons and scrolling](#pointer-buttons-and-scrolling)
- [Absolute positioning](#absolute-positioning)
- [Picking the right device when several resume](#picking-the-right-device-when-several-resume)
- [Keyboard: positions, not characters](#keyboard-positions-not-characters)
- [Typing text directly, on libei 1.6](#typing-text-directly-on-libei-16)
- [Touch](#touch)
- [Reading input instead of sending it](#reading-input-instead-of-sending-it)
- [Keeping a connection alive, and closing it](#keeping-a-connection-alive-and-closing-it)
- [Running your own EIS server](#running-your-own-eis-server)
- [Logging](#logging)

## Avoiding the consent dialog on every run

**`libei.oeffis` cannot do it.** liboeffis wraps the portal handshake into one
call and exposes no options dict, so the two things that make an approval
persist — `persist_mode` on `SelectDevices`, and the `restore_token` that
comes back on `Start` — are unreachable through it. This is a limitation of
the C library, not of these bindings; upstream's own docs say liboeffis is
"intentionally kept simple, any more complex needs should be handled by an
application talking to DBus directly"
([source](https://libinput.pages.freedesktop.org/libei/api/group__liboeffis.html)).

`libei.portal` is that: the same `CreateSession` → `SelectDevices` → `Start` →
`ConnectToEIS` sequence, driven directly over D-Bus (needs PyGObject —
`pip install 'python-libei[portal]'`), with those two as real parameters:

```python
from libei import ei, portal

with portal.RemoteDesktopSession.negotiate(
    devices=portal.DeviceType.POINTER,
    persist_mode=portal.PersistMode.UNTIL_REVOKED,
    restore_token=saved_token,  # None on the first run
) as session:
    save_somewhere(session.restore_token)  # a fresh token every time -- save it
    sender = ei.Sender.create_for_fd(session.eis_fd, name="my-app")
    ...  # inject input for as long as the session is needed
```

**Save whatever comes back on every run, not just the first.** The portal is
free to hand back a different token each time, and a caller that keeps only
the original would eventually present a stale one. (On GNOME the same token
comes back on each restore — that is one portal's behaviour, not a guarantee.)

Passing `restore_token` *without* a `persist_mode` raises `ValueError`: the
portal answers such a request with no token at all, so storing what came back
would write `None` over the token you just spent.

**Treat the token as a credential.** Anyone holding it can reopen input
injection on that desktop, so it belongs wherever you would keep a password.
The decision to store it at all belongs to the application rather than to this
library, which never writes it anywhere itself.

Three differences from `Oeffis` worth knowing:

- **Blocking, not event-driven.** `negotiate()` runs its own nested
  `GLib.MainLoop` per D-Bus round trip and returns only once connected, or
  raises `PortalVersionError` / `PortalDeniedError` / `PortalTimeoutError`.
- **Bounded.** Each round trip gets `timeout` seconds (60 by default —
  generous, since `Start` waits on a human answering a dialog). Without it a
  portal that dies after accepting the call would wedge the calling thread
  forever, which is the one thing `Oeffis`'s pollable fd protects against.
- **Close it.** The portal session lives in xdg-desktop-portal and outlives
  the object unless `Session.Close()` is called — `Gio.bus_get_sync()` hands
  back GLib's *shared* connection, so dropping the session tears nothing down,
  and a long-running process that negotiates repeatedly accumulates live
  sessions. The `with` block above handles it; otherwise call
  `session.close()`.

## Pointer, buttons and scrolling

Every burst of input is wrapped in `start_emulating()` … `frame()` …
`stop_emulating()`, and `frame()` is what actually commits the queued events.

```python
# Move the pointer 10px right, 5px down
device.start_emulating().pointer_motion(10, 5).frame().stop_emulating()

# Left click (BTN_LEFT; codes are Linux input codes, from
# <linux/input-event-codes.h>)
BTN_LEFT = 0x110
device.start_emulating()
device.button(BTN_LEFT, True).frame()
device.button(BTN_LEFT, False).frame()
device.stop_emulating()

# Scroll: smooth (logical pixels) or discrete (one detent is 120)
device.start_emulating().scroll_delta(0, 20).frame().stop_emulating()
device.start_emulating().scroll_discrete(0, 120).frame().stop_emulating()
```

`scroll_stop()` and `scroll_cancel()` exist too, for ending a kinetic scroll
gesture properly.

## Absolute positioning

Needs the `POINTER_ABSOLUTE` capability, and coordinates must fall inside one
of `device.regions`:

```python
device.start_emulating().pointer_motion_absolute(960, 540).frame().stop_emulating()
```

Regions carry more than bounds. `region.mapping_id` groups the ones that map
to the same thing, `device.region_at(x, y)` finds which region a point falls
in, and `region.convert_point(x, y)` turns a desktop-wide point into one
relative to that region — returning `None` when it falls outside, which also
answers "is it in here?" in a single call. All three need libei 1.1.

## Picking the right device when several resume

**Pick by capability, not by arrival order.** A seat can resume more than one
device — on GNOME you get *both* a relative `virtual pointer` and an absolute
`shared virtual absolute pointer`, and the relative one arrives first.

Reusing the quick start's "first `DEVICE_RESUMED` wins" loop hands you the
relative device, on which `pointer_motion_absolute()` does nothing at all: no
exception, no movement, just an internal libei warning (`device is not an
absolute pointer`, visible only with [logging](#logging) on).

```python
device = None
while device is None:
    select.select([sender.fd], [], [], 5)
    sender.dispatch()
    for event in sender.events:
        if event.event_type is ei.EventType.SEAT_ADDED:
            event.seat.bind((
                ei.DeviceCapability.POINTER_ABSOLUTE,
                ei.DeviceCapability.BUTTON,
            ))
        elif event.event_type is ei.EventType.DEVICE_RESUMED:
            if ei.DeviceCapability.POINTER_ABSOLUTE in event.device.capabilities:
                device = event.device      # skip the relative sibling
```

## Keyboard: positions, not characters

`keyboard_key()` takes a Linux evdev keycode — a *physical key position*, not
a character. `KEY_A = 30` means "the key where A sits on a US QWERTY board";
under Dvorak or AZERTY the compositor turns that same code into a different
character. There is no `type("hello")` here, so shifted characters mean
sending the modifier yourself:

```python
KEY_LEFTSHIFT, KEY_A = 42, 30
device.start_emulating()
device.keyboard_key(KEY_LEFTSHIFT, True).frame()
device.keyboard_key(KEY_A, True).frame()      # "A", not "a"
device.keyboard_key(KEY_A, False).frame()
device.keyboard_key(KEY_LEFTSHIFT, False).frame()
device.stop_emulating()
```

To get this right for whatever layout the user actually has, read the keymap
the compositor handed you and resolve characters through it — with the
`xkbcommon` bindings, say, which this package does not depend on:

```python
keymap = device.keymap          # None unless the device has KEYBOARD
if keymap is not None:
    assert keymap.keymap_type is ei.KeymapType.XKB   # the only type so far
    with keymap.fd as f:        # a fresh dup() each read; closing it is yours
        data = f.read(keymap.size)
```

`keymap.fd` duplicates libei's descriptor and rewinds the copy for you. Left
to itself a `dup()` shares the original's file offset, which libei leaves at
the end — reading through it returned zero bytes and no error, which is
indistinguishable from an empty keymap.

Modifier *state* arrives as events rather than being queryable — watch for
`EventType.KEYBOARD_MODIFIERS` and read `event.keyboard_xkb_modifiers`, which
gives `depressed`, `latched`, `locked` and `group`. That is how you find out
the compositor thinks Caps Lock is on before you start injecting.

## Typing text directly, on libei 1.6

A device with the `TEXT` capability takes characters directly, and the
compositor works out which keys that means under the active layout:

```python
device.start_emulating().text_utf8("héllo").frame().stop_emulating()
device.start_emulating().text_keysym(0x61, True).frame().stop_emulating()
```

This is the one path here that types text rather than pressing positions. It
needs libei 1.6 on both sides and a seat that offers `DeviceCapability.TEXT`;
on anything older, `text_utf8()` raises `LibraryNotFoundError`, so keep the
keycode path as a fallback.

## Touch

Touch uses its own short-lived object rather than the device directly:

```python
touch = device.touch_new()
device.start_emulating()
touch.down(100, 200)
device.frame()
touch.motion(150, 250)
device.frame()
touch.up()          # or touch.cancel(), if the gesture was aborted
device.frame()
device.stop_emulating()
```

A cancelled touch still reaches the other side as a `TOUCH_UP` event; what
separates it from a normal release is `event.touch_up_event.is_cancel`. Both
sides need version 2 or later of the `ei_touchscreen` interface, and against
anything older `cancel()` is a noop.

## Reading input instead of sending it

Use `ei.Receiver` in place of `ei.Sender` — same connection dance, but events
carry input *from* the compositor:

```python
receiver = ei.Receiver.create_for_fd(eis_fd, name="my-app")
receiver.dispatch()
for event in receiver.events:
    if event.event_type is ei.EventType.POINTER_MOTION:
        motion = event.pointer_event
        print(motion.dx, motion.dy)
```

Each event type has its own getter — `key_event`, `button_event`,
`pointer_event`, `pointer_absolute_event`, `scroll_event`,
`scroll_discrete_event`, `scroll_stop_event`, `touch_event` and
`touch_up_event`, `text_utf8_event`, `text_keysym_event` and
`keyboard_xkb_modifiers` — and **each checks the event's type before reading**,
raising `TypeError` rather than handing back the zero-filled result libei
would give for a mismatch.

Event types this package has never heard of do not raise: `event_type` returns
a plain `int`, because libei's own header says the enum "is not exhaustive".
To look at what is coming without consuming it, `peek_event_type()` reports
the next event's type and leaves it queued.

**Do not keep an event past its loop iteration.** Each event is released as
soon as the loop moves on, and using it afterwards raises `RuntimeError`.
Objects you pull *off* an event (`event.device`, `event.seat`) are safe to
keep — copy out `event.pointer_event` and friends rather than the event.

## Keeping a connection alive, and closing it

**Check the connection is alive.** A ping is a round trip that comes back as a
`PONG` event carrying the same object, so several can be in flight at once and
still be told apart:

```python
ping = sender.new_ping()
ping.send()
# ... later, in the event loop:
#   if event.event_type is ei.EventType.PONG and event.pong.id == ping.id:
#       ...
```

**Ask for another device.** If you closed one, or the ones the seat gave you
no longer cover what you need, `seat.request_device((cap, ...))` asks for
another — a subset of what `bind()` requested. The server may answer with
different capabilities, or not at all; anything it does create arrives as a
`DEVICE_ADDED` event. Needs libei 1.6.

**Shut down deliberately.** `sender.disconnect()` tears the session down
through the event queue: seats and devices are removed as though the server
had done it, and `DISCONNECT` is the last event you get. The context is inert
afterwards but still needs releasing like any other object. Needs libei 1.4.

## Running your own EIS server

`libei.eis` is the compositor side of the protocol. Most people want it for
*testing* — it lets you drive the client code above without a real compositor
or a consent dialog:

```python
import select
from libei import eis

server = eis.Eis.create_for_fd()
client_fd = server.add_client()  # hand this fd to a client's ei.Sender/Receiver

while True:
    select.select([server.fd], [], [])
    server.dispatch()  # events is empty until dispatch() reads the connection
    for event in server.events:
        if event.event_type is eis.EventType.CLIENT_CONNECT:
            event.client.connect()
            seat = event.client.new_seat("default")
            seat.configure_capabilities((eis.DeviceCapability.POINTER,))
            seat.add()
        elif event.event_type is eis.EventType.SEAT_BIND:
            device = event.seat.new_device()
            device.configure(
                name="my-pointer", capabilities=(eis.DeviceCapability.POINTER,)
            )
            device.add()
            device.resume()  # until you resume it, the client may not send
```

`tests/test_integration_socketpair.py` is a complete, working version of both
halves — connect, negotiate, and round-trip a pointer motion, in one process
against the real library.

## Logging

libei's own diagnostics are routed into Python's `logging` — the `libei.ei`,
`libei.eis` and `libei.oeffis` loggers — rather than being written to stderr
by the C library. This is how you see the warnings that otherwise look like
nothing happening at all, `device is not an absolute pointer` among them:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("libei").setLevel(logging.DEBUG)
```

At `DEBUG` this is a full protocol trace (every object, message and signature,
both directions — a few hundred lines for a single connect and one pointer
motion), which makes it the first thing to reach for when a negotiation
stalls. `WARNING` gets you just libei's complaints.
