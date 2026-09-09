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

import logging
import statistics
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Protocol, Sequence

from ecosystem_protocol import EventPublisher, stable_event_id

from sirvis.benchmarks.spec import BenchmarkTest, ExperimentSpec, suppressed
from sirvis.core.evidence import (
    EvidenceIdentity,
    EvidenceKind,
    EvidenceRecord,
    Measurement,
    Provenance,
    TrialRate,
    Validity,
    ValidityScope,
)
from sirvis.core.inventory import Inventory, build_inventory
from sirvis.core.machine import record_snapshot
from sirvis.core.models import ModelVariant
from sirvis.errors import (
    ModelNotFoundError,
    RuntimeUnreachableError,
    VariantUnconfirmedError,
)
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
    is_compromised,
    read_thermal_pressure,
)

logger = logging.getLogger("sirvis.benchmarks")

OWNER = "sirvis.benchmark"
RUNTIME_KEY = "lmstudio"

# Versioned method names (§12.1): a number produced by v1 is never silently
# compared with one from v2, so changing how something is measured makes new
# evidence rather than editing old evidence.
METHOD_TTFT = "stream.time_to_first_token.v1"
METHOD_THROUGHPUT = "stream.generation_tokens_per_second.v1"
METHOD_LATENCY = "stream.total_latency.v1"
METHOD_LOAD = "resource_manager.load_time.v1"
# M22b. Named as its own method because the two ways of arriving at the number
# are not interchangeable: an exact `reasoning_tokens` from the runtime, or the
# gap between the token count and the content chunks. The provenance says which.
METHOD_REASONING = "stream.reasoning_token_share.v1"

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
    # What the runtime says it spent thinking, when it says so at all. LM Studio
    # reports `usage.completion_tokens_details.reasoning_tokens` — 77 of 79 for
    # `gemma-4-e2b` — which is an exact figure where the chunk-count comparison
    # below is an inference. Absent for a model whose thinking arrives as
    # ordinary content, which is why both paths exist.
    reasoning_tokens: int | None = None
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
        # An exact answer beats a threshold whenever the runtime offers one.
        if self.reasoning_tokens:
            return self.reasoning_tokens
        if self.completion_tokens is None or not self.chunk_count:
            return 0
        if self.chunk_count >= self.completion_tokens * CONTENT_TOKEN_AGREEMENT:
            return 0
        return self.completion_tokens - self.chunk_count

    @property
    def reasoning_share(self) -> float | None:
        """The fraction of this completion spent thinking before answering.

        **M22b, and the reason it is a measurement rather than a flag.**
        `ravis/auto` breaks a tie on smallest-build-is-cheapest, which on this
        machine selects a reasoning distill that spends most of a small
        `max_tokens` budget on reasoning tokens and emits little or no content.
        RAVIS cannot know that from advertised metadata -- LM Studio's
        `/api/v0/models` publishes `type`, `arch` and `quantization` and nothing
        about reasoning -- and a guess from the model's name is exactly what
        §12.2 exists to stop.

        Zero is a real answer here, not an absence: a build that reports its
        tokens and shows every one of them as content spent nothing thinking,
        and that is the fact which makes it distinguishable from one that did.
        `None` is the genuine unknown -- a runtime that reported no token count
        at all, from which no share can be computed.
        """
        if not self.completion_tokens:
            return None
        return min(self.hidden_tokens / self.completion_tokens, 1.0)

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
        if self.reasoning_tokens:
            # Counted, not inferred: the answer is what is left once the
            # runtime's own reasoning figure is taken off the total.
            return max(self.completion_tokens - self.reasoning_tokens, 0)
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
            "reasoning_tokens": self.reasoning_tokens,
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
    # The rows `finish_run` wrote. Printed by the CLI summary rather than
    # merely held: an outcome that says where its files went but not which
    # database rows it produced leaves the lookup to a timestamp guess, which is
    # the reconstruction a stored id exists to avoid.
    result_ids: list[str] = field(default_factory=list)
    repetitions: list[Repetition] = field(default_factory=list)
    telemetry: list[MemorySample] = field(default_factory=list)
    # Scoped pairs, like every producer returns (§16 item 7). Was a bare list
    # of strings; the scope has to travel with the warning or a consumer is back
    # to matching prose.
    warnings: list[ScopedWarning] = field(default_factory=list)
    # The suppression that got this build answering, when it answered nothing as
    # written. Part of the evidence identity rather than a footnote, because a
    # measurement taken under a changed prompt is evidence about a different
    # question and must not collide with the one the suite asked.
    # The build the runtime confirmed while it was resident, when it could be
    # confirmed. Recorded here because the confirmation has to happen *during*
    # the run — see `_measure_model` — while the identity is written after it.
    confirmed_variant: Any = None
    thinking_suppression: str | None = None
    suppressions_tried: list[str] = field(default_factory=list)
    # What the runtime actually loaded, for the settings the experiment asked
    # about. This is what goes into the evidence identity — see `_evidence`.
    effective_configuration: dict[str, Any] = field(default_factory=dict)
    # §11.8's thermal reading, at both ends of the measured work. Two samples
    # rather than one because the interesting case is a machine that *became*
    # compromised while being measured — a run that starts nominal and ends
    # `fair` has its later repetitions taken under conditions its earlier ones
    # were not.
    thermal_before: str | None = None
    thermal_after: str | None = None
    # M13's tool-call trials, when the spec asked for them. `None` and "ran and
    # scored zero" are different findings and stay different: a build nobody
    # asked to call a tool has no tool evidence, which is not the same as a
    # build that was asked eight times and never managed it.
    tool_reliability: Any = None


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
    thermal: Callable[[], str | None] = read_thermal_pressure,
    events: EventPublisher | None = None,
    trace_id: str = "",
    should_stop: Callable[[], bool] | None = None,
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
    # **Attempted here and enforced after the load.** A cold machine has nothing
    # resident, so `lms ps` names nothing and this refused every run that had to
    # load its own model — the guard made a cold start impossible, and only a
    # machine that happened to be warm could benchmark at all. Confirming what
    # is loaded before loading it was the wrong moment to ask: nothing has
    # answered yet. The real gate is in `_measure_model`, with the model
    # resident; this pass takes the answer when a warm runtime can already give
    # one, so the identity is right from the start of the record.
    build["variant"] = _confirmed_variant(
        runtime, build["variant"], spec.model_key, required=False
    )

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

    # **Minted here when nobody supplied one, and stored on the row.**
    # A benchmark is not an HTTP request: it is started from the CLI, so there
    # is no inbound `traceparent` to inherit. A run without a trace publishes
    # nothing that can be correlated, so the id is created rather than left
    # empty — and it goes onto `benchmark_run` rather than into a local, so
    # that M14's enqueue-over-HTTP can populate the same column from the
    # caller's trace and change nothing here.
    trace = trace_id or uuid.uuid4().hex
    # Disabled unless a caller passed a configured one. Coalesced once so every
    # `emit` below is a single unconditional line — SIRVIS is gated at ruff's
    # complexity 8 and this function is already branchy.
    publisher = events or EventPublisher(service_type="sirvis")

    run_id = start_run(
        database, experiment_id, runtime_key=RUNTIME_KEY,
        runtime_snapshot={"runtime_key": RUNTIME_KEY},
        machine_snapshot_id=machine["snapshot_id"],
        results_path=str(directory.path),
        trace_id=trace,
    )
    # The opening event. Paired with a terminal one below so the span is an
    # interval: `traces.Span` refuses to derive a duration from a single event,
    # and a benchmark drawn as a point is exactly the wrong shape for the one
    # operation in this ecosystem that takes minutes.
    #
    # `machine_snapshot_id` and never the snapshot: `SystemSnapshot` labels
    # `hostname` sensitive and returns it anyway, and a hostname is very often
    # a person's first name. A consumer that wants the conditions dereferences
    # the id through `GET /machines/{id}`.
    publisher.emit(
        "sirvis.benchmark.started",
        trace_id=trace,
        data={
            "run_id": run_id,
            "experiment_id": experiment_id,
            "model_key": spec.model_key,
            "runtime_key": RUNTIME_KEY,
            "machine_snapshot_id": machine["snapshot_id"],
            "suite": spec.suite_id,
            "suite_version": spec.suite_version,
            "tests": len(spec.tests),
        },
        event_id=stable_event_id(run_id, "sirvis.benchmark.started"),
    )
    outcome = ExperimentOutcome(
        experiment_id=experiment_id, run_id=run_id, state=RunState.RUNNING,
        detail="preparing", results_path=str(directory.path), telemetry=telemetry,
    )

    try:
        await _execute(spec, runtime, resources, sampler, directory, outcome, clock,
                       thermal, should_stop)
    except (RuntimeUnavailableError, RuntimeUnreachableError, OSError) as failure:
        # A runtime that stopped answering mid-run is the ordinary failure here,
        # and the partial telemetry is worth more than the exception: it says
        # how far the run got and what memory looked like when it stopped.
        #
        # **`OSError` joined them when §10's full-disk cell was written**, and
        # the measurement is why: a disk that filled mid-run raised `ENOSPC`
        # out of a results write, nothing caught it, and the row stayed
        # `running / preparing` for a run that had ended. The lease was
        # released correctly, so the only casualty was the truth — and a run
        # listed as running is the one state an operator acts on.
        _say_what_happened(directory, outcome, failure)
        finish_run(database, run_id, state=RunState.FAILED, detail=str(failure))
        outcome.state, outcome.detail = RunState.FAILED, str(failure)
        # The class rather than a pasted message, plus a detail with the
        # operator's home directory folded back to `~`. A runtime failure string
        # is composed from a base URL, a subprocess invocation or a path, and
        # the default `lms` path expands to one containing the username. The
        # full text stays in the database, which is local; this crosses a wire.
        publisher.emit(
            "sirvis.benchmark.failed",
            trace_id=trace,
            severity="error",
            data={
                "run_id": run_id,
                "experiment_id": experiment_id,
                "model_key": spec.model_key,
                "failure": type(failure).__name__,
                "detail": _without_home(str(failure)),
            },
            event_id=stable_event_id(run_id, "sirvis.benchmark.failed"),
        )
        return outcome

    record = _evidence(spec, build, outcome, machine)
    outcome.record = record
    try:
        directory.write_result(record.as_dict())
        directory.write_telemetry([sample.as_dict() for sample in outcome.telemetry])
    except OSError as failure:
        # **A run that measured everything and could not write it down is a
        # failed run, not a successful one.** The measurements exist only in
        # this process, and §11.9's raw directory is what a rescoring reads —
        # calling this `succeeded` would put a row in the database pointing at
        # evidence that is not there.
        _say_what_happened(directory, outcome, failure)
        finish_run(database, run_id, state=RunState.FAILED, detail=str(failure))
        outcome.state, outcome.detail = RunState.FAILED, str(failure)
        publisher.emit(
            "sirvis.benchmark.failed",
            trace_id=trace,
            severity="error",
            data={
                "run_id": run_id,
                "experiment_id": experiment_id,
                "model_key": spec.model_key,
                "failure": type(failure).__name__,
                "detail": _without_home(str(failure)),
            },
            event_id=stable_event_id(run_id, "sirvis.benchmark.failed"),
        )
        return outcome
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
    publisher.emit(
        "sirvis.benchmark.completed",
        trace_id=trace,
        data={
            "run_id": run_id,
            "experiment_id": experiment_id,
            "model_key": spec.model_key,
            # The evidence's identity, not the evidence. A consumer follows the
            # id to `/results`; the record itself carries measurements, and the
            # raw takes behind them stay on disk for rescoring.
            "evidence_id": record.identity.evidence_id,
            "validity": record.validity.value,
            "results": len(outcome.result_ids),
        },
        event_id=stable_event_id(run_id, "sirvis.benchmark.completed"),
    )
    return outcome


def _say_what_happened(
    directory: ResultDirectory, outcome: Any, failure: Exception
) -> None:
    """Write the partial record, and never fail while reporting a failure.

    **The handler writes to the disk that may be the thing that broke.** On a
    full disk both of these raise `ENOSPC` again, and an exception thrown while
    recording one would replace a truthful "failed: no space left" with a
    traceback about the reporting. The database row is what an operator reads,
    so it is written by the caller after this returns whatever happened here.
    """
    for write in (lambda: directory.append_log(f"failed: {failure}"),
                  lambda: directory.write_telemetry(
                      [sample.as_dict() for sample in outcome.telemetry])):
        try:
            write()
        except OSError:
            # Nothing to add: the caller is already recording that the run
            # failed, and the reason it could not be written here is the same
            # reason the run failed at all.
            continue


def _without_home(detail: str) -> str:
    """A message with the operator's home directory folded back to `~`.

    Not a general redaction — it closes the one leak that is actually reachable
    here. `settings.lmstudio_cli_path` defaults to `~/.lmstudio/bin/lms`, which
    expands to a path containing the username, and a runtime failure quotes it.
    """
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        return detail
    return detail.replace(home, "~") if home else detail


async def _execute(
    spec: ExperimentSpec,
    runtime: GenerationRuntime,
    resources: ResourceManager,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: ExperimentOutcome,
    clock: Callable[[], float],
    thermal: Callable[[], str | None],
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """§11.2's lifecycle for a single model, between acquire and release."""
    resident_before = {model.model_key for model in await runtime.list_loaded_models()}
    # Compared on the family as well as the key: a build loaded as
    # `google/gemma-4-e4b@4bit` is reported by the catalogue as
    # `google/gemma-4-e4b`, so a qualified request would otherwise look cold
    # while its own weights were already resident — and "no load time was
    # measured" is a validity note that has to be true.
    was_warm = (
        spec.model_key in resident_before
        or spec.model_key.split("@", 1)[0] in resident_before
    )

    started = clock()
    lease = await resources.acquire(
        owner=OWNER, model_key=spec.model_key, configuration=dict(spec.load)
    )
    # **Everything after the acquire is inside the block that releases it.**
    # The five steps below — a memory sample, a log write, the variant gate, an
    # inventory read and a thermal reading — used to sit *above* the `try`, so
    # an exception in any of them skipped the `finally` and left the model
    # loaded with nothing left to reclaim it. A failed benchmark took the
    # machine's capacity with it, and the next run found it full because of a
    # run that had already given up. Found by an external audit, 9 September
    # 2026; `test_a_failure_after_the_load_still_releases_the_model` holds the
    # reproduction.
    #
    # The variant gate is the sharpest case: it *is* a refusal — it raises when
    # the runtime cannot say which build answered — so the one path designed to
    # stop a run was also the one that leaked a model every time it fired.
    try:
        load_seconds = None if was_warm else clock() - started
        if was_warm:
            outcome.warnings.append((
                ValidityScope.TIMING,
                f"{spec.model_key} was already resident, so no load time was measured — "
                "a warm acquire says nothing about how long this model takes to load",
            ))
        outcome.telemetry.append(sampler.sample(AFTER_LOAD))
        directory.append_log(
            f"acquired {spec.model_key} (session {lease.session_id}, "
            f"{'warm' if was_warm else f'loaded in {load_seconds:.2f}s'})"
        )

        # **The variant gate, with the build actually resident.** §12.2 makes
        # format and quantization part of evidence identity, and this is the
        # first moment the runtime can say which build is answering. Raising
        # here rather than before the load costs one load on a machine that
        # cannot confirm — and saves every run on a machine that can.
        outcome.confirmed_variant = _confirmed_variant(
            runtime, None, spec.model_key, required=True
        )

        resident = await runtime.list_loaded_models()
        outcome.effective_configuration = _effective_configuration(spec, resident)
        outcome.warnings.extend(_configuration_warnings(spec, resident))

        outcome.thermal_before = thermal()
        stopped = False
        for test in spec.tests:
            # **Between tests, not mid-inference.** A cancel that killed the
            # task would lose the partial telemetry §11.10 says to keep — how
            # far the run got and what memory looked like when it stopped is
            # most of the value of a run that ended early. So the check is
            # cooperative and the granularity is one test.
            if should_stop is not None and should_stop():
                stopped = True
                outcome.warnings.append((
                    ValidityScope.CONDITIONS,
                    "cancelled after "
                    f"{len([r for r in outcome.repetitions if r.phase != 'load'])} "
                    "measured repetition(s); the results kept are the ones taken "
                    "before the request to stop",
                ))
                break
            await _run_test(spec, test, runtime, sampler, directory, outcome, clock)
        # **The stop has to survive the loop that observed it.** The `break`
        # left the prose tests and nothing else: the trials began regardless,
        # and an ordinary agent benchmark is eight phrasings times three
        # repetitions, so cancelling bought twenty-four further generations with
        # the model held for all of them. The engine had already agreed to stop
        # at a safe boundary; the defect was that it then started new work.
        # Found by an external audit, 9 September 2026.
        # Not carried *into* the trials: `run_tool_trials` is M13's own contract
        # and the documented granularity is one test, which this keeps. A cancel
        # arriving mid-phase still waits out the trials, deliberately and not
        # silently — the fix here is that a cancel already *seen* stops the phase
        # from beginning.
        if spec.tool_trials and not stopped:
            await _run_tool_trials(spec, runtime, directory, outcome)
    finally:
        outcome.thermal_after = thermal()
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


async def _run_tool_trials(
    spec: ExperimentSpec,
    runtime: GenerationRuntime,
    directory: ResultDirectory,
    outcome: ExperimentOutcome,
) -> None:
    """M13's trials, run against a model that is already loaded and warm.

    After the prose tests rather than before, deliberately: they share the
    warmup those tests paid for, and a tool call measured on a cold runtime
    would carry the first-call cost that the warmups exist to absorb.

    Every attempt is written out (§11.9). "Six of eight, and here are the two
    phrasings that failed" is the finding; the rate alone cannot carry it.
    """
    from sirvis.benchmarks.clarvis_roles import run_tool_trials

    # The spec's repetition count reaches the trials, which it did not at first:
    # §13.2's threshold is eight phrasings **times three repetitions**, and a
    # trial runner fixed at one repetition can never satisfy the axis the
    # threshold turns on. `--repetitions 3` therefore means 24 attempts here,
    # the same number it means everywhere else in this engine.
    reliability = await run_tool_trials(
        runtime, spec.model_key, repetitions=spec.repetitions
    )
    outcome.tool_reliability = reliability
    directory.write_response("__tools__", "trial", 0, reliability.as_dict())
    directory.append_log(
        f"tool calls: {reliability.passed}/{reliability.total} well-formed; "
        f"follow-up {reliability.followup}"
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
        reasoning_tokens=_reasoning_tokens(usage),
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


def _reasoning_tokens(usage: Mapping[str, Any] | None) -> int | None:
    """What the runtime says went on thinking, if it says.

    Nested under `completion_tokens_details`, which is where the OpenAI schema
    puts it and where LM Studio follows. `None` rather than `0` when absent:
    "the runtime did not say" and "it says none" are different claims, and only
    the second one licenses trusting the total.
    """
    details = (usage or {}).get("completion_tokens_details")
    if not isinstance(details, Mapping):
        return None
    value = details.get("reasoning_tokens")
    return int(value) if isinstance(value, int) else None


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


def _confirmed_variant(
    runtime: Any, variant: ModelVariant | None, model_key: str, required: bool = True
) -> Any:
    """The build actually loaded, or a refusal to measure one nobody can name.

    **Asked of the runtime, because only the runtime knows.** A runtime that can
    hold two builds under one name implements `confirm_variant`; the rest do not
    have the ambiguity and are left alone. LM Studio has it: it groups variants
    under one entry and `/api/v0/models` reports whichever the app has selected
    rather than the one that is loaded — with an MLX and a GGUF of the same
    weights installed it says `mlx / 4bit` while the loaded build is
    `gguf / Q4_K_M`, and it will complete a request addressed to `…@q4_k_m`
    while returning "not found" for that same key on its metadata route.

    Filing the one as the other is not cosmetic. §12.2 makes format and
    quantization part of evidence identity, and on this machine two builds of a
    single family reach 1/8 and 8/8 on the same tool-call trial. So an
    unconfirmed variant stops the run: a wrong identity is worse than no
    evidence, because RAVIS admits and excludes on it.
    """
    resolve = getattr(runtime, "confirm_variant", None)
    if not callable(resolve):
        return variant
    confirmed = resolve(model_key)
    if confirmed is None and not required:
        # Nothing resident to confirm *yet*. Not an error at this point: the
        # model has not been loaded, so there is nothing for the runtime to
        # name. The caller asks again once it is.
        return variant
    if confirmed is None:
        raise VariantUnconfirmedError(
            f"cannot confirm which build of {model_key!r} is loaded, so the "
            "measurement would be filed against a guess. LM Studio groups "
            "variants under one entry and its HTTP API reports the selected one "
            "rather than the loaded one; `lms ps --json` resolves it. Install "
            "the CLI, or address the build by its qualified key."
        )
    if variant is None:
        # Asked purely as a gate — the caller wants the confirmation to happen
        # and holds the identity elsewhere.
        return confirmed
    if (
        confirmed.runtime_format == (variant.runtime_format or "")
        and confirmed.quantization == (variant.quantization or "")
    ):
        return variant
    # The catalogue disagreed with the runtime. What answered the request is
    # what was measured, so that is what the record says — and the disagreement
    # is worth a line in the log rather than a silent correction.
    logger.warning(
        "the runtime catalogue describes %s as %s/%s; the loaded build is %s/%s (%s)",
        model_key, variant.runtime_format, variant.quantization,
        confirmed.runtime_format, confirmed.quantization, confirmed.model_key,
    )
    return replace(
        variant,
        runtime_format=confirmed.runtime_format,
        quantization=confirmed.quantization,
    )


def _resolve(inventory: Inventory, model_key: str) -> dict[str, Any]:
    """The build behind a runtime's name for it (§6), or a 404 that stays one.

    **A qualified key names a build; the catalogue only lists the family.** LM
    Studio groups several builds under one entry, so `google/gemma-4-e4b@4bit`
    identifies a build the inventory has never heard of — while the runtime
    itself accepts that key for both loading and completions. Stripping the
    suffix here is what lets an operator address a build deliberately instead of
    getting whichever variant the application's dropdown happens to select.
    Everything that talks to the runtime keeps the key as given.
    """
    model = inventory.by_runtime_key(model_key) or inventory.by_runtime_key(
        model_key.split("@", 1)[0]
    )
    if model is None:
        raise ModelNotFoundError(
            f"no installed build carries the runtime key {model_key!r}", runtime_key=model_key
        )
    variant = inventory.variants[model.variant_id]
    family = inventory.families[variant.family_id]
    return {"model": model, "variant": variant, "family": family}


def _effective_configuration(
    spec: ExperimentSpec, resident: Sequence[LoadedModel]
) -> dict[str, Any]:
    """The settings the run actually happened under, where the runtime says.

    Only the keys the experiment asked about: a runtime reports plenty this
    engine never requested, and copying all of it into the evidence identity
    would make the key change whenever the runtime learned a new field.

    Empty when the model is not resident — which is not the same as "matched".
    An identity that silently fell back to the requested value there would
    claim knowledge the run does not have.
    """
    loaded = next((model for model in resident if model.model_key == spec.model_key), None)
    if loaded is None:
        return {}
    return {
        key: loaded.effective[key]
        for key in spec.load
        if loaded.effective.get(key) is not None
    }


# Every warning producer returns `(scope, message)` pairs.
#
# **The scope belongs to the warning, not to the function that emits it**, and
# that correction came from real data. Tagging the *producer* read
# `_generation_warnings` as `OUTPUT`, which is right for "the call succeeded but
# the model produced nothing" and wrong for its neighbour: "tokens that never
# arrived as content" is `hidden_tokens`, which the engine defines as a reasoning
# model thinking before its first content token — "spent inside the
# time-to-first-token window rather than in the generation window". That is rate
# accounting.
#
# `gemma-4-e4b` carried exactly that note beside **24/24 well-formed tool calls**.
# A per-producer scope would have demoted a model that answered every one — the
# mistake scopes exist to prevent, reached by a different route.
ScopedWarning = tuple[ValidityScope, str]


def _configuration_warnings(
    spec: ExperimentSpec, resident: Sequence[LoadedModel]
) -> list[ScopedWarning]:
    """§7.1 and §11.8: an effective configuration that is not the requested one.

    Not an error. A benchmark run at 8K on a request for 32K is a perfectly good
    measurement — of a model at 8K — and the only unacceptable outcome is
    publishing it as though it answered the question that was asked.
    """
    loaded = next((model for model in resident if model.model_key == spec.model_key), None)
    if loaded is None:
        return []
    warnings: list[ScopedWarning] = []
    requested = spec.load.get("context_length")
    effective = loaded.effective.get("context_length")
    if requested and effective and int(requested) != int(effective):
        warnings.append((
            ValidityScope.CONDITIONS,
            f"requested context_length {requested} but the model is loaded at {effective}; "
            "this result describes the configuration that ran, not the one asked for",
        ))
    if loaded.ignored:
        warnings.append(
            (ValidityScope.CONDITIONS, f"the runtime ignored: {', '.join(loaded.ignored)}")
        )
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
    # **Assembled with the scope each producer declares**, so a reader can ask
    # "is the timing trustworthy" without parsing prose (§16 item 7).
    # `outcome.warnings` is whatever ran earlier — the configuration comparison
    # among it — and already carries its own scopes.
    scoped: list[ScopedWarning] = list(outcome.warnings)
    scoped += _generation_warnings(measured)
    scoped += _suppression_warnings(outcome, measured)
    scoped += _thermal_warnings(outcome)
    scoped += _swap_warnings(outcome)
    warnings = [note for _, note in scoped]
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
    # M22b. Lower is better: it is the share of the budget that never reached
    # the answer. `MEASURED` only when every repetition carried the runtime's own
    # reasoning figure -- where the share rests on the chunk-count inference for
    # any of them, §12.1's lattice takes the weaker word for the whole metric.
    # Exact for a repetition the runtime broke down itself, and equally exact
    # for one that reported its tokens and showed every one of them as content:
    # that is a measured zero, not a guess. Inferred only where the share rests
    # on the gap between the count and the chunks -- the same line
    # `generation_tokens_per_second` draws two statements above.
    counted = all(
        r.reasoning_tokens is not None
        or (r.token_source == "reported" and r.hidden_tokens == 0)
        for r in measured
    )
    _add(measurements, "reasoning_token_share", [r.reasoning_share for r in measured],
         unit="fraction", direction="lower", method=METHOD_REASONING,
         kind=EvidenceKind.MEASURED if counted else EvidenceKind.ESTIMATED,
         notes=None if counted
         else "inferred from the gap between reported tokens and content chunks")
    load = [r.total_seconds for r in outcome.repetitions if r.phase == "load"]
    _add(measurements, "load_time_seconds", list(load), unit="seconds",
         direction="lower", method=METHOD_LOAD)

    # §12.2 keys evidence on the configuration a number was **produced under**,
    # which is not always the one that was asked for. Two ways they diverge, and
    # both belong in the identity rather than beside it:
    #
    # A prompt that had to be changed to get an answer is a different question,
    # so an adapted result cannot be mistaken for the declared one.
    #
    # And a runtime that loaded a different configuration than requested is a
    # different measurement. `lms load --context-length 8192` is honoured for
    # ordinary builds and ignored for LM Studio's vision models, which load at
    # their own default — 131072 for `gemma-4-e2b`. Keying on the request would
    # give that run the same evidence ID as one that genuinely ran at 8192,
    # which is exactly the collision §12.2 exists to prevent. The mismatch is
    # still reported as a validity warning; this stops it also being invisible
    # to anyone comparing evidence IDs.
    configuration = {**spec.load, **outcome.effective_configuration}
    if outcome.thinking_suppression:
        configuration["thinking_suppression"] = outcome.thinking_suppression

    # **What the runtime confirmed while resident wins.** The catalogue's answer
    # is what the pre-load pass could see, and on a cold machine that is all it
    # could see; §12.2's identity must name the build that actually answered.
    variant = build["variant"]
    settled = outcome.confirmed_variant
    if settled is not None and (
        settled.runtime_format != (variant.runtime_format or "")
        or settled.quantization != (variant.quantization or "")
    ):
        logger.warning(
            "the runtime catalogue describes %s as %s/%s; the loaded build was %s/%s (%s)",
            spec.model_key, variant.runtime_format, variant.quantization,
            settled.runtime_format, settled.quantization, settled.model_key,
        )
        variant = replace(
            variant,
            runtime_format=settled.runtime_format,
            quantization=settled.quantization,
        )
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
        # §13.2's rates, when the trials ran. `TrialRate` has been on this
        # record since M7 and was constructed nowhere until M13 — the shape was
        # right and the producer was missing.
        rates=_rates(outcome),
        validity=Validity.SUSPECT if warnings else Validity.VALID,
        validity_notes=tuple(warnings),
        validity_scopes=tuple(dict.fromkeys(scope for scope, _ in scoped)),
        # The request itself, unmerged. `configuration` above is
        # `{**spec.load, **effective}` because identity must name what ran; this
        # is the other half, so a reader can compare without parsing a warning.
        requested_configuration=dict(spec.load),
        machine_snapshot_id=str(machine["snapshot_id"]),
    )


def _rates(outcome: ExperimentOutcome) -> dict[str, TrialRate]:
    """Tool-call reliability as trial rates, or nothing at all.

    Nothing rather than a zero when the trials did not run. §12.1's whole
    argument is that absence and a bad result are different claims, and a
    `0/0` here would be neither — it would be a rate nobody measured, sitting
    in the field a router reads to decide whether a build can call tools.
    """
    reliability = outcome.tool_reliability
    if reliability is None or not reliability.total:
        return {}
    from sirvis.benchmarks.clarvis_roles import (
        TOOL_PROMPTS,
        followup_rate,
        tool_rate,
    )

    # The follow-up rate is omitted rather than nulled when the trial fell over:
    # a key that is absent is unmeasured, and a key holding nothing is a shape
    # every reader then has to guard.
    rates: dict[str, Any] = {
        "tool_call_well_formed": tool_rate(reliability, phrasings=len(TOOL_PROMPTS)),
    }
    followup = followup_rate(reliability)
    if followup is not None:
        rates["tool_followup_used_result"] = followup
    return rates


def _generation_warnings(measured: Sequence[Repetition]) -> list[ScopedWarning]:
    """§11.8's validity warnings that this engine can actually observe *here*.

    Only the ones it can see. Background contention, load instability and
    `runtime changed` are on §11.8's list and are not claimed anywhere in this
    engine, because asserting a warning nothing checked for would be worse than
    the gap.

    Swap used to belong on that list and no longer does -- see `_swap_warnings`.
    It was the omission this docstring did not name, which is the more
    misleading kind: a reader was told the unclaimed warnings were background
    contention and load instability, and inferred the rest were covered.
    """
    warnings: list[ScopedWarning] = []
    empty = [r.index for r in measured if not r.content]
    if empty:
        # M2 found a reasoning model spending its whole budget on thinking. The
        # call succeeded and the model said nothing, and those are two facts.
        # OUTPUT: unlike the reasoning-token note in `_suppression_warnings`,
        # this one really is "nothing came back".
        warnings.append((
            ValidityScope.OUTPUT,
            f"{len(empty)} measured repetition(s) returned no content — the call "
            "succeeded but the model produced nothing to measure",
        ))
    unexpected = sorted({r.finish_reason for r in measured
                         if r.finish_reason not in _EXPECTED_STOPS})
    if unexpected:
        warnings.append((
            ValidityScope.OUTPUT,
            f"unexpected generation stop: {', '.join(str(r) for r in unexpected)}",
        ))
    return warnings


# Below this, growth is the operating system moving pages around rather than
# the run reaching for disk. A run that swapped 64 MB did not measure the disk;
# one that swapped a gigabyte measured little else.
SWAP_GROWTH_BYTES = 256 * 1024 * 1024


def _swap_warnings(outcome: ExperimentOutcome) -> list[ScopedWarning]:
    """§11.8's swap warning, which was measured everywhere and reported nowhere.

    `MemoryProbe.sample` reads `vm.swapusage` at baseline, after load, post-run
    and post-unload, and the readings are real and non-null in stored telemetry.
    Nothing in this engine mentioned swap, so a run taken while the machine was
    paging was published `VALID` with no note -- a benchmark that measured the
    disk as much as the model, offered to RAVIS as a clean number.

    Growth from the baseline rather than an absolute level: a machine that was
    already swapping before the run began is a fact about the machine, and the
    thermal warning is the one that speaks to conditions. What invalidates a
    throughput figure is the run *causing* paging.
    """
    samples = [s for s in outcome.telemetry if s.swap_used_bytes is not None]
    if len(samples) < 2:
        return []
    baseline = next((s for s in samples if s.point == BASELINE), samples[0])
    peak = max(samples, key=lambda s: s.swap_used_bytes or 0)
    growth = (peak.swap_used_bytes or 0) - (baseline.swap_used_bytes or 0)
    if growth < SWAP_GROWTH_BYTES:
        return []
    return [(
        ValidityScope.TIMING,
        f"swap grew by {growth / (1024 ** 3):.1f} GB during this run "
        f"(baseline {(baseline.swap_used_bytes or 0) / (1024 ** 3):.1f} GB, "
        f"peak {(peak.swap_used_bytes or 0) / (1024 ** 3):.1f} GB at {peak.point}); "
        "these numbers measured the disk as well as the model",
    )]


def _thermal_warnings(outcome: ExperimentOutcome) -> list[ScopedWarning]:
    """§11.8: flag a thermally compromised run. Never discard it.

    The numbers are real; what they measure is a machine under duress, and on
    fanless hardware that is most of the difference between one result and
    another. The same build on this machine measured 38.4 tokens/second while
    heat-soaked and 56.8 after ten minutes of rest — a 48% swing that no
    within-run spread could show, because every repetition inside a run shares
    the condition.
    """
    before, after = outcome.thermal_before, outcome.thermal_after
    if is_compromised(before) and before == after:
        return [(
            ValidityScope.TIMING,
            f"the machine reported thermal pressure '{before}' throughout; these "
            "numbers describe a throttled machine and are not comparable with "
            "results taken from a rested one",
        )]
    if before != after and (is_compromised(before) or is_compromised(after)):
        return [(
            ValidityScope.TIMING,
            f"thermal pressure changed from '{before or 'unknown'}' to "
            f"'{after or 'unknown'}' during the run, so the later repetitions were "
            "not taken under the same conditions as the earlier ones",
        )]
    return []


def _suppression_warnings(
    outcome: ExperimentOutcome, measured: Sequence[Repetition]
) -> list[ScopedWarning]:
    """What the run had to do to get an answer, and what it could not measure.

    An adapted run is `SUSPECT` rather than `VALID`, and deliberately: the
    numbers are sound, but they are not about the prompt the suite declares, and
    a consumer filtering for clean measurements should not silently receive one
    taken under a different question.
    """
    notes: list[ScopedWarning] = []
    thought = [r.hidden_tokens for r in measured if r.hidden_tokens]
    if thought:
        # **TIMING, not OUTPUT** — and the note says why itself. These tokens
        # exist; they are reasoning, and the fact recorded is *which window they
        # landed in*. `gemma-4-e4b` carried this beside 24/24 well-formed tool
        # calls, so reading it as a statement about what came back would demote a
        # model that answered every one.
        notes.append((
            ValidityScope.TIMING,
            f"{len(thought)} repetition(s) generated tokens that never arrived as "
            f"content — a median of {int(statistics.median(thought))} of them. Those are "
            "reasoning, spent before the first answer token: the throughput here covers "
            "the answer only, and the time-to-first-token includes the thinking",
        ))
    if outcome.thinking_suppression:
        # CONDITIONS: a different prompt from the one the suite declares, so the
        # run answered a different question and nothing on it transfers.
        notes.append((
            ValidityScope.CONDITIONS,
            "the prompt as written produced no content, so this was measured with "
            f"thinking suppressed via {outcome.thinking_suppression} — a different "
            "prompt from the one this suite declares, and recorded in the evidence "
            "identity as such",
        ))
    elif outcome.suppressions_tried:
        # OUTPUT: nothing came back at all, whatever was tried.
        notes.append((
            ValidityScope.OUTPUT,
            "the build answered nothing as written, and none of "
            f"{', '.join(outcome.suppressions_tried)} changed that",
        ))
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
