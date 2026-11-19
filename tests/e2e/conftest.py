"""Pytest fixtures for multi-mode seasonal MOFU editorial E2E suites."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from tests.e2e.editorial_helpers import create_isolated_modes_db


@pytest.fixture
def isolated_modes_e2e_db() -> Generator[str, None, None]:
    """Yield a blank migrated temporary database for multi-mode E2E tests."""
    with create_isolated_modes_db() as tmp_url:
        yield tmp_url
