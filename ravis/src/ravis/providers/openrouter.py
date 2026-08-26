"""Discovery for an OpenRouter upstream, which publishes what it can do.

**The reasoning pool was empty and this is why.** `Capability.REASONING` fails
closed, so a model RAVIS knows nothing about is not in `ravis/reasoning` — which
is correct, and left the pool empty on a machine with six hundred API models
because none of them had been asked. The tempting fix is a list of name
fragments: `o1`, `-thinking`, `-reasoner`. That is a guess dressed as a fact,
and §9.4 rules it out for exactly the reason it is tempting — it would look
right most of the time.

There is no need to guess here. OpenRouter's `/models` carries
`supported_parameters` per model, listing the request parameters that model
actually accepts, and `reasoning` is one of them. That is the provider stating
its own capability, which is `ADVERTISED` — weaker than a measurement and far
stronger than a substring, and it is already the provenance LM Studio's
metadata gets.

**Only OpenRouter publishes this.** OpenAI's `/v1/models` carries `id`,
`created`, `owned_by` and a shutdown date; Google's carries `id` and a display
name. Neither says anything about capability, so neither gets an adapter — an
adapter that read fields that are not there would be this file with the
honesty removed.
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
from ravis.providers.generic_openai import PROTOCOL_DEFAULTS, GenericOpenAiAdapter

# How long a catalogue read is believed. OpenRouter's list is four hundred
# models and changes daily rather than by the second, so this is about not
# fetching it once per routing pass rather than about freshness.
CATALOGUE_TTL_SECONDS = 300.0

# Which `supported_parameters` entry evidences which capability.
#
# Each is a parameter the *request* may carry, so its presence means the model
# accepts that mode of use — which is what a pool invariant asks about. Absence
# is left UNKNOWN rather than recorded as unsupported: OpenRouter lists what it
# knows a model takes, and a parameter missing from that list is as likely to be
# unlisted as unsupported. Recording a negative from silence is the same error
# as recording a positive from a substring.
PARAMETER_EVIDENCE: dict[str, Capability] = {
    "reasoning": Capability.REASONING,
    "tools": Capability.TOOLS,
    "structured_outputs": Capability.STRUCTURED_OUTPUT,
    "response_format": Capability.STRUCTURED_OUTPUT,
}

# Modalities OpenRouter names in `architecture.input_modalities`.
MODALITY_EVIDENCE: dict[str, Capability] = {
    "image": Capability.VISION,
    "audio": Capability.AUDIO_IN,
}


class OpenRouterAdapter(GenericOpenAiAdapter):
    """Reads OpenRouter's own capability metadata.

    Inherits `health`, `models`, headers and relaying unchanged — OpenRouter
    answers `/v1/chat/completions` like any OpenAI-compatible endpoint, and the
    only thing it does differently is tell you more about its catalogue.
    """

    def __init__(
        self,
        *args: Any,
        catalogue_ttl_seconds: float = CATALOGUE_TTL_SECONDS,
        clock: Any = time.monotonic,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("name", "openrouter")
        super().__init__(*args, **kwargs)
        self._ttl = catalogue_ttl_seconds
        self._clock = clock
        self._catalogue: dict[str, dict[str, Any]] = {}
        self._fetched_at: float | None = None

    async def capabilities(self, model: str) -> ModelCapabilities:
        """Protocol defaults, then OpenRouter's metadata, then configuration.

        The same order LM Studio's adapter uses, and each layer only wins where
        it outranks what is held. An upstream that turns out not to be
        OpenRouter degrades to the generic adapter's honest ignorance.
        """
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
        entry = (await self._read_catalogue()).get(model)
        if entry is not None:
            _absorb(known, entry)
        apply_configured(known, self._configured.get(model, {}))
        return known

    async def _read_catalogue(self) -> dict[str, dict[str, Any]]:
        """Every model OpenRouter describes, keyed by id, cached for `_ttl`.

        A failed read yields an empty catalogue and is **not** cached. Caching
        it would hold a transient outage against the upstream for the whole
        window — and capability-less fails closed, so a blip would empty the
        pools for five minutes.
        """
        now = self._clock()
        if self._fetched_at is not None and now - self._fetched_at < self._ttl:
            return self._catalogue
        if not self._upstream.is_configured:
            return {}
        try:
            response = await self._client.get(
                self._upstream.api_url("/models"), headers=self._headers()
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return {}
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        self._catalogue = {
            str(entry["id"]): entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        }
        self._fetched_at = now
        return self._catalogue


def _absorb_price(known: ModelCapabilities, pricing: Any) -> None:
    """What a million tokens costs, from OpenRouter's own per-token figures.

    Published as strings of dollars *per token* — `"0.0000004"` — which is
    exact and unreadable, so it is scaled to a million and kept as a float.

    **Prompt plus completion, summed.** Blending them by an assumed input/output
    ratio would be inventing the shape of a workload nobody described; summing
    them is a defined quantity — the cost of a million tokens in and a million
    out — and it orders models the same way any fixed ratio would, which is all
    a ranking needs.

    A free model prices at `0.0`, and OpenRouter has hundreds of those. That is
    the same zero a local model gets, which is correct: neither costs money per
    token. What separates them afterwards is reach, not price.
    """
    if not isinstance(pricing, dict):
        return
    try:
        prompt = float(pricing.get("prompt") or 0.0)
        completion = float(pricing.get("completion") or 0.0)
    except (TypeError, ValueError):
        return
    if prompt < 0 or completion < 0:
        # OpenRouter uses -1 for "ask the provider". Not a price, and treating
        # it as one would make those models the cheapest in the catalogue.
        return
    known.price_per_million = (prompt + completion) * 1_000_000


def _absorb(known: ModelCapabilities, entry: dict[str, Any]) -> None:
    """Fold one OpenRouter catalogue entry into what is known about the model."""
    supported = entry.get("supported_parameters")
    for parameter in supported if isinstance(supported, list) else []:
        capability = PARAMETER_EVIDENCE.get(str(parameter))
        if capability is not None:
            known.record(
                CapabilityClaim(
                    capability=capability,
                    state=CapabilityState.SUPPORTED,
                    provenance=Provenance.ADVERTISED,
                    detail=f"OpenRouter lists {parameter!r} among supported_parameters",
                )
            )
    architecture = entry.get("architecture")
    modalities = (architecture or {}).get("input_modalities")
    for modality in modalities if isinstance(modalities, list) else []:
        capability = MODALITY_EVIDENCE.get(str(modality))
        if capability is not None:
            known.record(
                CapabilityClaim(
                    capability=capability,
                    state=CapabilityState.SUPPORTED,
                    provenance=Provenance.ADVERTISED,
                    detail=f"OpenRouter lists {modality!r} among input modalities",
                )
            )
    _absorb_price(known, entry.get("pricing"))
    context = entry.get("context_length")
    # A context window is a *number*, not a claim, so it does not go through the
    # provenance ladder — and an unknown one fails every `minimum_context`,
    # which is why leaving it absent is not the same as leaving it at zero.
    if isinstance(context, int) and context > 0:
        known.context_window = context
