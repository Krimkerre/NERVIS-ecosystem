"""The read-only management surface (§15.1, M18a).

Two properties matter more than the payloads: nothing here mutates, and nothing
here can leak a credential.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from tests.conftest_upstream import RecordingUpstream
from tests.test_transparent_proxy import _app_with

from ravis.app import create_app
from ravis.config import Settings
from ravis.policy import ApplicationPolicies, PrivacyLevel, RoutingPolicy

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


def test_a_read_endpoint_does_not_accept_a_write() -> None:
    """The read surface stays a read surface.

    This was `test_no_endpoint_accepts_a_mutation`, and it asserted that the
    whole module accepted no mutation -- by POSTing to eight *collection* paths.
    It could not see a `PUT` on an item path, so it went on passing after
    `PUT /pools/{pool_key}/members` shipped and began writing routing state to
    disk. A test that checks a claim by a method the claim does not involve
    proves nothing about it, and this one held the module's docstring in place
    while the docstring was wrong.
    """
    client = _client()
    with client:
        for path in READ_ENDPOINTS:
            assert client.post(path, json={}).status_code == 405, path


def test_every_write_this_surface_serves_is_one_of_the_five_it_declares() -> None:
    """The mutation surface, asserted rather than described.

    RAVIS serves five writes: two on a provider credential, one on a provider's
    enabled flag, one on its model filter, and one on a pool's membership. Each
    is deliberate; what is not acceptable is a sixth appearing without anybody
    noticing, which is exactly what happened to the fifth -- it shipped while
    the module said "Reads only. No endpoint here mutates", and without the
    authorization that sentence implied.

    Read off the published OpenAPI document rather than a hand-kept list, so a
    new verb has to be added here on purpose.
    """
    client = _client()
    with client:
        document = client.get("/openapi.json").json()

    writes = {
        f"{method.upper()} {path}"
        for path, operations in document["paths"].items()
        for method in operations
        if method in ("put", "post", "delete", "patch") and path.startswith("/api/v1/")
    }

    assert writes == {
        "PUT /api/v1/providers/credentials/{name}",
        "DELETE /api/v1/providers/credentials/{name}",
        "PUT /api/v1/providers/{name}/enabled",
        "PUT /api/v1/providers/{name}/models",
        "PUT /api/v1/pools/{pool_key}/members",
    }, "a write appeared or vanished on the management surface"


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
    "€0.00" would be a confident lie.

    The field is `spend_estimated` since M15, not `spend_today`. The rename is
    the point: a consumer reads the key, not the documentation beside it, and a
    field called `spend` on a figure RAVIS cannot stand behind is the exact
    misreading §14 rules out.
    """
    client = _client()
    with client:
        usage = client.get("/api/v1/usage").json()

    # Nothing has run, so nothing has been priced — and that is reported as
    # unknown rather than as nought spent.
    assert usage["spend_estimated"] is None
    assert usage["cost_available"] is False
    assert usage["calls_priced"] == 0


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


def test_a_shared_name_does_not_give_one_provider_another_s_catalogue() -> None:
    """A transparent upstream and a translated provider may answer to one name.

    Declaring `google` in RAVIS_UPSTREAMS alongside the translated Gemini
    provider does exactly that, and the catalogue was resolved by name — so the
    translated row reported the transparent upstream's model count as its own.
    Anthropic never hit this only because nobody declares an upstream called
    `anthropic`.

    A count of nothing and no count at all are different claims (§9.4), and the
    wrong provider's count is worse than either.
    """
    settings = Settings(
        database_path=":memory:",
        upstreams='[{"name": "google", "kind": "google"}]',
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    with TestClient(app) as client:
        rows = [r for r in client.get("/api/v1/providers").json()["items"]
                if r["name"] == "google"]

    assert len(rows) == 2, "both the transparent upstream and the translated provider"
    by_mode = {r["protocol_mode"]: r for r in rows}
    assert "catalogue_size" in by_mode["OPENAI_TRANSPARENT"]
    assert "catalogue_size" not in by_mode["TRANSLATED"], (
        "a translated provider has no catalogue to report"
    )


def test_the_policies_endpoint_reports_what_is_actually_in_force() -> None:
    """Empty still means "none configured" — but now because the file says so.

    This returned `[]` unconditionally until M16, with the honest note that no
    policy engine existed. A dashboard reading an empty list can now say
    "unrestricted" and be right, which it could not before.
    """
    client = _client()
    client.app.app.state.policies = ApplicationPolicies(
        by_application={"nervis": RoutingPolicy(excluded_models=("*-preview*",))},
        default=RoutingPolicy(privacy=PrivacyLevel.LOCAL_PREFERRED),
    )
    with client:
        rows = client.get("/api/v1/policies").json()["items"]

    by_id = {row["application_id"]: row["constraints"] for row in rows}
    # The default is listed as `*`, because an application with no row of its
    # own is governed by it — omitting it would leave a reader unable to answer
    # "what applies to Clarvis" from this response.
    assert by_id["*"] == ["privacy level LOCAL_PREFERRED"]
    assert by_id["nervis"] == ["models excluded: *-preview*"]


def test_an_unconfigured_deployment_reports_no_policy() -> None:
    """"Nothing configured" must stay distinguishable from "policy hidden"."""
    client = _client()
    client.app.app.state.policies = ApplicationPolicies()
    with client:
        assert client.get("/api/v1/policies").json()["items"] == []
