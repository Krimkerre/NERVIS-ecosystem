"""Building the NERVIS application.

Assembly only. Every decision this file makes is about wiring — what exists,
in what order, sharing what state — and none of it is about behaviour, which
lives in the modules being wired.

**No CORS middleware — and that reasoning was wrong once, reverifying §15
found the hole it left.** NERVIS serves the dashboard and the dashboard's own
API from one origin, so its own requests are same-origin and reading a
response back needs nothing added. What this file said next did not follow
from that: *"a header nobody's browser ever sends a request past"* — but CORS
response headers govern whether a page's script may *read* a response, never
whether the browser sends the request or whether a server acts on it. A page
on another origin can `fetch()` a state-changing route here with a body shaped
like a "simple request" and no server-side check ever saw it coming, which was
verified live against every route in this file except the six the control
token already covers. `nervis.api.origin_guard` closes it — checked in
`_register_correlation`, beside the Host check that solves the adjacent and
different problem of a page whose own hostname has been re-pointed here.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
from ecosystem_protocol import carrying, new_request_id, new_traceparent, trace_id_from
from ecosystem_protocol import router as ecosystem_router
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nervis import background, documents, logs, notifications, supervision, telemetry, workspace
from nervis.alerts import Alerts, Reading
from nervis.api import (
    background_router,
    chat_router,
    code_proxy_router,
    code_router,
    commands_router,
    diagnostics_router,
    documents_router,
    events_router,
    files_router,
    handovers_router,
    inspector_router,
    instances_router,
    learned_router,
    logs_router,
    notifications_router,
    proposals_router,
    recall_router,
    settings_transfer_router,
    supervision_router,
    traces_router,
    voice_router,
)
from nervis.api import router as api_router
from nervis.api.chat_personas import seed_chat_defaults
from nervis.api.code import UPSTREAM_TIMEOUT as CODE_UPSTREAM_TIMEOUT
from nervis.api.events import event_frames
from nervis.api.files import prune_trash
from nervis.api.origin_guard import expected_origins, refuses_cross_origin_mutation
from nervis.code_extension import ensure as ensure_clarvis
from nervis.code_proxy import Sessions as CodeSessions
from nervis.config import Settings
from nervis.ecosystem import (
    BUILD_VERSION,
    advertise_chat,
    advertise_supervision,
    advertise_voice,
    nervis_surface,
)
from nervis.enrollment import load_or_create
from nervis.errors import (
    CrossOriginMutationRefusedError,
    HostRejectedError,
    NervisError,
    to_response,
)
from nervis.events import GAP_EVENT, Hub
from nervis.flood import limits_from
from nervis.instances import Instances
from nervis.probes import probe
from nervis.registry import (
    Registry,
    RegistryEntry,
    RegistryState,
    admissible,
    declared_services,
)
from nervis.storage import installation_identity, prepare_database
from nervis.voice import VoiceCredential
from nervis.voice import config_directory as voice_config_directory
from nervis.web import register_dashboard

NextCall = Callable[[Request], Awaitable[Any]]

logger = logging.getLogger("nervis")

# How often the flood guard is settled while nothing arrives (`_settle_events_periodically`).
EVENT_SETTLE_SECONDS = 1.0


def create_app(settings: Settings) -> FastAPI:
    """The application, fully wired and ready to serve.

    Takes settings rather than reading them, so a test constructs an app with
    the configuration it means instead of by arranging the environment first.
    """
    api = FastAPI(title="NERVIS", version=BUILD_VERSION, lifespan=_lifespan)
    _attach_shared_state(api, settings)
    _register_correlation(api)
    _register_error_handling(api)
    # **The canonical stream is this service's stream (§16 item 9).** The shared
    # package's `/ecosystem/events` heartbeated on every service while §4.1
    # described it as *the* event stream — `Last-Event-ID` replay, cursor expiry,
    # bounded buffers, gap frames. NERVIS already implements all of it, so the
    # canonical route is given that generator rather than a second copy of
    # semantics it would be expensive to get subtly different.
    #
    # Set before the router is included so no request can arrive between the two.
    api.state.ecosystem_events = event_frames
    api.include_router(ecosystem_router)
    api.include_router(api_router)
    api.include_router(chat_router)
    api.include_router(events_router)
    api.include_router(files_router)
    api.include_router(diagnostics_router)
    api.include_router(supervision_router)
    api.include_router(traces_router)
    api.include_router(instances_router)
    api.include_router(handovers_router)
    api.include_router(inspector_router)
    api.include_router(logs_router)
    api.include_router(background_router)
    api.include_router(documents_router)
    api.include_router(learned_router)
    api.include_router(notifications_router)
    api.include_router(proposals_router)
    api.include_router(recall_router)
    api.include_router(settings_transfer_router)
    api.include_router(commands_router)
    api.include_router(voice_router)
    api.include_router(code_router)
    # Mounted last and at its own prefix: `/code/{path:path}` matches anything
    # under it, so a router registered after it would be shadowed. The one
    # place in this file where include order is a correctness question rather
    # than a tidiness one, said here so nobody has to rediscover it.
    api.include_router(code_proxy_router)
    register_dashboard(api)
    return api


def _attach_shared_state(api: FastAPI, settings: Settings) -> None:
    """Everything a handler reaches for through `request.app.state`."""
    # **Minted per process, persisted nowhere.** It authorises the six RAVIS
    # configuration proxies below and nothing else — see `api/control.py` for
    # what it defends and why a bearer credential would not. A restart is a new
    # value, which is the right behaviour for something a page holds: a browser
    # tab left open overnight loses the ability to change configuration until it
    # reloads, and reloading is how it gets the current token.
    api.state.control_token = secrets.token_urlsafe(32)
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
    # **The proxy gets its own client, not the probe's.** They carry different
    # things: a probe presents NERVIS's identity to a peer, and a proxied
    # request must present the browser's headers and none of NERVIS's. Sharing
    # one would make that separation a matter of remembering to strip.
    # Redirects are not followed — §13.3 asks for redirect *rewriting*, which
    # means the browser must see the redirect rather than have the proxy
    # resolve it somewhere the browser never learns about.
    api.state.code_client = httpx.AsyncClient(
        follow_redirects=False, timeout=CODE_UPSTREAM_TIMEOUT
    )
    api.state.code_sessions = CodeSessions()
    api.state.hub = Hub(
        api.state.database,
        retention_days=settings.event_retention_days,
        retention_events=settings.event_retention_count,
        # Always on in the running service. See `flood.py` for the night that
        # made it necessary and `config.py` for why each number is what it is.
        guard=limits_from(settings),
    )
    # When probing began, for the startup window in `_next_interval`. Monotonic
    # so a clock adjustment cannot widen or close the window by surprise.
    api.state.probe_started_at = time.monotonic()
    # When the last sweep began, for `_reopen_window_after_a_gap`. Wall clock
    # rather than monotonic, and that is the whole point — see that function.
    api.state.last_sweep_at = time.time()
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

    @api.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """The refusals NERVIS did not raise itself (§16 item 10).

        **One translation point was one too few.** `NervisError` was covered;
        everything FastAPI raises on its own was not, so a 404 for a path that
        does not exist and the events stream's own `409 EVENT_CURSOR_EXPIRED`
        both arrived as `{"detail": …}` — Starlette's shape, not §4.5's. A
        client parsing the envelope would have found no `error` object at all on
        exactly the responses it most needs to read.

        A `detail` that is already a mapping carries its own `code` and extras —
        the cursor refusal names the retention floor — so it is unpacked rather
        than stringified into the message.
        """
        detail = exc.detail
        structured: dict[str, Any] = detail if isinstance(detail, dict) else {}
        message = str(structured.get("message") or detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": str(structured.get("code") or f"HTTP_{exc.status_code}"),
                    "message": message,
                    "retryable": exc.status_code in (429, 503),
                    "details": {
                        key: value for key, value in structured.items()
                        if key not in ("code", "message")
                    },
                    "request_id": getattr(request.state, "request_id", ""),
                    "trace_id": getattr(request.state, "trace_id", ""),
                }
            },
            headers=getattr(exc, "headers", None),
        )


def _register_correlation(api: FastAPI) -> None:
    """Give every request an ID and echo it back (runbook §4.3).

    Correlation data and never authorization: a request or trace ID does not
    approve a gate or elevate a caller. NERVIS is the service that will
    eventually *display* these IDs across every other service, which makes it
    the one most likely to be tempted to trust one.
    """

    # Computed once, not per request: `served_hosts` and the port are fixed for
    # the life of this process, and the values a legitimate `Origin` can carry
    # do not change between one request and the next.
    same_origin = expected_origins(api.state.settings.served_hosts, api.state.settings.port)

    @api.middleware("http")
    async def correlate(request: Request, call_next: NextCall) -> Any:
        # **The Host check first, before anything reads the request (§16 item 5).**
        # A page on `attacker.example` whose DNS is re-pointed at `127.0.0.1`
        # reaches NERVIS as a same-origin request, and browsers omit `Origin` on
        # same-origin GETs — so an origin check has nothing to reject. The name
        # the browser resolved is still in `Host`, and NERVIS binds loopback and
        # nothing else can start. Reads matter as much as writes here: this is
        # the service that proxies to the other two and holds RAVIS's admin
        # credential.
        host = request.headers.get("host", "").strip()
        name = host.rsplit(":", 1)[0] if host.count(":") == 1 or host.startswith("[") else host
        permitted = {one.strip("[]").lower() for one in api.state.settings.served_hosts}
        if not host or name.strip("[]").lower() not in permitted:
            # `request.state.request_id` is not set yet — the check runs before
            # correlation on purpose, so a rebound request is refused before
            # anything reads it. `to_response` tolerates the absence.
            return to_response(
                request, HostRejectedError("Host is not allow-listed", host=host)
            )
        # **The check the Host check cannot make (`nervis.api.origin_guard`).**
        # A page genuinely served from another origin needs no rebinding at
        # all — it reaches this address directly, and its request carries that
        # origin honestly. Refused here, before anything reads the request, for
        # the same reason the Host check is: a request this service will not
        # act on should not get as far as acting on it.
        if refuses_cross_origin_mutation(request.method, request.headers, same_origin):
            return to_response(
                request,
                CrossOriginMutationRefusedError(
                    "this request did not come from this page",
                    method=request.method,
                ),
            )
        request.state.request_id = request.headers.get("x-request-id") or new_request_id()
        # The **trace id**, not the whole header. §11.2 joins events from
        # different services on this value, and `traceparent`'s third field is a
        # per-span parent id — so two spans in one trace carry two different
        # headers and matching on the string finds neither. Parsed in the shared
        # package, because all three services had the same line and all three
        # had it wrong.
        # **And minted when there is not one, because NERVIS is the caller.**
        # §11.2 wants trace context across *NERVIS → RAVIS/SIRVIS*, and only
        # accepting one covers the half where somebody else started the
        # operation. A browser sends no `traceparent`, so every request the
        # dashboard makes arrived untraced — which left `_note_turn` silent, and
        # RAVIS recording `trace_id: ""` on the decision. The result was that
        # every trace on this machine had exactly one lane and a warning saying
        # the caller had not published anything: the caller was NERVIS, and it
        # had nothing to publish under.
        #
        # A root span is the honest reading, not an invention: the request
        # genuinely originates here. It costs one id per request and is recorded
        # only where something emits a span under it.
        request.state.trace_id = trace_id_from(
            request.headers.get("traceparent", "")
        ) or trace_id_from(new_traceparent())
        # The same two ids the response will carry, put where anything logging
        # underneath this request can reach them without being handed them.
        with carrying(request_id=request.state.request_id,
                      trace_id=request.state.trace_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


def _install_clarvis(api: FastAPI) -> None:
    """One automatic install or update at startup, reported once.

    Logged rather than returned because nothing is waiting for it: the Code tab
    reads the extension state live, so the outcome reaches a person through the
    screen and this line is for whoever is reading the log.
    """
    settings = api.state.settings
    package = str(getattr(settings, "clarvis_vsix_path", "") or "")
    if not package:
        logger.info("clarvis_auto_install is on and no clarvis_vsix_path is set")
        return
    outcome = ensure_clarvis(str(getattr(settings, "code_server_binary", "") or ""), package)
    if outcome:
        logger.info("%s", outcome)


def _post_quietly(api: FastAPI, note: dict[str, Any]) -> None:
    """File one of `alerts.py`'s notes; a note that could not be filed never stops a feed."""
    database = getattr(api.state, "database", None)
    if database is None:
        return
    try:
        notifications.post(database, **note)
    except Exception:  # noqa: BLE001 - a failed note is logged, never fatal
        logger.exception("filing a %s notification failed", note.get("kind"))


async def _feed_alerts(api: FastAPI) -> None:
    """Every stored event, to `Alerts`, for as long as NERVIS runs.

    A subscriber that fell behind is cut loose with a gap marker; this one then
    subscribes again rather than waiting on a queue nothing writes to any more.
    """
    hub: Hub = api.state.hub
    queue = hub.subscribe()
    try:
        while True:
            event = await queue.get()
            if event.get("event_type") == GAP_EVENT:
                hub.unsubscribe(queue)
                queue = hub.subscribe()
                continue
            try:
                api.state.alerts.on_event(event, time.monotonic())
            except Exception:  # noqa: BLE001 - one odd event must not end the feed
                logger.exception("an alert trigger failed on %s", event.get("event_type"))
    finally:
        hub.unsubscribe(queue)


async def _read_for_alerts(api: FastAPI) -> None:
    """One probe tick's readings for `Alerts`: RAVIS's budget and memory, this Mac's swap.

    RAVIS is read only while the registry holds it usable, and each read that fails
    is simply absent this tick — a notice is never built from a guess.
    """
    budget, memory = await _ravis_readings(api)
    pressure = memory.get("under_pressure")
    _, swap_used = telemetry._swap()
    labels = {one.instance_id: one.label for one in api.state.instances.live("clarvis")}
    api.state.alerts.on_reading(Reading(
        now=time.monotonic(),
        budget=budget,
        memory_under_pressure=pressure if isinstance(pressure, bool) else None,
        memory_detail=str(memory.get("detail") or ""),
        swap_used=swap_used,
        window_labels=labels,
    ))


async def _ravis_readings(api: FastAPI) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """RAVIS's budget (or None) and its memory reading (or nothing), from its own reads."""
    entry = api.state.registry.get("ravis")
    if entry is None or not entry.is_usable:
        return None, {}
    base = entry.declaration.base_url
    usage = await _json_or_empty(api, base + "/api/v1/usage")
    health = await _json_or_empty(api, base + "/api/v1/health")
    budget = usage.get("budget")
    load = health.get("load")
    memory = load.get("memory") if isinstance(load, dict) else None
    return (budget if isinstance(budget, dict) else None,
            memory if isinstance(memory, dict) else {})


async def _json_or_empty(api: FastAPI, url: str) -> dict[str, Any]:
    try:
        found = (await api.state.probe_client.get(url, timeout=5)).json()
    except (httpx.HTTPError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


async def _settle_events_periodically(api: FastAPI) -> None:
    """Let the flood guard store final states and release, on a one-second tick.

    **Its own task, unlike retention on the probe timer**, because what it does
    is time-sensitive in a way retention is not: when a flooding service stops,
    the newest event of each kind it held back is the state the dashboard and the
    alarms should show, and the probe timer would leave the old one showing for
    up to twenty seconds. Arrivals settle the guard too; this covers the silence
    after a burst, which is exactly when there are none.
    """
    while True:
        await asyncio.sleep(EVENT_SETTLE_SECONDS)
        try:
            api.state.hub.settle()
        except Exception:  # noqa: BLE001 - a settle that raised must not end the tick
            logger.exception("settling the event flood guard failed")


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
    # **§13.5's automatic install, off unless asked for, and never fatal.** Run
    # in a thread because it shells out to code-server's CLI, which takes
    # seconds — blocking the event loop here would delay every route on a
    # service that has not finished starting. A failure is logged and dropped:
    # a missing binary or an unreadable package is a reason for the Code tab to
    # say something, not a reason for NERVIS not to start.
    if getattr(api.state.settings, "clarvis_auto_install", False):
        asyncio.create_task(asyncio.to_thread(_install_clarvis, api))
    settling = asyncio.create_task(_settle_events_periodically(api))
    # §18's other notices (`alerts.py`): fed by the hub as events are stored, and by
    # the probe timer's readings below.
    api.state.alerts = Alerts(lambda **note: _post_quietly(api, note))
    alerting = asyncio.create_task(_feed_alerts(api))
    try:
        yield
    finally:
        api.state.stopping.set()
        task.cancel()
        settling.cancel()
        alerting.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(asyncio.CancelledError):
            await settling
        # **What the flood guard still holds is stored before NERVIS goes.** A
        # held event is the newest state of its kind from a service that was
        # flooding; exiting with it in memory would drop exactly the event the
        # guard promises to keep.
        with contextlib.suppress(Exception):
            api.state.hub.settle(final=True)
        # The unattended thought, if one is in flight. It is no longer awaited
        # by the loop above, so cancelling that task no longer reaches it — and
        # an outstanding model call would hold the shutdown open exactly the way
        # the event stream used to.
        thinking: asyncio.Task[None] | None = getattr(api.state, _THINKING, None)
        if thinking is not None:
            thinking.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await thinking
        await api.state.probe_client.aclose()
        await api.state.code_client.aclose()


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
        registry.record(entry.key, _still_starting(api, entry, observation))
    _announce_transitions(api, before)


def _still_starting(
    api: FastAPI, entry: RegistryEntry, observation: dict[str, Any]
) -> dict[str, Any]:
    """A peer that has never answered *yet*, while the stack is coming up.

    **No reading yet, rather than a bad one.** The launcher starts five services
    at once and NERVIS is one of them, so its first sweep regularly lands in the
    two or three seconds before RAVIS has bound its port. Recorded plainly that
    reads `unreachable — no response: ConnectError`, which is what a service
    somebody killed also reads, and the dashboard turns red for a service that
    is a moment from answering. Measured on this machine: RAVIS was down for
    seven seconds across a restart, and NERVIS came back inside that gap.

    Claiming the peer is *starting* would be an invention — NERVIS cannot see
    that — and holding the last state would report a genuinely dead RAVIS as
    fine, which is the one thing these surfaces must never do. So the entry
    stays in the state it was already in before anybody asked: `discovering`,
    with nothing to say, which is what "NERVIS has not established what this is
    yet" means. The screen shows no reading rather than a wrong one, and the
    voice holds its status line until every entry has one.

    Narrow on purpose, and every clause earns its place:

    - **Only `unreachable`.** A 401, a 500 or a bad protocol version means the
      peer *answered*; nothing about that is a startup race.
    - **Only a peer never seen in this process.** `last_seen` is set by a
      successful probe, so a service that answered and then stopped is a real
      outage and reads as one, whatever the clock says.
    - **Only inside the startup window**, the same thirty seconds
      `_next_interval` probes faster over and `_worth_saying` stays quiet for.
      A peer that is still silent after that is unreachable and says so.
    """
    settings: Settings = api.state.settings
    if observation.get("state") is not RegistryState.UNREACHABLE:
        return observation
    if entry.last_seen:
        return observation
    if time.monotonic() - api.state.probe_started_at > settings.startup_window_seconds:
        return observation
    return {"state": RegistryState.DISCOVERING, "detail": ""}


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
    worth: list[_Noteworthy] = []
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
        noteworthy = _note_state_change(api, entry, was)
        if noteworthy is not None:
            worth.append(noteworthy)
    _file_notes(api, worth)


def _enforce_log_bounds(api: FastAPI) -> None:
    """Rotate and prune the launcher's logs, where there are any.

    Silent when no run directory is configured, which is the ordinary state for
    a NERVIS somebody started by hand: there is no documented log to bound, and
    inventing a path to tidy would be worse than leaving it alone.
    """
    configured = str(getattr(api.state.settings, "run_directory", "") or "")
    if configured:
        logs.enforce(Path(configured))


# How a state reads in a sentence. The status bar's own words are for a glance
# and these are for a line somebody reads tomorrow — "stale" without the age
# beside it has told them nothing.
# Two forms per state, because English does not derive the second from the first:
# "has stopped answering" becomes "have stopped answering", and a rule that
# appended a letter would write "has stopped answerings". Written out so a
# reader can see the sentence each state produces.
WRITTEN_STATE = {
    "healthy": ("is back to healthy", "are back to healthy"),
    "degraded": ("is degraded", "are degraded"),
    "unreachable": ("has stopped answering", "have stopped answering"),
    "stale": ("has gone quiet", "have gone quiet"),
    "unhealthy": ("reports itself unhealthy", "report themselves unhealthy"),
    "incompatible": ("is speaking a protocol NERVIS does not support",
                     "are speaking a protocol NERVIS does not support"),
    "unauthorized": ("is refusing NERVIS's credential", "are refusing NERVIS's credential"),
    "stopped": ("has stopped", "have stopped"),
    "discovering": ("is still negotiating", "are still negotiating"),
    # **Not a state, a transition.** A peer leaving `discovering` has answered
    # for the first time; it has not *recovered*, because it was never healthy
    # to begin with. Reported from the room after starting LM Studio and being
    # told it "is back to healthy, sir" — a sentence describing a history that
    # did not happen. Keyed separately from `healthy` so both sentences survive:
    # a peer that really did come back still says so.
    "connected": ("has connected", "have connected"),
    "connected_degraded": ("has connected, degraded", "have connected, degraded"),
}


def _reads_as(state: str, was: str) -> str:
    """Which sentence this transition earns, read from both of its ends."""
    if was == "discovering" and state in {"healthy", "degraded"}:
        return "connected" if state == "healthy" else "connected_degraded"
    return state


def _listed(labels: list[str]) -> str:
    """`A`, `A and B`, `A, B and C` — the way a person would say it."""
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _phrase_for(labels: list[str], state: str) -> str:
    """One sentence for however many services reached the same state.

    **Three notes for one event is how a centre teaches people to close it.** A
    cold start brings the stack up in sequence, so RAVIS, SIRVIS and code-server
    recover within one sweep of each other and filed one note each — three lines
    saying the stack came up. This centre already carries that scar: it once
    opened with four notes about an Ollama nobody had installed.
    """
    singular, plural = WRITTEN_STATE.get(state, (f"is now {state}", f"are now {state}"))
    return f"{_listed(labels)} {singular if len(labels) == 1 else plural}"


def _grouped(changes: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Labels by the state they reached, in the order they were observed.

    **Grouped by destination, which is what makes them one event.** A RAVIS that
    came back and a SIRVIS that stopped answering in the same sweep are two
    different things, and a single line saying both would be the flood's
    opposite failure: a sentence nobody can act on.
    """
    grouped: dict[str, list[str]] = {}
    for label, state in changes:
        grouped.setdefault(state, []).append(label)
    return grouped


def _phrase_keys(worth: list["_Noteworthy"]) -> list[tuple[str, str]]:
    """Each transition as (label, the sentence it earns) rather than (label, state).

    Grouping stays by sentence, so "RAVIS and SIRVIS have connected" and "LM
    Studio is back to healthy" are two notes in one sweep — which they are, and
    a single line claiming all three recovered would be false about two of them.
    """
    return [(one.label, _reads_as(one.state, one.was)) for one in worth]


@dataclass(frozen=True)
class _Noteworthy:
    """One transition that survived the filters and is worth telling somebody."""

    label: str
    state: str
    was: str
    detail: str
    usable: bool


def _note_state_change(api: FastAPI, entry: Any, was: Any) -> _Noteworthy | None:
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
        return None
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
        return None
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
        return None
    return _Noteworthy(
        label=entry.declaration.label,
        state=entry.state.value,
        was=was.value,
        detail=entry.detail,
        usable=bool(entry.is_usable),
    )


def _file_notes(api: FastAPI, worth: list[_Noteworthy]) -> None:
    """One note per destination state, for everything this sweep saw move.

    **The sweep is the unit, because the sweep is the observation.** Filing per
    transition produced three lines for one event on every cold start — the
    launcher brings the stack up in sequence, so RAVIS, SIRVIS and code-server
    all recover inside one pass. Three notes saying "the stack came up" is how a
    centre teaches somebody to close it unread, which is the same failure as the
    flood the startup window already exists to stop, arriving a different way.
    """
    database = getattr(api.state, "database", None)
    if database is None or not worth:
        return
    by_state = _grouped(_phrase_keys(worth))
    for state, labels in by_state.items():
        # Matched on the sentence, because that is what the group is keyed by
        # now. Matching on `one.state` would find nothing for a "connected"
        # group and file a note reading "state moved from  to connected".
        moved = [one for one in worth if _reads_as(one.state, one.was) == state]
        froms = sorted({one.was for one in moved})
        # The reason names the states, never the sentence key: "moved from
        # discovering to connected" would be describing this function's own
        # vocabulary rather than the registry's.
        reached = " and ".join(sorted({one.state for one in moved})) or state
        with contextlib.suppress(Exception):
            notifications.post(
                database,
                kind="service_state",
                title=_phrase_for(labels, state),
                # §4.1's discipline, carried through: the note says why it
                # exists, and the why is the transition rather than the
                # destination. "RAVIS is degraded" is a status; "it was healthy
                # twenty seconds ago" is the reason anybody wants to know now.
                # Several services rarely move *from* the same state, so the
                # sentence names each origin it saw rather than picking one.
                reason=(f"state moved from {' and '.join(froms)} to {reached}"),
                # The details differ per service, so they are labelled. One
                # unlabelled blob would leave a reader guessing which sentence
                # belonged to which name.
                body="; ".join(f"{one.label}: {one.detail}" for one in moved if one.detail),
                # Warning when *any* of them is unusable: a line that said "info"
                # because two of three recovered would bury the one that did not.
                severity="warning" if any(not one.usable for one in moved) else "info",
                source="registry",
            )


def _reopen_window_after_a_gap(api: FastAPI) -> None:
    """Treat a resumed probe loop exactly like a freshly started one.

    **A laptop that sleeps produces a notification centre full of recoveries
    that never happened.** Probing stops with the machine, so every entry ages
    into `stale` while nothing is asking it anything, and the first sweep after
    the wake finds all of them alive at once — filed as one "is back to healthy"
    per service, per wake. Observed on a machine sleeping every fifteen minutes:
    four notes each time, the wake in `pmset -g log` a minute before each batch.

    The startup window in `_note_state_change` cannot catch this. It is keyed to
    when *this process* began probing, and nothing restarted — `probe_started_at`
    was hours old and the window long closed. But the two situations are the
    same one: a period during which nobody was watching, followed by readings
    that only look like transitions. So this reopens that window rather than
    inventing a second kind of quiet, which also restores the fast re-probe in
    `_next_interval` — after a wake, probing quickly is wanted anyway.

    **Wall clock, deliberately, where the rest of this file uses monotonic.**
    `time.monotonic()` does not advance while the system is asleep, so the gap
    this exists to detect is precisely the gap monotonic cannot see. The cost is
    that an NTP correction can trip it; that spends thirty seconds of silence on
    a clock jump, which is the harmless direction to be wrong in.
    """
    settings: Settings = api.state.settings
    now = time.time()
    slept_through = now - getattr(api.state, "last_sweep_at", now)
    # The yardstick is the interval we asked for plus the window itself: longer
    # than that and the loop did not run when it said it would.
    if slept_through > settings.probe_interval_seconds + settings.startup_window_seconds:
        api.state.probe_started_at = time.monotonic()
    api.state.last_sweep_at = now


async def _refresh_periodically(api: FastAPI) -> None:
    """Re-probe on a timer, tolerating everything.

    A refresh that raised would kill the task and freeze every entry at
    whatever it last held — which is worse than a stale reading, because a
    frozen one still looks current. Staleness is computed on read for the same
    reason.
    """
    while True:
        _reopen_window_after_a_gap(api)
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
        # M25, on the same timer and guarded by its own interval — a second
        # *scheduler* would be machinery for something that runs twice an hour
        # at most, and this loop already wakes often enough to notice.
        #
        # **Started rather than awaited, which is the part that was wrong.**
        # Everything else on this timer is a bounded DELETE or a handful of
        # `stat` calls; this one asks a model a question, and awaiting it here
        # stops the health probes for as long as the answer takes. Found by the
        # external audit; live on the machine it was written for, where
        # background thinking runs every fifteen minutes. A service falling over
        # during a thought went unnoticed until the thought finished — which is
        # the one moment the reading matters most.
        with contextlib.suppress(Exception):
            _start_thought_if_due(api)
        # §11.3's rotation bounds, applied rather than merely published. "No
        # ecosystem service may quietly fill the disk with diagnostics" is not a
        # setting somebody reads, and on the machine this was written for the
        # four logs had reached 102 MB without anything ever checking. Costs
        # four `stat` calls on a sweep that already does more than that; the
        # copy only happens on the sweep that finds a log over the limit.
        with contextlib.suppress(Exception):
            _enforce_log_bounds(api)
        # §18's readings-based notices: budget, memory, swap, a Clarvis gate left waiting.
        try:
            await _read_for_alerts(api)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a notice that could not be read must not stop probing
            logger.exception("reading for notifications failed")
        await asyncio.sleep(_next_interval(api))


#: The unattended run in flight, if there is one. See `_start_thought_if_due`:
#: the interval stopped being an overlap guard the moment the thought stopped
#: being awaited, and this is what replaced it.
_THINKING = "background_task"

#: When the last unattended run happened, so the interval can be honoured
#: without a second timer. Held on the app rather than in the database: it is a
#: fact about this process, and a restart legitimately starts the clock again.
_LAST_THOUGHT = "background_last_run"


def _start_thought_if_due(api: FastAPI) -> None:
    """Run one unattended thought beside the probe loop rather than inside it.

    **Its own overlap guard, because the interval is no longer one.** While the
    thought was awaited, the loop could not come round again until it finished,
    so "has the interval elapsed" was the whole of the protection. Started as a
    task, the loop keeps ticking, and a thought that outlives its own interval
    would otherwise be joined by a second. The handle is what prevents that.

    Kept on the app rather than in a set: there is at most one of these, and a
    task nobody can name is a task nobody can cancel at shutdown.
    """
    running: asyncio.Task[None] | None = getattr(api.state, _THINKING, None)
    if running is not None and not running.done():
        return
    setattr(api.state, _THINKING, asyncio.create_task(_think_if_due(api)))


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


#: The reply budget for one unattended note. Enough for a free reasoning model to
#: think and still write a heading and two or three sentences (see `_asked_once`).
DIGEST_MAX_TOKENS = 1000


def _ask_ravis(api: FastAPI) -> Any:
    """The RAVIS call, as the callable `think` takes.

    Injected rather than imported so the decision, the ledger and the note stay
    testable without a network — and so the one place that spends money is a
    named seam rather than a line in the middle of a loop.

    **The chosen pool, then this machine's own** — `background.route`'s order,
    with `think` supplying the pool. `ravis/free-api` by default costs nothing
    and cannot take the memory chat needs; a free tier that is rate-limited or
    down should cost a slower note from a local model, not no note. A pool that
    answered with nothing is treated like one that failed.

    **No background marker.** §9.6.1's marker means "must be free", which admits
    a local model whatever the operator chose — the pool is the decision here,
    made under Settings → Unattended work.
    """
    async def ask(pool: str, session: str, brief: str, facts: str) -> tuple[str, str, str]:
        failures: list[str] = []
        for model in dict.fromkeys(m for m in (pool, background.LOCAL_POOL) if m):
            try:
                text, served, cost = await _asked_once(api, model, session, brief, facts)
            except Exception as failure:  # noqa: BLE001 - any failure moves to the fallback
                failures.append(f"{model}: {failure}")
                continue
            if text.strip():
                return text, served, cost
            failures.append(f"{model} returned nothing")
        raise RuntimeError("; ".join(failures))

    return ask


async def _asked_once(
    api: FastAPI, model: str, session: str, brief: str, facts: str
) -> tuple[str, str, str]:
    """One model's reply to the brief, or an exception saying why there is none."""
    settings = api.state.settings
    entry = api.state.registry.get("ravis")
    if not settings.ravis_client_credential or entry is None or not entry.is_usable:
        raise RuntimeError("RAVIS is not reachable with a credential")
    answer = await api.state.probe_client.post(
        entry.declaration.base_url + "/v1/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": brief},
                {"role": "user", "content": facts},
            ],
            # **1000, not 300** (13 September 2026). `ravis/free-api` served a
            # reasoning model that spent all 300 on thinking and returned no text,
            # so every morning's digest fell back to `ravis/local` and loaded a
            # model nobody asked for (12 and 13 September). Titles hit the same
            # wall at 24 and were raised to 400; a note is longer than a title.
            "max_tokens": DIGEST_MAX_TOKENS,
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
    return text, str(body.get("model") or model), cost

def _expire_attachments(api: FastAPI) -> None:
    """Drop attachment directories nothing has touched in a fortnight."""
    # Attachments expire from the room they arrived in.
    place = workspace.imported(api.state.settings)
    root = str(place) if place is not None else ""
    if not root:
        return
    gone = documents.prune_attachments(Path(root), now=time.time())
    if gone:
        logger.info("expired %d attachment(s) no conversation points at", gone)
    # The file manager's trash keeps the same fortnight, swept by the same job:
    # one timer for one promise, rather than a second one to forget about.
    workspace_root = str(getattr(api.state.settings, "workspace_path", "") or "").strip()
    if workspace_root:
        emptied = prune_trash(Path(workspace_root), now=time.time())
        if emptied:
            logger.info("emptied %d trashed entr(ies) past their fortnight", emptied)


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
