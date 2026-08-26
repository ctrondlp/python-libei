"""Python bindings for libei, libeis and liboeffis (Wayland Emulated Input).

This package is import-safe even when none of the native libraries are
installed -- loading is deferred to first use (see ``_capi/loader.py``).
Use the submodules directly:

- :mod:`libei.ei` -- the EI client library (inject or receive input)
- :mod:`libei.eis` -- the EIS server library (implement a compositor, or
  drive a test harness for the ``ei`` module without a real compositor)
- :mod:`libei.oeffis` -- negotiate an EI connection through the
  ``org.freedesktop.portal.RemoteDesktop`` XDG desktop portal
"""

__version__ = "0.1.0.dev0"

__all__ = ["__version__"]
