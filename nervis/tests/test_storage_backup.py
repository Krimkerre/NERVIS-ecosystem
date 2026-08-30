"""Runbook §13's pre-migration backup, for this package's own copy of the
migration runner. Three packages ship three runners on purpose — they are
independently deployable — so the guarantee is tested three times rather
than assumed to hold from one.
"""

from __future__ import annotations

import sqlite3

from nervis.storage import prepare_database
from nervis.storage import database as module
from nervis.storage.database import MIGRATIONS, current_version


def test_a_migration_backs_the_database_up_before_it_runs(tmp_path, monkeypatch) -> None:
    """Runbook §13: back up databases before migrations.

    This module's own docstring cited that rule and then supplied only the
    version number, calling it the thing "that makes both checkable" — which was
    true, and was not a backup. Migrations ran against live data with no prior
    state kept anywhere.

    Built by opening the database against a truncated migration list, so it is
    genuinely one version behind, and then opening it again against the real one.
    Deleting a bookkeeping row would not do: migrations are not idempotent, and
    re-running the last one fails on its own ALTER TABLE rather than exercising
    anything.
    """
    full = list(module.MIGRATIONS)
    behind = full[-2][0]

    monkeypatch.setattr(module, "MIGRATIONS", full[:-1])
    path = tmp_path / "under-test.db"
    assert prepare_database(str(path)).version == behind
    assert not list(tmp_path.glob("*.bak")), "a database being created has nothing to protect"

    monkeypatch.setattr(module, "MIGRATIONS", full)
    assert prepare_database(str(path)).version == full[-1][0]

    backup = path.with_name(f"{path.name}.v{behind}.bak")
    assert backup.exists(), f"expected {backup.name}, found {[p.name for p in tmp_path.iterdir()]}"
    # The copy holds the state *before* the migration, which is the only thing
    # that makes it worth keeping. Taken afterwards it would be a second copy of
    # the database you already have.
    # The module's own opener, not a bare `sqlite3.connect`: `current_version`
    # reads a named column and needs the row factory `_connect` installs.
    assert current_version(module._connect(str(backup))) == behind


def test_an_up_to_date_database_is_not_backed_up_on_every_start(tmp_path) -> None:
    """The normal case is a database with nothing pending, on every launch.
    Copying it each time would grow the disk for no benefit, and would eventually
    overwrite the one backup that mattered with a post-migration state."""
    path = tmp_path / "under-test.db"
    prepare_database(str(path))
    prepare_database(str(path))

    assert [p.name for p in tmp_path.glob("*.bak")] == []


def test_an_in_memory_database_has_nothing_to_back_up() -> None:
    """No file to write beside, and no prior state to lose. It must not throw."""
    assert prepare_database(":memory:").version == MIGRATIONS[-1][0]
