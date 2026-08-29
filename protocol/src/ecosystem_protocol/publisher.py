"""Publishing events to NERVIS without ever depending on NERVIS.

Runbook Stage 7's exit has three clauses and this file exists for the second:
*"collector outage leaves every product healthy"*. RAVIS routes requests and
SIRVIS runs benchmarks; neither may slow down, fail, or hold a request open
because a dashboard is not running. SIRVIS.md §928 says it in its own words —
telemetry export is *"optional, bounded, redacted, and never blocks a
benchmark"*.

So the shape is decided by what must not happen:

**`emit` is synchronous and returns immediately.** Not `async`, deliberately: an
`await` on the request path is a place where a slow collector becomes a slow
product, and the only way to be sure nobody adds one is for there to be nothing
to await. It appends to a bounded buffer and returns.

**The buffer is bounded and drops the oldest.** An unbounded queue turns a
collector outage into a memory leak, which is the outage taking the product down
by a slower route. Dropping the *oldest* rather than refusing the newest keeps
the most recent history, which is the half a person looking at a live problem
wants.

**A drop is counted and reportable.** Silence is the wrong answer to a failure
in this ecosystem, and telemetry that quietly stops is worse than none: it looks
exactly like a quiet system. `snapshot()` is what a service publishes so an
operator can see that publishing is failing.

**Nothing here raises.** `emit` is called from inside functions already at the
complexity limit, where wrapping each call in `try/except` is not available. The
contract is that a call site is one unconditional line and every failure is
swallowed here.

**Disabled is a first-class state.** With no NERVIS configured, `emit` is a
no-op that costs an attribute lookup. A service must run with no collector at
all, and that is the ordinary case for somebody running RAVIS on its own.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any, Mapping, Protocol

from ecosystem_protocol.events import envelope

LOG = logging.getLogger("ecosystem.publisher")

# How many events wait for a collector that is not answering. Two hundred and
# fifty-six matches the hub's own subscriber buffer, for the same reason it
# picked that number: enough to ride out a restart, not enough to matter if it
# is never drained.
DEFAULT_BUFFER = 256

# How many go in one request. The hub accepts a list precisely so a producer
# reconnecting after an outage does not turn one outage into a thundering herd.
BATCH = 50

# How long a publish may take before it is abandoned. Short, because nothing is
# waiting for it and a slow collector must not keep a connection alive across a
# whole drain cycle.
TIMEOUT_SECONDS = 2.0


class _Poster(Protocol):
    """The one thing this needs from an HTTP client.

    A Protocol rather than `httpx.AsyncClient`, so the shared package keeps its
    single runtime dependency. Each service already builds and owns clients with
    its own limits and timeouts; this borrows one rather than opening its own
    connection pool beside it.
    """

    async def post(self, url: str, *, json: Any, timeout: float) -> Any: ...


class EventPublisher:
    """A service's outbound event stream, or a no-op when there is no collector."""

    def __init__(
        self,
        *,
        service_type: str,
        service_id: str = "",
        instance_id: str = "",
        machine_id: str = "",
        base_url: str = "",
        buffer: int = DEFAULT_BUFFER,
    ) -> None:
        self.service_type = service_type
        self.service_id = service_id
        self.instance_id = instance_id
        self.machine_id = machine_id
        self._base_url = base_url.rstrip("/")
        self._pending: deque[dict[str, Any]] = deque(maxlen=buffer)
        self._published = 0
        self._dropped = 0
        self._failures = 0
        self._last_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)

    def emit(
        self,
        event_type: str,
        *,
        trace_id: str,
        severity: str = "info",
        data: Mapping[str, Any] | None = None,
        event_id: str = "",
    ) -> None:
        """Queue one event. Never blocks, never raises, never waits.

        **A missing trace_id skips the emit rather than sending an empty one.**
        NERVIS accepts an event with no trace, stores it, counts it against
        retention — and `summarise` then drops it, so it is invisible. Storing
        something nobody can ever see is the worst of the three outcomes, and it
        is what an empty-string default would produce every time a caller
        arrived without a `traceparent`.
        """
        if not self.enabled or not trace_id:
            return
        try:
            built = envelope(
                event_type=event_type,
                service_type=self.service_type,
                service_id=self.service_id,
                instance_id=self.instance_id,
                machine_id=self.machine_id,
                trace_id=trace_id,
                severity=severity,
                data=data,
                event_id=event_id,
            )
        except Exception as failure:  # noqa: BLE001 - telemetry may never raise
            self._failures += 1
            self._last_error = f"could not build {event_type}: {failure}"
            return
        # `deque(maxlen=…)` discards from the other end silently, so the count
        # is taken here — a drop nobody counted is a hole in a timeline that
        # nothing on any screen can explain.
        if len(self._pending) == self._pending.maxlen:
            self._dropped += 1
            self._report_drop()
        self._pending.append(built)

    async def flush(self, client: _Poster) -> int:
        """Send what is queued, and return how many were accepted.

        Failures put the batch back at the *front*, so ordering survives an
        outage — a timeline assembled from events that arrived out of order is
        one a reader has to distrust. The buffer's bound still applies, so a
        long outage drops the oldest rather than growing.
        """
        if not self.enabled or not self._pending:
            return 0
        batch = [self._pending.popleft() for _ in range(min(BATCH, len(self._pending)))]
        try:
            response = await client.post(
                f"{self._base_url}/api/v1/events", json=batch, timeout=TIMEOUT_SECONDS
            )
            status = int(getattr(response, "status_code", 0))
            # The hub answers 202 for anything it could read as JSON, and says
            # per-event what it did. A 4xx or 5xx is the hub itself being
            # unhappy, and those are worth retrying; a rejected *envelope* is
            # not, and is not retried because it will be rejected identically
            # forever. That distinction is why this checks the status rather
            # than the body.
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")
        except Exception as failure:  # noqa: BLE001 - telemetry may never raise
            self._failures += 1
            self._last_error = str(failure) or failure.__class__.__name__
            for event in reversed(batch):
                if len(self._pending) == self._pending.maxlen:
                    self._dropped += 1
                    self._report_drop()
                self._pending.appendleft(event)
            return 0
        self._published += len(batch)
        self._last_error = ""
        return len(batch)

    async def run(self, client: _Poster, *, every: float = 2.0) -> None:
        """Drain forever. Cancellation is the ordinary way this ends.

        Every exception other than cancellation is caught and the loop
        continues: a background task that dies takes publishing with it and says
        nothing, which is precisely the silent stop this file is written to
        avoid.
        """
        while True:
            try:
                await asyncio.sleep(every)
                while await self.flush(client):
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the loop outlives its failures
                LOG.debug("event publisher cycle failed", exc_info=True)

    async def drain(self, client: _Poster, *, deadline: float = 3.0) -> int:
        """Send what is left, within a deadline, on the way out.

        For a process that ends — a CLI benchmark, or a service shutting down.
        A fire-and-forget POST issued after the last `await` dies with the
        interpreter, and the event it loses is the *closing* one, which is
        exactly the event that turns a span from a point into an interval.

        Bounded, because a shutdown that hangs on a collector is the outage
        taking the product down at the last possible moment.
        """
        sent = 0
        try:
            async with asyncio.timeout(deadline):
                while self._pending:
                    moved = await self.flush(client)
                    if not moved:
                        break
                    sent += moved
        except (TimeoutError, Exception):  # noqa: BLE001 - never raise on the way out
            LOG.debug("event publisher drain did not finish", exc_info=True)
        return sent

    def _report_drop(self) -> None:
        """Say that events were lost, without making it the product's problem.

        **This was a readiness check, and that was wrong in a way worth
        recording.** Reporting a dropped event through `/ecosystem/health` made
        the check fail, which made `ready` false, which made the service
        advertise itself as degraded — so a dead collector degraded the
        product's published health. That is precisely the coupling Stage 7's
        clause forbids: *collector outage leaves every product healthy*. The
        thing built to prove the clause broke it, and it took killing a
        collector and reading `ready: False` to see it.

        A log line instead. It is a real signal — an operator grepping for why
        the timeline has holes finds it — and it cannot travel back up into the
        service's own health. `snapshot()` remains for anything that wants the
        numbers without an opinion attached.

        Logged once per power of two rather than per drop, so a long outage
        leaves a handful of lines rather than one per event.
        """
        if self._dropped & (self._dropped - 1):
            return
        LOG.warning(
            "event publishing has dropped %d event(s); the collector at %s has "
            "been unreachable long enough to overrun a %d-event buffer (%s)",
            self._dropped, self._base_url, self._pending.maxlen,
            self._last_error or "no detail",
        )

    def snapshot(self) -> dict[str, Any]:
        """What to publish so an operator can see publishing failing.

        Telemetry that stops quietly looks exactly like a quiet system, which is
        the confusion this whole ecosystem is written against. `dropped` is the
        number that matters: it is events that existed and that no timeline will
        ever contain.
        """
        return {
            "enabled": self.enabled,
            "queued": len(self._pending),
            "published": self._published,
            "dropped": self._dropped,
            "failures": self._failures,
            "last_error": self._last_error,
        }
