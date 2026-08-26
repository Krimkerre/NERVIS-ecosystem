"""`ravis/local` promises the request never leaves this machine. It has to.

The promise was prose. `ravis/local` described itself as "never leaves this
machine" and `ravis/private` as "cloud providers are excluded", and nothing
enforced either — the routing engine receives a flat table of model names with
no record of which upstream produced them, so it could not have enforced them
even in principle.

With four API providers configured, `ravis/local` answered with an OpenRouter
model. Observed live, 2026-08-26: the prompt left the machine, to a third party,
from the one pool whose entire purpose is that it does not.
"""

from __future__ import annotations

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.pools import POOLS_BY_ID, PoolRequirements
from ravis.routing.engine import RoutingEngine
from ravis.upstreams import is_local_address


def capable(model_id: str = "m") -> ModelCapabilities:
    known = ModelCapabilities(model_id)
    for capability in Capability:
        known.record(
            CapabilityClaim(capability, CapabilityState.SUPPORTED, Provenance.MEASURED)
        )
    known.context_window = 200_000
    return known


CANDIDATES = {"local-model": capable("local-model"),
              "vendor/api-model": capable("vendor/api-model")}
REMOTE = frozenset({"vendor/api-model"})


def selected(pool: str) -> str | None:
    return RoutingEngine().select(pool, CANDIDATES, remote_models=REMOTE).selected


# ── The promise ────────────────────────────────────────────────────────────


def test_a_local_pool_never_selects_a_remote_model() -> None:
    assert selected("ravis/local") == "local-model"


def test_a_local_pool_refuses_rather_than_falling_back() -> None:
    """§5.2: a pool with no satisfying candidate is unavailable, never relaxed.

    Relaxing here would be the failure in its most dangerous form — the request
    that most needed to stay local is the one a fallback would send away.
    """
    decision = RoutingEngine().select(
        "ravis/local", {"vendor/api-model": capable("vendor/api-model")}, remote_models=REMOTE
    )

    assert decision.selected is None
    assert "unavailable" in decision.reason


def test_the_refusal_says_why_that_model_was_excluded() -> None:
    """§9.7: a route explanation names what excluded a candidate."""
    decision = RoutingEngine().select(
        "ravis/local", {"vendor/api-model": capable("vendor/api-model")}, remote_models=REMOTE
    )

    reasons = [r for e in decision.excluded for r in e.reasons]
    assert any("never leaves this machine" in r for r in reasons)


def test_private_carries_the_same_constraint() -> None:
    assert selected("ravis/private") == "local-model"


def test_the_api_pool_is_the_mirror_image() -> None:
    assert selected("ravis/api") == "vendor/api-model"


def test_every_other_pool_may_use_an_api_model() -> None:
    """The point of the change, not a side effect of it.

    Only `ravis/local` and `ravis/private` are placement promises. A pool about
    speed or cost has no reason to refuse a model for running elsewhere, and
    refusing would make every one of them local-only on a machine with no
    runtime installed.
    """
    open_pools = [
        pool for pool in POOLS_BY_ID
        if POOLS_BY_ID[pool].requirements.locality == "any"
    ]
    assert "ravis/balanced" in open_pools and "ravis/cheap" in open_pools

    for pool in open_pools:
        decision = RoutingEngine().select(
            pool, {"vendor/api-model": capable("vendor/api-model")}, remote_models=REMOTE
        )
        assert decision.selected == "vendor/api-model", pool


# ── What counts as this machine ────────────────────────────────────────────


def test_loopback_is_local_and_everything_else_is_not() -> None:
    assert is_local_address("http://127.0.0.1:1234")
    assert is_local_address("http://localhost:11434")
    assert is_local_address("http://[::1]:8080")
    assert not is_local_address("https://api.openai.com")


def test_a_lan_address_is_not_this_machine() -> None:
    """192.168.1.50 is somebody else's computer.

    "Did not leave my network" is a different promise from "did not leave my
    machine", and the one these pools make is the second.
    """
    assert not is_local_address("http://192.168.1.50:1234")


def test_an_unparseable_address_fails_closed() -> None:
    """The cost of guessing wrong is asymmetric: one direction refuses a route,
    the other sends a prompt off a machine that was promised it would not."""
    assert not is_local_address("garbage")
    assert not is_local_address("")


def test_locality_defaults_to_any_so_no_pool_gains_a_constraint_by_accident() -> None:
    assert PoolRequirements().locality == "any"
