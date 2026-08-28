"""The adapter for an LM Studio upstream.

LM Studio speaks the OpenAI chat-completions protocol, so everything about
*reaching* it is the generic adapter's job and this subclasses it rather than
restating it. What is new is that LM Studio will answer questions about its
models: `/api/v0/models` reports a capability list, a context length and a type,
where `/v1/models` reports an identifier and nothing else.

That turns most of `GenericOpenAiAdapter`'s honest `UNKNOWN`s into ADVERTISED
claims, which is the whole point of a vendor adapter — and it is also why the
provenance ordering matters more here than anywhere else so far. Measured
against this machine's own corpus, LM Studio advertises `tool_use` for **both**
packagings of granite-4.0-h-tiny, while SIRVIS measured the GGUF build passing
24 of 24 tool trials and the MLX build passing 3. The catalogue is wrong about
one of them.

So nothing here is recorded above `ADVERTISED`, and SIRVIS's `MEASURED` evidence
outranks all of it (§9.5). An advertisement is a starting point, not a verdict.

The residency probe for the same endpoint lives in `runtime/lmstudio.py` and
stays there: it answers "what is loaded right now", which is a fact about the
runtime rather than about a model, and M14 consumes it on a different path.
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
from ravis.runtime.lmstudio import RESIDENCY_PATH

# LM Studio's token for tool support inside its `capabilities` array.
TOOL_USE = "tool_use"

# LM Studio's `type` field. It distinguishes a vision model from a text-only one
# and an embedding model from either, which is authoritative enough to claim on:
# the runtime that loads the weights is the thing that knows what they are.
TYPE_VISION = "vlm"
TYPE_EMBEDDINGS = "embeddings"

_ADVERTISED = "advertised by LM Studio at /api/v0/models"

# How long one read of the catalogue answers for. The routing path asks for
# every candidate's capabilities on every request, so without this a single
# chat completion costs one GET per installed model — 20 of them on the
# developer's machine. The fields read here are properties of a *build* and do
# not change while it sits on disk, so a short window costs nothing real.
#
# Residency also lives in this payload and does change, which is why the
# residency probe in `runtime/lmstudio.py` reads the endpoint itself rather than
# sharing this cache.
CATALOGUE_TTL_SECONDS = 60.0


class LmStudioAdapter(GenericOpenAiAdapter):
    """Discovery for an LM Studio upstream.

    Inherits `health`, `models`, `estimate_cost` and header handling unchanged —
    LM Studio answers `/v1/models` like any OpenAI-compatible endpoint, and a
    second implementation of those would be three more places to drift.
    """

    def __init__(
        self,
        *args: Any,
        catalogue_ttl_seconds: float = CATALOGUE_TTL_SECONDS,
        clock: Any = time.monotonic,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("name", "lmstudio")
        super().__init__(*args, **kwargs)
        self._ttl = catalogue_ttl_seconds
        self._clock = clock
        self._catalogue: dict[str, dict[str, Any]] = {}
        self._fetched_at: float | None = None

    async def capabilities(self, model: str) -> ModelCapabilities:
        """Protocol defaults, then LM Studio's own metadata, then configuration.

        Strictly in that order, and each layer only wins where it outranks what
        is already held. An upstream that turns out not to be LM Studio after all
        degrades to exactly the generic adapter's answers rather than failing.
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
        entry = await self._describe(model)
        if entry is not None:
            _absorb(known, entry)
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
            source="lmstudio/local",
            captured_at=time.time(),
        )
        apply_configured(known, self._configured.get(model, {}))
        return known

    async def _describe(self, model: str) -> dict[str, Any] | None:
        """LM Studio's entry for one model, or `None` if it cannot be had."""
        catalogue = await self._read_catalogue()
        return catalogue.get(model)

    async def _read_catalogue(self) -> dict[str, dict[str, Any]]:
        """Every model LM Studio knows about, keyed by id, cached for `_ttl`.

        Every failure yields an empty catalogue rather than raising. An upstream
        that is not LM Studio 404s here, and that is an ordinary configuration
        rather than a fault — the caller then gets the generic adapter's honest
        ignorance, which is the correct answer for an endpoint that never
        claimed to be anything more.

        A failed read is *not* cached. Caching it would hold a transient outage
        against the upstream for the whole window, turning a blip into a minute
        of every model looking capability-less — and capability-less fails
        closed, so the blip would empty the pools.
        """
        now = self._clock()
        if self._fetched_at is not None and now - self._fetched_at < self._ttl:
            return self._catalogue
        if not self._upstream.is_configured:
            return {}
        try:
            response = await self._client.get(
                self._upstream.url_for(RESIDENCY_PATH), headers=self._headers()
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return {}
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        self._catalogue = {
            entry["id"]: entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("id")
        }
        self._fetched_at = now
        return self._catalogue


def _absorb(known: ModelCapabilities, entry: dict[str, Any]) -> None:
    """Record what LM Studio says about one model, at ADVERTISED provenance."""
    _absorb_tools(known, entry)
    _absorb_modality(known, entry)
    context = entry.get("max_context_length")
    if isinstance(context, int) and context > 0:
        known.context_window = context


def _absorb_tools(known: ModelCapabilities, entry: dict[str, Any]) -> None:
    """Tool support — but only ever as a *positive* claim.

    A missing `capabilities` array means LM Studio did not say, not that the
    answer is no: on this machine it is absent for 8 of 20 installed models and
    present for the rest. Recording UNSUPPORTED from silence would assert a fact
    nobody stated, so silence is left to read as UNKNOWN, which §9.1 already
    fails closed against a pool that requires tools.

    An empty array is treated the same way rather than as a denial. The two are
    indistinguishable from here — a field defaulted to empty and a field that was
    populated with nothing look identical — and inventing a denial from an
    ambiguity is the mistake this whole module is careful about.
    """
    advertised = entry.get("capabilities")
    if not isinstance(advertised, list) or TOOL_USE not in advertised:
        return
    known.record(
        CapabilityClaim(
            capability=Capability.TOOLS,
            state=CapabilityState.SUPPORTED,
            provenance=Provenance.ADVERTISED,
            detail=_ADVERTISED,
        )
    )


def _absorb_modality(known: ModelCapabilities, entry: dict[str, Any]) -> None:
    """What kind of model this is, from LM Studio's `type`.

    Unlike the capability array, `type` is always present and is a closed choice
    rather than an optional annotation, so both directions can be claimed from
    it. Claiming UNSUPPORTED here fails *closed* — the worst case is a pool that
    reports itself unavailable, not a request routed to a model that cannot serve
    it — and any of it is overridden by measurement or by an operator.
    """
    kind = str(entry.get("type", ""))
    if not kind:
        return
    vision = CapabilityState.SUPPORTED if kind == TYPE_VISION else CapabilityState.UNSUPPORTED
    known.record(
        CapabilityClaim(
            capability=Capability.VISION,
            state=vision,
            provenance=Provenance.ADVERTISED,
            detail=f"{_ADVERTISED}: type={kind}",
        )
    )
    embeddings = kind == TYPE_EMBEDDINGS
    known.record(
        CapabilityClaim(
            capability=Capability.EMBEDDINGS,
            state=CapabilityState.SUPPORTED if embeddings else CapabilityState.UNSUPPORTED,
            provenance=Provenance.ADVERTISED,
            detail=f"{_ADVERTISED}: type={kind}",
        )
    )
    # An embedding model is not a chat model. Said explicitly because the
    # protocol default above already recorded TEXT as supported, and leaving that
    # standing would put an embedding endpoint in a text pool.
    known.record(
        CapabilityClaim(
            capability=Capability.TEXT,
            state=CapabilityState.UNSUPPORTED if embeddings else CapabilityState.SUPPORTED,
            provenance=Provenance.ADVERTISED,
            detail=f"{_ADVERTISED}: type={kind}",
        )
    )
