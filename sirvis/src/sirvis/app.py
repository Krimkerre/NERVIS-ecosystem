"""Building the ASGI application.

M0 is a shell on purpose: the `/ecosystem/*` surface, a migrated database, and
nothing that needs a runtime to exist. §21's exit for this milestone is that
`sirvis serve` works with no LM Studio present, because Stage 1 exits here and
Stage 4 cannot start until it does.
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from ecosystem_protocol import new_request_id
from ecosystem_protocol import router as ecosystem_router
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from sirvis.api import router as api_router
from sirvis.api.security import cors_headers, ensure_bootstrap_token, is_preflight
from sirvis.config import Settings
from sirvis.ecosystem import sirvis_surface
from sirvis.errors import SirvisError, to_response
from sirvis.resources import ResourceManager
from sirvis.runtimes import LMStudioAdapter
from sirvis.storage import prepare_database

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
    api = FastAPI(title="SIRVIS", version="0.0.1", docs_url=None, redoc_url=None)
    _attach_shared_state(api, settings, runtime)
    _register_correlation(api)
    _register_error_handling(api)
    _register_cors(api)
    api.include_router(ecosystem_router)
    api.include_router(api_router)
    return api


def _attach_shared_state(
    api: FastAPI, settings: Settings, runtime: LMStudioAdapter | None = None
) -> None:
    """Build the things every request needs, once, at startup."""
    api.state.settings = settings
    api.state.database = prepare_database(settings.database_path)
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
    api.state.machine_id = uuid.uuid5(uuid.NAMESPACE_DNS, "sirvis-machine").hex
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


def _register_correlation(api: FastAPI) -> None:
    """Give every request an ID and echo it back (runbook §4.3).

    Correlation data and never authorization: a request or trace ID does not
    approve a gate or elevate a caller. That sentence is in the runbook because
    the opposite is a tempting shortcut.
    """

    @api.middleware("http")
    async def correlate(request: Request, call_next: NextCall) -> Any:
        request.state.request_id = request.headers.get("x-request-id") or new_request_id()
        request.state.trace_id = request.headers.get("traceparent", "")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response
