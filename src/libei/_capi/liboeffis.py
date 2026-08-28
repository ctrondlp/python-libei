"""ctypes bindings for liboeffis (portal RemoteDesktop -> EIS fd negotiation).

See https://libinput.pages.freedesktop.org/libei/api/group__liboeffis.html.

Names here drop the ``oeffis_`` prefix the C symbols carry:
``oeffis_unref()`` is bound as ``unref`` and called as
``_capi.liboeffis.unref()``. The module already says which library it is.
"""

from __future__ import annotations

from ctypes import c_char_p, c_int, c_uint32, c_void_p

from .loader import LazyLibrary

lib = LazyLibrary("liboeffis.so.1")

new = lib.function("oeffis_new", (c_void_p,), c_void_p)
ref = lib.function("oeffis_ref", (c_void_p,), c_void_p)
unref = lib.function("oeffis_unref", (c_void_p,), c_void_p)
get_fd = lib.function("oeffis_get_fd", (c_void_p,), c_int)
get_eis_fd = lib.function("oeffis_get_eis_fd", (c_void_p,), c_int)
create_session = lib.function("oeffis_create_session", (c_void_p, c_uint32), None)
create_session_on_bus = lib.function(
    "oeffis_create_session_on_bus", (c_void_p, c_char_p, c_uint32), None
)
dispatch = lib.function("oeffis_dispatch", (c_void_p,), None)
get_event = lib.function("oeffis_get_event", (c_void_p,), c_int)
get_error_message = lib.function("oeffis_get_error_message", (c_void_p,), c_char_p)
