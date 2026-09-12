"""M4 over a wire — the adapter, and one real OpenAI client through Anthropic.

The translation is proven next door without a socket. What is left is what only
a client can show: that the headers are Anthropic's rather than OpenAI's, that a
provider error reaches the caller without carrying an internal address, that a
disconnect stops the provider generating, and that M4's acceptance criterion
holds — *an OpenAI client works through Anthropic*, with no way to tell from the
wire which of §6's two paths ran.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.conftest_upstream import RecordingAnthropic, RecordingUpstream

from ravis.app import create_app
from ravis.config import Settings
from ravis.core.capabilities import Capability, CapabilityState, Provenance
from ravis.core.requests import NormalizedRequest
from ravis.core.responses import FinishReason, StreamEventType
from ravis.providers.anthropic import AnthropicAdapter, AnthropicUpstreamError
from ravis.upstream import Upstream

ADDRESS = "ravis/anthropic/claude-opus-5"


def adapter_for(fake: RecordingAnthropic, **options: Any) -> AnthropicAdapter:
    """The adapter, pointed at an in-process Anthropic."""
    return AnthropicAdapter(
        upstream=Upstream(base_url="https://anthropic.invalid", declared_key="sk-test"),
        client=httpx.AsyncClient(transport=fake.transport()),
        **options,
    )


def request_for(**fields: Any) -> NormalizedRequest:
    fields.setdefault("messages", [{"role": "user", "content": "Hi"}])
    fields.setdefault("requested_model", "claude-opus-5")
    return NormalizedRequest(**fields)


# ── The call ─────────────────────────────────────────────────────────────────


def test_a_completion_is_translated_both_ways() -> None:
    fake = RecordingAnthropic()

    answer = asyncio.run(adapter_for(fake).complete(request_for()))

    assert fake.body_of()["messages"] == [{"role": "user", "content": "Hi"}]
    assert answer.text == "Hello."
    assert answer.finish_reason is FinishReason.STOP
    assert answer.usage.input_tokens == 12
    assert answer.provider == "anthropic"
    assert answer.latency_ms is not None


def test_the_credential_is_anthropics_header_not_openais() -> None:
    """`x-api-key` and `authorization: Bearer` are not interchangeable here."""
    fake = RecordingAnthropic()

    asyncio.run(adapter_for(fake).complete(request_for()))

    headers = fake.requests[-1].headers
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in headers


def test_a_provider_error_reaches_the_caller_without_an_internal_address() -> None:
    """This message is what a client sees when the chain is exhausted (§9.7)."""
    fake = RecordingAnthropic(
        status=429,
        error={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
    )

    with pytest.raises(AnthropicUpstreamError) as raised:
        asyncio.run(adapter_for(fake).complete(request_for()))

    assert "slow down" in str(raised.value)
    assert "anthropic.invalid" not in str(raised.value)


def test_an_error_before_the_stream_starts_raises_rather_than_yielding() -> None:
    """The relay's fallback boundary depends on nothing having been committed."""
    fake = RecordingAnthropic(
        status=529,
        error={"type": "error", "error": {"type": "overloaded_error", "message": "overloaded"}},
    )

    async def drain() -> None:
        async for _ in adapter_for(fake).stream(request_for(stream=True)):
            pytest.fail("a failed stream must not yield an event")

    with pytest.raises(AnthropicUpstreamError, match="overloaded"):
        asyncio.run(drain())


def test_a_stream_is_normalized_in_order() -> None:
    fake = RecordingAnthropic()

    async def collect() -> list[Any]:
        return [event async for event in adapter_for(fake).stream(request_for(stream=True))]

    events = asyncio.run(collect())

    assert [event.type for event in events] == [
        StreamEventType.TEXT,
        StreamEventType.TOOL_CALL_FRAGMENT,
        StreamEventType.TOOL_CALL_FRAGMENT,
        StreamEventType.TOOL_CALL_FRAGMENT,
        StreamEventType.FINISH,
        StreamEventType.USAGE,
    ]
    assert fake.body_of()["stream"] is True


def test_closing_the_stream_stops_the_provider_generating() -> None:
    """§8.6: a disconnect must reach the provider, or a paid one keeps billing."""
    fake = RecordingAnthropic()

    async def take_one() -> None:
        events = adapter_for(fake).stream(request_for(stream=True))
        async for _ in events:
            break
        await events.aclose()

    asyncio.run(take_one())

    assert fake.frames_pulled < len(fake.frames), "the upstream kept producing after the close"


# ── Discovery ────────────────────────────────────────────────────────────────


def test_capabilities_record_what_the_catalogue_advertises() -> None:
    fake = RecordingAnthropic()

    known = asyncio.run(adapter_for(fake).capabilities("claude-opus-5"))

    assert known.context_window == 1000000
    assert known.max_output_tokens == 128000
    assert known.state_of(Capability.VISION) is CapabilityState.SUPPORTED
    assert known.claims[Capability.VISION].provenance is Provenance.ADVERTISED
    assert known.state_of(Capability.STREAMING) is CapabilityState.SUPPORTED


def test_tool_support_stays_unknown_when_the_catalogue_is_silent() -> None:
    """§5.1's invariant is why: a guess here routes an agent on an assumption."""
    fake = RecordingAnthropic()

    known = asyncio.run(adapter_for(fake).capabilities("claude-opus-5"))

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN
    assert not known.satisfies(Capability.TOOLS)


def test_an_operator_declaration_outranks_the_catalogue() -> None:
    """§9.5: an operator knows things about their deployment RAVIS cannot see."""
    fake = RecordingAnthropic()
    adapter = adapter_for(
        fake, configured_capabilities={"claude-opus-5": {"tools": "SUPPORTED"}}
    )

    known = asyncio.run(adapter.capabilities("claude-opus-5"))

    assert known.satisfies(Capability.TOOLS)
    assert known.claims[Capability.TOOLS].provenance is Provenance.CONFIGURED


def test_an_unreachable_catalogue_leaves_the_model_at_its_defaults() -> None:
    """Discovery being down is not evidence about the model."""
    fake = RecordingAnthropic(status=500, error={"type": "error", "error": {"message": "down"}})

    known = asyncio.run(adapter_for(fake).capabilities("claude-opus-5"))

    assert known.state_of(Capability.TEXT) is CapabilityState.SUPPORTED
    assert known.state_of(Capability.VISION) is CapabilityState.UNKNOWN
    assert known.context_window is None


def test_models_and_health_answer_from_the_catalogue() -> None:
    fake = RecordingAnthropic()
    adapter = adapter_for(fake)

    assert asyncio.run(adapter.models()) == ["claude-opus-5"]
    assert asyncio.run(adapter.health()).reachable


def test_an_unconfigured_provider_is_empty_rather_than_broken() -> None:
    """Runbook §14.4: no credential is a state, not a failure."""
    adapter = AnthropicAdapter(
        upstream=Upstream(base_url="", declared_key=""),
        client=httpx.AsyncClient(transport=RecordingAnthropic().transport()),
    )

    assert asyncio.run(adapter.models()) == []
    assert not asyncio.run(adapter.health()).reachable


def test_cost_is_unknown_rather_than_free() -> None:
    """§14: a zero on a paid provider would be a lie with a currency attached."""
    assert asyncio.run(adapter_for(RecordingAnthropic()).estimate_cost(request_for())) is None


# ── An OpenAI client, through Anthropic — M4's acceptance criterion ──────────


def app_with(fake: RecordingAnthropic) -> TestClient:
    """RAVIS with a configured Anthropic provider and a fake local upstream."""
    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://upstream.invalid",
        anthropic_api_key="sk-test",
        anthropic_base_url="https://anthropic.invalid",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    local = httpx.AsyncClient(transport=RecordingUpstream().transport())
    app.app.state.upstream_client = local
    app.app.state.model_registry.use_client(local)
    # The provider adapter reaches its own upstream, which is a different fake:
    # registering it here rather than swapping one shared client keeps the two
    # upstreams distinguishable, which is the whole point of a translated path.
    app.app.state.translating = {
        "anthropic": adapter_for(fake, max_output_tokens=16000),
    }
    return TestClient(app)


def test_an_openai_completion_request_works_through_anthropic() -> None:
    fake = RecordingAnthropic()

    with app_with(fake) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": ADDRESS, "messages": [{"role": "user", "content": "Hi"}]},
        )

    body = response.json()
    assert response.status_code == 200
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "Hello."
    assert body["choices"][0]["finish_reason"] == "stop"
    # The provider was addressed by its bare model name, not by RAVIS's address.
    assert fake.body_of()["model"] == "claude-opus-5"


def test_an_openai_streaming_client_cannot_tell_which_path_ran() -> None:
    """§8: Clarvis must not be able to detect that an intermediary translated."""
    fake = RecordingAnthropic()

    with app_with(fake) as client, client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": ADDRESS, "stream": True, "messages": [{"role": "user", "content": "Hi"}]},
    ) as response:
        body = b"".join(response.iter_bytes())

    frames = _frames(body)
    assert frames[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert frames[1]["choices"][0]["delta"] == {"content": "Editing."}
    assert body.endswith(b"data: [DONE]\n\n")


def test_a_streamed_tool_call_opens_once_and_then_only_grows() -> None:
    """§8.3's fragment rule, all the way from Anthropic's wire to Clarvis's."""
    fake = RecordingAnthropic()

    with app_with(fake) as client, client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": ADDRESS, "stream": True, "messages": [{"role": "user", "content": "Hi"}]},
    ) as response:
        body = b"".join(response.iter_bytes())

    calls = [
        frame["choices"][0]["delta"]["tool_calls"][0]
        for frame in _frames(body)
        if frame["choices"] and frame["choices"][0]["delta"].get("tool_calls")
    ]
    assert calls[0]["id"] == "toolu_9"
    assert calls[0]["function"]["name"] == "edit_file"
    assert all("id" not in call for call in calls[1:]), "only the first frame opens the call"
    assert json.loads("".join(call["function"]["arguments"] for call in calls)) == {
        "path": "foo.ts"
    }


def test_the_recorded_path_says_the_request_was_translated() -> None:
    """§6: which path ran must be answerable from a trace, not from config."""
    fake = RecordingAnthropic()

    with app_with(fake) as client:
        client.post(
            "/v1/chat/completions",
            json={"model": ADDRESS, "messages": [{"role": "user", "content": "Hi"}]},
        )
        decisions = client.get("/api/v1/route-decisions").json()

    assert decisions["items"][0]["execution_path"] == "TRANSLATED_NATIVE"


def test_a_refused_translation_does_not_count_against_the_provider() -> None:
    """One client's untranslatable body must not take Anthropic offline.

    The request ends with an assistant message — a prefill — so RAVIS refuses it
    before sending. What matters as much as the refusal is the *class*: an
    invalid request, which opens no circuit and marks no provider unhealthy.
    """
    fake = RecordingAnthropic()

    with app_with(fake) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": ADDRESS,
                "messages": [
                    {"role": "user", "content": "Write a haiku"},
                    {"role": "assistant", "content": "Silent"},
                ],
            },
        )
        health = client.app.app.state.health.snapshot()

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "prefill" in response.json()["error"]["message"]
    # Generation requests, not discovery. Routing reads the Anthropic catalogue
    # now — that is what makes an Anthropic model selectable by a pool at all —
    # so the provider does see cached GETs against /v1/models. The invariant
    # here has always been about the *generation*: a request RAVIS refused must
    # never be sent to be generated.
    generations = [r for r in fake.requests if r.method == "POST"]
    assert generations == [], "a refused request must never reach the provider"
    assert all(entry["consecutive_failures"] == 0 for entry in health), (
        "a request RAVIS refused must leave no mark on the provider it never reached"
    )


def _frames(body: bytes) -> list[dict[str, Any]]:
    return [
        json.loads(line[6:])
        for line in body.split(b"\n\n")
        if line.startswith(b"data: ") and line[6:] != b"[DONE]"
    ]


# ── §10: a corrupt response, on the path that has to parse one ───────────────


def _corrupt() -> httpx.MockTransport:
    """An edge device answering 200 with an HTML error page.

    Not imagined: this is what a CDN or a corporate proxy in front of a provider
    returns when it, rather than the provider, decides to refuse.
    """
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"<html><body>502 Bad Gateway</body></html>",
                              headers={"content-type": "application/json"})

    return httpx.MockTransport(handle)


def test_a_corrupt_provider_body_is_a_refusal_rather_than_a_crash() -> None:
    """**The transparent path forwards what it cannot parse; this one must parse
    it, and that is the whole difference.** `complete()` calls `response.json()`
    on a 200 and a `json.JSONDecodeError` from a provider's edge device is not a
    RAVIS defect the caller should see as one.

    What §10 asks here is that the failure be *truthful*: a structured error
    naming the upstream, not a traceback and not a 200 with an empty answer.
    """
    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://upstream.invalid",
        anthropic_api_key="sk-test",
        anthropic_base_url="https://anthropic.invalid",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    local = httpx.AsyncClient(transport=RecordingUpstream().transport())
    app.app.state.upstream_client = local
    app.app.state.model_registry.use_client(local)
    app.app.state.translating = {
        "anthropic": AnthropicAdapter(
            upstream=Upstream(base_url="https://anthropic.invalid", declared_key="sk-test"),
            client=httpx.AsyncClient(transport=_corrupt()),
        ),
    }

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": ADDRESS, "messages": [{"role": "user", "content": "Hi"}]},
        )

    # Measured, not assumed: 502 with §4.5's envelope, and the attempt chain
    # carrying the parse failure as the reason rather than swallowing it.
    assert response.status_code == 502
    failure = response.json()["error"]
    assert failure["type"] == "upstream_error"
    attempt = failure["route"]["attempts"][0]
    assert attempt["outcome"] == "unknown", "a body nobody could read is not a known failure"
    assert "Expecting value" in attempt["detail"], "the parse failure was not reported at all"


# ── §10: a credential that has stopped working ───────────────────────────────


def test_a_refused_key_is_not_reported_as_an_unreachable_provider() -> None:
    """**RAVIS holds the expiring credentials and could not say one had
    expired.** Every HTTP failure in the health probe became `reachable=False`
    with the exception's class name as the detail, so a 401 and a dead socket
    produced the same row: *not answering*. They are different afternoons —
    one is "start the service", the other is "the key rotated" — and §10 asks
    the reading to be truthful, not merely non-crashing.
    """
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})

    adapter = AnthropicAdapter(
        upstream=Upstream(base_url="https://anthropic.invalid", declared_key="sk-expired"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )

    health = asyncio.run(adapter.health())

    assert health.credential_rejected is True
    assert health.reachable is True, "the provider answered; it was the key that was refused"
    assert "401" in health.detail


def test_a_provider_that_is_actually_down_is_still_reported_down() -> None:
    """The falsifier. Making a refused key readable must not make every failure
    read as one — a transport error has no status code and no credential in it."""
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nothing listening", request=request)

    adapter = AnthropicAdapter(
        upstream=Upstream(base_url="https://anthropic.invalid", declared_key="sk-test"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )

    health = asyncio.run(adapter.health())

    assert health.reachable is False
    assert health.credential_rejected is False


def test_a_server_error_from_a_provider_keeps_its_status() -> None:
    """The middle case, which the old code also lost: a 503 became
    `HTTPStatusError`, which tells a reader nothing about whether to wait."""
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503, json={"error": "overloaded"})

    adapter = AnthropicAdapter(
        upstream=Upstream(base_url="https://anthropic.invalid", declared_key="sk-test"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )

    health = asyncio.run(adapter.health())

    assert health.reachable is False
    assert health.detail == "HTTP 503"


def test_the_providers_listing_publishes_the_refused_credential() -> None:
    """The state has to reach a screen, or it is a field nobody sees. Published
    as its own key rather than left in `detail`, because `detail` is prose and
    will be reworded by somebody who does not know it is being parsed."""
    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})

    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://upstream.invalid",
        anthropic_api_key="sk-expired",
        anthropic_base_url="https://anthropic.invalid",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    local = httpx.AsyncClient(transport=RecordingUpstream().transport())
    app.app.state.upstream_client = local
    app.app.state.model_registry.use_client(local)
    app.app.state.translating = {
        "anthropic": AnthropicAdapter(
            upstream=Upstream(base_url="https://anthropic.invalid", declared_key="sk-expired"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        ),
    }

    with TestClient(app) as client:
        rows = client.get("/api/v1/providers").json()["items"]

    refused = [row for row in rows if row.get("credential_rejected")]
    assert [row["provider"] for row in refused] == ["anthropic"]
    assert refused[0]["reachable"] is True
    assert "401" in refused[0]["detail"]


# ── A failed listing is remembered, briefly ──────────────────────────────────
#
# A successful listing was cached for five minutes and a failed one not at all, so
# a missing or refused key made every routed request fetch it again — 165 ms of a
# 173 ms route when `tools/load_test.py` first ran (RAVIS.md §9.8).


def _refusing_anthropic(clock: list[float], answers: list[int]) -> tuple[Any, list[str]]:
    """An adapter whose listing answers each status in `answers` in turn."""
    import httpx

    from ravis.providers.anthropic import AnthropicAdapter
    from ravis.upstream import Upstream

    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        status = answers[min(len(asked), len(answers)) - 1]
        listing = {"data": [{"id": "claude-test", "type": "model"}]}
        return httpx.Response(status, json=listing if status == 200 else {"error": "no"})

    adapter = AnthropicAdapter(
        upstream=Upstream(base_url="https://anthropic.invalid", declared_key="k"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        clock=lambda: clock[0],
    )
    return adapter, asked


async def test_a_failed_anthropic_listing_is_asked_for_once_per_retry_window() -> None:
    from ravis.providers.anthropic import FAILED_DISCOVERY_RETRY_SECONDS

    clock = [1000.0]
    adapter, asked = _refusing_anthropic(clock, [401])

    for _ in range(50):
        assert await adapter.models() == []
    assert len(asked) == 1, "fifty routes must cost one refused listing"

    clock[0] += FAILED_DISCOVERY_RETRY_SECONDS
    await adapter.models()
    assert len(asked) == 2


async def test_a_anthropic_listing_that_recovers_is_listed_once_the_window_passes() -> None:
    from ravis.providers.anthropic import FAILED_DISCOVERY_RETRY_SECONDS

    clock = [1000.0]
    adapter, asked = _refusing_anthropic(clock, [401, 200])
    assert await adapter.models() == []

    clock[0] += FAILED_DISCOVERY_RETRY_SECONDS
    assert await adapter.models() == ['claude-test']
    assert await adapter.models() == ['claude-test']
    assert len(asked) == 2, "a recovered listing is cached again like any success"
