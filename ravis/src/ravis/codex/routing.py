"""Where each message Codex sends goes, decided on the reader's path without waiting.

One Codex process hosts every thread on this Mac (design §2.1), so its output is a mix of every
thread's events, account news and Codex's own requests. The reader task (`rpc.py`) calls in here
for each one, and everything here only *places* a message — in RAVIS's account queue, in one
thread's inbox, or straight back to Codex as a fixed answer — and returns. Nothing waits
(design §4.4, review AM3).

**Routing rules** (design §4.4):
- `account/*` and `modelProvider/authRecovery*` notifications go to the Codex service's account
  queue (`service.py`), which reads the account, the allowance and the models from them.
- A thread's notifications and requests go to that thread's inbox, if RAVIS opened one for it. In
  this increment the only thread-holder is the file-rules re-test (`reprove.py`); the agent
  sessions of M29's third increment register their threads the same way.
- **A request for a thread nobody holds is refused** with `-32601`, and so are the requests RAVIS
  never answers: dynamic tool calls, token refreshes it does not own, attestation and the legacy
  v1 approvals. Refusing an approval is a denial to Codex — the agent carries on as if declined
  (brief §7) — so nothing runs on a refusal.
- `currentTime/read` is answered with the time, and an MCP elicitation is declined by policy
  (review AL2).

**Turns are counted here too**, because several rules hang on "is a turn active": polling the
allowance pauses, sign-in and sign-out are refused, and a changed binary waits for idle.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ravis.codex.rpc import METHOD_NOT_FOUND, Connection, RequestId

#: The notification prefixes that belong to the account rather than to any thread.
ACCOUNT_NOTIFICATIONS = ("account/", "modelProvider/authRecovery")
#: Codex's requests RAVIS refuses wherever they come from (design §4.4).
REFUSED_REQUESTS = frozenset({
    "item/tool/call",
    "account/chatgptAuthTokens/refresh",
    "attestation/generate",
    "applyPatchApproval",
    "execCommandApproval",
})
#: How many messages one thread's inbox holds before it starts dropping (design §4.4).
INBOX_CAPACITY = 1000
#: Put in a thread's inbox after RAVIS declined an MCP elicitation for it: RAVIS's own word, never
#: one of Codex's methods, so an agent session can say `request.resolved {by: policy_elicitation}`.
ELICITATION_DECLINED = "ravis/elicitationDeclined"


@dataclass(frozen=True)
class InboxItem:
    """One message for a thread-holder: a notification, or a request to answer on `connection`."""

    method: str
    params: dict[str, Any]
    #: The request's id when Codex waits for an answer; None for a notification.
    request_id: RequestId | None = None
    #: The connection the request came on. An answer goes back on it, and on no other: a request
    #: from a Codex process that has since restarted has nobody left to hear the answer.
    connection: Connection | None = None


class Inbox:
    """A bounded queue of one holder's thread messages, filled by the reader without waiting.

    When it is full, streamed deltas are dropped first — each is a fragment of text a later
    completed item repeats whole — and only then the incoming message, with `overflowed` set so
    the holder knows its view of the thread is incomplete (design §4.4).
    """

    def __init__(self, capacity: int = INBOX_CAPACITY) -> None:
        self._items: deque[InboxItem] = deque()
        self._capacity = capacity
        self._arrived = asyncio.Event()
        self.overflowed = False

    def put(self, item: InboxItem) -> None:
        if len(self._items) >= self._capacity and not self._drop_a_delta():
            self.overflowed = True
            return
        self._items.append(item)
        self._arrived.set()

    async def get(self) -> InboxItem:
        while not self._items:
            self._arrived.clear()
            await self._arrived.wait()
        return self._items.popleft()

    def _drop_a_delta(self) -> bool:
        for index, queued in enumerate(self._items):
            if queued.request_id is None and queued.method.endswith(("Delta", "/delta")):
                del self._items[index]
                return True
        return False


class ActiveTurns:
    """Which threads have a turn in progress, from Codex's own `turn/started` and `turn/completed`.

    `changed` is set whenever the count moves, so a waiting loop wakes; `last_ended_at` is when the
    count last fell to zero, which starts the allowance read 30 seconds later (design §3.3).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._threads: set[str] = set()
        self._clock = clock
        self.changed = asyncio.Event()
        self.last_ended_at: float | None = None

    @property
    def count(self) -> int:
        return len(self._threads)

    def observe(self, method: str, params: dict[str, Any]) -> None:
        thread = params.get("threadId")
        if not isinstance(thread, str):
            return
        if method == "turn/started":
            self._threads.add(thread)
            self.changed.set()
        elif method == "turn/completed" and thread in self._threads:
            self._threads.discard(thread)
            if not self._threads:
                self.last_ended_at = self._clock()
            self.changed.set()

    def forget_all(self) -> None:
        """The process ended: no turn of it can still be running."""
        if self._threads:
            self._threads.clear()
            self.last_ended_at = self._clock()
            self.changed.set()


class MessageRouter:
    """The routing rules above, as the two handlers a `Connection` calls."""

    def __init__(
        self,
        on_account: Callable[[str, dict[str, Any]], None],
        turns: ActiveTurns,
        *,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._on_account = on_account
        self._turns = turns
        self._wall_clock = wall_clock
        self._inboxes: dict[str, Inbox] = {}

    def hold(self, thread_id: str, inbox: Inbox) -> None:
        """Deliver this thread's messages to `inbox` from now on."""
        self._inboxes[thread_id] = inbox

    def release(self, thread_id: str) -> None:
        self._inboxes.pop(thread_id, None)

    def notification(self, method: str, params: dict[str, Any]) -> None:
        self._turns.observe(method, params)
        if method.startswith(ACCOUNT_NOTIFICATIONS):
            self._on_account(method, params)
            return
        inbox = self._inbox_for(params)
        if inbox is not None:
            inbox.put(InboxItem(method, params))
        # Also for the account: a turn that failed on the plan's usage limit is the moment to
        # read the allowance again (design §9), so the service sees every turn's end.
        if method == "turn/completed":
            self._on_account(method, params)

    def request(
        self, connection: Connection, request_id: RequestId, method: str, params: dict[str, Any]
    ) -> None:
        if method == "currentTime/read":
            # "Current time as whole Unix seconds" (CurrentTimeReadResponse).
            connection.respond(request_id, {"currentTimeAt": int(self._wall_clock())})
            return
        if method == "mcpServer/elicitation/request":
            connection.respond(request_id, {"action": "decline"})
            # A task's windows learn it was declined by policy (design §4.4, review AL2).
            declined = self._inbox_for(params)
            if declined is not None:
                declined.put(InboxItem(ELICITATION_DECLINED, params))
            return
        inbox = None if method in REFUSED_REQUESTS else self._inbox_for(params)
        if inbox is None:
            connection.refuse(
                request_id, METHOD_NOT_FOUND, f"RAVIS does not answer {method} here"
            )
            return
        inbox.put(InboxItem(method, params, request_id, connection))

    def _inbox_for(self, params: dict[str, Any]) -> Inbox | None:
        thread = params.get("threadId")
        return self._inboxes.get(thread) if isinstance(thread, str) else None
