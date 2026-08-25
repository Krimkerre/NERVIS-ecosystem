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


class InvalidConfigurationError(NervisError):
    """The request is well-formed and asks for something incoherent."""

    code = "INVALID_CONFIGURATION"
    status = 422


class ServiceUnreachableError(NervisError):
    """A peer NERVIS reads did not answer.

    502 rather than 503: NERVIS is fine, and something it depends on is not.
    The distinction matters to whatever is watching NERVIS's own health, which
    must not go unhealthy because RAVIS was restarted.
    """

    code = "SERVICE_UNREACHABLE"
    status = 502


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
                "details": error.details,
                "request_id": getattr(request.state, "request_id", ""),
                "trace_id": getattr(request.state, "trace_id", ""),
            }
        },
    )
