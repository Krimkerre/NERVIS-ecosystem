"""An operator narrowing a pool, and the one thing they may not do.

A pool is its invariants and picks from whatever satisfies them. That keeps
working as a catalogue changes, and it is the right default — but with four API
providers publishing six hundred models between them it is a wider net than most
people want `ravis/fast` casting.

So a selection is an **optional narrowing**, and the safety argument is entirely
in the word *narrowing*: an operator may remove a model from a pool and may not
add one that fails its invariants. `ravis/local` promises the request never
leaves this machine, and a promise somebody can tick away in a picker is not a
promise.
"""

from __future__ import annotations

import pytest

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.pool_membership import PoolMembership
from ravis.routing.engine import RoutingEngine


def capable(model_id: str) -> ModelCapabilities:
    known = ModelCapabilities(model_id)
    for capability in Capability:
        known.record(CapabilityClaim(capability, CapabilityState.SUPPORTED, Provenance.MEASURED))
    known.context_window = 200_000
    return known


CANDIDATES = {name: capable(name) for name in ("local-model", "a/one", "a/two", "b/three")}
REMOTE = frozenset({"a/one", "a/two", "b/three"})


def selected(pool: str, chosen: tuple[str, ...]) -> str | None:
    return RoutingEngine().select(
        pool, CANDIDATES, remote_models=REMOTE, chosen=chosen
    ).selected


# ── Narrowing ──────────────────────────────────────────────────────────────


def test_an_empty_selection_leaves_the_pool_as_it_was() -> None:
    """Which is what a pool has always meant, and what it returns to."""
    assert selected("ravis/balanced", ()) in CANDIDATES


def test_a_selection_restricts_what_the_pool_may_choose() -> None:
    assert selected("ravis/balanced", ("b/three",)) == "b/three"


def test_a_selection_cannot_widen_past_the_invariants() -> None:
    """The whole safety argument.

    Ticking a remote model into `ravis/local` must not put it there — the
    invariant is applied first and the selection second, so the worst a bad
    selection can do is empty a pool.
    """
    assert selected("ravis/local", ("a/one", "a/two")) is None


def test_a_selection_that_excludes_everything_refuses_rather_than_relaxes() -> None:
    decision = RoutingEngine().select(
        "ravis/balanced", CANDIDATES, remote_models=REMOTE, chosen=("not-a-model",)
    )

    assert decision.selected is None
    assert "unavailable" in decision.reason


def test_the_refusal_names_the_selection_as_the_reason() -> None:
    """§9.7 again: a route explanation says what excluded a candidate, and
    "you did not tick it" is a different fix from "it lacks a capability"."""
    decision = RoutingEngine().select(
        "ravis/balanced", CANDIDATES, remote_models=REMOTE, chosen=("b/three",)
    )

    reasons = [r for e in decision.excluded for r in e.reasons]
    assert any("not among the models chosen for this pool" in r for r in reasons)


# ── Storage ────────────────────────────────────────────────────────────────


@pytest.fixture()
def store(tmp_path) -> PoolMembership:
    return PoolMembership(tmp_path / "pools.json")


def test_a_selection_round_trips(store: PoolMembership) -> None:
    store.set_for("ravis/fast", ("a/one", "a/two"))

    assert store.for_pool("ravis/fast") == ("a/one", "a/two")


def test_an_empty_selection_removes_the_entry_rather_than_storing_it(
    store: PoolMembership,
) -> None:
    """`[]` and "no entry" behave identically, so keeping both would leave the
    file carrying decisions nobody made."""
    store.set_for("ravis/fast", ("a/one",))
    store.set_for("ravis/fast", ())

    assert store.for_pool("ravis/fast") == ()
    assert "ravis/fast" not in store.all()


def test_a_corrupt_file_narrows_nothing(store: PoolMembership) -> None:
    """The permissive direction its siblings take. A convenience file must not
    be able to make a gateway serve nothing."""
    store.path.write_text("{not json", encoding="utf-8")

    assert store.all() == {}
    assert store.for_pool("ravis/fast") == ()


def test_non_strings_in_the_file_are_dropped(store: PoolMembership) -> None:
    store.path.write_text('{"ravis/fast": ["a/one", 3, null, ""]}', encoding="utf-8")

    assert store.for_pool("ravis/fast") == ("a/one",)
