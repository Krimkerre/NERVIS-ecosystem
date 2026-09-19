"""A fresh installation starts with the owner's voices (19 September 2026).

Asked for after installing on a Linux laptop, where NERVIS started with no voice at all: "ship the
current voices as standard". Seeded once, and only into an empty list, so a machine that has its
own voices keeps exactly them and one whose owner deleted them all is not refilled.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nervis import voice
from nervis.app import create_app
from nervis.config import Settings


def fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("NERVIS_SEED_DEFAULT_VOICES", "1")
    return TestClient(create_app(Settings(database_path=str(tmp_path / "nervis.db"))))


def test_a_fresh_installation_has_the_owners_voices_with_jarvis_chosen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = fresh(tmp_path, monkeypatch).get("/api/v1/voice").json()

    names = sorted(profile["name"] for profile in body["profiles"])
    assert names == ["JARVIS", "JARVIS S1", "JARVIS S2.1 PRO", "Miku", "Miku S2.1 PRO"]
    assert body["selected_profile"] == "vp_default_jarvis_pro"
    assert {p["voice_id"] for p in body["profiles"]} == {
        "14129c3e320149449d6bada6862f7338", "f88f4a28bb1d4cd7b34bc191b2202eb5"}


def test_voices_already_there_are_kept_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = fresh(tmp_path, monkeypatch)
    database = client.app.state.database  # type: ignore[attr-defined]
    for profile in voice.profiles(database):
        voice.delete_profile(database, profile.profile_id)
    voice.save_profile(database, voice.VoiceProfile("vp_mine", "Mine", "abc123"))
    voice.write_setting(database, voice.SEEDED_SETTING, "")

    assert voice.seed_default_profiles(database) is False
    assert [p.name for p in voice.profiles(database)] == ["Mine"]


def test_deleting_every_voice_does_not_bring_them_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = fresh(tmp_path, monkeypatch)
    database = client.app.state.database  # type: ignore[attr-defined]
    for profile in voice.profiles(database):
        voice.delete_profile(database, profile.profile_id)

    create_app(Settings(database_path=str(tmp_path / "nervis.db")))  # the next start

    assert voice.profiles(database) == []
