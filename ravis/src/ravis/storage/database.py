"""Opening the database, and moving it forward one migration at a time.

Migrations are forward-only and numbered. Each runs inside a transaction and
records itself, so an interrupted upgrade leaves the database at a known version
rather than half-way through one. The runbook (§13) requires a backup before a
migration and rollback proof for each; this module supplies the version number
that makes both checkable.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path

# Each entry is (version, description, SQL). Append only — editing a migration
# that has already run somewhere means two databases claiming the same version
# with different shapes, which is unrecoverable without knowing which is which.
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
        "client applications, per RAVIS.md §17",
        """
        CREATE TABLE IF NOT EXISTS client_application (
            application_id TEXT PRIMARY KEY,
            label          TEXT NOT NULL,
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        3,
        "settings key/value, per RAVIS.md §17",
        """
        CREATE TABLE IF NOT EXISTS setting (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
    (
        4,
        "routing sessions, per RAVIS.md §12.1 and §17",
        """
        CREATE TABLE IF NOT EXISTS routing_session (
            -- The composite of application and client-supplied ID. Primary key
            -- rather than `session_id`, which is what makes §12.1's isolation
            -- structural: two applications calling their session `main` cannot
            -- collide, because they are two different rows.
            session_key    TEXT PRIMARY KEY,
            session_id     TEXT NOT NULL,
            application_id TEXT NOT NULL,
            pool           TEXT NOT NULL DEFAULT '',
            model          TEXT NOT NULL DEFAULT '',
            provider       TEXT NOT NULL DEFAULT '',
            pool_revision  TEXT NOT NULL DEFAULT '',
            profile        TEXT NOT NULL DEFAULT '',
            -- Nullable on purpose: no adapter reports a prompt cache yet, and a
            -- NOT NULL default would record "no cache" where the truth is "never
            -- asked". Nothing here holds prompt or response content (§12.1).
            cache_state    TEXT,
            created_at     REAL NOT NULL,
            last_activity  REAL NOT NULL
        );
        -- Retention sweeps and the per-application listing both order by this.
        CREATE INDEX IF NOT EXISTS routing_session_activity
            ON routing_session (application_id, last_activity DESC);
        """,
    ),
]


class Database:
    """A migrated database, reachable safely from any thread.

    One connection per thread, created on first use. This is not premature
    caution: `sqlite3` refuses cross-thread use of a connection by default, and
    FastAPI runs synchronous handlers on a threadpool — so a single shared
    connection opened at startup fails the moment a request touches it from a
    worker thread. The health check found exactly that on its first run.

    A connection per thread rather than a lock around one connection: SQLite
    handles concurrent readers itself, and a lock would serialise every health
    poll behind whatever else was running.
    """

    def __init__(
        self, path: str, schema_version: int, anchor: sqlite3.Connection | None = None
    ) -> None:
        self.path = path
        self.schema_version = schema_version
        self._local = threading.local()
        # An in-memory database normally lives inside a single connection and
        # vanishes when it closes, so a second thread opening one would get a
        # different, empty database. The anchor connection holds the shared-cache
        # database open for as long as this object exists; every thread then
        # opens its own connection to the same one.
        self._anchor = anchor

    @property
    def connection(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use.

        Per thread rather than shared, because `sqlite3` refuses cross-thread use
        of a connection and FastAPI runs synchronous handlers on a threadpool.
        """
        existing = getattr(self._local, "connection", None)
        if existing is None:
            existing = _connect(self.path)
            self._local.connection = existing
        return existing


def resolved_path(path: str) -> str:
    """Translate ":memory:" into a uniquely-named shared-cache URI.

    Two problems, one solution. Plain ":memory:" gives every *connection* a
    private database, so a second thread opening one finds it empty — and a
    fixed shared name goes too far the other way, handing every caller in the
    process the same database, which silently couples tests that believe they
    are isolated. A fresh name per call gives each Database its own, reachable
    from every thread.
    """
    if path != ":memory:":
        return path
    return f"file:ravis-memory-{uuid.uuid4().hex}?mode=memory&cache=shared"


def _connect(path: str) -> sqlite3.Connection:
    """Open one connection and set the pragmas this service depends on.

    WAL because a reader must not block while a write is in flight — a health
    check that stalls behind a migration reports the service down when it is
    merely busy. Foreign keys because SQLite leaves them off by default, which
    silently turns a declared constraint into a comment.
    """
    if not path.startswith("file:") and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread stays at its default: each thread gets its own
    # connection above, so the guard is a correctness check rather than an
    # obstacle. Turning it off would hide the very bug this design fixes.
    connection = sqlite3.connect(path, isolation_level=None, uri=path.startswith("file:"))
    connection.row_factory = sqlite3.Row
    if "mode=memory" not in path:
        # WAL lets a reader proceed while a write is in flight, so a health poll
        # does not stall behind a migration. It does not apply in memory.
        connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def current_version(connection: sqlite3.Connection) -> int:
    """The highest migration this database has applied, or 0 if none have.

    Returns 0 rather than None for a fresh database: zero is the honest answer
    ("no migrations applied"), and it keeps every caller from special-casing
    absence (runbook §14.4 — null never stands in for empty).
    """
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'applied_migration'"
    ).fetchone()
    if tables is None:
        return 0
    row = connection.execute("SELECT MAX(version) AS version FROM applied_migration").fetchone()
    return int(row["version"]) if row["version"] is not None else 0


def prepare_database(path: str) -> Database:
    """Open the database, bring it to the latest schema, and return it ready to use.

    One entry point rather than separate open-then-migrate steps, because the two
    have to agree on the effective path and an in-memory database gets a freshly
    generated one. Splitting them made that agreement the caller's problem, which
    is how a test ended up sharing a database it believed was its own.
    """
    effective = resolved_path(path)
    connection = _connect(effective)
    version = _apply_migrations(connection)
    # For an in-memory database the migrating connection is kept as an anchor, so
    # the schema just applied survives until this object is discarded.
    anchor = connection if effective != path else None
    return Database(path=effective, schema_version=version, anchor=anchor)


def _apply_migrations(connection: sqlite3.Connection) -> int:
    """Apply every migration this database has not yet seen, returning its version.

    Idempotent: running it against an up-to-date database applies nothing and is
    the normal case on every start. Each migration commits with its own
    bookkeeping row in one transaction, so a crash between two migrations leaves
    a database at a real version rather than an invented one.
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
