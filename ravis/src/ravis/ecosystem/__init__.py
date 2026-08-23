"""What RAVIS advertises to the ecosystem.

The MEP surface itself — the five endpoints, the envelope, the version rule —
lives in the shared `ecosystem_protocol` package, because a health endpoint that
means something slightly different per service is worse than none. What stays
here is the only part that is genuinely RAVIS's: what RAVIS can do.
"""

from ravis.ecosystem.capabilities import DECLARED, ravis_surface

__all__ = ["DECLARED", "ravis_surface"]
