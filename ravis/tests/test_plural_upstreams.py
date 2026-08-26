"""Routing across more than one transparent upstream.

The rule that matters is that three things agree about a collision: the model
list a client reads, the candidate the router picks, and the URL the forwarder
sends to. If those disagree, a request is answered by an upstream the client
could not have known it was talking to.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
)
from ravis.runtime.residency import Residency, ResidencySnapshot
from ravis.transparent import (
    TransparentUpstream,
    merged_candidates,
    merged_catalogue,
    merged_residency,
    model_owners,
    resolve,
)
from ravis.upstream import Upstream
from ravis.upstreams import UpstreamSpec


class _Snapshot:
    def __init__(self, models: list[str]) -> None:
        self.models = [{"id": model, "owned_by": "test"} for model in models]


class _Registry:
    """Only what `resolve` and the merges actually read."""

    def __init__(self, models: list[str], residency: ResidencySnapshot) -> None:
        self._models = models
        self.residency = residency
        self.snapshot = _Snapshot(models)

    def model_ids(self) -> list[str]:
        return list(self._models)

    async def refresh(self) -> None:
        """A canned catalogue needs no refreshing, but the lifespan calls this
        on every registry it finds, so the stub has to answer it."""
        return None


class _Adapter:
    """An adapter that marks what it answered for, so a mix-up is visible."""

    def __init__(self, name: str, tools: bool) -> None:
        self.name = name
        self._tools = tools

    async def capabilities(self, model: str) -> ModelCapabilities:
        known = ModelCapabilities(model_id=model)
        known.record(
            CapabilityClaim(
                capability=Capability.TOOLS,
                state=CapabilityState.SUPPORTED if self._tools else CapabilityState.UNSUPPORTED,
                provenance=Provenance.ADVERTISED,
                detail=self.name,
            )
        )
        return known


def _built(
    name: str, models: list[str], *, tools: bool = True, residency: ResidencySnapshot | None = None
) -> TransparentUpstream:
    return TransparentUpstream(
        spec=UpstreamSpec(name=name, base_url=f"http://{name}.invalid"),
        upstream=Upstream(base_url=f"http://{name}.invalid", declared_key=""),
        adapter=_Adapter(name, tools),  # type: ignore[arg-type]
        registry=_Registry(models, residency or ResidencySnapshot()),  # type: ignore[arg-type]
    )


def _pair() -> dict[str, TransparentUpstream]:
    return {
        "lmstudio": _built("lmstudio", ["shared", "only-lms"]),
        "ollama": _built("ollama", ["shared", "only-ollama"], tools=False),
    }


# ── resolve ──────────────────────────────────────────────────────────────────


def test_an_address_reaches_the_upstream_it_names() -> None:
    """The only way to reach a model two upstreams both serve."""
    assert resolve(_pair(), "ravis/ollama/shared").name == "ollama"


def test_an_unaddressed_model_goes_to_whoever_lists_it() -> None:
    assert resolve(_pair(), "only-ollama").name == "ollama"


def test_a_collision_is_broken_by_declaration_order() -> None:
    """Configuration decides, not dict iteration."""
    assert resolve(_pair(), "shared").name == "lmstudio"

    reversed_pair = dict(reversed(list(_pair().items())))
    assert resolve(reversed_pair, "shared").name == "ollama"


def test_a_model_nobody_lists_still_goes_somewhere() -> None:
    """A catalogue that has not refreshed should not become a 404 here. §5.2
    refuses at the pool when no candidate satisfies it; this is the direct path."""
    assert resolve(_pair(), "never-heard-of-it").name == "lmstudio"


def test_nothing_configured_resolves_to_nothing() -> None:
    assert resolve({}, "anything") is None


# ── the catalogue a client reads ─────────────────────────────────────────────


def test_the_merged_catalogue_lists_a_shared_model_once() -> None:
    data = merged_catalogue(_pair())["data"]
    ids = [entry["id"] for entry in data]

    assert ids.count("shared") == 1
    assert {"only-lms", "only-ollama"} <= set(ids)


def test_pools_are_listed_once_not_once_per_upstream() -> None:
    """§5.0.1: Clarvis picks from this list, and a pool repeated twice is a
    duplicate entry in a UI rather than a second pool."""
    data = merged_catalogue(_pair())["data"]
    pool_ids = [entry["id"] for entry in data if entry["owned_by"] == "ravis"]

    assert len(pool_ids) == len(set(pool_ids))


def test_model_owners_shows_the_collision_the_catalogue_hides() -> None:
    """The list dedupes; an operator still needs to see that it had to."""
    assert model_owners(_pair())["shared"] == ["lmstudio", "ollama"]


# ── residency, and the default that would have invented a fact ───────────────


def test_a_non_reporting_upstreams_models_are_unknown_not_cold() -> None:
    """The trap: once any upstream reports, an absent entry reads as COLD.

    A generic OpenAI-compatible endpoint reports no residency at all. Left
    absent, its models would rank below a genuinely hot one on the strength of
    a default rather than an observation.
    """
    transparents = {
        "lmstudio": _built(
            "lmstudio",
            ["hot-one"],
            residency=ResidencySnapshot(states={"hot-one": Residency.HOT}, known=True),
        ),
        "generic": _built("generic", ["unreported"]),
    }

    merged = merged_residency(transparents)

    assert merged.known
    assert merged.state_of("hot-one") is Residency.HOT
    assert merged.state_of("unreported") is Residency.UNKNOWN


def test_residency_stays_unknown_when_nobody_reports() -> None:
    merged = merged_residency(_pair())

    assert not merged.known
    assert merged.state_of("shared") is Residency.UNKNOWN


# ── candidates, each asked through its own adapter ───────────────────────────


def test_each_upstream_answers_for_its_own_models() -> None:
    """A model on LM Studio must not have the Ollama adapter answer for it."""
    candidates = asyncio.run(merged_candidates(_pair(), None))

    assert candidates["only-lms"].claims[Capability.TOOLS].detail == "lmstudio"
    assert candidates["only-ollama"].claims[Capability.TOOLS].detail == "ollama"


def test_a_collision_is_answered_by_the_same_upstream_that_serves_it() -> None:
    """Otherwise the router would judge a candidate on one upstream's metadata
    and the forwarder would send it to the other."""
    candidates: dict[str, Any] = asyncio.run(merged_candidates(_pair(), None))

    assert candidates["shared"].claims[Capability.TOOLS].detail == "lmstudio"
    assert resolve(_pair(), "shared").name == "lmstudio"
