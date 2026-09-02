"""`/api/v1/inspector` — M11's API Inspector (§11.4).

One request through RAVIS, stage by stage. The decision comes from RAVIS's own
`/api/v1/route-decisions`; the content stages come from NERVIS's records where
NERVIS made the request, and are labelled *not published* everywhere else,
because RAVIS exposes no surface carrying them and §1 forbids inventing one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis import chat, inspector
from nervis.errors import NotFoundError
from nervis.peers import ravis as ravis_peer
from nervis.peers.reader import read as read_surface

router = APIRouter(prefix="/api/v1/inspector", tags=["inspector"])


@router.get("")
async def list_inspectable(request: Request) -> dict[str, Any]:
    """Recent requests, newest first, as rows to pick from.

    Deliberately thin: the row says what was asked for, what ran it and which
    path it took, and everything else waits until somebody opens one. RAVIS
    holds decisions in memory, so a short list after a restart is correct rather
    than broken and the screen says so.
    """
    entry = request.app.state.registry.get("ravis")
    result = await read_surface(
        request.app.state.probe_client, entry, ravis_peer.BY_KEY["routes"],
        service=ravis_peer.SERVICE, params={"limit": 25},
    )
    if not result.available or not isinstance(result.data, dict):
        return {"items": [], "live": False, "reason": result.reason or "RAVIS is not answering",
                "show_content": inspector.show_content(request.app.state.database)}
    rows = [
        {
            "decision_id": one.get("decision_id"),
            "request_id": one.get("request_id"),
            "application_id": one.get("application_id"),
            "decided_at": one.get("decided_at"),
            "requested": one.get("requested"),
            "selected": one.get("selected"),
            "pool": one.get("pool"),
            "execution_path": one.get("execution_path"),
        }
        for one in (result.data.get("items") or [])
        if isinstance(one, dict)
    ]
    return {"items": rows, "live": True, "reason": "",
            "show_content": inspector.show_content(request.app.state.database)}


@router.post("/content")
async def set_content(request: Request) -> dict[str, Any]:
    """Switch message content on or off.

    §11.4: *"content follows logging and privacy settings"*. This is that
    setting, and it is off until somebody turns it on — the inspector's subject
    is the route, and content is the part a person should have to ask for.
    """
    body = await request.json()
    on = bool(body.get("enabled"))
    return {"show_content": inspector.set_show_content(request.app.state.database, on)}


@router.get("/{decision_id}")
async def inspect_one(decision_id: str, request: Request) -> dict[str, Any]:
    """One decision, with §11.4's stages for the path it actually took."""
    database = request.app.state.database
    entry = request.app.state.registry.get("ravis")
    result = await read_surface(
        request.app.state.probe_client, entry, ravis_peer.BY_KEY["routes"],
        service=ravis_peer.SERVICE, params={"limit": 50},
    )
    found = None
    if result.available and isinstance(result.data, dict):
        for one in result.data.get("items") or []:
            if isinstance(one, dict) and one.get("decision_id") == decision_id:
                found = one
                break
    if found is None:
        # A RAVIS restart legitimately loses decisions — they are held in memory
        # — so this is "no longer held" rather than "never existed", and the
        # message says which so nobody goes looking for a bug.
        raise NotFoundError(
            f"RAVIS is not holding decision {decision_id}. Decisions live in memory, "
            "so a RAVIS restart drops them."
        )
    turn = chat.turn_for_request(database, str(found.get("request_id") or ""))
    return inspector.inspect(
        found, turn, allow_content=inspector.show_content(database)
    ).as_dict()
