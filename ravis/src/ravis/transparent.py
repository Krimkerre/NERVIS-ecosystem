"""The transparent upstreams RAVIS has actually built, and how a model reaches one.

`upstreams.py` reads what was *declared* and deliberately imports nothing — it is
read by `config.py` during startup checks, and a configuration module that pulled
in adapters and registries would make a syntax error in a provider a
configuration failure.

This is the other half: for each declared upstream, the adapter that discovers it
and the registry that caches its catalogue, plus the rule that decides which one
serves a given model.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ravis.config import Settings, resolved_capabilities
from ravis.core.pools import DEFAULT_POOLS, direct_provider
from ravis.credentials import CredentialStore, credential_for
from ravis.evidence.sirvis import candidates_with_evidence
from ravis.model_filter import ModelFilter
from ravis.providers.generic_openai import GenericOpenAiAdapter
from ravis.providers.lmstudio import DEFAULT_CONTEXT as LMSTUDIO_DEFAULT_CONTEXT
from ravis.providers.lmstudio import LmStudioAdapter
from ravis.providers.ollama import DEFAULT_CONTEXT, OllamaAdapter
from ravis.providers.openrouter import OpenRouterAdapter
from ravis.registry import ModelRegistry
from ravis.runtime.residency import Residency, ResidencySnapshot
from ravis.upstream import Upstream
from ravis.upstreams import (
    UpstreamSpec,
    api_root_for,
    is_local_address,
    upstream_specs,
)

# Which adapter discovers which kind of upstream. All three speak the OpenAI
# protocol, so this changes what RAVIS can *learn*, never how it reaches the
# upstream — §6's forwarding path is identical whichever comes back.
KINDS: dict[str, type[GenericOpenAiAdapter]] = {
    "lmstudio": LmStudioAdapter,
    "openrouter": OpenRouterAdapter,
    "ollama": OllamaAdapter,
    "generic": GenericOpenAiAdapter,
}


@dataclass(frozen=True)
class TransparentUpstream:
    """One upstream, with everything built for it."""

    spec: UpstreamSpec
    upstream: Upstream
    adapter: GenericOpenAiAdapter
    registry: ModelRegistry

    @property
    def name(self) -> str:
        return self.spec.name


def build_transparents(
    settings: Settings,
    client: httpx.AsyncClient,
    credentials: CredentialStore | None = None,
) -> dict[str, TransparentUpstream]:
    """Everything declared, built, keyed by name and in declaration order.

    Ordinary dicts preserve insertion order, and that order is load-bearing —
    it decides which upstream wins when two serve the same model id.

    `credentials` is optional so that the many tests constructing upstreams
    directly keep working, and because an upstream on loopback needs no key at
    all — LM Studio and Ollama are the ordinary case here and neither
    authenticates.
    """
    built: dict[str, TransparentUpstream] = {}
    for spec in upstream_specs(settings):
        upstream = Upstream(
            base_url=spec.base_url,
            declared_key=spec.api_key,
            api_root=api_root_for(spec.kind),
            # A closure rather than a value, so a key typed into the Credentials
            # screen reaches the next request instead of the next restart.
            credential=_resolver(spec, credentials),
        )
        registry = ModelRegistry(
            upstream=upstream,
            client=client,
            ttl_seconds=settings.models_cache_ttl_seconds,
        )
        adapter = adapter_for(spec, upstream, client, settings)
        # An adapter that reads catalogue metadata gets the registry's copy
        # rather than fetching the same document a second time. Built in this
        # order for that reason — the registry has to exist before the adapter
        # can be pointed at it.
        share = getattr(adapter, "use_catalogue", None)
        if callable(share):
            share(lambda registry=registry: registry.snapshot.models)
        built[spec.name] = TransparentUpstream(
            spec=spec, upstream=upstream, adapter=adapter, registry=registry,
        )
    return built


def _resolver(
    spec: UpstreamSpec, credentials: CredentialStore | None
) -> Callable[[], str] | None:
    """How this upstream finds its credential, each time it needs one."""
    if credentials is None:
        return None
    return lambda: _key_for(spec, credentials)


def _key_for(spec: UpstreamSpec, credentials: CredentialStore | None) -> str:
    """This upstream's credential, from the store if one was typed in.

    **Looked up by name and then by kind.** The name is first because it is what
    a deployment chose and the more specific of the two — two Google upstreams
    on different keys is a real arrangement, and only the name distinguishes
    them. The kind is the fallback so that the ordinary case works without
    anybody being told a rule: the Credentials screen seeds a row called
    `google`, and an upstream of kind `google` finds it whatever it was named.

    Falls back to the key written in the declaration itself, which is how every
    deployment predating this supplied one.
    """
    if credentials is None:
        return spec.api_key
    return credential_for(
        credentials, spec.name,
        credential_for(credentials, spec.kind, spec.api_key),
    )


def adapter_for(
    spec: UpstreamSpec,
    upstream: Upstream,
    client: httpx.AsyncClient,
    settings: Settings,
) -> GenericOpenAiAdapter:
    """The adapter that discovers one upstream (M8).

    An unrecognised `kind` yields the generic adapter rather than an error. A
    typo should cost the vendor metadata it would have read — which shows up as
    capabilities staying UNKNOWN and pools failing closed — rather than costing
    the ability to serve anything at all. A malformed *list* is fatal, because
    that one leaves RAVIS not knowing where to send anything.
    """
    adapter = KINDS.get(spec.kind.strip().lower(), GenericOpenAiAdapter)
    extra: dict[str, Any] = {}
    if adapter is OllamaAdapter:
        # Ollama serves a model at its own default window, not the
        # architecture's maximum, and nothing in its API reports what that
        # default is — so an operator who raised it says so here.
        extra["default_context"] = int(
            getattr(settings, "ollama_default_context", 0) or DEFAULT_CONTEXT
        )
    elif adapter is LmStudioAdapter:
        # The same gap in LM Studio: a model it has not loaded yet is opened at
        # LM Studio's default load length, which its API does not publish
        # either. Without this the adapter could only ever assume 8,192, so an
        # owner who raised the default in LM Studio would have RAVIS keep
        # routing long documents away from models that could now hold them.
        # The launcher reads LM Studio's settings and fills this in.
        extra["default_context"] = int(
            getattr(settings, "lmstudio_default_context", 0) or LMSTUDIO_DEFAULT_CONTEXT
        )
    return adapter(
        upstream=upstream,
        client=client,
        name=spec.name,
        configured_capabilities=resolved_capabilities(settings),
        **extra,
    )


def resolve(
    transparents: dict[str, TransparentUpstream],
    requested: str,
    filters: dict[str, ModelFilter] | None = None,
) -> TransparentUpstream | None:
    """Which upstream serves `requested`, or None when none is configured.

    Three rules, in order:

    1. **An address wins.** `ravis/<name>/<model>` names an upstream outright,
       and is the only way to reach a model that two upstreams both serve.
    2. **Otherwise, whoever has it.** The first upstream in declaration order
       whose catalogue contains the id. Order is configuration, so a tie is
       broken by something the operator wrote rather than by dict iteration.
    3. **Otherwise, the first.** A model nobody lists is still forwarded rather
       than refused — a catalogue that has not refreshed yet, or an upstream
       serving a model it does not advertise, should not become a 404 here.
       §5.2 already refuses at the *pool* when no candidate satisfies it; this
       is the direct-address path, where the client named something specific.
    """
    if not transparents:
        return None
    addressed = direct_provider(requested)
    if addressed is not None and addressed in transparents:
        return transparents[addressed]
    for candidate in transparents.values():
        offered = (filters or {}).get(candidate.name, ModelFilter())
        if requested in candidate.registry.model_ids() and offered.matches(requested):
            return candidate
    return next(iter(transparents.values()))


def model_owners(transparents: dict[str, TransparentUpstream]) -> dict[str, list[str]]:
    """Every model id, and the upstreams that list it, in declaration order.

    Exists for the diagnostic rather than the routing path: a model served by
    two upstreams is the case an operator most needs to see, because it is the
    one where the answer depends on declaration order and nothing on the wire
    says so.
    """
    owners: dict[str, list[str]] = {}
    for candidate in transparents.values():
        for model in candidate.registry.model_ids():
            owners.setdefault(model, []).append(candidate.name)
    return owners


def merged_catalogue(
    transparents: dict[str, TransparentUpstream],
    disabled: frozenset[str] = frozenset(),
    filters: dict[str, ModelFilter] | None = None,
    agent_backends: tuple[str, ...] = (),
) -> dict[str, Any]:
    """`GET /v1/models` across every upstream — pools once, models deduped.

    Deduped by id in declaration order, which matches how `resolve` breaks the
    same tie: the list a client reads and the upstream a request reaches must
    not disagree about which of two identically-named models is *the* one.

    The entry is not annotated with which upstream owns it. This body is read by
    Clarvis, whose parser is described in §5.0.1 as strict about the shape, and
    an extra key is a change to a contract for the sake of a diagnostic that
    `model_owners` already serves better.

    `agent_backends` come straight after the pools, in the pools' own shape and
    with no extra key either (§5.0.1 item 5): `ravis/clarvis-codex`, for a caller that
    asked, and nothing for anyone else (`api/openai/agent_backends.py`).
    """
    pools: list[dict[str, Any]] = [
        {"id": pool.pool_id, "object": "model", "owned_by": "ravis"} for pool in DEFAULT_POOLS
    ]
    backends: list[dict[str, Any]] = [
        {"id": backend, "object": "model", "owned_by": "ravis"} for backend in agent_backends
    ]
    models: dict[str, dict[str, Any]] = {}
    for candidate in transparents.values():
        # A disabled upstream is not advertised. Listing a model a request would
        # then be refused for is worse than not listing it: the client picks it
        # from this very response (§5.0.1).
        if candidate.name in disabled:
            continue
        offered = (filters or {}).get(candidate.name, ModelFilter())
        for entry in candidate.registry.snapshot.models:
            identifier = entry.get("id", "")
            if identifier and offered.matches(identifier) and identifier not in models:
                models[identifier] = {
                    "id": identifier,
                    "object": "model",
                    "owned_by": entry.get("owned_by", "organization_owner"),
                }
    return {"object": "list", "data": pools + backends + list(models.values())}


def merged_residency(transparents: dict[str, TransparentUpstream]) -> ResidencySnapshot:
    """One residency view across every upstream.

    The trap here is `ResidencySnapshot.state_of`: once *any* upstream reports
    residency the snapshot is `known`, and a model with no entry then reads as
    COLD rather than UNKNOWN. Merging naively would therefore invent a fact —
    every model behind a generic OpenAI-compatible endpoint would look cold, and
    §14's residency preference would rank it below a genuinely hot one on the
    strength of a default.

    So models belonging to an upstream that does not report residency are
    recorded UNKNOWN explicitly rather than left absent. Absence and ignorance
    are the same answer only while nothing is known; after that they diverge.
    """
    states: dict[str, Residency] = {}
    known = False
    details: list[str] = []
    for candidate in transparents.values():
        snapshot = candidate.registry.residency
        if snapshot.known:
            known = True
        for model in candidate.registry.model_ids():
            states.setdefault(
                model, snapshot.state_of(model) if snapshot.known else Residency.UNKNOWN
            )
        if not snapshot.known and snapshot.detail:
            details.append(f"{candidate.name}: {snapshot.detail}")
    return ResidencySnapshot(states=states, known=known, detail="; ".join(details))


def remote_models(transparents: dict[str, TransparentUpstream]) -> frozenset[str]:
    """Every model id served by an upstream that is not on this machine.

    The set `ravis/local` and `ravis/private` are enforced against. Computed from
    the upstreams themselves rather than declared per model, because the fact
    being asserted is about *where the request goes*, and that is a property of
    the upstream and nothing else — a model id tells you nothing about it.

    Unfiltered on purpose. A model excluded by a provider's filter is not a
    candidate anyway, so including it here costs nothing, while remembering to
    apply the filter in two places is a way for them to disagree — and the
    direction this one would fail in is a remote model missing from the set and
    therefore admitted to `ravis/local`.
    """
    return frozenset(
        model
        for built in transparents.values()
        if not is_local_address(built.spec.base_url)
        for model in built.registry.model_ids()
    )


async def translated_candidates(
    translating: dict[str, Any], evidence: Any = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """Every translated provider's models, and which provider serves each.

    **These were absent from routing entirely.** `merged_candidates` walks
    transparent upstreams, so an Anthropic model was never a pool candidate —
    reachable only by direct address, invisible to `ravis/clarvis-chat` and to
    every other pool. `_translating_for`'s docstring said so plainly: *"a pooled
    request cannot reach a translating adapter yet."*

    Both halves come out of one catalogue read, so the router cannot select a
    model it is then unable to attribute to a provider — which would leave it
    forwarding an Anthropic id down the transparent path to something that has
    never heard of it.

    A provider that cannot authenticate is skipped rather than listed. Its
    models would be eligible, chosen, and refused at the first request, and a
    pool that selects something unreachable is worse than one that never
    offered it.
    """
    merged: dict[str, Any] = {}
    owners: dict[str, str] = {}
    for name, adapter in translating.items():
        if not getattr(adapter, "has_credential", True):
            continue
        try:
            models = await adapter.models()
        except Exception:  # noqa: BLE001 — discovery failure is absence, not error
            continue
        found = await candidates_with_evidence(adapter, models, evidence)
        # First declared wins a collision, the rule every other merge here uses.
        for model, known in found.items():
            if model not in merged:
                merged[model] = known
                owners[model] = name
    return merged, owners


async def merged_candidates(
    transparents: dict[str, TransparentUpstream],
    evidence: Any,
    disabled: frozenset[str] = frozenset(),
    filters: dict[str, ModelFilter] | None = None,
) -> dict[str, Any]:
    """Every upstream's models and capabilities, in one table.

    Each upstream is asked through *its own* adapter, which is the whole point:
    a model on LM Studio gets LM Studio's catalogue read for it and a model on a
    generic endpoint gets honest ignorance, rather than one adapter answering
    for models it has never heard of.

    First declared wins a collision, the same rule `resolve` and
    `merged_catalogue` use. Three places agreeing matters more than any one of
    them being clever: a client reading the model list, a router choosing a
    candidate and a forwarder picking a URL must not disagree about which of two
    identically-named models is the one.
    """
    merged: dict[str, Any] = {}
    for candidate in transparents.values():
        if candidate.name in disabled:
            continue
        # Filtered *before* capabilities are assembled, not after. Against
        # OpenRouter that is the difference between building 417 capability
        # records per routing pass and building the handful an operator asked
        # for.
        offered = (filters or {}).get(candidate.name, ModelFilter())
        known = await candidates_with_evidence(
            candidate.adapter, offered.apply(candidate.registry.model_ids()), evidence
        )
        for model, capabilities in known.items():
            merged.setdefault(model, capabilities)
    return merged
