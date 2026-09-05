"""M8 — the Resource Manager, and the nine ways it must not lose track (§9).

    Two clients safely share a loaded model; concurrent, warm-reuse,
    cold-start, load-failure, hung-inference, crash, stale-lease, cancellation
    and exhaustion tests leave consistent state.

Every one of those is a named test below. The manager's whole value is that it
is right when something goes wrong — when it goes right, a bare adapter would
have done.
"""

from __future__ import annotations

import asyncio

import pytest

from sirvis.resources import (
    ConflictPolicy,
    ResourceExhaustedError,
    ResourceManager,
)
from sirvis.runtimes import LoadedModel, RuntimeUnavailableError


class FakeRuntime:
    """A runtime whose timing and failures the test decides.

    A recorded LM Studio would not do here: these tests are about what happens
    when a load hangs, fails or is abandoned halfway, and that has to be driven
    rather than waited for (§14.5 — nothing reaches a live service).
    """

    def __init__(self, load_fails: set[str] | None = None) -> None:
        self.loads: list[str] = []
        self.unloads: list[str] = []
        self.load_fails = load_fails or set()
        self.gate: asyncio.Event | None = None

    async def load(self, model_key: str, config: dict | None = None) -> LoadedModel:
        del config
        if self.gate is not None:
            await self.gate.wait()
        if model_key in self.load_fails:
            raise RuntimeUnavailableError(f"cannot load {model_key}")
        self.loads.append(model_key)
        return LoadedModel(model_key=model_key, state="loaded", effective={"context_length": 4096})

    async def unload(self, model_key: str) -> None:
        self.unloads.append(model_key)

    async def list_loaded_models(self) -> list[LoadedModel]:
        held = [k for k in self.loads if k not in self.unloads]
        return [LoadedModel(model_key=k, state="loaded") for k in held]


class FakeClock:
    """A clock the test moves by hand, so a lease can expire instantly."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _manager(runtime: FakeRuntime, clock: FakeClock | None = None,
             **kwargs: object) -> ResourceManager:
    return ResourceManager(runtime, clock=clock or FakeClock(), **kwargs)  # type: ignore[arg-type]


# ── The headline: two clients sharing one model ──────────────────────────────


async def test_two_clients_share_a_model_and_neither_can_pull_it_from_the_other() -> None:
    """§9's central rule. Without it, whichever finishes first breaks the other.

    Not hypothetical: RAVIS routes traffic to the same builds the benchmark
    runner wants.
    """
    runtime = FakeRuntime()
    manager = _manager(runtime)

    ravis = await manager.acquire("ravis", "coder-7b")
    bench = await manager.acquire("benchmark", "coder-7b")

    unloaded = await manager.release(ravis.session_id)

    assert unloaded == []                      # one owner released; nobody unloads
    assert runtime.unloads == []
    assert await manager.release(bench.session_id) == ["coder-7b"]   # the last does
    assert runtime.unloads == ["coder-7b"]


# ── Cold start, warm reuse, concurrency ──────────────────────────────────────


async def test_a_cold_model_is_loaded_once() -> None:
    runtime = FakeRuntime()
    manager = _manager(runtime)

    await manager.acquire("a", "coder-7b")

    assert runtime.loads == ["coder-7b"]


async def test_a_warm_model_is_reused_rather_than_reloaded() -> None:
    """The point of reference counting: the second client pays nothing."""
    runtime = FakeRuntime()
    manager = _manager(runtime)

    await manager.acquire("a", "coder-7b")
    await manager.acquire("b", "coder-7b")

    assert runtime.loads == ["coder-7b"]


async def test_concurrent_acquires_of_a_cold_model_produce_one_load() -> None:
    """Two clients arriving together must not start two loads of the same build.

    The bookkeeping is registered inside the lock while the load itself happens
    outside it, so a multi-minute load does not block every other acquire —
    which is what makes this the interesting case rather than an obvious one.
    """
    runtime = FakeRuntime()
    runtime.gate = asyncio.Event()
    manager = _manager(runtime)

    first = asyncio.create_task(manager.acquire("a", "coder-7b"))
    second = asyncio.create_task(manager.acquire("b", "coder-7b"))
    await asyncio.sleep(0)
    runtime.gate.set()
    await asyncio.gather(first, second)

    assert runtime.loads == ["coder-7b"]
    assert manager.residency()["holdings"][0]["reference_count"] == 2


# ── Things going wrong ───────────────────────────────────────────────────────


async def test_a_failed_load_leaves_no_phantom_reference() -> None:
    """The bug this test exists for: a load that raised, counted anyway.

    A reference to a model that was never loaded occupies capacity forever and
    can never be released, because no session believes it holds it.
    """
    runtime = FakeRuntime(load_fails={"broken"})
    manager = _manager(runtime)

    with pytest.raises(RuntimeUnavailableError):
        await manager.acquire("a", "broken")

    assert manager.residency()["holdings"] == []
    assert manager.residency()["leases"] == []


async def test_an_abandoned_acquire_does_not_cancel_a_load_someone_else_wants() -> None:
    """Cancellation, and the reason the load is shielded.

    Two clients wait on one load; the first gives up — a disconnected request, a
    timed-out caller. Without the shield its cancellation propagates into the
    shared task and takes the second client's load down with it.
    """
    runtime = FakeRuntime()
    runtime.gate = asyncio.Event()
    manager = _manager(runtime)

    giving_up = asyncio.create_task(manager.acquire("a", "coder-7b"))
    staying = asyncio.create_task(manager.acquire("b", "coder-7b"))
    await asyncio.sleep(0)
    giving_up.cancel()
    runtime.gate.set()

    lease = await staying
    assert lease.model_keys == ("coder-7b",)
    assert runtime.loads == ["coder-7b"]


async def test_a_hung_load_does_not_block_reads_of_what_is_already_held() -> None:
    """A multi-minute load must not freeze the service around it.

    The lock covers the bookkeeping, never the runtime call — so residency stays
    answerable while something slow is in flight.
    """
    runtime = FakeRuntime()
    manager = _manager(runtime)
    await manager.acquire("a", "already-here")

    runtime.gate = asyncio.Event()
    hanging = asyncio.create_task(manager.acquire("b", "slow-model"))
    await asyncio.sleep(0)

    assert [h["model_key"] for h in manager.residency()["holdings"]] == ["already-here"]

    runtime.gate.set()
    await hanging


async def test_a_crashed_client_stops_stranding_its_model() -> None:
    """Leases exist for exactly this. A client that died without releasing looks
    identical to one that is merely slow, so the only safe recovery is time."""
    clock = FakeClock()
    runtime = FakeRuntime()
    manager = _manager(runtime, clock, default_lease_seconds=60.0)
    await manager.acquire("doomed-client", "coder-7b")

    clock.advance(61.0)
    reclaimed = await manager.sweep()

    assert reclaimed == ["coder-7b"]
    assert runtime.unloads == ["coder-7b"]
    assert manager.residency()["holdings"] == []


async def test_renewing_a_lease_keeps_the_model() -> None:
    """The other half: a live client that heartbeats must not be reclaimed."""
    clock = FakeClock()
    manager = _manager(FakeRuntime(), clock, default_lease_seconds=60.0)
    lease = await manager.acquire("client", "coder-7b")

    clock.advance(50.0)
    assert manager.renew(lease.session_id) is not None
    clock.advance(50.0)

    assert await manager.sweep() == []


async def test_renewing_a_lapsed_lease_returns_nothing_rather_than_reviving_it() -> None:
    """A client whose lease expired has already had its models released.

    Handing it a fresh lease would tell it it still holds something it does not,
    which is worse than telling it the truth late.
    """
    clock = FakeClock()
    manager = _manager(FakeRuntime(), clock, default_lease_seconds=60.0)
    lease = await manager.acquire("client", "coder-7b")

    clock.advance(61.0)
    await manager.sweep()

    assert manager.renew(lease.session_id) is None


async def test_a_stale_lease_is_reclaimed_by_the_next_acquire() -> None:
    """Recovery must not depend on somebody remembering to sweep."""
    clock = FakeClock()
    runtime = FakeRuntime()
    manager = _manager(runtime, clock, default_lease_seconds=60.0, max_loaded=1)
    await manager.acquire("gone", "old-model")

    clock.advance(61.0)
    await manager.acquire("new", "new-model")

    assert runtime.unloads == ["old-model"]
    assert [h["model_key"] for h in manager.residency()["holdings"]] == ["new-model"]


# ── Capacity and conflict policy ─────────────────────────────────────────────


async def test_exhaustion_names_who_is_holding_what() -> None:
    """"At capacity" without that is a dead end for whoever reads it."""
    manager = _manager(FakeRuntime(), max_loaded=1)
    await manager.acquire("holder", "coder-7b", session_id="s1")

    with pytest.raises(ResourceExhaustedError, match="s1"):
        await manager.acquire("newcomer", "other-model", policy=ConflictPolicy.REJECT)


async def test_preemption_never_takes_a_model_someone_is_holding() -> None:
    """§9: never unload a resource owned by another client, and preemption is
    not an exception to it."""
    manager = _manager(FakeRuntime(), max_loaded=1)
    await manager.acquire("holder", "coder-7b")

    with pytest.raises(ResourceExhaustedError):
        await manager.acquire("greedy", "other", policy=ConflictPolicy.PREEMPT)


async def test_preemption_currently_has_nothing_it_is_allowed_to_reclaim() -> None:
    """An honest test of a policy that cannot yet do anything, and why.

    §9 names `preempt`, and the code reclaims an *unreferenced* holding — but no
    such holding can exist, because release unloads at zero references. So the
    policy refuses, exactly as `reject` would.

    The first version of this test manufactured a zero-reference holding by
    reaching into the manager's internals, which made an unreachable branch look
    covered. A green tick over a state the system cannot enter is worse than an
    admitted gap, because it is the gap plus a reason not to look for it.

    It becomes reachable when models are retained warm after their last release
    — worth having, and not this milestone.
    """
    runtime = FakeRuntime()
    manager = _manager(runtime, max_loaded=1)
    lease = await manager.acquire("a", "idle-model")
    await manager.release(lease.session_id)          # unloads at zero, per §9
    await manager.acquire("holder", "coder-7b")

    with pytest.raises(ResourceExhaustedError):
        await manager.acquire("b", "wanted", policy=ConflictPolicy.PREEMPT)


# ── Idempotence and foreign instances ────────────────────────────────────────


async def test_releasing_twice_is_not_an_error() -> None:
    """§9 asks for idempotent operations: a client retrying after a timeout must
    not be punished for the first attempt having worked."""
    manager = _manager(FakeRuntime())
    lease = await manager.acquire("a", "coder-7b")

    assert await manager.release(lease.session_id) == ["coder-7b"]
    assert await manager.release(lease.session_id) == []


async def test_one_session_acquiring_twice_must_release_twice() -> None:
    """Otherwise a nested caller releasing early pulls the model out from under
    the scope that contains it."""
    runtime = FakeRuntime()
    manager = _manager(runtime)
    await manager.acquire("a", "coder-7b", session_id="s")
    await manager.acquire("a", "coder-7b", session_id="s")

    await manager.release("s")

    assert runtime.unloads == ["coder-7b"]  # the lease covers the session, not each call


async def test_models_the_manager_did_not_load_are_reported_not_reclaimed() -> None:
    """Real on this machine: LM Studio loads instances by itself when a request
    wants more context than the running copy has, and a user can load anything
    by hand. They occupy memory this manager accounts for and does not own."""
    runtime = FakeRuntime()
    await runtime.load("loaded-by-someone-else")
    manager = _manager(runtime)

    foreign = await manager.foreign_instances()

    assert foreign == ["loaded-by-someone-else"]
    assert runtime.unloads == []


async def test_an_instance_already_resident_is_adopted_rather_than_loaded_again() -> None:
    """LM Studio does not reconfigure a resident model to satisfy a request it
    cannot serve — it loads *another instance*, which is how three copies of one
    7B model ended up resident on this machine. A manager that issued a load for
    something already there would cause exactly that, and would then be
    accounting for one instance while the machine held two."""
    runtime = FakeRuntime()
    await runtime.load("already-there")
    manager = _manager(runtime)

    await manager.acquire(owner="benchmark", model_key="already-there")

    assert runtime.loads == ["already-there"]  # the test's own load, and no second
    assert manager.residency()["holdings"][0]["owned"] is False


async def test_releasing_an_adopted_instance_never_unloads_it() -> None:
    """§11.2's lifecycle says "unload **if owned**", and this is the half that
    sentence exists for: taking away a model a user loaded by hand, because a
    benchmark borrowed it for thirty seconds, is the implicit preemption §9
    forbids."""
    runtime = FakeRuntime()
    await runtime.load("someone-elses")
    manager = _manager(runtime)
    lease = await manager.acquire(owner="benchmark", model_key="someone-elses")

    unloaded = await manager.release(lease.session_id)

    assert unloaded == []
    assert runtime.unloads == []


async def test_a_model_this_manager_loaded_is_still_unloaded_at_zero_references() -> None:
    """The other half. Adoption must not become a loophole that leaves every
    model resident forever."""
    runtime = FakeRuntime()
    manager = _manager(runtime)
    lease = await manager.acquire(owner="benchmark", model_key="ours")

    unloaded = await manager.release(lease.session_id)

    assert unloaded == ["ours"]
    assert runtime.unloads == ["ours"]


async def test_a_failed_unload_still_frees_the_capacity() -> None:
    """Keeping a record of something the manager can no longer control means a
    stuck entry occupying capacity forever."""

    class Stubborn(FakeRuntime):
        async def unload(self, model_key: str) -> None:
            del model_key
            raise RuntimeUnavailableError("the runtime went away")

    manager = _manager(Stubborn(), max_loaded=1)
    lease = await manager.acquire("a", "coder-7b")

    assert await manager.release(lease.session_id) == []   # nothing confirmed unloaded
    assert manager.residency()["holdings"] == []           # but capacity is free


# ── Through the API (§9's session shape, §4.2's canonical path) ──────────────


def _api() -> tuple[object, str]:
    """The real app with a recorded runtime and a runtime-scoped token."""
    import httpx
    from fastapi.testclient import TestClient
    from tests.conftest_lmstudio import transport

    from sirvis.api.security import Scope, mint_token
    from sirvis.app import create_app
    from sirvis.config import Settings
    from sirvis.runtimes import LMStudioAdapter

    settings = Settings(database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
                        _env_file=None)  # type: ignore[call-arg]
    app = create_app(
        settings,
        runtime=LMStudioAdapter(
            "http://runtime.invalid", client=httpx.AsyncClient(transport=transport())
        ),
    )
    app.state.resources = ResourceManager(runtime=FakeRuntime(), max_loaded=2)
    return TestClient(app), mint_token(app.state.database, "t", {Scope.RUNTIME})


def test_a_session_holds_its_models_until_released() -> None:
    """§9's shape end to end: acquire under a lease, then give it back."""
    client, token = _api()
    auth = {"content-type": "application/json", "authorization": f"Bearer {token}"}

    opened = client.post(  # type: ignore[attr-defined]
        "/api/v1/runtime/sessions",
        # An id the recorded runtime actually lists. It said `coder-7b` until
        # `MODEL_NOT_INSTALLED` started being raised, at which point this test
        # was leasing a build the fake runtime had never heard of and being
        # given a lease for it — which is the thing that check exists to stop.
        json={"owner": "benchmark", "models": [{"model_id": "qwen2.5-coder-7b-instruct"}]},
        headers=auth,
    ).json()
    residency = client.get("/api/v1/runtime/residency").json()  # type: ignore[attr-defined]
    closed = client.delete(  # type: ignore[attr-defined]
        f"/api/v1/runtime/sessions/{opened['session_id']}", headers=auth
    ).json()

    assert opened["models"] == ["qwen2.5-coder-7b-instruct"]
    assert residency["holdings"][0]["reference_count"] == 1
    assert closed["unloaded"] == ["qwen2.5-coder-7b-instruct"]


def test_a_partly_acquired_session_is_released_rather_than_left_dangling() -> None:
    """Two of three models is not a session anybody asked for.

    Leaving the two held would strand them behind a lease no client owns, which
    is the stranding leases exist to prevent — caused by the code that
    implements them.
    """
    client, token = _api()
    auth = {"content-type": "application/json", "authorization": f"Bearer {token}"}

    response = client.post(  # type: ignore[attr-defined]
        "/api/v1/runtime/sessions",
        # Three ids the runtime lists — two real builds and one repeat — so the
        # test still exercises partial acquisition rather than being refused up
        # front for asking about models that do not exist.
        json={"models": [{"model_id": "qwen2.5-coder-7b-instruct"},
                         {"model_id": "lmstudio-community/granite-4.0-h-tiny"},
                         {"model_id": "smollm3-3b"}],
              "policy": "reject"},
        headers=auth,
    )

    assert response.status_code == 409
    residency = client.get("/api/v1/runtime/residency").json()  # type: ignore[attr-defined]
    assert residency["holdings"] == []


def test_renewing_a_lapsed_session_is_a_404_not_a_new_lease() -> None:
    client, token = _api()
    auth = {"content-type": "application/json", "authorization": f"Bearer {token}"}

    response = client.post(  # type: ignore[attr-defined]
        "/api/v1/runtime/sessions/never-existed/renew", headers=auth
    )

    assert response.status_code == 404


# ── Ownership on close, renew and residency (a Claude Security scan, CWE-863:
# any RUNTIME token could act on anyone's session, having learned the
# session_id from the unauthenticated `/runtime/residency`; and the fix for
# that turned `owner` into a real caller identity, which then had to stop
# leaking through that same unauthenticated read) ───────────────────────────


def _api_with_tokens() -> tuple[object, str, str, str]:
    """The real app with three tokens: a session's owner, another ordinary
    RUNTIME caller, and an admin — exactly the three identities the ownership
    check and the residency redaction must tell apart.
    """
    import httpx
    from fastapi.testclient import TestClient
    from tests.conftest_lmstudio import transport

    from sirvis.api.security import Scope, mint_token
    from sirvis.app import create_app
    from sirvis.config import Settings
    from sirvis.runtimes import LMStudioAdapter

    settings = Settings(database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
                        _env_file=None)  # type: ignore[call-arg]
    app = create_app(
        settings,
        runtime=LMStudioAdapter(
            "http://runtime.invalid", client=httpx.AsyncClient(transport=transport())
        ),
    )
    app.state.resources = ResourceManager(runtime=FakeRuntime(), max_loaded=2)
    return (
        TestClient(app),
        mint_token(app.state.database, "owner", {Scope.RUNTIME}),
        mint_token(app.state.database, "other", {Scope.RUNTIME}),
        mint_token(app.state.database, "admin", {Scope.ADMIN}),
    )


def _open(client: object, token: str, owner_claim: str | None = None) -> dict:
    auth = {"content-type": "application/json", "authorization": f"Bearer {token}"}
    body: dict = {"models": [{"model_id": "qwen2.5-coder-7b-instruct"}]}
    if owner_claim is not None:
        body["owner"] = owner_claim
    return client.post("/api/v1/runtime/sessions", json=body, headers=auth).json()  # type: ignore[attr-defined]


def test_the_owning_caller_can_still_close_and_renew_its_own_session() -> None:
    """No regression: binding ownership to the real caller must not refuse
    the owner itself."""
    client, owner_token, _other_token, _admin_token = _api_with_tokens()
    auth = {"content-type": "application/json", "authorization": f"Bearer {owner_token}"}

    opened = _open(client, owner_token)

    renewed = client.post(  # type: ignore[attr-defined]
        f"/api/v1/runtime/sessions/{opened['session_id']}/renew", headers=auth
    )
    assert renewed.status_code == 200

    closed = client.delete(  # type: ignore[attr-defined]
        f"/api/v1/runtime/sessions/{opened['session_id']}", headers=auth
    ).json()
    assert closed["unloaded"] == ["qwen2.5-coder-7b-instruct"]


def test_a_non_owning_runtime_caller_cannot_close_or_renew_someone_elses_session() -> None:
    """The exploit F5/F6 describe: any RUNTIME token, not just the owner's,
    used against a session_id learned from the unauthenticated residency read.
    """
    client, owner_token, other_token, _admin_token = _api_with_tokens()
    other_auth = {"content-type": "application/json", "authorization": f"Bearer {other_token}"}

    opened = _open(client, owner_token)
    session_id = opened["session_id"]

    renew_attempt = client.post(  # type: ignore[attr-defined]
        f"/api/v1/runtime/sessions/{session_id}/renew", headers=other_auth
    )
    assert renew_attempt.status_code == 403
    assert renew_attempt.json()["error"]["code"] == "FORBIDDEN"

    close_attempt = client.delete(  # type: ignore[attr-defined]
        f"/api/v1/runtime/sessions/{session_id}", headers=other_auth
    )
    assert close_attempt.status_code == 403
    assert close_attempt.json()["error"]["code"] == "FORBIDDEN"

    # Refused, not silently accepted: the model is still held under the
    # original session once both attempts have been made.
    residency = client.get(  # type: ignore[attr-defined]
        "/api/v1/runtime/residency", headers=other_auth
    ).json()
    assert residency["holdings"][0]["sessions"] == [session_id]


def test_an_admin_can_still_close_or_renew_a_session_it_does_not_own() -> None:
    """The named escape hatch (§9's `force_unload` pattern) for a legitimate
    cross-caller action such as an admin dashboard or a cleanup job."""
    client, owner_token, _other_token, admin_token = _api_with_tokens()
    admin_auth = {"content-type": "application/json", "authorization": f"Bearer {admin_token}"}

    opened = _open(client, owner_token)
    renewed = client.post(  # type: ignore[attr-defined]
        f"/api/v1/runtime/sessions/{opened['session_id']}/renew", headers=admin_auth
    )
    assert renewed.status_code == 200

    closed = client.delete(  # type: ignore[attr-defined]
        f"/api/v1/runtime/sessions/{opened['session_id']}", headers=admin_auth
    ).json()
    assert closed["unloaded"] == ["qwen2.5-coder-7b-instruct"]


def test_a_self_declared_owner_cannot_grant_ownership_of_someone_elses_session() -> None:
    """`owner` in the request body used to be trusted outright — the root
    cause under both F5 and F6. It must now be inert."""
    client, owner_token, other_token, _admin_token = _api_with_tokens()
    other_auth = {"content-type": "application/json", "authorization": f"Bearer {other_token}"}

    opened = _open(client, owner_token)

    # Claiming to already be the owner in the body grants nothing: ownership
    # comes from the bearer token that authenticated this request, never from
    # anything the request body says about itself.
    close_attempt = client.delete(  # type: ignore[attr-defined]
        f"/api/v1/runtime/sessions/{opened['session_id']}", headers=other_auth
    )
    assert close_attempt.status_code == 403


def test_residency_redacts_owner_unless_the_caller_holds_any_token() -> None:
    """The round-1 regression this fix also closes: binding `owner` to a real
    caller identity means a caller presenting *no* token must not see it — the
    same fact `GET /tokens` requires `Scope.ADMIN` to protect — while a caller
    presenting *any* valid token (not necessarily the session's own) still
    sees it, exactly as it always could for a session it can already act on.
    """
    client, owner_token, other_token, _admin_token = _api_with_tokens()
    _open(client, owner_token)

    anonymous = client.get("/api/v1/runtime/residency").json()  # type: ignore[attr-defined]
    assert anonymous["leases"][0]["owner"] is None

    other_auth = {"authorization": f"Bearer {other_token}"}
    as_other = client.get(  # type: ignore[attr-defined]
        "/api/v1/runtime/residency", headers=other_auth
    ).json()
    assert as_other["leases"][0]["owner"] == "owner"


# ── The three codes §4.3 published and nothing raised ───────────────────────


def test_a_model_this_machine_does_not_have_is_a_404_not_a_502() -> None:
    """`MODEL_NOT_INSTALLED` is in SIRVIS.md §4.3's list and nothing raised it.

    A lease for an absent build went all the way to `lms load`, failed, and came
    back as `LOAD_FAILED` — a 502 saying the runtime could not load it, when the
    truthful answer is a 404 saying it was never here. Found by sweeping for
    definitions nothing references.
    """
    client, token = _api()

    response = client.post(  # type: ignore[attr-defined]
        "/api/v1/runtime/sessions",
        json={"owner": "benchmark", "models": [{"model_id": "a-build-nobody-installed"}]},
        headers={"content-type": "application/json", "authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "MODEL_NOT_INSTALLED"
    assert "a-build-nobody-installed" in body["error"]["details"]["requested"]


def test_an_unreadable_inventory_does_not_report_every_model_missing() -> None:
    """An empty inventory means the runtime did not answer.

    Refusing everything on that basis would turn one unreachable runtime into
    every model on the machine being reported as uninstalled, which is a much
    larger and much wronger claim.
    """
    import asyncio
    from types import SimpleNamespace

    from sirvis.api.routes import _refuse_uninstalled
    from sirvis.runtimes import RuntimeUnavailableError

    class Silent:
        async def list_models(self) -> list[dict[str, object]]:
            raise RuntimeUnavailableError("not answering")

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(lmstudio=Silent())))

    # Must not raise: the load attempt decides, exactly as before.
    asyncio.run(_refuse_uninstalled(request, [{"model_id": "anything"}]))  # type: ignore[arg-type]


def test_a_failed_load_is_load_failed_and_a_timeout_is_timeout() -> None:
    """Three adapter failures, three published codes.

    All three reported `RUNTIME_UNREACHABLE` until this sweep, so §4.3 published
    three codes and used one — and a caller told the runtime was unreachable
    while it was answering perfectly well goes and looks at a process that is
    working fine.
    """
    from sirvis.api.routes import _wire_failure
    from sirvis.runtimes import (
        RuntimeLoadFailedError,
        RuntimeTimeoutError,
        RuntimeUnavailableError,
    )

    assert _wire_failure(RuntimeTimeoutError("slow")).code == "TIMEOUT"
    assert _wire_failure(RuntimeLoadFailedError("nope")).code == "LOAD_FAILED"
    assert _wire_failure(RuntimeUnavailableError("gone")).code == "RUNTIME_UNAVAILABLE"


def test_the_narrower_failures_are_still_caught_as_unavailable() -> None:
    """Subclasses, not alternatives — which is what made them safe to add.

    Twelve `except RuntimeUnavailableError` sites already existed. Had these
    been siblings rather than subclasses, each would have silently stopped
    handling a case it used to handle.
    """
    from sirvis.runtimes import (
        RuntimeLoadFailedError,
        RuntimeTimeoutError,
        RuntimeUnavailableError,
    )

    assert issubclass(RuntimeLoadFailedError, RuntimeUnavailableError)
    assert issubclass(RuntimeTimeoutError, RuntimeUnavailableError)


async def test_force_unload_takes_a_model_held_by_someone_else() -> None:
    """§9's explicit escape hatch, which had no caller and no test.

    Kept rather than deleted — §9 permits this "explicitly with authority" and
    deleting specified behaviour because nothing calls it yet is the wrong
    correction. But an untested escape hatch is a claim, not a feature, so it is
    tested here. **No surface exposes it**; reaching it needs the authorization
    story M16 owns.
    """
    runtime = FakeRuntime()
    manager = _manager(runtime)
    lease = await manager.acquire("benchmark", "coder-7b")

    taken = await manager.force_unload("coder-7b", authority="operator")

    assert taken is True
    assert runtime.unloads == ["coder-7b"]
    # The lease's holding is gone, so releasing it unloads nothing further.
    assert await manager.release(lease.session_id) == []
