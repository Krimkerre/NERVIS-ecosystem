"""A model call awaited inside the health-probe loop, from the external audit.

Everything else on that timer is a bounded DELETE or a handful of `stat` calls,
and the comments beside them argue correctly that a second scheduler for a
millisecond of work is machinery nobody should maintain. The unattended thought
is not that: it asks a model a question. Awaited inline, the probes stopped for
as long as the answer took — and a service falling over during a thought went
unnoticed until the thought finished, which is the one moment a health reading
matters most.

Live on the machine this was written for: background thinking was switched on,
running every fifteen minutes.

The interval used to be the overlap guard as well, because the loop could not
come round again until the thought returned. Started as a task it can, so the
guard had to become a real one.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nervis import app as app_module


class _App:
    """Enough of the application for the starter to work against."""

    def __init__(self) -> None:
        self.state = type("S", (), {})()


def _never_finishes(started: asyncio.Event) -> Any:
    async def _think(_api: Any) -> None:
        started.set()
        await asyncio.sleep(3600)

    return _think


@pytest.mark.asyncio
async def test_the_thought_does_not_hold_up_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """**The finding itself.** The starter returns while the thought is still
    running, so the next probe happens on time."""
    started = asyncio.Event()
    monkeypatch.setattr(app_module, "_think_if_due", _never_finishes(started))
    api = _App()

    await asyncio.wait_for(asyncio.to_thread(lambda: None), 1)  # settle the loop
    app_module._start_thought_if_due(api)

    await asyncio.wait_for(started.wait(), 1)
    running = getattr(api.state, app_module._THINKING)
    assert not running.done(), "the fixture is wrong — the thought must still be running"
    running.cancel()


@pytest.mark.asyncio
async def test_a_second_thought_cannot_join_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The guard that had to replace the interval.** While the thought was
    awaited, "has the interval elapsed" was the whole of the protection: the
    loop simply could not come round again. It can now."""
    started = asyncio.Event()
    monkeypatch.setattr(app_module, "_think_if_due", _never_finishes(started))
    api = _App()

    app_module._start_thought_if_due(api)
    await asyncio.wait_for(started.wait(), 1)
    first = getattr(api.state, app_module._THINKING)

    app_module._start_thought_if_due(api)
    second = getattr(api.state, app_module._THINKING)

    assert second is first, "a second unattended run started beside the first"
    first.cancel()


@pytest.mark.asyncio
async def test_a_finished_thought_lets_the_next_one_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard on the guard: refusing forever would pass the test above and
    quietly switch the feature off after its first run."""
    async def _quick(_api: Any) -> None:
        return None

    monkeypatch.setattr(app_module, "_think_if_due", _quick)
    api = _App()

    app_module._start_thought_if_due(api)
    first = getattr(api.state, app_module._THINKING)
    await first

    app_module._start_thought_if_due(api)

    assert getattr(api.state, app_module._THINKING) is not first, (
        "no further unattended run can ever start"
    )
