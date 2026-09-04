"""Failures, and the one place they become wire responses.

The runbook §4.3 publishes a shape and a closed list of codes:

    {"error": {"code": "NOT_FOUND", "message": "...",
               "details": {}, "request_id": "...", "trace_id": "..."}}

Both sibling services render exactly this, and NERVIS renders it too rather
than FastAPI's default `{"detail": "..."}` — a consumer written against the
document would not find the code it was told to branch on.

Internally none of this exists: code raises exceptions with context and never
returns an error code or a sentinel. The translation happens exactly once, at
the edge, in `to_response`. That single point is what stops an exception type
leaking into a published contract and the wire shape drifting per endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class NervisError(Exception):
    """Base for every failure NERVIS raises deliberately.

    `code` is the stable, machine-readable string a client branches on. Part of
    the published contract, so it is chosen once and not renamed to follow a
    refactor.
    """

    code = "INTERNAL_ERROR"
    status = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        # Facts a caller can act on. Never a credential and never a prompt:
        # this dict is serialised straight onto the wire.
        self.details = details


class NotFoundError(NervisError):
    """Nothing here carries that identifier."""

    code = "NOT_FOUND"
    status = 404


class UnauthorizedError(NervisError):
    """The caller did not prove it may do this.

    401 rather than 403: the distinction is whether credentials were *missing
    or wrong* (401) or *valid and insufficient* (403), and every refusal on the
    registration path is the first kind — there are no partial rights to hold.
    """

    code = "UNAUTHORIZED"
    status = 401


class HostRejectedError(NervisError):
    """The request named a host NERVIS does not answer to (§16 item 5).

    403 rather than 401: nothing about a credential would help. The request took
    a route this service does not have, which is what a DNS-rebound page looks
    like from the inside.
    """

    code = "HOST_REJECTED"
    status = 403


class RefusedError(NervisError):
    """A well-formed request that breaks a rule NERVIS enforces.

    Distinct from `InvalidConfigurationError` because the caller is not
    misconfigured — it asked for something that is coherent and disallowed,
    like registering as a service that may not register.
    """

    code = "REFUSED"
    status = 409


class InvalidConfigurationError(NervisError):
    """The request is well-formed and asks for something incoherent."""

    code = "INVALID_CONFIGURATION"
    status = 422



def to_response(request: Request, error: NervisError) -> JSONResponse:
    """The one place an exception becomes a body (§4.3).

    `request_id` is read off the request rather than generated here so it
    matches the one in the response header and the logs; correlating a report
    with a log line is the entire reason the field exists.
    """
    return JSONResponse(
        status_code=error.status,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                # Retryability is a property of the failure, not of the
                # caller's patience: a rate limit clears on its own, a rejected
                # credential never will. §4.5 lists it and this envelope omitted
                # it, so every client had to infer from the status code what the
                # service already knew (§16 item 10).
                "retryable": error.status in (429, 503),
                "details": error.details,
                "request_id": getattr(request.state, "request_id", ""),
                "trace_id": getattr(request.state, "trace_id", ""),
            }
        },
    )
