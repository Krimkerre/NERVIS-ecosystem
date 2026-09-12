"""§14 — `LOCAL_PREFERRED` is never traded for another ranking term.

RAVIS.md §14: *privacy constraints can never be overridden by score.* Three of
the privacy levels exclude, and an excluded candidate never reaches the ranking,
so for them the rule holds by construction. `LOCAL_PREFERRED` is the one level
that ranks instead, which makes its whole protection a matter of *where* its term
sits in the sort key: every term consulted before it is a way for a request to
leave this machine against the level the caller's identity asked for.

Five were consulted before it. Session affinity, §12.2's short-session load
brake and a tool probe's preference for whatever answers without a load all led
the key, and a pool's own `prefer_remote` and `prefer_fast` came ahead of it
inside the preference terms. Exploration, applied after ranking, could separately
pick a hosted model from further down.

What these pin: each of those pulls moves the request off the machine when no
privacy level is asked for — the controls, without which every assertion below
could be passing for a reason of its own; none of them does under
`LOCAL_PREFERRED`; and inside the local group each still works, so privacy is
consulted first rather than the other terms being switched off.
"""

from __future__ import annotations

from typing import Any

import pytest

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.requests import NormalizedRequest, normalize
from ravis.policy import PrivacyLevel, RoutingPolicy
from ravis.routing.engine import Exploration, RoutingEngine
from ravis.routing.explain import RouteDecision
from ravis.runtime.residency import Residency, ResidencySnapshot
from ravis.runtime.resources import MemoryReading

AGENT = "ravis/clarvis-agent"

# Both local. The pool's declared order puts the cold one ahead of the resident
# one, which is what lets the in-group tests tell a working brake or affinity
# apart from the pool simply agreeing with them.
LOCAL_COLD = "qwen3-coder-30b"
LOCAL_RESIDENT = "codegemma-7b"
# Hosted, and *less* preferred by the pool than `LOCAL_COLD`. So whenever it wins
# below, the pull under test put it there and not the pool's own taste.
HOSTED = "deepseek-chat"
# A second hosted coder, for the one case that needs nothing local at all.
HOSTED_CODER = "claude-sonnet-4"

# For the two pool-preference pulls. Named to fit each pool's families, and
# selected explicitly (`chosen`) so the tests are about ranking, not membership.
CHAT_LOCAL = "ministral-14b"
CHAT_HOSTED = "claude-haiku-4.5"
FAST_LOCAL = "qwen3-1.7b"
FAST_HOSTED = "gpt-4o-mini"

HOSTED_MODELS = frozenset({HOSTED, HOSTED_CODER, CHAT_HOSTED, FAST_HOSTED})
PRIVATE = RoutingPolicy(privacy=PrivacyLevel.LOCAL_PREFERRED)

# Clarvis's probe tool, as it sends it (see `test_tool_probe_routing.py`).
NOOP_TOOL = {
    "type": "function",
    "function": {
        "name": "noop",
        "description": "does nothing",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _capable(model_id: str) -> ModelCapabilities:
    """A candidate every pool here admits: every capability, a long window.

    `ravis/clarvis-agent` requires tools, structured output and 128K, and a
    fixture the pool refused would turn these into tests of eligibility.
    """
    known = ModelCapabilities(model_id, context_window=200_000)
    for capability in Capability:
        known.record(CapabilityClaim(capability, CapabilityState.SUPPORTED, Provenance.CONFIGURED))
    return known


def _pressured() -> MemoryReading:
    total = 16 * 2**30
    return MemoryReading(available_bytes=int(total * 0.05), total_bytes=total)


def _probe() -> NormalizedRequest:
    """Clarvis's one-tool, one-token capability probe, parsed as a live one is."""
    return normalize(b"", {
        "model": AGENT,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
        "tools": [NOOP_TOOL],
    })


def _route(
    *names: str, pool: str = AGENT, loaded: tuple[str, ...] = (), **options: Any
) -> RouteDecision:
    """Resolve `pool` over `names`, with LM Studio's shape of residency report.

    `known=True` with only `loaded` listed: those resident, every other local
    model cold. Which names are hosted is fixed by `HOSTED_MODELS`, so no test
    can quietly disagree with another about where a model runs.
    """
    return RoutingEngine().select(
        pool,
        {name: _capable(name) for name in names},
        residency=ResidencySnapshot(
            states={name: Residency.HOT for name in loaded}, known=True
        ),
        remote_models=frozenset(name for name in names if name in HOSTED_MODELS),
        **options,
    )


# ── The pulls that lead the key ──────────────────────────────────────────────

PULLS = [
    pytest.param({"expected_session_requests": 1}, id="short-session"),
    pytest.param(
        {"expected_session_requests": 100, "memory": _pressured()},
        id="long-session-under-memory-pressure",
    ),
    pytest.param({"sticky": HOSTED}, id="affinity-to-a-hosted-model"),
]


def test_without_a_pull_the_pool_already_keeps_the_local_coder() -> None:
    """The baseline both halves below rest on: with nothing pulling, the pool's
    own order picks the local coder, privacy level or not."""
    assert _route(LOCAL_COLD, HOSTED).selected == LOCAL_COLD
    assert _route(LOCAL_COLD, HOSTED, policy=PRIVATE).selected == LOCAL_COLD


@pytest.mark.parametrize("pull", PULLS)
def test_each_pull_moves_an_ordinary_request_off_the_machine(pull: dict[str, Any]) -> None:
    """The control. Each pull really does send the request to the hosted model
    when no privacy level asks otherwise — so the assertion in the next test is
    load-bearing, and the fix provably leaves ordinary traffic alone."""
    decision = _route(LOCAL_COLD, HOSTED, **pull)

    assert decision.selected == HOSTED, decision.reason


@pytest.mark.parametrize("pull", PULLS)
def test_no_pull_moves_a_local_preferred_request_off_the_machine(pull: dict[str, Any]) -> None:
    """§14's rule, against the three terms that used to lead the key.

    Paying a load, or leaving a hosted conversation mid-session, is what the
    caller's level costs; sending the request away to avoid either is the
    trade §14 forbids.
    """
    decision = _route(LOCAL_COLD, HOSTED, policy=PRIVATE, **pull)

    assert decision.selected == LOCAL_COLD, decision.reason
    # Still ranked, never removed: LOCAL_PREFERRED is a preference (§9.2).
    assert decision.fallbacks == [HOSTED]


@pytest.mark.parametrize(
    "pull",
    [
        pytest.param({"expected_session_requests": 1}, id="short-session"),
        pytest.param({"sticky": HOSTED}, id="affinity-to-a-hosted-model"),
    ],
)
def test_a_pull_does_not_move_a_local_preferred_probe_off_the_machine(
    pull: dict[str, Any],
) -> None:
    """The probe's own guard scored a hosted candidate as cold, which tied it with
    the cold local coder — and the tie fell to affinity and the load brake, both
    still ahead of privacy. `test_tool_probe_routing.py` pins the same probe with
    no pull; this pins it with one."""
    decision = _route(LOCAL_COLD, HOSTED, policy=PRIVATE, request=_probe(), **pull)

    assert decision.selected == LOCAL_COLD, decision.reason


@pytest.mark.parametrize(
    ("pool", "local", "hosted", "options"),
    [
        pytest.param("ravis/chat", CHAT_LOCAL, CHAT_HOSTED, {}, id="pool-prefers-remote"),
        pytest.param(
            "ravis/fast", FAST_LOCAL, FAST_HOSTED,
            {"observed_ttft_ms": {FAST_HOSTED: 150.0, FAST_LOCAL: 900.0}},
            id="pool-prefers-fast",
        ),
    ],
)
def test_a_pools_own_lean_does_not_move_a_local_preferred_request(
    pool: str, local: str, hosted: str, options: dict[str, Any]
) -> None:
    """A pool's placement or speed preference is configuration; the privacy level
    is the caller's. The control half shows each pool really does lean hosted."""
    chosen = (local, hosted)

    ordinary = _route(local, hosted, pool=pool, chosen=chosen, **options)
    private = _route(local, hosted, pool=pool, chosen=chosen, policy=PRIVATE, **options)

    assert ordinary.selected == hosted, ordinary.reason
    assert private.selected == local, private.reason


# ── Inside the local group, every term still works ──────────────────────────


def test_the_load_brake_still_chooses_among_local_candidates() -> None:
    """Privacy first, not privacy only: the short session still spares a load when
    a local model is resident, and the pool's cold favourite loses to it."""
    decision = _route(
        LOCAL_COLD, LOCAL_RESIDENT, HOSTED,
        loaded=(LOCAL_RESIDENT,), policy=PRIVATE, expected_session_requests=1,
    )

    assert decision.selected == LOCAL_RESIDENT, decision.reason


def test_affinity_still_holds_a_session_on_its_local_model() -> None:
    """§12.1's continuity survives wherever it does not cost the privacy level."""
    decision = _route(LOCAL_COLD, LOCAL_RESIDENT, HOSTED, policy=PRIVATE, sticky=LOCAL_RESIDENT)

    assert decision.selected == LOCAL_RESIDENT, decision.reason


# ── Exploration ──────────────────────────────────────────────────────────────

ALWAYS = Exploration(rate=0.5, roll=0.0)


def test_exploration_moves_an_ordinary_request_to_the_hosted_alternative() -> None:
    """The control: with one alternative, and it hosted, the roll lands on it."""
    decision = _route(LOCAL_COLD, HOSTED, explore=ALWAYS)

    assert decision.selected == HOSTED
    assert decision.reason.startswith("trying"), decision.reason


def test_exploration_never_takes_a_local_preferred_request_off_the_machine() -> None:
    """With only a hosted alternative there is nothing to explore to, so the
    ordinary pick stands — and the explanation says so rather than "trying"."""
    decision = _route(LOCAL_COLD, HOSTED, policy=PRIVATE, explore=ALWAYS)

    assert decision.selected == LOCAL_COLD, decision.reason
    assert not decision.reason.startswith("trying")


@pytest.mark.parametrize("roll", [0.0, 0.49], ids=["low-roll", "high-roll"])
def test_exploration_still_explores_among_local_candidates(roll: float) -> None:
    """Both rolls, because the high one is what used to index past the local
    alternative onto the hosted one. Exploring still happens; it stays home."""
    decision = _route(
        LOCAL_COLD, LOCAL_RESIDENT, HOSTED,
        policy=PRIVATE, explore=Exploration(rate=0.5, roll=roll),
    )

    assert decision.selected == LOCAL_RESIDENT, decision.reason
    assert decision.reason.startswith("trying"), decision.reason


def test_exploration_is_untouched_when_nothing_local_is_eligible() -> None:
    """Nothing local is in the running, so exploring among hosted models moves no
    request anywhere it was not already going."""
    decision = _route(HOSTED_CODER, HOSTED, policy=PRIVATE, explore=ALWAYS)

    assert decision.selected == HOSTED, decision.reason
    assert decision.reason.startswith("trying"), decision.reason


# ── The explanation (§9.7) ───────────────────────────────────────────────────


def test_the_explanation_says_privacy_ranked_local_candidates_first() -> None:
    """A cold local model chosen over a hosted one for a one-shot client looks like
    §12.2 misfiring unless the reason says what outranked it."""
    decision = _route(LOCAL_COLD, HOSTED, policy=PRIVATE, expected_session_requests=1)

    assert "prefers this machine" in decision.reason, decision.reason


def test_the_explanation_says_when_nothing_local_could_be_used() -> None:
    """The level ranks and never excludes, so a hosted answer is legitimate — and
    exactly what an operator asks about, so it is said."""
    decision = _route(
        LOCAL_COLD, HOSTED, policy=PRIVATE,
        unavailable={LOCAL_COLD: "circuit open after 5 failures"},
    )

    assert decision.selected == HOSTED
    assert "no local candidate was eligible" in decision.reason, decision.reason
