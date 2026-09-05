"""M3b end to end — the translated execution path (§6, Path B).

The transparent path is proven by not changing bytes. This one constructs every
frame, so what it must prove is different: that the fork is decided once and
recorded, that a client sees an ordinary OpenAI stream, that cancellation still
reaches the provider, and that the path it took is answerable from a diagnostic
rather than from reading configuration.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import httpx
from fastapi.testclient import TestClient
from tests.conftest_upstream import RecordingUpstream

from ravis.api.openai.chat import _Call, _translated_relay
from ravis.app import create_app
from ravis.config import Settings
from ravis.core.requests import NormalizedRequest, normalize
from ravis.core.responses import (
    FinishReason,
    NormalizedResponse,
    NormalizedStreamEvent,
    StreamEventType,
    Usage,
)
from ravis.reliability import AttemptChain, HealthRegistry


class FakeAnthropic:
    """A translating adapter that records what reached it.

    Stands in for M4. What matters here is the *path*, so this speaks the
    normalized vocabulary and nothing else — if the relay ever passed it an
    OpenAI envelope, every assertion below would fail rather than quietly
    working because the shapes happen to overlap.
    """

    name = "fake"
    # Configured, because these tests are about the *path* and an adapter that
    # cannot authenticate is never reached. The real check is exercised in
    # `test_credential_wiring.py`.
    has_credential = True

    def __init__(self, events: list[NormalizedStreamEvent] | None = None,
                 fail: str = "") -> None:
        self.events = events or [
            NormalizedStreamEvent(type=StreamEventType.TEXT, text="Hi."),
            NormalizedStreamEvent(type=StreamEventType.FINISH,
                                  finish_reason=FinishReason.STOP),
        ]
        self.fail = fail
        self.saw: list[NormalizedRequest] = []
        self.closed = False
        # How many of `self.events` were actually produced, as opposed to
        # merely offered. `closed` alone cannot tell a real disconnect from a
        # stream that ran to completion — `finally` fires either way — so a
        # test proving abandonment has to count what was pulled, the same way
        # `RecordingUpstream.frames_pulled` does for the transparent path.
        self.pulled = 0

    async def complete(self, request: NormalizedRequest) -> NormalizedResponse:
        self.saw.append(request)
        if self.fail:
            raise RuntimeError(self.fail)
        return NormalizedResponse(text="Hi.", finish_reason=FinishReason.STOP,
                                  usage=Usage(input_tokens=3, output_tokens=1))

    def stream(self, request: NormalizedRequest) -> AsyncGenerator[NormalizedStreamEvent, None]:
        self.saw.append(request)

        async def events() -> AsyncGenerator[NormalizedStreamEvent, None]:
            try:
                if self.fail:
                    raise RuntimeError(self.fail)
                for event in self.events:
                    self.pulled += 1
                    yield event
                    await asyncio.sleep(0)
            finally:
                # §8.6: closing the generator is how a disconnect reaches the
                # provider. Recorded so a test can prove it happened.
                self.closed = True

        return events()


def _app(adapter: FakeAnthropic | None = None) -> TestClient:
    upstream = RecordingUpstream()
    settings = Settings(database_path=":memory:", upstream_base_url="http://upstream.invalid",
                        _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings)
    fake = httpx.AsyncClient(transport=upstream.transport())
    app.app.state.upstream_client = fake
    app.app.state.model_registry.use_client(fake)
    if adapter is not None:
        app.app.state.translating = {adapter.name: adapter}
    return TestClient(app)


def _frames(body: bytes) -> list[dict[str, Any]]:
    out = []
    for line in body.split(b"\n\n"):
        if line.startswith(b"data: ") and line[6:] != b"[DONE]":
            out.append(json.loads(line[6:]))
    return out


def test_a_translated_stream_looks_like_any_other_openai_stream() -> None:
    """The client is an unmodified OpenAI SDK. If Path B were distinguishable
    from Path A on the wire, every client would need to know which it got."""
    adapter = FakeAnthropic()
    with _app(adapter) as client, client.stream(
        "POST", "/v1/chat/completions",
        json={"model": "ravis/fake/claude-x", "stream": True},
    ) as response:
        body = b"".join(response.iter_bytes())

    frames = _frames(body)
    assert frames[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert frames[1]["choices"][0]["delta"] == {"content": "Hi."}
    assert frames[-1]["choices"][0]["finish_reason"] == "stop"
    assert body.endswith(b"data: [DONE]\n\n")


def test_the_adapter_receives_a_normalized_request_not_an_envelope() -> None:
    """§6's whole point: the client's format stops at the edge."""
    adapter = FakeAnthropic()
    with _app(adapter) as client:
        client.post("/v1/chat/completions",
                    json={"model": "ravis/fake/claude-x",
                          "messages": [{"role": "user", "content": "hello"}]})

    assert isinstance(adapter.saw[0], NormalizedRequest)
    assert adapter.saw[0].messages[0]["content"] == "hello"


def test_the_path_that_ran_is_recorded() -> None:
    """§6: diagnostics must expose which path ran. When a tool call arrives
    malformed the first question is whether it went through a translation at
    all, and that must be answerable from a trace."""
    adapter = FakeAnthropic()
    with _app(adapter) as client:
        client.post("/v1/chat/completions", json={"model": "ravis/fake/claude-x"})
        translated = client.get("/api/v1/route-decisions?limit=1").json()["items"][0]

        client.post("/v1/chat/completions", json={"model": "coder-a"})
        transparent = client.get("/api/v1/route-decisions?limit=1").json()["items"][0]

    assert translated["execution_path"] == "TRANSLATED_NATIVE"
    assert transparent["execution_path"] == "TRANSPARENT_OPENAI"


def test_a_foreign_provider_owns_its_own_catalogue() -> None:
    """An Anthropic model never appears in a local runtime's `/v1/models`.
    Checking it against the wrong catalogue would refuse every Path B request
    the moment the local upstream had any models at all."""
    adapter = FakeAnthropic()
    with _app(adapter) as client:
        response = client.post("/v1/chat/completions", json={"model": "ravis/fake/claude-x"})
        unknown = client.post("/v1/chat/completions", json={"model": "ravis/lmstudio/nope"})

    assert response.status_code == 200
    # Still checked for a provider whose catalogue RAVIS *can* see.
    assert unknown.status_code == 422


def test_a_translated_response_carries_usage_and_a_finish_reason() -> None:
    adapter = FakeAnthropic()
    with _app(adapter) as client:
        body = client.post("/v1/chat/completions",
                           json={"model": "ravis/fake/claude-x"}).json()

    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 4


def test_an_adapter_that_fails_before_a_byte_does_not_pretend_to_have_answered() -> None:
    adapter = FakeAnthropic(fail="the provider refused")
    with _app(adapter) as client:
        response = client.post("/v1/chat/completions", json={"model": "ravis/fake/claude-x"})

    assert response.status_code >= 400
    assert "refused" in response.text


async def test_a_disconnect_closes_the_adapter_stream() -> None:
    """§8.6. Left to the garbage collector the provider keeps generating tokens
    nobody reads and, on a paid provider, nobody should be billed for.

    **Rewritten: the original version could not fail.** It read two chunks off
    `TestClient(...).stream(...)` and then asserted `adapter.closed is True` —
    but `closed` is set in the adapter's own `finally`, which fires whether the
    stream was abandoned early *or* ran to completion. A Path B that ignored
    the disconnect entirely and drained all fifty events would still close
    normally at the end and pass this exactly as written. Confirmed by
    instrumenting `FakeAnthropic` with a pull counter and driving it through
    the real app: reading two chunks off `TestClient` left `pulled` at 0,
    because `TestClient` hands nothing back until the whole response is
    generated — the same limitation `test_a_disconnect_stops_the_upstream_
    generation` in `test_transparent_proxy.py` documents for the transparent
    path, for the identical reason.

    Driven against `_translated_relay` directly instead, mirroring that test
    and `test_cancellation_does_not_trigger_a_fallback`: pull one frame, close
    the generator the way Starlette does on a real disconnect, and assert on
    how much of the adapter's fifty events were ever produced.
    """
    adapter = FakeAnthropic(events=[
        NormalizedStreamEvent(type=StreamEventType.TEXT, text=f"chunk {i}") for i in range(50)
    ])
    request = normalize(json.dumps({"model": "ravis/fake/claude-x", "stream": True}).encode(),
                        {"model": "ravis/fake/claude-x", "stream": True})
    request.requested_model = "ravis/fake/claude-x"
    chain = AttemptChain(health=HealthRegistry(), provider=adapter.name)
    started = chain.begin("ravis/fake/claude-x")
    call = _Call(
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500))),
        destination=lambda _: ("http://unused.invalid", {}),
        body=b"{}",
        payload={"model": "ravis/fake/claude-x", "stream": True},
        chain=chain,
        recorded=None,
    )

    relay = _translated_relay(call, adapter, request, "ravis/fake/claude-x",
                              "chatcmpl-test", started)
    # Two pulls, not one: `_translated_frames` yields RAVIS's own opening
    # frame first, before it ever touches the adapter (documented at its own
    # definition — "the opening frame ... is constructed here, before this
    # function has awaited the provider at all"). The first `__anext__` only
    # reaches that; the second is what actually enters the adapter's stream.
    await relay.__anext__()
    await relay.__anext__()
    await relay.aclose()
    await call.client.aclose()

    # The discriminating assertion: a Path B that ignored the disconnect and
    # drained the adapter would leave `pulled` at 50, not close to 0.
    assert adapter.pulled <= 1, (
        f"the adapter produced {adapter.pulled} of its 50 events after the "
        "relay was closed — a disconnect must reach the provider"
    )
    assert adapter.closed is True


class SlowAnthropic(FakeAnthropic):
    """A provider that takes a measurable moment before its first token.

    The delay is the whole point: a fake that answers instantly cannot tell a
    correct measurement from one taken before the provider was asked, which is
    exactly why the defect below survived a passing suite.
    """

    DELAY_SECONDS = 0.25

    def stream(self, request: NormalizedRequest) -> AsyncGenerator[NormalizedStreamEvent, None]:
        self.saw.append(request)

        async def events() -> AsyncGenerator[NormalizedStreamEvent, None]:
            try:
                await asyncio.sleep(self.DELAY_SECONDS)
                for event in self.events:
                    yield event
                    await asyncio.sleep(0)
            finally:
                self.closed = True

        return events()

    async def complete(self, request: NormalizedRequest) -> NormalizedResponse:
        await asyncio.sleep(self.DELAY_SECONDS)
        return await super().complete(request)


def _recorded_attempt(client: TestClient) -> dict[str, Any]:
    decisions = client.get("/api/v1/route-decisions?limit=1").json()["items"]
    return (decisions[0]["execution"]["attempts"] or [{}])[-1]


def test_a_streamed_attempt_is_timed_from_the_provider_not_from_our_own_frame() -> None:
    """The opening frame is RAVIS's, and timing from it measures nothing.

    `_translated_frames` yields an opening frame carrying the role delta before
    it has awaited the provider at all, so a relay committing on its first
    *frame* stamps the clock roughly zero microseconds after it started. Found
    through NERVIS's API Inspector: a translated call to a hosted provider
    recorded 0.19 ms end to end while the client measured 6.1 seconds to first
    byte — a figure wrong by four orders of magnitude, on the surface RAVIS
    publishes for exactly this question.
    """
    adapter = SlowAnthropic()
    with _app(adapter) as client, client.stream(
        "POST", "/v1/chat/completions",
        json={"model": "ravis/fake/claude-x", "stream": True},
    ) as answer:
        answer.read()

    attempt = _recorded_attempt(client)
    floor = SlowAnthropic.DELAY_SECONDS * 1000 * 0.8
    assert attempt["ttft_ms"] is not None and attempt["ttft_ms"] > floor, attempt
    assert attempt["elapsed_ms"] > floor, attempt


def test_a_streamed_call_is_timed_like_the_non_streamed_one_beside_it() -> None:
    """Same adapter, same delay, same order of magnitude.

    The two paths measured differently for as long as the streamed one committed
    on RAVIS's own frame: ~700 ms recorded for a completion and under a
    millisecond for a stream against the same provider on the same machine.
    """
    streamed, whole = SlowAnthropic(), SlowAnthropic()
    with _app(streamed) as client, client.stream(
        "POST", "/v1/chat/completions",
        json={"model": "ravis/fake/claude-x", "stream": True},
    ) as answer:
        answer.read()
    streamed_ms = _recorded_attempt(client)["elapsed_ms"]

    with _app(whole) as client:
        client.post("/v1/chat/completions", json={"model": "ravis/fake/claude-x"})
    whole_ms = _recorded_attempt(client)["elapsed_ms"]

    assert streamed_ms > whole_ms / 4, (streamed_ms, whole_ms)
    assert whole_ms > streamed_ms / 4, (streamed_ms, whole_ms)


def test_a_provider_that_fails_before_its_first_token_is_not_recorded_as_a_success() -> None:
    """It was, and the circuit breaker was reading it.

    Committing on the opening frame called `succeeded` for a call that had not
    yet produced a token, so a provider failing immediately afterwards was
    logged as succeeded *and* failed — and one success closes a circuit
    outright.
    """
    adapter = FakeAnthropic(fail="the provider fell over")
    with _app(adapter) as client, client.stream(
        "POST", "/v1/chat/completions",
        json={"model": "ravis/fake/claude-x", "stream": True},
    ) as answer:
        answer.read()

    outcomes = [
        one["outcome"]
        for one in client.get("/api/v1/route-decisions?limit=1").json()["items"][0]
        ["execution"]["attempts"]
    ]
    assert "succeeded" not in outcomes, outcomes
