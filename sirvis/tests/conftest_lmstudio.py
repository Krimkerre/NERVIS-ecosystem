"""A recorded LM Studio, in process, so no test touches a runtime (§14.5).

The payloads are the real shapes this machine's LM Studio 0.4.21 returns, kept
verbatim rather than tidied. Two details in them are load-bearing and would be
lost by a cleaner fixture:

- `loaded_context_length` differs from `max_context_length`. A model loaded at
  8K on a build advertising 32K is the §7.1 case in the wild.
- An unknown endpoint answers **200 with an error body**. Any fixture that
  returned 404 for those would let an adapter bug through.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

INSTALLED = [
    {
        "id": "qwen2.5-coder-7b-instruct",
        "object": "model",
        "type": "llm",
        "publisher": "mlx-community",
        "arch": "qwen2",
        "compatibility_type": "mlx",
        "quantization": "4bit",
        "state": "loaded",
        "loaded_context_length": 8192,
        "max_context_length": 32768,
    },
    {
        "id": "lmstudio-community/granite-4.0-h-tiny",
        "object": "model",
        "type": "llm",
        "publisher": "lmstudio-community",
        "arch": "granitehybrid",
        "compatibility_type": "gguf",
        "quantization": "Q4_K_M",
        "state": "not-loaded",
        "max_context_length": 1048576,
        "capabilities": ["tool_use"],
    },
]


def transport(installed: list[dict[str, Any]] | None = None) -> httpx.MockTransport:
    """LM Studio's behaviour, including the part that looks like success."""
    models = INSTALLED if installed is None else installed

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v0/models":
            return httpx.Response(200, json={"object": "list", "data": models})
        if path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "model": "qwen2.5-coder-7b-instruct",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                },
            )
        # The trap: LM Studio answers 200 for anything it does not implement.
        return httpx.Response(
            200, json={"error": f"Unexpected endpoint or method. ({request.method} {path})"}
        )

    return httpx.MockTransport(handle)


def unreachable() -> httpx.MockTransport:
    """A closed LM Studio, which is the ordinary state of a laptop."""

    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    return httpx.MockTransport(handle)


def sse(frames: list[dict[str, Any]], done: bool = True, trailer: str = "") -> bytes:
    """Recorded server-sent events, in LM Studio's streaming shape.

    Built rather than pasted so a test can say what it is testing — a frame with
    usage, a frame with no content, a stream that stops without `[DONE]` — and
    have the body follow from that.
    """
    body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
    if done:
        body += "data: [DONE]\n\n"
    return (body + trailer).encode()


def delta(content: str = "", finish: str | None = None,
          usage: dict[str, Any] | None = None) -> dict[str, Any]:
    """One streaming frame."""
    frame: dict[str, Any] = {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "delta": {"content": content} if content else {},
                     "finish_reason": finish}],
    }
    if usage is not None:
        frame["usage"] = usage
    return frame


def streaming_transport(body: bytes) -> httpx.MockTransport:
    """A runtime that streams `body` for a completion and nothing else."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(200, content=body,
                                  headers={"content-type": "text/event-stream"})
        if request.url.path == "/api/v0/models":
            return httpx.Response(200, json={"object": "list", "data": INSTALLED})
        return httpx.Response(200, json={"error": "Unexpected endpoint or method."})

    return httpx.MockTransport(handle)
