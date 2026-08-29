"""The one thing that runs a queued benchmark (M14).

Deliberately a single worker with no concurrency. A benchmark measures a
machine; two benchmarks running at once measure each other, and §11.1 forbids
comparing a `controlled` result with a `shared` one as though they were
equivalent. A pool would quietly make every result `shared` without anybody
choosing that, so the serialisation is a measurement decision rather than an
implementation shortcut.

**A failed job is a finished job.** Every exit path writes a terminal state,
because a row left reading `running` is the state §11.10 exists to prevent — and
the queue would then never move, since the worker takes one job at a time.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

import yaml

from sirvis import jobs
from sirvis.benchmarks.clarvis_roles import role_spec
from sirvis.benchmarks.engine import run_experiment
from sirvis.benchmarks.spec import ExperimentSpec, parse_experiment
from sirvis.errors import SirvisError
from sirvis.storage import Database

LOG = logging.getLogger("sirvis.worker")

# How often the queue is looked at when it was empty. Long enough to cost
# nothing, short enough that submitting from a dashboard feels like submitting.
IDLE_SECONDS = 1.0


def _spec_for(job: dict[str, Any]) -> ExperimentSpec:
    """The submitted specification, with the job's overrides applied.

    The same two overrides the CLI applies, in the same order, because a
    benchmark submitted over HTTP and one run from a terminal have to produce
    comparable evidence — a queue that interpreted `--model` differently would
    make the two incomparable while looking identical.

    `parse_experiment` rather than a mapping constructor: it is the one reader,
    and it refuses a key this build cannot honour rather than dropping it. A
    specification that runs with half its instructions ignored produces a result
    nobody can interpret.
    """
    spec = parse_experiment(yaml.safe_dump(job["specification"]), source=job["job_id"])
    if job["model_override"]:
        spec = dataclasses.replace(spec, model_key=job["model_override"])
    if job["clarvis_role"]:
        # The role supplies the whole experiment; the submitted file contributes
        # only the model and the repetition counts. Mirrors the CLI exactly.
        spec = role_spec(job["clarvis_role"], spec.model_key,
                         warmups=spec.warmups, repetitions=spec.repetitions)
    return spec


async def run_one(api: Any, job: dict[str, Any]) -> None:
    """Run one claimed job to a terminal state, whatever happens.

    The engine already returns an outcome rather than raising for a benchmark
    that failed — §4.2's rule that a successful request is not a successful
    benchmark, applied inside the engine — so the `except` here is for the
    things that stop a benchmark from starting at all: a specification this
    build cannot honour, a runtime that is not there.
    """
    database: Database = api.state.database
    job_id = job["job_id"]
    try:
        spec = _spec_for(job)
    except (SirvisError, ValueError, KeyError, TypeError) as failure:
        jobs.finish(database, job_id, state=jobs.JobState.FAILED,
                    detail=f"the specification could not be read: {failure}")
        return

    try:
        outcome = await run_experiment(
            spec,
            runtime=api.state.lmstudio,
            resources=api.state.resources,
            database=database,
            results_root=api.state.settings.results_path,
            events=api.state.events,
            # The caller's trace, so a benchmark submitted over HTTP joins the
            # trace that submitted it. This is the column migration 7 added and
            # the reason it went on the row rather than into a local.
            trace_id=job["trace_id"] or "",
            should_stop=lambda: jobs.cancelled(database, job_id),
        )
    except SirvisError as failure:
        jobs.finish(database, job_id, state=jobs.JobState.FAILED,
                    detail=failure.message)
        return
    except Exception as failure:  # noqa: BLE001 - a job must always end
        LOG.exception("benchmark job %s failed unexpectedly", job_id)
        jobs.finish(database, job_id, state=jobs.JobState.FAILED,
                    detail=f"{type(failure).__name__}: {failure}")
        return

    jobs.attach_run(database, job_id, outcome.run_id)
    # **Cancelled beats the run's own verdict.** A run stopped early usually
    # ends `succeeded` or `partial` from the engine's point of view, and
    # reporting that would lose the one fact the person who pressed cancel
    # cares about: it stopped because they asked.
    if jobs.cancelled(database, job_id):
        jobs.finish(database, job_id, state=jobs.JobState.CANCELLED,
                    detail="cancelled while running; partial results were kept")
        return
    succeeded = str(outcome.state.value) == "succeeded"
    jobs.finish(
        database, job_id,
        state=jobs.JobState.SUCCEEDED if succeeded else jobs.JobState.FAILED,
        detail=outcome.detail,
    )


async def serve_queue(api: Any) -> None:
    """Claim and run, forever. Cancellation is the ordinary way this ends.

    Every failure other than cancellation is caught and the loop continues: a
    worker that dies takes the queue with it and says nothing, which would make
    a submitted job sit at `queued` for as long as the service ran.
    """
    while True:
        try:
            job = jobs.claim(api.state.database)
            if job is None:
                await asyncio.sleep(IDLE_SECONDS)
                continue
            await run_one(api, job)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the queue outlives one bad job
            LOG.exception("the benchmark queue hit an error and is continuing")
            await asyncio.sleep(IDLE_SECONDS)
