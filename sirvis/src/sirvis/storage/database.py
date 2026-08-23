"""Opening the database, and moving it forward one migration at a time.

Migrations are forward-only and numbered. Each runs inside a transaction and
records itself, so an interrupted upgrade leaves the database at a known version
rather than half-way through one. The runbook (§13) requires a backup before a
migration and rollback proof for each; this module supplies the version number
that makes both checkable.

Two details here are not stylistic, and both were found the hard way in the
sibling service rather than reasoned about:

- **`sqlite3` refuses cross-thread use of a connection**, and FastAPI runs sync
  handlers in a threadpool. One connection per thread, created on first use.
- **`":memory:"` gives each *connection* a private database**, so a test that
  migrates on one connection and reads on another finds an empty schema. The
  shared-cache URI form is what makes an in-memory database one database.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path

# Each entry is (version, description, SQL). Append only — editing a migration
# that has already run somewhere means two databases claiming the same version
# with different shapes, which is unrecoverable without knowing which is which.
#
# The domain tables — machines, models, experiments, evidence — arrive with the
# milestones that own them (§17). M0 creates only what M0 can honestly say it
# needs, because a table with no writer is a schema claim nobody is keeping.
MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "schema baseline: applied-migration bookkeeping",
        """
        CREATE TABLE IF NOT EXISTS applied_migration (
            version     INTEGER PRIMARY KEY,
            description TEXT    NOT NULL,
            applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        2,
        "settings key/value, per SIRVIS.md §17",
        """
        CREATE TABLE IF NOT EXISTS setting (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
]


class Database:
    """A migrated database, reachable safely from any thread.

    One connection per thread, created on first use. Not premature caution:
    `sqlite3` refuses cross-thread use by default, and the alternative — one
    shared connection — fails the first time a sync handler runs in FastAPI's
    threadpool.
    """

    def __init__(self, path: str, version: int) -> None:
        self.path = path
        self.version = version
        self._local = threading.local()

    @property
    def connection(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        existing = getattr(self._local, "connection", None)
        if existing is None:
            existing = _connect(self.path)
            self._local.connection = existing
        return existing


def resolved_path(path: str) -> str:
    """The path to actually open, with in-memory databases made shareable.

    A bare `":memory:"` gives every connection its *own* empty database, which
    means the migration runs somewhere nothing else can see. The URI form with a
    shared cache and a unique name gives one in-memory database that several
    connections reach — and a fresh one per call, so two tests never collide.
    """
    if path == ":memory:":
        return f"file:sirvis-{uuid.uuid4().hex}?mode=memory&cache=shared"
    return path


def _connect(path: str) -> sqlite3.Connection:
    """Open one connection, with the pragmas this service depends on."""
    uri = path.startswith("file:")
    if not uri:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, uri=uri, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    # Write-ahead logging so a reader is never blocked by a writer. A benchmark
    # run writes for minutes at a time, and the dashboard polling during it must
    # not stall behind that.
    if not uri:
        connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def current_version(connection: sqlite3.Connection) -> int:
    """The highest migration this database has applied, or 0 for a new one.

    0 rather than an error for a database with no bookkeeping table: an unopened
    database is at version zero, which is a real answer rather than a failure.
    """
    try:
        row = connection.execute("SELECT MAX(version) AS version FROM applied_migration").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["version"]) if row and row["version"] is not None else 0


def prepare_database(path: str) -> Database:
    """Open the database and bring it to the latest migration."""
    resolved = resolved_path(path)
    connection = _connect(resolved)
    version = _apply_migrations(connection)
    return Database(path=resolved, version=version)


def _apply_migrations(connection: sqlite3.Connection) -> int:
    """Apply every migration newer than the current version, in order.

    Each one commits with its own bookkeeping row inside a single transaction,
    so a crash leaves the database at a version that is true rather than at a
    version that claims work it did not finish.
    """
    version = current_version(connection)
    for number, description, statements in MIGRATIONS:
        if number <= version:
            continue
        with connection:
            connection.executescript(statements)
            connection.execute(
                "INSERT INTO applied_migration (version, description) VALUES (?, ?)",
                (number, description),
            )
        version = number
    return version
