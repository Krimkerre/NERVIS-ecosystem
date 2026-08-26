"""The event hub (§11.1): what arrives, what is kept, and what is refused.

**Malformed events are quarantined, never crash the hub.** §11.1 says it and
M6's exit repeats it, and it shapes every function here: nothing raises on bad
input from a producer. A producer sending rubbish is an ordinary thing for a
hub to survive, and the alternative — a 500 that takes the ingestion path down —
punishes every other producer for one's mistake.

Quarantined rather than dropped, because *"the hub is quiet"* and *"a producer
is sending rubbish"* look identical from outside and need different things done
about them.

**The hub is operational telemetry, not the system of record.** §11.1 is
explicit. Producers stay authoritative for their own facts, so an envelope is
stored whole and the columns beside it exist only to filter on. Re-serialising
an event from those columns would be NERVIS publishing its own version of
somebody else's fact.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from nervis.storage import Database

# §4.4's required fields. Everything else in the envelope is optional and is
# preserved untouched — §4.2 says consumers ignore unknown optional fields, and
# a hub that dropped them would make itself lossy for every future event type.
REQUIRED = ("event_id", "event_type", "occurred_at")

# What a subscriber may fall behind by before it is cut loose. §11.1 requires
# bounded buffers; the runbook's §4.1 says to emit `ecosystem.stream.gap` where
# possible and then close. A slow reader must never become back-pressure on a
# producer.
SUBSCRIBER_BUFFER = 256

SEVERITIES = ("debug", "info", "warning", "error", "critical")


@dataclass(frozen=True)
class Rejected:
    """Why one payload did not become an event."""

    reason: str
    detail: str = ""


def validate(payload: Any) -> Rejected | dict[str, Any]:
    """One payload, as an envelope or as the reason it is not one.

    Returns rather than raises. Every caller is on a path where a producer's
    mistake must not become NERVIS's failure.

    The checks are §4.4's and stop there. A hub that validated `data` against a
    per-type schema would need to know every event type in the ecosystem, which
    is the coupling the envelope exists to avoid — *"event type plus version
    determines the `data` schema"*, and that is the consumer's business.
    """
    if not isinstance(payload, Mapping):
        return Rejected("not an object", type(payload).__name__)
    missing = [name for name in REQUIRED if not str(payload.get(name) or "").strip()]
    if missing:
        return Rejected("missing required field(s)", ", ".join(missing))
    if not isinstance(payload.get("data", {}), Mapping):
        return Rejected("data is not an object", type(payload.get("data")).__name__)
    source = payload.get("source") or {}
    if not isinstance(source, Mapping):
        return Rejected("source is not an object", type(source).__name__)
    severity = str(payload.get("severity") or "info")
    if severity not in SEVERITIES:
        return Rejected("unknown severity", severity)
    try:
        # `allow_nan=False`, because the default permits `NaN` and `Infinity`
        # — which Python round-trips happily and which no other JSON parser in
        # the ecosystem will accept. An event that only NERVIS can read is not
        # an event.
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError) as failure:
        # Reachable from an ingestion path that did not come through JSON —
        # nothing today, but the cost of checking is one try block and the cost
        # of not checking is an exception at the INSERT, inside a transaction.
        return Rejected("not serialisable", str(failure))
    return dict(payload)


def _shape_of(payload: Any) -> str:
    """What a refused payload looked like, with nothing that was in it.

    Key names, a type, a length and a digest. Names are structure and are worth
    keeping — *"it had `event_type` but no `occurred_at`"* is the diagnostic.
    Values are the producer's business and are never NERVIS's to store.
    """
    digest = hashlib.sha256(repr(payload).encode("utf-8", "replace")).hexdigest()[:16]
    if isinstance(payload, Mapping):
        keys = ", ".join(sorted(str(k) for k in payload)[:20]) or "(no keys)"
        return f"object with keys: {keys} · sha256:{digest}"
    if isinstance(payload, list):
        return f"list of {len(payload)} · sha256:{digest}"
    return f"{type(payload).__name__} of length {len(repr(payload))} · sha256:{digest}"


class Hub:
    """Ingestion, storage, retention and fan-out for one NERVIS.

    In-process: §11.1 permits HTTP POST, SSE subscription or WebSocket ingestion,
    and NERVIS is a single process serving a single dashboard. A broker would be
    a dependency and an operational surface bought for nothing.
    """

    def __init__(
        self,
        database: Database,
        *,
        retention_days: float = 14.0,
        retention_events: int = 50_000,
    ) -> None:
        self._database = database
        self._retention_days = retention_days
        self._retention_events = retention_events
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    # ── Ingestion ───────────────────────────────────────────────────────────

    def ingest(self, payload: Any) -> Rejected | dict[str, Any]:
        """One payload in. Never raises, whatever a producer sends.

        A duplicate `event_id` is accepted and stored once — §4.4 says consumers
        tolerate duplicates, and a producer replaying after a reconnect is doing
        the right thing rather than making a mistake. It is **not** re-broadcast,
        because a subscriber that already saw it does not need it twice.
        """
        checked = validate(payload)
        if isinstance(checked, Rejected):
            self._quarantine(payload, checked)
            return checked
        sequence = self._store(checked)
        if sequence:
            # **The sequence travels with the broadcast.** It used to be added
            # only by `_view`, on the way out of storage — so a live frame went
            # out with an empty `id:`, and a client that reconnected after
            # receiving one had no cursor for it and silently replayed from
            # wherever its last *stored* read had left off.
            checked["_sequence"] = sequence
            self._broadcast(checked)
        return checked

    def _store(self, event: Mapping[str, Any]) -> int:
        """Write one event and return its sequence. Zero when it was already here."""
        source = event.get("source") or {}
        with self._database.connection as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO event (event_id, event_type, service_type, severity,
                                             trace_id, request_id, session_id, occurred_at,
                                             envelope)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event["event_id"]),
                    str(event["event_type"]),
                    str(source.get("service_type") or "") if isinstance(source, Mapping) else "",
                    str(event.get("severity") or "info"),
                    str(event.get("trace_id") or ""),
                    str(event.get("request_id") or ""),
                    str(event.get("session_id") or ""),
                    str(event["occurred_at"]),
                    json.dumps(event, allow_nan=False),
                ),
            )
        return int(cursor.lastrowid or 0) if cursor.rowcount > 0 else 0

    def _quarantine(self, payload: Any, rejected: Rejected) -> None:
        """Keep the *shape* of what was refused, never its values.

        This stored `repr(payload)[:4000]` — the whole thing — and the
        quarantine is readable over HTTP. The validator refuses on envelope
        shape alone, so a well-formed secret in a malformed envelope was stored
        verbatim and served: a producer that posts a prompt, a file excerpt or a
        token under the wrong `event_type` had it kept and shown. Truncation is
        not a mitigation, because the interesting part of a leaked value is
        rarely past four thousand characters.

        A digest answers the question quarantine exists for — *which producer is
        sending what shape of rubbish* — without carrying anything worth
        stealing. §11.1 asks for "safe diagnostics", and this is what makes them
        safe rather than merely bounded.
        """
        with self._database.connection as connection:
            connection.execute(
                "INSERT INTO event_quarantine (reason, detail, payload) VALUES (?, ?, ?)",
                (rejected.reason, rejected.detail, _shape_of(payload)),
            )

    def emit(
        self,
        event_type: str,
        *,
        severity: str = "info",
        data: Mapping[str, Any] | None = None,
        subject: Mapping[str, Any] | None = None,
        trace_id: str = "",
        request_id: str = "",
    ) -> Rejected | dict[str, Any]:
        """NERVIS's own event, in the same envelope everyone else's arrives in.

        §3.1 has NERVIS implementing *and* consuming the MEP, and a hub whose own
        events took a private path would be the one producer nobody could
        validate. That is also why this can return a `Rejected`: NERVIS's own
        events go through the same door and are refused by the same rules.
        """
        return self.ingest({
            "event_id": uuid.uuid4().hex,
            "event_type": event_type,
            "event_version": "1.0.0",
            "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "source": {"service_type": "nervis"},
            "subject": dict(subject or {}),
            # Without these a NERVIS event joins no trace, and §11.2's waterfall
            # draws the callee's lane alone — which is the one shape it exists
            # to improve on.
            "trace_id": trace_id,
            "request_id": request_id,
            "severity": severity,
            "data": dict(data or {}),
            "privacy": {"classification": "operational", "redactions": []},
        })

    # ── Reading ─────────────────────────────────────────────────────────────

    def query(
        self,
        *,
        after: int = 0,
        limit: int = 100,
        service: str = "",
        severity: str = "",
        event_type: str = "",
        trace_id: str = "",
        text: str = "",
    ) -> list[dict[str, Any]]:
        """§11.2's filters, oldest first.

        Oldest first because this doubles as replay: a client resuming from a
        cursor wants what it missed in the order it happened, and reversing at
        the caller is easier than reconstructing an order that was thrown away.

        `text` is a substring match over the stored envelope. Crude, and honest
        about it — §11.2 asks for free text, and anything cleverer would need an
        index this table has no reason to carry.
        """
        clauses = ["sequence > ?"]
        values: list[Any] = [after]
        for column, value in (
            ("service_type", service), ("severity", severity),
            ("event_type", event_type), ("trace_id", trace_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        if text:
            clauses.append("envelope LIKE ?")
            values.append(f"%{text}%")
        values.append(max(1, min(limit, 1000)))
        rows = self._database.connection.execute(
            f"SELECT sequence, envelope, received_at FROM event "  # noqa: S608 - names are literals
            f"WHERE {' AND '.join(clauses)} ORDER BY sequence LIMIT ?",
            values,
        )
        return [self._view(row) for row in rows]

    def quarantined(self, limit: int = 50) -> list[dict[str, Any]]:
        """What was refused and why — the diagnostic §11.1 asks quarantine for."""
        rows = self._database.connection.execute(
            "SELECT reason, detail, payload, received_at FROM event_quarantine "
            "ORDER BY quarantine_id DESC LIMIT ?",
            (max(1, min(limit, 500)),),
        )
        return [dict(row) for row in rows]

    def latest_sequence(self) -> int:
        row = self._database.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS n FROM event"
        ).fetchone()
        return int(row["n"])

    @staticmethod
    def _view(row: Any) -> dict[str, Any]:
        """One stored event, with the two facts the store adds.

        `received_at` travels beside `occurred_at` rather than replacing it:
        §11.2 requires clock skew to be visible, and one timestamp cannot show
        it.
        """
        envelope: dict[str, Any] = json.loads(row["envelope"])
        envelope["_sequence"] = row["sequence"]
        envelope["_received_at"] = row["received_at"]
        return envelope

    # ── Fan-out ─────────────────────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_BUFFER)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def _broadcast(self, event: Mapping[str, Any]) -> None:
        """Hand one event to every subscriber, dropping none of the producer's time.

        A full queue means a reader that has fallen behind. It gets a gap marker
        and is then left to notice — §11.1 bounds buffers precisely so a slow
        subscriber cannot become back-pressure on a producer, and a hub that
        blocked here would let one stalled browser tab stop the ecosystem's
        telemetry.
        """
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(dict(event))
            except asyncio.QueueFull:
                self._subscribers.discard(queue)
                # Room for the marker, taken from the oldest thing the reader
                # has not got to. The first version put the gap into the queue
                # that had just refused an event, so it was silently dropped and
                # the subscriber was cut loose with no explanation — which is
                # the one thing §4.1 asks a gap to prevent.
                with contextlib.suppress(asyncio.QueueEmpty, asyncio.QueueFull):
                    queue.get_nowait()
                    queue.put_nowait({
                        "event_type": "ecosystem.stream.gap",
                        "severity": "warning",
                        "data": {"reason": "subscriber fell behind and was disconnected"},
                    })

    # ── Retention ───────────────────────────────────────────────────────────

    def enforce_retention(self, *, now: datetime | None = None) -> int:
        """Drop what is past either bound, and report how much.

        Two bounds because they fail differently: age alone lets a burst fill a
        disk inside the window, and a count alone keeps a quiet week forever.
        §11.1 asks for bounded and configurable, and *"high-volume raw logs must
        not grow forever"*.

        Quarantine is bounded by the same count, because a producer emitting
        malformed events emits them at exactly the rate it emits good ones.
        """
        moment = now or datetime.now(timezone.utc)
        cutoff = moment.timestamp() - self._retention_days * 86_400
        stamp = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with self._database.connection as connection:
            aged = connection.execute("DELETE FROM event WHERE received_at < ?", (stamp,))
            excess = connection.execute(
                "DELETE FROM event WHERE sequence <= "
                "(SELECT COALESCE(MAX(sequence), 0) - ? FROM event)",
                (self._retention_events,),
            )
            connection.execute(
                "DELETE FROM event_quarantine WHERE quarantine_id <= "
                "(SELECT COALESCE(MAX(quarantine_id), 0) - ? FROM event_quarantine)",
                (self._retention_events,),
            )
        return int(aged.rowcount or 0) + int(excess.rowcount or 0)


def sse_frame(event: Mapping[str, Any]) -> bytes:
    """One event as an SSE frame, with the cursor a reconnect resumes from.

    `id:` carries the sequence rather than the `event_id`, because that is what
    `Last-Event-ID` is compared against on the way back in. §4.1 requires the
    `id`, `event` and one JSON `data` line.
    """
    cursor = event.get("_sequence", "")
    return (
        f"id: {cursor}\n"
        f"event: {event.get('event_type', 'message')}\n"
        f"data: {json.dumps(event)}\n\n"
    ).encode()


def heartbeat() -> bytes:
    """§4.1: a comment heartbeat at least every 15 seconds.

    A comment rather than an event, so nothing downstream has to filter keepalives
    out of a feed of facts.
    """
    return b": keepalive\n\n"


def replay_from(header: str, events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a reconnecting subscriber missed, given its `Last-Event-ID`."""
    try:
        after = int(header)
    except (TypeError, ValueError):
        return []
    return [event for event in events if int(event.get("_sequence", 0)) > after]
