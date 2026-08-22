"""Clarvis's own reading of an OpenAI stream, ported faithfully.

RAVIS.md §8.8: *use the real Clarvis expectations wherever possible; mirror its
behaviour rather than inventing a different interpretation of the protocol.*

That instruction is the whole reason this file exists rather than a convenient
JSON parse. A conformance suite that reads streams its own way tests RAVIS
against RAVIS's idea of the protocol, which is exactly the assumption the suite
is supposed to be checking. So both functions below are ports of the real thing:

    SseReader.push        ← clarvis/src/model/sse.ts, class SseParser
    absorb_tool_deltas    ← clarvis/src/model/OpenAiCompatibleProvider.ts

If Clarvis changes either, this file is wrong and the suite is lying. Anything
here that drifts from those two files is a defect in this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DATA_PREFIX = "data:"
DONE_PAYLOAD = "[DONE]"


class SseReader:
    """Splits a byte stream into `data:` payloads, the way Clarvis does.

    The important property is that it is **chunk-boundary independent**: an
    incomplete line is buffered until the rest arrives. That matters here
    because a proxy is free to re-chunk a stream — TCP makes no promise that a
    frame arrives whole — and a suite that assumed frame-aligned chunks would
    pass a broken proxy and fail a correct one.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def push(self, chunk: bytes) -> list[str]:
        """Feed bytes in, get back whatever complete payloads they completed."""
        self._buffer += chunk.decode("utf-8", errors="replace")
        lines = self._buffer.split("\n")
        # The final element is an incomplete line, or empty after a trailing
        # newline. Either way it is not ready, so it stays buffered.
        self._buffer = lines.pop() if lines else ""
        payloads = []
        for line in lines:
            trimmed = line.rstrip()  # drops the \r of \r\n
            # Comment lines are keep-alives from some providers, not data.
            if not trimmed or trimmed.startswith(":"):
                continue
            if not trimmed.startswith(DATA_PREFIX):
                continue
            payloads.append(trimmed[len(DATA_PREFIX):].strip())
        return payloads


@dataclass
class PartialCall:
    """One tool call being assembled across frames."""

    id: str = ""
    name: str = ""
    arguments: str = ""


def absorb_tool_deltas(pending: dict[int, PartialCall], deltas: list[dict[str, Any]]) -> None:
    """Fold streamed tool-call fragments into the calls they belong to.

    **Keyed by index, which is the whole difficulty.** OpenAI splits one call
    across many frames and identifies them only by position: the name arrives
    once, the id sometimes, and the arguments a few characters at a time. So
    every field falls back to what was already known, and only `arguments`
    accumulates.

    This is the function that silently produces a malformed tool call when it is
    wrong — which is why RAVIS.md §8.3 calls the fragment semantics
    release-critical, and why the transparent path refuses to touch them.
    """
    for delta in deltas:
        index = delta.get("index", 0)
        existing = pending.get(index, PartialCall())
        function = delta.get("function") or {}
        pending[index] = PartialCall(
            id=delta.get("id") or existing.id,
            name=function.get("name") or existing.name,
            arguments=existing.arguments + (function.get("arguments") or ""),
        )


@dataclass
class ReadStream:
    """Everything a Clarvis-side reader extracted from one response.

    `saw_done` is tracked separately from stream end because they are different
    facts: a stream that stops without `[DONE]` leaves Clarvis waiting for more,
    and a suite that treated end-of-body as completion would not notice.
    """

    text: str = ""
    reasoning: str = ""
    tool_calls: dict[int, PartialCall] = field(default_factory=dict)
    finish_reasons: list[str] = field(default_factory=list)
    saw_done: bool = False
    malformed_frames: int = 0


def read_stream(chunks: list[bytes]) -> ReadStream:
    """Consume a stream as Clarvis would, and report what arrived.

    Malformed frames are counted rather than raised, matching Clarvis: one bad
    chunk should cost a few tokens, not the whole answer a user is watching
    arrive.
    """
    import json

    reader = SseReader()
    result = ReadStream()
    for chunk in chunks:
        for payload in reader.push(chunk):
            if payload == DONE_PAYLOAD:
                result.saw_done = True
                continue
            try:
                frame = json.loads(payload)
            except json.JSONDecodeError:
                result.malformed_frames += 1
                continue
            _absorb_frame(result, frame)
    return result


def _absorb_frame(result: ReadStream, frame: dict[str, Any]) -> None:
    """Apply one decoded frame to the accumulating read."""
    for choice in frame.get("choices") or []:
        delta = choice.get("delta") or {}
        result.text += delta.get("content") or ""
        # §8.5: reasoning is preserved *separately*. Merging it into content is
        # the failure this field exists to prevent, so the reader keeps them
        # apart and the suite asserts on both.
        result.reasoning += delta.get("reasoning_content") or ""
        if delta.get("tool_calls"):
            absorb_tool_deltas(result.tool_calls, delta["tool_calls"])
        if choice.get("finish_reason"):
            result.finish_reasons.append(choice["finish_reason"])
