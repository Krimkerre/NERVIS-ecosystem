"""Building the ASGI application.

M0 is a shell on purpose: the `/ecosystem/*` surface, a migrated database, and
nothing that needs a runtime to exist. §21's exit for this milestone is that
`sirvis serve` works with no LM Studio present, because Stage 1 exits here and
Stage 4 cannot start until it does.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable

import httpx
from ecosystem_protocol import EventPublisher, carrying, new_request_id, trace_id_from
from ecosystem_protocol import router as ecosystem_router
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from sirvis import jobs as job_store
from sirvis.api import router as api_router
from sirvis.api.security import (
    check_host,
    cors_headers,
    ensure_bootstrap_token,
    is_preflight,
)
from sirvis.availability import Availability, watch_lmstudio
from sirvis.config import Settings
from sirvis.core.machine import machine_identity
from sirvis.downloads import serve_downloads
from sirvis.ecosystem import BUILD_VERSION, sirvis_surface
from sirvis.errors import SirvisError, to_response
from sirvis.resources import ResourceManager, StopReport
from sirvis.runtimes import LMStudioAdapter
from sirvis.storage import prepare_database, reconcile_interrupted
from sirvis.worker import serve_queue

NextCall = Callable[[Request], Awaitable[Any]]

# M11's outbound reads: a Hugging Face search, a download's status from LM Studio.
HUB_TIMEOUT_SECONDS = 15.0

# ── The stop, against the launcher's clock ───────────────────────────────────
#
# `tools/run.py stop` asks SIRVIS to stop, then forces a kill 12 seconds later
# (its `GRACE_BEFORE_KILL`). Everything SIRVIS does on the way out has to fit
# inside that, in this order, with at least a second to spare:
#
#     uvicorn's pause before it waits        0.1 s   fixed inside uvicorn
#     requests still in flight               1   s   GRACEFUL_SHUTDOWN_SECONDS
#     releasing and unloading models         6   s   STOP_BUDGET_SECONDS
#     draining the event queue               3   s   EVENT_DRAIN_SECONDS
#                                           ------
#                                           10.1 s   of 12
#
# `tests/test_release_on_stop.py` holds that sum against the launcher's number.

# **One second for requests in flight, because the long ones lose nothing by
# being cut off.** uvicorn waited for them without limit, so a session open
# waiting on a big load kept the release from starting at all until the
# launcher's forced kill made it moot. Everything else SIRVIS answers finishes
# well inside a second. A session open cancelled at the second does not strand
# its model: the load is shielded and carries on, the release unloads it if it
# lands inside the budget, and any lease the session already took is released
# with every other. uvicorn takes whole seconds, so one is the least it offers.
GRACEFUL_SHUTDOWN_SECONDS = 1

# How long SIRVIS waits for LM Studio to unload what it loaded. Six rather than
# the 2.5 it was while the launcher waited six, because the extra time buys
# something: a load already under way when the stop arrives is unloaded if it
# lands inside it, rather than left behind held by nobody.
#
# The budget bounds SIRVIS's *waiting*, not the unload. `lms unload` runs on a
# worker thread (`LMStudioAdapter.unload`), so one that outlasts the budget is
# not cut off halfway: the interpreter waits for that thread on exit. The CLI's
# own timeout is 10 seconds and the unloads start by 1.1 s, so even an unload
# LM Studio never answers lets the process end by about 11 — inside the twelve,
# though not with the full second to spare. A *load* still running does not end
# in time: its timeout is minutes, and the launcher's forced kill ends it.
STOP_BUDGET_SECONDS = 6.0

# The event drain's deadline, passed explicitly rather than left to the protocol
# package's default, so the sum above is of numbers this file actually uses.
EVENT_DRAIN_SECONDS = 3.0

LOG = logging.getLogger(__name__)


def create_app(settings: Settings, runtime: LMStudioAdapter | None = None) -> FastAPI:
    """Assemble the application from settings and return it.

    Construction is separated from use (runbook §14.2): everything the request
    path needs is built here, so no handler opens a database of its own.

    `runtime` is injectable, and the reason is a defect rather than symmetry.
    Tests used to build the app and then replace `app.state.lmstudio` with a
    recorded one — but the Resource Manager is constructed *here*, capturing the
    adapter it was given, so the swap left a live adapter behind inside it. A
    single test posting to `/runtime/sessions` then loaded a real model onto the
    developer's machine. Passing the runtime in closes the window: there is no
    moment at which a live adapter exists to be left behind.
    """
    api = FastAPI(
        title="SIRVIS", version=BUILD_VERSION, docs_url=None, redoc_url=None,
        lifespan=_lifespan,
    )
    _attach_shared_state(api, settings, runtime)
    _register_correlation(api)
    _register_error_handling(api)
    _register_cors(api)
    api.include_router(ecosystem_router)
    api.include_router(api_router)
    return api


@asynccontextmanager
async def _lifespan(api: FastAPI) -> AsyncIterator[None]:
    """Drain the event queue while the service is up, and once on the way out.

    SIRVIS had no lifespan at all, which was fine while nothing here outlived a
    request. M21 gives it one thing that does: `emit` only queues, so without a
    loop the events sit in memory and are never sent — the failure mode being
    that everything looks correct and the hub stays empty.

    The client is opened here rather than shared, because SIRVIS's only other
    outbound client belongs to the LM Studio adapter and borrowing it would tie
    a telemetry timeout to a model load.

    **On the way out it also gives back every model SIRVIS loaded**
    (`_release_models_on_stop`), since 12 September 2026 — until then a model
    loaded through SIRVIS outlived it in LM Studio, held by nobody.
    """
    # M14's queue. Started before the event pump because a submitted benchmark
    # must run whether or not anybody is collecting telemetry — the queue is the
    # product, the events are the observation of it.
    worker = asyncio.create_task(serve_queue(api))
    # M11's download watcher, for the same reason: a transfer LM Studio is making is
    # watched whether or not anybody collects the events about it.
    watcher = asyncio.create_task(serve_downloads(api))
    # §15.4: whether LM Studio is there, so the capabilities it carries say so when it is not.
    runtime_watch = asyncio.create_task(watch_lmstudio(api))
    publisher: EventPublisher = api.state.events
    if not publisher.enabled:
        try:
            yield
        finally:
            worker.cancel()
            watcher.cancel()
            runtime_watch.cancel()
            # After the queue is cancelled, so a benchmark cannot take a model
            # behind the release; its own `finally` then finds its session
            # already ended and does nothing. Before the drain below, whose
            # deadline shares the launcher's twelve seconds (`STOP_BUDGET_SECONDS`).
            await _release_models_on_stop(api)
        return
    async with httpx.AsyncClient() as client:
        pump = asyncio.create_task(publisher.run(client))
        try:
            yield
        finally:
            worker.cancel()
            watcher.cancel()
            runtime_watch.cancel()
            # Same place and reason as the branch above.
            await _release_models_on_stop(api)
            pump.cancel()
            # Bounded, so a hub that stopped answering cannot hold a shutdown
            # open. The closing event of a benchmark is the one worth waiting a
            # moment for.
            await publisher.drain(client, deadline=EVENT_DRAIN_SECONDS)


async def _release_models_on_stop(api: FastAPI) -> None:
    """Give back every model SIRVIS loaded, and say in the log that stopping is why.

    §9 makes SIRVIS the owner of every load and unload, and until 12 September
    2026 it released nothing when it stopped. A model loaded for the menu bar,
    the dashboard's Runtime screen, RAVIS or an interrupted benchmark stayed in
    LM Studio held by nobody, and the next SIRVIS saw it as foreign. The
    launcher covered the menu bar's own sessions and nobody else's
    (`_release_menu_sessions` in `tools/run.py`). The owner's decision that day:
    stopping unloads what SIRVIS loaded, so stopping the stack frees the memory.
    A model SIRVIS did not load is still never unloaded.

    Never raises. A failure here must not cost the event drain after it, nor
    turn an ordinary stop into uvicorn's "application shutdown failed".
    """
    manager: ResourceManager = api.state.resources
    try:
        report = await manager.release_on_stop(STOP_BUDGET_SECONDS)
    except Exception:  # noqa: BLE001 - nothing on the way out may raise
        LOG.exception("could not release models while SIRVIS was stopping")
        return
    _log_stop_report(report)


def _log_stop_report(report: StopReport) -> None:
    """One line per fact somebody looking at LM Studio after a stop needs.

    Every line names the reason. A release is otherwise indistinguishable in
    the log from a client closing its own session, and the question after a
    stop is always *who unloaded my model*. Warnings only for what may still be
    in memory, because that is the line worth acting on.
    """
    for lease in report.sessions:
        LOG.info(
            "released session %s (owner %s; %s) because SIRVIS stopped",
            lease.session_id, lease.owner, ", ".join(lease.model_keys),
        )
    if report.unloaded:
        LOG.info("unloaded %s because SIRVIS stopped", ", ".join(report.unloaded))
    if report.left_loaded:
        LOG.info(
            "left %s loaded when SIRVIS stopped: SIRVIS did not load it, so it is "
            "not SIRVIS's to unload", ", ".join(report.left_loaded),
        )
    if report.not_unloaded:
        LOG.warning(
            "SIRVIS stopped without LM Studio confirming it unloaded %s (%s); it may "
            "still be loaded, held by nobody",
            ", ".join(report.not_unloaded),
            f"no answer within {STOP_BUDGET_SECONDS:g}s" if not report.finished
            else "the unload did not succeed",
        )
    if report.still_loading:
        LOG.warning(
            "a load of %s was still under way when SIRVIS stopped; it may finish "
            "after SIRVIS has gone, held by nobody", ", ".join(report.still_loading),
        )


def _attach_shared_state(
    api: FastAPI, settings: Settings, runtime: LMStudioAdapter | None = None
) -> None:
    """Build the things every request needs, once, at startup."""
    api.state.settings = settings
    api.state.database = prepare_database(settings.database_path)
    # §11.10: a job survives a restart or is truthfully marked unrecoverable. A
    # run does neither by itself — `start_run` writes RUNNING before the work,
    # and a killed process leaves that row claiming to be in progress for ever.
    # This service is the thing that just started, so anything still unfinished
    # belonged to a process that no longer exists.
    abandoned = reconcile_interrupted(api.state.database)
    # The same reconciliation for jobs, and for the same reason (§11.10): a row
    # left reading `running` after the process died neither survived the restart
    # nor was truthfully marked unrecoverable. A *queued* job is left alone —
    # it never started, so it survives honestly and the worker will take it.
    for stranded in job_store.reconcile_interrupted(api.state.database):
        logging.getLogger(__name__).warning(
            "marked interrupted benchmark job %s unrecoverable", stranded
        )
    if abandoned:
        logging.getLogger(__name__).warning(
            "marked %d interrupted run(s) unrecoverable", len(abandoned),
            extra={"runs": abandoned},
        )
    # §4.5 requires a token and a fresh install has none, so the first run mints
    # one. It is deliberately not logged or printed here — `sirvis token` is the
    # one place it can be read, and only once.
    ensure_bootstrap_token(api.state.database)
    # Identity of this installation. Opaque and locally generated — never
    # derived from hardware, a serial number or a username (runbook §4.1).
    # A real machine identity, which is a different thing, arrives with M1's
    # SystemSnapshot; this is only who *this service* is.
    installation = uuid.uuid5(uuid.NAMESPACE_DNS, settings.database_path).hex[:12]
    api.state.service_id = f"sirvis-{installation}"
    # **The stored identity, not a constant.** This was
    # `uuid5(NAMESPACE_DNS, "sirvis-machine")` — the same value on every SIRVIS
    # in existence — while `machine_identity` sat two modules away holding
    # exactly what §4.1 asks for: locally generated, opaque, not derived from
    # hardware, stored so it survives a restart, and resettable by deleting a
    # row. The comment above distinguished "who this service is" from "which
    # machine this is" and then used the machine field for the former.
    #
    # It goes into `source.machine_id` on every event from M21 on, where a
    # constant would have every installation's events claiming to be one
    # machine's.
    api.state.machine_id = machine_identity(api.state.database)
    # M21. Always present so `state.events.emit(...)` is unconditional at every
    # call site; disabled unless an operator points SIRVIS at a hub.
    api.state.events = EventPublisher(
        service_type="sirvis",
        service_id=api.state.service_id,
        machine_id=api.state.machine_id,
        base_url=settings.nervis_base_url,
        secret=settings.nervis_events_secret,
    )
    # Built once, holds no state about what is loaded: the runtime is the
    # authority on that (§7), and a cache would be wrong the first time anything
    # else on this machine loaded something.
    api.state.lmstudio = runtime or LMStudioAdapter(
        base_url=settings.lmstudio_base_url,
        lms_path=settings.lmstudio_cli_path,
    )
    # §9: *all* load and unload operations flow through this. The adapter is
    # still reachable for reads — discovery, generation — but nothing else in
    # the service is allowed to drive lifecycle directly, because the moment two
    # code paths can unload a model, one of them does it while the other is
    # using it.
    # §8's two outbound readers (M11): Hugging Face for discovery, LM Studio's REST API
    # for downloads. Clients of their own rather than the adapter's, so a slow search
    # or a download poll never shares a timeout with a model load.
    api.state.hub_client = httpx.AsyncClient(
        base_url=settings.huggingface_base_url,
        timeout=HUB_TIMEOUT_SECONDS,
        headers={"user-agent": f"sirvis/{BUILD_VERSION}"},
    )
    api.state.download_client = httpx.AsyncClient(
        base_url=settings.lmstudio_base_url, timeout=HUB_TIMEOUT_SECONDS
    )
    api.state.resources = ResourceManager(
        runtime=api.state.lmstudio,
        default_lease_seconds=settings.default_lease_seconds,
        max_loaded=settings.max_loaded_models,
    )
    api.state.ecosystem = sirvis_surface(
        service_id=api.state.service_id,
        machine_id=api.state.machine_id,
        database=api.state.database,
    )
    api.state.availability = Availability()


def _register_cors(api: FastAPI) -> None:
    """Let an allow-listed browser origin read this service (§16's dashboard).

    Read endpoints without CORS are endpoints no browser can use, which made
    the benchmark results servable and unreadable at the same time. Preflights
    are answered here rather than reaching the router, where an OPTIONS to a
    GET-only path would be a 405 and the browser would report a CORS failure
    for what is really a routing answer.
    """

    @api.middleware("http")
    async def cors(request: Request, call_next: NextCall) -> Any:
        settings: Settings = request.app.state.settings
        origin = request.headers.get("origin", "")
        headers = {key.lower(): value for key, value in request.headers.items()}
        # **Before the preflight answer, and before every read.** `require`
        # guards mutations; a DNS-rebound page reading the machine inventory
        # never reaches it (§16 item 5).
        try:
            check_host(headers, settings)
        except SirvisError as refusal:
            return to_response(request, refusal)
        if is_preflight(request.method, headers):
            return Response(status_code=204, headers=cors_headers(origin, settings))
        response = await call_next(request)
        response.headers.update(cors_headers(origin, settings))
        return response


def _register_error_handling(api: FastAPI) -> None:
    """Turn a refusal into a response, in one place.

    One translation point so the wire shape of a refusal cannot drift per
    endpoint — and so no handler can accidentally include the presented
    credential in the body while explaining why it was rejected.
    """

    @api.exception_handler(SirvisError)
    async def handle_sirvis_error(request: Request, exc: SirvisError) -> JSONResponse:
        return to_response(request, exc)

    @api.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """The refusals this service did not raise itself (§16 item 10).

        One translation point was one too few: the service's own error type was
        covered and everything the framework raises was not, so a 404 for a path
        that does not exist arrived as Starlette's `{"detail": …}` rather than
        §4.5's envelope. A client parsing the envelope found no `error` object at
        all on exactly the responses it most needs to read.
        """
        detail = exc.detail
        structured: dict[str, Any] = detail if isinstance(detail, dict) else {}
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": str(structured.get("code") or f"HTTP_{exc.status_code}"),
                    "message": str(structured.get("message") or detail),
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
    approve a gate or elevate a caller. That sentence is in the runbook because
    the opposite is a tempting shortcut.
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
        # The same two ids the response will carry, put where anything logging
        # underneath this request can reach them without being handed them.
        with carrying(request_id=request.state.request_id,
                      trace_id=request.state.trace_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response
