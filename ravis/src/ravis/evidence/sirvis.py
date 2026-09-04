"""M13 — reading SIRVIS's evidence without flattening it (RAVIS.md §13).

§13.1 opens with the prohibition that shapes this whole module:

    **Never** reduce SIRVIS results to `model → score`.

So nothing here computes one. What arrives is a record keyed by family, build,
runtime, runtime configuration, machine, role and suite; what leaves is a
*capability claim* — a statement that a build satisfies a pool invariant, or
does not, or that nobody knows. Ranking stays where §13.3 puts it: "SIRVIS
recommendations are advisory inputs — RAVIS remains responsible for eligibility
and ranking."

**The threshold is not RAVIS's to choose.** SIRVIS.md §13.2 records it, and it
has two axes rather than one:

    clarvis-agent  tool_call_pass_rate >= 0.95
                   over >= 8 distinct prompt phrasings x >= 3 repetitions each

The phrasing axis is the one that catches failures. An earlier version said
">= 50 attempts" and was wrong in kind: it assumed repetition of a single
prompt, and a build can pass one phrasing fifty times while losing the filename
on seven of eight ways a person actually asks. Both axes are enforced below, and
a record that clears the rate while failing the sample requirement establishes
**nothing** — which is a different answer from failing, and is reported as one.

**Provenance is never upgraded** (§13.3). SIRVIS's `MEASURED` becomes RAVIS's
`MEASURED`; anything weaker than measured becomes `ESTIMATED` and cannot satisfy
a pool invariant, because §9.1 fails closed on what is not established. There is
no path here that turns an estimate into a measurement, including caching it,
re-serving it, or a second service repeating it back.

**Absence, staleness and unreachability are three different findings** and stay
apart. A build SIRVIS has never measured is UNKNOWN. A build measured too long
ago is UNKNOWN *and* the source is marked degraded. A SIRVIS that will not
answer degrades every claim to UNKNOWN and says so — §13.4: RAVIS still
operates, with the degradation labelled, and never fabricates a score.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence

import httpx
from ecosystem_protocol import is_supported_protocol, wire_identifier

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    Provenance,
)
from ravis.errors import UnsupportedProtocolVersionError

# §13.2's accepted threshold for the agent role, recorded in SIRVIS.md §13.2.
# Both axes, because the phrasing axis is the one that catches failures.
TOOL_CALL_PASS_RATE = 0.95
MIN_PHRASINGS = 8
MIN_REPETITIONS = 3

# The metric names SIRVIS publishes for M13's trials.
RATE_TOOL_CALLS = "tool_call_well_formed"

# The one outcome in SIRVIS's vocabulary that is not a failure. Named here so a
# reason never reports "21 of them used-result" as though success were the
# complaint.
OUTCOME_PASSED = "used-result"

# §11.4's reasoning share, filed by SIRVIS M22b: the fraction of a build's
# output that is thinking rather than answer. Named here rather than spelled
# inline for the same reason as the rate above — the wire name is a contract
# with another service, and a typo in one reads as "never measured" rather than
# as an error.
MEASUREMENT_REASONING_SHARE = "reasoning_token_share"

# SIRVIS's per-measurement provenance vocabulary, which is **not** RAVIS's
# `EvidenceProvenance` below. That enum records where a number reached RAVIS
# from; this string is SIRVIS's own statement about how the number was arrived
# at, and §12.1's lattice puts three weaker levels under it.
SIRVIS_MEASURED = "MEASURED"
RATE_FOLLOWUP = "tool_followup_used_result"

# How long a measurement is believed. Generous, because evidence about a build
# on a machine does not rot quickly — the identity already pins the machine, the
# runtime and the configuration, so what expires is confidence that nothing
# else changed. Not infinite, because §13.3 requires a staleness policy and a
# claim with no expiry is one nobody will ever revisit.
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 3600

# The major version of SIRVIS's build this evidence shape was written against.
# §13.4's pairwise gate requires an unsupported major to produce a deterministic
# effect, and the effect is refusal: a record whose shape this code has not been
# written against is not evidence, it is a guess about a shape.
#
# **Zero, because SIRVIS is pre-1.0** — it publishes `sirvis_version: "0.0.1"`.
# Worth stating plainly rather than leaving as a puzzling constant: the record
# carries a *build* version and there is no separate evidence-contract version
# to read, so this checks the only major there is.
SUPPORTED_EVIDENCE_MAJOR = 0

# The capability that names the evidence contract, from SIRVIS.md §4.1's table.
# This comment used to claim the capability "is checked separately — a peer that
# stops advertising it stops being read", and nothing checked it: RAVIS read
# `/api/v1/evidence` from whatever answered, negotiating nothing. `_negotiate`
# below is that sentence made true.
EVIDENCE_CAPABILITY = "sirvis.benchmarks.results@1"


class EvidenceProvenance(str, Enum):
    """§13.3's enum, which is RAVIS's own and not SIRVIS's.

    Deliberately separate from `core.capabilities.Provenance`. That one orders
    *capability claims* by trust so a measurement outranks an advertisement;
    this one records where a **number** came from, and the two answer different
    questions. Collapsing them would lose the distinction §13.3 exists to keep:
    `OBSERVED_BY_RAVIS` and `MEASURED_BY_SIRVIS` are both measurements, and only
    one of them was taken under controlled conditions.
    """

    MEASURED_BY_SIRVIS = "MEASURED_BY_SIRVIS"
    OBSERVED_BY_RAVIS = "OBSERVED_BY_RAVIS"
    PROVIDER_METADATA = "PROVIDER_METADATA"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"


# SIRVIS's evidence kinds mapped onto RAVIS's. §13.3 names the one that matters:
# `PARTIALLY_MEASURED` maps to `ESTIMATED` unless RAVIS models it explicitly,
# and RAVIS does not — so it lands here as an estimate and cannot satisfy an
# invariant. Anything unrecognised is UNKNOWN rather than assumed measured.
KIND_MAP = {
    "MEASURED": EvidenceProvenance.MEASURED_BY_SIRVIS,
    "PARTIALLY_MEASURED": EvidenceProvenance.ESTIMATED,
    "ESTIMATED": EvidenceProvenance.ESTIMATED,
    "PROVIDER_METADATA": EvidenceProvenance.PROVIDER_METADATA,
    "UNKNOWN": EvidenceProvenance.UNKNOWN,
}


class SourceState(str, Enum):
    """Whether SIRVIS's evidence can be believed right now (§13.4)."""

    FRESH = "fresh"
    DEGRADED = "degraded"
    ABSENT = "absent"


@dataclass(frozen=True)
class EvidenceRecord:
    """One SIRVIS record, as RAVIS holds it.

    Every identity field §13.1 lists is kept. They are not decoration: two
    packagings of one model share a family name, a parameter count and an
    architecture, and disagree 8/8 against 1/8 on the same tool-call trial. The
    fields that tell them apart are `variant`, `model_format` and
    `quantization`, and dropping any of them collapses that difference.
    """

    evidence_id: str
    runtime_key: str
    machine_id: str
    role: str
    model_family: str
    variant: str
    model_format: str | None
    quantization: str | None
    runtime: str
    runtime_configuration: Mapping[str, Any]
    suite: str
    suite_version: str
    provenance: EvidenceProvenance
    validity: str
    measured_at: str
    age_seconds: float | None
    samples: int
    #: Which parts of the record its validity notes call into question, as
    #: SIRVIS declared them (§16 item 7). Empty means *not stated* — every
    #: record written before scopes existed has none — and is deliberately not
    #: read as "nothing affected".
    validity_scopes: tuple[str, ...] = ()
    metrics: Mapping[str, Any] = field(default_factory=dict)
    evidence_ref: str = ""

    def rate(self, name: str) -> tuple[int, int] | None:
        """One trial rate as `(passed, total)`, or None when it is absent.

        None rather than `(0, 0)`: §12.1's distinction, and the one that decides
        whether a pool admits a build. "Nobody asked" and "asked and failed" are
        different claims, and only the second is about the model.
        """
        body = self.metrics.get(name)
        if not isinstance(body, Mapping):
            return None
        passed, total = body.get("passed"), body.get("total")
        if not isinstance(passed, int) or not isinstance(total, int) or total <= 0:
            return None
        return passed, total

    def outcomes(self, name: str) -> dict[str, int]:
        """How the failures failed, by name, or nothing when unrecorded.

        SIRVIS counts a closed vocabulary — `used-result`, `lost-arguments`,
        `no-call`, `retried`, `answered-in-prose`, `gave-up` — and now publishes
        the tally beside the rate. It is worth relaying because the rate hides
        the distinction that matters: a build at 3/24 that calls the tool every
        time and sends empty arguments has a different defect, and a different
        fix, from one that never calls it.

        Empty for older records, which carried no tally at all. Absent is not
        "no failures" and this returns nothing rather than pretending otherwise.
        """
        body = self.metrics.get(name)
        if not isinstance(body, Mapping):
            return {}
        found = body.get("outcomes")
        if not isinstance(found, Mapping):
            return {}
        return {
            str(outcome): int(count)
            for outcome, count in found.items()
            if isinstance(count, int) and count > 0
        }

    def measured_share(self, name: str) -> float | None:
        """One measurement's headline, but only when it was actually measured.

        None for anything weaker, which is the same distinction `rate` draws one
        method up: "nobody established this" and "this is 0.0" are different
        claims, and only the second is about the model.

        **Provenance is checked per measurement, not per record.** §13.3 forbids
        upgrading it, and this is where that rule earns its keep: a reasoning
        share arrives as `ESTIMATED` whenever the runtime hid its token counts,
        and SIRVIS files both kinds in the same record beside measurements that
        are fully counted. Ranking on an estimate would be exactly §9.4's
        invented measurement — an estimate is a good enough reason to *tell*
        somebody a build thinks a lot, and not a good enough reason to move a
        route without saying why.

        `direction` is verified rather than assumed. It exists so a consumer can
        sort two numbers it did not produce, and reading a higher-is-better
        quantity as though lower were better inverts a ranking with nothing
        visible going wrong — the failure §11.7 added the field to prevent.
        """
        body = self.metrics.get(name)
        if not isinstance(body, Mapping):
            return None
        provenance = body.get("provenance")
        kind = provenance.get("kind") if isinstance(provenance, Mapping) else None
        if kind != SIRVIS_MEASURED or body.get("direction") != "lower":
            return None
        median = body.get("median")
        if not isinstance(median, (int, float)) or isinstance(median, bool):
            return None
        return float(median)

    def as_dict(self) -> dict[str, Any]:
        """The shape a route explanation carries (§9.7 — no internal addresses)."""
        return {
            "evidence_id": self.evidence_id,
            "evidence_ref": self.evidence_ref,
            "runtime_key": self.runtime_key,
            "role": self.role,
            "target": {
                "family": self.model_family, "variant": self.variant,
                "format": self.model_format, "quantization": self.quantization,
                "runtime": self.runtime, "runtime_config": dict(self.runtime_configuration),
            },
            "suite": {"id": self.suite, "version": self.suite_version},
            "provenance": self.provenance.value,
            "validity": self.validity,
            "measured_at": self.measured_at,
            "age_seconds": self.age_seconds,
            "samples": self.samples,
        }


@dataclass
class EvidenceVerdict:
    """What one record establishes about one capability, and why.

    The reason travels with the answer because §9.7 requires a route to explain
    itself, and "excluded: no reliable tool calls" and "excluded: measured once
    where the threshold needs three" send a reader to different places.
    """

    state: CapabilityState
    detail: str
    record: EvidenceRecord | None = None


def _how_it_failed(record: EvidenceRecord) -> str:
    """The dominant failure, named, when SIRVIS recorded one.

    **A refusal that says why is a refusal somebody can act on.** "3/24 well-formed
    tool calls" sends a reader to look for a better model; "3/24 — 21 of them
    lost-arguments" tells them the build calls the tool and drops the filename,
    which is a quantisation or template problem and may well have a fix. Empty
    for records that predate the tally, because inventing a cause is worse than
    reporting a rate.
    """
    failures = {
        outcome: count
        for outcome, count in record.outcomes(RATE_TOOL_CALLS).items()
        if outcome != OUTCOME_PASSED
    }
    if not failures:
        return ""
    outcome, count = max(failures.items(), key=lambda item: (item[1], item[0]))
    return f" — {count} of them {outcome}"


def tool_verdict(record: EvidenceRecord | None) -> EvidenceVerdict:
    """Apply §13.2's threshold to one record.

    Four answers, and the third is the one a naive reading would miss:

    - no record, or no trial on it: **UNKNOWN** — nobody asked.
    - a rate below the bar: **UNSUPPORTED** — asked, and it failed.
    - a rate at or above the bar but too few attempts: **UNKNOWN**, not
      supported. §13.2 requires eight phrasings *times three repetitions*, and
      a build that cleared the rate over eight attempts has cleared one axis of
      a two-axis threshold. Admitting it would be routing on a sample the
      specification says is too small to conclude from.
    - the bar cleared on both axes, from measured provenance: **SUPPORTED**.
    """
    if record is None:
        return EvidenceVerdict(CapabilityState.UNKNOWN, "no SIRVIS evidence for this build")
    if record.provenance is not EvidenceProvenance.MEASURED_BY_SIRVIS:
        # §13.3: never upgrade. An estimate cannot establish an invariant.
        return EvidenceVerdict(
            CapabilityState.UNKNOWN,
            f"evidence is {record.provenance.value}, which cannot establish a capability",
            record,
        )
    tainted = {scope.upper() for scope in record.validity_scopes} & {"OUTPUT", "CONDITIONS"}
    if tainted:
        # **The scope decides, not the flag (§16 item 7).** A tool-call verdict
        # is a claim about what came back, so a warning about what came back —
        # or about the run having answered a different question entirely —
        # cannot establish it. A warning about *timing* can: a heat-soaked
        # machine swings tokens/second by 48% and says nothing about whether the
        # calls were well formed, and demoting on it would discard a correct
        # measurement because the room was warm.
        return EvidenceVerdict(
            CapabilityState.UNKNOWN,
            f"SIRVIS marked this evidence SUSPECT for {', '.join(sorted(tainted))}, "
            "which is what this capability is measured from",
            record,
        )
    if record.validity.upper() == "INVALID":
        # **The same rule, for a measurement SIRVIS itself disowned (§16 item 7).**
        # `validity` was ingested here and read nowhere, so a record SIRVIS marked
        # unusable routed exactly as a clean one did — the shape of §16 items 2
        # and 4, where a value is computed correctly and applied nowhere.
        #
        # `SUSPECT` alone is still not caught, and now for a better reason than
        # "RAVIS cannot tell": SIRVIS states the scope, and the check above reads
        # it. A record suspect only for TIMING establishes this capability —
        # a heat-soaked machine swings tokens/second by 48% and says nothing
        # about whether calls were well formed. An unscoped one predates scopes
        # entirely, counts as it always did, and carries the caveat.
        return EvidenceVerdict(
            CapabilityState.UNKNOWN,
            "SIRVIS marked this evidence INVALID, which cannot establish a capability",
            record,
        )
    counted = record.rate(RATE_TOOL_CALLS)
    if counted is None:
        return EvidenceVerdict(
            CapabilityState.UNKNOWN, "the suite ran but made no tool-call trial", record
        )
    passed, total = counted
    rate = passed / total
    phrasings = _phrasings(record)
    attempts_needed = MIN_PHRASINGS * MIN_REPETITIONS
    if rate < TOOL_CALL_PASS_RATE:
        return EvidenceVerdict(
            CapabilityState.UNSUPPORTED,
            f"{passed}/{total} well-formed tool calls, below the {TOOL_CALL_PASS_RATE:.0%} "
            f"threshold for {record.role}" + _how_it_failed(record),
            record,
        )
    if phrasings < MIN_PHRASINGS or total < attempts_needed:
        return EvidenceVerdict(
            CapabilityState.UNKNOWN,
            f"{passed}/{total} well-formed tool calls over {phrasings} phrasings — clears the "
            f"rate but not the sample: §13.2 requires {MIN_PHRASINGS} phrasings × "
            f"{MIN_REPETITIONS} repetitions ({attempts_needed} attempts)",
            record,
        )
    return EvidenceVerdict(
        CapabilityState.SUPPORTED,
        f"{passed}/{total} well-formed tool calls over {phrasings} phrasings, "
        f"measured by SIRVIS for {record.role}" + _measured_at(record) + _caveat(record),
        record,
    )


def _caveat(record: EvidenceRecord) -> str:
    """Say when a capability rests on evidence SIRVIS flagged (§16 item 7).

    Suspect evidence still establishes the capability — see the note in
    `tool_verdict` for why demoting on it would discard correct measurements
    because the machine was warm. What it must not do is arrive looking clean.
    §9.7 asks a route to explain itself, and "chosen on a measurement its own
    producer marked suspect" is exactly the kind of thing that explanation is
    for: it costs one clause here and saves somebody reading a benchmark number
    that nobody flagged to them.
    """
    if record.validity.upper() != "SUSPECT":
        return ""
    # A record whose only warnings are about timing is not a caveat on a
    # correctness claim, so it does not earn one here. An *unscoped* suspect
    # record does: "not stated" is not "nothing affected", and the honest
    # handling of not-stated is to count it and say so.
    if record.validity_scopes and not (
        {scope.upper() for scope in record.validity_scopes} - {"TIMING"}
    ):
        return ""
    return " — on evidence SIRVIS marked SUSPECT"


def _measured_at(record: EvidenceRecord) -> str:
    """The runtime configuration the trial ran under, when it is known.

    §12.2 keys evidence on the configuration a number was produced under, so a
    tool-call rate measured at 8K is evidence about 8K. A pool may require more
    context than the trial used, and admitting on that evidence is a small
    inference — visible in the explanation rather than hidden inside it.
    """
    context = (record.runtime_configuration or {}).get("context_length")
    return f" at context_length {context}" if context else ""


def _phrasings(record: EvidenceRecord) -> int:
    """How many distinct prompts the trial used, as SIRVIS reported it."""
    body = record.metrics.get(RATE_TOOL_CALLS)
    phrasings = body.get("phrasings") if isinstance(body, Mapping) else None
    return phrasings if isinstance(phrasings, int) else 0


class EvidenceStore:
    """SIRVIS's evidence, cached with a staleness policy (§13.3).

    Holds what was last read and how long ago, so a route decision does not
    depend on SIRVIS answering within its own latency budget — and so a SIRVIS
    that stops answering degrades the *source* rather than silently freezing
    yesterday's claims into permanent truth.
    """

    def __init__(
        self,
        base_url: str = "",
        role: str = "clarvis-agent",
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        clock: Any = time.monotonic,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._role = role
        self._max_age = max_age_seconds
        self._clock = clock
        # runtime key → role → the freshest record filed under that role.
        #
        # **This was one record per build, for one role.** RAVIS asked SIRVIS
        # only about `clarvis-agent`, on the reasoning that "a pool's invariant
        # is role-specific". Half of that holds and half does not: a pool that
        # wants a model measured *for its own work* does need its own role, and
        # a tool-call trial is a fact about the build. Reading one role meant
        # every other pool had no evidence at all, and the trials — all seven of
        # them on this machine — were invisible to every pool but one.
        self._records: dict[str, dict[str, EvidenceRecord]] = {}
        # Reasoning shares, keyed by runtime key and read across every role.
        # Separate from `_records` because they are gathered by a different
        # question — see `_absorb_shares`.
        self._shares: dict[str, float] = {}
        # Declared context ceilings from SIRVIS's inventory, keyed by runtime
        # key. §13 lists "model fit" among what RAVIS asks SIRVIS for, and a
        # context ceiling is the most basic fit fact there is — without it a
        # pool declaring a minimum fails closed on every candidate, which is
        # correct and useless.
        self._context: dict[str, int] = {}
        self._state = SourceState.ABSENT
        self._detail = "no SIRVIS configured"
        self._read_at: float | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url)

    @property
    def state(self) -> SourceState:
        return self._state

    @property
    def detail(self) -> str:
        return self._detail

    def context_window(self, runtime_key: str) -> int | None:
        """The build's declared context ceiling, as SIRVIS's inventory reports it.

        **Declared, not measured**, and the distinction is kept: this is the
        build's advertised maximum, which is a different claim from a context
        length something was benchmarked at. It is good enough to decide whether
        a pool's minimum is *possible*, which is the question `meets_context`
        asks, and it is not evidence that the build performs well there.
        """
        return self._context.get(runtime_key)

    def record_for(self, runtime_key: str, role: str = "") -> EvidenceRecord | None:
        """The freshest record for one build, or None.

        `role` narrows to evidence filed under that role — what a pool asking
        *"was this measured for my work"* needs. Empty takes the freshest record
        under any role, which is what a question about the build itself wants.

        A record older than the staleness window returns None *and* degrades the
        source: §9.1 fails closed on what is not established, and a measurement
        whose window has passed is no longer establishing anything.

        **The second half of that sentence was not code.** This returned None
        and never touched `_state`, which is assigned only inside `refresh`. So
        a route explanation reported `source: "fresh"` beside a build recorded
        as never measured, and the management surface said the evidence source
        was healthy while every record in it had aged out. The reader is told
        the measurement is missing and that the thing which would supply it is
        fine -- two statements that cannot both be acted on.

        Degraded rather than absent: SIRVIS answered, and what it said has
        simply expired. Absent is for a source that is not configured or did not
        reply, and the difference decides whether an operator looks at SIRVIS or
        at the clock.
        """
        record = self._pick(runtime_key, role)
        if record is None:
            return None
        if record.age_seconds is not None and record.age_seconds > self._max_age:
            self._state = SourceState.DEGRADED
            self._detail = (
                f"the newest record for {runtime_key} is "
                f"{int(record.age_seconds)}s old, past the {int(self._max_age)}s "
                "staleness window"
            )
            return None
        return record

    def _pick(self, runtime_key: str, role: str) -> EvidenceRecord | None:
        """One build's record: for a named role, or the freshest of any."""
        by_role = self._records.get(runtime_key)
        if not by_role:
            return None
        if role:
            return by_role.get(role)
        freshest: EvidenceRecord | None = None
        for record in by_role.values():
            if freshest is None or _fresher(record, freshest):
                freshest = record
        return freshest

    def trial_record_for(self, runtime_key: str) -> EvidenceRecord | None:
        """The freshest record that actually ran a tool-call trial, any role.

        **A trial is a fact about the build, not about the role it was filed
        under.** §13.1 defines it as a pass rate over phrasings and repetitions
        of one request — nothing in that is specific to Clarvis's agent
        workload, and the suite that produced every trial on this machine was
        run under `clarvis-agent` only because that was the flag that existed.
        A record with no trial in it establishes nothing about tools and must
        not shadow one that does, which is what picking "the freshest record"
        did: a later throughput run under `general` hid the trial underneath it.
        """
        by_role = self._records.get(runtime_key) or {}
        best: EvidenceRecord | None = None
        for record in by_role.values():
            if record.rate("tool_call_well_formed") is None:
                continue
            if best is None or _fresher(record, best):
                best = record
        if best is None:
            return None
        if best.age_seconds is not None and best.age_seconds > self._max_age:
            self._state = SourceState.DEGRADED
            self._detail = (
                f"the newest tool-call trial for {runtime_key} is "
                f"{int(best.age_seconds)}s old, past the {int(self._max_age)}s "
                "staleness window"
            )
            return None
        return best

    def roles_measured(self, runtime_key: str) -> dict[str, str]:
        """Which roles this build has been measured for, and how it did.

        The answer to *"what is this build qualified for"*, derived rather than
        declared. `SUPPORTED` where a role's evidence clears §13.1's bar,
        `UNSUPPORTED` where it was measured and did not, and a role that was
        never run does not appear at all — absence of a measurement is not a
        failed one, and the two admit and exclude differently.
        """
        fit: dict[str, str] = {}
        for role, record in (self._records.get(runtime_key) or {}).items():
            if record.rate("tool_call_well_formed") is None:
                # Measured for this role on some other axis. That is a real
                # fact and it is not a verdict on the role, so it is reported
                # as covered-without-a-verdict rather than as a pass.
                fit[role] = CapabilityState.UNKNOWN.value
                continue
            fit[role] = tool_verdict(record).state.value
        return fit

    def reasoning_share(self, runtime_key: str) -> float | None:
        """How much of this build's output is thinking rather than answer.

        A *fit* fact and not a quality one, which is the whole reason RAVIS is
        allowed to rank on it at all. §13.1's prohibition is on reducing SIRVIS
        results to `model -> score`; this reduces nothing and compares nothing
        across builds on merit. It answers one narrow question — of a finite
        output budget, how much does this build historically spend before it
        starts answering — and `routing.engine` uses it only where that question
        is live.

        Goes through `record_for`, so the staleness window and the source
        degradation it performs apply here exactly as they do to capability
        claims. A measurement that has aged out establishes nothing, whether it
        was going to admit a build or order one.
        """
        # The role-scoped record first, because a share measured under the role
        # RAVIS actually asks about is the most specific answer available.
        record = self.record_for(runtime_key)
        if record is not None:
            direct = record.measured_share(MEASUREMENT_REASONING_SHARE)
            if direct is not None:
                return direct
        return self._shares.get(runtime_key)

    def claims_for(self, runtime_key: str) -> list[CapabilityClaim]:
        """What this build's evidence establishes, as capability claims.

        Only claims that say something are returned. An UNKNOWN verdict records
        nothing rather than recording UNKNOWN, because `ModelCapabilities`
        already reads an absent claim as UNKNOWN — writing one would occupy the
        slot a better-sourced claim should win, and `record()` resolves by
        provenance rather than by arrival.
        """
        verdict = tool_verdict(self.trial_record_for(runtime_key))
        if verdict.state in (CapabilityState.UNKNOWN,):
            return []
        return [
            CapabilityClaim(
                capability=Capability.TOOLS,
                state=verdict.state,
                # §13.3's mapping onto the capability lattice: SIRVIS's measured
                # evidence is a measurement, and `Provenance.MEASURED` is what
                # outranks an advertisement without out-voting an operator.
                provenance=Provenance.MEASURED,
                detail=verdict.detail,
            )
        ]

    def explain(self, runtime_key: str) -> dict[str, Any]:
        """Why this build was or was not admitted, for a route explanation."""
        verdict = tool_verdict(self.trial_record_for(runtime_key))
        return {
            "source": self._state.value,
            # What this build has been measured *for*, which is a different
            # question from whether it can call a tool.
            "roles_measured": self.roles_measured(runtime_key),
            "source_detail": self._detail,
            "state": verdict.state.value,
            "detail": verdict.detail,
            "evidence": verdict.record.as_dict() if verdict.record else None,
        }

    def snapshot(self) -> dict[str, Any]:
        """The whole source, for the management surface."""
        return {
            "configured": self.is_configured,
            "state": self._state.value,
            "detail": self._detail,
            "role": self._role,
            "records": len(self._records),
            "max_age_seconds": self._max_age,
            "threshold": {
                "tool_call_pass_rate": TOOL_CALL_PASS_RATE,
                "phrasings": MIN_PHRASINGS,
                "repetitions_each": MIN_REPETITIONS,
            },
        }

    async def refresh(self, client: httpx.AsyncClient, candidates: Sequence[str]) -> None:
        """Re-read SIRVIS, or mark the source degraded and keep going (§13.4).

        Every failure lands in the same place — the source is degraded, the
        reason is recorded, and previously read records are *dropped* rather
        than served on. Serving them would be the one thing §13.3 forbids
        outright: presenting a cached measurement as a current one after the
        service that owns it stopped answering.
        """
        if not self.is_configured:
            self._state, self._detail = SourceState.ABSENT, "no SIRVIS configured"
            return
        if not candidates:
            self._records, self._state = {}, SourceState.FRESH
            self._detail = "no candidates to ask about"
            return
        try:
            refusal = await self._negotiate(client)
            if refusal:
                self._records, self._state = {}, SourceState.DEGRADED
                self._detail = refusal
                return
            response = await client.get(
                f"{self._base_url}/api/v1/evidence",
                # **No role filter.** Asking for one role returns evidence about
                # one question and hides the rest; the role travels *on* each
                # record, so filtering here threw away the field that makes the
                # answer usable. `self._role` remains the role a pool falls back
                # to when it declares none.
                params=[("candidate", key) for key in candidates],
            )
            response.raise_for_status()
            payload = response.json()
        except UnsupportedProtocolVersionError as mismatch:
            # §4.2 requires a consumer to reject an unsupported major with a
            # structured error, and §13.4 requires SIRVIS being unusable never
            # to stop RAVIS routing. Both hold: the refusal is raised
            # structurally where it is detected and applied as a degrade here.
            self._records, self._state = {}, SourceState.DEGRADED
            self._detail = mismatch.message
            return
        except (httpx.HTTPError, ValueError) as failure:
            self._records = {}
            self._state = SourceState.DEGRADED
            self._detail = f"SIRVIS did not answer: {type(failure).__name__}"
            return
        self._absorb(payload)
        await self._absorb_context(client)

    async def _negotiate(self, client: httpx.AsyncClient) -> str:
        """Check the protocol major and the capability before reading anything.

        Returns a reason not to read, or an empty string when SIRVIS may be
        read. Raises `UnsupportedProtocolVersionError` for a major mismatch
        specifically, because §4.2 makes that structural rather than advisory —
        a withdrawn capability is a service choosing not to offer something,
        while a major mismatch is two builds that cannot understand each other.

        **Tolerant of a peer with no MEP surface.** A 404 on either endpoint
        leaves this silent rather than refusing: `RAVIS_SIRVIS_BASE_URL` may
        point at a build predating the shared protocol package, and §13.4 makes
        evidence optional rather than the router's problem. What it will not do
        is read evidence from a service that answers and says no.
        """
        version = await self._peer_json(client, "/ecosystem/version")
        declared = str(version.get("protocol_version") or "")
        if declared and not is_supported_protocol(declared):
            raise UnsupportedProtocolVersionError(
                f"SIRVIS speaks protocol {declared}, which this build does not implement"
            )

        capabilities = await self._peer_json(client, "/ecosystem/capabilities")
        entries = capabilities.get("capabilities")
        if not isinstance(entries, list):
            return ""
        wanted = wire_identifier(EVIDENCE_CAPABILITY)
        for entry in entries:
            if isinstance(entry, Mapping) and entry.get("id") == wanted:
                if entry.get("state") == "available":
                    return ""
                reason = entry.get("reason") or "no reason given"
                return f"SIRVIS reports {EVIDENCE_CAPABILITY} as {entry.get('state')}: {reason}"
        return f"SIRVIS does not advertise {EVIDENCE_CAPABILITY}"

    async def _peer_json(self, client: httpx.AsyncClient, path: str) -> Mapping[str, Any]:
        """One MEP read, treating an absent surface as an empty answer."""
        response = await client.get(f"{self._base_url}{path}")
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, Mapping) else {}

    async def _absorb_context(self, client: httpx.AsyncClient) -> None:
        """Read declared context ceilings, tolerating their absence.

        A separate request because they are a different question: evidence is
        per role, an inventory is not. A failure here leaves the ceilings empty
        rather than degrading the evidence — a router that knows a build calls
        tools and does not know its context window is in a worse position than
        one that knows both, and a better one than one that knows neither.
        """
        try:
            response = await client.get(f"{self._base_url}/api/v1/models")
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            self._context = {}
            return
        items = payload.get("items") if isinstance(payload, Mapping) else None
        ceilings: dict[str, int] = {}
        for item in items or []:
            if not isinstance(item, Mapping):
                continue
            declared = item.get("declared_context")
            value = declared.get("value") if isinstance(declared, Mapping) else None
            key = item.get("runtime_key")
            if isinstance(value, int) and isinstance(key, str):
                ceilings[key] = value
        self._context = ceilings

    def _absorb(self, payload: Any) -> None:
        """Turn one response into records, refusing a shape this cannot read."""
        if not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
            self._records = {}
            self._state = SourceState.DEGRADED
            self._detail = "SIRVIS returned a shape this build cannot read"
            return
        variants = payload.get("candidate_variants")
        by_variant = (
            {variant: key for key, variant in variants.items()}
            if isinstance(variants, Mapping)
            else {}
        )
        records: dict[str, dict[str, EvidenceRecord]] = {}
        # **Read from the same payload, in the same walk.** Reasoning shares used
        # to need a second request: the main read asked for one role, and how
        # much of its output a build spends thinking is not a role-specific fact
        # — it is a property of the build's generation and equally true whichever
        # role asked. That read existed to work around the role filter. The
        # filter is gone, so the round trip is too.
        shares: dict[str, float] = {}
        for item in payload["items"]:
            if isinstance(item, Mapping):
                key = by_variant.get(str((item.get("target") or {}).get("variant") or ""), "")
                share = _measured_share(item.get("metrics"))
                if key and share is not None:
                    # Newest wins, and the API returns newest first.
                    shares.setdefault(key, share)
            record = _read_record(item, by_variant)
            if record is None:
                continue
            by_role = records.setdefault(record.runtime_key, {})
            held = by_role.get(record.role)
            if held is None or _fresher(record, held):
                by_role[record.role] = record
        self._shares = shares
        self._records = records
        self._read_at = self._clock()
        self._state = SourceState.FRESH
        roles = sorted({role for by_role in records.values() for role in by_role})
        total = sum(len(by_role) for by_role in records.values())
        self._detail = (
            f"{total} record(s) for {len(records)} build(s)"
            + (f" across role(s) {', '.join(roles)}" if roles else "")
        )


def _fresher(candidate: EvidenceRecord, held: EvidenceRecord) -> bool:
    """Whether one record is newer than another, tolerating an unknown age."""
    if candidate.age_seconds is None:
        return False
    if held.age_seconds is None:
        return True
    return candidate.age_seconds < held.age_seconds


def _variant_map(payload: Any) -> dict[str, str]:
    """Variant → runtime key, inverted from SIRVIS's own mapping.

    The same inversion `_absorb` does. SIRVIS resolves candidate keys to
    variants so RAVIS never has to infer equivalence between two packagings of
    one model — §13.1's rule, and the reason this is read rather than guessed.
    """
    variants = payload.get("candidate_variants") if isinstance(payload, Mapping) else None
    if not isinstance(variants, Mapping):
        return {}
    return {str(variant): str(key) for key, variant in variants.items()}


def _measured_share(metrics: Any) -> float | None:
    """`reasoning_token_share`, but only when SIRVIS counted rather than inferred.

    The same rule `EvidenceRecord.measured_share` applies, over a raw payload:
    §13.3 forbids upgrading provenance, and a share estimated from a runtime
    that hid its token counts is not established. `direction` is checked rather
    than assumed for the reason §11.7 added the field — reading a
    higher-is-better quantity as lower-is-better inverts a ranking silently.
    """
    if not isinstance(metrics, Mapping):
        return None
    body = metrics.get(MEASUREMENT_REASONING_SHARE)
    if not isinstance(body, Mapping):
        return None
    provenance = body.get("provenance")
    kind = provenance.get("kind") if isinstance(provenance, Mapping) else None
    if kind != SIRVIS_MEASURED or body.get("direction") != "lower":
        return None
    median = body.get("median")
    if not isinstance(median, (int, float)) or isinstance(median, bool):
        return None
    return float(median)


def _read_record(item: Any, by_variant: Mapping[str, str]) -> EvidenceRecord | None:
    """One evidence item, or None when it is not one this build understands.

    An unreadable item is skipped rather than raising: a SIRVIS that adds a
    record shape this build has not seen should cost that record, not every
    other candidate's evidence in the same response.
    """
    if not isinstance(item, Mapping):
        return None
    version = item.get("sirvis_version") or ""
    if not _supported_major(str(version)):
        return None
    target = item.get("target")
    target = target if isinstance(target, Mapping) else {}
    suite = item.get("suite")
    suite = suite if isinstance(suite, Mapping) else {}
    variant = str(target.get("variant") or "")
    runtime_key = by_variant.get(variant, variant)
    metrics = item.get("metrics")
    return EvidenceRecord(
        evidence_id=str(item.get("evidence_id") or ""),
        runtime_key=runtime_key,
        machine_id=str(item.get("machine_id") or ""),
        role=str(item.get("role") or ""),
        model_family=str(target.get("model_family") or ""),
        variant=variant,
        model_format=target.get("format"),
        quantization=target.get("quantization"),
        runtime=str(target.get("runtime") or ""),
        runtime_configuration=target.get("runtime_config") or {},
        suite=str(suite.get("id") or ""),
        suite_version=str(suite.get("version") or ""),
        provenance=KIND_MAP.get(
            str(item.get("evidence_type") or ""), EvidenceProvenance.UNKNOWN
        ),
        validity=str(item.get("validity") or ""),
        validity_scopes=tuple(
            str(scope) for scope in (item.get("validity_scopes") or [])
        ),
        measured_at=str(item.get("measured_at") or ""),
        age_seconds=_age(item.get("age_seconds")),
        samples=int(item.get("samples") or 0),
        metrics=metrics if isinstance(metrics, Mapping) else {},
        evidence_ref=str(item.get("evidence_ref") or ""),
    )


def _supported_major(version: str) -> bool:
    """Whether this build understands that evidence contract (§13.4).

    An unsupported major is refused rather than parsed optimistically. The
    fields might happen to line up, and a route decided on a shape nobody
    verified is the failure this check exists to make impossible.
    """
    head = version.split(".")[0] if version else ""
    if not head.isdigit():
        return False
    return int(head) == SUPPORTED_EVIDENCE_MAJOR


def _age(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


async def candidates_with_evidence(
    adapter: Any, models: Sequence[str], store: EvidenceStore | None
) -> dict[str, Any]:
    """Every model's capabilities, with SIRVIS's measurements folded in.

    One function because there are two call sites — the routing path and the
    management surface — and a route explanation that disagreed with the pools
    endpoint about whether a build can call tools would be the worst kind of
    diagnostic: two screens, both confident, one of them wrong.

    `record()` resolves the overlay by provenance rather than by arrival, so a
    measurement beats the protocol default and an operator's `CONFIGURED`
    override still beats the measurement. §9.5's ordering, unchanged.
    """
    known = {model: await adapter.capabilities(model) for model in models}
    if store is None:
        return known
    for model, capabilities in known.items():
        for claim in store.claims_for(model):
            capabilities.record(claim)
        # Only when RAVIS has none of its own. An operator's configured window
        # is a fact about their deployment and outranks a catalogue's
        # advertisement, the same way `CONFIGURED` outranks `MEASURED` above.
        if capabilities.context_window is None:
            ceiling = store.context_window(model)
            if ceiling is not None:
                capabilities.context_window = ceiling
    return known
