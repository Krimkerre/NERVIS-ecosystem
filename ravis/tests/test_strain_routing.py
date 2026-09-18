"""A provider's own words about its limits, used to order candidates (§12.3).

M20 counted requests in flight, read providers' rate-limit headers and published
the lot, and changed no route with any of it — which the base review named: *an
existing load display is not a scheduler*. On 18 September 2026 the owner chose
the small half of the fix, a penalty inside the ranking that already exists
rather than a queue in front of the providers. These tests are what that penalty
is allowed to do, and what it must never do.
"""

from __future__ import annotations

from ravis.core.capabilities import ModelCapabilities
from ravis.policy import PrivacyLevel, RoutingPolicy
from ravis.reliability.failures import FailureClass
from ravis.reliability.health import HealthRegistry
from ravis.reliability.load import LoadTracker
from ravis.reliability.strain import BUSY, CLEAR, REFUSING, strain_of
from ravis.routing.engine import RoutingEngine

PROVIDER_OF = {"alpha-1": "alpha", "beta-1": "beta"}


def _candidates() -> dict[str, ModelCapabilities]:
    """Two models a general pool considers equally right for the job."""
    return {
        name: ModelCapabilities(model_id=name, context_window=131072)
        for name in PROVIDER_OF
    }


def _tracker(now: list[float]) -> LoadTracker:
    return LoadTracker(provider_of_host=lambda _host: "", clock=lambda: now[0])


# ── What counts as strain, and what does not ─────────────────────────────────


def test_a_provider_that_just_refused_is_the_worst_place_to_send_the_next_request() -> None:
    now = [1000.0]
    health = HealthRegistry(clock=lambda: now[0])
    health.record(FailureClass.RATE_LIMIT, target="alpha-1", provider="alpha", started_at=now[0])

    strained = strain_of(PROVIDER_OF, PROVIDER_OF.__getitem__, None, health)

    assert strained == {"alpha-1": REFUSING}


def test_congestion_lifts_by_itself() -> None:
    """A provider busy an hour ago is not busy, and the penalty must expire.

    Nothing else lifts it: the level is the only thing keeping traffic off a
    provider that was never excluded in the first place.
    """
    now = [1000.0]
    health = HealthRegistry(clock=lambda: now[0])
    health.record(FailureClass.OVERLOAD, target="alpha-1", provider="alpha", started_at=now[0])
    now[0] += 3600.0

    assert strain_of(PROVIDER_OF, PROVIDER_OF.__getitem__, None, health) == {}


def test_a_provider_stating_almost_no_headroom_is_busy_rather_than_refusing() -> None:
    """What the provider said about itself, which is the only non-invented measure."""
    strained = _with_limits([1000.0], {"requests": {"limit": "500", "remaining": "20"}})

    assert strained == {"alpha-1": BUSY}


def test_a_provider_with_headroom_left_is_clear() -> None:
    assert _with_limits([1000.0], {"requests": {"limit": "500", "remaining": "400"}}) == {}


def test_a_statement_from_ten_minutes_ago_says_nothing() -> None:
    """A remaining count describes a window that has since reset."""
    strained = _with_limits([1000.0], {"requests": {"limit": "500", "remaining": "1"}}, age=600.0)

    assert strained == {}


def test_a_reset_duration_is_not_read_as_a_count() -> None:
    """`6m0s` is a duration. Read as a number it would be 6 of 500 — nearly spent."""
    strained = _with_limits([1000.0], {"tokens": {"limit": "500", "reset": "6m0s"}})

    assert strained == {}


def test_requests_in_flight_are_not_strain_whoever_they_are_to() -> None:
    """Neither half of the count is a level, for two different reasons.

    For a hosted provider RAVIS is not the only caller of that key and does not
    know the ceiling, so a count would be measured against nothing. For a local
    runtime the wait is real — LM Studio queues the second generation — but this
    term outranks the pool's preference, so scoring it would move the next
    request to the next candidate, which for most pools is hosted and paid for.
    Spending money because the machine is busy is the owner's decision, not a
    side effect of a latency preference.
    """
    now = [1000.0]
    load = _tracker(now)
    load._running[("alpha", "alpha-1", False)] = 9
    load._running[("beta", "beta-1", True)] = 1

    assert strain_of(PROVIDER_OF, PROVIDER_OF.__getitem__, load, None) == {}


def test_a_router_with_neither_half_is_unchanged() -> None:
    assert strain_of(PROVIDER_OF, PROVIDER_OF.__getitem__, None, None) == {}


# ── What the penalty does to a decision ──────────────────────────────────────


def test_a_strained_provider_loses_to_an_equal_candidate_elsewhere() -> None:
    decision = RoutingEngine().select(
        "ravis/auto", _candidates(), strain={"alpha-1": REFUSING, "beta-1": CLEAR}
    )

    assert decision.selected == "beta-1"


def test_the_explanation_says_a_provider_was_passed_over() -> None:
    """§9.7: a candidate that lost for a reason unrelated to its quality must say so.

    Without the sentence, a reader sees a model they did not expect on top and
    concludes the ranking is broken.
    """
    decision = RoutingEngine().select(
        "ravis/auto", _candidates(), strain={"alpha-1": REFUSING}
    )

    assert "under strain" in decision.reason
    assert "not excluded" in decision.reason


def test_strain_never_excludes_a_candidate() -> None:
    """§10 excludes a *failing* provider. A congested one is working and busy.

    With every candidate strained there is still a route: emptying a pool
    because its providers are busy would refuse requests the providers would
    have answered.
    """
    decision = RoutingEngine().select(
        "ravis/auto", _candidates(), strain={"alpha-1": REFUSING, "beta-1": REFUSING}
    )

    assert decision.routed is True
    assert decision.excluded == []


def test_strain_says_nothing_when_it_changed_no_order() -> None:
    """The winner is as strained as everything below it, so nothing was passed over."""
    decision = RoutingEngine().select(
        "ravis/auto", _candidates(), strain={"alpha-1": BUSY, "beta-1": BUSY}
    )

    assert "under strain" not in decision.reason


def test_a_local_lean_is_not_overridden_by_strain() -> None:
    """§14 rule 14: privacy is never overridden by score, and strain is score.

    `LOCAL_PREFERRED` is the level that leans rather than excludes, so it is the
    one a score can actually outweigh — and a local runtime already generating
    is `BUSY` by design. If strain led the key, a request that asked to stay on
    this machine would leave it the moment the machine was busy.
    """
    decision = RoutingEngine().select(
        "ravis/auto",
        _candidates(),
        remote_models=frozenset({"beta-1"}),
        policy=RoutingPolicy(privacy=PrivacyLevel.LOCAL_PREFERRED),
        strain={"alpha-1": REFUSING},
    )

    assert decision.selected == "alpha-1"


def test_session_affinity_still_outranks_strain() -> None:
    """§12.1 lists the four conditions that break stickiness. Congestion is not one.

    A busy provider is a reason to prefer elsewhere for a new conversation, not
    to move one already in progress onto another model mid-flight.
    """
    decision = RoutingEngine().select(
        "ravis/auto", _candidates(), sticky="alpha-1", strain={"alpha-1": REFUSING}
    )

    assert decision.selected == "alpha-1"


def _with_limits(now: list[float], limits: dict[str, dict[str, str]], age: float = 0.0):
    """A tracker that has seen one provider state these limits `age` seconds ago."""
    from ravis.reliability.load import _Limit, _Provider

    load = _tracker(now)
    known = _Provider()
    for kind, values in limits.items():
        known.limits[kind] = _Limit(values, now[0] - age)
    load._providers["alpha"] = known
    return strain_of(PROVIDER_OF, PROVIDER_OF.__getitem__, load, None)
