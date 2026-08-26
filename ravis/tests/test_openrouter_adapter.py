"""Reading capability from a provider that publishes it.

`ravis/reasoning` was empty, and that was correct behaviour: `REASONING` fails
closed, so a model nothing has asked about is not in the pool. On a machine with
six hundred API models it left the pool empty anyway, because nothing had asked.

The tempting fix is a list of name fragments — `o1`, `-thinking`, `-reasoner`.
That is a guess dressed as a fact, and §9.4 rules it out for exactly the reason
it is tempting: it would look right most of the time.

There is no need to guess. OpenRouter's `/models` carries `supported_parameters`
per model, listing what that model actually accepts, and `reasoning` is one of
them. That is the provider stating its own capability — ADVERTISED, weaker than
a measurement and far stronger than a substring.

**Only OpenRouter publishes this.** OpenAI's catalogue carries `id`, `created`,
`owned_by` and a shutdown date; Google's carries `id` and a display name.
Neither says anything about capability, which is why neither has an adapter.
"""

from __future__ import annotations

import httpx
import pytest

from ravis.core.capabilities import Capability, CapabilityState, Provenance
from ravis.providers.openrouter import OpenRouterAdapter
from ravis.upstream import Upstream

ENTRY = {
    "id": "vendor/thinker",
    "context_length": 200_000,
    "supported_parameters": ["temperature", "reasoning", "tools", "structured_outputs"],
    "architecture": {"input_modalities": ["text", "image"]},
}
PLAIN = {"id": "vendor/plain", "supported_parameters": ["temperature"]}


def an_adapter(entries: list[dict] | None = None, fail: bool = False) -> OpenRouterAdapter:
    catalogue = entries if entries is not None else [ENTRY, PLAIN]

    def handle(_: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(500)
        return httpx.Response(200, json={"data": catalogue})

    return OpenRouterAdapter(
        upstream=Upstream(base_url="https://openrouter.ai/api", declared_key="k",
                          api_root="/v1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )


@pytest.mark.asyncio
async def test_reasoning_is_read_from_supported_parameters() -> None:
    known = await an_adapter().capabilities("vendor/thinker")

    assert known.state_of(Capability.REASONING) is CapabilityState.SUPPORTED
    assert known.claims[Capability.REASONING].provenance is Provenance.ADVERTISED


@pytest.mark.asyncio
async def test_a_parameter_the_provider_does_not_list_stays_unknown() -> None:
    """Absence is not a denial.

    OpenRouter lists what it knows a model takes, and a parameter missing from
    that list is as likely to be unlisted as unsupported. Recording a negative
    from silence is the same error as recording a positive from a substring —
    and UNKNOWN already fails closed, which is the safe direction.
    """
    known = await an_adapter().capabilities("vendor/plain")

    assert known.state_of(Capability.REASONING) is CapabilityState.UNKNOWN


@pytest.mark.asyncio
async def test_tools_and_structured_output_come_from_the_same_place() -> None:
    known = await an_adapter().capabilities("vendor/thinker")

    assert known.state_of(Capability.TOOLS) is CapabilityState.SUPPORTED
    assert known.state_of(Capability.STRUCTURED_OUTPUT) is CapabilityState.SUPPORTED


@pytest.mark.asyncio
async def test_vision_comes_from_the_declared_input_modalities() -> None:
    known = await an_adapter().capabilities("vendor/thinker")

    assert known.state_of(Capability.VISION) is CapabilityState.SUPPORTED


@pytest.mark.asyncio
async def test_the_context_window_is_a_number_not_a_claim() -> None:
    """So it does not go through the provenance ladder — and an unknown one
    fails every `minimum_context`, which is why absent is not zero."""
    known = await an_adapter().capabilities("vendor/thinker")

    assert known.context_window == 200_000
    assert (await an_adapter().capabilities("vendor/plain")).context_window is None


@pytest.mark.asyncio
async def test_an_upstream_that_is_not_openrouter_degrades_to_honest_ignorance() -> None:
    """A failed catalogue read must not be worse than having no adapter."""
    known = await an_adapter(fail=True).capabilities("vendor/thinker")

    assert known.state_of(Capability.REASONING) is CapabilityState.UNKNOWN


@pytest.mark.asyncio
async def test_a_failed_read_is_not_cached() -> None:
    """Caching a blip would hold it against the upstream for the whole window —
    and capability-less fails closed, so it would empty the pools for it."""
    calls: list[int] = []

    def handle(_: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500 if len(calls) == 1 else 200, json={"data": [ENTRY]})

    adapter = OpenRouterAdapter(
        upstream=Upstream(base_url="https://openrouter.ai/api", declared_key="k",
                          api_root="/v1"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )

    assert (await adapter.capabilities("vendor/thinker")).state_of(
        Capability.REASONING
    ) is CapabilityState.UNKNOWN
    assert (await adapter.capabilities("vendor/thinker")).state_of(
        Capability.REASONING
    ) is CapabilityState.SUPPORTED
