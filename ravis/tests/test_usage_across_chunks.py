"""A usage frame split across two HTTP chunks, which used to vanish entirely.

HTTP chunking has nothing to do with SSE framing. A provider — or anything
between it and RAVIS — is free to end a chunk in the middle of a frame, and
`data: {"choices":[],"usage":{"prompt_toke` / `ns":91,...}}` is a perfectly
ordinary way for a usage frame to arrive.

`_usage_in` was called on each chunk alone. Neither half contains the complete
`"usage"` substring in a parseable line, so both were skipped, and the call was
recorded with no token counts at all — priced `UNKNOWN`, which §14 is explicit
a budget reads as *nothing spent*. Nothing announced it. The only symptom was
RAVIS's spend figures sitting below the provider's own, which is exactly the
question the operator had just asked about their DeepSeek bill.

The loop now inspects a carry-over of the bytes after the last complete frame.
The chunk itself is still yielded whole and unaltered, so §6's byte-for-byte
guarantee on the transparent path is untouched: this looks, it never rewrites.
"""

from __future__ import annotations

from typing import Any

import pytest

from ravis.api.openai.chat import USAGE_CARRY_BYTES, _carry_over, _usage_in

WHOLE = (
    b'data: {"choices":[],"usage":{"prompt_tokens":91,"completion_tokens":7,'
    b'"total_tokens":98}}\n\n'
)


def _split_at(frame: bytes, index: int) -> tuple[bytes, bytes]:
    return frame[:index], frame[index:]


def _read_across(chunks: list[bytes]) -> Any:
    """The loop's reading logic, exactly as `_stream_transparent` runs it."""
    carried = b""
    reported = None
    for chunk in chunks:
        combined = carried + chunk
        seen = _usage_in(combined)
        if seen is not None:
            reported = seen
        carried = _carry_over(combined)
    return reported


def test_a_frame_split_mid_word_is_still_read() -> None:
    """The reported case: the boundary lands inside the `"usage"` key itself, so
    neither half can pass the substring test that guards the parse."""
    head, tail = _split_at(WHOLE, WHOLE.index(b'"usage"') + 4)

    assert _usage_in(head) is None, "the fixture is wrong — the halves must each miss"
    assert _usage_in(tail) is None

    counted = _read_across([head, tail])

    assert counted is not None, "a usage frame split across chunks was lost entirely"
    assert counted.input_tokens == 91
    assert counted.output_tokens == 7


@pytest.mark.parametrize("cut", range(6, len(WHOLE), 7))
def test_it_survives_a_boundary_anywhere_in_the_frame(cut: int) -> None:
    """Swept rather than sampled. The defect is a boundary landing in one
    particular place, and a single chosen split proves only that one."""
    head, tail = _split_at(WHOLE, cut)

    counted = _read_across([head, tail])

    assert counted is not None and counted.input_tokens == 91, (
        f"a split at byte {cut} lost the usage"
    )


def test_a_frame_split_three_ways_is_still_read() -> None:
    """Nothing says a provider splits a frame only once."""
    a, b, c = WHOLE[:30], WHOLE[30:60], WHOLE[60:]

    counted = _read_across([a, b, c])

    assert counted is not None and counted.input_tokens == 91


def test_a_complete_frame_is_not_read_twice() -> None:
    """**The carry-over must forget what it has already offered.** Keeping whole
    frames would re-report an older reading after a newer one, which inverts
    "latest wins" and understates a call that grew."""
    first = b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":1}}\n\n'
    second = b'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":48}}\n\n'

    counted = _read_across([first, second])

    assert counted is not None
    assert counted.output_tokens == 48, "an earlier frame was re-read after a later one"


def test_the_carry_over_keeps_only_an_unfinished_frame() -> None:
    """What bounds this buffer. Everything before the last frame terminator has
    been offered in full and is dropped, so it holds one partial frame rather
    than the whole stream."""
    assert _carry_over(b"data: one\n\ndata: two\n\ndata: par") == b"data: par"
    assert _carry_over(b"data: one\n\n") == b""


def test_a_provider_that_never_terminates_a_frame_cannot_grow_it() -> None:
    """The deliberate trade: a fragment that outgrows the cap loses its usage
    figure rather than holding an unbounded buffer for a stream that will never
    close one."""
    endless = b"x" * (USAGE_CARRY_BYTES * 3)

    assert len(_carry_over(endless)) == USAGE_CARRY_BYTES


def test_a_content_delta_still_costs_nothing_to_reject() -> None:
    """The half that runs on hundreds of chunks per call. Carrying bytes must
    not turn the cheap substring rejection into a JSON parse."""
    assert _usage_in(b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n') is None


def test_the_real_stream_records_usage_from_a_split_frame() -> None:
    """**The loop, not a copy of it.**

    Every test above drives `_read_across`, which reimplements the three lines
    inside `_stream_transparent`. That is enough to pin the reading logic and
    not enough to notice if the loop stops using it — the same shape as the
    price-book bug found the day before, where a correct function had no
    callers. So this one sends genuinely split chunks through the application
    and reads what reached the ledger.
    """
    from ravis.cost import Price
    from tests.test_fallback import ScriptedUpstream, _app_with

    head, tail = _split_at(WHOLE, WHOLE.index(b'"usage"') + 4)
    upstream = ScriptedUpstream(
        {"talker": {"context_window": "32000"}},
        frames=[b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n', head, tail,
                b"data: [DONE]\n\n"],
    )

    with _app_with(upstream) as client:
        client.app.app.state.prices.state(
            "talker", Price(input_per_million=1.0, output_per_million=1.0)
        )
        reply = client.post(
            "/v1/chat/completions",
            json={"model": "talker", "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]},
        )
        assert reply.status_code == 200
        assert b'"usage"' in reply.content, "the client must still receive every byte"
        records = client.app.app.state.usage_ledger.recent(50)

    priced = [r for r in records if r.model == "talker"]
    assert priced, "the streamed call left no usage record at all"
    counted = priced[-1].usage
    assert counted is not None and counted.input_tokens == 91, (
        "the split usage frame never reached the ledger — this call is priced "
        "UNKNOWN, which a budget reads as nothing spent"
    )
