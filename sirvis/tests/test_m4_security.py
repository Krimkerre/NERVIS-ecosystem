"""M4 — the public API, and the two checks that guard its mutating half (§4.5).

    An external script can inspect SIRVIS and control a model *through SIRVIS*;
    an unauthenticated or wrong-origin mutation is refused.

Most of these are negative tests, deliberately. A security control is defined by
what it refuses, and a suite that only proved the happy path would pass just as
well against a service with no checks at all.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.conftest_lmstudio import transport

from sirvis.api.security import (
    ANONYMOUS,
    Caller,
    Scope,
    hash_token,
    mint_token,
    redacted,
    resolve_caller,
    token_summary,
)
from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.runtimes import LMStudioAdapter
from sirvis.storage import prepare_database

# §4.2 lists `/api/v1/runtime/sessions` and no `/runtime/load`. M4 shipped the
# latter, which was an invented path — the ecosystem's governing rule forbids
# inventing another component's API surface, and that applies to inventing one's
# own just as much when a specification already names it. M8 replaced it with
# the canonical session endpoint, which is also the only shape that can carry a
# lease.
LOAD = "/api/v1/runtime/sessions"
JSON = {"content-type": "application/json"}


def _app(**overrides: object) -> tuple[TestClient, str]:
    """The real app with a recorded runtime, plus an admin token to use."""
    settings = Settings(
        database_path=":memory:",
        lmstudio_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
        **overrides,  # type: ignore[arg-type]
    )
    # The runtime is handed in rather than swapped afterwards: the Resource
    # Manager captures the adapter it is built with, so replacing
    # `app.state.lmstudio` later left a *live* one inside it — and this file's
    # session test then loaded a real model on the developer's machine.
    app = create_app(
        settings,
        runtime=LMStudioAdapter(
            "http://runtime.invalid", client=httpx.AsyncClient(transport=transport())
        ),
    )
    token = mint_token(app.state.database, "test", {Scope.ADMIN})
    return TestClient(app), token


# ── The gate: what must be refused ───────────────────────────────────────────


def test_an_unauthenticated_mutation_is_refused() -> None:
    """§4.5's gate, and the reason loopback is not a boundary: any other process
    on this machine can reach the port."""
    client, _ = _app()

    response = client.post(LOAD, json={"models": [{"model_id": "anything"}]}, headers=JSON)

    assert response.status_code == 401


def test_a_wrong_origin_mutation_is_refused_even_with_a_valid_token() -> None:
    """§4.5's gate, and the case a token alone cannot cover.

    The browser holds the user's credentials and attaches them automatically, so
    a hostile page's request arrives *correctly authenticated*. The key is used
    properly by the wrong party's intent, which is why a second question has to
    be asked.
    """
    client, token = _app(allowed_origins=["http://127.0.0.1:8080"])

    response = client.post(
        LOAD,
        json={"models": [{"model_id": "anything"}]},
        headers={**JSON, "authorization": f"Bearer {token}", "origin": "http://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert "origin" in response.json()["error"]["message"]


def test_an_allow_listed_origin_with_a_token_is_permitted() -> None:
    """The other half — the check must be capable of saying yes.

    Asserted against the *checks* rather than by driving a real acquisition:
    reaching the Resource Manager here would drive a lifecycle operation, and a
    security test has no business loading a model to prove an origin was
    accepted. `require` is the thing under test, so `require` is what is called.
    """
    from fastapi import Request

    from sirvis.api.security import Scope, require

    client, token = _app(allowed_origins=["http://127.0.0.1:8080"])
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", f"Bearer {token}".encode()),
            (b"origin", b"http://127.0.0.1:8080"),
        ],
        "app": client.app,
    }

    caller = require(Request(scope), Scope.RUNTIME)

    assert caller.is_anonymous is False
    assert caller.permits(Scope.RUNTIME)


def test_a_form_content_type_is_refused() -> None:
    """§4.5's CSRF defence, and the mechanism is worth stating.

    A `<form>` on any page can POST anywhere without permission, but only as one
    of three "simple" content types that need no preflight. Requiring JSON makes
    the browser ask SIRVIS first — and that question is one the origin check
    gets to answer.
    """
    client, token = _app()

    response = client.post(
        LOAD,
        content="model_id=anything",
        headers={"content-type": "application/x-www-form-urlencoded",
                 "authorization": f"Bearer {token}"},
    )

    assert response.status_code == 415


def test_a_token_without_the_scope_is_refused() -> None:
    """A read token must not be able to spend the machine's memory."""
    client, _ = _app()
    read_only = mint_token(client.app.state.database, "viewer", {Scope.READ})  # type: ignore[attr-defined]

    response = client.post(
        LOAD,
        json={"models": [{"model_id": "anything"}]},
        headers={**JSON, "authorization": f"Bearer {read_only}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["details"]["required_scope"] == "runtime"


def test_an_unknown_token_is_anonymous_rather_than_an_error() -> None:
    """A bad credential and no credential are the same thing to the resolver.

    Distinguishing them in the response would tell an attacker that a token
    exists and was nearly right, which helps nobody who is allowed to be here.
    """
    database = prepare_database(":memory:")
    mint_token(database, "real", {Scope.ADMIN})

    caller = resolve_caller(database, {"authorization": "Bearer not-a-real-token"})

    assert caller == ANONYMOUS


# ── The credential itself ────────────────────────────────────────────────────


def test_only_a_hash_is_stored() -> None:
    """A credential a database can hand back is one a database leak hands out."""
    database = prepare_database(":memory:")

    token = mint_token(database, "test", {Scope.READ})
    rows = database.connection.execute("SELECT token_hash FROM api_token").fetchall()

    assert rows[0]["token_hash"] == hash_token(token)
    assert token not in rows[0]["token_hash"]


def test_the_token_never_appears_in_any_listing() -> None:
    """M4's redaction test. The list of what exists is useful; the values are not."""
    database = prepare_database(":memory:")
    token = mint_token(database, "test", {Scope.RUNTIME})

    rendered = repr(token_summary(database))

    assert token not in rendered
    assert "runtime" in rendered


def test_redaction_reports_presence_and_never_a_prefix() -> None:
    """Not even a few characters: a prefix narrows a search."""
    assert redacted("supersecretvalue") == "configured"
    assert redacted(None) == "not configured"


def test_the_health_alias_says_a_token_exists_without_revealing_it() -> None:
    client, _ = _app()

    body = client.get("/api/v1/health").json()

    assert body["api_token"] == "configured"
    assert body["service_type"] == "sirvis"
    assert body["ready"] is True


def test_listing_tokens_needs_admin() -> None:
    """Which scopes exist is itself worth protecting — it says what to steal."""
    client, _ = _app()

    assert client.get("/api/v1/tokens").status_code == 401


# ── Scope semantics ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("held", "required", "permitted"),
    [
        (Scope.ADMIN, Scope.RUNTIME, True),
        (Scope.ADMIN, Scope.READ, True),
        (Scope.RUNTIME, Scope.RUNTIME, True),
        (Scope.RUNTIME, Scope.ADMIN, False),
        (Scope.READ, Scope.BENCHMARK, False),
        (Scope.BENCHMARK, Scope.RUNTIME, False),
    ],
)
def test_admin_implies_everything_and_nothing_else_does(
    held: Scope, required: Scope, permitted: bool
) -> None:
    """The convenience and its cost: a leaked admin token is total, which is
    exactly why the narrower three exist."""
    caller = Caller(label="t", scopes=frozenset({held}))

    assert caller.permits(required) is permitted


# ── Reads stay open ──────────────────────────────────────────────────────────


def test_reads_a_peer_needs_for_negotiation_stay_unauthenticated() -> None:
    """§4.5: reads may be unauthenticated where a peer needs them.

    RAVIS has to be able to look SIRVIS up before anybody has exchanged
    anything, so locking discovery would make negotiation impossible.
    """
    client, _ = _app()

    for path in ("/ecosystem/health", "/ecosystem/capabilities", "/api/v1/models"):
        assert client.get(path).status_code == 200, path


def test_an_unsupported_protocol_major_is_refused_cleanly() -> None:
    """M4's exit names this. §4.2: majors match, minors never refuse."""
    from ecosystem_protocol import PROTOCOL_VERSION, is_supported_protocol

    assert is_supported_protocol(PROTOCOL_VERSION) is True
    assert is_supported_protocol("2.0.0") is False
