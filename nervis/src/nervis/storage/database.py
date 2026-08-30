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
    (
        4,
        "chat conversations and messages, per NERVIS.md §7.2",
        # §7.2's list, and nothing beyond it: conversation ID, title,
        # timestamps, messages, RAVIS route IDs. Local only by default.
        #
        # `route_decision_id` and `request_id` are what make §7.1's inspector a
        # lookup rather than a re-derivation — RAVIS already recorded why it
        # chose what it chose, and storing the handle beats storing a copy that
        # can disagree with it.
        #
        # ON DELETE CASCADE because §7.2 requires deletion to work, and a
        # conversation whose messages outlive it is not deleted, it is hidden.
        """
        CREATE TABLE IF NOT EXISTS chat_conversation (
            conversation_id TEXT PRIMARY KEY,
            title           TEXT NOT NULL DEFAULT '',
            profile         TEXT NOT NULL DEFAULT '',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS chat_message (
            message_id        TEXT PRIMARY KEY,
            conversation_id   TEXT NOT NULL
                REFERENCES chat_conversation(conversation_id) ON DELETE CASCADE,
            role              TEXT NOT NULL,
            content           TEXT NOT NULL,
            model             TEXT NOT NULL DEFAULT '',
            profile           TEXT NOT NULL DEFAULT '',
            request_id        TEXT NOT NULL DEFAULT '',
            route_decision_id TEXT NOT NULL DEFAULT '',
            interrupted       INTEGER NOT NULL DEFAULT 0,
            created_at        TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS chat_message_by_conversation
            ON chat_message(conversation_id, created_at);
        """,
    ),
    (
        5,
        "the event hub, per NERVIS.md §11.1 and the runbook's §4.4 envelope",
        # `sequence` is the primary key and `event_id` is merely unique, which
        # is the other way round from the obvious choice. Two things need it:
        # §4.4's deduplication, which `INSERT OR IGNORE` against the UNIQUE
        # constraint gives for free, and §11.1's **replay cursor**, which needs
        # a monotonic value a client can resume from. `rowid` cannot be indexed
        # explicitly and is not a contract; an explicit sequence is both.
        #
        # `received_at` is separate from `occurred_at` and both are kept. A
        # producer's clock is its own, and §11.2 requires clock skew to be
        # visible rather than smoothed away — one timestamp cannot show it.
        #
        # `envelope` holds the whole event as it arrived. The columns beside it
        # exist to filter on; they are a projection, not the record. §11.1 is
        # explicit that the hub is operational telemetry and never the system of
        # record, so re-serialising from columns would be NERVIS inventing a
        # version of somebody else's fact.
        """
        CREATE TABLE IF NOT EXISTS event (
            sequence     INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id     TEXT NOT NULL UNIQUE,
            event_type   TEXT NOT NULL,
            service_type TEXT NOT NULL DEFAULT '',
            severity     TEXT NOT NULL DEFAULT 'info',
            trace_id     TEXT NOT NULL DEFAULT '',
            request_id   TEXT NOT NULL DEFAULT '',
            session_id   TEXT NOT NULL DEFAULT '',
            occurred_at  TEXT NOT NULL,
            received_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            envelope     TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS event_by_arrival ON event(received_at);
        CREATE INDEX IF NOT EXISTS event_by_trace   ON event(trace_id);
        CREATE INDEX IF NOT EXISTS event_by_type    ON event(event_type);

        -- §11.1: malformed events are quarantined with safe diagnostics and
        -- never crash the hub. Kept rather than dropped, because "the hub is
        -- silent" and "a producer is sending rubbish" look identical from the
        -- outside and need different things done about them.
        CREATE TABLE IF NOT EXISTS event_quarantine (
            quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reason        TEXT NOT NULL,
            detail        TEXT NOT NULL DEFAULT '',
            payload       TEXT NOT NULL,
            received_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );
        """,
    ),
    (
        6,
        "named voice profiles for spoken output, per NERVIS.md §18.2",
        # Profiles, not the credential. The Fish Audio key lives in a `0600`
        # file in the user's config directory (`nervis.voice`), because this
        # database is created in the working directory with whatever mode the
        # umask allows — fine for configuration, wrong for a secret.
        #
        # `speed` is Fish's `prosody.speed` and is per voice rather than global:
        # the right speed is a property of the voice, not of the listener.
        """
        CREATE TABLE IF NOT EXISTS voice_profile (
            profile_id TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            voice_id   TEXT NOT NULL,
            engine     TEXT NOT NULL DEFAULT 's2.1-pro-free',
            speed      REAL NOT NULL DEFAULT 1.0,
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
    version = _apply_migrations(connection, Path(resolved))
    _refuse_a_newer_database(Path(resolved), version, "nervis")
    # Only an in-memory database needs the anchor; a file survives on its own.
    anchor = connection if resolved != path else None
    return Database(path=resolved, version=version, anchor=anchor)


# Where a pre-migration backup goes, and what it is called. Next to the database
# and named for the version it restores *to*, so the file says what it is worth:
# `nervis.db.v7.bak` is a database at version 7, which is the thing you want when
# a migration to 8 went wrong.
#
# Deterministic rather than timestamped: re-running the same upgrade overwrites
# its own backup instead of leaving a directory of near-identical files nobody
# can choose between. A timestamp would preserve every attempt, and the one that
# matters is the last state before the migration that is running now.
def _backup_path(database: Path, version: int) -> Path:
    return database.with_name(f"{database.name}.v{version}.bak")


def _back_up_before_migrating(connection: sqlite3.Connection, database: Path,
                              version: int) -> Path | None:
    """Copy the database before a migration touches it (runbook §13).

    **Only when something is about to change.** An up-to-date database is the
    normal case on every start, and backing up there would rewrite a file on
    every launch for no benefit.

    `sqlite3.Connection.backup` rather than copying the file: it is the online
    backup API, it takes a read lock for the duration, and it produces one
    consistent file even in WAL mode — where the bytes on disk are split across
    `-wal` and `-shm` and a plain copy can miss committed transactions that have
    not yet been checkpointed.

    A failure here stops the migration rather than being logged and stepped over.
    The whole point of the backup is that the next statement is destructive, and
    proceeding without one would leave the operator in exactly the position §13
    exists to prevent — with the guarantee's cost paid and none of its benefit.
    """
    if version == 0 or not str(database) or database.name == ":memory:":
        # Nothing to protect: a database being created has no prior state, and an
        # in-memory one has no file to write beside.
        return None
    target = _backup_path(database, version)
    with sqlite3.connect(target) as copy:
        connection.backup(copy)
    return target


class DatabaseIsNewerThanThisBuild(RuntimeError):
    """Raised when the database has migrations this build has never heard of.

    Its own type rather than a bare `RuntimeError` so the traceback's last line
    names the condition — that line is what an operator reads, and "RuntimeError"
    is not a diagnosis.
    """


def _refuse_a_newer_database(database: Path, version: int, service: str) -> None:
    """Stop before a downgrade corrupts what it does not understand (§13).

    Migrations are forward-only, so an older build opening a newer database
    applies nothing and carries on — measured, and it opened without complaint
    against two migrations it had never seen. It then reads and writes a schema
    whose shape it is wrong about: a renamed column reads as absent, a widened
    one is written narrow, and nothing surfaces until the data is already mixed.

    §13 says never to downgrade across an incompatible migration *without a
    restore*, which is exactly the instruction this carries — and the restore now
    exists, so the message names the command rather than the principle.

    Refusing to start is the right failure. The alternative is a service that
    runs and quietly damages evidence the same runbook calls immutable.
    """
    known = MIGRATIONS[-1][0]
    if version <= known:
        return
    raise DatabaseIsNewerThanThisBuild(
        f"{database} was written by a newer build (schema {version}); "
        f"this one understands {known}. Run the newer {service}, or "
        f"`{service} restore-database --version {known}` to go back — "
        f"which discards anything the newer build recorded."
    )


def available_backups(database: Path) -> list[tuple[int, Path]]:
    """Every backup beside this database, newest version first.

    Sorted by the version they restore *to*, not by modification time: a file's
    mtime says when it was written, and after two upgrades in a row that is not
    the same ordering as "how far back does this take me".
    """
    found: list[tuple[int, Path]] = []
    for candidate in database.parent.glob(f"{database.name}.v*.bak"):
        tail = candidate.name[len(database.name) + 2 : -4]
        if tail.isdigit():
            found.append((int(tail), candidate))
    return sorted(found, reverse=True)


def restore_backup(database: Path, version: int | None = None) -> int:
    """Put a backup back, and return the version the database is left at.

    **Through SQLite rather than by copying the file, and this is the whole
    point of the function.** Copying a backup over the database appears to work
    and does not: in WAL mode the live `-wal` survives the copy and replays over
    the restored file, so the operator is handed back the state they were trying
    to roll away from, with no error anywhere. Measured, not feared — a naive
    copy in this exact shape returned the post-migration value.

    The backup API writes through the same journal the database uses, so the
    stale WAL cannot outlive the restore. `version=None` takes the newest
    backup, which is the one an interrupted upgrade wants.

    Refuses rather than guesses when there is nothing to restore. A restore that
    silently does nothing is the same failure as the copy above.
    """
    backups = available_backups(database)
    if not backups:
        raise FileNotFoundError(f"no backup beside {database}")
    if version is None:
        restored, source = backups[0]
    else:
        match = [(number, path) for number, path in backups if number == version]
        if not match:
            offer = ", ".join(str(number) for number, _ in backups)
            raise FileNotFoundError(f"no backup at version {version} beside {database}; have {offer}")
        restored, source = match[0]

    origin = sqlite3.connect(source)
    target = sqlite3.connect(database)
    try:
        origin.backup(target)
    finally:
        target.close()
        origin.close()
    return restored


def _apply_migrations(connection: sqlite3.Connection,
                      database: Path | None = None) -> int:
    """Apply every migration newer than the current version, in order.

    Each one commits with its own bookkeeping row inside a single transaction,
    so a crash leaves the database at a version that is true rather than at a
    version that claims work it did not finish.
    """
    version = current_version(connection)
    pending = [entry for entry in MIGRATIONS if entry[0] > version]
    if pending and database is not None:
        # Before the first one, not before each: the backup's value is the state
        # the operator started from, and rewriting it between migrations would
        # replace that with a half-upgraded database.
        _back_up_before_migrating(connection, database, version)
    for number, description, statements in pending:
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
