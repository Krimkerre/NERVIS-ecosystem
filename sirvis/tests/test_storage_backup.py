"""Runbook §13's pre-migration backup, for this package's own copy of the
migration runner. Three packages ship three runners on purpose — they are
independently deployable — so the guarantee is tested three times rather
than assumed to hold from one.
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from sirvis.storage import database as module
from sirvis.storage import prepare_database
from sirvis.storage.database import MIGRATIONS, current_version


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


def test_restoring_by_copying_the_file_silently_returns_the_wrong_data(tmp_path) -> None:
    """The reason `restore_backup` exists, pinned as a measurement rather than a
    warning in a docstring.

    In WAL mode the committed rows can still live in the `-wal` sidecar. Copying
    a backup over the database leaves that sidecar in place, and SQLite replays
    it over the restored file — so the operator is handed back exactly the state
    they were rolling away from, with no error anywhere. A restore that appears
    to work and does not is worse than one that fails.
    """
    database = tmp_path / "under-test.db"
    live = module._connect(str(database))
    live.execute("CREATE TABLE k (v TEXT)")
    live.execute("INSERT INTO k VALUES ('before')")
    live.commit()

    backup = tmp_path / "under-test.db.v1.bak"
    with sqlite3.connect(backup) as copy:
        live.backup(copy)

    live.execute("UPDATE k SET v = 'after'")
    live.commit()

    shutil.copyfile(backup, database)
    live.close()

    reopened = module._connect(str(database))
    assert reopened.execute("SELECT v FROM k").fetchone()[0] == "after"


def test_restore_backup_puts_the_old_state_back(tmp_path) -> None:
    """The same setup, through the function: SQLite's own backup API writes
    through the journal the database uses, so the stale WAL cannot outlive it."""
    database = tmp_path / "under-test.db"
    live = module._connect(str(database))
    live.execute("CREATE TABLE k (v TEXT)")
    live.execute("INSERT INTO k VALUES ('before')")
    live.commit()

    backup = tmp_path / "under-test.db.v1.bak"
    with sqlite3.connect(backup) as copy:
        live.backup(copy)

    live.execute("UPDATE k SET v = 'after'")
    live.commit()
    live.close()

    assert module.restore_backup(database) == 1
    reopened = module._connect(str(database))
    assert reopened.execute("SELECT v FROM k").fetchone()[0] == "before"


def test_restore_takes_the_newest_backup_and_can_be_told_otherwise(tmp_path) -> None:
    """Two upgrades in a row leave two backups. The default is the newest, which
    is what an interrupted upgrade wants; naming a version reaches further back.
    Ordered by the version they restore to rather than by mtime — a file's
    timestamp says when it was written, not how far back it takes you."""
    database = tmp_path / "under-test.db"
    live = module._connect(str(database))
    live.execute("CREATE TABLE k (v TEXT)")
    live.commit()

    for version, value in ((3, "three"), (7, "seven")):
        live.execute("DELETE FROM k")
        live.execute("INSERT INTO k VALUES (?)", (value,))
        live.commit()
        with sqlite3.connect(tmp_path / f"under-test.db.v{version}.bak") as copy:
            live.backup(copy)
    live.close()

    assert [number for number, _ in module.available_backups(database)] == [7, 3]
    assert module.restore_backup(database) == 7
    assert module._connect(str(database)).execute("SELECT v FROM k").fetchone()[0] == "seven"
    assert module.restore_backup(database, 3) == 3
    assert module._connect(str(database)).execute("SELECT v FROM k").fetchone()[0] == "three"


def test_a_restore_with_nothing_to_restore_refuses(tmp_path) -> None:
    """A restore that silently does nothing is the same class of failure as the
    file copy above: the operator believes they rolled back and did not."""
    database = tmp_path / "under-test.db"
    prepare_database(str(database))

    with pytest.raises(FileNotFoundError):
        module.restore_backup(database)

    with sqlite3.connect(tmp_path / "under-test.db.v2.bak") as copy:
        module._connect(str(database)).backup(copy)
    # The version that exists is named in the refusal, because "no backup at
    # version 5" without saying what there *is* sends somebody to `ls`.
    with pytest.raises(FileNotFoundError, match="have 2"):
        module.restore_backup(database, 5)


def test_a_database_from_a_newer_build_is_refused_rather_than_used(tmp_path) -> None:
    """Runbook §13: never downgrade across an incompatible migration without a
    restore.

    Migrations are forward-only, so an older build opening a newer database
    applies nothing and carries on. Measured before the guard existed: it opened
    without complaint against two migrations it had never seen, then read and
    wrote a schema it was wrong about. Nothing surfaces until the data is mixed.
    """
    database = tmp_path / "under-test.db"
    prepare_database(str(database))
    known = MIGRATIONS[-1][0]

    connection = module._connect(str(database))
    for ahead in (known + 1, known + 2):
        connection.execute(
            "INSERT INTO applied_migration (version, description) VALUES (?, ?)",
            (ahead, "written by a newer build"),
        )
    connection.commit()
    connection.close()

    with pytest.raises(module.DatabaseTooNewError) as refusal:
        prepare_database(str(database))

    said = str(refusal.value)
    # The message has to carry both numbers and the way out. "Incompatible
    # schema" alone sends somebody to read source at the moment they can least
    # afford to.
    assert str(known + 2) in said and str(known) in said
    assert "restore-database" in said


def test_a_database_at_the_build_s_own_version_is_not_refused(tmp_path) -> None:
    """The guard must not fire on the ordinary case, which is every start."""
    database = tmp_path / "under-test.db"
    prepare_database(str(database))
    prepare_database(str(database))


def _version_of(database) -> int:
    """The schema version, whichever name this package's Database gives it."""
    return getattr(database, "schema_version", None) or database.version


def test_every_intermediate_version_migrates_to_latest(tmp_path) -> None:
    """Runbook §13 asks each migration for forward proof.

    Every other test builds a fresh database, which applies the whole list in
    order — so the chain is only ever exercised from zero. Nothing covered the
    case that actually happens to an operator: a database sitting at version 4
    when a build carrying version 5 arrives. A migration that assumes the schema
    it was written against, or that is appended out of order, passes the suite
    and fails on the machine.

    Every starting point rather than the newest one. The newest is the case
    somebody just tested by hand; the old ones are the databases nobody has
    opened in months, which is exactly where an upgrade breaks.

    **What this does not cover, stated so it is not mistaken for coverage.** The
    databases here are empty, so it catches ordering and idempotence faults and
    not data-dependent ones — a `CREATE UNIQUE INDEX` over a column with
    duplicate rows succeeds against no rows and fails against real ones. Proving
    that needs representative data per migration, which is a per-migration job
    rather than a generic one. The live databases are migrated by hand at release
    time for the same reason.
    """
    full = list(module.MIGRATIONS)
    latest = full[-1][0]

    for step in range(1, len(full)):
        started_at = full[step - 1][0]
        database = tmp_path / f"from-v{started_at}.db"

        original = module.MIGRATIONS
        try:
            module.MIGRATIONS = full[:step]
            assert _version_of(prepare_database(str(database))) == started_at
            module.MIGRATIONS = full
            reached = _version_of(prepare_database(str(database)))
        finally:
            module.MIGRATIONS = original

        assert reached == latest, (
            f"a database at version {started_at} reached {reached}, not {latest}"
        )
        # The upgrade left a backup at the version it started from — the one an
        # operator wants when the upgrade is the thing that went wrong.
        assert (tmp_path / f"from-v{started_at}.db.v{started_at}.bak").exists()
