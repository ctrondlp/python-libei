# python-libei

Python bindings for [libei, libeis and liboeffis](https://libinput.pages.freedesktop.org/libei/) —
the Wayland input-emulation libraries. Use this to **move the pointer, click,
type, or scroll on a Wayland desktop** from Python, the way `xdotool` did on
X11.

Pure ctypes, no build step, no dependencies.

```python
device.start_emulating().pointer_motion(5, 0).frame().stop_emulating()
```

**The one concept:** events queue up, and **`frame()` is what sends them** as
one logical hardware event. Forget it and nothing happens — no exception, no
warning, no movement. Fill the package, then post it.

New here? [docs/getting-started.md](docs/getting-started.md) is install
through a first real pointer motion, in five minutes.

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
  [Keys are positions, not characters](docs/recipes.md#keyboard-positions-not-characters).
- **Not a screen-reading library.** libei is input only. Pair it with the
  ScreenCast portal and PipeWire if you also need pixels.
- **Not a way around user consent.** A real session goes through the portal's
  consent dialog, by design. Input that must bypass that prompt belongs at the
  kernel layer (`/dev/uinput`) instead — a different tool and a different
  trust model.

## Which API do I need?

Five modules, and most callers need exactly two of them: `oeffis` or `portal`
to get permission, then `ei` to inject.

| You want to… | Use | Notes |
| --- | --- | --- |
| **Inject input** into a desktop | `libei.ei` → `Sender` | The automation case. This is what the quick start below does |
| **Consume input** from a compositor | `libei.ei` → `Receiver` | Compositor-side or input-capture code; same connection dance |
| **Get permission**, simply | `libei.oeffis` | One call, pollable fd, no dependencies. The consent dialog appears on **every** run |
| **Get permission**, and not be asked again | `libei.portal` | Same handshake over D-Bus directly, with `persist_mode` / `restore_token`. Needs PyGObject |
| **Be the server**, for tests or a compositor | `libei.eis` | Drives your client code with no real compositor and no consent dialog |

Each module has `is_available()`, an `Error` exception, and an `EventType` /
`DeviceCapability` enum. `ei` and `eis` also share the shapes around them:
`Device`, `Seat`, `Region`, `Keymap`, `Touch`, `Ping`, `Event`, and the frozen
dataclasses its accessors return. The package ships `py.typed`, so callers
type-check against real annotations rather than `Any`.

## What's implemented

Injection covers the input types most automation needs.

| `DeviceCapability` | What you get |
| --- | --- |
| `POINTER` | `pointer_motion()` |
| `POINTER_ABSOLUTE` | `pointer_motion_absolute()`, `device.regions` |
| `BUTTON` | `button()` |
| `KEYBOARD` | `keyboard_key()`, `device.keymap`, `KEYBOARD_MODIFIERS` events |
| `SCROLL` | `scroll_delta()`, `scroll_discrete()`, `scroll_stop()`, `scroll_cancel()` |
| `TOUCH` | `device.touch_new()` → `down()` / `motion()` / `up()` |
| `TEXT` | `text_utf8()`, `text_keysym()`, `TEXT_UTF8` / `TEXT_KEYSYM` events (libei 1.6+) |

### Recognized, but not in any released libei

| `DeviceCapability` | State |
| --- | --- |
| `GESTURES` | Exists on libei's `main` branch only. **Binding it against a shipping library silently does nothing** — no error, no device, no events |
| `STYLUS` | Same |

These are a different case from the table above, and worth stating separately
because a skimming reader could otherwise take them as supported. 1.6.0's own
`enum ei_device_capability` stops at `TEXT`, and its `enum ei_event_type`
stops at `EI_EVENT_TEXT_UTF8` — so the swipe/pinch/hold/stylus members of
`EventType` cannot arrive either. The values here match upstream `main`
exactly, so they are ready for whatever release adds them. The 22
gesture/stylus accessor functions `main` adds are deliberately not bound:
nothing that ships today exports them, so nothing here could be verified
against a real library, which is the bar every other binding in this package
was held to.

`EventType` otherwise mirrors libei's enum in full, and any event type can be
*identified* and released safely whether or not it has an accessor.

Beyond sending input, the wrapper also covers ping/pong round trips
(`Context.new_ping()`), touch cancellation (`Touch.cancel()`), keymap transfer
(`Device.keymap`), region mapping ids and coordinate conversion,
`Context.disconnect()`, `Context.peek_event_type()`, and
`Seat.request_device()`. On the server side, `libei.eis` mirrors all of it and
adds `Eis.set_flag()` and `Client.pid`. Underneath, the ctypes layer binds 250
of the 302 functions the three libraries export as of 1.6.0; what is left out,
and why, is in
[docs/developers/architecture.md](docs/developers/architecture.md#what-is-bound-and-what-is-deliberately-not).

## Status

Beta (`0.5.1`), published on [PyPI](https://pypi.org/project/python-libei/)
since `0.1.0`, and **the API is not frozen** — expect renames before 1.0.

The injection path is exercised end to end against the real libraries by the
integration tests, and is in use as the Wayland input backend of a separate
GUI-automation project. Both portal paths can only ever be verified by hand,
since they need a consent dialog nothing can drive automatically; both have
been, most recently `libei.portal` against a real GNOME Wayland session on
2026-09-01. Development is against libei 1.6.0, and CI runs the suite against
Ubuntu's 1.2.1 so the 1.0.0 floor is exercised on a real old build rather than
asserted.

Exactly what was run, when, and against which versions:
[docs/developers/verification.md](docs/developers/verification.md).

## Alternatives

| Instead of this | Why you might |
| --- | --- |
| [snegg](https://gitlab.freedesktop.org/whot/snegg) | The reference bindings, by libei's own author — closer to upstream, and first to get new API. Self-described as for "rapid prototyping" with an explicitly unstable API, and `import snegg.ei` fails outright where libei isn't installed. [`docs/vs-snegg.md`](docs/vs-snegg.md) covers the differences in detail. |
| The RemoteDesktop portal's own D-Bus API directly (`NotifyPointerMotion`, `NotifyKeyboardKeycode`, …), bypassing libei/EI entirely | `libei.portal` already gets you the D-Bus session and its `persist_mode`/`restore_token` handling — reach past libei entirely only if you don't want the EI protocol at all. The catch if you do: `NotifyPointerMotionAbsolute` needs a PipeWire stream id, which only exists after a second, separate ScreenCast consent dialog. libei has no such requirement. |
| `ydotool` and other `/dev/uinput` tools | Kernel-level, so they work under any compositor and need no portal session — at the cost of a privileged daemon, and of sidestepping the consent model that EI exists to enforce. |

## Requirements

- Linux or FreeBSD with a Wayland compositor (GNOME, KDE, Sway, …).
  Nothing here is kernel-specific -- it is pure ctypes over the native
  libraries, with no syscall the C library doesn't already abstract. The
  portal paths are the part likeliest to come up short off Linux, since
  they need an xdg-desktop-portal RemoteDesktop backend to talk to.
- CPython 3.10 or newer (tested on 3.13)
- The native libraries: on Fedora, `sudo dnf install libei libeis liboeffis`;
  on FreeBSD, `pkg install libei` (the `x11/libei` port), which supplies all
  three sonames including `liboeffis`
- `libei.portal` only: PyGObject (`pip install 'python-libei[portal]'`), plus
  whatever GObject-introspection libraries your distro needs for `Gio` --
  PyPI's PyGObject wheel supplies the Python side only. Not needed for
  `libei.ei`, `libei.eis` or `libei.oeffis`.
- libei 1.0.0 or newer for the core: connecting, binding a seat, and
  sending pointer, button, keyboard, scroll and touch input all use symbols
  that have existed with a stable signature since 1.0.0, and upstream keeps
  API/ABI back-compatible within the 1.x series. Only 1.5.0 and 1.6.0 have
  actually been run against -- 1.6.0 on both Fedora 44 and FreeBSD 15, where
  the injection path passes the full suite with nothing skipped.

  Newer libei buys you more, per feature:

  | Needs | For |
  | --- | --- |
  | 1.1 | `Region.mapping_id`, `Region.convert_point()`, `Device.region_at()` |
  | 1.4 | ping/pong round trips (`Context.new_ping()`), `Context.disconnect()`, touch cancellation (`Touch.cancel()`) |
  | 1.5 | `eis.Client.pid` |
  | 1.6 | `Device.text_utf8()` / `text_keysym()` and TEXT events, `Seat.request_device()`, `Eis.set_flag()` |

  Nothing is resolved until it is called, so a build without one of these
  costs you only that call — it raises `LibraryNotFoundError` naming the
  missing symbol, while the rest of the package keeps working. The one
  exception is `Event.touch_up_event`, which degrades instead of raising:
  on libei older than 1.4 it reports `is_cancel=False`, since a library
  with no notion of cancellation genuinely never sends one.

  These versions were established by building libei 1.2.1 and diffing its
  exported symbols against every binding here, not by reading `@since`
  annotations — three of them are missing upstream, and taking their
  absence to mean 1.0 got touch cancellation wrong by four releases.

## Install

From [PyPI](https://pypi.org/project/python-libei/):

```sh
pip install python-libei
```

The distribution is named `python-libei`, the import is `libei` -- so
`pip show python-libei`, but `from libei import ei`.

Pure Python, no build step: the wheel is `py3-none-any` and ctypes talks to
the native libraries directly, so there is no compiler, no headers and no
`libei-devel` involved at install time. What `pip` does *not* bring is the
native libraries themselves -- see [Requirements](#requirements) above; on
Fedora, `sudo dnf install libei libeis liboeffis`.

To track `main` instead, or to hack on it, install from a checkout:

```sh
git clone https://github.com/ctrondlp/python-libei.git
cd python-libei
pip install .          # or `pip install -e '.[dev]'` to develop
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

**The dialog comes back every run** with `libei.oeffis`, which exposes no way
to persist an approval. If being prompted once per launch is unacceptable for
what you're building, use `libei.portal` instead —
[Avoiding the consent dialog on every run](docs/recipes.md#avoiding-the-consent-dialog-on-every-run).

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
several devices — see
[Picking the right device](docs/recipes.md#picking-the-right-device-when-several-resume)
before reusing this pattern.

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

# Press the A key (KEY_A -- a key *position*, not the character "a")
KEY_A = 30
device.start_emulating()
device.keyboard_key(KEY_A, True).frame()
device.keyboard_key(KEY_A, False).frame()
device.stop_emulating()

# Scroll: smooth (logical pixels) or discrete (one detent is 120)
device.start_emulating().scroll_delta(0, 20).frame().stop_emulating()
device.start_emulating().scroll_discrete(0, 120).frame().stop_emulating()
```

Keyboards need care, because a keycode is a key *position* and the character
it produces is the layout's business — `KEY_A = 30` types something else under
AZERTY. Absolute positioning needs the `POINTER_ABSOLUTE` capability and
coordinates inside one of `device.regions`, and binding it alongside `POINTER`
is what produces two devices where order cannot be trusted. Touch has its own
short-lived object rather than going through the device.

All four, with working code:
[docs/recipes.md](docs/recipes.md#pointer-buttons-and-scrolling).

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

Nearly all of these fail *silently*, which is why
[docs/troubleshooting.md](docs/troubleshooting.md) is a checklist rather than
a list of error messages. Start there when nothing happens.

## Documentation

- [docs/getting-started.md](docs/getting-started.md) — install through a first
  pointer motion, in five minutes
- [docs/recipes.md](docs/recipes.md) — keyboards, touch, absolute positioning,
  consent persistence, receiver mode, running your own EIS server, logging
- [docs/troubleshooting.md](docs/troubleshooting.md) — the "when nothing
  happens" checklist
- [docs/vs-snegg.md](docs/vs-snegg.md) — how this differs from the reference
  bindings, and two signature issues found by cross-checking the C source
- [docs/developers/](docs/developers/) — the four-layer architecture, and what
  has actually been verified against which libei versions
- [CONTRIBUTING.md](CONTRIBUTING.md) — setup, checks, testing against an old
  libei, releasing

## License

MIT.
