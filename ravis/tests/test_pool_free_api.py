"""`ravis/free-api` costs nothing and runs somewhere else. Both halves are load-bearing.

**Why it is not `ravis/cheap`.** Cheap prefers local, and on any machine with a
runtime "least monetary cost" resolves to a local model — correct for cheap and
wrong for the caller this pool exists for. NERVIS's unattended work (M25) must
not load a local model: loading one is exactly how work nobody is watching
starts competing for memory with the conversation somebody is having.

**Why it is not §9.6.1's background marker either.** That marker means *must be
free* and a local model satisfies it, which is the one outcome background work
cannot afford.

**And why it sits below `private`.** A free tier is free because the prompt is
worth something. This is an egress path with logging, so a request that asked to
stay on the machine must not be able to reach it — and the way that holds is
that the two constraints have an empty intersection rather than a rule somebody
remembered to write.
"""

from __future__ import annotations

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.pools import POOLS_BY_ID
from ravis.reliability.failures import FailureClass
from ravis.routing.engine import RoutingEngine


def capable(model_id: str, price: float = 0.0) -> ModelCapabilities:
    """A fully capable model at a given price.

    Price lives on the capability record rather than being passed to `select`:
    the engine reads `price_per_million` off each candidate, which is where a
    provider's catalogue puts it.
    """
    known = ModelCapabilities(model_id)
    for capability in Capability:
        known.record(
            CapabilityClaim(capability, CapabilityState.SUPPORTED, Provenance.MEASURED)
        )
    known.context_window = 200_000
    known.price_per_million = price
    return known


# A local model, a free hosted one, and a paid hosted one.
CANDIDATES = {
    "local-model": capable("local-model"),
    "vendor/free-model": capable("vendor/free-model"),
    "vendor/paid-model": capable("vendor/paid-model", price=3.0),
}
REMOTE = frozenset({"vendor/free-model", "vendor/paid-model"})


def decide(pool: str, candidates: dict | None = None):
    return RoutingEngine().select(
        pool, candidates if candidates is not None else CANDIDATES,
        remote_models=REMOTE,
    )


# ── Free, and elsewhere ─────────────────────────────────────────────────────


def test_the_free_pool_takes_the_free_hosted_model() -> None:
    assert decide("ravis/free-api").selected == "vendor/free-model"


def test_it_refuses_a_local_model_however_free_it_is() -> None:
    """The half that separates this from `ravis/cheap`.

    A local model prices at zero and would win outright on cost. It is excluded
    for *where it runs*, which is the constraint unattended work actually needs.
    """
    decision = decide("ravis/free-api", {"local-model": capable("local-model")})
    assert decision.selected is None
    assert "unavailable" in decision.reason


def test_it_refuses_a_paid_model_however_fast_it_is() -> None:
    decision = decide(
        "ravis/free-api", {"vendor/paid-model": capable("vendor/paid-model", price=3.0)}
    )
    assert decision.selected is None


def test_cheap_and_free_disagree_on_the_same_machine() -> None:
    """Which is the whole reason both exist.

    Cheap prefers local and takes the local model; free refuses it. A build
    where these two agreed would have one of them wrong.
    """
    assert decide("ravis/cheap").selected == "local-model"
    assert decide("ravis/free-api").selected == "vendor/free-model"


# ── Below private, structurally ─────────────────────────────────────────────


def test_free_and_local_have_no_model_in_common() -> None:
    """The boundary is an empty intersection rather than a rule to remember.

    `ravis/free-api` requires remote and `ravis/local` requires local, so there is
    no candidate that satisfies both — a request that asked to stay on the
    machine cannot reach a logged free tier by any route through these pools.
    """
    free = POOLS_BY_ID["ravis/free-api"].requirements.locality
    for private in ("ravis/local", "ravis/private"):
        assert POOLS_BY_ID[private].requirements.locality == "local"
        assert free == "remote"


def test_the_description_says_it_is_not_private() -> None:
    """A pool whose privacy cost is invisible is one somebody will reach for
    with a prompt they should not have sent."""
    said = POOLS_BY_ID["ravis/free-api"].description.lower()
    assert "logged" in said or "trained" in said
    assert "never a private route" in said


def test_the_ceiling_is_zero_rather_than_merely_low() -> None:
    assert POOLS_BY_ID["ravis/free-api"].max_price_per_million == 0.0


# ── Rate limits are the normal case, and already handled ────────────────────


def test_a_rate_limit_moves_on_rather_than_retrying() -> None:
    """Free tiers cap per minute and per day, so a 429 means "this one is
    spent, try the next" — which is what the existing policy already says.

    Asserted rather than added: this pool needed no new failure handling, and a
    test is how that stays true if the policy is ever revisited.
    """
    policy = FailureClass.RATE_LIMIT.policy
    assert policy.retry_same_target is False
    assert policy.may_fall_back is True


# ── Free is not the same as able to answer ──────────────────────────────────


def test_a_music_model_is_not_a_free_chat_model() -> None:
    """Found in this pool on a real catalogue.

    `google/lyria-3-pro-preview` publishes a price of zero and runs remotely, so
    it satisfied every constraint this pool has — and it generates music. Free
    was never the whole question; being able to answer at all comes first.
    """
    pool = POOLS_BY_ID["ravis/free-api"]
    for generator in ("google/lyria-3-pro-preview", "google/lyria-3-clip-preview",
                      "meta/musicgen-large", "suno/bark"):
        assert not pool._is_routable(generator)


def test_a_tick_cannot_override_routability() -> None:
    """The half that only showed up on a live catalogue.

    Routability lived in `default_membership`, so a pool the operator had
    curated by hand skipped it — and `ravis/free-api` held two Lyria entries
    because they are free, remote, and were ticked. Whether a model can answer
    a chat completion is not taste, so `eligible` refuses them too and there is
    no path that admits one.
    """
    pool = POOLS_BY_ID["ravis/free-api"]
    lyria = capable("google/lyria-3-pro-preview")
    assert "google/lyria-3-pro-preview" not in pool.eligible(
        {"google/lyria-3-pro-preview": lyria}, frozenset({"google/lyria-3-pro-preview"})
    )


def test_a_chat_model_that_hears_is_still_a_chat_model() -> None:
    """The trap in the fix: excluding on the word `audio` would drop a working
    model to catch a broken one."""
    pool = POOLS_BY_ID["ravis/free-api"]
    assert pool._is_routable("openai/gpt-4o-audio-preview")
    assert pool._is_routable("openrouter/free")
