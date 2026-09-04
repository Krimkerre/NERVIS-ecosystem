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
from ravis.errors import (
    OriginRejectedError,
    RateLimitedError,
    UnsupportedMediaTypeError,
)

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


# Content types a browser can send from a plain `<form>` without asking
# permission first. A request carrying one of these is never preflighted, so the
# Origin allowlist never gets consulted — which is precisely why a mutation must
# not accept them.
SAFELISTED_CONTENT_TYPES = frozenset({
    "text/plain",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
})


def check_content_type(headers: dict[str, str], method: str) -> None:
    """Refuse a state-changing request that a form could have sent.

    **This makes an argument the code already made actually true.** The CORS
    note below reasons that `/v1/chat/completions` is safe from a page because "a
    JSON body always preflights, so this permits an allow-listed origin and
    nobody else". Nothing on the server side made the body JSON: the handler read
    raw bytes and parsed them whatever the header said, so a form posting
    `text/plain` — safelisted, never preflighted, therefore never measured
    against the Origin allowlist — was accepted and ran inference on the
    operator's account. Found by reading the handler rather than by testing the
    claim, which is the usual way an assumed control turns out to be absent.

    Only the three safelisted types are refused, rather than requiring JSON
    outright. A `DELETE` with no body carries no content type and must still
    work, and anything a browser cannot send without a preflight is already
    covered by the Origin check.
    """
    if method.upper() not in MUTATING_METHODS:
        return
    media = headers.get("content-type", "").split(";")[0].strip().lower()
    if media in SAFELISTED_CONTENT_TYPES:
        raise UnsupportedMediaTypeError(
            "a state-changing request must be sent as application/json",
            media_type=media,
            method=method,
        )


def check_host(headers: dict[str, str], settings: Settings) -> None:
    """Reject a request that believes it is talking to somewhere else.

    **This is the half `check_origin` structurally cannot do.** A page on
    `attacker.example` whose DNS is re-pointed at `127.0.0.1` — rebinding —
    reaches RAVIS as a *same-origin* request, and browsers omit `Origin` on
    same-origin GETs. `check_origin` then sees no origin to reject and allows it,
    exactly as its comment says it should: "no Origin header means no browser
    made this request" is true of curl and false of this.

    What the request cannot hide is the name the browser resolved, which is still
    in `Host`. RAVIS binds loopback and nothing else can start (§16 item 2), so a
    `Host` naming anything else describes a route RAVIS does not have.

    An absent `Host` is refused too: HTTP/1.1 requires one, so its absence is a
    client doing something deliberate rather than an ordinary caller.
    """
    host = headers.get("host", "").strip()
    if not host:
        raise OriginRejectedError("a Host header is required", origin="", method="")
    # The port is not checked, only the name. A deployment may move the port and
    # the launcher already does when one is taken; the attack this defends does
    # not turn on which port answered. Brackets come off IPv6 literals.
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 or host.startswith("[") else host
    permitted = {one.strip("[]").lower() for one in settings.allowed_hosts}
    if name.strip("[]").lower() not in permitted:
        raise OriginRejectedError("Host is not allow-listed", origin=host, method="")


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


# Methods a browser may use cross-origin. This line used to be reads only, and
# left the decision to whoever landed the first mutation. M10 is that mutation,
# and the decision is yes — with a reason and a limit.
#
# **Why yes.** The credential screen *is* a browser on another origin: NERVIS is
# served from somewhere other than RAVIS's port. Without PUT and DELETE here, the
# one screen that lets an operator configure a provider cannot work at all, and a
# provider nobody can configure is a provider nobody can use.
#
# **Why this is not a widening.** `allowed_origins` is empty by default, so no
# browser origin is permitted anything until an operator names one. This changes
# what an already-trusted origin may do, not who is trusted.
#
# **Why PUT rather than POST** for credentials, beyond idempotency: POST with a
# simple content-type is CORS-safelisted and is sent *without* a preflight, so
# the allow-list never gets consulted. PUT always preflights. Choosing it means a
# page that was never allow-listed cannot slip a credential write through as a
# simple request — the browser asks first, and RAVIS says no.
#
# **POST is here for `/v1/chat/completions`**, which is the service's whole
# purpose and the one thing a browser client exists to call. It is not the
# safelisted kind: a JSON body always preflights, so this permits an
# allow-listed origin and nobody else. Without it the dashboard's chat screen
# fails at the preflight and reports RAVIS as unreachable, which is the wrong
# diagnosis of a CORS list.
CORS_METHODS = "GET, HEAD, OPTIONS, POST, PUT, DELETE"

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
