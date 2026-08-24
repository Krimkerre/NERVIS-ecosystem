"""The credential endpoints (M10), and the leaks they are shaped to prevent.

The interesting assertions are negative: what the responses do *not* contain.
§15.1 requires provider results to be redacted, and the strongest way to satisfy
that is an endpoint with no path by which a value could be returned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ravis.app import create_app
from ravis.config import Settings
from ravis.credentials import CredentialFile, CredentialStore

SECRET = "sk-must-never-be-echoed-12345"


@pytest.fixture()
def client(tmp_path: Path) -> Any:
    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings)
    inner = app
    while not hasattr(inner, "state"):
        inner = inner.app  # type: ignore[attr-defined]
    inner.state.credentials = CredentialStore(
        file=CredentialFile(tmp_path / "credentials.json"),
        environment={},
        keychain=False,
    )
    with TestClient(app) as ready:
        yield ready


def test_every_known_provider_has_a_row_before_anything_is_set(client: Any) -> None:
    """The row that matters most is the empty one — it is what someone is
    looking at when they are trying to add a key."""
    names = [item["name"] for item in client.get("/api/v1/providers/credentials").json()["items"]]

    assert {"anthropic", "google", "openrouter"} <= set(names)


def test_a_stored_credential_is_reported_configured(client: Any) -> None:
    client.put("/api/v1/providers/credentials/google", json={"secret": SECRET})

    rows = {i["name"]: i for i in client.get("/api/v1/providers/credentials").json()["items"]}
    assert rows["google"]["configured"]
    assert rows["google"]["source"] == "file"


def test_no_response_ever_carries_the_value(client: Any) -> None:
    """The whole point. There is no endpoint that reads a credential back, so
    this holds by construction rather than by remembering to strip a field."""
    written = client.put("/api/v1/providers/credentials/google", json={"secret": SECRET})
    listed = client.get("/api/v1/providers/credentials")

    assert SECRET not in written.text
    assert SECRET not in listed.text


def test_an_empty_credential_is_refused_without_echoing_it(client: Any) -> None:
    """A pydantic constraint would put the offending input in the 422 body, so
    the field is unconstrained and emptiness is checked in the handler."""
    response = client.put("/api/v1/providers/credentials/google", json={"secret": "  "})

    assert response.status_code == 400
    assert "empty" in response.json()["error"]["message"]


def test_a_rejected_value_is_not_echoed_either(client: Any) -> None:
    response = client.put("/api/v1/providers/credentials/google", json={"secret": ""})

    assert response.status_code == 400
    assert SECRET not in response.text


def test_forgetting_removes_it(client: Any) -> None:
    client.put("/api/v1/providers/credentials/google", json={"secret": SECRET})

    status = client.delete("/api/v1/providers/credentials/google").json()

    assert not status["configured"]
    assert status["source"] == "absent"


def test_forgetting_does_not_reach_into_the_environment(tmp_path: Path) -> None:
    """RAVIS removes only what it put there, so the row may still read
    configured afterwards — the truth rather than a failed delete."""
    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings)
    inner = app
    while not hasattr(inner, "state"):
        inner = inner.app  # type: ignore[attr-defined]
    inner.state.credentials = CredentialStore(
        file=CredentialFile(tmp_path / "credentials.json"),
        environment={"RAVIS_GOOGLE_API_KEY": "from-env"},
        keychain=False,
    )
    with TestClient(app) as client:
        client.put("/api/v1/providers/credentials/google", json={"secret": SECRET})
        status = client.delete("/api/v1/providers/credentials/google").json()

    assert status["configured"]
    assert status["source"] == "environment"


def test_the_listing_shape_matches_the_management_envelope(client: Any) -> None:
    """§15.1's `{items, next_cursor, snapshot_revision}`, so a consumer written
    against the other collections does not need a special case."""
    body = client.get("/api/v1/providers/credentials").json()

    assert set(body) == {"items", "next_cursor", "snapshot_revision"}


def test_put_and_delete_are_reachable_from_an_allowed_browser_origin() -> None:
    """The credential screen is a browser on another origin. Without these in
    the CORS methods the one screen that configures a provider cannot work.

    PUT always preflights — unlike a simple POST — so a page that was never
    allow-listed still cannot slip a write through.
    """
    from ravis.admission import CORS_METHODS

    assert "PUT" in CORS_METHODS
    assert "DELETE" in CORS_METHODS


def test_a_secret_is_not_written_into_the_json_file_world_readable(
    client: Any, tmp_path: Path
) -> None:
    import stat

    client.put("/api/v1/providers/credentials/google", json={"secret": SECRET})

    path = tmp_path / "credentials.json"
    assert json.loads(path.read_text())["google"] == SECRET, "stored"
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, "owner only"
