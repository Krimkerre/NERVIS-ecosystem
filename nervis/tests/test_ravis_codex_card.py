"""The Codex card's control routes, reached from RAVIS → Dashboard through NERVIS.

N2b (14 September 2026) gave the card four, each taking the sign-in's hop — the page's control
token checked (`test_control_token`'s subject), then NERVIS's RAVIS admin credential presented —
and each can go wrong quietly at this hop:

- **a task's Stop** (`POST /api/v1/ravis/codex/runs/{sid}/stop`): the page's `Idempotency-Key` not
  reaching RAVIS exactly as sent, so a retried click is acted on twice; the page choosing its own
  `source` or adding to the confirmation; an id or a key forwarded that RAVIS's address and headers
  should never carry. Replayed from `owner-stop.json` → `nervis_route`, RAVIS's own contract;
- **removing a site the owner allowed** (`DELETE /api/v1/ravis/codex/sites/{host}`): a name that
  could change RAVIS's address, forwarded with the admin credential attached;
- **a new Codex build's report and its acceptance**: NERVIS giving up before RAVIS's checks answer;
- **all four**: RAVIS's refusal flattened, the answer cached or carrying the credential, and
  `configure`'s new `headers` argument able to replace the credential.

And the rule the card rests on, held from the route table and from NERVIS's source: no NERVIS route
reaches any other agent-session or project-lock route, and NERVIS serves exactly the Codex control
routes RAVIS's contract lists for it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from nervis.api.routes import CODEX_CONTROL_TIMEOUT_SECONDS, CODEX_VERSION_TIMEOUT_SECONDS
from nervis.app import create_app
from nervis.config import Settings
from nervis.peers import ravis as ravis_peer

REPOSITORY = Path(__file__).resolve().parents[2]
#: RAVIS's contract for these routes, owned by RAVIS and read here rather than copied.
CONTRACT = REPOSITORY / "ravis" / "tests" / "fixtures" / "relay-contract"
OWNER_STOP = json.loads((CONTRACT / "owner-stop.json").read_text(encoding="utf-8"))
CODEX_ADMIN = json.loads((CONTRACT / "codex-admin.json").read_text(encoding="utf-8"))

#: Not a real credential: a value distinctive enough that finding it anywhere is a finding.
ADMIN = "admin.codex-card-test-credential-5b2e"
SID = "as_01J9ZK4T6Q8M2V7R3N5B1C0D"
TURN = "019a1c2e-8c4f-7a21-b5d3-3e6c9b7f2a41"
KEY = "FIXTUREdashboardStopKey01"
STOP = f"/api/v1/ravis/codex/runs/{SID}/stop"
CONFIRMATION = {"project": "add-utc-demo", "turn_id": TURN}
SHA256 = "7c2d1ac2d5fa680c2d97e55d140337fa8a38e8fbde184bf7df5c313732ea121c"
#: RAVIS as `configure` sees it: only the base URL is read.
ENTRY: Any = SimpleNamespace(declaration=SimpleNamespace(base_url="http://ravis.test"))


class FakeRavis:
    """Answers every call to RAVIS's Codex and agent-session routes with one status and body, and
    remembers what it was sent.

    NERVIS's background probes share the client this replaces, so only calls to those routes are
    recorded; a probe's health read is not something these tests ask about.
    """

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status, self.body = status, body if body is not None else {}
        self.seen: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith(("/api/v1/codex", "/api/v1/agent-sessions")):
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


def call(
    client: TestClient, method: str, path: str, body: Any = None, *, key: str | None = None,
) -> httpx.Response:
    """The page's request: its control token, and its Idempotency-Key when it sends one."""
    headers = {"x-nervis-control": str(client.app.state.control_token)}  # type: ignore[attr-defined]
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.request(method, path, json=body, headers=headers)


def served_routes(client: TestClient) -> list[tuple[str, str]]:
    """(method, path) for every route NERVIS serves, read from the real route table.

    FastAPI wraps an included router lazily; `original_router` is the way back to its routes.
    """
    from fastapi.routing import _IncludedRouter

    found: list[tuple[str, str]] = []
    pending = list(client.app.routes)  # type: ignore[attr-defined]
    while pending:
        route = pending.pop()
        if isinstance(route, _IncludedRouter):
            pending.extend(route.original_router.routes)
            continue
        for method in sorted(getattr(route, "methods", None) or ()):
            if method != "HEAD":
                found.append((method, str(getattr(route, "path", ""))))
    return found


def ravis_route(method: str, path: str) -> dict[str, Any]:
    """One route of RAVIS's Codex admin contract."""
    return next(
        route for route in CODEX_ADMIN["routes"]
        if (route["method"], route["path"]) == (method, path)
    )


def answer_of(route: dict[str, Any], name: str) -> tuple[int, Any]:
    """The status and body of one named example in a contract route."""
    response = next(example for example in route["examples"] if example["name"] == name)["response"]
    return response["status"], response["body"]


def nervis_path_for(method: str, ravis_path: str) -> str | None:
    """The card's NERVIS route for an admin call RAVIS's contract shows, or None for the others."""
    if method == "DELETE" and ravis_path.startswith("/api/v1/codex/sites/"):
        return "/api/v1/ravis/codex/sites/" + ravis_path.rsplit("/", 1)[1]
    if (method, ravis_path) in (
        ("GET", "/api/v1/codex/version-check"), ("POST", "/api/v1/codex/accept-version"),
    ):
        return "/api/v1/ravis" + ravis_path.removeprefix("/api/v1")
    return None


def card_refusals() -> list[tuple[str, str, Any, int, Any]]:
    """Every refusal RAVIS's contract gives for the site removal and the two version routes."""
    found = []
    for route in CODEX_ADMIN["routes"]:
        for example in route["examples"]:
            response, request = example["response"], example["request"]
            nervis_path = nervis_path_for(route["method"], request["path"])
            if nervis_path is not None and response["status"] >= 400:
                found.append((route["method"], nervis_path, request.get("body"),
                              response["status"], response["body"]))
    return found


# ── A task's Stop ────────────────────────────────────────────────────────────


def test_the_contract_still_has_what_this_file_replays() -> None:
    """Without this, a fixture that lost its examples would let the replays pass on nothing."""
    route = OWNER_STOP["nervis_route"]
    assert (route["method"], route["path"]) == ("POST", "/api/v1/ravis/codex/runs/{sid}/stop")
    assert {example["name"] for example in route["examples"]} >= {
        "forwarded", "RAVIS's refusal passed through unchanged",
        "a malformed id is not forwarded", "a missing or malformed key is not forwarded",
    }
    assert "authorization" in route["configure_headers"]
    stop_codes = {example["response"]["body"]["error"]["code"]
                  for example in OWNER_STOP["examples"] if example["response"]["status"] >= 400}
    assert {"CONFIRMATION_MISMATCH", "NOTHING_RUNNING", "AGENT_SESSION_NOT_FOUND",
            "RATE_LIMITED", "OWNER_STOP_NOT_ALLOWED"} <= stop_codes
    card_codes = {body["error"]["code"] for *_, body in card_refusals()}
    assert {"SITE_NOT_REMOVED", "FORBIDDEN", "CODEX_HASH_MISMATCH",
            "CODEX_VERSION_CHECK_FAILED"} <= card_codes


@pytest.mark.parametrize(
    "example", OWNER_STOP["nervis_route"]["examples"], ids=lambda example: example["name"]
)
def test_the_stop_route_does_what_ravis_s_contract_shows(
    nervis: TestClient, example: dict[str, Any],
) -> None:
    """Forwarded with the admin credential, `source: dashboard` and the page's own key; RAVIS's
    answer back unchanged; a malformed id or key answered here, in the contract's words."""
    request, forwarded, response = (
        example["request"], example["forwarded_request"], example["response"]
    )
    ravis = behind(nervis, FakeRavis(response["status"], response["body"]))

    answered = call(nervis, "POST", request["path"], request["body"],
                    key=request["headers"]["Idempotency-Key"])

    assert (answered.status_code, answered.json()) == (response["status"], response["body"])
    if forwarded is None:
        assert ravis.seen == []
        return
    [sent] = ravis.seen
    assert (sent.method, sent.url.path) == (forwarded["method"], forwarded["path"])
    assert sent.headers["idempotency-key"] == forwarded["headers"]["Idempotency-Key"]
    assert json.loads(sent.content) == forwarded["body"]
    assert sent.headers["authorization"] == f"Bearer {ADMIN}"


def test_a_retried_click_reaches_ravis_with_the_page_s_own_key(nervis: TestClient) -> None:
    """RAVIS replays the answer it gave a key it has already seen, so the key has to arrive exactly
    as the page sent it, every time. One NERVIS made up would turn a retry into a second stop."""
    ravis = behind(nervis, FakeRavis(202, {"state": "stopping"}))
    other = "another-click-key-0000000000000"

    for key in (KEY, KEY, other):
        assert call(nervis, "POST", STOP, CONFIRMATION, key=key).status_code == 202

    assert [sent.headers["idempotency-key"] for sent in ravis.seen] == [KEY, KEY, other]


def test_the_page_neither_chooses_the_source_nor_adds_to_the_confirmation(
    nervis: TestClient,
) -> None:
    """RAVIS counts and audits the menu bar's stops apart from the dashboard's, so the source is
    NERVIS's to state; and the confirmation RAVIS checks is the folder and turn, nothing else."""
    ravis = behind(nervis, FakeRavis(202, {"state": "stopping"}))
    elsewhere = {"project": "someone-else", "turn_id": "019a0000-0000-7000-8000-000000000000"}
    page = {**CONFIRMATION, "source": "menu_bar", "kind": "answer", "confirm": elsewhere}

    call(nervis, "POST", STOP, page, key=KEY)

    [sent] = ravis.seen
    assert json.loads(sent.content) == {"source": "dashboard", "confirm": CONFIRMATION}


@pytest.mark.parametrize("key", [
    None, "", "short", "a" * 15, "a" * 129, "sixteen chars ok", "key/with/a/slash-00",
    "semi;colon;key;00000", "dots.are.not.allowed.00",
])
def test_a_missing_or_malformed_key_is_refused_before_ravis(
    nervis: TestClient, key: str | None,
) -> None:
    ravis = behind(nervis, FakeRavis(202, {"state": "stopping"}))

    answered = call(nervis, "POST", STOP, CONFIRMATION, key=key)

    assert answered.status_code == 400
    assert "Idempotency-Key" in answered.json()["message"]
    assert answered.headers["cache-control"] == "no-store"
    assert ravis.seen == []


@pytest.mark.parametrize("key", ["a" * 16, "Z9_-" * 32])
def test_a_key_at_either_end_of_the_allowed_length_is_forwarded(
    nervis: TestClient, key: str,
) -> None:
    ravis = behind(nervis, FakeRavis(202, {"state": "stopping"}))

    assert call(nervis, "POST", STOP, CONFIRMATION, key=key).status_code == 202
    assert [sent.headers["idempotency-key"] for sent in ravis.seen] == [key]


@pytest.mark.parametrize("sid", [
    "not-a-session", "as_short", "as_" + "A" * 41, "AS_01J9ZK4T6Q8M2V7R3N5B1C0D",
    "as_01J9ZK4T6Q8M2V7R3N5B1C0D-", "as_01J9ZK4T6Q8M2V7R3N5B1C0D%20", "as_01J9ZK4T6Q%2E%2E",
])
def test_a_task_id_ravis_never_mints_is_refused_before_ravis(nervis: TestClient, sid: str) -> None:
    ravis = behind(nervis, FakeRavis(202, {"state": "stopping"}))

    answered = call(nervis, "POST", f"/api/v1/ravis/codex/runs/{sid}/stop", CONFIRMATION, key=KEY)

    assert answered.status_code == 400
    assert "is not a Codex task id NERVIS will forward" in answered.json()["message"]
    assert ravis.seen == []


def test_an_id_that_climbs_out_of_the_stop_route_reaches_nothing(nervis: TestClient) -> None:
    """A slash is never part of a task id, even encoded: no route answers, and RAVIS hears
    nothing."""
    ravis = behind(nervis, FakeRavis(202, {"state": "stopping"}))

    for path in (f"/api/v1/ravis/codex/runs/{SID}%2F..%2F..%2Freprove/stop",
                 f"/api/v1/ravis/codex/runs/{SID}/../../reprove"):
        assert call(nervis, "POST", path, CONFIRMATION, key=KEY).status_code in (404, 405)
    assert ravis.seen == []


@pytest.mark.parametrize(
    "example", OWNER_STOP["examples"], ids=lambda example: example["name"]
)
def test_ravis_s_answer_to_a_stop_reaches_the_page_as_ravis_gave_it(
    nervis: TestClient, example: dict[str, Any],
) -> None:
    """Each asks the card to say something different — the task changed, nothing is running, too
    many stops, the key refused — so none may be flattened on the way."""
    response = example["response"]
    behind(nervis, FakeRavis(response["status"], response["body"]))

    answered = call(nervis, "POST", STOP, CONFIRMATION, key=KEY)

    assert (answered.status_code, answered.json()) == (response["status"], response["body"])


@pytest.mark.parametrize(
    "name", ["authorization", "Authorization", "AUTHORIZATION", "aUtHoRiZaTiOn"]
)
def test_configure_refuses_headers_that_would_replace_the_credential(name: str) -> None:
    """Raised before anything is sent — with RAVIS registered or not — so the mistake can't ship."""
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async def attempt(entry: Any) -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            await ravis_peer.configure(
                client, entry, "POST", "/api/v1/codex/sign-out", ADMIN, {},
                headers={name: "Bearer admin.someone-else"},
            )

    for entry in (ENTRY, None):
        with pytest.raises(ValueError, match="authorization"):
            asyncio.run(attempt(entry))
    assert sent == []


def test_the_credential_is_written_after_any_header_passed_in() -> None:
    """Behind `configure`'s refusal, a second line: even a header named authorization that reached
    the call itself is overwritten by NERVIS's own credential."""
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async def attempt() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            await ravis_peer._credential_call(
                client, "POST", "http://ravis.test/api/v1/codex/sign-out", ADMIN, {},
                headers={"authorization": "Bearer admin.someone-else"},
            )

    asyncio.run(attempt())
    [request] = sent
    assert request.headers.get_list("authorization") == [f"Bearer {ADMIN}"]


def test_configure_sends_its_headers_beside_the_credential() -> None:
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(202, json={"state": "stopping"})

    async def attempt() -> tuple[int, dict[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            return await ravis_peer.configure(
                client, ENTRY, "POST", f"/api/v1/agent-sessions/{SID}/owner-stop", ADMIN,
                {"source": "dashboard", "confirm": CONFIRMATION}, headers={"Idempotency-Key": KEY},
            )

    assert asyncio.run(attempt()) == (202, {"state": "stopping"})
    [request] = sent
    assert request.headers["idempotency-key"] == KEY
    assert request.headers["authorization"] == f"Bearer {ADMIN}"


# ── What NERVIS serves for Codex at all ──────────────────────────────────────


def test_no_nervis_route_reaches_another_agent_session_or_project_lock_route(
    nervis: TestClient,
) -> None:
    """Stop is the dashboard's only task control (owner decision (a)). RAVIS refuses NERVIS on
    every other agent-session route anyway; NERVIS holds the same line in its routes and in every
    RAVIS path its source names."""
    served = served_routes(nervis)
    assert not [path for _, path in served if "agent-sessions" in path or "project-locks" in path]
    assert [route for route in served if "/codex/runs" in route[1]] == [
        ("POST", "/api/v1/ravis/codex/runs/{sid}/stop")
    ]
    source = REPOSITORY / "nervis" / "src" / "nervis"
    naming = [
        line for file in source.rglob("*.py")
        for line in file.read_text(encoding="utf-8").splitlines()
        if "agent-sessions" in line or "project-locks" in line
    ]
    assert naming, "the Stop route's RAVIS path was not found, so this proved nothing"
    assert all("/owner-stop" in line and "project-locks" not in line for line in naming)


def test_nervis_serves_exactly_the_codex_control_routes_ravis_lists(nervis: TestClient) -> None:
    """`codex-admin.json` → `nervis_control_routes`, and nothing beside it but the read of the
    waiting sign-in (0.28.12), gated like the writes because it returns the sign-in page."""
    listed = {
        tuple(entry["nervis"].split(" ", 1))
        for entry in CODEX_ADMIN["nervis_control_routes"]["routes"]
    }
    served = {
        route for route in served_routes(nervis) if route[1].startswith("/api/v1/ravis/codex")
    }

    assert served - {("GET", "/api/v1/ravis/codex/sign-in")} == listed


# ── Removing an allowed site ─────────────────────────────────────────────────


def test_removing_a_site_reaches_ravis_with_the_admin_credential(nervis: TestClient) -> None:
    status, view = answer_of(ravis_route("DELETE", "/api/v1/codex/sites/{host}"), "removed")
    ravis = behind(nervis, FakeRavis(status, view))

    answered = call(nervis, "DELETE", "/api/v1/ravis/codex/sites/huggingface.co")

    assert (answered.status_code, answered.json()) == (status, view)
    [sent] = ravis.seen
    assert (sent.method, sent.url.path) == ("DELETE", "/api/v1/codex/sites/huggingface.co")
    assert sent.headers["authorization"] == f"Bearer {ADMIN}"


@pytest.mark.parametrize("host", [
    "*.crates.io", "a..b", "-bad.example", "bad-.example", ".leading.example", "trailing.example.",
    "x" * 254, "host.example:443", "user@host.example", "two words.example", "%2E%2E",
    "under_score.example",
])
def test_a_site_name_that_could_change_ravis_s_address_is_refused_before_ravis(
    nervis: TestClient, host: str,
) -> None:
    ravis = behind(nervis, FakeRavis(200, {"defaults": [], "added": []}))

    answered = call(nervis, "DELETE", f"/api/v1/ravis/codex/sites/{host}")

    assert answered.status_code == 400
    assert "is not a site name NERVIS will forward" in answered.json()["message"]
    assert ravis.seen == []


def test_a_slash_in_a_site_name_reaches_no_route(nervis: TestClient) -> None:
    ravis = behind(nervis, FakeRavis(200, {"defaults": [], "added": []}))

    for host in ("huggingface.co%2F..%2F..%2Freprove", "huggingface.co/../../reprove"):
        assert call(nervis, "DELETE", f"/api/v1/ravis/codex/sites/{host}").status_code in (404, 405)
    assert ravis.seen == []


# ── A new Codex build: the report, and accepting it ──────────────────────────


def test_the_version_report_reaches_ravis_and_outwaits_its_checks(nervis: TestClient) -> None:
    status, report = answer_of(
        ravis_route("GET", "/api/v1/codex/version-check"),
        "a new binary whose strict-rules surface changed",
    )
    ravis = behind(nervis, FakeRavis(status, report))

    answered = call(nervis, "GET", "/api/v1/ravis/codex/version-check")

    assert (answered.status_code, answered.json()) == (status, report)
    [sent] = ravis.seen
    assert (sent.method, sent.url.path) == ("GET", "/api/v1/codex/version-check")
    assert sent.headers["authorization"] == f"Bearer {ADMIN}"
    # RAVIS runs the new build's version and both schema trees at up to 30 s each, then starts it
    # twice in a throwaway home, each call there waiting up to 15 s.
    assert CODEX_VERSION_TIMEOUT_SECONDS > 3 * 30 + 6 * 15 > CODEX_CONTROL_TIMEOUT_SECONDS
    assert sent.extensions["timeout"]["read"] == CODEX_VERSION_TIMEOUT_SECONDS


def test_accepting_sends_the_reported_sha256_and_hands_back_codex_s_state(
    nervis: TestClient,
) -> None:
    route = ravis_route("POST", "/api/v1/codex/accept-version")
    status, state = answer_of(
        route, "accepted, and still paused until the re-test proves the file rules"
    )
    ravis = behind(nervis, FakeRavis(status, state))

    answered = call(nervis, "POST", "/api/v1/ravis/codex/accept-version", {"sha256": SHA256})

    assert (answered.status_code, answered.json()) == (status, state)
    # Accepting is trust, not proof: the build is accepted and its file rules stay unproven.
    assert state["runtime"]["verdict"] == "accepted"
    assert state["runtime"]["strict_rules"] == "unproven"
    [sent] = ravis.seen
    assert (sent.method, sent.url.path) == ("POST", "/api/v1/codex/accept-version")
    assert json.loads(sent.content) == {"sha256": SHA256}
    assert sent.extensions["timeout"]["read"] == CODEX_VERSION_TIMEOUT_SECONDS


# ── All four ─────────────────────────────────────────────────────────────────

#: (method, NERVIS path, page body, Idempotency-Key) for a request each route forwards.
FORWARDED = (
    ("POST", STOP, CONFIRMATION, KEY),
    ("DELETE", "/api/v1/ravis/codex/sites/huggingface.co", None, None),
    ("GET", "/api/v1/ravis/codex/version-check", None, None),
    ("POST", "/api/v1/ravis/codex/accept-version", {"sha256": SHA256}, None),
)


@pytest.mark.parametrize(("method", "path", "body", "status", "refusal"), [
    *card_refusals(),
    # Not among the contract's examples for these routes, and still RAVIS's to word.
    ("DELETE", "/api/v1/ravis/codex/sites/localhost", None, 422,
     {"error": {"code": "SITES_REFUSED", "message": "localhost isn't a public site.",
                "retryable": False, "details": {"refused": [{"host": "localhost",
                                                            "reason": "local_name"}]}}}),
    ("GET", "/api/v1/ravis/codex/version-check", None, 409,
     {"error": {"code": "CODEX_NOT_AVAILABLE",
                "message": "The Homebrew link does not lead to a file.",
                "retryable": False, "details": {}}}),
])
def test_ravis_s_refusal_reaches_the_page_as_ravis_said_it(
    nervis: TestClient, method: str, path: str, body: Any, status: int, refusal: Any,
) -> None:
    behind(nervis, FakeRavis(status, refusal))

    answered = call(nervis, method, path, body)

    assert (answered.status_code, answered.json()) == (status, refusal)


@pytest.mark.parametrize(("method", "path", "body", "key"), FORWARDED)
def test_no_answer_is_cached_or_carries_the_credential(
    nervis: TestClient, method: str, path: str, body: Any, key: str | None,
) -> None:
    behind(nervis, FakeRavis(200, {"state": "signed_in"}))

    answered = call(nervis, method, path, body, key=key)

    assert answered.headers["cache-control"] == "no-store"
    assert ADMIN not in answered.text
    assert all(ADMIN not in value for value in answered.headers.values())


@pytest.mark.parametrize(("method", "path", "body", "key"), FORWARDED)
def test_without_an_admin_credential_nothing_is_sent_and_the_page_is_told_why(
    tmp_path: Path, method: str, path: str, body: Any, key: str | None,
) -> None:
    with TestClient(create_app(settings_for(tmp_path, credential=""))) as client:
        ravis = behind(client, FakeRavis(200, {}))
        answered = call(client, method, path, body, key=key)

    assert answered.status_code == 403
    assert "admin credential" in answered.json()["message"]
    assert ravis.seen == []
