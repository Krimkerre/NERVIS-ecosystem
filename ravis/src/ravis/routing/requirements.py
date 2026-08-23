"""What a request needs from a model, derived from the request itself.

RAVIS.md §9.5: before scoring, analyse the request for images, tools, response
schema, context requirement, reasoning and streaming.

This is the half M5 could not do. A pool's invariants are static — `clarvis-agent`
always requires tools — but a request carries its own demands, and §5.1 says so
directly: `clarvis-chat` treats tool support as optional *unless the request
itself supplies tools*. A router that only checked pool invariants would happily
send a tools-bearing request to a model that cannot call them, and the failure
would appear as a model that "ignored" the tools rather than as a routing fault.

Every requirement here is a **hard constraint** (§9.2): it eliminates candidates
before any ranking happens, and no preference can outweigh it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ravis.core.capabilities import Capability, ModelCapabilities
from ravis.core.requests import NormalizedRequest

# Characters per token, for estimating how much context a request needs. A rough
# heuristic and openly labelled as one: a real tokenizer is per-model, and
# fetching one for every provider to answer a question this coarse would cost
# more than the estimate is worth. It is used only to exclude models whose known
# window is *clearly* too small, never to trim or reject a request.
CHARS_PER_TOKEN = 4

# Only enforce a context requirement above this many estimated tokens. Below it
# the estimate's error is larger than the number itself, and excluding a model on
# that basis would be arithmetic theatre.
CONTEXT_ESTIMATE_FLOOR = 2000


@dataclass
class RequestRequirements:
    """The capabilities this particular request needs.

    `unverifiable` is separate from the requirements themselves: it collects
    things that could not be checked rather than things that failed. §12 forbids
    silently truncating context, and the honest handling of "this model's window
    is unknown" is to say so in the explanation — not to pretend it passed, and
    not to exclude every model on an endpoint that publishes no window.
    """

    required: set[Capability] = field(default_factory=set)
    estimated_context_tokens: int = 0
    reasons: dict[Capability, str] = field(default_factory=dict)

    def describe(self) -> list[str]:
        """The requirements, in the words a route explanation will show."""
        described = [
            f"{capability.value} REQUIRED ({self.reasons.get(capability, 'requested')})"
            for capability in sorted(self.required, key=lambda item: item.value)
        ]
        if self.estimated_context_tokens >= CONTEXT_ESTIMATE_FLOOR:
            described.append(f"context ≈{self.estimated_context_tokens} tokens (estimated)")
        return described


def analyse(request: NormalizedRequest) -> RequestRequirements:
    """Derive hard requirements from what the client actually sent.

    Deliberately conservative in one direction only: a capability is required
    when the request *uses* it, never when the request merely might. Requiring
    vision because a model could conceivably be shown an image would exclude
    candidates for no reason.
    """
    requirements = RequestRequirements()

    if request.carries_tools:
        # §5.1: tool support is optional for a chat pool *unless the request
        # supplies tools*. Sending them to a model that cannot call them means
        # the tool silently never fires.
        _require(requirements, Capability.TOOLS, "the request supplies tools")

    if request.carries_images:
        _require(requirements, Capability.VISION, "the request contains an image")

    if request.response_schema is not None:
        _require(
            requirements,
            Capability.STRUCTURED_OUTPUT,
            "the request asks for a JSON schema response",
        )

    if request.reasoning_effort:
        _require(requirements, Capability.REASONING, "the request sets reasoning_effort")

    if request.stream:
        _require(requirements, Capability.STREAMING, "the request asks for a stream")

    requirements.estimated_context_tokens = _estimate_context(request)
    return requirements


def _require(requirements: RequestRequirements, capability: Capability, why: str) -> None:
    requirements.required.add(capability)
    requirements.reasons[capability] = why


def _estimate_context(request: NormalizedRequest) -> int:
    """Roughly how much context this request will occupy.

    Counts the characters of message content and tool definitions. Structured
    content parts are counted by their text where they have any — an image part
    contributes its URL length, which understates a real image badly, and that
    is acceptable because the vision requirement above already gates those.
    """
    characters = sum(_characters_in(message) for message in request.messages)
    characters += sum(len(str(tool)) for tool in request.tools)
    return characters // CHARS_PER_TOKEN


def _characters_in(message: dict[str, object]) -> int:
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(part.get("text", ""))) for part in content if isinstance(part, dict))
    return 0


def unmet_by(
    requirements: RequestRequirements, known: ModelCapabilities
) -> list[str]:
    """Every reason this model cannot serve this request.

    All reasons rather than the first, matching how pool invariants report —
    §9.2's gate requires the explanation to name what excluded a candidate, and a
    model failing on both vision and context needs a different fix from one
    failing on vision alone.
    """
    unmet = []
    for capability in sorted(requirements.required, key=lambda item: item.value):
        if not known.satisfies(capability):
            state = known.state_of(capability).value
            why = requirements.reasons.get(capability, "requested")
            unmet.append(f"{capability.value} is {state} but {why}")
    unmet.extend(_context_shortfall(requirements, known))
    return unmet


def _context_shortfall(
    requirements: RequestRequirements, known: ModelCapabilities
) -> list[str]:
    """Exclude a model whose *known* window is clearly too small.

    Note the asymmetry with a pool's declared `minimum_context`, which fails
    closed on an unknown window. Here an unknown window does **not** exclude,
    and the reason is practical rather than principled: a generic
    OpenAI-compatible endpoint publishes no context windows at all, so failing
    closed on this would make every request unroutable against the very upstream
    RAVIS is built to serve.

    A pool's minimum is an operator's explicit demand and deserves the strict
    reading. A request's estimate is RAVIS's own arithmetic, and excluding real
    candidates on the strength of its own guess is the weaker claim. The
    unverified case is surfaced in the explanation instead.
    """
    needed = requirements.estimated_context_tokens
    if needed < CONTEXT_ESTIMATE_FLOOR or known.context_window is None:
        return []
    if known.context_window >= needed:
        return []
    return [f"context window {known.context_window} < estimated {needed} tokens needed"]


def unverified_notes(
    requirements: RequestRequirements, candidates: dict[str, ModelCapabilities]
) -> list[str]:
    """Checks that could not be performed, for the route explanation.

    §9.7 requires an explanation to distinguish facts from unknowns. A context
    requirement nobody could verify is an unknown, and saying so is the
    difference between a route that was checked and one that merely was not
    rejected.
    """
    needed = requirements.estimated_context_tokens
    if needed < CONTEXT_ESTIMATE_FLOOR:
        return []
    unmeasured = sorted(
        model for model, known in candidates.items() if known.context_window is None
    )
    if not unmeasured:
        return []
    return [
        f"context ≈{needed} tokens could not be verified for "
        f"{len(unmeasured)} model(s) with an unpublished window"
    ]
