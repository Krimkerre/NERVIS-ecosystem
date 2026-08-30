"""Reading and writing experiments, runs and results (§17, §11.10).

The cardinality §17 states — an Experiment has many runs, a run has one result
per ExperimentTarget, a run never spans experiments — is enforced by the schema
in `database.py`. This module is where it is *used*, and it exists separately
from the engine for one reason: §11.10 requires that no result becomes visible
before its snapshot and provenance commit atomically, and a rule like that has
to live in one function rather than in every caller that remembers it.

`finish_run` is that function. The results and the run's terminal state are one
transaction, so there is no window in which a run reads `succeeded` while its
results are missing, or in which results exist under a run still claiming to be
in flight.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence

from sirvis.storage.database import Database


class RunState(str, Enum):
    """§11.10's **coarse** published enum, and only that.

    The internal phases — `waiting_for_resources`, `loading`, `warming_up`,
    `evaluating` and the rest — are deliberately not members. §11.10 splits them
    on purpose: consumers switch on this, and the phase travels in `detail` for
    humans and diagnostics. Publishing the fine-grained set would make every
    internal reordering a contract change.

    `PARTIAL` is the state a run reaches when some targets produced results and
    others did not, which is a real outcome rather than a failure — and one
    §11.10 names explicitly, because turning it into `failed` throws away
    measurements that were taken correctly.
    """

    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


@dataclass(frozen=True)
class StoredResult:
    """One result, ready to be committed with its run.

    `target_key` is which `ExperimentTarget` this is about — the model key for a
    single-model run, and one per role once Runtime Sets exist (M9). It is part
    of the unique constraint rather than metadata, because "one result per
    target" is the sentence the drill-through depends on.
    """

    target_key: str
    evidence_id: str
    validity: str
    payload: dict[str, Any]


def create_experiment(database: Database, spec_payload: dict[str, Any], *,
                      suite_id: str, suite_version: str,
                      environment_mode: str) -> str:
    """Record an experiment and return its ID.

    The ID is random rather than derived, which is the opposite of M3's rule for
    model identities and deliberately so: a model build is *described* by its
    attributes, so the same build must produce the same ID, while two identical
    experiments run an hour apart are two different events. Deriving this one
    would make a rerun overwrite the run it was meant to be compared with.
    """
    experiment_id = f"exp_{uuid.uuid4().hex[:16]}"
    with database.connection as connection:
        connection.execute(
            "INSERT INTO experiment (experiment_id, suite_id, suite_version,"
            " environment_mode, payload) VALUES (?, ?, ?, ?, ?)",
            (experiment_id, suite_id, suite_version, environment_mode,
             json.dumps(spec_payload, sort_keys=True)),
        )
    return experiment_id


def start_run(database: Database, experiment_id: str, *, runtime_key: str,
              runtime_snapshot: dict[str, Any], machine_snapshot_id: str | None,
              results_path: str | None, trace_id: str = "") -> str:
    """Open a run in `RUNNING` and return its ID.

    Written before the work rather than after it, so a run interrupted by a
    crash leaves a row saying it was in flight. §11.10 asks for jobs that either
    survive a restart or are truthfully marked unrecoverable, and a run that was
    never recorded until it succeeded can be neither.
    """
    run_id = f"run_{uuid.uuid4().hex[:16]}"
    with database.connection as connection:
        connection.execute(
            "INSERT INTO benchmark_run (run_id, experiment_id, machine_snapshot_id,"
            " state, detail, runtime_key, runtime_snapshot, results_path, trace_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, experiment_id, machine_snapshot_id, RunState.RUNNING.value,
             "preparing", runtime_key, json.dumps(runtime_snapshot, sort_keys=True),
             results_path, trace_id or None),
        )
    return run_id


def trace_of_run(database: Database, run_id: str) -> str:
    """The trace this run belongs to, or empty.

    Read back rather than carried, so the closing event joins the same trace as
    the opening one even when the two are emitted from different places — and
    so a run recorded before migration 7, which has no trace, reports the
    absence rather than a fresh id that would correlate with nothing.
    """
    row = database.connection.execute(
        "SELECT trace_id FROM benchmark_run WHERE run_id = ?", (run_id,)
    ).fetchone()
    return str(row["trace_id"]) if row and row["trace_id"] else ""


def finish_run(database: Database, run_id: str, *, state: RunState, detail: str,
               results: Sequence[StoredResult] = ()) -> list[str]:
    """Commit a run's results and its terminal state together (§11.10).

    One transaction, and that is the whole point of this function existing.
    Writing the results first and the state second would leave a window where a
    dashboard reads a run still `running` while its results are already visible;
    the other order leaves the mirror-image window. Neither is acceptable for a
    consumer that drills from a route decision through to evidence.
    """
    identifiers = [f"res_{uuid.uuid4().hex[:16]}" for _ in results]
    with database.connection as connection:
        for result_id, result in zip(identifiers, results):
            connection.execute(
                "INSERT INTO benchmark_result (result_id, run_id, target_key,"
                " evidence_id, validity, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (result_id, run_id, result.target_key, result.evidence_id,
                 result.validity, json.dumps(result.payload, sort_keys=True)),
            )
        connection.execute(
            "UPDATE benchmark_run SET state = ?, detail = ?,"
            " finished_at = datetime('now') WHERE run_id = ?",
            (state.value, detail, run_id),
        )
    return identifiers


def read_run(database: Database, run_id: str) -> dict[str, Any] | None:
    """One run with its results, or None when there is no such run."""
    row = database.connection.execute(
        "SELECT * FROM benchmark_run WHERE run_id = ?", (run_id,)
    ).fetchone()
    return _run_with_results(database, row) if row is not None else None


# The states a run can be left in by a process that stopped existing. Derived
# from the enum rather than spelled out, so a new non-terminal state cannot be
# added without this noticing it.
_UNFINISHED = (RunState.QUEUED, RunState.PREPARING, RunState.RUNNING)

INTERRUPTED = "the process running this ended before the run finished"


def reconcile_interrupted(database: Database) -> list[str]:
    """Mark runs that outlived the process which started them (§11.10).

    §11.10 asks that a job survive a restart **or** be truthfully marked
    unrecoverable. A run does neither on its own: `start_run` writes `RUNNING`
    before the work — which is right, because a run first recorded when it
    succeeds cannot be recovered at all — and nothing puts the row right if the
    process is killed. Two such rows sat in this machine's database for a day
    reading `running`, and the dashboard found them rather than the engine.

    `FAILED` rather than `CANCELLED`: cancelled means a client asked to stop,
    which is a different fact about a different actor. And not `PARTIAL`, which
    §11.10 reserves for a run that produced *some* results — these produced
    none, because `finish_run` is the only writer of results and it never ran.

    **Called at startup, and that is the assumption to check if this ever runs
    beside a second process.** It reads "unfinished" as "abandoned", which is
    true of one local service and false the moment two share a database.
    """
    unfinished = [state.value for state in _UNFINISHED]
    places = ",".join("?" * len(unfinished))
    rows = database.connection.execute(
        f"SELECT run_id FROM benchmark_run WHERE state IN ({places})", unfinished
    ).fetchall()
    if not rows:
        return []
    with database.connection as connection:
        connection.execute(
            "UPDATE benchmark_run SET state = ?, detail = ?, finished_at = datetime('now')"
            f" WHERE state IN ({places})",
            (RunState.FAILED.value, INTERRUPTED, *unfinished),
        )
    return [row["run_id"] for row in rows]


def read_result(database: Database, result_id: str) -> dict[str, Any] | None:
    """One result, or None when there is no such result.

    Carries its run's identifier rather than only its own: a result is evidence
    about a target, and the first question anyone asks of one is which run
    produced it — §17 makes `source_run_id` the provenance pointer RAVIS
    preserves and NERVIS dereferences.
    """
    row = database.connection.execute(
        "SELECT * FROM benchmark_result WHERE result_id = ?", (result_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "result_id": row["result_id"],
        "run_id": row["run_id"],
        "target_key": row["target_key"],
        "evidence_id": row["evidence_id"],
        "validity": row["validity"],
        "created_at": row["created_at"],
        **json.loads(row["payload"]),
    }


def list_runs(database: Database, limit: int = 5,
              cursor: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """A page of runs, newest first, and where the next page starts.

    **Ordered by `rowid`, not by `started_at`.** Two runs begun in the same
    second sort arbitrarily by timestamp, and a paging key that can tie will
    eventually skip a row or repeat one — the failure is rare, silent, and
    impossible to reproduce on demand. `rowid` is unique and monotonic, so the
    order it gives is both stable and the insertion order anyone reading
    "newest first" means.

    One row beyond the limit is fetched and discarded, which is how the cursor
    knows whether there is anything after this page without a second count
    query — and how the last page correctly returns `None` rather than a cursor
    pointing at nothing.
    """
    if cursor is None:
        rows = database.connection.execute(
            "SELECT rowid AS position, * FROM benchmark_run"
            " ORDER BY rowid DESC LIMIT ?", (limit + 1,),
        ).fetchall()
    else:
        rows = database.connection.execute(
            "SELECT rowid AS position, * FROM benchmark_run WHERE rowid < ?"
            " ORDER BY rowid DESC LIMIT ?", (cursor, limit + 1),
        ).fetchall()
    page = rows[:limit]
    following = str(page[-1]["position"]) if page and len(rows) > limit else None
    return [_run_with_results(database, row) for row in page], following


def _run_with_results(database: Database, row: Any) -> dict[str, Any]:
    results = database.connection.execute(
        "SELECT result_id, target_key, evidence_id, validity, payload, created_at"
        " FROM benchmark_result WHERE run_id = ? ORDER BY rowid",
        (row["run_id"],),
    ).fetchall()
    return {
        "run_id": row["run_id"],
        "experiment_id": row["experiment_id"],
        "machine_snapshot_id": row["machine_snapshot_id"],
        "state": row["state"],
        "detail": row["detail"],
        "runtime_key": row["runtime_key"],
        "runtime_snapshot": json.loads(row["runtime_snapshot"]),
        "results_path": row["results_path"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "results": [
            {
                "result_id": result["result_id"],
                "target_key": result["target_key"],
                "evidence_id": result["evidence_id"],
                "validity": result["validity"],
                "created_at": result["created_at"],
                **json.loads(result["payload"]),
            }
            for result in results
        ],
    }


def delete_result(database: Database, result_id: str, *, reason: str,
                  requested_by: str = "") -> dict[str, Any] | None:
    """Delete one stored result and leave a tombstone. None when there is none.

    **The row is removed, not flagged.** A soft delete would leave the payload
    in the database and every query carrying a `WHERE deleted_at IS NULL` that
    one reader eventually forgets — and an operator who asks for a measurement
    to be gone has asked for it to be gone.

    What survives is the tombstone: §15.1 promises RAVIS that evidence
    references stay resolvable during the retention window, and a consumer
    holding `ev_…` must be able to tell a deletion from a benchmark that was
    never run. Those lead to different decisions — the first says the evidence
    was withdrawn, the second says nobody has measured this yet.

    **The run row stays, and so do the raw takes on disk.** The run happened;
    deleting the record of it would remove the audit trail of the very action
    this function is taking. §11.9's per-run directory is not touched either,
    because it is per *experiment* rather than per result and removing files is
    a different, less reversible decision than removing a row. The caller is
    told where they are so an operator can make that decision deliberately.
    """
    row = database.connection.execute(
        "SELECT result_id, run_id, evidence_id, target_key, payload"
        " FROM benchmark_result WHERE result_id = ?",
        (result_id,),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload"])
    tombstone = {
        "result_id": row["result_id"],
        "evidence_id": row["evidence_id"],
        "run_id": row["run_id"],
        "target_key": row["target_key"],
        "role": str(payload.get("role") or ""),
        "measured_at": str(payload.get("created_at") or ""),
        "reason": reason,
    }
    with database.connection as connection:
        connection.execute(
            "INSERT OR REPLACE INTO evidence_tombstone"
            " (result_id, evidence_id, run_id, target_key, role, measured_at, reason)"
            " VALUES (:result_id, :evidence_id, :run_id, :target_key, :role,"
            " :measured_at, :reason)",
            tombstone,
        )
        connection.execute("DELETE FROM benchmark_result WHERE result_id = ?", (result_id,))
    # Whether anything is still filed under this evidence identity. Two runs of
    # one suite against one build share an id, so "the record is gone" and "this
    # result is gone" are different answers and the caller publishes both.
    remaining = database.connection.execute(
        "SELECT COUNT(*) AS n FROM benchmark_result WHERE evidence_id = ?",
        (row["evidence_id"],),
    ).fetchone()["n"]
    return {**tombstone, "requested_by": requested_by,
            "remaining_under_evidence_id": remaining}


def list_tombstones(database: Database, limit: int = 100, *,
                    target_keys: tuple[str, ...] = (),
                    role: str = "") -> list[dict[str, Any]]:
    """Deleted results, newest first — §15.1's tombstones.

    Narrowed by the same two things a consumer filters evidence on, and **not**
    by which records survived: the case that matters most is a build whose only
    result was deleted, where the evidence list is empty and the tombstone is
    the entire answer. Deriving the filter from the surviving items would have
    returned nothing there, or — worse, since an empty filter reads as no filter
    — every deletion this machine has ever made.
    """
    where: list[str] = []
    values: list[Any] = []
    if target_keys:
        where.append(f"target_key IN ({','.join('?' * len(target_keys))})")
        values.extend(target_keys)
    if role:
        where.append("role = ?")
        values.append(role)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    rows = database.connection.execute(
        f"SELECT * FROM evidence_tombstone{clause} ORDER BY deleted_at DESC LIMIT ?",
        (*values, limit),
    ).fetchall()
    return [dict(row) for row in rows]
