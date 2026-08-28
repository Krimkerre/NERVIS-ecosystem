"""RAVIS M16's remaining half — the tiebreak that knows about reasoning overhead.

`max_tokens` is one budget shared between thinking and answering. A build that
spends most of it reasoning returns a fraction of the answer the caller asked
for, and sometimes returns none — which reaches the caller as an empty message
rather than as a routing decision, and is therefore invisible at exactly the
moment it matters.

The half of M16 that shipped applied policy. This half orders candidates that
policy already admitted, and the gate here is not "lower shares sort first". It
is that the term stays **silent** in every situation where RAVIS has not
established something: no budget, no measurement, an estimate rather than a
count, or a measurement past its staleness window. A tiebreak that fires on
absence would demote most of the catalogue, since most of it has never been
benchmarked on this machine.
"""

from __future__ import annotations

from typing import Any

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.requests import NormalizedRequest
from ravis.evidence.sirvis import EvidenceProvenance, EvidenceRecord
from ravis.policy import PrivacyLevel, RoutingPolicy
from ravis.routing.engine import RoutingEngine

THINKER = "vendor/a-thinks-a-lot"
ANSWERER = "vendor/z-answers"


def capable(model_id: str) -> ModelCapabilities:
    known = ModelCapabilities(model_id)
    for capability in Capability:
        known.record(CapabilityClaim(capability, CapabilityState.SUPPORTED, Provenance.MEASURED))
    known.context_window = 200_000
    return known


# Deliberately named so the thinker sorts *first* alphabetically. `ravis/auto`
# declares no preference, so without this milestone it ranks alphabetically —
# and a fixture whose thinker already sorted last would pass against its own
# bug.
CANDIDATES = {THINKER: capable(THINKER), ANSWERER: capable(ANSWERER)}

# For the privacy test, where the local build must be the *unattractive* one:
# it thinks, and it sorts second. See `test_it_cannot_move_a_request_off_this_machine`.
LOCAL_THINKER = "vendor/z-local-thinks"
REMOTE_ANSWERER = "vendor/a-remote-answers"
PRIVACY_CANDIDATES = {
    LOCAL_THINKER: capable(LOCAL_THINKER), REMOTE_ANSWERER: capable(REMOTE_ANSWERER),
}


def route(
    *,
    budget: int | None = 64,
    shares: dict[str, float] | None = None,
    pool: str = "ravis/auto",
    policy: RoutingPolicy | None = None,
    remote: frozenset[str] = frozenset(),
) -> Any:
    return RoutingEngine().select(
        pool,
        CANDIDATES,
        request=NormalizedRequest(messages=[{"role": "user", "content": "hi"}],
                                  max_output_tokens=budget),
        reasoning_share=shares,
        policy=policy,
        remote_models=remote,
    )


def measurement(median: float, *, kind: str = "MEASURED", direction: str = "lower") -> Any:
    """One `reasoning_token_share` in the shape SIRVIS publishes it."""
    return {
        "unit": "fraction", "direction": direction, "samples": 5, "median": median,
        "mean": median, "min": median, "max": median, "stddev": 0.0,
        "p10": None, "p90": None, "repetitions": [median] * 5,
        "provenance": {"kind": kind, "method": "sirvis.reasoning_share.v1"},
    }


def held(metrics: dict[str, Any], *, age: float = 60.0) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id="ev_1", runtime_key=THINKER, machine_id="m1", role="clarvis-agent",
        model_family="thinks", variant="var_1", model_format="gguf", quantization="Q4_K_M",
        runtime="lmstudio", runtime_configuration={}, suite="s", suite_version="1",
        provenance=EvidenceProvenance.MEASURED_BY_SIRVIS, validity="VALID",
        measured_at="2026-08-24T00:00:00", age_seconds=age, samples=5, metrics=metrics,
    )


# ── The measurement is read only when it establishes something ──────────────


def test_a_measured_share_is_read() -> None:
    assert held({"reasoning_token_share": measurement(0.68)}).measured_share(
        "reasoning_token_share"
    ) == 0.68


def test_an_estimated_share_establishes_nothing() -> None:
    """§13.3 — provenance is never upgraded, and M22b files ESTIMATED whenever
    the runtime hid its token counts. Ranking on it would be §9.4's invented
    measurement."""
    assert held(
        {"reasoning_token_share": measurement(0.68, kind="ESTIMATED")}
    ).measured_share("reasoning_token_share") is None


def test_a_partially_measured_share_establishes_nothing() -> None:
    assert held(
        {"reasoning_token_share": measurement(0.68, kind="PARTIALLY_MEASURED")}
    ).measured_share("reasoning_token_share") is None


def test_a_direction_that_is_not_lower_is_refused() -> None:
    """The field exists so a consumer can sort a number it did not produce.
    Reading a higher-is-better quantity as lower-is-better inverts the ranking
    with nothing visible going wrong, so a mismatch is dropped rather than used.
    """
    assert held(
        {"reasoning_token_share": measurement(0.68, direction="higher")}
    ).measured_share("reasoning_token_share") is None


def test_an_absent_measurement_is_absent_rather_than_zero() -> None:
    assert held({}).measured_share("reasoning_token_share") is None


# ── What it does to a route ─────────────────────────────────────────────────


def test_a_measured_thinker_ranks_below_an_answerer() -> None:
    """The M16 case. `ravis/auto` declares no preference, so the two are equal
    on everything else and the alphabetical order puts the thinker first."""
    assert route(shares={THINKER: 0.68}).selected == ANSWERER


def test_without_the_measurement_the_thinker_still_wins() -> None:
    """The same route, with the evidence taken away — so the test above is
    pinning the measurement rather than the fixture's spelling."""
    assert route(shares=None).selected == THINKER


def test_an_uncapped_request_is_not_reordered() -> None:
    """Nothing is being crowded out, so nothing is ranked. A pool that cares
    about the latency or the tokens says so through speed or price."""
    assert route(budget=None, shares={THINKER: 0.68}).selected == THINKER


def test_an_unmeasured_build_is_not_demoted_against_a_measured_zero() -> None:
    """Absence is not a verdict (§12.1). A build SIRVIS has never reached must
    tie with one measured never to think, not lose to it."""
    assert route(shares={ANSWERER: 0.0}).selected == THINKER


def test_two_measured_builds_are_ordered_by_how_much_they_think() -> None:
    """Both measured, both non-zero, and the winner is the one the alphabet puts
    second — so this pins the comparison rather than "any measurement loses"."""
    assert route(shares={THINKER: 0.9, ANSWERER: 0.4}).selected == ANSWERER


# ── What it must never do ──────────────────────────────────────────────────


def test_it_cannot_move_a_request_off_this_machine() -> None:
    """§14's rule 14: a privacy constraint is never overridden by score.

    `LOCAL_PREFERRED` is the one rung of the ladder that ranks instead of
    excluding, so it is the only one this term could out-vote. The fixture is
    built so it would: the local build is the thinker *and* sorts second, so
    both the alphabet and the tiebreak point off-device, and only the privacy
    term holds the request here. The control below proves it points that way.
    """
    decision = RoutingEngine().select(
        "ravis/auto", PRIVACY_CANDIDATES,
        request=NormalizedRequest(max_output_tokens=64),
        reasoning_share={LOCAL_THINKER: 0.9},
        policy=RoutingPolicy(privacy=PrivacyLevel.LOCAL_PREFERRED),
        remote_models=frozenset({REMOTE_ANSWERER}),
    )
    assert decision.selected == LOCAL_THINKER


def test_the_privacy_fixture_would_otherwise_route_away() -> None:
    """The control for the test above. Without the privacy level, the same
    inputs select the remote build — so the assertion there is load-bearing."""
    decision = RoutingEngine().select(
        "ravis/auto", PRIVACY_CANDIDATES,
        request=NormalizedRequest(max_output_tokens=64),
        reasoning_share={LOCAL_THINKER: 0.9},
        remote_models=frozenset({REMOTE_ANSWERER}),
    )
    assert decision.selected == REMOTE_ANSWERER


def test_it_cannot_admit_a_build_the_pool_excluded() -> None:
    """Ranking runs on what eligibility already allowed. A pool that admits one
    candidate gets that candidate, whatever its share."""
    decision = route(shares={THINKER: 0.0, ANSWERER: 0.9},
                     pool="ravis/local", remote=frozenset({THINKER}))
    assert decision.selected == ANSWERER


# ── And says so ────────────────────────────────────────────────────────────


def test_the_explanation_names_the_tiebreak_and_shows_the_arithmetic() -> None:
    """§9.4 — no opaque magic. A reader who disagrees with the tiebreak has to
    be able to see it happening, and the fraction alone is not a reason."""
    reason = route(shares={THINKER: 0.68}).reason
    assert THINKER in reason
    assert "68%" in reason
    assert "max_tokens=64" in reason
    assert "20 tokens for the answer" in reason


def test_the_explanation_stays_quiet_when_nothing_was_reordered() -> None:
    """A note on every capped request trains a reader to skip the line that
    matters."""
    assert "reasoning" not in route(shares={ANSWERER: 0.0}).reason


def test_the_explanation_no_longer_claims_evidence_never_orders() -> None:
    """The sentence said "evidence decides eligibility rather than order", which
    this milestone makes false. The claim that survives is the narrower one:
    nothing ranks an admitted build above another *on quality*."""
    reason = route(shares={THINKER: 0.68}).reason
    assert "eligibility rather than order" not in reason
    assert "rather than on quality" in reason
