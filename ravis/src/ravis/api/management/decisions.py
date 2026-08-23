"""A bounded record of recent route decisions.

RAVIS.md §9.7 requires every dynamic route to be explainable, and §15.1 serves
those explanations at `/api/v1/route-decisions`. Serving them means keeping them,
because a decision cannot be recomputed later: re-running the router would use
today's catalogue, today's residency and today's memory, and would happily
produce a *different* answer than the one being asked about. An explanation you
recompute is a guess about the past.

Bounded and in memory. Route decisions are diagnostic rather than business
state — losing them on restart costs a debugging session, whereas persisting
every decision costs disk forever, and §17's storage model does not list them.
Durable decisions arrive if and when something needs them beyond the current
process.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ravis.routing.explain import RouteDecision

# How many decisions to keep. Enough to cover a debugging session — a Clarvis
# agent run is tens of requests — and small enough that the memory cost is
# irrelevant. A person scrolling a dashboard never wants the thousandth.
DEFAULT_CAPACITY = 200


@dataclass
class RecordedDecision:
    """One decision, with the identity and timing a diagnostic needs."""

    decision_id: str
    decided_at: float
    application_id: str
    decision: RouteDecision
    request_id: str = ""
    # What actually happened when the decision was executed: the targets tried,
    # in order, and how each one ended (§10's fallback chain). Written after the
    # response completes, which for a stream is long after the decision was
    # recorded — hence a mutable field rather than a constructor argument.
    #
    # `None` means the request has not finished yet, and is distinct from an
    # empty attempt list, which would mean it finished without trying anything
    # (runbook §14.4). A dashboard showing a route decision mid-stream is the
    # normal case, not an error.
    attempts: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """The published shape.

        `RouteDecision.as_dict` is deliberately reused rather than reformatted
        here: the explanation a person reads and the explanation an API returns
        must be the same object, or they drift and one of them starts lying.
        """
        body = self.decision.as_dict()
        body.update(
            {
                "decision_id": self.decision_id,
                "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.decided_at)),
                "application_id": self.application_id,
                "request_id": self.request_id,
                "execution": self.attempts,
            }
        )
        return body


@dataclass
class DecisionLog:
    """The most recent decisions, newest first."""

    capacity: int = DEFAULT_CAPACITY
    _entries: deque[RecordedDecision] = field(default_factory=deque)

    def record(
        self, decision: RouteDecision, application_id: str, request_id: str
    ) -> RecordedDecision:
        """Store a decision and return it with its assigned identity.

        A command that also returns — the one place §14.2's command/query split
        is worth bending, because the caller needs the generated ID to correlate
        the decision with the response it is about, and a second lookup to fetch
        what was just written would be worse.
        """
        if not self._entries.maxlen:
            self._entries = deque(self._entries, maxlen=self.capacity)
        recorded = RecordedDecision(
            decision_id=uuid.uuid4().hex[:12],
            decided_at=time.time(),
            application_id=application_id,
            decision=decision,
            request_id=request_id,
        )
        self._entries.appendleft(recorded)
        return recorded

    def recent(self, limit: int) -> list[RecordedDecision]:
        """The newest decisions, most recent first."""
        return list(self._entries)[:limit]

    def find(self, decision_id: str) -> RecordedDecision | None:
        """One decision by ID, or `None` when it has aged out.

        `None` rather than an exception: a decision falling out of a bounded log
        is ordinary, and the caller turns it into a 404 that says so.
        """
        return next(
            (entry for entry in self._entries if entry.decision_id == decision_id), None
        )
