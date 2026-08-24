"""M10 — multi-model benchmarks (§11.2, §11.3), and the gate that matters most.

    Interaction matrix produced; a simultaneous-load failure is recorded as a
    result, not converted into separate-model success.

The second clause is the one to read the tests for. By the time co-residency
fails, the alone phase has already succeeded and there is a directory of good
measurements sitting there — so the wrong behaviour is not a crash, it is a
*plausible success*. Several tests below exist only to make that impossible.

Nothing here reaches a runtime. A fake generates deterministically, which is
what lets a test assert on a degradation percentage at all: real throughput
varies, and a test that tolerated that variance would tolerate the bug too.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from sirvis.benchmarks.engine import Repetition
from sirvis.benchmarks.multi import (
    MODE_ALONE,
    MODE_ALTERNATING,
    MODE_CONCURRENT,
    MODE_SEQUENTIAL,
    MultiModelSpec,
    interaction_matrix,
    run_multi_experiment,
)
from sirvis.benchmarks.spec import BenchmarkTest
from sirvis.core.runtime_sets import RuntimeSet, RuntimeSetMember
from sirvis.resources import ResourceExhaustedError, ResourceManager
from sirvis.runtimes.base import GenerationChunk, LoadedModel
from sirvis.storage import RunState, prepare_database, read_run
from sirvis.telemetry import MemoryProbe, MemorySample, SystemSnapshot

CHAT = "chat-model"
AGENT = "agent-model"

TESTS = (BenchmarkTest(id="t1", version="1", prompt="two plus two?"),)


def a_set() -> RuntimeSet:
    return RuntimeSet.define(
        name="clarvis-balanced",
        members=[
            RuntimeSetMember(role="chat", model_id=CHAT, context_length=16384),
            RuntimeSetMember(role="agent", model_id=AGENT, context_length=32768),
        ],
    )


def a_spec(**overrides: Any) -> MultiModelSpec:
    return MultiModelSpec.from_set(
        a_set(), suite_id="s", suite_version="1", tests=TESTS,
        warmups=0, repetitions=2, **overrides,
    )


class FakeRuntime:
    """A runtime whose speed depends on how many models are resident.

    That dependence *is* the thing under test: co-residency should cost
    throughput, so the fake makes it cost throughput deterministically, and the
    matrix has a known answer to be checked against.
    """

    def __init__(self, fail_second_load: bool = False, fail_concurrent: str | None = None) -> None:
        self.loaded: list[str] = []
        self.fail_second_load = fail_second_load
        self.fail_concurrent = fail_concurrent
        self.generations: list[str] = []
        self.max_resident = 0

    async def load(self, model_key: str, config: dict[str, Any] | None = None) -> LoadedModel:
        del config
        if self.fail_second_load and self.loaded and model_key not in self.loaded:
            raise ResourceExhaustedError("not enough memory for a second model")
        if model_key not in self.loaded:
            self.loaded.append(model_key)
        self.max_resident = max(self.max_resident, len(self.loaded))
        return LoadedModel(model_key=model_key, state="loaded")

    async def unload(self, model_key: str) -> None:
        if model_key in self.loaded:
            self.loaded.remove(model_key)

    async def list_loaded_models(self) -> list[LoadedModel]:
        return [LoadedModel(model_key=key, state="loaded") for key in self.loaded]

    async def list_models(self) -> list[dict[str, Any]]:
        return [
            {"id": CHAT, "type": "llm", "state": "loaded", "max_context_length": 32768},
            {"id": AGENT, "type": "llm", "state": "loaded", "max_context_length": 32768},
        ]

    def stream_generate(
        self, model_key: str, messages: list[dict[str, Any]], **options: Any
    ) -> AsyncIterator[GenerationChunk]:
        del messages, options

        async def chunks() -> AsyncIterator[GenerationChunk]:
            self.generations.append(model_key)
            if self.fail_concurrent == model_key and len(self.loaded) > 1:
                raise RuntimeError(f"{model_key} fell over under load")
            # Every extra resident model halves the token count, so the
            # degradation the matrix reports is a number the test can predict.
            tokens = 8 // max(1, len(self.loaded))
            for index in range(tokens):
                await asyncio.sleep(0)
                yield GenerationChunk(content=f"tok{index} ")
            yield GenerationChunk(
                content="", usage={"completion_tokens": tokens, "prompt_tokens": 4},
                finish_reason="stop",
            )

        return chunks()


def a_snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        platform_name="Darwin", architecture="arm64", is_apple_silicon=True,
        unified_memory_bytes=64_000_000_000,
    )


class FakeProbe(MemoryProbe):
    """A probe with numbers of its own, so the memory rows do not depend on the
    host running the suite.

    Exactly the reasoning `run` already applies to thermal one line below, and
    not applying it here cost six red CI runs: §11.8's rows are about *whether a
    reading was taken at each labelled point*, and a real reader gives whatever
    the machine happens to have. GitHub's runners have no swap, so the real
    probe reported `swap_used_bytes` as null there — correct behaviour, since
    absence means "could not tell" — and the assertion that the alone condition
    carries a swap figure failed on CI while passing on a developer's Mac.

    `include_swap` is still honoured rather than always answering. The in-flight
    poll passes False to skip a subprocess, and a fake that answered anyway
    would hide a caller that had stopped asking at the labelled points.
    """

    def sample(self, point: str, include_swap: bool = True) -> MemorySample:
        return MemorySample(
            point=point,
            captured_at=0.0,
            available_bytes=8_000_000_000,
            total_bytes=64_000_000_000,
            swap_used_bytes=1_000_000 if include_swap else None,
        )


def run(spec: MultiModelSpec, runtime: FakeRuntime, tmp_path: Any,
        thermal: Any = None) -> Any:
    database = prepare_database(":memory:")
    outcome = asyncio.run(run_multi_experiment(
        spec,
        runtime=runtime,  # type: ignore[arg-type]
        resources=ResourceManager(runtime),  # type: ignore[arg-type]
        database=database,
        results_root=str(tmp_path),
        probe=FakeProbe(),
        snapshot=a_snapshot(),
        # Driven rather than read: §11.8's flag is about the state *changing*
        # between conditions, and a real reader on a test machine gives whatever
        # it gives.
        **({"thermal": thermal} if thermal else {}),
    ))
    return outcome, database


# ── The lifecycle (§11.2) ────────────────────────────────────────────────────


def test_alone_is_measured_with_nothing_else_resident(tmp_path: Any) -> None:
    """The control column, and the only way it is a control.

    If a second member were loaded before the first was measured, "alone" would
    already be a co-residency measurement wearing the wrong label — and every
    degradation percentage computed from it would be wrong in a direction
    nobody could see.
    """
    runtime = FakeRuntime()
    outcome, _ = run(a_spec(modes=()), runtime, tmp_path)

    alone = outcome.repetitions_for("chat", MODE_ALONE)
    assert alone, "the alone phase must produce measurements"
    # 8 tokens means exactly one model was resident while it generated.
    assert all(r.completion_tokens == 8 for r in alone)


def test_load_order_is_recorded_and_followed(tmp_path: Any) -> None:
    """§11.2 ends with "load order is always recorded"."""
    runtime = FakeRuntime()
    outcome, _ = run(a_spec(), runtime, tmp_path)

    assert outcome.load_order == ["chat", "agent"]
    assert outcome.matrix["load_order"] == ["chat", "agent"]


def test_every_member_load_gets_its_own_memory_sample(tmp_path: Any) -> None:
    """§11.8: "For Runtime Sets, record after every model load".

    One `after_load` sample would describe whichever load happened last, which
    is exactly the figure a co-residency investigation does not want.
    """
    runtime = FakeRuntime()
    outcome, _ = run(a_spec(), runtime, tmp_path)

    phases = [sample.point for sample in outcome.telemetry]
    assert "after_load:chat" in phases
    assert "after_load:agent" in phases


def test_the_members_are_co_resident_during_the_modes(tmp_path: Any) -> None:
    runtime = FakeRuntime()
    run(a_spec(), runtime, tmp_path)

    assert runtime.max_resident == 2


def test_everything_is_released_when_the_run_finishes(tmp_path: Any) -> None:
    """A benchmark that strands two models is worse than one that fails."""
    runtime = FakeRuntime()
    run(a_spec(), runtime, tmp_path)

    assert runtime.loaded == []


# ── The modes (§11.3) ────────────────────────────────────────────────────────


def test_each_mode_produces_its_own_measurements(tmp_path: Any) -> None:
    runtime = FakeRuntime()
    outcome, _ = run(a_spec(), runtime, tmp_path)

    for mode in (MODE_ALONE, MODE_SEQUENTIAL, MODE_ALTERNATING, MODE_CONCURRENT):
        for role in ("chat", "agent"):
            assert outcome.repetitions_for(role, mode), f"{role} has no {mode} measurements"


def test_alternating_interleaves_the_members(tmp_path: Any) -> None:
    """chat → agent → chat → agent, which is the point of the mode.

    Running each member's repetitions in a block would let the runtime settle
    between switches and measure nothing about switching — the cost this mode
    exists to find.
    """
    runtime = FakeRuntime()
    spec = a_spec(modes=(MODE_ALTERNATING,))
    run(spec, runtime, tmp_path)

    # Drop the alone phase; what remains is the alternating mode in order.
    during_mode = runtime.generations[-4:]
    assert during_mode == [CHAT, AGENT, CHAT, AGENT]


def test_a_member_failing_under_concurrent_load_does_not_cancel_the_other(
    tmp_path: Any,
) -> None:
    """The finding the mode exists to produce, and it must survive.

    A set where the agent falls over under concurrent load while chat keeps
    answering is exactly what a concurrent benchmark is for. Cancelling the
    survivor would destroy the evidence for it.
    """
    runtime = FakeRuntime(fail_concurrent=AGENT)
    outcome, _ = run(a_spec(modes=(MODE_CONCURRENT,)), runtime, tmp_path)

    assert outcome.repetitions_for("chat", MODE_CONCURRENT), "the survivor must be measured"
    assert not outcome.repetitions_for("agent", MODE_CONCURRENT)
    assert any("under concurrent load" in warning for warning in outcome.warnings)


# ── The interaction matrix ───────────────────────────────────────────────────


def test_the_matrix_reports_degradation_against_alone(tmp_path: Any) -> None:
    """§11.3's table, with the percentages that make it worth reading.

    The fake halves the tokens generated per extra resident model, so the
    co-resident column must be materially below the alone column and the
    degradation strictly positive. The exact-percentage arithmetic is proven in
    the handcrafted test below, where the timings are chosen rather than
    measured.
    """
    runtime = FakeRuntime()
    outcome, _ = run(a_spec(), runtime, tmp_path)

    row = outcome.matrix["rows"]["chat"]
    alone = row["figures"][MODE_ALONE]["tokens_per_second"]
    co_resident = row["figures"][MODE_SEQUENTIAL]["tokens_per_second"]
    assert alone is not None and co_resident is not None
    assert co_resident < alone, "co-residency must show as slower, not merely different"
    assert row["degradation_percent"][MODE_SEQUENTIAL]["tokens_per_second"] > 0


def test_degradation_is_positive_for_worse_in_both_metrics() -> None:
    """Throughput falling and time-to-first-token rising are both degradation.

    The two move in opposite directions, so the sign is normalised here rather
    than left for whoever reads the table to work out per row.
    """
    matrix = interaction_matrix(
        a_spec(),
        _outcome_with(
            {"chat": {MODE_ALONE: _reps(2, 1.0, 0.1), MODE_SEQUENTIAL: _reps(2, 2.0, 0.2)}}
        ),
    )

    degradation = matrix["rows"]["chat"]["degradation_percent"][MODE_SEQUENTIAL]
    assert degradation["tokens_per_second"] > 0
    assert degradation["time_to_first_token"] > 0


def test_a_condition_with_no_samples_is_none_rather_than_zero() -> None:
    """A measurement that did not happen is not a measurement of zero."""
    matrix = interaction_matrix(a_spec(), _outcome_with({"chat": {MODE_ALONE: _reps(1, 1.0, 0.1)}}))

    figures = matrix["rows"]["chat"]["figures"][MODE_SEQUENTIAL]
    assert figures["tokens_per_second"] is None
    assert figures["samples"] == 0


def test_the_matrix_says_its_numbers_came_from_one_run(tmp_path: Any) -> None:
    """Alone is measured here rather than borrowed from M6's corpus.

    A degradation computed against numbers from another day, at another thermal
    state, measures the week rather than the co-residency — so the matrix states
    which it is.
    """
    runtime = FakeRuntime()
    outcome, _ = run(a_spec(), runtime, tmp_path)

    assert outcome.matrix["basis"] == "measured in this run"


# ── §10's gate: a load failure is a result ───────────────────────────────────


def test_a_simultaneous_load_failure_is_a_result(tmp_path: Any) -> None:
    """The gate, verbatim, and the reason it needs a test of its own.

    The alone phase has already succeeded by this point, so the failure mode to
    guard against is not a crash — it is a run that looks like a success because
    two good sets of single-model measurements are sitting in the directory.
    """
    runtime = FakeRuntime(fail_second_load=True)
    outcome, database = run(a_spec(), runtime, tmp_path)

    assert outcome.state is RunState.FAILED
    assert outcome.co_residency_failure is not None
    # Recorded as a result, not merely logged: the finding is the point.
    assert outcome.result_ids, "a co-residency failure must still persist results"
    stored = read_run(database, outcome.run_id)
    assert stored is not None and stored["state"] == RunState.FAILED.value


def test_a_load_failure_never_reports_the_combination_as_working(tmp_path: Any) -> None:
    """"Not converted into separate-model success" — the other half of the gate.

    The alone measurements are kept, because they are real and they are context.
    What must not happen is a matrix that presents them as a comparison.
    """
    runtime = FakeRuntime(fail_second_load=True)
    outcome, _ = run(a_spec(), runtime, tmp_path)

    assert outcome.repetitions_for("chat", MODE_ALONE), "the alone measurements are kept"
    assert outcome.matrix["complete"] is False
    assert outcome.matrix["conditions"] == [MODE_ALONE]
    assert MODE_SEQUENTIAL not in outcome.matrix["rows"]["chat"]["figures"]
    assert any("say nothing about the combination" in w for w in outcome.warnings)


def test_a_failed_load_leaves_nothing_resident(tmp_path: Any) -> None:
    """A half-loaded set is not a state to leave the machine in."""
    runtime = FakeRuntime(fail_second_load=True)
    run(a_spec(), runtime, tmp_path)

    assert runtime.loaded == []


# ── Evidence identity (§12.2) ────────────────────────────────────────────────


def test_a_co_resident_result_cannot_collide_with_a_solo_one(tmp_path: Any) -> None:
    """The mistake this repository has already made once.

    A throughput figure measured beside another model is about a different thing
    than the same figure measured alone. If both keyed to one `evidence_id`, the
    corpus would hold two different numbers under one key and disagree with
    itself. So the same build, same suite, same machine — measured once
    co-resident and once not — must produce two different keys.
    """
    co_resident, co_db = run(a_spec(), FakeRuntime(), tmp_path)
    solo, solo_db = run(a_spec(), FakeRuntime(fail_second_load=True), tmp_path / "solo")

    co_ids = _evidence_ids(co_db, co_resident.run_id)
    solo_ids = _evidence_ids(solo_db, solo.run_id)
    assert co_ids and solo_ids
    assert co_ids.isdisjoint(solo_ids)


def test_each_role_gets_its_own_result(tmp_path: Any) -> None:
    """§17: one result per ExperimentTarget, and for a set the target is the role.

    Keyed by role rather than by model because two roles could name one build,
    and two results about "that build" would collide on the run's unique
    constraint for a reason nobody could see from the outside.
    """
    outcome, database = run(a_spec(), FakeRuntime(), tmp_path)
    stored = read_run(database, outcome.run_id)

    assert stored is not None
    assert sorted(result["target_key"] for result in stored["results"]) == ["agent", "chat"]


def test_the_result_records_what_it_was_measured_beside(tmp_path: Any) -> None:
    outcome, database = run(a_spec(), FakeRuntime(), tmp_path)
    stored = read_run(database, outcome.run_id)

    assert stored is not None
    chat = next(r for r in stored["results"] if r["target_key"] == "chat")
    co_residency = chat["co_residency"]
    assert co_residency["runtime_set"] == "clarvis-balanced"
    assert co_residency["load_order"] == ["chat", "agent"]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _reps(count: int, seconds_per_token: float, ttft: float) -> list[Repetition]:
    """Repetitions with known throughput, for matrix arithmetic without a run."""
    return [
        Repetition(
            test_id="t1", phase="measured", index=index, total_seconds=seconds_per_token,
            content="tok " * 4, ttft_seconds=ttft, completion_tokens=4,
        )
        for index in range(count)
    ]


def _outcome_with(measurements: dict[str, dict[str, list[Repetition]]]) -> Any:
    """A bare outcome carrying only what the matrix reads."""
    from sirvis.benchmarks.multi import MultiModelOutcome

    outcome = MultiModelOutcome(
        experiment_id="e", run_id="r", state=RunState.SUCCEEDED, detail="",
        results_path="", load_order=["chat", "agent"],
    )
    outcome.measurements = measurements
    return outcome


def _evidence_ids(database: Any, run_id: str) -> set[str]:
    """The evidence keys a run's results were stored under."""
    stored = read_run(database, run_id)
    assert stored is not None
    return {result["evidence_id"] for result in stored["results"]}


# ── The CLI (M10's entry point) ──────────────────────────────────────────────


def test_the_cli_names_every_model_before_loading_any(capsys: Any, monkeypatch: Any) -> None:
    """The loading rule, extended to the run that loads several.

    The single-model prompt names one model. A multi-model run loads each
    member alone and then all together, and the person at the keyboard is
    entitled to the whole bill before the first byte moves — including when the
    answer is no, which is what this test gives.
    """
    from sirvis.cli import _confirm_multi_load
    from sirvis.config import Settings

    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr("sys.stdin", _NotATty())

    allowed = _confirm_multi_load(a_spec(), settings, assume_yes=False)

    assert allowed is False, "no terminal and no --yes must refuse, not assume"
    printed = capsys.readouterr()
    assert CHAT in printed.out and AGENT in printed.out
    assert "ALL are loaded together" in printed.out


class _NotATty:
    def isatty(self) -> bool:
        return False


# ── §11.3's memory rows ──────────────────────────────────────────────────────


def test_the_matrix_carries_peak_memory_and_swap_per_condition(tmp_path: Any) -> None:
    """§11.3's table has Peak RAM and Swap rows beneath the per-model ones.

    They belong to the machine rather than to a role: under concurrent load both
    models press on the same memory, so a per-role figure would double-count the
    thing that is actually shared.
    """
    outcome, _ = run(a_spec(), FakeRuntime(), tmp_path)

    memory = outcome.matrix["memory"]
    assert set(memory) == {MODE_ALONE, MODE_SEQUENTIAL, MODE_ALTERNATING, MODE_CONCURRENT}
    for condition in memory.values():
        assert "lowest_available_bytes" in condition
        assert "swap_used_bytes" in condition


def test_peak_memory_is_the_minimum_not_the_median() -> None:
    """The peak is the moment the machine was closest to running out.

    A median would describe a comfortable average of a run that briefly was not
    comfortable at all — and the brief part is the part that swaps.
    """
    from sirvis.benchmarks.multi import _summarise

    figures = _summarise([
        _rep_with_memory(8_000_000_000),
        _rep_with_memory(1_000_000_000),
        _rep_with_memory(7_000_000_000),
    ])

    assert figures["lowest_available_bytes"] == 1_000_000_000


def test_an_unreadable_memory_reading_is_none_rather_than_zero() -> None:
    """Zero available memory and "we could not tell" are different findings."""
    from sirvis.benchmarks.multi import _summarise

    assert _summarise([_rep_with_memory(None)])["lowest_available_bytes"] is None


def _rep_with_memory(available: int | None) -> Repetition:
    return Repetition(
        test_id="t1", phase="measured", index=0, total_seconds=1.0, content="tok ",
        ttft_seconds=0.1, completion_tokens=4, lowest_available_bytes=available,
    )


def test_the_alone_condition_has_a_peak_sample_of_its_own(tmp_path: Any) -> None:
    """§11.8 wants swap baseline, peak and final — and alone *is* the baseline.

    Only the co-residency modes emitted a peak sample, so swap could be read
    while two models were resident and not while one was. That made the alone
    column's swap null, and §10.1's whole question — what does adding the second
    model cost — unanswerable for the figure most likely to answer it.

    Found on the first pair run that carried memory rows: every co-resident
    condition reported swap and the control reported none.
    """
    outcome, _ = run(a_spec(), FakeRuntime(), tmp_path)

    assert outcome.matrix["memory"][MODE_ALONE]["swap_used_bytes"] is not None


def test_a_run_whose_thermal_state_moved_says_so(tmp_path: Any) -> None:
    """§11.8: flag a thermally compromised run, never silently discard it.

    A degradation percentage compares two conditions measured at different
    times — the control first on an idle machine, concurrent last after every
    other mode. The first real reading showed the machine at `nominal` through
    alone and sequential and `fair` for alternating and concurrent, so the
    contention figure carries that drift and no arithmetic here can separate it.

    A bias with a direction, not noise: the control is always measured under the
    better conditions, so contention is overstated by whatever was lost.
    """
    states = iter(["nominal", "nominal", "nominal", "fair", "fair", "fair"])
    outcome, _ = run(a_spec(), FakeRuntime(), tmp_path,
                     thermal=lambda: next(states, "fair"))

    assert any("thermal state changed" in note for note in outcome.warnings)


def test_a_run_at_one_thermal_state_is_not_flagged(tmp_path: Any) -> None:
    """The flag has to be capable of staying quiet, or it says nothing."""
    outcome, _ = run(a_spec(), FakeRuntime(), tmp_path, thermal=lambda: "nominal")

    assert not any("thermal state changed" in note for note in outcome.warnings)
