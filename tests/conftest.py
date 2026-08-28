"""Shared fixtures.

Tests are split into two tiers:

- Unit tests (most of the suite) never touch the real native libraries --
  they exercise the Python object model against fakes/mocks, so they run
  identically on a machine with libei installed and one without.
- Integration tests (marked ``@pytest.mark.integration``) drive the real
  ``libei.so.1``/``libeis.so.1`` and are skipped automatically when those
  aren't installed, rather than failing the whole suite in an environment
  that simply doesn't have them.
"""

from __future__ import annotations

import pytest

from libei import ei as ei_mod
from libei import eis as eis_mod


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "integration: requires libei/libeis to be installed"
    )


requires_libei = pytest.mark.skipif(
    not (ei_mod.is_available() and eis_mod.is_available()),
    reason="libei.so.1 and/or libeis.so.1 not installed",
)


def requires_symbol(soname: str, symbol: str) -> pytest.MarkDecorator:
    """Skip unless the installed native library exports ``symbol``.

    Version-gated features (libei 1.1/1.4/1.6) are the reason this exists.
    Catching ``LibraryNotFoundError`` around the call is not enough on its
    own: a capability the installed library has never heard of -- ``TEXT``
    on anything before 1.6 -- is accepted by ``configure_capabilities()``
    and then silently produces no device, so a test waiting on one hangs
    until its timeout and fails rather than skipping. Checking the symbol
    up front decides before any negotiation starts.
    """
    import ctypes

    try:
        present = hasattr(ctypes.CDLL(soname), symbol)
    except OSError:
        present = False
    return pytest.mark.skipif(
        not present, reason=f"{soname} does not export {symbol} (libei too old)"
    )
