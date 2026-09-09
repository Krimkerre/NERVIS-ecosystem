"""The resolved model → provider table, computed from configuration alone.

M0's acceptance requires `ravis doctor` to print this "without contacting any
upstream", and the constraint is the feature. During an incident the question is
usually "why did it pick that one", and a table that answers it from
configuration works precisely when the upstreams do not.

**Patterns, not a model list.** Before M8 this returned nothing and said so.
Now upstreams exist — but asking one what it has installed is a network call,
which is the one thing this diagnostic may not do. So the rows describe the
*rules* rather than their outcome: which addresses reach which provider, which
patterns a filter admits, and which it removes. That is also the more useful
answer, because a model missing from the catalogue is usually a rule that
excluded it rather than a runtime that lost it.

**Printed in evaluation order.** The order of the rows is the order the router
applies them, so the table reads as the decision itself: direct addresses first,
then each upstream in declaration order — the same tie-break `transparent.py`
uses — with a provider's exclusions above its inclusions, because exclude wins.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ravis.config import Settings
from ravis.credentials import CredentialStore
from ravis.model_filter import ModelFilters
from ravis.provider_state import ProviderState
from ravis.upstreams import UpstreamConfigurationError, UpstreamSpec, upstream_specs

# What the provider column holds when a rule routes a model nowhere. Not an
# empty string: a blank cell reads as missing data, and this is a decision.
NO_ROUTE = "— no route —"

# Every provider §6 reaches by translation rather than by forwarding, paired
# with the settings field that used to be its only credential source. Adding a
# translated provider means adding it here too, which is the point of the list
# existing: the previous version could not be extended without being rewritten.
TRANSLATING_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("anthropic", "anthropic_api_key"),
    ("google", "google_api_key"),
)


def shared_provider_names(upstream_names: Iterable[str]) -> list[str]:
    """Names claimed by both a transparent upstream and a translated provider.

    A name identifies a provider only *within* one of the two tables, so
    declaring an upstream called `google` beside the translated Gemini provider
    leaves one name meaning two things. RAVIS resolves it — a direct address
    reaches the translated provider when it has a credential, and the
    transparent upstream still contributes its catalogue to `/v1/models` — but
    it resolved it silently, and the two defects that came of that were both
    found by hand.

    Returned rather than raised. Two providers under one name is a working
    configuration, and refusing to start over something RAVIS knows how to
    resolve would be worse than saying so.
    """
    translated = {name for name, _ in TRANSLATING_PROVIDERS}
    return sorted(translated.intersection(upstream_names))


@dataclass(frozen=True)
class ProviderResolution:
    """One row of the truth table.

    `decided_by` names the setting responsible. It exists because a mapping
    without a reason turns "why is this wrong?" into an excavation through
    configuration files, and that excavation always happens at the worst moment.

    `model` holds a glob pattern or a direct address rather than a concrete
    model id — see the module docstring. `*` means "anything not claimed by a
    row above it".
    """

    model: str
    provider: str
    decided_by: str


def resolve_provider_map(
    settings: Settings,
    *,
    filters: ModelFilters | None = None,
    state: ProviderState | None = None,
    credentials: CredentialStore | None = None,
) -> list[ProviderResolution]:
    """Every routing rule this configuration declares, in the order applied.

    Returns an empty list rather than None when nothing is configured: absence
    of providers is "none configured", not a failure, and an empty collection is
    what a caller can iterate without a guard (runbook §14.4).

    `filters` and `state` are injectable because both otherwise read the user's
    real configuration directory, and a diagnostic that behaves differently
    under test than in production is not a diagnostic anyone should trust.

    A malformed upstream declaration is reported as a row rather than raised.
    `doctor` is what an operator runs *because* something is wrong, so the one
    command that can explain a broken configuration must not be the one that
    dies on it.
    """
    filters = filters or ModelFilters.default()
    state = state or ProviderState()
    rows = _translating_rows(settings, state, credentials)
    try:
        specs = upstream_specs(settings)
    except UpstreamConfigurationError as failure:
        return [*rows, ProviderResolution("*", NO_ROUTE, f"malformed RAVIS_UPSTREAMS: {failure}")]
    for spec in specs:
        rows.extend(_upstream_rows(spec, filters, state))
    return rows


def _translating_rows(
    settings: Settings,
    state: ProviderState,
    credentials: CredentialStore | None,
) -> list[ProviderResolution]:
    """Path B providers, addressed as `ravis/<provider>/<model>` (§6).

    **A table rather than one hand-written provider.** This named Anthropic
    literally, so when M7 put Gemini on the same path the row for it was simply
    absent and `doctor` showed nothing for an address that routes perfectly
    well — the diagnostic disagreeing with the router about what exists.

    **Where the credential comes from is part of the answer.** The gate read the
    settings field alone, so a key typed into the Credentials screen — which
    M10 stores, in the keyring or the file behind it — produced no row either.
    The store is
    consulted here in the same order `credential_for` uses on the request path,
    because a diagnostic that disagrees with the request path is worse than no
    diagnostic at all. Reading a local file is not contacting an upstream, so
    the module's own rule still holds.
    """
    rows: list[ProviderResolution] = []
    for name, field in TRANSLATING_PROVIDERS:
        decided_by = _credential_origin(name, getattr(settings, field, ""), credentials)
        if decided_by is None:
            continue
        address = f"ravis/{name}/*"
        if not state.is_enabled(name):
            rows.append(ProviderResolution(address, NO_ROUTE, "disabled in providers.json"))
        else:
            rows.append(ProviderResolution(address, f"{name} (translated)", decided_by))
    return rows


def _credential_origin(
    name: str, declared: str, credentials: CredentialStore | None
) -> str | None:
    """Which setting supplies this provider's key, or None if nothing does.

    Mirrors `credential_for`: the store wins, the settings field is the
    fallback. Naming the *source* rather than a fixed environment variable is
    the point of the column — "credential store (file)" and
    "RAVIS_GOOGLE_API_KEY" send an operator to two different places.
    """
    if credentials is not None:
        status = credentials.status(name)
        if status.configured:
            return f"credential store ({status.source.value})"
    if declared:
        return f"RAVIS_{name.upper()}_API_KEY"
    return None


def _upstream_rows(
    spec: UpstreamSpec, filters: ModelFilters, state: ProviderState
) -> list[ProviderResolution]:
    """One transparent upstream's rules: its address, then its filter."""
    origin = f"upstream {spec.name!r} → {spec.base_url}"
    if not state.is_enabled(spec.name):
        return [ProviderResolution(f"ravis/{spec.name}/*", NO_ROUTE, "disabled in providers.json")]

    rows = [ProviderResolution(f"ravis/{spec.name}/*", spec.name, origin)]
    model_filter = filters.for_provider(spec.name)
    # Exclusions are listed first because that is the order `matches()` applies
    # them in: a pattern in both lists is excluded, and a table that showed the
    # include above it would describe the opposite of what happens.
    rows.extend(
        ProviderResolution(pattern, NO_ROUTE, f"models.json exclude for {spec.name!r}")
        for pattern in model_filter.exclude
    )
    if model_filter.include:
        rows.extend(
            ProviderResolution(pattern, spec.name, f"models.json include for {spec.name!r}")
            for pattern in model_filter.include
        )
        return rows
    # No include list means the whole catalogue passes, which is a real routing
    # rule and the one most likely to surprise someone reading `/v1/models`.
    rows.append(ProviderResolution("*", spec.name, origin))
    return rows
