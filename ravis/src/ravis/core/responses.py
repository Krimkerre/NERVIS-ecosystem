"""Provider-independent response and stream-event shapes (RAVIS.md §7).

These exist for two consumers: translated adapters, which build them from a
provider's native output, and observability, which reads them regardless of
which path ran. The transparent path produces no normalized response at all —
it forwards bytes — and that absence is correct rather than a gap.

The stream event type is the more delicate of the two. §8.3 calls tool-call
fragment semantics release-critical, so the event carries the fragment *as it
arrived* — index, optional id, optional name, and whatever slice of the
arguments this frame held — rather than an assembled call. Assembly is the
consumer's job, and doing it here would mean a translated path silently
reframing what a transparent path preserves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FinishReason(str, Enum):
    """Why generation stopped, in OpenAI's vocabulary.

    OpenAI's names rather than invented ones, because these reach the client on
    the wire and the client is an OpenAI-compatible consumer. `CANCELLED` is the
    addition: §8.6 insists cancellation is not a failure, and giving it a name
    keeps it from being recorded as an error or billed as a completion.
    """

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class Usage:
    """Token counts, where the provider reported them.

    Every field is optional and stays `None` when unreported. §14 is explicit
    that unknown usage stays unknown and that an estimate is never presented as
    an invoice, so a zero here would be a lie with a currency attached — `None`
    and `0` mean different things and the cost engine at M15 depends on it.
    """

    # **`input_tokens` includes `cached_input_tokens`.** OpenAI and Google both
    # report it that way -- `prompt_tokens` and `promptTokenCount` are totals,
    # with the cached figure a subset -- and `cost.estimate` prices the two
    # halves apart by subtracting one from the other. Anthropic reports them as
    # disjoint counts, so its adapter adds them before they arrive here.
    # Stated because it cannot be inferred from the field names, and the one
    # adapter that got it wrong understated every cached call.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    # What the provider said this call cost, where it says so at all. Not an
    # invoice and not RAVIS's arithmetic — see `CostState.REPORTED`. `None`
    # everywhere else, which is most providers.
    reported_cost: float | None = None

    @property
    def is_reported(self) -> bool:
        """Whether the provider told us anything at all."""
        return any(
            value is not None
            for value in (self.input_tokens, self.output_tokens, self.cached_input_tokens)
        )


@dataclass
class ToolCall:
    """One assembled tool call."""

    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class NormalizedResponse:
    """A completed generation, in terms no provider owns."""

    text: str = ""
    reasoning: str = ""
    # Images the model *emitted*, as `data:` URLs — not images it was shown.
    # A generation model answers with pixels, and text-only normalization
    # discards them silently: a 200 with an empty answer, which reads as the
    # model refusing rather than as the gateway dropping the payload.
    images: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: FinishReason = FinishReason.UNKNOWN
    usage: Usage = field(default_factory=Usage)
    provider: str = ""
    model: str = ""
    latency_ms: float | None = None
    provider_request_id: str = ""


class StreamEventType(str, Enum):
    """The kinds of thing a stream can carry."""

    TEXT = "text"
    REASONING = "reasoning"
    IMAGE = "image"
    TOOL_CALL_FRAGMENT = "tool_call_fragment"
    FINISH = "finish"
    USAGE = "usage"
    ERROR = "error"


@dataclass
class NormalizedStreamEvent:
    """One thing that happened during a stream.

    A fragment, not a call. The `arguments` field holds whatever slice of the
    JSON this frame carried — frequently not valid JSON on its own, and that is
    the normal case rather than a defect. Reassembly belongs to whoever consumes
    the stream, exactly as it does on the wire, so that a translated path
    reproduces the same fragment boundaries a transparent path would have passed
    through untouched.
    """

    type: StreamEventType
    text: str = ""
    # A whole `data:` URL, never a slice. Images arrive as one part rather than
    # fragmented, so there is nothing here for a consumer to reassemble.
    image_url: str = ""
    tool_index: int | None = None
    tool_id: str = ""
    tool_name: str = ""
    arguments: str = ""
    finish_reason: FinishReason | None = None
    usage: Usage | None = None
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
