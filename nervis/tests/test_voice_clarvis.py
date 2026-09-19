"""Clarvis's voice, chosen in NERVIS and asked for by a Clarvis window (19 September 2026).

The owner's decisions: Clarvis follows NERVIS for its voice, but has a voice of its own there —
"I don't want them to sound identical". NERVIS never writes a Clarvis setting (CLARVIS.md §6.7):
a registered window asks, with the token NERVIS gave it, and NERVIS speaks with its own Fish key.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.test_m8b_status import register

from nervis import voice
from nervis.app import create_app
from nervis.config import Settings

MP3 = b"ID3\x04\x00\x00\x00clarvis voice"
RICK = "d2e75a3e3fd6419893057c02a375a113"


def an_api(tmp_path: Path, fish: list[httpx.Request]) -> TestClient:
    app = create_app(Settings(database_path=str(tmp_path / "nervis.db"), _env_file=None))  # type: ignore[call-arg]

    def answer(request: httpx.Request) -> httpx.Response:
        if "api.fish.audio/v1/tts" in str(request.url):
            fish.append(request)
            return httpx.Response(200, content=MP3)
        return httpx.Response(503)

    app.state.probe_client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    return TestClient(app, headers={"x-nervis-control": app.state.control_token})


def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("NERVIS_SEED_DEFAULT_VOICES", "1")
    return an_api(tmp_path, [])


# ── The choice ───────────────────────────────────────────────────────────────


def test_a_fresh_installation_gives_clarvis_rick_and_nervis_jarvis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = seeded(tmp_path, monkeypatch).get("/api/v1/voice").json()

    assert body["selected_profile"] == "vp_default_jarvis_pro"
    assert body["clarvis_profile"] == "vp_default_rick"
    names = {profile["name"] for profile in body["profiles"]}
    assert {"Rick Sanchez", "DramaButler", "JARVIS S2.1 PRO"} <= names


def test_an_installation_with_its_own_voices_gains_clarvis_s_beside_them(tmp_path: Path) -> None:
    """The owner's Mac: its own list stays, Clarvis's voices are added once, Rick chosen."""
    database = an_api(tmp_path, []).app.state.database  # type: ignore[attr-defined]
    voice.save_profile(database, voice.VoiceProfile("vp_mine", "Mine", "abc123"))

    assert voice.seed_clarvis_voice(database) is True
    assert voice.read_setting(database, voice.CLARVIS_PROFILE_SETTING) == "vp_default_rick"
    assert "Mine" in [p.name for p in voice.profiles(database)]
    assert voice.seed_clarvis_voice(database) is False, "once"


def test_a_rick_already_there_is_chosen_not_duplicated(tmp_path: Path) -> None:
    database = an_api(tmp_path, []).app.state.database  # type: ignore[attr-defined]
    voice.save_profile(database, voice.VoiceProfile("vp_myrick", "Rick", RICK, "s2.1-pro"))

    voice.seed_clarvis_voice(database)

    assert voice.read_setting(database, voice.CLARVIS_PROFILE_SETTING) == "vp_myrick"
    assert [p.voice_id for p in voice.profiles(database)].count(RICK) == 1


def test_deleting_clarvis_s_voice_clears_the_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = seeded(tmp_path, monkeypatch)

    client.delete("/api/v1/voice/profiles/vp_default_rick")

    assert client.get("/api/v1/voice").json()["clarvis_profile"] == ""


# ── A window asking ──────────────────────────────────────────────────────────


def _window(client: TestClient) -> tuple[str, dict[str, str]]:
    instance_id, token = register(client, 47001)
    return instance_id, {"Authorization": f"Bearer {token}"}


def test_a_window_learns_clarvis_s_voice_and_has_it_spoken_with_nervis_s_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fish: list[httpx.Request] = []
    monkeypatch.setenv("NERVIS_SEED_DEFAULT_VOICES", "1")
    client = an_api(tmp_path, fish)
    client.put("/api/v1/voice/credential", json={"secret": "fish-key-value"})
    client.put("/api/v1/voice/settings", json={"muted": True})  # NERVIS's page mute
    instance_id, as_window = _window(client)
    base = f"/api/v1/registry/instances/clarvis/{instance_id}"

    chosen = client.get(f"{base}/voice", headers=as_window).json()
    spoken = client.post(f"{base}/speak", json={"text": "Build passed."}, headers=as_window)

    assert chosen["profile"]["voice_id"] == RICK and chosen["can_speak"] is True
    assert spoken.status_code == 200 and spoken.content == MP3, spoken.text
    assert b'"reference_id":"' + RICK.encode() in fish[0].content.replace(b" ", b"")
    assert fish[0].headers["model"] == "s2.1-pro"
    assert "fish-key-value" not in chosen.__repr__(), "the key never goes to the window"


def test_only_the_window_s_own_token_may_ask(tmp_path: Path) -> None:
    client = an_api(tmp_path, [])
    instance_id, _ = _window(client)
    base = f"/api/v1/registry/instances/clarvis/{instance_id}"

    for headers in ({}, {"Authorization": "Bearer not-its-token"}):
        assert client.get(f"{base}/voice", headers=headers).status_code == 401
        assert client.post(f"{base}/speak", json={"text": "hi"}, headers=headers).status_code == 401


def test_without_a_key_the_window_is_told_so_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NERVIS_SEED_DEFAULT_VOICES", "1")
    client = an_api(tmp_path, [])
    instance_id, as_window = _window(client)
    base = f"/api/v1/registry/instances/clarvis/{instance_id}"

    assert client.get(f"{base}/voice", headers=as_window).json()["can_speak"] is False
    refused = client.post(f"{base}/speak", json={"text": "hi"}, headers=as_window)
    assert refused.status_code == 409 and refused.json()["reason"] == "no_credential"
