"""Shared pytest fixtures."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def database_url() -> str | None:
    """URL of a disposable test database; None if integration is disabled."""
    if os.environ.get("FOFOCA_INTEGRATION_DB") != "true":
        return None
    return os.environ.get("FOFOCA_TEST_DATABASE_URL")
