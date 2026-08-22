"""Recorded upstream streams, one per scenario.

Recorded rather than generated, and byte-exact rather than pretty: these are
what a real OpenAI-compatible server actually emits, including the awkward parts
a tidy fixture would smooth away. The whole point of the suite is to notice when
RAVIS smooths something away, so the fixtures must not do it first.

Each fixture is a list of **chunks**, not frames. The split between them is
deliberately misaligned with frame boundaries in some cases, because TCP makes
no promise that a frame arrives whole and a proxy is free to re-chunk. A suite
that fed frame-aligned chunks would pass a proxy that drops partial lines.
"""

from __future__ import annotations

# ── Plain chat ───────────────────────────────────────────────────────────────
# The ordinary case, and the one whose failure is most visible to a user.
PLAIN_CHAT = [
    b'data: {"id":"c","object":"chat.completion.chunk","choices":[{"index":0,'
    b'"delta":{"role":"assistant"}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"content":"Hello"}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"content":", world"}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
    b"data: [DONE]\n\n",
]

# ── A single tool call, arguments fragmented mid-token ───────────────────────
# RAVIS.md §8.3's worked example. The arguments split inside a JSON string, so
# no individual frame parses as JSON on its own — reassembly is the only way to
# recover the path, and reassembling wrongly yields a plausible wrong filename
# rather than an error.
FRAGMENTED_TOOL_CALL = [
    b'data: {"choices":[{"index":0,"delta":{"role":"assistant"}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_123",'
    b'"type":"function","function":{"name":"edit_file","arguments":"{\\"pa"}}]}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
    b'"function":{"arguments":"th\\":\\"src/"}}]}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
    b'"function":{"arguments":"auth.ts\\"}"}}]}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n',
    b"data: [DONE]\n\n",
]

# ── Two tool calls interleaved by index ──────────────────────────────────────
# The case that breaks a proxy assuming one call at a time: fragments for index
# 0 and index 1 alternate, and only the index says which belongs to which.
PARALLEL_TOOL_CALLS = [
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_a",'
    b'"function":{"name":"read_file","arguments":"{\\"p"}}]}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"id":"call_b",'
    b'"function":{"name":"list_dir","arguments":"{\\"d"}}]}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
    b'"function":{"arguments":"ath\\":\\"a.ts\\"}"}}]}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":1,'
    b'"function":{"arguments":"ir\\":\\"src\\"}"}}]}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n',
    b"data: [DONE]\n\n",
]

# ── Reasoning kept out of content ────────────────────────────────────────────
# §8.5: a reasoning model's thinking arrives in `reasoning_content`, and merging
# it into `content` would put the model's private deliberation in front of the
# user as if it were the answer.
REASONING_STREAM = [
    b'data: {"choices":[{"index":0,"delta":{"reasoning_content":"The user wants "}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"reasoning_content":"a greeting."}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"content":"Hi."}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n',
    b"data: [DONE]\n\n",
]

# ── The same plain chat, chunked against the grain ───────────────────────────
# Identical bytes to PLAIN_CHAT, split mid-frame and mid-JSON. A reader that
# buffers incomplete lines sees no difference; one that assumes a chunk is a
# frame loses half the stream. RAVIS must preserve bytes without caring either
# way, which is what makes this a fair test of the proxy rather than the reader.
MISALIGNED_CHUNKS = [
    b'data: {"choices":[{"index":0,"delta":{"content":"Hel',
    b'lo"}}]}\n\ndata: {"choices":[{"index":0,"del',
    b'ta":{"content":", world"}}]}\n\ndata: {"choices":[{"index":0,'
    b'"delta":{},"finish_reason":"stop"}]}\n\ndata: ',
    b"[DONE]\n\n",
]

# ── A stream that stops without [DONE] ───────────────────────────────────────
# Not a RAVIS behaviour to imitate — a fixture proving the suite can *detect*
# the omission. A check that cannot fail is not a check (runbook §14.1).
TRUNCATED_STREAM = [
    b'data: {"choices":[{"index":0,"delta":{"content":"Hel"}}]}\n\n',
    b'data: {"choices":[{"index":0,"delta":{"content":"lo"}}]}\n\n',
]

ALL_FIXTURES = {
    "plain_chat": PLAIN_CHAT,
    "fragmented_tool_call": FRAGMENTED_TOOL_CALL,
    "parallel_tool_calls": PARALLEL_TOOL_CALLS,
    "reasoning_stream": REASONING_STREAM,
    "misaligned_chunks": MISALIGNED_CHUNKS,
}
