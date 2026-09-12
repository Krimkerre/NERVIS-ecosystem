"""The four configuration writes NERVIS proxies to RAVIS (§16 item 4).

RAVIS stopped treating a loopback bind as authorization for enabling a
provider, narrowing a catalogue or re-pointing a pool. The credential that
replaced it is an `admin.`-prefixed one, and the browser must never hold it —
so these writes travel through NERVIS, which already held it for the credential
writes it was proxying before.

What is worth testing here is not the HTTP plumbing but the two ways this layer
fails quietly: forwarding without the header, and reporting a refusal as
success.
"""

from __future__ import annotations

import json

import httpx
import pytest

from nervis.peers import ravis as ravis_peer


class _Entry:
    """The shape `configure` reads off a registry entry."""

    def __init__(self, base_url: str = "http://ravis.invalid") -> None:
        self.declaration = type("_D", (), {"base_url": base_url})()


@pytest.mark.asyncio()
async def test_the_admin_credential_is_presented_on_every_configuration_write() -> None:
    """The header is the whole point of the hop.

    Without it RAVIS answers 403 and the Providers screen stops working — which
    is a visible failure. The dangerous version is the opposite: a future edit
    that drops the header on one of the four and leaves the other three, found
    only when somebody notices one button is broken.
    """
    seen: list[tuple[str, str, str | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    writes = [
        ("PUT", "/api/v1/providers/openai/enabled", {"enabled": False}),
        ("PUT", "/api/v1/providers/openai/models", {"allow": []}),
        ("PUT", "/api/v1/pools/chat/members", {"models": []}),
        ("POST", "/api/v1/pools/curate", None),
    ]
    for method, path, body in writes:
        status, _ = await ravis_peer.configure(
            client, _Entry(), method, path, "admin.secret", body
        )
        assert status == 200

    assert len(seen) == 4
    for _, path, authorization in seen:
        assert authorization == "Bearer admin.secret", path


@pytest.mark.asyncio()
async def test_no_credential_is_refused_here_rather_than_sent_unauthenticated() -> None:
    """A missing credential is NERVIS's problem to report, not RAVIS's to refuse.

    Forwarding without one would produce a 403 from RAVIS that reads as "RAVIS
    refused you" when the truth is "NERVIS was never given a credential" — two
    different fixes, and the screen would name the wrong one.
    """
    called = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    status, body = await ravis_peer.configure(
        client, _Entry(), "PUT", "/api/v1/providers/openai/enabled", "", {"enabled": True}
    )

    assert status == 403
    assert "admin credential" in body["message"]
    assert not called, "nothing should have been sent upstream"


@pytest.mark.asyncio()
async def test_a_dashboard_read_carries_nervis_s_credential_and_ravis_s_answer_untouched() -> None:
    """Found 12 September 2026: the page read RAVIS itself, anonymously, sharing one
    allowance of sixty a minute with every other tab and script. Through NERVIS the read
    is named, and what RAVIS answered — a refusal included — reaches the page as it was.
    """
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, json={"error": {"code": "rate_limited"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    status, body, media_type, failure = await ravis_peer.relay_read(
        client, _Entry(), "api/v1/route-decisions", "limit=200", "client.nervis"
    )

    assert (status, failure) == (429, "")
    assert json.loads(body) == {"error": {"code": "rate_limited"}}
    assert media_type.startswith("application/json")
    assert seen[0].headers["authorization"] == "Bearer client.nervis"
    assert str(seen[0].url) == "http://ravis.invalid/api/v1/route-decisions?limit=200"


@pytest.mark.asyncio()
async def test_a_ravis_that_does_not_answer_is_marked_for_the_page() -> None:
    """The marker is what lets the page say "did not answer" rather than "answered 502"."""

    async def refuse(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async def stall(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    for handler, expected in ((refuse, (502, "unreachable")), (stall, (504, "slow"))):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        status, _, _, failure = await ravis_peer.relay_read(
            client, _Entry(), "api/v1/usage", "", "client.nervis"
        )
        assert (status, failure) == expected
    status, _, _, failure = await ravis_peer.relay_read(client, None, "api/v1/usage", "", "")
    assert (status, failure) == (503, "unreachable")


def test_only_management_and_ecosystem_paths_are_relayable() -> None:
    assert ravis_peer.relayable("api/v1/pools/clarvis-agent/members")
    assert ravis_peer.relayable("ecosystem/capabilities")
    assert not ravis_peer.relayable("v1/chat/completions"), "never the gateway itself"
    assert not ravis_peer.relayable("api/v1/../../v1/models")
    assert not ravis_peer.relayable("api/v1//usage")


@pytest.mark.asyncio()
async def test_an_unregistered_ravis_is_503_rather_than_a_crash() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    status, body = await ravis_peer.configure(
        client, None, "POST", "/api/v1/pools/curate", "admin.secret"
    )

    assert status == 503
    assert "not registered" in body["message"]


@pytest.mark.asyncio()
async def test_a_refusal_upstream_travels_verbatim() -> None:
    """403 from RAVIS must not be flattened into a 200 with an error inside.

    The status is what the screen branches on; a proxy that swallows it turns a
    refused write into one that silently did nothing.
    """
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "needs an admin credential"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    status, body = await ravis_peer.configure(
        client, _Entry(), "PUT", "/api/v1/providers/openai/enabled", "wrong", {"enabled": True}
    )

    assert status == 403
    assert "admin credential" in body["error"]["message"]
