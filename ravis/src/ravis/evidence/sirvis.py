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
            f"threshold for {record.role}",
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
        f"measured by SIRVIS for {record.role}" + _measured_at(record),
        record,
    )


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
        self._records: dict[str, EvidenceRecord] = {}
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

    def record_for(self, runtime_key: str) -> EvidenceRecord | None:
        """The freshest record for one build, or None.

        A record older than the staleness window returns None *and* leaves the
        source degraded: §9.1 fails closed on what is not established, and a
        measurement whose window has passed is no longer establishing anything.
        """
        record = self._records.get(runtime_key)
        if record is None:
            return None
        if record.age_seconds is not None and record.age_seconds > self._max_age:
            return None
        return record

    def claims_for(self, runtime_key: str) -> list[CapabilityClaim]:
        """What this build's evidence establishes, as capability claims.

        Only claims that say something are returned. An UNKNOWN verdict records
        nothing rather than recording UNKNOWN, because `ModelCapabilities`
        already reads an absent claim as UNKNOWN — writing one would occupy the
        slot a better-sourced claim should win, and `record()` resolves by
        provenance rather than by arrival.
        """
        verdict = tool_verdict(self.record_for(runtime_key))
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
        verdict = tool_verdict(self.record_for(runtime_key))
        return {
            "source": self._state.value,
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
                params=[("role", self._role), *(("candidate", key) for key in candidates)],
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
        records: dict[str, EvidenceRecord] = {}
        for item in payload["items"]:
            record = _read_record(item, by_variant)
            if record is None:
                continue
            held = records.get(record.runtime_key)
            if held is None or _fresher(record, held):
                records[record.runtime_key] = record
        self._records = records
        self._read_at = self._clock()
        self._state = SourceState.FRESH
        self._detail = f"{len(records)} record(s) for role {self._role}"


def _fresher(candidate: EvidenceRecord, held: EvidenceRecord) -> bool:
    """Whether one record is newer than another, tolerating an unknown age."""
    if candidate.age_seconds is None:
        return False
    if held.age_seconds is None:
        return True
    return candidate.age_seconds < held.age_seconds


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
