"""The resolved model → provider table, computed from configuration alone.

M0's acceptance requires `ravis doctor` to print this "without contacting any
upstream", and the constraint is the feature. During an incident the question is
usually "why did it pick that one", and a table that answers it from
configuration works precisely when the upstreams do not.

No provider adapters exist yet — those arrive at M8, runbook Stage 5 — so this
resolves what has been *configured*, not what is reachable. Keeping the shape
now means M8 fills the table in rather than inventing the diagnostic afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass

from ravis.config import Settings


@dataclass(frozen=True)
class ProviderResolution:
    """One row of the truth table.

    `decided_by` names the setting responsible. It exists because a mapping
    without a reason turns "why is this wrong?" into an excavation through
    configuration files, and that excavation always happens at the worst moment.
    """

    model: str
    provider: str
    decided_by: str


def resolve_provider_map(settings: Settings) -> list[ProviderResolution]:
    """Every model this configuration can route, and the setting that decided it.

    Returns an empty list rather than None when nothing is configured: absence of
    providers is "none configured", not a failure, and an empty collection is
    what a caller can iterate without a guard (runbook §14.4).

    At M0 this is genuinely empty — there are no provider settings to read yet.
    The function exists now so the CLI, the tests and the M8 adapters all agree
    on the shape before anything depends on it.
    """
    del settings  # No provider configuration exists before M8; the parameter is the contract.
    return []
