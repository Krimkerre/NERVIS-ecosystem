"""Opening the database, and moving it forward one migration at a time.

Migrations are forward-only and numbered. Each runs inside a transaction and
records itself, so an interrupted upgrade leaves the database at a known version
rather than half-way through one.

**Deliberately not SQLAlchemy and not Alembic**, which NERVIS.md §3 lists among
its suggested tools. Both sibling services already migrate this way with the
standard library, the schema here is a handful of tables NERVIS owns outright,
and adding an ORM plus a migration framework to a third service to describe them
would buy nothing the other two found they needed. Stated rather than done
quietly, because the specification does say the words and a reader is entitled
to know this was a decision.

Two details here are not stylistic. Both were found the hard way in the sibling
services and are copied for exactly that reason:

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
# §14 lists the entities NERVIS will own: Service, ServiceInstance,
# HealthSnapshot, Event, Trace, TraceSpan, ChatConversation, ChatMessage,
# WorkspaceReference, DiagnosticAnalysis, Setting. They arrive with the
# milestones that own them. M0 creates only what M0 can honestly say it needs,
# because a table with no writer is a schema claim nobody is keeping.
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
        "settings key/value, per NERVIS.md §14",
        """
        CREATE TABLE IF NOT EXISTS setting (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
    (
        3,
        "this installation's own identity, per runbook §4.1",
        # `service_id` and `machine_id` are generated once and then stable
        # across restarts, which is what makes them worth publishing: an
        # identity regenerated each start tells a peer nothing it can correlate.
        # A single-row table with a fixed key rather than a column on some other
        # table, because nothing else at M0 has a row to hang it from.
        """
        CREATE TABLE IF NOT EXISTS installation (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
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

    def __init__(
        self, path: str, version: int, anchor: sqlite3.Connection | None = None
    ) -> None:
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
        return f"file:nervis-{uuid.uuid4().hex}?mode=memory&cache=shared"
    return path


def _connect(path: str) -> sqlite3.Connection:
    """Open one connection, with the pragmas this service depends on."""
    uri = path.startswith("file:")
    if not uri:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, uri=uri, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    # Write-ahead logging so a reader is never blocked by a writer. The event
    # hub will write continuously from M6 while the dashboard polls, and that
    # is exactly the shape WAL exists for.
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
        row = connection.execute(
            "SELECT MAX(version) AS version FROM applied_migration"
        ).fetchone()
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


def installation_identity(database: Database) -> tuple[str, str]:
    """This installation's `service_id` and `machine_id`, generated once.

    §4.1 requires `machine_id` to be *locally generated, opaque,
    non-hardware-derived and resettable* — never a hostname, a serial number, a
    MAC address or anything reversible. A random UUID satisfies every clause,
    and resetting it is deleting the row.

    Stable across restarts, which is the whole point: a peer correlating two
    reports from this machine needs the same answer both times, and `uuid4()` in
    a constructor would give it a different one per process. That is what
    `instance_id` is for, and the protocol package generates that separately.
    """
    stored = dict(
        (row["key"], row["value"])
        for row in database.connection.execute("SELECT key, value FROM installation")
    )
    generated = {
        key: stored.get(key) or uuid.uuid4().hex
        for key in ("service_id", "machine_id")
    }
    with database.connection as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO installation (key, value) VALUES (?, ?)",
            list(generated.items()),
        )
    return generated["service_id"], generated["machine_id"]
