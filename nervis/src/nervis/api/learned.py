"""`/api/v1/learned` — the notes NERVIS was told (M23).

Read them, forget one, forget all. Writing goes through `/api/v1/commands/run`
like every other act, because writing is an act: §12 puts it behind an
enumerated operation and a confirmation, and giving it a second door here would
be a way around the road every other change takes.

**These endpoints are a convenience, not the only door.** The record is a
markdown file next to the shipped notes. Somebody who prefers an editor has one.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from nervis import learned, logs
from nervis.errors import NotFoundError

router = APIRouter(prefix="/api/v1/learned", tags=["learned"])


@router.get("")
async def list_notes() -> dict[str, Any]:
    """Every note, oldest first, with where the file lives.

    The path is returned because "you can edit this yourself" is only true if
    somebody is told where. A screen that lists notes without saying what it is
    listing makes the file feel like an internal detail rather than the record.

    Relative, though. Naming the file serves that purpose; naming the machine's
    home directory serves nobody, and this route has no authentication in front
    of it (§16 item 12).
    """
    found = learned.notes()
    return {
        "items": [note.as_dict() for note in found],
        "count": len(found),
        "path": logs.shown(learned.path()),
    }


@router.delete("/{heading}")
async def forget_note(heading: str) -> dict[str, Any]:
    """Drop every note under one heading.

    By heading rather than by position: a person may have edited the file
    between reading it and pressing this, and acting on a stale index would
    delete a different note than the one on screen.
    """
    if not learned.forget(heading):
        raise NotFoundError(f"nothing is filed under {heading!r}")
    return {"forgotten": heading}


@router.delete("")
async def forget_everything() -> dict[str, Any]:
    """Delete the file. Retrieval then behaves exactly as it did before M23."""
    return {"forgotten": learned.forget_all()}
