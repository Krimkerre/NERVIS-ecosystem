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
    (
        5,
        "session request count, for RAVIS.md §12.2's load-versus-don't tradeoff",
        """
        -- How many requests this session has made. §12.1's field list does not
        -- include it and §12.2 requires it: the tradeoff between using a loaded
        -- model and loading a better one turns on "expected session length",
        -- and this is the only honest source of that — an observation rather
        -- than a prediction. A count is metadata, not content, so §12.1's rule
        -- that a session implies no stored prompts or responses is untouched.
        --
        -- Appended as its own migration rather than folded into 4, which has
        -- already run: editing a migration that shipped means two databases
        -- claiming one version with different shapes.
        ALTER TABLE routing_session ADD COLUMN requests INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        6,
        "capability trials: RAVIS's own tool-call test of hosted models (§13.3)",
        """
        -- One row per model and capability RAVIS has tried itself, the provenance
        -- §13.3 calls OBSERVED_BY_RAVIS. Hosted models only: a trial against a local
        -- runtime would load a model, which RAVIS never does on its own account.
        -- Holds the outcome and when it was reached, never the prompt or the reply.
        CREATE TABLE IF NOT EXISTS capability_trial (
            model      TEXT NOT NULL,
            capability TEXT NOT NULL,
            state      TEXT NOT NULL,
            detail     TEXT NOT NULL DEFAULT '',
            provider   TEXT NOT NULL DEFAULT '',
            tried_at   REAL NOT NULL,
            PRIMARY KEY (model, capability)
        );
        """,
    ),
    (
        7,
        "usage records, per RAVIS.md §14 and §17, kept across restarts",
        """
        -- One row per completed call, as `cost.UsageRecord` holds it. Kept here because
        -- a ledger in memory emptied the spend screen, and the monthly budget with it,
        -- on every restart. Token counts are nullable on purpose: an unreported count
        -- stays unknown rather than becoming zero (§14). No prompt, no completion.
        CREATE TABLE IF NOT EXISTS usage_record (
            at                  REAL NOT NULL,
            model               TEXT NOT NULL,
            provider            TEXT NOT NULL,
            application_id      TEXT NOT NULL,
            input_tokens        INTEGER,
            output_tokens       INTEGER,
            cached_input_tokens INTEGER,
            reasoning_tokens    INTEGER,
            reported_cost       REAL,
            cost                REAL,
            cost_state          TEXT NOT NULL,
            currency            TEXT,
            price_source        TEXT NOT NULL DEFAULT '',
            price_captured_at   REAL NOT NULL DEFAULT 0,
            latency_ms          REAL,
            request_id          TEXT NOT NULL DEFAULT '',
            session_id          TEXT NOT NULL DEFAULT '',
            decision_id         TEXT NOT NULL DEFAULT '',
            pool                TEXT NOT NULL DEFAULT ''
        );
        -- Retention and every "since" read order by time.
        CREATE INDEX IF NOT EXISTS usage_record_at ON usage_record (at);
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
    version = _apply_migrations(connection, Path(effective))
    _refuse_a_newer_database(Path(effective), version, "ravis")
    # For an in-memory database the migrating connection is kept as an anchor, so
    # the schema just applied survives until this object is discarded.
    anchor = connection if effective != path else None
    return Database(path=effective, schema_version=version, anchor=anchor)


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


class DatabaseTooNewError(RuntimeError):
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
    raise DatabaseTooNewError(
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
            raise FileNotFoundError(
                f"no backup at version {version} beside {database}; have {offer}"
            )
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
    """Apply every migration this database has not yet seen, returning its version.

    Idempotent: running it against an up-to-date database applies nothing and is
    the normal case on every start. Each migration commits with its own
    bookkeeping row in one transaction, so a crash between two migrations leaves
    a database at a real version rather than an invented one.
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
