"""Entering provider credentials (M10).

Deliberately **not** part of `routes.py`. That module's contract is reads only,
with mutations deferred to M18b behind `Idempotency-Key`, separate authorization
and audit events (§15.1). Credential entry is M10's own mandate — "provider UI"
— and is a different shape from a general resource mutation:

* **Write-only.** A credential goes in and never comes back out. The read
  endpoint returns `CredentialStatus`, a type with no field able to hold a
  value, so §15.1's redaction requirement is satisfied by construction rather
  than by remembering to strip a field.
* **`PUT`, not `POST`.** Setting a named credential to a value is idempotent by
  nature: doing it twice leaves the same state. That is what M18b's
  `Idempotency-Key` exists to reconstruct for operations that lack it, so the
  machinery is not needed here rather than skipped here.

**Why a service may write credentials at all.** The prototype dashboard said
credentials were never editable from a screen, on the reasoning that a service
able to write them is a service whose compromise rewrites them. That reasoning
is weaker than it looked: RAVIS binds to loopback, and anything that can reach
this endpoint can already read the process memory holding the same keys. Against
that, a provider nobody can configure is a provider nobody can use — which is
the state Google and OpenRouter would have shipped into.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ravis.credentials import CredentialStore

router = APIRouter(prefix="/api/v1/providers", tags=["management"])

# The providers RAVIS knows how to reach. Listed rather than discovered so the
# screen can show a row for a provider that has *no* credential yet — which is
# the only row that matters when someone is trying to add one.
KNOWN_PROVIDERS = ("anthropic", "google", "openrouter")


class CredentialInput(BaseModel):
    """One credential arriving from the UI.

    **Deliberately unconstrained.** A pydantic validation failure echoes the
    offending input back in the 422 body, so every constraint on this field is a
    path by which a secret reaches a response and a client-side error log. With
    no constraint there is nothing for pydantic to reject, and emptiness is
    checked in the handler instead — where the message can name the problem
    without quoting the value.
    """

    secret: str


def _store(request: Request) -> CredentialStore:
    return request.app.state.credentials  # type: ignore[no-any-return]


def _refused(detail: str) -> JSONResponse:
    return JSONResponse({"error": {"message": detail, "type": "forbidden"}}, status_code=403)


def _may_write(request: Request) -> str | None:
    """Whether this request may change a credential, and why not if it may not.

    A loopback-bound RAVIS is reachable only from the machine it runs on, which
    is the deployment this endpoint is for. A non-loopback bind already fails to
    start without TLS *and* a client credential (§9.6.0), so reaching here on
    one means an identity was presented — and an anonymous identity on a
    published service must not be able to write keys.
    """
    settings = request.app.state.settings
    if settings.is_loopback_bind():
        return None
    identity = getattr(request.state, "identity", None)
    if identity is None or getattr(identity, "is_anonymous", False):
        return "credential changes require an authenticated client on a non-loopback bind"
    return None


@router.get("/credentials")
async def list_credentials(request: Request) -> dict[str, Any]:
    """Every known provider, whether it has a credential, and from where.

    Never a value, and never part of one. The screen needs exactly this to show
    "configured / not configured" and to warn when the file's permissions have
    drifted.
    """
    store = _store(request)
    known = set(store.known()) | set(KNOWN_PROVIDERS)
    return {
        "items": [store.status(name).as_dict() for name in sorted(known)],
        "next_cursor": None,
        "snapshot_revision": 1,
    }


@router.put("/credentials/{name}")
async def set_credential(name: str, body: CredentialInput, request: Request) -> Any:
    """Store one provider credential, replacing any previous value."""
    refusal = _may_write(request)
    if refusal:
        return _refused(refusal)
    if not body.secret.strip():
        return JSONResponse(
            {"error": {"message": "credential must not be empty",
                       "type": "invalid_request_error"}},
            status_code=400,
        )
    try:
        status = _store(request).store(name, body.secret)
    except ValueError as failure:
        # The message names the problem, never the value that caused it.
        return JSONResponse(
            {"error": {"message": str(failure), "type": "invalid_request_error"}},
            status_code=400,
        )
    return status.as_dict()


@router.delete("/credentials/{name}")
async def forget_credential(name: str, request: Request) -> Any:
    """Remove one credential from RAVIS's own file.

    The response may still report the credential configured, from the
    environment or the Keychain. That is the truth rather than a failed delete:
    RAVIS does not remove things it did not put there.
    """
    refusal = _may_write(request)
    if refusal:
        return _refused(refusal)
    return _store(request).forget(name).as_dict()
