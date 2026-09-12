"""§8.7 — a tool probe is answered by a model that is already there.

Clarvis decides whether an agent can call tools by asking: one tool, one token,
the message `ping` (`supportsTools` in `clarvis/src/model/OpenAiCompatibleProvider.ts`),
with a ten-second timeout. §8.7 calls routing that question well the sharpest
wire-level constraint in the integration — *do not route a tiny tool probe to a
cold or unsupported candidate and thereby make the pool appear incapable.*

Unsupported was already impossible. Cold was the default: `ravis/clarvis-agent`
ranks its declared coding families ahead of residency, so the probe went to the
preferred coder whether or not it was loaded.

What these pin: a probe prefers a candidate that needs no load; a tool request
that is not a probe routes exactly as before; a probe with nothing loaded is
still routed rather than refused; and a privacy level preferring this machine is
not traded away to spare a probe a load.
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
from ravis.routing.requirements import is_tool_probe
from ravis.runtime.residency import Residency, ResidencySnapshot

AGENT = "ravis/clarvis-agent"

# `qwen3-coder` sits well ahead of `codegemma` in the pool's declared order, so
# without §8.7 the cold one wins. Every steering test below rests on that, and
# the non-probe controls assert it rather than assume it.
PREFERRED_COLD = "qwen3-coder-30b"
RESIDENT = "codegemma-7b"
# Hosted, and deliberately *less* preferred than the cold local coder, so a
# probe landing here is the probe term's doing and not the pool's.
HOSTED = "deepseek-chat"

# Clarvis's probe tool, as it sends it.
NOOP_TOOL = {
    "type": "function",
    "function": {
        "name": "noop",
        "description": "does nothing",
        "parameters": {"type": "object", "properties": {}},
    },
}


def _capable(model_id: str) -> ModelCapabilities:
    """A candidate `ravis/clarvis-agent` admits: every capability, a long window.

    All capabilities rather than tools alone, because the pool also requires
    structured output and 128K, and every request requires text. A fixture the
    pool refused would turn these into tests of eligibility instead of ranking.
    """
    known = ModelCapabilities(model_id, context_window=200_000)
    for capability in Capability:
        known.record(CapabilityClaim(capability, CapabilityState.SUPPORTED, Provenance.CONFIGURED))
    return known


def _request(**overrides: Any) -> NormalizedRequest:
    """Clarvis's probe body field for field, with what a test changes.

    Built through `normalize` rather than as a `NormalizedRequest` directly, so
    the `max_tokens` spelling Clarvis uses is parsed the way a live request is.
    An override of `None` removes the field.
    """
    body: dict[str, Any] = {
        "model": AGENT,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
        "tools": [NOOP_TOOL],
        **overrides,
    }
    return normalize(b"", {key: value for key, value in body.items() if value is not None})


def _route(
    request: NormalizedRequest,
    *names: str,
    loaded: tuple[str, ...] = (),
    remote: frozenset[str] = frozenset(),
    **options: Any,
) -> RouteDecision:
    """Resolve the agent pool the way LM Studio's residency report would shape it.

    `known=True` with only `loaded` listed is what LM Studio reports: the named
    models resident, every other local model cold.
    """
    return RoutingEngine().select(
        AGENT,
        {name: _capable(name) for name in names},
        residency=ResidencySnapshot(
            states={name: Residency.HOT for name in loaded}, known=True
        ),
        request=request,
        remote_models=remote,
        **options,
    )


# ── Recognising a probe ──────────────────────────────────────────────────────


def test_clarvis_tool_probe_is_recognised_by_its_shape() -> None:
    assert is_tool_probe(_request())


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"max_tokens": None}, "an uncapped tool request is an agent turn"),
        ({"max_tokens": 256}, "a capped one still has room for a tool call"),
        ({"tools": [NOOP_TOOL, NOOP_TOOL]}, "one token with a toolset is a cache warm-up"),
        ({"tools": None}, "without tools there is nothing to probe"),
        ({"max_tokens": True}, "a boolean is not a token count"),
    ],
)
def test_nothing_else_is_a_probe(overrides: dict[str, Any], why: str) -> None:
    assert not is_tool_probe(_request(**overrides)), why


# ── Routing one ──────────────────────────────────────────────────────────────


def test_a_probe_goes_to_the_loaded_model_not_the_cold_preferred_one() -> None:
    """§8.7's instruction, in one test.

    The pool prefers the cold coder, and an ordinary agent turn still gets it
    (the next test). The probe goes to the model already loaded, which answers
    the same question — both can call tools — without waiting on a load.
    """
    probe = _route(_request(), PREFERRED_COLD, RESIDENT, loaded=(RESIDENT,))

    assert probe.selected == RESIDENT, probe.reason
    # Ranked behind, never removed: if the loaded model fails, the chain still
    # reaches the cold one rather than giving up (§9.2, §10).
    assert probe.fallbacks == [PREFERRED_COLD]
    assert "capability probe (§8.7)" in probe.reason


@pytest.mark.parametrize(
    "overrides",
    [{"max_tokens": None}, {"tools": [NOOP_TOOL, NOOP_TOOL]}],
    ids=["agent-turn", "one-token-cache-warm-up"],
)
def test_a_tool_request_that_is_not_a_probe_routes_as_before(overrides: dict[str, Any]) -> None:
    """The narrowness gate: probes only, never tool requests in general.

    Same pool, candidates and residency as the test above. A real agent turn
    should reach for the preferred coder and pay the load, which is §12.2's
    default and is left alone here.
    """
    decision = _route(_request(**overrides), PREFERRED_COLD, RESIDENT, loaded=(RESIDENT,))

    assert decision.selected == PREFERRED_COLD, decision.reason
    assert "§8.7" not in decision.reason


def test_a_probe_with_nothing_loaded_goes_to_a_hosted_candidate() -> None:
    """The decision for "nothing local is resident": hosted, which needs no load.

    Every eligible candidate can call tools, so a hosted one answers the probe
    truthfully for the pool in one round trip — where the cold local coder would
    spend it loading.
    """
    hosted = frozenset({HOSTED})

    probe = _route(_request(), PREFERRED_COLD, HOSTED, remote=hosted)
    turn = _route(_request(max_tokens=None), PREFERRED_COLD, HOSTED, remote=hosted)

    assert probe.selected == HOSTED, probe.reason
    assert turn.selected == PREFERRED_COLD, "the control: without a probe, preference wins"


def test_a_probe_with_only_cold_candidates_is_routed_not_refused() -> None:
    """Residency ranks and never excludes (§9.2).

    Refusing here would leave the pool looking incapable for as long as nothing
    happened to be loaded — the outcome §8.7 exists to prevent. So the probe
    routes exactly as before, and the explanation says it could not be steered.
    """
    probe = _route(_request(), PREFERRED_COLD, RESIDENT)

    assert probe.selected == PREFERRED_COLD
    assert "routed as usual rather than refused" in probe.reason


def test_local_preferred_privacy_is_not_traded_to_spare_a_probe_a_load() -> None:
    """§14: a privacy constraint is never overridden by score.

    `LOCAL_PREFERRED` ranks rather than excludes, so a term ahead of it could
    move the probe off the machine. It must not: with no local model loaded the
    probe stays local and pays the load, and with one loaded it takes that one.
    """
    local_preferred = RoutingPolicy(privacy=PrivacyLevel.LOCAL_PREFERRED)
    hosted = frozenset({HOSTED})

    cold = _route(_request(), PREFERRED_COLD, HOSTED, remote=hosted, policy=local_preferred)
    warm = _route(
        _request(), PREFERRED_COLD, RESIDENT, HOSTED,
        loaded=(RESIDENT,), remote=hosted, policy=local_preferred,
    )

    assert cold.selected == PREFERRED_COLD, cold.reason
    assert "leave this machine" in cold.reason
    assert warm.selected == RESIDENT, warm.reason


def test_a_session_that_last_used_a_cold_model_does_not_pull_the_probe_onto_it() -> None:
    """Affinity is for a conversation, and a one-token probe has none to continue."""
    probe = _route(
        _request(), PREFERRED_COLD, RESIDENT, loaded=(RESIDENT,), sticky=PREFERRED_COLD
    )

    assert probe.selected == RESIDENT, probe.reason


def test_a_probe_is_never_spent_exploring() -> None:
    """Exploration is the one way a probe could still land further down the ranking.

    The control shows the roll really does explore on an ordinary turn, so the
    probe's result is the suppression working and not a roll that missed.
    """
    always = Exploration(rate=0.5, roll=0.0)

    probe = _route(_request(), PREFERRED_COLD, RESIDENT, loaded=(RESIDENT,), explore=always)
    turn = _route(
        _request(max_tokens=None), PREFERRED_COLD, RESIDENT, loaded=(RESIDENT,), explore=always
    )

    assert probe.selected == RESIDENT, probe.reason
    assert turn.selected == RESIDENT, "the control: an ordinary turn explores to it"
    assert turn.reason.startswith("trying"), turn.reason
