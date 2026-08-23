"""The discovery adapter, and its refusal to guess.

The behaviour under test is mostly *not* answering. A generic OpenAI-compatible
endpoint publishes model IDs and nothing else, so an adapter that reported tool
support would be inventing it — and §5.1's hard invariant means that invention
routes an agent to a model that cannot call tools.
"""

from __future__ import annotations

import httpx

from ravis.core.capabilities import Capability, CapabilityState
from ravis.core.requests import NormalizedRequest
from ravis.providers.base import ProtocolMode, ProviderAdapter, describe
from ravis.providers.generic_openai import GenericOpenAiAdapter
from ravis.upstream import Upstream


def _adapter(configured: dict | None = None, reachable: bool = True) -> GenericOpenAiAdapter:
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        if not reachable:
            raise httpx.ConnectError("upstream is down")
        return httpx.Response(200, json={"object": "list", "data": [{"id": "a"}, {"id": "b"}]})

    return GenericOpenAiAdapter(
        upstream=Upstream(base_url="http://upstream.invalid", api_key=""),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        configured_capabilities=configured,
    )


def test_it_satisfies_the_adapter_protocol() -> None:
    """Structural, so nothing has to inherit from RAVIS to be an adapter."""
    assert isinstance(_adapter(), ProviderAdapter)


def test_it_declares_the_transparent_protocol_mode() -> None:
    """§6 requires diagnostics to report which execution path ran."""
    assert _adapter().protocol_mode is ProtocolMode.OPENAI_TRANSPARENT


async def test_it_lists_the_upstream_models() -> None:
    assert await _adapter().models() == ["a", "b"]


async def test_an_unreachable_upstream_yields_an_empty_list_not_an_error() -> None:
    """An empty catalogue is a legitimate state; a raised error is not (§14.4)."""
    assert await _adapter(reachable=False).models() == []


async def test_health_reports_unreachable_with_a_reason() -> None:
    health = await _adapter(reachable=False).health()

    assert health.reachable is False
    assert health.detail


async def test_tool_support_is_unknown_rather_than_assumed() -> None:
    """The refusal this adapter exists to make.

    Speaking the OpenAI protocol says nothing about whether a given model behind
    it can call tools, so §7 forbids asserting it. UNKNOWN fails closed, which
    makes a tool-requiring pool unavailable — correct, and visibly so.
    """
    known = await _adapter().capabilities("some-model")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN
    assert known.satisfies(Capability.TOOLS) is False


async def test_protocol_level_capabilities_are_seeded() -> None:
    """§7 permits seeding defaults that are neither safety- nor capability-critical."""
    known = await _adapter().capabilities("some-model")

    assert known.state_of(Capability.TEXT) is CapabilityState.SUPPORTED
    assert known.state_of(Capability.STREAMING) is CapabilityState.SUPPORTED


async def test_configuration_can_establish_tool_support() -> None:
    """The one mechanism available before probing or SIRVIS evidence exists."""
    adapter = _adapter(configured={"m": {"tools": "SUPPORTED"}})

    known = await adapter.capabilities("m")

    assert known.satisfies(Capability.TOOLS) is True


async def test_configuration_for_another_model_does_not_leak() -> None:
    adapter = _adapter(configured={"other": {"tools": "SUPPORTED"}})

    known = await adapter.capabilities("m")

    assert known.satisfies(Capability.TOOLS) is False


async def test_a_typo_in_configuration_costs_one_claim_not_the_route() -> None:
    """And it fails closed, which is the safe direction for a mistake to fail in."""
    adapter = _adapter(configured={"m": {"tolls": "SUPPORTED", "tools": "NOPE"}})

    known = await adapter.capabilities("m")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN


async def test_cost_is_unknown_rather_than_zero() -> None:
    """A free local model and an unpriced cloud model are different facts (§14)."""
    assert await _adapter().estimate_cost(NormalizedRequest()) is None


def test_describe_carries_no_credential_or_endpoint() -> None:
    """This shape reaches NERVIS, traces and route explanations (runbook §9)."""
    summary = describe(_adapter())

    assert set(summary) == {"provider", "protocol_mode"}


async def test_a_configured_context_window_is_recorded() -> None:
    """The operator's only route to a pool that declares a minimum context.

    A generic OpenAI-compatible endpoint publishes no windows, and a declared
    minimum fails closed on an unknown one — so without this the agent pool
    stays unroutable however capable its models actually are.
    """
    adapter = _adapter(configured={"m": {"context_window": "32768"}})

    known = await adapter.capabilities("m")

    assert known.meets_context(32768) is True


async def test_a_nonsense_context_window_is_ignored_rather_than_crashing() -> None:
    """A typo costs that one claim, and fails closed."""
    adapter = _adapter(configured={"m": {"context_window": "lots"}})

    known = await adapter.capabilities("m")

    assert known.context_window is None
