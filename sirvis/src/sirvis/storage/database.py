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
    (
        5,
        "experiments, runs and results, per SIRVIS.md §17 and §11.10",
        """
        -- §17's cardinality, written down because the drill-through RAVIS and
        -- NERVIS need cannot be built without it: an Experiment has many runs,
        -- a run has one result per ExperimentTarget, and a run never spans
        -- experiments. The foreign keys and the unique constraint below are
        -- that sentence made mechanical rather than remembered.
        CREATE TABLE IF NOT EXISTS experiment (
            experiment_id    TEXT PRIMARY KEY,
            suite_id         TEXT NOT NULL,
            suite_version    TEXT NOT NULL,
            -- `controlled` or `shared` (§11.1). Stored on the experiment rather
            -- than inferred later, because §11.1 forbids silently comparing the
            -- two and a comparison cannot refuse what it cannot see.
            environment_mode TEXT NOT NULL,
            created_at       TEXT NOT NULL DEFAULT (datetime('now')),
            -- The specification as given, whole. A column per field would need
            -- a migration every time a suite learns a new knob, and the fields
            -- that matter for querying are lifted out above.
            payload          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS benchmark_run (
            run_id              TEXT PRIMARY KEY,
            experiment_id       TEXT NOT NULL REFERENCES experiment(experiment_id),
            -- The conditions this run happened under. Nullable only because a
            -- machine that reports nothing is still allowed to run a benchmark;
            -- the result then says so rather than claiming a snapshot it lacks.
            machine_snapshot_id TEXT REFERENCES machine_snapshot(snapshot_id),
            -- §11.10's *coarse* published enum: queued, preparing, running,
            -- succeeded, failed, cancelled, partial. The internal phase lives
            -- in `detail`, which is for humans; consumers switch on `state`.
            state               TEXT NOT NULL,
            detail              TEXT NOT NULL DEFAULT '',
            runtime_key         TEXT NOT NULL,
            runtime_snapshot    TEXT NOT NULL,
            -- Where §11.9's raw responses went. A path rather than a blob: the
            -- point of preserving raw output is that it can be rescored without
            -- rerunning inference, and a database is the wrong home for it.
            results_path        TEXT,
            started_at          TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at         TEXT
        );

        CREATE INDEX IF NOT EXISTS benchmark_run_by_experiment
            ON benchmark_run (experiment_id, started_at DESC);

        CREATE TABLE IF NOT EXISTS benchmark_result (
            result_id  TEXT PRIMARY KEY,
            run_id     TEXT NOT NULL REFERENCES benchmark_run(run_id),
            -- Which ExperimentTarget this result is about. A single-model
            -- experiment has one; a Runtime Set (M9) has one per role.
            target_key TEXT NOT NULL,
            -- §12.2's derived key. Stored so a consumer can find every result
            -- about the same build, runtime config and role without recomputing
            -- the hash, and never as the primary key: two runs of one suite
            -- against one build are two results and one evidence identity.
            evidence_id TEXT NOT NULL,
            validity    TEXT NOT NULL,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            -- "A run has one result per ExperimentTarget" (§17). Enforced here
            -- so a retry that writes twice fails loudly instead of leaving two
            -- results that disagree about the same target.
            UNIQUE (run_id, target_key)
        );

        CREATE INDEX IF NOT EXISTS benchmark_result_by_evidence
            ON benchmark_result (evidence_id, created_at DESC);
        """,
    ),
    (
        6,
        "runtime sets and their revisions, per SIRVIS.md §10",
        """
        -- §10's versioning, made mechanical. The identity row carries what is
        -- stable about a set — its name — and the revision rows carry the
        -- definitions. Splitting them is what lets `clarvis-balanced` keep one
        -- identity while its membership changes, so "how has this set performed
        -- over time" is answerable at all.
        CREATE TABLE IF NOT EXISTS runtime_set (
            runtime_set_id TEXT PRIMARY KEY,
            name           TEXT NOT NULL UNIQUE,
            created_at     TEXT NOT NULL DEFAULT (datetime('now'))
        );

        -- **Never updated, only inserted.** §10's gate is that old results
        -- retain the revision they measured, and a row a result points at must
        -- therefore be immutable. There is no UPDATE anywhere against this
        -- table, and adding one would silently rewrite history that has already
        -- been cited.
        CREATE TABLE IF NOT EXISTS runtime_set_revision (
            runtime_set_id  TEXT NOT NULL REFERENCES runtime_set(runtime_set_id),
            revision        INTEGER NOT NULL,
            -- The hash of everything that changes what gets loaded. Two saves
            -- of an identical definition find this and return the existing
            -- revision rather than manufacturing a new one, so a revision
            -- number always means the definition actually changed.
            definition_hash TEXT NOT NULL,
            purpose         TEXT NOT NULL DEFAULT '',
            payload         TEXT NOT NULL,
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (runtime_set_id, revision)
        );

        CREATE INDEX IF NOT EXISTS runtime_set_revision_by_hash
            ON runtime_set_revision (runtime_set_id, definition_hash);
        """,
    ),
    (
        7,
        "a trace id per benchmark run, per the runbook's Stage 7",
        """
        -- **On the row, not in a local variable.** A benchmark is not an HTTP
        -- request: it is started from the CLI, so there is no inbound
        -- `traceparent` to inherit and the id has to be minted. Storing it here
        -- rather than passing it down the call stack is what lets M14's
        -- enqueue-over-HTTP later populate the same column from
        -- `request.state.trace_id` without the engine or the events changing at
        -- all — the caller's trace becomes the run's trace by writing one
        -- field.
        --
        -- Nullable, because every run recorded before this migration has no
        -- trace and inventing one would claim a correlation that never existed.
        ALTER TABLE benchmark_run ADD COLUMN trace_id TEXT;

        CREATE INDEX IF NOT EXISTS benchmark_run_by_trace
            ON benchmark_run (trace_id);
        """,
    ),
    (
        8,
        "the benchmark job queue, per SIRVIS §4.2 and §11.10",
        """
        -- **A job is not a run.** A run is the engine's record of work that
        -- started; a job is a *request* for work, which may sit queued, be
        -- cancelled before anything loads, or fail before a run exists. §4.2's
        -- rule that "a successful HTTP request is not a successful benchmark"
        -- is the same distinction one level up: submitting returns a job, and
        -- the job is the thing a client polls.
        --
        -- `run_id` is nullable and filled when the engine starts, which is what
        -- lets a client follow a job through to the evidence it produced.
        CREATE TABLE IF NOT EXISTS benchmark_job (
            job_id           TEXT PRIMARY KEY,
            -- The specification exactly as submitted, so a job is reproducible
            -- and so a queued one does not depend on a file still being there.
            specification    TEXT NOT NULL,
            model_override   TEXT,
            clarvis_role     TEXT,
            -- queued · running · succeeded · failed · cancelled
            state            TEXT NOT NULL,
            detail           TEXT NOT NULL DEFAULT '',
            -- Set by a cancel request while the job is running. The worker
            -- reads it between repetitions; a flag rather than a signal
            -- because the work is in this process and killing it would lose
            -- the partial telemetry §11.10 says to keep.
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            -- The caller's trace, so a benchmark submitted over HTTP joins the
            -- trace that submitted it rather than minting one of its own.
            -- `benchmark_run.trace_id` is populated from this.
            trace_id         TEXT,
            run_id           TEXT REFERENCES benchmark_run(run_id),
            submitted_at     TEXT NOT NULL DEFAULT (datetime('now')),
            started_at       TEXT,
            finished_at      TEXT
        );

        -- The worker's own query: the oldest thing still waiting.
        CREATE INDEX IF NOT EXISTS benchmark_job_by_state
            ON benchmark_job (state, submitted_at);
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
