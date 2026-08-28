"""ctypes bindings for libeis (the EIS server library).

See https://libinput.pages.freedesktop.org/libei/api/group__libeis.html
(mirror: https://gitlab.freedesktop.org/libinput/libei/-/blob/main/src/libeis.h).

``eis_setup_backend_fd(ctx)`` and ``eis_backend_fd_add_client(ctx)`` both
take *only* the context -- there is no fd parameter. The latter *returns* a
new fd (to be handed to a client's ``ei_setup_backend_fd(ei, fd)``); it
doesn't accept one. This is easy to get backwards from the name alone --
see docs/vs-snegg.md for how that showed up in practice while this was
being worked out.

Names here drop the ``eis_`` prefix the C symbols carry:
``eis_unref()`` is bound as ``unref`` and called as
``_capi.libeis.unref()``. The module already says which library it is.
"""

from __future__ import annotations

from ctypes import (
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

lib = LazyLibrary("libeis.so.1")

log_set_handler = lib.function("eis_log_set_handler", (c_void_p, c_void_p), None)
log_set_priority = lib.function("eis_log_set_priority", (c_void_p, c_int), None)

new = lib.function("eis_new", (c_void_p,), c_void_p)
unref = lib.function("eis_unref", (c_void_p,), c_void_p)
setup_backend_socket = lib.function(
    "eis_setup_backend_socket", (c_void_p, c_char_p), c_int
)
setup_backend_fd = lib.function("eis_setup_backend_fd", (c_void_p,), c_int)
# Returns a new fd for one client connection (or a negative errno); it does
# not take one. See module docstring.
backend_fd_add_client = lib.function("eis_backend_fd_add_client", (c_void_p,), c_int)
get_fd = lib.function("eis_get_fd", (c_void_p,), c_int)
dispatch = lib.function("eis_dispatch", (c_void_p,), None)
# Same contract as ei_peek_event(): an owned reference the caller must
# unref, and calling eis_get_event() while holding it is undefined
# behavior -- see eis.Eis.peek_event_type().
peek_event = lib.function("eis_peek_event", (c_void_p,), c_void_p)
set_flag = lib.function("eis_set_flag", (c_void_p, c_int), c_int)  # libei 1.6+
get_event = lib.function("eis_get_event", (c_void_p,), c_void_p)
now = lib.function("eis_now", (c_void_p,), c_uint64)

event_unref = lib.function("eis_event_unref", (c_void_p,), c_void_p)
event_get_type = lib.function("eis_event_get_type", (c_void_p,), c_int)
event_get_client = lib.function("eis_event_get_client", (c_void_p,), c_void_p)
event_get_seat = lib.function("eis_event_get_seat", (c_void_p,), c_void_p)
event_get_device = lib.function("eis_event_get_device", (c_void_p,), c_void_p)
event_get_time = lib.function("eis_event_get_time", (c_void_p,), c_uint64)
event_seat_has_capability = lib.function(
    "eis_event_seat_has_capability", (c_void_p, c_int), c_bool
)
event_emulating_get_sequence = lib.function(
    "eis_event_emulating_get_sequence", (c_void_p,), c_uint32
)
event_keyboard_get_key = lib.function(
    "eis_event_keyboard_get_key", (c_void_p,), c_uint32
)
event_keyboard_get_key_is_press = lib.function(
    "eis_event_keyboard_get_key_is_press", (c_void_p,), c_bool
)
event_button_get_button = lib.function(
    "eis_event_button_get_button", (c_void_p,), c_uint32
)
event_button_get_is_press = lib.function(
    "eis_event_button_get_is_press", (c_void_p,), c_bool
)
event_pointer_get_dx = lib.function("eis_event_pointer_get_dx", (c_void_p,), c_double)
event_pointer_get_dy = lib.function("eis_event_pointer_get_dy", (c_void_p,), c_double)
event_pointer_get_absolute_x = lib.function(
    "eis_event_pointer_get_absolute_x", (c_void_p,), c_double
)
event_pointer_get_absolute_y = lib.function(
    "eis_event_pointer_get_absolute_y", (c_void_p,), c_double
)
event_scroll_get_dx = lib.function("eis_event_scroll_get_dx", (c_void_p,), c_double)
event_scroll_get_dy = lib.function("eis_event_scroll_get_dy", (c_void_p,), c_double)
event_scroll_get_stop_x = lib.function(
    "eis_event_scroll_get_stop_x", (c_void_p,), c_bool
)
event_scroll_get_stop_y = lib.function(
    "eis_event_scroll_get_stop_y", (c_void_p,), c_bool
)
event_scroll_get_discrete_dx = lib.function(
    "eis_event_scroll_get_discrete_dx", (c_void_p,), c_int32
)
event_scroll_get_discrete_dy = lib.function(
    "eis_event_scroll_get_discrete_dy", (c_void_p,), c_int32
)
event_touch_get_id = lib.function("eis_event_touch_get_id", (c_void_p,), c_uint32)
event_touch_get_x = lib.function("eis_event_touch_get_x", (c_void_p,), c_double)
event_touch_get_y = lib.function("eis_event_touch_get_y", (c_void_p,), c_double)
event_touch_get_is_cancel = lib.function(
    "eis_event_touch_get_is_cancel", (c_void_p,), c_bool
)
event_text_get_utf8 = lib.function(  # libei 1.6+
    "eis_event_text_get_utf8", (c_void_p,), c_char_p
)
event_text_get_keysym = lib.function(  # libei 1.6+
    "eis_event_text_get_keysym", (c_void_p,), c_uint32
)
event_text_get_keysym_is_press = lib.function(  # libei 1.6+
    "eis_event_text_get_keysym_is_press", (c_void_p,), c_bool
)
# Borrowed reference -- the event still owns it, so wrap() rather than adopt().
event_pong_get_ping = lib.function("eis_event_pong_get_ping", (c_void_p,), c_void_p)

client_ref = lib.function("eis_client_ref", (c_void_p,), c_void_p)
client_unref = lib.function("eis_client_unref", (c_void_p,), c_void_p)
client_is_sender = lib.function("eis_client_is_sender", (c_void_p,), c_bool)
# pid_t, i.e. a 32-bit signed int on Linux. Socket backend only, and
# negative errno on failure.
backend_socket_get_client_pid = lib.function(
    "eis_backend_socket_get_client_pid", (c_void_p,), c_int32
)
client_get_name = lib.function("eis_client_get_name", (c_void_p,), c_char_p)
client_connect = lib.function("eis_client_connect", (c_void_p,), None)
client_disconnect = lib.function("eis_client_disconnect", (c_void_p,), None)
client_new_seat = lib.function("eis_client_new_seat", (c_void_p, c_char_p), c_void_p)
# Owned reference; eis_ping() then triggers the round trip that comes back
# as an EIS_EVENT_PONG.
client_new_ping = lib.function(  # libei 1.4+
    "eis_client_new_ping", (c_void_p,), c_void_p
)

seat_ref = lib.function("eis_seat_ref", (c_void_p,), c_void_p)
seat_unref = lib.function("eis_seat_unref", (c_void_p,), c_void_p)
seat_get_name = lib.function("eis_seat_get_name", (c_void_p,), c_char_p)
seat_get_client = lib.function("eis_seat_get_client", (c_void_p,), c_void_p)
seat_has_capability = lib.function("eis_seat_has_capability", (c_void_p, c_int), c_bool)
seat_configure_capability = lib.function(
    "eis_seat_configure_capability", (c_void_p, c_int), None
)
seat_add = lib.function("eis_seat_add", (c_void_p,), None)
seat_remove = lib.function("eis_seat_remove", (c_void_p,), None)
seat_new_device = lib.function("eis_seat_new_device", (c_void_p,), c_void_p)

device_ref = lib.function("eis_device_ref", (c_void_p,), c_void_p)
device_unref = lib.function("eis_device_unref", (c_void_p,), c_void_p)
device_get_context = lib.function("eis_device_get_context", (c_void_p,), c_void_p)
device_get_client = lib.function("eis_device_get_client", (c_void_p,), c_void_p)
device_get_seat = lib.function("eis_device_get_seat", (c_void_p,), c_void_p)
device_get_name = lib.function("eis_device_get_name", (c_void_p,), c_char_p)
device_get_type = lib.function("eis_device_get_type", (c_void_p,), c_int)
device_get_width = lib.function("eis_device_get_width", (c_void_p,), c_uint32)
device_get_height = lib.function("eis_device_get_height", (c_void_p,), c_uint32)
device_has_capability = lib.function(
    "eis_device_has_capability", (c_void_p, c_int), c_bool
)
device_get_region = lib.function(
    "eis_device_get_region", (c_void_p, c_size_t), c_void_p
)
device_configure_name = lib.function(
    "eis_device_configure_name", (c_void_p, c_char_p), None
)
device_configure_type = lib.function(
    "eis_device_configure_type", (c_void_p, c_int), None
)
device_configure_capability = lib.function(
    "eis_device_configure_capability", (c_void_p, c_int), None
)
device_configure_size = lib.function(
    "eis_device_configure_size", (c_void_p, c_uint32, c_uint32), None
)
device_new_region = lib.function("eis_device_new_region", (c_void_p,), c_void_p)
device_new_keymap = lib.function(
    "eis_device_new_keymap", (c_void_p, c_int, c_int, c_size_t), c_void_p
)
device_keyboard_get_keymap = lib.function(
    "eis_device_keyboard_get_keymap", (c_void_p,), c_void_p
)
device_keyboard_send_xkb_modifiers = lib.function(
    "eis_device_keyboard_send_xkb_modifiers",
    (c_void_p, c_uint32, c_uint32, c_uint32, c_uint32),
    None,
)
device_add = lib.function("eis_device_add", (c_void_p,), None)
device_remove = lib.function("eis_device_remove", (c_void_p,), None)
device_pause = lib.function("eis_device_pause", (c_void_p,), None)
device_resume = lib.function("eis_device_resume", (c_void_p,), None)
device_start_emulating = lib.function(
    "eis_device_start_emulating", (c_void_p, c_uint32), None
)
device_stop_emulating = lib.function("eis_device_stop_emulating", (c_void_p,), None)
device_frame = lib.function("eis_device_frame", (c_void_p, c_uint64), None)
device_pointer_motion = lib.function(
    "eis_device_pointer_motion", (c_void_p, c_double, c_double), None
)
device_pointer_motion_absolute = lib.function(
    "eis_device_pointer_motion_absolute", (c_void_p, c_double, c_double), None
)
device_button_button = lib.function(
    "eis_device_button_button", (c_void_p, c_uint32, c_bool), None
)
device_scroll_delta = lib.function(
    "eis_device_scroll_delta", (c_void_p, c_double, c_double), None
)
device_scroll_discrete = lib.function(
    "eis_device_scroll_discrete", (c_void_p, c_int32, c_int32), None
)
device_scroll_stop = lib.function(
    "eis_device_scroll_stop", (c_void_p, c_bool, c_bool), None
)
device_scroll_cancel = lib.function(
    "eis_device_scroll_cancel", (c_void_p, c_bool, c_bool), None
)
device_keyboard_key = lib.function(
    "eis_device_keyboard_key", (c_void_p, c_uint32, c_bool), None
)
device_touch_new = lib.function("eis_device_touch_new", (c_void_p,), c_void_p)
device_get_region_at = lib.function(  # libei 1.1+
    "eis_device_get_region_at", (c_void_p, c_double, c_double), c_void_p
)
# The _with_length form, not plain eis_device_text_utf8(): a c_char_p stops at
# the first NUL, so a Python str containing one would be silently truncated.
device_text_utf8_with_length = lib.function(  # libei 1.6+
    "eis_device_text_utf8_with_length", (c_void_p, c_char_p, c_size_t), None
)
device_text_keysym = lib.function(  # libei 1.6+
    "eis_device_text_keysym", (c_void_p, c_uint32, c_bool), None
)

region_ref = lib.function("eis_region_ref", (c_void_p,), c_void_p)
region_unref = lib.function("eis_region_unref", (c_void_p,), c_void_p)
region_get_x = lib.function("eis_region_get_x", (c_void_p,), c_uint32)
region_get_y = lib.function("eis_region_get_y", (c_void_p,), c_uint32)
region_get_width = lib.function("eis_region_get_width", (c_void_p,), c_uint32)
region_get_height = lib.function("eis_region_get_height", (c_void_p,), c_uint32)
region_get_physical_scale = lib.function(
    "eis_region_get_physical_scale", (c_void_p,), c_double
)
region_set_size = lib.function(
    "eis_region_set_size", (c_void_p, c_uint32, c_uint32), None
)
region_set_offset = lib.function(
    "eis_region_set_offset", (c_void_p, c_uint32, c_uint32), None
)
region_set_physical_scale = lib.function(
    "eis_region_set_physical_scale", (c_void_p, c_double), None
)
region_add = lib.function("eis_region_add", (c_void_p,), None)
region_contains = lib.function(
    "eis_region_contains", (c_void_p, c_double, c_double), c_bool
)
region_get_mapping_id = lib.function(  # libei 1.1+
    "eis_region_get_mapping_id", (c_void_p,), c_char_p
)
region_set_mapping_id = lib.function(  # libei 1.1+
    "eis_region_set_mapping_id", (c_void_p, c_char_p), None
)

keymap_ref = lib.function("eis_keymap_ref", (c_void_p,), c_void_p)
keymap_unref = lib.function("eis_keymap_unref", (c_void_p,), c_void_p)
keymap_add = lib.function("eis_keymap_add", (c_void_p,), None)
keymap_get_type = lib.function("eis_keymap_get_type", (c_void_p,), c_int)
keymap_get_size = lib.function("eis_keymap_get_size", (c_void_p,), c_size_t)
keymap_get_fd = lib.function("eis_keymap_get_fd", (c_void_p,), c_int)
keymap_get_device = lib.function("eis_keymap_get_device", (c_void_p,), c_void_p)

touch_ref = lib.function("eis_touch_ref", (c_void_p,), c_void_p)
touch_unref = lib.function("eis_touch_unref", (c_void_p,), c_void_p)
touch_get_device = lib.function("eis_touch_get_device", (c_void_p,), c_void_p)
touch_down = lib.function("eis_touch_down", (c_void_p, c_double, c_double), None)
touch_motion = lib.function("eis_touch_motion", (c_void_p, c_double, c_double), None)
touch_up = lib.function("eis_touch_up", (c_void_p,), None)
touch_cancel = lib.function("eis_touch_cancel", (c_void_p,), None)

ping = lib.function("eis_ping", (c_void_p,), None)  # libei 1.4+
ping_get_id = lib.function("eis_ping_get_id", (c_void_p,), c_uint64)  # libei 1.4+
ping_ref = lib.function("eis_ping_ref", (c_void_p,), c_void_p)  # libei 1.4+
ping_unref = lib.function("eis_ping_unref", (c_void_p,), c_void_p)  # libei 1.4+
