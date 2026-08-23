"""Failure types, and the one place they become wire responses.

Why this module exists
----------------------
RAVIS speaks two error dialects, and which one it owes depends on which door the
request came in through (RAVIS.md §4.2, ECOSYSTEM_RUNBOOK.md §4.5):

    /v1/*         OpenAI-compatible error shapes, because OpenAI SDK clients and
                  Clarvis parse them and would break on anything else
    /api/v1/*     the MEP error envelope
    /ecosystem/*  the MEP error envelope

Internally none of that exists: code raises exceptions with context and never
returns an error code or a sentinel (runbook §14.4). The translation happens
exactly once, at the edge, in `to_response`. That single-point rule is what stops
an exception type leaking into a published contract — the wire shape is a promise
to other people's software, and it must not track our class names.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class RavisError(Exception):
    """Base for every failure RAVIS raises deliberately.

    `code` is the stable, machine-readable string a client may branch on. It is
    part of the published contract, so it is chosen once and not renamed to match
    a refactor. `status` is the HTTP status the runbook's mapping assigns to this
    class of failure (§4.1).
    """

    code = "INTERNAL_ERROR"
    status = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        # Details carry the *facts* a caller needs to act — a limit that was
        # exceeded, a field that failed validation. Never a secret, a credential
        # or a prompt: this dict is serialised straight onto the wire.
        self.details = details


class RequestTooLargeError(RavisError):
    """The body exceeded the §4.4 ceiling, refused before authentication ran."""

    code = "REQUEST_TOO_LARGE"
    status = 413


class TooManyImagesError(RavisError):
    """More inline images than §4.4 allows.

    Separate from RequestTooLargeError on purpose: a thousand small images can sit
    comfortably under any byte ceiling and still overwhelm a vision backend, so a
    byte limit alone does not bound this.
    """

    code = "TOO_MANY_IMAGES"
    status = 413


class RemoteUrlRefusedError(RavisError):
    """A client asked RAVIS to fetch a URL. RAVIS does not fetch on request.

    Refusal is the feature, not a limitation (§4.4). A gateway that dereferences
    caller-supplied URLs is an SSRF proxy into the local network — it can be
    pointed at a metadata service, a neighbouring container or a loopback admin
    port, and it does so with RAVIS's own network position.
    """

    code = "REMOTE_URL_REFUSED"
    status = 400


class RateLimitedError(RavisError):
    """This application identity has exceeded its inbound allowance (§4.4)."""

    code = "RATE_LIMITED"
    status = 429


class OriginRejectedError(RavisError):
    """The Origin or Host header is not allow-listed (§4.4).

    Loopback is not a boundary against a browser: any page the user visits can
    POST to 127.0.0.1, carrying whatever credentials the browser already holds.
    An identity check does not stop that, which is why this check is separate.
    """

    code = "ORIGIN_REJECTED"
    status = 403


class NotFoundError(RavisError):
    """The addressed thing does not exist, or no longer does.

    Raised rather than using FastAPI's HTTPException so the response goes
    through `to_response` and comes out in the dialect the path owes — §4.5
    requires the MEP envelope on `/api/v1`, and HTTPException renders
    `{"detail": …}`, which is neither of our two contracts.
    """

    code = "NOT_FOUND"
    status = 404


class UnsupportedProtocolVersionError(RavisError):
    """A peer asked for a protocol major this build does not implement.

    Runbook §4.2 requires this to fail cleanly and structurally rather than by
    guessing at compatibility, so a mismatch is loud at negotiation time instead
    of subtly wrong three calls later.
    """

    code = "UNSUPPORTED_PROTOCOL_VERSION"
    status = 400


def _openai_shape(error: RavisError) -> dict[str, Any]:
    """Render as OpenAI's error object, which /v1 clients parse."""
    return {
        "error": {
            "message": error.message,
            "type": error.code.lower(),
            "param": error.details.get("param"),
            "code": error.code,
        }
    }


def _mep_shape(error: RavisError, request_id: str, trace_id: str) -> dict[str, Any]:
    """Render as the MEP error envelope (runbook §4.5)."""
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            # Retryability is a property of the failure, not of the caller's
            # patience: a rate limit clears on its own, a rejected origin never
            # will. Saying so stops clients retrying what cannot succeed.
            "retryable": error.status in (429, 503),
            "details": error.details,
            "request_id": request_id,
            "trace_id": trace_id,
        }
    }


def to_response(request: Request, error: RavisError) -> JSONResponse:
    """Translate an exception into the dialect this path owes.

    The path prefix decides, not the exception: the same RateLimitedError raised on
    /v1 and on /api/v1 must serialise differently, because two different
    published contracts describe them.
    """
    request_id = getattr(request.state, "request_id", "")
    trace_id = getattr(request.state, "trace_id", "")
    is_openai_surface = request.url.path.startswith("/v1")
    body = (
        _openai_shape(error)
        if is_openai_surface
        else _mep_shape(error, request_id, trace_id)
    )
    # Correlation IDs travel in headers on both surfaces even though only one
    # carries them in the body (§4.2), so a bug report has an ID either way.
    return JSONResponse(
        status_code=error.status, content=body, headers={"X-Request-ID": request_id}
    )
