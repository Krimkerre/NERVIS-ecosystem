"""Migrations are forward-only, idempotent, and reachable from any thread."""

from __future__ import annotations

import threading

from ravis.storage import prepare_database
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
