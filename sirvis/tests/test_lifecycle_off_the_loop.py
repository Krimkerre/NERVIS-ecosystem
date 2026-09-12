"""Blocking calls stay off SIRVIS's event loop: `lms`, and the probes around it (§9, §15.4).

LM Studio has no HTTP load or unload, so lifecycle goes through the `lms` CLI,
and the CLI is a blocking subprocess. Called straight from a coroutine it froze
the whole service for as long as it ran: loading a big model meant health
checks timing out, and the dashboard and menu bar showing SIRVIS down until the
model arrived. Found 12 September 2026, after the stop budget met the same
thing in unloads.

Each of the first tests starts a call whose blocking stand-in holds until the
test lets it go, then asks the service something meanwhile. On the loop, the
stand-in blocks everything and the question is only reached once the call has
finished; off it, the answer arrives while the call is still running.

The last two are what the blocked loop used to prevent by accident: an acquire
arriving in the middle of an unload — of its own model, or of one the ceiling
has no room for until the unload gives its memory back.

No test reaches a live runtime (runbook §14.5): every `lms` call is replaced.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpx
import pytest
from tests.conftest_lmstudio import transport

from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.resources import ResourceManager
from sirvis.runtimes import LMStudioAdapter, LoadedModel
from sirvis.telemetry.memory import MemoryProbe, MemorySample, MemoryWatcher, _next_tick


class Held:
    """A blocking call that holds until the test lets it go."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def hold(self) -> None:
        self.started.set()
        # Bounded, so a regression fails the test rather than hanging it.
        self.release.wait(2.0)
        self.finished.set()


async def _once_started(held: Held) -> None:
    """Yield to the loop until the held call has begun.

    On a blocked loop this resumes only after the call has *finished*, which is
    exactly what the `finished` check after it catches.
    """
    while not held.started.is_set():
        await asyncio.sleep(0.005)


def _client(app: Any) -> httpx.AsyncClient:
    """Requests on the test's own loop — the loop a blocking call would stall."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _holding_lms(held: Held) -> tuple[LMStudioAdapter, list[list[str]]]:
    """The real adapter, with only its `lms` call replaced by one that holds."""
    ran: list[list[str]] = []
    adapter = LMStudioAdapter("http://runtime.invalid", lms_path="/nonexistent/lms")

    def lms(arguments: list[str], timeout: float, **rest: object) -> str:
        del timeout, rest
        ran.append(arguments)
        held.hold()
        return ""

    async def nothing_resident() -> list[LoadedModel]:
        return []

    adapter._run_lms = lms  # type: ignore[method-assign]
    adapter.list_loaded_models = nothing_resident  # type: ignore[method-assign]
    return adapter, ran


# ── The service keeps answering while `lms` runs ─────────────────────────────


async def test_a_slow_lms_load_leaves_health_and_residency_answering(settings: Settings) -> None:
    """The case the dashboard and menu bar saw: SIRVIS reported down for the
    length of a load."""
    held = Held()
    adapter, ran = _holding_lms(held)
    app = create_app(settings, runtime=adapter)
    manager: ResourceManager = app.state.resources
    loading = asyncio.create_task(manager.acquire(owner="menubar", model_key="qwen/qwen3-1.7b"))

    await _once_started(held)
    async with _client(app) as client:
        health = await client.get("/ecosystem/health")
        residency = await client.get("/api/v1/runtime/residency")
    answered_while_loading = not held.finished.is_set()
    held.release.set()
    await loading

    assert answered_while_loading
    assert health.status_code == 200
    assert residency.status_code == 200
    assert ran == [["load", "qwen/qwen3-1.7b", "--yes"]]


async def test_reading_installed_builds_leaves_the_loop_free() -> None:
    """`lms ls`, twice, under every model listing — and so under residency,
    every acquire and every load's confirmation."""
    held = Held()
    adapter = LMStudioAdapter(
        "http://runtime.invalid",
        client=httpx.AsyncClient(transport=transport()),
        lms_path="/nonexistent/lms",
    )

    def builds() -> None:
        held.hold()

    adapter._local_builds = builds  # type: ignore[method-assign]
    adapter._local_sizes = lambda: None  # type: ignore[method-assign]
    listing = asyncio.create_task(adapter.list_models())

    await _once_started(held)
    answered_while_listing = not held.finished.is_set()
    held.release.set()
    models = await listing

    assert answered_while_listing
    assert models  # the runtime's catalogue still comes back


async def test_detecting_the_machine_leaves_the_loop_free(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not `lms`, but the same failure on a request path: `/system` runs a
    string of blocking probes on every read."""
    from sirvis.api import routes

    held = Held()
    real = routes.detect_system

    def slow_detect() -> Any:
        held.hold()
        return real()

    monkeypatch.setattr(routes, "detect_system", slow_detect)
    app = create_app(settings)

    async with _client(app) as client:
        system = asyncio.create_task(client.get("/api/v1/system"))
        await _once_started(held)
        health = await client.get("/ecosystem/health")
        answered_while_detecting = not held.finished.is_set()
        held.release.set()
        response = await system

    assert answered_while_detecting
    assert health.status_code == 200
    assert response.status_code == 200


async def test_confirming_the_loaded_build_leaves_the_loop_free(tmp_path: Any) -> None:
    """`lms ps`, asked twice by every benchmark — and the queue runs benchmarks
    on the service's own loop, so each ask stalled every request to SIRVIS."""
    from tests.test_m6_benchmark import FakeRuntime, _run

    from sirvis.runtimes.variants import LoadedVariant

    held = Held()

    class AsksLmsPs(FakeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.asked = 0

        def confirm_variant(self, model_key: str) -> LoadedVariant | None:
            self.asked += 1
            if self.asked == 1:
                held.hold()  # before the load, with nothing resident to name yet
                return None
            return LoadedVariant(model_key=model_key, family=model_key,
                                 runtime_format="gguf", quantization="Q4_K_M")

    runtime = AsksLmsPs()
    run = asyncio.create_task(_run(runtime, results_root=tmp_path))

    await _once_started(held)
    answered_while_confirming = not held.finished.is_set()
    held.release.set()
    await run

    assert answered_while_confirming
    assert runtime.asked == 2  # and the gate after the load still asked


# ── What the blocked loop used to prevent by accident ────────────────────────


class GatedRuntime:
    """A runtime whose unloads hold until the test opens the gate."""

    def __init__(self) -> None:
        self.loads: list[str] = []
        self.unloads: list[str] = []
        self.unload_started = asyncio.Event()
        self.unload_gate = asyncio.Event()

    async def load(self, model_key: str, config: dict[str, Any] | None = None) -> LoadedModel:
        del config
        self.loads.append(model_key)
        return LoadedModel(model_key=model_key, state="loaded")

    async def unload(self, model_key: str) -> None:
        self.unload_started.set()
        await self.unload_gate.wait()
        self.unloads.append(model_key)

    async def list_loaded_models(self) -> list[LoadedModel]:
        # Resident until its unload has actually returned, as in LM Studio.
        resident = [k for k in self.loads if self.loads.count(k) > self.unloads.count(k)]
        return [LoadedModel(model_key=k, state="loaded") for k in dict.fromkeys(resident)]


async def test_an_acquire_during_an_unload_of_its_model_waits_and_loads_it_again() -> None:
    """Otherwise it shares a holding about to vanish: the client is handed a
    lease on a model that is gone a moment later."""
    runtime = GatedRuntime()
    manager = ResourceManager(runtime)
    first = await manager.acquire(owner="menubar", model_key="m")
    releasing = asyncio.create_task(manager.release(first.session_id))
    await runtime.unload_started.wait()

    second = asyncio.create_task(manager.acquire(owner="dashboard", model_key="m"))
    await asyncio.sleep(0.05)
    waited_for_the_unload = not second.done()
    runtime.unload_gate.set()

    assert await releasing == ["m"]
    lease = await second
    holdings = manager.residency()["holdings"]
    assert waited_for_the_unload
    assert runtime.loads == ["m", "m"]
    assert [(h["model_key"], h["owned"], h["sessions"]) for h in holdings] == [
        ("m", True, [lease.session_id])
    ]


async def test_an_acquire_with_no_room_until_an_unload_finishes_waits_for_it() -> None:
    """A ceiling of one, and the one model still leaving. Loading anyway would
    put two models where the ceiling says one; refusing would turn a sub-second
    unload into a "busy" the client could have waited out."""
    runtime = GatedRuntime()
    manager = ResourceManager(runtime, max_loaded=1)
    first = await manager.acquire(owner="menubar", model_key="a")
    releasing = asyncio.create_task(manager.release(first.session_id))
    await runtime.unload_started.wait()

    second = asyncio.create_task(manager.acquire(owner="dashboard", model_key="b"))
    await asyncio.sleep(0.05)
    waited_for_the_memory = not second.done()
    runtime.unload_gate.set()
    await releasing
    await second

    assert waited_for_the_memory
    assert runtime.loads == ["a", "b"]


# ── Benchmark sampling ───────────────────────────────────────────────────────
#
# A benchmark reads memory at named points, polls it four times a second while
# a generation runs, and reads thermal state before and after — `vm_stat`,
# `sysctl` and `osascript`, all blocking. The queue runs benchmarks on the
# service's loop, so the dashboard and menu bar could see SIRVIS go quiet
# mid-run.


class HoldingProbe(MemoryProbe):
    """A memory probe whose first reading holds, like a `vm_stat` that will not return."""

    def __init__(self, held: Held) -> None:
        super().__init__()
        self._held = held

    def sample(self, point: str, include_swap: bool = True) -> MemorySample:
        if not self._held.started.is_set():
            self._held.hold()
        return MemorySample(point=point, captured_at=0.0, available_bytes=8 * 2**30,
                            swap_used_bytes=0 if include_swap else None)


async def test_a_memory_poll_that_hangs_mid_generation_leaves_health_answering(
    settings: Settings,
) -> None:
    held = Held()
    app = create_app(settings)

    async with MemoryWatcher(HoldingProbe(held), interval=0.25), _client(app) as client:
        await _once_started(held)
        health = await client.get("/ecosystem/health")
        answered_while_reading = not held.finished.is_set()
        held.release.set()

    assert answered_while_reading
    assert health.status_code == 200


async def test_a_labelled_reading_that_hangs_leaves_health_answering(
    settings: Settings, tmp_path: Any
) -> None:
    """The reading at the baseline point, before anything loads."""
    from tests.test_m6_benchmark import SNAPSHOT, FakeRuntime, Ticking, _spec

    from sirvis.benchmarks.engine import run_experiment
    from sirvis.storage import prepare_database

    held = Held()
    runtime = FakeRuntime()
    app = create_app(settings)
    run = asyncio.create_task(run_experiment(
        _spec(), runtime=runtime, resources=ResourceManager(runtime),
        database=prepare_database(":memory:"), results_root=str(tmp_path),
        probe=HoldingProbe(held), snapshot=SNAPSHOT, clock=Ticking(),
        thermal=lambda: "nominal",
    ))

    await _once_started(held)
    async with _client(app) as client:
        health = await client.get("/ecosystem/health")
    answered_while_reading = not held.finished.is_set()
    held.release.set()
    await run

    assert answered_while_reading
    assert health.status_code == 200


async def test_a_thermal_reading_that_hangs_leaves_health_answering(
    settings: Settings, tmp_path: Any
) -> None:
    """Thermal state is an `osascript` call with a ten-second timeout."""
    from tests.test_m6_benchmark import FakeRuntime, _run

    held = Held()

    def thermal() -> str:
        held.hold()
        return "nominal"

    app = create_app(settings)
    run = asyncio.create_task(_run(FakeRuntime(), results_root=tmp_path, thermal=thermal))

    await _once_started(held)
    async with _client(app) as client:
        health = await client.get("/ecosystem/health")
    answered_while_reading = not held.finished.is_set()
    held.release.set()
    await run

    assert answered_while_reading
    assert health.status_code == 200


def test_a_tick_a_slow_reading_overran_is_skipped_rather_than_queued() -> None:
    """Readings run one at a time on a quarter-second schedule. One that took
    0.6 s overran the ticks at 0.25 and 0.5; the next waits for 0.75 rather than
    starting at once, twice over, to catch up."""
    assert _next_tick(0.0, 0.25, now=0.6) == 0.75
    assert _next_tick(0.0, 0.25, now=0.1) == 0.25  # a prompt reading keeps the schedule
    assert _next_tick(0.0, 0.25, now=0.25) == 0.25  # exactly on time is not late
