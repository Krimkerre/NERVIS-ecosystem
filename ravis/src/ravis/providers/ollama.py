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

Second, the capability array is read as **closed** over the vocabulary Ollama
actually tracks: a model whose array omits `tools` is recorded UNSUPPORTED, not
left UNKNOWN. That is a stronger claim than the LM Studio adapter makes about
its own capability list, and it is made on evidence — checked against a live
Ollama 0.32.3, where `llama3.2:3b` reports `["completion", "tools"]` and
`all-minilm` reports `["embedding"]` alone. An embedding model that declines to
claim `completion` is an array that enumerates rather than annotates.

The world is closed over those five tokens **only**. Capabilities Ollama has no
vocabulary for — structured output, parallel tools, prompt caching, audio — are
never denied on the strength of an array that was never going to mention them.
Silence about a subject is not a denial, and conflating the two is how a pool
loses a candidate that would have served it.
"""

from __future__ import annotations

import time
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
from ravis.cost import Price
from ravis.providers.generic_openai import PROTOCOL_DEFAULTS, GenericOpenAiAdapter

# Ollama's native model-detail endpoint. A POST, unlike everything else RAVIS
# reads for discovery, because it takes the model as a body parameter.
SHOW_PATH = "/api/show"

# Ollama's capability vocabulary, mapped onto §9.5's lattice. Tokens it reports
# that are not listed here are ignored rather than guessed at — and, just as
# importantly, this is exactly the set the closed-world reading in `_absorb`
# applies to. Adding a row here widens what an absent entry is taken to deny,
# so a row is only correct once Ollama is known to report that token.
_CAPABILITY_MAP = {
    "completion": Capability.TEXT,
    "tools": Capability.TOOLS,
    "vision": Capability.VISION,
    "embedding": Capability.EMBEDDINGS,
    "thinking": Capability.REASONING,
}

_ADVERTISED = "advertised by Ollama at /api/show"

# How long one model's detail answers for. The routing path asks every candidate
# for its capabilities on every request, and `/api/show` is a POST taking one
# model — so without this a chat completion costs one round trip per installed
# model. What is read here describes a *tag*, which does not change while it
# sits in the store.
DETAIL_TTL_SECONDS = 60.0


class OllamaAdapter(GenericOpenAiAdapter):
    """Discovery for an Ollama upstream."""

    def __init__(
        self,
        *args: Any,
        detail_ttl_seconds: float = DETAIL_TTL_SECONDS,
        clock: Any = time.monotonic,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("name", "ollama")
        super().__init__(*args, **kwargs)
        self._ttl = detail_ttl_seconds
        self._clock = clock
        self._details: dict[str, tuple[float, dict[str, Any]]] = {}

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
        # Nothing per token. Not an estimate and not a default — a model
        # running on hardware the operator already owns bills nothing for a
        # token, and it is the strongest argument a router has for reaching
        # here before reaching for a paid API.
        known.price_per_million = 0.0
        # §14's split price as well as the ranking figure. Without it a local
        # call reported cost UNKNOWN — while RAVIS knew perfectly well it was
        # free — which is the same conflation of "free" and "unpriced" the cost
        # engine exists to prevent, arrived at from the other direction.
        known.price = Price(
            input_per_million=0.0,
            output_per_million=0.0,
            source="ollama/local",
            captured_at=time.time(),
        )
        apply_configured(known, self._configured.get(model, {}))
        return known

    async def _show(self, model: str) -> dict[str, Any] | None:
        """Ollama's detail for one model, or `None` if it cannot be had.

        As with LM Studio, every failure is `None` rather than an exception: an
        upstream that is not Ollama 404s here, and the caller then gets the
        generic adapter's honest ignorance.

        Failures are not cached, for the same reason they are not there. A
        cached failure would hold a transient outage against the upstream for
        the whole window, and capability-less fails closed — so a blip would
        empty the pools for a minute rather than for a request.
        """
        now = self._clock()
        cached = self._details.get(model)
        if cached is not None and now - cached[0] < self._ttl:
            return cached[1]
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
        if not isinstance(payload, dict):
            return None
        self._details[model] = (now, payload)
        return payload


def _absorb(known: ModelCapabilities, detail: dict[str, Any]) -> None:
    """Record what Ollama says about one model, at ADVERTISED provenance.

    The array is read as closed over `_CAPABILITY_MAP`'s tokens: present means
    SUPPORTED, absent means UNSUPPORTED. Verified against a live Ollama — see the
    module docstring for the two models that establish it.

    A *missing* array is different from an empty one and is handled as such. No
    array at all means this endpoint answered `/api/show` without the field —
    an older Ollama, or something that is not Ollama — and nothing may be
    concluded from it. An array that is present and empty is Ollama saying the
    model does nothing it tracks, which the closed reading denies capability by
    capability.
    """
    advertised = detail.get("capabilities")
    if isinstance(advertised, list):
        tokens = {str(token) for token in advertised}
        for token, capability in _CAPABILITY_MAP.items():
            supported = token in tokens
            known.record(
                CapabilityClaim(
                    capability=capability,
                    state=(
                        CapabilityState.SUPPORTED if supported else CapabilityState.UNSUPPORTED
                    ),
                    provenance=Provenance.ADVERTISED,
                    detail=f"{_ADVERTISED}: {token}"
                    + ("" if supported else " absent from a list that enumerates"),
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
