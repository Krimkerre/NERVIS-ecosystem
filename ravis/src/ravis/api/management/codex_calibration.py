"""Calibration's route: `/api/v1/codex/calibration/runs`, dev-only (design §10.4; RAVIS.md §15.1.2).

**It exists only while `RAVIS_CODEX_CALIBRATION=1`**: `app.py` mounts this router then and never
otherwise, so on the running stack every path here is a plain 404. The launcher never sets it.

**Only the owner's command-line credential** — `require_owner_cli`, as the file-rules re-test
— may start a run or read one, because a run spends the plan's allowance and its progress names the
owner's project folders. NERVIS's `admin.launcher`, every client and anonymous callers get
403 `REPROOF_NOT_ALLOWED`, and no NERVIS route forwards here (`codex-admin.json`,
`calibration_route`).

| route | what |
|---|---|
| `POST /runs` | start a run: 202 with its progress; needs an `Idempotency-Key` |
| `GET /runs` | the latest run's progress and result, or null |
| `GET /runs/{run_id}` | that run's, or null for a run this RAVIS doesn't know |

The body is `{"project_a", "project_b", "allowance_go_ahead": true, "scenarios"?: ["K5a", …]}`; a
run of only model-free questions (K10, K5a) needs no allowance go-ahead. Refusals: 422
`INVALID_REQUEST_BODY` for a body or project that can't be used, 409 `CODEX_NOT_READY` when Codex
can't run it now (not signed in, the allowance used up, a project locked), 409
`CODEX_RUN_IN_PROGRESS` beside a task, a re-test or another run.

**Audited:** the run started (with its scenario ids); each answer and the result are audited by the
service as they happen.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ravis.api.management import audit
from ravis.api.management.codex import _json_object, require_owner_cli
from ravis.codex import refusals
from ravis.codex.idempotency import valid_key
from ravis.codex.service import CodexService

router = APIRouter(prefix="/api/v1/codex/calibration", tags=["codex"])


def _service(request: Request) -> CodexService:
    return request.app.state.codex_service  # type: ignore[no-any-return]


@router.post("/runs", dependencies=[Depends(require_owner_cli)])
async def start_calibration(request: Request) -> JSONResponse:
    """Start a calibration run: 202 with its progress, which `GET` follows."""
    key = request.headers.get("idempotency-key", "")
    if not valid_key(key):
        raise refusals.idempotency_key_required()
    service = _service(request)
    application_id = request.state.identity.application_id
    body_sha256 = hashlib.sha256(await request.body()).hexdigest()
    kept = service.kept_calibration_answers.find(application_id, key, body_sha256)
    if kept is not None:
        return JSONResponse(kept.body, status_code=kept.status)
    body = await _json_object(request)
    view = service.start_calibration(application_id, body)
    audit.record(
        request, "ravis.codex.calibration_started",
        run_id=view["run_id"], scenarios=[scenario["id"] for scenario in view["scenarios"]],
    )
    answer = {"calibration": view}
    service.kept_calibration_answers.keep(application_id, key, body_sha256, 202, answer)
    return JSONResponse(answer, status_code=202)


@router.get("/runs", dependencies=[Depends(require_owner_cli)])
async def read_latest_calibration(request: Request) -> dict[str, Any]:
    return {"calibration": _service(request).calibration_view(None)}


@router.get("/runs/{run_id}", dependencies=[Depends(require_owner_cli)])
async def read_calibration(run_id: str, request: Request) -> dict[str, Any]:
    return {"calibration": _service(request).calibration_view(run_id)}
