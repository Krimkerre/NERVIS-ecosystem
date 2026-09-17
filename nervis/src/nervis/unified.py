"""M17 — one trace, with everything else NERVIS knows about that moment.

M7 built the waterfall: spans, gaps marked rather than interpolated, clock skew
named. What it could not answer is the question somebody actually arrives with,
which is never *"what were the spans"* — it is *"what was going on when this
happened"*. So this adds the three things M17 names around a trace: what each
service's health was, which log lines belong to it, and what is known about the
model that ran.

**Three kinds of claim, kept apart.** §11.3 requires diagnostics to distinguish
a service-reported fact, a NERVIS observation and an operator inference, and
that rule does real work here rather than being quoted:

- A log line carrying the trace id **is** part of the trace. Fact.
- A log line merely written during the same seconds is a NERVIS observation
  about a window, and might belong to something else entirely.
- Health *at the time* is reconstructed from the transitions NERVIS recorded,
  and where nothing was recorded the honest answer is that the state then is
  unknown — not that it was whatever it is now.

Each linked thing therefore carries *how* it was linked, and a caller that shows
them identically is misreporting the last two as the first.

**A broken link still produces a partial trace**, which is M17's exit clause and
the reason nothing here raises. Every section is optional and its absence is
reported as absence: no logs configured, no evidence for that model, no
transition recorded. A unified view that failed whole when one input was missing
would be useless in exactly the situation it exists for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# How far either side of a trace a log line may fall and still be offered as
# *possibly* related. Wide enough to catch the line written just before the
# request that explains it, narrow enough that it is not simply "recent".
WINDOW_SECONDS = 2.0

# How many correlated lines to carry. A trace that overlapped a busy second
# should not return a thousand lines nobody reads.
MAX_LINES = 60

# How a link was made, and these are not interchangeable.
BY_TRACE = "carries the trace id"
BY_WINDOW = "written during the same window"


@dataclass
class Linked:
    """One correlated thing, and the strength of the claim that links it."""

    how: str
    items: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"how": self.how, "items": self.items, "reason": self.reason}


def health_at(
    services: Sequence[Mapping[str, Any]],
    transitions: Iterable[Mapping[str, Any]],
    when: float | None,
) -> list[dict[str, Any]]:
    """Each service's state at the moment of the trace, where it is knowable.

    **Not the state now.** A trace read an hour later is read against a machine
    that has since changed, and showing today's health beside yesterday's
    failure is how somebody concludes the failure is unexplained. The state
    *then* is reconstructed from `nervis.service.state_changed` — the last
    transition at or before the trace — and where none was recorded the answer
    is `unknown`, which is a different claim from `healthy`.
    """
    latest: dict[str, dict[str, Any]] = {}
    for event in transitions:
        moment = _moment(event)
        if when is not None and moment is not None and moment > when:
            continue
        data = event.get("data")
        if not isinstance(data, Mapping):
            continue
        service = str(data.get("service") or "")
        if service:
            latest[service] = {"state": str(data.get("to") or ""), "at": moment}

    found = []
    for entry in services:
        key = str(entry.get("key") or "")
        seen = latest.get(key)
        now = str(entry.get("state") or "")
        found.append({
            "service": key,
            "state_then": seen["state"] if seen else "unknown",
            "known": seen is not None,
            "state_now": now,
            # Worth showing plainly: a service that was fine then and is broken
            # now, or the reverse, is the most useful line on the overlay.
            "changed_since": bool(seen and seen["state"] != now),
        })
    return found


def window_around(started: float, ended: float | None = None) -> tuple[float, float]:
    """The seconds a log line may fall in and still be offered as near a trace."""
    last = ended if ended is not None and ended >= started else started
    return started - WINDOW_SECONDS, last + WINDOW_SECONDS


def correlate_logs(
    lines: Sequence[Mapping[str, Any]],
    trace_id: str,
    started: float | None,
    ended: float | None = None,
) -> Linked:
    """Log lines belonging to this trace, by the strongest link available.

    Prefers the trace id and says so. Falls back to the time window and says
    *that* — because a line written in the same second as a request is evidence
    about the second, not about the request, and presenting the two the same way
    turns a coincidence into a finding.

    **The window is a window** (17 September 2026). Until then the fallback offered whatever lines
    it was handed — the newest few of each log — as "written during the same window", although no
    log line said when it was written, so a trace from yesterday showed today's lines. A line now
    counts only if its own `time` (log lines carry one since ecosystem-protocol 0.2.3) falls within
    `WINDOW_SECONDS` of the trace; a line with no time is never offered, and the reason says which
    of those left the list empty.
    """
    # **A read about the trace is not part of it** (17 September 2026). NERVIS's access log
    # names the trace id in every request to view it, so the dashboard's own reads of a trace
    # became that trace's "correlated log lines" — the only ones, for a trace a day old.
    lines = [line for line in lines if not _looks_it_up(line, trace_id)]
    carrying = [
        dict(line) for line in lines
        if trace_id and (
            trace_id == str(line.get("trace_id") or "")
            or trace_id in str(line.get("message") or "")
        )
    ]
    if carrying:
        return Linked(BY_TRACE, carrying[:MAX_LINES])
    if started is None:
        return Linked(BY_WINDOW, [], "the trace has no start time, so no window can be taken")
    first, last = window_around(started, ended)
    dated = [(line, at) for line in lines if (at := _line_time(line)) is not None]
    if not dated:
        return Linked(BY_WINDOW, [], "no log line carries this trace id, and none of the lines "
                      "read says when it was written, so none can be placed near this trace "
                      "(log lines carry a time since ecosystem-protocol 0.2.3)")
    near = [dict(line) for line, at in dated if first <= at <= last]
    if not near:
        return Linked(BY_WINDOW, [], "no log line carries this trace id, and none of the lines "
                      f"read was written within {WINDOW_SECONDS:g} s of it")
    return Linked(BY_WINDOW, near[:MAX_LINES],
                  f"no log line carries this trace id — these were written within "
                  f"{WINDOW_SECONDS:g} s of it and may belong to something else entirely")


def _line_time(line: Mapping[str, Any]) -> float | None:
    """When a log line says it was written, as epoch seconds, or None."""
    from nervis.traces import _moment as parse

    return parse(line.get("time"))


def _looks_it_up(line: Mapping[str, Any], trace_id: str) -> bool:
    """Whether a log line is a GET or HEAD to NERVIS's API that names the trace id — a lookup."""
    if not trace_id or str(line.get("trace_id") or "") == trace_id:
        return False
    pattern = r'"(?:GET|HEAD) /api/v1/[^" ]*' + re.escape(trace_id)
    return re.search(pattern, str(line.get("message") or "")) is not None


def runtime_context(
    models: Sequence[str], evidence: Mapping[str, Any] | None
) -> dict[str, Any]:
    """What SIRVIS knows about the models this trace used, where it knows any.

    M17's exit says *where available*, and that qualifier is the whole design:
    SIRVIS measures what somebody asked it to measure, so most models have no
    evidence and saying so is the answer. An absent measurement is never
    inferred from a present one for a similar build.
    """
    if not models:
        return {"models": [], "available": False,
                "reason": "no model is named in this trace"}
    if not evidence:
        return {"models": list(models), "available": False,
                "reason": "SIRVIS published no evidence for the model(s) in this trace"}
    return {"models": list(models), "available": True, "reason": "", "evidence": dict(evidence)}


def models_in(trace: Mapping[str, Any]) -> list[str]:
    """Every model named by the events under this trace, in order of appearance."""
    found: list[str] = []
    for event in trace.get("events") or []:
        data = event.get("data") if isinstance(event, Mapping) else None
        if not isinstance(data, Mapping):
            continue
        for key in ("model", "selected"):
            name = str(data.get(key) or "")
            if name and name not in found:
                found.append(name)
    return found


def unify(
    trace: Mapping[str, Any],
    services: Sequence[Mapping[str, Any]],
    transitions: Iterable[Mapping[str, Any]],
    log_lines: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One trace with its health overlay, correlated logs and runtime context.

    Every section is independently optional. A missing input produces a section
    that says it is missing, never an exception and never a whole view that
    fails — M17's exit calls that a partial trace and it is the ordinary case,
    because the thing being diagnosed is usually the thing that is broken.
    """
    started = trace.get("started")
    window_ms = trace.get("window_ms")
    ended = (
        started + window_ms / 1000
        if isinstance(started, float) and isinstance(window_ms, (int, float))
        else None
    )
    models = models_in(trace)
    return {
        "trace": dict(trace),
        "health": health_at(services, transitions, started if isinstance(started, float) else None),
        "logs": correlate_logs(
            log_lines, str(trace.get("trace_id") or ""),
            started if isinstance(started, float) else None,
            ended,
        ).as_dict(),
        "runtime": runtime_context(models, evidence),
        "partial": bool(trace.get("warnings")),
    }


def _moment(event: Mapping[str, Any]) -> float | None:
    """When an event happened, as a float, or nothing."""
    from nervis.traces import _moment as parse

    return parse(event.get("occurred_at") or event.get("received_at"))


__all__ = [
    "BY_TRACE", "BY_WINDOW", "Linked", "MAX_LINES", "WINDOW_SECONDS",
    "correlate_logs", "health_at", "window_around", "models_in", "runtime_context", "unify",
]
