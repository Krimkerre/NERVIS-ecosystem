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

from dataclasses import dataclass

from ravis.config import Settings
from ravis.model_filter import ModelFilters
from ravis.provider_state import ProviderState
from ravis.upstreams import UpstreamConfigurationError, UpstreamSpec, upstream_specs

# What the provider column holds when a rule routes a model nowhere. Not an
# empty string: a blank cell reads as missing data, and this is a decision.
NO_ROUTE = "— no route —"


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
    rows = _translating_rows(settings, state)
    try:
        specs = upstream_specs(settings)
    except UpstreamConfigurationError as failure:
        return [*rows, ProviderResolution("*", NO_ROUTE, f"malformed RAVIS_UPSTREAMS: {failure}")]
    for spec in specs:
        rows.extend(_upstream_rows(spec, filters, state))
    return rows


def _translating_rows(settings: Settings, state: ProviderState) -> list[ProviderResolution]:
    """Path B providers, reachable only by direct address (§6).

    A translating provider is not a pool candidate — `chat.py` reaches one
    solely through the `ravis/<provider>/<model>` form — so the row is written
    as that address rather than as a bare pattern it would never match.
    """
    if not settings.anthropic_api_key:
        return []
    if not state.is_enabled("anthropic"):
        return [ProviderResolution("ravis/anthropic/*", NO_ROUTE, "disabled in providers.json")]
    return [
        ProviderResolution("ravis/anthropic/*", "anthropic (translated)", "RAVIS_ANTHROPIC_API_KEY")
    ]


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
