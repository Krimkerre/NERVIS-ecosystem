"""`ravis conformance clarvis` — the suite behind RAVIS.md §8.9's release gate.

The exit criterion is a sentence, not a metric: **Clarvis cannot tell that an
intermediary was inserted.** That phrasing suggests the shape of the test.

Rather than asserting that RAVIS produces some expected value, each check reads
the same fixture twice — once straight, and once after it has travelled through
the real RAVIS application — using the ported Clarvis reader for both, and
requires the two readings to be identical. The fixture is the oracle. A hardcoded
expectation would only prove RAVIS matches this file's idea of the protocol,
which is the assumption under test.

The suite runs entirely in-process against recorded fixtures: no network, no
live model, no socket (runbook §14.5). That is what lets it run on every change
instead of when someone remembers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from ravis.app import create_app
from ravis.compatibility.clarvis import fixtures
from ravis.compatibility.clarvis.contract import ReadStream, read_stream
from ravis.config import Settings


@dataclass
class Check:
    """One named requirement, and whether it held."""

    name: str
    passed: bool
    detail: str = ""


@dataclass
class ConformanceResult:
    """Everything the suite checked, in the order it checked it."""

    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name=name, passed=passed, detail=detail))


class _FixtureUpstream:
    """An OpenAI-compatible server that replays one recorded stream.

    `frames_pulled` is what makes the cancellation check possible: it counts how
    many chunks were actually taken, which distinguishes "the client stopped
    reading" from "the upstream stopped producing".
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.frames_pulled = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": "fixture-model"}]})
        return httpx.Response(200, stream=_Replay(self._pull()))

    def _pull(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.frames_pulled += 1
            yield chunk


class _Replay(httpx.AsyncByteStream):
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> Any:
        for chunk in self._chunks:
            yield chunk


def _app_against(upstream: _FixtureUpstream) -> Any:
    """The real RAVIS application, with a recorded upstream underneath it.

    Nothing above the transport is stubbed: admission control, identity, the
    proxy and its middleware are the code that serves production traffic. A
    suite that bypassed them would certify something nobody runs.
    """
    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://fixture.invalid",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    app.app.state.upstream_client = httpx.AsyncClient(transport=upstream.transport())
    app.app.state.model_registry.use_client(app.app.state.upstream_client)
    return app


async def _through_ravis(chunks: list[bytes]) -> tuple[ReadStream, list[bytes]]:
    """Send a streaming completion through RAVIS and read it as Clarvis would.

    Returns the received chunks rather than one joined blob, because *how many*
    arrived is itself a requirement: a proxy that buffers produces byte-identical
    output in a single chunk, and every content check would still pass.
    """
    upstream = _FixtureUpstream(chunks)
    app = _app_against(upstream)
    received: list[bytes] = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ravis.invalid"
    ) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": "fixture-model", "stream": True}
    ) as response:
        async for chunk in response.aiter_raw():
            received.append(chunk)
    return read_stream(received), received


def _compare(result: ConformanceResult, name: str, direct: ReadStream, proxied: ReadStream,
             field_name: str) -> None:
    """Assert one field reads identically with and without the proxy."""
    straight = getattr(direct, field_name)
    through = getattr(proxied, field_name)
    result.record(
        name,
        straight == through,
        "" if straight == through else f"direct={straight!r} through={through!r}",
    )


async def run_suite() -> ConformanceResult:
    """Run every Stage 2 scenario and return what held.

    Stage 3 scenarios — separate pools, capability probing and fallback — are
    absent by design, not oversight: each needs routing, which the runbook
    forbids at this stage (§8.8). They are added when M5, M6 and M12 land.
    """
    result = ConformanceResult()
    await _check_models_endpoint(result)
    await _check_stream("chat stream", fixtures.PLAIN_CHAT, result, "text")
    await _check_done_terminator(result)
    await _check_tool_calls(result)
    await _check_reasoning(result)
    await _check_byte_preservation(result)
    await _check_not_buffered(result)
    await _check_cancellation(result)
    await _check_suite_can_fail(result)
    return result


async def _check_models_endpoint(result: ConformanceResult) -> None:
    """`/v1/models` answers 200 without credentials, from cache (§5.0.1)."""
    app = _app_against(_FixtureUpstream(fixtures.PLAIN_CHAT))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ravis.invalid"
    ) as client:
        response = await client.get("/v1/models")
    result.record(
        "/v1/models cached response",
        response.status_code == 200 and response.json().get("object") == "list",
        f"status={response.status_code}",
    )


async def _check_stream(name: str, chunks: list[bytes], result: ConformanceResult,
                        field_name: str) -> None:
    direct = read_stream(chunks)
    proxied, _ = await _through_ravis(chunks)
    _compare(result, name, direct, proxied, field_name)


async def _check_done_terminator(result: ConformanceResult) -> None:
    """A stream that ends without `[DONE]` leaves Clarvis waiting (§8.2)."""
    proxied, _ = await _through_ravis(fixtures.PLAIN_CHAT)
    result.record("[DONE] terminator", proxied.saw_done, "no [DONE] frame reached the client")


async def _check_tool_calls(result: ConformanceResult) -> None:
    """§8.3, release-critical: fragments and indexes must survive intact."""
    direct = read_stream(fixtures.FRAGMENTED_TOOL_CALL)
    proxied, _ = await _through_ravis(fixtures.FRAGMENTED_TOOL_CALL)
    assembled = proxied.tool_calls.get(0)
    expected = direct.tool_calls.get(0)
    reassembled_identically = (
        assembled is not None and expected is not None
        and assembled.arguments == expected.arguments
    )
    result.record(
        "fragmented tool arguments",
        reassembled_identically,
        f"assembled={assembled.arguments if assembled else None!r}",
    )
    result.record(
        "tool_call_id preserved",
        assembled is not None and assembled.id == "call_123",
        f"id={assembled.id if assembled else None!r}",
    )

    parallel_direct = read_stream(fixtures.PARALLEL_TOOL_CALLS)
    parallel_proxied, _ = await _through_ravis(fixtures.PARALLEL_TOOL_CALLS)
    same_indexes = set(parallel_direct.tool_calls) == set(parallel_proxied.tool_calls)
    same_args = all(
        parallel_direct.tool_calls[i].arguments == parallel_proxied.tool_calls[i].arguments
        for i in parallel_direct.tool_calls
    )
    result.record(
        "multiple tool indexes",
        same_indexes and same_args and len(parallel_proxied.tool_calls) == 2,
        f"indexes={sorted(parallel_proxied.tool_calls)}",
    )


async def _check_reasoning(result: ConformanceResult) -> None:
    """§8.5: reasoning stays in its own field and never leaks into content."""
    direct = read_stream(fixtures.REASONING_STREAM)
    proxied, _ = await _through_ravis(fixtures.REASONING_STREAM)
    result.record(
        "reasoning_content preserved",
        direct.reasoning == proxied.reasoning and proxied.reasoning != "",
        f"reasoning={proxied.reasoning!r}",
    )
    result.record(
        "reasoning kept out of content",
        proxied.text == direct.text and "user wants" not in proxied.text,
        f"content={proxied.text!r}",
    )


async def _check_byte_preservation(result: ConformanceResult) -> None:
    """Chunk boundaries are the proxy's business; frame content is not.

    The fixture is deliberately split mid-JSON. RAVIS may re-chunk however it
    likes, so the assertion is on the concatenated bytes rather than on the
    chunking — but nothing may be added, dropped or reordered.
    """
    _, received = await _through_ravis(fixtures.MISALIGNED_CHUNKS)
    joined = b"".join(received)
    expected = b"".join(fixtures.MISALIGNED_CHUNKS)
    result.record(
        "bytes preserved across re-chunking",
        joined == expected,
        f"{len(joined)} bytes out, {len(expected)} in",
    )


async def _check_not_buffered(result: ConformanceResult) -> None:
    """The stream must reach the client progressively, not in one piece.

    Worth its own check because every *content* assertion survives buffering. A
    proxy that collects the whole response and emits it once produces
    byte-identical output, identical text and identical tool calls — while
    destroying the only thing a user actually sees: tokens appearing as they are
    generated rather than a wall of text after a long silence.

    Measured by driving the ASGI application directly and counting
    `http.response.body` messages, **not** through an HTTP client. An in-process
    client collects the whole body before handing it back, so it reports one
    chunk however well the app behaves — which would make this check fail
    against a correct proxy and pass against nothing.
    """
    emitted = await _count_body_messages(fixtures.PLAIN_CHAT)
    result.record(
        "stream is not buffered",
        emitted > 1,
        f"app emitted {emitted} body message(s) for a {len(fixtures.PLAIN_CHAT)}-chunk stream",
    )


async def _count_body_messages(chunks: list[bytes]) -> int:
    """Run one streaming request against the raw ASGI app and count body sends.

    The `receive` here never reports a disconnect: a live client stays connected
    while it reads, and reporting a disconnect would make Starlette stop the
    stream early — measuring the harness rather than the proxy.
    """
    app = _app_against(_FixtureUpstream(chunks))
    body = b'{"model":"fixture-model","stream":true}'
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("127.0.0.1", 1),
    }
    sent: list[dict[str, Any]] = []
    delivered = False

    async def receive() -> dict[str, Any]:
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await asyncio.wait_for(app(scope, receive, send), timeout=30)
    return len([m for m in sent if m["type"] == "http.response.body" and m.get("body")])


async def _check_cancellation(result: ConformanceResult) -> None:
    """§8.6: abandoning the client must abandon the upstream generation."""
    from ravis.api.openai.chat import _relay

    upstream = _FixtureUpstream(fixtures.PLAIN_CHAT)
    client = httpx.AsyncClient(transport=upstream.transport())
    relay = _relay(client, "http://fixture.invalid/v1/chat/completions", {}, b'{"stream":true}')
    await relay.__anext__()
    await relay.aclose()
    await client.aclose()
    result.record(
        "cancellation propagated",
        upstream.frames_pulled < len(fixtures.PLAIN_CHAT),
        f"{upstream.frames_pulled} of {len(fixtures.PLAIN_CHAT)} chunks pulled after disconnect",
    )


async def _check_suite_can_fail(result: ConformanceResult) -> None:
    """A negative control: prove the `[DONE]` check can actually fail.

    Runbook §14.1 — a check that cannot fail is not a check. This feeds the
    suite a deliberately truncated stream and requires the reader to notice.
    """
    truncated = read_stream(fixtures.TRUNCATED_STREAM)
    result.record(
        "suite detects a missing [DONE]",
        truncated.saw_done is False,
        "a truncated stream was reported as complete",
    )


def render(result: ConformanceResult) -> str:
    """The §8.8 checklist, in the shape the build plan shows."""
    width = max(len(check.name) for check in result.checks) + 2
    lines = ["Clarvis OpenAI Compatibility", ""]
    for check in result.checks:
        mark = "PASS" if check.passed else "FAIL"
        detail = "" if check.passed else f"   {check.detail}"
        lines.append(f"  [{mark}] {check.name:<{width}}{detail}")
    lines.append("")
    lines.append("PASS" if result.passed else "FAIL")
    return "\n".join(lines)
