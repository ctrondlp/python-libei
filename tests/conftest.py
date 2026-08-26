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
