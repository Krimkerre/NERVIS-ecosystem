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

import logging
from typing import Any, Awaitable, Callable

from ecosystem_protocol import new_request_id
from ecosystem_protocol import router as ecosystem_router
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from nervis.api import router as api_router
from nervis.config import Settings
from nervis.ecosystem import BUILD_VERSION, nervis_surface
from nervis.errors import NervisError, to_response
from nervis.storage import installation_identity, prepare_database
from nervis.web import register_dashboard

NextCall = Callable[[Request], Awaitable[Any]]

logger = logging.getLogger("nervis")


def create_app(settings: Settings) -> FastAPI:
    """The application, fully wired and ready to serve.

    Takes settings rather than reading them, so a test constructs an app with
    the configuration it means instead of by arranging the environment first.
    """
    api = FastAPI(title="NERVIS", version=BUILD_VERSION)
    _attach_shared_state(api, settings)
    _register_correlation(api)
    _register_error_handling(api)
    api.include_router(ecosystem_router)
    api.include_router(api_router)
    register_dashboard(api)
    return api


def _attach_shared_state(api: FastAPI, settings: Settings) -> None:
    """Everything a handler reaches for through `request.app.state`."""
    api.state.settings = settings
    api.state.database = prepare_database(settings.database_path)

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
        request.state.trace_id = request.headers.get("traceparent", "")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response
