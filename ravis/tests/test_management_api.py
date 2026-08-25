"""The read-only management surface (§15.1, M18a).

Two properties matter more than the payloads: nothing here mutates, and nothing
here can leak a credential.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.conftest_upstream import RecordingUpstream
from tests.test_transparent_proxy import _app_with

READ_ENDPOINTS = [
    "/api/v1/health",
    "/api/v1/pools",
    "/api/v1/models",
    "/api/v1/providers",
    "/api/v1/profiles",
    "/api/v1/policies",
    "/api/v1/route-decisions",
    "/api/v1/usage",
]


def _client() -> TestClient:
    client, _ = _app_with(RecordingUpstream())
    return client


def test_every_read_endpoint_answers() -> None:
    client = _client()
    with client:
        for path in READ_ENDPOINTS:
            assert client.get(path).status_code == 200, path


def test_list_responses_carry_the_required_envelope() -> None:
    """§15.1: `{items, next_cursor, snapshot_revision}`."""
    client = _client()
    with client:
        body = client.get("/api/v1/pools").json()

    assert set(body) == {"items", "next_cursor", "snapshot_revision"}


def test_no_endpoint_accepts_a_mutation() -> None:
    """M18a is reads only. A mutation needs Idempotency-Key, separate
    authorization and an audit event (§15.1) — none of which exist yet, and
    shipping the verb without them would be worse than not shipping it."""
    client = _client()
    with client:
        for path in READ_ENDPOINTS:
            assert client.post(path, json={}).status_code == 405, path


def test_a_provider_record_never_carries_a_credential_value() -> None:
    """§15.1's "never expose credential values", checked rather than assumed."""
    client, settings = _app_with(RecordingUpstream())
    settings.upstream_api_key = "super-secret-key"
    with client:
        body = client.get("/api/v1/providers").json()

    assert "super-secret-key" not in str(body)


def test_a_provider_record_says_whether_a_credential_is_set() -> None:
    """Presence is useful and safe; the value is neither."""
    client = _client()
    with client:
        record = client.get("/api/v1/providers").json()["items"][0]

    assert "credential_configured" in record
    assert isinstance(record["credential_configured"], bool)


def test_pool_membership_is_derived_not_stored() -> None:
    """§5.2: install a qualifying model and it joins with nobody editing a list.

    The agent pool requires tools and 32K context, which the fixture upstream
    publishes nothing about — so it must report itself unavailable rather than
    listing members it cannot justify.
    """
    client = _client()
    with client:
        pools = {p["pool_id"]: p for p in client.get("/api/v1/pools").json()["items"]}

    assert pools["ravis/clarvis-agent"]["available"] is False
    assert pools["ravis/clarvis-agent"]["members"] == []


def test_model_capabilities_carry_their_provenance() -> None:
    """§15.1 asks for capability-evidenced results.

    A capability believed because it was measured is a different claim from one
    believed because a catalogue said so; flattening them hides the disagreement
    that matters.
    """
    client = _client()
    with client:
        items = client.get("/api/v1/models").json()["items"]

    text = items[0]["capabilities"]["text"]
    assert text["state"] == "SUPPORTED"
    assert text["provenance"] == "DEFAULT"


def test_a_routed_request_appears_in_the_decision_log() -> None:
    """The endpoint this milestone exists for."""
    client = _client()
    with client:
        client.post("/v1/chat/completions", json={"model": "ravis/clarvis-chat"})
        decisions = client.get("/api/v1/route-decisions").json()["items"]

    assert decisions
    assert decisions[0]["pool"] == "ravis/clarvis-chat"
    assert decisions[0]["decision_id"]


def test_a_refused_request_is_recorded_too() -> None:
    """A no-route is the decision most worth being able to look at afterwards."""
    client = _client()
    with client:
        client.post("/v1/chat/completions", json={"model": "ravis/clarvis-agent"})
        decisions = client.get("/api/v1/route-decisions").json()["items"]

    assert decisions[0]["selected"] is None
    assert decisions[0]["excluded"] or decisions[0]["reason"]


def test_one_decision_can_be_fetched_by_id() -> None:
    client = _client()
    with client:
        client.post("/v1/chat/completions", json={"model": "ravis/clarvis-chat"})
        first = client.get("/api/v1/route-decisions").json()["items"][0]
        fetched = client.get(f"/api/v1/route-decisions/{first['decision_id']}").json()

    assert fetched["decision_id"] == first["decision_id"]


def test_an_aged_out_decision_says_so_rather_than_erroring_vaguely() -> None:
    client = _client()
    with client:
        response = client.get("/api/v1/route-decisions/doesnotexist")

    assert response.status_code == 404
    assert "age out" in response.json()["error"]["message"]


def test_usage_reports_cost_as_unknown_rather_than_zero() -> None:
    """§14 forbids presenting an estimate as an invoice, and a dashboard reading
    "€0.00" would be a confident lie."""
    client = _client()
    with client:
        usage = client.get("/api/v1/usage").json()

    assert usage["spend_today"] is None
    assert usage["cost_available"] is False


def test_policies_are_empty_until_the_policy_engine_exists() -> None:
    """Empty is the honest answer: no policy is in force, so showing none is
    accurate and showing an invented default would misrepresent RAVIS."""
    client = _client()
    with client:
        assert client.get("/api/v1/policies").json()["items"] == []


def test_health_reports_observed_target_state_alongside_the_live_probe() -> None:
    """§10's counters have to be visible somewhere, or they are only a comment.

    The probe and the observed history are separate keys on purpose: a provider
    can be reachable and open-circuited at the same time, and a single "healthy"
    boolean would have to pick one of those to report.

    **The target is named `default`, not `upstream`.** Health is scoped per
    provider, and a provider-scoped circuit takes out every model behind it — so
    once M8 made upstreams plural, one shared label meant a single failing
    runtime opened the breaker for the entire catalogue. The scope is now the
    upstream's own name, and `default` is the name `upstreams.py` assigns when a
    deployment uses the singular settings. Keeping "upstream" for that case and
    real names for the plural one would leave two vocabularies for one thing.
    """
    client = _client()
    with client:
        client.post("/v1/chat/completions", json={"model": "any"})
        body = client.get("/api/v1/health").json()

    observed = {target["target"]: target for target in body["targets"]}
    assert observed["default"]["state"] == "CLOSED"
    assert observed["default"]["successes"] == 1
    assert observed["any"]["scope"] == "model"
    # Only what was actually called. A model that was considered and not chosen
    # has no health record, because nothing has happened to it.
    assert set(observed) == {"default", "any"}


def test_health_reports_nothing_observed_before_any_traffic() -> None:
    """An empty list, not a missing key: nothing seen is not nothing wrong."""
    client = _client()
    with client:
        body = client.get("/api/v1/health").json()

    assert body["targets"] == []
