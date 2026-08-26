"""Assembling a trace from what was actually recorded (§11.2).

**Never synthesize a span as fact.** §11.2 says it twice — *"mark missing spans
and clock skew"* — and it is the only rule here that changes the code's shape.
A waterfall is a drawing of durations, and a drawing is exactly where an invented
number stops looking invented: a bar is a bar whether it was measured or guessed.

So:

- A span's duration comes from **two recorded timestamps or nowhere**. One
  timestamp is a point in time and is drawn as one.
- A service that appears in a trace with no event of its own is a **gap**,
  labelled, with no bar.
- Clock skew is **reported, not corrected**. A child that starts before its
  parent is a fact about two clocks, and shifting it to look tidy would delete
  the only evidence that they disagree.

The hub is the source. §11.1 makes it operational telemetry rather than the
system of record, so nothing here reaches back into a producer to fill a hole —
a hole is the finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

# Services that may appear in a trace, in the order a request passes through
# them. Used only for laying the waterfall out; a service absent from a trace is
# absent from the drawing rather than drawn empty.
LANE_ORDER = ("clarvis", "nervis", "ravis", "sirvis", "lmstudio", "ollama")


def _moment(value: Any) -> float | None:
    """One ISO timestamp as epoch seconds, or None.

    None rather than a fallback. A missing or unparseable timestamp is the thing
    §11.2 wants visible, and substituting "now" would place a span at the moment
    somebody opened the screen.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class Span:
    """One service's contribution to a trace, as recorded.

    `started` and `ended` are epoch seconds or None. `duration_ms` is None
    whenever either is — which is most of the time today, because a single event
    is a point rather than an interval, and inventing an end for it is the
    forbidden thing.
    """

    service: str
    label: str
    started: float | None = None
    ended: float | None = None
    events: int = 0
    severity: str = "info"

    @property
    def duration_ms(self) -> float | None:
        """The measured interval, or None when there is not one.

        **One event is a point, not a zero-length interval.** A single event sets
        `started` and `ended` to the same moment, so the arithmetic gives 0.0 —
        which draws a zero-width bar and reads as "this took no time" rather
        than "this was one recorded moment". Keyed on the number of events
        because that is the evidence: two or more bound an interval, one does
        not.
        """
        if self.events < 2 or self.started is None or self.ended is None:
            return None
        if self.ended < self.started:
            return None
        return round((self.ended - self.started) * 1000, 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "label": self.label,
            "started": self.started,
            "ended": self.ended,
            "duration_ms": self.duration_ms,
            "events": self.events,
            "severity": self.severity,
            # A span with one timestamp is a point on the timeline, and the
            # drawing must not give it width.
            "is_point": self.duration_ms is None,
        }


@dataclass
class Trace:
    """Everything recorded under one trace id."""

    trace_id: str
    spans: list[Span] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def started(self) -> float | None:
        moments = [s.started for s in self.spans if s.started is not None]
        return min(moments) if moments else None

    @property
    def window_ms(self) -> float | None:
        starts = [s.started for s in self.spans if s.started is not None]
        ends = [s.ended if s.ended is not None else s.started for s in self.spans]
        finite = [e for e in ends if e is not None]
        if not starts or not finite:
            return None
        return round((max(finite) - min(starts)) * 1000, 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "started": self.started,
            "window_ms": self.window_ms,
            "spans": [span.as_dict() for span in self.spans],
            "events": self.events,
            "warnings": self.warnings,
        }


def assemble(trace_id: str, events: Iterable[Mapping[str, Any]]) -> Trace:
    """One trace, from the events recorded under it.

    Spans are grouped by producing service, because that is the grain the
    envelope carries: §4.4 has a `source` and a `span_id`, and until producers
    emit paired start/end events a service's span is *the interval its events
    cover*. Stated rather than implied — it is a weaker claim than a real span
    and the difference matters when reading a waterfall.
    """
    trace = Trace(trace_id=trace_id)
    by_service: dict[str, Span] = {}
    ordered = sorted(events, key=lambda e: _moment(e.get("occurred_at")) or 0.0)

    for event in ordered:
        source = event.get("source") or {}
        service = (
            str(source.get("service_type") or "unknown")
            if isinstance(source, Mapping) else "unknown"
        )
        at = _moment(event.get("occurred_at"))
        span = by_service.get(service)
        if span is None:
            span = Span(service=service, label=service.upper())
            by_service[service] = span
        span.events += 1
        if _rank(str(event.get("severity") or "info")) > _rank(span.severity):
            span.severity = str(event.get("severity") or "info")
        if at is not None:
            span.started = at if span.started is None else min(span.started, at)
            span.ended = at if span.ended is None else max(span.ended, at)
        trace.events.append(dict(event))

    trace.spans = [
        by_service[key] for key in LANE_ORDER if key in by_service
    ] + [span for key, span in by_service.items() if key not in LANE_ORDER]

    _note_gaps(trace, ordered)
    _note_skew(trace)
    return trace


def _rank(severity: str) -> int:
    order = ("debug", "info", "warning", "error", "critical")
    return order.index(severity) if severity in order else 1


def _note_gaps(trace: Trace, events: list[Mapping[str, Any]]) -> None:
    """Say what is missing, without drawing it.

    A trace that reaches RAVIS necessarily passed through whatever called it, so
    a caller with no events of its own is a hole in the record rather than a
    service that did nothing. It is named in `warnings` and gets **no span**,
    because a span with no evidence behind it is the invented bar §11.2 forbids.
    """
    seen = {span.service for span in trace.spans}
    if "ravis" in seen and "nervis" not in seen and "clarvis" not in seen:
        trace.warnings.append(
            "RAVIS appears with no calling service recorded — the caller either "
            "does not publish events or its span was lost. No bar is drawn for it."
        )
    for event in events:
        if not str((event.get("source") or {}).get("service_type") or ""):
            trace.warnings.append("an event carries no source service and is grouped as unknown")
            break


def _note_skew(trace: Trace) -> None:
    """Report clocks that disagree; never move a span to hide it.

    §11.2 asks for skew to be *marked*. Correcting it would delete the only
    evidence the two clocks differ, and a waterfall that has been quietly
    straightened is worse than one that visibly cannot be — the second makes
    somebody go and look.
    """
    for event in trace.events:
        occurred = _moment(event.get("occurred_at"))
        received = _moment(event.get("_received_at"))
        if occurred is None or received is None:
            continue
        drift = occurred - received
        if drift > 2.0:
            trace.warnings.append(
                f"{event.get('event_type', 'an event')} is stamped {drift:.1f}s after "
                "NERVIS received it — the producer's clock is ahead"
            )
            break


def summarise(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every trace present in a set of events, newest first.

    A listing rather than a full assembly: the index answers *which traces
    exist*, and assembling each one to answer that would read every event twice.
    """
    traces: dict[str, dict[str, Any]] = {}
    for event in events:
        trace_id = str(event.get("trace_id") or "")
        if not trace_id:
            continue
        source = event.get("source") or {}
        service = str(source.get("service_type") or "") if isinstance(source, Mapping) else ""
        row = traces.setdefault(trace_id, {
            "trace_id": trace_id, "events": 0, "services": [],
            "started": None, "severity": "info",
        })
        row["events"] += 1
        if service and service not in row["services"]:
            row["services"].append(service)
        at = _moment(event.get("occurred_at"))
        if at is not None and (row["started"] is None or at < row["started"]):
            row["started"] = at
        severity = str(event.get("severity") or "info")
        if _rank(severity) > _rank(row["severity"]):
            row["severity"] = severity
    return sorted(traces.values(), key=lambda r: r["started"] or 0.0, reverse=True)
