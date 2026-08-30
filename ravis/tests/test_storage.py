"""Migrations are forward-only, idempotent, and reachable from any thread."""

from __future__ import annotations

import sqlite3
import threading

from ravis.storage import prepare_database
from ravis.storage import database as module
from ravis.storage.database import MIGRATIONS, current_version


def test_migrating_reaches_the_latest_version() -> None:
    database = prepare_database(":memory:")

    assert database.schema_version == MIGRATIONS[-1][0]


def test_each_in_memory_database_is_isolated() -> None:
    """Two callers asking for ":memory:" must not silently share one database.

    A fixed shared-cache name would couple every test in the process, which is
    worse than the private-per-connection default it was introduced to fix.
    """
    first = prepare_database(":memory:")
    second = prepare_database(":memory:")

    first.connection.execute("INSERT INTO setting (key, value) VALUES ('a', 'b')")
    rows = second.connection.execute("SELECT COUNT(*) AS n FROM setting").fetchone()["n"]

    assert rows == 0


def test_migrating_twice_changes_nothing(tmp_path) -> None:  # noqa: ANN001
    """Idempotence is the normal case: every start runs this."""
    path = str(tmp_path / "ravis.db")
    first = prepare_database(path).schema_version

    assert prepare_database(path).schema_version == first


def test_a_fresh_database_reports_version_zero(tmp_path) -> None:  # noqa: ANN001
    """Zero, not None — absence of migrations is a number, not a missing value."""
    from ravis.storage.database import _connect

    connection = _connect(str(tmp_path / "empty.db"))

    assert current_version(connection) == 0


def test_the_database_is_reachable_from_another_thread(tmp_path) -> None:  # noqa: ANN001
    """The bug the health check caught on its first run.

    `sqlite3` refuses cross-thread use of a connection, and FastAPI runs
    synchronous handlers on a threadpool — so a connection opened at startup is
    used from a different thread than the one that created it. This asserts the
    per-thread connection actually works rather than trusting that it does.
    """
    database = prepare_database(str(tmp_path / "ravis.db"))
    results: list[int] = []

    def read_from_another_thread() -> None:
        results.append(database.connection.execute("SELECT 1").fetchone()[0])

    worker = threading.Thread(target=read_from_another_thread)
    worker.start()
    worker.join()

    assert results == [1]


def test_foreign_keys_are_enforced(tmp_path) -> None:  # noqa: ANN001
    """SQLite leaves them off by default, which makes a constraint a comment."""
    database = prepare_database(str(tmp_path / "ravis.db"))

    assert database.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


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
    assert prepare_database(str(path)).schema_version == behind
    assert not list(tmp_path.glob("*.bak")), "a database being created has nothing to protect"

    monkeypatch.setattr(module, "MIGRATIONS", full)
    assert prepare_database(str(path)).schema_version == full[-1][0]

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
    assert prepare_database(":memory:").schema_version == MIGRATIONS[-1][0]
