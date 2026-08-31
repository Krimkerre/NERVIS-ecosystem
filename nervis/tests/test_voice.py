"""Spoken output, and the gate in front of it (NERVIS.md §18.2).

Not a numbered milestone — §18.2 attaches voice to a *condition* rather than to
a milestone, and says the condition twice: cloud synthesis is an egress path, so
it obeys the same privacy policy as routing, and *"failing closed on the route
and open on the voice is still a leak."*

Most of this file is that one sentence, tested from several directions. The rest
covers the two things that make the feature usable at all: a key that goes in
and cannot come back out, and voices a person can name.
"""

from __future__ import annotations

import stat
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from nervis import voice
from nervis.app import create_app
from nervis.config import Settings

# A model that RAVIS reports as reached over the network, and one it reports as
# answering on this machine. Named rather than inlined because which is which is
# the whole subject of this file.
REMOTE_MODEL = "gpt-4o-mini"
LOCAL_MODEL = "deepseek-r1-distill-qwen-1.5b"

FISH_AUDIO_BYTES = b"ID3\x04\x00\x00\x00fake mp3 payload"


def an_api(
    *,
    models: list[dict[str, Any]] | None = None,
    ravis_answers: bool = True,
    fish_status: int = 200,
) -> TestClient:
    """A NERVIS with a scripted RAVIS and a scripted Fish Audio behind it."""
    settings = Settings(
        database_path=":memory:",
        ravis_base_url="http://127.0.0.1:8731",
        sirvis_base_url="http://127.0.0.1:9",
        clarvis_base_url="http://127.0.0.1:9",
        lmstudio_base_url="http://127.0.0.1:9",
        ollama_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)

    def handle(request: httpx.Request) -> httpx.Response:
        target = str(request.url)
        if "api.fish.audio/v1/tts" in target:
            if fish_status >= 400:
                return httpx.Response(fish_status, text="refused")
            return httpx.Response(200, content=FISH_AUDIO_BYTES)
        if "api.fish.audio/model" in target:
            return httpx.Response(200, json={"items": [{"_id": "abc123", "title": "Narrator"}]})
        if "/api/v1/models" in target:
            if not ravis_answers:
                return httpx.Response(503, json={"error": "unavailable"})
            return httpx.Response(200, json={"items": models if models is not None else [
                {"model_id": REMOTE_MODEL, "local": False},
                {"model_id": LOCAL_MODEL, "local": True},
            ]})
        return httpx.Response(200, json={})

    app.state.probe_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return TestClient(app)


def with_voice(client: TestClient, *, muted: bool = False) -> str:
    """A configured installation: a key, one named voice, and it selected."""
    client.put("/api/v1/voice/credential", json={"secret": "fish-key-value"})
    saved = client.post(
        "/api/v1/voice/profiles",
        json={"name": "Narrator", "voice_id": "abc123"},
    ).json()
    client.put(
        "/api/v1/voice/settings",
        json={"enabled": True, "muted": muted, "selected_profile": saved["profile_id"]},
    )
    return str(saved["profile_id"])


# ── The gate (§18.2) ────────────────────────────────────────────────────────


def test_a_local_reply_is_never_read_aloud_by_a_cloud_voice() -> None:
    """The sentence this whole module exists for.

    A model that answered on this machine produced text that never left it.
    Sending that text to Fish Audio to be spoken would send it after all —
    §18.2: "failing closed on the route and open on the voice is still a leak."
    """
    client = an_api()
    with_voice(client)

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "Your build finished.", "source_model": LOCAL_MODEL},
    )

    assert answer.status_code == 409
    assert answer.json()["reason"] == "local_only"
    # And it says which model, because a refusal nobody can act on is noise.
    assert LOCAL_MODEL in answer.json()["detail"]


def test_a_reply_that_already_left_the_machine_may_be_spoken() -> None:
    """The other half. A gate that refuses everything is not a gate."""
    client = an_api()
    with_voice(client)

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "Your build finished.", "source_model": REMOTE_MODEL},
    )

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "audio/mpeg"
    assert answer.content == FISH_AUDIO_BYTES


def test_an_unknown_model_is_treated_as_local() -> None:
    """Fail closed. A model RAVIS has never heard of might be a local one, and
    the cost of being wrong in that direction is a leak — while the cost of
    being wrong in the other is a slightly worse voice."""
    client = an_api()
    with_voice(client)

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "Something.", "source_model": "a-model-nobody-declared"},
    )

    assert answer.status_code == 409
    assert answer.json()["reason"] == "local_only"


def test_ravis_being_unreachable_fails_closed_too() -> None:
    """Not knowing is not permission.

    The question "did this text leave the machine?" has no safe default answer,
    so an unanswerable one is refused rather than assumed.
    """
    client = an_api(ravis_answers=False)
    with_voice(client)

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "Something.", "source_model": REMOTE_MODEL},
    )

    assert answer.status_code == 409
    assert answer.json()["reason"] == "local_only"


def test_a_tri_state_local_flag_is_not_read_as_remote() -> None:
    """`local: null` means RAVIS could not tell. That is not `false`.

    Written because `not item["local"]` and `item["local"] is False` differ on
    exactly this value, and the first one leaks.
    """
    client = an_api(models=[{"model_id": REMOTE_MODEL, "local": None}])
    with_voice(client)

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "Something.", "source_model": REMOTE_MODEL},
    )

    assert answer.status_code == 409


def test_nervis_own_words_need_no_model() -> None:
    """A voice preview is NERVIS reading its own line. It was never private."""
    client = an_api()
    with_voice(client)

    answer = client.post("/api/v1/voice/speak", json={"text": "Testing one two."})

    assert answer.status_code == 200


def test_mute_is_honoured_by_the_service_not_only_the_page() -> None:
    """§18.2: "Mute is honoured immediately and persists."

    Persisting in the browser would leave a second tab talking, which is not
    what a person who pressed mute asked for.
    """
    client = an_api()
    with_voice(client, muted=True)

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "Anything.", "source_model": REMOTE_MODEL},
    )

    assert answer.status_code == 409
    assert answer.json()["reason"] == "muted"


# ── The credential ──────────────────────────────────────────────────────────


def test_the_key_goes_in_and_never_comes_back_out() -> None:
    """There is no endpoint that returns it, which is stronger than one that
    remembers to redact."""
    client = an_api()
    client.put("/api/v1/voice/credential", json={"secret": "fish-key-value"})

    body = client.get("/api/v1/voice").json()

    assert body["credential"] == {
        "configured": True,
        "source": "file",
        "file_is_private": True,
    }
    assert "fish-key-value" not in client.get("/api/v1/voice").text


def test_the_credential_file_is_private(tmp_path: Any) -> None:
    """`0600`, and created that way rather than chmod-ed afterwards.

    Writing then chmod-ing leaves a window in which the key exists at the
    umask's discretion, which is a window an attacker on a shared machine does
    not need to be lucky to hit.
    """
    credential = voice.VoiceCredential(tmp_path / "voice-credential.json", environment={})
    credential.store("fish-key-value")

    mode = (tmp_path / "voice-credential.json").stat().st_mode

    assert not mode & (stat.S_IRGRP | stat.S_IROTH)
    assert credential.reveal() == "fish-key-value"
    assert credential.file_is_private()


def test_a_key_in_the_environment_survives_a_delete(tmp_path: Any) -> None:
    """NERVIS removes what it put there and nothing else.

    Reporting `configured: false` while the environment still supplies one would
    be a lie the next spoken line would expose.
    """
    credential = voice.VoiceCredential(
        tmp_path / "voice-credential.json",
        environment={voice.KEY_ENVIRONMENT_VARIABLE: "from-the-environment"},
    )
    credential.store("from-the-file")
    assert credential.source() == "file"

    credential.forget()

    assert credential.configured()
    assert credential.source() == "environment"


def test_the_capability_is_advertised_only_once_configured() -> None:
    """§18.2: "advertised only when configured", and without needing a restart.

    A capability that needs one lies for as long as the process lives.
    """
    client = an_api()
    before = _voice_state(client)

    client.put("/api/v1/voice/credential", json={"secret": "fish-key-value"})
    after = _voice_state(client)

    assert before == "unavailable"
    assert after == "available"


def _voice_state(client: TestClient) -> str:
    body = client.get("/ecosystem/capabilities").json()
    found = [c for c in body["capabilities"] if c["id"] == "nervis.voice"]
    return str(found[0]["state"])


def test_speaking_without_a_key_says_so_rather_than_failing() -> None:
    """The browser has its own voice and will use it. This is a fact, not a
    fault, so it arrives as a reason rather than a 500."""
    client = an_api()
    client.post("/api/v1/voice/profiles", json={"name": "N", "voice_id": "abc123"})

    answer = client.post("/api/v1/voice/speak", json={"text": "Hello."})

    assert answer.status_code == 409
    assert answer.json()["reason"] == "no_credential"


# ── Named voices ────────────────────────────────────────────────────────────


def test_a_voice_can_be_named_saved_and_chosen() -> None:
    """A reference id is thirty-two hex characters and says nothing about how it
    sounds, which makes a list of them useless for choosing by ear."""
    client = an_api()
    profile_id = with_voice(client)

    body = client.get("/api/v1/voice").json()

    assert body["selected_profile"] == profile_id
    assert body["profiles"][0]["name"] == "Narrator"
    assert body["profiles"][0]["voice_id"] == "abc123"


def test_deleting_the_chosen_voice_clears_the_choice() -> None:
    """Otherwise the screen names a profile it cannot show, and synthesis picks
    a voice nobody selected."""
    client = an_api()
    profile_id = with_voice(client)

    client.delete(f"/api/v1/voice/profiles/{profile_id}")

    assert client.get("/api/v1/voice").json()["selected_profile"] == ""


def test_an_unknown_engine_falls_back_rather_than_being_stored() -> None:
    """Fish answers an unrecognised engine header with its *account default*
    instead of an error, so an invalid value stored here would surface as the
    wrong voice and no message at all."""
    client = an_api()
    saved = client.post(
        "/api/v1/voice/profiles",
        json={"name": "N", "voice_id": "abc123", "engine": "not-an-engine"},
    ).json()

    assert saved["engine"] == voice.DEFAULT_ENGINE


def test_speed_is_clamped_to_what_fish_accepts() -> None:
    """A slider that refuses its own extreme is a worse control than one that
    stops at it."""
    client = an_api()
    saved = client.post(
        "/api/v1/voice/profiles",
        json={"name": "N", "voice_id": "abc123", "speed": 9.0},
    ).json()

    assert saved["speed"] == 2.0


def test_the_catalogue_reads_the_id_fish_actually_sends() -> None:
    """It arrives as `_id`. Reading `id` yields a list of blank rows rather than
    an error, which is the kind of bug that survives review."""
    client = an_api()
    client.put("/api/v1/voice/credential", json={"secret": "fish-key-value"})

    body = client.get("/api/v1/voice/catalogue").json()

    assert body["items"] == [{"voice_id": "abc123", "title": "Narrator"}]
    assert body["key_accepted"] is True


# ── What actually gets spoken ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        # Every engine reads a bare asterisk aloud as the word "asterisk", and a
        # model told not to write stage directions writes them anyway. The whole
        # span goes, not just the punctuation: the action was never meant to be
        # spoken, so removing only the markers says "sighs" out loud.
        ("*leans back* The build passed.", "The build passed."),
        # A screenful of punctuation read aloud. The transcript still shows it.
        ("Run this:\n```bash\nls -la\n```\nThat is all.", "Run this:\nThat is all."),
        ("Use `ls -la` now.", "Use now."),
        # The label is worth hearing; the URL never is.
        ("See [the runbook](https://example.com/a/b).", "See the runbook."),
        ("## Heading\nBody text.", "Heading\nBody text."),
    ],
)
def test_what_is_stripped_before_speaking(written: str, expected: str) -> None:
    assert voice.speakable(written) == expected


def test_a_long_reply_is_cut_at_a_sentence() -> None:
    """A reply cut mid-sentence sounds like a fault; one cut at a full stop
    sounds like it finished."""
    text = ("This is a sentence. " * 200).strip()

    spoken = voice.speakable(text)

    assert len(spoken) <= voice.MAX_SPOKEN_CHARACTERS
    assert spoken.endswith(".")


def test_a_reply_that_is_only_code_is_not_silence_to_explain() -> None:
    """Nothing speakable survived, which is a reason rather than a failure."""
    client = an_api()
    with_voice(client)

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "```python\nprint(1)\n```", "source_model": REMOTE_MODEL},
    )

    assert answer.status_code == 409
    assert answer.json()["reason"] == "nothing_to_say"


# ── The engine settings (§18.2, and one lesson from the sibling project) ────


def test_the_spend_cap_fails_toward_refusing() -> None:
    """A cap that is zero, negative or unreadable must refuse, not wave through.

    Treating a nonsense cap as "no limit configured" is exactly how a guard
    becomes a bill. Zero therefore means *never* — a useful setting rather than
    a broken one, because the browser's voice still says everything.
    """
    assert voice.within_cap(0, 200) is True
    assert voice.within_cap(199, 200) is True
    # Strictly less-than: the number somebody typed is the number they get.
    assert voice.within_cap(200, 200) is False
    assert voice.within_cap(0, 0) is False
    assert voice.within_cap(0, -1) is False


def test_a_reply_past_the_daily_cap_is_read_by_the_browser() -> None:
    """Not silence, and not an error. The line is still heard."""
    client = an_api()
    with_voice(client)
    # Switched on, because the cap is off unless somebody asks for it.
    client.put("/api/v1/voice/settings", json={"daily_cap": 0, "daily_cap_enabled": True})

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "Your build finished.", "source_model": REMOTE_MODEL},
    )

    assert answer.status_code == 409
    assert answer.json()["reason"] == "daily_cap"
    # The browser is handed the words so the fallback says the same thing.
    assert answer.json()["fallback_text"] == "Your build finished."


def test_the_cap_counts_answers_not_attempts() -> None:
    """Charging for a refused or unreachable request would let an outage
    exhaust the day's budget without a word being spoken."""
    client = an_api(fish_status=503)
    with_voice(client)

    client.post("/api/v1/voice/speak", json={"text": "One.", "source_model": REMOTE_MODEL})

    assert client.get("/api/v1/voice").json()["spent_today"] == 0


def test_a_spoken_reply_is_counted() -> None:
    client = an_api()
    with_voice(client)

    client.post("/api/v1/voice/speak", json={"text": "One.", "source_model": REMOTE_MODEL})
    client.post("/api/v1/voice/speak", json={"text": "Two.", "source_model": REMOTE_MODEL})

    assert client.get("/api/v1/voice").json()["spent_today"] == 2


def test_the_privacy_refusal_outranks_the_spend_refusal() -> None:
    """Order matters in the report, not only in the outcome.

    A refusal to *leak* reported as a refusal to *spend* would send somebody to
    raise their cap in search of a problem that is not there — and raising it
    would not make the local reply speakable, because nothing should.
    """
    client = an_api()
    with_voice(client)
    client.put("/api/v1/voice/settings", json={"daily_cap": 0})

    answer = client.post(
        "/api/v1/voice/speak", json={"text": "Hello.", "source_model": LOCAL_MODEL}
    )

    assert answer.json()["reason"] == "local_only"


def test_the_latency_mode_reaches_fish() -> None:
    """Fish's default is `normal`, measured at 1.5-3 s before the first word.
    Anything chosen here has to actually travel or the setting is decoration."""
    sent: list[dict[str, Any]] = []

    settings = Settings(
        database_path=":memory:",
        ravis_base_url="http://127.0.0.1:8731",
        sirvis_base_url="http://127.0.0.1:9",
        clarvis_base_url="http://127.0.0.1:9",
        lmstudio_base_url="http://127.0.0.1:9",
        ollama_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)

    def handle(request: httpx.Request) -> httpx.Response:
        target = str(request.url)
        if "api.fish.audio/v1/tts" in target:
            import json as _json

            sent.append({**_json.loads(request.content), "engine": request.headers.get("model")})
            return httpx.Response(200, content=FISH_AUDIO_BYTES)
        if "/api/v1/models" in target:
            return httpx.Response(200, json={"items": [{"model_id": REMOTE_MODEL, "local": False}]})
        return httpx.Response(200, json={})

    app.state.probe_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = TestClient(app)
    with_voice(client)
    client.put("/api/v1/voice/settings", json={"latency": "normal"})

    client.post("/api/v1/voice/speak", json={"text": "Hello.", "source_model": REMOTE_MODEL})

    assert sent[0]["latency"] == "normal"
    # And the engine travels as a *header*. Fish answers an unrecognised one
    # with the account default rather than an error, so a body field here would
    # be the wrong voice and no message at all.
    assert sent[0]["engine"] == voice.DEFAULT_ENGINE
    assert "model" not in sent[0]


def test_an_unknown_latency_mode_is_ignored_rather_than_stored() -> None:
    client = an_api()
    client.put("/api/v1/voice/settings", json={"latency": "instant"})

    assert client.get("/api/v1/voice").json()["latency"] == voice.DEFAULT_LATENCY


def test_trimming_off_lets_a_long_reply_run() -> None:
    """Nothing is hidden by trimming — the transcript always shows the whole
    reply — so turning it off removes the ceiling rather than raising it."""
    client = an_api()
    with_voice(client)
    client.put("/api/v1/voice/settings", json={"trim_long_replies": False})
    long_reply = ("This is a sentence. " * 300).strip()

    answer = client.post(
        "/api/v1/voice/speak", json={"text": long_reply, "source_model": LOCAL_MODEL}
    )

    # Refused for privacy, but the text handed back is the untrimmed one, which
    # is what shows the setting took effect.
    assert len(answer.json()["fallback_text"]) > voice.MAX_SPOKEN_CHARACTERS


def test_every_offered_engine_has_a_tradeoff_to_read() -> None:
    """A picker listing four version strings is a picker nobody can use."""
    client = an_api()
    body = client.get("/api/v1/voice").json()

    assert set(body["engines"]) == set(body["engine_detail"])
    assert all(body["engine_detail"][engine] for engine in body["engines"])


def test_a_model_ravis_does_not_place_is_not_said_to_be_local() -> None:
    """Both refusals end in the browser's voice; only one of them is a claim.

    §9.4's rule about never presenting a guess as a measurement, at the grain of
    a refusal message. Somebody reading "RAVIS does not report where it runs"
    goes and looks at RAVIS; somebody reading "it answered on this machine"
    concludes their cloud model is secretly local and stops looking.
    """
    client = an_api()
    with_voice(client)

    unknown = client.post(
        "/api/v1/voice/speak", json={"text": "Hi.", "source_model": "never-declared"}
    ).json()
    confirmed = client.post(
        "/api/v1/voice/speak", json={"text": "Hi.", "source_model": LOCAL_MODEL}
    ).json()

    assert unknown["reason"] == confirmed["reason"] == "local_only"
    assert "does not report where" in unknown["detail"]
    assert "answered on this machine" in confirmed["detail"]


def test_a_ravis_that_predates_the_field_is_unknown_rather_than_remote() -> None:
    """A missing key is not `false`.

    Read as remote, this gate would open on every model against any RAVIS built
    before it — which is the exact deployment where nobody would think to check.
    """
    client = an_api(models=[{"model_id": REMOTE_MODEL}])
    with_voice(client)

    answer = client.post(
        "/api/v1/voice/speak", json={"text": "Hi.", "source_model": REMOTE_MODEL}
    )

    assert answer.status_code == 409
    assert "does not report where" in answer.json()["detail"]


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        # A reasoning model's thinking arrives in `content`, not in
        # `reasoning_content`, so nothing upstream has separated it out.
        ("<think>The user wants a greeting. Keep it short.</think>Good evening.", "Good evening."),
        # Cut off mid-thought: an opening tag and no closing one. Matching only
        # balanced pairs would read the whole truncated monologue aloud.
        ("Here it is.<think>Now let me consider every", "Here it is."),
        ("<thinking>hmm</thinking>Ready.", "Ready."),
    ],
)
def test_a_chain_of_thought_is_never_read_aloud(written: str, expected: str) -> None:
    """Seen live on the first greeting ever generated: DeepSeek-R1 through LM
    Studio put its entire reasoning in the spoken text."""
    assert voice.speakable(written) == expected


def test_a_pasted_fish_url_is_accepted_as_a_voice_id() -> None:
    """The help text says a voice id is the last segment of a fish.audio URL,
    so the address bar is exactly where somebody gets one — and it arrives with
    a trailing slash. Fish rejects that with a 400 naming the character class
    and not the offending character, which is a report nobody can act on.

    Found live, on the first key anybody entered.
    """
    client = an_api()

    for pasted in (
        "14129c3e320149449d6bada6862f7338/",
        "https://fish.audio/m/14129c3e320149449d6bada6862f7338/",
        "  https://fish.audio/m/14129c3e320149449d6bada6862f7338?tab=voice  ",
        "14129c3e320149449d6bada6862f7338",
    ):
        saved = client.post(
            "/api/v1/voice/profiles", json={"name": "N", "voice_id": pasted}
        ).json()
        assert saved["voice_id"] == "14129c3e320149449d6bada6862f7338", pasted


def test_a_voice_id_that_is_not_one_is_refused_at_the_field() -> None:
    """Next to the box that is wrong, rather than days later as a refusal in the
    middle of a conversation."""
    answer = an_api().post(
        "/api/v1/voice/profiles", json={"name": "N", "voice_id": "not a voice id!"}
    )

    assert answer.status_code == 422
    assert "1-128 characters" in answer.text


def test_adding_a_second_voice_does_not_replace_the_first() -> None:
    """Creating went through `PUT /profiles/new`, and `new` is a perfectly good
    id — so every voice was stored under it and overwrote the one before. The
    symptom reads as a save that failed rather than as a save that succeeded
    and destroyed something."""
    client = an_api()

    client.post("/api/v1/voice/profiles", json={"name": "First", "voice_id": "aaa111"})
    client.post("/api/v1/voice/profiles", json={"name": "Second", "voice_id": "bbb222"})

    names = [p["name"] for p in client.get("/api/v1/voice").json()["profiles"]]
    assert names == ["First", "Second"]


def test_a_long_reply_is_announced_rather_than_read() -> None:
    """Reading the first two minutes and stopping sounds like a fault, and
    leaves the listener unsure whether they missed the answer — which is on
    screen in front of them the whole time."""
    wall = ("This is a sentence about something. " * 40).strip()

    spoken = voice.spoken_form(wall, trim=True)

    assert spoken in voice.WALL_OF_TEXT_LINES
    assert len(spoken) < 100


def test_the_same_long_reply_is_always_announced_the_same_way() -> None:
    """A voice that says something different on a re-read sounds like a
    different answer."""
    wall = ("Another sentence entirely. " * 40).strip()

    assert voice.spoken_form(wall, trim=True) == voice.spoken_form(wall, trim=True)


def test_a_short_reply_is_read_in_full() -> None:
    assert voice.spoken_form("Your build finished.", trim=True) == "Your build finished."


def test_trimming_off_reads_the_wall_of_text() -> None:
    """Off means read everything, however long — not "announce it anyway"."""
    wall = ("This is a sentence about something. " * 40).strip()

    spoken = voice.spoken_form(wall, trim=False)

    assert spoken not in voice.WALL_OF_TEXT_LINES
    assert len(spoken) > voice.WALL_OF_TEXT_CHARACTERS


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("Let me know when you emerge. 😄", "Let me know when you emerge."),
        ("Nice work 🎉🎉 on that fix.", "Nice work on that fix."),
        ("Shipped ✅ and green.", "Shipped and green."),
        # Ordinary punctuation and accents are not pictographs.
        ("Naïve — but it works: 42%.", "Naïve — but it works: 42%."),
    ],
)
def test_emoji_are_not_read_aloud(written: str, expected: str) -> None:
    """Both personas already say not to write anything you would not say out
    loud, and a model put a smiley on the end of a sentence anyway. An
    instruction is a request; this is the enforcement.

    What a voice does with one is provider-specific — silence, a pause, or the
    character's name read out — and none of those is what the sentence meant.
    The transcript keeps it either way.
    """
    assert voice.speakable(written) == expected


def test_the_daily_cap_is_off_until_asked_for() -> None:
    """A reversal, and the reason is the cost of *reaching* it: the fallback is
    the browser's own voice, and dropping mid-conversation from a chosen voice
    to that one is worse than the bill it avoids. A guard whose failure mode is
    "everything suddenly sounds wrong" gets switched off in irritation rather
    than tuned."""
    client = an_api()
    with_voice(client)
    client.put("/api/v1/voice/settings", json={"daily_cap": 0})

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "Your build finished.", "source_model": REMOTE_MODEL},
    )

    # Zero would refuse everything if the cap were enforced; it is not.
    assert answer.status_code == 200
    assert client.get("/api/v1/voice").json()["daily_cap_enabled"] is False


def test_switching_the_cap_on_makes_it_bite() -> None:
    client = an_api()
    with_voice(client)
    client.put("/api/v1/voice/settings", json={"daily_cap": 0, "daily_cap_enabled": True})

    answer = client.post(
        "/api/v1/voice/speak",
        json={"text": "Your build finished.", "source_model": REMOTE_MODEL},
    )

    assert answer.status_code == 409
    assert answer.json()["reason"] == "daily_cap"


def test_the_counter_runs_whether_or_not_the_cap_does() -> None:
    """The reading is there to look at before deciding to enforce it."""
    client = an_api()
    with_voice(client)

    client.post("/api/v1/voice/speak", json={"text": "One.", "source_model": REMOTE_MODEL})
    client.post("/api/v1/voice/speak", json={"text": "Two.", "source_model": REMOTE_MODEL})

    body = client.get("/api/v1/voice").json()
    assert body["spent_today"] == 2
    assert body["daily_cap_enabled"] is False


def test_silence_is_an_option_and_the_browser_is_the_default() -> None:
    """§18.2 offers both halves — *fall back to local system TTS, or stay
    silent and say so*. The browser's voice sends nothing, which is why it is
    the right refusal for a local model's reply; it also sounds nothing like the
    voice somebody chose, which is why the other half exists.

    Reading it is the default, because a voice feature whose out-of-the-box
    behaviour is "sometimes nothing happens" is indistinguishable from a broken
    one.
    """
    client = an_api()
    with_voice(client)

    spoken_by_browser = client.post(
        "/api/v1/voice/speak", json={"text": "Your build finished.", "source_model": LOCAL_MODEL}
    ).json()
    assert spoken_by_browser["fallback_text"] == "Your build finished."

    client.put("/api/v1/voice/settings", json={"fallback": "silence"})
    silent = client.post(
        "/api/v1/voice/speak", json={"text": "Your build finished.", "source_model": LOCAL_MODEL}
    ).json()

    # No text to say means say nothing — the same shape a mute already uses, so
    # silence needed no second mechanism.
    assert "fallback_text" not in silent
    # And the refusal still names its reason, so the page can tell a policy from
    # a fault.
    assert silent["reason"] == "local_only"


def test_silence_applies_to_a_failing_provider_too() -> None:
    """Not only to the privacy gate: somebody who does not want the browser's
    voice does not want it when Fish is down either."""
    client = an_api(fish_status=503)
    with_voice(client)
    client.put("/api/v1/voice/settings", json={"fallback": "silence"})

    answer = client.post(
        "/api/v1/voice/speak", json={"text": "Anything.", "source_model": REMOTE_MODEL}
    ).json()

    assert answer["reason"] == "refused"
    assert "fallback_text" not in answer


def test_an_unknown_fallback_mode_keeps_the_working_one() -> None:
    """A client sending a mode this build does not have gets the one that
    works, rather than a stored value nothing implements."""
    client = an_api()
    client.put("/api/v1/voice/settings", json={"fallback": "interpretive dance"})

    assert client.get("/api/v1/voice").json()["fallback"] == voice.DEFAULT_FALLBACK


def test_announcing_status_is_its_own_flag() -> None:
    """**Wanting replies read aloud and wanting to be told a service fell over
    are different wants.** Somebody reading quietly still wants to hear that
    RAVIS stopped answering; somebody who likes the voice in chat may not want
    the machine talking at them while they work in another tab."""
    client = an_api()
    with_voice(client)

    assert client.get("/api/v1/voice").json()["announce_status"] is False

    client.put("/api/v1/voice/settings", json={"announce_status": True})
    settings = client.get("/api/v1/voice").json()

    assert settings["announce_status"] is True
    assert settings["enabled"] is True, "turning announcements on must not touch the rest"


def test_it_is_off_until_somebody_asks_for_it() -> None:
    """A dashboard that starts speaking unprompted the first time it is opened
    is a surprise, and the status bar already carries the same fact silently."""
    client = an_api()

    assert client.get("/api/v1/voice").json()["announce_status"] is False


def test_nervis_own_words_need_no_model_to_be_spoken() -> None:
    """A status announcement is NERVIS's own sentence, not a model's reply, so
    it carries no `source_model` — and the §18.2 gate, which exists to stop a
    locally-produced reply leaving the machine, has nothing to withhold."""
    client = an_api()
    with_voice(client)

    answered = client.post("/api/v1/voice/speak",
                           json={"text": "RAVIS has stopped answering, sir.", "source_model": ""})

    assert answered.status_code != 403, answered.text
