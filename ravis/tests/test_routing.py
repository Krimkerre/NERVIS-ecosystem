"""Pool resolution: predictable selection, and a hard invariant that holds.

M5's acceptance is two claims — profiles select predictably, and the agent pool
enforces tools — so these tests are mostly about *refusing* to route.
"""

from __future__ import annotations

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.pools import POOLS_BY_ID, direct_target, is_pool_id
from ravis.routing.engine import RoutingEngine


def _model(name: str, tools: bool | None = None, context: int | None = None) -> ModelCapabilities:
    """A candidate. `tools=None` means nobody has said — the common real case."""
    known = ModelCapabilities(model_id=name, context_window=context)
    if tools is not None:
        known.record(
            CapabilityClaim(
                capability=Capability.TOOLS,
                state=CapabilityState.SUPPORTED if tools else CapabilityState.UNSUPPORTED,
                provenance=Provenance.CONFIGURED,
            )
        )
    return known


def test_a_plain_model_name_is_passed_through_untouched() -> None:
    """§5.3: an explicit request outranks any inference RAVIS could make."""
    decision = RoutingEngine().select("qwen3-4b", {"qwen3-4b": _model("qwen3-4b")})

    assert decision.selected == "qwen3-4b"


def test_the_agent_pool_refuses_a_model_with_unknown_tool_support() -> None:
    """§5.1's hard invariant, and the reason capability discovery fails closed.

    Nobody has said whether this model calls tools. Admitting it would route an
    agent to a model that may not, and the failure would surface as a broken
    tool call far from this decision.
    """
    candidates = {"mystery": _model("mystery", tools=None, context=65536)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.routed is False
    assert "unavailable" in decision.reason


def test_the_agent_pool_refuses_a_model_that_cannot_use_tools() -> None:
    candidates = {"chatty": _model("chatty", tools=False, context=65536)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.routed is False


def test_the_agent_pool_accepts_a_tool_capable_model() -> None:
    candidates = {"coder": _model("coder", tools=True, context=65536)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.selected == "coder"


def test_a_tool_capable_model_with_too_little_context_is_still_refused() -> None:
    """Both invariants apply; satisfying one is not enough."""
    candidates = {"coder": _model("coder", tools=True, context=8192)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.routed is False


def test_an_exclusion_names_every_reason_not_just_the_first() -> None:
    """A model failing two ways needs a different fix from one failing once."""
    candidates = {"weak": _model("weak", tools=False, context=1024)}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert len(decision.excluded[0].reasons) == 2


def test_the_chat_pool_does_not_require_tools() -> None:
    """§5.1: tool support is optional for clarvis-chat unless the request brings tools."""
    candidates = {"chatty": _model("chatty", tools=None)}

    decision = RoutingEngine().select("ravis/clarvis-chat", candidates)

    assert decision.selected == "chatty"


def test_selection_is_predictable_across_repeated_calls() -> None:
    """M5's acceptance criterion. A route explanation describing a coin toss is
    not an explanation."""
    candidates = {name: _model(name) for name in ("c", "a", "b")}
    engine = RoutingEngine()

    first = engine.select("ravis/clarvis-chat", candidates)
    second = engine.select("ravis/clarvis-chat", candidates)

    assert first.selected == second.selected == "a"


def test_a_declared_preference_beats_alphabetical_order() -> None:
    candidates = {name: _model(name, tools=True, context=65536)
                  for name in ("alpha-model", "qwen-coder")}

    decision = RoutingEngine().select("ravis/clarvis-agent", candidates)

    assert decision.selected == "qwen-coder"


def test_the_explanation_admits_it_has_no_quality_evidence() -> None:
    """§9.7 separates facts from unknowns; §9.4 forbids an invented weighting."""
    candidates = {"a": _model("a")}

    decision = RoutingEngine().select("ravis/clarvis-chat", candidates)

    assert "No benchmark evidence" in decision.reason


def test_an_empty_catalogue_is_a_no_route_with_a_distinct_reason() -> None:
    """"Nothing is available" and "nothing qualifies" need different fixes."""
    decision = RoutingEngine().select("ravis/clarvis-agent", {})

    assert decision.routed is False
    assert "no models are available" in decision.reason


def test_a_direct_address_bypasses_selection() -> None:
    candidates = {"qwen3-4b": _model("qwen3-4b")}

    decision = RoutingEngine().select("ravis/lmstudio/qwen3-4b", candidates)

    assert decision.selected == "qwen3-4b"


def test_a_direct_address_to_a_missing_model_is_a_no_route() -> None:
    """Better than forwarding it to fail confusingly at the provider."""
    decision = RoutingEngine().select("ravis/lmstudio/absent", {"other": _model("other")})

    assert decision.routed is False


def test_pool_ids_are_recognised_and_direct_addresses_are_not_pools() -> None:
    assert is_pool_id("ravis/clarvis-agent") is True
    assert direct_target("ravis/clarvis-agent") is None
    assert direct_target("ravis/lmstudio/qwen3-4b") == "qwen3-4b"


def test_no_pool_id_contains_a_substring_clarvis_filters_out() -> None:
    """§5.0.1: Clarvis's catalogue filter is an unanchored substring match, so a
    pool named `ravis/image` would silently vanish from its picker."""
    forbidden = ("embed", "whisper", "transcribe", "image", "moderation", "audio",
                 "realtime", "rerank", "guard")
    offenders = [
        pool_id for pool_id in POOLS_BY_ID
        if any(word in pool_id for word in forbidden)
    ]

    assert offenders == []
