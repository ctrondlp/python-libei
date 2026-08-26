"""Pythonic wrapper around liboeffis -- negotiates an EIS connection through
the ``org.freedesktop.portal.RemoteDesktop`` XDG desktop portal.

This is the path a sandboxed or otherwise non-privileged client uses to get
an EI socket: it asks the portal, the user is shown a consent dialog, and on
approval this hands back a file descriptor to pass to
:meth:`libei.ei.Sender.create_for_fd`.

    oeffis = Oeffis.create(devices=DeviceType.POINTER)
    while True:
        ready, _, _ = select.select([oeffis.fd], [], [], timeout)
        if not ready:
            continue
        if oeffis.dispatch():
            break
    sender = ei.Sender.create_for_fd(oeffis.eis_fd, name="my-app")

Note: in live testing this path has been the least reliable part of the
underlying libraries (see the project README) -- treat failures here as
possibly environment-specific, not necessarily a bug in this wrapper.
"""

from __future__ import annotations

import enum
import logging
import os

from . import _capi

logger = logging.getLogger("libei.oeffis")


def is_available() -> bool:
    """Whether liboeffis.so.1 can be loaded on this system."""
    return _capi.liboeffis.lib.is_available()


class DisconnectedError(Exception):
    """The portal session ended unexpectedly (error, or denied by the user)."""

    def __init__(self, message: str | None) -> None:
        super().__init__(message)
        self.message = message


class SessionClosedError(DisconnectedError):
    """The portal explicitly closed the session (not necessarily an error)."""

    def __init__(self) -> None:
        super().__init__(message="Session closed")


class DeviceType(enum.IntFlag):
    ALL_DEVICES = 0
    KEYBOARD = 1
    POINTER = 2
    TOUCHSCREEN = 4


class _EventType(enum.IntEnum):
    NONE = 0
    CONNECTED_TO_EIS = 1
    CLOSED = 2
    DISCONNECTED = 3


class Oeffis:
    """Wraps a liboeffis context for one portal session.

    Must be kept alive for the duration of the session -- destroying it
    closes the session and invalidates ``eis_fd`` for any :mod:`libei.ei`
    context still using it.
    """

    def __init__(self) -> None:
        pointer = _capi.liboeffis.new(None)
        if not pointer:
            raise DisconnectedError("oeffis_new() returned NULL")
        self._pointer = pointer
        self._eis_fd: int | None = None
        # Set the first (and only the first) time the `eis_fd` property is
        # read -- reading it hands the fd to the caller (typically to pass
        # straight to Sender.create_for_fd(), which takes ownership and
        # closes it itself), so __del__ must not also close it once that's
        # happened. But oeffis_get_eis_fd() docs say the caller owns the
        # dup()'d fd it returns, and if the session dies (or this object is
        # just dropped) before anyone ever reads `eis_fd`, nothing else
        # will ever close it -- __del__ closes it itself in that case.
        self._eis_fd_claimed = False
        self._state = _EventType.NONE

    def __del__(self) -> None:
        eis_fd = getattr(self, "_eis_fd", None)
        if eis_fd is not None and not getattr(self, "_eis_fd_claimed", True):
            os.close(eis_fd)
        pointer = getattr(self, "_pointer", None)
        if pointer:
            _capi.liboeffis.unref(pointer)

    @property
    def fd(self) -> int:
        """Poll this fd; call :meth:`dispatch` whenever it's readable."""
        return _capi.liboeffis.get_fd(self._pointer)

    @property
    def eis_fd(self) -> int:
        """The fd to pass to :meth:`libei.ei.Sender.create_for_fd`.

        Raises :class:`DisconnectedError` if accessed before
        :meth:`dispatch` has returned ``True``.
        """
        if self._state != _EventType.CONNECTED_TO_EIS:
            raise DisconnectedError(self.error_message)
        assert self._eis_fd is not None
        self._eis_fd_claimed = True
        return self._eis_fd

    def dispatch(self) -> bool:
        """Process pending events; return True once connected to EIS.

        Raises :class:`DisconnectedError` or :class:`SessionClosedError` if
        the session ended; further calls after that keep raising the same
        exception rather than silently doing nothing.
        """
        if self._state == _EventType.CLOSED:
            raise SessionClosedError()
        if self._state == _EventType.DISCONNECTED:
            raise DisconnectedError(self.error_message)

        _capi.liboeffis.dispatch(self._pointer)
        while True:
            raw_event = _capi.liboeffis.get_event(self._pointer)
            try:
                event = _EventType(raw_event)
            except ValueError:
                # Same contract as ei/eis EventType: an event value this
                # table doesn't know about must not crash the caller. Skip
                # it and keep draining rather than raising out of dispatch.
                logger.debug("ignoring unknown oeffis event type %d", raw_event)
                continue
            if event == _EventType.NONE:
                return False
            if event == _EventType.CONNECTED_TO_EIS:
                eis_fd = _capi.liboeffis.get_eis_fd(self._pointer)
                if eis_fd < 0:
                    # Documented as "-1 on failure or before the fd was
                    # retrieved". Treating that as a live fd would hand -1
                    # to ei_setup_backend_fd() and fail far from the cause.
                    self._state = _EventType.DISCONNECTED
                    raise DisconnectedError(
                        self.error_message
                        or "oeffis_get_eis_fd() failed after CONNECTED_TO_EIS"
                    )
                self._eis_fd = eis_fd
                self._state = _EventType.CONNECTED_TO_EIS
                return True
            if event == _EventType.DISCONNECTED:
                self._state = _EventType.DISCONNECTED
                raise DisconnectedError(self.error_message)
            if event == _EventType.CLOSED:
                self._state = _EventType.CLOSED
                raise SessionClosedError()

    @property
    def error_message(self) -> str | None:
        """The last error liboeffis reported, or ``None`` if it has none.

        Populated when a session disconnects or fails; a healthy session
        normally reports nothing, but ``None`` only ever means "no message
        available", not "no error occurred".
        """
        message = _capi.liboeffis.get_error_message(self._pointer)
        return message.decode("utf-8") if message else None

    @classmethod
    def create(
        cls,
        devices: DeviceType = DeviceType.ALL_DEVICES,
        busname: str = "org.freedesktop.portal.Desktop",
    ) -> Oeffis:
        """Start a RemoteDesktop portal session request.

        Returns immediately -- the portal typically prompts the user for
        consent, so poll :attr:`fd` and call :meth:`dispatch` until it
        returns ``True`` before reading :attr:`eis_fd`.
        """
        session = cls()
        _capi.liboeffis.create_session_on_bus(
            session._pointer, busname.encode("utf-8"), devices
        )
        return session


__all__ = [
    "DeviceType",
    "DisconnectedError",
    "Oeffis",
    "SessionClosedError",
    "is_available",
]
