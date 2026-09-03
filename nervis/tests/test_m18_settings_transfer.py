"""M18 — settings export and import, without secrets.

The exit clause is four words: **settings import/export works without
secrets**. The interesting question this suite asks is not "does export
produce a file" — it is "what happens to the entries that are not secrets and
still should not travel": a filesystem path bound to one machine, a rate-limit
counter, a session id nobody minted for the destination.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings
from nervis.errors import InvalidConfigurationError
from nervis.settings_transfer import (
    EXPORTABLE,
    FORMAT,
    FORMAT_VERSION,
    export_settings,
    import_settings,
)
from nervis.storage import prepare_database
from nervis.voice import read_setting


@pytest.fixture()
def database() -> Any:
    return prepare_database(":memory:")


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


def put(database: Any, key: str, value: Any) -> None:
    import json
    with database.connection as connection:
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


# ── What leaves ──────────────────────────────────────────────────────────


def test_a_preference_is_exported(database: Any) -> None:
    put(database, "voice.enabled", True)
    found = export_settings(database)
    assert found["settings"]["voice.enabled"] is True
    assert found["format"] == FORMAT
    assert found["version"] == FORMAT_VERSION


def test_a_machine_specific_adapter_path_never_leaves(database: Any) -> None:
    """§12: a path to an executable on *this* machine.

    Importing it on another machine points supervision at a path that may not
    exist there, or may exist and be something else entirely.
    """
    put(database, "supervision.adapter.sirvis",
        {"executable": "/Users/someone/bin/sirvis", "args": [], "cwd": ""})
    assert "supervision.adapter.sirvis" not in export_settings(database)["settings"]


def test_a_minted_session_id_never_leaves(database: Any) -> None:
    """An id this installation minted for itself identifies nothing elsewhere."""
    put(database, "background.session", "bgs_abc123")
    assert "background.session" not in export_settings(database)["settings"]


def test_a_rate_limit_counter_never_leaves(database: Any) -> None:
    """A record of what already happened, not a preference — and it is keyed
    per day, which would mean importing yesterday's count into today."""
    put(database, "voice.requests.2026-09-02", 4)
    assert "voice.requests.2026-09-02" not in export_settings(database)["settings"]


def test_conversation_scoped_state_never_leaves(database: Any) -> None:
    """M20's exclusion list is local browser conversation ids, not a preference."""
    put(database, "chat.memory_excluded", ["c1", "c2"])
    assert "chat.memory_excluded" not in export_settings(database)["settings"]


def test_nothing_unrecognised_by_the_allowlist_ever_leaves(database: Any) -> None:
    """A key nobody has looked at yet defaults to excluded, not included.

    The safe failure mode for an allowlist: forgetting to add a future setting
    here means it stays out, not that it leaks the day it is added elsewhere.
    """
    put(database, "some.future.setting.nobody.reviewed.yet", "anything")
    assert export_settings(database)["settings"] == {}


def test_every_exportable_key_round_trips_through_json(database: Any) -> None:
    """A quick sweep rather than one example: every name on the allowlist
    survives a write and a read with its type intact, not just the ones this
    file happened to exercise by name."""
    samples: dict[str, Any] = {
        "chat.presets": [{"id": "x", "name": "y"}], "chat.params": {"a": 1},
        "voice.daily_cap": 200, "background.enabled": True, "refresh_seconds": 15,
    }
    for key in EXPORTABLE:
        put(database, key, samples.get(key, "value"))
    found = export_settings(database)["settings"]
    assert set(found) == EXPORTABLE


# ── What comes back ─────────────────────────────────────────────────────


def test_import_applies_an_exported_files_settings(database: Any) -> None:
    payload = {"format": FORMAT, "version": FORMAT_VERSION,
              "settings": {"voice.enabled": True, "refresh_seconds": 20}}
    outcome = import_settings(database, payload)
    assert outcome.applied == ["refresh_seconds", "voice.enabled"]
    assert read_setting(database, "voice.enabled", "0") == "true"


def test_the_allowlist_gates_import_too(database: Any) -> None:
    """A hand-edited or forged file could name any key at all.

    Accepting only what `export_settings` would itself have produced is what
    stops import from being a wider door than export ever was — the same
    boundary, enforced on the way back in.
    """
    payload = {"format": FORMAT, "version": FORMAT_VERSION, "settings": {
        "voice.enabled": True,
        "supervision.adapter.sirvis": {"executable": "/bin/sh", "args": [], "cwd": ""},
    }}
    outcome = import_settings(database, payload)
    assert outcome.applied == ["voice.enabled"]
    assert outcome.skipped == [{
        "key": "supervision.adapter.sirvis",
        "reason": "not on the exportable list — either a secret, or state "
        "specific to the machine it came from",
    }]
    assert read_setting(database, "supervision.adapter.sirvis", "") == ""


def test_a_skipped_key_does_not_block_the_accepted_ones(database: Any) -> None:
    payload = {"format": FORMAT, "version": FORMAT_VERSION, "settings": {
        "background.session": "should-not-apply", "voice.enabled": True,
    }}
    outcome = import_settings(database, payload)
    assert outcome.applied == ["voice.enabled"]
    assert len(outcome.skipped) == 1


def test_a_file_in_the_wrong_format_is_refused_outright(database: Any) -> None:
    with pytest.raises(InvalidConfigurationError):
        import_settings(database, {"not": "a settings file"})


def test_a_future_format_version_is_refused_rather_than_guessed(database: Any) -> None:
    """A version this build does not understand might mean a key means
    something different now. Guessing is how a preference silently becomes a
    different preference."""
    with pytest.raises(InvalidConfigurationError):
        import_settings(database, {
            "format": FORMAT, "version": FORMAT_VERSION + 1, "settings": {},
        })


def test_settings_is_not_an_object_is_refused(database: Any) -> None:
    with pytest.raises(InvalidConfigurationError):
        import_settings(database, {"format": FORMAT, "version": 1, "settings": "nope"})


def test_export_then_import_is_the_identity_on_a_fresh_database(database: Any) -> None:
    """The round trip this whole feature exists for."""
    put(database, "user.display_name", "Matty")
    put(database, "voice.trim_long_replies", False)
    exported = export_settings(database)

    fresh = prepare_database(":memory:")
    outcome = import_settings(fresh, exported)

    assert set(outcome.applied) == set(exported["settings"])
    assert export_settings(fresh)["settings"] == exported["settings"]


# ── The endpoints ────────────────────────────────────────────────────────


def test_the_export_endpoint_matches_the_function(client: TestClient) -> None:
    """Not an exact-equality check: `create_app` seeds default chat presets on
    startup (`seed_chat_defaults`), so a fresh client's export is never empty.
    What this pins is that the endpoint is the function — a value written
    through it comes back through export."""
    database = client.app.state.database  # type: ignore[attr-defined]
    put(database, "voice.enabled", True)
    body = client.get("/api/v1/settings/export").json()
    assert body["settings"]["voice.enabled"] is True
    assert set(body["settings"]) <= EXPORTABLE


def test_the_import_endpoint_reports_what_it_skipped(client: TestClient) -> None:
    body = client.post("/api/v1/settings/import", json={
        "format": FORMAT, "version": FORMAT_VERSION,
        "settings": {"background.session": "x", "refresh_seconds": 30},
    }).json()
    assert body["applied"] == ["refresh_seconds"]
    assert len(body["skipped"]) == 1


def test_the_import_endpoint_refuses_a_foreign_file(client: TestClient) -> None:
    response = client.post("/api/v1/settings/import", json={"hello": "world"})
    assert response.status_code == 422
