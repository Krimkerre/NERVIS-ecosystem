"""The provider-independent request shape (RAVIS.md §7).

§7 requires that an OpenAI-compatible route keep the bytes the client sent, so
the transparent path can forward them untouched after a route is chosen —
because §6 is explicit that re-serialising an already-compatible stream buys
nothing and risks the fragile parts: tool-call indexes, fragmented arguments,
reasoning fields, `[DONE]`.

**That requirement is met, and not by anything in this file.** This docstring
used to open with *"one field here does more work than the rest combined:
`original_envelope`"*, and the class docstring said the transparent path
"forwards `original_envelope` instead — which is why that field is not optional
in practice". Neither was true. Path A forwards `call.body` and `call.body_for`
in `ravis/src/ravis/api/openai/chat.py`, which hold the same bytes; the field
was written, described at length, and read nowhere. Found by the dead-code gate
once it learned to look at fields.

What remains here is the *normalized* view, which is what a translated adapter
renders from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedRequest:
    """What a client asked for, in terms no provider owns.

    Built by parsing an incoming request once. Translated adapters (M3b) render
    it into their provider's native shape; the transparent path ignores this
    object entirely and forwards the client's own bytes from the call record.
    """

    messages: list[dict[str, Any]] = field(default_factory=list)
    system: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_choice: Any = None
    response_schema: dict[str, Any] | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    stream: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    # The model the client named. Not a routing decision — §5.3 puts an explicit
    # pool above a request signal — but it is what a direct address resolves to,
    # and it travels untouched on the transparent path.
    requested_model: str = ""

    @property
    def carries_tools(self) -> bool:
        """Whether the request supplies tools.

        A useful routing *signal* and never a role identifier: §5.3 is explicit
        that `ravis/clarvis-agent` outranks the observation that tools happen to
        be present, so nothing may infer the agent role from this alone.
        """
        return bool(self.tools)

    @property
    def carries_images(self) -> bool:
        """Whether any message carries an image part."""
        return any(
            isinstance(part, dict) and part.get("type") == "image_url"
            for message in self.messages
            for part in (message.get("content") or [])
            if isinstance(message.get("content"), list)
        )


def normalize(envelope: bytes, payload: dict[str, Any]) -> NormalizedRequest:
    """Build a NormalizedRequest from a parsed OpenAI-compatible body.

    Takes the already-parsed payload rather than parsing here, because the
    caller has parsed it once for admission control and parsing twice would be
    both wasteful and a chance for the two views to disagree.

    Unknown fields are deliberately dropped from the normalized view. That is
    the point: a translated adapter should not silently forward a parameter it
    does not understand — §7 says unsupported features must never silently
    disappear, and inventing a translation for an unrecognised field is how they
    do. The client's own bytes are kept by the caller, which is what the
    transparent path forwards.

    `envelope` is accepted and unused. Kept in the signature because every call
    site passes it and the argument documents *which* bytes belong to this
    request — dropping it would leave nothing at the boundary pairing the two.
    """
    del envelope
    return NormalizedRequest(
        messages=payload.get("messages") or [],
        system=_extract_system(payload.get("messages") or []),
        tools=payload.get("tools") or [],
        tool_choice=payload.get("tool_choice"),
        response_schema=_extract_schema(payload),
        temperature=payload.get("temperature"),
        max_output_tokens=payload.get("max_tokens") or payload.get("max_completion_tokens"),
        reasoning_effort=payload.get("reasoning_effort"),
        stream=bool(payload.get("stream")),
        metadata=payload.get("metadata") or {},
        requested_model=payload.get("model") or "",
    )


def _extract_system(messages: list[dict[str, Any]]) -> str | None:
    """Pull leading system content out, for providers that take it separately.

    Anthropic and Gemini carry the system prompt as a top-level field rather
    than as a message, so a translated adapter needs it lifted out. Only string
    content is lifted: a structured system message is left in place rather than
    flattened, because flattening it would lose whatever structure it had.
    """
    for message in messages:
        content = message.get("content")
        if message.get("role") == "system" and isinstance(content, str):
            return content
    return None


def _extract_schema(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Find a JSON-schema response format, if the client asked for one."""
    response_format = payload.get("response_format") or {}
    if not isinstance(response_format, dict):
        return None
    if response_format.get("type") != "json_schema":
        return None
    schema = response_format.get("json_schema")
    return schema if isinstance(schema, dict) else None
