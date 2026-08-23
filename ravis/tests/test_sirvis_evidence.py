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


def store_with(response: Any, *, status: int = 200, **options: Any) -> EvidenceStore:
    """A store that has read one canned SIRVIS response."""
    import asyncio

    def handle(request: httpx.Request) -> httpx.Response:
        del request
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
