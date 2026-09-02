"""Building the NERVIS application.

Assembly only. Every decision this file makes is about wiring — what exists,
in what order, sharing what state — and none of it is about behaviour, which
lives in the modules being wired.

**No CORS middleware, and that is a decision rather than an omission.** NERVIS
serves the dashboard and the dashboard's own API from one origin, so its
requests are same-origin and CORS never enters the picture. The dashboard's
cross-origin reads go to RAVIS and SIRVIS, which each carry the allowlist that
governs them. Adding a third copy here would be a header nobody's browser ever
sends a request past.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
from ecosystem_protocol import new_request_id, trace_id_from
from ecosystem_protocol import router as ecosystem_router
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from nervis import background, documents, notifications, supervision
from nervis.api import (
    background_router,
    chat_router,
    commands_router,
    diagnostics_router,
    documents_router,
    events_router,
    instances_router,
    learned_router,
    notifications_router,
    proposals_router,
    supervision_router,
    traces_router,
    voice_router,
)
from nervis.api import router as api_router
from nervis.api.chat_personas import seed_chat_defaults
from nervis.config import Settings
from nervis.ecosystem import (
    BUILD_VERSION,
    advertise_chat,
    advertise_supervision,
    advertise_voice,
    nervis_surface,
)
from nervis.enrollment import load_or_create
from nervis.errors import NervisError, to_response
from nervis.events import Hub
from nervis.instances import Instances
from nervis.probes import probe
from nervis.registry import Registry, RegistryState, admissible, declared_services
from nervis.storage import installation_identity, prepare_database
from nervis.voice import VoiceCredential
from nervis.voice import config_directory as voice_config_directory
from nervis.web import register_dashboard

NextCall = Callable[[Request], Awaitable[Any]]

logger = logging.getLogger("nervis")


def create_app(settings: Settings) -> FastAPI:
    """The application, fully wired and ready to serve.

    Takes settings rather than reading them, so a test constructs an app with
    the configuration it means instead of by arranging the environment first.
    """
    api = FastAPI(title="NERVIS", version=BUILD_VERSION, lifespan=_lifespan)
    _attach_shared_state(api, settings)
    _register_correlation(api)
    _register_error_handling(api)
    api.include_router(ecosystem_router)
    api.include_router(api_router)
    api.include_router(chat_router)
    api.include_router(events_router)
    api.include_router(diagnostics_router)
    api.include_router(supervision_router)
    api.include_router(traces_router)
    api.include_router(instances_router)
    api.include_router(background_router)
    api.include_router(documents_router)
    api.include_router(learned_router)
    api.include_router(notifications_router)
    api.include_router(proposals_router)
    api.include_router(commands_router)
    api.include_router(voice_router)
    register_dashboard(api)
    return api


def _attach_shared_state(api: FastAPI, settings: Settings) -> None:
    """Everything a handler reaches for through `request.app.state`."""
    api.state.settings = settings
    api.state.database = prepare_database(settings.database_path)

    # §18.2's voice credential. A file in the user's config directory rather
    # than a row in the database above: `nervis.db` is created in the working
    # directory with whatever mode the umask allows, which is fine for
    # configuration and wrong for a secret. The voice *profiles* are in the
    # database, because those are configuration and not secrets.
    api.state.voice_credential = VoiceCredential(
        voice_config_directory() / "voice-credential.json"
    )
    # Which models RAVIS says are remote, cached by the speak endpoint. Seeded
    # so the attribute always exists: `getattr` with a default would hide a
    # misspelling of the name for as long as the cache stayed cold.
    api.state.voice_locality = None

    # RAVIS's model counts, cached by the chat reading (`api/chat.py`). Seeded
    # for the same reason as the line above: an attribute that only exists once
    # something has written it turns a misspelling into a cold cache that never
    # warms.
    api.state.chat_catalogue = None

    # NERVIS's own voice and its starting presets, as editable settings rather
    # than hidden rules.
    seed_chat_defaults(api.state.database)

    # Read from the database rather than generated per process. §4.1 requires
    # `machine_id` to be stable per installation and `service_id` to be a stable
    # configured identity; a `uuid4()` here would give a peer a different answer
    # after every restart, which is precisely what makes correlation impossible.
    # `instance_id` is the one that changes per process, and the protocol
    # package generates that itself.
    service_id, machine_id = installation_identity(api.state.database)
    api.state.service_id = service_id
    api.state.machine_id = machine_id

    api.state.ecosystem = nervis_surface(
        service_id=service_id,
        machine_id=machine_id,
        database=api.state.database,
    )
    # §18.2: advertised only when configured. Read once here and kept current by
    # the credential endpoints, so the answer never needs a restart to be true.
    advertise_voice(api.state.ecosystem, api.state.voice_credential.configured())
    # §7's generated titles are RAVIS background calls, and RAVIS honours the
    # marker only from an authenticated identity — so what this advertises
    # depends on whether a credential was configured, not on what NERVIS built.
    advertise_chat(api.state.ecosystem, bool(settings.ravis_client_credential))
    # §5.1's registry, built from configuration alone. A declaration whose
    # endpoint fails the SSRF guard is dropped and recorded rather than raised:
    # one bad entry must not stop NERVIS starting, which is the same rule that
    # says an offline service never breaks the page.
    admitted, refused = admissible(declared_services(settings), settings.allowed_hosts)
    api.state.refused_endpoints = refused
    for key, reason in refused:
        logger.warning("registry entry %s refused: %s", key, reason)
    api.state.registry = Registry(admitted, stale_after_seconds=settings.stale_after_seconds)
    # **After the registry, because it counts what is in it.** §3.1 attaches
    # supervision to a condition rather than a milestone, so this is a reading
    # of the configuration: a machine whose services were all started by a
    # launcher owns none, and the capability says so rather than claiming a
    # control that would refuse.
    advertise_supervision(api.state.ecosystem, sum(
        1 for entry in api.state.registry.all()
        if supervision.adapter(api.state.database, entry.key).configured
    ))
    # One client for every probe. Connection reuse matters here: six services on
    # a twenty-second timer is a new TCP handshake every three seconds
    # otherwise, against processes on this same machine.
    api.state.probe_client = httpx.AsyncClient()
    api.state.hub = Hub(
        api.state.database,
        retention_days=settings.event_retention_days,
        retention_events=settings.event_retention_count,
    )
    # When probing began, for the startup window in `_next_interval`. Monotonic
    # so a clock adjustment cannot widen or close the window by surprise.
    api.state.probe_started_at = time.monotonic()
    # M8a. The secret is created on first run rather than configured — see
    # `enrollment.py` for why a file's permissions are the authentication here.
    api.state.enrollment_secret = load_or_create(settings.database_path)
    api.state.instances = Instances(allowed_hosts=frozenset(settings.allowed_hosts))
    # Injected rather than called, so a test can register an instance and then
    # move time past its lease without sleeping through it.
    api.state.instances_clock = time.time
    # The same injection for the chat clock, and for a sharper reason than
    # convenience. The conversation-gap phrase changes wording at the second —
    # "0 seconds ago", "1 second ago", "2 seconds ago" — so a test asserting the
    # phrasing was really asserting how long the test itself took to run, and
    # failed whenever a slow run crossed a boundary. A clock a test can hold
    # still is what lets the *phrasing* be checked instead of the machine.
    api.state.chat_clock = lambda: datetime.now().astimezone()


def _register_error_handling(api: FastAPI) -> None:
    """Turn a refusal into a response, in one place.

    One translation point so the wire shape of a refusal cannot drift per
    endpoint.
    """

    @api.exception_handler(NervisError)
    async def handle_nervis_error(request: Request, exc: NervisError) -> JSONResponse:
        return to_response(request, exc)


def _register_correlation(api: FastAPI) -> None:
    """Give every request an ID and echo it back (runbook §4.3).

    Correlation data and never authorization: a request or trace ID does not
    approve a gate or elevate a caller. NERVIS is the service that will
    eventually *display* these IDs across every other service, which makes it
    the one most likely to be tempted to trust one.
    """

    @api.middleware("http")
    async def correlate(request: Request, call_next: NextCall) -> Any:
        request.state.request_id = request.headers.get("x-request-id") or new_request_id()
        # The **trace id**, not the whole header. §11.2 joins events from
        # different services on this value, and `traceparent`'s third field is a
        # per-span parent id — so two spans in one trace carry two different
        # headers and matching on the string finds neither. Parsed in the shared
        # package, because all three services had the same line and all three
        # had it wrong.
        request.state.trace_id = trace_id_from(
            request.headers.get("traceparent", "")
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


@contextlib.asynccontextmanager
async def _lifespan(api: FastAPI) -> AsyncIterator[None]:
    """Keep the registry warm while the service is up.

    **The first pass is scheduled, not awaited, and that is a correction.**
    Awaiting it here runs it before uvicorn binds the socket, which makes the
    self-probe fail by construction — NERVIS cannot answer itself while it is
    still starting — and reports as `unreachable` any peer that happens to still
    be coming up, which on a one-command launcher is most of them. Both read as
    real outages on a dashboard opened seconds later.

    Until that pass lands, entries read `discovering`, which is exactly what
    §5.1 lists that state for: not asked yet, as distinct from asked and silent.
    """
    task = asyncio.create_task(_refresh_periodically(api))
    # **Something has to tell an open stream that the service is stopping.**
    # `/api/v1/events/stream` is an endless generator, and uvicorn's graceful
    # shutdown waits for open connections to close — so a single dashboard tab
    # with the feed open held NERVIS in "Waiting for connections to close"
    # indefinitely. The port was released and the process never exited, which
    # looks from outside like the service being down and refusing to die.
    #
    # Found by restarting NERVIS with a dashboard open, which is the ordinary
    # case rather than an unusual one — and it only became possible when Stage 7
    # gave the page a real stream to hold.
    api.state.stopping = asyncio.Event()
    try:
        yield
    finally:
        api.state.stopping.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await api.state.probe_client.aclose()


async def refresh_registry(api: FastAPI) -> None:
    """Probe every declared service once, concurrently.

    Concurrently because they are independent and a stopped service costs the
    full timeout — six of those in sequence is twelve seconds during which every
    other entry is also not being refreshed.
    """
    registry: Registry = api.state.registry
    entries = registry.all()
    observations = await asyncio.gather(
        *(
            probe(
                api.state.probe_client, entry.declaration, entry,
                # Named to RAVIS, anonymous to everyone else: the credential
                # belongs to one peer, and the probe loop is the steadiest
                # reader NERVIS has. A probe refused with 429 records the peer
                # as degraded, which is a rate limit displayed as an outage.
                credential=(
                    api.state.settings.ravis_client_credential
                    if entry.key == "ravis" else ""
                ),
            )
            for entry in entries
        ),
        return_exceptions=True,
    )
    before = {entry.key: entry.state for entry in entries}
    for entry, observation in zip(entries, observations, strict=True):
        if isinstance(observation, BaseException):
            # A probe should map every failure to a state rather than raise, so
            # reaching here is a bug in `probes.py`. Logged and recorded as
            # unreachable rather than allowed to kill the refresh: the whole
            # registry going dark because one probe had an unhandled path is
            # exactly the failure §5.1's gate forbids.
            logger.exception("probe for %s raised", entry.key, exc_info=observation)
            continue
        registry.record(entry.key, observation)
    _announce_transitions(api, before)


def _announce_transitions(api: FastAPI, before: dict[str, Any]) -> None:
    """Emit an event for each peer whose state actually changed.

    The hub's first real producer, and NERVIS's own: RAVIS and SIRVIS advertise
    `events@1` as unavailable until their Stage 7 milestones, so until then the
    only thing with events to publish is the service watching them.

    **Only on a change.** A probe every twenty seconds against six peers would
    otherwise write eighteen events a minute saying nothing happened, and
    retention would then be measuring how long NERVIS had been running rather
    than how much had occurred.
    """
    hub = getattr(api.state, "hub", None)
    if hub is None:
        return
    # **Nothing observed during shutdown is news.** The sweep can land after
    # uvicorn has begun closing the socket, and NERVIS then observes *itself*
    # as unreachable — which reached the hub as a warning and came back out in
    # a chat answer as "a ConnectError indicating no response from the nervis
    # service", reported by the very process that was answering the question.
    # A restart is not an outage, and this is the only place that can tell the
    # difference.
    stopping = getattr(api.state, "stopping", None)
    if stopping is not None and stopping.is_set():
        return
    for entry in api.state.registry.all():
        was = before.get(entry.key)
        if was is None or was == entry.state:
            continue
        hub.emit(
            "nervis.service.state_changed",
            # **Absent is not broken.** An optional peer that has never answered
            # is software nobody installed, and a warning about it is an alarm
            # for a machine that is working exactly as configured — it turned
            # "what has gone wrong lately" into four lines about Ollama on a
            # machine that has never had Ollama. The transition is still
            # recorded, because the hub is the record of what NERVIS observed;
            # it is recorded as the ordinary fact it is.
            severity=(
                "warning" if not entry.is_usable and not entry.awaiting_first_contact
                else "info"
            ),
            subject={"type": "service", "id": entry.key},
            data={
                "service": entry.key,
                "label": entry.declaration.label,
                "from": was.value,
                "to": entry.state.value,
                "detail": entry.detail,
            },
        )
        _note_state_change(api, entry, was)


# How a state reads in a sentence. The status bar's own words are for a glance
# and these are for a line somebody reads tomorrow — "stale" without the age
# beside it has told them nothing.
WRITTEN_STATE = {
    "healthy": "is back to healthy",
    "degraded": "is degraded",
    "unreachable": "has stopped answering",
    "stale": "has gone quiet",
    "unhealthy": "reports itself unhealthy",
    "incompatible": "is speaking a protocol NERVIS does not support",
    "unauthorized": "is refusing NERVIS's credential",
    "stopped": "has stopped",
    "discovering": "is still negotiating",
}


def _note_state_change(api: FastAPI, entry: Any, was: Any) -> None:
    """File the transition in the notification centre as well as the hub.

    **The announcement and the note are one event seen twice.** M21 is explicit
    that muting speech must not lose the record, and the honest way to satisfy
    that is for the record not to be written by the thing that speaks. The
    dashboard's announcer is a browser that may be closed, muted, or on another
    tab; this runs in the probe loop, which is running either way. A user who
    was out all afternoon comes back to the list whether or not anything was
    ever said aloud.

    **Absent is not news**, the same rule the hub's severity and the voice
    announcer both use. An optional peer nobody installed is software that is
    working exactly as configured, and a centre that opens with four notes
    about Ollama on a machine that has never had Ollama is one nobody reads.
    """
    database = getattr(api.state, "database", None)
    if database is None or entry.awaiting_first_contact:
        return
    # **Nothing is filed while the stack is still coming up.** A launcher starts
    # the services in sequence and the machine is busy doing it, so NERVIS's
    # early sweeps catch peers mid-startup and its own probe of itself can time
    # out under the load — every one of which resolves seconds later. Reported
    # as a centre full of "has stopped answering" after every cold start, each
    # note true for about twenty seconds and worthless by the time anybody read
    # it.
    #
    # `awaiting_first_contact` and the `discovering` guard below already cover a
    # peer NERVIS has never reached. What they do not cover is the second
    # transition: a service seen healthy once, then missed while the rest of the
    # stack is still loading, which reads as `healthy -> unreachable` and looks
    # exactly like a real outage.
    #
    # The window is the one `_next_interval` already uses to probe faster,
    # rather than a number of its own — the fast window exists precisely because
    # this period is untrustworthy, and something worth re-probing quickly is
    # not something worth telling somebody about yet. **The hub still records
    # every transition**, so nothing is lost: the hub is the record of what
    # NERVIS observed, and this is the shorter list of what is worth saying.
    settings: Settings = api.state.settings
    if time.monotonic() - api.state.probe_started_at <= settings.startup_window_seconds:
        return
    # **A first sighting is a roll call, not news.** `discovering` is the state
    # every entry starts in, so the first sweep after a restart moves all of
    # them out of it — and posting that would greet the user with one note per
    # service every time NERVIS came up, which is how a notification centre
    # becomes something people close without reading. The hub still records the
    # transition, because the hub is the record of what NERVIS observed; this
    # is the shorter list of what is worth telling somebody. It also means a
    # service that was down before the restart and is still down produces no
    # note, which is right: nothing changed.
    if was.value == RegistryState.DISCOVERING.value:
        return
    label = entry.declaration.label
    moved = WRITTEN_STATE.get(entry.state.value, f"is now {entry.state.value}")
    with contextlib.suppress(Exception):
        notifications.post(
            database,
            kind="service_state",
            title=f"{label} {moved}",
            # §4.1's discipline, carried through: the note says why it exists,
            # and the why is the transition rather than the destination. "RAVIS
            # is degraded" is a status; "it was healthy twenty seconds ago" is
            # the reason anybody wants to know about it now.
            reason=f"its state moved from {was.value} to {entry.state.value}",
            body=entry.detail,
            severity=(
                "warning" if not entry.is_usable else "info"
            ),
            source="registry",
        )


async def _refresh_periodically(api: FastAPI) -> None:
    """Re-probe on a timer, tolerating everything.

    A refresh that raised would kill the task and freeze every entry at
    whatever it last held — which is worse than a stale reading, because a
    frozen one still looks current. Staleness is computed on read for the same
    reason.
    """
    while True:
        try:
            await refresh_registry(api)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a probe loop must outlive any single failure
            logger.exception("registry refresh failed")
        # §11.1's retention, on the probe timer rather than a second one. It is
        # a bounded DELETE against an indexed column; giving it its own task
        # would be a scheduler for something that takes a millisecond.
        with contextlib.suppress(Exception):
            api.state.hub.enforce_retention()
        # Attachments, on the same timer and for the same reason. A conversation
        # deleted through the dashboard takes its files with it; this catches the
        # directories left behind by a browser that cleared its own history and
        # so can never ask for them again.
        with contextlib.suppress(Exception):
            _expire_attachments(api)
        # Dismissed notifications, on the same timer and for the same reason.
        # Only the dismissed ones: an undismissed note is still waiting for the
        # user however old it is, and expiring those would mean a fortnight away
        # quietly emptied the centre.
        with contextlib.suppress(Exception):
            notifications.prune_dismissed(api.state.database)
        # M25, on the same timer and guarded by its own interval. A second task
        # would be a scheduler for something that runs twice an hour at most,
        # and this one already wakes often enough to notice.
        with contextlib.suppress(Exception):
            await _think_if_due(api)
        await asyncio.sleep(_next_interval(api))


#: When the last unattended run happened, so the interval can be honoured
#: without a second timer. Held on the app rather than in the database: it is a
#: fact about this process, and a restart legitimately starts the clock again.
_LAST_THOUGHT = "background_last_run"


async def _think_if_due(api: FastAPI) -> None:
    """Run one unattended thought, if this installation wants one and it is due.

    **Every gate is checked here rather than inside `think`.** Whether it is
    switched on, whether the interval has elapsed, whether today's ceiling is
    used up and whether there is anything worth saying are four different
    questions, and a run that answered them all at the point of spending money
    would be a run nobody could reason about beforehand.
    """
    database = getattr(api.state, "database", None)
    if database is None:
        return
    config = background.settings(database)
    if background.may_run(database, config):
        return
    last = getattr(api.state, _LAST_THOUGHT, 0.0)
    if last and time.monotonic() - last < config.interval_minutes * 60:
        return

    unwell = _unwell(api)
    prompted = background.worth_thinking_about(
        config, unwell, _hours_since_digest(database), _events_since_digest(api)
    )
    if prompted is None:
        return
    setattr(api.state, _LAST_THOUGHT, time.monotonic())
    await background.think(database, prompted, config, ask=_ask_ravis(api))


def _unwell(api: FastAPI) -> list[dict[str, Any]]:
    """Services that are not usable, with how long they have been that way.

    The count is kept on the entry rather than derived from the event hub: the
    hub records transitions and this question is about a state *persisting*,
    which is the one thing a record of changes cannot answer directly.
    """
    seen: dict[str, int] = getattr(api.state, "unwell_for", {})
    out: list[dict[str, Any]] = []
    for entry in api.state.registry.all():
        if entry.is_usable or entry.awaiting_first_contact:
            seen.pop(entry.key, None)
            continue
        seen[entry.key] = seen.get(entry.key, 0) + 1
        out.append({
            "label": entry.declaration.label, "state": entry.state.value,
            "observations": seen[entry.key], "detail": entry.detail,
        })
    api.state.unwell_for = seen
    return out


def _hours_since_digest(database: Any) -> float:
    """How long since the last digest ran, in hours; a large number if never."""
    rows = [r for r in background.runs(database, 200) if r["trigger"] == "daily_digest"]
    if not rows:
        return 9_999.0
    last = str(rows[0]["ran_at"])
    with contextlib.suppress(ValueError):
        when = datetime.fromisoformat(last.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - when).total_seconds() / 3600
    return 9_999.0


def _events_since_digest(api: FastAPI) -> list[dict[str, Any]]:
    """What the hub has recorded lately, as the digest's raw material."""
    hub = getattr(api.state, "hub", None)
    if hub is None:
        return []
    with contextlib.suppress(Exception):
        return list(hub.query(limit=200))
    return []


def _ask_ravis(api: FastAPI) -> Any:
    """The RAVIS call, as the callable `think` takes.

    Injected rather than imported so the decision, the ledger and the note stay
    testable without a network — and so the one place that spends money is a
    named seam rather than a line in the middle of a loop.

    **No background marker.** §9.6.1's marker means "must be free", which
    resolves to a local model — and loading a local model is exactly how
    unattended work starts competing with the conversation somebody is having.
    That is the trade M25 names, and it is why the pool is configuration.
    """
    async def ask(pool: str, session: str, brief: str, facts: str) -> tuple[str, str, str]:
        settings = api.state.settings
        entry = api.state.registry.get("ravis")
        if not settings.ravis_client_credential or entry is None or not entry.is_usable:
            raise RuntimeError("RAVIS is not reachable with a credential")
        answer = await api.state.probe_client.post(
            entry.declaration.base_url + "/v1/chat/completions",
            json={
                "model": pool,
                "messages": [
                    {"role": "system", "content": brief},
                    {"role": "user", "content": facts},
                ],
                "max_tokens": 300,
                "user": session,
            },
            headers={
                "content-type": "application/json",
                "x-request-id": new_request_id(),
                "authorization": f"Bearer {settings.ravis_client_credential}",
            },
            timeout=90.0,
        )
        if answer.status_code >= 400:
            raise RuntimeError(f"RAVIS answered HTTP {answer.status_code}")
        body = answer.json()
        text = str(
            ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        )
        used = body.get("usage") or {}
        total = used.get("total_tokens")
        # Tokens rather than currency. NERVIS counts requests and does not know
        # prices — §14's rule about estimates and invoices — so a figure in money
        # here would be one NERVIS was asserting rather than measuring.
        cost = f"{total:,} tokens" if isinstance(total, int) else "not reported"
        return text, str(body.get("model") or pool), cost

    return ask


def _expire_attachments(api: FastAPI) -> None:
    """Drop attachment directories nothing has touched in a fortnight."""
    root = str(getattr(api.state.settings, "workspace_path", "") or "").strip()
    if not root:
        return
    gone = documents.prune_attachments(Path(root), now=time.time())
    if gone:
        logger.info("expired %d attachment(s) no conversation points at", gone)


def _next_interval(api: FastAPI) -> float:
    """Fast for a bounded window after start, ordinary thereafter.

    A launcher starts the services in sequence, so NERVIS's first pass routinely
    catches a peer mid-startup. At the ordinary interval the dashboard then
    reports that peer unreachable for twenty seconds after it is up — the same
    class of wrongness as a stale status bar: a true reading held too long to
    still be true.

    **The window is bounded by the clock and not by whether anything looks
    unwell**, and that distinction is the whole of this function. The first
    version used the condition, which is a loop with no exit: four MEP endpoints
    every three seconds against three services is eighty requests a minute,
    RAVIS's anonymous limit is sixty, and being rate limited reads as unwell —
    so the fast interval kept itself on and NERVIS manufactured its own outage.
    Observed, not theorised.
    """
    settings: Settings = api.state.settings
    elapsed = time.monotonic() - api.state.probe_started_at
    if elapsed > settings.startup_window_seconds:
        return settings.probe_interval_seconds
    unsettled = any(not entry.is_usable for entry in api.state.registry.all())
    return (
        settings.recovery_interval_seconds if unsettled else settings.probe_interval_seconds
    )
