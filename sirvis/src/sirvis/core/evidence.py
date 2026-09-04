"""The evidence schema (§12), and the rules that make it worth trusting.

This is the milestone Stage 4 exits on, and §21.2 says its acceptance is
*verbatim* that exit criterion. Everything after it — recommendations, the RAVIS
query API, the whole point of SIRVIS — is downstream of getting these shapes
right, because RAVIS routes on what comes out of here and a number that
overstates what was observed becomes a wrong route on somebody else's machine.

Four rules are enforced by construction rather than by convention:

**No scalar-only canonical score.** A `Measurement` cannot be built from a
headline; it is built from its repetitions and derives the headline. There is no
constructor that accepts 38.4 and forgets where it came from, which means no
code path can produce a number whose samples were discarded.

**Every repetition is preserved.** §11.7 exists because Clarvis's own work saw
identical prompt variants differ by ~32%, and concluded a single take can
measure noise rather than a difference. The samples travel with the summary.

**Provenance is never upgraded.** Aggregation can only weaken it: a composite of
measured and estimated inputs is `PARTIALLY_MEASURED`, never `MEASURED`, and no
sequence of combinations climbs back up.

**A missing value never becomes zero.** `UNKNOWN` stays unknown and carries its
reason. Zero is a measurement; absence is not.
"""

from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

# Below this many samples a percentile describes the sample rather than the
# thing sampled — with five takes, P10 is within rounding of the minimum. §11.7
# asks for percentiles "where sample size permits", and this is where it does
# not: the honest answer is absence, not a number computed anyway.
PERCENTILE_MINIMUM_SAMPLES = 5

# Spread needs at least two points to exist at all.
SPREAD_MINIMUM_SAMPLES = 2


class EvidenceKind(str, Enum):
    """§12.1's four levels, ordered from strongest to weakest.

    The ordering is load-bearing: `combine` returns the weakest of its inputs,
    which is the whole of "aggregation cannot promote evidence" expressed as a
    lattice rather than as a warning in a docstring.
    """

    MEASURED = "MEASURED"
    PARTIALLY_MEASURED = "PARTIALLY_MEASURED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"

    def for_consumers_without_partial(self) -> EvidenceKind:
        """How a consumer that does not understand `PARTIALLY_MEASURED` must read it.

        §12.1 is explicit: as `ESTIMATED`, **never** as `MEASURED`. Provided as
        a method so a client library can degrade correctly instead of each one
        inventing its own reading of a value it does not recognise.
        """
        return EvidenceKind.ESTIMATED if self is EvidenceKind.PARTIALLY_MEASURED else self


def combine(kinds: Sequence[EvidenceKind]) -> EvidenceKind:
    """The provenance of a composite. It can only weaken (§12.1).

    - everything measured → `MEASURED`
    - some measured, some not → `PARTIALLY_MEASURED`
    - nothing measured but something estimated → `ESTIMATED`
    - nothing at all → `UNKNOWN`

    An empty input is `UNKNOWN` rather than `MEASURED`. Aggregating no evidence
    produces no evidence, and the vacuous-truth reading — "all zero inputs were
    measured" — is exactly the bug this function exists to make impossible.
    """
    if not kinds:
        return EvidenceKind.UNKNOWN
    present = set(kinds)
    if present == {EvidenceKind.MEASURED}:
        return EvidenceKind.MEASURED
    if EvidenceKind.MEASURED in present:
        return EvidenceKind.PARTIALLY_MEASURED
    if EvidenceKind.ESTIMATED in present:
        return EvidenceKind.ESTIMATED
    return EvidenceKind.UNKNOWN


@dataclass(frozen=True)
class Provenance:
    """Where one value came from (§12.1's envelope).

    `method` names the procedure, versioned, so a number produced by
    `tokens_per_second.v1` is never silently compared with one from `v2`. A
    change in how something is measured makes new evidence rather than an
    update to old evidence (§12.4).
    """

    kind: EvidenceKind
    method: str = ""
    observed_at: str | None = None
    source_run_id: str | None = None
    confidence: float | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        """Refuse the two shapes that would make provenance decorative.

        A `MEASURED` claim with no method cannot be reproduced or compared, and
        an `UNKNOWN` with no reason tells a reader nothing they did not already
        know from the absence of a value.
        """
        if self.kind is EvidenceKind.MEASURED and not self.method:
            raise ValueError("a MEASURED value must name the method that produced it")
        if self.kind is EvidenceKind.UNKNOWN and not (self.notes or self.method):
            raise ValueError("an UNKNOWN value must state why it is unknown")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "method": self.method,
            "observed_at": self.observed_at,
            "source_run_id": self.source_run_id,
            "confidence": self.confidence,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Measurement:
    """Repeated observations of one quantity, and the summary they support.

    **Constructed from repetitions only.** There is deliberately no way to build
    one from a headline: §21's exit forbids a scalar-only canonical score, and
    the way to forbid it is to make the samples the input rather than an
    optional extra somebody can omit under deadline.

    Every statistic is derived here and none is stored, so the summary cannot
    drift from the data it summarises.
    """

    repetitions: tuple[float, ...]
    unit: str
    # `higher` or `lower` — which direction is better. Without it a consumer
    # cannot rank two numbers, and guessing from the unit is how tokens-per-
    # second and time-to-first-token end up sorted the same way.
    direction: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if not self.repetitions:
            raise ValueError("a measurement needs at least one repetition")
        if self.direction not in ("higher", "lower"):
            raise ValueError("direction must be 'higher' or 'lower'")

    @property
    def samples(self) -> int:
        return len(self.repetitions)

    @property
    def median(self) -> float:
        """The headline (§11.7). Median rather than mean because one slow take —
        a thermal blip, a background build — moves a mean and not a median."""
        return statistics.median(self.repetitions)

    @property
    def spread(self) -> float | None:
        """Sample standard deviation, or None below two samples.

        None rather than 0.0. A single observation has no spread; reporting zero
        would claim perfect consistency measured once, which is the most
        misleading number this schema could produce.
        """
        if self.samples < SPREAD_MINIMUM_SAMPLES:
            return None
        return statistics.stdev(self.repetitions)

    def percentile(self, fraction: float) -> float | None:
        """P10 or P90, or None when the sample is too small to support one.

        §11.7 asks for these "where sample size permits". With five takes a P10
        is within rounding of the minimum, so below the threshold this returns
        absence rather than a number that describes the sample instead of the
        thing being sampled.
        """
        if self.samples < PERCENTILE_MINIMUM_SAMPLES:
            return None
        ordered = sorted(self.repetitions)
        index = min(int(fraction * (len(ordered) - 1) + 0.5), len(ordered) - 1)
        return ordered[index]

    def as_dict(self) -> dict[str, Any]:
        """The published shape: summary *and* the samples behind it.

        Both, always. A consumer that wants the headline can read `median`, and
        one that wants to know whether the headline means anything can read
        `repetitions` — which is the difference §11.7 exists to preserve.
        """
        return {
            "unit": self.unit,
            "direction": self.direction,
            "samples": self.samples,
            "median": self.median,
            "mean": statistics.fmean(self.repetitions),
            "min": min(self.repetitions),
            "max": max(self.repetitions),
            "stddev": self.spread,
            "p10": self.percentile(0.10),
            "p90": self.percentile(0.90),
            "repetitions": list(self.repetitions),
            "provenance": self.provenance.as_dict(),
        }


@dataclass(frozen=True)
class TrialRate:
    """A pass rate over discrete trials, which is not a distribution.

    Tool-call reliability is the case §13.2 makes first-class: eight phrasings
    times three repetitions is twenty-four attempts, and the useful number is
    how many produced a well-formed call. Modelling it as a `Measurement` would
    invite a median over ones and zeros, which is meaningless.

    `passed` and `total` are both kept, because 23/24 and 230/240 are different
    amounts of evidence for the same rate.
    """

    passed: int
    total: int
    provenance: Provenance
    phrasings: int | None = None
    repetitions_each: int | None = None
    # **How the failures failed, by name.**
    #
    # `3/24` reads as "cannot call tools". On this machine the build behind that
    # number calls `readFile` every single time and sends empty arguments — it
    # loses the filename, twenty-one times out of twenty-four. That is a
    # different defect from never calling the tool at all, it has a different
    # fix, and a rate cannot express either.
    #
    # The trial runner has always produced this tally; it stopped at the record
    # boundary, so nothing downstream could tell the two apart. Optional and
    # defaulting to empty because older records do not carry it and an absent
    # tally is "nobody recorded it" rather than "there were no failures".
    outcomes: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.total <= 0:
            raise ValueError("a trial rate needs at least one attempt")
        if not 0 <= self.passed <= self.total:
            raise ValueError("passed must be between zero and total")
        counted = sum(self.outcomes.values())
        if self.outcomes and counted != self.total:
            # A tally that does not add up is worse than none: it would be read
            # as a complete account of the attempts and is not one.
            raise ValueError(
                f"the outcome tally covers {counted} attempts, not {self.total}"
            )

    @property
    def rate(self) -> float:
        return self.passed / self.total

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total": self.total,
            "rate": self.rate,
            "phrasings": self.phrasings,
            "repetitions": self.repetitions_each,
            # Sorted by count, because the first line of a failure report should
            # be the failure that happened most.
            "outcomes": dict(
                sorted(self.outcomes.items(), key=lambda item: (-item[1], item[0]))
            ),
            "provenance": self.provenance.as_dict(),
        }


@dataclass(frozen=True)
class EvidenceIdentity:
    """§12.2's key. Evidence is **never** keyed as model → score.

    Every field here changes what the evidence is *about*, which is why they are
    all part of the identity rather than metadata beside it. The example §12.2
    gives is this machine's: a Qwen family MLX 4-bit build under the MLX runtime
    at 32K for `clarvis-agent` is different evidence from the same base model as
    GGUF Q4_K_M under llama.cpp — and on this hardware those two reach 1/8 and
    8/8 on the same tool-call trial.
    """

    machine_id: str
    model_family: str
    model_variant: str
    runtime: str
    role: str
    benchmark_suite: str
    benchmark_version: str
    source_repository: str | None = None
    source_revision: str | None = None
    model_format: str | None = None
    quantization: str | None = None
    runtime_version: str | None = None
    runtime_configuration: Mapping[str, Any] = field(default_factory=dict)

    @property
    def evidence_id(self) -> str:
        """A stable key derived from every identifying field.

        Derived rather than generated for the same reason M3's identifiers are:
        the same measurement conditions produce the same key, so two runs of one
        suite against one build are recognisably about the same thing without a
        table to remember it.

        Changing *any* field produces a different key, which is §12.4's rule
        made mechanical — a change creates new evidence rather than rewriting
        old evidence.
        """
        parts = [
            self.machine_id, self.model_family, self.model_variant,
            self.source_repository, self.source_revision, self.model_format,
            self.quantization, self.runtime, self.runtime_version,
            _canonical(self.runtime_configuration), self.role,
            self.benchmark_suite, self.benchmark_version,
        ]
        canonical = "\x1f".join((part or "").strip().lower() for part in parts)
        return f"ev_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "machine_id": self.machine_id,
            "role": self.role,
            "target": {
                "model_family": self.model_family,
                "variant": self.model_variant,
                "source_repository": self.source_repository,
                "source_revision": self.source_revision,
                "format": self.model_format,
                "quantization": self.quantization,
                "runtime": self.runtime,
                "runtime_version": self.runtime_version,
                "runtime_config": dict(self.runtime_configuration),
            },
            "suite": {"id": self.benchmark_suite, "version": self.benchmark_version},
        }


def _canonical(mapping: Mapping[str, Any]) -> str:
    """A configuration rendered so two equal configurations hash alike.

    Sorted, because a dictionary's insertion order is not part of what a
    configuration *is* — and without this the same 32K context run twice would
    produce two evidence IDs depending on which key happened to be set first.
    """
    return ";".join(f"{key}={mapping[key]}" for key in sorted(mapping))


class ValidityScope(str, Enum):
    """What a validity warning actually undermines (§16 item 7).

    **A record-level flag was too blunt to act on.** 44 of 93 records on the
    machine this was written against were `SUSPECT`, and a consumer had two
    equally wrong options: trust them all, or discard them all. Twenty-one were
    suspect because the machine was thermally throttled — a 48% swing in
    tokens/second, per `_thermal_warnings`, and no evidence at all about whether
    the model formed well-formed tool calls. Discarding that is losing a correct
    measurement because the room was warm.

    Three scopes, because that is how the producers already divide and no finer
    division is currently observable. A warning nobody can place stays
    `CONDITIONS`, which is the conservative reading: it taints everything.
    """

    #: The rate numbers describe something other than the model — thermal
    #: pressure, swap. Correctness claims on the same run are unaffected.
    TIMING = "TIMING"
    #: What came back is in question: tokens that never arrived as content,
    #: unexpected generation stops. Timing may still be sound.
    OUTPUT = "OUTPUT"
    #: The run did not answer the question asked — a configuration mismatch, an
    #: adapted prompt — so nothing measured on it is safe to reuse.
    CONDITIONS = "CONDITIONS"


class Validity(str, Enum):
    """Whether the conditions of a run allow its numbers to be believed (§11.8).

    Separate from provenance. A measurement can be genuinely `MEASURED` and
    still `SUSPECT` — taken while the machine was thermally throttled, or while
    it was swapping — and collapsing the two would leave a consumer unable to
    tell "we did not measure this" from "we measured it under conditions that
    make it not comparable".
    """

    VALID = "VALID"
    SUSPECT = "SUSPECT"
    INVALID = "INVALID"


@dataclass(frozen=True)
class EvidenceRecord:
    """§12.3's envelope: one suite, one build, one role, one machine.

    There is deliberately no `score` field and no way to add one. §12.2 forbids
    evidence keyed as model → score, and the enforcement is structural rather
    than a review comment: metrics are named, united, directed and provenanced,
    and a single ranking number has nowhere to live.
    """

    identity: EvidenceIdentity
    measurements: Mapping[str, Measurement] = field(default_factory=dict)
    rates: Mapping[str, TrialRate] = field(default_factory=dict)
    validity: Validity = Validity.VALID
    validity_notes: tuple[str, ...] = ()

    # **What the run asked for, beside what it got (§16 item 7).** The identity
    # above is keyed on the *effective* configuration, and must stay that way —
    # §12.2's collision rule means a run that requested 32768 and got 8192 is the
    # same measurement as one that requested 8192, because the same thing ran.
    #
    # That keying loses the request, though, and the request is what tells a
    # reader whether the number answers their question. The mismatch was already
    # reported as a validity note, in prose; prose is not something a router can
    # compare. Empty when nothing was requested for a key, which is a different
    # fact from requesting and receiving the same value — so it is stored either
    # way rather than omitted when it matches.
    requested_configuration: Mapping[str, Any] = field(default_factory=dict)

    # Which parts of this record the notes above call into question. Empty on a
    # `VALID` record, and empty on records written before this existed — a
    # reader must treat "no scopes" as "not stated" rather than as "nothing
    # affected", which is why RAVIS keeps its previous behaviour for them.
    validity_scopes: tuple[ValidityScope, ...] = ()
    machine_snapshot_id: str | None = None
    sirvis_version: str = "0.0.1"

    @property
    def evidence_type(self) -> EvidenceKind:
        """The record's provenance, weakened to match its weakest input.

        Derived, never set. If it were a field, a caller could write `MEASURED`
        onto a record holding an estimate — which is the one thing §12.1 says
        cannot happen, so the schema does not offer the opportunity.
        """
        kinds = [m.provenance.kind for m in self.measurements.values()]
        kinds += [r.provenance.kind for r in self.rates.values()]
        return combine(kinds)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.identity.as_dict(),
            "evidence_type": self.evidence_type.value,
            "samples": max(
                (m.samples for m in self.measurements.values()), default=0
            ),
            "metrics": {
                **{name: m.as_dict() for name, m in self.measurements.items()},
                **{name: r.as_dict() for name, r in self.rates.items()},
            },
            "validity": self.validity.value,
            "validity_notes": list(self.validity_notes),
            "requested_configuration": dict(self.requested_configuration),
            "validity_scopes": [scope.value for scope in self.validity_scopes],
            "machine_snapshot_id": self.machine_snapshot_id,
            "sirvis_version": self.sirvis_version,
        }


@dataclass(frozen=True)
class RoleVerdict:
    """A pass or fail for **build + runtime config + role** (§12.5).

    Never for a conceptual model. §12.5's example is the rule: one build can be
    `PASS` for `clarvis-chat` and `FAIL` for `clarvis-agent` on tool-call
    reliability, and "do not mark the whole family bad" is the sentence this
    type exists to make hard to disobey — the identity it carries is a variant's,
    and there is no constructor that takes a family.
    """

    identity: EvidenceIdentity
    passed: bool
    reason: str

    def __post_init__(self) -> None:
        if not self.passed and not self.reason:
            raise ValueError("a failing verdict must say why")

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.identity.evidence_id,
            "variant": self.identity.model_variant,
            "runtime_config": dict(self.identity.runtime_configuration),
            "role": self.identity.role,
            "verdict": "PASS" if self.passed else "FAIL",
            "reason": self.reason,
        }
