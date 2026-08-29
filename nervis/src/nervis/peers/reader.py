"""One negotiated read of one peer surface, shared by every peer.

`ravis.py` and `sirvis.py` declare *which* surfaces exist and what each needs;
everything about how a read is gated, attempted and reported lives here. Two
copies of this would drift, and the whole point of the envelope is that a
screen sees one shape however the read turned out.

**Negotiated before called.** §5.2's gate says the UI must *"never call a
guessed endpoint"*, and a check that lives in the browser is one that anything
not the browser can skip.

**One shape for every outcome.** The peer being down, the peer refusing, a
capability being absent and a successful read all return the same envelope. The
alternative is each screen inventing its own way to say "no", which is how a
dashboard ends up with four different empty states that mean the same thing and
one that silently renders zero.

**Nothing is reshaped on the way through.** A body arrives as the peer sent it.
That is §9's requirement in NERVIS's own layer: provenance, sample counts,
units and staleness survive because nothing here is in a position to drop them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx
from ecosystem_protocol import new_traceparent

from nervis.negotiation import Availability, Operation, may_attempt, negotiate
from nervis.registry import RegistryEntry

# How long a management read may take. Short: these back a dashboard, and a
# screen that hangs for thirty seconds has already failed the reader even if the
# answer eventually arrives.
READ_TIMEOUT_SECONDS = 4.0


@dataclass(frozen=True)
class Surface:
    """One thing NERVIS reads from RAVIS, and what has to be true first.

    `path` is the peer's, not NERVIS's. Keeping the two vocabularies separate means
    a rename on either side is a change in one table rather than a hunt through
    handlers.
    """

    key: str
    path: str
    capability: str
    label: str
    # GET for everything except §14.3's recommendation, which is a POST because
    # its inputs are a body and its result is generated rather than stored. It
    # still reads nothing and changes nothing, which is why it lives with the
    # reads rather than behind a mutation gate.
    method: str = "GET"


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


def peer_credential(request: Any, service: str) -> str:
    """The credential NERVIS presents to one peer, if it holds one.

    Keyed by service rather than passed at every call site, so a new reader
    cannot forget it and — more importantly — cannot present RAVIS's credential
    to SIRVIS by copying a line. NERVIS holds one credential per peer for one
    purpose, and this is the only place the mapping exists.
    """
    settings = request.app.state.settings
    if service == "ravis":
        return str(getattr(settings, "ravis_client_credential", "") or "")
    return ""


async def read(
    client: httpx.AsyncClient,
    entry: RegistryEntry | None,
    surface: Surface,
    *,
    service: str = "",
    params: Mapping[str, Any] | None = None,
    request_id: str = "",
    trace_id: str = "",
    credential: str = "",
) -> PeerRead:
    """One negotiated read of one peer surface.

    `request_id` is forwarded rather than regenerated, so a NERVIS screen, the
    NERVIS log line and the peer's log line for the same click all carry one id
    (§4.3). It is correlation data and never authorization — Peers are required
    to treat it as such, and NERVIS must not start relying on it meaning more.
    """
    verdict = negotiate(
        Operation(surface.key, service, surface.capability, surface.label), entry
    )
    if not may_attempt(verdict, entry):
        return PeerRead(
            surface=surface.key,
            available=False,
            availability=verdict.availability.value,
            reason=verdict.reason or f"{surface.capability} is not usable",
        )

    assert entry is not None  # `may_attempt` is false without one
    headers = _context_headers(request_id, trace_id, credential)
    try:
        response = await client.request(
            surface.method,
            entry.declaration.base_url + surface.path,
            params=dict(params or {}) if surface.method == "GET" else None,
            json=dict(params or {}) if surface.method != "GET" else None,
            headers=headers,
            timeout=READ_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as failure:
        # The registry said the peer was reachable and this call disagreed. That is
        # ordinary — the registry's reading is up to one probe interval old —
        # and it is reported rather than smoothed over, because "it was up
        # twenty seconds ago" is not a claim a screen should make on its own.
        return PeerRead(
            surface=surface.key,
            available=False,
            availability=Availability.SERVICE_DOWN.value,
            reason=f"the peer did not answer: {type(failure).__name__}",
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
            reason="the peer answered with something that is not JSON",
        )
    return PeerRead(
        surface=surface.key,
        available=True,
        availability=verdict.availability.value,
        reason=verdict.reason,
        data=body,
    )


def _refusal(response: httpx.Response) -> str:
    """The peer's own words for why it said no, when it gave any.

    Both services publish the runbook §4.3 envelope on `/api/v1`, so a refusal
    carries a code and a message. Passing those through beats replacing them
    with "HTTP 422" — the whole reason that envelope exists is that a consumer
    can show the reason to a person.
    """
    try:
        body = response.json()
    except ValueError:
        return f"the peer answered HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, Mapping) else None
    if isinstance(error, Mapping) and error.get("message"):
        return f"{error.get('code', 'ERROR')}: {error['message']}"
    return f"the peer answered HTTP {response.status_code}"



def _context_headers(request_id: str, trace_id: str, credential: str = "") -> dict[str, str]:
    """§4.3's context, forwarded to a peer.

    `traceparent` is a **new span within the same trace**, never the caller's
    header passed on: forwarding makes the receiver's parent the sender's
    parent, so every service becomes a sibling and §11.2's waterfall has no
    shape.
    """
    headers: dict[str, str] = {}
    # **What makes NERVIS a named caller rather than an anonymous one.** RAVIS
    # allows an anonymous caller sixty reads a minute and a named one six
    # hundred (§14.4's identity policy), and NERVIS is the busiest reader it
    # has: a dashboard polling several screens through this reader tripped that
    # limit routinely, and a rate-limited read is indistinguishable from an
    # empty service at the screen. The credential is NERVIS's own identity, not
    # the user's, and it travels only to the peer it belongs to.
    if credential:
        headers["authorization"] = f"Bearer {credential}"
    if request_id:
        headers["x-request-id"] = request_id
    if trace_id:
        headers["traceparent"] = new_traceparent(trace_id)
    return headers
