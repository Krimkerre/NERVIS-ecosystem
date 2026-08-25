"""M4 — chat as a normal RAVIS client, with the reply kept (§7).

M4's exit: *"`ravis/auto` chat works; streaming behaves; the route inspector
shows the decision."* §7 adds the constraints that shape the code — it is a
client of RAVIS's *published* API, it is **not** Clarvis chat, and §7.2's
storage is conversation ID, title, timestamps, messages and route IDs, local
only and deletable.

The streaming tests drive a fake RAVIS rather than a real one, because the cases
worth asserting are a stream that stops halfway and a model that emits nothing —
neither of which a real runtime will produce on request.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from nervis import chat as store
from nervis.app import create_app
from nervis.config import Settings
from nervis.registry import RegistryState
from nervis.storage import prepare_database


def frames(*deltas: str, done: bool = True, reasoning: int = 0) -> list[bytes]:
    """One RAVIS SSE stream, as bytes on the wire."""
    lines: list[bytes] = []
    for _ in range(reasoning):
        payload = {"model": "fake-1b", "choices": [{"delta": {"reasoning_content": "..."}}]}
        lines.append(f"data: {json.dumps(payload)}\n\n".encode())
    for delta in deltas:
        payload = {"model": "fake-1b", "choices": [{"delta": {"content": delta}}]}
        lines.append(f"data: {json.dumps(payload)}\n\n".encode())
    if done:
        lines.append(b"data: [DONE]\n\n")
    return lines


def an_api(stream: list[bytes] | None = None, *, status: int = 200,
           capabilities: dict[str, str] | None = None,
           state: RegistryState = RegistryState.HEALTHY) -> TestClient:
    """NERVIS with a RAVIS that streams exactly what the test says.

    The registry entry is written directly rather than probed: these tests are
    about what happens once RAVIS is known, and waiting for a probe would make
    every one of them depend on timing.
    """
    settings = Settings(
        database_path=":memory:",
        # A loopback literal, because M2's SSRF guard refuses a hostname —
        # resolving one would make the check depend on DNS at probe time. The
        # MockTransport intercepts regardless of the address.
        ravis_base_url="http://127.0.0.1:8731",
        sirvis_base_url="http://127.0.0.1:9",
        clarvis_base_url="http://127.0.0.1:9",
        lmstudio_base_url="http://127.0.0.1:9",
        ollama_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        if status >= 400:
            return httpx.Response(
                status, json={"error": {"code": "NO_ROUTE", "message": "no models are available"}}
            )
        payload = b"".join(stream or frames("ok"))
        return httpx.Response(200, stream=httpx.ByteStream(payload))

    app.state.probe_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    entry = app.state.registry.get("ravis")
    assert entry is not None
    entry.state = state
    entry.capabilities = capabilities if capabilities is not None else {
        "ravis.openai_compatible.chat_completions": "available",
        "ravis.routing.explanations": "available",
    }
    entry.checked_at = 1e12  # far future, so staleness never fires mid-test
    return TestClient(app)


def turn(client: TestClient, content: str, **extra: Any) -> httpx.Response:
    return client.post("/api/v1/chat", json={"content": content, **extra})


# ── The store (§7.2) ────────────────────────────────────────────────────────


def test_a_conversation_is_titled_from_its_first_message_never_generated() -> None:
    """§7 wants generated titles as a RAVIS *background call* carrying §9.6.1's
    marker. RAVIS defines `may_declare_background_calls` and honours it nowhere,
    so a generated title would route as ordinary work through `ravis/auto` and
    could select a paid model for a string nobody reads.

    §7 states the trade outright: an untitled conversation is a smaller failure
    than a title billed to a frontier model. Truncation costs nothing.
    """
    database = prepare_database(":memory:")
    conversation = store.start_conversation(database, profile="ravis/auto")

    long_question = "How do I " + "x" * 80
    store.append(database, conversation, store.Message(store.new_id(), "user", long_question))

    assert store.conversations(database)[0]["title"].startswith("How do I ")
    assert len(store.conversations(database)[0]["title"]) <= store.TITLE_LENGTH


def test_a_later_message_does_not_retitle() -> None:
    database = prepare_database(":memory:")
    conversation = store.start_conversation(database, profile="ravis/auto")

    store.append(database, conversation, store.Message(store.new_id(), "user", "first"))
    store.append(database, conversation, store.Message(store.new_id(), "user", "second"))

    assert store.conversations(database)[0]["title"] == "first"


def test_deleting_a_conversation_takes_its_messages(tmp_path: Any) -> None:
    """§7.2 requires deletion. A conversation whose messages outlive it is not
    deleted, it is hidden."""
    database = prepare_database(str(tmp_path / "n.db"))
    conversation = store.start_conversation(database, profile="ravis/auto")
    store.append(database, conversation, store.Message(store.new_id(), "user", "hello"))

    assert store.delete(database, conversation) is True

    left = database.connection.execute(
        "SELECT COUNT(*) AS n FROM chat_message WHERE conversation_id = ?", (conversation,)
    ).fetchone()
    assert left["n"] == 0
    assert store.delete(database, conversation) is False


def test_history_keeps_an_interrupted_turn_and_drops_an_empty_one() -> None:
    """What the user saw is part of the conversation whether or not it finished.

    Dropping it would make the model answer as though its own half-sentence had
    never happened. An *empty* turn carries nothing to answer as though, so it
    is left out of what RAVIS is sent.
    """
    database = prepare_database(":memory:")
    conversation = store.start_conversation(database, profile="ravis/auto")
    store.append(database, conversation, store.Message(store.new_id(), "user", "count"))
    store.append(
        database, conversation,
        store.Message(store.new_id(), "assistant", "one, two", interrupted=True),
    )
    store.append(database, conversation, store.Message(store.new_id(), "assistant", ""))

    assert store.history(database, conversation) == [
        {"role": "user", "content": "count"},
        {"role": "assistant", "content": "one, two"},
    ]


# ── Streaming (§7 MVP) ──────────────────────────────────────────────────────


def test_a_turn_streams_and_is_stored() -> None:
    client = an_api(frames("Hel", "lo"))

    response = turn(client, "hi")
    conversation = response.headers["x-conversation-id"]

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    stored = client.get(f"/api/v1/chat/conversations/{conversation}").json()["items"]
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[1]["content"] == "Hello"
    assert stored[1]["interrupted"] is False
    assert stored[1]["model"] == "fake-1b"


def test_ravis_s_frames_reach_the_client_unchanged() -> None:
    """Reshaping them would make NERVIS a second protocol nobody documented,
    and would break the moment RAVIS added a field."""
    client = an_api(frames("a", "b"))

    body = turn(client, "hi").text

    assert body.count("data:") == 3  # two deltas and [DONE]
    assert '"delta"' in body
    assert "[DONE]" in body


def test_the_conversation_id_arrives_in_the_stream_as_well_as_a_header() -> None:
    """An `EventSource` cannot read headers.

    A client that cannot learn which conversation it just started has to guess,
    which is how a reply lands in the wrong one.
    """
    client = an_api()

    response = turn(client, "hi")

    assert response.text.startswith(f": conversation {response.headers['x-conversation-id']}")


def test_a_reply_that_produces_only_reasoning_is_still_recorded() -> None:
    """Observed on the second turn of the first real conversation.

    A reasoning model spent its whole `max_tokens` budget on `reasoning_content`
    and emitted no `content`. Skipping the append left the question sitting
    there with no answer beside it and nothing to say why — so an empty turn
    that *finished* is stored, because "it answered with nothing" is true and is
    what a screen needs in order to explain it.
    """
    client = an_api(frames(done=True, reasoning=5))

    response = turn(client, "why")
    stored = client.get(
        f"/api/v1/chat/conversations/{response.headers['x-conversation-id']}"
    ).json()["items"]

    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[1]["content"] == ""
    assert stored[1]["interrupted"] is False


def test_a_stream_that_never_finishes_is_marked_interrupted() -> None:
    """No `[DONE]`, so the reply is partial and says so.

    Storing it unmarked would let the next turn present a half-sentence as a
    finished thought.
    """
    client = an_api(frames("one, two", done=False))

    response = turn(client, "count")
    stored = client.get(
        f"/api/v1/chat/conversations/{response.headers['x-conversation-id']}"
    ).json()["items"]

    assert stored[1]["content"] == "one, two"
    assert stored[1]["interrupted"] is True


def test_a_refusal_arrives_as_a_frame_rather_than_a_status() -> None:
    """Once a 200 and a content type have gone out there is no status left."""
    client = an_api(status=422)

    body = turn(client, "hi").text

    assert "event: error" in body
    assert "no models are available" in body


# ── The gate (§5.2), which does not stop applying because this one writes ───


def test_a_withdrawn_chat_capability_is_refused_without_a_request() -> None:
    client = an_api(capabilities={"ravis.openai_compatible.chat_completions": "unavailable"})

    response = turn(client, "hi")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CONFIGURATION"


def test_a_service_thought_down_is_still_attempted() -> None:
    """Liveness is not a veto here either.

    The registry's reading is up to one probe interval old, and refusing on it
    means turning away a RAVIS that came back twenty seconds ago.
    """
    client = an_api(state=RegistryState.UNREACHABLE)

    response = turn(client, "hi")

    assert response.status_code == 200


def test_a_service_never_reached_is_not_attempted() -> None:
    """Nothing was ever advertised, so there is nothing but a guess to call."""
    client = an_api(capabilities={}, state=RegistryState.DISCOVERING)

    assert turn(client, "hi").status_code == 422


# ── Multi-turn, and what §7 forbids ─────────────────────────────────────────


def test_a_second_turn_carries_the_first() -> None:
    sent: list[dict[str, Any]] = []
    client = an_api(frames("sure"))

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("sure"))))

    client.app.state.probe_client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        handle
    )

    first = turn(client, "hello")
    turn(client, "again", conversation_id=first.headers["x-conversation-id"])

    assert [m["content"] for m in sent[-1]["messages"]] == ["hello", "sure", "again"]


def test_a_system_prompt_is_sent_first_when_asked_for() -> None:
    sent: list[dict[str, Any]] = []
    client = an_api()

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client.app.state.probe_client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        handle
    )

    turn(client, "hi", system="Be terse.")

    assert sent[0]["messages"][0] == {"role": "system", "content": "Be terse."}


def test_nothing_here_carries_a_clarvis_session_or_a_tool() -> None:
    """§7: not a workspace, not a coding agent, no tools, no gates, no agent role.

    Asserted on the body actually sent, because the absence is the feature and
    an absence is the one thing a reader cannot see.
    """
    sent: list[dict[str, Any]] = []
    client = an_api()

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client.app.state.probe_client._transport = httpx.MockTransport(  # type: ignore[attr-defined]
        handle
    )

    turn(client, "hi")

    body = sent[0]
    assert not (set(body) & {"tools", "tool_choice", "functions", "session_id", "workspace"})
    assert body["model"] == "ravis/auto"


def test_an_unknown_conversation_is_a_structured_404() -> None:
    client = an_api()

    response = turn(client, "hi", conversation_id="nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_a_conversation_can_be_renamed_and_removed() -> None:
    client = an_api()
    conversation = turn(client, "hi").headers["x-conversation-id"]

    client.put(f"/api/v1/chat/conversations/{conversation}/title", json={"title": "Notes"})
    assert client.get("/api/v1/chat/conversations").json()["items"][0]["title"] == "Notes"

    assert client.delete(f"/api/v1/chat/conversations/{conversation}").status_code == 200
    assert client.get("/api/v1/chat/conversations").json()["items"] == []
