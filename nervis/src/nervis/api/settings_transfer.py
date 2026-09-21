"""`/api/v1/settings/export`, `/import` and `/peer` — settings that cross a boundary.

The allowlist that decides what may cross lives in `nervis.settings_transfer`, not here.
This file is the routes that call it: two for a file somebody saves and picks, and two for
the other computer, read live through the link (`nervis.peers.computer`).

**The same allowlist for all four.** A pull from the other machine is applied through
`import_settings`, exactly as a chosen file is, so there is one answer to "what may cross"
rather than one per door.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis.peers.computer import differences, linked_peer, peer_settings
from nervis.settings_transfer import export_settings, import_settings

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("/export")
async def export_(request: Request) -> dict[str, Any]:
    """Every exportable setting, ready to save as a file.

    A `GET` rather than a download endpoint: the browser already has this body
    as JSON once the request resolves, and turning it into a saved file from
    there is a client-side concern, the same way the PDF export's preview and
    its download are two different steps.
    """
    return export_settings(request.app.state.database)


@router.post("/import")
async def import_(request: Request) -> dict[str, Any]:
    """Apply an exported file's settings, and report what was skipped.

    The body *is* the file — a person picks it in the browser and its contents
    are posted whole, unedited between picking and sending, so what is applied
    is provably what they chose.
    """
    body = await request.json()
    outcome = import_settings(request.app.state.database, body)
    return outcome.as_dict()


@router.get("/peer")
async def peer_preview(request: Request) -> dict[str, Any]:
    """What the other computer has, and what a pull would change here. Changes nothing.

    Answers with the same shape whether or not there is a link, whether or not the other
    machine is awake: a screen asking "should I offer this?" gets one object to read rather
    than a status code to interpret.
    """
    peer = linked_peer(request.app.state.database)
    dials = bool(peer["enabled"] and peer["address"])
    answer: dict[str, Any] = {
        "linked": dials,
        "address": peer["address"],
        "reachable": False,
        "detail": "",
        "changes": [],
        "unchanged": 0,
    }
    # **Asked even when this machine dials nobody**, because a link has two ends and only one
    # of them holds the address: the machine that *accepts* the connection has nothing in its
    # settings, and a pull offered only to the dialling side would be missing from whichever
    # computer is sitting still. Two refused loopback connections is what that costs.
    theirs, trouble = peer_settings()
    if theirs is None:
        answer["detail"] = trouble if dials else (
            "no other computer is linked — Settings → Another computer, or tools/run.py link add")
        return answer
    if not dials:
        answer["linked"] = True
        answer["address"] = peer["address"] or "the computer that linked to this one"
    ours = export_settings(request.app.state.database)["settings"]
    changes = differences(theirs.get("settings") or {}, ours)
    answer["reachable"] = True
    answer["changes"] = changes
    answer["unchanged"] = len(theirs.get("settings") or {}) - len(changes)
    answer["exported_at"] = theirs.get("exported_at", "")
    return answer


@router.post("/peer")
async def peer_apply(request: Request) -> dict[str, Any]:
    """Apply what the other computer has now, and report what was applied and skipped.

    **Read again rather than taking a body.** The page has just shown a preview, and the
    honest thing to apply is what the other machine holds at the moment somebody says yes —
    not what a browser is holding from a minute ago, and not whatever a caller chose to post.
    The preview is then a preview of this, rather than of a different request.
    """
    peer = linked_peer(request.app.state.database)
    theirs, trouble = peer_settings()
    if theirs is None:
        return {"applied": [], "skipped": [],
                "detail": trouble if peer["address"] else "no other computer is linked"}
    outcome = import_settings(request.app.state.database, theirs)
    address = peer["address"] or "the computer that linked to this one"
    return {**outcome.as_dict(), "address": address, "detail": ""}
