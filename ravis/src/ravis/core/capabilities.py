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

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ravis.cost import Price

from dataclasses import dataclass, field
from enum import Enum


class Capability(str, Enum):
    """The capabilities RAVIS tracks (§9.5).

    A str enum so a capability survives a round trip through JSON — these end up
    in route explanations and management responses, and an opaque integer there
    would be unreadable in exactly the situation someone is reading it.
    """

    TEXT = "text"
    # **Reading an image, not making one.** These are two capabilities and not
    # one mode of the same: `qwen2.5vl` reads a page and can never draw
    # anything, and the six hundred vision-capable models in a catalogue
    # include eleven that emit an image. Conflating them would put a model
    # that cannot draw into a pool that asks for a drawing.
    VISION = "vision"
    IMAGE_OUT = "image_out"
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
    # What a million tokens through this model costs, in USD, prompt plus
    # completion.
    #
    # A number rather than a claim, like `context_window` and for the same
    # reason: it is not something a model can partially support, so the
    # provenance ladder has nothing to arbitrate.
    #
    # **`0.0` and `None` are entirely different answers.** Zero is a fact about
    # a model running on hardware already paid for — a local runtime charges
    # nothing per token, and that is the single strongest argument a router has
    # for preferring it. `None` means nobody published a price, which is the
    # ordinary case for OpenAI, Google and Anthropic: their catalogues carry no
    # pricing at all. Ranking must sort `None` *last* rather than treat it as
    # free, or every unpriced model wins the cheap pool by being unmeasured.
    price_per_million: float | None = None

    # §14's split price, when the provider publishes one. Separate from the
    # blended figure above rather than replacing it: that one exists to *rank*
    # and is consulted on every routing pass, while this one exists to compute
    # what a call cost and is consulted once, afterwards. Summing input and
    # output is right for the first and useless for the second, since the two
    # differ by an order of magnitude nearly everywhere.
    price: Price | None = None

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


def apply_configured(known: ModelCapabilities, declared: dict[str, str]) -> None:
    """Record an operator's declared capabilities for one model.

    Shared by every adapter rather than reimplemented per provider, because the
    override is a fact about the *operator's deployment* and not about the
    upstream: the same declaration must mean the same thing whichever provider
    it is attached to, and two copies of this loop is how they stop meaning it.

    An unrecognised capability or state name is ignored rather than raising: a
    typo in configuration should cost that one claim, not the ability to route
    at all. It stays UNKNOWN, which fails closed — the safe direction for a
    mistake to fail in.
    """
    # `context_window` is a number rather than a capability state, and it is an
    # operator's only way to make a pool with a minimum context usable against
    # an upstream that publishes no windows — a declared minimum fails closed on
    # an unknown one.
    window = declared.get("context_window")
    if window is not None and str(window).isdigit():
        known.context_window = int(window)
    for name, state in declared.items():
        if name == "context_window":
            continue
        try:
            capability = Capability(name)
            claimed = CapabilityState(state)
        except ValueError:
            continue
        known.record(
            CapabilityClaim(
                capability=capability,
                state=claimed,
                provenance=Provenance.CONFIGURED,
                detail="declared in configuration",
            )
        )
