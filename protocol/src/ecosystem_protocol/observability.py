"""Structured logging and the correlation IDs every request carries.

Shared rather than per-service for two reasons the runbook states outright. §4.3
fixes the correlation *vocabulary* across the ecosystem, so a collector can join
a Clarvis request to a RAVIS route to a SIRVIS benchmark — which only works if
all three spell it the same way. And §9's redaction list is a security control:
two copies of it means one of them is eventually missing a key, and the failure
is silent by construction.

ECOSYSTEM_RUNBOOK.md §4.3 fixes the vocabulary: `X-Request-ID` on every request,
created if absent, and `traceparent` for W3C trace context. Those IDs are
correlation data and **never authorization** — a trace ID does not approve a
gate, authorize a tool, or elevate a caller. That sentence is in the runbook
because the opposite is a tempting shortcut.

Logs are JSON lines so a collector can read them without a parser, and so a grep
for a request ID returns whole records rather than fragments.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from typing import Any

# Fields that must never be written to a log, in any product (runbook §9).
# Listed here rather than remembered: a redaction rule that lives only in a
# reviewer's head is a rule that eventually ships broken.
REDACTED_KEYS = frozenset(
    {"authorization", "api_key", "credential", "token", "prompt", "messages", "content"}
)


#: The request being served on this task, for whatever logs underneath it.
#:
#: **A context variable rather than an argument threaded through every call.**
#: `JsonLineFormatter` has always promoted these three fields when a record
#: carries them, and nothing ever set them: no log line in the ecosystem was
#: correlated with anything, while §4.3 fixed the vocabulary and §15 claimed the
#: property. Passing IDs into every `logger.info` across four packages is a rule
#: that holds for a month — the call added later without them is invisible, and
#: it is always the one being read during an incident. This is set once, where
#: each service already identifies the request, and reaches lines this codebase
#: never wrote — `httpx` logging an outbound call now names the inbound request
#: that caused it, which no call-site change could have done.
#:
#: **Except uvicorn's access line**, which is written after the application has
#: returned and the context has been reset, so it carries nothing. Recorded
#: rather than worked around: the access line already names the method, path and
#: status, and reaching it would mean owning uvicorn's logging rather than the
#: application's.
_CARRIED: ContextVar[dict[str, str]] = ContextVar("ecosystem_correlation", default={})


@contextlib.contextmanager
def carrying(request_id: str = "", trace_id: str = "",
             application_id: str = "", session_id: str = "") -> Iterator[None]:
    """Mark this task as serving one request, for the duration of the block.

    Empty values are dropped rather than carried: a field present and blank
    reads as "this request had no trace", which is a different claim from "this
    line belongs to no request", and startup logging is the second one.
    """
    carried = {
        name: value for name, value in (
            ("request_id", request_id),
            ("trace_id", trace_id),
            ("application_id", application_id),
            # §4.4 puts `session_id` on every event and §4.3 fixes it as shared
            # vocabulary. Clarvis sends `x-session-id`, RAVIS reads it into a
            # session, NERVIS forwards it — and no event ever carried it, so
            # NERVIS's `session_id` column had a reader, a schema and no writer.
            ("session_id", session_id),
        ) if value
    }
    token = _CARRIED.set(carried)
    try:
        yield
    finally:
        _CARRIED.reset(token)


def correlation() -> dict[str, str]:
    """What this task is currently serving, if anything.

    Public because the log filter is not the only consumer: an event published
    while handling a request belongs to the same request, and reading it here
    keeps one answer rather than two that drift.
    """
    return dict(_CARRIED.get())


class CorrelationFilter(logging.Filter):
    """Attach the current request's IDs to records that do not carry their own.

    **Fills gaps, never overrules.** A line that names a different request — a
    background task acting on behalf of one — is telling the truth about itself,
    and replacing that with the ambient value would substitute a default for a
    fact.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for name, value in _CARRIED.get().items():
            if not getattr(record, name, None):
                setattr(record, name, value)
        return True


class JsonLineFormatter(logging.Formatter):
    """Render a record as one JSON object per line.

    Correlation IDs are promoted to top-level fields rather than buried in the
    message, because that is what makes them filterable at the collector.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # `detail` is here because it was being dropped. Three call sites pass
        # one — a failing refresh, an interrupted stream, an exhausted fallback
        # chain — and each computes the sentence that says *which* models were
        # tried and why. The formatter emitted the headline and discarded the
        # reason, so the log said "no upstream attempt succeeded" and left the
        # reader to go and reproduce it.
        for field in ("request_id", "trace_id", "application_id", "detail"):
            value = getattr(record, field, None)
            if value:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(level: str) -> None:
    """Point the root logger at stdout with the JSON formatter.

    stdout rather than a file: the process is supervised by something that owns
    log placement and rotation, and a service that writes its own files fights
    whatever is running it.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter())
    # On the handler rather than on a logger: a filter attached to one logger
    # never sees another's records, and the lines worth correlating during an
    # incident include uvicorn's, which this package does not own.
    handler.addFilter(CorrelationFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def new_request_id() -> str:
    """A fresh request ID for a caller that did not supply one.

    Hex rather than a full UUID string: it appears in every log line and every
    error body, and 32 characters is already long enough to be unique here.
    """
    return uuid.uuid4().hex


def redact(values: dict[str, Any]) -> dict[str, Any]:
    """Blank anything in REDACTED_KEYS before it reaches a log or an event.

    Redacts at the producer, which is where the runbook §9 puts the obligation:
    a collector cannot un-leak a secret it has already received.
    """
    return {
        key: ("[redacted]" if key.lower() in REDACTED_KEYS else value)
        for key, value in values.items()
    }


# W3C `traceparent`: version-traceid-parentid-flags, all lowercase hex.
# https://www.w3.org/TR/trace-context/
_TRACEPARENT = re.compile(r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def trace_id_from(traceparent: str) -> str:
    """The 32-hex trace id inside a `traceparent`, or "" if there is not one.

    **All three services stored the whole header as `trace_id`**, which cannot
    correlate anything: the header's third field is a *per-span* parent id, so
    two spans in one trace carry two different headers and matching on the
    string finds neither. §11.2's entire premise is that events from different
    services join on this value.

    An all-zero trace id or parent id is invalid per the specification and is
    refused rather than propagated — a zero id would join every malformed trace
    into one, which is worse than having none.

    Unknown versions are accepted for their trace id. The spec says a receiver
    must not reject a higher version outright, and the first two fields are
    fixed for every version defined so far; refusing them would make NERVIS the
    reason a newer client's traces vanished.
    """
    match = _TRACEPARENT.match((traceparent or "").strip().lower())
    if not match:
        return ""
    _, trace_id, parent_id, _ = match.groups()
    if trace_id == "0" * 32 or parent_id == "0" * 16:
        return ""
    return trace_id


def new_traceparent(trace_id: str = "", span_id: str = "") -> str:
    """A `traceparent` to send onward, continuing a trace or starting one.

    Generated rather than forwarded unchanged, because forwarding makes the
    receiver's parent the sender's parent — every service ends up a sibling and
    the waterfall §11.2 asks for has no shape.
    """
    trace = trace_id if len(trace_id) == 32 else uuid.uuid4().hex
    span = span_id if len(span_id) == 16 else uuid.uuid4().hex[:16]
    return f"00-{trace}-{span}-01"
