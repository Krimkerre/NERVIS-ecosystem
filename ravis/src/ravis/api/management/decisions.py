"""A record of route decisions, kept in memory and on disk.

RAVIS.md §9.7 requires every dynamic route to be explainable, and §15.1 serves
those explanations at `/api/v1/route-decisions`. Serving them means keeping them,
because a decision cannot be recomputed later: re-running the router would use
today's catalogue, today's residency and today's memory, and would happily
produce a *different* answer than the one being asked about. An explanation you
recompute is a guess about the past.

**Persisted since 18 September 2026, by the owner's decision.** It was bounded
and in memory, on the reasoning that a route decision is diagnostic rather than
business state — losing one on restart cost a debugging session, and persisting
every one cost disk forever. Two things made that trade go bad. The dashboard
links to a decision by id, and the link rotted after two hundred requests, so a
timeline older than an afternoon pointed at nothing; and §17's storage model
lists `RouteDecision · RouteCandidate · RequestMetric` in as many words, which
made the deviation one that had to be re-argued rather than inherited.

So the shape is `UsageLedger`'s, which went the same way on 12 September for the
same reason: memory stays the working set and is still bounded, the database is
what a restart reads it back from, and a retention window plus a row ceiling
keep the cost of "forever" finite. A record carries no prompt and no completion,
so storing it stores neither (§9.7, runbook §9).

**M11 is where the old design stopped being free.** §12.1 has a session correlate
its route decisions, and the session gate tests restart; a session that outlived
the decisions it points at would correlate to nothing. `RoutingSession` therefore
persists the route facts §12.1 names — pool, model, provider, profile — rather
than holding a reference to a record that used to evaporate. That stays as it is:
it is a stored fact of the session, not a pointer into this table.
"""

from __future__ import annotations

import json
import time
import uuid
import zlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ravis.routing.explain import ExcludedCandidate, RouteDecision
from ravis.storage.database import Database

# How many decisions to keep in memory. Enough to cover a debugging session — a
# Clarvis agent run is tens of requests — and small enough that the memory cost
# is irrelevant. A person scrolling a dashboard never wants the thousandth; a
# reader who does want it asks for more than this and gets it from the database.
DEFAULT_CAPACITY = 200

# How long a stored decision is kept. Shorter than usage's ninety days on
# purpose: a spend record is a handful of numbers, while an explanation carries
# every candidate considered — hundreds of model ids against an OpenRouter
# catalogue — so the same window would cost an order of magnitude more disk for
# an answer nobody asks about a season later.
DECISION_RETENTION_SECONDS = 30 * 24 * 3600.0

# The most rows kept whatever the window says. A retention window bounds an
# ordinary month; it does not bound a runaway loop, and the four-hour soak test
# makes 28,000 requests in a night. The ceiling is what stops a disk filling
# between one look at the dashboard and the next.
#
# **Twenty thousand because it was measured, not because it is round.** Against
# this machine's real catalogue an explanation is 87 KB of text — 538 candidates
# and 537 exclusions, each with its reason — which `zlib` takes to about 7 KB.
# So this ceiling is roughly 140 MB of database, and the first number tried,
# fifty thousand, was several gigabytes before anyone measured a row.
DECISION_ROW_CEILING = 20_000


@dataclass
class RecordedDecision:
    """One decision, with the identity and timing a diagnostic needs."""

    decision_id: str
    decided_at: float
    application_id: str
    decision: RouteDecision
    request_id: str = ""
    # The trace this decision belongs to. Recorded so §11.2's waterfall can put
    # a route beside the events either side of it — a decision that says why a
    # model was chosen but not which request it belonged to is a fact with no
    # neighbours.
    trace_id: str = ""
    # What actually happened when the decision was executed: the targets tried,
    # in order, and how each one ended (§10's fallback chain). Written after the
    # response completes, which for a stream is long after the decision was
    # recorded — hence a mutable field rather than a constructor argument.
    #
    # `None` means the response was never consumed to completion, and is
    # distinct from an empty attempt list, which would mean it finished without
    # trying anything (runbook §14.4). A dashboard showing a route decision
    # mid-stream is the normal case, not an error.
    #
    # A *cancelled* stream is not one of these: it records a `cancelled`
    # attempt, because "the user pressed Stop" and "this is still running" are
    # the two readings a person most needs to tell apart. The one case that
    # genuinely stays `None` is a streaming response the client never read a
    # byte of — the generator's body never runs, so nothing inside it can
    # record anything.
    attempts: dict[str, Any] | None = None
    # §6: "Diagnostics must expose which path ran — `TRANSPARENT_OPENAI` or
    # `TRANSLATED_NATIVE`." When a tool call arrives malformed the first
    # question is whether it went through a translation at all, and that has to
    # be answerable from a trace rather than by reading configuration.
    #
    # Empty until the request is executed, for the same reason `attempts` is
    # `None` until then: a decision recorded mid-stream has not yet run.
    execution_path: str = ""
    # What decided this route, in a shape it can be decided from again: which
    # capabilities the request required and why, the context it estimated, its
    # output cap, whether it was a probe, and the policy in force. Derived facts
    # only — the same ones the explanation already shows in prose, in a form
    # arithmetic can read. Nothing here is content; see `replay.constraints_of`.
    #
    # Empty for a decision recorded before replay existed, and for any caller
    # that does not supply them; replay then routes on the defaults and says so
    # by producing an answer for an unconstrained request rather than inventing
    # constraints it does not have.
    constraints: dict[str, Any] = field(default_factory=dict)
    # How this record reaches the database, set by the log that made it. The two
    # fields above are written by the request as it finishes, long after the row
    # was inserted, and a caller that holds this object has no business knowing
    # where it is stored — so it calls `stored()` and the log does the rest.
    # `None` when nothing is persisting, which is every test that builds one of
    # these by hand.
    save: Callable[[RecordedDecision], None] | None = field(
        default=None, repr=False, compare=False
    )

    def stored(self) -> None:
        """Write the fields filled in after the decision through to the store."""
        if self.save is not None:
            self.save(self)

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
                "trace_id": self.trace_id,
                "execution": self.attempts,
                "execution_path": self.execution_path or None,
            }
        )
        return body


@dataclass
class DecisionLog:
    """The most recent decisions, newest first."""

    capacity: int = DEFAULT_CAPACITY
    database: Database | None = None
    retention_seconds: float = DECISION_RETENTION_SECONDS
    row_ceiling: int = DECISION_ROW_CEILING
    clock: Callable[[], float] = time.time
    _entries: deque[RecordedDecision] = field(default_factory=deque)

    def __post_init__(self) -> None:
        self._entries = deque(self._entries, maxlen=self.capacity)
        if self.database is not None:
            self.enforce_retention()
            self._entries.extend(self._newest_stored(self.capacity))

    def record(
        self, decision: RouteDecision, application_id: str, request_id: str,
        trace_id: str = "", constraints: dict[str, Any] | None = None,
    ) -> RecordedDecision:
        """Store a decision and return it with its assigned identity.

        A command that also returns — the one place §14.2's command/query split
        is worth bending, because the caller needs the generated ID to correlate
        the decision with the response it is about, and a second lookup to fetch
        what was just written would be worse.
        """
        recorded = RecordedDecision(
            decision_id=uuid.uuid4().hex[:12],
            decided_at=self.clock(),
            application_id=application_id,
            decision=decision,
            request_id=request_id,
            trace_id=trace_id,
            constraints=constraints or {},
            save=self._note_execution if self.database is not None else None,
        )
        self._entries.appendleft(recorded)
        if self.database is not None:
            self.database.connection.execute(_INSERT_DECISION, _decision_row(recorded))
        return recorded

    def recent(self, limit: int) -> list[RecordedDecision]:
        """The newest decisions, most recent first.

        Memory answers as far as it reaches, which is every ordinary dashboard
        read; a request for more than memory holds goes to the database, so the
        page of history does not quietly stop where the in-memory bound happens
        to fall. Memory and the table agree, because every write goes to both.
        """
        if self.database is None or limit <= len(self._entries):
            return list(self._entries)[:limit]
        return self._newest_stored(limit)

    def find(self, decision_id: str) -> RecordedDecision | None:
        """One decision by ID, from memory or the database, or `None`.

        `None` rather than an exception: a decision falling outside what is kept
        is ordinary, and the caller turns it into a 404 that says so.
        """
        found = next(
            (entry for entry in self._entries if entry.decision_id == decision_id), None
        )
        if found is not None or self.database is None:
            return found
        rows = self.database.connection.execute(
            "SELECT * FROM route_decision WHERE decision_id = ?", (decision_id,)
        ).fetchall()
        return _from_row(rows[0]) if rows else None

    def enforce_retention(self) -> int:
        """Delete decisions past the window or beyond the ceiling. Returns how many.

        Both bounds, because they answer different failures: the window is what
        keeps a quiet month from accumulating forever, and the ceiling is what
        keeps a busy night from filling a disk before the window comes round.
        """
        if self.database is None:
            return 0
        connection = self.database.connection
        cutoff = self.clock() - self.retention_seconds
        removed = connection.execute(
            "DELETE FROM route_decision WHERE decided_at < ?", (cutoff,)
        ).rowcount
        removed += connection.execute(
            "DELETE FROM route_decision WHERE decision_id IN ("
            "  SELECT decision_id FROM route_decision"
            "  ORDER BY decided_at DESC, rowid DESC LIMIT -1 OFFSET ?"
            ")",
            (self.row_ceiling,),
        ).rowcount
        return max(removed, 0)

    def _newest_stored(self, limit: int) -> list[RecordedDecision]:
        """The newest stored decisions, most recent first."""
        assert self.database is not None
        rows = self.database.connection.execute(
            "SELECT * FROM route_decision ORDER BY decided_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_from_row(row) for row in rows]

    def _note_execution(self, recorded: RecordedDecision) -> None:
        """Write back the two fields a finished request fills in.

        An update of those columns rather than a replace of the row: reads break
        a tie on insertion order, and `INSERT OR REPLACE` deletes and re-inserts,
        which would move a decision to the end of that order every time the
        request it belongs to finished.
        """
        assert self.database is not None
        self.database.connection.execute(
            "UPDATE route_decision SET execution = ?, execution_path = ?"
            " WHERE decision_id = ?",
            (
                None if recorded.attempts is None else json.dumps(recorded.attempts),
                recorded.execution_path,
                recorded.decision_id,
            ),
        )


_INSERT_DECISION = (
    "INSERT OR REPLACE INTO route_decision (decision_id, decided_at, application_id,"
    " request_id, trace_id, requested, pool, selected, reason, explanation, execution,"
    " execution_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _stored_body(recorded: RecordedDecision) -> dict[str, Any]:
    """The explanation as stored: the published shape, plus what it leaves out.

    `as_dict` drops `circuit_open` because a reader of the API does not need to
    know which exclusions lift by themselves. A record read back does: it is the
    difference between "this pool admits nothing" and "this pool is resting", and
    a stored decision that cannot say which is a record of a different decision.

    The constraints ride here too, and stay out of the published shape. They are
    for deciding again rather than for reading — a route explanation is meant to
    be read by a person, and a second copy of what it already says in words would
    make it worse at that.
    """
    body = recorded.decision.as_dict()
    body["resting"] = [
        entry.model for entry in recorded.decision.excluded if entry.circuit_open
    ]
    body["constraints"] = recorded.constraints
    return body


def _decision_row(recorded: RecordedDecision) -> tuple[Any, ...]:
    """One record as a `route_decision` row, in `_INSERT_DECISION`'s column order."""
    decision = recorded.decision
    return (
        recorded.decision_id,
        recorded.decided_at,
        recorded.application_id,
        recorded.request_id,
        recorded.trace_id,
        decision.requested,
        decision.pool_id,
        decision.selected,
        decision.reason,
        _packed(_stored_body(recorded)),
        None if recorded.attempts is None else json.dumps(recorded.attempts),
        recorded.execution_path,
    )


def _packed(body: dict[str, Any]) -> bytes:
    """One explanation as it is stored: deflated JSON.

    Level 6, `zlib`'s default, because the measurement that justified this said
    nothing about the levels either side of it and a number chosen for being
    higher is not a measurement.
    """
    return zlib.compress(json.dumps(body).encode(), 6)


def _unpacked(stored: Any) -> dict[str, Any]:
    """A stored explanation, however it was written.

    Tolerant of plain text, which is what a row written by hand looks like —
    `zlib` is how this writes them, not a claim about how they must arrive.
    """
    raw = zlib.decompress(stored) if isinstance(stored, bytes | bytearray) else stored
    loaded: dict[str, Any] = json.loads(raw)
    return loaded


def _from_row(row: Any) -> RecordedDecision:
    """A stored row as the record it was."""
    body = _unpacked(row["explanation"])
    resting = set(body.get("resting", ()))
    decision = RouteDecision(
        requested=body.get("requested", ""),
        pool_id=body.get("pool"),
        selected=body.get("selected"),
        fallbacks=list(body.get("fallbacks", ())),
        reason=body.get("reason", ""),
        considered=list(body.get("considered", ())),
        excluded=[
            ExcludedCandidate(
                model=entry["model"],
                reasons=list(entry.get("reasons", ())),
                circuit_open=entry["model"] in resting,
            )
            for entry in body.get("excluded", ())
        ],
        requirements=list(body.get("requirements", ())),
        unverified=list(body.get("unverified", ())),
    )
    return RecordedDecision(
        decision_id=row["decision_id"],
        decided_at=row["decided_at"],
        application_id=row["application_id"],
        decision=decision,
        request_id=row["request_id"],
        trace_id=row["trace_id"],
        attempts=None if row["execution"] is None else json.loads(row["execution"]),
        execution_path=row["execution_path"],
        constraints=body.get("constraints", {}),
    )
