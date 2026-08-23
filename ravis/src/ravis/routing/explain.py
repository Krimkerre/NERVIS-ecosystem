"""What was decided, and the reasoning that produced it (RAVIS.md §9.7).

Every dynamic route must answer *why this model?* — and a no-route decision is
first-class and explainable rather than a bare failure.

The reason this is a stored object rather than a log line: §9.7 requires the
explanation to distinguish facts from estimates from unknowns, and requires the
rejected alternatives with their reasons. A route that cannot say what it
excluded, and why, leaves "it picked the wrong model" undiagnosable — you cannot
tell a bad measurement from a bad weighting from a candidate that was never
considered at all.

Redaction is built in rather than applied later: this object reaches route
explanations, NERVIS and traces, so it holds model identifiers and reasons and
never prompts, credentials or internal URLs (§9.7, runbook §9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExcludedCandidate:
    """A model that was considered and ruled out, with every reason."""

    model: str
    reasons: list[str]


@dataclass
class RouteDecision:
    """The outcome of one routing pass.

    `selected` is `None` for a no-route decision, which is a legitimate and
    explainable outcome rather than an error — §9.2 says that when a constraint
    cannot be satisfied, RAVIS returns a structured no-route rather than routing
    around the constraint to find something that fits.
    """

    requested: str
    pool_id: str | None = None
    selected: str | None = None
    # The ranked alternatives that may be tried if `selected` fails (§10's
    # Primary → Fallback 1 → Fallback 2). Every entry has already passed the
    # same eligibility filter as the primary, which is how §10's requirement —
    # that a fallback still satisfy the original hard constraints and the pool
    # invariants — is guaranteed rather than re-checked at failure time.
    fallbacks: list[str] = field(default_factory=list)
    reason: str = ""
    considered: list[str] = field(default_factory=list)
    excluded: list[ExcludedCandidate] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    # Checks that could not be performed — distinct from checks that failed.
    # §9.7 requires an explanation to separate facts from unknowns, and a
    # requirement nobody could verify is an unknown rather than a pass.
    unverified: list[str] = field(default_factory=list)

    @property
    def routed(self) -> bool:
        return self.selected is not None

    def as_dict(self) -> dict[str, Any]:
        """The shape published in diagnostics and events.

        Carries identifiers and reasons only. Nothing here should ever need
        redacting downstream, because nothing sensitive is put in.
        """
        return {
            "requested": self.requested,
            "pool": self.pool_id,
            "selected": self.selected,
            "fallbacks": self.fallbacks,
            "reason": self.reason,
            "requirements": self.requirements,
            "unverified": self.unverified,
            "considered": self.considered,
            "excluded": [
                {"model": candidate.model, "reasons": candidate.reasons}
                for candidate in self.excluded
            ],
        }

    def render(self) -> str:
        """A human-readable explanation, in the shape §9.7 illustrates."""
        if not self.routed:
            lines = [f"No route for {self.requested}"]
            if self.requirements:
                lines.append(f"  required: {' · '.join(self.requirements)}")
        else:
            lines = [f"Selected: {self.selected}", f"  {self.reason}"]
            if self.fallbacks:
                lines.append(f"  fallback order: {' → '.join(self.fallbacks)}")
        for note in self.unverified:
            lines.append(f"  unverified: {note}")
        for candidate in self.excluded:
            lines.append(f"  Not {candidate.model} — {'; '.join(candidate.reasons)}")
        return "\n".join(lines)
