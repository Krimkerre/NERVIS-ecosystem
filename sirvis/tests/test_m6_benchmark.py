"""M6 — the single-model benchmark engine (§11).

    `sirvis benchmark run examples/basic.yaml` persists a valid result.

Every test here runs against a recorded runtime (§14.5), and that constraint is
not a formality for this milestone: M6 is the first one whose job is to load
models, and four were loaded onto the developer's machine during this build
without anyone intending it. The live half of the exit criterion — an actual
load, generation and unload against LM Studio — is verified by hand and recorded
in STATUS.md, because it takes minutes and spends the machine's memory.

What is asserted here is everything that decides whether a stored result can be
believed: that repetitions are preserved, that a warm model does not publish a
load time, that a token count nobody reported is not presented as measured, and
that a run which failed leaves a truthful row rather than nothing.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
from tests.conftest_lmstudio import INSTALLED

from sirvis.benchmarks import BenchmarkTest, ExperimentSpec, GenerationConfig, run_experiment
from sirvis.core.evidence import EvidenceKind, Validity
from sirvis.errors import ModelNotFoundError
from sirvis.resources import ResourceManager
from sirvis.runtimes.base import GenerationChunk, LoadedModel, RuntimeUnavailableError
from sirvis.storage import RunState, prepare_database
from sirvis.telemetry import MemoryProbe, MemorySample, SystemSnapshot

MODEL = "qwen2.5-coder-7b-instruct"


class FakeRuntime:
    """A runtime that answers instantly and remembers what it was asked to do.

    Serves both protocols the engine sits between: the lifecycle slice the
    Resource Manager drives, and the reading slice the engine measures through.
    One object rather than two so a test can assert that the engine loaded
    exactly once for work it did through a completely different interface.
    """

    def __init__(
        self,
        *,
        resident: tuple[str, ...] = (),
        content: tuple[str, ...] = ("Hel", "lo"),
        usage: dict[str, Any] | None = None,
        finish: str | None = "stop",
        effective_context: int = 8192,
        ignored: tuple[str, ...] = (),
        fail_after: int | None = None,
    ) -> None:
        self.content = content
        self.usage = usage
        self.finish = finish
        self.effective_context = effective_context
        self.ignored = ignored
        self.fail_after = fail_after
        self.generations = 0
        # Every request, so a test can assert that the prompt and the generation
        # settings in the specification are what actually reached the runtime.
        self.requests: list[tuple[str, list[dict[str, Any]], dict[str, Any]]] = []
        self.loads: list[tuple[str, dict[str, Any] | None]] = []
        self.unloads: list[str] = []
        self.resident = {
            key: LoadedModel(model_key=key, state="loaded",
                             effective={"context_length": effective_context})
            for key in resident
        }

    async def list_models(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in INSTALLED]

    async def list_loaded_models(self) -> list[LoadedModel]:
        return list(self.resident.values())

    async def load(self, model_key: str, config: dict[str, Any] | None = None) -> LoadedModel:
        self.loads.append((model_key, config))
        loaded = LoadedModel(
            model_key=model_key, state="loaded", requested=dict(config or {}),
            effective={"context_length": self.effective_context},
            ignored=list(self.ignored),
        )
        self.resident[model_key] = loaded
        return loaded

    async def unload(self, model_key: str) -> None:
        self.unloads.append(model_key)
        self.resident.pop(model_key, None)

    async def stream_generate(
        self, model_key: str, messages: list[dict[str, Any]], **options: Any
    ) -> AsyncIterator[GenerationChunk]:
        self.generations += 1
        self.requests.append((model_key, messages, dict(options)))
        if self.fail_after is not None and self.generations > self.fail_after:
            raise RuntimeUnavailableError("the runtime stopped answering")
        for piece in self.content:
            yield GenerationChunk(content=piece)
        yield GenerationChunk(finish_reason=self.finish, usage=self.usage)


class SteadyProbe(MemoryProbe):
    """Memory that never moves, so a test asserts on the engine and not the machine."""

    def sample(self, point: str, include_swap: bool = True) -> MemorySample:
        return MemorySample(point=point, captured_at=0.0, available_bytes=8 * 2**30,
                            total_bytes=16 * 2**30,
                            swap_used_bytes=0 if include_swap else None)


class Ticking:
    """A clock advancing half a second per reading.

    Deterministic on purpose: with a fake runtime answering in microseconds,
    real timings would be noise, and the numbers under test are exactly the ones
    derived from the clock."""

    def __init__(self, step: float = 0.5) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


SNAPSHOT = SystemSnapshot(platform_name="Darwin", architecture="arm64", is_apple_silicon=True)


def _spec(**overrides: Any) -> ExperimentSpec:
    base = {
        "suite_id": "perf",
        "suite_version": "1",
        "model_key": MODEL,
        "tests": (BenchmarkTest(id="t1", version="1", prompt="hello",
                                generation=GenerationConfig(max_tokens=16)),),
        "load": {"context_length": 8192},
        "warmups": 1,
        "repetitions": 3,
    }
    return ExperimentSpec(**{**base, **overrides})  # type: ignore[arg-type]


async def _run(runtime: FakeRuntime, spec: ExperimentSpec | None = None,
               results_root: Any = None) -> Any:
    database = prepare_database(":memory:")
    resources = ResourceManager(runtime=runtime)
    outcome = await run_experiment(
        spec or _spec(), runtime=runtime, resources=resources, database=database,
        results_root=str(results_root), probe=SteadyProbe(), snapshot=SNAPSHOT,
        clock=Ticking(),
    )
    return outcome, database


async def test_a_run_persists_a_valid_result(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """M6's exit criterion, in the smallest form that means it."""
    runtime = FakeRuntime(usage={"prompt_tokens": 9, "completion_tokens": 4})

    outcome, database = await _run(runtime, results_root=tmp_path)

    assert outcome.state is RunState.SUCCEEDED
    stored = database.connection.execute(
        "SELECT * FROM benchmark_result WHERE run_id = ?", (outcome.run_id,)
    ).fetchall()
    assert len(stored) == 1
    assert stored[0]["target_key"] == MODEL
    assert stored[0]["evidence_id"] == outcome.record.identity.evidence_id
    assert (tmp_path / outcome.experiment_id / "result.json").exists()


async def test_every_repetition_is_preserved_not_just_the_headline(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§11.7, and M7's schema rule: the samples publish alongside the summary,
    always, because a headline means nothing without the spread behind it."""
    outcome, _ = await _run(FakeRuntime(), _spec(repetitions=4), results_root=tmp_path)

    ttft = outcome.record.measurements["time_to_first_token_seconds"]
    assert ttft.samples == 4
    assert len(ttft.as_dict()["repetitions"]) == 4
    assert ttft.median == pytest.approx(0.5)


async def test_warmups_are_run_and_kept_but_never_measured(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§11.7 excludes warmups from the statistics, not from the record: a warmup
    that failed explains a measured run that looks strange afterwards."""
    runtime = FakeRuntime()

    outcome, _ = await _run(runtime, _spec(warmups=2, repetitions=3), results_root=tmp_path)

    assert runtime.generations == 5
    assert outcome.record.measurements["total_latency_seconds"].samples == 3
    responses = sorted(p.name for p in (tmp_path / outcome.experiment_id / "responses").iterdir())
    assert sum("warmup" in name for name in responses) == 2
    assert sum("measured" in name for name in responses) == 3


async def test_a_cold_model_publishes_a_load_time_with_one_sample(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """One take has a median and no spread. Reporting 0.0 would claim perfect
    consistency measured once — the exact number M7's schema refuses to invent."""
    outcome, _ = await _run(FakeRuntime(), results_root=tmp_path)

    load = outcome.record.measurements["load_time_seconds"]
    assert load.samples == 1
    assert load.spread is None


async def test_a_warm_model_publishes_no_load_time_at_all(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The alternative is a load time three orders of magnitude too fast, which
    would look like the best result in the table."""
    runtime = FakeRuntime(resident=(MODEL,))

    outcome, _ = await _run(runtime, results_root=tmp_path)

    assert "load_time_seconds" not in outcome.record.measurements
    assert runtime.loads == []
    assert any("already resident" in note for note in outcome.record.validity_notes)


async def test_a_token_count_nobody_reported_is_never_called_measured(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Counting stream chunks is close to counting tokens and is not the same
    thing. §12.1's lattice then weakens the whole record, which is correct."""
    outcome, _ = await _run(FakeRuntime(usage=None), results_root=tmp_path)

    throughput = outcome.record.measurements["generation_tokens_per_second"]
    assert throughput.provenance.kind is EvidenceKind.ESTIMATED
    assert outcome.record.evidence_type is EvidenceKind.PARTIALLY_MEASURED


async def test_reported_usage_keeps_the_record_measured(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runtime = FakeRuntime(usage={"prompt_tokens": 9, "completion_tokens": 4})

    outcome, _ = await _run(runtime, results_root=tmp_path)

    assert outcome.record.evidence_type is EvidenceKind.MEASURED
    assert outcome.record.validity is Validity.VALID


async def test_a_model_that_says_nothing_is_a_result_with_a_warning(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """M2 found `qwen3-1.7b` spending an entire token budget on reasoning. The
    call succeeded and the model produced nothing; those are two facts, and the
    run records both rather than failing."""
    outcome, _ = await _run(FakeRuntime(content=()), results_root=tmp_path)

    assert outcome.state is RunState.SUCCEEDED
    assert outcome.record.validity is Validity.SUSPECT
    assert any("no content" in note for note in outcome.record.validity_notes)
    # Nothing arrived, so there is no first token to have timed.
    assert "time_to_first_token_seconds" not in outcome.record.measurements


async def test_a_configuration_the_runtime_did_not_honour_is_a_warning(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§7.1 in the wild: LM Studio routinely loads at a smaller context than the
    model advertises. The measurement is fine — it just describes a different
    configuration from the one that was asked for."""
    runtime = FakeRuntime(effective_context=4096)

    outcome, _ = await _run(runtime, results_root=tmp_path)

    assert outcome.record.validity is Validity.SUSPECT
    assert any("4096" in note and "8192" in note for note in outcome.record.validity_notes)


async def test_an_unexpected_stop_reason_is_recorded(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§11.8 lists it as a validity warning. `stop` and `length` are the
    experiment's own choices; anything else happened to it."""
    outcome, _ = await _run(FakeRuntime(finish="content_filter"), results_root=tmp_path)

    assert any("content_filter" in note for note in outcome.record.validity_notes)


async def test_a_runtime_that_stops_answering_leaves_a_truthful_run(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§11.10: expect and handle LM Studio going away. The partial telemetry is
    worth more than the exception — it says how far the run got."""
    runtime = FakeRuntime(fail_after=1)

    outcome, database = await _run(runtime, _spec(warmups=1, repetitions=3),
                                   results_root=tmp_path)

    assert outcome.state is RunState.FAILED
    assert database.connection.execute(
        "SELECT COUNT(*) AS n FROM benchmark_result"
    ).fetchone()["n"] == 0
    assert (tmp_path / outcome.experiment_id / "telemetry/measurements.jsonl").exists()


async def test_a_failed_run_still_releases_what_it_held(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A crashed benchmark stranding a multi-gigabyte model is what §9's leases
    exist to prevent, and the release has to happen on the failure path too."""
    runtime = FakeRuntime(fail_after=0)

    await _run(runtime, results_root=tmp_path)

    assert runtime.unloads == [MODEL]


async def test_nothing_is_recorded_for_a_model_that_is_not_installed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§6: a 404 stays a 404 and never becomes a nearest match, because a fuzzy
    hit would be a guess wearing an identity's clothes."""
    runtime = FakeRuntime()

    with pytest.raises(ModelNotFoundError):
        await _run(runtime, _spec(model_key="not-installed"), results_root=tmp_path)


async def test_the_engine_never_loads_except_through_the_resource_manager(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§9's rule, asserted rather than trusted: one acquire, one load, and the
    unload happens when the lease is released rather than when the engine feels
    finished with it."""
    runtime = FakeRuntime()

    await _run(runtime, _spec(repetitions=3), results_root=tmp_path)

    assert runtime.loads == [(MODEL, {"context_length": 8192})]
    assert runtime.unloads == [MODEL]


async def test_the_specification_is_what_reaches_the_runtime(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A benchmark measures what it sent. §11.5 freezes prompts and generation
    settings precisely so a result can be compared later, which is worth nothing
    if the engine sends something else."""
    runtime = FakeRuntime()

    await _run(runtime, results_root=tmp_path)

    model_key, messages, options = runtime.requests[0]
    assert model_key == MODEL
    assert messages == [{"role": "user", "content": "hello"}]
    assert options == {"temperature": 0.0, "max_tokens": 16}


async def test_the_raw_directory_carries_the_run_it_describes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§11.9: raw responses enable rescoring without rerunning inference, which
    is only true if the responses, the machine and the specification are all
    there."""
    outcome, _ = await _run(FakeRuntime(), results_root=tmp_path)

    root = tmp_path / outcome.experiment_id
    assert (root / "experiment.json").exists()
    assert (root / "system.json").exists()
    assert (root / "runtime.json").exists()
    assert (root / "logs/run.log").read_text().strip()
