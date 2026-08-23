"""Streaming generation — the half of M6 that lives in the adapter (§11.4).

M2's `generate` is a round trip and stays one. This exists because
time-to-first-token cannot be recovered from a response that arrives whole, and
because LM Studio's most dangerous behaviour — answering 200 with an error body
— looks different in a stream than it does in a round trip: it does not arrive
as SSE at all, so a parser that skipped every non-`data:` line would report a
refusal as a model that produced nothing.
"""

from __future__ import annotations

import httpx
import pytest
from tests.conftest_lmstudio import delta, sse, streaming_transport

from sirvis.runtimes import GenerationChunk, LMStudioAdapter, RuntimeUnavailableError


def _adapter(body: bytes) -> LMStudioAdapter:
    return LMStudioAdapter(
        "http://runtime.invalid",
        client=httpx.AsyncClient(transport=streaming_transport(body)),
    )


async def _collect(adapter: LMStudioAdapter) -> list[GenerationChunk]:
    messages = [{"role": "user", "content": "hi"}]
    return [chunk async for chunk in adapter.stream_generate("m", messages)]


async def test_content_arrives_chunk_by_chunk_so_the_first_can_be_timed() -> None:
    chunks = await _collect(_adapter(sse([delta("Hel"), delta("lo"), delta(finish="stop")])))

    assert "".join(chunk.content for chunk in chunks) == "Hello"
    assert chunks[-1].finish_reason == "stop"


async def test_usage_is_carried_when_the_runtime_reports_it() -> None:
    """`stream_options.include_usage` is what separates a counted token from an
    estimated one, and the engine downgrades provenance without it."""
    body = sse([delta("hi"), delta(usage={"prompt_tokens": 11, "completion_tokens": 2})])

    chunks = await _collect(_adapter(body))

    assert chunks[-1].usage == {"prompt_tokens": 11, "completion_tokens": 2}


async def test_an_error_object_is_a_failure_rather_than_an_empty_completion() -> None:
    """LM Studio's 200-with-an-error-body trap, in its streaming disguise. A
    parser ignoring non-SSE lines would return zero chunks and the caller would
    record a model that said nothing."""
    body = b'{"error": "Model unloaded or unavailable"}'

    with pytest.raises(RuntimeUnavailableError, match="Model unloaded"):
        await _collect(_adapter(body))


async def test_an_error_inside_a_data_frame_is_also_a_failure() -> None:
    body = sse([{"error": "context length exceeded"}], done=False)

    with pytest.raises(RuntimeUnavailableError, match="context length exceeded"):
        await _collect(_adapter(body))


async def test_a_malformed_frame_is_named_rather_than_skipped() -> None:
    with pytest.raises(RuntimeUnavailableError, match="malformed stream frame"):
        await _collect(_adapter(b"data: {not json}\n\n"))


async def test_an_empty_body_says_so_instead_of_succeeding_silently() -> None:
    with pytest.raises(RuntimeUnavailableError, match="no data frames"):
        await _collect(_adapter(b""))
