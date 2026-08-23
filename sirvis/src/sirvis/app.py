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

from sirvis.api import router as api_router
from sirvis.config import Settings
from sirvis.ecosystem import sirvis_surface
from sirvis.runtimes import LMStudioAdapter
from sirvis.storage import prepare_database

NextCall = Callable[[Request], Awaitable[Any]]


def create_app(settings: Settings) -> FastAPI:
    """Assemble the application from settings and return it.

    Construction is separated from use (runbook §14.2): everything the request
    path needs is built here, so no handler opens a database of its own. That is
    also what makes the whole thing testable without a running service.
    """
    api = FastAPI(title="SIRVIS", version="0.0.1", docs_url=None, redoc_url=None)
    _attach_shared_state(api, settings)
    _register_correlation(api)
    api.include_router(ecosystem_router)
    api.include_router(api_router)
    return api


def _attach_shared_state(api: FastAPI, settings: Settings) -> None:
    """Build the things every request needs, once, at startup."""
    api.state.settings = settings
    api.state.database = prepare_database(settings.database_path)
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
    api.state.lmstudio = LMStudioAdapter(base_url=settings.lmstudio_base_url)
    api.state.ecosystem = sirvis_surface(
        service_id=api.state.service_id,
        machine_id=api.state.machine_id,
        database=api.state.database,
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
        request.state.trace_id = request.headers.get("traceparent", "")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response
