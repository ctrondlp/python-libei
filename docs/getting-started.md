# Getting started

Moving a pointer on a real Wayland desktop, from nothing, in five minutes.

## 1. Install

```sh
pip install python-libei
```

Pure Python, no build step — ctypes talks to the native libraries directly, so
there is no compiler and no `libei-devel` involved. What `pip` does *not*
bring is the native libraries themselves:

```sh
sudo dnf install libei libeis liboeffis     # Fedora
```

The distribution is named `python-libei`, the import is `libei` — so
`pip show python-libei`, but `from libei import ei`.

Importing is always safe even where the libraries are missing, because they
are loaded on first *use*, not at import:

```python
from libei import ei

if not ei.is_available():
    ...  # fall back to another input backend
```

## 2. The one concept: fill a frame, then send it

Everything else follows from this. Events do not go out when you call the
method that describes them — they queue up, and **`frame()` is what commits
them as one logical hardware event**:

```python
device.start_emulating().pointer_motion(5, 0).frame().stop_emulating()
```

Forget `frame()` and nothing happens. No exception, no warning, no movement —
which is the single most common reason a first attempt appears to do nothing
at all. Each method returns the device, so they chain.

## 3. The five words you need

| Term | Meaning |
| --- | --- |
| **Sender** | A client that *injects* input. This is what you want for automation |
| **Receiver** | A client that *consumes* input. For compositor-side code |
| **Seat** | A group of input devices, offered by the compositor. You ask it for the capabilities you need |
| **Capability** | What kind of input you want: `POINTER`, `KEYBOARD`, `TOUCH`, `SCROLL`, `BUTTON`, … |
| **Device** | What you actually send events through, handed to you after you bind a capability |

The flow is always the same: **connect → bind a capability on a seat → wait
for a device → send events through it.**

## 4. A complete first program

Getting an EI connection means asking the desktop portal, which shows the user
a consent dialog. After that you have an fd, and everything downstream is the
same regardless of how you got it.

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

Three things in there are load-bearing:

- **Wait for `DEVICE_RESUMED`, not `DEVICE_ADDED`.** A device arrives paused,
  and libei calls sending events before it resumes "a client bug".
- **`dispatch()` before `events`.** `events` drains only what is already
  queued; it yields nothing until `dispatch()` has read from the socket.
- **This loop takes the first device to resume**, which is fine here because
  only `POINTER` was bound. Bind more than one capability and a seat may
  resume several devices — see
  [recipes.md](recipes.md#picking-the-right-device-when-several-resume).

**The dialog comes back every run.** That is `libei.oeffis`'s limitation, not
a rule of the protocol — see
[recipes.md](recipes.md#avoiding-the-consent-dialog-on-every-run) for the
`libei.portal` route with `persist_mode` and `restore_token`.

## 5. When nothing happens

It will, at least once. Almost always a missing `frame()`, emulating before
`DEVICE_RESUMED`, or a device that lacks the capability for the event being
sent — all three fail *silently*. The 10-point checklist is
[troubleshooting.md](troubleshooting.md).

Turn on logging before guessing:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("libei").setLevel(logging.DEBUG)
```

libei's own diagnostics are routed into Python's `logging` rather than written
to stderr by the C library, so this is how you see warnings like `device is
not an absolute pointer` that otherwise look like nothing happening at all.

## Where to go next

| If you want… | Read |
|---|---|
| Keyboards, touch, scrolling, absolute positioning | [recipes.md](recipes.md) |
| Something silently does nothing | [troubleshooting.md](troubleshooting.md) |
| Which module to use for what | the README's [Which API do I need?](../README.md#which-api-do-i-need) |
| To read input rather than send it | [recipes.md](recipes.md#reading-input-instead-of-sending-it) |
| To run your own EIS server, for tests | [recipes.md](recipes.md#running-your-own-eis-server) |
| How the bindings are built | [developers/architecture.md](developers/architecture.md) |
