"""Each task's event stream: ids, the two replay buffers, and SSE frames (design §3.5.4, AM12).

**Ids** are per task, rising integers. They are reserved ahead in blocks of 1,000 in the task's
database row (`agent_session.last_event_id`), so after a RAVIS restart new ids start above every id
a window may hold — otherwise Clarvis would drop new events as duplicates (C1's notes). The
database is written once per block, never per event.

**Two buffers, in memory only, never on disk** (review AM12):
- **deltas** (`agent.delta`, `command.output`): the last 2,000, or 8 MB;
- **every other event**, for the task's life, up to 20,000 events or 32 MB — the oldest
  `item.started` and `usage.updated` go first, since a completed item repeats what its start said.

**Resuming.** A cursor inside the kept range replays every non-delta event after it, and the deltas
still kept, with one `deltas_skipped` frame where deltas were dropped. A cursor from before the
oldest kept *required* event is refused before any byte is streamed, with 409
`EVENT_CURSOR_EXPIRED {oldest_event_id}`: the window reads the snapshot and reconnects with
`?after=<snapshot.last_event_id>` (runbook §4.1). A task's buffers go 30 minutes after it ends.

**Slow readers** (review AM3): each open stream has its own bounded queue (1 MB). A reader that
lets it overflow is disconnected — its stream just ends — and resumes from its cursor. A slow
window never slows Codex, the task, or another window.

**Frames:** `retry: 3000` first, then `id:`/`event:`/`data:` frames whose data is one JSON object —
`session_id` and the event's fields — and a `: heartbeat` comment every 15 s. `deltas_skipped` has
no `id:`, so it never moves a window's cursor (its shape is this increment's, recorded in
`conventions.json` → `open_points`).
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DELTA_EVENTS = frozenset({"agent.delta", "command.output"})
#: What goes first when the other buffer is full (design §3.5.4).
DROPPED_FIRST = frozenset({"item.started", "usage.updated"})
DELTAS_KEPT, DELTA_BYTES = 2000, 8 * 1024 * 1024
OTHERS_KEPT, OTHER_BYTES = 20000, 32 * 1024 * 1024
RESERVE_BLOCK = 1000
SUBSCRIBER_BYTES = 1024 * 1024
RETRY = b"retry: 3000\n\n"
HEARTBEAT = b": heartbeat\n\n"


@dataclass(frozen=True)
class Event:
    id: int
    name: str
    #: `session_id` and the event's fields, as the frame's data.
    data: dict[str, Any]
    frame: bytes

    @property
    def size(self) -> int:
        return len(self.frame)


def frame(event_id: int | None, name: str, data: dict[str, Any]) -> bytes:
    head = f"id: {event_id}\n" if event_id is not None else ""
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"{head}event: {name}\ndata: {body}\n\n".encode()


class _Buffer:
    """One bounded replay buffer, remembering the highest id it had to let go."""

    def __init__(self, count: int, size: int, dropped_first: frozenset[str]) -> None:
        self.events: deque[Event] = deque()
        self._count, self._size, self._dropped_first = count, size, dropped_first
        self.bytes = 0
        #: The highest id evicted whose loss a resuming window would notice.
        self.lost_through = 0

    def add(self, event: Event) -> None:
        self.events.append(event)
        self.bytes += event.size
        while len(self.events) > self._count or self.bytes > self._size:
            self._evict()

    def _evict(self) -> None:
        for index, queued in enumerate(self.events):
            if queued.name in self._dropped_first:
                del self.events[index]
                self.bytes -= queued.size
                return
        gone = self.events.popleft()
        self.bytes -= gone.size
        self.lost_through = gone.id


class Subscriber:
    """One open stream's queue of frames, filled without waiting and bounded (AM3)."""

    def __init__(self, limit: int = SUBSCRIBER_BYTES) -> None:
        self._frames: deque[tuple[int, bytes]] = deque()
        self._bytes = 0
        self._limit = limit
        self._ready = asyncio.Event()
        self.closed = False

    def offer(self, event: Event) -> None:
        if self.closed:
            return
        if self._bytes + event.size > self._limit:
            # Too slow: end this stream; the window resumes from its cursor.
            self.close()
            return
        self._frames.append((event.id, event.frame))
        self._bytes += event.size
        self._ready.set()

    def close(self) -> None:
        self.closed = True
        self._ready.set()

    async def take(self, timeout: float) -> list[tuple[int, bytes]]:
        """The frames queued so far, waiting up to `timeout` for the first; [] after a timeout."""
        if not self._frames and not self.closed:
            try:
                async with asyncio.timeout(timeout):
                    await self._ready.wait()
            except TimeoutError:
                return []
        taken = list(self._frames)
        self._frames.clear()
        self._bytes = 0
        self._ready.clear()
        return taken


@dataclass(frozen=True)
class Replay:
    events: list[Event]
    #: Whether deltas after the cursor were dropped, so a `deltas_skipped` frame goes first.
    deltas_skipped: bool


class EventLog:
    """One task's events: appended by the task, replayed and fanned out to its open streams."""

    def __init__(
        self, session_id: str, *, after: int, reserve: Callable[[int], None]
    ) -> None:
        self.session_id = session_id
        self.last_id = after
        self._reserved = after
        self._reserve = reserve
        self._deltas = _Buffer(DELTAS_KEPT, DELTA_BYTES, frozenset())
        self._others = _Buffer(OTHERS_KEPT, OTHER_BYTES, DROPPED_FIRST)
        # Everything before this log began — a previous RAVIS's events — is lost to a resume.
        self._others.lost_through = after
        self._deltas.lost_through = after
        self._subscribers: set[Subscriber] = set()

    def append(self, name: str, fields: dict[str, Any]) -> Event:
        self.last_id += 1
        if self.last_id > self._reserved:
            self._reserved = self.last_id + RESERVE_BLOCK
            self._reserve(self._reserved)
        data = {"session_id": self.session_id, **fields}
        event = Event(self.last_id, name, data, frame(self.last_id, name, data))
        (self._deltas if name in DELTA_EVENTS else self._others).add(event)
        for subscriber in list(self._subscribers):
            subscriber.offer(event)
        return event

    def replay(self, after: int) -> Replay | int:
        """The events after a cursor, or the oldest kept event's id when the cursor is too old."""
        if after >= self.last_id:
            return Replay([], deltas_skipped=False)
        if after < self._others.lost_through:
            return self.oldest_event_id()
        kept = [e for e in (*self._others.events, *self._deltas.events) if e.id > after]
        kept.sort(key=lambda event: event.id)
        return Replay(kept, deltas_skipped=self._deltas.lost_through > after)

    def oldest_event_id(self) -> int:
        return self._others.events[0].id if self._others.events else self.last_id + 1

    def subscribe(self) -> Subscriber:
        subscriber = Subscriber()
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    def close_streams(self) -> None:
        for subscriber in list(self._subscribers):
            subscriber.close()
        self._subscribers.clear()
