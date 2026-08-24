"""The Ollama adapter, and the claims it deliberately declines to make.

Written against Ollama's documented `/api/show` shape and **not yet run against
a live instance**, which is why the adapter under test only ever claims support
and never denies it. These tests pin that restraint so it is a decision rather
than an omission — and so tightening it later has to be deliberate.
"""

from __future__ import annotations

from typing import Any

import httpx

from ravis.core.capabilities import Capability, CapabilityState, Provenance
from ravis.providers.base import ProviderAdapter
from ravis.providers.ollama import OllamaAdapter
from ravis.upstream import Upstream

# Ollama's documented `/api/show` response, trimmed to what the adapter reads.
LLAMA_DETAIL = {
    "capabilities": ["completion", "tools"],
    "model_info": {"general.architecture": "llama", "llama.context_length": 131072},
    "details": {"family": "llama", "parameter_size": "3.2B"},
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
        upstream=Upstream(base_url="http://ollama.invalid", api_key=""),
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


async def test_a_capability_absent_from_the_array_stays_unknown() -> None:
    """Deliberate under-claiming while this adapter is unverified against a live
    Ollama. Tightening this to UNSUPPORTED is a decision to take with a real
    instance in front of you, not a default to drift into."""
    detail = {"capabilities": ["completion"], "model_info": {}}
    known = await _adapter(detail).capabilities("no-tools:latest")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN
    assert not known.satisfies(Capability.TOOLS)


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
