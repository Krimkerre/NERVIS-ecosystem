"""The MEP surface every ecosystem service exposes (ECOSYSTEM_RUNBOOK.md §4.1).

Stage 1 of the build order requires this to work before the gateway does: RAVIS
answers identity, health, capabilities and version before it proxies its first
completion, so peers can negotiate with it rather than assume things about it.
"""

from ravis.ecosystem.capabilities import PROTOCOL_VERSION, capability_snapshot
from ravis.ecosystem.routes import router

__all__ = ["PROTOCOL_VERSION", "capability_snapshot", "router"]
