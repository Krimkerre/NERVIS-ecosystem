"""`/api/v1/recall` — M20's conversation memory (§7.2).

Two things a caller needs and one it must not have. It can ask **what would be
recalled** for a question, and it can switch recall on and off. It cannot ask
NERVIS to recall something *into* a turn: recall happens on the chat path, from
the question the person actually asked, so there is no route by which a caller
could choose what an answer is grounded in.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis import recall

router = APIRouter(prefix="/api/v1/recall", tags=["recall"])


@router.get("")
async def state(request: Request) -> dict[str, Any]:
    """Whether recall is on, and what it would search."""
    database = request.app.state.database
    stored = database.connection.execute(
        "SELECT COUNT(*) FROM chat_conversation"
    ).fetchone()
    return {
        "enabled": recall.enabled(database),
        "conversations": int(stored[0]) if stored else 0,
        "max_passages": recall.MAX_PASSAGES,
    }


@router.post("/enable")
async def enable(request: Request) -> dict[str, Any]:
    """Switch recall on or off.

    Off is the default and stays meaningful: with it off no search runs at all,
    so the assembled prompt is what it was before this milestone rather than
    merely similar to it.
    """
    body = await request.json()
    return {"enabled": recall.set_enabled(request.app.state.database, bool(body.get("enabled")))}


@router.post("/preview")
async def preview(request: Request) -> dict[str, Any]:
    """What a question would recall, without asking anything.

    M20's exit says what is recalled must be *shown*, and this is how a screen
    shows it before committing to a turn — the same search the chat path runs,
    so the preview is the thing rather than an illustration of it.
    """
    body = await request.json()
    database = request.app.state.database
    if not recall.enabled(database):
        return {"enabled": False, "items": [],
                "reason": "recall is switched off, so nothing is searched"}
    found = recall.search(
        database, str(body.get("content") or ""),
        exclude=str(body.get("conversation_id") or ""),
    )
    return {"enabled": True, "items": [one.as_dict() for one in found], "reason": ""}
