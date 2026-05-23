"""Shared fixtures for all tests."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Return a temporary SQLite database path."""
    return str(tmp_path / "test.db")
