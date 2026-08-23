"""Shared fixtures.

Runbook §14.5: no test reaches a live model, a network or a real service. Every
database here is in memory and every client speaks to the app object through
ASGI, so the suite runs in seconds and means the same thing on any machine.
"""

from __future__ import annotations

import pytest

from sirvis.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Default settings, pinned to an in-memory database.

    Explicit rather than reading the environment: a test that picks up a
    developer's .env passes or fails for reasons the test does not state.
    """
    return Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
    )
