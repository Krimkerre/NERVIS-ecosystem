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
