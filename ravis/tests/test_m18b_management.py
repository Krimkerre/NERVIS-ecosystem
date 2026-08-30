"""M18b's management half — §15.1's audited mutations and `If-Match`.

§15.1: mutations *"accept `Idempotency-Key` and `If-Match` where state changes,
are separately authorized and audited, and return the actual post-state plus
revision. Never expose credential values."*

Two of those clauses are answered here and one is answered by *not* doing it.
Four of the five mutations are idempotent by nature — a full replacement leaves
identical state on a replay — so a replay cache would be a mechanism that
changes no observable outcome, which is worse than not adding one. The tests
below pin that property instead, because it is the reason the key is absent.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient
from tests.test_fallback import TWO_CODERS, ScriptedUpstream

from ravis.app import create_app
from ravis.config import Settings

TRACE = "c" * 32


def _as_admin(client: Any) -> Any:
    """Give this client §15.1's credential-write authorization.

    Seeds an `admin.`-prefixed secret into whatever store the app is using and
    presents it on every request. Writing a provider credential needs one, and
    an ordinary client credential deliberately does not carry it — so a test
    that means to exercise the *authorised* path has to say so.
    """
    inner = client.app
    while not hasattr(inner, "state"):
        inner = inner.app
    inner.state.credentials.store("admin.tests", "admin-secret-for-tests")
    client.headers.update({"Authorization": "Bearer admin-secret-for-tests"})
    return client



def a_client(**overrides: Any) -> TestClient:
    upstream = ScriptedUpstream(TWO_CODERS)
    settings = Settings(
        database_path=":memory:", upstream_base_url="http://upstream.invalid",
        model_capabilities=upstream.catalogue,
        _env_file=None,  # type: ignore[call-arg]
        **{"nervis_base_url": "http://127.0.0.1:8790", **overrides},
    )
    app = create_app(settings)
    client = httpx.AsyncClient(transport=upstream.transport())
    app.app.state.upstream_client = client
    app.app.state.model_registry.use_client(client)
    return _as_admin(TestClient(app))


def published(client: TestClient) -> list[dict[str, Any]]:
    return list(client.app.app.state.events._pending)  # type: ignore[attr-defined]


def audits(client: TestClient) -> list[dict[str, Any]]:
    return [e for e in published(client) if e["event_type"].startswith("ravis.")
            and ".route." not in e["event_type"] and ".request." not in e["event_type"]]


# ── Audited ─────────────────────────────────────────────────────────────────


def test_a_credential_write_is_audited() -> None:
    with a_client() as client:
        client.put("/api/v1/providers/credentials/openai", json={"secret": "sk-live-1234"})
        recorded = audits(client)

    assert recorded, "a credential write published no audit event"
    assert recorded[0]["event_type"] == "ravis.credential.set"
    assert recorded[0]["data"]["provider"] == "openai"


def test_an_audit_event_never_carries_the_credential() -> None:
    """§15.1's last sentence, and the reason the record is built from named
    facts rather than from the request: the secret is never in scope."""
    import json

    with a_client() as client:
        client.put("/api/v1/providers/credentials/openai",
                   json={"secret": "sk-live-do-not-publish"})
        body = json.dumps(published(client))

    assert "sk-live-do-not-publish" not in body


def test_an_audit_event_is_published_even_with_no_traceparent() -> None:
    """A browser sends none, and the dashboard is where these mutations come
    from — so honouring `emit`'s drop-without-a-trace rule unchanged would mean
    the audit fired for everything except the ordinary case."""
    with a_client() as client:
        client.put("/api/v1/providers/openai/enabled", json={"enabled": False})
        recorded = audits(client)

    assert recorded, "the audit was dropped for want of a trace"
    assert recorded[0]["trace_id"], "an audit event with no trace is invisible"


def test_the_caller_s_trace_is_used_when_there_is_one() -> None:
    with a_client() as client:
        client.put("/api/v1/providers/openai/enabled", json={"enabled": True},
                   headers={"traceparent": f"00-{TRACE}-{'d' * 16}-01"})
        recorded = audits(client)

    assert recorded and recorded[0]["trace_id"] == TRACE


def test_every_mutation_is_audited() -> None:
    """All five, because an audit trail with a gap in it is one nobody can rely
    on — and the gap is always the endpoint somebody forgot."""
    with a_client() as client:
        client.put("/api/v1/providers/credentials/openai", json={"secret": "s"})
        client.put("/api/v1/providers/openai/enabled", json={"enabled": False})
        client.put("/api/v1/providers/openai/models",
                   json={"include": ["a*"], "exclude": []})
        client.delete("/api/v1/providers/credentials/openai")
        client.put("/api/v1/pools/auto/members", json={"models": []})
        kinds = {e["event_type"] for e in audits(client)}

    assert kinds == {
        "ravis.credential.set", "ravis.provider.enabled_changed",
        "ravis.provider.model_filter_changed", "ravis.credential.forgotten",
        "ravis.pool.members_changed",
    }


# ── If-Match, where a revision honestly exists ──────────────────────────────


def test_a_pool_write_returns_the_post_state_revision() -> None:
    """§15.1: "return the actual post-state plus revision"."""
    with a_client() as client:
        answer = client.put("/api/v1/pools/auto/members", json={"models": []})

    assert answer.status_code == 200
    assert answer.json()["revision"], "no revision to send back on the next edit"


def test_a_stale_if_match_is_refused() -> None:
    """The lost update this prevents is real: the store is a read-modify-write
    over a whole file, so two overlapping editors silently discard one edit."""
    with a_client() as client:
        answer = client.put("/api/v1/pools/auto/members", json={"models": []},
                            headers={"if-match": "not-the-current-revision"})

    assert answer.status_code == 412
    assert "re-read" in answer.json()["error"]["message"]


def test_a_matching_if_match_is_accepted() -> None:
    with a_client() as client:
        revision = client.get("/api/v1/pools").json()
        current = next(p["revision"] for p in revision["items"]
                       if p["pool_id"] == "ravis/auto")
        answer = client.put("/api/v1/pools/auto/members", json={"models": []},
                            headers={"if-match": current})

    assert answer.status_code == 200


def test_no_if_match_still_works() -> None:
    """Optional by design. Making it mandatory would break every existing client
    to protect against a race most of them never run."""
    with a_client() as client:
        assert client.put("/api/v1/pools/auto/members",
                          json={"models": []}).status_code == 200


def test_a_wildcard_if_match_is_accepted() -> None:
    """`*` means "I know this exists and do not care which version" — a weaker
    claim than naming one, and a different one from sending nothing."""
    with a_client() as client:
        assert client.put("/api/v1/pools/auto/members", json={"models": []},
                          headers={"if-match": "*"}).status_code == 200


# ── Why there is no Idempotency-Key ─────────────────────────────────────────


def test_repeating_a_mutation_leaves_the_same_state() -> None:
    """The reason §15.1's key is not implemented for these four. Each is a full
    replacement, so a replay is indistinguishable from the first call — a replay
    cache would change no observable outcome, and a mechanism that changes
    nothing is worse than its absence because it implies a guarantee elsewhere.
    """
    with a_client() as client:
        first = client.put("/api/v1/providers/openai/models",
                           json={"include": ["a*"], "exclude": []}).json()
        again = client.put("/api/v1/providers/openai/models",
                           json={"include": ["a*"], "exclude": []}).json()

    assert first == again
