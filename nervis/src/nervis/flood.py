"""The flood guard: one service cannot fill the event hub (§11.1).

**Why it exists.** On 14 September 2026 a RAVIS 0.26.0 bug flipped Codex between
"ready" and "switching skills" about a hundred times a second, peaking at 274, and
every flip was a real state change, so RAVIS published every one. The hub keeps at
most 50,000 events, and in eight minutes the loop had filled that and pushed out the
whole history from 4 to 14 September; nine other events were left. **The consecutive
events were not identical** — they alternated between two states — so a guard that
only merged identical repeats would have stored every one of them.

**Three rules, each against a different part of that night.**

1. *Identical repeats are stored once, with a count.* An event whose source, type,
   subject, severity and data match the newest stored event of its type from that
   source, within `collapse_seconds` of the last sighting, adds one to that row's
   count instead of becoming a row. Trace ids — and, for an event in a trace, its
   time — are part of the match while a source is behaving: measured on the
   4 September backup, the events a 60-second window would otherwise merge were
   distinct chat turns and SIRVIS recommendations, each in its own trace, and
   merging those would drop a span from a waterfall. While a source is over its rate
   they are ignored, because every flip of the loop carried a fresh trace id and a
   match on it would merge nothing.
2. *A source has a burst allowance and a sustained rate.* Every arrival spends a
   token; `burst` of them are available at once and they come back at `per_minute`.
   Past that, events are held back — and **of each type only the newest is kept
   waiting**, so a burst of alternating states costs one row, not thousands.
3. *A source has a daily share of the store.* `daily_rows` a day, whatever its rate.
   That is the rule that protects the history: at 5% of the retention cap, a source
   sending at its full allowance for all fourteen days holds 70% of the store, so it
   cannot on its own push a quiet service's events out before their fourteen days
   are up.

**The latest state is never dropped.** When a held type has been quiet for
`quiet_seconds`, its newest event is stored — so when a burst ends the dashboard and
the alarms read the state it ended on, not the one it happened to be sampled at. Those
final events may run past the daily share by at most one burst's worth, which is what
keeps a pathological burst-pause-burst producer bounded too. Shutting down stores
whatever is still held, regardless.

**When the guard engages and releases, it says so once** — a
`nervis.events.flood_guarded` event each way, with the source, the reason, and on
release how much was held back. Those are written by the hub directly, never through
this class, so they cannot spend a source's allowance or engage a guard themselves.

**Producers see nothing.** This decides what is *stored*; the route still answers
202 and counts a held event as accepted, because the publisher retries on a refusal
and a refusal here would make a looping producer loop harder.

Pure state, no I/O. The hub asks `admit` what to do with an event, does the writing,
and tells `stored` what it wrote — so every rule here is testable with a fake clock.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nervis.config import Settings

# The guard's own event. Named once, because the hub writes it, the read route
# filters on it and the dashboard recognises it.
GUARD_EVENT = "nervis.events.flood_guarded"

# The window the daily share is counted over. A day because the measured traffic is
# easiest to reason about by the day — the busiest real source stored about 350.
SHARE_WINDOW_SECONDS = 86_400.0

# How long a source must be silent before the guard forgets it. Its allowance is
# full again long before this, and a forgotten source's share is re-read from the
# store when it next sends, so forgetting loses nothing but memory.
FORGET_AFTER_SECONDS = 3_600.0

# Fields that differ on every occurrence of the same fact.
PER_OCCURRENCE = ("event_id", "occurred_at")
# Fields that tie an event to one request. Part of the match unless the source is
# flooding — see rule 1 above.
PER_TRACE = ("trace_id", "span_id", "request_id", "session_id")

# How many event types a guard event names. A producer inventing a type per event
# would otherwise make the guard's own event as large as the flood.
MAX_TYPES_REPORTED = 20


@dataclass(frozen=True)
class GuardLimits:
    """The numbers, with the defaults `config.py` justifies against measured rates."""

    burst: int = 120
    per_minute: float = 12.0
    daily_rows: int = 2_500
    collapse_seconds: float = 60.0
    quiet_seconds: float = 5.0
    release_seconds: float = 300.0


def limits_from(settings: Settings) -> GuardLimits:
    """The guard's limits from NERVIS's settings.

    The daily share is a fraction of the retention cap rather than a count, so an
    operator who shrinks the store shrinks what one source may take of it.
    """
    return GuardLimits(
        burst=max(1, settings.event_source_burst),
        per_minute=max(0.0, settings.event_source_per_minute),
        daily_rows=max(1, int(settings.event_retention_count * settings.event_source_daily_share)),
        collapse_seconds=settings.event_collapse_seconds,
        quiet_seconds=settings.event_guard_quiet_seconds,
        release_seconds=settings.event_guard_release_seconds,
    )


def source_of(event: Mapping[str, Any]) -> tuple[str, str]:
    """Which source an event counts against: its service type and service id.

    Not the instance: every Clarvis window shares one service id, and a guard keyed
    on the window would let one service with many windows take many shares.
    """
    source = event.get("source")
    if not isinstance(source, Mapping):
        return ("", "")
    return (str(source.get("service_type") or ""), str(source.get("service_id") or ""))


def fingerprint(event: Mapping[str, Any], *, trace: bool) -> str:
    """What makes two events the same fact, as a digest.

    Everything the producer sent except the per-occurrence fields — and the trace
    ids too when `trace` is false. Fields the hub adds (`_sequence` and friends)
    are never part of it.

    **When `trace` is true and the event belongs to a trace, its time counts too.**
    An event in a trace is a point on §11.2's waterfall, and a service's bar runs
    from its first event to its last; merging two that differ only in when they
    happened shrank that bar to a point, which is how the traces suite caught it.
    """
    if not trace:
        ignored: tuple[str, ...] = PER_OCCURRENCE + PER_TRACE
    elif event.get("trace_id"):
        ignored = ("event_id",)
    else:
        ignored = PER_OCCURRENCE
    kept = {k: v for k, v in event.items() if k not in ignored and not str(k).startswith("_")}
    return hashlib.sha256(json.dumps(kept, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class Count:
    """A repeat count to write onto a row that is already stored."""

    sequence: int
    repeats: int
    last_seen: str


@dataclass(frozen=True)
class Notice:
    """A guard event for the hub to store: engaged or released, for one source."""

    severity: str
    service_type: str
    data: dict[str, Any]


@dataclass(frozen=True)
class Admission:
    """What to do with one arriving event."""

    store: bool
    count: Count | None = None
    notice: Notice | None = None


@dataclass
class Settlement:
    """What a settle pass found to write: counts, final states and guard events."""

    counts: list[Count] = field(default_factory=list)
    finals: list[dict[str, Any]] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)


@dataclass
class _Latest:
    """The newest stored event of one type from one source, for matching repeats."""

    sequence: int
    exact: str
    loose: str
    seen: float
    seen_at: str
    repeats: int = 1
    # A repeat counted while the source was guarded and not yet written. Written
    # on the next settle rather than per event, so a flood of identical events is
    # not a flood of writes either.
    unwritten: bool = False


@dataclass
class _Episode:
    """One stretch of a source being guarded."""

    reason: str
    since: str
    last_held: float
    collapsed: int = 0
    thinned: int = 0
    types: dict[str, int] = field(default_factory=dict)

    def held_back(self, kind: str, *, collapsed: bool) -> None:
        """One more event that will never be its own row."""
        if collapsed:
            self.collapsed += 1
        else:
            self.thinned += 1
        self.types[kind] = self.types.get(kind, 0) + 1


@dataclass
class _Source:
    """Everything the guard remembers about one source."""

    service_type: str
    service_id: str
    tokens: float
    refilled: float
    window_start: float
    window_rows: int
    last_arrival: float
    latest: dict[str, _Latest] = field(default_factory=dict)
    # The newest held-back event of each type, and when it arrived.
    held: dict[str, tuple[dict[str, Any], float]] = field(default_factory=dict)
    episode: _Episode | None = None

    def who(self) -> dict[str, str]:
        return {"service_type": self.service_type, "service_id": self.service_id}

    def label(self) -> str:
        name = self.service_type or "a service with no name"
        return f"{name} ({self.service_id})" if self.service_id else name


class FloodGuard:
    """Per-source allowances, repeat matching and held-back state for one hub.

    `stored_recently(service_type, service_id)` counts what the store already holds
    from a source in the last day. It is asked once, when a source is first seen,
    so a restart in the middle of a flood does not hand the source a fresh share.
    """

    def __init__(
        self, limits: GuardLimits, stored_recently: Callable[[str, str], int]
    ) -> None:
        self.limits = limits
        self._stored_recently = stored_recently
        self._sources: dict[tuple[str, str], _Source] = {}

    # ── Arrivals ────────────────────────────────────────────────────────────

    def admit(self, event: Mapping[str, Any], now: float, wall: str) -> Admission:
        """Store this event, count it onto a stored row, or hold it back."""
        source = self._source(event, now)
        source.last_arrival = now
        kind = str(event.get("event_type") or "")
        over = self._spend(source, now)
        notice = None
        if over and source.episode is None:
            notice = self._engage(source, over, now, wall)
        latest = self._repeated(source, kind, event, now, wall)
        if latest is not None:
            written = None if latest.unwritten else Count(latest.sequence, latest.repeats, wall)
            return Admission(store=False, count=written, notice=notice)
        if not over:
            self._supersede(source, kind)
            return Admission(store=True, notice=notice)
        self._hold(source, kind, event, now)
        return Admission(store=False, notice=notice)

    def stored(
        self, event: Mapping[str, Any], sequence: int, now: float, wall: str
    ) -> Count | None:
        """Record a row the hub has just written, and return a count it displaced.

        A repeat count still waiting to be written belongs to the row being
        replaced as the newest of its type, so it is handed back to be written now
        rather than lost with the entry.
        """
        source = self._source(event, now)
        kind = str(event.get("event_type") or "")
        source.window_rows += 1
        displaced = source.latest.get(kind)
        source.latest[kind] = _Latest(
            sequence, fingerprint(event, trace=True), fingerprint(event, trace=False), now, wall
        )
        if displaced is not None and displaced.unwritten:
            return Count(displaced.sequence, displaced.repeats, displaced.seen_at)
        return None

    def _source(self, event: Mapping[str, Any], now: float) -> _Source:
        key = source_of(event)
        source = self._sources.get(key)
        if source is None:
            source = _Source(
                service_type=key[0], service_id=key[1], tokens=float(self.limits.burst),
                refilled=now, window_start=now, window_rows=self._stored_recently(*key),
                last_arrival=now,
            )
            self._sources[key] = source
        elif now - source.window_start >= SHARE_WINDOW_SECONDS:
            source.window_start, source.window_rows = now, 0
        return source

    def _spend(self, source: _Source, now: float) -> str:
        """Spend one arrival's token, or say which allowance this source is over."""
        earned = max(0.0, now - source.refilled) * self.limits.per_minute / 60.0
        source.tokens = min(float(self.limits.burst), source.tokens + earned)
        source.refilled = now
        if source.window_rows >= self.limits.daily_rows:
            return "daily_share"
        if source.tokens < 1.0:
            return "rate"
        source.tokens -= 1.0
        return ""

    def _repeated(
        self, source: _Source, kind: str, event: Mapping[str, Any], now: float, wall: str
    ) -> _Latest | None:
        """The stored row this event repeats, with its count moved on, or None."""
        latest = source.latest.get(kind)
        if latest is None or now - latest.seen > self.limits.collapse_seconds:
            return None
        if not self._matches(source, latest, event):
            return None
        latest.repeats += 1
        latest.seen, latest.seen_at = now, wall
        episode = source.episode
        if episode is not None:
            latest.unwritten = True
            episode.last_held = now
            episode.held_back(kind, collapsed=True)
            # The newest state is the stored one again, so a different state held
            # back for this type is no longer anybody's latest.
            if source.held.pop(kind, None) is not None:
                episode.held_back(kind, collapsed=False)
        return latest

    @staticmethod
    def _matches(source: _Source, latest: _Latest, event: Mapping[str, Any]) -> bool:
        if fingerprint(event, trace=True) == latest.exact:
            return True
        return source.episode is not None and fingerprint(event, trace=False) == latest.loose

    def _hold(self, source: _Source, kind: str, event: Mapping[str, Any], now: float) -> None:
        """Keep this event waiting as the newest of its type, replacing any older one."""
        episode = source.episode
        if episode is None:  # `admit` engages before it ever holds
            return
        if kind in source.held:
            episode.held_back(kind, collapsed=False)
        source.held[kind] = (dict(event), now)
        episode.last_held = now

    @staticmethod
    def _supersede(source: _Source, kind: str) -> None:
        """A stored event makes a held one of the same type stale."""
        if source.held.pop(kind, None) is not None and source.episode is not None:
            source.episode.held_back(kind, collapsed=False)

    def _engage(self, source: _Source, reason: str, now: float, wall: str) -> Notice:
        source.episode = _Episode(reason=reason, since=wall, last_held=now)
        why = (
            f"it has already stored {self.limits.daily_rows} events today"
            if reason == "daily_share"
            else f"more than {self.limits.burst} at once or {self.limits.per_minute:g} a minute"
        )
        return Notice("warning", source.service_type, {
            "phase": "engaged", **source.who(), "reason": reason, "since": wall,
            "burst": self.limits.burst, "per_minute": self.limits.per_minute,
            "daily_rows": self.limits.daily_rows,
            "detail": f"{source.label()} is sending events faster than NERVIS keeps them "
                      f"({why}), so repeats are counted instead of stored and the rest are "
                      "held back; the last event of each kind is still kept",
        })

    # ── Settling ────────────────────────────────────────────────────────────

    def settle(self, now: float, wall: str, *, final: bool = False) -> Settlement:
        """Counts to write, final states to store, and guards to release.

        `final` is for shutdown: everything still held is stored and every guard
        is released, whatever the limits say, because an event held in memory
        when the process exits is an event dropped.
        """
        settlement = Settlement()
        for key, source in list(self._sources.items()):
            settlement.counts.extend(self._unwritten(source))
            episode = source.episode
            if episode is None:
                self._forget_if_idle(key, source, now)
                continue
            settlement.finals.extend(self._quiet(source, now, final=final))
            if final or self._calm(source, episode, now):
                settlement.notices.append(self._release(source, episode, wall, final=final))
        return settlement

    @staticmethod
    def _unwritten(source: _Source) -> list[Count]:
        counts = []
        for latest in source.latest.values():
            if latest.unwritten:
                latest.unwritten = False
                counts.append(Count(latest.sequence, latest.repeats, latest.seen_at))
        return counts

    def _quiet(self, source: _Source, now: float, *, final: bool) -> list[dict[str, Any]]:
        """The held events whose type has gone quiet, which are now final states.

        Allowed past the daily share by one burst's worth and no further. Beyond
        that they stay held until the window turns over or NERVIS stops, which
        bounds a producer that bursts, pauses just long enough and bursts again.
        """
        allowance = self.limits.daily_rows + self.limits.burst - source.window_rows
        ready: list[dict[str, Any]] = []
        for kind, (event, arrived) in list(source.held.items()):
            waiting = now - arrived < self.limits.quiet_seconds or len(ready) >= allowance
            if waiting and not final:
                continue
            del source.held[kind]
            ready.append(event)
        return ready

    def _calm(self, source: _Source, episode: _Episode, now: float) -> bool:
        """Whether a guarded source has held nothing back for long enough to release."""
        return not source.held and now - episode.last_held >= self.limits.release_seconds

    @staticmethod
    def _release(source: _Source, episode: _Episode, wall: str, *, final: bool) -> Notice:
        source.episode = None
        held_back = episode.collapsed + episode.thinned
        ending = "NERVIS stopped" if final else "it slowed down"
        return Notice("info", source.service_type, {
            "phase": "released", **source.who(), "reason": episode.reason,
            "since": episode.since, "until": wall, "held_back": held_back,
            "collapsed": episode.collapsed, "thinned": episode.thinned,
            "types": _top(episode.types), "stopped": final,
            "detail": f"NERVIS held back {held_back} events from {source.label()} "
                      f"between {episode.since} and {wall}, until {ending}",
        })

    def _forget_if_idle(self, key: tuple[str, str], source: _Source, now: float) -> None:
        """Drop what can no longer match, and a source silent for an hour."""
        for kind in [k for k, v in source.latest.items()
                     if now - v.seen > self.limits.collapse_seconds]:
            del source.latest[kind]
        if not source.latest and now - source.last_arrival >= FORGET_AFTER_SECONDS:
            del self._sources[key]

    # ── Reading ─────────────────────────────────────────────────────────────

    def report(self) -> list[dict[str, Any]]:
        """Every source guarded right now, with what it has held back so far."""
        active = []
        for source in self._sources.values():
            episode = source.episode
            if episode is None:
                continue
            active.append({
                **source.who(), "reason": episode.reason, "since": episode.since,
                "held_back": episode.collapsed + episode.thinned + len(source.held),
                "types": _top(episode.types),
            })
        return active


def _top(types: Mapping[str, int]) -> dict[str, int]:
    """The most held-back types, at most `MAX_TYPES_REPORTED` of them."""
    ranked = sorted(types.items(), key=lambda item: (-item[1], item[0]))
    return dict(ranked[:MAX_TYPES_REPORTED])
