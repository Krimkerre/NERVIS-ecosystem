"""Changing RAVIS's configuration through NERVIS needs the page's own token.

**The gate §16 item 4 closed, re-opened one hop up.** RAVIS stopped treating a
loopback bind as authorization: every management mutation there needs an admin
credential, and anonymous or ordinary inference callers get 403. NERVIS holds
that credential and proxies six of those mutations — provider enable, model
filters, pool members, pool curation, and credential write and delete — so until
this, anything that could reach `127.0.0.1:8790` could change RAVIS's
configuration without holding anything at all. The credential was moved out of
the browser's reach and the decision to *use* it was left ungated.

**Why a page token and not a bearer credential.** RAVIS declined to build a CSRF
token for itself, and `RAVIS.md` §4.4 gives the reason: a token defends *ambient*
credentials, and RAVIS's callers present a bearer header, which a cross-origin
page cannot set. NERVIS's dashboard is the other case exactly — a same-origin
page carrying no credential at all, where the browser's willingness to send the
request *is* the ambient authority. So the defence is the one that fits: a value
minted per process, embedded in the page NERVIS serves, and required back on the
six routes. A page on another origin may issue the request and may not read
`/index.html`, so it cannot learn the token; a reader that can already read the
page can already do everything the page can do, and is not the attacker this
closes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings

# The six proxies, as (method, path, body). Written out rather than derived from
# the router: a route that stops being gated should fail this list, and a list
# built from the code under test agrees with it by construction.
MUTATIONS = (
    ("PUT", "/api/v1/ravis/providers/openai/enabled", {"enabled": True}),
    ("PUT", "/api/v1/ravis/providers/openai/models", {"models": []}),
    # A bare key, because an encoded slash never reaches the route: Starlette
    # decodes the path before matching, so `ravis%2Fauto` becomes a 404 and
    # would have tested the router rather than the gate.
    ("PUT", "/api/v1/ravis/pools/auto/members", {"models": []}),
    ("POST", "/api/v1/ravis/pools/curate", {}),
    ("PUT", "/api/v1/ravis/credentials/openai", {"secret": "not-a-real-secret"}),
    ("DELETE", "/api/v1/ravis/credentials/openai", None),
)


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path),
        # `TestClient` addresses the app as `http://testserver`, and §16 item 5's
        # Host check refuses anything else — a 403 that would otherwise be
        # mistaken here for the control gate's own refusal.
        served_hosts=["127.0.0.1", "localhost", "::1", "testserver"],
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


def token_of(client: Any) -> str:
    return str(client.app.state.control_token)


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_a_configuration_change_without_the_token_is_refused(
    client: Any, method: str, path: str, body: Any
) -> None:
    answered = client.request(method, path, json=body)
    assert answered.status_code == 403
    # §4.5's envelope, so a caller can tell this from RAVIS's own refusal.
    error = answered.json()["error"]
    assert error["code"] == "CONTROL_TOKEN_REQUIRED"
    assert error["retryable"] is False


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_the_wrong_token_is_refused_like_no_token(
    client: Any, method: str, path: str, body: Any
) -> None:
    answered = client.request(
        method, path, json=body, headers={"x-nervis-control": "not-the-token"}
    )
    assert answered.status_code == 403


@pytest.mark.parametrize(("method", "path", "body"), MUTATIONS)
def test_the_page_s_own_token_gets_past_the_gate(
    client: Any, method: str, path: str, body: Any
) -> None:
    """Past *this* gate. What RAVIS then says is RAVIS's business.

    With no RAVIS running the proxy reports the peer unreachable, which is a
    different answer from 403 and is the whole point: the request was allowed to
    reach the hop that fails honestly.
    """
    answered = client.request(
        method, path, json=body, headers={"x-nervis-control": token_of(client)}
    )
    refusal = answered.json().get("error", {}) if answered.status_code >= 400 else {}
    assert refusal.get("code") != "CONTROL_TOKEN_REQUIRED"


def test_reads_stay_open(client: Any) -> None:
    """The token gates mutations only.

    NERVIS's read surface is what the dashboard is, and a console that asked for
    a credential before showing a health table would be a console nobody opens.
    """
    for path in ("/api/v1/services", "/api/v1/ravis", "/api/v1/health"):
        assert client.get(path).status_code == 200


def test_the_token_is_in_the_page_and_nowhere_else(client: Any) -> None:
    """The page carries it; the API surface does not hand it out.

    A token reachable from a JSON route would be a token any caller can fetch,
    which is the same as no token — and unlike the page, a JSON body is readable
    cross-origin when a route is open.
    """
    page = client.get("/index.html")
    assert token_of(client) in page.text
    for path in ("/api/v1/services", "/api/v1/settings", "/api/v1/health",
                 "/ecosystem/capabilities", "/api/v1/ravis"):
        assert token_of(client) not in client.get(path).text


def test_two_processes_do_not_share_a_token(tmp_path: Any) -> None:
    """Minted per process, so a token learned once does not keep working.

    Nothing persists it: a restart is a new value, and the page a browser is
    still holding stops being able to change configuration until it reloads.
    """
    made = []
    for name in ("one", "two"):
        settings = Settings(  # type: ignore[call-arg]
            database_path=str(tmp_path / f"{name}.db"), workspace_path=str(tmp_path),
            served_hosts=["127.0.0.1", "localhost", "::1", "testserver"],
            _env_file=None,
        )
        with TestClient(create_app(settings)) as client:
            made.append(token_of(client))
    assert made[0] != made[1]
    assert all(len(token) >= 32 for token in made)
