"""Shared fixtures for all tests."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Return a temporary SQLite database path."""
    return str(tmp_path / "test.db")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep tests independent from a deployment .env file in the repo root."""
    for key in (
        "BOT_TOKEN",
        "USER_API_ID",
        "USER_API_HASH",
        "APP_ENV",
        "WEB_HOST",
        "WEB_PORT",
        "WEB_CORS_ORIGINS",
        "WEB_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
