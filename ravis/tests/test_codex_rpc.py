"""The JSON-RPC conversation with Codex, and its I/O discipline (design §4.4, review AM3).

One Codex process serves every project, so the connection must never let one slow part freeze the
rest: requests carry their own deadlines, because Codex answers a malformed line with nothing
(brief §1); a line that can't be read is dropped and counted; a handler's bug doesn't end the
reader; overload is retried briefly; and a write that stays blocked marks the connection hung.

These tests drive `Connection` through an in-memory pipe pair, `ScriptedCodex`: what RAVIS writes
is parsed and answered by a small script, as lines on the connection's output.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from ravis.codex.rpc import (
    OVERLOADED,
    CodexRpcError,
    CodexTimeoutError,
    CodexUnavailableError,
    Connection,
    RpcTimings,
)

QUICK = RpcTimings(write_seconds=0.05, hung_after_seconds=0.2, overload_backoff_seconds=(0.01,) * 3)
Answer = Callable[[dict[str, Any]], list[dict[str, Any]]]


class ScriptedCodex:
    """Codex's two pipes in memory: each line RAVIS writes is answered by `answer`."""

    def __init__(self, answer: Answer, *, limit: int = 2**16) -> None:
        self.output = asyncio.StreamReader(limit=limit)
        self.sent: list[dict[str, Any]] = []
        self.answer = answer
        self.drain_blocks = False
        self.input_closed = False

    def write(self, data: bytes) -> None:
        for line in data.decode().splitlines():
            message = json.loads(line)
            self.sent.append(message)
            for reply in self.answer(message):
                self.say(reply)

    def say(self, message: dict[str, Any]) -> None:
        self.output.feed_data((json.dumps(message) + "\n").encode())

    async def drain(self) -> None:
        if self.drain_blocks:
            await asyncio.Event().wait()

    def close(self) -> None:
        self.input_closed = True


def echo(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Answer each request with its own method; notifications and answers get nothing back."""
    if "id" not in message or "method" not in message:
        return []
    return [{"id": message["id"], "result": {"method": message["method"]}}]


def connect(
    codex: ScriptedCodex,
    *,
    notifications: list[tuple[str, dict[str, Any]]] | None = None,
    on_notification: Callable[[str, dict[str, Any]], None] | None = None,
) -> Connection:
    seen = notifications if notifications is not None else []
    connection = Connection(
        codex.output,
        codex,  # type: ignore[arg-type]
        on_notification=on_notification or (lambda method, params: seen.append((method, params))),
        on_request=lambda conn, request_id, _method, _params: conn.respond(request_id, {"ok": 1}),
        timings=QUICK,
    )
    connection.start()
    return connection


async def test_a_request_is_answered_and_leaves_out_params_it_has_none_of() -> None:
    codex = ScriptedCodex(echo)
    connection = connect(codex)

    answered = await connection.request("account/logout", timeout=1)
    await connection.request("model/list", {"limit": 1}, timeout=1)

    assert answered == {"method": "account/logout"}
    assert codex.sent[0] == {"id": 1, "method": "account/logout"}
    assert codex.sent[1] == {"id": 2, "method": "model/list", "params": {"limit": 1}}
    assert connection.pending_requests == 0
    await connection.stop("done")


async def test_a_refusal_names_the_method_and_keeps_codexs_message() -> None:
    def refuse(message: dict[str, Any]) -> list[dict[str, Any]]:
        text = "Invalid request: unknown variant `x`, expected one of …"
        return [{"id": message["id"], "error": {"code": -32600, "message": text}}]

    connection = connect(ScriptedCodex(refuse))

    with pytest.raises(CodexRpcError) as refused:
        await connection.request("thread/start", {}, timeout=1)

    assert refused.value.method == "thread/start"
    assert refused.value.is_drift
    await connection.stop("done")


async def test_an_unanswered_request_times_out_and_nothing_is_left_waiting() -> None:
    connection = connect(ScriptedCodex(lambda _message: []))

    with pytest.raises(CodexTimeoutError):
        await connection.request("account/read", {}, timeout=0.05)

    assert connection.pending_requests == 0
    await connection.stop("done")


async def test_unreadable_lines_are_counted_and_the_conversation_carries_on() -> None:
    codex = ScriptedCodex(echo, limit=256)
    connection = connect(codex)

    codex.output.feed_data(b"{not json\n")
    codex.output.feed_data(b"[1, 2, 3]\n")
    codex.output.feed_data(b'{"x": "' + b"y" * 1000 + b'"}\n')
    answered = await connection.request("account/read", {}, timeout=1)

    assert answered == {"method": "account/read"}
    assert connection.unreadable_lines >= 3
    await connection.stop("done")


async def test_overload_is_retried_briefly_and_then_answered() -> None:
    attempts: list[int] = []

    def busy_twice(message: dict[str, Any]) -> list[dict[str, Any]]:
        attempts.append(message["id"])
        if len(attempts) <= 2:
            error = {"code": OVERLOADED, "message": "Server overloaded; retry later."}
            return [{"id": message["id"], "error": error}]
        return echo(message)

    connection = connect(ScriptedCodex(busy_twice))

    answered = await connection.request("model/list", {}, timeout=1)

    assert answered == {"method": "model/list"}
    assert len(attempts) == 3
    await connection.stop("done")


async def test_overload_that_never_clears_fails_after_the_last_retry() -> None:
    def always_busy(message: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"id": message["id"], "error": {"code": OVERLOADED, "message": "busy"}}]

    codex = ScriptedCodex(always_busy)
    connection = connect(codex)

    with pytest.raises(CodexRpcError) as refused:
        await connection.request("model/list", {}, timeout=1)

    assert refused.value.is_overload
    assert len(codex.sent) == len(QUICK.overload_backoff_seconds) + 1
    await connection.stop("done")


async def test_the_end_of_codexs_output_fails_every_waiting_request() -> None:
    codex = ScriptedCodex(lambda _message: [])
    connection = connect(codex)

    waiting = asyncio.create_task(connection.request("account/read", {}, timeout=5))
    await asyncio.sleep(0.01)
    codex.output.feed_eof()

    with pytest.raises(CodexUnavailableError, match="output ended"):
        await waiting
    assert connection.closed.is_set()
    with pytest.raises(CodexUnavailableError):
        await connection.request("account/read", {}, timeout=1)
    await connection.stop("done")


async def test_a_write_blocked_past_its_limit_marks_the_connection_hung() -> None:
    """Design §4.4: a write blocked for 10 s triggers the hang path, and never blocks the caller."""
    codex = ScriptedCodex(lambda _message: [])
    codex.drain_blocks = True
    connection = connect(codex)

    with pytest.raises(CodexUnavailableError, match="stopped reading"):
        await connection.request("account/read", {}, timeout=5)

    assert connection.hung
    await connection.stop("done")


async def test_notifications_and_codexs_requests_reach_their_handlers() -> None:
    codex = ScriptedCodex(echo)
    seen: list[tuple[str, dict[str, Any]]] = []
    connection = connect(codex, notifications=seen)

    codex.say({"method": "account/updated", "params": {"authMode": None}, "emittedAtMs": 1})
    codex.say({"id": "srv-1", "method": "currentTime/read", "params": {"threadId": "t"}})
    await connection.request("account/read", {}, timeout=1)
    # The answer to Codex's request is queued behind RAVIS's own, so give the writer a moment.
    for _ in range(100):
        if {"id": "srv-1", "result": {"ok": 1}} in codex.sent:
            break
        await asyncio.sleep(0.01)

    assert seen == [("account/updated", {"authMode": None})]
    assert {"id": "srv-1", "result": {"ok": 1}} in codex.sent
    await connection.stop("done")


async def test_stop_returns_even_when_the_writer_is_cancelled_mid_flush() -> None:
    """A cancel landing as a flush finishes must still end the writer (Python 3.11 `wait_for`)."""
    codex = ScriptedCodex(echo)
    connection = connect(codex)

    for attempt in range(20):
        await connection.request("model/list", {"attempt": attempt}, timeout=1)
    await asyncio.wait_for(connection.stop("done"), 2)

    assert connection.closed.is_set()


async def test_a_handler_that_fails_does_not_end_the_reader() -> None:
    codex = ScriptedCodex(echo)

    def broken(_method: str, _params: dict[str, Any]) -> None:
        raise RuntimeError("a bug in a handler")

    connection = connect(codex, on_notification=broken)

    codex.say({"method": "account/updated", "params": {}})
    answered = await connection.request("account/read", {}, timeout=1)

    assert answered == {"method": "account/read"}
    await connection.stop("done")
