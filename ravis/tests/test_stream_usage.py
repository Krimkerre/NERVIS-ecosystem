"""A streamed call must say what it used, even when the client never asked.

Found live on 16 September 2026 (STATUS.md, "RAVIS 0.28.2"): the first OpenAI
calls RAVIS ever recorded, two NERVIS chat turns on `gpt-4.1-2025-04-14`, were
written down with no token counts and no cost. OpenAI streams a call's usage
only to a request that set `stream_options.include_usage`, LM Studio does the
same (three of its four records had no counts), and neither NERVIS nor Clarvis
sets it. §14's budget reads an uncounted call as nothing spent.

RAVIS now asks on the client's behalf whenever the client did not say either
way. These tests use an upstream that behaves as OpenAI documents: counts only
when asked, in a last chunk whose `choices` is empty, with `"usage": null` on
the chunks before it.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.test_fallback import ScriptedUpstream, _app_with, _Frames

from ravis.cost import CostState, Price, PriceBook

MODEL = "talker"
CATALOGUE: dict[str, dict[str, str]] = {MODEL: {"context_window": "32000"}}
ASK: dict[str, Any] = {
    "model": MODEL, "stream": True, "messages": [{"role": "user", "content": "hi"}],
}


def _frame(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _openai_stream(model: str, asked: bool) -> Iterator[bytes]:
    """OpenAI's stream shape, with and without `include_usage`."""
    nulls: dict[str, Any] = {"usage": None} if asked else {}
    head = {"id": "c1", "object": "chat.completion.chunk", "model": model}
    yield _frame({**head, "choices": [{"index": 0, "delta": {"content": "pong"},
                                       "finish_reason": None}], **nulls})
    yield _frame({**head, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                  **nulls})
    if asked:
        yield _frame({**head, "choices": [],
                      "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13}})
    yield b"data: [DONE]\n\n"


class UsageOnRequest(ScriptedUpstream):
    """An upstream that counts a streamed call only when asked, like OpenAI.

    `strict` models refuse `stream_options` outright, in OpenAI's wording for a
    field it does not know — the shape a stricter OpenAI-compatible server
    could take.
    """

    def __init__(self, strict: frozenset[str] = frozenset()) -> None:
        super().__init__(CATALOGUE)
        self.strict = strict

    def _handle(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        if "messages" not in payload or not payload.get("stream"):
            return super()._handle(request)
        model = payload.get("model", "")
        self.served.append(model)
        self.bodies.append(payload)
        if model in self.strict and "stream_options" in payload:
            return httpx.Response(400, json={"error": {
                "message": "Unrecognized request argument supplied: stream_options",
                "type": "invalid_request_error", "param": None, "code": None,
            }})
        options = payload.get("stream_options")
        asked = isinstance(options, dict) and options.get("include_usage") is True
        return httpx.Response(200, stream=_Frames(_openai_stream(model, asked)))


def _priced(client: TestClient) -> None:
    client.app.app.state.prices.state(  # type: ignore[attr-defined]
        MODEL, Price(input_per_million=2.0, output_per_million=8.0)
    )


def _records(client: TestClient) -> list[Any]:
    ledger = client.app.app.state.usage_ledger  # type: ignore[attr-defined]
    return [record for record in ledger.recent(50) if record.model == MODEL]


# ── Asking on the client's behalf ────────────────────────────────────────────


def test_a_stream_that_did_not_say_is_asked_and_counted() -> None:
    upstream = UsageOnRequest()
    with _app_with(upstream) as client:
        _priced(client)
        reply = client.post("/v1/chat/completions", json=ASK)
        assert reply.status_code == 200, reply.text
        records = _records(client)

    assert upstream.bodies[-1]["stream_options"] == {"include_usage": True}
    # The client gets the answer, and the one extra frame OpenRouter already
    # sends everyone: empty choices, carrying the counts.
    assert b'"pong"' in reply.content and reply.content.rstrip().endswith(b"[DONE]")
    assert b'"choices": [], "usage"' in reply.content
    usage = records[-1].usage
    assert usage is not None and (usage.input_tokens, usage.output_tokens) == (12, 1), (
        "the call was recorded without its counts, which a budget reads as nothing spent"
    )
    assert records[-1].cost_state is not CostState.UNKNOWN
    assert records[-1].cost is not None and records[-1].cost > 0


def test_the_clients_own_stream_options_are_kept_beside_it() -> None:
    upstream = UsageOnRequest()
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json={
            **ASK, "stream_options": {"include_obfuscation": False},
        })
        assert reply.status_code == 200, reply.text
    assert upstream.bodies[-1]["stream_options"] == {
        "include_obfuscation": False, "include_usage": True,
    }


@pytest.mark.parametrize("said", [True, False])
def test_a_client_that_said_either_way_is_left_alone(said: bool) -> None:
    """Its body reaches the upstream exactly as it was sent."""
    upstream = UsageOnRequest()
    sent = {**ASK, "stream_options": {"include_usage": said}}
    with _app_with(upstream) as client:
        assert client.post("/v1/chat/completions", json=sent).status_code == 200
    assert upstream.bodies[-1] == sent


def test_a_request_that_does_not_stream_is_not_touched() -> None:
    """A whole response carries its usage anyway, and `stream_options` is refused
    by some servers on a request that does not stream."""
    upstream = UsageOnRequest()
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json={**ASK, "stream": False})
        assert reply.status_code == 200, reply.text
    assert "stream_options" not in upstream.bodies[-1]


def test_a_malformed_stream_options_is_the_clients_business() -> None:
    upstream = UsageOnRequest()
    sent = {**ASK, "stream_options": "yes please"}
    with _app_with(upstream) as client:
        client.post("/v1/chat/completions", json=sent)
    assert upstream.bodies[-1]["stream_options"] == "yes please"


# ── An upstream that will not take the field ─────────────────────────────────


def test_an_upstream_refusing_the_field_ravis_added_answers_without_it() -> None:
    upstream = UsageOnRequest(strict=frozenset({MODEL}))
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json=ASK)
        assert reply.status_code == 200, reply.text
        assert b'"pong"' in reply.content
        assert upstream.served == [MODEL, MODEL]
        assert "stream_options" not in upstream.bodies[1]

        # Remembered: the next call does not ask, so it is not refused first.
        assert client.post("/v1/chat/completions", json=ASK).status_code == 200
        assert upstream.served == [MODEL] * 3
        assert "stream_options" not in upstream.bodies[2]


def test_a_refused_field_the_client_sent_is_never_dropped() -> None:
    """The client asked for the counts; answering without them is not an answer
    to what it asked, so this model's refusal stands."""
    upstream = UsageOnRequest(strict=frozenset({MODEL}))
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json={
            **ASK, "stream_options": {"include_usage": True},
        })
    assert upstream.served == [MODEL], "no retry without the client's own field"
    assert b"stream_options" in reply.content


# ── Pricing a dated build ────────────────────────────────────────────────────


@pytest.mark.parametrize("dated,undated", [
    ("gpt-4o-2024-08-06", "gpt-4o"),
    ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
])
def test_a_dated_build_is_priced_under_its_undated_name(dated: str, undated: str) -> None:
    """OpenAI dates a build `-2024-08-06`, Anthropic `-20251001`; both reach the rate
    an operator wrote under the name the vendor's pricing page uses."""
    book = PriceBook()
    rate = Price(input_per_million=2.5, output_per_million=10.0)
    book.state(undated, rate)
    found = book.price_of(dated)
    assert found is not None and found.input_per_million == rate.input_per_million
    assert book.price_of("gpt-4o-2024") is None, "a partial date is not a date"


def test_learning_that_a_model_refuses_ravis_field_never_strips_a_clients() -> None:
    """What RAVIS learned about its own addition is not licence to edit a client's
    request: a later client that asks for the counts itself still sends them."""
    upstream = UsageOnRequest(strict=frozenset({MODEL}))
    with _app_with(upstream) as client:
        assert client.post("/v1/chat/completions", json=ASK).status_code == 200
        client.post("/v1/chat/completions", json={
            **ASK, "stream_options": {"include_usage": True},
        })
    assert upstream.bodies[-1]["stream_options"] == {"include_usage": True}


def test_a_refusal_of_options_the_client_sent_is_not_retried() -> None:
    """RAVIS merged its request into the client's `stream_options`, but the field
    is still the client's: leaving it out is not RAVIS's to do, so asking the same
    model again would only be refused again."""
    upstream = UsageOnRequest(strict=frozenset({MODEL}))
    with _app_with(upstream) as client:
        client.post("/v1/chat/completions", json={
            **ASK, "stream_options": {"include_obfuscation": False},
        })
    assert upstream.served == [MODEL], "a second, identical refusal was asked for"
