"""M14 — the benchmark queue: submit, poll, cancel (§4.2, §11.10).

A job is not a run, and most of this file is that distinction. A run is the
engine's record of work that started; a job is a *request* for work, which may
sit queued, be cancelled before anything loads, or fail before a run exists.
§4.2's rule that "a successful HTTP request is not a successful benchmark" is
the same distinction one level up.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient

from sirvis import jobs
from sirvis.api.security import Scope, mint_token
from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.storage import prepare_database

SPEC: dict[str, Any] = {
    "id": "basic", "suite": "performance-basic", "suite_version": "1",
    "role": "general", "environment": "shared",
    "target": {"model": "a-model", "load": {"context_length": 8192}},
    "warmups": 0, "repetitions": 1,
    "tests": [{"id": "t1", "version": 1, "prompt": "hi",
               "generation": {"temperature": 0, "max_tokens": 8}}],
}


def an_api() -> tuple[TestClient, str]:
    settings = Settings(
        database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    return (
        TestClient(app),
        mint_token(app.state.database, "dashboard", {Scope.BENCHMARK}),
    )


def cancel(client: TestClient, token: str, job_id: str) -> httpx.Response:
    """Cancel, as a real client would.

    `content-type: application/json` is not decoration: §4.5 refuses a mutation
    that did not arrive as JSON, which is a CSRF defence — a form post from
    another origin cannot set it.
    """
    return client.post(
        f"/api/v1/benchmark-jobs/{job_id}/cancel", json={},
        headers={"authorization": f"Bearer {token}"},
    )


def submit(client: TestClient, token: str, **body: Any) -> httpx.Response:
    return client.post(
        "/api/v1/benchmark-jobs",
        json={"specification": SPEC, **body},
        headers={"authorization": f"Bearer {token}"},
    )


# ── Submit ──────────────────────────────────────────────────────────────────


def test_submitting_returns_a_job_rather_than_a_result() -> None:
    """§4.2: a successful HTTP request is not a successful benchmark. A
    benchmark takes minutes, so 200-with-a-result would either hold the
    connection open for the whole run or lie about what happened."""
    client, token = an_api()

    answer = submit(client, token)

    assert answer.status_code == 202
    job = answer.json()["job"]
    assert job["state"] == "queued"
    assert job["job_id"].startswith("bj_")
    assert "run_id" in job and job["run_id"] is None, "no run exists yet"


def test_submitting_needs_the_benchmark_scope() -> None:
    """§4.5 separates the scopes by what they cost. This one costs time — a
    dashboard that draws graphs must not be able to occupy the machine."""
    client, _ = an_api()

    answer = client.post("/api/v1/benchmark-jobs", json={"specification": SPEC})

    assert answer.status_code in (401, 403)


def test_a_job_without_a_specification_is_refused() -> None:
    """Storing the specification as submitted is what makes a queued job
    independent of a file still being there."""
    client, token = an_api()

    answer = client.post(
        "/api/v1/benchmark-jobs", json={"model": "a-model"},
        headers={"authorization": f"Bearer {token}"},
    )

    assert answer.status_code >= 400


def test_the_caller_s_trace_is_carried_onto_the_job() -> None:
    """So the run this becomes joins the trace that asked for it rather than
    minting one of its own. This is what `benchmark_run.trace_id` was added
    for — the column went on the row precisely so this could fill it."""
    client, token = an_api()

    answer = client.post(
        "/api/v1/benchmark-jobs", json={"specification": SPEC},
        headers={"authorization": f"Bearer {token}",
                 "traceparent": f"00-{'a' * 32}-{'b' * 16}-01"},
    )

    assert answer.json()["job"]["trace_id"] == "a" * 32


# ── Poll ────────────────────────────────────────────────────────────────────


def test_a_job_can_be_polled_and_listed() -> None:
    client, token = an_api()
    job_id = submit(client, token).json()["job"]["job_id"]

    one = client.get(f"/api/v1/benchmark-jobs/{job_id}")
    many = client.get("/api/v1/benchmark-jobs")

    assert one.status_code == 200
    assert one.json()["job"]["job_id"] == job_id
    assert job_id in [j["job_id"] for j in many.json()["items"]]


def test_polling_an_unknown_job_says_so() -> None:
    client, _ = an_api()

    assert client.get("/api/v1/benchmark-jobs/bj_nope").status_code == 404


def test_reads_are_open_like_every_other_read() -> None:
    client, token = an_api()
    submit(client, token)

    assert client.get("/api/v1/benchmark-jobs").status_code == 200


# ── Cancel ──────────────────────────────────────────────────────────────────


def test_cancelling_a_queued_job_stops_it_before_anything_loads() -> None:
    """The whole point of cancelling early: no model is ever loaded."""
    client, token = an_api()
    job_id = submit(client, token).json()["job"]["job_id"]

    answer = cancel(client, token, job_id)

    assert answer.json()["job"]["state"] == "cancelled"


def test_cancelling_is_idempotent() -> None:
    """§4.2. A client with a stale view retrying is not doing something wrong,
    and a 409 would make an idempotent retry look like a failure."""
    client, token = an_api()
    job_id = submit(client, token).json()["job"]["job_id"]
    first = cancel(client, token, job_id)
    again = cancel(client, token, job_id)

    assert first.status_code == 200 and again.status_code == 200
    assert again.json()["job"]["state"] == "cancelled"


def test_cancelling_a_running_job_does_not_claim_it_already_stopped() -> None:
    """Reporting `cancelled` while inference is still in flight would tell a
    reader the machine is free, and the next benchmark would land on a busy
    one. The flag is set; the state changes when it actually stops."""
    database = prepare_database(":memory:")
    job_id = jobs.submit(database, specification=SPEC)
    jobs.claim(database)

    after = jobs.cancel(database, job_id)

    assert after is not None
    assert after["state"] == "running", "it has not stopped yet"
    assert after["cancel_requested"] is True
    assert jobs.cancelled(database, job_id)


# ── The queue itself ────────────────────────────────────────────────────────


def test_claiming_takes_the_oldest_and_only_once() -> None:
    """The guard in the UPDATE is what makes a second worker safe rather than
    quietly wrong."""
    database = prepare_database(":memory:")
    first = jobs.submit(database, specification=SPEC)
    jobs.submit(database, specification=SPEC)

    claimed = jobs.claim(database)
    again = jobs.claim(database)

    assert claimed is not None and claimed["job_id"] == first
    assert again is not None and again["job_id"] != first, "the same job twice"


def test_an_empty_queue_claims_nothing() -> None:
    assert jobs.claim(prepare_database(":memory:")) is None


def test_a_job_running_when_the_process_died_is_marked_unrecoverable() -> None:
    """§11.10: survive a restart, or be truthfully marked. A row left reading
    `running` is neither — and the queue would never move again, because the
    worker takes one job at a time."""
    database = prepare_database(":memory:")
    job_id = jobs.submit(database, specification=SPEC)
    jobs.claim(database)

    stranded = jobs.reconcile_interrupted(database)

    assert stranded == [job_id]
    found = jobs.read(database, job_id)
    assert found is not None and found["state"] == "failed"
    assert "stopped while this job was running" in found["detail"]


def test_a_queued_job_survives_a_restart_untouched() -> None:
    """It never started, so it survives honestly and the worker will take it.
    Failing it would throw away work nobody had begun."""
    database = prepare_database(":memory:")
    job_id = jobs.submit(database, specification=SPEC)

    assert jobs.reconcile_interrupted(database) == []
    found = jobs.read(database, job_id)
    assert found is not None and found["state"] == "queued"


def test_the_specification_comes_back_parsed_rather_than_as_a_string() -> None:
    """A client that has to json.loads a string out of a JSON document is being
    handed the storage format rather than an answer."""
    database = prepare_database(":memory:")
    job_id = jobs.submit(database, specification=SPEC)

    found = jobs.read(database, job_id)

    assert found is not None
    assert found["specification"]["suite"] == "performance-basic"
