"""The JSON-RPC conversation with one Codex app-server, over its standard input and output.

Codex's app-server speaks newline-delimited JSON-RPC 2.0 on stdio (protocol brief §1): one message
per line, `"jsonrpc"` left out, a request is `{id, method, params}`, an answer `{id, result}` or
`{id, error}`, and a notification `{method, params}`. Codex also *sends* requests — approvals,
questions, `currentTime/read` — which carry an id and wait for RAVIS's answer.

**Why it is built this way: the I/O discipline** (design §4.4, review AM3). One Codex process
serves every project on this Mac, so one slow part must never freeze the rest:

- **One reader task** reads Codex's output, parses each line and hands it on. The handlers it calls
  only put the message somewhere and return; nothing on the read path waits for a database, a
  browser or another program. A reader that waited could fill Codex's output pipe, and Codex would
  then stall writing to every project at once.
- **One writer task** owns Codex's input, fed by a bounded queue of 256 lines. Each write gets five
  seconds, and a write still blocked after ten means Codex has stopped reading: the connection
  marks itself `hung`, and the supervisor treats that as a crash (`supervisor.py`).
- **Every request has its own deadline.** Codex answers a malformed line with nothing at all
  (brief §1, observed), so a request left to wait for an answer could wait forever.
- **Overload is retried, briefly.** A full request intake answers `-32001`; the request is sent
  again after 0.5, 1 and 2 seconds, then fails (design §4.4).

Nothing here decides what a message means: `routing.py` does that for Codex's notifications and
requests, and each caller decides what an answer means.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("ravis")

#: A JSON-RPC request id: Codex's own ids are integers or strings (brief §1).
RequestId = int | str
#: Called with (method, params) for every notification Codex sends.
NotificationHandler = Callable[[str, dict[str, Any]], None]
#: Called with (the connection, request id, method, params) for every request Codex sends.
RequestHandler = Callable[["Connection", RequestId, str, dict[str, Any]], None]

#: The longest line RAVIS accepts from Codex. Codex's messages carry whole diffs and command
#: output, so asyncio's 64 KiB default is too small; a line longer than this is dropped and
#: counted, and the request it answered times out like any unanswered one.
LINE_LIMIT_BYTES = 32 * 1024 * 1024

#: The longest `stop()` waits for the reader and writer to end once cancelled.
STOP_WAIT_SECONDS = 1.0

#: The JSON-RPC error codes RAVIS answers with or reads (brief §8, design §4.4).
METHOD_NOT_FOUND = -32601
OVERLOADED = -32001
INVALID_REQUEST = -32600
#: The start of the two `-32600` messages that mean Codex no longer speaks the protocol RAVIS
#: was built against: the drift alarm (design §4.3).
DRIFT_MESSAGES = ("Invalid request: unknown variant", "Invalid request: invalid type")


@dataclass(frozen=True)
class RpcTimings:
    """How long the connection waits for Codex's input pipe, and how overload is retried."""

    #: One attempt at flushing a line into Codex's input.
    write_seconds: float = 5.0
    #: A write blocked this long means Codex has stopped reading its input.
    hung_after_seconds: float = 10.0
    #: The pauses before each retry of a request Codex answered `-32001`.
    overload_backoff_seconds: tuple[float, ...] = (0.5, 1.0, 2.0)


class CodexRpcError(Exception):
    """Codex answered a request with a JSON-RPC error.

    Most client mistakes arrive as `-32600` and differ only in their message (brief §8), so the
    message is kept whole. It is Codex's own wording about RAVIS's request, never content.
    """

    def __init__(self, method: str, code: int, message: str) -> None:
        super().__init__(f"Codex refused {method}: {message} ({code})")
        self.method = method
        self.code = code
        self.message = message

    @property
    def is_drift(self) -> bool:
        """Whether this answer says the protocol changed under RAVIS (design §4.3)."""
        return self.code == INVALID_REQUEST and self.message.startswith(DRIFT_MESSAGES)

    @property
    def is_overload(self) -> bool:
        """Whether Codex's request intake was full, which clears on its own."""
        return self.code == OVERLOADED


class CodexUnavailableError(Exception):
    """No answer can come: the process is gone, its input is closed, or its queue is full."""


class CodexTimeoutError(CodexUnavailableError):
    """Codex did not answer within the request's deadline."""


class _HungError(Exception):
    """A write stayed blocked past `hung_after_seconds`."""


class Connection:
    """One conversation with one Codex process: requests out, answers and Codex's messages in.

    Built around the process's two pipes and started with `start()`. When it closes — Codex's
    output ended, its input broke, or a write hung — every waiting request fails with
    `CodexUnavailableError`, and `closed` is set so the supervisor notices.
    """

    def __init__(
        self,
        output: asyncio.StreamReader,
        input_pipe: asyncio.StreamWriter,
        *,
        on_notification: NotificationHandler,
        on_request: RequestHandler,
        timings: RpcTimings = RpcTimings(),
        clock: Callable[[], float] = time.monotonic,
        queue_size: int = 256,
    ) -> None:
        self._output = output
        self._input = input_pipe
        self._on_notification = on_notification
        self._on_request = on_request
        self._timings = timings
        self._clock = clock
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=queue_size)
        # Each waiting request's method, kept so a refusal can say what Codex refused.
        self._pending: dict[RequestId, tuple[str, asyncio.Future[Any]]] = {}
        self._ids = itertools.count(1)
        self._tasks: list[asyncio.Task[None]] = []
        # When the line being written started waiting on the pipe; None while nothing waits.
        self._write_started: float | None = None
        self.closed = asyncio.Event()
        self.close_reason = ""
        self.hung = False
        #: Lines that were not a JSON object, or were too long to read. Counted, never logged:
        #: a line of Codex's output can carry a task's content.
        self.unreadable_lines = 0

    def start(self) -> None:
        """Start the reader and writer tasks."""
        self._tasks = [
            asyncio.create_task(self._read(), name="codex-reader"),
            asyncio.create_task(self._write(), name="codex-writer"),
        ]

    @property
    def pending_requests(self) -> int:
        """How many of RAVIS's requests are waiting for an answer."""
        return len(self._pending)

    def writer_blocked_seconds(self) -> float:
        """How long the line being written has waited on Codex's input pipe; 0 when none waits."""
        started = self._write_started
        return 0.0 if started is None else self._clock() - started

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float
    ) -> Any:
        """Send one request and return Codex's result, retrying briefly while Codex is overloaded.

        Raises `CodexRpcError` for Codex's refusal, `CodexTimeoutError` when no answer came in
        time, and `CodexUnavailableError` when none can come.
        """
        for pause in self._timings.overload_backoff_seconds:
            try:
                return await self._request_once(method, params, timeout)
            except CodexRpcError as refusal:
                if not refusal.is_overload:
                    raise
            await asyncio.sleep(pause)
        return await self._request_once(method, params, timeout)

    async def _request_once(
        self, method: str, params: dict[str, Any] | None, timeout: float
    ) -> Any:
        if self.closed.is_set():
            raise CodexUnavailableError(self.close_reason)
        request_id = next(self._ids)
        answer: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (method, answer)
        try:
            self._enqueue(_message(request_id, method, params))
            # `asyncio.timeout`, never `wait_for`: on Python 3.11 `wait_for` swallows a
            # cancellation that arrives as its inner wait finishes, and a task that misses its
            # cancel never stops — which is how `stop()` once waited forever on the writer.
            async with asyncio.timeout(timeout):
                return await answer
        except TimeoutError:
            raise CodexTimeoutError(
                f"Codex did not answer {method} within {timeout:g} s"
            ) from None
        finally:
            self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a notification, which Codex does not answer."""
        self._send_quietly(_message(None, method, params))

    def respond(self, request_id: RequestId, result: dict[str, Any]) -> None:
        """Answer one of Codex's requests."""
        self._send_quietly({"id": request_id, "result": result})

    def refuse(self, request_id: RequestId, code: int, message: str) -> None:
        """Answer one of Codex's requests with a JSON-RPC error."""
        self._send_quietly({"id": request_id, "error": {"code": code, "message": message}})

    def close_input(self) -> None:
        """Close Codex's input. An idle Codex exits within about 10 ms of this (brief §1)."""
        with contextlib.suppress(OSError, RuntimeError):
            self._input.close()

    async def stop(self, reason: str) -> None:
        """End the reader and writer and fail every waiting request. Safe to call twice.

        The wait for the two tasks is bounded, so a task that somehow outlives its cancel can
        never hold RAVIS's shutdown past the launcher's patience.
        """
        self._close(reason)
        for task in self._tasks:
            task.cancel()
        if not self._tasks:
            return
        ended, _ = await asyncio.wait(self._tasks, timeout=STOP_WAIT_SECONDS)
        for task in ended:
            if not task.cancelled():
                task.exception()  # retrieved, so a writer that failed isn't reported twice

    def _send_quietly(self, message: dict[str, Any]) -> None:
        """Queue a line nobody waits on; a closed or full connection only loses it."""
        try:
            self._enqueue(message)
        except CodexUnavailableError as failure:
            logger.warning("codex: a message to Codex was not sent: %s", failure)

    def _enqueue(self, message: dict[str, Any]) -> None:
        if self.closed.is_set():
            raise CodexUnavailableError(self.close_reason)
        line = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            self._queue.put_nowait(line)
        except asyncio.QueueFull:
            raise CodexUnavailableError("Codex's input queue is full") from None

    async def _read(self) -> None:
        """Read Codex's output until it ends, handing each message on without waiting."""
        while True:
            try:
                line = await self._output.readline()
            except ValueError:
                # Longer than LINE_LIMIT_BYTES: asyncio has dropped it. Keep reading.
                self.unreadable_lines += 1
                continue
            if not line:
                self._close("Codex's output ended")
                return
            self._dispatch(line)

    def _dispatch(self, line: bytes) -> None:
        try:
            message = json.loads(line)
        except ValueError:
            self.unreadable_lines += 1
            return
        if not isinstance(message, dict):
            self.unreadable_lines += 1
            return
        method = message.get("method")
        if isinstance(method, str):
            self._hand_on(message, method)
        else:
            self._settle(message)

    def _hand_on(self, message: dict[str, Any], method: str) -> None:
        """Pass a notification or one of Codex's requests to its handler, which must not wait."""
        raw_params = message.get("params")
        params = raw_params if isinstance(raw_params, dict) else {}
        try:
            if "id" in message:
                self._on_request(self, message["id"], method, params)
            else:
                self._on_notification(method, params)
        except Exception:  # noqa: BLE001 — a handler's bug must not end the conversation
            logger.exception("codex: handling %s failed", method)

    def _settle(self, message: dict[str, Any]) -> None:
        """Resolve the request an answer belongs to; an answer nobody waits for is dropped."""
        request_id = message.get("id")
        waiting = self._pending.get(request_id) if isinstance(request_id, int | str) else None
        if waiting is None or waiting[1].done():
            return
        method, answer = waiting
        error = message.get("error")
        if isinstance(error, dict):
            code, text = error.get("code"), error.get("message")
            answer.set_exception(
                CodexRpcError(method, code if isinstance(code, int) else 0, str(text or ""))
            )
        else:
            answer.set_result(message.get("result"))

    async def _write(self) -> None:
        """Write queued lines into Codex's input, one at a time, each within its deadline."""
        try:
            while True:
                line = await self._queue.get()
                self._write_started = self._clock()
                self._input.write(line)
                await self._drain()
                self._write_started = None
        except _HungError:
            self.hung = True
            self._close("Codex stopped reading its input")
        except (OSError, RuntimeError) as failure:
            self._close(f"Codex's input closed: {failure}")

    async def _drain(self) -> None:
        """Wait for the line to leave the pipe, giving up once it has waited `hung_after`."""
        while True:
            try:
                async with asyncio.timeout(self._timings.write_seconds):
                    await self._input.drain()
                return
            except TimeoutError:
                if self.writer_blocked_seconds() >= self._timings.hung_after_seconds:
                    raise _HungError from None

    def _close(self, reason: str) -> None:
        if self.closed.is_set():
            return
        self.close_reason = reason
        self.closed.set()
        for _, answer in self._pending.values():
            if not answer.done():
                answer.set_exception(CodexUnavailableError(reason))


def _message(
    request_id: RequestId | None, method: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """A request (with an id) or a notification (without), leaving out params when there are none.

    The probe sent `account/logout` and `account/rateLimits/read` with no params at all, and Codex
    accepted both (brief §10), so RAVIS does the same rather than inventing an empty object.
    """
    message: dict[str, Any] = {} if request_id is None else {"id": request_id}
    message["method"] = method
    if params is not None:
        message["params"] = params
    return message
