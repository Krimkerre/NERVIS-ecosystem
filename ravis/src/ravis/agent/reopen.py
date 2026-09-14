"""Reopening a task's Codex thread, so it reaches the sites the owner allowed (R5).

**Why.** Codex 0.154.0 reads a thread's site list when it loads the thread, and never again while
the thread stays loaded: a site allowed later stayed blocked in the turn that was running and in the
thread's next turn (calibration runs `cal_ed672bf12c6f` and `cal_85aa0ece0f52`), while the same
thread reopened from disk reached it (`cal_5a1d6ecc33b4`). So after a blocked site RAVIS lets go of
the thread (`thread/unsubscribe`), waits until Codex has unloaded it — `thread/loaded/list` stops
listing it, about 60 s later — and resumes it with `thread/resume`, as after a restart.

**This module is the letting go and the waiting.** `session.py` decides when a task reopens, what a
Stop, a switch or a settle skips (the resume, never the wait), and when a turn that waited starts;
the contract is `agent-sessions.json` → `reopening`.

**Real Codex's shapes** (K3's transcript in run `cal_5a1d6ecc33b4`): `thread/unsubscribe {threadId}`
answers `{status: "unsubscribed"}`, and `thread/loaded/list {}` answers `{data: [thread ids],
nextCursor}`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ravis.codex.rpc import CodexRpcError, CodexUnavailableError

Request = Callable[..., Awaitable[Any]]
#: How many pages of `thread/loaded/list` RAVIS reads before it takes a thread as still listed.
LOADED_PAGES = 10


@dataclass
class Reopening:
    """One reopen of a task's thread, from `thread/unsubscribe` until the wait has ended."""

    group_id: str | None
    hosts: tuple[str, ...]
    #: When it began, as `SessionView` → `codex.reopening.since` shows it.
    since: str
    #: False once a Stop, a switch or a settle skipped the resume; the wait goes on regardless.
    resume: bool = True
    #: A turn asked for meanwhile, as (kind, text): it starts once the thread is resumed.
    queued: tuple[str, str] | None = None
    #: The background work doing the waiting, so the task's end or Codex's can cancel it.
    task: asyncio.Task[None] | None = None

    def view(self) -> dict[str, Any]:
        """`SessionView` → `codex.reopening`."""
        return {"group_id": self.group_id, "hosts": list(self.hosts), "since": self.since}


async def let_go(request: Request, thread_id: str, *, seconds: float) -> None:
    """`thread/unsubscribe`: RAVIS stops following the thread, so Codex may unload it.

    A refusal changes nothing that follows: the wait below is what tells whether Codex let go.
    """
    with contextlib.suppress(CodexRpcError, CodexUnavailableError):
        await request("thread/unsubscribe", {"threadId": thread_id}, timeout=seconds)


async def unloaded(
    request: Request, thread_id: str, *, poll_seconds: float, cap_seconds: float,
    request_seconds: float,
) -> bool:
    """Whether Codex unloaded the thread within the cap: asked at once, then every poll.

    Measured on the event loop's own clock, so a test that moves the task's clock by hand (the
    unanswered policy's) can't make the cap pass early or never.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + cap_seconds
    while await still_loaded(request, thread_id, seconds=request_seconds):
        left = deadline - loop.time()
        if left <= 0:
            return False
        await asyncio.sleep(min(poll_seconds, left))
    return True


async def still_loaded(request: Request, thread_id: str, *, seconds: float) -> bool:
    """Whether `thread/loaded/list` still lists the thread, page by page.

    **Not knowing counts as listed**: a list Codex didn't answer is never taken as "let go", so an
    unanswered ask keeps RAVIS waiting, up to the cap, rather than resuming a thread too early.
    """
    cursor: object = None
    for _ in range(LOADED_PAGES):
        params = {} if cursor is None else {"cursor": cursor}
        try:
            listed = await request("thread/loaded/list", params, timeout=seconds)
        except (CodexRpcError, CodexUnavailableError):
            return True
        page = listed if isinstance(listed, dict) else {}
        data = page.get("data")
        if not isinstance(data, list) or thread_id in data:
            return True
        cursor = page.get("nextCursor")
        if not isinstance(cursor, str) or not cursor:
            return False
    return True
