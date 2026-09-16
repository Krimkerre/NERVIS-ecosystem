"""Tasks NERVIS handed to Clarvis, and how far each has got (NERVIS M27, CLARVIS.md §6.4).

The owner decided on 16 September 2026 that a Clarvis "task" is a handover from NERVIS.
NERVIS records each one it writes (`nervis.command.attempted`, with the handover's id)
and Clarvis says where the task has got to (`clarvis.task.*`, naming the same id). This
read joins the two, **across every editor window**, since one task is picked up in one
window and built across several.

**Nothing is stored for it.** Both halves are events the hub already keeps for its own
reasons, so a handover older than the hub's retention drops out of this list with them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis import clarvis

router = APIRouter(prefix="/api/v1/handovers", tags=["clarvis"])

# How far back each read goes, newest first. Handovers are a few a day.
READ_LIMIT = 500


@router.get("")
async def read_handovers(request: Request) -> dict[str, Any]:
    """Every handover the hub still holds, with Clarvis's latest word on it."""
    hub = request.app.state.hub
    audits = hub.query(event_type="nervis.command.attempted", limit=READ_LIMIT, latest=True)
    reports = sorted(
        [*hub.query(event_type=clarvis.TASK_STARTED, limit=READ_LIMIT, latest=True),
         *hub.query(event_type=clarvis.TASK_COMPLETED, limit=READ_LIMIT, latest=True)],
        key=lambda event: int(event.get("_sequence") or 0),
    )
    return {"items": clarvis.handovers(audits, reports)}
