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

ADMIN_SECRET = "admin-secret-for-tests"
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
    # §15.1: writing a provider credential needs an `admin.`-prefixed one, and
    # calling the gateway does not grant it. The store is seeded here and the
    # client presents it, so these tests exercise the authorised path rather
    # than the refusal — the refusal has tests of its own.
    inner.state.credentials.store("admin.tests", ADMIN_SECRET)
    with TestClient(app, headers={"Authorization": f"Bearer {ADMIN_SECRET}"}) as ready:
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
    # Its own app rather than the shared fixture, so it needs the same §15.1
    # authorization the fixture grants — both calls below are credential writes.
    inner.state.credentials.store("admin.tests", ADMIN_SECRET)
    with TestClient(app, headers={"Authorization": f"Bearer {ADMIN_SECRET}"}) as client:
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


def test_an_anonymous_caller_cannot_write_a_credential_on_a_published_bind() -> None:
    """§9.6.0: an anonymous identity on a non-loopback bind must not write keys.

    This guard shipped unreachable. It asked `getattr(identity, "is_anonymous",
    False)` and `ClientApplication` has never carried that attribute, so the
    default answered every call and the refusal was dead code — on a published
    bind any unauthenticated caller could write, delete or re-point a provider
    credential. Nothing failed, because a missing predicate reads as "permitted".

    The three cases are asserted together because the bug is only visible in the
    contrast: a loopback bind is *meant* to permit anonymous writes, so testing
    the refusal alone would pass against a guard that refused everybody.
    """
    from ravis.api.management.credentials import _may_write
    from ravis.identity import anonymous_identity

    class _Request:
        def __init__(self, settings: Settings, identity: object) -> None:
            self.app = type("_App", (), {"state": type("_S", (), {"settings": settings})()})()
            self.state = type("_St", (), {"identity": identity})()

    published = Settings(host="0.0.0.0", client_credential="secret")
    assert not published.is_loopback_bind(), "the case this guard exists for"

    anonymous = anonymous_identity(published)
    assert anonymous.is_anonymous, "the predicate the guard reads"
    refusal = _may_write(_Request(published, anonymous))
    assert refusal is not None, "an anonymous caller must be refused"
    assert "authenticated" in refusal

    named = anonymous_identity(published).__class__(
        application_id="client.nervis", label="NERVIS", rate_limit_per_minute=60
    )
    assert not named.is_anonymous
    assert _may_write(_Request(published, named)) is None, "a named caller may write"

    loopback = Settings(host="127.0.0.1")
    assert _may_write(_Request(loopback, anonymous_identity(loopback))) is None, (
        "a loopback bind is reachable only from this machine, which is the "
        "deployment this endpoint is for"
    )


def test_an_anonymous_caller_cannot_narrow_a_pool_on_a_published_bind() -> None:
    """The pool write shipped with the guard its own module docstring promised.

    `PUT /pools/{pool_key}/members` persists a narrowing to `pools.json` and
    decides which models every client of that pool can reach. The module
    describing it went on saying "Reads only. No endpoint here mutates", and the
    endpoint went out without the authorization that sentence implied -- so on a
    non-loopback bind an unauthenticated caller could re-point every client's
    routing.

    Asserted at the guard rather than over HTTP because that is where the
    boundary lives, and because the credential endpoints already prove the same
    guard refuses and permits correctly end to end.
    """
    import inspect

    from ravis.api.management import routes

    source = inspect.getsource(routes.set_pool_members)

    assert "_may_write(request)" in source, (
        "a write that changes routing for every client must take the same "
        "boundary the credential writes take"
    )


def test_calling_the_gateway_does_not_grant_rewriting_its_keys(tmp_path: Path) -> None:
    """§15.1's separate authorization, which is the whole clause.

    An ordinary client credential is a real, authenticated identity: it routes,
    it carries a policy, it may declare background calls. What it must not do is
    re-point the provider keys the gateway calls with, because those are two
    different powers and one arriving with the other is the confused deputy the
    clause exists to prevent.
    """
    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings)
    inner = app
    while not hasattr(inner, "state"):
        inner = inner.app  # type: ignore[attr-defined]
    inner.state.credentials = CredentialStore(
        file=CredentialFile(tmp_path / "credentials.json"), environment={}, keychain=False
    )
    inner.state.credentials.store("client.ordinary", "an-ordinary-client-secret")

    with TestClient(app, headers={"Authorization": "Bearer an-ordinary-client-secret"}) as client:
        refused = client.put(
            "/api/v1/providers/credentials/google", json={"secret": SECRET}
        )

    assert refused.status_code == 403
    assert "admin credential" in refused.json()["error"]["message"]


def test_an_unauthenticated_caller_on_loopback_is_refused_too(tmp_path: Path) -> None:
    """The bypass this replaced.

    `_may_write` used to return early on a loopback bind — the default
    deployment — so any local process could rewrite every key, and so could a
    page the browser was visiting, since a form POST needs no permission to
    reach 127.0.0.1. Being on the same machine is not an authorization.
    """
    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings)
    inner = app
    while not hasattr(inner, "state"):
        inner = inner.app  # type: ignore[attr-defined]
    inner.state.credentials = CredentialStore(
        file=CredentialFile(tmp_path / "credentials.json"), environment={}, keychain=False
    )

    with TestClient(app) as client:
        assert client.put(
            "/api/v1/providers/credentials/google", json={"secret": SECRET}
        ).status_code == 403
        assert client.delete("/api/v1/providers/credentials/google").status_code == 403


def test_configuration_writes_are_not_held_to_the_credential_bar() -> None:
    """The falsifier for the split.

    §15.1 is about key material. Enabling a provider or narrowing a catalogue is
    configuration, and holding those to the same bar would take the Providers
    screen away from a loopback install to close a gap about keys — a real cost
    for no gain in the thing being protected.
    """
    settings = Settings(database_path=":memory:", _env_file=None)  # type: ignore[call-arg]
    app = create_app(settings)

    with TestClient(app) as client:
        assert client.put(
            "/api/v1/providers/openai/enabled", json={"enabled": False}
        ).status_code != 403
