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

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
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
        return {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
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
