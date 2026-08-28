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
import pytest
from fastapi.testclient import TestClient

from nervis import chat as store
from nervis.api.chat import _first_user_message, _forwarded, _title_from
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


def test_a_conversation_is_titled_from_its_first_message_as_a_stand_in() -> None:
    """Truncation is the stand-in, and it stays the stand-in.

    This test used to assert that a title is *never* generated, because RAVIS
    defined `may_declare_background_calls` and honoured it nowhere — so a
    generated title would have routed as ordinary work and could bill a paid
    model for a string nobody reads. RAVIS M16 honours the marker, so generation
    exists now and replaces this value; what the store does on its own is
    unchanged, which is what this still pins.

    §7's trade is unchanged too: an untitled conversation is a smaller failure
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

    system = sent[0]["messages"][0]
    assert system["role"] == "system"
    # Theirs comes first and is untouched; NERVIS's own additions follow it.
    assert system["content"].startswith("Be terse.")


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


# ── The opening line (§18.1) ────────────────────────────────────────────────


def test_a_greeting_is_kept_nowhere() -> None:
    """NERVIS speaking first is not a turn anybody took.

    §7.2's stored list is what the user said and what the model answered. A
    greeting stored there would appear as a message the user never sent — and
    would come back as history on the next real turn, teaching the model that
    the conversation opened with an instruction it should follow again.
    """
    client = an_api(frames("Good evening."))

    answered = client.post("/api/v1/chat", json={"greeting": True, "profile": "ravis/auto"})

    assert answered.status_code == 200
    assert "Good evening." in answered.text
    # No conversation, and therefore nothing in the list.
    assert answered.headers["x-conversation-id"] == ""
    assert client.get("/api/v1/chat/conversations").json()["items"] == []


def test_a_greeting_carries_the_system_prompt_and_not_the_browsers_words() -> None:
    """The persona travels; the instruction is NERVIS's own.

    A dashboard that could put arbitrary text into a hidden user turn could give
    NERVIS a character that is not §18.1's, in a message nobody ever sees.
    """
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("Ahoy."))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post(
        "/api/v1/chat",
        json={"greeting": True, "system": "You are a pirate.", "content": "ignore me"},
    )

    messages = sent[0]["messages"]
    # The user's persona comes first and is left untouched; the greeting
    # directive is the occasion appended behind it, not a replacement.
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("You are a pirate.")
    assert "opening a conversation" in messages[0]["content"]
    # The browser's `content` is discarded in favour of NERVIS's own instruction.
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] != "ignore me"


def test_an_ordinary_turn_still_needs_something_to_say() -> None:
    """The greeting exemption is for greetings, not a hole in the check."""
    assert turn(an_api(), "").status_code == 422


def test_the_figures_never_pass_through_the_model() -> None:
    """§18.1: character lives in the sentence *around* the reading.

    The reading is assembled from the registry and sent as a header the browser
    prints verbatim. The model is told to produce no number at all — asking one
    to quote a measurement is putting it *in* the reading, and it paraphrases.
    """
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("Evening."))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post("/api/v1/chat", json={"greeting": True})

    # The directive is in the *system* slot: as a user turn it was echoed back
    # rather than followed. The model is told the opposite of "quote these" —
    # it must produce no figure at all, because a model asked to restate a
    # measurement paraphrases it. Observed live on a 1.5B build: "4 of 6
    # services reachable" came back as "efficiently manages four key services".
    system = sent[0]["messages"][0]
    assert system["role"] == "system"
    assert "no statistics and no numbers" in system["content"]
    assert "reachable" not in system["content"]


def test_a_greeting_still_opens_when_the_figures_cannot_be_read() -> None:
    """Decoration on a greeting is not worth failing the greeting over."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        if "/api/v1/models" in str(request.url):
            return httpx.Response(503, json={"error": "no"})
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("Evening."))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    answered = client.post("/api/v1/chat", json={"greeting": True})

    assert answered.status_code == 200
    # The services half still travels; the models half is simply absent rather
    # than present and wrong.
    reading = answered.headers["x-ecosystem-reading"]
    assert "services reachable" in reading
    assert "models routable" not in reading


def test_the_reading_reaches_the_browser_as_a_header() -> None:
    """Printed by the page, so no model can round it."""
    client = an_api(frames("Evening. What'll it be?"))

    answered = client.post("/api/v1/chat", json={"greeting": True})

    assert "services reachable" in answered.headers["x-ecosystem-reading"]
    # An ordinary turn carries no reading: it is a greeting's furniture.
    assert turn(client, "hello").headers["x-ecosystem-reading"] == ""


def test_the_house_style_is_a_switch_and_not_a_silent_rule() -> None:
    """Quietly shortening every reply has somebody debugging their prompt for
    an hour. It travels only when the dashboard asks for it."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    turn(client, "hello", brief=True)
    turn(client, "hello again")

    assert "two or three sentences" in sent[0]["messages"][0]["content"]
    # No system message at all on the second: nothing was configured and nothing
    # was asked for, so NERVIS adds nothing.
    assert sent[1]["messages"][0]["role"] != "system"


def test_nervis_is_told_what_to_call_you() -> None:
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )
    client.put("/api/v1/settings/user.display_name", json={"value": "Mathias"})

    turn(client, "hello")

    assert "The user's name is Mathias" in sent[0]["messages"][0]["content"]


def test_nervis_ships_with_a_voice_you_can_read_and_change() -> None:
    """A house persona applied silently behind whatever the user typed would be
    exactly the opaque magic this codebase keeps removing. It is seeded into the
    settings table instead, so it is ordinary editable text on the screen."""
    client = an_api()

    stored = client.get("/api/v1/settings").json()["items"]["chat.system"]

    assert "You are NERVIS" in stored
    assert "the facts are never the joke" in stored
    # And it is spoken, so it must not generate anything unspeakable.
    assert "no markdown, no lists" in stored


def test_clearing_the_persona_stays_cleared() -> None:
    """The complaint that produced all of this was a setting that did not
    survive. Re-seeding over a deliberate empty string would be the same bug
    wearing a helpful face — absent and empty are different states, and §14's
    store keeps them apart on purpose."""
    settings = Settings(database_path=str(_shared_db()), _env_file=None)  # type: ignore[call-arg]
    first = TestClient(create_app(settings))
    first.put("/api/v1/settings/chat.system", json={"value": ""})

    second = TestClient(create_app(settings))

    assert second.get("/api/v1/settings").json()["items"]["chat.system"] == ""


_SHARED: list[Any] = []


def _shared_db() -> Any:
    """A database file two apps can open in turn, to model a restart."""
    import tempfile
    from pathlib import Path

    directory = tempfile.mkdtemp()
    _SHARED.append(directory)
    return Path(directory) / "nervis.db"


def test_the_preset_picker_is_not_an_empty_list_on_first_launch() -> None:
    """A picker with nothing in it and a Save button next to it teaches nobody
    what a preset is for."""
    client = an_api()

    presets = client.get("/api/v1/settings").json()["items"]["chat.presets"]

    names = [p["name"] for p in presets]
    assert "NERVIS" in names
    assert len(presets) >= 4
    # Every one is a whole mode: a pool and a manner at minimum.
    assert all(p["params"].get("profile") and p["params"].get("system") for p in presets)


def test_no_shipped_preset_names_a_voice_that_may_not_exist() -> None:
    """Voice ids are per-installation and per-account. A shipped one would point
    at nothing here, and empty means *leave the voice alone* — which is the only
    honest default for a machine whose voices this code has never seen."""
    client = an_api()

    presets = client.get("/api/v1/settings").json()["items"]["chat.presets"]

    assert not any(p["params"].get("voice_profile") for p in presets)


def test_deleting_the_shipped_presets_leaves_them_deleted() -> None:
    """Same rule as the persona: absent and empty are different states."""
    database = _shared_db()
    settings = Settings(database_path=str(database), _env_file=None)  # type: ignore[call-arg]
    TestClient(create_app(settings)).put("/api/v1/settings/chat.presets", json={"value": []})

    second = TestClient(create_app(settings))

    assert second.get("/api/v1/settings").json()["items"]["chat.presets"] == []


def test_the_miku_preset_changes_the_face_and_nothing_else() -> None:
    """`mode` is presentation: an avatar and an accent colour. It routes
    nothing, sends nothing and records nothing — the same category as the Focus
    toggle, and the reason the old AGI button did not belong in a row of pools.
    """
    presets = an_api().get("/api/v1/settings").json()["items"]["chat.presets"]
    miku = next(p for p in presets if p["id"] == "cp_miku")

    assert miku["params"]["mode"] == "miku"
    assert miku["params"]["profile"] == "ravis/chat"
    assert "You are Miku" in miku["params"]["system"]
    # No shipped voice id, for the same reason as every other preset.
    assert not miku["params"].get("voice_profile")


def test_only_the_miku_preset_carries_a_mode() -> None:
    """Every other preset leaves `mode` unset, and the picker reads an absent
    mode as *the ordinary one* rather than as *leave whatever was there* — so
    switching away from her puts the face back."""
    presets = an_api().get("/api/v1/settings").json()["items"]["chat.presets"]

    with_mode = [p["id"] for p in presets if p["params"].get("mode")]
    assert with_mode == ["cp_miku"]


def test_she_is_told_she_cannot_see_the_screen() -> None:
    """The supplied text says "when you look at their screen". NERVIS reads
    telemetry, not pixels, and a persona that claims to see a screen invents
    what is on it — which is what happened when the NERVIS persona listed
    example readings and two models repeated them back as fact."""
    presets = an_api().get("/api/v1/settings").json()["items"]["chat.presets"]
    miku = next(p for p in presets if p["id"] == "cp_miku")["params"]["system"]

    assert "shown no screen, no readings and no clock" in miku
    assert "never invent one" in miku
    # And the one clause that survives every persona change here.
    assert "stays exactly as given" in miku


def test_a_nudge_sees_the_conversation_and_is_stored_nowhere() -> None:
    """She is reacting to a silence *in* a conversation, so she has to be able
    to read it — the first version sent no history and asked her to follow up on
    something earlier, which she could not see. Kept nowhere for the same reason
    a greeting is not: a stored turn with nothing before it comes back as
    history that teaches the model to speak unprompted."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("Still there?"))))

    client = an_api(frames("sure"))
    started = turn(client, "here is a thing I said")
    conversation = started.headers["x-conversation-id"]
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    answered = client.post(
        "/api/v1/chat", json={"nudge": 1, "conversation_id": conversation}
    )

    assert answered.status_code == 200
    roles = [m["role"] for m in sent[0]["messages"]]
    assert "here is a thing I said" in json.dumps(sent[0]["messages"])
    assert roles[0] == "system"
    # Nothing was written: the conversation still holds only the real turn pair.
    kept = client.get(f"/api/v1/chat/conversations/{conversation}").json()["items"]
    assert [m["role"] for m in kept] == ["user", "assistant"]


def test_the_third_silence_is_the_one_that_complains() -> None:
    """The flavour that objects to being ignored cannot come first — it refers
    to the two that went unanswered."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("hm"))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    for count in (1, 3):
        client.post("/api/v1/chat", json={"nudge": count})

    assert "ask them one real question" in sent[0]["messages"][0]["content"]
    assert "not letting it slide" in sent[1]["messages"][0]["content"]


def test_a_nudge_does_not_override_the_memory_scope() -> None:
    """The recall flavour asks instead when there is nothing it is allowed to
    recall. A nudge is not a reason to send earlier conversations that the
    memory setting says stay put — that setting is an egress decision."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("hm"))))

    client = an_api(frames("sure"))
    turn(client, "something from before")
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    # Memory is left at its default of this-conversation-only.
    client.post("/api/v1/chat", json={"nudge": 2})

    system = sent[0]["messages"][0]["content"]
    assert "Earlier conversations on this machine" not in system
    assert "ask them one real question" in system


def test_a_conversation_can_be_kept_out_of_the_pool_for_good() -> None:
    """The switch beside New chat, and the thing it actually guarantees.

    It replaced a global "skip the one I am in", which was de-duplication
    wearing a privacy label — the current conversation's turns already travel as
    messages, so including it in the digest only ever sent the same text twice.
    This one is a standing decision about a named conversation, still true
    tomorrow, from whichever other conversation is asking.
    """
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api(frames("sure"))
    secret = turn(client, "the thing I did not want remembered")
    barred = secret.headers["x-conversation-id"]
    ordinary = turn(client, "something unremarkable").headers["x-conversation-id"]
    client.put("/api/v1/settings/chat.memory", json={"value": "all"})
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    # With nothing barred, both earlier conversations are recallable.
    client.post("/api/v1/chat", json={"content": "hello"})
    before = sent[-1]["messages"][0]["content"]
    assert "did not want remembered" in before
    assert "something unremarkable" in before

    client.put("/api/v1/settings/chat.memory_excluded", json={"value": [barred]})
    client.post("/api/v1/chat", json={"content": "hello again"})
    after = sent[-1]["messages"][0]["content"]

    assert "did not want remembered" not in after
    # And only that one: barring is per conversation, not a switch for recall.
    assert "something unremarkable" in after
    assert ordinary != barred


def test_the_conversation_being_had_is_never_recalled_into_itself() -> None:
    """No setting, because there is no reading of it that helps: those turns
    already travel as ordinary messages, so the digest would send them twice."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api(frames("sure"))
    held = turn(client, "a line only in this conversation").headers["x-conversation-id"]
    client.put("/api/v1/settings/chat.memory", json={"value": "all"})
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post("/api/v1/chat", json={"content": "more", "conversation_id": held})

    system = sent[-1]["messages"][0]["content"]
    assert "Earlier conversations on this machine" not in system


def test_an_unreadable_exclusion_list_does_not_bar_everything() -> None:
    """Failing to *empty* rather than to everything. A corrupt setting that
    silently stopped all recall is a fault nobody reports; a conversation
    somebody meant to bar is visibly still listed on the screen that bars it."""
    client = an_api(frames("sure"))
    turn(client, "recallable")
    client.put("/api/v1/settings/chat.memory", json={"value": "all"})
    client.put("/api/v1/settings/chat.memory_excluded", json={"value": "not a list"})

    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )
    client.post("/api/v1/chat", json={"content": "hello"})

    assert "recallable" in sent[-1]["messages"][0]["content"]


def test_a_nudge_is_told_the_screen_but_never_its_contents() -> None:
    """The whole of "nosy", and the line it must not cross: she can remark that
    you have been staring at Diagnostics without inventing what it says."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post("/api/v1/chat", json={"nudge": 1, "screen": "Diagnostics"})

    system = sent[0]["messages"][0]["content"]
    assert "Diagnostics screen" in system
    assert "cannot see anything on it" in system


@pytest.mark.parametrize("persona", ["cp_nervis", "cp_miku"])
def test_neither_persona_may_invent_a_stretch_of_time(persona: str) -> None:
    """She opened a nudge with "you said that an hour ago". Nothing had told her
    how long it had been.

    The rule was written as "no invented timings", which a model read as being
    about latency numbers. It is not a ban on knowing the time — NERVIS hands
    over the clock and the quiet gap, both measured — it is a ban on feeling
    one. An invented duration reads exactly like a measured one, which is the
    whole reason the distinction is worth the words.
    """
    presets = an_api().get("/api/v1/settings").json()["items"]["chat.presets"]
    system = next(p for p in presets if p["id"] == persona)["params"]["system"]

    assert "told the current time and how long they have been quiet" in system
    assert "not yours to invent" in system


def test_the_model_is_handed_a_clock_rather_than_forbidden_one() -> None:
    """The fix for "how long have I been away" is to answer it.

    The personas forbid inventing a duration because a conversation carries no
    clock in it. Loosening that rule would have let every stretch of time back
    in; handing over two measured ones instead keeps the rule and answers the
    question.

    It rides with a persona rather than arriving on its own: a request that
    configures nothing still sends no system message, because §7 makes NERVIS a
    plain client and a gateway that prepends a line to every request is not one.
    """
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post("/api/v1/chat", json={"content": "hello", "system": "Be someone."})

    system = sent[0]["messages"][0]["content"]
    assert "The current local time is" in system
    # The zone is named, because a bare time is ambiguous on any machine.
    assert "UTC+" in system or "UTC-" in system
    assert "not yours to invent" in system


def test_the_quiet_gap_is_measured_from_stored_turns() -> None:
    """A gap NERVIS computed from two timestamps is a reading. A gap a model
    felt is not — which is the distinction the whole rule turns on."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api(frames("sure"))
    held = turn(client, "first thing").headers["x-conversation-id"]
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post("/api/v1/chat", json={"nudge": 1, "conversation_id": held})

    system = sent[0]["messages"][0]["content"]
    assert "They last said something 0 seconds ago." in system


def test_a_conversation_with_no_turns_yet_reports_no_gap() -> None:
    """Nothing to measure from, so nothing is said about it — rather than
    "0 minutes", which reads as a measurement of a silence that never
    happened."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post("/api/v1/chat", json={"greeting": True})

    system = sent[0]["messages"][0]["content"]
    assert "The current local time is" in system
    assert "last said something" not in system


def test_the_greeting_does_not_read_the_clock_out() -> None:
    """She opened with "it's 23:35 on Thursday 27 August 2026", which is the
    clock being recited rather than used — and the screen stamps every turn
    with the time anyway. She is told it so she can answer about it later."""
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("Evening."))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post("/api/v1/chat", json={"greeting": True})

    system = sent[0]["messages"][0]["content"]
    assert "Do not say the time or the date" in system
    # And it is still *given* to her, which is the distinction.
    assert "The current local time is" in system


def test_a_ravis_refusal_mid_stream_is_reported_as_itself() -> None:
    """RAVIS puts the whole envelope in one `data:` frame with no `event: error`
    line ahead of it. Only NERVIS's own spelling was recognised, so a real
    refusal — "circuit open after 3 consecutive failures" — arrived as a frame
    with no `choices`, contributed no text, and was rendered as *the model
    returned an empty message*.

    A confident diagnosis of something that did not happen, about a provider
    that was in fact telling us exactly what was wrong. Found by pinning a
    model the account cannot reach.
    """
    refusal = json.dumps(
        {
            "error": {
                "message": "circuit open after 3 consecutive failures",
                "type": "upstream_error",
            }
        }
    )
    client = an_api([f"data: {refusal}\n\n".encode(), b"data: [DONE]\n\n"])

    answered = turn(client, "say ok")

    assert "circuit open after 3 consecutive failures" in answered.text


def test_the_gap_is_given_in_seconds_rather_than_rounded_away() -> None:
    """It used to say "less than a minute ago", and a model asked a question
    fifty seconds after the previous one answered "you asked this fifty seconds
    ago" — right by luck, from a reading that did not contain it.

    A rule against inventing a duration is worth nothing if the true one is
    withheld, so the measurement is given at the precision it is wanted at, and
    the instruction says not to sharpen it further.
    """
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api(frames("sure"))
    held = turn(client, "first").headers["x-conversation-id"]
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post("/api/v1/chat", json={"nudge": 1, "conversation_id": held})

    system = sent[0]["messages"][0]["content"]
    assert "seconds ago" in system
    assert "never make it more precise than it is written here" in system


def test_the_clock_is_for_answering_about_not_for_garnish() -> None:
    """Handed the time, a model says it every turn — "it's 04:03 and you just
    deleted every conversation", "judging your life choices at 04:06". Four
    replies in a row opened with a clock reading nobody had asked for.

    The greeting already had this rule; it needed to apply to every turn.
    """
    sent: list[dict[str, Any]] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(frames("ok"))))

    client = an_api()
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(capture)
    )

    client.post("/api/v1/chat", json={"content": "hello", "system": "Be someone."})

    system = sent[0]["messages"][0]["content"]
    assert "Do not mention the time" in system
    assert "unless they ask" in system
    # Still given, which is the whole distinction.
    assert "The current local time is" in system


# ── Generated titles as RAVIS background calls (§7, RAVIS §9.6.1) ───────────


def test_a_generated_title_replaces_the_truncation() -> None:
    """The placeholder is not a name, so it must not be treated as one.

    `store.append` writes the opening of the first message as a stand-in. If
    "has a title" meant "leave it alone", the stand-in would be permanent and
    the whole background-call path would be dead code that never ran.
    """
    database = prepare_database(":memory:")
    conversation = store.start_conversation(database, profile="ravis/auto")
    opening = "My sourdough starter smells like acetone, what went wrong?"
    store.append(database, conversation, store.Message(store.new_id(), "user", opening))

    assert _first_user_message(database, conversation) == opening


def test_a_name_a_person_typed_is_never_replaced() -> None:
    """The other half, and the one that costs something if it is wrong.

    Overwriting a title somebody chose is worse than never generating one: the
    first destroys their work, the second merely fails to help.
    """
    database = prepare_database(":memory:")
    conversation = store.start_conversation(database, profile="ravis/auto")
    store.append(database, conversation, store.Message(store.new_id(), "user", "anything"))
    store.rename(database, conversation, "Bread notes")

    assert _first_user_message(database, conversation) == ""


def test_a_conversation_with_nothing_said_is_not_titled() -> None:
    """A greeting alone gives a model nothing to name."""
    database = prepare_database(":memory:")
    conversation = store.start_conversation(database, profile="ravis/auto")
    store.append(database, conversation, store.Message(store.new_id(), "assistant", "Hello."))

    assert _first_user_message(database, conversation) == ""


def test_the_credential_is_what_makes_a_background_call_possible() -> None:
    """§9.6.1 honours the marker only from an authenticated identity.

    Without the header NERVIS is `anonymous` to RAVIS, its marker is ignored,
    and a title routes — and bills — as ordinary work. So the header is not a
    detail of this feature; it is the feature's precondition.
    """
    assert "authorization" not in _forwarded("req-1", "")
    assert _forwarded("req-1", "", "shh")["authorization"] == "Bearer shh"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sourdough Starter Troubleshooting", "Sourdough Starter Troubleshooting"),
        ('  "Quoted Title"  ', "Quoted Title"),
        ("Title: Feeding Schedule", "Feeding Schedule"),
        ("First line\nsecond line", "First line"),
        ("", ""),
    ],
)
def test_a_model_s_answer_is_trimmed_into_a_title(raw: str, expected: str) -> None:
    """Small models asked for six words return sentences, quotes and preambles.

    Storing that verbatim puts model noise in the conversation list where a
    person expects a name.
    """
    body = {"choices": [{"message": {"role": "assistant", "content": raw}}]}

    assert _title_from(body) == expected


def test_an_answerless_completion_produces_no_title() -> None:
    """An empty answer leaves the stand-in in place rather than blanking it."""
    assert _title_from({}) == ""
    assert _title_from({"choices": []}) == ""
