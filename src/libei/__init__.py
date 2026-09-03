"""Python bindings for libei, libeis and liboeffis (Wayland Emulated Input).

Move the pointer, click, type, scroll or touch on a Wayland desktop from
Python -- the automation, remote-desktop and accessibility jobs that used
``xdotool`` on X11 -- or implement the compositor side of the same protocol.

This package is import-safe even when none of the native libraries are
installed -- loading is deferred to first use (see ``_capi/loader.py``).
Use the submodules directly:

- :mod:`libei.ei` -- the EI client library (inject or receive input)
- :mod:`libei.eis` -- the EIS server library (implement a compositor, or
  drive a test harness for the ``ei`` module without a real compositor)
- :mod:`libei.oeffis` -- negotiate an EI connection through the
  ``org.freedesktop.portal.RemoteDesktop`` XDG desktop portal
- :mod:`libei.portal` -- negotiate that same portal directly over D-Bus
  instead, for ``persist_mode``/``restore_token`` support liboeffis's C API
  doesn't expose

Scope, in short: this needs a compositor speaking EI/EIS -- there is no X11
fallback. Pointer (relative and absolute), button, keyboard, scroll, touch
and text are driveable; the gesture and stylus capabilities are present in
the enums for a future libei -- they exist on upstream's main branch but in
no released version -- and have no send methods or event accessors. Keyboard input is
evdev keycodes -- key positions, not characters -- though ``text_utf8()``
sends characters directly where libei 1.6 is available. See the README for
the full breakdown, including which features need which libei version.

Beta: the API is not frozen.
"""

__version__ = "0.4.0"

__all__ = ["__version__"]
