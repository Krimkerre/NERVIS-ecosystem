"""A fake upstream, in-process, so no test touches a network (runbook §14.5).

Built on httpx's MockTransport rather than a live server: a real socket makes
the suite slow, flaky and dependent on a port being free, and none of that
measures anything about RAVIS.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

import httpx

# A realistic streamed tool call, fragmented exactly as RAVIS.md §8.3 describes.
# The arguments are split mid-token on purpose: this is the shape that breaks
# when a proxy reassembles frames, and the reason the transparent path never
# parses them.
TOOL_CALL_FRAMES = [
    b'data: {"choices":[{"delta":{"role":"assistant"},"index":0}]}\n\n',
    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_123",'
    b'"function":{"name":"edit_file","arguments":"{\\"pa"}}]},"index":0}]}\n\n',
    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
    b'"function":{"arguments":"th\\":\\"foo"}}]},"index":0}]}\n\n',
    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
    b'"function":{"arguments":".ts\\"}"}}]},"index":0}]}\n\n',
    b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls","index":0}]}\n\n',
    b"data: [DONE]\n\n",
]


class RecordingUpstream:
    """Answers like an OpenAI-compatible server, and records what happened.

    `frames_pulled` is the interesting one: it counts how many chunks were
    actually taken from the generator, which is how a test can tell whether a
    disconnect stopped the upstream or merely stopped the client reading.
    """

    def __init__(self, frames: list[bytes] | None = None) -> None:
        self.frames = frames if frames is not None else TOOL_CALL_FRAMES
        self.frames_pulled = 0
        self.requests: list[httpx.Request] = []
        self.models_requests = 0
        self.residency_requests = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        # LM Studio's native endpoint also ends in "/models", so the two are
        # distinguished by full path — counting them together once hid a second
        # upstream call behind an assertion that looked like it was passing.
        if request.url.path == "/api/v0/models":
            self.residency_requests += 1
            return httpx.Response(404, json={"error": "not lmstudio"})
        if request.url.path.endswith("/models"):
            self.models_requests += 1
            return httpx.Response(200, json={"object": "list", "data": self._catalogue()})
        if b'"stream": true' in request.content or b'"stream":true' in request.content:
            return httpx.Response(200, stream=_IterableStream(self._frames()))
        return httpx.Response(200, json=self._completion())

    def _frames(self) -> Iterator[bytes]:
        for frame in self.frames:
            self.frames_pulled += 1
            yield frame

    @staticmethod
    def _catalogue() -> list[dict[str, Any]]:
        return [
            {"id": "qwen2.5-coder-7b", "object": "model", "owned_by": "local"},
            {"id": "llama-3.1-8b", "object": "model", "owned_by": "local"},
        ]

    @staticmethod
    def _completion() -> dict[str, Any]:
        """A non-streamed completion, with the `usage` a real upstream returns.

        It had no `usage` key at all, which is not a shape any OpenAI-compatible
        server produces -- and that omission is why no test could catch the
        transparent non-streaming path recording no usage. A fixture that cannot
        carry the field cannot fail when the field is dropped.
        """
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 7, "total_tokens": 22},
        }


class _IterableStream(httpx.AsyncByteStream):
    """Adapts an iterator of bytes into a stream httpx's async client can read.

    Async rather than sync because RAVIS forwards through an AsyncClient, and a
    fake that streams synchronously would not exercise the path that actually
    runs — including the cancellation behaviour these tests are here to check.
    """

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def failing_transport(status: int, body: dict[str, Any]) -> httpx.MockTransport:
    """An upstream that answers with an error, for the error-forwarding tests."""

    def handle(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status, content=json.dumps(body).encode())

    return httpx.MockTransport(handle)


# One Anthropic message, streamed: a sentence, then a tool call. The order is
# the point — the tool call is Anthropic content block **1**, and OpenAI tool
# index **0**, which is the translation that misassembles every fragment when it
# is wrong. The arguments are split mid-string for the same reason the OpenAI
# fixture above splits them.
ANTHROPIC_TOOL_FRAMES = [
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_01",'
    b'"model":"claude-opus-5","usage":{"input_tokens":42}}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
    b'"content_block":{"type":"text","text":""}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
    b'"delta":{"type":"text_delta","text":"Editing."}}\n\n',
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":1,'
    b'"content_block":{"type":"tool_use","id":"toolu_9","name":"edit_file","input":{}}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,'
    b'"delta":{"type":"input_json_delta","partial_json":"{\\"pa"}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":1,'
    b'"delta":{"type":"input_json_delta","partial_json":"th\\":\\"foo.ts\\"}"}}\n\n',
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use"},'
    b'"usage":{"output_tokens":17}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
]

# What `GET /v1/models/{id}` answers. Trimmed to the leaves the adapter reads,
# and deliberately **without** a tool-support key: that is the real catalogue's
# shape, and the reason tool support stays UNKNOWN until an operator says
# otherwise.
ANTHROPIC_CATALOGUE_ENTRY = {
    "id": "claude-opus-5",
    "display_name": "Claude Opus 5",
    "max_input_tokens": 1000000,
    "max_tokens": 128000,
    "capabilities": {
        "image_input": {"supported": True},
        "structured_outputs": {"supported": True},
        "thinking": {"supported": True},
    },
}


class RecordingAnthropic:
    """A fake Anthropic Messages API, in process, that records what reached it.

    Answers the three endpoints the adapter uses and nothing else. `requests`
    holds every httpx request it saw, which is how a test asserts on the
    *translated body* rather than on what the adapter says it sent.
    """

    def __init__(
        self,
        frames: list[bytes] | None = None,
        message: dict[str, Any] | None = None,
        status: int = 200,
        error: dict[str, Any] | None = None,
    ) -> None:
        self.frames = frames if frames is not None else ANTHROPIC_TOOL_FRAMES
        self.message = message or {
            "id": "msg_01",
            "type": "message",
            "model": "claude-opus-5",
            "content": [{"type": "text", "text": "Hello."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }
        self.status = status
        self.error = error
        self.frames_pulled = 0
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def body_of(self, index: int = -1) -> dict[str, Any]:
        """The JSON body of one recorded request — what translation produced."""
        return json.loads(self.requests[index].content)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error is not None:
            return httpx.Response(self.status, json=self.error)
        if request.url.path.startswith("/v1/models/"):
            return httpx.Response(200, json=ANTHROPIC_CATALOGUE_ENTRY)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "claude-opus-5"}]})
        if b'"stream": true' in request.content or b'"stream":true' in request.content:
            return httpx.Response(200, stream=_IterableStream(self._frames()))
        return httpx.Response(self.status, json=self.message)

    def _frames(self) -> Iterator[bytes]:
        for frame in self.frames:
            self.frames_pulled += 1
            yield frame
