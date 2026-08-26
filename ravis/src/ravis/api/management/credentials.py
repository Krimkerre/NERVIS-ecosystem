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
from ravis.model_filter import ModelFilter, ModelFilters
from ravis.provider_state import ProviderState

router = APIRouter(prefix="/api/v1/providers", tags=["management"])

# The providers RAVIS knows how to reach. Listed rather than discovered so the
# screen can show a row for a provider that has *no* credential yet — which is
# the only row that matters when someone is trying to add one.
KNOWN_PROVIDERS = ("anthropic", "google", "openrouter")

# What to call each provider on a screen.
#
# **The id stays `google` and only the label changes.** The id is a wire value:
# it keys the credential file, it is half the environment variable name
# (`RAVIS_GOOGLE_API_KEY`), and it is a path segment in `ravis/<provider>/<model>`.
# Renaming it would orphan every credential already stored and break every
# address already written down, to fix something only a person reads. "Google AI
# Studio" is the accurate name because that is the surface these keys come from
# — as opposed to Vertex AI, which is the same models behind a service account
# and a different credential shape entirely.
PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "google": "Google AI Studio",
    "openrouter": "OpenRouter",
}

# How many matched ids a filter preview returns. The screen needs enough to see
# that a pattern did what was meant, not the whole of a 417-model catalogue —
# and `matched_total` is reported separately so this cap is never mistaken for
# the answer.
SAMPLE_LIMIT = 50


class FilterInput(BaseModel):
    """Include and exclude patterns for one provider's catalogue."""

    include: list[str] = []
    exclude: list[str] = []


class EnabledInput(BaseModel):
    """Whether a provider may be routed to."""

    enabled: bool


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
    routable = _routable(request)
    return {
        "items": [
            {
                **store.status(name).as_dict(),
                "label": PROVIDER_LABELS.get(name, name),
                # Whether anything can *use* this credential.
                #
                # A screen that accepts a key for a provider RAVIS cannot reach
                # is a screen making a promise the service does not keep, and
                # this row is the difference between "not configured" and
                # "configured and going nowhere". §4.1's advertise-when rule
                # applied to a settings page.
                "routable": name in routable,
            }
            for name in sorted(known)
        ],
        "next_cursor": None,
        "snapshot_revision": 1,
    }


def _routable(request: Request) -> set[str]:
    """Providers a request could actually be sent to.

    Two ways to be reachable: a translating adapter for a provider whose wire
    protocol is its own, or a transparent upstream declared with that name or
    kind. Both are read from live application state rather than from a list, so
    this cannot drift from what routing will actually do.
    """
    translating = set(getattr(request.app.state, "translating", {}))
    transparents = getattr(request.app.state, "transparents", {})
    declared = {name for name in transparents}
    declared |= {t.spec.kind.strip().lower() for t in transparents.values()}
    return translating | declared


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


@router.put("/{name}/enabled")
async def set_enabled(name: str, body: EnabledInput, request: Request) -> Any:
    """Turn a provider on or off without touching its credential.

    Kept distinct from removing the key, because they answer different
    questions. Disabling is reversible in one click and says "not right now";
    deleting the credential says "this deployment no longer holds one". A UI
    that offered only the second would make an operator destroy configuration
    to achieve a pause.
    """
    refusal = _may_write(request)
    if refusal:
        return _refused(refusal)
    state: ProviderState = request.app.state.provider_state
    return {"name": name, "enabled": state.set_enabled(name, body.enabled)}


async def _catalogue(request: Request, name: str) -> list[str]:
    """Every model id the provider publishes, before filtering.

    Read from the cached registry for a transparent upstream and from the
    adapter for a translated one. A provider that cannot be reached yields an
    empty list rather than raising: the filter screen must still render for a
    provider whose key has not been entered, because entering the key is the
    next thing the operator is going to do.
    """
    transparents = getattr(request.app.state, "transparents", {})
    built = transparents.get(name)
    if built is not None:
        return list(built.registry.model_ids())
    adapter = getattr(request.app.state, "translating", {}).get(name)
    if adapter is None:
        return []
    try:
        return list(await adapter.models())
    except Exception:  # noqa: BLE001 - an unreachable provider is an empty catalogue
        return []


@router.get("/{name}/models")
async def read_model_filter(name: str, request: Request) -> dict[str, Any]:
    """The provider's filter, and what it currently selects.

    Returns the *counts* alongside the matched ids because the count is the
    number an operator is actually reading: "23 of 417" is the feedback that
    makes a pattern editable, and it is the difference between guessing at a
    glob and seeing what it did.

    `sample` is capped, and `matched_total` is reported separately so the cap
    can never be mistaken for the result. A truncated list presented as the
    whole answer is the exact failure this whole filter exists to prevent.
    """
    catalogue = await _catalogue(request, name)
    filters: ModelFilters = request.app.state.model_filters
    model_filter = filters.for_provider(name)
    matched = model_filter.apply(catalogue)
    return {
        "name": name,
        "filter": model_filter.as_dict(),
        "filtered": not model_filter.is_empty,
        "catalogue_total": len(catalogue),
        "matched_total": len(matched),
        "sample": matched[:SAMPLE_LIMIT],
        "sample_limit": SAMPLE_LIMIT,
        "catalogue_sample": catalogue[:SAMPLE_LIMIT],
    }


@router.put("/{name}/models")
async def set_model_filter(name: str, body: FilterInput, request: Request) -> Any:
    """Replace one provider's filter, and report what it now selects."""
    refusal = _may_write(request)
    if refusal:
        return _refused(refusal)
    filters: ModelFilters = request.app.state.model_filters
    filters.set_for(
        name, ModelFilter(include=tuple(body.include), exclude=tuple(body.exclude))
    )
    return await read_model_filter(name, request)
