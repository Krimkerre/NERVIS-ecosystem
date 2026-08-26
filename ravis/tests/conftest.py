"""Shared fixtures.

Runbook §14.5: no test reaches a live model, a network or a real service. Every
database here is in-memory and every client speaks to the app object directly
through ASGI, so the suite runs in seconds and its results mean the same thing
on any machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ravis.config import Settings


@pytest.fixture(autouse=True)
def _never_the_operators_own_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every on-disk store at a throwaway directory, for every test.

    This file already promised it: *"its results mean the same thing on any
    machine"*. Four stores broke that promise by resolving their own paths at
    construction — credentials, provider enable/disable, model filters and pool
    membership all land in `~/.config/ravis`, and `create_app` builds all four.
    So the suite read whatever the developer's dashboard had been clicking.

    It was not theoretical. Narrowing `ravis/clarvis-chat` to four models in the
    picker made `test_a_pool_id_is_resolved_before_forwarding` fail, because the
    fake upstream in that test serves none of the four — a green suite turned
    red from a UI interaction in another process, with nothing in the diff.

    `config_directory` reads `XDG_CONFIG_HOME` precisely so a test never touches
    a real home directory. The mechanism was there; nothing used it globally.
    Autouse and function-scoped, so tests cannot leak state into each other
    either.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture
def settings() -> Settings:
    """Default settings, pinned to an in-memory database.

    Explicit rather than relying on the environment: a test that reads the
    developer's .env passes or fails for reasons the test does not state.
    """
    return Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
    )
