"""Codex's state, sign-in, account, versions and the file-rules re-test: `/api/v1/codex` (§15.1.2).

The shapes, codes and messages are the contract fixtures `codex-state.json` and `codex-admin.json`
(`tests/fixtures/relay-contract/`); this module is the HTTP edge of `ravis.codex.service`.

**Who may call what** (RAVIS.md §15.1.2; design §3.3, §3.4):

| route | who |
|---|---|
| `GET /api/v1/codex` | anyone: the launcher, NERVIS's GET relay, anonymous callers |
| `POST`/`GET`/`DELETE /sign-in`, `POST /sign-out`, `POST /account/confirm` | admin |
| `GET /version-check`, `POST /accept-version`, `DELETE /accept-version/{sha256}` | admin |
| `POST /reprove` | admin of an application in `codex_reproof_applications` (`owner_cli`) |
| `GET /reprove` | any named caller |
| `GET /sites` | Clarvis's client credential, NERVIS's (its GET relay) or an admin credential |
| `POST /sites` | Clarvis's client credential only (`require_agent_client`) |
| `DELETE /sites/{host}` | admin: NERVIS's Codex card, through its control route |

**Admin here is UX and audit, not a boundary** against a program running as the owner (design §2.2
fact 14): the credentials are 0600 files and NERVIS's control token is served in its page. What the
split does buy is that an ordinary client — Clarvis — can't sign Codex out or accept a build, and
that **NERVIS can never start Codex work**: the re-test refuses NERVIS's `admin.launcher` like
everyone else (F-A3), and no NERVIS route forwards to it.

**How NERVIS's credentials screen reaches the sign-in** (owner requirement, 13 September 2026):
its control routes check NERVIS's control token and forward with NERVIS's admin credential,
exactly as provider keys are saved today. Everything these routes return is safe to render there:
the account appears only as a hint (`o…@example.com`) and never as a token, because RAVIS never
holds one. The sign-in page's address is on the admin routes only — `GET /api/v1/codex` says a
sign-in waits, but not where.

**Audited** (design §3.9): sign-in started and cancelled, signed out, account confirmed, version
accepted and revoked, re-test started, sites allowed and a site removed (R5); the re-test's answers
and result are audited by the service as they happen.

**The allowed sites** (R5, `codex-admin.json`): the owner allows the sites a task will likely need
from Clarvis before it starts, and removes an added one from NERVIS's Codex card. The list itself is
Codex's own configuration, read and written by `agent/sites.py`'s `SiteAllowlist`.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ravis.agent.identity import require_agent_client
from ravis.agent.sites import MOST_HOSTS, SiteAllowlist
from ravis.api.management import audit
from ravis.codex import refusals
from ravis.codex.idempotency import valid_key
from ravis.codex.service import CodexService

router = APIRouter(prefix="/api/v1/codex", tags=["codex"])

#: A binary's sha256, as the version routes take it.
SHA256 = re.compile(r"[0-9a-f]{64}")


def _service(request: Request) -> CodexService:
    return request.app.state.codex_service  # type: ignore[no-any-return]


def require_admin(request: Request) -> None:
    """An `admin.` credential, read directly from the resolved identity (never via `getattr`)."""
    if not request.state.identity.may_write_configuration:
        raise refusals.admin_required()


def require_named_caller(request: Request) -> None:
    if request.state.identity.is_anonymous:
        raise refusals.named_caller_required()


def require_sites_reader(request: Request) -> None:
    """Who may read the allowed sites (R5): Clarvis, NERVIS's GET relay, or an admin credential."""
    identity = request.state.identity
    if identity.is_anonymous:
        raise refusals.sites_reader_required()
    if identity.may_write_configuration:
        return
    readers = {"nervis", *_service(request).settings.agent_client_applications}
    if identity.application_id not in readers:
        raise refusals.sites_reader_required()


def require_owner_cli(request: Request) -> None:
    """The routes that start Codex work: an admin credential of a re-test application (F-A3)."""
    identity = request.state.identity
    allowed = _service(request).settings.codex_reproof_applications
    if not identity.may_write_configuration or identity.application_id not in allowed:
        raise refusals.reproof_not_allowed()


async def _json_object(request: Request) -> dict[str, Any]:
    """The request's JSON object; an empty body is `{}`."""
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise refusals.invalid_body("The body must be a JSON object.") from None
    if not isinstance(parsed, dict):
        raise refusals.invalid_body("The body must be a JSON object.")
    return parsed


@router.get("")
async def read_codex(request: Request) -> dict[str, Any]:
    """Codex's state, allowance and tasks, from memory: always 200 while RAVIS is up.

    Task ids and turn ids only for a named caller (`codex-state.json` → `runs_rules`).
    """
    return _service(request).snapshot(named=not request.state.identity.is_anonymous)


@router.post("/sign-in", dependencies=[Depends(require_admin)])
async def start_sign_in(request: Request) -> JSONResponse:
    body = await _json_object(request)
    method = body.get("method", "browser")
    if method != "browser":
        raise refusals.sign_in_method_not_supported(method)
    status, answer = await _service(request).start_sign_in()
    if status == 202:
        audit.record(request, "ravis.codex.sign_in_started")
    return JSONResponse(answer, status_code=status)


@router.get("/sign-in", dependencies=[Depends(require_admin)])
async def read_sign_in(request: Request) -> dict[str, Any]:
    """While a browser is awaited: the page's address, so the dashboard can open it again."""
    return {"sign_in": _service(request).sign_in_view()}


@router.delete("/sign-in", dependencies=[Depends(require_admin)])
async def cancel_sign_in(request: Request) -> dict[str, Any]:
    cancelled = await _service(request).cancel_sign_in()
    audit.record(request, "ravis.codex.sign_in_cancelled", cancelled=cancelled)
    return {"cancelled": cancelled}


@router.post("/sign-out", dependencies=[Depends(require_admin)])
async def sign_out(request: Request) -> dict[str, Any]:
    await _json_object(request)
    answer = await _service(request).sign_out()
    audit.record(request, "ravis.codex.signed_out")
    return answer


@router.post("/account/confirm", dependencies=[Depends(require_admin)])
async def confirm_account(request: Request) -> dict[str, Any]:
    body = await _json_object(request)
    # Present, as a string — or as null, for an account Codex reports without an email.
    if "email_hint" not in body or not isinstance(body["email_hint"], str | None):
        raise refusals.invalid_body(
            "The body must carry email_hint: the account hint the dashboard showed."
        )
    answer = await _service(request).confirm_account(body["email_hint"])
    audit.record(request, "ravis.codex.account_confirmed")
    return answer


@router.get("/version-check", dependencies=[Depends(require_admin)])
async def version_check(request: Request) -> dict[str, Any]:
    return await _service(request).version_check()


@router.post("/accept-version", dependencies=[Depends(require_admin)])
async def accept_version(request: Request) -> dict[str, Any]:
    body = await _json_object(request)
    sha256 = body.get("sha256")
    if not isinstance(sha256, str) or SHA256.fullmatch(sha256) is None:
        raise refusals.invalid_body(
            "The body must carry sha256: the installed binary's 64-character sha256."
        )
    answer, recorded = await _service(request).accept_version(
        sha256, request.state.identity.application_id
    )
    if recorded:
        audit.record(request, "ravis.codex.version_accepted", sha256=sha256)
    return answer


@router.delete("/accept-version/{sha256}", dependencies=[Depends(require_admin)])
async def revoke_version(sha256: str, request: Request) -> dict[str, Any]:
    answer, revoked = await _service(request).revoke_version(sha256)
    if revoked:
        audit.record(request, "ravis.codex.version_acceptance_revoked", sha256=sha256)
    return answer


@router.post("/reprove", dependencies=[Depends(require_owner_cli)])
async def start_reproof(request: Request) -> JSONResponse:
    """Start the file-rules re-test: 202 while it runs; the result comes from `GET`."""
    key = request.headers.get("idempotency-key", "")
    if not valid_key(key):
        raise refusals.idempotency_key_required()
    service = _service(request)
    application_id = request.state.identity.application_id
    body_sha256 = hashlib.sha256(await request.body()).hexdigest()
    kept = service.kept_reproof_answers.find(application_id, key, body_sha256)
    if kept is not None:
        return JSONResponse(kept.body, status_code=kept.status)
    await _json_object(request)
    service.start_reproof(application_id)
    audit.record(request, "ravis.codex.reproof_started")
    answer = {"reproof": {"state": "running"}}
    service.kept_reproof_answers.keep(application_id, key, body_sha256, 202, answer)
    return JSONResponse(answer, status_code=202)


@router.get("/reprove", dependencies=[Depends(require_named_caller)])
async def read_reproof(request: Request) -> dict[str, Any]:
    return _service(request).reproof_view()


# ── The sites Codex's commands may reach (R5) ────────────────────────────────


def _sites(request: Request) -> SiteAllowlist:
    return _service(request).agents.context.sites


def _hosts(body: dict[str, Any]) -> list[object]:
    """The body's `hosts`: 1 to 20 strings, or 422; each is checked as a site afterwards."""
    hosts = body.get("hosts")
    if (not isinstance(hosts, list) or not 1 <= len(hosts) <= MOST_HOSTS
            or not all(isinstance(host, str) for host in hosts)):
        raise refusals.invalid_body(f"The body must carry hosts: 1 to {MOST_HOSTS} host names.")
    return hosts


@router.get("/sites", dependencies=[Depends(require_sites_reader)])
async def read_sites(request: Request) -> dict[str, Any]:
    """RAVIS's default sites and the ones the owner added, read from Codex each time."""
    return await _sites(request).listed()


@router.post("/sites", dependencies=[Depends(require_agent_client)])
async def allow_sites(request: Request) -> dict[str, Any]:
    """The owner's click in Clarvis before a task: every host checked, then one write."""
    hosts = _hosts(await _json_object(request))
    allowed, view = await _sites(request).allow(hosts)
    audit.record(request, "ravis.codex.sites_allowed", hosts=allowed)
    return view


@router.delete("/sites/{host}", dependencies=[Depends(require_admin)])
async def remove_site(host: str, request: Request) -> dict[str, Any]:
    """NERVIS's Codex card removing a site the owner added; a default is never removed."""
    removed, view = await _sites(request).remove(host)
    if removed is not None:
        audit.record(request, "ravis.codex.site_removed", host=removed)
    return view
