"""How chat reaches a peer, and the headers it goes out with.

**Lifted out of `chat.py` because everything reaches for it.** Titles call
RAVIS, the situation reads call both peers, and the completion relay uses the
same headers — so these lived in the middle of a 2,500-line module that four
other concerns had to import *around*. Here they are the one place a request
leaves NERVIS from.

Nothing in this file decides anything. Every read is bounded and returns absence
rather than raising, because the callers are assembling a picture for a model
and a missing surface is a fact about the picture rather than a failure of the
turn.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from ecosystem_protocol import new_traceparent
from fastapi import Request

from nervis.errors import InvalidConfigurationError
from nervis.registry import RegistryEntry

#: How long any one peer read may take while a person waits on a reply.
#: Short on purpose: the answer is furniture around a completion, and a slow
#: surface should be absent from it rather than delay it.
FACTS_TIMEOUT_SECONDS = 4.0

#: Headers forwarded from the caller's request to RAVIS, lower-cased.
FORWARDED = (
    "x-client-application",
    "x-client-version",
    "x-session-id",
)

async def _sirvis_read(request: Request, path: str) -> dict[str, Any]:
    """One GET against SIRVIS, or an empty answer. Never raises."""
    return await _peer_read(request, "sirvis", path)


async def _ravis_read(request: Request, path: str) -> dict[str, Any]:
    """One GET against RAVIS, or an empty answer. Never raises."""
    return await _peer_read(request, "ravis", path)


async def _peer_read(request: Request, service: str, path: str) -> dict[str, Any]:
    """A bounded read of one peer surface, absent rather than guessed on failure.

    One implementation for both peers because every one of these fails the same
    four ways — no registration, a non-200, a body that is not JSON, a timeout —
    and a per-surface copy of that ladder is four places for "absent" to quietly
    become "empty".
    """
    entry: RegistryEntry | None = request.app.state.registry.get(service)
    if entry is None or not entry.is_usable:
        return {}
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        answered = await client.get(
            entry.declaration.base_url + path,
            timeout=FACTS_TIMEOUT_SECONDS,
            headers=_named(request) if service == "ravis" else None,
        )
        if answered.status_code >= 400:
            return {}
        found = answered.json()
    except (httpx.HTTPError, ValueError, AttributeError):
        return {}
    return found if isinstance(found, dict) else {}


async def _refusal(response: httpx.Response) -> str:
    """RAVIS's own words, which §4.3's envelope exists to make readable."""
    try:
        body = json.loads(await response.aread())
    except ValueError:
        return f"RAVIS answered HTTP {response.status_code}"
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return f"RAVIS answered HTTP {response.status_code}"


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError as failure:
        raise InvalidConfigurationError(f"body is not valid JSON: {failure}") from failure
    if not isinstance(body, dict):
        raise InvalidConfigurationError("body must be a JSON object")
    return body


def _named(request: Request) -> dict[str, str]:
    """NERVIS's own identity for a plain read, or nothing.

    The same credential the completion path presents. Reads carry it for the
    rate limit rather than for privilege: anonymous is sixty a minute, and this
    read is taken on every turn that asks about models.
    """
    credential = str(request.app.state.settings.ravis_client_credential or "")
    return {"authorization": f"Bearer {credential}"} if credential else {}


def _forwarded(request_id: str, trace_id: str, credential: str = "") -> dict[str, str]:
    """The context headers one turn carries to RAVIS (§4.3).

    `traceparent` is a **new span in the same trace**, not the incoming header
    forwarded: forwarding would make RAVIS's parent NERVIS's parent, and §11.2's
    waterfall would draw two siblings where there is a call.

    The credential is what makes NERVIS a *named* caller. Without it every
    request arrives as `anonymous`, which RAVIS treats as least-privileged by
    construction — no background marker honoured and no policy of its own. That
    was the state until now, and it is why titles could not be generated.
    """
    headers = {"content-type": "application/json", "x-request-id": request_id}
    if trace_id:
        headers["traceparent"] = new_traceparent(trace_id)
    if credential:
        headers["authorization"] = f"Bearer {credential}"
    return headers


