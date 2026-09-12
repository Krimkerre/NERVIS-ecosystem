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

import hmac
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ravis.api.management import audit
from ravis.credentials import CredentialStore, Secret
from ravis.errors import ForbiddenError, to_response
from ravis.identity import ADMIN_PREFIX, CLIENT_PREFIX
from ravis.model_filter import ModelFilter, ModelFilters
from ravis.provider_state import ProviderState
from ravis.reliability.failures import FailureClass, HealthScope
from ravis.reliability.health import HealthRegistry

router = APIRouter(prefix="/api/v1/providers", tags=["management"])

# The providers RAVIS knows how to reach. Listed rather than discovered so the
# screen can show a row for a provider that has *no* credential yet — which is
# the only row that matters when someone is trying to add one.
KNOWN_PROVIDERS = ("anthropic", "deepseek", "google", "openai", "openrouter", "xai")

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
    # Both speak the OpenAI protocol, so neither needs an adapter — only an
    # address and a row to type a key into. `xai` rather than `x` or `grok`:
    # the id is a path segment in `ravis/<provider>/<model>` and half an
    # environment variable name, and "x" is too short to read as a provider.
    "deepseek": "DeepSeek",
    "google": "Google AI Studio",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "xai": "xAI",
}

# How many matched ids a filter preview returns. The screen needs enough to see
# that a pattern did what was meant, not the whole of a 417-model catalogue —
# and `matched_total` is reported separately so this cap is never mistaken for
# the answer.
SAMPLE_LIMIT = 50

# How long a successful catalogue refresh answers for when a credential write
# **did not change the key** — so a replayed write reports the catalogue that
# refresh left instead of fetching it again. A write that changes the key never
# waits on this; it refreshes at once.
#
# **A minute, because of what a replay looks like.** It arrives seconds after
# the write it repeats: a double-click, a browser resubmitting, or an operator
# saving again after NERVIS gave up — and NERVIS gives this write ten seconds
# before answering 502 (`_credential_call` in `nervis/peers/ravis.py`), so a
# slow refresh it abandoned is repeated well inside the window. Against that, a
# person re-saving the same key on purpose, to make RAVIS look again, waits at
# most a minute where the alternative is the five-minute periodic refresh
# (`models_cache_ttl_seconds`). And a minute is already how stale RAVIS lets a
# credential lookup be (`CACHE_SECONDS`), so this adds no new kind of wait.
REFRESH_COOLDOWN_SECONDS = 60.0


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


def _refused(request: Request, detail: str) -> JSONResponse:
    """The refusal, in the dialect this path owes (§16 item 10).

    Built through `to_response` rather than by hand. The hand-written version
    answered `/api/v1` in the OpenAI shape — no `code`, no `retryable`, no
    correlation ids — while `to_response` had picked the right shape by path
    prefix all along and was never called.
    """
    return to_response(request, ForbiddenError(detail))


def _may_write(request: Request) -> str | None:
    """Whether this request may change RAVIS's *configuration*.

    Enabling a provider, narrowing a pool, filtering a catalogue: settings, not
    key material.

    **The loopback bypass is gone, which is the whole of §16 item 4.** This used
    to return early on a loopback bind, so administration arrived free with the
    ability to call the gateway: any local process that could send a prompt could
    also disable a provider, narrow a catalogue, or re-point a pool for every
    other client on the machine. Clarvis holds an ordinary client credential, so
    "any local process" was not hypothetical.

    That bypass was defensible while the Providers screen called RAVIS directly
    with nothing to present — a bar here would have been a bar on the user. It
    stopped being defensible when those four writes moved behind NERVIS, which
    already holds the `admin.` credential and already proxies the credential
    writes the same way. The cost the old reasoning priced is gone; the gap is
    not.

    **Credentials remain separate** — see `_may_write_credentials`. Same grantor
    today, different clause, and §15.1 is specifically about key material. Two
    predicates rather than one so a future operator role can be given settings
    without keys.
    """
    identity = getattr(request.state, "identity", None)
    # Read directly, not through `getattr(..., False)`. An earlier version of
    # this guard was written against an `is_anonymous` that `ClientApplication`
    # did not have, so the default answered every call. A missing security
    # predicate must raise, not resolve to "permitted".
    if identity is None or not identity.may_write_configuration:
        return (
            "changing RAVIS's configuration needs an admin credential (§16 item 4); "
            "calling the gateway does not grant it"
        )
    return None


def _may_write_credentials(request: Request) -> str | None:
    """§15.1's separate authorization, for key material and nothing else.

    **The loopback bypass is gone here, and that is the whole change.** It used
    to return early on a loopback bind — the default deployment — on the
    reasoning that such a RAVIS is reachable only from the machine it runs on.
    True, and not the boundary the clause asks for: any local process could
    re-point every provider key, and the ability came free with the ability to
    call the gateway.

    So an ordinary client credential is refused too. The permission comes only
    from an `admin.`-prefixed one, which the launcher mints and hands to NERVIS
    — the same split SIRVIS already has, where a `benchmark` token can queue
    work and only an `admin` one can erase a result.
    """
    identity = getattr(request.state, "identity", None)
    if identity is None or not getattr(identity, "may_write_credentials", False):
        return (
            "changing a provider credential needs an admin credential (§15.1); "
            "calling the gateway does not grant it"
        )
    return None


@router.get("/credentials")
async def list_credentials(request: Request) -> dict[str, Any]:
    """Every known provider, whether it has a credential, and from where.

    Never a value, and never part of one. The screen needs exactly this to show
    "configured / not configured" and to warn when the file's permissions have
    drifted.

    **Provider keys only, never identity credentials.** The same file also
    holds the `client.*` / `admin.*` bearer credentials `identity.py` uses to
    authenticate callers, and this endpoint has no authorization guard of its
    own — it is reachable by an anonymous caller. Reporting a `client.` or
    `admin.` *name* discloses nothing about its value, but it does disclose
    which application ids exist and, worse, which one holds the `admin.`
    credential that grants `may_write_credentials`/`may_write_configuration` —
    reconnaissance an anonymous caller must not get for free. So those
    namespaces are excluded before anything is merged with `KNOWN_PROVIDERS`.
    """
    store = _store(request)
    provider_names = {
        name for name in store.known()
        if not name.startswith(CLIENT_PREFIX) and not name.startswith(ADMIN_PREFIX)
    }
    known = provider_names | set(KNOWN_PROVIDERS)
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
    """Store one provider credential, replacing any previous value.

    **Idempotent in effect, not only in state.** Storing the same key twice
    always left the same state, which is why §15.1's `Idempotency-Key` was left
    off these writes. But every write also re-read the provider's catalogue over
    the network, so a replayed PUT repeated a live refresh while changing
    nothing. A replay cache would have hidden that behind a remembered response;
    the repair is not to repeat the effect. So the refresh runs when the write
    changed the key this name resolves to, or when the last successful refresh
    is older than `REFRESH_COOLDOWN_SECONDS` — and otherwise the total is read
    from the snapshot that refresh left.
    """
    refusal = _may_write_credentials(request)
    if refusal:
        return _refused(request, refusal)
    if not body.secret.strip():
        return JSONResponse(
            {"error": {"message": "credential must not be empty",
                       "type": "invalid_request_error"}},
            status_code=400,
        )
    store = _store(request)
    # Read before the write, so the two can be compared after it. For a name
    # nobody has looked up in the last minute this costs one Keychain lookup,
    # once, on a deliberate act — and `store()` makes another straight after.
    before = store.resolve(name)
    try:
        status = store.store(name, body.secret)
    except ValueError as failure:
        # The message names the problem, never the value that caused it.
        return JSONResponse(
            {"error": {"message": str(failure), "type": "invalid_request_error"}},
            status_code=400,
        )
    refreshed, catalogue_total = await _recatalogue(
        request, name, changed=_changed(before, store.resolve(name))
    )
    # Named facts only: which provider, whether it is now configured, and
    # whether this write re-read the catalogue or reported the last read. The
    # secret is in scope above and is deliberately not passed — §15.1's "never
    # expose credential values" held by construction rather than by a redaction
    # somebody has to remember.
    audit.record(request, audit.ACTION_CREDENTIAL_SET,
                 provider=name, configured=status.configured,
                 source=status.source, catalogue_total=catalogue_total,
                 catalogue_refreshed=refreshed)
    return {**status.as_dict(), "catalogue_total": catalogue_total}


async def _recatalogue(
    request: Request, name: str, *, changed: bool
) -> tuple[bool, int | None]:
    """Re-read the catalogue of whatever this credential unlocks.

    **A key is usually the reason the catalogue was empty**, and until now
    nothing connected the two: the credential took effect on the very next
    request, and the *model list* did not. So a provider keyed a moment ago
    reported no models, and a request to one of them came back `no_route` —
    which reads as "that model does not exist" rather than "I have not looked
    since you gave me the key". A five-minute timer eventually fixed it, which
    is the worst duration for a bug: long enough to be reported, short enough to
    have healed by the time anybody investigates.

    Awaited rather than backgrounded. Saving a credential is a deliberate act
    and its whole point is the catalogue that follows, so the wait belongs in
    the response that reports the result. A refresh that fails leaves the
    previous snapshot alone, so the worst case is the state that existed before.

    Matched by upstream name *and* by kind, the same two-step `_key_for` uses, so
    an upstream named `gemini` of kind `google` is refreshed by a credential
    stored under either.

    **Not repeated for a write that changed nothing.** When the key did not
    change and every matched catalogue was refreshed successfully within
    `REFRESH_COOLDOWN_SECONDS`, a second fetch could only return what the first
    did, so the total is read from the snapshot instead. Whether a refresh ran
    is returned beside the total so the audit can say which of the two a write
    did; the response carries the same total either way.
    """
    transparents = getattr(request.app.state, "transparents", {})
    targets = [
        built for key, built in transparents.items()
        if key == name or built.spec.kind.strip().lower() == name
    ]
    if not targets:
        return False, None
    due = changed or not _recently_refreshed(targets)
    if due:
        for built in targets:
            await built.registry.refresh()
    return due, sum(len(built.registry.model_ids()) for built in targets)


def _changed(before: Secret, after: Secret) -> bool:
    """Whether a write changed the credential this name resolves to.

    **The resolved values, compared — not the sources.** Moving a key from the
    file into the Keychain changes where it lives and not what it is, and
    writing the file with the value the environment already supplies changes
    nothing an upstream will be sent. Neither is a reason to re-read a
    catalogue. An absent credential resolves to an empty value, so a first
    write always counts as a change.

    Compared in memory with `hmac.compare_digest` and never logged, returned or
    audited. These two `reveal()` calls are the only place a value leaves
    `Secret` in this module, and they leave it for one comparison.
    """
    return not hmac.compare_digest(before.reveal().encode(), after.reveal().encode())


def _recently_refreshed(targets: list[Any]) -> bool:
    """Whether every matched catalogue was refreshed, successfully, inside the cooldown.

    **The registry's own `refreshed_at`, not a clock kept here.** It is already
    application state, so there is no second record of the same fact to drift
    from the first — and it counts every refresh, startup's and the periodic
    one's as well as an earlier write's, which is the question being asked: has
    anybody looked recently, not has this endpoint. Compared on
    `time.monotonic()`, the clock `ModelRegistry` stamps it with.

    **A failure is not a refresh.** `refresh()` leaves `refreshed_at` alone and
    records `last_error` when a fetch fails, so a snapshot carrying an error is
    due however recent its last success was — the rule every catalogue cache in
    RAVIS keeps: a failed read is not cached.
    """
    now = time.monotonic()
    for built in targets:
        snapshot = built.registry.snapshot
        if not snapshot.has_been_refreshed or snapshot.last_error:
            return False
        if now - snapshot.refreshed_at >= REFRESH_COOLDOWN_SECONDS:
            return False
    return True


@router.delete("/credentials/{name}")
async def forget_credential(name: str, request: Request) -> Any:
    """Remove one credential from RAVIS's own file.

    The response may still report the credential configured, from the
    environment or the Keychain. That is the truth rather than a failed delete:
    RAVIS does not remove things it did not put there.

    **Touches no catalogue**, unlike `set_credential`, so it has no effect a
    replay could repeat: removing a key twice leaves the same state and sends
    nothing anywhere. That is why the refresh cooldown has nothing to guard here.
    """
    refusal = _may_write_credentials(request)
    if refusal:
        return _refused(request, refusal)
    status = _store(request).forget(name)
    # `still_configured` is the fact worth auditing: a delete that leaves the
    # credential in place from the environment or the Keychain is not a failed
    # delete, and an audit trail recording only "forgotten" would describe a
    # state the deployment is not in.
    audit.record(request, audit.ACTION_CREDENTIAL_FORGOTTEN,
                 provider=name, still_configured=status.configured,
                 source=status.source)
    return status.as_dict()


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
        return _refused(request, refusal)
    state: ProviderState = request.app.state.provider_state
    enabled = state.set_enabled(name, body.enabled)
    audit.record(request, audit.ACTION_PROVIDER_ENABLED, provider=name, enabled=enabled)
    return {"name": name, "enabled": enabled}


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


# How many ids one catalogue page carries. Larger than `SAMPLE_LIMIT` because
# this endpoint answers a different question: `sample` shows an operator what a
# pattern *did*, and is capped so a truncated list can never be mistaken for the
# result — while this one exists to enumerate a catalogue exhaustively, and says
# so by carrying `total` and a cursor on every page.
CATALOGUE_PAGE = 250
CATALOGUE_MAX_PAGE = 1000


@router.get("/{name}/catalogue")
async def read_catalogue(name: str, request: Request) -> dict[str, Any]:
    """Every model this provider publishes, a page at a time, with what is selected.

    The filter screen offers a list to tick through, and a list to tick through
    has to be the whole list. `read_model_filter` deliberately caps what it
    returns; capping here would mean a model the operator cannot see and cannot
    select, which is a worse failure than a long response.

    `selected` is computed from the live filter rather than sent back by the
    page, so a filter changed in another tab shows up on the next open instead
    of being silently overwritten by a stale checkbox.
    """
    catalogue = await _catalogue(request, name)
    filters: ModelFilters = request.app.state.model_filters
    model_filter = filters.for_provider(name)
    selected = set(model_filter.apply(catalogue))
    unusable = _unavailability(request)

    offset = max(0, _int(request.query_params.get("offset"), 0))
    limit = min(CATALOGUE_MAX_PAGE, max(1, _int(request.query_params.get("limit"), CATALOGUE_PAGE)))
    page = catalogue[offset : offset + limit]
    return {
        "name": name,
        "items": [
            {"id": model, "selected": model in selected, "unavailable": unusable(model)}
            for model in page
        ],
        "total": len(catalogue),
        "offset": offset,
        "limit": limit,
        # Absent rather than equal to `total` when the listing is done, so a
        # client loops on "is there a next page" instead of on arithmetic.
        "next_offset": offset + limit if offset + limit < len(catalogue) else None,
        "filtered": not model_filter.is_empty,
        "filter": model_filter.as_dict(),
    }


def _unavailability(request: Request) -> Any:
    """A reader for "has this provider told us the model does not exist?".

    **Measured, and only measured.** `MODEL_UNAVAILABLE` is what RAVIS records
    when a provider answers 404 for a model it lists — which is what a
    deprecated id looks like from here, and equally what an id the account has
    no access to looks like. Both mean *you cannot use this*, which is the
    question a picker is asking.

    There is no other honest source. OpenAI publishes deprecations as an HTML
    page and marks nothing on `GET /v1/models`, so the alternative was a
    hardcoded list that covers one provider, goes stale unnoticed, and dresses
    "a web page said so" as knowledge. This covers every provider, needs no
    network, and is wrong only until the next attempt.

    Three states. `None` means never called — which is not a claim of health,
    for the same reason a provider nobody has probed shows a breaker of `null`
    rather than CLOSED.
    """
    health: HealthRegistry = request.app.state.health

    def of(model: str) -> bool | None:
        known = health.known(HealthScope.MODEL, model)
        # The *record* is the evidence, not the request count. A model that has
        # only ever failed has no completed request to its name — `failed` does
        # not increment one — so gating on `requests` reported "never called"
        # for exactly the models this exists to find.
        if known is None:
            return None
        return known.failures_by_class.get(FailureClass.MODEL_UNAVAILABLE.value, 0) > 0

    return of


def _int(raw: str | None, fallback: int) -> int:
    """A query parameter as a number, or the fallback. Never a 422.

    A malformed cursor should restart the listing, not refuse it: the operator
    did not type it, a client did, and the recoverable answer is the useful one.
    """
    try:
        return int(raw) if raw is not None else fallback
    except ValueError:
        return fallback


@router.put("/{name}/models")
async def set_model_filter(name: str, body: FilterInput, request: Request) -> Any:
    """Replace one provider's filter, and report what it now selects."""
    refusal = _may_write(request)
    if refusal:
        return _refused(request, refusal)
    filters: ModelFilters = request.app.state.model_filters
    filters.set_for(
        name, ModelFilter(include=tuple(body.include), exclude=tuple(body.exclude))
    )
    # Counts rather than the patterns. A filter is operator configuration and
    # not a secret, but an audit line is a summary — and the endpoint returns
    # the whole filter to the caller who asked for it anyway.
    audit.record(request, audit.ACTION_PROVIDER_MODELS, provider=name,
                 include=len(body.include), exclude=len(body.exclude))
    return await read_model_filter(name, request)
