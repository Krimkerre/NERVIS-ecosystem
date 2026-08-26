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

from ravis.app import create_app
from ravis.config import Settings
from ravis.core.requests import NormalizedRequest
from ravis.core.responses import (
    FinishReason,
    NormalizedResponse,
    NormalizedStreamEvent,
    StreamEventType,
    Usage,
)


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


def test_a_disconnect_closes_the_adapter_stream() -> None:
    """§8.6. Left to the garbage collector the provider keeps generating tokens
    nobody reads and, on a paid provider, nobody should be billed for."""
    adapter = FakeAnthropic(events=[
        NormalizedStreamEvent(type=StreamEventType.TEXT, text=f"chunk {i}") for i in range(50)
    ])
    with _app(adapter) as client, client.stream(
        "POST", "/v1/chat/completions",
        json={"model": "ravis/fake/claude-x", "stream": True},
    ) as response:
        for _ in zip(range(2), response.iter_bytes()):
            pass

    assert adapter.closed is True
