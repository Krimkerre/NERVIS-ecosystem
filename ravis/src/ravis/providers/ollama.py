"""The adapter for an Ollama upstream.

Ollama serves an OpenAI-compatible surface at `/v1`, so reaching it is again the
generic adapter's job. What it adds is `POST /api/show`, which returns a
capability list and a `model_info` block carrying the architecture's context
length — neither of which `/v1/models` will tell anyone.

**Two differences from the LM Studio adapter, both deliberate.**

First, `/api/show` is a POST taking one model, where LM Studio's catalogue
returns every model in a single GET. So this asks per model rather than
filtering a list, which is one request per capability lookup and is why the
result is not consulted anywhere hotter than pool admission.

Second, and more importantly: this adapter makes **only positive claims**, where
the LM Studio one also claims UNSUPPORTED from its `type` field. Ollama's
capability array is documented as enumerating what a model can do, which would
make an absent entry a denial — but unlike the LM Studio adapter, nothing here
has been run against a live instance. Under-claiming costs a pool that reports
itself unavailable; over-claiming routes a request to a model that cannot serve
it. Until this has been checked against a real Ollama, it fails closed on
purpose, and the docstring on `_absorb` says what to tighten afterwards.
"""

from __future__ import annotations

from typing import Any

import httpx

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
    apply_configured,
)
from ravis.providers.generic_openai import PROTOCOL_DEFAULTS, GenericOpenAiAdapter

# Ollama's native model-detail endpoint. A POST, unlike everything else RAVIS
# reads for discovery, because it takes the model as a body parameter.
SHOW_PATH = "/api/show"

# Ollama's capability vocabulary, mapped onto §9.5's lattice. Tokens it reports
# that are not listed here are ignored rather than guessed at.
_CAPABILITY_MAP = {
    "completion": Capability.TEXT,
    "tools": Capability.TOOLS,
    "vision": Capability.VISION,
    "embedding": Capability.EMBEDDINGS,
    "thinking": Capability.REASONING,
}

_ADVERTISED = "advertised by Ollama at /api/show"


class OllamaAdapter(GenericOpenAiAdapter):
    """Discovery for an Ollama upstream."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("name", "ollama")
        super().__init__(*args, **kwargs)

    async def capabilities(self, model: str) -> ModelCapabilities:
        """Protocol defaults, then Ollama's own metadata, then configuration."""
        known = ModelCapabilities(model_id=model)
        for capability, state in PROTOCOL_DEFAULTS.items():
            known.record(
                CapabilityClaim(
                    capability=capability,
                    state=state,
                    provenance=Provenance.DEFAULT,
                    detail="implied by the OpenAI chat-completions protocol",
                )
            )
        detail = await self._show(model)
        if detail is not None:
            _absorb(known, detail)
        apply_configured(known, self._configured.get(model, {}))
        return known

    async def _show(self, model: str) -> dict[str, Any] | None:
        """Ollama's detail for one model, or `None` if it cannot be had.

        As with LM Studio, every failure is `None` rather than an exception: an
        upstream that is not Ollama 404s here, and the caller then gets the
        generic adapter's honest ignorance.
        """
        if not self._upstream.is_configured:
            return None
        try:
            response = await self._client.post(
                self._upstream.url_for(SHOW_PATH),
                headers=self._headers(),
                json={"model": model},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None


def _absorb(known: ModelCapabilities, detail: dict[str, Any]) -> None:
    """Record what Ollama says about one model, at ADVERTISED provenance.

    Positive claims only, for the reason in the module docstring. Once this has
    been verified against a live Ollama, the tightening is to treat a *present*
    capability array as closed — absence of `tools` in it then becomes an
    UNSUPPORTED claim rather than silence, which is what makes a route
    explanation able to say "Ollama says no" instead of "nobody said".
    """
    advertised = detail.get("capabilities")
    if isinstance(advertised, list):
        for token in advertised:
            capability = _CAPABILITY_MAP.get(str(token))
            if capability is None:
                continue
            known.record(
                CapabilityClaim(
                    capability=capability,
                    state=CapabilityState.SUPPORTED,
                    provenance=Provenance.ADVERTISED,
                    detail=f"{_ADVERTISED}: {token}",
                )
            )
    context = _context_length(detail.get("model_info"))
    if context is not None:
        known.context_window = context


def _context_length(model_info: Any) -> int | None:
    """The architecture's context length out of Ollama's `model_info` block.

    The key is namespaced by architecture — `llama.context_length`,
    `qwen3.context_length` — so the architecture has to be read first rather
    than the block scanned for anything ending in `context_length`. Scanning
    would pick up a projector's or an adapter's window on a multi-part model and
    report it as the model's own.
    """
    if not isinstance(model_info, dict):
        return None
    architecture = model_info.get("general.architecture")
    if not isinstance(architecture, str) or not architecture:
        return None
    value = model_info.get(f"{architecture}.context_length")
    return value if isinstance(value, int) and value > 0 else None
