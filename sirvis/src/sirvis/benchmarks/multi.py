"""M10 — measuring a Runtime Set, not the models in it (§11.2, §11.3).

M6 measures one model well. This measures what §10.1 says a single-model
benchmark cannot:

    Two models fitting separately does not prove they work well together.

So the unit of measurement here is the *combination*, and the output is §11.3's
interaction matrix — the same figures alone, co-resident, and under concurrent
load, with the degradation between them.

**Alone is measured here rather than borrowed.** M6's corpus already holds
single-model numbers for these builds, and reusing them would be cheaper and
wrong: they were taken on another day, at another thermal state, against another
background. A degradation percentage computed across those conditions measures
the week, not the co-residency. So a multi-model run measures each member alone
first, in the same bracketed session as everything else, and pays three times
the generation cost to make the comparison mean something.

**The failure this file exists to get right** is §10's gate, and it is a rule
about what must *not* happen:

    a simultaneous-load failure is a **result**, never silently converted into
    separate-model success.

Which is a live hazard rather than a hypothetical: the alone phase runs first
and succeeds, so at the moment co-residency fails there is a directory full of
perfectly good measurements sitting there, and reporting them as the run's
result would state that the combination works. `_load_together` is where that is
refused, and `test_a_simultaneous_load_failure_is_a_result` is where it is held.

**Co-residency is part of the evidence identity, not a note beside it.** §12.2
keys evidence by everything that changes what it is *about*, and a throughput
figure taken with another model resident is about a different thing than the
same figure taken alone. It rides in `runtime_configuration`, so the two produce
different `evidence_id`s and cannot collide — the mistake this repository has
already made once, when the identity recorded what was asked for rather than
what ran.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Sequence, cast

from sirvis.benchmarks.engine import (
    OWNER,
    RUNTIME_KEY,
    GenerationRuntime,
    Repetition,
    _effective_configuration,
    _evidence,
    _inventory,
    _measure,
    _resolve,
)
from sirvis.benchmarks.spec import BenchmarkTest, ExperimentSpec
from sirvis.core.machine import record_snapshot
from sirvis.core.runtime_sets import RuntimeSet
from sirvis.errors import RuntimeUnreachableError
from sirvis.resources import ResourceExhaustedError, ResourceManager
from sirvis.runtimes.base import RuntimeUnavailableError
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
    POST_RUN,
    POST_UNLOAD,
    MemoryProbe,
    MemorySample,
    SystemSnapshot,
    detect_system,
    is_compromised,
    read_thermal_pressure,
)

# §11.3's three modes, plus the baseline they are compared against. `alone` is
# not one of §11.3's modes because §11.3 is about co-residency — it is the
# control, and it is named here so a measurement can say which condition
# produced it.
MODE_ALONE = "alone"
MODE_SEQUENTIAL = "sequential"
MODE_ALTERNATING = "alternating"
MODE_CONCURRENT = "concurrent"

DEFAULT_MODES = (MODE_SEQUENTIAL, MODE_ALTERNATING, MODE_CONCURRENT)

# The phase name a co-resident measurement is filed under. `measured` is M6's
# and stays M6's: mixing the two would let a co-resident repetition into a
# single-model statistic.
PHASE_FOR = {
    MODE_ALONE: "measured",
    MODE_SEQUENTIAL: "measured-sequential",
    MODE_ALTERNATING: "measured-alternating",
    MODE_CONCURRENT: "measured-concurrent",
}

# Memory sample phases §11.8 asks for beyond M6's. "For Runtime Sets, record
# after every model load" — so each load gets its own labelled sample rather
# than one `after_load` that describes whichever load happened last.
AFTER_LOAD_ROLE = "after_load:{role}"
PEAK_FOR_MODE = "peak:{mode}"


@dataclass(frozen=True)
class MultiModelSpec:
    """One Runtime Set, one suite, and the modes to run it in (§11.1).

    Carries a real `ExperimentSpec` per member rather than a bag of fields,
    because every measurement primitive M6 built takes one — so a role's alone
    phase is *literally* a single-model run, and there is no second
    implementation of warmup, suppression or timing to drift from the first.
    """

    runtime_set: RuntimeSet
    per_role: tuple[ExperimentSpec, ...]
    modes: tuple[str, ...] = DEFAULT_MODES

    @staticmethod
    def from_set(
        runtime_set: RuntimeSet,
        *,
        suite_id: str,
        suite_version: str,
        tests: Sequence[BenchmarkTest],
        warmups: int = 1,
        repetitions: int = 3,
        modes: tuple[str, ...] = DEFAULT_MODES,
    ) -> MultiModelSpec:
        """Expand a set into one experiment specification per member.

        In **load order**, not declaration order — §11.2 ends with "load order
        is always recorded", and a run that measured in one order while
        reporting another would make that record a lie.
        """
        return MultiModelSpec(
            runtime_set=runtime_set,
            per_role=tuple(
                ExperimentSpec(
                    suite_id=suite_id,
                    suite_version=suite_version,
                    model_key=member.model_id,
                    tests=tuple(tests),
                    load=_load_for(member),
                    warmups=warmups,
                    repetitions=repetitions,
                    role=member.role,
                )
                for member in runtime_set.members_in_load_order()
            ),
            modes=modes,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_set": self.runtime_set.as_dict(),
            "modes": list(self.modes),
            "members": [spec.as_dict() for spec in self.per_role],
            # Recorded at the top level as well as inside the set, because §11.2
            # asks for the order the run *used* and a reader should not have to
            # reconstruct it from a nested definition.
            "load_order": [spec.role for spec in self.per_role],
        }


def _load_for(member: Any) -> dict[str, Any]:
    """A member's load configuration, with its context length folded in."""
    load = dict(member.configuration)
    if member.context_length is not None:
        load.setdefault("context_length", member.context_length)
    return load


@dataclass
class MultiModelOutcome:
    """What a multi-model run produced, including when it could not proceed."""

    experiment_id: str
    run_id: str
    state: RunState
    detail: str
    results_path: str
    load_order: list[str] = field(default_factory=list)
    # role → mode → the repetitions measured in that condition.
    measurements: dict[str, dict[str, list[Repetition]]] = field(default_factory=dict)
    matrix: dict[str, Any] = field(default_factory=dict)
    telemetry: list[MemorySample] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # The rows `finish_run` wrote. Printed by the CLI summary rather than
    # merely held: an outcome that says where its files went but not which
    # database rows it produced leaves the lookup to a timestamp guess, which is
    # the reconstruction a stored id exists to avoid.
    result_ids: list[str] = field(default_factory=list)
    # Set when the members could not be made co-resident. Its presence is what
    # stops the alone measurements being read as a working combination.
    co_residency_failure: str | None = None
    load_seconds: dict[str, float] = field(default_factory=dict)
    # role → the configuration that role's model is **actually** resident under,
    # read from the runtime once every member is loaded.
    #
    # §12.2 keys evidence on the configuration a number was produced under, not
    # the one that was asked for. The view below stamped
    # `spec.per_role[0].load` -- the *first* member's *requested* config -- onto
    # every member's record, so in a set whose members differ in context length
    # (§10's own example is chat@16384 with agent@32768) the second member's
    # evidence was filed under the first member's window. A RAVIS query for the
    # agent build at 32768 then matched nothing, and two runs that differed only
    # in the second member's context collided on one identity.
    effective_by_role: dict[str, dict[str, Any]] = field(default_factory=dict)
    # §11.8's thermal reading, per condition. A degradation percentage is a
    # comparison between two conditions, so a reading taken once for the whole
    # run cannot say which of the two was measured warm.
    thermal: dict[str, str | None] = field(default_factory=dict)

    def repetitions_for(self, role: str, mode: str) -> list[Repetition]:
        return self.measurements.get(role, {}).get(mode, [])

    def record(self, role: str, mode: str, measured: list[Repetition]) -> None:
        self.measurements.setdefault(role, {})[mode] = measured


async def run_multi_experiment(
    spec: MultiModelSpec,
    *,
    runtime: GenerationRuntime,
    resources: ResourceManager,
    database: Database,
    results_root: str,
    probe: MemoryProbe | None = None,
    snapshot: SystemSnapshot | None = None,
    clock: Callable[[], float] = time.perf_counter,
    thermal: Callable[[], str | None] = read_thermal_pressure,
) -> MultiModelOutcome:
    """§11.2's multi-model lifecycle, end to end.

    Returns the outcome rather than raising, for M6's reason and one more: the
    interesting failure here — the set will not go co-resident — is a *finding*
    about the combination, and raising would discard both the finding and the
    alone measurements that give it context.
    """
    sampler = probe or MemoryProbe()
    inventory = await _inventory(runtime)
    machine = record_snapshot(database, snapshot or detect_system())

    experiment_id = create_experiment(
        database, spec.as_dict(),
        suite_id=spec.per_role[0].suite_id, suite_version=spec.per_role[0].suite_version,
        environment_mode=spec.per_role[0].environment_mode,
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
    outcome = MultiModelOutcome(
        experiment_id=experiment_id, run_id=run_id, state=RunState.RUNNING,
        detail="preparing", results_path=str(directory.path),
        load_order=[member.role for member in spec.per_role],
        telemetry=[sampler.sample(BASELINE)],
    )

    try:
        await _execute(spec, runtime, resources, sampler, directory, outcome, clock, thermal)
    except (RuntimeUnavailableError, RuntimeUnreachableError) as failure:
        directory.append_log(f"failed: {failure}")
        directory.write_telemetry([sample.as_dict() for sample in outcome.telemetry])
        finish_run(database, run_id, state=RunState.FAILED, detail=str(failure))
        outcome.state, outcome.detail = RunState.FAILED, str(failure)
        return outcome

    outcome.warnings.extend(_thermal_warnings(outcome))
    outcome.matrix = interaction_matrix(spec, outcome)
    directory.write_result({"interaction_matrix": outcome.matrix})
    directory.write_telemetry([sample.as_dict() for sample in outcome.telemetry])
    _finish(spec, outcome, inventory, machine, database, directory)
    return outcome


def _thermal_warnings(outcome: MultiModelOutcome) -> list[str]:
    """§11.8: flag a thermally compromised run; never silently discard it.

    **A degradation percentage compares two conditions, and this run measures
    them at different times.** The control is taken first, on a machine that has
    just been idle; concurrent generation is taken last, after every other mode
    has been run. The first reading of a real pair showed exactly that — the
    machine sat at `nominal` through the alone and sequential phases and read
    `fair` for alternating and concurrent — so the contention figure carries
    whatever thermal drift accumulated in between, and no arithmetic here can
    separate the two.

    That is a bias rather than noise: it has a direction. Every degradation
    number is against a control measured under better conditions than the thing
    it is compared with, so contention is overstated by whatever the machine
    lost along the way. Flagged rather than corrected — the correction would be
    a guess, and §11.8 asks for the flag.
    """
    readings = {
        point: state for point, state in outcome.thermal.items() if state
    }
    distinct = set(readings.values())
    # **Compromised throughout is not the same as "no drift".**
    #
    # This returned nothing whenever fewer than two *distinct* states were seen,
    # so it detected thermal drift and only drift. A run whose every condition
    # read `serious` -- the whole thing taken on a heat-soaked machine -- has one
    # distinct reading, produced no warning, and was published `VALID` with
    # empty `validity_notes`. §11.8 says "Flag thermally compromised runs. Do
    # not silently discard them", and this file's own docstring says the same;
    # the single-model engine has had the branch since M6.
    #
    # It matters most here: `contention_penalty` reads these degradation figures
    # as facts about co-residency, and on the fanless hardware STATUS.md records
    # losing 48% of throughput to heat, a set benchmarked while hot would file an
    # interaction matrix marked complete with nothing saying the machine was
    # throttled.
    if readings and all(is_compromised(state) for state in readings.values()):
        described = ", ".join(f"{point} {state}" for point, state in sorted(readings.items()))
        return [
            f"the machine reported thermal pressure throughout this run ({described}); "
            "these numbers describe a throttled machine and are not comparable with "
            "results taken from a rested one"
        ]
    if len(distinct) < 2:
        return []
    described = ", ".join(f"{point} {state}" for point, state in sorted(readings.items()))
    return [
        "the machine's thermal state changed during this run "
        f"({described}), so the conditions being compared were not measured "
        "under equal conditions and the degradation figures include that drift"
    ]


async def _execute(
    spec: MultiModelSpec,
    runtime: GenerationRuntime,
    resources: ResourceManager,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: MultiModelOutcome,
    clock: Callable[[], float],
    thermal: Callable[[], str | None],
) -> None:
    """§11.2's order, which is the experiment rather than an implementation.

    Alone first, because it is the control and a control taken *after* the
    machine has held two models is a control taken on a different machine.
    """
    # §11.8 asks for thermal capture, and this said it happened "per mode below"
    # while discarding the reader — so a multi-model run recorded none at all,
    # where M6 records two per single-model run. It cost a real question: the
    # agent's alone throughput came back 68, 58, 58 across three runs of one
    # pair while its concurrent figure held at 44.6, 45.7, 45.3, and nothing in
    # the record could say whether the machine was warmer for the last two.
    outcome.thermal["baseline"] = thermal()
    for member in spec.per_role:
        await _measure_alone(member, spec, runtime, resources, sampler, directory, outcome, clock)
    outcome.thermal[MODE_ALONE] = thermal()

    lease = await _load_together(spec, resources, sampler, directory, outcome)
    if lease is None:
        return
    # Per member, and only now: this is the first moment every member is
    # resident together, which is the state the co-resident numbers are measured
    # in. `_effective_configuration` returns {} for a model the runtime does not
    # report as loaded, which keeps "not known" distinct from "matched".
    resident = await runtime.list_loaded_models()
    outcome.effective_by_role = {
        member.role: _effective_configuration(member, resident) for member in spec.per_role
    }

    try:
        for mode in spec.modes:
            await _run_mode(mode, spec, runtime, sampler, directory, outcome, clock)
            outcome.thermal[mode] = thermal()
    finally:
        outcome.telemetry.append(sampler.sample(POST_RUN))
        await resources.release(lease.session_id)
        outcome.telemetry.append(sampler.sample(POST_UNLOAD))


async def _measure_alone(
    member: ExperimentSpec,
    spec: MultiModelSpec,
    runtime: GenerationRuntime,
    resources: ResourceManager,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: MultiModelOutcome,
    clock: Callable[[], float],
) -> None:
    """One member, resident by itself — the control column of the matrix.

    Released before the next member is measured, which is what makes it a
    control at all: a second member measured while the first was still resident
    would already be a co-residency measurement wearing the alone label.
    """
    started = clock()
    lease = await resources.acquire(
        owner=OWNER, model_key=member.model_key, configuration=dict(member.load)
    )
    outcome.load_seconds[member.role] = clock() - started
    outcome.telemetry.append(sampler.sample(AFTER_LOAD_ROLE.format(role=member.role)))
    directory.append_log(f"alone: {member.role} ({member.model_key})")
    try:
        await _measure_role(member, spec, MODE_ALONE, runtime, sampler, directory, outcome, clock)
    finally:
        # §11.8 asks for swap baseline, peak and final. The alone condition is
        # the *baseline* every co-resident figure is compared against, and it
        # had no peak sample of its own — only the modes did — so swap could be
        # read while two models were resident and not while one was. The one
        # comparison §10.1 turns on was the one that could not be made.
        outcome.telemetry.append(sampler.sample(PEAK_FOR_MODE.format(mode=MODE_ALONE)))
        await resources.release(lease.session_id)
        outcome.telemetry.append(sampler.sample(f"post_unload:{member.role}"))


async def _load_together(
    spec: MultiModelSpec,
    resources: ResourceManager,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: MultiModelOutcome,
) -> Any:
    """Make every member resident at once, or record why they cannot be.

    **§10's gate lives here.** A failure at this point is the most interesting
    thing a multi-model run can discover, and it arrives at the worst possible
    moment for honesty: the alone phase has just succeeded, so there is a
    directory of good measurements that would read as a working combination if
    this were reported as anything other than what it is.

    So the failure is recorded as a *result* — a finding about the set, with the
    alone measurements kept and explicitly labelled alone-only — and the run is
    never marked succeeded. Nothing here falls back to "well, each one loaded".
    """
    lease = None
    try:
        for member in spec.per_role:
            lease = await resources.acquire(
                owner=OWNER,
                model_key=member.model_key,
                configuration=dict(member.load),
                session_id=lease.session_id if lease else None,
            )
            # §11.8: for Runtime Sets, record after every model load.
            outcome.telemetry.append(sampler.sample(AFTER_LOAD_ROLE.format(role=member.role)))
            directory.append_log(f"co-resident: added {member.role} ({member.model_key})")
    except (ResourceExhaustedError, RuntimeUnavailableError, RuntimeUnreachableError) as failure:
        if lease is not None:
            await resources.release(lease.session_id)
        outcome.co_residency_failure = str(failure)
        outcome.warnings.append(
            f"the members of {spec.runtime_set.name} could not be made co-resident: {failure}. "
            "The alone measurements below describe each model by itself and say nothing "
            "about the combination"
        )
        directory.append_log(f"co-residency failed: {failure}")
        return None
    outcome.telemetry.append(sampler.sample(AFTER_LOAD))
    return lease


async def _run_mode(
    mode: str,
    spec: MultiModelSpec,
    runtime: GenerationRuntime,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: MultiModelOutcome,
    clock: Callable[[], float],
) -> None:
    """One of §11.3's modes, with every member resident throughout."""
    directory.append_log(f"mode: {mode}")
    if mode == MODE_CONCURRENT:
        await _run_concurrent(spec, runtime, sampler, directory, outcome, clock)
    elif mode == MODE_ALTERNATING:
        await _run_alternating(spec, runtime, sampler, directory, outcome, clock)
    else:
        for member in spec.per_role:
            await _measure_role(
                member, spec, MODE_SEQUENTIAL, runtime, sampler, directory, outcome, clock
            )
    outcome.telemetry.append(sampler.sample(PEAK_FOR_MODE.format(mode=mode)))


async def _run_alternating(
    spec: MultiModelSpec,
    runtime: GenerationRuntime,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: MultiModelOutcome,
    clock: Callable[[], float],
) -> None:
    """chat → agent → chat → agent (§11.3).

    Round-robin by repetition rather than by member, which is the whole
    difference from sequential: what it measures is the cost of *switching*
    between two resident models, and running each one's repetitions in a block
    would let the runtime settle and measure nothing.
    """
    for index in range(max(member.repetitions for member in spec.per_role)):
        for member in spec.per_role:
            if index >= member.repetitions:
                continue
            for test in member.tests:
                result = await _measure(
                    test, member, runtime, sampler,
                    PHASE_FOR[MODE_ALTERNATING], index, _shim(outcome), clock,
                )
                directory.write_response(
                    test.id, f"{member.role}-{PHASE_FOR[MODE_ALTERNATING]}", index,
                    result.as_dict(),
                )
                outcome.measurements.setdefault(member.role, {}).setdefault(
                    MODE_ALTERNATING, []
                ).append(result)


async def _run_concurrent(
    spec: MultiModelSpec,
    runtime: GenerationRuntime,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: MultiModelOutcome,
    clock: Callable[[], float],
) -> None:
    """Every member generating at once (§11.3).

    `asyncio.gather` rather than a task group, because one member failing must
    not cancel the others: a set where the agent model falls over under
    concurrent load while the chat model keeps answering is precisely the
    finding this mode exists to produce, and cancelling the survivor would
    destroy the evidence for it.
    """
    async def for_member(member: ExperimentSpec) -> tuple[str, list[Repetition]]:
        measured: list[Repetition] = []
        for index in range(member.repetitions):
            for test in member.tests:
                measured.append(await _measure(
                    test, member, runtime, sampler,
                    PHASE_FOR[MODE_CONCURRENT], index, _shim(outcome), clock,
                ))
        return member.role, measured

    gathered = await asyncio.gather(
        *(for_member(member) for member in spec.per_role), return_exceptions=True
    )
    for member, result in zip(spec.per_role, gathered, strict=True):
        if isinstance(result, BaseException):
            outcome.warnings.append(
                f"{member.role} failed under concurrent load: {result}"
            )
            continue
        role, measured = result
        outcome.record(role, MODE_CONCURRENT, measured)
        for index, repetition in enumerate(measured):
            directory.write_response(
                repetition.test_id, f"{role}-{PHASE_FOR[MODE_CONCURRENT]}", index,
                repetition.as_dict(),
            )


async def _measure_role(
    member: ExperimentSpec,
    spec: MultiModelSpec,
    mode: str,
    runtime: GenerationRuntime,
    sampler: MemoryProbe,
    directory: ResultDirectory,
    outcome: MultiModelOutcome,
    clock: Callable[[], float],
) -> None:
    """Warm up and measure one member, in one condition."""
    del spec  # The member carries everything the measurement needs.
    measured: list[Repetition] = []
    for index in range(member.warmups):
        for test in member.tests:
            warm = await _measure(
                test, member, runtime, sampler, f"warmup-{mode}", index, _shim(outcome), clock
            )
            directory.write_response(test.id, f"{member.role}-warmup-{mode}", index,
                                     warm.as_dict())
    for index in range(member.repetitions):
        for test in member.tests:
            result = await _measure(
                test, member, runtime, sampler, PHASE_FOR[mode], index, _shim(outcome), clock
            )
            directory.write_response(test.id, f"{member.role}-{PHASE_FOR[mode]}", index,
                                     result.as_dict())
            measured.append(result)
    outcome.record(member.role, mode, measured)


class _Shim:
    """The two fields `_measure` writes back onto an outcome.

    M6's `_measure` records suppression attempts on the outcome it is given.
    A multi-model run has one outcome for many roles, so it is handed a shim per
    call and anything recorded is folded into the run's warnings — rather than
    changing `_measure`'s signature, which would touch the measurement path
    every single-model result already depends on.
    """

    def __init__(self, outcome: MultiModelOutcome) -> None:
        self._outcome = outcome
        # Shared lists, not copies: what `_measure` appends must land on the
        # run's own record, or a warning raised mid-measurement would vanish
        # with the shim.
        self.warnings: list[str] = outcome.warnings
        self.telemetry: list[MemorySample] = outcome.telemetry
        self.suppressions_tried: list[str] = []
        self.thinking_suppression: str | None = None


def _shim(outcome: MultiModelOutcome) -> Any:
    return _Shim(outcome)


def interaction_matrix(spec: MultiModelSpec, outcome: MultiModelOutcome) -> dict[str, Any]:
    """§11.3's matrix: the same figures per condition, and the degradation.

    **A missing condition is missing, never zero and never inferred.** When
    co-residency failed there is no co-resident column, and the matrix says so
    in `complete: false` rather than presenting the alone figures in a shape
    that reads like a comparison.
    """
    conditions = [MODE_ALONE, *spec.modes] if not outcome.co_residency_failure else [MODE_ALONE]
    rows: dict[str, Any] = {}
    for member in spec.per_role:
        rows[member.role] = _row(member.role, conditions, outcome)
    return {
        "runtime_set": spec.runtime_set.name,
        "revision": spec.runtime_set.revision,
        # Which build played each role. The matrix keyed its rows by role and
        # named no builds, so a consumer holding one could not tell *which*
        # pair it described — and §10.1 is exactly that co-residency behaviour
        # does not transfer between combinations, so a matrix that cannot
        # identify its own pair is a measurement of nothing in particular.
        "members": {member.role: member.model_key for member in spec.per_role},
        "load_order": outcome.load_order,
        "conditions": conditions,
        "rows": rows,
        # §11.3's matrix carries Peak RAM and Swap beneath the per-model rows,
        # and they belong to the *machine* rather than to any one role: under
        # concurrent load both models are pressing on the same memory, so a
        # per-role figure would double-count the thing that is actually shared.
        "memory": _memory(conditions, rows, outcome),
        "thermal": dict(outcome.thermal),
        "complete": outcome.co_residency_failure is None,
        "co_residency_failure": outcome.co_residency_failure,
        # Named so nobody has to infer it: every number here was measured in
        # this run, under one thermal bracket, rather than joined across runs.
        "basis": "measured in this run",
    }


def _memory(
    conditions: list[str], rows: dict[str, Any], outcome: MultiModelOutcome
) -> dict[str, Any]:
    """§11.3's Peak RAM and Swap rows, per condition.

    Peak is read from the repetitions, where a watcher sampled *during*
    generation — the point sample taken after a mode finished would miss the
    moment that matters, because by then the runtime has released whatever it
    briefly needed.

    Swap comes from the telemetry point for the mode. It is the figure §10.1
    predicts will move and, on the first live run of this engine, the figure
    that did not — which is exactly why it is reported rather than assumed.
    """
    by_point = {sample.point: sample for sample in outcome.telemetry}
    peaks: dict[str, Any] = {}
    for condition in conditions:
        across_roles = [
            row["figures"][condition].get("lowest_available_bytes")
            for row in rows.values()
            if condition in row["figures"]
        ]
        sample = by_point.get(PEAK_FOR_MODE.format(mode=condition))
        peaks[condition] = {
            "lowest_available_bytes": _lowest(across_roles),
            "swap_used_bytes": sample.swap_used_bytes if sample else None,
        }
    return peaks


def _row(role: str, conditions: list[str], outcome: MultiModelOutcome) -> dict[str, Any]:
    """One model's figures across every condition, plus its degradation."""
    figures = {
        condition: _summarise(outcome.repetitions_for(role, condition))
        for condition in conditions
    }
    return {
        "figures": figures,
        "degradation_percent": {
            condition: _degradation(figures.get(MODE_ALONE), figures.get(condition))
            for condition in conditions
            if condition != MODE_ALONE
        },
    }


def _summarise(measured: Sequence[Repetition]) -> dict[str, float | None]:
    """Median throughput and time-to-first-token, or None when nothing ran.

    Median rather than mean, matching §11.7's treatment of the single-model
    statistics — a concurrent run's slowest repetition is exactly the outlier a
    mean would let dominate.
    """
    if not measured:
        return {
            "tokens_per_second": None, "time_to_first_token": None,
            "lowest_available_bytes": None, "samples": 0,
        }
    return {
        "tokens_per_second": _median(
            [r.generation_tokens_per_second for r in measured]
        ),
        "time_to_first_token": _median([r.ttft_seconds for r in measured]),
        # The **minimum** rather than the median: §11.3's matrix asks for peak
        # memory, and the peak is the moment the machine was closest to running
        # out. A median would describe a comfortable average of a run that
        # briefly was not comfortable at all.
        "lowest_available_bytes": _lowest(
            [r.lowest_available_bytes for r in measured]
        ),
        "samples": len(measured),
    }


def _lowest(values: Sequence[int | None]) -> int | None:
    """The smallest reported reading, or None when nothing was readable."""
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _median(values: Sequence[float | None]) -> float | None:
    """The median of whatever was actually reported, or None."""
    present = sorted(value for value in values if value is not None)
    if not present:
        return None
    middle = len(present) // 2
    if len(present) % 2:
        return present[middle]
    return (present[middle - 1] + present[middle]) / 2


def _degradation(
    alone: Mapping[str, Any] | None, under: Mapping[str, Any] | None
) -> dict[str, float | None]:
    """How much worse a condition is than alone, as a percentage.

    Positive means *worse* in both rows, which needs saying because the two
    metrics move in opposite directions: throughput falling and time-to-first-
    token rising are both degradation, so the sign is normalised rather than
    left for a reader to work out per row.
    """
    return {
        "tokens_per_second": _drop(
            (alone or {}).get("tokens_per_second"), (under or {}).get("tokens_per_second")
        ),
        "time_to_first_token": _rise(
            (alone or {}).get("time_to_first_token"),
            (under or {}).get("time_to_first_token"),
        ),
    }


def _drop(baseline: float | None, measured: float | None) -> float | None:
    """Percent lost against the baseline, or None when either is unknown."""
    if not baseline or measured is None:
        return None
    return round((baseline - measured) / baseline * 100, 2)


def _rise(baseline: float | None, measured: float | None) -> float | None:
    """Percent added to the baseline, or None when either is unknown."""
    if not baseline or measured is None:
        return None
    return round((measured - baseline) / baseline * 100, 2)


def _finish(
    spec: MultiModelSpec,
    outcome: MultiModelOutcome,
    inventory: Any,
    machine: Mapping[str, Any],
    database: Database,
    directory: ResultDirectory,
) -> None:
    """Persist one result per role, and the run state the evidence supports.

    A co-residency failure finishes the run as FAILED **and still writes every
    result**, which is §10's gate stated as code: the finding is recorded, the
    alone measurements are kept, and nothing anywhere says the combination
    worked.
    """
    results = [
        _result_for(member, spec, outcome, inventory, machine) for member in spec.per_role
    ]
    if outcome.co_residency_failure:
        outcome.result_ids = finish_run(
            database, outcome.run_id, state=RunState.FAILED,
            detail=f"co-residency failed: {outcome.co_residency_failure}", results=results,
        )
        outcome.state = RunState.FAILED
        outcome.detail = f"co-residency failed: {outcome.co_residency_failure}"
        directory.append_log("recorded as a result: the set did not go co-resident")
        return
    outcome.result_ids = finish_run(
        database, outcome.run_id, state=RunState.SUCCEEDED, detail="completed",
        results=results,
    )
    outcome.state, outcome.detail = RunState.SUCCEEDED, "completed"


def _result_for(
    member: ExperimentSpec,
    spec: MultiModelSpec,
    outcome: MultiModelOutcome,
    inventory: Any,
    machine: Mapping[str, Any],
) -> StoredResult:
    """One role's evidence, keyed so it cannot collide with a solo measurement.

    The co-residency condition rides in `runtime_configuration`, which §12.2
    makes part of the identity — so the same build measured alone and measured
    beside another model produce different `evidence_id`s. Without that they
    would be the same key with different numbers, which is how a corpus starts
    disagreeing with itself.
    """
    condition = MODE_SEQUENTIAL if not outcome.co_residency_failure else MODE_ALONE
    shim = _OutcomeView(member, outcome, condition)
    record = _evidence(
        _spec_with_condition(member, spec, condition), _resolve(inventory, member.model_key),
        cast(Any, shim), machine,
    )
    payload = record.as_dict() | {
        "interaction_matrix": outcome.matrix,
        "co_residency": {
            "runtime_set": spec.runtime_set.name,
            "revision": spec.runtime_set.revision,
            "load_order": outcome.load_order,
            "condition": condition,
            "failure": outcome.co_residency_failure,
        },
    }
    return StoredResult(
        # One result per ExperimentTarget (§17), and for a Runtime Set the
        # target is the role rather than the model: two roles could name one
        # build, and two results about "that build" would violate the run's
        # unique (run_id, target_key) constraint for a reason nobody could see.
        target_key=member.role,
        evidence_id=record.identity.evidence_id,
        validity=record.validity.value,
        payload=payload,
    )


def _spec_with_condition(
    member: ExperimentSpec, spec: MultiModelSpec, condition: str
) -> ExperimentSpec:
    """The member's spec, carrying what it was measured *beside*."""
    others = [
        f"{other.role}:{other.model_key}"
        for other in spec.per_role
        if other.role != member.role
    ]
    return ExperimentSpec(
        suite_id=member.suite_id,
        suite_version=member.suite_version,
        model_key=member.model_key,
        tests=member.tests,
        load=dict(member.load) | {
            "co_resident_with": sorted(others),
            "co_residency_condition": condition,
            "runtime_set": f"{spec.runtime_set.name}@{spec.runtime_set.revision}",
        },
        warmups=member.warmups,
        repetitions=member.repetitions,
        environment_mode=member.environment_mode,
        role=member.role,
        suppress_thinking=member.suppress_thinking,
    )


class _OutcomeView:
    """One role's repetitions, shaped like the outcome `_evidence` reads.

    A view rather than a copy of M6's dataclass: `_evidence` needs `repetitions`
    in the `measured` phase, plus warnings and thermal readings. Building a real
    `ExperimentOutcome` here would mean keeping two constructors in step, and a
    view cannot fall behind one.
    """

    def __init__(
        self, member: ExperimentSpec, outcome: MultiModelOutcome, condition: str,
    ) -> None:
        measured = outcome.repetitions_for(member.role, condition)
        # Re-phased to `measured` because that is the only phase `_evidence`
        # counts, and the condition is already carried by the identity above.
        self.repetitions = [
            replace(r, phase="measured")
            for r in measured
        ]
        self.warnings = list(outcome.warnings)
        self.telemetry = outcome.telemetry
        self.thinking_suppression: str | None = None
        self.suppressions_tried: list[str] = []
        # This role's own, not the first member's. See `effective_by_role`.
        self.effective_configuration = dict(outcome.effective_by_role.get(member.role, {}))
        self.thermal_before: str | None = None
        self.thermal_after: str | None = None
        # M13's trials do not run under a multi-model experiment: the tool-call
        # question is about one build, and asking it while a second model is
        # resident measures the pair. None rather than an empty result, so the
        # record carries no rate at all rather than a rate of zero.
        self.tool_reliability: Any = None
