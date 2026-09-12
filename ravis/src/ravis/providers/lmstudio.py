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
# developer's machine. Most fields read here are properties of a *build* and do
# not change while it sits on disk, so a short window costs them nothing real.
#
# **One field read here does change: `loaded_context_length`** (see
# `_served_window`), so a load or an unload reaches routing up to a minute late.
# A model loaded since the last read keeps its cold default for that minute,
# which under-reports and is safe; a model unloaded since keeps the window it
# was loaded with, which over-reports if that was larger than the default. The
# Ollama adapter caches `/api/ps` for the same minute and accepts the same
# trade, and a shorter window would put a catalogue GET back on most requests.
#
# Residency lives in this payload too, which is why the residency probe in
# `runtime/lmstudio.py` reads the endpoint itself rather than sharing this cache.
CATALOGUE_TTL_SECONDS = 60.0

# LM Studio's word, in `state`, for a model in memory right now — the same
# vocabulary `runtime/lmstudio.py` maps to HOT.
STATE_LOADED = "loaded"

#: The window LM Studio opens a model with when a request arrives for one that
#: is not loaded.
#:
#: **Not the build's ceiling, which is the bug this constant exists for.**
#: `/api/v0/models` publishes `max_context_length` for every build and
#: `loaded_context_length` only for a model in memory. What a cold model will
#: get is LM Studio's own `defaultContextLength` setting — `lms load --help`
#: says the same of its flag, "If not provided, the default value will be
#: used" — and nothing in the HTTP API reports that setting.
#:
#: **8,192 is what this machine's LM Studio does, read rather than guessed**, on
#: 12 September 2026. Its settings hold `defaultContextLength` as a custom
#: 8,192; all 91 llama.cpp loads its server logs record, 20 August to
#: 12 September, opened `n_ctx_slot = 8192`; and a load triggered by RAVIS's own
#: routing call put `exaone-deep-2.4b`, whose ceiling is 32,768, in memory at
#: 8,192 (STATUS.md, the §16 item 12 run). One record points the other way —
#: M9's extra copies of `qwen2.5-coder-7b-instruct` at 32,768 — and it does not
#: say what loaded them. LM Studio's vision builds ignore a requested length and
#: load at their own (`gemma-4-e2b` at 131,072), so for those this under-reports.
#:
#: **Under-reporting is the chosen direction, as it is for Ollama** (RAVIS.md
#: §9.8's closing paragraph): it costs a cold model a long-context pool until
#: something loads it. Over-reporting routes a long prompt to a model that will
#: be opened at a fraction of it — the silent truncation RAVIS.md §10 forbids.
#: A deployment whose LM Studio default differs passes its own `default_context`.
DEFAULT_CONTEXT = 8192


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
        default_context: int = DEFAULT_CONTEXT,
        clock: Any = time.monotonic,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("name", "lmstudio")
        super().__init__(*args, **kwargs)
        self._ttl = catalogue_ttl_seconds
        # A parameter rather than a read of the constant, the same shape as the
        # Ollama adapter's, because the figure is a setting inside LM Studio and
        # a deployment that changed it has to be able to say so.
        self._default_context = default_context
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
            _absorb(known, entry, self._default_context)
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


def _absorb(known: ModelCapabilities, entry: dict[str, Any], default_context: int) -> None:
    """Record what LM Studio says about one model, at ADVERTISED provenance."""
    _absorb_tools(known, entry)
    _absorb_modality(known, entry)
    window = _served_window(entry, default_context)
    if window is not None:
        known.context_window = window


def _served_window(entry: dict[str, Any], default_context: int) -> int | None:
    """The context this model will actually be served with.

    **What LM Studio loaded, not what the build allows** — the correction the
    Ollama adapter already makes, for the same reason (RAVIS.md §9.8's closing
    paragraph). This used to report `max_context_length`, the build's ceiling:
    `qwen2.5-coder-7b-instruct` advertised 32,768 while loaded at 8,192, so the
    agent pool's 32,768 minimum admitted it on the strength of a configuration
    that was not running, and LM Studio loaded further copies to cover the
    difference (STATUS.md, "A model's advertised context is not the context it
    is loaded with").

    A loaded model's window is `loaded_context_length`. A cold model has none
    yet, and the one it will get is `DEFAULT_CONTEXT`, so that is reported. Both
    are capped by the ceiling: a window larger than the build can address is not
    a window, and `min` keeps the answer inside what is true either way.

    **The loaded length is believed only on an entry in state `loaded`.** It has
    only ever been seen there. A version that left it on an unloaded entry would
    over-report, the dangerous direction; a model still `loading` is reported at
    the default for the seconds that takes, the safe one.

    **Unknown stays unknown.** No ceiling and nothing loaded is `None`, as it
    was: this adapter answers for upstreams that may not be LM Studio at all,
    and a default is a correction to a window RAVIS knows, not a claim about one
    it does not.
    """
    ceiling = _positive(entry.get("max_context_length"))
    loaded = _positive(entry.get("loaded_context_length"))
    if entry.get("state") == STATE_LOADED and loaded is not None:
        return loaded if ceiling is None else min(loaded, ceiling)
    return None if ceiling is None else min(ceiling, default_context)


def _positive(value: Any) -> int | None:
    """A context length out of the payload, or `None` for anything that is not one."""
    return value if isinstance(value, int) and value > 0 else None


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
