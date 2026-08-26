# python-libei

Python bindings for [libei, libeis and liboeffis](https://libinput.pages.freedesktop.org/libei/) —
the Wayland input-emulation libraries. Use this to **move the pointer, click,
type, or scroll on a Wayland desktop** from Python, the way `xdotool` did on
X11.

Pure ctypes, no build step, no dependencies.

```python
device.start_emulating().pointer_motion(5, 0).frame().stop_emulating()
```

## Requirements

- Linux with a Wayland compositor (GNOME, KDE, Sway, …)
- The native libraries: on Fedora, `sudo dnf install libei libeis liboeffis`
- libei 1.0.0 or newer. Every symbol this package binds has existed with a
  stable signature since 1.0.0, and upstream keeps API/ABI back-compatible
  within the 1.x series — but only 1.5.0 and 1.6.0 (Fedora 44) have actually
  been run against. An install that's genuinely too old fails at first use
  with `LibraryNotFoundError`, not at import.

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

# Type the letter "a" (KEY_A)
KEY_A = 30
device.start_emulating()
device.keyboard_key(KEY_A, True).frame()
device.keyboard_key(KEY_A, False).frame()
device.stop_emulating()

# Scroll: smooth (logical pixels) or discrete (one detent is 120)
device.start_emulating().scroll_delta(0, 20).frame().stop_emulating()
device.start_emulating().scroll_discrete(0, 120).frame().stop_emulating()
```

Absolute positioning needs the `POINTER_ABSOLUTE` capability, and coordinates
fall inside one of `device.regions`:

```python
device.start_emulating().pointer_motion_absolute(960, 540).frame().stop_emulating()
```

Touch uses its own short-lived object rather than the device directly:

```python
touch = device.touch_new()
device.start_emulating()
touch.down(100, 200)
device.frame()
touch.motion(150, 250)
device.frame()
touch.up()
device.frame()
device.stop_emulating()
```

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
before `DEVICE_RESUMED`.

**The loop hangs waiting for a device.** Check that the seat actually offers
the capability you bound (`seat.capabilities`).

## API summary

| Module | Use it for |
| --- | --- |
| `libei.ei` | Clients: `Sender` (inject), `Receiver` (consume) |
| `libei.eis` | Servers: `Eis`, for compositors and for testing clients |
| `libei.oeffis` | Getting an EI fd from the desktop portal |

Each module has `is_available()`, an `Error` exception, and an `EventType` /
`DeviceCapability` enum. The package ships `py.typed`.

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
