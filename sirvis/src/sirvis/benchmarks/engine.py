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

import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Mapping, Protocol, Sequence

from sirvis.benchmarks.spec import BenchmarkTest, ExperimentSpec
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
    content: str
    ttft_seconds: float | None = None
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    chunk_count: int = 0
    token_source: str = "reported"
    lowest_available_bytes: int | None = None

    @property
    def generation_tokens_per_second(self) -> float | None:
        """Output tokens per second of *generation*, excluding prompt processing.

        §11.4 keeps prompt throughput and generation throughput apart, and the
        divide is the first token: everything before it is the runtime reading
        the prompt, everything after is it writing. Dividing by the total would
        blend the two and make a long prompt look like a slow model.
        """
        if self.ttft_seconds is None or self.completion_tokens is None:
            return None
        generating = self.total_seconds - self.ttft_seconds
        if generating <= 0 or self.completion_tokens <= 1:
            # One token, or a stream that finished within the resolution of the
            # clock. A rate computed from that describes the timer.
            return None
        return (self.completion_tokens - 1) / generating

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
            "token_source": self.token_source,
            "generation_tokens_per_second": self.generation_tokens_per_second,
            "content": self.content,
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
    for index in range(spec.warmups):
        result = await _measure(test, spec, runtime, sampler, "warmup", index, outcome, clock)
        directory.write_response(test.id, "warmup", index, result.as_dict())
    for index in range(spec.repetitions):
        result = await _measure(
            test, spec, runtime, sampler, "measured", index, outcome, clock
        )
        directory.write_response(test.id, "measured", index, result.as_dict())
        outcome.repetitions.append(result)


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
    pieces: list[str] = []
    finish_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    chunks = 0

    async with MemoryWatcher(sampler) as watcher:
        async for chunk in runtime.stream_generate(
            spec.model_key, test.messages(), **test.generation.as_options()
        ):
            if chunk.content:
                chunks += 1
                if first_token_at is None:
                    first_token_at = clock()
                pieces.append(chunk.content)
            finish_reason = chunk.finish_reason or finish_reason
            usage = chunk.usage or usage

    total = clock() - started
    outcome.telemetry.extend(watcher.samples)
    completion, source = _completion_tokens(usage, chunks)
    return Repetition(
        test_id=test.id,
        phase=phase,
        index=index,
        total_seconds=total,
        content="".join(pieces),
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
    estimated = any(r.token_source != "reported" for r in measured)

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
        runtime_configuration=dict(spec.load),
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
