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
from typing import Any, AsyncGenerator, Protocol, runtime_checkable

from ravis.core.capabilities import ModelCapabilities
from ravis.core.requests import NormalizedRequest
from ravis.core.responses import NormalizedResponse, NormalizedStreamEvent

#: How long a health probe may take before it is called unreachable.
#:
#: **Separate from `upstream_timeout_seconds`, which is 300 seconds.** That
#: number is right for what it governs — streaming a long completion — and wrong
#: for asking whether a provider is alive. The probe shares the upstream client,
#: so it inherited the 300, and a provider that accepted a connection and then
#: stalled would have held the Providers screen for five minutes. The client's
#: 10-second connect timeout does not cover it: a server answering slowly is not
#: a server failing to connect.
#:
#: Five seconds is above every real measurement taken here — OpenAI's `/models`
#: is the slowest at ~830 ms — and far below the point where a person decides
#: the page is broken. A probe that exceeds it is reported unreachable, which is
#: the honest answer: a provider this slow to say hello is not one to route to.
HEALTH_TIMEOUT_SECONDS = 5.0


class ProtocolMode(str, Enum):
    """Which execution path an adapter's upstream requires (§6).

    Diagnostics must be able to report which one ran (§6), because when a tool
    call arrives malformed the first question is whether it travelled through a
    translation at all — and that question should be answerable from a trace
    rather than from reading configuration.
    """

    OPENAI_TRANSPARENT = "OPENAI_TRANSPARENT"
    TRANSLATED = "TRANSLATED"


class TranslationError(Exception):
    """A request a translating adapter refuses, rather than mistranslates.

    Part of the adapter contract rather than of any one provider, because §6's
    fork is what has to classify it: a request RAVIS itself could not render is
    an **invalid request**, not an upstream failure, so it must not retry, must
    not fall back, and must not count against the provider's circuit breaker.
    One client's malformed body taking a provider offline for every other
    caller on the machine is the failure this distinction prevents.
    """


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
        forbids presenting an estimate as an invoice.

        **Every adapter still returns `None`, and that is now a design choice
        rather than a gap.** This said the pricing "arrives with the cost engine
        at M15"; M15 shipped, and it did not price per adapter. Cost is computed
        once, centrally, from a `PriceBook` filled by the catalogue and by the
        operator's `prices.json` -- so the answer does not depend on which
        adapter served the call, and a provider that publishes no price is
        `UNKNOWN` everywhere rather than in one adapter's opinion.

        The hook stays because the protocol may want a provider that can quote a
        request before running it. Nothing does today.
        """
        ...


@runtime_checkable
class TranslatingAdapter(ProviderAdapter, Protocol):
    """An adapter whose upstream speaks its own protocol (§6, Path B).

    Implemented at M3b. Declared now because M3a's job is to fix the surface
    that M6 filters against, and leaving the translated half undeclared would
    invite it to be invented differently later.
    """

    @property
    def has_credential(self) -> bool:
        """Whether this provider could authenticate a request sent right now.

        A translated provider is registered whether or not a key exists, so that
        the Credentials screen can say the provider is reachable *before* anyone
        has typed one — the alternative reads "unroutable" precisely when a
        person is about to fix it, and hides that a restart used to be needed.
        Routing consults this instead.
        """
        ...

    async def complete(self, request: NormalizedRequest) -> NormalizedResponse:
        """Run a non-streaming generation and normalize the result."""
        ...

    def stream(self, request: NormalizedRequest) -> AsyncGenerator[NormalizedStreamEvent, None]:
        """Run a streaming generation, yielding normalized events.

        Not `async def`: this returns an async generator rather than awaiting
        one, so that closing it propagates cancellation to the upstream the way
        the transparent relay does (§8.6).

        **A generator rather than an iterator, and the difference is the whole
        of §8.6.** `aclose()` is part of the contract here, not an
        implementation detail: it is what unwinds an adapter's own
        `async with client.stream(...)` when the client disconnects, so the
        provider stops generating and — on a paid provider — stops billing. An
        iterator makes no such promise, and M3b's relay could not close one.
        """
        ...


def describe(adapter: ProviderAdapter) -> dict[str, Any]:
    """A small, redaction-safe summary for diagnostics and events.

    Deliberately carries no credential, no base URL and no header: this shape
    reaches NERVIS, traces and route explanations, and runbook §9 keeps
    credentials in their owning store.
    """
    return {"provider": adapter.name, "protocol_mode": adapter.protocol_mode.value}
