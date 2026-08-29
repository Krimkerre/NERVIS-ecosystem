"""The shared event envelope — §4.4's shape, built once for every producer.

Runbook §4.4 gives every service the same envelope and NERVIS the hub that
receives it. The hub validates the shape (`nervis/events.py`) and rejects what
does not fit; this is the other end, so that a producer does not have to
rediscover the shape, the severity vocabulary, or the redaction obligation.

**Redaction is the producer's job and it is not optional.** §9 forbids prompts,
model output, credentials and raw paths in telemetry, and observability.py says
why the obligation sits here: *a collector cannot un-leak a secret it has
already received*. NERVIS quarantines a malformed envelope; nothing on the
receiving side can quarantine a well-formed one carrying somebody's API key.

**`redact()` is shallow, and this is not.** That function is a dict
comprehension over top-level keys, which is right for a log record and wrong
for an event: `data={"spec": {"tests": [{"prompt": …}]}}` walks straight
through it. Reaching for the existing helper and assuming it covers a nested
payload is the likely way this leaks, so the walk here is recursive and covers
lists as well as dicts.

Nothing here performs I/O. Building an envelope must be safe to do on a request
path, and a producer that cannot reach NERVIS should still be able to construct
the event it failed to send — `publisher.py` holds everything that can block.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from ecosystem_protocol.observability import REDACTED_KEYS

# §4.4's required fields, mirroring `nervis/events.py`'s `REQUIRED`. Named here
# so a producer fails in its own tests rather than in the hub's quarantine.
REQUIRED_FIELDS = ("event_id", "event_type", "occurred_at")

# The hub's vocabulary, and the whole of it. An unknown severity is rejected on
# arrival, so a producer inventing "warn" or "fatal" discovers it as a
# quarantined event rather than as a type error.
SEVERITIES = ("debug", "info", "warning", "error", "critical")

# How much of a free-text value survives. Long enough for a message to be
# useful, short enough that a stack trace or a pasted document cannot ride out
# inside one — and the truncation is *marked*, because a silently shortened
# string is a value a reader will quote back as if it were complete.
MAX_TEXT = 300


def redact_deep(value: Any, *, depth: int = 0) -> Any:
    """`redact`, but all the way down, and through lists.

    A key whose name is in `REDACTED_KEYS` is replaced wherever it appears, at
    any depth and inside any list. The names are matched case-insensitively for
    the same reason the shallow one does it: a producer writing `API_KEY` is
    making the same mistake as one writing `api_key`.

    Depth is bounded. A payload that nests deeply enough to matter is already a
    payload nobody should be putting in an event, and a recursive walk over an
    accidentally cyclic structure would take the producer down with it — which
    is the one thing telemetry must never do.
    """
    if depth > 6:
        return "[too deeply nested to redact]"
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[redacted]"
                if str(key).lower() in REDACTED_KEYS
                else redact_deep(item, depth=depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_deep(item, depth=depth + 1) for item in value]
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + "… [truncated]"
    return value


def stable_event_id(*parts: str) -> str:
    """An id that is the same every time this event is built.

    **Retry safety turns on this.** The hub inserts with `INSERT OR IGNORE` on
    `event_id`, so re-sending after a collector outage is harmless — but only if
    the id is unchanged. A `uuid4()` minted per attempt is a *different* event
    to the hub: it is stored again, and `span.events` counts it twice. That
    number is not decoration. `traces.Span.duration_ms` returns None below two
    events, so an inflated count is what decides whether a span is drawn as an
    interval or as a point.

    Derived from the caller's own identifiers — a run id and an event type, say
    — so two genuinely different events never collide while one event retried
    five times stays one event.
    """
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def envelope(
    *,
    event_type: str,
    service_type: str,
    service_id: str = "",
    instance_id: str = "",
    machine_id: str = "",
    trace_id: str = "",
    severity: str = "info",
    data: Mapping[str, Any] | None = None,
    event_id: str = "",
    occurred_at: str = "",
) -> dict[str, Any]:
    """One §4.4 envelope, redacted, ready to send.

    `source.service_type` is load-bearing beyond identification: NERVIS's
    `traces.assemble` groups events into spans by exactly that field, so it is
    what makes a service appear as its own lane on a waterfall. A producer that
    omits it lands in a lane called "unknown" together with everybody else who
    omitted it.

    `trace_id` is deliberately not defaulted to anything. An event with an empty
    one is accepted by the hub and then dropped by `summarise`, so it would be
    stored, counted against retention, and invisible — the worst of the three
    outcomes. A caller with no trace should skip the emit instead, which
    `publisher.emit` enforces.
    """
    if severity not in SEVERITIES:
        raise ValueError(f"{severity!r} is not one of {', '.join(SEVERITIES)}")
    body = {
        "event_id": event_id or uuid.uuid4().hex,
        "event_type": event_type,
        # UTC with an explicit offset. The hub sorts on this to build a
        # waterfall across services, and a naive local timestamp from one
        # producer would place its span at an hour that never happened.
        "occurred_at": (occurred_at or datetime.now(timezone.utc).isoformat()),
        "severity": severity,
        "source": {
            "service_type": service_type,
            "service_id": service_id,
            "instance_id": instance_id,
            "machine_id": machine_id,
        },
        "data": redact_deep(dict(data or {})),
    }
    if trace_id:
        body["trace_id"] = trace_id
    return body
