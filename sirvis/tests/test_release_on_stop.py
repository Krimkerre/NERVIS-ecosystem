"""SIRVIS gives back the models it loaded when it stops (§9).

It released nothing until 12 September 2026. A model loaded for the menu bar,
the dashboard's Runtime screen, RAVIS or an interrupted benchmark stayed in LM
Studio after the service stopped, held by nobody, and the next SIRVIS reported
it as foreign. The owner's decision that day was that stopping unloads what
SIRVIS loaded, so stopping the stack frees the memory.

What that has to mean, one test each: every session released and every model
SIRVIS loaded unloaded, once; a model somebody else loaded left exactly where it
is; a runtime that never answers costing the budget and no more — through a
fake, and through the real adapter, whose `lms` call used to block the very loop
the budget's timer runs on; a benchmark's own release after the stop doing
nothing; a load under way unloaded once it lands, or named when it does not;
nothing held meaning the runtime is asked nothing; and the service itself doing
all of this when it stops, saying why in its log.

No test reaches a live runtime (runbook §14.5): every runtime here is a fake,
and the one real adapter has its `lms` call replaced.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
import threading
import time
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from sirvis.config import Settings
from sirvis.resources import ResourceManager
from sirvis.runtimes import LMStudioAdapter, LoadedModel


class FakeRuntime:
    """A runtime whose loads, unloads and silences the test decides.

    `resident` is what somebody else loaded before SIRVIS asked — a model loaded
    by hand in LM Studio — so adoption can be driven rather than described.
    """

    def __init__(self, resident: tuple[str, ...] = ()) -> None:
        self.resident = list(resident)
        self.loads: list[str] = []
        self.unloads: list[str] = []
        # Every call of any kind, so "asked the runtime nothing" is checkable.
        self.calls = 0
        self.load_started = asyncio.Event()
        # Set to an Event to hold the call there; never setting it is a runtime
        # that accepted the request and went silent.
        self.load_gate: asyncio.Event | None = None
        self.unload_gate: asyncio.Event | None = None

    async def load(self, model_key: str, config: dict[str, Any] | None = None) -> LoadedModel:
        del config
        self.calls += 1
        self.load_started.set()
        if self.load_gate is not None:
            await self.load_gate.wait()
        self.loads.append(model_key)
        return LoadedModel(model_key=model_key, state="loaded")

    async def unload(self, model_key: str) -> None:
        self.calls += 1
        if self.unload_gate is not None:
            await self.unload_gate.wait()
        self.unloads.append(model_key)

    async def list_loaded_models(self) -> list[LoadedModel]:
        self.calls += 1
        held = [*self.resident, *(key for key in self.loads if key not in self.unloads)]
        return [LoadedModel(model_key=key, state="loaded") for key in held]


# ── The manager ──────────────────────────────────────────────────────────────


async def test_stopping_releases_every_session_and_unloads_what_sirvis_loaded() -> None:
    """The decision itself: three clients, two models, all given back — and the
    model two of them shared unloaded once, not once per client."""
    runtime = FakeRuntime()
    manager = ResourceManager(runtime)
    menu = await manager.acquire(owner="menubar", model_key="qwen/qwen3-1.7b")
    dashboard = await manager.acquire(owner="dashboard", model_key="qwen/qwen3-1.7b")
    ravis = await manager.acquire(owner="ravis", model_key="google/gemma-4-e4b")

    report = await manager.release_on_stop(budget_seconds=1.0)

    assert sorted(runtime.unloads) == ["google/gemma-4-e4b", "qwen/qwen3-1.7b"]
    assert sorted(report.unloaded) == ["google/gemma-4-e4b", "qwen/qwen3-1.7b"]
    assert {lease.session_id for lease in report.sessions} == {
        menu.session_id, dashboard.session_id, ravis.session_id,
    }
    assert report.finished is True
    assert report.not_unloaded == ()
    assert manager.residency()["holdings"] == []
    assert manager.residency()["leases"] == []
    # Nobody is left being told they hold a model that has gone.
    assert manager.renew(menu.session_id) is None


async def test_stopping_leaves_a_model_sirvis_did_not_load_where_it_is() -> None:
    """§9: never unload a resource owned by another client. One model was loaded
    by hand and borrowed through a session; another was loaded by LM Studio and
    never touched. Stopping releases the session and unloads neither."""
    runtime = FakeRuntime(resident=("loaded-by-hand", "loaded-by-lm-studio"))
    manager = ResourceManager(runtime)
    await manager.acquire(owner="benchmark", model_key="loaded-by-hand")
    await manager.acquire(owner="menubar", model_key="ours")

    report = await manager.release_on_stop(budget_seconds=1.0)

    assert runtime.unloads == ["ours"]
    assert report.left_loaded == ("loaded-by-hand",)
    assert report.unloaded == ("ours",)
    assert report.not_unloaded == ()
    assert manager.residency()["holdings"] == []


async def test_a_runtime_that_never_answers_costs_the_budget_and_no_more() -> None:
    """A stop that waited on a silent runtime would be killed by the launcher
    with nothing logged. The leases go first regardless, and the report names
    what was not confirmed."""
    runtime = FakeRuntime()
    manager = ResourceManager(runtime)
    await manager.acquire(owner="menubar", model_key="ours")
    runtime.unload_gate = asyncio.Event()  # never set

    started = time.monotonic()
    report = await manager.release_on_stop(budget_seconds=0.2)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
    assert report.finished is False
    assert report.unloaded == ()
    assert report.not_unloaded == ("ours",)
    assert manager.residency()["leases"] == []


async def test_one_unload_that_never_answers_does_not_stop_the_others_being_tried() -> None:
    """The unloads on the way out run at once. One after another, the model
    sorted first hanging meant the second was never asked for, and stayed in
    memory for no reason but its name."""

    class OneHangs(FakeRuntime):
        async def unload(self, model_key: str) -> None:
            if model_key == "a-hangs":
                await asyncio.Event().wait()
            await super().unload(model_key)

    runtime = OneHangs()
    manager = ResourceManager(runtime)
    await manager.acquire(owner="menubar", model_key="a-hangs")
    await manager.acquire(owner="dashboard", model_key="b-answers")

    report = await manager.release_on_stop(budget_seconds=0.2)

    assert runtime.unloads == ["b-answers"]
    assert report.unloaded == ("b-answers",)
    assert report.not_unloaded == ("a-hangs",)
    assert report.finished is False


async def test_the_adapters_own_unload_cannot_hold_the_stop_past_its_budget() -> None:
    """The fake above proves the budget; this proves it holds for the real adapter.

    `lms unload` is a blocking subprocess, and it used to run on the event loop,
    where the budget's timer cannot fire until the call returns. Against the
    real adapter the budget was therefore a comment: a runtime that accepted the
    command and went quiet held the stop for the CLI's whole timeout, past the
    launcher's forced kill. Only `_run_lms` is replaced here — the adapter's own
    `unload` is what runs."""
    answered = threading.Event()

    def silent_lms(arguments: list[str], timeout: float, **rest: object) -> str:
        del arguments, timeout, rest
        answered.wait(3.0)  # LM Studio took the command and said nothing
        return ""

    async def load(model_key: str, config: dict[str, Any] | None = None) -> LoadedModel:
        del config
        return LoadedModel(model_key=model_key, state="loaded")

    async def nothing_resident() -> list[LoadedModel]:
        return []

    adapter = LMStudioAdapter("http://runtime.invalid", lms_path="/nonexistent/lms")
    adapter._run_lms = silent_lms  # type: ignore[method-assign]
    adapter.load = load  # type: ignore[method-assign]
    adapter.list_loaded_models = nothing_resident  # type: ignore[method-assign]
    manager = ResourceManager(adapter)
    await manager.acquire(owner="menubar", model_key="qwen/qwen3-1.7b")

    started = time.monotonic()
    try:
        report = await manager.release_on_stop(budget_seconds=0.2)
        elapsed = time.monotonic() - started
    finally:
        answered.set()  # let the thread go, so the test does not wait on it

    assert elapsed < 1.5
    assert report.finished is False
    assert report.not_unloaded == ("qwen/qwen3-1.7b",)


async def test_a_benchmark_stopped_with_the_service_is_not_unloaded_twice() -> None:
    """The lifespan cancels the queue, and a cancelled benchmark still runs its
    `finally` — which releases a session the stop has already ended. That second
    release must find nothing to do."""
    runtime = FakeRuntime()
    manager = ResourceManager(runtime)
    running = asyncio.Event()

    async def benchmark() -> None:
        lease = await manager.acquire(owner="benchmark", model_key="ours")
        try:
            running.set()
            await asyncio.Event().wait()  # mid-run
        finally:
            # The engine's own release, as `run_experiment` does it.
            await manager.release(lease.session_id)

    run = asyncio.create_task(benchmark())
    await running.wait()
    run.cancel()  # what the lifespan does to the queue
    await manager.release_on_stop(budget_seconds=1.0)
    with pytest.raises(asyncio.CancelledError):
        await run

    assert runtime.unloads == ["ours"]


async def test_a_load_under_way_when_sirvis_stops_is_unloaded_once_it_lands() -> None:
    """A benchmark stopped mid-load: its acquire is cancelled, the shielded load
    carries on, and when it lands nobody is left to record it. Without this the
    model would be loaded by SIRVIS and held by nobody — the defect itself."""
    runtime = FakeRuntime()
    runtime.load_gate = asyncio.Event()
    manager = ResourceManager(runtime)
    benchmark = asyncio.create_task(manager.acquire(owner="benchmark", model_key="ours"))
    await runtime.load_started.wait()
    benchmark.cancel()
    asyncio.get_running_loop().call_later(0.05, runtime.load_gate.set)

    report = await manager.release_on_stop(budget_seconds=1.0)

    assert runtime.loads == ["ours"]
    assert runtime.unloads == ["ours"]
    assert report.unloaded == ("ours",)
    assert report.finished is True


async def test_a_load_that_does_not_land_in_time_is_named_rather_than_waited_for() -> None:
    runtime = FakeRuntime()
    runtime.load_gate = asyncio.Event()
    manager = ResourceManager(runtime)
    benchmark = asyncio.create_task(manager.acquire(owner="benchmark", model_key="ours"))
    await runtime.load_started.wait()
    benchmark.cancel()

    report = await manager.release_on_stop(budget_seconds=0.1)

    assert report.finished is False
    assert report.still_loading == ("ours",)
    runtime.load_gate.set()  # let the load finish, so no task outlives the test
    await asyncio.sleep(0)


async def test_stopping_with_nothing_held_asks_the_runtime_nothing() -> None:
    """The ordinary stop. Something loaded by hand is there; SIRVIS holds nothing,
    so it has nothing to say to the runtime — not even a question."""
    runtime = FakeRuntime(resident=("loaded-by-hand",))
    manager = ResourceManager(runtime)

    report = await manager.release_on_stop(budget_seconds=1.0)

    assert runtime.calls == 0
    assert report.sessions == ()
    assert report.unloaded == report.left_loaded == report.not_unloaded == ()
    assert report.finished is True


# ── The service ──────────────────────────────────────────────────────────────


def test_the_service_releases_what_it_loaded_when_it_stops(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """End to end through the lifespan: a model held while the service runs is
    unloaded when it stops, and the log says stopping is why."""
    from fastapi.testclient import TestClient

    from sirvis.app import create_app

    app = create_app(settings)
    runtime = FakeRuntime()
    manager = ResourceManager(runtime)
    app.state.resources = manager
    caplog.set_level(logging.INFO, logger="sirvis.app")

    with TestClient(app) as client:
        client.portal.call(partial(manager.acquire, owner="menubar", model_key="qwen/qwen3-1.7b"))
        assert runtime.unloads == []

    assert runtime.unloads == ["qwen/qwen3-1.7b"]
    assert "unloaded qwen/qwen3-1.7b because SIRVIS stopped" in caplog.text
    assert "(owner menubar; qwen/qwen3-1.7b) because SIRVIS stopped" in caplog.text


# ── The stop, against the launcher's clock ───────────────────────────────────

# uvicorn's `Server.shutdown` sleeps this long after asking connections to close
# and before it starts waiting on them (uvicorn 0.52, `server.py`). It is not
# configurable, so it is stated here rather than read.
UVICORN_PAUSE_SECONDS = 0.1

LAUNCHER = Path(__file__).resolve().parents[2] / "tools" / "run.py"


def _launcher_grace_for_sirvis() -> float:
    """How long `tools/run.py stop` waits before force-killing SIRVIS, from its source.

    Parsed rather than imported: importing the launcher runs its module-level
    setup, and all this needs is two literals.
    """
    if not LAUNCHER.is_file():
        pytest.skip("tools/run.py is not beside this SIRVIS, so its wait cannot be read")
    literals: dict[str, Any] = {}
    for node in ast.parse(LAUNCHER.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            with contextlib.suppress(ValueError, TypeError):
                literals[node.targets[0].id] = ast.literal_eval(node.value)
    grace = literals.get("GRACE_BEFORE_KILL", {})
    return float(grace.get("SIRVIS", literals["DEFAULT_GRACE_BEFORE_KILL"]))


def test_the_whole_stop_fits_inside_the_launchers_wait_with_a_second_to_spare() -> None:
    """uvicorn's pause, the wait for requests in flight, the release and the
    event drain, back to back, against `GRACE_BEFORE_KILL` in `tools/run.py`.
    Change either side and this says whether the release still runs before
    the forced kill."""
    from sirvis.app import EVENT_DRAIN_SECONDS, GRACEFUL_SHUTDOWN_SECONDS, STOP_BUDGET_SECONDS

    worst = (UVICORN_PAUSE_SECONDS + GRACEFUL_SHUTDOWN_SECONDS + STOP_BUDGET_SECONDS
             + EVENT_DRAIN_SECONDS)

    assert (GRACEFUL_SHUTDOWN_SECONDS, STOP_BUDGET_SECONDS, EVENT_DRAIN_SECONDS) == (1, 6.0, 3.0)
    assert worst + 1.0 <= _launcher_grace_for_sirvis()


def test_serve_bounds_uvicorns_wait_for_requests_in_flight(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unbounded, a session open waiting on a big load kept SIRVIS's release
    from starting until the launcher's forced kill ended the process."""
    from sirvis import cli
    from sirvis.app import GRACEFUL_SHUTDOWN_SECONDS

    served: dict[str, Any] = {}

    def serve(app: object, **options: Any) -> None:
        del app
        served.update(options)

    monkeypatch.setattr(cli.uvicorn, "run", serve)

    assert cli._run_serve(settings) == cli.EXIT_OK
    assert served["timeout_graceful_shutdown"] == GRACEFUL_SHUTDOWN_SECONDS
