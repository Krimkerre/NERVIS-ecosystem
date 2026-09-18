"""`/v1/responses`, translated into the path RAVIS already routes (M23).

Two claims, and the acceptance criterion is the second: the surface works for
what it says it supports, and Chat Completions is unchanged. Everything this
translation does not do is refused by name rather than accepted and ignored —
which is the part worth testing hardest, because a silent omission is a request
the caller made and never learns was dropped.
"""

from __future__ import annotations

import json
from typing import Any

from tests.test_fallback import ScriptedUpstream, _app_with

TALKER: dict[str, dict[str, str]] = {
    "talker": {"context_window": "32000"},
    "coder": {"tools": "SUPPORTED", "structured_output": "SUPPORTED",
              "context_window": "131072"},
}
ANSWER: dict[str, Any] = {
    "id": "c1",
    "created": 1_700_000_000,
    "model": "talker",
    "choices": [{"index": 0, "finish_reason": "stop",
                 "message": {"role": "assistant", "content": "hello there"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
}
CALLED: dict[str, Any] = {
    "id": "c2",
    "created": 1_700_000_000,
    "model": "coder",
    "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "read_file",
                                     "arguments": '{"path":"a.txt"}'}}],
    }}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
}
TOOL = {"type": "function", "name": "read_file", "description": "read one file",
        "parameters": {"type": "object", "properties": {}}}


def test_a_plain_request_comes_back_in_the_responses_shape() -> None:
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses", json={"model": "talker", "input": "hi"}).json()

    assert answer["object"] == "response"
    assert answer["status"] == "completed"
    assert answer["output_text"] == "hello there"
    assert answer["output"][0]["content"][0] == {
        "type": "output_text", "text": "hello there", "annotations": []
    }
    assert answer["usage"] == {"input_tokens": 11, "output_tokens": 2, "total_tokens": 13}


def test_instructions_become_the_system_message_and_lead() -> None:
    """A standing instruction after the conversation is a different request."""
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        client.post("/v1/responses", json={
            "model": "talker", "instructions": "be brief", "input": "hi",
        })

    sent = upstream.bodies[-1]["messages"]
    assert sent[0] == {"role": "system", "content": "be brief"}
    assert sent[1] == {"role": "user", "content": "hi"}


def test_a_tool_is_renested_and_its_call_comes_back_as_an_output_item() -> None:
    """The two shapes differ only in nesting, and the call id is the caller's handle."""
    upstream = ScriptedUpstream(TALKER, answers={"coder": CALLED})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses", json={
            "model": "coder", "input": "read a.txt", "tools": [TOOL],
        }).json()

    assert upstream.bodies[-1]["tools"][0]["function"]["name"] == "read_file"
    called = answer["output"][0]
    assert called["type"] == "function_call"
    assert (called["name"], called["call_id"]) == ("read_file", "call_1")
    assert json.loads(called["arguments"]) == {"path": "a.txt"}


def test_a_function_result_is_sent_back_as_a_tool_message() -> None:
    """The round trip: what came out as `function_call` goes back in as its output."""
    upstream = ScriptedUpstream(TALKER, answers={"coder": CALLED})
    with _app_with(upstream) as client:
        client.post("/v1/responses", json={
            "model": "coder",
            "input": [
                {"type": "message", "role": "user", "content": "read a.txt"},
                {"type": "function_call", "call_id": "call_1", "name": "read_file",
                 "arguments": '{"path":"a.txt"}'},
                {"type": "function_call_output", "call_id": "call_1", "output": "hello"},
            ],
            "tools": [TOOL],
        })

    sent = upstream.bodies[-1]["messages"]
    assert sent[1]["tool_calls"][0]["id"] == "call_1"
    assert sent[2] == {"role": "tool", "tool_call_id": "call_1", "content": "hello"}


def test_a_schema_and_a_reasoning_effort_are_carried_across() -> None:
    upstream = ScriptedUpstream(TALKER, answers={"coder": ANSWER})
    with _app_with(upstream) as client:
        client.post("/v1/responses", json={
            "model": "coder",
            "input": "hi",
            "max_output_tokens": 64,
            "reasoning": {"effort": "low"},
            "text": {"format": {"type": "json_schema", "name": "reply",
                                "schema": {"type": "object"}, "strict": True}},
        })

    sent = upstream.bodies[-1]
    assert sent["max_tokens"] == 64
    assert sent["reasoning_effort"] == "low"
    assert sent["response_format"]["json_schema"]["name"] == "reply"


def test_a_declared_privacy_binds_on_this_surface_too() -> None:
    """§14 arrives in `metadata`, and a surface that dropped it would be a
    quietly more permissive way into the same gateway.

    The upstream in this test is not on this machine, so `LOCAL_ONLY` has
    nowhere to go — and being refused is the proof the declaration was read. A
    surface that discarded `metadata` would have answered happily.
    """
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses", json={
            "model": "ravis/auto", "input": "hi",
            "metadata": {"background": True, "privacy": "LOCAL_ONLY"},
        })

    assert answer.status_code >= 400
    assert upstream.served == [], "a request that must stay local reached a remote provider"


def test_metadata_reaches_the_router_unchanged() -> None:
    """The same fact from the other side, on a request that does route."""
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        client.post("/v1/responses", json={
            "model": "talker", "input": "hi", "metadata": {"background": True},
        })

    assert upstream.bodies[-1]["metadata"] == {"background": True}


def test_a_capped_answer_is_reported_as_incomplete() -> None:
    upstream = ScriptedUpstream(TALKER, answers={"talker": {
        **ANSWER, "choices": [{"index": 0, "finish_reason": "length",
                               "message": {"role": "assistant", "content": "hel"}}],
    }})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses",
                             json={"model": "talker", "input": "hi"}).json()

    assert answer["status"] == "incomplete"
    assert answer["incomplete_details"] == {"reason": "max_output_tokens"}


# ── What is refused, by name ─────────────────────────────────────────────────


def test_a_streamed_request_is_refused_and_says_where_streaming_works() -> None:
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses",
                             json={"model": "talker", "input": "hi", "stream": True})

    assert answer.status_code == 400
    assert answer.json()["error"]["param"] == "stream"
    assert "/v1/chat/completions" in answer.json()["error"]["message"]
    assert upstream.served == [], "a refused request must not reach a provider"


def test_asking_to_continue_an_earlier_response_is_refused() -> None:
    """RAVIS keeps no conversation, so there is nothing to continue from."""
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses", json={
            "model": "talker", "input": "hi", "previous_response_id": "resp_1",
        })

    assert answer.status_code == 400
    assert answer.json()["error"]["param"] == "previous_response_id"


def test_asking_for_the_answer_to_be_stored_is_refused() -> None:
    """The refusal that matters most: accepted and ignored, the caller only
    finds out when they try to read the answer back."""
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses",
                             json={"model": "talker", "input": "hi", "store": True})

    assert answer.status_code == 400
    assert answer.json()["error"]["param"] == "store"


def test_store_false_is_not_a_request_for_anything() -> None:
    """The ordinary case, and the one a blunt "is the key present" check breaks."""
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses", json={
            "model": "talker", "input": "hi", "store": False, "stream": False,
        })

    assert answer.status_code == 200


def test_a_provider_run_tool_is_refused_rather_than_forwarded() -> None:
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses", json={
            "model": "talker", "input": "hi", "tools": [{"type": "web_search"}],
        })

    assert answer.status_code == 400
    assert "web_search" in answer.json()["error"]["message"]


def test_a_route_refusal_reaches_the_caller_as_it_was_written() -> None:
    """An error is not reshaped: the router's own sentence is the useful part."""
    # A pool whose invariants nothing in this catalogue satisfies: `talker`
    # cannot call tools, and the agent pool requires them.
    upstream = ScriptedUpstream({"talker": TALKER["talker"]})
    with _app_with(upstream) as client:
        answer = client.post("/v1/responses",
                             json={"model": "ravis/clarvis-agent", "input": "hi"})

    assert answer.status_code >= 400
    assert "error" in answer.json()
    assert upstream.served == []


# ── No regression in Chat Completions, which is M23's acceptance criterion ───


def test_chat_completions_still_answers_exactly_as_before() -> None:
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        answer = client.post("/v1/chat/completions",
                             json={"model": "talker",
                                   "messages": [{"role": "user", "content": "hi"}]})

    assert answer.status_code == 200
    assert answer.json()["choices"][0]["message"]["content"] == "hello there"


def test_a_responses_request_is_routed_and_recorded_like_any_other() -> None:
    """One gateway: the decision log, the ledger and the policy see one kind of
    request, whichever surface it arrived on."""
    upstream = ScriptedUpstream(TALKER, answers={"talker": ANSWER})
    with _app_with(upstream) as client:
        client.post("/v1/responses", json={"model": "talker", "input": "hi"})
        decisions = client.get("/api/v1/route-decisions?limit=5").json()["items"]
        usage = client.get("/api/v1/usage").json()

    assert decisions and decisions[0]["selected"] == "talker"
    assert usage["executed"] >= 1
