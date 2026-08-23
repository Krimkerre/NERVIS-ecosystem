"""What a model can do, how confidently we know it, and where that came from.

RAVIS.md §9.5 lists the capabilities and their four states. §5.2 makes them
load-bearing: a pool declares requirements, every eligible member must satisfy
them, and a pool with no satisfying candidate is *unavailable* rather than
routed-to-anyway. §9.1 settles the hard case — an unknown capability **fails
closed** when the pool requires it.

The part worth understanding before changing anything here is why provenance is
a separate field rather than folded into the state.

A model advertising `tool_use` and a model *measured* calling tools successfully
are not the same claim, and they disagree in both directions. The prototype in
`template/` found exactly that on one machine: three installed builds advertised
nothing and worked, and one advertised support while losing seven tool calls in
eight to its runtime's parser. Collapsing those into a single boolean discards
the disagreement, and the disagreement is the data — it is what tells you the
advertisement is unreliable for that runtime.

So a capability carries both what is claimed and how it came to be known, and a
measurement outranks an advertisement rather than overwriting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Capability(str, Enum):
    """The capabilities RAVIS tracks (§9.5).

    A str enum so a capability survives a round trip through JSON — these end up
    in route explanations and management responses, and an opaque integer there
    would be unreadable in exactly the situation someone is reading it.
    """

    TEXT = "text"
    VISION = "vision"
    AUDIO_IN = "audio_in"
    AUDIO_OUT = "audio_out"
    TOOLS = "tools"
    PARALLEL_TOOLS = "parallel_tools"
    STRUCTURED_OUTPUT = "structured_output"
    REASONING = "reasoning"
    STREAMING = "streaming"
    EMBEDDINGS = "embeddings"
    PROMPT_CACHING = "prompt_caching"


class CapabilityState(str, Enum):
    """Whether a model has a capability (§9.5).

    `UNKNOWN` is a first-class answer and the most important one here. It is what
    an honest adapter returns for a generic OpenAI-compatible endpoint, which
    advertises nothing beyond model IDs — and it is distinct from `UNSUPPORTED`,
    which is a positive claim that the model cannot do the thing.

    Conflating them would be the expensive mistake: `UNSUPPORTED` correctly
    excludes a model forever, while `UNKNOWN` should merely fail closed *today*
    and become known once something probes or measures it.
    """

    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class Provenance(str, Enum):
    """How a capability claim came to be believed.

    Ordered by trust, weakest first. `MEASURED` outranks `ADVERTISED` because a
    model that demonstrably called a tool is better evidence than a flag in a
    catalogue, and because catalogues have been observed to be wrong in both
    directions.

    `CONFIGURED` sits above measurement deliberately: an operator overriding a
    capability has said something about their own deployment that RAVIS cannot
    observe, and silently out-voting them would be the wrong kind of clever.
    """

    DEFAULT = "DEFAULT"
    ADVERTISED = "ADVERTISED"
    MEASURED = "MEASURED"
    CONFIGURED = "CONFIGURED"


_TRUST_ORDER = {
    Provenance.DEFAULT: 0,
    Provenance.ADVERTISED: 1,
    Provenance.MEASURED: 2,
    Provenance.CONFIGURED: 3,
}


@dataclass(frozen=True)
class CapabilityClaim:
    """One capability, its state, and where the claim came from."""

    capability: Capability
    state: CapabilityState
    provenance: Provenance
    detail: str = ""

    def outranks(self, other: CapabilityClaim) -> bool:
        """True when this claim should win over `other`.

        Ties go to the incumbent rather than the newcomer: re-reading the same
        catalogue should not churn a stored claim, and a later measurement at the
        same provenance is not automatically better than an earlier one.
        """
        return _TRUST_ORDER[self.provenance] > _TRUST_ORDER[other.provenance]


@dataclass
class ModelCapabilities:
    """Everything known about one model's capabilities.

    Claims are stored per capability rather than as a flat set of booleans, so
    that "we measured this" and "the catalogue said so" remain distinguishable
    after the fact. A route explanation that can say *why* it believed a model
    had tools is the difference between a diagnosable bad route and a shrug.
    """

    model_id: str
    claims: dict[Capability, CapabilityClaim] = field(default_factory=dict)
    context_window: int | None = None
    max_output_tokens: int | None = None

    def state_of(self, capability: Capability) -> CapabilityState:
        """The believed state, defaulting to UNKNOWN.

        Absence and ignorance are the same answer here and correctly so: nothing
        has told us about this capability, which is exactly what UNKNOWN means.
        """
        claim = self.claims.get(capability)
        return claim.state if claim else CapabilityState.UNKNOWN

    def record(self, claim: CapabilityClaim) -> None:
        """Store a claim unless something better-sourced is already held.

        This is where measurement beating advertisement actually happens. It is a
        command: it changes state and returns nothing, so a caller cannot mistake
        it for a query about whether the claim won.
        """
        existing = self.claims.get(claim.capability)
        if existing is None or claim.outranks(existing):
            self.claims[claim.capability] = claim

    def satisfies(self, capability: Capability) -> bool:
        """Whether this model may be used where `capability` is REQUIRED.

        **Fails closed** (§9.1): only SUPPORTED qualifies. PARTIAL does not,
        because a pool invariant is a promise and "sometimes" breaks it; UNKNOWN
        does not, because not having asked is not the same as a yes.
        """
        return self.state_of(capability) is CapabilityState.SUPPORTED

    def meets_context(self, minimum: int) -> bool:
        """Whether the context window is known to reach `minimum`.

        An unknown window fails, for the same reason as above: §12 forbids
        silently truncating context, and routing a long request to a model whose
        limit nobody knows is how that happens.
        """
        return self.context_window is not None and self.context_window >= minimum
