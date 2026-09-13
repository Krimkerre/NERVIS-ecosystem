"""Codex's ChatGPT sign-in, reached from RAVIS → Credentials through NERVIS.

The owner asked on 13 September 2026 for a button there. RAVIS serves the sign-in on admin
routes; NERVIS forwards five of them with its RAVIS admin credential after checking the
page's control token (the token itself is `test_control_token`'s subject). What can go
wrong quietly at this hop, and is tested here:

- **the credential not presented**, or presented on the wrong RAVIS path;
- **RAVIS's refusal flattened**, so the screen can no longer say "another program holds the
  sign-in ports" or "Codex is not installed" — replayed from RAVIS's own contract fixture,
  so a refusal RAVIS adds there is covered without editing this file;
- **the credential or the sign-in page's address kept** where they should not be: the
  credential in an answer, the address in a browser cache, or the address reachable through
  the read-only relay, which carries NERVIS's ordinary credential and not the admin one;
- **a route that starts Codex work**, which NERVIS must never have.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from nervis.api.routes import CODEX_CONTROL_TIMEOUT_SECONDS
from nervis.app import create_app
from nervis.config import Settings

#: Not a real credential: a value distinctive enough that finding it anywhere is a finding.
ADMIN = "admin.codex-test-credential-7f3a"
AUTH_URL = "https://auth.openai.com/oauth/authorize?fixture=1"
WAITING = {
    "sign_in": {
        "state": "waiting_for_browser",
        "auth_url": AUTH_URL,
        "callback_port": 1455,
        "started_at": "2026-09-13T01:54:00Z",
        "expires_at": "2026-09-13T02:04:00Z",
    }
}
#: RAVIS's contract for these routes, owned by RAVIS and read here rather than copied.
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "ravis" / "tests" / "fixtures" / "relay-contract" / "codex-admin.json"
)

#: (NERVIS method, NERVIS path, page body, RAVIS path, RAVIS's answer). Written out, so a
#: route that disappears or moves fails here rather than agreeing with itself.
FORWARDED = (
    ("POST", "/api/v1/ravis/codex/sign-in", {"method": "browser"},
     "/api/v1/codex/sign-in", (202, WAITING)),
    ("GET", "/api/v1/ravis/codex/sign-in", None,
     "/api/v1/codex/sign-in", (200, WAITING)),
    ("DELETE", "/api/v1/ravis/codex/sign-in", None,
     "/api/v1/codex/sign-in", (200, {"cancelled": True})),
    ("POST", "/api/v1/ravis/codex/sign-out", {},
     "/api/v1/codex/sign-out", (200, {"state": "signed_out", "account": None})),
    ("POST", "/api/v1/ravis/codex/account/confirm", {"email_hint": "o…@example.com"},
     "/api/v1/codex/account/confirm", (200, {"state": "signed_in"})),
)
NERVIS_PATH_FOR = {
    ("POST", "/api/v1/codex/sign-in"): "/api/v1/ravis/codex/sign-in",
    ("GET", "/api/v1/codex/sign-in"): "/api/v1/ravis/codex/sign-in",
    ("DELETE", "/api/v1/codex/sign-in"): "/api/v1/ravis/codex/sign-in",
    ("POST", "/api/v1/codex/sign-out"): "/api/v1/ravis/codex/sign-out",
    ("POST", "/api/v1/codex/account/confirm"): "/api/v1/ravis/codex/account/confirm",
}


class FakeRavis:
    """Answers every Codex call with one status and body, and remembers what it was sent.

    NERVIS's background probes share the client this replaces, so only calls to RAVIS's
    Codex routes are recorded; a probe's health read is not something these tests ask about.
    """

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status, self.body = status, body if body is not None else {}
        self.seen: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v1/codex"):
            self.seen.append(request)
        return httpx.Response(self.status, json=self.body)


def settings_for(tmp_path: Path, credential: str = ADMIN) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path),
        served_hosts=["127.0.0.1", "localhost", "::1", "testserver"],
        # Dead ports for every peer: nothing here may reach a service running on this Mac.
        ravis_base_url="http://127.0.0.1:9", sirvis_base_url="http://127.0.0.1:9",
        clarvis_base_url="http://127.0.0.1:9", lmstudio_base_url="http://127.0.0.1:9",
        ollama_base_url="http://127.0.0.1:9",
        ravis_admin_credential=credential,
        _env_file=None,
    )


@pytest.fixture()
def nervis(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(settings_for(tmp_path))) as client:
        yield client


def behind(client: TestClient, ravis: FakeRavis) -> FakeRavis:
    """Put `ravis` behind NERVIS, replacing the client NERVIS reaches RAVIS with."""
    client.app.state.probe_client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(ravis.handle)
    )
    return ravis


def call(client: TestClient, method: str, path: str, body: Any = None) -> httpx.Response:
    token = str(client.app.state.control_token)  # type: ignore[attr-defined]
    return client.request(method, path, json=body, headers={"x-nervis-control": token})


@pytest.mark.parametrize(("method", "path", "body", "ravis_path", "answer"), FORWARDED)
def test_each_control_reaches_its_ravis_route_with_the_admin_credential(
    nervis: TestClient, method: str, path: str, body: Any, ravis_path: str,
    answer: tuple[int, Any],
) -> None:
    ravis = behind(nervis, FakeRavis(*answer))

    answered = call(nervis, method, path, body)

    assert (answered.status_code, answered.json()) == answer
    [sent] = ravis.seen
    assert (sent.method, sent.url.path) == (method, ravis_path)
    assert sent.headers["authorization"] == f"Bearer {ADMIN}"
    assert (json.loads(sent.content) if sent.content else None) == body, (
        "the page's body is RAVIS's to judge, so it travels as it came"
    )


@pytest.mark.parametrize(("method", "path", "body", "_ravis_path", "answer"), FORWARDED)
def test_the_admin_credential_never_reaches_the_page_and_the_answer_is_not_cached(
    nervis: TestClient, method: str, path: str, body: Any, _ravis_path: str,
    answer: tuple[int, Any],
) -> None:
    """The sign-in page's address is in these answers while a sign-in waits, so a browser
    cache must not keep it; the credential that fetched it must not be in them at all."""
    behind(nervis, FakeRavis(*answer))

    answered = call(nervis, method, path, body)

    assert ADMIN not in answered.text
    assert all(ADMIN not in value for value in answered.headers.values())
    assert answered.headers["cache-control"] == "no-store"


def refusals_in_the_contract() -> list[tuple[str, str, Any, int, Any]]:
    """Every refusal RAVIS's contract fixture gives for the five routes NERVIS forwards."""
    routes = json.loads(FIXTURE.read_text(encoding="utf-8"))["routes"]
    found = []
    for route in routes:
        nervis_path = NERVIS_PATH_FOR.get((route["method"], route["path"]))
        if nervis_path is None:
            continue
        for example in route["examples"]:
            response = example["response"]
            if response["status"] >= 400:
                request_body = example["request"].get("body")
                found.append((route["method"], nervis_path, request_body,
                              response["status"], response["body"]))
    return found


def test_the_contract_still_has_the_refusals_this_file_replays() -> None:
    """Without this, a fixture that lost its examples would make the replay pass on nothing."""
    codes = {body["error"]["code"] for *_, body in refusals_in_the_contract()}
    assert {"CODEX_SIGN_IN_PORT_BUSY", "SIGN_IN_METHOD_NOT_SUPPORTED", "INVALID_REQUEST_BODY",
            "CODEX_NOT_AVAILABLE", "CODEX_UNTESTED_VERSION", "CODEX_RUN_IN_PROGRESS"} <= codes


@pytest.mark.parametrize(("method", "path", "body", "status", "refusal"), [
    *refusals_in_the_contract(),
    # Not in the fixture for these routes, and still RAVIS's to word if it ever sends it.
    ("POST", "/api/v1/ravis/codex/sign-in", {"method": "browser"}, 409,
     {"error": {"code": "CODEX_NOT_READY", "message": "Codex is not ready: signed out.",
                "retryable": False, "details": {"reason": "signed_out"}}}),
    # A RAVIS from before the sign-in routes: the page says that RAVIS does not offer it.
    ("POST", "/api/v1/ravis/codex/sign-in", {"method": "browser"}, 404, {"detail": "Not Found"}),
])
def test_ravis_s_refusal_reaches_the_page_as_ravis_said_it(
    nervis: TestClient, method: str, path: str, body: Any, status: int, refusal: Any,
) -> None:
    behind(nervis, FakeRavis(status, refusal))

    answered = call(nervis, method, path, body)

    assert (answered.status_code, answered.json()) == (status, refusal)


def test_the_longer_wait_is_the_one_used(nervis: TestClient) -> None:
    """RAVIS waits ten seconds on Codex before answering 503 itself; NERVIS must outwait it."""
    ravis = behind(nervis, FakeRavis(202, WAITING))

    call(nervis, "POST", "/api/v1/ravis/codex/sign-in", {"method": "browser"})

    assert CODEX_CONTROL_TIMEOUT_SECONDS > 10
    assert ravis.seen[0].extensions["timeout"]["read"] == CODEX_CONTROL_TIMEOUT_SECONDS


def test_without_an_admin_credential_nothing_is_sent_and_the_page_is_told_why(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(settings_for(tmp_path, credential=""))) as client:
        ravis = behind(client, FakeRavis(202, WAITING))
        answered = call(client, "POST", "/api/v1/ravis/codex/sign-in", {"method": "browser"})

    assert answered.status_code == 403
    assert "admin credential" in answered.json()["message"]
    assert not ravis.seen


def test_the_relay_cannot_read_the_sign_in_page_s_address_with_the_admin_credential(
    nervis: TestClient,
) -> None:
    """The relay is how the page reads RAVIS, and it carries NERVIS's ordinary credential.
    RAVIS refuses that on its admin sign-in route; the relay must not upgrade it."""
    ravis = behind(nervis, FakeRavis(403, {"error": {"code": "FORBIDDEN"}}))

    answered = nervis.get("/api/v1/relay/ravis/api/v1/codex/sign-in")

    assert answered.status_code == 403
    assert ADMIN not in ravis.seen[0].headers.get("authorization", "")


def test_no_nervis_route_reaches_the_file_rules_re_test(nervis: TestClient) -> None:
    """NERVIS never starts Codex work (design §3.4): the re-test starts only from the menu bar."""
    ravis = behind(nervis, FakeRavis(202, {"reproof": {"state": "running"}}))
    paths = [getattr(route, "path", "") for route in nervis.app.routes]  # type: ignore[attr-defined]

    assert not [path for path in paths if "reprove" in path or "calibration" in path]
    assert call(nervis, "POST", "/api/v1/ravis/codex/reprove", {}).status_code in (404, 405)
    assert not ravis.seen
