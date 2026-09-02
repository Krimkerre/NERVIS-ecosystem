"""`/api/v1/notifications` — the notification centre (M21).

Three reads and two writes, and deliberately no bulk anything: there is no
mark-all-read and no per-kind mute, because both are the class silence M21
forbids under a friendlier name.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis import notifications
from nervis.errors import NotFoundError

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(request: Request) -> dict[str, Any]:
    """Newest first, with the count the badge draws.

    The count comes back on the listing rather than from its own endpoint: the
    badge and the list are always shown together, and two requests could
    disagree with each other in the gap between them.
    """
    database = request.app.state.database
    limit = _int(request.query_params.get("limit"), notifications.DEFAULT_LIMIT)
    include = request.query_params.get("include_dismissed", "").lower() in {"1", "true", "yes"}
    unread_only = request.query_params.get("unread", "").lower() in {"1", "true", "yes"}
    dismissed_only = request.query_params.get("dismissed", "").lower() in {"1", "true", "yes"}
    items = notifications.recent(
        database,
        limit,
        include_dismissed=include,
        only_unread=unread_only,
        only_dismissed=dismissed_only,
    )
    return {
        "items": [note.as_dict() for note in items],
        "unread": notifications.unread_count(database),
        "includes_dismissed": include,
    }


@router.post("/{note_id}/read")
async def read_notification(note_id: str, request: Request) -> dict[str, Any]:
    """Mark one note read. Reading is not dismissing — it only clears the badge."""
    database = request.app.state.database
    if not notifications.mark_read(database, note_id):
        raise NotFoundError(f"no notification {note_id!r}")
    return {"note_id": note_id, "unread": notifications.unread_count(database)}


@router.post("/{note_id}/dismiss")
async def dismiss_notification(note_id: str, request: Request) -> dict[str, Any]:
    """Dismiss one note. It stops being listed; it is not deleted."""
    database = request.app.state.database
    if not notifications.dismiss(database, note_id):
        raise NotFoundError(f"no notification {note_id!r}")
    return {"note_id": note_id, "unread": notifications.unread_count(database)}


def _int(raw: str | None, fallback: int) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return fallback
