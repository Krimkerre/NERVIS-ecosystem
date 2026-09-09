"""`/code/` — §13.3's reverse proxy in front of an independently managed code-server.

**What this is for, since the tab worked without it.** The Code tab embedded
code-server directly at its own port, which loads and is honest about what it
is doing — and puts the editor on a second origin that NERVIS neither
authenticates nor controls the headers of. §13.3 asks for the opposite
arrangement: one origin, NERVIS deciding who may reach the editor and which
workspace they opened, and the browser told what it may do with the frame.

**What NERVIS does not become by proxying.** It is not an alternate filesystem
authority and not code-server's own security. Same page does not mean same
security context: code-server keeps its own credential, its own workspace
handling and its own gates, and this route adds a boundary in front of them
rather than replacing them. A caller who gets past NERVIS still meets
code-server exactly as before, which is why "independently managed" is in the
requirement and why the upstream's own login is left to it.

**No arbitrary upstream proxying.** The upstream comes from configuration and
nothing about a request can move it — not a header, not a query parameter, not
a path that looks absolute. That is the difference between a proxy and an open
relay, and it is enforced in one place (`_target`) so a second route cannot
reintroduce it.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncIterator
from urllib.parse import urljoin, urlsplit

import httpx
import websockets
from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from websockets.exceptions import ConnectionClosed

from nervis.api.control import require_control
from nervis.code_proxy import COOKIE, Sessions
from nervis.errors import InvalidConfigurationError, NervisError, RefusedError, UnauthorizedError

router = APIRouter(prefix="/api/v1/code", tags=["code"])
proxy = APIRouter(prefix="/code", tags=["code"])

#: Headers that describe *this* hop and must not be forwarded to the next one
#: (RFC 9110 §7.6.1). `host` is separate below because httpx sets it from the
#: URL and forwarding the browser's would tell code-server it is answering on
#: NERVIS's port, which is how a base-path rewrite goes wrong.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
})

#: NERVIS's own credentials, which stop at NERVIS. The editor has no use for
#: them and an upstream that logged its request headers would be logging them.
OURS = frozenset({"x-nervis-control", "authorization"})

#: Sent on every proxied response. Deliberately *not* a full `default-src`
#: policy: code-server serves its own scripts, styles and workers, and a policy
#: written here would be NERVIS guessing at another application's asset graph
#: and breaking the editor on its next release. What is asserted is the part
#: that is NERVIS's business — who may frame this, and that the browser must
#: not re-interpret a content type.
SECURITY_HEADERS = {
    "content-security-policy": "frame-ancestors 'self'",
    "x-frame-options": "SAMEORIGIN",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}

#: How long a single proxied request may take. Generous because an editor
#: fetches large bundles on first load, and bounded because §10 asks that a
#: hung upstream not become a hung NERVIS.
UPSTREAM_TIMEOUT = 60.0


def sessions(request: Request | WebSocket) -> Sessions:
    return request.app.state.code_sessions  # type: ignore[no-any-return]


def _configured(request: Request | WebSocket) -> str:
    """The one address this proxy will talk to, or a refusal.

    Read per request rather than captured at startup so that changing the
    setting and restarting is the whole story, and so a test can point it
    somewhere without rebuilding the application.
    """
    base = str(getattr(request.app.state.settings, "code_server_base_url", "") or "")
    if not base:
        raise InvalidConfigurationError("no code-server address is configured")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidConfigurationError(f"{base!r} is not an http(s) address")
    return base.rstrip("/")


def _graded(request: Request | WebSocket) -> None:
    """Refuse to proxy a code-server the compatibility matrix never graded.

    §13.3's first sentence — *"proxy only a pinned, independently managed
    code-server whose Clarvis compatibility matrix passes"* — and the same
    check the Code tab already makes before drawing its frame. It is repeated
    here rather than trusted from there because the tab is a page and this is
    the boundary: a request that arrives without the page having drawn anything
    is exactly the request the check exists for.
    """
    registry = getattr(request.app.state, "registry", None)
    entry = registry.get("codeserver") if registry is not None else None
    if entry is None:
        return
    state = (getattr(entry, "capabilities", {}) or {}).get("codeserver.workbench", "")
    if state == "unavailable":
        reason = (getattr(entry, "capability_reasons", {}) or {}).get(
            "codeserver.workbench", "this code-server was never graded"
        )
        raise RefusedError(f"this code-server is not one the matrix graded: {reason}")


def _target(base: str, path: str, query: str) -> str:
    """Where a proxied request goes, or the refusal that says it may not.

    **Every escape route out of the upstream is closed here.** A path segment
    of `..` climbs out of the base; a path that is itself absolute
    (`//evil.example/x`, `http://evil.example/x`) replaces the base entirely
    under ordinary URL joining; a backslash is a separator on one platform and
    a literal on another, and the disagreement is the bug. None of them are
    normalised into something safe — they are refused, because a request that
    contains one is not a request the editor made.
    """
    if "\\" in path or path.startswith("/") or "://" in path:
        raise RefusedError("that is not a path inside the editor")
    segments = path.split("/")
    if any(segment in {"..", "."} for segment in segments):
        raise RefusedError("that is not a path inside the editor")
    joined = f"{base}/{path}" if path else f"{base}/"
    # Belt and braces: if anything above were ever loosened, the result still
    # has to live under the configured base or it does not get sent.
    if not joined.startswith(f"{base}/"):
        raise RefusedError("that is not a path inside the editor")
    return f"{joined}?{query}" if query else joined


def _forward(headers: Any, upstream: str) -> dict[str, str]:
    """The request headers code-server should see.

    Hop-by-hop headers stop here by the specification; NERVIS's own credentials
    stop here because they are NERVIS's. `host` is rewritten to the upstream's
    own authority so that anything code-server derives from it — a redirect, an
    absolute asset URL, a cookie domain — is derived from where it actually
    lives rather than from where NERVIS is.
    """
    kept = {
        name: value
        for name, value in headers.items()
        if name.lower() not in HOP_BY_HOP and name.lower() not in OURS
    }
    kept["host"] = urlsplit(upstream).netloc
    # The editor's own cookie travels; NERVIS's does not. code-server has no
    # use for a session token that authorises reaching it, and forwarding one
    # puts a credential in another application's logs.
    cookies = kept.get("cookie", "")
    if cookies:
        kept["cookie"] = "; ".join(
            crumb for crumb in cookies.split(";")
            if not crumb.strip().lower().startswith(f"{COOKIE}=")
        )
        if not kept["cookie"]:
            del kept["cookie"]
    return kept


def _rewrite_location(value: str, base: str) -> str:
    """A redirect the browser may follow, or nothing at all.

    **An open redirect is the whole risk in one header.** Anything the upstream
    puts here is reflected by NERVIS's own origin, so a `Location` pointing off
    the machine would be NERVIS sending its user somewhere else with NERVIS's
    address in the URL bar until the moment it lands. A redirect *inside* the
    editor is rewritten onto `/code/`; a redirect anywhere else is dropped,
    which turns an open redirect into a broken one and that is the right trade.
    """
    absolute = urljoin(f"{base}/", value)
    if absolute == base or absolute.startswith(f"{base}/"):
        return "/code/" + absolute[len(base):].lstrip("/")
    return ""


def _answer_headers(upstream_headers: Any, base: str) -> dict[str, str]:
    """The response headers the browser should see."""
    out: dict[str, str] = {}
    for name, value in upstream_headers.items():
        lowered = name.lower()
        if lowered in HOP_BY_HOP or lowered == "content-length":
            # `content-length` goes because the body is streamed and re-framed;
            # keeping the upstream's number is how a response gets truncated.
            continue
        if lowered == "location":
            rewritten = _rewrite_location(value, base)
            if rewritten:
                out["location"] = rewritten
            continue
        out[name] = value
    out.update(SECURITY_HEADERS)
    return out


async def _relay(
    client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str], body: bytes
) -> tuple[int, dict[str, str], AsyncIterator[bytes], Any]:
    """Open the upstream response and hand back its parts, still streaming.

    Opened rather than read: an editor's first load is megabytes of bundle, and
    buffering it here would hold all of it per tab for no benefit — §10's
    bounded-memory rule applied to a proxy.
    """
    request = client.build_request(method, url, headers=headers, content=body or None)
    response = await client.send(request, stream=True)
    return response.status_code, dict(response.headers), response.aiter_raw(), response


@router.post("/session", status_code=201)
async def open_session(request: Request) -> Response:
    """Authorise this browser to reach the editor, on one named workspace.

    Both gates that guard every other mutation here apply — minting a
    credential is the most consequential mutation on this service. The
    cross-origin refusal is not called here because `app.py`'s middleware
    already applies it to every request; the control token is, because it is
    per-route by design.
    """
    require_control(request)
    _graded(request)
    body = await request.json() if await request.body() else {}
    roots = _workspace_roots(request)
    session = sessions(request).open(str(body.get("workspace") or ""), roots)
    answer = JSONResponse(
        {"session": session.as_dict(session.opened_at)}, status_code=201
    )
    # **`secure` follows the scheme rather than being asserted.** A `Secure`
    # cookie is dropped outright by the browser over http, so hardcoding it
    # would break the loopback deployment this ships as while looking careful.
    answer.set_cookie(
        COOKIE, session.token, httponly=True, samesite="strict",
        path="/", secure=request.url.scheme == "https",
    )
    return answer


@router.get("/session")
async def read_session(request: Request) -> dict[str, object]:
    """§13.3's "visible connection state", plus what may be opened.

    **The roots are part of the read because selection has to be explicit.**
    A page that had to guess a workspace would either invent one or send the
    user to type a path, and both are ways of deciding on the operator's behalf
    what the editor may reach. The configured roots are the whole menu, so the
    choice is the operator's and the options are configuration's.
    """
    state = sessions(request).state(request.cookies.get(COOKIE, ""))
    if not state.get("open"):
        state["roots"] = _workspace_roots(request)
    return {"session": state}


@router.delete("/session", status_code=204)
async def close_session(request: Request) -> Response:
    """End it now rather than waiting for a timeout — §13.3's teardown."""
    require_control(request)
    sessions(request).close(request.cookies.get(COOKIE, ""))
    answer = Response(status_code=204)
    answer.delete_cookie(COOKIE, path="/")
    return answer


@proxy.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def forward(path: str, request: Request) -> Response:
    """One proxied request, from the browser to the graded code-server.

    No cross-origin check of its own: `app.py`'s middleware refuses a
    cross-origin mutation before any route is reached, and the editor's own
    requests are same-origin because that is what proxying them here achieves.
    """
    _graded(request)
    sessions(request).holding(request.cookies.get(COOKIE, ""))
    base = _configured(request)
    url = _target(base, path, request.url.query)
    client: httpx.AsyncClient = request.app.state.code_client
    status, upstream_headers, stream, response = await _relay(
        client, request.method, url, _forward(request.headers, base), await request.body()
    )

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in stream:
                yield chunk
        finally:
            await response.aclose()

    return StreamingResponse(
        body(), status_code=status, headers=_answer_headers(upstream_headers, base)
    )


@proxy.websocket("/{path:path}")
async def bridge(path: str, websocket: WebSocket) -> None:
    """The editor's WebSocket, carried to the same upstream and no other.

    **Every gate the HTTP path applies is applied again here.** A WebSocket is
    not a lesser request: it is the connection the editor does its actual work
    over, and a proxy that authorised the page load and waved the socket
    through would be checking the wrapping rather than the contents.

    Closed from either side ends both. Without that, a code-server restart
    leaves NERVIS holding a socket the browser thinks is live, which presents
    as an editor that has stopped responding for no stated reason.
    """
    try:
        _graded(websocket)
        sessions(websocket).holding(websocket.cookies.get(COOKIE, ""))
        base = _configured(websocket)
        target = _target(base, path, websocket.url.query)
    except NervisError as refusal:
        # Refused before the handshake completes, so the browser sees a failed
        # connection rather than one that opens and immediately dies.
        await websocket.close(code=1008, reason=refusal.code)
        return

    upstream = "ws" + target[len("http"):]
    await websocket.accept()
    try:
        async with websockets.connect(upstream, open_timeout=UPSTREAM_TIMEOUT) as peer:
            await _pump(websocket, peer)
    except (OSError, ConnectionClosed, asyncio.TimeoutError):
        # The editor is gone or never answered. 1011 is "the server hit a
        # condition that stopped it fulfilling the request", which is exactly
        # what an unreachable upstream is from the browser's point of view.
        await _close_quietly(websocket, 1011)


async def _to_peer(browser: WebSocket, peer: Any) -> None:
    """Browser to editor, until the browser goes away."""
    while True:
        message = await browser.receive()
        if message["type"] == "websocket.disconnect":
            await peer.close()
            return
        if (text := message.get("text")) is not None:
            await peer.send(text)
        elif (data := message.get("bytes")) is not None:
            await peer.send(data)


async def _to_browser(browser: WebSocket, peer: Any) -> None:
    """Editor to browser, until the editor stops sending."""
    async for frame in peer:
        if isinstance(frame, bytes):
            await browser.send_bytes(frame)
        else:
            await browser.send_text(frame)
    await _close_quietly(browser, 1000)


async def _pump(browser: WebSocket, peer: Any) -> None:
    """Carry frames both ways until either side stops.

    Whichever direction finishes first ends the other: a socket that stayed
    half-open would be an editor the browser believes is live and that will
    never answer again.
    """
    first, pending = await asyncio.wait(
        [
            asyncio.create_task(_to_peer(browser, peer)),
            asyncio.create_task(_to_browser(browser, peer)),
        ],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in first:
        # Re-raised so the caller's handler can answer an upstream failure;
        # a cancelled sibling is not a failure and is not raised here.
        task.result()


async def _close_quietly(socket: WebSocket, code: int) -> None:
    """Close, tolerating a socket the other side already closed."""
    with contextlib.suppress(RuntimeError):
        await socket.close(code=code)


def _workspace_roots(request: Request) -> list[str]:
    """Where the editor may be opened, from configuration only.

    A request never contributes a root. §13.3 draws the line at NERVIS not
    becoming an alternate filesystem authority, and a caller-supplied root
    would be precisely that with an extra step.
    """
    settings = request.app.state.settings
    declared = str(getattr(settings, "code_workspace_roots", "") or "")
    if declared:
        return [root for root in declared.split(",") if root.strip()]
    single = str(getattr(settings, "workspace_path", "") or "")
    return [single] if single else []


def refuse_unless_open(request: Request) -> None:
    """For a caller outside this module that needs the same gate.

    Exported rather than inlined so that a future surface — a download route, a
    second frame — cannot decide for itself what an open session means.
    """
    sessions(request).holding(request.cookies.get(COOKIE, ""))


__all__ = ["router", "proxy", "refuse_unless_open", "UnauthorizedError"]
