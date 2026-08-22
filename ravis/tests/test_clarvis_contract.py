"""The ported Clarvis reader must behave like the original.

`contract.py` is a port of two real files in the Clarvis extension. If it drifts,
the conformance suite still passes — it would simply be checking RAVIS against a
fiction. These tests pin the behaviours the original is explicit about.
"""

from __future__ import annotations

from ravis.compatibility.clarvis.contract import PartialCall, SseReader, absorb_tool_deltas


def test_a_frame_split_across_chunks_is_still_read() -> None:
    """The property that makes the suite fair to a re-chunking proxy."""
    reader = SseReader()

    first = reader.push(b'data: {"a":')
    second = reader.push(b'1}\n\n')

    assert first == []
    assert second == ['{"a":1}']


def test_carriage_returns_are_trimmed() -> None:
    """Providers differ on \\n versus \\r\\n; Clarvis trims the difference away."""
    assert SseReader().push(b'data: {"a":1}\r\n\r\n') == ['{"a":1}']


def test_comment_lines_are_skipped() -> None:
    """Keep-alive comments are not data, and parsing them as data is a crash."""
    assert SseReader().push(b': heartbeat\n\ndata: {"a":1}\n\n') == ['{"a":1}']


def test_arguments_accumulate_while_other_fields_fall_back() -> None:
    """The rule that makes fragmented tool calls reassemble correctly.

    A frame that omits the name and id must not erase them, and only the
    arguments concatenate. Getting this backwards produces a call with the right
    arguments and no name — which fails somewhere far from the cause.
    """
    pending: dict[int, PartialCall] = {}

    absorb_tool_deltas(pending, [{"index": 0, "id": "call_1",
                                  "function": {"name": "edit_file", "arguments": '{"pa'}}])
    absorb_tool_deltas(pending, [{"index": 0, "function": {"arguments": 'th":"a.ts"}'}}])

    assert pending[0].id == "call_1"
    assert pending[0].name == "edit_file"
    assert pending[0].arguments == '{"path":"a.ts"}'


def test_a_missing_index_is_treated_as_zero() -> None:
    """Clarvis reads `delta.index ?? 0`; a provider that omits it means the first call."""
    pending: dict[int, PartialCall] = {}

    absorb_tool_deltas(pending, [{"function": {"arguments": "{}"}}])

    assert pending[0].arguments == "{}"


def test_calls_at_different_indexes_stay_separate() -> None:
    """Interleaved parallel calls are addressed only by index."""
    pending: dict[int, PartialCall] = {}

    absorb_tool_deltas(pending, [{"index": 0, "function": {"arguments": "a"}}])
    absorb_tool_deltas(pending, [{"index": 1, "function": {"arguments": "x"}}])
    absorb_tool_deltas(pending, [{"index": 0, "function": {"arguments": "b"}}])

    assert pending[0].arguments == "ab"
    assert pending[1].arguments == "x"
