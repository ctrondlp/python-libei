"""ctypes bindings for libei (the EI client library).

Signatures are hand-declared against the libei API documentation
(https://libinput.pages.freedesktop.org/libei/api/group__libei.html) rather
than parsed from C declaration strings, so a wrong argument count or type is
a visible diff here instead of a runtime surprise the first time the
function is actually called.

Names here drop the ``ei_`` prefix the C symbols carry:
``ei_unref()`` is bound as ``unref`` and called as
``_capi.libei.unref()``. The module already says which library it is.
"""

from __future__ import annotations

from ctypes import (
    CFUNCTYPE,
    POINTER,
    c_bool,
    c_char_p,
    c_double,
    c_int,
    c_int32,
    c_size_t,
    c_uint32,
    c_uint64,
    c_void_p,
)

from .loader import LazyLibrary

lib = LazyLibrary("libei.so.1")

log_handler_t = CFUNCTYPE(None, c_void_p, c_int, c_char_p, c_void_p)

log_set_handler = lib.function("ei_log_set_handler", (c_void_p, c_void_p), None)
log_set_priority = lib.function("ei_log_set_priority", (c_void_p, c_int), None)

new_sender = lib.function("ei_new_sender", (c_void_p,), c_void_p)
new_receiver = lib.function("ei_new_receiver", (c_void_p,), c_void_p)
unref = lib.function("ei_unref", (c_void_p,), c_void_p)
configure_name = lib.function("ei_configure_name", (c_void_p, c_char_p), None)
setup_backend_fd = lib.function("ei_setup_backend_fd", (c_void_p, c_int), c_int)
setup_backend_socket = lib.function(
    "ei_setup_backend_socket", (c_void_p, c_char_p), c_int
)
get_fd = lib.function("ei_get_fd", (c_void_p,), c_int)
dispatch = lib.function("ei_dispatch", (c_void_p,), None)
get_event = lib.function("ei_get_event", (c_void_p,), c_void_p)
now = lib.function("ei_now", (c_void_p,), c_uint64)
is_sender = lib.function("ei_is_sender", (c_void_p,), c_bool)
# Returns an owned reference that the caller must unref, and calling
# ei_get_event() while still holding it is undefined behavior -- see
# ei.Context.peek_event_type(), which never lets that reference escape.
peek_event = lib.function("ei_peek_event", (c_void_p,), c_void_p)
disconnect = lib.function("ei_disconnect", (c_void_p,), None)  # libei 1.4+

seat_get_name = lib.function("ei_seat_get_name", (c_void_p,), c_char_p)
seat_has_capability = lib.function("ei_seat_has_capability", (c_void_p, c_int), c_bool)
# ei_seat_bind_capabilities(seat, cap, cap, ..., 0) is variadic; ctypes
# converts the trailing plain-int args using default rules as long as they
# aren't listed in argtypes, so only the fixed prefix is declared here.
seat_bind_capabilities = lib.function("ei_seat_bind_capabilities", (c_void_p,), None)
seat_unbind_capabilities = lib.function(
    "ei_seat_unbind_capabilities", (c_void_p,), None
)
# Variadic and sentinel-terminated like ei_seat_bind_capabilities above.
seat_request_device_with_capabilities = lib.function(  # libei 1.6+
    "ei_seat_request_device_with_capabilities", (c_void_p,), None
)
seat_ref = lib.function("ei_seat_ref", (c_void_p,), c_void_p)
seat_unref = lib.function("ei_seat_unref", (c_void_p,), c_void_p)

event_unref = lib.function("ei_event_unref", (c_void_p,), c_void_p)
event_get_type = lib.function("ei_event_get_type", (c_void_p,), c_int)
event_get_seat = lib.function("ei_event_get_seat", (c_void_p,), c_void_p)
event_get_device = lib.function("ei_event_get_device", (c_void_p,), c_void_p)
event_get_time = lib.function("ei_event_get_time", (c_void_p,), c_uint64)
event_emulating_get_sequence = lib.function(
    "ei_event_emulating_get_sequence", (c_void_p,), c_uint32
)
event_keyboard_get_xkb_mods_depressed = lib.function(
    "ei_event_keyboard_get_xkb_mods_depressed", (c_void_p,), c_uint32
)
event_keyboard_get_xkb_mods_latched = lib.function(
    "ei_event_keyboard_get_xkb_mods_latched", (c_void_p,), c_uint32
)
event_keyboard_get_xkb_mods_locked = lib.function(
    "ei_event_keyboard_get_xkb_mods_locked", (c_void_p,), c_uint32
)
event_keyboard_get_xkb_group = lib.function(
    "ei_event_keyboard_get_xkb_group", (c_void_p,), c_uint32
)
event_keyboard_get_key = lib.function(
    "ei_event_keyboard_get_key", (c_void_p,), c_uint32
)
event_keyboard_get_key_is_press = lib.function(
    "ei_event_keyboard_get_key_is_press", (c_void_p,), c_bool
)
event_button_get_button = lib.function(
    "ei_event_button_get_button", (c_void_p,), c_uint32
)
event_button_get_is_press = lib.function(
    "ei_event_button_get_is_press", (c_void_p,), c_bool
)
event_pointer_get_dx = lib.function("ei_event_pointer_get_dx", (c_void_p,), c_double)
event_pointer_get_dy = lib.function("ei_event_pointer_get_dy", (c_void_p,), c_double)
event_pointer_get_absolute_x = lib.function(
    "ei_event_pointer_get_absolute_x", (c_void_p,), c_double
)
event_pointer_get_absolute_y = lib.function(
    "ei_event_pointer_get_absolute_y", (c_void_p,), c_double
)
event_scroll_get_dx = lib.function("ei_event_scroll_get_dx", (c_void_p,), c_double)
event_scroll_get_dy = lib.function("ei_event_scroll_get_dy", (c_void_p,), c_double)
event_scroll_get_stop_x = lib.function(
    "ei_event_scroll_get_stop_x", (c_void_p,), c_bool
)
event_scroll_get_stop_y = lib.function(
    "ei_event_scroll_get_stop_y", (c_void_p,), c_bool
)
event_scroll_get_discrete_dx = lib.function(
    "ei_event_scroll_get_discrete_dx", (c_void_p,), c_int32
)
event_scroll_get_discrete_dy = lib.function(
    "ei_event_scroll_get_discrete_dy", (c_void_p,), c_int32
)
event_touch_get_id = lib.function("ei_event_touch_get_id", (c_void_p,), c_uint32)
event_touch_get_x = lib.function("ei_event_touch_get_x", (c_void_p,), c_double)
event_touch_get_y = lib.function("ei_event_touch_get_y", (c_void_p,), c_double)
event_touch_get_is_cancel = lib.function(
    "ei_event_touch_get_is_cancel", (c_void_p,), c_bool
)
event_text_get_utf8 = lib.function(  # libei 1.6+
    "ei_event_text_get_utf8", (c_void_p,), c_char_p
)
event_text_get_keysym = lib.function(  # libei 1.6+
    "ei_event_text_get_keysym", (c_void_p,), c_uint32
)
event_text_get_keysym_is_press = lib.function(  # libei 1.6+
    "ei_event_text_get_keysym_is_press", (c_void_p,), c_bool
)
# Borrowed reference -- the event still owns it, so wrap() rather than adopt().
event_pong_get_ping = lib.function("ei_event_pong_get_ping", (c_void_p,), c_void_p)

device_ref = lib.function("ei_device_ref", (c_void_p,), c_void_p)
device_unref = lib.function("ei_device_unref", (c_void_p,), c_void_p)
device_get_seat = lib.function("ei_device_get_seat", (c_void_p,), c_void_p)
device_get_context = lib.function("ei_device_get_context", (c_void_p,), c_void_p)
device_get_name = lib.function("ei_device_get_name", (c_void_p,), c_char_p)
device_get_type = lib.function("ei_device_get_type", (c_void_p,), c_int)
device_get_width = lib.function("ei_device_get_width", (c_void_p,), c_uint32)
device_get_height = lib.function("ei_device_get_height", (c_void_p,), c_uint32)
device_has_capability = lib.function(
    "ei_device_has_capability", (c_void_p, c_int), c_bool
)
device_get_region = lib.function("ei_device_get_region", (c_void_p, c_size_t), c_void_p)
device_close = lib.function("ei_device_close", (c_void_p,), None)
device_start_emulating = lib.function(
    "ei_device_start_emulating", (c_void_p, c_uint32), None
)
device_stop_emulating = lib.function("ei_device_stop_emulating", (c_void_p,), None)
device_frame = lib.function("ei_device_frame", (c_void_p, c_uint64), None)
device_pointer_motion = lib.function(
    "ei_device_pointer_motion", (c_void_p, c_double, c_double), None
)
device_pointer_motion_absolute = lib.function(
    "ei_device_pointer_motion_absolute", (c_void_p, c_double, c_double), None
)
device_button_button = lib.function(
    "ei_device_button_button", (c_void_p, c_uint32, c_bool), None
)
device_scroll_delta = lib.function(
    "ei_device_scroll_delta", (c_void_p, c_double, c_double), None
)
device_scroll_discrete = lib.function(
    "ei_device_scroll_discrete", (c_void_p, c_int32, c_int32), None
)
device_scroll_stop = lib.function(
    "ei_device_scroll_stop", (c_void_p, c_bool, c_bool), None
)
device_scroll_cancel = lib.function(
    "ei_device_scroll_cancel", (c_void_p, c_bool, c_bool), None
)
device_keyboard_key = lib.function(
    "ei_device_keyboard_key", (c_void_p, c_uint32, c_bool), None
)
device_touch_new = lib.function("ei_device_touch_new", (c_void_p,), c_void_p)
device_keyboard_get_keymap = lib.function(
    "ei_device_keyboard_get_keymap", (c_void_p,), c_void_p
)
device_get_region_at = lib.function(  # libei 1.1+
    "ei_device_get_region_at", (c_void_p, c_double, c_double), c_void_p
)
# The _with_length form, not plain ei_device_text_utf8(): a c_char_p stops at
# the first NUL, so a Python str containing one would be silently truncated.
device_text_utf8_with_length = lib.function(  # libei 1.6+
    "ei_device_text_utf8_with_length", (c_void_p, c_char_p, c_size_t), None
)
device_text_keysym = lib.function(  # libei 1.6+
    "ei_device_text_keysym", (c_void_p, c_uint32, c_bool), None
)

region_ref = lib.function("ei_region_ref", (c_void_p,), c_void_p)
region_unref = lib.function("ei_region_unref", (c_void_p,), c_void_p)
region_get_x = lib.function("ei_region_get_x", (c_void_p,), c_uint32)
region_get_y = lib.function("ei_region_get_y", (c_void_p,), c_uint32)
region_get_width = lib.function("ei_region_get_width", (c_void_p,), c_uint32)
region_get_height = lib.function("ei_region_get_height", (c_void_p,), c_uint32)
region_get_physical_scale = lib.function(
    "ei_region_get_physical_scale", (c_void_p,), c_double
)
region_contains = lib.function(
    "ei_region_contains", (c_void_p, c_double, c_double), c_bool
)
region_get_mapping_id = lib.function(  # libei 1.1+
    "ei_region_get_mapping_id", (c_void_p,), c_char_p
)
# x and y are in/out parameters, overwritten only when the point is inside.
region_convert_point = lib.function(  # libei 1.1+
    "ei_region_convert_point",
    (c_void_p, POINTER(c_double), POINTER(c_double)),
    c_bool,
)

keymap_ref = lib.function("ei_keymap_ref", (c_void_p,), c_void_p)
keymap_unref = lib.function("ei_keymap_unref", (c_void_p,), c_void_p)
keymap_get_type = lib.function("ei_keymap_get_type", (c_void_p,), c_int)
keymap_get_size = lib.function("ei_keymap_get_size", (c_void_p,), c_size_t)
keymap_get_fd = lib.function("ei_keymap_get_fd", (c_void_p,), c_int)
keymap_get_device = lib.function("ei_keymap_get_device", (c_void_p,), c_void_p)

touch_ref = lib.function("ei_touch_ref", (c_void_p,), c_void_p)
touch_unref = lib.function("ei_touch_unref", (c_void_p,), c_void_p)
touch_get_device = lib.function("ei_touch_get_device", (c_void_p,), c_void_p)
touch_down = lib.function("ei_touch_down", (c_void_p, c_double, c_double), None)
touch_motion = lib.function("ei_touch_motion", (c_void_p, c_double, c_double), None)
touch_up = lib.function("ei_touch_up", (c_void_p,), None)
touch_cancel = lib.function("ei_touch_cancel", (c_void_p,), None)

# ei_new_ping() returns an owned reference; ei_ping() then triggers the round
# trip that comes back as an EI_EVENT_PONG.
new_ping = lib.function("ei_new_ping", (c_void_p,), c_void_p)  # libei 1.4+
ping = lib.function("ei_ping", (c_void_p,), None)  # libei 1.4+
ping_get_id = lib.function("ei_ping_get_id", (c_void_p,), c_uint64)  # libei 1.4+
ping_ref = lib.function("ei_ping_ref", (c_void_p,), c_void_p)  # libei 1.4+
ping_unref = lib.function("ei_ping_unref", (c_void_p,), c_void_p)  # libei 1.4+
