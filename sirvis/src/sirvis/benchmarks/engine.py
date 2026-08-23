"""The single-model benchmark engine (§11, milestone M6).

M6's exit criterion is one sentence — `sirvis benchmark run examples/basic.yaml`
persists a valid result — and the lifecycle it has to walk is §11.2's:

    validate → capture baseline → acquire resources → load → record load time →
    verify effective config → warm up → measured repetitions → evaluate →
    finalize telemetry → unload if owned → persist

Four things here are decisions rather than mechanics.

**Nothing loads except through the Resource Manager.** §9 requires every load
and unload to flow through one owner, and a benchmark engine is exactly the
component that would be tempted to bypass it — it wants the model to itself. It
takes a lease instead, which means a run cannot evict a model RAVIS is serving
from, and a crashed run's model is reclaimed when the lease lapses rather than
stranded.

**A warm model is not a load.** If the build is already resident when the run
starts, no load time is recorded at all. The alternative — reporting the few
milliseconds an already-satisfied acquire takes — would publish a load time
three orders of magnitude too fast, and it would look like the best result in
the table.

**Token counts are believed only when the runtime reports them.** With
`stream_options.include_usage` LM Studio returns real counts; without them the
only available number is a count of stream chunks, which is close to a token
count and is not one. Throughput derived that way is published at `ESTIMATED`
provenance, and §12.1's lattice then weakens the whole record — which is the
correct outcome, not a defect.

**A generation that returns nothing is a result, not an error.** M2 found
`qwen3-1.7b` spending an entire eight-token budget on reasoning and emitting no
content. The call succeeded and the model said nothing; those are separate
facts, so the run completes and the record carries a validity warning.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Mapping, Protocol, Sequence

from sirvis.benchmarks.spec import BenchmarkTest, ExperimentSpec, suppressed
from sirvis.core.evidence import (
    EvidenceIdentity,
    EvidenceKind,
    EvidenceRecord,
    Measurement,
    Provenance,
    Validity,
)
from sirvis.core.inventory import Inventory, build_inventory
from sirvis.core.machine import record_snapshot
from sirvis.errors import ModelNotFoundError, RuntimeUnreachableError
from sirvis.resources import ResourceManager
from sirvis.runtimes.base import GenerationChunk, LoadedModel, RuntimeUnavailableError
from sirvis.storage import (
    Database,
    ResultDirectory,
    RunState,
    StoredResult,
    create_experiment,
    finish_run,
    start_run,
)
from sirvis.telemetry import (
    AFTER_LOAD,
    BASELINE,
    BEFORE_GENERATION,
    POST_RUN,
    POST_UNLOAD,
    MemoryProbe,
    MemorySample,
    MemoryWatcher,
    SystemSnapshot,
    detect_system,
)

OWNER = "sirvis.benchmark"
RUNTIME_KEY = "lmstudio"

# Versioned method names (§12.1): a number produced by v1 is never silently
# compared with one from v2, so changing how something is measured makes new
# evidence rather than editing old evidence.
METHOD_TTFT = "stream.time_to_first_token.v1"
METHOD_THROUGHPUT = "stream.generation_tokens_per_second.v1"
METHOD_LATENCY = "stream.total_latency.v1"
METHOD_LOAD = "resource_manager.load_time.v1"

# How much of a runtime's reported output must have shown up as content before
# that count is believed to describe the content stream.
#
# Measured on this machine rather than chosen: an ordinary model's content
# chunks account for **93–100%** of its reported completion tokens (130/137,
# 250/255, 117/123, 256/256), while a reasoning model measured with its thinking
# suppressed managed **27 of 255** — the other 228 were thinking, generated
# before the first content token ever arrived. The separation is an order of
# magnitude wide, so the exact threshold does not matter; what matters is that
# below it, the reported count is describing work the content window never saw.
CONTENT_TOKEN_AGREEMENT = 0.8

# Some runtimes route a model's thinking into `reasoning_content`, where this
# engine never sees it. Others do not, and the thinking arrives as ordinary
# content wrapped in these tags — `tencent/Hunyuan-1.8B` does exactly that here.
#
# Measuring that as an answer is not a small error. A Hunyuan run reported a
# 0.043 s time-to-first-token and 55 tokens/second over 256 tokens that
# contained no answer at all, and looked clean doing it: the empty-content check
# could not fire, because from the stream's point of view the model was talking.
THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

# Finish reasons that mean the model stopped for a reason the experiment chose.
# Anything else is §11.8's "unexpected generation stop" and becomes a warning.
_EXPECTED_STOPS = ("stop", "length", "eos", None)


class GenerationRuntime(Protocol):
    """The slice of a runtime this engine reads.

    Deliberately does not include `load` or `unload`: the engine must not be
    able to drive lifecycle even by accident (§9). Those reach the runtime only
    through the Resource Manager, which is passed separately.
    """

    def stream_generate(
        self, model_key: str, messages: list[dict[str, Any]], **options: Any
    ) -> AsyncIterator[GenerationChunk]:
        ...

    async def list_models(self) -> list[dict[str, Any]]:
        ...

    async def list_loaded_models(self) -> list[LoadedModel]:
        ...


def answer_offset(text: str) -> int | None:
    """Where the answer starts in a stream that may open with a think block.

    `None` means *not yet knowable*, which is a third answer and the reason this
    is a function rather than a condition inline: a stream arrives in arbitrary
    chunks, so the first one may be `<th` — neither a think block nor an answer
    until more of it exists. Deciding early either way mis-times the first token.
    """
    head = text.lstrip()
    if not head:
        return None
    if head.startswith(THINK_OPEN):
        closed = text.find(THINK_CLOSE)
        return None if closed < 0 else closed + len(THINK_CLOSE)
    if THINK_OPEN.startswith(head[: len(THINK_OPEN)]):
        # Still could become "<think>" once another chunk lands.
        return None
    return len(text) - len(head)


@dataclass(frozen=True)
class Repetition:
    """One generation, measured. Every one of these is preserved (§11.7).

    `token_source` says whether the counts came from the runtime or from
    counting stream chunks, and it travels with the numbers rather than being
    resolved into a boolean somewhere upstream — a consumer comparing two
    results needs to know that one of them was estimated.
    """

    test_id: str
    phase: str
    index: int
    total_seconds: float
    # The answer only. Thinking that arrived inline is split off into
    # `thinking`, so a metric computed from this is about what the model said
    # rather than about what it thought first.
    content: str
    ttft_seconds: float | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    chunk_count: int = 0
    token_source: str = "reported"
    lowest_available_bytes: int | None = None
    # Preserved rather than discarded: §11.9 keeps raw output so a result can be
    # re-read later, and "the model thought for 900 characters and never
    # answered" is the finding, not noise to drop.
    thinking: str = ""

    @property
    def hidden_tokens(self) -> int:
        """Tokens the runtime counted that never arrived as content.

        A reasoning model generates its thinking *before* the first content
        token, so those tokens are spent inside the time-to-first-token window
        rather than in the generation window. Reported as a number rather than a
        flag because it is the size of the gap that makes it unmistakable: a
        handful of tokens is ordinary stream bookkeeping, and two hundred is a
        model that thought first.
        """
        if self.completion_tokens is None or not self.chunk_count:
            return 0
        if self.chunk_count >= self.completion_tokens * CONTENT_TOKEN_AGREEMENT:
            return 0
        return self.completion_tokens - self.chunk_count

    @property
    def content_tokens(self) -> int | None:
        """The tokens this repetition actually spent producing its answer.

        The runtime's own count when it agrees with what the stream showed, and
        the content-chunk count when it does not — because a total that includes
        two hundred tokens of thinking cannot be divided by the window in which
        the answer appeared.
        """
        if self.completion_tokens is None:
            return self.chunk_count or None
        return self.chunk_count if self.hidden_tokens else self.completion_tokens

    @property
    def generation_tokens_per_second(self) -> float | None:
        """Answer tokens per second, excluding prompt processing *and thinking*.

        §11.4 keeps prompt throughput and generation throughput apart, and the
        divide is the first token: everything before it is the runtime reading
        the prompt, everything after is it writing. Dividing by the total would
        blend the two and make a long prompt look like a slow model.

        **A reasoning model breaks that split**, and did so live before this
        guard existed: `lfm2.5-2.6b-mlx`, measured with its thinking suppressed,
        spent 3.36 s generating 228 tokens of reasoning and then 0.33 s
        producing 27 tokens of answer. Dividing all 255 by the 0.33 s window
        published **767 tokens/second** for a 2.6B model on a laptop — an
        impossible number, at `MEASURED` provenance, from arithmetic that was
        correct for every model that does not think.
        """
        tokens = self.content_tokens
        if self.ttft_seconds is None or tokens is None:
            return None
        generating = self.total_seconds - self.ttft_seconds
        if generating <= 0 or tokens <= 1:
            # One token, or a stream that finished within the resolution of the
            # clock. A rate computed from that describes the timer.
            return None
        return (tokens - 1) / generating

    def as_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "phase": self.phase,
            "index": self.index,
            "ttft_seconds": self.ttft_seconds,
            "total_seconds": self.total_seconds,
            "finish_reason": self.finish_reason,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "chunk_count": self.chunk_count,
            "hidden_tokens": self.hidden_tokens,
            "token_source": self.token_source,
            "generation_tokens_per_second": self.generation_tokens_per_second,
            "content": self.content,
            "thinking": self.thinking,
            "lowest_available_bytes": self.lowest_available_bytes,
        }


@dataclass
class ExperimentOutcome:
    """What a run produced, and where the raw material for it lives."""

    experiment_id: str
    run_id: str
    state: RunState
    detail: str
    results_path: str
    record: EvidenceRecord | None = None
    result_ids: list[str] = field(default_factory=list)
    repetitions: list[Repetition] = field(default_factory=list)
    telemetry: list[MemorySample] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The suppression that got this build answering, when it answered nothing as
    # written. Part of the evidence identity rather than a footnote, because a
    # measurement taken under a changed prompt is evidence about a different
    # question and must not collide with the one the suite asked.
    thinking_suppression: str | None = None
    suppressions_tried: list[str] = field(default_factory=list)


async def run_experiment(
    spec: ExperimentSpec,
    *,
    runtime: GenerationRuntime,
    resources: ResourceManager,
    database: Database,
    results_root: str,
    probe: MemoryProbe | None = None,
    snapshot: SystemSnapshot | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> ExperimentOutcome:
    """Run one single-model experiment end to end and persist its result.

    Returns the outcome rather than raising on a failed benchmark: §4.2's rule
    that "a successful HTTP request is not a successful benchmark" has a
    counterpart here — a benchmark that fails has still produced a run, a
    reason, and usually some telemetry, and throwing all of that away to raise
    an exception loses the diagnosis with the result.
    """
    sampler = probe or MemoryProbe()
    telemetry = [sampler.sample(BASELINE)]
    inventory = await _inventory(runtime)
    build = _resolve(inventory, spec.model_key)

    machine = record_snapshot(database, snapshot or detect_system())
    experiment_id = create_experiment(
        database, spec.as_dict(), suite_id=spec.suite_id,
        suite_version=spec.suite_version, environment_mode=spec.environment_mode,
    )
    directory = ResultDirectory(results_root, experiment_id)
    directory.prepare()
    directory.write_experiment(spec.as_dict())
    directory.write_system(machine)
    directory.write_runtime({"runtime_key": RUNTIME_KEY, "installed": len(inventory.installed)})

    run_id = start_run(
        database, experiment_id, runtime_key=RUNTIME_KEY,
        runtime_snapshot={"runtime_key": RUNTIME_KEY},
        machine_snapshot_id=machine["snapshot_id"],
        results_path=str(directory.path),
    )
    outcome = ExperimentOutcome(
        experiment_id=experiment_id, run_id=run_id, state=RunState.RUNNING,
        detail="preparing", results_path=str(directory.path), telemetry=telemetry,
    )

    try:
        await _execute(spec, runtime, resources, sampler, directory, outcome, clock)
    except (RuntimeUnavailableError, RuntimeUnreachableError) as failure:
        # A runtime that stopped answering mid-run is the ordinary failure here,
        # and the partial telemetry is worth more than the exception: it says
        # how far the run got and what memory looked like when it stopped.
        directory.append_log(f"failed: {failure}")
        directory.write_telemetry([sample.as_dict() for sample in outcome.telemetry])
        finish_run(database, run_id, state=RunState.FAILED, detail=str(failure))
        outcome.state, outcome.detail = RunState.FAILED, str(failure)
        return outcome

    record = _evidence(spec, build, outcome, machine)
    outcome.record = record
    directory.write_result(record.as_dict())
    directory.write_telemetry([sample.as_dict() for sample in outcome.telemetry])
    outcome.result_ids = finish_run(
        database, run_id, state=RunState.SUCCEEDED, detail="completed",
        results=[StoredResult(
            target_key=spec.model_key,
            evidence_id=record.identity.evidence_id,
            validity=record.validity.value,
            payload=record.as_dict(),
        )],
    )
    outcome.state, outcome.detail = RunState.SUCCEEDED, "completed"
    return outcome


async def _execute(
    spec: ExperimentSpec,
    runtime: GenerationRuntime,
    resources: ResourceManager,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: ExperimentOutcome,
    clock: Callable[[], float],
) -> None:
    """§11.2's lifecycle for a single model, between acquire and release."""
    resident_before = {model.model_key for model in await runtime.list_loaded_models()}
    was_warm = spec.model_key in resident_before

    started = clock()
    lease = await resources.acquire(
        owner=OWNER, model_key=spec.model_key, configuration=dict(spec.load)
    )
    load_seconds = None if was_warm else clock() - started
    if was_warm:
        outcome.warnings.append(
            f"{spec.model_key} was already resident, so no load time was measured — "
            "a warm acquire says nothing about how long this model takes to load"
        )
    outcome.telemetry.append(sampler.sample(AFTER_LOAD))
    directory.append_log(
        f"acquired {spec.model_key} (session {lease.session_id}, "
        f"{'warm' if was_warm else f'loaded in {load_seconds:.2f}s'})"
    )

    outcome.warnings.extend(
        _configuration_warnings(spec, await runtime.list_loaded_models())
    )

    try:
        for test in spec.tests:
            await _run_test(spec, test, runtime, sampler, directory, outcome, clock)
    finally:
        outcome.telemetry.append(sampler.sample(POST_RUN))
        # §11.2: unload if owned. `release` unloads only what nobody else holds,
        # which is the whole reason the manager exists — a model RAVIS is also
        # using stays where it is.
        await resources.release(lease.session_id)
        outcome.telemetry.append(sampler.sample(POST_UNLOAD))

    if load_seconds is not None:
        outcome.repetitions.append(
            Repetition(test_id="__load__", phase="load", index=0,
                       total_seconds=load_seconds, content="")
        )


async def _run_test(
    spec: ExperimentSpec,
    test: BenchmarkTest,
    runtime: GenerationRuntime,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: ExperimentOutcome,
    clock: Callable[[], float],
) -> None:
    """Warm up, then measure, one test at a time.

    Warmups are per test rather than once per experiment because they exist to
    absorb the runtime's first-call costs *for this prompt* — LM Studio loads on
    demand, and a prompt length it has not seen pays for its own KV cache.
    """
    warmed = []
    for index in range(spec.warmups):
        result = await _measure(test, spec, runtime, sampler, "warmup", index, outcome, clock)
        directory.write_response(test.id, "warmup", index, result.as_dict())
        warmed.append(result)

    # **The warmups are the probe.** They already run, §11.7 already excludes
    # them from the statistics, and a build that said nothing in all of them is
    # about to say nothing five more times. Spending the measured repetitions to
    # discover that produces a result nobody can use.
    effective = test
    if warmed and not any(warm.content for warm in warmed) and spec.suppress_thinking:
        effective = await _suppress(spec, test, runtime, sampler, directory, outcome, clock)

    for index in range(spec.repetitions):
        result = await _measure(
            effective, spec, runtime, sampler, "measured", index, outcome, clock
        )
        directory.write_response(test.id, "measured", index, result.as_dict())
        outcome.repetitions.append(result)


async def _suppress(
    spec: ExperimentSpec,
    test: BenchmarkTest,
    runtime: GenerationRuntime,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: ExperimentOutcome,
    clock: Callable[[], float],
) -> BenchmarkTest:
    """Ask the same question differently, and keep the first phrasing answered.

    Reached only when the build produced no content in *every* warmup. That is
    not a failure to record and move on from: a reasoning model that spends its
    whole budget thinking has done real work — `qwen3-1.7b` and `lfm2.5-2.6b-mlx`
    each generated 255 tokens and emitted none of them — and "it said nothing"
    measures the token cap rather than the model.

    Each attempt costs one generation and stops at the first that answers.
    Nothing is inferred from the model's name: `/no_think` works for Qwen and is
    inert text elsewhere, so the strategies are *tried* rather than selected, and
    the one that worked is recorded. When none works, the run measures exactly
    what was asked and says which phrasings were attempted — which is strictly
    more than the bare "returned no content" it used to report.
    """
    outcome.suppressions_tried = list(spec.suppress_thinking)
    for strategy in spec.suppress_thinking:
        candidate = suppressed(test, strategy)
        probe = await _measure(
            candidate, spec, runtime, sampler, f"probe-{strategy}", 0, outcome, clock
        )
        directory.write_response(test.id, f"probe-{strategy}", 0, probe.as_dict())
        if probe.content:
            outcome.thinking_suppression = strategy
            directory.append_log(
                f"{test.id}: no content as written; measuring with {strategy}"
            )
            return candidate
    directory.append_log(
        f"{test.id}: no content as written, and none of "
        f"{', '.join(spec.suppress_thinking)} changed that"
    )
    return test


async def _measure(
    test: BenchmarkTest,
    spec: ExperimentSpec,
    runtime: GenerationRuntime,
    sampler: MemoryProbe,
    phase: str,
    index: int,
    outcome: ExperimentOutcome,
    clock: Callable[[], float],
) -> Repetition:
    """One generation, timed from the request to the last chunk.

    The clock starts before the request is sent, which is deliberate: §11.4's
    time-to-first-token is what a *user* waits, and the connection and prompt
    processing are part of that wait whether or not they are the model's fault.
    """
    outcome.telemetry.append(sampler.sample(BEFORE_GENERATION))
    started = clock()
    first_token_at: float | None = None
    text = ""
    answer_from: int | None = None
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    chunks = 0

    async with MemoryWatcher(sampler) as watcher:
        async for chunk in runtime.stream_generate(
            spec.model_key, test.messages(), **test.generation.as_options()
        ):
            if chunk.content:
                text += chunk.content
                if answer_from is None:
                    # Still inside a think block, or too little text to tell.
                    # Neither the clock nor the chunk counter may start yet: both
                    # describe the answer, and the answer has not begun.
                    offset = answer_offset(text)
                    if offset is not None and len(text) > offset:
                        answer_from = offset
                        first_token_at = clock()
                        chunks = 1
                else:
                    chunks += 1
            finish_reason = chunk.finish_reason or finish_reason
            usage = chunk.usage or usage

    total = clock() - started
    outcome.telemetry.extend(watcher.samples)
    completion, source = _completion_tokens(usage, chunks)
    thought = text if answer_from is None else text[:answer_from]
    return Repetition(
        test_id=test.id,
        phase=phase,
        index=index,
        total_seconds=total,
        content="" if answer_from is None else text[answer_from:],
        thinking=thought,
        ttft_seconds=None if first_token_at is None else first_token_at - started,
        finish_reason=finish_reason,
        prompt_tokens=_count(usage, "prompt_tokens"),
        completion_tokens=completion,
        chunk_count=chunks,
        token_source=source,
        lowest_available_bytes=watcher.lowest_available_bytes,
    )


def _completion_tokens(usage: Mapping[str, Any] | None, chunks: int) -> tuple[int | None, str]:
    """Output tokens, and where the number came from.

    Counting content chunks is *close* to counting tokens and is not the same
    thing — a runtime may batch several tokens into one frame, or emit one for a
    role delta. So the estimate is labelled, and everything derived from it is
    published as `ESTIMATED` rather than as a measurement.
    """
    reported = _count(usage, "completion_tokens")
    if reported is not None:
        return reported, "reported"
    return (chunks or None), "stream_chunks"


def _count(usage: Mapping[str, Any] | None, key: str) -> int | None:
    value = (usage or {}).get(key)
    return int(value) if isinstance(value, int) else None


async def _inventory(runtime: GenerationRuntime) -> Inventory:
    """What is installed, or a clear failure — never an empty catalogue.

    An unreachable runtime returning "nothing is installed" would make the next
    step report the model as missing, which sends whoever reads it looking for a
    download rather than for a closed application.
    """
    try:
        return build_inventory(await runtime.list_models())
    except RuntimeUnavailableError as failure:
        raise RuntimeUnreachableError(
            f"the runtime is not answering, so nothing can be measured: {failure}"
        ) from failure


def _resolve(inventory: Inventory, model_key: str) -> dict[str, Any]:
    """The build behind a runtime's name for it (§6), or a 404 that stays one."""
    model = inventory.by_runtime_key(model_key)
    if model is None:
        raise ModelNotFoundError(
            f"no installed build carries the runtime key {model_key!r}", runtime_key=model_key
        )
    variant = inventory.variants[model.variant_id]
    family = inventory.families[variant.family_id]
    return {"model": model, "variant": variant, "family": family}


def _configuration_warnings(spec: ExperimentSpec, resident: Sequence[LoadedModel]) -> list[str]:
    """§7.1 and §11.8: an effective configuration that is not the requested one.

    Not an error. A benchmark run at 8K on a request for 32K is a perfectly good
    measurement — of a model at 8K — and the only unacceptable outcome is
    publishing it as though it answered the question that was asked.
    """
    loaded = next((model for model in resident if model.model_key == spec.model_key), None)
    if loaded is None:
        return []
    warnings = []
    requested = spec.load.get("context_length")
    effective = loaded.effective.get("context_length")
    if requested and effective and int(requested) != int(effective):
        warnings.append(
            f"requested context_length {requested} but the model is loaded at {effective}; "
            "this result describes the configuration that ran, not the one asked for"
        )
    if loaded.ignored:
        warnings.append(f"the runtime ignored: {', '.join(loaded.ignored)}")
    return warnings


def _evidence(
    spec: ExperimentSpec,
    build: dict[str, Any],
    outcome: ExperimentOutcome,
    machine: Mapping[str, Any],
) -> EvidenceRecord:
    """Turn the repetitions into §12.3's envelope.

    Every metric is built from its samples — there is no path here that produces
    a headline without them, because `Measurement` has no constructor that takes
    one (M7).
    """
    measured = [r for r in outcome.repetitions if r.phase == "measured"]
    warnings = list(outcome.warnings) + _generation_warnings(measured)
    warnings += _suppression_warnings(outcome, measured)
    # Either the runtime reported nothing, or it reported a count the content
    # stream contradicts. Both mean the numerator is inferred rather than
    # counted, and §12.1's lattice then weakens the whole record — which is the
    # correct outcome for a rate derived from a chunk count.
    estimated = any(r.token_source != "reported" or r.hidden_tokens for r in measured)

    measurements: dict[str, Measurement] = {}
    _add(measurements, "time_to_first_token_seconds", [r.ttft_seconds for r in measured],
         unit="seconds", direction="lower", method=METHOD_TTFT)
    _add(measurements, "total_latency_seconds", [r.total_seconds for r in measured],
         unit="seconds", direction="lower", method=METHOD_LATENCY)
    _add(measurements, "generation_tokens_per_second",
         [r.generation_tokens_per_second for r in measured],
         unit="tokens/second", direction="higher", method=METHOD_THROUGHPUT,
         kind=EvidenceKind.ESTIMATED if estimated else EvidenceKind.MEASURED,
         notes="derived from stream chunk count; the runtime reported no token usage"
         if estimated else None)
    load = [r.total_seconds for r in outcome.repetitions if r.phase == "load"]
    _add(measurements, "load_time_seconds", list(load), unit="seconds",
         direction="lower", method=METHOD_LOAD)

    # The suppression belongs *in the identity*, not beside it. §12.2 keys
    # evidence on the configuration a number was produced under, and a prompt
    # that had to be changed to get an answer is a different configuration — so
    # the adapted result gets its own evidence ID and can never be averaged with
    # or mistaken for the one the suite asked for.
    configuration = dict(spec.load)
    if outcome.thinking_suppression:
        configuration["thinking_suppression"] = outcome.thinking_suppression

    variant = build["variant"]
    identity = EvidenceIdentity(
        machine_id=str(machine["machine_id"]),
        model_family=build["family"].display_name,
        model_variant=variant.variant_id,
        runtime=RUNTIME_KEY,
        role=spec.role,
        benchmark_suite=spec.suite_id,
        benchmark_version=spec.suite_version,
        source_repository=variant.source_repository,
        model_format=variant.runtime_format,
        quantization=variant.quantization,
        runtime_configuration=configuration,
    )
    return EvidenceRecord(
        identity=identity,
        measurements=measurements,
        validity=Validity.SUSPECT if warnings else Validity.VALID,
        validity_notes=tuple(warnings),
        machine_snapshot_id=str(machine["snapshot_id"]),
    )


def _generation_warnings(measured: Sequence[Repetition]) -> list[str]:
    """§11.8's validity warnings that this engine can actually observe.

    Only the ones it can see. Background contention and load instability are on
    §11.8's list and are not claimed here, because asserting a warning nothing
    checked for would be worse than the gap.
    """
    warnings = []
    empty = [r.index for r in measured if not r.content]
    if empty:
        # M2 found a reasoning model spending its whole budget on thinking. The
        # call succeeded and the model said nothing, and those are two facts.
        warnings.append(
            f"{len(empty)} measured repetition(s) returned no content — the call "
            "succeeded but the model produced nothing to measure"
        )
    unexpected = sorted({r.finish_reason for r in measured
                         if r.finish_reason not in _EXPECTED_STOPS})
    if unexpected:
        warnings.append(f"unexpected generation stop: {', '.join(str(r) for r in unexpected)}")
    return warnings


def _suppression_warnings(
    outcome: ExperimentOutcome, measured: Sequence[Repetition]
) -> list[str]:
    """What the run had to do to get an answer, and what it could not measure.

    An adapted run is `SUSPECT` rather than `VALID`, and deliberately: the
    numbers are sound, but they are not about the prompt the suite declares, and
    a consumer filtering for clean measurements should not silently receive one
    taken under a different question.
    """
    notes = []
    thought = [r.hidden_tokens for r in measured if r.hidden_tokens]
    if thought:
        notes.append(
            f"{len(thought)} repetition(s) generated tokens that never arrived as "
            f"content — a median of {int(statistics.median(thought))} of them. Those are "
            "reasoning, spent before the first answer token: the throughput here covers "
            "the answer only, and the time-to-first-token includes the thinking"
        )
    if outcome.thinking_suppression:
        notes.append(
            "the prompt as written produced no content, so this was measured with "
            f"thinking suppressed via {outcome.thinking_suppression} — a different "
            "prompt from the one this suite declares, and recorded in the evidence "
            "identity as such"
        )
    elif outcome.suppressions_tried:
        notes.append(
            "the build answered nothing as written, and none of "
            f"{', '.join(outcome.suppressions_tried)} changed that"
        )
    return notes


def _add(target: dict[str, Measurement], name: str, values: Sequence[float | None], *,
         unit: str, direction: str, method: str,
         kind: EvidenceKind = EvidenceKind.MEASURED, notes: str | None = None) -> None:
    """Add a metric, or add nothing at all.

    A metric missing from even one repetition is omitted entirely rather than
    summarised over the ones that worked. §11.7's whole argument is that a
    headline means something only in relation to the samples behind it, and a
    median over "the three takes that happened to report tokens" is a number
    whose sample size is a coincidence.
    """
    present = [value for value in values if value is not None]
    if not present or len(present) != len(values):
        return
    target[name] = Measurement(
        repetitions=tuple(present), unit=unit, direction=direction,
        provenance=Provenance(kind=kind, method=method, notes=notes),
    )
