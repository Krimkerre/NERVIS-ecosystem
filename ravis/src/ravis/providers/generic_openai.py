"""The adapter for a generic OpenAI-compatible upstream.

The one M1 already forwards to, now able to answer questions about itself. Its
honesty is the interesting part: a generic OpenAI-compatible endpoint publishes
model *identifiers* and nothing else. It does not say which models call tools,
how large their context windows are, or whether they can see an image.

So this adapter answers `UNKNOWN` for almost everything, and that is the correct
answer rather than a gap to be filled with a guess. §7 permits seeding defaults
but forbids asserting safety- or capability-critical behaviour without
authoritative metadata or probing, and tool support is exactly that: §5.1's hard
invariant is that every member of `ravis/clarvis-agent` must satisfy the tool
requirement, so a guess here routes an agent to a model that cannot call tools
and produces a failure far from its cause.

The consequence is worth stating plainly, because it will look like a bug at M6:
against a bare OpenAI-compatible endpoint with no configuration, the agent pool
will be **unavailable**. That is §5.2 working — a pool with no satisfying
candidate is unavailable rather than routed-to-anyway. Configuring capabilities,
probing (§8.7), or SIRVIS evidence (M13) is what makes it available, and each of
those is a real answer where a default would be a fiction.
"""

from __future__ import annotations

import time

import httpx

from ravis.core.capabilities import (
    Capability,
    CapabilityClaim,
    CapabilityState,
    ModelCapabilities,
    Provenance,
    apply_configured,
)
from ravis.core.requests import NormalizedRequest
from ravis.providers.base import ProtocolMode, ProviderHealth
from ravis.upstream import Upstream

# Capabilities implied by speaking the OpenAI chat-completions protocol at all.
# Seeded at DEFAULT provenance so anything better — an advertisement, a probe, a
# measurement, an operator override — replaces them without argument. Neither is
# safety- or capability-critical, which is what makes seeding them permissible.
PROTOCOL_DEFAULTS = {
    Capability.TEXT: CapabilityState.SUPPORTED,
    Capability.STREAMING: CapabilityState.SUPPORTED,
}


class GenericOpenAiAdapter:
    """Discovery for an OpenAI-compatible upstream.

    Satisfies `ProviderAdapter` structurally. Deliberately not a
    `TranslatingAdapter`: this upstream already speaks the client's protocol, so
    RAVIS forwards bytes to it (§6) and there is nothing for a `complete` or
    `stream` method here to do that would not be a re-serialisation.
    """

    protocol_mode = ProtocolMode.OPENAI_TRANSPARENT

    def __init__(
        self,
        upstream: Upstream,
        client: httpx.AsyncClient,
        name: str = "generic_openai",
        configured_capabilities: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.name = name
        self._upstream = upstream
        self._client = client
        # Operator-declared capabilities, keyed by model then capability. The
        # only way to make a tool-requiring pool usable before probing exists,
        # and recorded at CONFIGURED provenance because an operator knows things
        # about their deployment that RAVIS cannot observe.
        self._configured = configured_capabilities or {}

    async def health(self) -> ProviderHealth:
        """Reachability, measured by the cheapest call the protocol offers."""
        if not self._upstream.is_configured:
            return ProviderHealth(reachable=False, detail="no upstream configured")
        started = time.monotonic()
        try:
            response = await self._client.get(
                self._upstream.url_for("/v1/models"), headers=self._headers()
            )
            response.raise_for_status()
        except httpx.HTTPError as failure:
            return ProviderHealth(reachable=False, detail=str(failure))
        return ProviderHealth(reachable=True, latency_ms=(time.monotonic() - started) * 1000)

    async def models(self) -> list[str]:
        """Model IDs the upstream will accept, or an empty list."""
        if not self._upstream.is_configured:
            return []
        try:
            response = await self._client.get(
                self._upstream.url_for("/v1/models"), headers=self._headers()
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []
        entries = payload.get("data", []) if isinstance(payload, dict) else []
        return [entry["id"] for entry in entries if isinstance(entry, dict) and entry.get("id")]

    async def capabilities(self, model: str) -> ModelCapabilities:
        """What is known about `model` — which, here, is mostly nothing.

        Protocol defaults first, operator configuration second, so configuration
        wins. Everything not covered by either is left absent, which reads as
        `UNKNOWN` and fails closed wherever a pool requires it.
        """
        known = ModelCapabilities(model_id=model)
        for capability, state in PROTOCOL_DEFAULTS.items():
            known.record(
                CapabilityClaim(
                    capability=capability,
                    state=state,
                    provenance=Provenance.DEFAULT,
                    detail="implied by the OpenAI chat-completions protocol",
                )
            )
        apply_configured(known, self._configured.get(model, {}))
        return known

    async def estimate_cost(self, request: NormalizedRequest) -> float | None:
        """Unknown until the cost engine lands at M15.

        `None`, never `0.0`: a free local model and an unpriced cloud model are
        different facts, and §14 forbids presenting an estimate as an invoice.
        """
        del request  # No pricing table exists yet; the parameter is the contract.
        return None

    def _headers(self) -> dict[str, str]:
        """RAVIS's own credential for the upstream — never a caller's."""
        if not self._upstream.api_key:
            return {}
        return {"authorization": f"Bearer {self._upstream.api_key}"}
