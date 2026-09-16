"""A setting one model refuses, and an exploration pick that fails, must not cost a turn.

Found live on 16 September 2026 (STATUS.md, "NERVIS 0.34.3"): a NERVIS chat
turn, exploring, was sent to `gpt-4.1-2025-04-14`, and OpenAI answered 400 with
"Unrecognized request arguments supplied: explore, explore_prefer_unmeasured,
reasoning_effort". RAVIS read that as an invalid request, decided another model
would fail the same way, and stopped — while the same body, a moment later,
was answered by another model. Three faults in one refusal:

- RAVIS forwarded its own routing switches (`explore…`) to the upstream;
- OpenAI's wording for an unknown field was not recognised as one model's
  objection to a setting, so no other model was tried;
- a model RAVIS picked only to measure it could fail the person's turn.

These tests drive real requests through the real application with only the
upstream replaced, and read what happened from the upstream itself.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.test_fallback import ScriptedUpstream, _app_with

from ravis.reliability.failures import FailureClass, classify_response
from ravis.reliability.parameters import droppable, refused_parameters

# OpenAI's own words from the live refusal, verbatim.
LIVE_REFUSAL = (
    "Unrecognized request arguments supplied: explore, explore_prefer_unmeasured, reasoning_effort"
)

# Two models `ravis/chat` will take, described identically, as in
# `test_exploration_wire.py`.
PAIR: dict[str, dict[str, str]] = {
    "vendor/alpha": {"context_window": "32000"},
    "vendor/beta": {"context_window": "32000"},
}

# What NERVIS sends on an ordinary chat turn with exploration switched on.
NERVIS_TURN: dict[str, Any] = {
    "model": "ravis/chat",
    "messages": [{"role": "user", "content": "write the numbers one to thirty"}],
    "explore": True,
    "explore_prefer_unmeasured": True,
    "reasoning_effort": "none",
}


class StrictUpstream(ScriptedUpstream):
    """An upstream that, like OpenAI, refuses top-level fields it does not define.

    `strict` maps a model to the fields it refuses. The refusal names exactly the
    ones the body carried, in OpenAI's wording and shape (no `code`).
    """

    def __init__(self, catalogue: dict[str, dict[str, str]],
                 strict: dict[str, set[str]], **kwargs: Any) -> None:
        super().__init__(catalogue, **kwargs)
        self.strict = strict

    def _refusal(self, model: str, request: httpx.Request) -> httpx.Response | None:
        payload = json.loads(request.content or b"{}")
        unknown = sorted(self.strict.get(model, set()) & payload.keys())
        if unknown:
            return httpx.Response(400, json={"error": {
                "message": "Unrecognized request arguments supplied: " + ", ".join(unknown),
                "type": "invalid_request_error", "param": None, "code": None,
            }})
        return super()._refusal(model, request)


def _roll(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    """Pin the one random draw: 0.0 always explores, 0.99 never does."""
    monkeypatch.setattr("ravis.api.openai.chat.random.random", lambda: value)


def _usual_and_explored(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Which of the pair `ravis/chat` picks normally, and which one it explores."""
    _roll(monkeypatch, 0.99)
    upstream = ScriptedUpstream(PAIR)
    with _app_with(upstream) as client:
        assert client.post("/v1/chat/completions", json=NERVIS_TURN).status_code == 200
    usual = upstream.served[-1]
    return usual, next(model for model in PAIR if model != usual)


# ── Reading the refusal ──────────────────────────────────────────────────────


def test_the_live_refusal_names_the_three_fields() -> None:
    assert refused_parameters(LIVE_REFUSAL) == {
        "explore", "explore_prefer_unmeasured", "reasoning_effort",
    }


def test_a_sentence_after_the_list_is_not_read_as_a_field() -> None:
    message = "Unrecognized request argument supplied: reasoning_effort. See the docs."
    assert refused_parameters(message) == {"reasoning_effort"}


def test_openais_one_model_wordings_are_read_too() -> None:
    assert refused_parameters(
        "Unsupported parameter: 'max_tokens' is not supported with this model."
    ) == {"max_tokens"}
    assert refused_parameters(
        "Unsupported value: 'temperature' does not support 0.2 with this model."
    ) == {"temperature"}


def test_only_settings_that_tune_an_answer_may_be_left_out() -> None:
    assert droppable(frozenset({"reasoning_effort", "temperature"}))
    assert not droppable(frozenset({"reasoning_effort", "max_tokens"})), (
        "max_tokens is somebody's spending ceiling"
    )
    assert not droppable(frozenset({"tools"}))
    assert not droppable(frozenset()), "a refusal naming nothing is no reason to drop anything"
    assert refused_parameters("Invalid 'messages[0].role': 'robot'.") == frozenset()


def test_openais_unknown_field_refusal_is_one_models_objection() -> None:
    """Classified so another model may be tried, not as a request that fails everywhere."""
    body = json.dumps({"error": {"message": LIVE_REFUSAL, "type": "invalid_request_error",
                                 "code": None}}).encode()
    assert classify_response(400, body) is FailureClass.UNSUPPORTED_PARAMETER


# ── RAVIS's own fields stay in RAVIS ─────────────────────────────────────────


@pytest.mark.parametrize("stream", [False, True])
def test_ravis_routing_fields_never_reach_the_upstream(
    monkeypatch: pytest.MonkeyPatch, stream: bool,
) -> None:
    _roll(monkeypatch, 0.99)
    upstream = ScriptedUpstream(PAIR)
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json={
            **NERVIS_TURN, "explore_rate": 0.2, "stream": stream,
        })
        assert reply.status_code == 200, reply.text
    sent = upstream.bodies[-1]
    assert not {"explore", "explore_rate", "explore_prefer_unmeasured"} & sent.keys(), sent
    assert sent["reasoning_effort"] == "none", "a setting the upstream may use still travels"


# ── A refused setting: the same model, once more, without it ────────────────


def _pinned(client: TestClient, stream: bool) -> httpx.Response:
    return client.post("/v1/chat/completions", json={
        "model": "vendor/alpha", "stream": stream,
        "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "none",
    })


@pytest.mark.parametrize("stream", [False, True])
def test_a_model_refusing_a_tuning_setting_is_asked_again_without_it(stream: bool) -> None:
    """A model addressed directly has no fallback, so this is the only way it answers."""
    upstream = StrictUpstream(PAIR, strict={"vendor/alpha": {"reasoning_effort"}})
    with _app_with(upstream) as client:
        reply = _pinned(client, stream)
        assert reply.status_code == 200, reply.text
        assert "[DONE]" in reply.text if stream else True
        assert upstream.served == ["vendor/alpha", "vendor/alpha"]
        assert "reasoning_effort" in upstream.bodies[0]
        assert "reasoning_effort" not in upstream.bodies[1]

        # **Remembered.** The next request leaves it out from the start, so the
        # model is asked once, not refused first every time.
        assert _pinned(client, stream).status_code == 200
        assert upstream.served == ["vendor/alpha"] * 3
        assert "reasoning_effort" not in upstream.bodies[2]


def test_the_retry_is_recorded_with_what_was_left_out() -> None:
    upstream = StrictUpstream(PAIR, strict={"vendor/alpha": {"reasoning_effort"}})
    with _app_with(upstream) as client:
        assert _pinned(client, False).status_code == 200
        decisions = client.get("/api/v1/route-decisions").json()
    attempts = decisions["items"][0]["execution"]
    assert attempts["dropped_parameters"] == {"vendor/alpha": ["reasoning_effort"]}
    assert [a["outcome"] for a in attempts["attempts"]] == ["unsupported_parameter", "succeeded"]


def test_a_refused_setting_that_is_not_droppable_moves_to_another_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`stream_options` changes what comes back; it is never left out."""
    usual, other = _usual_and_explored(monkeypatch)
    upstream = StrictUpstream(PAIR, strict={usual: {"stream_options"}})
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json={
            "model": "ravis/chat", "messages": [{"role": "user", "content": "hi"}],
            "stream": True, "stream_options": {"include_usage": True},
        })
        assert reply.status_code == 200, reply.text
    assert upstream.served == [usual, other], "another model, and no same-target retry"
    assert "stream_options" in upstream.bodies[1], "the setting still travels to the next model"


# ── The live failure, end to end ─────────────────────────────────────────────


def test_the_live_failure_now_answers_from_the_explored_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """16 September's turn: exploring, NERVIS's body, a strict upstream."""
    _usual, explored = _usual_and_explored(monkeypatch)
    _roll(monkeypatch, 0.0)
    upstream = StrictUpstream(PAIR, strict={explored: {
        "explore", "explore_prefer_unmeasured", "reasoning_effort",
    }})
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json=NERVIS_TURN)
        assert reply.status_code == 200, reply.text
    # RAVIS's own fields never went, so the refusal named only the setting, and
    # the explored model answered once it was left out: the turn is served and
    # the model gets measured, which is what exploring it was for.
    assert upstream.served == [explored, explored]
    assert "reasoning_effort" not in upstream.bodies[1]


# ── An exploration pick that fails costs nothing ─────────────────────────────


INVALID = (400, {"error": {"message": "Invalid 'messages[0].name': 'x y'.",
                           "type": "invalid_request_error"}})
REFUSED = (400, {"error": {"message": "Blocked by the content_filter.", "code": "content_filter"}})


def test_a_failed_exploration_pick_falls_back_to_the_usual_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usual, explored = _usual_and_explored(monkeypatch)
    _roll(monkeypatch, 0.0)
    upstream = ScriptedUpstream(PAIR, refuse={explored: INVALID})
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json=NERVIS_TURN)
        assert reply.status_code == 200, reply.text
        assert upstream.served == [explored, usual]

        # **And it is not explored again for a while.** The same winning roll
        # now finds no model to explore — the only alternative is held — and
        # the usual pick answers first time.
        assert client.post("/v1/chat/completions", json=NERVIS_TURN).status_code == 200
        assert upstream.served == [explored, usual, usual]
        decisions = client.get("/api/v1/route-decisions").json()
    held = [item["execution"]["held_from_exploration"] for item in decisions["items"]]
    assert [explored] in held


def test_an_ordinary_pick_answering_invalid_request_still_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exception is for the explored pick only; §10's rule stands otherwise."""
    usual, _explored = _usual_and_explored(monkeypatch)
    _roll(monkeypatch, 0.99)
    upstream = ScriptedUpstream(PAIR, refuse={usual: INVALID})
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json=NERVIS_TURN)
    assert reply.status_code == 400
    assert upstream.served == [usual]


def test_a_safety_refusal_from_an_exploration_pick_still_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§10: never route around a safety refusal, whoever picked the model."""
    _usual, explored = _usual_and_explored(monkeypatch)
    _roll(monkeypatch, 0.0)
    upstream = ScriptedUpstream(PAIR, refuse={explored: REFUSED})
    with _app_with(upstream) as client:
        reply = client.post("/v1/chat/completions", json=NERVIS_TURN)
    assert reply.status_code == 400
    assert upstream.served == [explored]
