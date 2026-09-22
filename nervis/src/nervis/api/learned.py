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

from fastapi import APIRouter, Request

from nervis import learned, logs
from nervis.errors import NotFoundError
from nervis.peers.computer import linked_peer, peer_json

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


@router.get("/peer")
def what_they_know(request: Request) -> dict[str, Any]:
    """The other computer's notes, and which of them this one does not have.

    Notes are the third thing that crosses the link, after settings and conversations, and for
    the same reason: they are kept per machine on purpose — `learned.md` is git-ignored because
    the repository is public (owner's decision, 23 September 2026) — so without this a fact
    taught to one computer would be known only there.

    Reads only, and answers in one shape whether or not there is a link.
    """
    peer = linked_peer(request.app.state.database)
    answer: dict[str, Any] = {"address": peer["address"], "detail": "", "changes": [],
                              "same": 0, "reachable": False}
    theirs, trouble = peer_json("/api/v1/learned")
    listed = theirs.get("items") if isinstance(theirs, dict) else None
    if not isinstance(listed, list):
        answer["detail"] = trouble or (
            "the other computer did not answer with notes — it may be running an older NERVIS"
        ) if peer["address"] or trouble else (
            "no other computer is linked — Settings → Another computer")
        return answer
    changes = learned.differences([one for one in listed if isinstance(one, dict)],
                                  learned.notes())
    answer["reachable"] = True
    answer["changes"] = changes
    answer["same"] = max(0, len(listed) - len(changes))
    if not changes:
        answer["detail"] = "nothing to bring over — this computer knows them all already"
    return answer


@router.post("/peer")
async def bring_them_over(request: Request) -> dict[str, Any]:
    """Bring the ticked notes here, read again from the other computer.

    **The body chooses which; the other computer supplies what.** `{"ids": [...]}` names notes
    by what they say (`learned.note_id`), and the text is taken from a fresh read — so what is
    written is what that computer holds now, not what a browser was holding a minute ago.
    """
    try:
        body = await request.json()
    except ValueError:
        body = {}
    wanted = {str(one) for one in body.get("ids", [])} if isinstance(body, dict) else set()
    if not wanted:
        return {"ok": False, "written": [], "detail": "nothing was ticked"}
    theirs, trouble = peer_json("/api/v1/learned")
    listed = theirs.get("items") if isinstance(theirs, dict) else None
    if not isinstance(listed, list):
        return {"ok": False, "written": [],
                "detail": trouble or "the other computer did not answer"}
    chosen = [one for one in listed if isinstance(one, dict)
              and learned.note_id(str(one.get("heading", "")), str(one.get("body", ""))) in wanted]
    written = learned.take(chosen)
    return {"ok": bool(written), "written": [note.as_dict() for note in written],
            "detail": "" if written else "none of those could be written"}


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
