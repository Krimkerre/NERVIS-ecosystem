"""Admission control: the checks that refuse a request before it costs anything.

RAVIS.md §4.4. The ordering here is the substance of the rule, not an
implementation detail, so it is worth stating plainly.

`BodySizeLimiter` is a **pure ASGI wrapper**, not a framework middleware. That is
a deliberate choice made by looking at how the same rule failed elsewhere: in the
router these limits were borrowed from, the body cap was registered before the
auth middleware and its docstring said so — but Starlette inserts middleware at
index 0 and builds the stack in reverse, so the executed order was the opposite
of the written one and the cap ran *after* authentication. The test could not see
it either, because it exercised a throwaway app whose only middleware was the cap.

Wrapping the ASGI app directly removes the question. This runs first because it
is outermost, and that is visible at the call site in `app.py` rather than
implied by registration order.
"""

from __future__ import annotations

import ipaddress
import time
from collections import defaultdict, deque

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ravis.config import Settings
from ravis.errors import OriginRejectedError, RateLimitedError

# Methods that change state. Origin and CSRF requirements apply to these; a GET
# that a browser can make cross-origin is a disclosure risk, but a POST is an
# action taken with the user's ambient credentials.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class BodySizeLimiter:
    """Refuse an oversized body before anything else touches the request.

    Two checks, because either alone is insufficient. A declared Content-Length
    is refused immediately, which costs nothing. A body that arrives without one
    — or that lies about it — is counted as it streams, and refused the moment
    the running total crosses the ceiling rather than after the whole thing has
    been buffered.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    @property
    def app(self) -> ASGIApp:
        """The wrapped application.

        Public because the wrapper is a transport layer rather than an
        encapsulation boundary: whatever wired this up still needs to reach the
        application it wrapped, and reading a private attribute to do so would
        make every caller complicit in an accident waiting to happen.
        """
        return self._app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if self._declared_length_exceeds_limit(scope):
            await _send_plain_refusal(send, 413, "REQUEST_TOO_LARGE")
            return
        await self._app(scope, self._counting_receive(receive, send), send)

    def _declared_length_exceeds_limit(self, scope: Scope) -> bool:
        """True when the caller admits up front that the body is too large."""
        for name, value in scope.get("headers", []):
            if name == b"content-length" and value.isdigit():
                return int(value) > self._max_bytes
        return False

    def _counting_receive(self, receive: Receive, send: Send) -> Receive:
        """Wrap `receive` so the body is measured as it arrives.

        A caller that omits Content-Length, or understates it, is caught here
        instead of being trusted. The counter closes over one request, so there
        is no shared state to race on.
        """
        received = 0

        async def counting() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    await _send_plain_refusal(send, 413, "REQUEST_TOO_LARGE")
                    # Reporting disconnect stops the application reading further:
                    # the response has already been sent, so continuing to feed it
                    # body would be writing after the reply.
                    return {"type": "http.disconnect"}
            return message

        return counting


async def _send_plain_refusal(send: Send, status: int, code: str) -> None:
    """Emit a minimal JSON refusal without involving the framework.

    This runs below FastAPI, so the exception handlers are not available yet.
    The shape matches the MEP envelope closely enough to be parsed, and the
    status code carries the meaning either way.
    """
    body = (
        b'{"error":{"code":"' + code.encode()
        + b'","message":"Request refused by admission control"}}'
    )
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def client_address(headers: dict[str, str], peer: str, settings: Settings) -> str:
    """Determine the address to rate-limit against.

    A forwarded header is honoured **only** when the immediate peer is a
    configured trusted proxy. Otherwise the caller would choose their own
    rate-limit bucket simply by setting a header, which turns the limit into a
    formality (§4.4).
    """
    if peer not in settings.trusted_proxies:
        return peer
    forwarded = headers.get("x-forwarded-for", "")
    if not forwarded:
        return peer
    # The left-most entry is the original client; the rest are proxies it passed
    # through. Only the first is meaningful here.
    candidate = forwarded.split(",")[0].strip()
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return peer
    return candidate


def check_origin(headers: dict[str, str], method: str, settings: Settings) -> None:
    """Reject a browser request from an origin that is not allow-listed.

    Loopback is not a boundary against a browser: a page the user is visiting can
    POST to 127.0.0.1 carrying whatever credentials the browser already holds.
    An identity check does not help, because the browser has a real one. Raises
    rather than returning a verdict, so a caller cannot forget to act on it.
    """
    origin = headers.get("origin", "")
    if not origin:
        # No Origin header means no browser made this request. Command-line
        # clients and SDKs do not set one, and they are the ordinary caller.
        return
    if origin not in settings.allowed_origins:
        raise OriginRejectedError(
            "Origin is not allow-listed", origin=origin, method=method
        )


# Methods a browser may use cross-origin. Reads only, and deliberately so: every
# `/api/v1` endpoint that exists today is a read (M18a exposes no mutation), so
# allowing POST would widen the surface past anything that can currently use it.
# When a mutation lands, whoever adds it decides then whether a browser on
# another origin should be able to reach it — which is a decision worth making
# explicitly rather than inheriting from this line.
CORS_METHODS = "GET, HEAD, OPTIONS"

# Request headers a browser may send cross-origin. `authorization` is here
# because an allow-listed dashboard on a non-loopback bind needs the §4.4 client
# credential to be read at all; `content-type` and the two correlation headers
# because §4.3 fixes them as the vocabulary every request carries.
CORS_REQUEST_HEADERS = "authorization, content-type, x-request-id, traceparent"


def is_preflight(method: str, headers: dict[str, str]) -> bool:
    """Whether this is a CORS preflight rather than a real request.

    Distinguished by `Access-Control-Request-Method`, not by the verb alone: a
    plain OPTIONS is an ordinary request and should be handled as one.
    """
    return method == "OPTIONS" and "access-control-request-method" in headers


def cors_headers(origin: str, settings: Settings) -> dict[str, str]:
    """The CORS headers for an allow-listed origin, or none at all.

    Three properties matter more than the mechanics, and all three are choices:

    - **The origin is echoed, never `*`.** A wildcard would let any page on the
      internet read this service the moment the port is reachable.
    - **`Access-Control-Allow-Credentials` is absent.** Its combination with an
      echoed origin is the classic hole — it lets a hostile page make
      *authenticated* reads using the browser's ambient credentials. RAVIS
      authenticates with a bearer token the page must supply deliberately
      (§9.6.0), so nothing here needs the browser to attach anything by itself.
    - **An origin that is not allow-listed gets nothing**, and the browser
      discards the response. `check_origin` has already refused the request
      outright by this point; this is the second half of the same decision,
      reading the same setting, so the two cannot drift apart.
    """
    if not origin or origin not in settings.allowed_origins:
        return {}
    return {
        "access-control-allow-origin": origin,
        "access-control-allow-methods": CORS_METHODS,
        "access-control-allow-headers": CORS_REQUEST_HEADERS,
        # Correlation IDs are useless to a browser it cannot read them from.
        "access-control-expose-headers": "x-request-id, x-ecosystem-actor",
        # Ten minutes. Long enough that a dashboard polling every few seconds
        # does not preflight every call, short enough that revoking an origin
        # takes effect while somebody is still watching.
        "access-control-max-age": "600",
        # The response differs by origin, so a shared cache must not serve one
        # origin's response to another.
        "vary": "Origin",
    }


class RateLimiter:
    """Per-identity request counting over a sliding one-minute window.

    In-memory and per-process on purpose at M0: RAVIS is a local-first service
    with one process, and a shared store would be infrastructure bought for a
    problem that does not exist yet. The interface is what matters — when a
    second process appears, this is the only implementation that changes.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, identity_key: str, limit_per_minute: int, now: float | None = None) -> None:
        """Record one request, raising if the identity is over its allowance.

        Sliding window rather than fixed buckets: a fixed window lets a caller
        send twice the limit across a boundary, which is precisely the burst that
        saturates a local runtime.
        """
        moment = time.monotonic() if now is None else now
        window = self._hits[identity_key]
        cutoff = moment - 60.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= limit_per_minute:
            raise RateLimitedError(
                "Inbound rate limit exceeded",
                limit_per_minute=limit_per_minute,
                identity=identity_key,
            )
        window.append(moment)
