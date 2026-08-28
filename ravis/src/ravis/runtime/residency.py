"""Which models are loaded, and what it costs to use one that is not.

RAVIS.md §12.2. The states are the whole point:

    HOT          loaded now — answers immediately
    WARM         recently used, worth retaining
    COLD         installed but unloaded — using it means paying a load
    UNAVAILABLE  not installed
    UNKNOWN      the runtime does not say

The tradeoff §12.2 describes is why this exists at all. A model already loaded
answers in under a second; a better model that is cold may cost fourteen seconds
to load first. For one short question the loaded model wins despite being weaker.
For a coding session about to make a hundred requests, paying the load is
obviously right.

RAVIS resolves that tradeoff now, and this paragraph outlived the milestones it
was waiting for. Both inputs arrived: sessions at M11 and SIRVIS's evidence at
M13, and M14 put them together -- expected session length is measured from an
application's own completed sessions, not predicted, and a load that would not
amortise over that length is declined. `_load_would_not_amortise` in the routing
engine is the rule; `LOAD_AMORTISES_AFTER_REQUESTS` is the threshold.

What this module still does is the part that came first: stop *ignoring*
residency -- which is what caused a loaded model to be evicted and replaced
during testing, purely because another sorted earlier alphabetically.

**RAVIS observes; it does not load or unload.** §12.2 restricts it to operations
the runtime adapter or a SIRVIS management capability actually owns, so RAVIS and
NERVIS never fight over a runtime's lifecycle. Preferring what is already loaded
requires no such ownership, which is exactly why it is the part that can land
early.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Residency(str, Enum):
    """Where a model is, from the router's point of view."""

    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


# How much a residency state is preferred, lowest first. UNKNOWN ranks with COLD
# rather than with HOT: not knowing whether a model is loaded is not evidence
# that it is, and guessing optimistically is what produces a surprise load.
_RESIDENCY_RANK = {
    Residency.HOT: 0,
    Residency.WARM: 1,
    Residency.COLD: 2,
    Residency.UNKNOWN: 2,
    Residency.UNAVAILABLE: 3,
}


def residency_rank(state: Residency) -> int:
    """Sort key for residency — lower is cheaper to reach."""
    return _RESIDENCY_RANK[state]


@dataclass
class ResidencySnapshot:
    """What was loaded when this was taken.

    A snapshot rather than a live query: §9.8 budgets routing at P50 under 5 ms
    and requires routing to read cached state rather than make live calls, so
    this is refreshed alongside the model catalogue and read from memory.
    """

    states: dict[str, Residency] = field(default_factory=dict)
    known: bool = False
    detail: str = ""

    def state_of(self, model: str) -> Residency:
        """The residency of one model.

        `UNKNOWN` when the runtime does not report residency at all — the honest
        answer for a generic OpenAI-compatible endpoint, and one that keeps a
        router from behaving as if every model were cold, or every model hot.
        """
        if not self.known:
            return Residency.UNKNOWN
        return self.states.get(model, Residency.COLD)

    @property
    def loaded(self) -> list[str]:
        """Every model currently loaded, for diagnostics and explanations."""
        return sorted(
            model for model, state in self.states.items()
            if state in (Residency.HOT, Residency.WARM)
        )
