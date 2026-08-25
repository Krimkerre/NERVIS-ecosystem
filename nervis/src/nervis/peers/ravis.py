"""What NERVIS reads from RAVIS (§8), and what it refuses to read.

**Never RAVIS's database.** §8 states it in one sentence — *"It never reads
RAVIS's database"* — and M3's exit repeats it. The guarantee here is
structural rather than promised: this module takes a base URL and an HTTP
client and has no filesystem access at all, so there is no path by which it
could open `ravis.db` even by accident. `nervis.config.Settings` deliberately
has no field naming one.

**Negotiated before called.** §5.2's gate says the UI must *"never call a
guessed endpoint"*, and a check that lives in the browser is a check that can be
skipped by anything that is not the browser. Every surface below names the
capability it needs; the read is refused, with RAVIS's own stated reason, when
that capability is not advertised as usable.

**One shape for every outcome.** RAVIS being down, RAVIS refusing, a capability
being absent and a successful read all return the same envelope. The alternative
is each screen inventing its own way to say "no", which is how a dashboard ends
up with four different empty states that mean the same thing and one that
silently renders zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from nervis.negotiation import Availability, Operation, Verdict, negotiate
from nervis.registry import RegistryEntry

# How long a management read may take. Short: these back a dashboard, and a
# screen that hangs for thirty seconds has already failed the reader even if the
# answer eventually arrives.
READ_TIMEOUT_SECONDS = 4.0


@dataclass(frozen=True)
class Surface:
    """One thing NERVIS reads from RAVIS, and what has to be true first.

    `path` is RAVIS's, not NERVIS's. Keeping the two vocabularies separate means
    a rename on either side is a change in one table rather than a hunt through
    handlers.
    """

    key: str
    path: str
    capability: str
    label: str


# M3's list: health, providers, models, routes, usage, sessions — plus pools,
# which the Routes screen needs to explain what a decision chose between.
#
# `sessions` is here and will refuse: RAVIS M11 has not shipped, so
# `ravis.sessions@1` is advertised `unavailable`. That is the point of listing
# it. A surface that is planned and absent should say so with the milestone
# attached, which is exactly what §4.1 makes RAVIS publish.
SURFACES: tuple[Surface, ...] = (
    Surface("health", "/api/v1/health", "ravis.management", "Health"),
    Surface("providers", "/api/v1/providers", "ravis.management", "Providers"),
    Surface("models", "/api/v1/models", "ravis.management", "Models"),
    Surface("pools", "/api/v1/pools", "ravis.virtual_profiles", "Pools"),
    Surface("routes", "/api/v1/route-decisions", "ravis.routing.explanations", "Route decisions"),
    Surface("usage", "/api/v1/usage", "ravis.usage_cost", "Usage"),
    Surface("sessions", "/api/v1/sessions", "ravis.sessions", "Sessions"),
)

BY_KEY = {surface.key: surface for surface in SURFACES}


@dataclass(frozen=True)
class PeerRead:
    """One read, however it turned out.

    `data` is `None` for every unsuccessful outcome rather than an empty
    collection. An empty list means *RAVIS has none of these*, which is a real
    answer and must not be confused with *nobody managed to ask*.
    """

    surface: str
    available: bool
    reason: str
    availability: str
    data: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "available": self.available,
            "availability": self.availability,
            "reason": self.reason,
            "data": self.data,
        }


async def read(
    client: httpx.AsyncClient,
    entry: RegistryEntry | None,
    surface: Surface,
    *,
    params: Mapping[str, Any] | None = None,
    request_id: str = "",
) -> PeerRead:
    """One negotiated read of one RAVIS surface.

    `request_id` is forwarded rather than regenerated, so a NERVIS screen, the
    NERVIS log line and the RAVIS log line for the same click all carry one id
    (§4.3). It is correlation data and never authorization — RAVIS is required
    to treat it as such, and NERVIS must not start relying on it meaning more.
    """
    verdict = negotiate(
        Operation(surface.key, "ravis", surface.capability, surface.label), entry
    )
    if not _may_attempt(verdict, entry):
        return PeerRead(
            surface=surface.key,
            available=False,
            availability=verdict.availability.value,
            reason=verdict.reason or f"{surface.capability} is not usable",
        )

    assert entry is not None  # `_may_attempt` is false without one
    headers = {"x-request-id": request_id} if request_id else {}
    try:
        response = await client.get(
            entry.declaration.base_url + surface.path,
            params=dict(params or {}),
            headers=headers,
            timeout=READ_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as failure:
        # The registry said RAVIS was reachable and this call disagreed. That is
        # ordinary — the registry's reading is up to one probe interval old —
        # and it is reported rather than smoothed over, because "it was up
        # twenty seconds ago" is not a claim a screen should make on its own.
        return PeerRead(
            surface=surface.key,
            available=False,
            availability=Availability.SERVICE_DOWN.value,
            reason=f"RAVIS did not answer: {type(failure).__name__}",
        )

    if response.status_code >= 400:
        return PeerRead(
            surface=surface.key,
            available=False,
            availability=Availability.UNAVAILABLE.value,
            reason=_refusal(response),
        )
    try:
        body = response.json()
    except ValueError:
        return PeerRead(
            surface=surface.key,
            available=False,
            availability=Availability.UNAVAILABLE.value,
            reason="RAVIS answered with something that is not JSON",
        )
    return PeerRead(
        surface=surface.key,
        available=True,
        availability=verdict.availability.value,
        reason=verdict.reason,
        data=body,
    )


def _may_attempt(verdict: Verdict, entry: RegistryEntry | None) -> bool:
    """Whether to make the call, which is a narrower question than "is it usable".

    **Liveness is not a veto, and treating it as one was a bug.** The registry's
    reading is up to one probe interval old, so gating a read on it meant NERVIS
    refused a perfectly healthy RAVIS for twenty seconds after it came up —
    reporting `ConnectError` for a service that was answering. That is guessing
    in the other direction from the one §5.2 forbids.

    What the gate is actually for is *"never calls a guessed endpoint"*: a
    capability that was never advertised, or that the service says it does not
    offer. Those stay refused without a request. A capability last seen usable
    on a service now thought unreachable is **attempted** — the connection
    refuses in about a millisecond on loopback, and the transport's answer is
    both fresher and more specific than the registry's.

    The registry's job is to describe. `negotiate()` still returns
    `SERVICE_DOWN`, and a *control* should grey out on it; a read should try.
    """
    if verdict.usable:
        return True
    if entry is None or verdict.availability is not Availability.SERVICE_DOWN:
        return False
    # Down, but we know what it offered when it last answered.
    return entry.capabilities.get(verdict.operation.capability) in {"available", "degraded"}


def _refusal(response: httpx.Response) -> str:
    """RAVIS's own words for why it said no, when it gave any.

    RAVIS publishes the runbook §4.3 envelope on `/api/v1`, so a refusal carries
    a code and a message. Passing those through beats replacing them with
    "HTTP 422" — the whole reason that envelope exists is that a consumer can
    show the reason to a person.
    """
    try:
        body = response.json()
    except ValueError:
        return f"RAVIS answered HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, Mapping) else None
    if isinstance(error, Mapping) and error.get("message"):
        return f"{error.get('code', 'ERROR')}: {error['message']}"
    return f"RAVIS answered HTTP {response.status_code}"
