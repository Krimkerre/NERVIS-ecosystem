"""`POST /v1/chat/completions` — transparent forwarding (RAVIS.md §6, Path A).

The whole design in one sentence: **this moves bytes and does not interpret
them.** The request body reaches the upstream exactly as the client sent it, and
the response reaches the client exactly as the upstream sent it.

That is not laziness, it is the point. §6 says not to normalise an
already-compatible stream, because every transformation is somewhere a detail
can be lost, and the details at risk are the ones Clarvis depends on: tool-call
indexes and IDs, fragmented JSON arguments, reasoning fields, finish reasons,
usage chunks and `[DONE]`. A proxy that never parses a tool-call fragment cannot
reassemble one wrongly.

The body *is* parsed once — but only to inspect it for the §4.4 image cap and to
learn whether the client asked for a stream. What gets forwarded is the original
bytes, never a re-serialisation of that parse.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ravis.content import check_image_count
from ravis.upstream import forwardable_headers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["openai"])

# Response headers that describe *our* hop rather than the payload, and would be
# wrong if copied: httpx has already decoded the body, so a forwarded
# content-encoding would tell the client to decode it a second time.
SKIPPED_RESPONSE_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding"})


@router.post("/chat/completions")
async def create_chat_completion(request: Request) -> Response:
    """Forward a completion request to the configured upstream.

    No routing: M1 has one upstream and the client's `model` field travels to it
    untouched. Choosing between models arrives at M5, and the transparent path
    is deliberately proven before anything decides anything.
    """
    upstream = request.app.state.upstream
    if not upstream.is_configured:
        return _openai_error(
            "No upstream is configured. Set RAVIS_UPSTREAM_BASE_URL.",
            "upstream_not_configured",
            503,
        )

    body = await request.body()
    parsed = _inspect(body, request)
    if isinstance(parsed, JSONResponse):
        return parsed

    headers = forwardable_headers(dict(request.headers), upstream)
    target = upstream.url_for("/v1/chat/completions")
    client: httpx.AsyncClient = request.app.state.upstream_client

    if parsed.get("stream") is True:
        return _stream_from_upstream(client, target, headers, body)
    return await _forward_and_return(client, target, headers, body)


def _inspect(body: bytes, request: Request) -> dict[str, Any] | JSONResponse:
    """Parse the body for admission checks only, never for forwarding.

    Returns the parsed payload, or a ready-made error response when the request
    must be refused. The §4.4 URL refusal is deliberately *not* applied here:
    that rule says the field is forwarded untouched on the transparent path,
    because the upstream owns its own dereferencing policy and RAVIS is not the
    one making the fetch.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return _openai_error("Request body is not valid JSON", "invalid_request_error", 400)
    if not isinstance(parsed, dict):
        return _openai_error("Request body must be a JSON object", "invalid_request_error", 400)

    messages = parsed.get("messages", [])
    if isinstance(messages, list):
        check_image_count(messages, request.app.state.settings)
    return parsed


async def _forward_and_return(
    client: httpx.AsyncClient, target: str, headers: dict[str, str], body: bytes
) -> Response:
    """The non-streaming path: one request, one response, forwarded whole.

    The upstream's status code travels with the body. An upstream 429 must reach
    the client as a 429 — turning it into a 500 would tell the client to give up
    where it should have retried.
    """
    try:
        upstream_response = await client.post(target, headers=headers, content=body)
    except httpx.HTTPError as failure:
        return _upstream_unreachable(failure)
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=_forwardable_response_headers(upstream_response),
        media_type=upstream_response.headers.get("content-type"),
    )


def _stream_from_upstream(
    client: httpx.AsyncClient, target: str, headers: dict[str, str], body: bytes
) -> StreamingResponse:
    """The streaming path: raw bytes out as raw bytes arrive.

    Nothing here inspects a frame. `[DONE]` is correct because the upstream's
    `[DONE]` is forwarded verbatim, tool-call fragments survive because they are
    never touched, and there is no buffering because each chunk is yielded the
    moment it lands.
    """
    return StreamingResponse(
        _relay(client, target, headers, body),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Without this a reverse proxy may buffer the whole stream and
            # deliver it at the end, which looks exactly like the model being
            # slow and is the bug this header exists to prevent.
            "X-Accel-Buffering": "no",
        },
    )


async def _relay(
    client: httpx.AsyncClient, target: str, headers: dict[str, str], body: bytes
) -> AsyncIterator[bytes]:
    """Yield upstream bytes until the stream ends or the client goes away.

    Cancellation is the important part (§8.6). When the client disconnects, this
    generator is closed, which exits the `async with` and tears down the upstream
    connection — so the model stops generating instead of producing tokens
    nobody will read and, on a paid provider, nobody should be billed for.
    That propagation is why the request is issued inside the generator rather
    than awaited into a buffer first.
    """
    try:
        async with client.stream("POST", target, headers=headers, content=body) as upstream:
            if upstream.status_code >= 400:
                # An error before the stream begins is a normal response, not a
                # stream: read it whole and pass the upstream's own error object
                # through, since it is already in the shape clients parse.
                detail = await upstream.aread()
                yield _sse_error(detail)
                return
            async for chunk in upstream.aiter_bytes():
                yield chunk
    except httpx.HTTPError as failure:
        logger.warning("upstream stream failed", extra={"detail": str(failure)})
        yield _sse_error(json.dumps(_error_body(str(failure), "upstream_error")).encode())


def _sse_error(detail: bytes) -> bytes:
    """Wrap an error body as a single SSE frame, terminated properly.

    A stream that stops without `[DONE]` leaves a client waiting for more, so
    even a failure ends the stream the way the protocol says to end it.
    """
    return b"data: " + detail.strip() + b"\n\ndata: [DONE]\n\n"


def _forwardable_response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in SKIPPED_RESPONSE_HEADERS
    }


def _error_body(message: str, error_type: str) -> dict[str, Any]:
    return {"error": {"message": message, "type": error_type, "param": None, "code": error_type}}


def _openai_error(message: str, error_type: str, status: int) -> JSONResponse:
    """An error in OpenAI's shape, because /v1 clients parse it (§4.5)."""
    return JSONResponse(status_code=status, content=_error_body(message, error_type))


def _upstream_unreachable(failure: httpx.HTTPError) -> JSONResponse:
    logger.warning("upstream unreachable", extra={"detail": str(failure)})
    return _openai_error(f"Upstream request failed: {failure}", "upstream_error", 502)
