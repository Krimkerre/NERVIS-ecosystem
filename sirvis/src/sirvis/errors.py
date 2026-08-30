"""Failures, and the one place they become wire responses (§4.3).

§4.3 publishes a shape and a closed list of codes:

    {"error": {"code": "INSUFFICIENT_MEMORY", "message": "...",
               "details": {}, "request_id": "...", "trace_id": "..."}}

The service shipped FastAPI's default `{"detail": "..."}` instead, which is a
different contract — a consumer written against the document would not find the
code it was told to branch on. Fixed before more surface is built on top of it,
because a wire shape is a promise to other people's software and the cost of
changing one rises with every consumer.

Internally none of this exists: code raises exceptions with context and never
returns an error code or a sentinel. The translation happens exactly once, at
the edge, in `to_response` — that single point is what stops an exception type
leaking into a published contract and the wire shape drifting per endpoint.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class SirvisError(Exception):
    """Base for every failure SIRVIS raises deliberately.

    `code` is the stable, machine-readable string a client branches on, drawn
    from §4.3's list. Part of the published contract, so it is chosen once and
    not renamed to follow a refactor.
    """

    code = "INTERNAL_ERROR"
    status = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        # Facts a caller can act on — a limit exceeded, the models currently
        # held. Never a credential and never a prompt: this dict is serialised
        # straight onto the wire.
        self.details = details


class ModelNotFoundError(SirvisError):
    """No build carries the identifier or runtime key that was asked for.

    §6 is explicit that 404 is a correct answer and must stay distinguishable
    from a guess — RAVIS resolves builds through this path, and a fuzzy match
    would become a route.
    """

    code = "MODEL_NOT_FOUND"
    status = 404


class ModelNotInstalledError(SirvisError):
    """The build is known but is not on this machine."""

    code = "MODEL_NOT_INSTALLED"
    status = 404


class VariantUnconfirmedError(SirvisError):
    """The loaded build cannot be identified, so nothing may be recorded about it.

    §12.2 makes format and quantization part of evidence identity: a measurement
    of an MLX build filed as the GGUF build is evidence about one thing
    attributed to another, and RAVIS admits and excludes on exactly that. LM
    Studio groups variants under one entry and its HTTP API describes the
    selected one rather than the loaded one, so when its CLI cannot be asked
    there is nothing left that knows — and a benchmark that cannot name what it
    measured is a record nobody can use twice.

    **`INVALID_CONFIGURATION` rather than a code of its own.** §4.3's list is
    closed and a consumer enumerates it; inventing an entry for one case is a
    contract change nobody agreed to. And the name fits what happened: this
    machine holds two builds under one runtime entry with nothing able to say
    which is loaded, which is a configuration that cannot answer the question
    rather than a failure of the model or the runtime.
    """

    code = "INVALID_CONFIGURATION"
    status = 409


class RuntimeUnreachableError(SirvisError):
    """The runtime is not answering.

    502 rather than 503: SIRVIS is fine and the thing behind it is not, which
    tells an operator which process to look at.
    """

    code = "RUNTIME_UNAVAILABLE"
    status = 502


class InsufficientMemoryError(SirvisError):
    """The machine cannot hold what was requested."""

    code = "INSUFFICIENT_MEMORY"
    status = 409


class ResourceBusyError(SirvisError):
    """Capacity is held by somebody else (§9).

    409 rather than 429: this is not rate limiting and retrying immediately will
    not help. Something has to be released first, and `details` says by whom.
    """

    code = "RESOURCE_BUSY"
    status = 409


class InvalidConfigurationError(SirvisError):
    """The request is well-formed and asks for something incoherent."""

    code = "INVALID_CONFIGURATION"
    status = 422


class UnsupportedParameterError(SirvisError):
    """A value the runtime cannot honour (§7.1).

    Its own code because §7.1 forbids silently ignoring one: a caller must be
    able to tell "you asked for something I cannot do" from "your request was
    malformed", since only the first means the result would have described a
    configuration nobody chose.
    """

    code = "UNSUPPORTED_PARAMETER"
    status = 422


class LoadFailedError(SirvisError):
    """The runtime accepted the request and could not load the model."""

    code = "LOAD_FAILED"
    status = 502


class BenchmarkNotFoundError(SirvisError):
    """No such suite, job, run or result."""

    code = "BENCHMARK_NOT_FOUND"
    status = 404


class DeadlineExceededError(SirvisError):
    """An operation exceeded its deadline.

    Not named `TimeoutError`: that is a builtin meaning something else, and a
    module where it means two things depending on the import is a trap. The
    published `code` is §4.3's `TIMEOUT` regardless of what the class is called.
    """

    code = "TIMEOUT"
    status = 504


class AuthenticationRequiredError(SirvisError):
    """A mutating endpoint reached without a usable token (§4.5)."""

    code = "UNAUTHENTICATED"
    status = 401


class ForbiddenError(SirvisError):
    """A token or origin that exists and is not permitted here (§4.5)."""

    code = "FORBIDDEN"
    status = 403


class UnsupportedMediaTypeError(SirvisError):
    """A mutation that did not arrive as JSON (§4.5's CSRF defence)."""

    code = "UNSUPPORTED_MEDIA_TYPE"
    status = 415


def to_response(request: Request, error: SirvisError) -> JSONResponse:
    """Render one failure in §4.3's shape.

    Correlation IDs are attached here rather than by each raiser, so no code
    path can forget them. They are what lets somebody find this failure in the
    log afterwards, and a failure nobody can find is the one reported as "it
    just broke".
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
