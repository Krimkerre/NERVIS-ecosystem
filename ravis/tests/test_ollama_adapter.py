"""The Ollama adapter, and the exact reach of its capability array.

Verified against a live Ollama 0.32.3. The fixtures below are the real
`/api/show` responses for `llama3.2:3b` and `all-minilm`, and the pair is what
establishes that the array *enumerates* rather than annotates: an embedding
model reports `["embedding"]` alone, declining even `completion`.

So absence within the array is a denial — and the tests that matter most here
are the two that bound that reading, keeping it to the tokens Ollama actually
tracks and off the ones it has no vocabulary for.
"""

from __future__ import annotations

from typing import Any

import httpx

from ravis.core.capabilities import Capability, CapabilityState, Provenance
from ravis.providers.base import ProviderAdapter
from ravis.providers.ollama import OllamaAdapter
from ravis.upstream import Upstream

# Real `/api/show` responses, trimmed to what the adapter reads. Kept faithful to
# the live payloads so a change in Ollama's shape breaks a test, not a routing
# decision.
LLAMA_DETAIL = {
    "capabilities": ["completion", "tools"],
    "model_info": {"general.architecture": "llama", "llama.context_length": 131072},
    "details": {"family": "llama", "parameter_size": "3.2B"},
}
MINILM_DETAIL = {
    "capabilities": ["embedding"],
    "model_info": {"general.architecture": "bert", "bert.context_length": 512},
    "details": {"family": "bert"},
}


def _adapter(
    detail: dict[str, Any] | None = None,
    *,
    show_status: int = 200,
    configured: dict[str, dict[str, str]] | None = None,
) -> OllamaAdapter:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            if show_status != 200:
                return httpx.Response(show_status, json={"error": "model not found"})
            return httpx.Response(200, json=detail or {})
        return httpx.Response(200, json={"object": "list", "data": [{"id": "llama3.2:latest"}]})

    return OllamaAdapter(
        upstream=Upstream(base_url="http://ollama.invalid", declared_key=""),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        configured_capabilities=configured,
    )


def test_it_satisfies_the_adapter_protocol() -> None:
    assert isinstance(_adapter(), ProviderAdapter)


def test_it_names_itself_ollama() -> None:
    assert _adapter().name == "ollama"


async def test_advertised_tools_are_recorded_as_advertised() -> None:
    known = await _adapter(LLAMA_DETAIL).capabilities("llama3.2:latest")

    assert known.state_of(Capability.TOOLS) is CapabilityState.SUPPORTED
    assert known.claims[Capability.TOOLS].provenance is Provenance.ADVERTISED


async def test_the_architecture_namespaced_context_length_is_read() -> None:
    """`llama.context_length`, not a scan for anything ending in context_length."""
    known = await _adapter(LLAMA_DETAIL).capabilities("llama3.2:latest")

    assert known.context_window == 131072


async def test_a_context_length_for_another_architecture_is_not_borrowed() -> None:
    """A multi-part model carries more than one window; only its own counts."""
    detail = {
        "capabilities": ["completion"],
        "model_info": {"general.architecture": "qwen3", "llama.context_length": 4096},
    }
    known = await _adapter(detail).capabilities("qwen3:latest")

    assert known.context_window is None


async def test_vision_and_reasoning_tokens_map_onto_the_lattice() -> None:
    detail = {"capabilities": ["completion", "vision", "thinking"], "model_info": {}}
    known = await _adapter(detail).capabilities("llava:latest")

    assert known.state_of(Capability.VISION) is CapabilityState.SUPPORTED
    assert known.state_of(Capability.REASONING) is CapabilityState.SUPPORTED


async def test_an_unrecognised_capability_token_is_ignored_not_guessed() -> None:
    detail = {"capabilities": ["completion", "teleportation"], "model_info": {}}
    known = await _adapter(detail).capabilities("m")

    assert known.state_of(Capability.TEXT) is CapabilityState.SUPPORTED


# ── The restraint, pinned ────────────────────────────────────────────────────


async def test_a_capability_absent_from_a_present_array_is_denied() -> None:
    """The array enumerates, so omission is Ollama saying no."""
    detail = {"capabilities": ["completion"], "model_info": {}}
    known = await _adapter(detail).capabilities("no-tools:latest")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNSUPPORTED
    assert not known.satisfies(Capability.TOOLS)


async def test_a_missing_array_denies_nothing() -> None:
    """No array at all is an older Ollama, or not Ollama. Nothing follows from it."""
    known = await _adapter({"model_info": {}}).capabilities("silent:latest")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN
    assert known.state_of(Capability.VISION) is CapabilityState.UNKNOWN


async def test_a_capability_ollama_has_no_word_for_is_never_denied() -> None:
    """The closed reading covers Ollama's vocabulary, not §9.5's whole lattice.

    An array that was never going to mention structured output must not be read
    as ruling it out — that would cost a pool a candidate on the strength of a
    silence about a subject Ollama does not discuss.
    """
    known = await _adapter(LLAMA_DETAIL).capabilities("llama3.2:3b")

    assert known.state_of(Capability.STRUCTURED_OUTPUT) is CapabilityState.UNKNOWN
    assert known.state_of(Capability.PARALLEL_TOOLS) is CapabilityState.UNKNOWN


async def test_an_embedding_model_is_not_a_text_model() -> None:
    """`all-minilm` reports `["embedding"]` and nothing else, verified live.

    The consequence is the point: without the closed reading TEXT would still be
    SUPPORTED from the protocol default, and an embedding endpoint would sit in
    a text pool waiting to be routed a chat request.
    """
    known = await _adapter(MINILM_DETAIL).capabilities("all-minilm:latest")

    assert known.state_of(Capability.EMBEDDINGS) is CapabilityState.SUPPORTED
    assert known.state_of(Capability.TEXT) is CapabilityState.UNSUPPORTED
    assert known.context_window == 512


async def test_an_upstream_that_is_not_ollama_degrades_to_generic_answers() -> None:
    known = await _adapter(LLAMA_DETAIL, show_status=404).capabilities("llama3.2:latest")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN
    assert known.claims[Capability.TEXT].provenance is Provenance.DEFAULT
    assert known.context_window is None


async def test_an_operator_override_outranks_the_catalogue() -> None:
    adapter = _adapter(LLAMA_DETAIL, configured={"llama3.2:latest": {"tools": "UNSUPPORTED"}})
    known = await adapter.capabilities("llama3.2:latest")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNSUPPORTED
    assert known.claims[Capability.TOOLS].provenance is Provenance.CONFIGURED


# ── The per-model detail cache ───────────────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _counting_adapter(clock: _Clock, *, fail: bool = False) -> tuple[OllamaAdapter, list[int]]:
    calls = [0]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            calls[0] += 1
            if fail:
                raise httpx.ConnectError("upstream is down")
            return httpx.Response(200, json=LLAMA_DETAIL)
        return httpx.Response(200, json={"object": "list", "data": []})

    adapter = OllamaAdapter(
        upstream=Upstream(base_url="http://ollama.invalid", declared_key=""),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        clock=clock,
    )
    return adapter, calls


async def test_a_models_detail_is_asked_for_once() -> None:
    """`/api/show` takes one model, so the cache is per model rather than a list."""
    adapter, calls = _counting_adapter(_Clock())

    for _ in range(3):
        await adapter.capabilities("llama3.2:3b")

    assert calls[0] == 1


async def test_each_model_is_cached_separately() -> None:
    adapter, calls = _counting_adapter(_Clock())

    await adapter.capabilities("llama3.2:3b")
    await adapter.capabilities("all-minilm:latest")

    assert calls[0] == 2


async def test_the_detail_is_re_read_once_the_window_passes() -> None:
    clock = _Clock()
    adapter, calls = _counting_adapter(clock)

    await adapter.capabilities("llama3.2:3b")
    clock.now += 61.0
    await adapter.capabilities("llama3.2:3b")

    assert calls[0] == 2


async def test_a_failed_read_is_not_cached() -> None:
    """Same reasoning as the LM Studio catalogue: a held failure empties pools."""
    adapter, calls = _counting_adapter(_Clock(), fail=True)

    known = await adapter.capabilities("llama3.2:3b")
    await adapter.capabilities("llama3.2:3b")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN
    assert calls[0] == 2
