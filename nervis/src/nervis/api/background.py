"""`/api/v1/background` — unattended work, and the switch that governs it (M25).

Read the configuration and the ledger; change the configuration. There is
deliberately no endpoint that *starts* a run: unattended work is unattended, and
a "run now" button would be an attended run wearing the wrong name — the thing
it would exercise is the timer, which is not what anybody wants to test.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from nervis import background
from nervis.errors import RefusedError

router = APIRouter(prefix="/api/v1/background", tags=["background"])


@router.get("")
async def read_background(request: Request) -> dict[str, Any]:
    """The settings, the ledger, and why a run would or would not happen now.

    `why_not` is returned even when it is empty, because "nothing has happened"
    has several causes and a screen that cannot tell them apart sends somebody
    looking for a bug in the one case where the answer is that they switched it
    off.
    """
    database = request.app.state.database
    config = background.settings(database)
    return {
        **config.as_dict(),
        "runs": background.runs(database),
        "ran_today": background.ran_today(database),
        "why_not": background.may_run(database, config),
    }


@router.post("")
async def configure_background(request: Request) -> dict[str, Any]:
    """Change the switch, the pool, the interval, the ceiling or one trigger."""
    body = await request.json()
    if not isinstance(body, dict):
        raise RefusedError("a configuration must be a JSON object")
    try:
        config = background.configure(request.app.state.database, **body)
    except ValueError as refusal:
        raise RefusedError(str(refusal)) from refusal
    return config.as_dict()
