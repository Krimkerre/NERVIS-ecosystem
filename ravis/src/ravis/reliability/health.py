"""Provider and model health, and the circuit breakers that read it (§10).

§10 asks for two things that are usually built as one: health *tracking*
(availability, HTTP errors, timeouts, rate limits, TTFT, request latency,
stream interruptions) and circuit *breaking* (`CLOSED`, `OPEN`, `HALF_OPEN` —
do not keep routing to a failing provider). They are one structure here,
because a breaker that reads from a separate health store is two sources of
truth about the same target, and they drift.

The breaker's job is narrow and worth stating plainly: it stops RAVIS sending
requests to something that has already proved it cannot answer them. Without it
a dead provider is discovered again on every single request, and every one of
those requests pays the full timeout before failing.

**Time is injected.** Every state change here is a function of elapsed time, so
a test that had to sleep through a cooldown would either be slow or be lying
about what it exercised. `clock` defaults to `time.monotonic` — monotonic
rather than wall time, because a clock adjustment must not reopen a circuit.

**No locking.** This runs on one asyncio event loop, and none of these methods
await, so no other coroutine can observe a half-applied update. Adding a thread
would break that assumption, which is exactly why it is written down.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable

from ravis.core.capabilities import Capability
from ravis.reliability.failures import FailureClass, HealthScope

# How many recent latencies to keep per target. Enough to average out one slow
# response, small enough that a bounded structure stays bounded. These feed
# diagnostics only — no routing decision reads them yet, and M19's production
# observations are where they acquire weight.
LATENCY_SAMPLES = 32


class BreakerState(Enum):
    """§10's three states, and what each one permits.

    `HALF_OPEN` is the one that matters. Without it, recovery needs either a
    background prober or an operator, and a circuit that only a human can close
    turns a thirty-second outage into a thirty-minute one.
    """

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class TargetHealth:
    """What has happened to one provider or one model, and whether to use it.

    Counters are cumulative for the process lifetime; they answer "has this ever
    misbehaved, and how", which is the question during an incident. The breaker
    state answers "should the next request go here", which is a different
    question with a much shorter memory — hence `consecutive_failures`, which any
    success resets, rather than a ratio over all time. A provider that failed
    ten times an hour ago and has worked since is healthy, and a ratio would
    keep insisting otherwise.
    """

    target: str
    scope: HealthScope
    failure_threshold: int
    cooldown_seconds: float
    clock: Callable[[], float] = time.monotonic

    requests: int = 0
    successes: int = 0
    failures_by_class: dict[str, int] = field(default_factory=dict)
    stream_interruptions: int = 0
    consecutive_failures: int = 0
    opened_at: float | None = None
    last_failure: str = ""
    # Time to first byte, in seconds, for streamed responses. Separate from
    # total latency because they measure different experiences: TTFT is how long
    # the user stares at nothing, and it is the number §10 names first.
    ttft_samples: deque[float] = field(default_factory=lambda: deque(maxlen=LATENCY_SAMPLES))
    latency_samples: deque[float] = field(default_factory=lambda: deque(maxlen=LATENCY_SAMPLES))
    # Set while a HALF_OPEN probe is in flight, so a burst of concurrent
    # requests sends exactly one probe rather than all of them at a target that
    # is still presumed broken.
    probing: bool = False

    @property
    def state(self) -> BreakerState:
        """The breaker's state *now*, with the cooldown applied on read.

        Computed rather than stored, because the OPEN → HALF_OPEN transition is
        caused by time passing and nothing else. A stored state would need
        something to notice the cooldown expired — a timer, or a sweep — and
        both are machinery for a fact that can simply be derived.
        """
        if self.opened_at is None:
            return BreakerState.CLOSED
        if self.clock() - self.opened_at < self.cooldown_seconds:
            return BreakerState.OPEN
        return BreakerState.HALF_OPEN

    def allows(self) -> bool:
        """Whether a request may be sent to this target right now.

        A HALF_OPEN target admits one probe at a time. The probe is not marked
        here: `allows` is a query and must not change what it reports on
        (runbook §14.2). `begin` is the command that claims it.
        """
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        return state is BreakerState.HALF_OPEN and not self.probing

    def refusal(self) -> str:
        """Why this target is being skipped, in words a route explanation shows.

        Written for the person reading `Not qwen2.5-coder-7b — …` and wondering
        whether RAVIS is broken or the model is.
        """
        remaining = 0.0
        if self.opened_at is not None:
            remaining = max(0.0, self.cooldown_seconds - (self.clock() - self.opened_at))
        return (
            f"circuit open after {self.consecutive_failures} consecutive failures "
            f"({self.last_failure}); retrying in {remaining:.0f}s"
        )

    def begin(self) -> float:
        """Claim an attempt and return its start time.

        Returns the timestamp rather than storing it, so concurrent attempts do
        not overwrite each other's start — the caller holds its own.
        """
        self.requests += 1
        if self.state is BreakerState.HALF_OPEN:
            self.probing = True
        return self.clock()

    def succeeded(self, started_at: float, ttft: float | None = None) -> None:
        """Record a working request, and close the circuit.

        One success closes it outright rather than decrementing towards closed.
        The alternative — requiring several — leaves a recovered provider
        throttled for no reason, and the failure counter will reopen the circuit
        immediately if the recovery was illusory.
        """
        self.successes += 1
        self.consecutive_failures = 0
        self.opened_at = None
        self.probing = False
        self.latency_samples.append(self.clock() - started_at)
        if ttft is not None:
            self.ttft_samples.append(ttft)

    def blamed_elsewhere(self, failure_class: FailureClass) -> None:
        """Count an attempt that failed for somebody else's reason, and let go.

        **Releasing the probe was all this used to do — as `probe_ended` — and
        the count was lost with it.** An attempt is claimed on both the model
        and the provider; when the provider is to blame, the model's record kept
        the `requests` and recorded no failure, so a model behind an unreachable
        provider read `2 requests, 0 successes, error_rate 0.0`. That confident
        zero is the exact reading `error_rate` refuses to give for a target
        nobody has called, handed instead to one that has never worked.

        So the class is counted here and nothing else is: no `last_failure`, no
        `consecutive_failures`, no latency sample, no breaker. Those are blame,
        and the blame belongs to the other scope — a model must not be dropped
        from routing because its runtime was down, and its latency figures must
        not be shifted by attempts that never reached it.

        **Releasing `probing` is still the load-bearing half.** Both records may
        be left probing, only one is judged, and an unreleased probe latches the
        circuit: `allows()` is False while probing, only a success clears it,
        and no success can arrive while `allows()` is False. Reached most easily
        on the recovery path, since a half-open probe is by definition the first
        request sent to a provider that was just down.
        """
        name = failure_class.value
        self.failures_by_class[name] = self.failures_by_class.get(name, 0) + 1
        self.probing = False

    def failed(self, failure_class: FailureClass, started_at: float) -> None:
        """Record a failure, and open the circuit if it has earned it.

        Only failures whose scope matches this target count against the breaker.
        A malformed request is recorded — it is a fact about traffic — but it is
        the client's fault, and letting it open a circuit would take a provider
        out of service because somebody sent bad JSON.
        """
        name = failure_class.value
        self.failures_by_class[name] = self.failures_by_class.get(name, 0) + 1
        self.latency_samples.append(self.clock() - started_at)
        self.probing = False
        if failure_class.policy.scope is not self.scope:
            return
        self.last_failure = name
        self.consecutive_failures += 1
        # A failure during a probe reopens immediately, whatever the count: the
        # probe existed to answer one question, and it answered it.
        if self.state is BreakerState.HALF_OPEN or self.consecutive_failures >= (
            self.failure_threshold
        ):
            self.opened_at = self.clock()

    def interrupted(self) -> None:
        """A stream that started and then broke (§10's stream interruptions).

        Counted separately and never fed to the breaker. By the time a stream
        breaks the client already holds part of an answer, and RAVIS cannot
        retry or fall back without corrupting it — so this is a number to look
        at rather than one to act on.
        """
        self.stream_interruptions += 1

    @property
    def error_rate(self) -> float | None:
        """Failures as a fraction of attempts, or None when nothing was tried.

        `None` rather than `0.0`, which is the same distinction this file keeps
        everywhere else: a provider nobody has called and a provider that has
        never failed are not the same claim, and a dashboard reporting a
        confident zero for the first is repeating a lie it was handed.

        Counted from the failure classes rather than as `requests - successes`,
        because an attempt in flight is neither yet — and subtracting would
        report a momentary failure every time somebody looked mid-request.
        """
        if not self.requests:
            return None
        return sum(self.failures_by_class.values()) / self.requests

    def as_dict(self) -> dict[str, Any]:
        """Diagnostics only: counts and states, never a URL or a credential."""
        return {
            "target": self.target,
            "scope": self.scope.value,
            "state": self.state.value,
            "requests": self.requests,
            "successes": self.successes,
            "failures": dict(self.failures_by_class),
            "stream_interruptions": self.stream_interruptions,
            "consecutive_failures": self.consecutive_failures,
            "error_rate": self.error_rate,
            "mean_ttft_seconds": _mean(self.ttft_samples),
            "mean_latency_seconds": _mean(self.latency_samples),
        }


#: How long a capability suppression lasts when the caller names no figure.
#: `Settings.tool_refusal_suppression_seconds` says why half an hour.
SUPPRESSION_SECONDS = 1800.0


@dataclass(frozen=True)
class CapabilitySuppression:
    """One model kept away from requests that need one capability, for a while.

    **A scope the breaker does not have, on purpose.** A circuit is keyed on
    `(scope, target)` with no capability in it, so opening a MODEL circuit
    because a model refused tools would also take it out of every plain chat
    request — and runbook §2.1 requires a tool-failing model to stay eligible
    for a chat pool. This is the missing `(model, capability)` scope: the model
    is skipped for requests that need the capability, and for nothing else.

    **Time-boxed rather than permanent**, because a refusal can stop being true
    without RAVIS hearing about it: an operator reloads the model with a
    template that supports tools, or a hosted endpoint that lacked tool support
    comes back. Durable knowledge about what a model cannot do has its own home
    in capability trials and configuration; this only says "not for now".

    Frozen: a repeat refusal replaces the record rather than editing it, so a
    reader holding one never sees it change underneath them.
    """

    model: str
    # Which upstream served the refusal, for the person reading the health
    # endpoint. Not part of the key — see `HealthRegistry.suppress`.
    provider: str
    capability: Capability
    # The upstream's own words, already stripped of anything credential-shaped
    # by `AttemptChain.failed`, so an explanation says *why* and not just *that*.
    reason: str
    since: float
    until: float

    def active_at(self, now: float) -> bool:
        """Whether this suppression still applies. Worked out on read, like
        `TargetHealth.state`, so nothing has to notice the window closing."""
        return now < self.until

    def remaining(self, now: float) -> float:
        return max(0.0, self.until - now)

    def describe(self, now: float) -> str:
        """The route explanation's sentence: what happened, and when it ends.

        Relative time rather than a clock time, because the registry's clock is
        monotonic — the same choice `TargetHealth.refusal` makes — and a reader
        who wants a wall time has one to add it to.
        """
        minutes = max(1, math.ceil(self.remaining(now) / 60))
        name = self.capability.value
        return (
            f"refused {name} ({self.reason}); requests with {name} resume in "
            f"{minutes} min, and requests without {name} still use it"
        )

    def as_dict(self, now: float) -> dict[str, Any]:
        """The `/api/v1/health` shape: identifiers, the reason, and seconds."""
        return {
            "model": self.model,
            "provider": self.provider,
            "capability": self.capability.value,
            "reason": self.reason,
            "window_seconds": round(self.until - self.since),
            "lifts_in_seconds": round(self.remaining(now)),
        }


class HealthRegistry:
    """Every target RAVIS has spoken to, keyed by scope and name.

    Providers and models share one registry but not one namespace: a provider
    called `local` and a model called `local` are different things, and merging
    them would let one open the other's circuit.

    Entries are created on first use and never expire. The set of targets is
    bounded by configuration — the models an upstream serves — so this cannot
    grow without bound the way a per-request key would.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        suppression_seconds: float = SUPPRESSION_SECONDS,
    ) -> None:
        self._targets: dict[tuple[HealthScope, str], TargetHealth] = {}
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        # (model, capability) suppressions, beside the breakers rather than in a
        # store of their own — this module's founding reason: two structures
        # about one target drift. Same clock, same in-memory lifetime, and the
        # same query/command split as `allows` and `of`.
        #
        # Keyed on the model id alone, as MODEL circuits are, not on the
        # provider: routing identifies a candidate by its model id, so a key the
        # router cannot ask with would be a suppression nothing ever consults.
        self._suppressions: dict[tuple[str, Capability], CapabilitySuppression] = {}
        self._suppression_seconds = suppression_seconds
        # Public, because it is the *only* clock anything in this layer may
        # read. An attempt's start and its end must be measured against one
        # clock or the elapsed time is meaningless, and the cheapest way to
        # guarantee that is to have exactly one to reach for.
        self.clock = clock

    def of(self, scope: HealthScope, target: str) -> TargetHealth:
        """The health record for one target, created on first sight."""
        key = (scope, target)
        if key not in self._targets:
            self._targets[key] = TargetHealth(
                target=target,
                scope=scope,
                failure_threshold=self._failure_threshold,
                cooldown_seconds=self._cooldown_seconds,
                clock=self.clock,
            )
        return self._targets[key]

    def known(self, scope: HealthScope, target: str) -> TargetHealth | None:
        """The health record for one target, or None if it has never had one.

        The counterpart to `of`, which creates on first sight. A *listing* must
        not conjure records for every provider it names — a provider nobody has
        called would acquire a CLOSED breaker and a clean history purely by
        being displayed, which reads as "healthy" and is really "unknown".
        """
        return self._targets.get((scope, target))

    def record(self, failure_class: FailureClass, target: str, provider: str,
               started_at: float) -> None:
        """Attribute a failure to whichever target its class blames.

        The failure decides where it lands, not the caller. A model that OOMs
        and a provider that refuses connections arrive through the same code
        path, and getting the attribution wrong is what makes a breaker either
        useless or catastrophic.
        """
        # Both records were claimed by `AttemptChain.begin`, so both must be
        # released here even though only one is blamed. Skipping the other
        # leaves a half-open circuit latched forever.
        scope = failure_class.policy.scope
        if scope is HealthScope.PROVIDER:
            self.of(HealthScope.PROVIDER, provider).failed(failure_class, started_at)
            self.of(HealthScope.MODEL, target).blamed_elsewhere(failure_class)
        else:
            # MODEL and NONE both land on the model record: NONE is recorded so
            # the counters stay complete, while `failed` itself declines to open
            # a circuit on a scope mismatch.
            self.of(HealthScope.MODEL, target).failed(failure_class, started_at)
            self.of(HealthScope.PROVIDER, provider).blamed_elsewhere(failure_class)

    def allows(self, scope: HealthScope, target: str) -> bool:
        """Whether a target may be called — **without** creating a record for it.

        Separate from `of` because this is a query and `of` is not: `of` creates
        on first sight, which is right when something is about to be attempted
        and wrong when something is merely being asked about. Routing asks about
        every model in the catalogue on every request, and the creating version
        filled the health snapshot with rows for models nobody had ever called
        (runbook §14.2 — a query must not change what it reports on).

        An unknown target is allowed. Nothing has failed, because nothing has
        happened.
        """
        known = self._targets.get((scope, target))
        return known is None or known.allows()

    def refusal(self, scope: HealthScope, target: str) -> str:
        """Why a target is being skipped, or empty when it is not being skipped."""
        known = self._targets.get((scope, target))
        return known.refusal() if known is not None else ""

    def unavailable(self, models: list[str],
                    provider: str | Callable[[str], str]) -> dict[str, str]:
        """The models routing must currently avoid, each with its reason.

        Returned as a mapping rather than a set because the reason has to reach
        the route explanation. §9.7 requires every excluded candidate to say why
        it was excluded, and "the circuit is open" is only a useful answer when
        it arrives with the count and the cooldown attached.

        A provider-level circuit takes every model **behind that provider** out
        at once, which is the point of scoping: one refused connection should
        not have to be rediscovered thirteen times.

        `provider` may be a resolver rather than a name, and with more than one
        upstream it must be. Passing a single label made a provider circuit
        exclude every candidate on every upstream — the blast radius of one
        failing local runtime became the whole catalogue.
        """
        resolve = provider if callable(provider) else (lambda _model: provider)
        blocked: dict[str, str] = {}
        for model in models:
            owner = resolve(model)
            if not self.allows(HealthScope.PROVIDER, owner):
                blocked[model] = self.refusal(HealthScope.PROVIDER, owner)
            elif not self.allows(HealthScope.MODEL, model):
                blocked[model] = self.refusal(HealthScope.MODEL, model)
        return blocked

    def snapshot(self) -> list[dict[str, Any]]:
        """Every target's current state, for diagnostics and the CLI."""
        return [health.as_dict() for _, health in sorted(self._targets.items(),
                                                         key=lambda item: item[0][1])]

    def suppress(self, model: str, provider: str, capability: Capability,
                 reason: str) -> bool:
        """Keep `model` away from requests needing `capability`; True if newly so.

        The only command on suppressions. A repeat refusal while one is still
        active restarts the window from now — the newest refusal is the best
        evidence — but it is not *new*, so the caller publishes one event per
        suppression rather than one per refusal. Expired records are dropped
        here, in the command, so the queries below never change what they
        report on (runbook §14.2).
        """
        now = self.clock()
        self._suppressions = {
            key: held for key, held in self._suppressions.items() if held.active_at(now)
        }
        fresh = (model, capability) not in self._suppressions
        self._suppressions[(model, capability)] = CapabilitySuppression(
            model=model, provider=provider, capability=capability, reason=reason,
            since=now, until=now + self._suppression_seconds,
        )
        return fresh

    def suppressed(self, models: Iterable[str], capability: Capability) -> dict[str, str]:
        """Which of `models` are kept away from `capability` now, each with why.

        The shape `unavailable` returns, for its reason: the reason has to reach
        the route explanation (§9.7), and "refused tools" is only useful with
        the upstream's words and the time left attached.
        """
        now = self.clock()
        found: dict[str, str] = {}
        for model in models:
            held = self._suppressions.get((model, capability))
            if held is not None and held.active_at(now):
                found[model] = held.describe(now)
        return found

    def suppression(self, model: str, capability: Capability) -> dict[str, Any] | None:
        """One active suppression in its `/api/v1/health` shape, or None."""
        now = self.clock()
        held = self._suppressions.get((model, capability))
        return held.as_dict(now) if held is not None and held.active_at(now) else None

    def suppressions(self) -> list[dict[str, Any]]:
        """Every active suppression, ordered by model, for `/api/v1/health`."""
        now = self.clock()
        ordered = sorted(self._suppressions.items(),
                         key=lambda item: (item[0][0], item[0][1].value))
        return [held.as_dict(now) for _, held in ordered if held.active_at(now)]

    def lift(self, model: str, capability: Capability) -> bool:
        """End a suppression early; True when one was still active.

        An operator's undo, for when the reason a model refused has been fixed
        and waiting out the window would only delay finding that out.
        """
        held = self._suppressions.pop((model, capability), None)
        return held is not None and held.active_at(self.clock())


def _mean(samples: deque[float]) -> float | None:
    """The average of a sample window, or None when nothing was measured.

    None rather than 0.0, and the difference is not pedantic: a target that has
    never been called and a target that answers instantly are not the same
    thing, and reporting zero for the first would be a lie a dashboard repeats
    (runbook §14.4).
    """
    return sum(samples) / len(samples) if samples else None
