"""M9 — what one Clarvis window is doing, assembled from what it published.

`CLARVIS.md` §6 gives NERVIS three read surfaces and no others: the registry row
an extension host claimed for itself (M8a), that host's own `/v1/status` (M8b),
and the events it forwarded to the hub. This module joins those three into the
answer the diagnostics screen asks, and does nothing else.

**Nothing here is stored.** M9's exit says *no Clarvis state duplicated
independently*, and the failure it guards against is subtle: a NERVIS that kept
its own copy of "the agent is running" would keep showing it after Clarvis
stopped, and the copy would be indistinguishable from the truth right up until
it was wrong. So every field is derived on the way out of a read, from rows the
hub already holds for its own reasons. There is no clarvis table.

**A task is what Clarvis said, in the order it said it.** `clarvis.task.started`
and `clarvis.task.completed` are §6.4 events with an opaque id, so a task's state
is a fold over the events carrying that id — not an inference from status, and
never a guess. A task that started and has not completed is *running* because
nothing said otherwise, which is a different claim from *stalled* and is the
only one the evidence supports.

**Isolation is a filter, not a convention.** §6.6 is explicit that events and
status from one window never appear under another, so events are selected by
`source.instance_id` and a window with no events gets an empty list rather than
the hub's recent traffic.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

# §6.4's task family. Only these two decide a task's state; a third spelling
# would be a family this vocabulary does not have, and is ignored rather than
# folded in as though it were understood.
TASK_STARTED = "clarvis.task.started"
TASK_COMPLETED = "clarvis.task.completed"

# The agent-run family, in the order a run passes through them. `step` is
# deliberately absent from the terminal set: a run emitting steps is still
# running, and treating the newest step as an outcome would end every run at
# whatever it last did.
AGENT_STARTED = "clarvis.agent.started"
AGENT_TERMINAL = ("clarvis.agent.completed", "clarvis.agent.failed",
                  "clarvis.agent.cancelled")

# What a gate event says, and the whole of what NERVIS does with it. §6.7 allows
# displaying *that* a gate awaits the user; the question itself is never
# published and there is no resolve path here, by construction rather than by
# policy — this module imports nothing that could make a request.
GATE_REQUESTED = "clarvis.gate.requested"
GATE_RESOLVED = "clarvis.gate.resolved"

# How many events one screen shows. Enough to see a run take shape, small enough
# that a busy window does not turn the read into a page of history.
EVENT_WINDOW = 40


def _envelope(event: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """An event's type and its data, with neither invented."""
    data = event.get("data")
    return str(event.get("event_type") or ""), data if isinstance(data, Mapping) else {}


def _task_id(data: Mapping[str, Any]) -> str:
    """The opaque task id §6.3 promises, or nothing.

    Clipped, because it is a free-form string from another process and this one
    reaches a screen. §6.4 forbids paths and content in a payload; a length cap
    is the part NERVIS can enforce on its own side.
    """
    found = data.get("task_id") or data.get("id") or ""
    return str(found)[:64] if isinstance(found, (str, int)) else ""


def tasks(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every task Clarvis mentioned, newest first, folded from its own events.

    A task appears once however many events it has. `started` creates it and
    `completed` closes it, and an outcome is copied only if Clarvis sent one —
    §6.3's *unknown values stay unknown* applies to a result class exactly as it
    applies to a duration.
    """
    seen: dict[str, dict[str, Any]] = {}
    for event in events:
        event_type, data = _envelope(event)
        if event_type not in (TASK_STARTED, TASK_COMPLETED):
            continue
        task_id = _task_id(data)
        if not task_id:
            continue
        entry = seen.setdefault(task_id, {"task_id": task_id, "state": "running",
                                          "started_at": "", "completed_at": ""})
        if event_type == TASK_STARTED:
            entry["started_at"] = str(event.get("occurred_at") or "")
        else:
            entry["state"] = "completed"
            entry["completed_at"] = str(event.get("occurred_at") or "")
            outcome = data.get("outcome") or data.get("result")
            if isinstance(outcome, str) and outcome:
                entry["outcome"] = outcome[:40]
    return sorted(seen.values(), key=lambda t: t["started_at"], reverse=True)


def agent_run(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The most recent agent run, and whether it has ended.

    Read from events rather than from status because status answers *now* and
    this answers *what happened* — a run that finished thirty seconds ago is
    invisible to `/v1/status`, which reports `idle`, and is exactly what somebody
    opening the screen wants to see.
    """
    run: dict[str, Any] = {}
    for event in events:
        event_type, _ = _envelope(event)
        if event_type == AGENT_STARTED:
            # A new run replaces the previous one rather than merging: two runs
            # in one window are two runs, and folding their steps together would
            # report a count that belongs to neither.
            run = {"state": "running", "started_at": str(event.get("occurred_at") or ""),
                   "steps": 0, "ended_at": ""}
        elif not run:
            continue
        elif event_type == "clarvis.agent.step":
            run["steps"] = int(run.get("steps", 0)) + 1
        elif event_type in AGENT_TERMINAL:
            run["state"] = event_type.rsplit(".", 1)[-1]
            run["ended_at"] = str(event.get("occurred_at") or "")
    return run


def gate(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Whether a gate is waiting, and of which category.

    **Displayed, never answered.** §6.7 lets NERVIS show that a gate awaits the
    user and forbids resolving one on their behalf, and the enforcement is that
    there is nothing to call: the Bridge has no write path, and this package
    reads events. A resolved gate clears the wait rather than staying on screen,
    because a stale "waiting for you" is how somebody learns to ignore the real
    one.
    """
    waiting: dict[str, Any] = {}
    for event in events:
        event_type, data = _envelope(event)
        if event_type == GATE_REQUESTED:
            category = str(data.get("category") or "")
            waiting = {"category": category[:40], "since": str(event.get("occurred_at") or "")}
        elif event_type == GATE_RESOLVED:
            waiting = {}
    return waiting


def diagnostics(
    instance: Mapping[str, Any],
    status: Mapping[str, Any],
    events: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """One window's whole answer.

    `events` arrives oldest-first, which is what the folds above require: a
    task's outcome is whichever event came last, and reversing that would report
    the first thing that happened as the current state.
    """
    return {
        "instance_id": str(instance.get("instance_id") or ""),
        "label": str(instance.get("label") or ""),
        "endpoint": str(instance.get("endpoint") or ""),
        "live": bool(instance.get("live")),
        "expires_in": instance.get("expires_in"),
        "capabilities": dict(instance.get("capabilities") or {}),
        "status": dict(status),
        "agent_run": agent_run(events),
        "tasks": tasks(events)[:EVENT_WINDOW],
        "gate": gate(events),
        "events": [dict(event) for event in events[-EVENT_WINDOW:]][::-1],
        "event_count": len(events),
    }


__all__ = ["EVENT_WINDOW", "agent_run", "diagnostics", "gate", "tasks"]
