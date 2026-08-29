"""M14's benchmark queue — submit, poll, cancel (SIRVIS §4.2, §11.10).

**A job is not a run**, and keeping them apart is most of this module. A run is
the engine's record of work that started; a job is a *request* for work, which
may sit queued for minutes, be cancelled before anything loads, or fail before
a run exists at all. §4.2's rule that *"a successful HTTP request is not a
successful benchmark"* is the same distinction one level up: submitting returns
a job, and the job is what a client polls.

**One at a time, deliberately.** A benchmark measures a machine, and two
benchmarks running at once measure each other. §11.1 already forbids comparing a
`controlled` result with a `shared` one as though they were equivalent; running
two concurrently would make every result `shared` without anybody choosing that.
The queue is therefore a queue rather than a pool, and that is a measurement
decision rather than an implementation shortcut.

**Cancellation is a flag, not a signal.** The work runs in this process, so
cancelling could mean killing the task — and §11.10 wants a run that ends early
to keep its partial telemetry, which a killed task loses. The worker reads the
flag between repetitions instead: slower to take effect, and it leaves behind
the record of how far the run got.
"""

from __future__ import annotations

import json
import uuid
from enum import Enum
from typing import Any, Mapping, Sequence

from sirvis.storage import Database


class JobState(str, Enum):
    """§11.10's coarse published enum, for jobs rather than runs.

    `cancelled` is separate from `failed` because they are facts about different
    actors: one is a client changing its mind, the other is the work not
    working. A consumer that treats them alike reports a user's own decision as
    an error.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED)

# What a job that outlived its process is marked with. The wording is the same
# shape as the runs' own, because it is the same fact about a different row.
INTERRUPTED = "the service stopped while this job was running"


def submit(
    database: Database,
    *,
    specification: Mapping[str, Any],
    model: str = "",
    clarvis_role: str = "",
    trace_id: str = "",
) -> str:
    """Enqueue one benchmark and return its id.

    The specification is stored **as submitted** rather than as a path. A queued
    job that depended on a file still being there would fail hours later for a
    reason nothing recorded, and a job is supposed to be reproducible from what
    it holds.
    """
    job_id = f"bj_{uuid.uuid4().hex[:12]}"
    with database.connection as connection:
        connection.execute(
            "INSERT INTO benchmark_job (job_id, specification, model_override,"
            " clarvis_role, state, trace_id) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, json.dumps(dict(specification), sort_keys=True),
             model or None, clarvis_role or None, JobState.QUEUED.value,
             trace_id or None),
        )
    return job_id


def read(database: Database, job_id: str) -> dict[str, Any] | None:
    row = database.connection.execute(
        "SELECT * FROM benchmark_job WHERE job_id = ?", (job_id,)
    ).fetchone()
    return _as_dict(row) if row else None


def recent(database: Database, limit: int = 50) -> list[dict[str, Any]]:
    rows = database.connection.execute(
        "SELECT * FROM benchmark_job ORDER BY submitted_at DESC, rowid DESC LIMIT ?",
        (max(1, min(limit, 200)),),
    ).fetchall()
    return [_as_dict(row) for row in rows]


def claim(database: Database) -> dict[str, Any] | None:
    """Take the oldest queued job, or None. Atomic against a second worker.

    The `WHERE state = 'queued'` in the UPDATE is what makes it atomic: two
    workers racing both read the same row, and only the one whose update
    actually changes a row has claimed it. There is one worker today, and the
    guard costs nothing and is the difference between this being safe to run
    twice and being quietly wrong when somebody does.
    """
    row = database.connection.execute(
        "SELECT job_id FROM benchmark_job WHERE state = ?"
        " ORDER BY submitted_at, rowid LIMIT 1",
        (JobState.QUEUED.value,),
    ).fetchone()
    if row is None:
        return None
    with database.connection as connection:
        changed = connection.execute(
            "UPDATE benchmark_job SET state = ?, started_at = datetime('now')"
            " WHERE job_id = ? AND state = ?",
            (JobState.RUNNING.value, row["job_id"], JobState.QUEUED.value),
        )
    if not changed.rowcount:
        return None
    return read(database, str(row["job_id"]))


def attach_run(database: Database, job_id: str, run_id: str) -> None:
    """Record which run this job became, once the engine has opened one.

    Written separately from `finish` because it is knowable earlier, and a job
    that fails mid-run should still say which run to look at.
    """
    with database.connection as connection:
        connection.execute(
            "UPDATE benchmark_job SET run_id = ? WHERE job_id = ?", (run_id, job_id)
        )


def finish(database: Database, job_id: str, *, state: JobState, detail: str) -> None:
    with database.connection as connection:
        connection.execute(
            "UPDATE benchmark_job SET state = ?, detail = ?,"
            " finished_at = datetime('now') WHERE job_id = ?",
            (state.value, detail, job_id),
        )


def cancel(database: Database, job_id: str) -> dict[str, Any] | None:
    """Ask for a job to stop. Idempotent, as §4.2 requires.

    Three cases and they are genuinely different:

    **Queued** — nothing has started, so it is cancelled outright and no model
    is ever loaded.

    **Running** — the flag is set and the worker reads it between repetitions.
    The job stays `running` until it actually stops, because reporting
    `cancelled` while inference is still in flight would be the confident wrong
    answer this repository collects: a client would see a free machine and
    submit the next benchmark onto a busy one.

    **Terminal** — nothing happens, and that is a success rather than an error.
    Cancelling something that already finished is a client with a stale view,
    not a client doing something wrong, and answering 409 would make an
    idempotent retry look like a failure.
    """
    found = read(database, job_id)
    if found is None:
        return None
    state = JobState(found["state"])
    if state in TERMINAL:
        return found
    if state is JobState.QUEUED:
        finish(database, job_id, state=JobState.CANCELLED,
               detail="cancelled before it started")
        return read(database, job_id)
    with database.connection as connection:
        connection.execute(
            "UPDATE benchmark_job SET cancel_requested = 1 WHERE job_id = ?", (job_id,)
        )
    return read(database, job_id)


def cancelled(database: Database, job_id: str) -> bool:
    """Whether a stop has been asked for. Read by the engine between repetitions."""
    row = database.connection.execute(
        "SELECT cancel_requested FROM benchmark_job WHERE job_id = ?", (job_id,)
    ).fetchone()
    return bool(row and row["cancel_requested"])


def reconcile_interrupted(database: Database) -> list[str]:
    """Mark jobs that outlived the process which started them (§11.10).

    The same rule the runs already follow, for the same reason: §11.10 asks that
    work survive a restart **or** be truthfully marked unrecoverable, and a row
    left reading `running` after the process died is neither. A queued job is
    left alone — it has not started, so it survives a restart honestly and the
    worker will pick it up.

    `FAILED` rather than `CANCELLED`, because nobody asked it to stop.
    """
    rows = database.connection.execute(
        "SELECT job_id FROM benchmark_job WHERE state = ?", (JobState.RUNNING.value,)
    ).fetchall()
    if not rows:
        return []
    with database.connection as connection:
        connection.execute(
            "UPDATE benchmark_job SET state = ?, detail = ?,"
            " finished_at = datetime('now') WHERE state = ?",
            (JobState.FAILED.value, INTERRUPTED, JobState.RUNNING.value),
        )
    return [str(row["job_id"]) for row in rows]


def _as_dict(row: Any) -> dict[str, Any]:
    """One job, in the shape the API publishes.

    The specification comes back parsed. A client that has to `json.loads` a
    string out of a JSON document is being handed the storage format rather than
    an answer.
    """
    body = dict(row)
    try:
        body["specification"] = json.loads(body.get("specification") or "{}")
    except ValueError:
        body["specification"] = {}
    body["cancel_requested"] = bool(body.get("cancel_requested"))
    return body


def as_public(job: Mapping[str, Any]) -> dict[str, Any]:
    """What `/api/v1/benchmark-jobs` returns for one job."""
    return {
        "job_id": job["job_id"],
        "state": job["state"],
        "detail": job["detail"],
        "cancel_requested": job["cancel_requested"],
        "run_id": job["run_id"],
        "trace_id": job["trace_id"],
        "submitted_at": job["submitted_at"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        # The target, lifted out so a queue table can be rendered without every
        # client re-deriving it from the specification.
        "model": job["model_override"] or (job["specification"].get("target") or {}).get("model"),
        "suite": job["specification"].get("suite"),
        "clarvis_role": job["clarvis_role"],
    }


def public_list(jobs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [as_public(job) for job in jobs]
