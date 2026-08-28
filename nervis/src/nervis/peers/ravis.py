"""What NERVIS reads from RAVIS (§8), and what it refuses to read.

**Never RAVIS's database.** §8 states it in one sentence — *"It never reads
RAVIS's database"* — and M3's exit repeats it. The guarantee is structural
rather than promised: `reader.py` takes a base URL and an HTTP client and has
no filesystem access at all, so there is no path by which this could open
`ravis.db` even by accident. `nervis.config.Settings` deliberately has no field
naming one.

How a read is gated, attempted and reported lives in `reader.py`. This file is
the table of what exists.
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from nervis.peers.reader import Surface, read
from nervis.registry import RegistryEntry

SERVICE = "ravis"

# M3's list: health, providers, models, routes, usage, sessions — plus pools,
# which the Routes screen needs to explain what a decision chose between.
#
# `sessions` was listed here as a surface that would refuse, because RAVIS M11
# had not shipped and `ravis.sessions@1` was advertised `unavailable`. M11
# shipped: the capability is `available`, `/api/v1/sessions` answers, and the
# Sessions screen reads it. The comment outlived the milestone by long enough
# that a reader would have believed the surface was still absent.
#
# The reasoning it recorded is still the right reasoning, and still applies to
# whichever surface is next: a planned and absent surface should be listed with
# the milestone attached, which is what §4.1 makes RAVIS publish.
SURFACES: tuple[Surface, ...] = (
    Surface("health", "/api/v1/health", "ravis.management", "Health"),
    Surface("providers", "/api/v1/providers", "ravis.management", "Providers"),
    Surface("models", "/api/v1/models", "ravis.management", "Models"),
    Surface("pools", "/api/v1/pools", "ravis.virtual_profiles", "Pools"),
    Surface("routes", "/api/v1/route-decisions", "ravis.routing.explanations", "Route decisions"),
    Surface("usage", "/api/v1/usage", "ravis.usage_cost", "Usage"),
    Surface("sessions", "/api/v1/sessions", "ravis.sessions", "Sessions"),
)

BY_KEY: dict[str, Surface] = {surface.key: surface for surface in SURFACES}


async def decision_for(
    client: httpx.AsyncClient, entry: RegistryEntry | None, request_id: str
) -> dict[str, Any] | None:
    """The route decision behind one request, or None.

    §7.1's inspector — *"this makes ordinary chat a RAVIS debugging tool"* —
    needs the decision that produced a reply. RAVIS records `request_id` on
    every decision and echoes the same id in `x-request-id`, so the correlation
    already exists; NERVIS sends its own id and looks that one up.

    A scan of the recent page rather than a filtered query, because
    `/api/v1/route-decisions` publishes no filter. Bounded and cheap: the
    decision for a reply that just streamed is at the top, and a miss returns
    None rather than an error — decisions are held in memory and a RAVIS restart
    legitimately loses them.
    """
    if not request_id:
        return None
    result = await read(
        client, entry, BY_KEY["routes"], service=SERVICE, params={"limit": 50}
    )
    if not result.available or not isinstance(result.data, Mapping):
        return None
    for item in result.data.get("items") or []:
        if isinstance(item, Mapping) and item.get("request_id") == request_id:
            return dict(item)
    return None
