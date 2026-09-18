"""The catalogue one routing pass reads: every upstream's models, as they are now.

Extracted from the chat path when a second reader needed the same thing. That
second reader is replay (§M21's dry run), which answers *what would RAVIS choose
for this request today?* — a question that is only worth asking if "today" means
exactly what an ordinary request would see. Two assemblies would drift, and the
one that drifted would be the one nobody was watching, so the replay would
quietly start answering about a catalogue no request ever routes against.

Nothing here decides anything. It reads the adapters, the residency they
publish, and the price book, and hands back what the engine's caller has to
supply. Every function takes the request only to reach `app.state`, which is
where the adapters live; none of them reads the request's body, and replay has
no body to read.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from ravis.core.capabilities import ModelCapabilities
from ravis.core.pools import direct_provider, is_pool_id
from ravis.cost import PriceBook, price_from_book
from ravis.evidence.sirvis import candidates_with_evidence
from ravis.providers.base import ProviderAdapter
from ravis.registry import ModelRegistry
from ravis.reliability.failures import HealthScope
from ravis.runtime.residency import ResidencySnapshot
from ravis.transparent import (
    TransparentUpstream,
    merged_candidates,
    merged_residency,
    remote_models,
    resolve,
    translated_candidates,
)

# What a provider is called when a deployment declares only the singular
# upstream settings. Kept here with `provider_of`, which is the only thing that
# has to reach for it.
UPSTREAM_PROVIDER = "upstream"


@dataclass(frozen=True)
class Catalogue:
    """What every upstream offers right now, and where each candidate lives."""

    candidates: dict[str, ModelCapabilities]
    residency: ResidencySnapshot
    # Which candidates would leave this machine. `ravis/local` and
    # `ravis/private` are refusals rather than preferences, so the engine has to
    # be told — it has no way to know from a capability record.
    remote: frozenset[str]
    # Which translating adapter serves each translated model, so a selection can
    # be attributed back to the adapter that will carry it.
    translated_owners: dict[str, str]


def disabled_providers(request: Request) -> frozenset[str]:
    """Providers an operator has switched off (M10).

    Read per request rather than cached: the file is small and local, and a
    toggle that only took effect after a restart would be a toggle nobody
    trusts during an incident.
    """
    state = getattr(request.app.state, "provider_state", None)
    return frozenset(state.disabled()) if state else frozenset()


def model_filters(request: Request) -> dict[str, Any] | None:
    """Each provider's model filter, or None when nothing is configured."""
    filters = getattr(request.app.state, "model_filters", None)
    return filters.all() if filters else None


def harvest_prices(request: Request, candidates: dict[str, ModelCapabilities]) -> None:
    """Record every published price this catalogue carried.

    Only models that actually publish one: a model with no price is left absent
    from the book rather than entered as free, which is the distinction the
    whole cost engine rests on.
    """
    prices: PriceBook | None = getattr(request.app.state, "prices", None)
    if prices is None:
        return
    for model, known in candidates.items():
        if known.price is not None:
            prices.record(model, known.price)


async def direct_providers(request: Request) -> frozenset[str]:
    """Vendors this machine can buy from at the source, right now.

    **The set the reseller rule is allowed to prefer**, and every word of the
    condition is load-bearing. A vendor belongs here only if it is configured,
    its circuit is closed, *and it actually lists models*. The rule refuses an
    aggregator's copy, so a vendor that cannot serve the request is not a reason
    to refuse the only route that can.

    That third condition is not hypothetical. On this machine `google` is
    configured and credentialed and publishes **no catalogue at all**, while
    OpenRouter offers 43 `google/*` models. Without the check the rule would
    have refused all 43 in favour of a provider with nothing behind it, and
    Gemini would have become unreachable — a price preference turned into an
    outage, which is exactly what the fail-open clause exists to prevent.

    Asking the adapters is cheap: their discovery sits behind a TTL, so this is
    a dictionary lookup in the ordinary case, and a listing that fails leaves
    the vendor out — the safe direction.
    """
    translating: dict[str, Any] = getattr(request.app.state, "translating", {})
    transparents: dict[str, TransparentUpstream] = getattr(
        request.app.state, "transparents", {}
    )
    health = request.app.state.health

    def closed(name: str) -> bool:
        known = health.known(HealthScope.PROVIDER, name)
        return known is None or known.allows()

    usable = set()
    for name, adapter in translating.items():
        if not closed(name):
            continue
        try:
            if await adapter.models():
                usable.add(name)
        except Exception:  # noqa: BLE001 — a failed listing is not a catalogue
            continue
    for built in transparents.values():
        if closed(built.name) and built.registry.model_ids():
            usable.add(built.name)
    return frozenset(usable)


def provider_of(request: Request) -> Callable[[str], str]:
    """Resolve a model to the provider that will actually serve it.

    Used for policy, provider health and session attribution — three readers of
    one question, and the answer has to be the same one execution uses or each
    of them describes a request that did not happen.

    **The owner map is consulted, and its absence was a policy bypass.** A
    translated provider's models reach the candidate set as bare ids —
    `claude-audit-1`, not `ravis/anthropic/claude-audit-1` — and this resolved
    only the explicit form, falling through to the transparent upstreams for
    everything else. So the same model answered `anthropic` at execution
    (`_translating_for` reads `request.state.translated_owners`) and `default`
    here, and a deny-list naming `anthropic` was compared against `default`,
    matched nothing, and let the request through. Found by an external audit on
    9 September 2026; `test_hard_constraints_route.py` holds the reproduction.

    The same wrong answer credited a successful Anthropic call to `default` in
    the health record, and the accounting path had already been fixed for it
    separately — `owner_of` below reads the map exactly as this now does, which
    is the tell that one resolution should have served both.

    Read lazily: the map is put on the request after this closure is built.

    Falls back to the single label when nothing is declared, so a deployment
    using the singular settings keeps exactly the health record it had.
    """
    transparents: dict[str, TransparentUpstream] = getattr(
        request.app.state, "transparents", {}
    )
    filters = model_filters(request)

    def provider(model: str) -> str:
        translating: dict[str, Any] = getattr(request.app.state, "translating", {})
        addressed = direct_provider(model)
        if addressed is not None and addressed in translating:
            return addressed
        # Before the transparent upstreams, matching `_translating_for`: a model
        # a translated provider owns is served by that provider whatever a
        # transparent catalogue happens to also list.
        owned = getattr(request.state, "translated_owners", {}).get(model)
        if owned is not None:
            return str(owned)
        if not transparents:
            return UPSTREAM_PROVIDER
        built = resolve(transparents, model, filters)
        return built.name if built else UPSTREAM_PROVIDER

    return provider


def chosen_models(request: Request, requested: str) -> tuple[str, ...]:
    """The models an operator picked for this pool, or empty.

    Empty for anything that is not a pool: a direct address names its target and
    a bare model name is passed through, so neither has a membership list to
    consult.
    """
    membership = getattr(request.app.state, "pool_membership", None)
    if membership is None or not is_pool_id(requested):
        return ()
    return tuple(membership.for_pool(requested))


def observed_ttft(request: Request) -> dict[str, float]:
    """Median TTFT per model, for the models measured often enough to mean it."""
    store = getattr(request.app.state, "observations", None)
    return store.ttft_for_ranking() if store is not None else {}


def reasoning_shares(request: Request, models: Sequence[str]) -> dict[str, float]:
    """SIRVIS's measured reasoning share per candidate, for the ones it has one.

    Only the models this pass is actually considering, and only the shares that
    were counted rather than inferred — `EvidenceStore.reasoning_share` drops
    the rest, along with anything past the staleness window. A build with no
    entry here is not ranked down; see `_reasoning_rank`.
    """
    store = getattr(request.app.state, "evidence", None)
    if store is None:
        return {}
    shares = {model: store.reasoning_share(model) for model in models}
    return {model: share for model, share in shares.items() if share is not None}


def role_evidence(request: Request, models: Sequence[str]) -> dict[str, dict[str, str]]:
    """Which roles SIRVIS has measured each candidate for, and how it did.

    The map a pool's membership is derived from: `{model: {role: state}}`. Only
    the models this pass is considering, and only those with a record — a build
    nobody has measured is absent rather than present with an UNKNOWN, because
    `_admits` reads absence and UNKNOWN the same way and an empty dict is
    cheaper to reason about than one full of nothings.
    """
    store = getattr(request.app.state, "evidence", None)
    if store is None or not hasattr(store, "roles_measured"):
        return {}
    fit = {model: store.roles_measured(model) for model in models}
    return {model: roles for model, roles in fit.items() if roles}


async def assemble(request: Request) -> Catalogue:
    """Every upstream's models, each asked through its own adapter.

    Assembled per pass rather than cached. That is cheap today because the
    generic adapter performs no I/O to answer — it merges protocol defaults with
    operator configuration — and the moment an adapter needs a network call to
    answer, this is the line that has to change.

    With one upstream this is what it always was; with several it is the only
    way a model on LM Studio gets LM Studio's catalogue read for it instead of
    whichever adapter happened to be primary. Translated providers join the
    candidate set: they were absent entirely, so an Anthropic model could not be
    selected by any pool — only addressed directly.
    """
    registry: ModelRegistry = request.app.state.model_registry
    # SIRVIS's measurements with RAVIS's own trials beside them (see `trials.py`).
    evidence = getattr(request.app.state, "capability_evidence", None) or getattr(
        request.app.state, "evidence", None
    )
    transparents: dict[str, TransparentUpstream] = getattr(
        request.app.state, "transparents", {}
    )
    owners: dict[str, str] = {}
    if transparents:
        candidates = await merged_candidates(
            transparents, evidence, disabled_providers(request), model_filters(request)
        )
        residency = merged_residency(transparents)
        translated, owners = await translated_candidates(
            {
                name: adapter
                for name, adapter in getattr(request.app.state, "translating", {}).items()
                if name not in disabled_providers(request)
            },
            evidence,
        )
        for model, known in translated.items():
            candidates.setdefault(model, known)
    else:
        adapter: ProviderAdapter = request.app.state.adapter
        candidates = await candidates_with_evidence(adapter, registry.model_ids(), evidence)
        residency = registry.residency
    # §14's price book, filled from the catalogue this pass already read. Done
    # here rather than on the refresh timer because the capability records are
    # what carry a price, and this is where they are assembled — harvesting it
    # anywhere else would mean reading the catalogue a second time to learn
    # something the first read already knew.
    harvest_prices(request, candidates)
    # And the gaps the catalogue left, from the same book: operator-stated rates
    # for the providers that publish none, so a direct build ranks on its price
    # rather than on its name. See `price_from_book`.
    price_from_book(candidates, getattr(request.app.state, "prices", None))
    return Catalogue(
        candidates=candidates,
        residency=residency,
        remote=remote_models(transparents) | frozenset(owners),
        translated_owners=owners,
    )
