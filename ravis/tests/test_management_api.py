"""The read-only management surface (§15.1, M18a).

Two properties matter more than the payloads: nothing here mutates, and nothing
here can leak a credential.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from tests.conftest import as_administrator
from tests.conftest_upstream import RecordingUpstream
from tests.test_transparent_proxy import _app_with

from ravis.app import create_app
from ravis.config import Settings
from ravis.core.capabilities import Capability
from ravis.cost import CostState, UsageRecord
from ravis.credentials import CredentialStore
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
    "/api/v1/usage/daily",
]


def _client() -> TestClient:
    """The app, with an admin credential presented.

    Configuration writes need one from §16 item 4 onwards; the reads in this
    file never did and are unaffected by carrying it.
    """
    client, _ = _app_with(RecordingUpstream())
    client.headers.update(as_administrator(client.app.app.state.credentials))
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


def test_every_write_this_surface_serves_is_one_it_declares() -> None:
    """The mutation surface, asserted rather than described.

    RAVIS serves fifty writes: two on a provider credential, one on a provider's
    enabled flag, one on its model filter, one on a pool's membership, one
    curating every pool, one lifting a tool-refusal suppression, ten for
    Codex (sign-in, its cancellation, sign-out, account confirmation, accepting
    and revoking a build, the file-rules re-test, adding and removing an
    allowed site, and switching a skill), one switching a skill for either engine,
    thirteen for the skill store (a review from GitHub or a website, a review from a zip,
    discarding a review, installing or updating, looking for an update, removing, and the
    marketplace's refresh, look-ups, search and three on its sources),
    fourteen on Codex tasks (the agent-session relay, the owner Stop among them),
    and five on the project lock. Each is
    deliberate; what is not acceptable is another appearing without anybody
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
        # Added deliberately: the same narrowing the line above writes for one
        # pool, applied to every pool at once. It stores nothing a per-pool PUT
        # could not, and its undo is the same endpoint with `{"clear": true}`.
        "POST /api/v1/pools/curate",
        # Added deliberately, 12 September 2026: ends a tool-refusal suppression
        # before its half hour is up. Guarded and audited like the pool write;
        # it changes routing state held in memory and nothing on disk.
        "POST /api/v1/health/suppressions/{model}/lift",
        # Added deliberately, 13 September 2026 (M29's second increment, RAVIS.md
        # §15.1.2): Codex's browser sign-in, its cancellation, sign-out, confirming a
        # changed account, and accepting or revoking a Codex build. Each needs an
        # admin credential and is audited.
        "POST /api/v1/codex/sign-in",
        "DELETE /api/v1/codex/sign-in",
        "POST /api/v1/codex/sign-out",
        "POST /api/v1/codex/account/confirm",
        "POST /api/v1/codex/accept-version",
        # Added deliberately, 13 September 2026 (M29's third increment, RAVIS.md §15.1.2):
        # the agent-session relay. Every one but the owner Stop needs a Clarvis client
        # credential, and every one with `{sid}` that task's token; the owner Stop takes only
        # the owner's admin applications, stops and does nothing else. Each is tested in
        # `tests/test_agent_sessions_*.py`, with the route table in the identity file.
        "POST /api/v1/agent-sessions",
        "POST /api/v1/agent-sessions/{sid}/turns",
        "POST /api/v1/agent-sessions/{sid}/steer",
        "POST /api/v1/agent-sessions/{sid}/interrupt",
        "POST /api/v1/agent-sessions/{sid}/requests/{rid}/answer",
        "POST /api/v1/agent-sessions/{sid}/presence",
        "POST /api/v1/agent-sessions/{sid}/mode",
        "POST /api/v1/agent-sessions/{sid}/leftover",
        "POST /api/v1/agent-sessions/{sid}/settle-claim",
        "POST /api/v1/agent-sessions/{sid}/settle",
        "POST /api/v1/agent-sessions/{sid}/cancel",
        "DELETE /api/v1/agent-sessions/{sid}",
        # Added deliberately, 15 September 2026 (RAVIS 0.28.0, the owner's decisions): the skill
        # store, for NERVIS's Skills page alone. Every one needs an admin credential; installs,
        # updates, removals and source changes are audited without any file's contents. Held in
        # `tests/test_skill_store_routes.py`, `tests/test_skill_market.py` and the fixture
        # `tests/fixtures/skill-store/contract.json`.
        "POST /api/v1/skills/previews",
        "POST /api/v1/skills/previews/zip",
        "POST /api/v1/skills/previews/discard",
        "POST /api/v1/skills/installs",
        "POST /api/v1/skills/installs/update-preview",
        "POST /api/v1/skills/installs/remove",
        "POST /api/v1/skills/market/refresh",
        "POST /api/v1/skills/market/resolve",
        "POST /api/v1/skills/market/search",
        "POST /api/v1/skills/market/sources",
        "POST /api/v1/skills/market/sources/hide",
        "POST /api/v1/skills/market/sources/remove",
        "POST /api/v1/agent-sessions/{sid}/reissue-token",
        "POST /api/v1/agent-sessions/{sid}/owner-stop",
        # Added deliberately, 13 September 2026 (M29's fourth increment, RAVIS.md §15.1.2):
        # the project lock for Clarvis's own runs. Every one needs a Clarvis client credential;
        # heartbeat and release the lease too, transfer the lease or the holding task's token.
        # Each is tested in `tests/test_project_locks.py`; the route table in the identity file.
        "POST /api/v1/project-locks",
        "POST /api/v1/project-locks/{lid}/heartbeat",
        "POST /api/v1/project-locks/{lid}/release",
        "POST /api/v1/project-locks/{lid}/takeover",
        "POST /api/v1/project-locks/{lid}/transfer",
        "DELETE /api/v1/codex/accept-version/{sha256}",
        # The file-rules re-test: the one write that starts Codex work, so only the
        # owner's command-line credential may call it, never NERVIS's (F-A3).
        "POST /api/v1/codex/reprove",
        # Added deliberately, 14 September 2026 (R5, RAVIS.md §15.1.2): the sites Codex's
        # commands may reach. Adding takes only Clarvis's client credential (the owner's click
        # before a task), removing only an admin credential (NERVIS's Codex card); a default
        # is never removed, and both are audited. Tested in `tests/test_codex_sites.py`.
        "POST /api/v1/codex/sites",
        "DELETE /api/v1/codex/sites/{host}",
        # Added deliberately, 14 September 2026 (RAVIS 0.26.0): switching one of Codex's skills
        # on or off, NERVIS's Codex card's, through its control route. Admin only, only a skill
        # Codex lists, audited. Tested in `tests/test_codex_skills.py`.
        "POST /api/v1/codex/skills",
        # Added deliberately, 15 September 2026 (RAVIS 0.27.0): switching a skill on or off for
        # Codex or for the other models, NERVIS's Skills page's, through its control route. Admin
        # only, only a path RAVIS lists for that engine, audited. `tests/test_skills_routes.py`.
        "POST /api/v1/skills",
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


def test_a_decision_that_is_not_there_says_how_long_they_are_kept() -> None:
    """A 404 that is a fact about retention rather than about existence.

    A running RAVIS keeps its decisions in a table now, so the honest answer to
    an id nobody recognises is how long one would have lasted — not "it aged
    out", which was true of the bounded log and would now send a reader looking
    for a bound that no longer decides anything.
    """
    client = _client()
    with client:
        response = client.get("/api/v1/route-decisions/doesnotexist")

    assert response.status_code == 404
    assert "30 days" in response.json()["error"]["message"]


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


def test_daily_spend_is_one_row_a_day_and_a_reset_counts_from_its_moment() -> None:
    """Added 12 September 2026 with the dashboard's spending page and reset button.

    The calls sit three and two days back at ten in the morning, local time, so the
    day each lands on cannot change with the moment the test happens to run.
    """
    now = time.localtime()
    first = time.mktime((now.tm_year, now.tm_mon, now.tm_mday - 3, 10, 0, 0, 0, 0, -1))
    second = first + 86400.0
    calls = [
        (first, "claude-haiku-4-5", "nervis", 0.002),
        (second, "claude-sonnet-5", "clarvis", 0.01),
        (second + 60, "claude-sonnet-5", "clarvis", 0.02),
        (second + 120, "qwen-local", "clarvis", None),
    ]
    client = _client()
    with client:
        ledger = client.app.app.state.usage_ledger
        for at, model, application, cost in calls:
            ledger.record(UsageRecord(
                model=model, provider="anthropic", application_id=application, cost=cost,
                currency="USD" if cost else None, at=at,
                cost_state=CostState.ESTIMATED if cost else CostState.UNKNOWN,
            ))
        daily = client.get("/api/v1/usage/daily").json()
        reset = client.get("/api/v1/usage", params={"since": second + 30}).json()

    days = [time.strftime("%Y-%m-%d", time.localtime(at)) for at in (second, first)]
    assert [day["day"] for day in daily["items"]] == days, "newest first, one row a day"
    later = daily["items"][0]
    assert (later["calls"], later["calls_priced"], later["calls_unpriced"]) == (3, 2, 1)
    assert later["spend_estimated"] == pytest.approx(0.03)
    assert later["by_model"][0]["name"] == "claude-sonnet-5", "costliest first"
    assert [row["name"] for row in later["by_application"]] == ["clarvis"]
    assert daily["retention_days"] == 90

    assert (reset["spend_window"], reset["spend_since"]) == ("since", second + 30)
    assert (reset["calls_priced"], reset["calls_unpriced"]) == (1, 1)
    assert reset["spend_estimated"] == pytest.approx(0.02)


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
        # A dead address: with Google's default the app listed Google's real models at start-up,
        # which the off-the-machine guard caught (18 September 2026). The row still carries a
        # catalogue size — of nothing — which is what the test is about.
        upstreams='[{"name": "google", "kind": "google", "base_url": "http://127.0.0.1:9"}]',
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


def test_a_curated_pool_follows_a_catalogue_that_changes() -> None:
    """The question this design exists to answer: what happens when a provider
    ships a new model?

    Membership is derived, never stored, so a model published tomorrow that
    matches the pool's declared families is a member the first time anybody
    asks — and one that is withdrawn stops being one, with no action from an
    operator and nothing to re-run. Storing the computed list would break both:
    a stored list is a snapshot of the catalogue at the moment it was written,
    and this machine carried one of 549 models while the catalogue held 591.
    """
    from ravis.core.pools import POOLS_BY_ID

    chat = POOLS_BY_ID["ravis/chat"]
    before = ["anthropic/claude-haiku-4.5", "anthropic/claude-sonnet-5"]

    # A provider adds a model in a family this pool already declares, and
    # retires one it had.
    after = ["anthropic/claude-haiku-9", "anthropic/claude-sonnet-5"]

    assert "anthropic/claude-haiku-4.5" in chat.default_membership(before)
    joined = chat.default_membership(after)
    assert "anthropic/claude-haiku-9" in joined, "a new model in a declared family joins"
    assert "anthropic/claude-haiku-4.5" not in joined, "a withdrawn one leaves"


def test_curating_pins_nothing() -> None:
    """`POST /pools/curate` removes narrowings rather than writing one.

    The first version stored each pool's computed membership, which is the one
    change that would stop it following the catalogue at all — the endpoint
    would have frozen the very thing it exists to keep current.
    """
    client = _client()
    with client:
        # Pinned to a model the fixture catalogue actually holds — a PUT naming
        # something ineligible stores nothing, and the test would then be
        # asserting the release of a pin that was never made.
        listed = client.get("/api/v1/pools/chat/members").json()["items"]
        first = next(item["id"] for item in listed)
        client.put("/api/v1/pools/chat/members", json={"models": [first]})
        answered = client.post("/api/v1/pools/curate", json={})
        after = client.get("/api/v1/pools").json()["items"]

    assert answered.status_code == 200
    body = answered.json()
    assert body["stored"] is False
    assert any(row["was_pinned"] for row in body["pools"]), "the pin was there to release"
    pinned = [p for p in after if p["pool_id"] == "ravis/chat"][0]
    # Following the default again, not stuck on the single model that was pinned.
    assert pinned["member_count"] != 1


# ── §10: a keychain that hangs, through the route that reads one ─────────────


def test_a_hanging_keychain_does_not_hang_the_providers_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The store's own tests prove the fallthrough; this proves the screen.**
    A Keychain prompt on a display nobody is looking at is the failure mode
    worth preventing, and the listing is where an operator would hit it: it
    resolves a credential for every configured provider, so one lookup waiting
    on a dialog would take the whole page with it.

    `subprocess.run` is patched to raise the timeout `security` would produce
    after five seconds, and the row must still say where the credential came
    from instead of never arriving.
    """
    import subprocess

    from ravis import credentials as store_module

    monkeypatch.setattr(store_module.shutil, "which", lambda _: "/usr/bin/security")

    def hangs(*_: object, **__: object) -> object:
        raise subprocess.TimeoutExpired(cmd="security", timeout=5.0)

    monkeypatch.setattr(subprocess, "run", hangs)
    client, _ = _app_with(RecordingUpstream())
    # `client.app` is the outer middleware; the state lives on the app it wraps,
    # which is what every other test here reaches through too.
    client.app.app.state.credentials = CredentialStore(  # type: ignore[attr-defined]
        keychain=True,
        allow_environment=True,
        environment={"RAVIS_ANTHROPIC_API_KEY": "sk-from-the-environment"},
    )

    with client:
        rows = client.get("/api/v1/providers").json()["items"]

    assert rows, "the listing did not answer at all"
    assert all("credential_source" in row for row in rows)


# ── §10's bounded-queues outcome, on the log an operator reads ──────────────


def test_the_decision_log_is_bounded_and_says_where_it_starts() -> None:
    """**A log that grows with traffic is a memory leak with a UI.** The
    decision log is the one RAVIS keeps in memory, and every routed request
    appends to it — so its bound is what stands between a busy afternoon and a
    gateway that has to be restarted. Bounded is half of §10's outcome; the
    other half is that a reader can tell it is bounded rather than discovering
    it, which is what the aged-out 404 says in its own words.
    """
    from ravis.api.management.decisions import DecisionLog

    log = DecisionLog(capacity=5)
    client, _ = _app_with(RecordingUpstream())
    client.app.app.state.decision_log = log  # type: ignore[attr-defined]

    with client:
        for _ in range(20):
            client.post("/v1/chat/completions", json={"model": "any", "messages": []})
        listed = client.get("/api/v1/route-decisions?limit=200").json()["items"]

    assert len(listed) <= 5, f"the log kept {len(listed)} of 20 with a capacity of 5"
    assert listed, "a bounded log that keeps nothing is not observable either"


def test_a_decision_pushed_out_by_the_bound_reads_as_aged_out() -> None:
    """The observable half, in the case that actually happens: somebody follows
    a decision id from a trace an hour later. Saying "aged out" is a fact about
    the bound; a bare 404 would read as "that never happened"."""
    from ravis.api.management.decisions import DecisionLog

    client, _ = _app_with(RecordingUpstream())
    client.app.app.state.decision_log = DecisionLog(capacity=2)  # type: ignore[attr-defined]

    with client:
        client.post("/v1/chat/completions", json={"model": "any", "messages": []})
        first = client.get("/api/v1/route-decisions?limit=10").json()["items"]
        oldest = first[-1]["decision_id"] if first else ""
        for _ in range(5):
            client.post("/v1/chat/completions", json={"model": "any", "messages": []})
        gone = client.get(f"/api/v1/route-decisions/{oldest}")

    assert oldest, "no decision was recorded to push out"
    assert gone.status_code == 404
    assert "age out" in gone.json()["error"]["message"]


# ── Tool-refusal suppressions: visible on /health, and liftable early ───────


def test_health_lists_an_active_suppression_until_it_lifts() -> None:
    """The field the Diagnostics screen reads. A suppressed model still serves
    requests without tools, so this lists resting models, not broken ones."""
    now = [1000.0]
    client = _client()
    with client:
        health = client.app.app.state.health  # type: ignore[attr-defined]
        health.clock = lambda: now[0]
        health.suppress("qwen/qwen3-1.7b", "lmstudio", Capability.TOOLS,
                        "HTTP 400: this model does not support tools")
        during = client.get("/api/v1/health").json()["capability_suppressions"]
        now[0] += 1800.0
        after = client.get("/api/v1/health").json()["capability_suppressions"]

    assert during == [{
        "model": "qwen/qwen3-1.7b",
        "provider": "lmstudio",
        "capability": "tools",
        "reason": "HTTP 400: this model does not support tools",
        "window_seconds": 1800,
        "lifts_in_seconds": 1800,
    }]
    assert after == []


def test_lifting_a_suppression_ends_it_and_returns_the_post_state() -> None:
    """A model id with a slash in it, because most local ids have one."""
    client = _client()
    with client:
        health = client.app.app.state.health  # type: ignore[attr-defined]
        health.suppress("qwen/qwen3-1.7b", "lmstudio", Capability.TOOLS,
                        "does not support tools")
        lifted = client.post("/api/v1/health/suppressions/qwen/qwen3-1.7b/lift")
        again = client.post("/api/v1/health/suppressions/qwen/qwen3-1.7b/lift")

    assert lifted.status_code == 200
    assert lifted.json() == {
        "model": "qwen/qwen3-1.7b",
        "capability": "tools",
        "was_suppressed": True,
        "capability_suppressions": [],
    }
    assert again.status_code == 200
    assert again.json()["was_suppressed"] is False


def test_lifting_a_suppression_needs_an_admin_credential() -> None:
    """It changes how every client's tool requests are routed, so it is guarded
    like the pool write: being able to call the gateway does not grant it."""
    client, _ = _app_with(RecordingUpstream())
    with client:
        health = client.app.app.state.health  # type: ignore[attr-defined]
        health.suppress("coder", "upstream", Capability.TOOLS, "does not support tools")
        refused = client.post("/api/v1/health/suppressions/coder/lift")
        still = [held["model"] for held in health.suppressions()]

    assert refused.status_code == 403
    assert still == ["coder"]
