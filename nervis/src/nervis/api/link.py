"""`/api/v1/link/*` — finding the owner's other computers, and letting them find this one.

The mechanism is `nervis.discovery`; this is the three routes a screen needs. Linking itself
stays where it was: `tools/run.py link add`, in a terminal, because it asks for the other
computer's password once and a web page is the wrong place to type that.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request

from nervis.discovery import Announcer, find_computers, own_name, tool
from nervis.errors import InvalidConfigurationError
from nervis.storage.database import Database

router = APIRouter(prefix="/api/v1/link", tags=["link"])

#: The setting behind "Let other computers find this one". Not on the exportable list:
#: whether a machine announces itself is a decision about that machine and its network.
FINDABLE_KEY = "link.findable"


def findable(database: Database) -> bool:
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (FINDABLE_KEY,)
    ).fetchone()
    try:
        return bool(row and json.loads(row["value"]) is True)
    except (TypeError, ValueError):
        return False


def _state(request: Request, detail: str = "") -> dict[str, Any]:
    announcer: Announcer = request.app.state.announcer
    return {
        "findable": findable(request.app.state.database),
        "announcing": announcer.running,
        "name": own_name(),
        "tool": tool(),
        "detail": detail,
    }


@router.get("/discover")
def discover() -> dict[str, Any]:
    """Look for other computers announcing NERVIS on this network. Changes nothing.

    A plain `def`, so FastAPI runs it on a worker thread: listening takes a few seconds, and
    doing that on the event loop would stall every other request NERVIS is answering.
    """
    return find_computers()


@router.get("/findable")
def read_findable(request: Request) -> dict[str, Any]:
    """Whether this computer is announcing itself, and under what name."""
    return _state(request)


@router.put("/findable")
async def write_findable(request: Request) -> dict[str, Any]:
    """Turn the announcement on or off, now — not at the next start.

    Stored first and then applied, so what the card shows after a reload is what was asked
    for even if the announcement itself could not start (no Avahi on this Linux, say); the
    sentence explaining why comes back with the answer.
    """
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("value"), bool):
        raise InvalidConfigurationError("body must be {\"value\": true} or {\"value\": false}")
    with request.app.state.database.connection as connection:
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (FINDABLE_KEY, json.dumps(body["value"])),
        )
    detail = request.app.state.announcer.sync(body["value"])
    return _state(request, detail)
