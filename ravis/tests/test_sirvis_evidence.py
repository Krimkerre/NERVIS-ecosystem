"""M13 — consuming SIRVIS evidence (§13), and §13.4's pairwise gate.

    real SIRVIS measured, estimated, unknown, stale, tombstoned, runtime-down
    and unsupported-major fixtures produce deterministic route effects.

Seven fixtures, seven sections. The gate is not "evidence works" — it is that
each *kind of absence* produces its own answer, because the failure this
prevents is a router treating "not measured", "measured and failed" and "the
measurement service is down" as one thing.

The fixtures are the real shape: they were captured from the two runs this
machine actually has, where two packagings of one model score 8/8 and 1/8 on
the same trial.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from ecosystem_protocol import PROTOCOL_VERSION

from ravis.core.capabilities import Capability, CapabilityState, ModelCapabilities, Provenance
from ravis.evidence.sirvis import (
    MIN_PHRASINGS,
    MIN_REPETITIONS,
    EvidenceProvenance,
    EvidenceStore,
    SourceState,
    candidates_with_evidence,
    tool_verdict,
)

GGUF = "lmstudio-community/granite-4.0-h-tiny"
MLX = "mlx-community/granite-4.0-h-tiny"


def record(
    *,
    runtime_key: str = GGUF,
    variant: str = "var_gguf",
    passed: int = 24,
    total: int = 24,
    phrasings: int = 8,
    kind: str = "MEASURED",
    age: float = 60.0,
    version: str = "0.0.1",
    fmt: str = "gguf",
    outcomes: dict[str, int] | None = None,
) -> dict[str, Any]:
    """One evidence item in the shape `/api/v1/evidence` actually returns."""
    del runtime_key
    return {
        "evidence_id": f"ev_{variant}",
        "machine_id": "machine-1",
        "role": "clarvis-agent",
        "target": {
            "model_family": "granite-4.0-h-tiny", "variant": variant,
            "format": fmt, "quantization": "Q4_K_M", "runtime": "lmstudio",
            "runtime_config": {"context_length": 8192},
        },
        "suite": {"id": "clarvis-role-clarvis-agent", "version": "1"},
        "evidence_type": kind,
        "samples": 6,
        "metrics": {
            "tool_call_well_formed": {
                "passed": passed, "total": total, "rate": passed / total,
                "phrasings": phrasings, "repetitions": total // max(1, phrasings),
                # Absent unless a test asks for it: records written before the
                # tally existed carry none, and the reader has to cope.
                **({"outcomes": outcomes} if outcomes else {}),
                "provenance": {"kind": kind, "method": "clarvis.tool_call.streamed.v1"},
            }
        },
        "validity": "VALID",
        "measured_at": "2026-08-24T00:00:00",
        "age_seconds": age,
        "evidence_ref": f"sirvis://evidence/ev_{variant}/res_1",
        "sirvis_version": version,
    }


def payload(*items: dict[str, Any], variants: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "items": list(items),
        "next_cursor": None,
        "snapshot_revision": 1,
        "resolved_candidates": sorted(variants.values()) if variants else [],
        "unresolved_candidates": [],
        "candidate_variants": variants or {GGUF: "var_gguf", MLX: "var_mlx"},
        "tombstones": [],
    }


# A minimal MEP surface, so every store in this file negotiates for real before
# it reads. Serving the evidence payload from `/ecosystem/*` too would have let
# negotiation pass by accident on its tolerant defaults, which is the opposite
# of what these tests are for.
MEP_VERSION = {"protocol_version": PROTOCOL_VERSION}
MEP_CAPABILITIES = {
    "revision": 1,
    "capabilities": [
        {"id": "sirvis.benchmarks.results", "version": "1.0.0", "state": "available", "reason": ""}
    ],
}


def mep_surface(*, version: Any = None, capabilities: Any = None) -> dict[str, Any]:
    """The two MEP bodies a store reads before it will look at evidence."""
    return {
        "/ecosystem/version": MEP_VERSION if version is None else version,
        "/ecosystem/capabilities": MEP_CAPABILITIES if capabilities is None else capabilities,
    }


def store_with(
    response: Any, *, status: int = 200, surface: dict[str, Any] | None = None, **options: Any
) -> EvidenceStore:
    """A store that has read one canned SIRVIS response."""
    import asyncio

    mep = mep_surface() if surface is None else surface

    # `candidate=` rather than `role=` marks the evidence read. RAVIS asked for
    # one role and now asks for none — the role travels on each record, so
    # filtering the request threw away the field that makes the answer usable.
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path in mep:
            return httpx.Response(200, json=mep[request.url.path])
        if isinstance(response, str):
            return httpx.Response(status, content=response)
        return httpx.Response(status, json=response)

    store = EvidenceStore(base_url="http://sirvis.invalid", **options)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    asyncio.run(store.refresh(client, [GGUF, MLX]))
    return store


# ── 1. Measured: the fixture that admits a build ─────────────────────────────


def test_measured_evidence_above_the_threshold_supports_the_capability() -> None:
    """24 of 24 over 8 phrasings — §13.2's threshold on both axes."""
    store = store_with(payload(record(), variants={GGUF: "var_gguf"}))

    claims = store.claims_for(GGUF)

    assert len(claims) == 1
    assert claims[0].capability is Capability.TOOLS
    assert claims[0].state is CapabilityState.SUPPORTED
    # §9.5's ordering: a measurement beats an advertisement and a protocol
    # default, and is still out-ranked by an operator's own declaration.
    assert claims[0].provenance is Provenance.MEASURED


def test_the_claim_says_what_it_was_measured_over() -> None:
    store = store_with(payload(record(), variants={GGUF: "var_gguf"}))

    detail = store.claims_for(GGUF)[0].detail

    assert "24/24" in detail and "8 phrasings" in detail


# ── 2. Measured and failing: the fixture that excludes one ───────────────────


def test_a_build_below_the_rate_is_unsupported_not_unknown() -> None:
    """1 of 8 is a measurement, and it says the build cannot do this.

    `UNSUPPORTED` rather than `UNKNOWN` because the difference is real:
    UNKNOWN can be resolved by measuring, and this *was* measured.
    """
    store = store_with(payload(
        record(variant="var_mlx", passed=1, total=24, fmt="mlx"),
        variants={MLX: "var_mlx"},
    ))

    claims = store.claims_for(MLX)

    assert claims[0].state is CapabilityState.UNSUPPORTED
    assert "below the 95% threshold" in claims[0].detail


def test_two_packagings_of_one_model_get_opposite_verdicts() -> None:
    """The case §13.1 exists for, and this machine's actual measurements.

    Same family, same parameter count, same architecture. Nothing observable
    from the name distinguishes them and the tool-call trial does: 8/8 against
    1/8. A router keyed on model name routes an agent to the wrong one.
    """
    store = store_with(payload(
        record(variant="var_gguf"),
        record(variant="var_mlx", passed=1, total=24, fmt="mlx"),
        variants={GGUF: "var_gguf", MLX: "var_mlx"},
    ))

    assert store.claims_for(GGUF)[0].state is CapabilityState.SUPPORTED
    assert store.claims_for(MLX)[0].state is CapabilityState.UNSUPPORTED


# ── 3. The sample axis: clears the rate, fails the threshold ─────────────────


def test_a_perfect_rate_over_too_few_attempts_establishes_nothing() -> None:
    """§13.2 is two axes, and this is the one a naive reading drops.

    8 of 8 is a 100% pass rate over 8 attempts. The threshold requires 8
    phrasings **times 3 repetitions**. So this build has cleared one axis of a
    two-axis bar, and admitting it would route on a sample the specification
    says is too small to conclude from.

    It is the exact shape of the first role run made on this machine, which is
    why it is a test rather than a hypothetical.
    """
    store = store_with(payload(
        record(passed=8, total=8, phrasings=8), variants={GGUF: "var_gguf"}
    ))

    verdict = tool_verdict(store.record_for(GGUF))

    assert verdict.state is CapabilityState.UNKNOWN
    assert "clears the rate but not the sample" in verdict.detail
    assert f"{MIN_PHRASINGS} phrasings × {MIN_REPETITIONS} repetitions" in verdict.detail
    # UNKNOWN records no claim at all, so a better-sourced one can still win.
    assert store.claims_for(GGUF) == []


def test_one_phrasing_repeated_enough_times_is_still_not_enough() -> None:
    """The failure the phrasing axis was added to catch.

    An earlier threshold said ">= 50 attempts" and was wrong in kind: a build
    can pass one phrasing fifty times while losing the filename on seven of the
    eight ways a person actually asks.
    """
    store = store_with(payload(
        record(passed=50, total=50, phrasings=1), variants={GGUF: "var_gguf"}
    ))

    assert tool_verdict(store.record_for(GGUF)).state is CapabilityState.UNKNOWN


# ── 4. Estimated: provenance is never upgraded ───────────────────────────────


def test_an_estimate_cannot_establish_a_capability() -> None:
    """§13.3: never upgrade provenance.

    A perfect rate from an estimate is still an estimate, and §9.1 fails closed
    on what is not established.
    """
    store = store_with(payload(
        record(kind="ESTIMATED"), variants={GGUF: "var_gguf"}
    ))

    verdict = tool_verdict(store.record_for(GGUF))

    assert verdict.state is CapabilityState.UNKNOWN
    assert "ESTIMATED" in verdict.detail
    assert store.claims_for(GGUF) == []


def test_partially_measured_maps_to_estimated() -> None:
    """§13.3 names this mapping explicitly."""
    store = store_with(payload(
        record(kind="PARTIALLY_MEASURED"), variants={GGUF: "var_gguf"}
    ))

    assert store.record_for(GGUF).provenance is EvidenceProvenance.ESTIMATED  # type: ignore[union-attr]
    assert store.claims_for(GGUF) == []


# ── 5. Unknown: never measured at all ────────────────────────────────────────


def test_a_build_with_no_evidence_records_nothing() -> None:
    """Absent, not zero. Nobody asked, so nothing is claimed either way."""
    store = store_with(payload(record(), variants={GGUF: "var_gguf"}))

    assert store.claims_for(MLX) == []
    assert tool_verdict(store.record_for(MLX)).detail == "no SIRVIS evidence for this build"


# ── 6. Stale: measured, but too long ago ─────────────────────────────────────


def test_evidence_past_its_window_stops_establishing_anything() -> None:
    """§13.3's staleness policy, failing closed.

    A measurement whose window has passed is not a measurement that says no —
    it is one that has stopped saying anything, and §9.1 fails closed on that.
    """
    store = store_with(
        payload(record(age=90 * 24 * 3600), variants={GGUF: "var_gguf"}),
        max_age_seconds=30 * 24 * 3600,
    )

    assert store.record_for(GGUF) is None
    assert store.claims_for(GGUF) == []


def test_evidence_inside_the_window_is_still_believed() -> None:
    store = store_with(
        payload(record(age=10 * 24 * 3600), variants={GGUF: "var_gguf"}),
        max_age_seconds=30 * 24 * 3600,
    )

    assert store.claims_for(GGUF)[0].state is CapabilityState.SUPPORTED


# ── 7. Runtime down, and an unsupported major ────────────────────────────────


def test_a_sirvis_that_will_not_answer_degrades_the_source_and_drops_claims() -> None:
    """§13.4: RAVIS keeps routing, with the degradation labelled.

    Claims are dropped rather than served on. Serving the last read after the
    service that owns it stopped answering is the one thing §13.3 forbids
    outright — a cached measurement presented as a current one.
    """
    store = store_with("upstream is on fire", status=503)

    assert store.state is SourceState.DEGRADED
    assert "did not answer" in store.detail
    assert store.claims_for(GGUF) == []


def test_an_unreadable_shape_degrades_rather_than_raising() -> None:
    store = store_with({"unexpected": "shape"})

    assert store.state is SourceState.DEGRADED
    assert "cannot read" in store.detail


def test_an_unsupported_major_is_refused_rather_than_parsed_optimistically() -> None:
    """§13.4's fixture. The fields might line up; that is not a reason to guess.

    A route decided on a shape nobody verified is the failure this prevents.
    """
    store = store_with(payload(record(version="9.0.0"), variants={GGUF: "var_gguf"}))

    assert store.record_for(GGUF) is None
    assert store.claims_for(GGUF) == []


def test_an_unconfigured_sirvis_is_absent_rather_than_degraded() -> None:
    """§13.4's optionality: no SIRVIS is a deployment, not a fault."""
    store = EvidenceStore(base_url="")

    assert store.state is SourceState.ABSENT
    assert store.claims_for(GGUF) == []


# ── The overlay: what routing actually sees ──────────────────────────────────


class FakeAdapter:
    """An adapter that knows nothing, which is the honest default (M3a)."""

    async def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(model_id=model)


def test_the_overlay_lands_on_the_candidate_set() -> None:
    """The one function both call sites use, so they cannot disagree."""
    import asyncio

    store = store_with(payload(
        record(variant="var_gguf"),
        record(variant="var_mlx", passed=1, total=24, fmt="mlx"),
        variants={GGUF: "var_gguf", MLX: "var_mlx"},
    ))

    known = asyncio.run(candidates_with_evidence(FakeAdapter(), [GGUF, MLX], store))

    assert known[GGUF].satisfies(Capability.TOOLS)
    assert not known[MLX].satisfies(Capability.TOOLS)


def test_an_operator_declaration_still_outranks_a_measurement() -> None:
    """§9.5's ordering survives M13.

    An operator knows things about their deployment RAVIS cannot observe, and
    silently out-voting them with a measurement would be the wrong kind of
    clever — in either direction.
    """
    import asyncio

    class Configured(FakeAdapter):
        async def capabilities(self, model: str) -> ModelCapabilities:
            known = ModelCapabilities(model_id=model)
            known.record(_configured_claim(Capability.TOOLS, CapabilityState.SUPPORTED))
            return known

    store = store_with(payload(
        record(variant="var_mlx", passed=1, total=24, fmt="mlx"), variants={MLX: "var_mlx"}
    ))

    known = asyncio.run(candidates_with_evidence(Configured(), [MLX], store))

    assert known[MLX].satisfies(Capability.TOOLS), "the operator's declaration wins"


def _configured_claim(capability: Capability, state: CapabilityState) -> Any:
    from ravis.core.capabilities import CapabilityClaim

    return CapabilityClaim(
        capability=capability, state=state,
        provenance=Provenance.CONFIGURED, detail="declared in configuration",
    )


def test_no_response_field_anywhere_is_a_score() -> None:
    """§13.1: never reduce SIRVIS results to model → score.

    Structural rather than stylistic — there must be nowhere for one to live.
    """
    store = store_with(payload(record(), variants={GGUF: "var_gguf"}))

    rendered = repr(store.explain(GGUF)) + repr(store.snapshot())

    for forbidden in ("'score'", "'rank'", "'rating'"):
        assert forbidden not in rendered


@pytest.mark.parametrize("state", [SourceState.FRESH, SourceState.DEGRADED, SourceState.ABSENT])
def test_the_source_state_is_always_reportable(state: SourceState) -> None:
    """A diagnostic must be able to say which of the three it is."""
    assert state.value in {"fresh", "degraded", "absent"}


# ── Context ceilings: the second invariant a pool declares ───────────────────


def inventory(**ceilings: int) -> dict[str, Any]:
    return {"items": [
        {"runtime_key": key,
         "declared_context": {"value": value, "provenance": "DECLARED",
                              "detail": "the build's advertised maximum"}}
        for key, value in ceilings.items()
    ]}


def store_with_both(evidence: dict[str, Any], models: dict[str, Any]) -> EvidenceStore:
    """A store that has read both SIRVIS surfaces."""
    import asyncio

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=models)
        return httpx.Response(200, json=evidence)

    store = EvidenceStore(base_url="http://sirvis.invalid")
    asyncio.run(store.refresh(
        httpx.AsyncClient(transport=httpx.MockTransport(handle)), [GGUF, MLX]
    ))
    return store


def test_a_declared_ceiling_reaches_the_candidate() -> None:
    """§13 lists model fit among what RAVIS asks SIRVIS for.

    Without it a pool declaring a minimum context fails closed on every
    candidate — correct, and useless.
    """
    import asyncio

    store = store_with_both(
        payload(record(), variants={GGUF: "var_gguf"}),
        inventory(**{GGUF: 1048576}),
    )

    known = asyncio.run(candidates_with_evidence(FakeAdapter(), [GGUF], store))

    assert known[GGUF].meets_context(32768)


def test_the_claim_says_what_context_the_trial_ran_at() -> None:
    """§12.2 keys evidence on the configuration it was produced under.

    A pool may require more context than the trial used, and admitting on that
    evidence is a small inference — kept visible rather than hidden.
    """
    store = store_with_both(
        payload(record(), variants={GGUF: "var_gguf"}), inventory(**{GGUF: 1048576})
    )

    assert "at context_length 8192" in store.claims_for(GGUF)[0].detail


def test_an_absent_inventory_leaves_the_ceiling_unknown_rather_than_zero() -> None:
    """Knowing the tools answer and not the context one beats knowing neither."""
    store = store_with(payload(record(), variants={GGUF: "var_gguf"}))

    assert store.context_window(GGUF) is None
    assert store.claims_for(GGUF)[0].state is CapabilityState.SUPPORTED


# ── Negotiation: what RAVIS checks before it believes anything ───────────────


def test_evidence_is_read_when_sirvis_advertises_the_capability() -> None:
    """The happy path, and the one every other test in this file runs through."""
    store = store_with(payload(record(), variants={GGUF: "var_gguf"}))

    assert store.state is SourceState.FRESH


def test_a_withdrawn_capability_stops_evidence_being_read() -> None:
    """A comment claimed this for two milestones and nothing implemented it.

    §4.1 exists so a peer can say "not this, not yet". Reading `/api/v1/evidence`
    from a service that declares the evidence surface unavailable would make the
    capability list decorative — and §13.3 forbids presenting whatever came back
    as a measurement.
    """
    store = store_with(
        payload(record(), variants={GGUF: "var_gguf"}),
        surface=mep_surface(
            capabilities={
                "revision": 4,
                "capabilities": [
                    {
                        "id": "sirvis.benchmarks.results",
                        "version": "1.0.0",
                        "state": "unavailable",
                        "reason": "results database is being rebuilt",
                    }
                ],
            }
        ),
    )

    assert store.state is SourceState.DEGRADED
    assert "results database is being rebuilt" in store.detail
    assert store.record_for(GGUF) is None


def test_a_capability_that_is_not_advertised_at_all_is_refused() -> None:
    """Silence is not consent. §4.1: absent means absent, not assume-it-works."""
    store = store_with(
        payload(record(), variants={GGUF: "var_gguf"}),
        surface=mep_surface(capabilities={"revision": 1, "capabilities": []}),
    )

    assert store.state is SourceState.DEGRADED
    assert "does not advertise" in store.detail


def test_an_unsupported_protocol_major_is_refused_but_never_fatal() -> None:
    """Both halves of the rule at once.

    §4.2 requires a consumer to reject an unsupported major structurally, and
    §13.4 requires SIRVIS being unusable never to stop RAVIS routing. So the
    refusal is raised as `UnsupportedProtocolVersionError` where it is detected
    and lands here as a degraded source rather than an exception.
    """
    store = store_with(
        payload(record(), variants={GGUF: "var_gguf"}),
        surface=mep_surface(version={"protocol_version": "99.0.0"}),
    )

    assert store.state is SourceState.DEGRADED
    assert "protocol 99.0.0" in store.detail
    assert store.record_for(GGUF) is None


def test_a_peer_with_no_mep_surface_is_still_read() -> None:
    """A 404 on `/ecosystem/*` is a build older than the shared package.

    Refusing there would make evidence mandatory in a deployment that upgrades
    RAVIS first, and §13.4 makes it optional. Answering-and-saying-no is the
    only refusal; not answering is not one.
    """
    import asyncio

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/ecosystem/"):
            return httpx.Response(404, json={"detail": "Not Found"})
        return httpx.Response(200, json=payload(record(), variants={GGUF: "var_gguf"}))

    store = EvidenceStore(base_url="http://sirvis.invalid")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    asyncio.run(store.refresh(client, [GGUF, MLX]))

    assert store.state is SourceState.FRESH


def test_evidence_is_requested_for_every_upstream_not_just_the_first() -> None:
    """`model_registry` is the *first* declared upstream's catalogue.

    With the plural upstreams M8 shipped, `_refresh_evidence` asked SIRVIS only
    about that one, so builds served by the second upstream routed on advertised
    capability alone while RAVIS held measured evidence for their neighbours.
    The Evidence screen reported the source healthy throughout, so the gap
    looked like SIRVIS having nothing to say about those models.
    """
    import asyncio

    from ravis.app import _refresh_evidence

    asked: list[list[str]] = []

    class _Store:
        is_configured = True

        async def refresh(self, _: object, models: list[str]) -> None:
            asked.append(list(models))

    class _Registry:
        def __init__(self, models: list[str]) -> None:
            self._models = models

        def model_ids(self) -> list[str]:
            return list(self._models)

    class _Built:
        def __init__(self, registry: _Registry) -> None:
            self.registry = registry

    class _State:
        evidence = _Store()
        upstream_client = object()
        model_registry = _Registry(["first-upstream-build"])
        transparents = {
            "default": _Built(_Registry(["first-upstream-build"])),
            "second": _Built(_Registry(["second-upstream-build"])),
        }

    api = type("_App", (), {"state": _State()})()
    asyncio.run(_refresh_evidence(api))  # type: ignore[arg-type]

    assert asked == [["first-upstream-build", "second-upstream-build"]], (
        "both upstreams' builds, sorted so two refreshes can be compared"
    )


def test_ravis_can_read_sirvis_reasoning_share() -> None:
    """SIRVIS M22b's exit: RAVIS M16's tiebreak can read the measurement.

    `ravis/auto` breaks a tie on smallest-build-is-cheapest, which selects a
    reasoning distill that spends most of a small budget thinking. RAVIS cannot
    know that from advertised metadata, so M22b measures it per build and files
    it as evidence. This asserts the wire between the two: the metric survives
    SIRVIS's serialisation and arrives in the record RAVIS holds, named and
    with its provenance intact.

    Asserted here rather than only in SIRVIS because a measurement that reaches
    the producer's own record and not the consumer's is a milestone that looks
    finished from one side.
    """
    from ravis.evidence.sirvis import _read_record

    # The shape SIRVIS actually serialises, through RAVIS's own reader.
    record = _read_record(
        {
            "evidence_id": "ev_1",
            "sirvis_version": "0.14.0",
            "machine_id": "m1",
            "role": "clarvis-chat",
            "evidence_type": "MEASURED",
            "samples": 3,
            "target": {"variant": "var_mlx", "model_family": "qwen3-1.7b",
                       "runtime": "mlx", "runtime_config": {"context_length": 8192}},
            "suite": {"id": "performance-basic", "version": "1"},
            "metrics": {
                "reasoning_token_share": {
                    "unit": "fraction", "direction": "lower", "samples": 3,
                    "median": 0.8, "provenance": {"kind": "MEASURED"},
                }
            },
        },
        {"var_mlx": "qwen3-1.7b@mlx"},
    )

    assert record is not None
    share = record.metrics.get("reasoning_token_share")

    assert share is not None, "the tiebreak needs this metric by name"
    assert share["median"] == 0.8
    assert share["direction"] == "lower", "less budget lost to thinking is better"


# ── The share is not a role-specific fact (found by a real benchmark) ────────


def test_a_reasoning_share_measured_under_another_role_is_still_read() -> None:
    """M16's tiebreak was dead in practice, and only a real run showed it.

    A benchmark of `deepseek-r1-distill-qwen-1.5b` measured
    `reasoning_token_share` at 0.992 — five repetitions that produced no content
    at all, which is precisely the build the tiebreak exists to demote. It did
    not fire. The run recorded `role: general`, because the specification
    measures raw throughput rather than fitness for a Clarvis role, while this
    store asks SIRVIS for `clarvis-agent` and nothing else.

    §12.5 scopes verdicts to a build *and a role* because fitness is
    role-specific. How much of its output a build spends thinking before it
    answers is not that kind of fact — it is the same whichever role asked.

    **This needed a second request and no longer does.** The workaround was a
    role-agnostic read alongside the role-scoped one; the read is role-agnostic
    now, so one response carries every role's records and the share is picked
    out of the same walk. What the test asserts is unchanged and slightly
    stronger: a share measured under `general` reaches a store that used to ask
    only about `clarvis-agent`.
    """
    import asyncio

    both_roles = {
        # The clarvis-agent record this store always saw, and beside it the
        # `general` run that carries the share — one response, as SIRVIS
        # actually answers when nobody filters by role.
        "items": [
            record(kind="MEASURED"),
            record(variant="var_gguf") | {"role": "general", "metrics": {
            "reasoning_token_share": {
                "unit": "fraction", "direction": "lower", "samples": 5,
                "median": 0.992,
                "provenance": {"kind": "MEASURED", "method": "sirvis.reasoning.v1"},
            }
        }}],
        "next_cursor": None, "snapshot_revision": 1,
        "resolved_candidates": ["var_gguf"], "unresolved_candidates": [],
        "candidate_variants": {GGUF: "var_gguf"}, "tombstones": [],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        mep = mep_surface()
        if request.url.path in mep:
            return httpx.Response(200, json=mep[request.url.path])
        return httpx.Response(200, json=both_roles)

    store = EvidenceStore(base_url="http://sirvis.invalid")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    asyncio.run(store.refresh(client, [GGUF, MLX]))

    assert store.reasoning_share(GGUF) == 0.992


def test_an_estimated_share_under_another_role_still_establishes_nothing() -> None:
    """Widening the lookup must not widen what counts as measured. §13.3 forbids
    upgrading provenance, and reading across roles is not a reason to relax it.
    """
    import asyncio

    estimated = {
        "items": [record(variant="var_gguf") | {"role": "general", "metrics": {
            "reasoning_token_share": {
                "unit": "fraction", "direction": "lower", "samples": 1,
                "median": 0.7,
                "provenance": {"kind": "ESTIMATED", "method": "x"},
            }
        }}],
        "next_cursor": None, "snapshot_revision": 1,
        "resolved_candidates": ["var_gguf"], "unresolved_candidates": [],
        "candidate_variants": {GGUF: "var_gguf"}, "tombstones": [],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        mep = mep_surface()
        if request.url.path in mep:
            return httpx.Response(200, json=mep[request.url.path])
        if "candidate=" in str(request.url):
            return httpx.Response(200, json=payload(record()))
        return httpx.Response(200, json=estimated)

    store = EvidenceStore(base_url="http://sirvis.invalid")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    asyncio.run(store.refresh(client, [GGUF]))

    assert store.reasoning_share(GGUF) is None


def test_a_sirvis_that_will_not_answer_the_second_read_is_not_degraded() -> None:
    """The share is a ranking hint; eligibility does not depend on it. Marking
    the source degraded because an optional second read failed would be the tail
    wagging the dog."""
    import asyncio

    def handle(request: httpx.Request) -> httpx.Response:
        mep = mep_surface()
        if request.url.path in mep:
            return httpx.Response(200, json=mep[request.url.path])
        if "candidate=" in str(request.url):
            return httpx.Response(200, json=payload(record()))
        return httpx.Response(500, json={"error": "no"})

    store = EvidenceStore(base_url="http://sirvis.invalid")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    asyncio.run(store.refresh(client, [GGUF]))

    assert store.state is SourceState.FRESH
    assert store.reasoning_share(GGUF) is None


def test_a_refusal_names_the_failure_it_saw() -> None:
    """"3/24 well-formed tool calls" sends a reader looking for a better model.
    "3/24 — 21 of them lost-arguments" tells them the build calls the tool and
    drops the filename, which is a quantisation or template problem and may have
    a fix. Measured on this machine: the two granite builds differ exactly so.
    """
    store = store_with(payload(
        record(passed=3, total=24, outcomes={"lost-arguments": 21, "used-result": 3}),
        variants={GGUF: "var_gguf"},
    ))

    verdict = tool_verdict(store.record_for(GGUF))

    assert verdict.state is CapabilityState.UNSUPPORTED
    assert "21 of them lost-arguments" in verdict.detail


def test_success_is_never_reported_as_the_complaint() -> None:
    """`used-result` is the one outcome that is not a failure."""
    store = store_with(payload(
        record(passed=3, total=24, outcomes={"used-result": 3, "no-call": 21}),
        variants={GGUF: "var_gguf"},
    ))

    verdict = tool_verdict(store.record_for(GGUF))

    assert "no-call" in verdict.detail and "used-result" not in verdict.detail


def test_a_record_without_a_tally_says_only_what_it_knows() -> None:
    """Older records carry none. Inventing a cause is worse than a bare rate."""
    store = store_with(payload(record(passed=3, total=24), variants={GGUF: "var_gguf"}))

    verdict = tool_verdict(store.record_for(GGUF))

    assert verdict.state is CapabilityState.UNSUPPORTED
    assert "of them" not in verdict.detail


# ── Every role, and a trial that belongs to the build ────────────────────────


def test_a_tool_trial_is_read_whichever_role_filed_it() -> None:
    """§13.1 defines a trial as a pass rate over phrasings and repetitions of one
    request. Nothing in that is specific to Clarvis's agent workload — the suite
    that produced every trial on this machine ran under `clarvis-agent` only
    because that was the flag that existed.

    A later record with no trial in it must not shadow one that has it, which is
    what "the freshest record" did: a throughput run under `general` hid the
    trial underneath it.
    """
    import asyncio

    both = {
        "items": [
            # Newest first, as the API returns them: a throughput run with no
            # trial, then the trial itself.
            record(variant="var_gguf") | {"role": "general", "metrics": {}, "rates": {}},
            record(kind="MEASURED"),
        ],
        "next_cursor": None, "snapshot_revision": 1,
        "resolved_candidates": ["var_gguf"], "unresolved_candidates": [],
        "candidate_variants": {GGUF: "var_gguf"}, "tombstones": [],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        mep = mep_surface()
        if request.url.path in mep:
            return httpx.Response(200, json=mep[request.url.path])
        return httpx.Response(200, json=both)

    store = EvidenceStore(base_url="http://sirvis.invalid")
    asyncio.run(store.refresh(httpx.AsyncClient(transport=httpx.MockTransport(handle)), [GGUF]))

    assert store.trial_record_for(GGUF) is not None
    assert store.claims_for(GGUF), "the trial establishes a capability claim"


def test_which_roles_a_build_has_been_measured_for_is_reported() -> None:
    """Derived rather than declared: the answer to *what is this build qualified
    for* comes from the roles it was actually run under.

    A role measured on some other axis reports UNKNOWN rather than a pass — it
    was covered, and nothing about its fitness was established.
    """
    import asyncio

    both = {
        "items": [
            record(kind="MEASURED"),
            record(variant="var_gguf") | {"role": "chat", "metrics": {}, "rates": {}},
        ],
        "next_cursor": None, "snapshot_revision": 1,
        "resolved_candidates": ["var_gguf"], "unresolved_candidates": [],
        "candidate_variants": {GGUF: "var_gguf"}, "tombstones": [],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        mep = mep_surface()
        if request.url.path in mep:
            return httpx.Response(200, json=mep[request.url.path])
        return httpx.Response(200, json=both)

    store = EvidenceStore(base_url="http://sirvis.invalid")
    asyncio.run(store.refresh(httpx.AsyncClient(transport=httpx.MockTransport(handle)), [GGUF]))

    fit = store.roles_measured(GGUF)

    assert fit["clarvis-agent"] == "SUPPORTED"
    assert fit["chat"] == "UNKNOWN", "measured for the role, silent about fitness"
    assert "general" not in fit, "a role nobody ran does not appear"


# ── 8. The share the staleness window could not reach ────────────────────────


def _with_share(age: float, share: float = 0.9) -> dict[str, Any]:
    """One item carrying a reasoning share, aged as the caller asks."""
    item = record(age=age)
    # The shape `_measured_share` accepts: a median, `direction: "lower"` so a
    # ranking cannot be inverted silently, and MEASURED provenance — §13.3
    # forbids reading an inferred share as an established one.
    item["metrics"]["reasoning_token_share"] = {
        "median": share,
        "direction": "lower",
        "samples": 5,
        "provenance": {"kind": "MEASURED", "method": "sirvis.reasoning_share.v1"},
    }
    return item


def test_a_reasoning_share_past_the_window_stops_being_believed() -> None:
    """**The last line of `reasoning_share` was the exception to its own
    docstring.** Found by the external audit.

    That docstring says the staleness window applies "exactly as it does to
    capability claims", and the method did go through `record_for` first — which
    correctly returns None for an aged-out record. Then it fell through to
    `_shares`, a flat map of build to number built in the same walk and carrying
    nothing but the value. So a build whose every record had expired handed back
    its stale share anyway, and routing ordered candidates on a measurement the
    same object was simultaneously refusing to admit.
    """
    store = store_with(
        payload(_with_share(age=90 * 24 * 3600), variants={GGUF: "var_gguf"}),
        max_age_seconds=30 * 24 * 3600,
    )

    assert store.record_for(GGUF) is None, "the fixture is wrong — the record must be stale"
    assert store.reasoning_share(GGUF) is None, (
        "an expired reasoning share is still ordering candidates"
    )


def test_a_share_inside_the_window_is_still_believed() -> None:
    """The guard on the guard: refusing every share would pass the test above
    and quietly remove the tiebreak M16 exists for."""
    store = store_with(
        payload(_with_share(age=10 * 24 * 3600), variants={GGUF: "var_gguf"}),
        max_age_seconds=30 * 24 * 3600,
    )

    assert store.reasoning_share(GGUF) == 0.9


def test_an_expired_share_says_it_was_the_share_that_expired() -> None:
    """Silence is the half that made this hard to see: `record_for` degrades on
    expiry precisely so a route explanation cannot report a healthy source
    beside a missing measurement, and the fallback reported neither.

    **Asserted on the wording rather than the state, because the state proves
    nothing here.** `record_for` runs first and has already degraded the source
    by the time the fallback is reached — a probe that removed the fallback's
    own degradation failed nothing at all. Only the detail line distinguishes
    them: one names the newest *record*, the other names the *share*, and an
    operator reading it needs to know which measurement went stale.
    """
    store = store_with(
        payload(_with_share(age=90 * 24 * 3600), variants={GGUF: "var_gguf"}),
        max_age_seconds=30 * 24 * 3600,
    )

    store.reasoning_share(GGUF)

    assert store.state is SourceState.DEGRADED
    assert "reasoning share" in store.detail, (
        "the source degraded without saying which measurement expired"
    )
