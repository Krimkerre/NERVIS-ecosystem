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
from sirvis.benchmarks.engine import answer_offset
from sirvis.core.evidence import EvidenceKind, Validity, ValidityScope
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
        speaks_only_when: str | None = None,
    ) -> None:
        # A reasoning model, as far as this engine can see one: it emits nothing
        # unless the prompt carries a marker. Real ones emit reasoning deltas and
        # no content, which reaches the adapter as exactly this.
        self.speaks_only_when = speaks_only_when
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
        if self.speaks_only_when is not None and not any(
            self.speaks_only_when in str(message.get("content", "")) for message in messages
        ):
            # 255 tokens of thinking and not one of them content — the shape
            # `qwen3-1.7b` and `lfm2.5-2.6b-mlx` both produce at a 256 cap.
            yield GenerationChunk(finish_reason="length",
                                  usage={"prompt_tokens": 9, "completion_tokens": 255})
            return
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
               results_root: Any = None,
               thermal: Any = None,
               should_stop: Any = None) -> Any:
    database = prepare_database(":memory:")
    resources = ResourceManager(runtime=runtime)
    outcome = await run_experiment(
        spec or _spec(), runtime=runtime, resources=resources, database=database,
        results_root=str(results_root), probe=SteadyProbe(), snapshot=SNAPSHOT,
        clock=Ticking(), thermal=thermal or (lambda: "nominal"),
        should_stop=should_stop,
    )
    return outcome, database


async def test_a_run_persists_a_valid_result(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """M6's exit criterion, in the smallest form that means it."""
    runtime = FakeRuntime(usage={"prompt_tokens": 9, "completion_tokens": 2})

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


async def test_tokens_that_never_arrived_as_content_do_not_inflate_throughput(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Caught live, at `MEASURED` provenance, by a number that was impossible.

    `lfm2.5-2.6b-mlx` measured with its thinking suppressed spent 3.36 s
    generating 228 tokens of reasoning and 0.33 s producing 27 tokens of answer.
    Dividing all 255 by the answer window published **767 tokens/second** for a
    2.6B model on a laptop — from arithmetic that is correct for every model
    that does not think first.
    """
    runtime = FakeRuntime(
        content=("an", "swer"),
        usage={"prompt_tokens": 9, "completion_tokens": 200},
    )

    outcome, _ = await _run(runtime, _spec(repetitions=3), results_root=tmp_path)

    throughput = outcome.record.measurements["generation_tokens_per_second"]
    # Two content chunks over a half-second window, not two hundred tokens.
    assert throughput.median == pytest.approx(2.0)
    assert throughput.provenance.kind is EvidenceKind.ESTIMATED
    assert any("reasoning" in note for note in outcome.record.validity_notes)


async def test_a_reported_reasoning_count_beats_the_chunk_heuristic(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """LM Studio reports `usage.completion_tokens_details.reasoning_tokens` —
    77 of 79 for `gemma-4-e2b`. That is an exact figure where the chunk-count
    comparison is an inference, so it wins wherever it is offered, and the
    threshold stays only for runtimes that say nothing."""
    runtime = FakeRuntime(
        content=("an", "swer"),
        usage={"prompt_tokens": 9, "completion_tokens": 200,
               "completion_tokens_details": {"reasoning_tokens": 190}},
    )

    outcome, _ = await _run(runtime, _spec(repetitions=2), results_root=tmp_path)

    first = outcome.repetitions[0]
    assert first.reasoning_tokens == 190
    assert first.hidden_tokens == 190
    # 200 counted, 190 of them thinking: the answer is the 10 that remain, not
    # the two chunks the heuristic would have guessed.
    assert first.content_tokens == 10


async def test_a_runtime_that_reports_no_reasoning_is_not_assumed_to_have_none(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """`None` and `0` are different claims. Absent means the runtime did not
    say, which is exactly when the chunk-count fallback has to do the work."""
    runtime = FakeRuntime(
        content=("an", "swer"), usage={"prompt_tokens": 9, "completion_tokens": 200}
    )

    outcome, _ = await _run(runtime, _spec(repetitions=2), results_root=tmp_path)

    first = outcome.repetitions[0]
    assert first.reasoning_tokens is None
    assert first.hidden_tokens == 198  # inferred, because nothing was reported


async def test_an_ordinary_stream_is_not_mistaken_for_a_thinking_one(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Real streams show content chunks for 93–100% of their reported tokens —
    a handful of tokens never appear, and that is ordinary bookkeeping rather
    than a model that thought first."""
    runtime = FakeRuntime(
        content=tuple("word" for _ in range(19)),
        usage={"prompt_tokens": 9, "completion_tokens": 20},
    )

    outcome, _ = await _run(runtime, results_root=tmp_path)

    assert outcome.record.measurements["generation_tokens_per_second"].provenance.kind is (
        EvidenceKind.MEASURED
    )
    assert outcome.record.validity is Validity.VALID


async def test_reported_usage_keeps_the_record_measured(tmp_path) -> None:  # type: ignore[no-untyped-def]
    runtime = FakeRuntime(usage={"prompt_tokens": 9, "completion_tokens": 2})

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


async def test_a_build_that_answers_nothing_is_asked_differently(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The measured repetitions are not spent discovering what the warmups
    already showed. A reasoning model that says nothing has done real work —
    255 tokens of it — and recording "it said nothing" measures the token cap
    rather than the model."""
    runtime = FakeRuntime(speaks_only_when="/no_think")

    outcome, _ = await _run(runtime, _spec(warmups=1, repetitions=3), results_root=tmp_path)

    assert outcome.thinking_suppression == "no_think_suffix"
    assert outcome.record.measurements["time_to_first_token_seconds"].samples == 3
    # One warmup, one probe that worked, three measured. The second strategy is
    # never tried, because the first one answered.
    assert runtime.generations == 5


async def test_an_adapted_run_is_different_evidence_from_the_one_asked_for(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§12.2 keys evidence on the configuration a number came from. A prompt
    that had to be changed to get an answer is a different configuration, so the
    adapted result gets its own identity and can never be averaged with — or
    mistaken for — the measurement the suite declared."""
    adapted, _ = await _run(
        FakeRuntime(speaks_only_when="/no_think"), _spec(warmups=1), results_root=tmp_path
    )
    plain, _ = await _run(FakeRuntime(), _spec(warmups=1), results_root=tmp_path)

    assert adapted.record.identity.evidence_id != plain.record.identity.evidence_id
    assert adapted.record.identity.runtime_configuration["thinking_suppression"] == (
        "no_think_suffix"
    )
    # Sound numbers, but not about the question the suite asked.
    assert adapted.record.validity is Validity.SUSPECT
    assert any("no_think_suffix" in note for note in adapted.record.validity_notes)


async def test_a_build_that_answers_nothing_however_it_is_asked_says_what_was_tried(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """`lfm2.5-2.6b-mlx` ignores every suppression this engine knows. The result
    is the same empty measurement as before — with the phrasings that were
    attempted, which is strictly more than "returned no content"."""
    runtime = FakeRuntime(speaks_only_when="never appears")

    outcome, _ = await _run(runtime, _spec(warmups=1, repetitions=2), results_root=tmp_path)

    assert outcome.thinking_suppression is None
    assert "time_to_first_token_seconds" not in outcome.record.measurements
    assert any("no_think_suffix" in note and "direct_system" in note
               for note in outcome.record.validity_notes)


async def test_suppression_is_never_attempted_on_a_build_that_answers(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """It costs a generation, so it fires only where it is needed."""
    runtime = FakeRuntime()

    outcome, _ = await _run(runtime, _spec(warmups=2, repetitions=3), results_root=tmp_path)

    assert outcome.suppressions_tried == []
    assert runtime.generations == 5


async def test_an_experiment_may_refuse_to_be_adapted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`suppress_thinking: none`. Someone measuring how a build behaves *as
    asked* must be able to say so and get exactly that."""
    runtime = FakeRuntime(speaks_only_when="/no_think")

    outcome, _ = await _run(
        runtime, _spec(warmups=1, repetitions=2, suppress_thinking=()), results_root=tmp_path
    )

    assert outcome.thinking_suppression is None
    assert outcome.suppressions_tried == []
    assert runtime.generations == 3


def test_the_answer_starts_after_a_think_block_and_not_before() -> None:
    """`None` is a third answer — *not yet knowable* — and it has to be, because
    a stream arrives in arbitrary chunks. A first chunk of `<th` is neither a
    think block nor an answer until more of it exists, and deciding early either
    way mis-times the first token."""
    assert answer_offset("") is None
    assert answer_offset("<th") is None
    assert answer_offset("<think>still going") is None
    assert answer_offset("<think>done</think>the answer") == len("<think>done</think>")
    assert answer_offset("a plain answer") == 0
    assert answer_offset("   leading space") == 3
    # A model whose answer merely mentions the tag is not opening one.
    assert answer_offset("the <think> tag is used by") == 0


async def test_thinking_that_arrives_as_content_is_not_measured_as_an_answer(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """Some runtimes route thinking to `reasoning_content`, where this engine
    never sees it. Others do not: `tencent/Hunyuan-1.8B` streams it as ordinary
    content, and a run of it reported a 0.043 s time-to-first-token and 55
    tokens/second over 256 tokens containing no answer — and looked clean, since
    from the stream's point of view the model was talking."""
    runtime = FakeRuntime(content=("<thi", "nk>", "hmm", "</think>", "ans", "wer"))

    outcome, _ = await _run(runtime, _spec(warmups=0, repetitions=3), results_root=tmp_path)

    first = outcome.repetitions[0]
    assert first.content == "answer"
    assert first.thinking == "<think>hmm</think>"
    # Timed from the answer's first token, not the thinking's: three clock ticks
    # in (start, first answer chunk), not one.
    assert first.ttft_seconds == pytest.approx(0.5)
    assert first.chunk_count == 2


async def test_a_stream_that_only_ever_thinks_has_no_answer_to_measure(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The case that defeated the empty-content check. It fires again now."""
    runtime = FakeRuntime(content=("<think>", "and never stops"))

    outcome, _ = await _run(runtime, _spec(warmups=1, repetitions=2), results_root=tmp_path)

    assert outcome.record.validity is Validity.SUSPECT
    assert "time_to_first_token_seconds" not in outcome.record.measurements
    assert any("no content" in note for note in outcome.record.validity_notes)


async def test_a_run_taken_on_a_throttled_machine_says_so(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§11.8: flag a thermally compromised run, never discard it. The numbers
    are real — they describe a machine under duress, which on fanless hardware
    is most of the difference between one result and another. The same build
    here measured 38.4 tok/s heat-soaked and 56.8 rested."""
    outcome, _ = await _run(FakeRuntime(), results_root=tmp_path, thermal=lambda: "serious")

    assert outcome.record.validity is Validity.SUSPECT
    assert any("thermal pressure 'serious'" in note for note in outcome.record.validity_notes)


async def test_a_machine_that_heats_up_mid_run_is_flagged_too(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The interesting case, and the one a single reading misses: repetitions
    taken after the machine changed state are not comparable with the ones
    before it, and every repetition inside a run shares the same summary."""
    readings = iter(["nominal", "fair"])

    outcome, _ = await _run(
        FakeRuntime(), results_root=tmp_path, thermal=lambda: next(readings, "fair")
    )

    assert any("changed from 'nominal' to 'fair'" in n for n in outcome.record.validity_notes)


async def test_a_machine_that_will_not_report_is_not_called_hot(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Unknown is not compromised. Treating silence as heat would put a warning
    on every result from every platform that does not implement this."""
    outcome, _ = await _run(FakeRuntime(), results_root=tmp_path, thermal=lambda: None)

    assert outcome.record.validity is Validity.VALID
    assert not any("thermal" in note for note in outcome.record.validity_notes)


async def test_a_configuration_the_runtime_did_not_honour_is_a_warning(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """§7.1 in the wild: LM Studio routinely loads at a smaller context than the
    model advertises. The measurement is fine — it just describes a different
    configuration from the one that was asked for."""
    runtime = FakeRuntime(effective_context=4096)

    outcome, _ = await _run(runtime, results_root=tmp_path)

    assert outcome.record.validity is Validity.SUSPECT
    assert any("4096" in note and "8192" in note for note in outcome.record.validity_notes)


async def test_the_identity_records_the_configuration_that_ran(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Live, `lms load --context-length 8192` is honoured for ordinary builds
    and ignored by LM Studio's vision models: `gemma-4-e2b` loaded at 131072.
    Keying evidence on the *request* gave that run the same ID as one that
    genuinely ran at 8192 — the collision §12.2 exists to prevent, and the same
    reasoning already applied to an adapted prompt."""
    honoured, _ = await _run(FakeRuntime(effective_context=8192), results_root=tmp_path)
    ignored, _ = await _run(FakeRuntime(effective_context=131072), results_root=tmp_path)

    assert honoured.record.identity.evidence_id != ignored.record.identity.evidence_id
    assert ignored.record.identity.runtime_configuration["context_length"] == 131072
    # The warning stays: the identity says what ran, the note says it was not
    # what was asked for, and a reader needs both.
    assert any("131072" in note for note in ignored.record.validity_notes)


async def test_an_unverifiable_configuration_is_not_claimed_as_the_requested_one(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    """A model the runtime does not list as resident tells us nothing about how
    it was configured. Falling back to the requested value there would put a
    number in the identity that nobody confirmed."""
    class Silent(FakeRuntime):
        """Loads happily and reports nothing as resident — which some runtimes
        genuinely do, and which is not the same as reporting a match."""

        async def list_loaded_models(self) -> list[LoadedModel]:
            return []

    outcome, _ = await _run(Silent(), results_root=tmp_path)

    assert outcome.effective_configuration == {}
    # Falls back to what was asked for, because there is nothing better — but
    # the identity is not claiming the runtime confirmed it.
    assert outcome.record.identity.runtime_configuration["context_length"] == 8192


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


def test_a_run_that_swapped_says_so() -> None:
    """§11.8's swap warning: measured at four points, reported at none.

    `MemoryProbe.sample` reads `vm.swapusage` at baseline, after load, post-run
    and post-unload, and the readings are real in stored telemetry. Nothing in
    the engine mentioned swap, so a run taken while the machine was paging was
    published VALID with no note -- a benchmark that measured the disk as much
    as the model, handed to RAVIS as a clean number.

    Growth from the baseline rather than an absolute level: a machine already
    swapping before the run is a fact about the machine, and what invalidates a
    throughput figure is the run *causing* the paging.
    """
    from sirvis.benchmarks.engine import _swap_warnings
    from sirvis.telemetry import AFTER_LOAD, BASELINE, POST_RUN, MemorySample

    class _Outcome:
        telemetry = [
            MemorySample(point=BASELINE, captured_at=0.0, swap_used_bytes=100 * 1024 ** 2),
            MemorySample(point=AFTER_LOAD, captured_at=1.0, swap_used_bytes=3 * 1024 ** 3),
            MemorySample(point=POST_RUN, captured_at=2.0, swap_used_bytes=2 * 1024 ** 3),
        ]

    warnings = _swap_warnings(_Outcome())  # type: ignore[arg-type]

    assert warnings, "a run that paged three gigabytes is not a clean measurement"
    # Scoped pairs since §16 item 7: paging makes the *rate* describe the disk
    # as well as the model, and says nothing about what came back.
    scope, note = warnings[0]
    assert scope is ValidityScope.TIMING
    assert "swap grew" in note
    assert "after_load" in note, "and says where the peak was"


def test_a_machine_already_swapping_is_not_blamed_on_the_run() -> None:
    """Steady swap is a fact about the machine, not about this measurement.

    Asserted beside the test above because a warning that fires on every run of
    a busy laptop is one nobody reads.
    """
    from sirvis.benchmarks.engine import _swap_warnings
    from sirvis.telemetry import BASELINE, POST_RUN, MemorySample

    class _Outcome:
        telemetry = [
            MemorySample(point=BASELINE, captured_at=0.0, swap_used_bytes=4 * 1024 ** 3),
            MemorySample(point=POST_RUN, captured_at=1.0,
                         swap_used_bytes=4 * 1024 ** 3 + 1024 ** 2),
        ]

    assert _swap_warnings(_Outcome()) == []  # type: ignore[arg-type]


def test_a_thinking_build_is_distinguishable_from_a_quiet_one_by_evidence() -> None:
    """M22b's exit: from evidence rather than from the model's name.

    `ravis/auto` breaks a tie on smallest-build-is-cheapest, which on this
    machine picks a reasoning distill that spends most of a small `max_tokens`
    budget thinking and emits little content. No runtime advertises that --
    LM Studio publishes `type`, `arch` and `quantization` and nothing about
    reasoning -- so RAVIS needs a measurement, and a guess from the name is
    what §12.2 exists to stop.

    The zero matters as much as the non-zero. A build that reports its tokens
    and shows every one of them as content spent nothing thinking, and that is
    the fact which makes it distinguishable rather than merely unmeasured.
    """
    from sirvis.benchmarks.engine import Repetition

    def rep(**fields: object) -> Repetition:
        return Repetition(index=0, phase="measured", test_id="t1", total_seconds=1.0,
                          content="answer", **fields)  # type: ignore[arg-type]

    thinker = rep(completion_tokens=80, reasoning_tokens=64, chunk_count=16)
    quiet = rep(completion_tokens=80, chunk_count=80)

    assert thinker.reasoning_share == 0.8
    assert quiet.reasoning_share == 0.0
    assert thinker.reasoning_share > quiet.reasoning_share


def test_a_runtime_that_counts_nothing_yields_no_share_rather_than_zero() -> None:
    """Absence and zero are different claims, and only one of them is safe.

    A zero would tell RAVIS the build spends nothing on thinking, which is
    exactly the tiebreak input -- asserting it from a runtime that reported no
    tokens at all would be the invented measurement this engine exists to avoid.
    """
    from sirvis.benchmarks.engine import Repetition

    silent = Repetition(index=0, phase="measured", test_id="t1", total_seconds=1.0,
                        content="answer", chunk_count=12)

    assert silent.reasoning_share is None


async def test_the_share_reaches_the_evidence_record(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """And it has to arrive as a metric RAVIS can read, not stay in the engine."""
    outcome, _ = await _run(FakeRuntime(), results_root=tmp_path)

    share = outcome.record.measurements.get("reasoning_token_share")

    assert share is not None, "M22b's measurement must reach the record"
    assert share.unit == "fraction"
    assert share.direction == "lower", "less of the budget lost to thinking is better"


async def test_the_published_share_carries_what_a_consumer_ranks_on(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The wire half of the contract, pinned from this side of it.

    RAVIS M16 ranks on this measurement and reads exactly three fields out of
    it: `median` for the value, `direction` to know which way to sort, and
    `provenance.kind` to refuse anything weaker than a count. RAVIS does not
    depend on this package and cannot assert the shape it is sent — so the
    check has to live here, where the shape is decided.

    Naming the three fields is the point. A rename that keeps the measurement
    working locally would leave the consumer silently reading `None`, which
    reads as "never measured" rather than as a break — and the whole design of
    that consumer is to stay quiet when nothing was measured.
    """
    outcome, _ = await _run(FakeRuntime(), results_root=tmp_path)

    published = outcome.record.measurements["reasoning_token_share"].as_dict()

    assert isinstance(published["median"], float)
    assert published["direction"] == "lower"
    assert published["provenance"]["kind"] in ("MEASURED", "ESTIMATED")


async def test_a_disk_that_fills_mid_run_ends_the_run_rather_than_leaving_it_running(
    tmp_path,  # type: ignore[no-untyped-def]
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """**Measured on 8 September 2026 and it was a real defect.** A results
    write that raised `ENOSPC` was caught by nothing: the exception left the
    engine, the lease was released correctly, and the row stayed
    `running / preparing` for a run that had ended. §10 asks for truthful state
    under the condition, and a run listed as running is the one state an
    operator acts on — they wait for it.
    """
    import errno

    from sirvis.storage.results import ResultDirectory

    def full(*_: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ResultDirectory, "write_telemetry", full)
    runtime = FakeRuntime()

    outcome, database = await _run(runtime, results_root=tmp_path)

    assert outcome.state is RunState.FAILED
    assert "No space left" in outcome.detail
    rows = database.connection.execute("SELECT state FROM benchmark_run").fetchall()
    assert [row["state"] for row in rows] == ["failed"]


async def test_a_full_disk_still_releases_the_model_it_held(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The lease is the expensive thing to strand — a multi-gigabyte model held
    by a run nobody is watching — and the disk failing must not skip the
    release any more than a runtime failure does."""
    import errno

    from sirvis.storage.results import ResultDirectory

    def full(*_: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(ResultDirectory, "write_telemetry", full)
    runtime = FakeRuntime()

    await _run(runtime, results_root=tmp_path)

    assert runtime.unloads == [MODEL]


# ── A failure after the model is loaded must still give it back ──────────────


async def test_a_failure_after_the_load_still_releases_the_model(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """**The lease was acquired outside the block that releases it.**

    `_execute` took the lease and then did five fallible things before its
    `try` began — a memory sample, a log write, the variant confirmation, an
    inventory read and a thermal reading. An exception in any of them skipped
    the `finally` that releases, so a failed benchmark left a model loaded and
    holding capacity that nothing would ever reclaim: the next run would find
    the machine full because of a run that had already given up.

    Found by an external audit on 9 September 2026. The inventory read is used
    here because it is the plainest of the five — a runtime that answers the
    load and then stops answering is an ordinary way for this to happen.
    """
    class LosesTheRuntimeAfterLoading(FakeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.listed = 0

        async def list_loaded_models(self) -> list[LoadedModel]:
            self.listed += 1
            # The first read is the engine's "what was resident before"; the
            # failure is the one it makes after the model is in memory.
            if self.listed > 1:
                raise RuntimeUnavailableError("the runtime stopped answering")
            return []

    runtime = LosesTheRuntimeAfterLoading()

    outcome, _ = await _run(runtime, results_root=tmp_path)

    assert outcome.state is not RunState.SUCCEEDED, "the failure did not register"
    assert runtime.loads, "the model was never loaded, so this proves nothing"
    assert runtime.unloads == [MODEL], (
        "a benchmark that failed after loading kept the model resident, and the "
        f"capacity with it: loaded {runtime.loads}, unloaded {runtime.unloads}"
    )


async def test_a_cancelled_run_does_not_start_the_tool_trials(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """**The stop was seen, accepted, and then ignored by the next phase.**

    Cancellation is cooperative and its granularity is one test — that much is
    deliberate and documented. What was not deliberate: the loop noticed the
    request to stop, broke out of the prose tests, and then ran
    `_run_tool_trials` unconditionally. An ordinary agent benchmark is eight
    phrasings times three repetitions, so pressing cancel bought twenty-four
    further generations, the model held for all of them and the serial queue
    with it.

    This is not a complaint about mid-inference cancellation. The engine had
    already inspected the flag at a safe boundary and agreed to stop; the
    defect is that it then began new work. Found by an external audit,
    9 September 2026.
    """
    runtime = FakeRuntime()
    spec = _spec(tool_trials=True)

    outcome, _ = await _run(runtime, spec, results_root=tmp_path,
                            should_stop=lambda: True)

    assert runtime.generations == 0, (
        "a run cancelled before its first test still generated "
        f"{runtime.generations} time(s) — the tool trials started anyway"
    )
    assert any("cancelled" in text for _scope, text in outcome.warnings), (
        "the run does not say it was cancelled"
    )
