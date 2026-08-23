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

import json
import logging
import sys
import uuid
from typing import Any

# Fields that must never be written to a log, in any product (runbook §9).
# Listed here rather than remembered: a redaction rule that lives only in a
# reviewer's head is a rule that eventually ships broken.
REDACTED_KEYS = frozenset(
    {"authorization", "api_key", "credential", "token", "prompt", "messages", "content"}
)


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
