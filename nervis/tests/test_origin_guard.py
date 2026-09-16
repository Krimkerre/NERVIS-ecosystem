"""A state-changing route refuses a request that did not come from this page.

**Reverifying §15's safety-gate line found a hole the control token does not
cover.** That token gates six RAVIS-proxy routes; every other mutating route in
this service — supervision, chat, background jobs, settings import, proposals,
and more — checked nothing about where a request came from. `app.py`'s own
former reasoning argued this was fine because CORS never enters the picture for
a same-origin dashboard; that argument is true and beside the point, because
CORS response headers govern whether a page's script may *read* a response,
never whether the browser sends the request or the server acts on it.

**Proved live before this file existed.** A forged `Origin` header and a body
declared `Content-Type: text/plain` — a browser "simple request", no preflight
— flipped NERVIS's supervision switch and wrote an attacker-chosen executable
and argument list into the adapter table, through the real app, with `json.loads
(await request.body())` never once asking what `Content-Type` said. Every test
below either proves that exact path is closed, or proves this closing it did
not also close anything legitimate.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis.api.origin_guard import expected_origins, refuses_cross_origin_mutation
from nervis.app import create_app
from nervis.config import Settings

# ── The pure decision, in isolation ─────────────────────────────────────────


def test_the_expected_origins_cover_every_served_host() -> None:
    origins = expected_origins(["127.0.0.1", "localhost", "::1"], 8790)
    assert origins == {
        "http://127.0.0.1:8790",
        "http://localhost:8790",
        # An IPv6 literal needs brackets in a URL authority; a name or IPv4
        # address must not have them, or nothing a browser ever sends matches.
        "http://[::1]:8790",
    }


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "get", "head"])
def test_a_safe_method_is_never_refused(method: str) -> None:
    """Reads stay open, whatever the headers say — the same rule the control
    token's own module states: a console that demanded a credential before
    drawing a health table would be a console nobody opens."""
    assert not refuses_cross_origin_mutation(
        method, {"origin": "https://evil.example"}, frozenset()
    )


def test_sec_fetch_site_cross_site_is_refused() -> None:
    assert refuses_cross_origin_mutation(
        "POST", {"sec-fetch-site": "cross-site"}, frozenset({"http://127.0.0.1:8790"})
    )


@pytest.mark.parametrize("value", ["same-origin", "same-site", "none"])
def test_sec_fetch_site_non_cross_site_values_are_allowed(value: str) -> None:
    assert not refuses_cross_origin_mutation(
        "POST", {"sec-fetch-site": value}, frozenset()
    )


def test_sec_fetch_site_wins_even_when_origin_would_disagree() -> None:
    """The more reliable signal is checked first, and only first.

    A page cannot set `Sec-Fetch-Site` itself — the browser does — which is
    exactly why it outranks `Origin`, a header a request can simply omit or
    (on the fallback path only) that an old client might get wrong.
    """
    assert not refuses_cross_origin_mutation(
        "POST",
        {"sec-fetch-site": "same-origin", "origin": "https://evil.example"},
        frozenset({"http://127.0.0.1:8790"}),
    )


def test_origin_is_the_fallback_when_sec_fetch_site_is_absent() -> None:
    expected = frozenset({"http://127.0.0.1:8790"})
    assert not refuses_cross_origin_mutation("POST", {"origin": "http://127.0.0.1:8790"}, expected)
    assert refuses_cross_origin_mutation("POST", {"origin": "https://evil.example"}, expected)


def test_neither_header_present_is_not_a_refusal() -> None:
    """Not a browser subject to fetch metadata or CORS at all — `TestClient`,
    another local process, this machine's own tooling. Refusing this would
    defend against a threat only a browser can create, using a signal only a
    browser sends, which `nervis.api.control`'s own docstring already accepts
    is not this file's boundary to hold."""
    assert not refuses_cross_origin_mutation("POST", {}, frozenset({"http://127.0.0.1:8790"}))


# ── Wired into the real app ─────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path),
        served_hosts=["127.0.0.1", "localhost", "::1", "testserver"],
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


EVIL = {"Content-Type": "text/plain", "Origin": "https://evil.example"}

# One mutating route per file this defect actually touched, each addressed
# exactly as the live proof-of-concept was: a body that parses as JSON despite
# a `Content-Type` that would never trigger a CORS preflight. Not the same
# route twice — the point is that the fix lives in one middleware, not in
# each of these files, and only reaching every file proves that.
CROSS_ORIGIN_TARGETS = (
    ("POST", "/api/v1/supervision/enable", b'{"enabled": true}'),
    ("POST", "/api/v1/chat", b'{"content": "hello"}'),
    ("POST", "/api/v1/background", b'{}'),
    ("POST", "/api/v1/recall/enable", b'{"enabled": true}'),
    ("DELETE", "/api/v1/proposals", b''),
)


@pytest.mark.parametrize(("method", "path", "body"), CROSS_ORIGIN_TARGETS)
def test_a_forged_cross_origin_request_is_refused(
    client: Any, method: str, path: str, body: bytes
) -> None:
    answered = client.request(method, path, content=body, headers=EVIL)
    assert answered.status_code == 403
    error = answered.json()["error"]
    assert error["code"] == "CROSS_ORIGIN_MUTATION_REFUSED"
    assert error["retryable"] is False


def test_the_original_finding_is_actually_closed(client: Any) -> None:
    """The exact sequence proved live before the fix: enable, then configure an
    adapter with an attacker-chosen executable and argument list. Both steps
    must be refused — closing only the first and leaving the second reachable
    would still let the finding's worse half through."""
    enable = client.post(
        "/api/v1/supervision/enable", content=b'{"enabled": true}', headers=EVIL
    )
    assert enable.status_code == 403
    # The origin guard's own refusal, not the control token's (NERVIS 0.34.15), so this
    # still proves the middleware closes it.
    assert enable.json()["error"]["code"] == "CROSS_ORIGIN_MUTATION_REFUSED"

    configure = client.post(
        "/api/v1/supervision/adapter/sirvis",
        content=b'{"executable": "/bin/bash", "args": ["-c", "echo pwned"], "cwd": ""}',
        headers=EVIL,
    )
    assert configure.status_code == 403
    assert configure.json()["error"]["code"] == "CROSS_ORIGIN_MUTATION_REFUSED"


def test_a_bare_post_with_no_body_is_refused_too(client: Any) -> None:
    """§12's `start`/`stop`/`restart` need no body at all — as plain a "simple
    request" as exists, and the one the eventual launch chain depends on."""
    answered = client.post(
        "/api/v1/supervision/sirvis/stop", headers={"Origin": "https://evil.example"}
    )
    assert answered.status_code == 403


def test_the_dashboards_own_calls_still_work(client: Any) -> None:
    """The fix has to distinguish, not merely refuse everything. `TestClient`'s
    default calls carry neither `Sec-Fetch-Site` nor `Origin` — the same shape
    the dashboard's own same-origin `fetch()` calls have always had — and must
    keep succeeding exactly as before."""
    answered = client.post(
        "/api/v1/supervision/enable", json={"enabled": True},
        headers={"x-nervis-control": client.app.state.control_token},
    )
    assert answered.status_code == 200
    assert answered.json() == {"enabled": True}


def test_a_real_browsers_own_same_origin_call_still_works(client: Any) -> None:
    """What a browser actually sends for the dashboard's own `fetch()` calls:
    `Sec-Fetch-Site: same-origin`, which every browser implementing fetch
    metadata attaches on its own and a page cannot override."""
    answered = client.post(
        "/api/v1/supervision/enable", json={"enabled": True},
        headers={"Sec-Fetch-Site": "same-origin",
                 "x-nervis-control": client.app.state.control_token},
    )
    assert answered.status_code == 200


def test_reads_stay_open_across_origins(client: Any) -> None:
    """The rule this whole file exists to keep true: a mutation is refused, a
    read is not. `nervis.api.control`'s six routes learned this lesson first."""
    for path in ("/api/v1/services", "/api/v1/supervision", "/api/v1/health"):
        assert client.get(path, headers={"Origin": "https://evil.example"}).status_code == 200
