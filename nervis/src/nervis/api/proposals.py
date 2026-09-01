"""`/api/v1/proposals` — what became of each offer (M22).

One write and two reads. The write files an answer; the reads are what a person
needs before erasing the record, and the erase itself.

**No endpoint records silence**, and there is deliberately no sweeper that turns
an old unanswered offer into a decline. M22's sentence — *a person who closed the
tab did not decline* — is enforced by there being no code path that could.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis import proposals
from nervis.errors import RefusedError

router = APIRouter(prefix="/api/v1/proposals", tags=["proposals"])


@router.post("/outcome")
async def record_outcome(request: Request) -> dict[str, Any]:
    """File what a person did with one offer.

    Refuses an outcome outside the three, rather than storing it and letting a
    later reader decide what an unrecognised word meant.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise RefusedError("an outcome must be a JSON object")
    try:
        proposals.record(
            request.app.state.database,
            proposal_id=str(body.get("proposal_id") or ""),
            operation=str(body.get("operation") or ""),
            outcome=str(body.get("outcome") or ""),
            target=str(body.get("target") or ""),
            edited_to=str(body.get("edited_to") or ""),
            conversation_id=str(body.get("conversation_id") or ""),
        )
    except ValueError as refusal:
        raise RefusedError(str(refusal)) from refusal
    # The updated history comes back, so the card that just recorded an answer
    # can show what it now knows without a second request.
    past = proposals.history(
        request.app.state.database,
        str(body.get("operation") or ""),
        str(body.get("target") or ""),
    )
    return {"recorded": True, "history": past.as_dict()}


@router.get("")
async def list_outcomes(request: Request) -> dict[str, Any]:
    """The whole record, newest first.

    A control that erases something has to be able to show what it will erase,
    or "clear" is a button people press hopefully.
    """
    items = proposals.recent(request.app.state.database)
    return {"items": items, "count": len(items)}


@router.delete("")
async def forget_outcomes(request: Request) -> dict[str, Any]:
    """Erase the record. Every proposal afterwards is composed as it was before.

    That is a property of where history is applied rather than a promise made
    here: `propose` never reads it, so there is nothing left behind to unlearn.
    """
    return {"forgotten": proposals.forget(request.app.state.database)}
