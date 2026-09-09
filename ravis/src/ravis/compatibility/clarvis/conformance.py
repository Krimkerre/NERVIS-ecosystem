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
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import httpx

from ravis.api.openai.chat import UPSTREAM_PROVIDER, _Call, _relay
from ravis.app import create_app
from ravis.compatibility.clarvis import fixtures
from ravis.compatibility.clarvis.contract import ReadStream, read_stream
from ravis.config import Settings
from ravis.credentials import CredentialFile, CredentialStore
from ravis.pool_membership import PoolMembership
from ravis.reliability import AttemptChain, HealthRegistry

# Two models, both declaring tool support, for the Stage 3 scenarios. Tools are
# declared rather than probed because a fixture upstream publishes model IDs and
# nothing else — the same position a real generic OpenAI-compatible endpoint is
# in, which is why §5.2 makes operator configuration the source of truth until
# probing (§8.7) or SIRVIS evidence (M13) exists.
# Everything `ravis/clarvis-agent` requires, which §5.1 always named and the
# pool only recently enforced: tools, structured calls and long context. A
# fixture declaring tools alone describes a model that pool now refuses, and the
# suite would report Clarvis's own pool as unroutable against a conformant
# upstream.
TOOL_CAPABLE = {
    "tools": "SUPPORTED",
    "structured_output": "SUPPORTED",
    "context_window": "131072",
}
FALLBACK_CATALOGUE = {"coder-a": TOOL_CAPABLE, "coder-b": TOOL_CAPABLE}
# One model that can hold a conversation and one that can also call tools. The
# separation is the point: the chat pool may use either, the agent pool may only
# use the second, so a single request cannot satisfy both by accident.
MIXED_CATALOGUE = {"chat-only-model": {"tools": "UNSUPPORTED"}, "coder-model": TOOL_CAPABLE}


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

    def __init__(
        self,
        chunks: list[bytes],
        catalogue: tuple[str, ...] = ("fixture-model",),
        refuse: tuple[str, ...] = (),
    ) -> None:
        self.chunks = chunks
        self.frames_pulled = 0
        self.catalogue = catalogue
        # Models this upstream answers with a 503 rather than a stream. That is
        # how the fallback scenario is driven: a *routing* failure would prove
        # nothing, because §8.8 requires the fallback to be dynamic rather than
        # configured, so the primary has to be genuinely chosen and genuinely
        # fail.
        self.refuse = refuse
        self.served: list[str] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            data = [{"id": model} for model in self.catalogue]
            return httpx.Response(200, json={"object": "list", "data": data})
        model = json.loads(request.content or b"{}").get("model", "")
        self.served.append(model)
        if model in self.refuse:
            return httpx.Response(503, json={"error": {"message": "model is overloaded"}})
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


def _single_attempt(client: httpx.AsyncClient, model: str) -> _Call:
    """A one-candidate call, for driving the relay generator directly.

    Used by the cancellation check, which cannot go through an HTTP client:
    `TestClient` and `httpx` both buffer a whole response, so neither can
    express a disconnect halfway through a stream. Driving the generator is
    what Starlette itself does.

    The chain has no fallbacks, which is what the check is measuring: how many
    frames the upstream produced before it was abandoned. Whether cancellation
    could *trigger* a fallback is a different question, answered structurally —
    `GeneratorExit` is not an `Exception`, so nothing in the relay catches it
    (§10) — and asserted directly in `tests/test_reliability.py`.
    """
    chain = AttemptChain(health=HealthRegistry(), provider=UPSTREAM_PROVIDER)
    chain.load(model, [])
    return _Call(
        client=client,
        destination=lambda _: ("http://fixture.invalid/v1/chat/completions", {}),
        body=json.dumps({"model": model, "stream": True}).encode(),
        payload={"model": model, "stream": True},
        chain=chain,
        recorded=None,
    )


def _app_against(upstream: _FixtureUpstream, capabilities: dict[str, dict[str, str]]
                 | None = None) -> Any:
    """The real RAVIS application, with a recorded upstream underneath it.

    Nothing above the transport is stubbed: admission control, identity, the
    proxy and its middleware are the code that serves production traffic. A
    suite that bypassed them would certify something nobody runs.
    """
    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://fixture.invalid",
        model_capabilities=capabilities or {},
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    # **No credential this machine happens to hold**, and the same argument as
    # the pools note below: a suite that reads the operator's configuration
    # certifies *this installation* rather than the build.
    #
    # This one was worse than a wrong verdict. The application registers its
    # translated providers unconditionally — Anthropic and Google exist whether
    # or not a key does — so on a machine with keys configured, the fixture's
    # catalogue was joined by every real Anthropic model, `ravis/clarvis-chat`
    # preferred `claude-haiku` over the fixture's own `chat-only-model`, and the
    # suite sent a fixture-shaped request to Anthropic's live API. It came back
    # `400: messages: at least one message is required`, which is the check
    # failing for a reason that has nothing to do with the code under test.
    #
    # It also meant the release gate spent the operator's money and took three
    # and a half minutes doing it — against ECOSYSTEM_RUNBOOK §14.5, which says
    # no test reaches a live model.
    app.app.state.credentials = CredentialStore(
        allow_environment=False,
        keychain=False,
        file=CredentialFile(
            Path(tempfile.gettempdir()) / "ravis-conformance-no-such-credentials.json"
        ),
    )
    # Emptied rather than rebuilt: the adapters were constructed by `create_app`
    # with the real store already closed over, so replacing the store above does
    # not unmake them.
    app.app.state.translating = {}
    app.app.state.upstream_client = httpx.AsyncClient(transport=upstream.transport())
    app.app.state.model_registry.use_client(app.app.state.upstream_client)
    # **Against the declared pools, not this operator's narrowed ones.**
    #
    # `PoolMembership.default()` reads `~/.config/ravis/pools.json`, which the
    # Pools screen writes whenever somebody ticks a model. A conformance suite
    # that reads it certifies *this installation* rather than the build — and it
    # duly went red on a machine where `ravis/clarvis-chat` had been narrowed to
    # nine real model ids, none of them the fixture's, so the pool resolved to
    # nothing and the verdict was about a UI click made days earlier.
    #
    # Pointed at a path that does not exist rather than at `None`: the store
    # already treats an unreadable file as "no narrowing", which is exactly the
    # state a fresh install is in and the one this suite means to test.
    app.app.state.pool_membership = PoolMembership(
        path=Path(tempfile.gettempdir()) / "ravis-conformance-no-such-pools.json"
    )
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
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
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


@contextmanager
def _no_operator_state() -> Iterator[None]:
    """Run with a config directory that does not exist.

    **The suite certified this installation, not the build.** `_app_against`
    already pointed `PoolMembership` at a path that cannot be read, for exactly
    this reason and with the reason written down — and then `models.json`,
    `prices.json`, `policies.json` and `observations.json` were all still read
    from `~/.config/ravis`, because each store finds its own way there.

    Isolating one env var closes all of them at once, including the next one
    somebody adds. That is the difference that matters: the previous fix had to
    be repeated per store and duly was not.

    Found by a check that passed for the wrong reason. `clarvis-chat` resolved to
    the fixture's `chat-only-model` because it sorted first alphabetically among
    hundreds of hosted models this machine had cached — not because the pool had
    been narrowed to the catalogue under test. Curating the chat pool changed the
    winner to a real hosted model, the request left for the Anthropic API, and a
    502 came back. The pool change was correct; the suite was reading a machine.
    """
    original = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(
        Path(tempfile.gettempdir()) / "ravis-conformance-no-such-config"
    )
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = original


async def run_suite() -> ConformanceResult:
    """Run every scenario §8.8 lists, Stage 2 and Stage 3, and return what held.

    The two stages are still separable and still worth naming. Stage 2 is
    wire-level and holds against a single upstream with no routing at all;
    Stage 3 needs routing to exist — separate pools (M5), the agent pool's tool
    invariant (M6) and dynamic fallback (M12) — and §8.8 is explicit that
    fallback "cannot be static configuration by definition", which is why the
    fallback checks below make a real primary genuinely fail rather than
    configuring a second model as the answer.
    """
    with _no_operator_state():
        return await _run_every_check()


async def _run_every_check() -> ConformanceResult:
    """Every scenario, in order. Split out so the isolation above wraps all of
    them rather than each one remembering to ask for it."""
    result = ConformanceResult()
    await _check_models_endpoint(result)
    await _check_stream("chat stream", fixtures.PLAIN_CHAT, result, "text")
    await _check_done_terminator(result)
    await _check_frames_are_well_formed(result)
    await _check_tool_calls(result)
    await _check_reasoning(result)
    await _check_usage(result)
    await _check_upstream_error(result)
    await _check_byte_preservation(result)
    await _check_not_buffered(result)
    await _check_cancellation(result)
    await _check_pool_separation(result)
    await _check_agent_tool_invariant(result)
    await _check_fallback(result)
    await _check_suite_can_fail(result)
    return result


async def _pool_request(
    upstream: _FixtureUpstream,
    capabilities: dict[str, dict[str, str]],
    pool: str,
    stream: bool = False,
) -> httpx.Response:
    """Address a pool through the whole application, catalogue warmed first.

    The refresh is explicit because nothing runs the lifespan here: an ASGI
    transport starts the app without starting it up, so a registry that would be
    warm in production is empty in a harness, and every pool would resolve to
    "no models available" for a reason that has nothing to do with conformance.
    """
    app = _app_against(upstream, capabilities)
    await app.app.state.model_registry.refresh()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        return await client.post(
            "/v1/chat/completions", json={"model": pool, "stream": stream}
        )


async def _check_pool_separation(result: ConformanceResult) -> None:
    """Chat and agent resolve independently, and may reach different models.

    §8.8's Stage 3 scenario, and the reason Clarvis's two separate provider
    settings matter: a client that points both at RAVIS must be able to get a
    conversational model for chat and a tool-capable one for the agent without
    configuring either by name.
    """
    catalogue = tuple(MIXED_CATALOGUE)
    chat_upstream = _FixtureUpstream(fixtures.PLAIN_CHAT, catalogue)
    chat = await _pool_request(chat_upstream, MIXED_CATALOGUE, "ravis/clarvis-chat")
    agent_upstream = _FixtureUpstream(fixtures.FRAGMENTED_TOOL_CALL, catalogue)
    agent = await _pool_request(agent_upstream, MIXED_CATALOGUE, "ravis/clarvis-agent")
    # Read from the upstream's own record of what it was asked to run, rather
    # than from anything RAVIS reports about itself. The question is which model
    # actually received the request, and only the upstream can answer that.
    answered = (chat_upstream.served, agent_upstream.served)
    result.record(
        "clarvis-chat and clarvis-agent route separately",
        chat.status_code == 200
        and agent.status_code == 200
        and answered[0] == ["chat-only-model"]
        and answered[1] == ["coder-model"],
        f"chat→{answered[0]} agent→{answered[1]} "
        f"status={chat.status_code}/{agent.status_code}",
    )


async def _check_agent_tool_invariant(result: ConformanceResult) -> None:
    """The agent pool refuses a catalogue with no tool-capable model (§5.2).

    A no-route, not a best-effort substitution. This is the check that stops the
    single worst failure mode in the integration: an agent silently placed on a
    model that cannot call tools, which looks like the model being bad at coding
    rather than like a routing error.
    """
    catalogue = {"chat-only-model": {"tools": "UNSUPPORTED"}}
    response = await _pool_request(
        _FixtureUpstream(fixtures.PLAIN_CHAT, tuple(catalogue)),
        catalogue,
        "ravis/clarvis-agent",
    )
    body = response.json()
    result.record(
        "clarvis-agent refuses a non-tool model",
        response.status_code == 422 and body.get("error", {}).get("code") == "no_route",
        f"status={response.status_code} body={body.get('error', {}).get('code')!r}",
    )


async def _check_fallback(result: ConformanceResult) -> None:
    """§8.8 Stage 3: primary failure → a valid compatible fallback (§10).

    Both models satisfy the agent pool's tool invariant, so the fallback is
    valid by the same rule that admitted the primary — which is §10's actual
    requirement, and the reason the fallback list comes from the router rather
    than from configuration.

    Two things are asserted, and the second is M12's acceptance criterion:
    the request succeeds against the second model, *and* the stream the client
    receives is byte-identical to the fixture. A fallback that prepended a
    failed attempt's error frame would satisfy the first and fail the second.
    """
    upstream = _FixtureUpstream(
        fixtures.FRAGMENTED_TOOL_CALL, tuple(FALLBACK_CATALOGUE), refuse=("coder-a",)
    )
    app = _app_against(upstream, FALLBACK_CATALOGUE)
    await app.app.state.model_registry.refresh()
    received: list[bytes] = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client, client.stream(
        "POST", "/v1/chat/completions", json={"model": "ravis/clarvis-agent", "stream": True}
    ) as response:
        async for chunk in response.aiter_raw():
            received.append(chunk)

    result.record(
        "fallback reaches a compatible model",
        upstream.served == ["coder-a", "coder-b"],
        f"attempted {upstream.served}",
    )
    expected = b"".join(fixtures.FRAGMENTED_TOOL_CALL)
    result.record(
        "fallback leaves the stream uncorrupted",
        b"".join(received) == expected,
        f"{len(b''.join(received))} bytes out, {len(expected)} expected",
    )


async def _check_models_endpoint(result: ConformanceResult) -> None:
    """`/v1/models` answers 200 without credentials, from cache (§5.0.1)."""
    app = _app_against(_FixtureUpstream(fixtures.PLAIN_CHAT))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
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


async def _check_frames_are_well_formed(result: ConformanceResult) -> None:
    """Nothing RAVIS forwards should be unparseable to a Clarvis client.

    `read_stream` has always counted malformed frames — *"one bad chunk should
    cost a few tokens, not the whole answer"* — and nothing has ever read the
    count. Every other check compares what survived the proxy against what a
    direct read produced, so a proxy that corrupted a frame in a way both sides
    skipped identically passed all of them. This is the check that notices.
    """
    direct = read_stream(fixtures.PLAIN_CHAT)
    proxied, _ = await _through_ravis(fixtures.PLAIN_CHAT)
    result.record(
        "frames parse",
        proxied.malformed_frames <= direct.malformed_frames,
        f"{proxied.malformed_frames} unparseable frame(s) reached the client, "
        f"against {direct.malformed_frames} read directly",
    )


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


async def _check_usage(result: ConformanceResult) -> None:
    """§15 lists usage among the contract tests, and no fixture carried one.

    The frame an upstream sends for `stream_options: {"include_usage": true}`
    has an empty `choices` and only token counts, so a proxy that drops it looks
    correct to every other check in this suite — and to the reader those checks
    are built on, until it learned to look. What reads the counts downstream is
    the cost engine, which cannot invent them.
    """
    direct = read_stream(fixtures.USAGE_STREAM)
    proxied, _ = await _through_ravis(fixtures.USAGE_STREAM)
    result.record(
        "usage frame reaches the client",
        proxied.usage is not None,
        f"usage={proxied.usage}",
    )
    result.record(
        "usage counts are unchanged",
        proxied.usage == direct.usage,
        f"upstream={direct.usage} proxied={proxied.usage}",
    )


async def _check_upstream_error(result: ConformanceResult) -> None:
    """An upstream that fails after the headers, which §15 names and nothing covered.

    A provider that dies mid-generation has already sent 200, so it reports the
    failure as a frame and stops — no `[DONE]`, ever. Two things must survive:
    the error, because a reader that never sees it waits forever for a stream
    that has ended; and the text already delivered, because that is what the
    person is looking at while it happens.
    """
    direct = read_stream(fixtures.UPSTREAM_ERROR_MIDSTREAM)
    proxied, _ = await _through_ravis(fixtures.UPSTREAM_ERROR_MIDSTREAM)
    result.record(
        "a mid-stream upstream error reaches the client",
        proxied.error is not None,
        f"error={proxied.error}",
    )
    result.record(
        "the error is passed through unchanged",
        proxied.error == direct.error,
        f"upstream={direct.error} proxied={proxied.error}",
    )
    result.record(
        "text delivered before the failure survives it",
        proxied.text == direct.text and proxied.text != "",
        f"text={proxied.text!r}",
    )
    result.record(
        "a failed stream is not reported as complete",
        not proxied.saw_done,
        "a [DONE] appeared on a stream the upstream never finished",
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
            # A real server always populates this; the scope is hand-built here,
            # and without it the §16 item 5 Host check refuses the request and
            # this measures a 403 rather than whether the stream was buffered.
            (b"host", b"127.0.0.1"),
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
    upstream = _FixtureUpstream(fixtures.PLAIN_CHAT)
    client = httpx.AsyncClient(transport=upstream.transport())
    relay = _relay(_single_attempt(client, "fixture-model"))
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
