"""M9 — Runtime Sets (§10), and the three things its gate actually asks.

    Two models stay loaded and independently addressable; two revisions
    distinguishable; old results retain their revision.

Each is a section below. The third is the one that is easy to believe and hard
to keep: it is only true if nothing ever updates a revision row, and a test that
merely reads a revision back would pass on a schema that rewrote it.

The fit estimate gets its own section because §10.1 is a warning rather than a
feature — *two models fitting separately does not prove they work well
together* — and the estimate must be unable to claim otherwise.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.conftest_lmstudio import transport

from sirvis.api.security import Scope, mint_token
from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.core.runtime_sets import (
    FitEstimate,
    RuntimeSet,
    RuntimeSetError,
    RuntimeSetMember,
    estimate_fit,
)
from sirvis.resources import ResourceManager
from sirvis.runtimes import LMStudioAdapter, LoadedModel
from sirvis.storage import prepare_database
from sirvis.storage.runtime_sets import (
    find_by_name,
    list_revisions,
    list_runtime_sets,
    read_runtime_set,
    save_runtime_set,
)

SETS = "/api/v1/runtime-sets"
SESSIONS = "/api/v1/runtime/sessions"
JSON = {"content-type": "application/json"}

CHAT = "qwen2.5-coder-7b-instruct"
AGENT = "lmstudio-community/granite-4.0-h-tiny"


def a_set(name: str = "clarvis-balanced", **overrides: Any) -> RuntimeSet:
    """§10's own YAML example, as a definition."""
    fields: dict[str, Any] = {
        "members": [
            RuntimeSetMember(role="chat", model_id=CHAT, context_length=16384),
            RuntimeSetMember(role="agent", model_id=AGENT, context_length=32768),
        ],
        "purpose": "a chat model and an agent model, resident together",
    }
    fields.update(overrides)
    return RuntimeSet.define(name=name, **fields)


class FakeRuntime:
    """A runtime that loads instantly and remembers what it was asked to hold.

    Not a recorded LM Studio: §14.5 forbids a test reaching a real runtime, and
    what M9 needs to observe is which models ended up resident under one
    session — which is a fact about the manager, not about LM Studio.
    """

    def __init__(self) -> None:
        self.loaded: list[str] = []

    async def load(self, model_key: str, config: dict[str, Any] | None = None) -> LoadedModel:
        self.loaded.append(model_key)
        return LoadedModel(model_key=model_key, state="loaded", requested=config or {})

    async def unload(self, model_key: str) -> None:
        if model_key in self.loaded:
            self.loaded.remove(model_key)

    async def list_loaded_models(self) -> list[LoadedModel]:
        return [LoadedModel(model_key=key, state="loaded") for key in self.loaded]


def _app(runtime: FakeRuntime | None = None) -> tuple[TestClient, str]:
    """The real app, with a recorded catalogue and a runtime that cannot bite.

    The Resource Manager is replaced wholesale rather than having its adapter
    swapped, because it captures the adapter it was built with — the mistake
    M4's tests recorded, where a swapped `app.state.lmstudio` left a live one
    inside the manager and a session test loaded a real model.
    """
    settings = Settings(
        database_path=":memory:",
        lmstudio_base_url="http://127.0.0.1:9",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(
        settings,
        runtime=LMStudioAdapter(
            "http://runtime.invalid", client=httpx.AsyncClient(transport=transport())
        ),
    )
    if runtime is not None:
        app.state.resources = ResourceManager(runtime)
    token = mint_token(app.state.database, "test", {Scope.ADMIN})
    return TestClient(app), token


def _post(client: TestClient, token: str, path: str, body: dict[str, Any]) -> httpx.Response:
    return client.post(path, json=body, headers={**JSON, "authorization": f"Bearer {token}"})


def _get(client: TestClient, token: str, path: str) -> httpx.Response:
    return client.get(path, headers={"authorization": f"Bearer {token}"})


# ── The definition ───────────────────────────────────────────────────────────


def test_a_set_keeps_one_identity_across_revisions() -> None:
    """A set is its name; the revision is which definition it currently means.

    This is what makes "how has clarvis-balanced performed over time" a question
    with an answer — an ID that changed with the membership would make every
    revision a different set.
    """
    first = a_set()
    second = a_set(members=[RuntimeSetMember(role="chat", model_id=CHAT)])

    assert first.runtime_set_id == second.runtime_set_id
    assert first.definition_hash != second.definition_hash


def test_two_members_cannot_claim_one_role() -> None:
    """"Independently addressable" is what breaks: which one is *the* chat model?"""
    with pytest.raises(RuntimeSetError, match="appears twice"):
        a_set(
            members=[
                RuntimeSetMember(role="chat", model_id=CHAT),
                RuntimeSetMember(role="chat", model_id=AGENT),
            ]
        )


def test_load_order_defaults_to_declaration_order() -> None:
    """Order changes what a multi-model run measures, so it is not reordered."""
    assert a_set().load_order == ("chat", "agent")


def test_a_declared_load_order_must_name_every_member() -> None:
    """A partial order would load some members and silently skip the rest."""
    with pytest.raises(RuntimeSetError, match="load_order"):
        a_set(load_order=["chat"])


def test_load_order_is_part_of_the_definition() -> None:
    """Loading chat-then-agent is a different experiment from agent-then-chat."""
    assert a_set().definition_hash != a_set(load_order=["agent", "chat"]).definition_hash


def test_a_set_needs_a_name_and_a_member() -> None:
    with pytest.raises(RuntimeSetError, match="name"):
        a_set(name="  ")
    with pytest.raises(RuntimeSetError, match="at least one member"):
        a_set(members=[])


# ── Revisions, and the promise old results depend on ─────────────────────────


def test_saving_an_unchanged_definition_does_not_move_the_revision() -> None:
    """A version that increments for no reason is a version nobody reads.

    A UI saving on every edit, or a script re-applying the same YAML, must find
    the revision it already has.
    """
    database = prepare_database(":memory:")

    first = save_runtime_set(database, a_set())
    again = save_runtime_set(database, a_set())

    assert first.revision == 1
    assert again.revision == 1
    assert len(list_revisions(database, first.runtime_set_id)) == 1


def test_a_changed_definition_becomes_the_next_revision() -> None:
    database = prepare_database(":memory:")

    first = save_runtime_set(database, a_set())
    second = save_runtime_set(
        database,
        a_set(members=[
            RuntimeSetMember(role="chat", model_id=CHAT, context_length=8192),
            RuntimeSetMember(role="agent", model_id=AGENT, context_length=32768),
        ]),
    )

    assert (first.revision, second.revision) == (1, 2)
    assert first.runtime_set_id == second.runtime_set_id


def test_an_old_revision_still_describes_what_it_described(  # §10's gate, verbatim
) -> None:
    """The promise a result three weeks old depends on.

    Revision 1 said the chat model had a 16384 context. After revision 2 changes
    it, revision 1 must still say 16384 — otherwise a result citing revision 1 is
    citing a definition that has been rewritten underneath it.
    """
    database = prepare_database(":memory:")
    save_runtime_set(database, a_set())
    save_runtime_set(
        database,
        a_set(members=[
            RuntimeSetMember(role="chat", model_id=CHAT, context_length=131072),
            RuntimeSetMember(role="agent", model_id=AGENT, context_length=32768),
        ]),
    )

    identity = a_set().runtime_set_id
    original = read_runtime_set(database, identity, revision=1)
    latest = read_runtime_set(database, identity)

    assert original is not None and latest is not None
    assert original.member_for("chat").context_length == 16384  # type: ignore[union-attr]
    assert latest.member_for("chat").context_length == 131072  # type: ignore[union-attr]
    assert latest.revision == 2


def test_reading_without_a_revision_gives_the_latest() -> None:
    database = prepare_database(":memory:")
    save_runtime_set(database, a_set())
    save_runtime_set(database, a_set(purpose="changed"))

    assert read_runtime_set(database, a_set().runtime_set_id).revision == 2  # type: ignore[union-attr]


def test_sets_are_findable_by_the_name_a_person_types() -> None:
    database = prepare_database(":memory:")
    save_runtime_set(database, a_set())

    found = find_by_name(database, "clarvis-balanced")

    assert found is not None and found.name == "clarvis-balanced"
    assert find_by_name(database, "nothing-by-that-name") is None


def test_listing_shows_each_set_once_at_its_latest_revision() -> None:
    """Two sets and four revisions is still two rows."""
    database = prepare_database(":memory:")
    save_runtime_set(database, a_set())
    save_runtime_set(database, a_set(purpose="changed"))
    save_runtime_set(database, a_set(name="coder-only"))
    save_runtime_set(database, a_set(name="coder-only", purpose="changed"))

    listed = list_runtime_sets(database)

    assert len(listed) == 2
    assert {item.revision for item in listed} == {2}


# ── Fit: what arithmetic may and may not claim (§10.1) ───────────────────────


def test_weights_that_already_exceed_the_machine_are_refused() -> None:
    """§10.1's example: 24 GB and 30 GB on a 64 GB machine, plus everything else.

    Refusing here is a claim about numbers, not a prediction — the weights alone
    do not fit, before a byte of KV cache exists.
    """
    estimate = estimate_fit({"a": 40_000_000_000, "b": 30_000_000_000}, 64_000_000_000)

    assert estimate.verdict == FitEstimate.REFUSED
    assert "before" in estimate.detail


def test_a_combination_that_clears_the_bar_is_only_plausible() -> None:
    """Never "fits". §10.1 exists because that inference is the mistake."""
    estimate = estimate_fit({"a": 8_000_000_000, "b": 8_000_000_000}, 64_000_000_000)

    assert estimate.verdict == FitEstimate.PLAUSIBLE
    assert "not established until it is measured" in estimate.detail


def test_one_unknown_size_makes_the_total_unknown_not_smaller() -> None:
    """A sum missing a term is not a sum.

    Treating an unrecorded size as zero would approve a set that cannot load —
    the exact direction §12.1 forbids guessing in.
    """
    estimate = estimate_fit({"a": 8_000_000_000, "b": None}, 16_000_000_000)

    assert estimate.verdict == FitEstimate.UNKNOWN
    assert estimate.weights_bytes.value is None


def test_no_machine_snapshot_means_unknown_rather_than_approval() -> None:
    estimate = estimate_fit({"a": 8_000_000_000}, None)

    assert estimate.verdict == FitEstimate.UNKNOWN


def test_an_estimate_says_it_is_an_estimate_in_its_own_payload() -> None:
    """§10's gate: resource totals must identify measurement versus estimate.

    A consumer reading only the JSON has to be able to tell, so the label is a
    field rather than a sentence in the docs.
    """
    payload = estimate_fit({"a": 1}, 2).as_dict()

    assert payload["basis"] == "estimate"
    assert "KV cache" in payload["excludes"]


# ── The API ──────────────────────────────────────────────────────────────────


def test_defining_a_set_over_http_returns_its_revision_and_fit() -> None:
    client, token = _app()

    response = _post(client, token, SETS, {
        "name": "clarvis-balanced",
        "models": [
            {"role": "chat", "model": CHAT, "context_length": 16384},
            {"role": "agent", "model": AGENT, "context_length": 32768},
        ],
    })

    body = response.json()
    assert response.status_code == 200
    assert body["revision"] == 1
    assert [member["role"] for member in body["members"]] == ["chat", "agent"]
    assert body["fit"]["basis"] == "estimate"


def test_re_posting_the_same_definition_is_idempotent_over_http() -> None:
    client, token = _app()
    definition = {
        "name": "clarvis-balanced",
        "models": [{"role": "chat", "model": CHAT}],
    }

    first = _post(client, token, SETS, definition).json()
    second = _post(client, token, SETS, definition).json()

    assert first["revision"] == second["revision"] == 1


def test_a_duplicate_role_is_a_422_that_says_which_role() -> None:
    client, token = _app()

    response = _post(client, token, SETS, {
        "name": "broken",
        "models": [{"role": "chat", "model": CHAT}, {"role": "chat", "model": AGENT}],
    })

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CONFIGURATION"


def test_every_revision_is_readable_so_the_gate_can_be_checked() -> None:
    client, token = _app()
    _post(client, token, SETS, {"name": "s", "models": [{"role": "chat", "model": CHAT}]})
    _post(client, token, SETS, {"name": "s", "models": [{"role": "chat", "model": AGENT}]})
    identity = _get(client, token, SETS).json()["items"][0]["runtime_set_id"]

    revisions = _get(client, token, f"{SETS}/{identity}/revisions").json()["items"]

    assert [item["revision"] for item in revisions] == [1, 2]
    assert revisions[0]["members"][0]["model_id"] == CHAT
    assert revisions[1]["members"][0]["model_id"] == AGENT


def test_asking_for_a_revision_that_does_not_exist_is_a_404() -> None:
    """Never a fall back to the latest.

    A caller naming revision 2 is asking about the definition a result cited;
    answering with a different one substitutes a different combination for the
    one under investigation.
    """
    client, token = _app()
    _post(client, token, SETS, {"name": "s", "models": [{"role": "chat", "model": CHAT}]})
    identity = _get(client, token, SETS).json()["items"][0]["runtime_set_id"]

    assert _get(client, token, f"{SETS}/{identity}?revision=7").status_code == 404
    assert _get(client, token, f"{SETS}/rset_missing").status_code == 404


# ── Sessions: two models, resident, independently addressable ────────────────


def test_a_session_opened_against_a_set_holds_every_member() -> None:
    """M9's first acceptance clause, over the endpoint §9 specifies."""
    runtime = FakeRuntime()
    client, token = _app(runtime)
    _post(client, token, SETS, {
        "name": "clarvis-balanced",
        "models": [
            {"role": "chat", "model": CHAT, "context_length": 16384},
            {"role": "agent", "model": AGENT, "context_length": 32768},
        ],
    })

    session = _post(client, token, SESSIONS, {"runtime_set": "clarvis-balanced"}).json()

    assert sorted(session["models"]) == sorted([CHAT, AGENT])
    assert runtime.loaded == [CHAT, AGENT], "members load in the set's declared order"
    # Independently addressable: a caller names a role and gets one model.
    assert session["roles"] == {"chat": CHAT, "agent": AGENT}


def test_a_session_records_the_revision_it_resolved() -> None:
    """§10 calls a set immutable *at use*.

    Editing the set while a session is open must not change what that session is
    a session of — so the revision is resolved once and reported.
    """
    runtime = FakeRuntime()
    client, token = _app(runtime)
    _post(client, token, SETS, {"name": "s", "models": [{"role": "chat", "model": CHAT}]})

    session = _post(client, token, SESSIONS, {"runtime_set": "s"}).json()
    _post(client, token, SETS, {"name": "s", "models": [{"role": "chat", "model": AGENT}]})

    assert session["revision"] == 1


def test_a_context_length_reaches_the_runtime_rather_than_staying_a_label() -> None:
    """It is part of what makes two co-resident models fit, so it must be sent."""
    loads: list[dict[str, Any]] = []

    class Recording(FakeRuntime):
        async def load(
            self, model_key: str, config: dict[str, Any] | None = None
        ) -> LoadedModel:
            loads.append(config or {})
            return await super().load(model_key, config)

    client, token = _app(Recording())
    _post(client, token, SETS, {
        "name": "s", "models": [{"role": "chat", "model": CHAT, "context_length": 16384}],
    })

    _post(client, token, SESSIONS, {"runtime_set": "s"})

    assert loads[0]["context_length"] == 16384


def test_opening_a_session_against_an_unknown_set_is_a_404() -> None:
    client, token = _app(FakeRuntime())

    response = _post(client, token, SESSIONS, {"runtime_set": "never-defined"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"


def test_an_explicit_model_list_still_works() -> None:
    """§9's original shape is unchanged; the set is an addition, not a migration."""
    runtime = FakeRuntime()
    client, token = _app(runtime)

    session = _post(client, token, SESSIONS, {"models": [{"model_id": CHAT}]}).json()

    assert session["models"] == [CHAT]
    assert "runtime_set_id" not in session


def test_both_members_share_one_session_and_one_release() -> None:
    """Two models under one lease, released together — §9's reference counting.

    Two sessions would mean a client that finished with the set could release
    half of it, which is the state the Resource Manager exists to prevent.
    """
    runtime = FakeRuntime()
    client, token = _app(runtime)
    _post(client, token, SETS, {
        "name": "s",
        "models": [{"role": "chat", "model": CHAT}, {"role": "agent", "model": AGENT}],
    })
    session = _post(client, token, SESSIONS, {"runtime_set": "s"}).json()

    released = client.delete(
        f"{SESSIONS}/{session['session_id']}",
        headers={**JSON, "authorization": f"Bearer {token}"},
    ).json()

    assert sorted(released["unloaded"]) == sorted([CHAT, AGENT])
    assert runtime.loaded == []


def test_a_session_is_not_blocked_by_an_estimate_that_cannot_be_made() -> None:
    """Ignorance is not a refusal, and the asymmetry is the whole design.

    No installed size is recorded for any build on this machine yet — nothing
    captures one — so the estimate is UNKNOWN in practice. It must therefore let
    the session through: refusing on an absent number would make every set
    unloadable on the strength of arithmetic nobody could do. The refusal path
    is exercised where it can be, against known weights, above.
    """
    runtime = FakeRuntime()
    client, token = _app(runtime)
    _post(client, token, SETS, {
        "name": "s",
        "models": [{"role": "chat", "model": CHAT}, {"role": "agent", "model": AGENT}],
    })

    session = _post(client, token, SESSIONS, {"runtime_set": "s"}).json()

    assert session["fit"]["verdict"] == FitEstimate.UNKNOWN
    assert session["fit"]["basis"] == "estimate"
    assert sorted(runtime.loaded) == sorted([CHAT, AGENT])


def test_the_manager_holds_both_members_under_one_lease() -> None:
    """The same fact as the HTTP test, asserted where the state actually lives."""
    runtime = FakeRuntime()
    manager = ResourceManager(runtime)

    async def acquire_both() -> Any:
        lease = await manager.acquire(owner="test", model_key=CHAT)
        return await manager.acquire(
            owner="test", model_key=AGENT, session_id=lease.session_id
        )

    lease = asyncio.run(acquire_both())

    assert sorted(lease.model_keys) == sorted([CHAT, AGENT])
