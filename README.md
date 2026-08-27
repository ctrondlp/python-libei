# python-libei

Python bindings for [libei, libeis and liboeffis](https://libinput.pages.freedesktop.org/libei/) —
the Wayland input-emulation libraries. Use this to **move the pointer, click,
type, or scroll on a Wayland desktop** from Python, the way `xdotool` did on
X11.

Pure ctypes, no build step, no dependencies.

```python
device.start_emulating().pointer_motion(5, 0).frame().stop_emulating()
```

## What it's for

Driving a Wayland desktop from Python, when you need real input events rather
than a widget-tree back door:

- **GUI test automation** — click and type at an application the way a user
  does, against the real compositor.
- **Remote desktop and screen sharing** — inject the remote user's input into
  the local session.
- **Accessibility tooling** — on-screen keyboards, dwell clicking, alternative
  pointing devices.
- **Macros and scripting** — the `xdotool`-shaped jobs that stopped working
  when the desktop moved off X11.
- **Compositor and protocol work** — `libei.eis` is the server half of the
  protocol, so an EIS server (or a test double for one) can be written in
  Python too.

### What it isn't

- **Not an X11 tool.** This speaks the EI/EIS protocol to a compositor that
  implements it. On an X server there is nothing to talk to, and nothing here
  falls back to XTEST — despite the `xdotool` comparison above, it is not a
  drop-in replacement for one.
- **Keyboard input is key positions, not characters.** `keyboard_key()`
  takes Linux evdev *keycodes*, and what character one produces is the
  compositor's layout to decide. `text_utf8()` does send characters
  directly, but only against libei 1.6 with a TEXT-capable device — see
  [Keys are positions, not characters](#keys-are-positions-not-characters).
- **Not a screen-reading library.** libei is input only. Pair it with the
  ScreenCast portal and PipeWire if you also need pixels.
- **Not a way around user consent.** A real session goes through the portal's
  consent dialog, by design. Input that must bypass that prompt belongs at the
  kernel layer (`/dev/uinput`) instead — a different tool and a different
  trust model.

## What's implemented

Injection covers the input types most automation needs; the rest of libei's
capability enum is recognized but not driveable.

| `DeviceCapability` | What you get |
| --- | --- |
| `POINTER` | `pointer_motion()` |
| `POINTER_ABSOLUTE` | `pointer_motion_absolute()`, `device.regions` |
| `BUTTON` | `button()` |
| `KEYBOARD` | `keyboard_key()`, `device.keymap`, `KEYBOARD_MODIFIERS` events |
| `SCROLL` | `scroll_delta()`, `scroll_discrete()`, `scroll_stop()`, `scroll_cancel()` |
| `TOUCH` | `device.touch_new()` → `down()` / `motion()` / `up()` |
| `TEXT` | `text_utf8()`, `text_keysym()`, `TEXT_UTF8` / `TEXT_KEYSYM` events (libei 1.6+) |
| `GESTURES` | not in any released libei — see below |
| `STYLUS` | not in any released libei — see below |

`GESTURES` and `STYLUS` are a different case from the rest of that table.
They, and the swipe/pinch/hold/stylus members of `EventType`, exist on
libei's `main` branch but in **no released version** — 1.6.0's own
`enum ei_device_capability` stops at `TEXT`, and its `enum ei_event_type`
stops at `EI_EVENT_TEXT_UTF8`. The values here match upstream `main`
exactly, so they are ready for whatever release adds them; until then,
binding those capabilities against a real library does nothing and the
events cannot arrive. The 22 gesture/stylus accessor functions `main` adds
are deliberately not bound — nothing that ships today exports them, so
nothing here could be verified against a real library, which is the bar
every other binding in this package was held to.

`EventType` otherwise mirrors libei's enum in full, and any event type can
be *identified* and released safely whether or not it has an accessor.

Beyond sending input, the wrapper also covers ping/pong round trips
(`Context.new_ping()`), touch cancellation (`Touch.cancel()`), keymap
transfer (`Device.keymap`), region mapping ids and coordinate conversion,
`Context.disconnect()`, `Context.peek_event_type()`, and
`Seat.request_device()`. On the server side, `libei.eis` mirrors all of it
and adds `Eis.set_flag()` and `Client.pid`.

Underneath, the ctypes layer binds 250 of the 302 functions the three
libraries export as of 1.6.0 (libei 109/132, libeis 131/158, liboeffis
10/12). What is left out is deliberate: `*_get_user_data()` /
`*_set_user_data()` (the Python wrapper object is where you keep state),
the `*_ref()` / `*_unref()` pairs that `CObject` handles for you, the
logging-context accessors, `*_event_type_to_string()`, the NUL-terminated
`*_device_text_utf8()` (the `_with_length` form is bound instead, so text
containing a NUL isn't truncated), `ei_new()` (superseded by
`ei_new_sender()` / `ei_new_receiver()`), `*_clock_set_now_func()`, and the
`*_get_context()` accessors, which have nothing to hand back: a context is
only ever created by its own `create_for_*()`, never wrapped from a raw
pointer.

## Status

Alpha (`0.1.0.dev0`), not yet on PyPI, and the API is not frozen — expect
renames before 1.0. What that qualifier covers, concretely:

- The injection path — connect, bind, wait for a device, send events — is
  exercised end-to-end against the real libraries by
  `tests/test_integration_socketpair.py`, and is in use as the Wayland input
  backend of a separate GUI-automation project.
- Text input, touch cancellation, ping/pong, keymap transfer, region mapping
  ids and `peek_event_type()` are each round-tripped through a real libeis
  server in `tests/test_integration_extras.py`.
- The portal path (`libei.oeffis`) has only ever been verified by hand, since
  it needs an interactive consent dialog that nothing here can drive. See
  [Troubleshooting](#troubleshooting).
- Verified against libei 1.6.0 on Fedora 44 / GNOME 50.4, on CPython 3.13;
  the core injection path also against 1.5.0. Older versions are supported by
  symbol analysis and per-feature version gates, not by having been run — and
  the 1.1/1.4/1.6 features above cannot have been, since 1.6.0 is what's
  installed here.

## Alternatives

| Instead of this | Why you might |
| --- | --- |
| [snegg](https://gitlab.freedesktop.org/whot/snegg) | The reference bindings, by libei's own author — closer to upstream, and first to get new API. Self-described as for "rapid prototyping" with an explicitly unstable API, and `import snegg.ei` fails outright where libei isn't installed. [`docs/vs-snegg.md`](docs/vs-snegg.md) covers the differences in detail. |
| The portal's D-Bus API directly (`org.freedesktop.portal.RemoteDesktop`, via Gio or dbus-python) | No native library and no bindings at all — `NotifyPointerMotion`, `NotifyKeyboardKeycode` and friends are plain method calls. The catch is absolute motion: `NotifyPointerMotionAbsolute` needs a PipeWire stream id, which only exists after a second, separate ScreenCast consent dialog. libei has no such requirement. |
| `ydotool` and other `/dev/uinput` tools | Kernel-level, so they work under any compositor and need no portal session — at the cost of a privileged daemon, and of sidestepping the consent model that EI exists to enforce. |

## Requirements

- Linux with a Wayland compositor (GNOME, KDE, Sway, …)
- CPython 3.10 or newer (tested on 3.13)
- The native libraries: on Fedora, `sudo dnf install libei libeis liboeffis`
- libei 1.0.0 or newer for the core: connecting, binding a seat, and
  sending pointer, button, keyboard, scroll and touch input all use symbols
  that have existed with a stable signature since 1.0.0, and upstream keeps
  API/ABI back-compatible within the 1.x series. Only 1.5.0 and 1.6.0
  (Fedora 44) have actually been run against.

  Newer libei buys you more, per feature:

  | Needs | For |
  | --- | --- |
  | 1.1 | `Region.mapping_id`, `Region.convert_point()`, `Device.region_at()` |
  | 1.4 | ping/pong round trips (`Context.new_ping()`), `Context.disconnect()` |
  | 1.6 | `Device.text_utf8()` / `text_keysym()` and TEXT events, `Seat.request_device()`, `Eis.set_flag()` |

  Nothing is resolved until it is called, so a build without one of these
  costs you only that call — it raises `LibraryNotFoundError` naming the
  missing symbol, while the rest of the package keeps working.

## Install

Not published to PyPI yet — install from a checkout:

```sh
git clone https://github.com/ctrondlp/python-libei.git
cd python-libei
pip install .
```

Importing is always safe, even where the native libraries are missing — they
are loaded on first use, not at import. Check before you rely on them:

```python
from libei import ei

if not ei.is_available():
    ...  # fall back to another input backend
```

## Concepts

Five terms are enough to read the rest of this file:

| Term | Meaning |
| --- | --- |
| **Sender** | A client that *injects* input. This is what you want for automation. |
| **Receiver** | A client that *consumes* input. For compositor-side code. |
| **Seat** | A group of input devices, offered by the compositor. You ask it for the capabilities you need. |
| **Capability** | What kind of input you want: `POINTER`, `KEYBOARD`, `TOUCH`, `SCROLL`, `BUTTON`, … |
| **Device** | What you actually send events through, handed to you after you bind a capability. |

The flow is always the same: **connect → bind a capability on a seat → wait
for a device → send events through it.**

## Quick start

Getting an EI connection means asking the desktop portal, which shows the user
a consent dialog. After that you have a fd, and everything else is the same
regardless of how you got it.

```python
import select
from libei import ei, oeffis

# 1. Ask the portal for permission. The user sees a consent dialog.
session = oeffis.Oeffis.create(devices=oeffis.DeviceType.POINTER)
while True:
    ready, _, _ = select.select([session.fd], [], [], 30)
    if not ready:
        raise TimeoutError("portal request timed out")
    if session.dispatch():
        break  # session.eis_fd is now valid

# 2. Connect as a sender.
sender = ei.Sender.create_for_fd(session.eis_fd, name="my-app")

# 3. Ask for a pointer, and wait for the compositor to hand one over.
device = None
while device is None:
    select.select([sender.fd], [], [], 5)
    sender.dispatch()  # events stays empty until dispatch() reads the socket
    for event in sender.events:
        if event.event_type is ei.EventType.SEAT_ADDED:
            event.seat.bind((ei.DeviceCapability.POINTER,))
        elif event.event_type is ei.EventType.DEVICE_RESUMED:
            device = event.device

# 4. Send input.
device.start_emulating().pointer_motion(5, 0).frame().stop_emulating()
```

Wait for `DEVICE_RESUMED`, not `DEVICE_ADDED` — a device arrives paused, and
libei calls sending events before it resumes "a client bug".

This loop takes the first device to resume, which is fine here because only
`POINTER` was bound. Bind more than one capability and a seat may resume
several devices — see the absolute-positioning notes under
[Sending input](#sending-input) before reusing this pattern.

## Sending input

Every burst of input is wrapped in `start_emulating()` … `frame()` …
`stop_emulating()`. `frame()` is what actually commits the queued events as one
logical hardware event; without it nothing is delivered. Each method returns
the device, so they chain.

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

# Press the A key (KEY_A -- a key *position*, not the character
# "a"; see "Keys are positions, not characters" below)
KEY_A = 30
device.start_emulating()
device.keyboard_key(KEY_A, True).frame()
device.keyboard_key(KEY_A, False).frame()
device.stop_emulating()

# Scroll: smooth (logical pixels) or discrete (one detent is 120)
device.start_emulating().scroll_delta(0, 20).frame().stop_emulating()
device.start_emulating().scroll_discrete(0, 120).frame().stop_emulating()
```

### Keys are positions, not characters

`keyboard_key()` takes a Linux evdev keycode — a *physical key position*, not
a character. `KEY_A = 30` means "the key where A sits on a US QWERTY board";
under Dvorak or AZERTY the compositor turns that same code into a different
character. There is no `type("hello")` and no keysym mapping here, so shifted
characters mean sending the modifier yourself:

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

### Or skip layouts entirely, on libei 1.6

A device with the `TEXT` capability takes characters directly, and the
compositor works out which keys that means under the active layout:

```python
device.start_emulating().text_utf8("héllo").frame().stop_emulating()
device.start_emulating().text_keysym(0x61, True).frame().stop_emulating()
```

This is the one path here that types text rather than pressing positions.
It needs libei 1.6 on both sides and a seat that offers
`DeviceCapability.TEXT`; on anything older, `text_utf8()` raises
`LibraryNotFoundError`, so keep the keycode path as a fallback.

Modifier *state* arrives as events rather than being queryable — watch for
`EventType.KEYBOARD_MODIFIERS` and read `event.keyboard_xkb_modifiers`, which
gives `depressed`, `latched`, `locked` and `group`. That is how you find out
the compositor thinks Caps Lock is on before you start injecting.

Absolute positioning needs the `POINTER_ABSOLUTE` capability, and coordinates
fall inside one of `device.regions`:

```python
device.start_emulating().pointer_motion_absolute(960, 540).frame().stop_emulating()
```

Regions carry more than bounds. `region.mapping_id` groups the ones that map
to the same thing, `device.region_at(x, y)` finds which region a point falls
in, and `region.convert_point(x, y)` turns a desktop-wide point into one
relative to that region — returning `None` when it falls outside, which also
answers "is it in here?" in a single call. All three need libei 1.1.

**Pick the device by capability, not by arrival order.** A seat can resume
more than one device — on GNOME you get *both* a relative `virtual pointer`
and an absolute `shared virtual absolute pointer`, and the relative one
arrives first. Reusing the quick-start's "first `DEVICE_RESUMED` wins" loop
here hands you the relative device, on which `pointer_motion_absolute()`
does nothing at all: no exception, no movement, just an internal libei
warning (`device is not an absolute pointer`, visible only if you turn on
[logging](#logging)). Wait for the one you need:

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

## Things that will bite you

- **`dispatch()` before `events`.** `events` drains only what is already
  queued; it yields nothing until `dispatch()` has read from the socket.
- **Don't keep an event past its loop iteration.** Each event is released as
  soon as the loop moves on, and using it afterwards raises `RuntimeError`.
  Objects you pull *off* an event (`event.device`, `event.seat`) are safe to
  keep — copy out `event.pointer_event` and friends rather than the event.
- **`frame()` or nothing happens.** Events queue up until a `frame()` commits
  them.
- **`bind()` needs at least one capability.** Binding an empty set sends
  nothing, so the device you are waiting for never arrives; this raises
  `ValueError` rather than hanging.
- **Capabilities are per-seat.** A seat only offers some; check
  `seat.capabilities` before binding.
- **One seat can resume several devices.** Bind both `POINTER` and
  `POINTER_ABSOLUTE` and GNOME gives you two, relative first. Taking
  whichever resumes first is a coin flip — select on `device.capabilities`
  instead. Sending an event the device lacks the capability for is silently
  ignored, which makes this look like the injection simply not working.

- **Read the accessor that matches the event type.** `event.key_event` on a
  `POINTER_MOTION` event raises `TypeError` naming both types. libei itself
  would have returned `KeyEvent(key=0, is_press=False)` — a real-looking
  value — while logging a `Bug:` line the caller never sees, so branch on
  `event_type` first. `TOUCH_UP` has its own `touch_up_event`, since it
  carries no coordinates.
- **`GESTURES` and `STYLUS` are not in any released libei.** They match
  upstream `main` and are here ready for it, but 1.6.0's capability enum
  stops at `TEXT`. Binding them against a shipping library silently does
  nothing — no error, no device, no events.

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
`keyboard_xkb_modifiers` — and each checks the event's type before reading,
raising `TypeError` rather than handing back the zero-filled result libei
would give for a mismatch.

Gesture and stylus events have no accessor and cannot arrive from a
released libei at all (see [What's implemented](#whats-implemented)), but
they can be identified and skipped safely if they ever do. So can event
types this package has never heard of:
`event_type` returns a plain `int` rather than raising, because libei's own
header says the enum "is not exhaustive". To look at what is coming without
consuming it, `peek_event_type()` reports the next event's type and leaves it
queued.

## Connection lifecycle

Beyond sending input, a long-lived client usually wants three things.

**Check the connection is alive.** A ping is a round trip that comes back as
a `PONG` event carrying the same object, so several can be in flight at once
and still be told apart:

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

At `DEBUG` this is a full protocol trace (every object, message and
signature, both directions — a few hundred lines for a single connect and
one pointer motion), which makes it the first thing to reach for when a
negotiation stalls. `WARNING` gets you just libei's complaints.

## Troubleshooting

**The portal dialog appears, I approve it, and nothing happens.** Earlier
testing on GNOME 44 saw the round trip hang indefinitely even after clicking
through the dialog, and it went unroot-caused for a while. Revisited
2026-08-25 on GNOME 50.4 with a `busctl monitor` trace on the real portal:
`CreateSession -> SelectDevices -> Start -> ConnectToEIS` completed cleanly in
a few seconds, 3/3 consecutive attempts, with `Start()`'s `Response` signal
arriving only after a multi-second gap consistent with a real dialog being
answered. The code was correctly waiting the whole time -- `dispatch()`
returning `False` just means no `Response` has arrived yet.

The likely explanation for the earlier hangs: `RemoteDesktop.Start()` can
involve more than one prompt (an access-request dialog, then a device-sharing
confirmation), and dismissing or missing one leaves `Start()` never returning
-- indistinguishable from a hang on the caller's side. Not independently
confirmed by watching the dialogs themselves, only inferred from this trace
plus which step it stalled at previously; if you hit this again, check
whether a second prompt is waiting before assuming it's this library.
`libei.eis` remains the right fallback for anything that doesn't need the
portal at all, e.g. tests.

**`LibraryNotFoundError`.** The native library isn't installed, or is too old
to export a function this package binds. Check with `ei.is_available()`.

**My events never arrive.** Almost always a missing `frame()`, or emulating
before `DEVICE_RESUMED`, or a device that lacks the capability for the event
you're sending — all three fail silently. Turn on [logging](#logging) at
`DEBUG` to see what actually reached the compositor.

**The loop hangs waiting for a device.** Check that the seat actually offers
the capability you bound (`seat.capabilities`).

## API summary

| Module | Use it for |
| --- | --- |
| `libei.ei` | Clients: `Sender` (inject), `Receiver` (consume) |
| `libei.eis` | Servers: `Eis`, for compositors and for testing clients |
| `libei.oeffis` | Getting an EI fd from the desktop portal |

Each module has `is_available()`, an `Error` exception, and an `EventType` /
`DeviceCapability` enum. `ei` and `eis` also share the shapes around them:
`Device`, `Seat`, `Region`, `Keymap`, `Touch`, `Ping`, `Event`, and the frozen
dataclasses its accessors return. The package ships `py.typed`, so callers
type-check against real annotations rather than `Any`.

For which of libei's capabilities are actually driveable, and which C
functions are left unbound, see [What's implemented](#whats-implemented).

## Development

```
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

ruff check src tests
python -m mypy

pytest                        # everything; the integration tests below
                              # skip themselves if libei/libeis is absent
pytest -m integration         # only the tests that drive the real libraries
```

## Design notes

Written from scratch, taking its overall shape from
[snegg](https://gitlab.freedesktop.org/whot/snegg) (the reference bindings by
libei's own author), with different priorities suited to being embedded as a
dependency rather than used for prototyping.
[`docs/vs-snegg.md`](docs/vs-snegg.md) covers the specifics, including two
signature issues found by cross-checking against the real libei source.

## License

MIT.
