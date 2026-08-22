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
