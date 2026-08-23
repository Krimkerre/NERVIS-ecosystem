"""Residency, read from LM Studio's own endpoint.

**A deliberate slice of M8 pulled forward, and worth being explicit about.**
M8 owns the LM Studio adapter. What is here is only the residency probe, because
M14 is inert without one: the generic OpenAI-compatible endpoint publishes model
IDs and no runtime state, so a router that could only see `/v1/models` would have
nothing to prefer and would go on picking alphabetically.

The generic adapter deliberately does *not* read this endpoint — reading a
vendor's proprietary surface is that vendor's adapter's job, and a generic
adapter claiming vendor knowledge is the invention M3a refuses to make. This
module is that vendor code, kept separate and named for what it is.
"""

from __future__ import annotations

import httpx

from ravis.runtime.residency import Residency, ResidencySnapshot

# LM Studio's native endpoint, which reports per-model state. Its
# OpenAI-compatible `/v1/models` does not, which is the whole reason this exists.
RESIDENCY_PATH = "/api/v0/models"

# LM Studio's own vocabulary for what a model is doing, mapped to ours. Anything
# it reports that is not listed here becomes COLD rather than UNKNOWN: the model
# is installed and answering questions about itself, so the one thing we do know
# is that it is not unavailable.
_STATE_MAP = {
    "loaded": Residency.HOT,
    "loading": Residency.COLD,
    "not-loaded": Residency.COLD,
}


async def probe_residency(base_url: str, client: httpx.AsyncClient) -> ResidencySnapshot:
    """Ask LM Studio which models are loaded.

    Every failure yields an *unknown* snapshot rather than an empty one. The
    distinction matters: empty would mean "nothing is loaded", which would make
    every model look equally cold and silently undo the preference this exists to
    provide. Unknown means the router carries on as it did before.
    """
    if not base_url:
        return ResidencySnapshot(detail="no upstream configured")
    try:
        response = await client.get(f"{base_url.rstrip('/')}{RESIDENCY_PATH}")
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as failure:
        # Not an error worth surfacing: an upstream that is not LM Studio will
        # 404 here, and that is the ordinary case rather than a fault.
        return ResidencySnapshot(detail=f"residency unavailable: {failure}")

    entries = payload.get("data", []) if isinstance(payload, dict) else []
    states = {
        entry["id"]: _STATE_MAP.get(str(entry.get("state", "")), Residency.COLD)
        for entry in entries
        if isinstance(entry, dict) and entry.get("id")
    }
    if not states:
        return ResidencySnapshot(detail="no models reported")
    return ResidencySnapshot(states=states, known=True)
