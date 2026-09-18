"""Changing RAVIS's configuration through NERVIS needs the page's own token.

**The gate §16 item 4 closed, re-opened one hop up.** RAVIS stopped treating a
loopback bind as authorization: every management mutation there needs an admin
credential, and anonymous or ordinary inference callers get 403. NERVIS holds
that credential and proxies seven of those mutations — provider enable, model
filters, pool members, pool curation, credential write and delete, and lifting a
tool-refusal suppression — and, since 13 September 2026, Codex's ChatGPT sign-in
(start, read, cancel, sign out, confirm the account), and since 14 September the
Codex card's four (a task's Stop, removing a site, the version report, accepting a
version) — so until
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

# Clients here carry no token unless a test gives one (`conftest.page_control_token`).
SENDS_NO_CONTROL_TOKEN = True

# The proxies, as (method, path, body). Written out rather than derived from
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
    # RAVIS's budgets, from RAVIS → Spending (18 September 2026).
    ("PUT", "/api/v1/ravis/budgets", {"budgets": []}),
    ("PUT", "/api/v1/ravis/credentials/openai", {"secret": "not-a-real-secret"}),
    ("DELETE", "/api/v1/ravis/credentials/openai", None),
    # A model id with its slash left in, which is how the Diagnostics screen sends
    # it: the route takes the id as a path, and the slash is part of the id.
    ("POST", "/api/v1/ravis/health/suppressions/qwen/qwen3-1.7b/lift", None),
    # Codex's ChatGPT sign-in, from RAVIS → Credentials (13 September 2026). The GET
    # is here too: it is a read, but while a sign-in waits it carries the way into it.
    ("POST", "/api/v1/ravis/codex/sign-in", {"method": "browser"}),
    ("GET", "/api/v1/ravis/codex/sign-in", None),
    ("DELETE", "/api/v1/ravis/codex/sign-in", None),
    ("POST", "/api/v1/ravis/codex/sign-out", {}),
    ("POST", "/api/v1/ravis/codex/account/confirm", {"email_hint": None}),
    # The Codex card on RAVIS → Dashboard (14 September 2026): a task's Stop, removing a site
    # the owner added, and a new Codex build's report and acceptance. The version report is a
    # read gated like the writes, because RAVIS runs the checks on request. The Stop carries no
    # Idempotency-Key here, so past the gate NERVIS refuses it and nothing reaches RAVIS.
    ("POST", "/api/v1/ravis/codex/runs/as_01J9ZK4T6Q8M2V7R3N5B1C0D/stop",
     {"project": "add-utc-demo", "turn_id": "019a1c2e-8c4f-7a21-b5d3-3e6c9b7f2a41"}),
    ("DELETE", "/api/v1/ravis/codex/sites/example.invalid", None),
    ("GET", "/api/v1/ravis/codex/version-check", None),
    ("POST", "/api/v1/ravis/codex/accept-version", {"sha256": "0" * 64}),
    # A skill's switch on NERVIS → Skills, for Codex or for the other models (NERVIS 0.32.0; the
    # Codex card's own switch before), forwarded with the admin credential.
    ("POST", "/api/v1/ravis/skills", {"path": "/x/SKILL.md", "engine": "models", "enabled": True}),
    # The skill store on NERVIS → Skills (NERVIS 0.33.0, RAVIS 0.28.0): reviews, installs, updates,
    # removals and the marketplace, each forwarded with the admin credential.
    ("POST", "/api/v1/ravis/skills/previews", {"origin": "github", "url": "https://github.com/o/r"}),
    ("POST", "/api/v1/ravis/skills/previews/zip", {}),
    ("POST", "/api/v1/ravis/skills/previews/discard", {"preview_id": "sp_review"}),
    ("POST", "/api/v1/ravis/skills/installs", {"preview_id": "sp_review"}),
    ("POST", "/api/v1/ravis/skills/installs/update-preview", {"name": "pdf"}),
    ("POST", "/api/v1/ravis/skills/installs/remove", {"name": "pdf"}),
    ("POST", "/api/v1/ravis/skills/market/refresh", {}),
    ("POST", "/api/v1/ravis/skills/market/resolve", {"source": "voltagent", "links": []}),
    ("POST", "/api/v1/ravis/skills/market/search", {"query": "pdf"}),
    ("POST", "/api/v1/ravis/skills/market/sources", {"kind": "github", "repository": "o/r"}),
    ("POST", "/api/v1/ravis/skills/market/sources/hide", {"source": "openai", "hidden": True}),
    ("POST", "/api/v1/ravis/skills/market/sources/remove", {"source": "own-source"}),
    # NERVIS's own writes that name a program to run or store a provider key (NERVIS 0.34.15;
    # design/security/review-2026-09-16.md, S1 and S2). Refused before the service or verb is
    # looked at, so an unknown one is a 403 here, not a 404.
    ("POST", "/api/v1/supervision/enable", {"enabled": True}),
    ("POST", "/api/v1/supervision/adapter/sirvis",
     {"executable": "/bin/bash", "args": ["-c", "echo pwned"], "cwd": ""}),
    ("POST", "/api/v1/supervision/sirvis/stop", None),
    ("POST", "/api/v1/supervision/sirvis/circuit/clear", None),
    ("PUT", "/api/v1/voice/credential", {"secret": "not-a-real-key"}),
    ("DELETE", "/api/v1/voice/credential", None),
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


def test_a_head_request_for_the_page_is_answered_and_carries_no_token(client: Any) -> None:
    """How a Linux desktop opens a link: `gio open` asks the page's type with a HEAD first.

    A 405 there meant the launcher's "open the dashboard" opened nothing on Linux — and gio's
    error line printed the address, token and all, into the launcher's log (18 September 2026).
    The answer is GET's headers with no body, so the token goes nowhere.
    """
    for path in ("/index.html", "/"):
        answer = client.head(path, follow_redirects=False)
        assert answer.status_code in (200, 307), (path, answer.status_code)
        assert answer.content == b""
    assert client.head("/index.html").headers["content-type"].startswith("text/html")


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


def test_no_route_that_reads_the_ravis_admin_credential_is_left_ungated() -> None:
    """`MUTATIONS` above is a list a route can fall off of. This is the gate.

    **Not "any non-GET route under `/api/v1/ravis`."** `/api/v1/ravis/{surface}`
    accepts POST too — §14.3's negotiated recommendations take a body — and reads
    nothing of RAVIS's configuration; gating it would refuse a read for holding no
    control token, which is exactly the console-that-asks-for-a-credential mistake
    this module's own docstring rejects. The line that actually matters is whether
    a route's handler ever reaches `settings.ravis_admin_credential` — that value
    is RAVIS's own configuration authority, and any code path holding it can act on
    RAVIS's behalf whether or not it happens to arrive by PUT, POST or DELETE.

    **Found by walking the real route table, not a copy of it.** FastAPI wraps an
    included router lazily now; `_IncludedRouter.original_router` is the escape
    hatch back to routes with a normal `.dependant` on them. A handler is said to
    "reach" the credential when its compiled bytecode names it — `co_names` holds
    every attribute FastAPI's dependency injection could not have hidden, since
    the reference has to survive to be read at all.

    **Proved to fail, not just written to pass.** Commenting out `require_control`
    on any one of the six turns this red; the module's other tests only prove the
    six that already remember to ask are guarded; this is the one that would have
    noticed a seventh that forgot.
    """
    from fastapi.routing import _IncludedRouter

    from nervis.api.control import require_control
    from nervis.app import create_app
    from nervis.config import Settings

    settings = Settings(  # type: ignore[call-arg]
        database_path=":memory:", workspace_path="/tmp",
        served_hosts=["127.0.0.1", "localhost", "::1", "testserver"],
        _env_file=None,
    )
    app = create_app(settings)

    def touches_the_credential(route: Any) -> bool:
        code = getattr(getattr(route, "endpoint", None), "__code__", None)
        return code is not None and "ravis_admin_credential" in code.co_names

    def gated(route: Any) -> bool:
        dependant = getattr(route, "dependant", None)
        return dependant is not None and any(
            dependency.call is require_control for dependency in dependant.dependencies
        )

    ravis_routes = [
        route
        for included in app.routes
        if isinstance(included, _IncludedRouter)
        for route in included.original_router.routes
        if getattr(route, "path", "").startswith("/api/v1/ravis")
    ]
    # If FastAPI's internals move again, this fails loudly rather than passing on
    # an empty list — the same "would pass vacuously" guard the milestone-state
    # check in test_m4_chat.py carries for the same reason.
    listed = sum(1 for _, path, _ in MUTATIONS if path.startswith("/api/v1/ravis"))
    assert len(ravis_routes) >= listed, (
        f"found only {len(ravis_routes)} /api/v1/ravis routes; the real table has "
        f"at least {listed} — route discovery is broken, not the gate"
    )

    ungated = [
        f"{sorted(route.methods - {'HEAD'})} {route.path}"
        for route in ravis_routes
        if touches_the_credential(route) and not gated(route)
    ]
    assert not ungated, (
        "these routes read settings.ravis_admin_credential and do not require "
        f"the control token: {ungated}"
    )

    # And the reverse mistake, so `MUTATIONS` cannot silently drift ahead of the
    # code: every literal example above has to land on a route that touches the
    # credential, or the list is exercising something that has moved. Matched
    # through Starlette's own path compiler rather than string equality, because
    # `MUTATIONS` holds instantiated examples ("providers/openai/enabled") and the
    # route table holds templates ("providers/{name}/enabled").
    from starlette.routing import compile_path

    credentialed = [route for route in ravis_routes if touches_the_credential(route)]
    for method, literal_path, _ in MUTATIONS:
        if not literal_path.startswith("/api/v1/ravis"):
            continue  # NERVIS's own guarded writes, which hold no RAVIS credential
        landed = [
            route for route in credentialed
            if method in route.methods and compile_path(route.path)[0].match(literal_path)
        ]
        assert landed, (
            f"{method} {literal_path} in MUTATIONS matches no route that touches "
            "the credential — the list is testing a path that has moved"
        )
    # And nothing touches the credential that MUTATIONS never named at all.
    unnamed = [
        route for route in credentialed
        if not any(
            method in route.methods and compile_path(route.path)[0].match(literal_path)
            for method, literal_path, _ in MUTATIONS
        )
    ]
    assert not unnamed, (
        f"these routes touch the credential and are not in MUTATIONS: "
        f"{[(sorted(r.methods - {'HEAD'}), r.path) for r in unnamed]}"
    )


def _write_routes(app: Any) -> list[tuple[str, str]]:
    """Every (method, concrete path) the app accepts a write on, from its real route table."""
    from fastapi.routing import APIRoute, _IncludedRouter

    routes: list[Any] = []
    for entry in app.routes:
        if isinstance(entry, _IncludedRouter):
            routes.extend(entry.original_router.routes)
        elif isinstance(entry, APIRoute):
            routes.append(entry)
    found = []
    for route in routes:
        path = getattr(route, "path", "")
        concrete = path.replace("{path:path}", "a/b").replace("{model:path}", "q/m")
        while "{" in concrete:
            start = concrete.index("{")
            concrete = concrete[:start] + "x" + concrete[concrete.index("}", start) + 1:]
        for method in sorted(getattr(route, "methods", None) or ()):
            if method not in ("GET", "HEAD", "OPTIONS"):
                found.append((method, concrete))
    return found


def test_every_write_the_page_makes_needs_the_token(client: Any) -> None:
    """NERVIS 0.34.17: one rule for every write under `/api/v1/`, found from the route table.

    A route added later is covered without being listed anywhere; only the writes that are not
    the page's to make (`control.NOT_THE_PAGES`) are left to their own guards.
    """
    from nervis.api import control

    writes = _write_routes(client.app)
    checked = [(m, p) for m, p in writes if p.startswith("/api/v1/")]
    assert len(checked) > 60, f"only {len(checked)} writes found; route discovery is broken"
    left_out = []
    for method, path in checked:
        if not control.needs_control(method, path):
            left_out.append(path)
            continue
        answered = client.request(method, path, json={})
        assert answered.status_code == 403, (method, path, answered.status_code)
        assert answered.json()["error"]["code"] == "CONTROL_TOKEN_REQUIRED", (method, path)
    assert sorted(set(left_out)) == [
        "/api/v1/events",
        "/api/v1/registry/instances",
        "/api/v1/registry/instances/x/x",
        "/api/v1/registry/instances/x/x/heartbeat",
    ]


def test_the_writes_that_are_not_the_page_s_keep_their_own_guards(client: Any) -> None:
    """Events, registration and SIRVIS's recommendation read go past the page's rule — and a
    registration still needs the enrollment secret."""
    assert client.post("/api/v1/events", json=[]).status_code == 202
    registration = client.post("/api/v1/registry/instances", json={})
    assert registration.status_code == 401
    recommendation = client.post("/api/v1/sirvis/recommendations", json={})
    assert recommendation.json().get("error", {}).get("code") != "CONTROL_TOKEN_REQUIRED"


@pytest.mark.parametrize(("method", "path", "needed"), [
    ("POST", "/api/v1/chat", True),
    ("delete", "/api/v1/learned", True),
    ("PUT", "/api/v1/workspace/files/a.txt", True),
    ("GET", "/api/v1/chat/conversations", False),
    ("OPTIONS", "/api/v1/chat", False),
    ("POST", "/code/login", False),
    ("POST", "/api/v1/events", False),
    ("POST", "/api/v1/sirvis/recommendations", False),
    ("POST", "/api/v1/sirvis/benchmarks", True),
    ("POST", "/api/v1/eventsx", True),
    ("POST", "/api/v1/registry/instancesx", True),
    ("POST", "/api/v1/sirvis/recommendations/x", True),
])
def test_which_requests_need_the_token(method: str, path: str, needed: bool) -> None:
    from nervis.api import control

    assert control.needs_control(method, path) is needed
