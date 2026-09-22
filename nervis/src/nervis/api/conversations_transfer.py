"""`/api/v1/chat/transfer` and `/api/v1/chat/peer` — conversations crossing the link.

Two halves of one thing, and every NERVIS has both: `transfer` is what this computer *offers*
to the one linked to it, `peer` is what it *takes* from the other. Which half is used depends
on which computer somebody is sitting at, not on which one dialled.

The rules that make this safe to press live in `nervis.conversations_transfer`: adds only,
identifiers and times kept, nothing overwritten and nothing removed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis.conversations_transfer import differences, listing, one, take
from nervis.peers.computer import linked_peer, peer_conversation, peer_conversations

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

#: How many conversations one press may bring over. High enough for a real history, bounded
#: because each one is a separate read across the link and a runaway list should not become a
#: request that never ends.
MOST_AT_ONCE = 100


@router.get("/transfer")
def offer(request: Request) -> dict[str, Any]:
    """This computer's conversations, listed for the computer linked to it. No messages."""
    return {"conversations": listing(request.app.state.database)}


@router.get("/transfer/{conversation_id}")
def offer_one(conversation_id: str, request: Request) -> dict[str, Any]:
    """One conversation with its turns, for the computer linked to this one."""
    found = one(request.app.state.database, conversation_id)
    return found or {"conversation": {}, "messages": [], "detail": "no such conversation"}


@router.get("/peer")
def what_they_have(request: Request) -> dict[str, Any]:
    """The other computer's conversations, and what taking them would add here.

    Reads only, and answers in one shape whether or not there is a link and whether or not
    the other computer is awake.
    """
    peer = linked_peer(request.app.state.database)
    answer: dict[str, Any] = {"address": peer["address"], "detail": "", "changes": [],
                              "same": 0, "reachable": False}
    theirs, trouble = peer_conversations()
    if theirs is None:
        answer["detail"] = trouble if peer["address"] else (
            "no other computer is linked — Settings → Another computer")
        return answer
    ours = listing(request.app.state.database)
    changes = differences([row for row in theirs if isinstance(row, dict)], ours)
    answer["reachable"] = True
    answer["changes"] = changes
    answer["same"] = max(0, len(theirs) - len(changes))
    if not changes:
        answer["detail"] = "nothing to bring over — this computer already has them all"
    return answer


@router.post("/peer")
async def bring_them_over(request: Request) -> dict[str, Any]:
    """Bring the chosen conversations here, one read of the other computer each.

    **Only what was asked for.** `{"ids": [...]}` is the list somebody ticked; without it
    nothing is taken, because "bring every conversation I have ever had" is not a thing to do
    by accident — the opposite of the settings pull, where taking everything is the ordinary
    case and the list is short.
    """
    try:
        body = await request.json()
    except ValueError:
        body = {}
    chosen = body.get("ids") if isinstance(body, dict) else None
    if not isinstance(chosen, list) or not chosen:
        return {"ok": False, "brought": [], "messages": 0,
                "detail": "nothing was ticked, so nothing was brought over"}
    brought: list[dict[str, Any]] = []
    messages = 0
    trouble = ""
    for conversation_id in [str(one_id) for one_id in chosen[:MOST_AT_ONCE]]:
        theirs, said = peer_conversation(conversation_id)
        if theirs is None:
            trouble = said
            break
        stored = take(request.app.state.database, theirs)
        if not stored.get("ok"):
            trouble = str(stored.get("detail", ""))
            continue
        messages += int(stored.get("added", 0))
        brought.append({"conversation_id": conversation_id, "title": stored.get("title", ""),
                        "added": stored.get("added", 0)})
    return {"ok": bool(brought), "brought": brought, "messages": messages, "detail": trouble}
