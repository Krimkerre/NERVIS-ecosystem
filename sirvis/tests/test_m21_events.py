"""M21 — a benchmark is visible from outside, and never at its own expense.

SIRVIS.md's exit for this milestone is two clauses: *"a benchmark job trace is
visible externally; tracing failure does not block benchmark execution"*. The
second is the one worth testing hardest — a benchmark takes minutes and spends
gigabytes, and a dashboard being down may not cost it anything.

A benchmark is also the one operation here that is **not** an HTTP request, so
the trace has to be minted rather than inherited. That is the difference from
RAVIS's half of Stage 7 and most of what these tests are about.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from ecosystem_protocol import EventPublisher

from sirvis.storage import prepare_database
from sirvis.storage.repositories import start_run, trace_of_run


def a_publisher(**kwargs: Any) -> EventPublisher:
    return EventPublisher(
        service_type="sirvis", service_id="sirvis-1", machine_id="m1",
        base_url="http://127.0.0.1:8790", **kwargs,
    )


# ── The trace lives on the run, not in a local ──────────────────────────────


def test_a_run_records_the_trace_it_belongs_to() -> None:
    """On the row, so a closing event can find it however far from the opening
    one it is emitted — and so M14's enqueue-over-HTTP can later write the
    caller's trace into the same column without the engine changing."""
    database = prepare_database(":memory:")
    database.connection.execute(
        "INSERT INTO experiment (experiment_id, suite_id, suite_version,"
        " environment_mode, payload) VALUES ('e1', 's', '1', 'controlled', '{}')"
    )

    run_id = start_run(
        database, "e1", runtime_key="lmstudio", runtime_snapshot={},
        machine_snapshot_id=None, results_path=None, trace_id="t" * 32,
    )

    assert trace_of_run(database, run_id) == "t" * 32


def test_a_run_with_no_trace_reports_the_absence_rather_than_inventing_one() -> None:
    """Every run recorded before migration 7 has none, and a fresh id would
    correlate with nothing while looking exactly like one that correlates."""
    database = prepare_database(":memory:")
    database.connection.execute(
        "INSERT INTO experiment (experiment_id, suite_id, suite_version,"
        " environment_mode, payload) VALUES ('e1', 's', '1', 'controlled', '{}')"
    )

    run_id = start_run(
        database, "e1", runtime_key="lmstudio", runtime_snapshot={},
        machine_snapshot_id=None, results_path=None,
    )

    assert trace_of_run(database, run_id) == ""


def test_the_column_exists_and_is_indexed() -> None:
    """A trace lookup over every run this machine has ever done is the one query
    a cross-service timeline makes."""
    database = prepare_database(":memory:")

    columns = {
        row["name"]
        for row in database.connection.execute("PRAGMA table_info(benchmark_run)")
    }
    indexes = {
        row["name"]
        for row in database.connection.execute("PRAGMA index_list(benchmark_run)")
    }

    assert "trace_id" in columns
    assert "benchmark_run_by_trace" in indexes


# ── Identity ────────────────────────────────────────────────────────────────


def test_the_machine_id_is_this_installation_s_and_not_a_constant() -> None:
    """It was `uuid5(NAMESPACE_DNS, "sirvis-machine")` — the same value on every
    SIRVIS in existence — while the stored identity sat two modules away.

    It reaches `source.machine_id` on every event from here on, where a constant
    would have every installation's events claiming to be one machine's.
    """
    from sirvis.app import create_app
    from sirvis.config import Settings

    def machine_of(path: str) -> str:
        api = create_app(Settings(database_path=path, _env_file=None))  # type: ignore[call-arg]
        return str(api.state.machine_id)

    first, second = machine_of(":memory:"), machine_of(":memory:")

    assert first and second
    assert first != second, "two installations reported the same machine"


# ── Tracing failure does not block a benchmark ──────────────────────────────


def test_a_benchmark_runs_with_no_collector_configured() -> None:
    """SIRVIS.md: telemetry export is optional. A benchmark must run on a laptop
    with nothing else installed."""
    publisher = EventPublisher(service_type="sirvis")

    publisher.emit("sirvis.benchmark.started", trace_id="t" * 32, data={"run_id": "r"})

    assert not publisher.enabled
    assert publisher.snapshot()["queued"] == 0


def test_a_collector_that_refuses_costs_the_benchmark_nothing() -> None:
    class Refusing:
        async def post(self, url: str, *, json: Any, timeout: float) -> Any:
            del url, json, timeout
            raise ConnectionError("no hub")

    publisher = a_publisher()
    publisher.emit("sirvis.benchmark.started", trace_id="t" * 32, data={"run_id": "r"})

    asyncio.run(publisher.flush(Refusing()))

    assert publisher.snapshot()["queued"] == 1, "the event is kept, not lost"
    assert publisher.snapshot()["failures"] == 1


def test_a_drain_on_the_way_out_is_bounded() -> None:
    """A benchmark process is short-lived: the closing event has to be sent
    before the interpreter exits, and waiting for a dead hub must not hang the
    command."""

    class Hanging:
        async def post(self, url: str, *, json: Any, timeout: float) -> Any:
            del url, json, timeout
            await asyncio.sleep(30)

    publisher = a_publisher()
    publisher.emit("sirvis.benchmark.completed", trace_id="t" * 32, data={})

    async def exercise() -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await publisher.drain(Hanging(), deadline=0.2)
        return loop.time() - started

    assert asyncio.run(exercise()) < 3.0


# ── What must never leave the machine ───────────────────────────────────────


@pytest.mark.parametrize(
    "payload",
    [
        {"prompt": "the frozen question"},
        {"spec": {"tests": [{"prompt": "nested one level down"}]}},
        {"messages": [{"content": "a model's answer"}]},
        {"api_key": "sk-live-1234"},
    ],
)
def test_a_prompt_or_an_answer_never_reaches_an_event(payload: dict[str, Any]) -> None:
    """§9 forbids prompts and model output in telemetry, and `spec.as_dict()` —
    the most tempting payload for a started event — embeds every one of them.

    The nested case is the one that matters: the shared `redact` is a
    comprehension over top-level keys and would let it straight through.
    """
    publisher = a_publisher()

    publisher.emit("sirvis.benchmark.started", trace_id="t" * 32, data=payload)

    body = str(publisher.snapshot()) + str(list(publisher._pending))
    for leaked in ("frozen question", "nested one level down", "a model's answer",
                   "sk-live-1234"):
        assert leaked not in body


def test_the_home_directory_is_folded_out_of_a_failure_message() -> None:
    """A runtime failure quotes the `lms` path, which defaults under the
    operator's home and therefore contains their username."""
    from pathlib import Path

    from sirvis.benchmarks.engine import _without_home

    detail = f"could not start {Path.home()}/.lmstudio/bin/lms"

    folded = _without_home(detail)

    assert str(Path.home()) not in folded
    assert "~/.lmstudio/bin/lms" in folded


def test_the_capability_says_it_publishes() -> None:
    from fastapi.testclient import TestClient

    from sirvis.app import create_app
    from sirvis.config import Settings

    client = TestClient(create_app(Settings(database_path=":memory:", _env_file=None)))  # type: ignore[call-arg]
    body = client.get("/ecosystem/capabilities").json()

    states = {c["id"]: c["state"] for c in body["capabilities"]}
    assert states["sirvis.events"] == "available"


def test_the_events_secret_reaches_the_publisher() -> None:
    """NERVIS 0.34.18 refuses a batch that proves no sender (the security review's S7); the
    launcher hands SIRVIS its secret as `SIRVIS_NERVIS_EVENTS_SECRET`, for the service and the
    benchmark command alike."""
    from sirvis.app import create_app
    from sirvis.cli import _publisher
    from sirvis.config import Settings
    from sirvis.storage import prepare_database

    settings = Settings(  # type: ignore[call-arg]
        database_path=":memory:", nervis_events_secret="sirvis-events-secret-for-this-test",
        _env_file=None,
    )
    assert create_app(settings).state.events._secret == "sirvis-events-secret-for-this-test"
    database = prepare_database(":memory:")
    assert _publisher(settings, database)._secret == "sirvis-events-secret-for-this-test"
