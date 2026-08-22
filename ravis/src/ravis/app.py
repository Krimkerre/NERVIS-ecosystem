"""Building the ASGI application, in an order that is visible rather than implied.

The wiring order below is load-bearing (RAVIS.md §4.4), so it is written as
nesting rather than as registration:

    BodySizeLimiter          ← outermost, runs first, refuses before auth
      └─ FastAPI
           └─ correlation + identity + origin + rate limit middleware
                └─ routes

A framework's middleware registration order is a convention; nesting is a fact.
This is the shape the §4.4 gate asserts — "the body-size test proves refusal
occurs without authentication having run" — and the reason it is asserted is
that the router these limits came from got it backwards while its docstring
claimed otherwise.
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ravis.admission import BodySizeLimiter, RateLimiter, check_origin, client_address
from ravis.config import Settings
from ravis.ecosystem import router as ecosystem_router
from ravis.errors import RavisError, to_response
from ravis.identity import resolve_identity
from ravis.observability import new_request_id
from ravis.storage import prepare_database

NextCall = Callable[[Request], Awaitable[Any]]


def create_app(settings: Settings) -> Any:
    """Assemble the application from settings, and return the outermost layer.

    Construction is separated from use (runbook §14.2): everything the request
    path needs is built here and attached, so no handler constructs a database
    connection or a limiter of its own. That is also what makes the whole thing
    testable without a running service.
    """
    api = FastAPI(title="RAVIS", version="0.0.1", docs_url=None, redoc_url=None)
    _attach_shared_state(api, settings)
    _register_middleware(api, settings)
    _register_error_handling(api)
    api.include_router(ecosystem_router)
    # Wrapping last means this ends up outermost, which is the entire point.
    return BodySizeLimiter(api, settings.max_request_bytes)


def _attach_shared_state(api: FastAPI, settings: Settings) -> None:
    """Build the things every request needs, once, at startup."""
    api.state.settings = settings
    api.state.database = prepare_database(settings.database_path)
    api.state.rate_limiter = RateLimiter()
    # Identity of this installation. Opaque and locally generated — never derived
    # from hardware, a serial number or a username (runbook §4.1).
    installation = uuid.uuid5(uuid.NAMESPACE_DNS, settings.database_path).hex[:12]
    api.state.service_id = f"ravis-{installation}"
    api.state.machine_id = uuid.uuid5(uuid.NAMESPACE_DNS, "ravis-machine").hex
    # Bumped whenever the advertised capability set changes, so a consumer can
    # distinguish a real change from a re-read.
    api.state.capability_revision = 1


def _register_middleware(api: FastAPI, settings: Settings) -> None:
    """Correlation, identity, origin and rate limiting, in that order.

    Order matters within this layer too: correlation first so that everything
    after it can be logged against a request ID, identity next because the rate
    limit is keyed to it, and origin before the limit because a rejected origin
    should not consume somebody's allowance.
    """

    @api.middleware("http")
    async def apply_admission_control(request: Request, call_next: NextCall) -> Any:
        request.state.request_id = request.headers.get("x-request-id") or new_request_id()
        request.state.trace_id = request.headers.get("traceparent", "")
        headers = {key.lower(): value for key, value in request.headers.items()}

        identity = resolve_identity(headers, settings)
        request.state.identity = identity

        try:
            check_origin(headers, request.method, settings)
            peer = request.client.host if request.client else "unknown"
            address = client_address(headers, peer, settings)
            api.state.rate_limiter.check(
                f"{identity.application_id}:{address}", identity.rate_limit_per_minute
            )
        except RavisError as refusal:
            return to_response(request, refusal)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        # Report the resolved identity back; never read it as an assertion
        # (RAVIS.md §9.6.0).
        response.headers["X-Ecosystem-Actor"] = identity.application_id
        return response


def _register_error_handling(api: FastAPI) -> None:
    """Translate exceptions into whichever dialect the path owes.

    One handler, one translation point. A handler per exception type would let
    the wire shape drift apart per endpoint, which is the failure `errors.py`
    exists to prevent.
    """

    @api.exception_handler(RavisError)
    async def handle_ravis_error(request: Request, exc: RavisError) -> JSONResponse:
        return to_response(request, exc)
