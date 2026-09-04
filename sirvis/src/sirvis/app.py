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
from sirvis.config import Settings
from sirvis.core.machine import machine_identity
from sirvis.ecosystem import sirvis_surface
from sirvis.errors import SirvisError, to_response
from sirvis.resources import ResourceManager
from sirvis.runtimes import LMStudioAdapter
from sirvis.storage import prepare_database, reconcile_interrupted
from sirvis.worker import serve_queue

NextCall = Callable[[Request], Awaitable[Any]]


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
        title="SIRVIS", version="0.0.1", docs_url=None, redoc_url=None,
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
    """
    # M14's queue. Started before the event pump because a submitted benchmark
    # must run whether or not anybody is collecting telemetry — the queue is the
    # product, the events are the observation of it.
    worker = asyncio.create_task(serve_queue(api))
    publisher: EventPublisher = api.state.events
    if not publisher.enabled:
        try:
            yield
        finally:
            worker.cancel()
        return
    async with httpx.AsyncClient() as client:
        pump = asyncio.create_task(publisher.run(client))
        try:
            yield
        finally:
            worker.cancel()
            pump.cancel()
            # Bounded, so a hub that stopped answering cannot hold a shutdown
            # open. The closing event of a benchmark is the one worth waiting a
            # moment for.
            await publisher.drain(client)


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
