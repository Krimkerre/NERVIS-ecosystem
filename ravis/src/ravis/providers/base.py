"""The adapter surface (RAVIS.md §7), split along the line §6 draws.

**A deliberate deviation from §7's sketch, and the reason for it.**

§7 sketches one `ProviderAdapter` carrying `health`, `models`, `capabilities`,
`complete`, `stream` and `estimate_cost`. Implemented literally, every
transparent adapter would have to supply `complete` and `stream` that nothing
ever calls — because §6 is emphatic that a transparent route forwards bytes and
must *not* parse and re-serialise them. Those methods would raise
`NotImplementedError`, and an interface whose methods lie about what they do is
worse than two honest ones.

So the surface is split by what an adapter can truthfully offer:

    ProviderAdapter      health · models · capabilities · estimate_cost
                         Every adapter. This is discovery, and it is what M6's
                         capability filtering needs.

    TranslatingAdapter   the above, plus complete · stream
                         Only adapters whose upstream does not speak the client's
                         protocol. Implemented at M3b, used on Path B.

Both are `Protocol` classes rather than base classes: an adapter satisfies them
by shape, so nothing has to inherit from RAVIS to be one, and a test double is a
plain object rather than a subclass.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from ravis.core.capabilities import ModelCapabilities
from ravis.core.requests import NormalizedRequest
from ravis.core.responses import NormalizedResponse, NormalizedStreamEvent


class ProtocolMode(str, Enum):
    """Which execution path an adapter's upstream requires (§6).

    Diagnostics must be able to report which one ran (§6), because when a tool
    call arrives malformed the first question is whether it travelled through a
    translation at all — and that question should be answerable from a trace
    rather than from reading configuration.
    """

    OPENAI_TRANSPARENT = "OPENAI_TRANSPARENT"
    TRANSLATED = "TRANSLATED"


class ProviderHealth:
    """Whether an upstream is reachable, and what it said if not."""

    def __init__(self, reachable: bool, detail: str = "", latency_ms: float | None = None) -> None:
        self.reachable = reachable
        self.detail = detail
        self.latency_ms = latency_ms


@runtime_checkable
class ProviderAdapter(Protocol):
    """What every adapter can answer about its upstream.

    Discovery only. Nothing here sends a completion, which is why a transparent
    adapter can implement all of it honestly.
    """

    name: str
    protocol_mode: ProtocolMode

    async def health(self) -> ProviderHealth:
        """Whether the upstream is reachable right now."""
        ...

    async def models(self) -> list[str]:
        """The model IDs this upstream will accept.

        Returns an empty list when the upstream offers none — not an error, and
        not `None`. An unconfigured or empty provider is a legitimate state
        (runbook §14.4).
        """
        ...

    async def capabilities(self, model: str) -> ModelCapabilities:
        """What is known about one model, and how confidently.

        Must not overstate. §7 allows a provider family to seed defaults but
        forbids asserting safety- or capability-critical behaviour without
        authoritative metadata or probing — so an adapter that cannot establish
        tool support returns `UNKNOWN` for it and lets the pool fail closed,
        rather than guessing and letting an agent route to a model that cannot
        call tools.
        """
        ...

    async def estimate_cost(self, request: NormalizedRequest) -> float | None:
        """The estimated monetary cost, or `None` when it is not known.

        `None` rather than `0.0`, because they are different claims and §14
        forbids presenting an estimate as an invoice. The pricing that makes this
        answerable arrives with the cost engine at M15; until then every adapter
        honestly returns `None`.
        """
        ...


@runtime_checkable
class TranslatingAdapter(ProviderAdapter, Protocol):
    """An adapter whose upstream speaks its own protocol (§6, Path B).

    Implemented at M3b. Declared now because M3a's job is to fix the surface
    that M6 filters against, and leaving the translated half undeclared would
    invite it to be invented differently later.
    """

    async def complete(self, request: NormalizedRequest) -> NormalizedResponse:
        """Run a non-streaming generation and normalize the result."""
        ...

    def stream(self, request: NormalizedRequest) -> AsyncIterator[NormalizedStreamEvent]:
        """Run a streaming generation, yielding normalized events.

        Not `async def`: this returns an async iterator rather than awaiting one,
        so that closing it propagates cancellation to the upstream the way the
        transparent relay does (§8.6).
        """
        ...


def describe(adapter: ProviderAdapter) -> dict[str, Any]:
    """A small, redaction-safe summary for diagnostics and events.

    Deliberately carries no credential, no base URL and no header: this shape
    reaches NERVIS, traces and route explanations, and runbook §9 keeps
    credentials in their owning store.
    """
    return {"provider": adapter.name, "protocol_mode": adapter.protocol_mode.value}
