"""The `Host` header, checked — DNS rebinding's only real defence.

**Why `Origin` is not enough, which is the whole reason this exists.** A page on
`attacker.example` whose DNS is re-pointed at `127.0.0.1` reaches RAVIS as a
*same-origin* request. Browsers omit `Origin` on same-origin GETs, so
`check_origin` sees nothing to reject and lets it through — its own comment says
"no Origin header means no browser made this request", which is true of curl and
false of exactly this attack.

What the attacker's request cannot hide is where it thinks it is going: the
`Host` header still reads `attacker.example`, because that is the name the
browser resolved. RAVIS binds loopback and nothing else can start (§16 item 2),
so any `Host` that is not a loopback name is a request that took a route RAVIS
does not have.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ravis.app import create_app
from ravis.config import Settings


@pytest.fixture()
def client() -> Any:
    app = create_app(Settings(database_path=":memory:", _env_file=None))  # type: ignore[call-arg]
    with TestClient(app) as ready:
        yield ready


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:8731", "localhost:8731", "[::1]:8731", "127.0.0.1", "localhost"],
)
def test_a_loopback_host_is_served(client: Any, host: str) -> None:
    """Every spelling a real client uses, including without a port.

    Asserted per spelling because a host check that only knew `127.0.0.1:8731`
    would refuse the dashboard, the launcher and every `curl localhost` — and be
    discovered as "RAVIS is down" rather than as a rule doing its job.
    """
    assert client.get("/api/v1/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["attacker.example", "attacker.example:8731", "ravis.internal"])
def test_a_foreign_host_is_refused(client: Any, host: str) -> None:
    """The rebinding request, which `check_origin` cannot see.

    A GET, deliberately: the read side is where `Origin` is absent and where the
    disclosure happens. Refusing the write side alone would leave every model,
    pool, policy and route decision readable by a page that renamed itself.
    """
    refused = client.get("/api/v1/health", headers={"Host": host})

    assert refused.status_code == 403
    assert "host" in refused.json()["error"]["message"].lower()


def test_a_request_with_no_host_header_is_refused(client: Any) -> None:
    """HTTP/1.1 requires one, so its absence is not an ordinary client."""
    assert client.get("/api/v1/health", headers={"Host": ""}).status_code == 403


def test_a_simple_content_type_cannot_reach_chat_completions(client: Any) -> None:
    """The second half of the rebinding chain, found by reading rather than by me.

    `admission.py` argues that `/v1/chat/completions` is safe from a page because
    "a JSON body always preflights, so this permits an allow-listed origin and
    nobody else". Nothing on the server made the body JSON: the handler read raw
    bytes and `json.loads` them whatever the header said, so a form POST with
    `enctype="text/plain"` — which is CORS-safelisted and sends no preflight —
    was accepted and ran inference on the operator's account.

    The Host check above breaks the same chain a step earlier. This closes it at
    the step the comment already claimed was closed, which is the more useful
    place for it: the claim becomes true rather than aspirational.
    """
    refused = client.post(
        "/v1/chat/completions",
        headers={"Host": "127.0.0.1:8731", "Content-Type": "text/plain"},
        content='{"model":"ravis/chat","messages":[{"role":"user","content":"hi"}]}',
    )

    assert refused.status_code == 415
    assert "json" in refused.json()["error"]["message"].lower()


def test_json_is_still_accepted(client: Any) -> None:
    """The falsifier. A content-type rule that refused everything would pass the
    test above and take the whole service with it."""
    answered = client.post(
        "/v1/chat/completions",
        headers={"Host": "127.0.0.1:8731", "Content-Type": "application/json"},
        json={"model": "ravis/chat", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert answered.status_code != 415
