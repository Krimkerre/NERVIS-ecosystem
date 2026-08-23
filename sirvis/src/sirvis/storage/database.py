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
  shared-cache URI form is what makes an in-memory database one database — and
  it then needs an *anchor*, because a shared-cache in-memory database is
  destroyed when its last connection closes. Both halves are required; having
  only the first presents as `no such table` from an unrelated line.
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
    (
        3,
        "machine identity and immutable snapshots, per SIRVIS.md §5.1",
        """
        CREATE TABLE IF NOT EXISTS machine (
            machine_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- Snapshots are append-only and never updated. §5.1 requires every
        -- benchmark to reference an *immutable* snapshot: if the row could be
        -- edited, the machine's description could drift away from the
        -- conditions a result was measured under, which is the whole reason
        -- the reference exists. The payload is stored whole rather than as
        -- columns so that a field added at M14 does not need a migration to
        -- appear in results captured before it.
        CREATE TABLE IF NOT EXISTS machine_snapshot (
            snapshot_id TEXT PRIMARY KEY,
            machine_id  TEXT NOT NULL REFERENCES machine(machine_id),
            captured_at TEXT NOT NULL DEFAULT (datetime('now')),
            payload     TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS machine_snapshot_by_machine
            ON machine_snapshot (machine_id, captured_at DESC);
        """,
    ),
    (
        4,
        "API tokens and their scopes, per SIRVIS.md §4.5",
        """
        -- Only the hash is stored. A token is a credential, and a credential a
        -- database can hand back is one a database leak hands out — so the
        -- plaintext exists exactly once, at the moment it is minted, and is
        -- never recoverable afterwards. Losing one means minting another.
        CREATE TABLE IF NOT EXISTS api_token (
            token_hash TEXT PRIMARY KEY,
            label      TEXT NOT NULL,
            scopes     TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_used  TEXT
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

    def __init__(self, path: str, version: int,
                 anchor: sqlite3.Connection | None = None) -> None:
        self.path = path
        self.version = version
        # An in-memory database exists only while a connection to it is open.
        # The migrating connection is kept here as that anchor: without it the
        # schema is applied, the connection is garbage-collected, the database
        # evaporates, and the next thread opens an empty one — which presents
        # as `no such table: setting` from a completely unrelated line.
        self._anchor = anchor
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
    # Only an in-memory database needs the anchor; a file survives on its own.
    anchor = connection if resolved != path else None
    return Database(path=resolved, version=version, anchor=anchor)


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
