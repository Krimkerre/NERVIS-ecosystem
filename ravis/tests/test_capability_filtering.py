"""M6 — request-derived hard constraints, table-driven.

§9.2's gate asks for exactly this shape: *table-driven tests prove each hard
constraint excludes an otherwise top-ranked candidate*. So each case below sets
up a model that would win on every other basis and then makes one capability
insufficient, proving the constraint — not the ordering — is what removed it.
"""

from __future__ import annotations

import pytest

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.core.requests import NormalizedRequest
from ravis.routing.engine import RoutingEngine
from ravis.routing.requirements import CONTEXT_ESTIMATE_FLOOR, analyse

CHAT = "ravis/clarvis-chat"


def _capable(name: str, *supported: Capability, context: int | None = None) -> ModelCapabilities:
    known = ModelCapabilities(model_id=name, context_window=context)
    for capability in supported:
        known.record(
            CapabilityClaim(
                capability=capability,
                state=CapabilityState.SUPPORTED,
                provenance=Provenance.CONFIGURED,
            )
        )
    return known


TOOL_REQUEST = NormalizedRequest(tools=[{"type": "function", "function": {"name": "f"}}])
IMAGE_REQUEST = NormalizedRequest(
    messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:x"}}]}]
)
SCHEMA_REQUEST = NormalizedRequest(response_schema={"name": "x"})
REASONING_REQUEST = NormalizedRequest(reasoning_effort="high")
STREAM_REQUEST = NormalizedRequest(stream=True)

# Each row: the request, the capability it demands, and a readable name.
HARD_CONSTRAINTS = [
    pytest.param(TOOL_REQUEST, Capability.TOOLS, id="tools"),
    pytest.param(IMAGE_REQUEST, Capability.VISION, id="vision"),
    pytest.param(SCHEMA_REQUEST, Capability.STRUCTURED_OUTPUT, id="structured_output"),
    pytest.param(REASONING_REQUEST, Capability.REASONING, id="reasoning"),
    pytest.param(STREAM_REQUEST, Capability.STREAMING, id="streaming"),
]


@pytest.mark.parametrize("request_shape,demanded", HARD_CONSTRAINTS)
def test_a_request_requirement_excludes_an_otherwise_winning_candidate(
    request_shape: NormalizedRequest, demanded: Capability
) -> None:
    """§9.2's gate: each hard constraint removes a model that would have won.

    `aaa` sorts first and would be selected on every other basis. It lacks the
    one capability the request demands, so it must lose to `zzz` — which proves
    the constraint did the work rather than the ordering.
    """
    candidates = {"aaa": _capable("aaa"), "zzz": _capable("zzz", demanded)}

    decision = RoutingEngine().select(CHAT, candidates, request=request_shape)

    assert decision.selected == "zzz"


@pytest.mark.parametrize("request_shape,demanded", HARD_CONSTRAINTS)
def test_no_candidate_satisfying_the_requirement_is_a_no_route(
    request_shape: NormalizedRequest, demanded: Capability
) -> None:
    """§9.2 forbids relaxing a constraint to find something that fits."""
    del demanded
    candidates = {"aaa": _capable("aaa")}

    decision = RoutingEngine().select(CHAT, candidates, request=request_shape)

    assert decision.routed is False


@pytest.mark.parametrize("request_shape,demanded", HARD_CONSTRAINTS)
def test_the_exclusion_names_the_capability_and_why_it_was_needed(
    request_shape: NormalizedRequest, demanded: Capability
) -> None:
    """§9.2's gate also requires the explanation to name the exclusion."""
    candidates = {"aaa": _capable("aaa")}

    decision = RoutingEngine().select(CHAT, candidates, request=request_shape)

    assert any(demanded.value in reason for reason in decision.excluded[0].reasons)


def test_a_chat_pool_without_tools_in_the_request_does_not_require_them() -> None:
    """§5.1: tool support is optional for clarvis-chat *unless* the request
    supplies tools. The same pool, the same models, a different request."""
    candidates = {"plain": _capable("plain")}

    decision = RoutingEngine().select(CHAT, candidates, request=NormalizedRequest())

    assert decision.selected == "plain"


def test_a_request_needing_more_context_than_a_model_has_excludes_it() -> None:
    big = "x" * (CONTEXT_ESTIMATE_FLOOR * 8)
    request = NormalizedRequest(messages=[{"role": "user", "content": big}])
    candidates = {
        "small": _capable("small", context=1024),
        "roomy": _capable("roomy", context=200_000),
    }

    decision = RoutingEngine().select(CHAT, candidates, request=request)

    assert decision.selected == "roomy"


def test_an_unpublished_context_window_does_not_exclude_a_model() -> None:
    """A deliberate asymmetry with a pool's declared minimum, which fails closed.

    A generic OpenAI-compatible endpoint publishes no context windows, so failing
    closed here would make every large request unroutable against the very
    upstream RAVIS exists to serve. A pool's minimum is an operator's explicit
    demand; this is RAVIS's own estimate, and excluding real candidates on the
    strength of its own arithmetic is the weaker claim.
    """
    big = "x" * (CONTEXT_ESTIMATE_FLOOR * 8)
    request = NormalizedRequest(messages=[{"role": "user", "content": big}])
    candidates = {"unpublished": _capable("unpublished")}

    decision = RoutingEngine().select(CHAT, candidates, request=request)

    assert decision.routed is True


def test_that_unverified_check_is_reported_rather_than_hidden() -> None:
    """§9.7 separates facts from unknowns: a check nobody could perform is an
    unknown, not a pass."""
    big = "x" * (CONTEXT_ESTIMATE_FLOOR * 8)
    request = NormalizedRequest(messages=[{"role": "user", "content": big}])

    decision = RoutingEngine().select(CHAT, {"u": _capable("u")}, request=request)

    assert any("could not be verified" in note for note in decision.unverified)


def test_a_small_request_is_not_context_checked_at_all() -> None:
    """Below the floor the estimate's error exceeds the number itself."""
    request = NormalizedRequest(messages=[{"role": "user", "content": "hi"}])

    assert analyse(request).estimated_context_tokens < CONTEXT_ESTIMATE_FLOOR


def test_a_directly_named_model_is_not_second_guessed() -> None:
    """§5.3 puts an explicit request above inference: refusing a model the client
    named, on capability data RAVIS may simply not have, would overrule them."""
    candidates = {"named": _capable("named")}

    decision = RoutingEngine().select("named", candidates, request=TOOL_REQUEST)

    assert decision.selected == "named"


def test_requirements_appear_in_the_explanation_with_their_cause() -> None:
    decision = RoutingEngine().select(CHAT, {"a": _capable("a")}, request=TOOL_REQUEST)

    assert any("supplies tools" in item for item in decision.requirements)
