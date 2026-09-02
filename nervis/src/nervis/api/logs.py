"""`/api/v1/logs` — M10's raw-log adapters (§11.3).

Fourth on the diagnostics ladder and deliberately last: a text log is never
parsed when structured data exists, so nothing here is a substitute for the
event hub. What it is for is the case the hub cannot serve — a service that
crashed before it could publish anything, or an upstream's own complaint that
reached stderr and nowhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from nervis import logs

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])


def _run_directory(request: Request) -> Path | None:
    """Where the launcher put the logs, or nothing at all.

    Empty is the honest answer for a NERVIS somebody started by hand: the
    documented directory is documented *by the launcher*, and guessing one would
    be inventing a source §11.3 forbids inventing.
    """
    configured = str(getattr(request.app.state.settings, "run_directory", "") or "")
    return Path(configured) if configured else None


@router.get("")
async def list_sources(request: Request) -> dict[str, Any]:
    """Every documented adapter, with its format, size and rotation count."""
    run = _run_directory(request)
    if run is None:
        return {"items": [], "configured": False,
                "reason": "no run directory is configured, so no log adapter exists"}
    return {"items": [one.as_dict() for one in logs.sources(run)],
            "configured": True, "reason": "",
            "limits": {"max_bytes": logs.MAX_BYTES, "max_files": logs.MAX_FILES,
                       "retention_days": logs.RETENTION_DAYS}}


@router.get("/{service}")
async def read_source(service: str, request: Request) -> dict[str, Any]:
    """Recent lines from one adapter, filtered and redacted."""
    run = _run_directory(request)
    if run is None:
        return {"items": [], "present": False,
                "reason": "no run directory is configured, so no log adapter exists"}
    query = request.query_params
    return logs.read(
        run, service,
        limit=_int(query.get("limit"), logs.DEFAULT_LIMIT),
        level=query.get("level", ""),
        text=query.get("text", ""),
    )


def _int(raw: str | None, fallback: int) -> int:
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return fallback
