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

import json
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


async def write_credential(
    client: httpx.AsyncClient,
    entry: RegistryEntry | None,
    name: str,
    secret: str,
    credential: str,
) -> tuple[int, dict[str, Any]]:
    """Store one provider credential in RAVIS, on the operator's behalf.

    **Why this goes through NERVIS at all (§15.1).** RAVIS no longer takes a
    loopback bind as authorization for a credential write: calling the gateway
    must not confer the power to re-point the keys it calls with. The dashboard
    used to `PUT` straight to RAVIS with no header, which worked only because
    that bypass existed.

    So the write travels here, and NERVIS presents the `admin.`-prefixed
    credential the launcher minted for it — the same shape as the SIRVIS
    benchmark and admin tokens it already holds separately. The secret passes
    through and is never stored, logged or echoed: RAVIS answers with a status
    row carrying `configured`, not a value.

    Returns the upstream status and body rather than raising, because every
    failure here is something the screen must say rather than something NERVIS
    can fix: no credential, RAVIS unreachable, RAVIS refusing.
    """
    if entry is None or not entry.declaration.base_url:
        return 503, {"message": "RAVIS is not registered"}
    if not credential:
        return 403, {
            "message": (
                "NERVIS holds no admin credential for RAVIS, so it cannot change a "
                "provider key. The launcher mints one at start; if RAVIS was started "
                "another way, set NERVIS_RAVIS_ADMIN_CREDENTIAL."
            )
        }
    return await _credential_call(
        client, "PUT", f"{entry.declaration.base_url}/api/v1/providers/credentials/{name}",
        credential, {"secret": secret},
    )


async def forget_credential(
    client: httpx.AsyncClient,
    entry: RegistryEntry | None,
    name: str,
    credential: str,
) -> tuple[int, dict[str, Any]]:
    """Remove one provider credential from RAVIS, on the operator's behalf.

    The same authorization as writing one, and deliberately so: §15.1 is about
    who may change key material, and removing a key changes it.
    """
    if entry is None or not entry.declaration.base_url:
        return 503, {"message": "RAVIS is not registered"}
    if not credential:
        return 403, {"message": "NERVIS holds no admin credential for RAVIS"}
    return await _credential_call(
        client, "DELETE", f"{entry.declaration.base_url}/api/v1/providers/credentials/{name}",
        credential, None,
    )


async def configure(
    client: httpx.AsyncClient,
    entry: RegistryEntry | None,
    method: str,
    path: str,
    credential: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """One configuration write, on the operator's behalf (§16 item 4).

    **The same move `write_credential` already made, for the other four writes.**
    RAVIS used to take a loopback bind as authorization for enabling a provider,
    narrowing a catalogue or re-pointing a pool, so the dashboard `PUT` straight
    to RAVIS with no header and it worked. That bypass meant administration
    arrived free with the ability to call the gateway — Clarvis holds an ordinary
    client credential, and a bug in an agent loop could have disabled a provider
    for everything else on the machine.

    So these travel here instead, and NERVIS presents the `admin.` credential the
    launcher minted for it. **The browser never holds it**, which is the reason
    for the hop: a page cannot be given a secret it must not keep, and a runtime
    token in a tab is not the same thing as an administrative credential.

    `method` and `path` are parameters rather than four near-identical functions
    because the four differ in nothing else, and a copy is how one of them ends
    up without the header — the note on `_credential_call` says the same thing
    one surface along.
    """
    if entry is None or not entry.declaration.base_url:
        return 503, {"message": "RAVIS is not registered"}
    if not credential:
        return 403, {
            "message": (
                "NERVIS holds no admin credential for RAVIS, so it cannot change its "
                "configuration. The launcher mints one at start; if RAVIS was started "
                "another way, set NERVIS_RAVIS_ADMIN_CREDENTIAL."
            )
        }
    return await _credential_call(
        client, method, f"{entry.declaration.base_url}{path}", credential, body
    )


async def _credential_call(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    credential: str,
    body: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    """One authorised call to RAVIS's credential surface.

    Shared by both operations because they fail the same three ways, and a
    second copy is how one of them ends up without the authorization header.
    """
    try:
        answered = await client.request(
            method, url,
            headers={"authorization": f"Bearer {credential}"},
            json=body,
            timeout=10.0,
        )
    except httpx.HTTPError as failure:
        return 502, {"message": f"RAVIS did not answer: {type(failure).__name__}"}
    try:
        return answered.status_code, dict(answered.json())
    except ValueError:
        return answered.status_code, {"message": answered.text[:200]}


# What the dashboard may read of RAVIS through NERVIS: the management reads and the
# ecosystem surface. Never `/v1`, the gateway itself — relaying a completion would spend
# NERVIS's allowance on a conversation that is not NERVIS's.
RELAYED_PREFIXES = ("api/v1/", "ecosystem/")
# Longer than the page waits for most reads, so the page's own deadline decides what
# counts as slow, and short enough that a stalled RAVIS does not pile requests up here.
RELAY_TIMEOUT_SECONDS = 5.0


def relayable(path: str) -> bool:
    """Whether a page may read this RAVIS path through NERVIS: a relayed prefix, and no
    segment that is empty or climbs out of it."""
    return (
        path.startswith(RELAYED_PREFIXES)
        and "\\" not in path
        and all(segment not in ("", ".", "..") for segment in path.split("/"))
    )


async def relay_read(
    client: httpx.AsyncClient,
    entry: RegistryEntry | None,
    path: str,
    query: str,
    credential: str,
) -> tuple[int, bytes, str, str]:
    """One read of RAVIS for the dashboard, answered exactly as RAVIS answered it.

    **Why the page stopped reading RAVIS itself.** Measured 12 September 2026: the
    overview alone made about twenty-seven reads of RAVIS a minute, all anonymous, and
    RAVIS gives every anonymous caller on this machine one allowance of sixty a minute
    between them — every open tab and any script included. Nothing had been refused yet;
    two tabs and one more caller would have been enough. Through NERVIS the reads carry
    NERVIS's own client credential, and the browser still holds no secret.

    Returns `(status, body, media type, failure)`. `failure` is empty whenever RAVIS
    answered, and whatever it answered — a refusal included — passes through untouched,
    because the page tells a refusal from an outage by the status. When RAVIS did not
    answer, `failure` says how, `unreachable` (502) or `slow` (504), and the page turns
    that back into the outcome it shows for a service that is not there.
    """
    if entry is None or not entry.declaration.base_url:
        return 503, b'{"message": "RAVIS is not registered"}', "application/json", "unreachable"
    url = f"{entry.declaration.base_url}/{path}" + (f"?{query}" if query else "")
    headers = {"authorization": f"Bearer {credential}"} if credential else {}
    try:
        answered = await client.get(url, headers=headers, timeout=RELAY_TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        return 504, b'{"message": "RAVIS did not answer in time"}', "application/json", "slow"
    except httpx.HTTPError as failure:
        said = json.dumps({"message": f"RAVIS did not answer: {type(failure).__name__}"})
        return 502, said.encode(), "application/json", "unreachable"
    media_type = answered.headers.get("content-type", "application/json")
    return answered.status_code, answered.content, media_type, ""
