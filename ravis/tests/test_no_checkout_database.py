"""The suite never opens the checkout's own `ravis.db` (found 13 September 2026).

`Settings()` defaults to `database_path = "ravis.db"`, relative to the working directory. A full
suite run from `ravis/` built apps with defaults, opened the live RAVIS's database, and — once
migration 8 existed — migrated it past what the running RAVIS could read. `tests/conftest.py` now
points every default at an in-memory database before anything is collected, and fails the run if
it created the checkout's file. This holds both halves to it.
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import CHECKOUT_DATABASE

from ravis.config import Settings
from ravis.storage.database import resolved_path


def test_a_default_database_path_is_never_a_file_in_the_checkout_or_working_folder() -> None:
    default = Settings().database_path
    assert default == ":memory:"
    for place in (CHECKOUT_DATABASE, Path.cwd() / "ravis.db"):
        assert resolved_path(default) != str(place)
    # Even a `.env` file can't bring the relative default back while the suite runs.
    assert Settings(_env_file=None).database_path == ":memory:"  # type: ignore[call-arg]


def test_the_checkout_database_the_guard_watches_is_ravis_own() -> None:
    assert CHECKOUT_DATABASE.name == "ravis.db"
    assert (CHECKOUT_DATABASE.parent / "pyproject.toml").read_text().count('name = "ravis"') == 1
