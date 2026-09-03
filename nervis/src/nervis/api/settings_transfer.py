"""`/api/v1/settings/export` and `/import` — M18's backup/export, without secrets.

The allowlist that decides what may cross this boundary lives in
`nervis.settings_transfer`, not here. This file is the two routes that call it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

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
