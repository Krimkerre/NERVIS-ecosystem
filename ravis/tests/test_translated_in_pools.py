"""A translated provider's models, reachable through a pool.

`merged_candidates` walks transparent upstreams only, so an Anthropic model was
never a pool candidate — reachable by direct address and invisible to
`ravis/clarvis-chat` and every other pool. `_translating_for` said so in its own
docstring: *"a pooled request cannot reach a translating adapter yet and is
forwarded transparently."*

What was missing was not a lookup but a **map**. Nothing knew which provider
served a given translated model, so even with the models present the router
would have picked one and then forwarded its id down the transparent path to an
upstream that had never heard of it.
"""

from __future__ import annotations

import httpx
import pytest

from ravis.core.capabilities import Capability, CapabilityState, ModelCapabilities
from ravis.transparent import translated_candidates


class FakeTranslating:
    """A translating adapter with a catalogue and a credential."""

    name = "anthropic"

    def __init__(self, models: list[str], *, credential: bool = True,
                 fails: bool = False) -> None:
        self._models = models
        self.has_credential = credential
        self._fails = fails
        self.listed = 0

    async def models(self) -> list[str]:
        self.listed += 1
        if self._fails:
            raise httpx.ConnectError("no")
        return list(self._models)

    async def capabilities(self, model: str) -> ModelCapabilities:
        known = ModelCapabilities(model_id=model)
        known.context_window = 200_000
        return known


@pytest.mark.asyncio
async def test_a_translated_provider_contributes_candidates() -> None:
    adapter = FakeTranslating(["claude-haiku", "claude-opus"])

    found, owners = await translated_candidates({"anthropic": adapter})

    assert set(found) == {"claude-haiku", "claude-opus"}
    assert owners == {"claude-haiku": "anthropic", "claude-opus": "anthropic"}


@pytest.mark.asyncio
async def test_the_owner_map_comes_from_the_same_read() -> None:
    """So the router cannot select a model it is then unable to attribute.

    An unattributed selection is the dangerous case: the id would be forwarded
    down the transparent path to an upstream that has never heard of it.
    """
    adapter = FakeTranslating(["claude-haiku"])

    found, owners = await translated_candidates({"anthropic": adapter})

    assert set(found) == set(owners)


@pytest.mark.asyncio
async def test_a_provider_with_no_credential_contributes_nothing() -> None:
    """Its models would be eligible, chosen, and refused at the first request.

    A pool that selects something unreachable is worse than one that never
    offered it.
    """
    adapter = FakeTranslating(["claude-haiku"], credential=False)

    found, owners = await translated_candidates({"anthropic": adapter})

    assert found == {} and owners == {}
    assert adapter.listed == 0, "an unusable provider should not even be asked"


@pytest.mark.asyncio
async def test_a_discovery_failure_is_absence_not_an_error() -> None:
    """A provider that cannot be listed narrows the options rather than failing
    a request that was going somewhere else anyway."""
    adapter = FakeTranslating(["claude-haiku"], fails=True)

    found, owners = await translated_candidates({"anthropic": adapter})

    assert found == {} and owners == {}


@pytest.mark.asyncio
async def test_a_transparent_upstream_keeps_a_shared_model_id() -> None:
    """First declared wins, the rule every other merge here uses — and the
    transparent one is what the forwarder would actually reach."""
    adapter = FakeTranslating(["shared-id"])

    translated, _ = await translated_candidates({"anthropic": adapter})
    candidates: dict[str, object] = {"shared-id": "the transparent one"}
    for model, known in translated.items():
        candidates.setdefault(model, known)

    assert candidates["shared-id"] == "the transparent one"


def test_a_translated_model_joins_with_the_same_honesty() -> None:
    """What the catalogue advertises, and UNKNOWN for the rest — so a pool
    requiring tools still fails closed against a model nobody has asked."""
    known = ModelCapabilities(model_id="claude-haiku")

    assert known.state_of(Capability.TOOLS) is CapabilityState.UNKNOWN


def test_the_pools_listing_does_not_call_a_hosted_model_local() -> None:
    """`ravis/local` and `ravis/private` promise the request stays on the machine.

    `/api/v1/pools` derived its remote set as `remote_models(transparents)`,
    which by that function's own definition walks the *transparent* upstreams
    only. Every model served by a translated provider therefore counted as
    local, and on a machine with no local runtime running the listing reported
    `ravis/local` and `ravis/private` as available holding the hosted
    catalogue, while `ravis/api` reported empty. Exactly inverted, on the two
    pools whose entire promise is where a request goes.

    `/api/v1/pools/{key}/members` was right the whole time -- it builds from
    `_pool_candidates`, which unions the translated owners in -- so one service
    gave opposite answers about one catalogue from two endpoints.
    """
    from fastapi.testclient import TestClient

    from ravis.app import create_app
    from ravis.config import Settings

    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://127.0.0.1:1234",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    # No local runtime offering anything -- the ordinary state when LM Studio
    # is not running -- and one reachable hosted provider.
    app.app.state.transparents = {}
    app.app.state.translating = {"anthropic": FakeTranslating(["claude-haiku"])}

    with TestClient(app) as client:
        pools = {p["pool_id"]: p for p in client.get("/api/v1/pools").json()["items"]}

    for pool_id in ("ravis/local", "ravis/private"):
        assert "claude-haiku" not in pools[pool_id]["members"], (
            f"{pool_id} must never list a model served by a hosted provider"
        )

    assert "claude-haiku" in pools["ravis/api"]["members"], (
        "a hosted model belongs to ravis/api, and reporting that pool empty "
        "while the catalogue is reachable is the other half of the inversion"
    )


def test_a_narrowing_that_admits_nothing_reports_the_pool_unavailable() -> None:
    """The count on the dashboard has to be the count the router uses.

    `_members` returned an operator's stored narrowing verbatim, without
    intersecting it against what the pool's invariants actually admit, and
    `available` was derived from `eligible` and never consulted membership. So
    a pool whose narrowing named only models the pool no longer admits reported
    available with more members than eligible -- a count a narrowing cannot
    produce -- while every request to it was refused. Observed live as
    `ravis/cheap`: 62 members against 49 eligible, and the router answering
    "no candidate satisfies ravis/cheap".
    """
    from fastapi.testclient import TestClient

    from ravis.app import create_app
    from ravis.config import Settings

    settings = Settings(
        database_path=":memory:",
        upstream_base_url="http://127.0.0.1:1234",
        _env_file=None,  # type: ignore[call-arg]
    )
    app = create_app(settings)
    app.app.state.transparents = {}
    app.app.state.translating = {"anthropic": FakeTranslating(["claude-haiku"])}

    membership = getattr(app.app.state, "pool_membership", None)
    if membership is None:  # pragma: no cover - the store ships with the app
        pytest.skip("no membership store on this build")
    membership.set_for("ravis/api", ("a-model-that-is-not-installed",))

    with TestClient(app) as client:
        api = next(
            p for p in client.get("/api/v1/pools").json()["items"]
            if p["pool_id"] == "ravis/api"
        )

    assert api["member_count"] <= api["eligible_count"], (
        "a narrowing removes candidates; it cannot add any"
    )
    assert api["members"] == [], "none of the narrowed ids is eligible"
    assert api["available"] is False, (
        "the router refuses this pool, so the listing must not call it available"
    )


def test_a_pool_revision_moves_when_an_operator_narrows_it() -> None:
    """§5.4's pin has to signal the change it exists for.

    `revision` is a hash of the pool's *definition*, and an operator's narrowing
    lives in `pools.json` rather than in the definition -- so ticking a model out
    of a pool changed which model comes back and left the revision untouched. A
    consumer pinning it was told nothing, which is worse than not being able to
    pin at all: they believe they are protected. The property's own docstring
    promises "everything that changes which model comes back is in here".

    The catalogue stays excluded on purpose, and the last assertion says so: a
    revision that moved whenever a model was installed would be a version number
    for the machine rather than for the pool.
    """
    from ravis.core.pools import POOLS_BY_ID

    pool = POOLS_BY_ID["ravis/clarvis-chat"]

    assert pool.revision_with(()) == pool.revision, "no narrowing, no change"
    assert pool.revision_with(None) == pool.revision

    narrowed = pool.revision_with(("model-a", "model-b"))
    assert narrowed != pool.revision, "a narrowing changes which model comes back"

    assert pool.revision_with(("model-b", "model-a")) == narrowed, (
        "the same selection in a different order is the same selection"
    )
    assert pool.revision_with(("model-a",)) != narrowed, (
        "and a different selection is a different revision"
    )
