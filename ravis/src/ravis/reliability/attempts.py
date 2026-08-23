"""The fallback chain, and the budget that stops it running forever (§10).

§10 asks for `Primary → Fallback 1 → Fallback 2`, a retry budget of *max
attempts, max total latency, max total monetary cost*, and one prohibition that
outranks all of it: **client cancellation is not a retry and must not trigger
fallback.**

This module owns the *policy* — which target to try next, when to stop, and
what to record — and owns none of the I/O. That split is why cancellation is
safe: the chain never sees a request, never wraps one in `try`, and therefore
cannot catch the `GeneratorExit` or `CancelledError` that a disconnect raises
in the caller. Cancellation ends the caller's loop by unwinding through it, and
an object that only accepts method calls has no way to interfere.

The other rule the split buys is stream integrity, which is M12's acceptance
criterion. A fallback is only ever a decision about *which request to make
next*; once the caller has handed a byte to the client it simply stops asking,
and there is no code path by which the chain could insist otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ravis.reliability.failures import FailureClass, HealthScope
from ravis.reliability.health import HealthRegistry


@dataclass(frozen=True)
class RetryBudget:
    """The three ceilings §10 names, one of which cannot be enforced yet.

    `max_total_cost` is accepted and ignored, and saying so is better than
    pretending. Cost estimation is M15; until it lands RAVIS knows what a
    request *was* only after the fact, and for a local model the answer is zero
    anyway. The field exists so the budget is complete when the number arrives,
    and `unenforced` says out loud which ceiling is decorative — a limit
    silently not applied is worse than one that is absent (runbook §14.4).
    """

    max_attempts: int = 3
    max_total_seconds: float = 120.0
    max_total_cost: float | None = None

    @property
    def unenforced(self) -> list[str]:
        return ["max_total_cost (needs M15 cost estimation)"] if self.max_total_cost else []

    def exceeded_by(self, attempts: int, elapsed: float) -> str | None:
        """Why the budget is spent, or None while it is not.

        Latency is checked *before* the next attempt rather than during one: a
        chain that has already spent its allowance should not open another
        connection, and interrupting an attempt in flight is the client's
        prerogative, not the budget's.
        """
        if attempts >= self.max_attempts:
            return f"retry budget spent: {attempts} attempt(s), maximum {self.max_attempts}"
        if elapsed >= self.max_total_seconds:
            return (
                f"retry budget spent: {elapsed:.1f}s elapsed, "
                f"maximum {self.max_total_seconds:.0f}s"
            )
        return None


@dataclass
class Attempt:
    """One target and what became of it — the history a failure explains with."""

    model: str
    outcome: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"model": self.model, "outcome": self.outcome, "detail": self.detail}


@dataclass
class AttemptChain:
    """Walks a route decision's candidates until one answers or the budget ends.

    Constructed per request. It holds the queue of candidates the router already
    ranked — every one of which satisfies the pool invariants and the request's
    hard constraints, because §10 requires that of a fallback and the router is
    the only thing that can guarantee it. This class never widens the set; it
    only decides how far down it to walk.
    """

    health: HealthRegistry
    provider: str
    budget: RetryBudget = field(default_factory=RetryBudget)

    _queue: list[str] = field(default_factory=list, init=False)
    _retry: str | None = field(default=None, init=False)
    _attempts: list[Attempt] = field(default_factory=list, init=False)
    _last_class: FailureClass | None = field(default=None, init=False)
    _started_at: float = field(default=0.0, init=False)
    _stopped: str = field(default="", init=False)

    def now(self) -> float:
        """The current time, on the health registry's clock.

        The chain deliberately owns no clock of its own. An attempt's start is
        stamped by `TargetHealth.begin` and its end by `succeeded`/`failed`, so
        a second clock here would mean one interval measured against two — the
        kind of thing that is invisible until a test injects a fake one and the
        arithmetic quietly stops meaning anything.
        """
        return self.health.clock()

    def load(self, primary: str, fallbacks: list[str]) -> None:
        """Set the chain: the router's choice, then its ranked alternatives."""
        self._queue = [primary, *fallbacks]
        self._started_at = self.now()

    def next_target(self) -> str | None:
        """The next model to try, or None when the chain is finished.

        Three things can finish it: the budget, the previous failure's policy
        (an invalid request is not retried anywhere), and running out of
        candidates whose circuit is closed.
        """
        if self._retry is not None:
            target, self._retry = self._retry, None
            return target
        if not self._may_continue():
            return None
        return self._next_permitted()

    def begin(self, target: str) -> float:
        """Claim an attempt against both the model's and the provider's health."""
        self.health.of(HealthScope.PROVIDER, self.provider).begin()
        return self.health.of(HealthScope.MODEL, target).begin()

    def succeeded(self, target: str, started_at: float, ttft: float | None = None) -> None:
        """Record a working attempt against every target it exercised.

        Both scopes are credited, because a successful call is evidence about
        the provider *and* about the model, and crediting only one leaves the
        other's circuit stuck open after a recovery.
        """
        self.health.of(HealthScope.MODEL, target).succeeded(started_at, ttft)
        self.health.of(HealthScope.PROVIDER, self.provider).succeeded(started_at, ttft)
        self._attempts.append(Attempt(model=target, outcome="succeeded"))
        self._last_class = None

    def failed(self, target: str, started_at: float, failure_class: FailureClass,
               detail: str = "") -> None:
        """Record a failed attempt and let its class decide what happens next.

        The policy is consulted here rather than by the caller, so that a new
        failure class cannot change behaviour by being handled inconsistently in
        two places.
        """
        self.health.record(failure_class, target, self.provider, started_at)
        self._attempts.append(Attempt(target, failure_class.value, detail))
        self._last_class = failure_class
        if failure_class.policy.retry_same_target:
            self._retry = target

    def interrupted(self, target: str) -> None:
        """A stream that broke after the client already had bytes (§10).

        Terminal by construction. Recorded on both scopes because either could
        be the cause, and neither can be retried: the client holds a partial
        answer, and a second attempt would append a second answer to it.
        """
        self.health.of(HealthScope.MODEL, target).interrupted()
        self.health.of(HealthScope.PROVIDER, self.provider).interrupted()
        self._attempts.append(Attempt(target, "stream_interrupted"))
        self._stopped = "the stream had already begun; a fallback would corrupt it"

    @property
    def attempts(self) -> list[Attempt]:
        return list(self._attempts)

    @property
    def last_class(self) -> FailureClass:
        """The class the chain ended on, for the error the client receives."""
        return self._last_class or FailureClass.UNKNOWN

    def summary(self) -> dict[str, Any]:
        """The attempt history, in the shape §9.7 explanations are published in."""
        return {
            "provider": self.provider,
            "attempts": [attempt.as_dict() for attempt in self._attempts],
            "stopped_because": self._stopped,
            "budget_unenforced": self.budget.unenforced,
        }

    def exhausted_message(self) -> str:
        """One sentence saying why nothing answered.

        Names the number of attempts and the reason the chain stopped, because
        "all upstream attempts failed" without either is a message that sends
        the reader to the logs.
        """
        tried = ", ".join(f"{a.model} ({a.outcome})" for a in self._attempts) or "nothing"
        return f"No upstream attempt succeeded. Tried: {tried}. {self._stopped}".strip()

    def _may_continue(self) -> bool:
        """Whether the chain is allowed another target at all."""
        spent = self.budget.exceeded_by(len(self._attempts), self.now() - self._started_at)
        if spent is not None:
            self._stopped = spent
            return False
        if self._last_class is not None and not self._last_class.policy.may_fall_back:
            self._stopped = (
                f"{self._last_class.value} is not a fallback-eligible failure; "
                "another model would fail the same way"
            )
            return False
        return True

    def _next_permitted(self) -> str | None:
        """Pop candidates until one has a closed circuit, or the queue empties.

        A skipped candidate is recorded rather than dropped silently. Otherwise
        the explanation for a failed request omits the models that were never
        tried, which is precisely the information needed to tell "everything is
        broken" from "everything was already known to be broken".
        """
        skipped = ""
        while self._queue:
            candidate = self._queue.pop(0)
            # Asked about, not claimed: these are `allows`/`refusal` on the
            # registry rather than `of(...).allows()`, so a candidate that is
            # only *considered* does not acquire a health record.
            if not self.health.allows(HealthScope.PROVIDER, self.provider):
                refusal = self.health.refusal(HealthScope.PROVIDER, self.provider)
                self._stopped = refusal
                self._queue.clear()
                self._attempts.append(Attempt(candidate, "skipped", refusal))
                return None
            if not self.health.allows(HealthScope.MODEL, candidate):
                skipped = self.health.refusal(HealthScope.MODEL, candidate)
                self._attempts.append(Attempt(candidate, "skipped", skipped))
                continue
            return candidate
        if not self._stopped:
            # The last skip reason, not a generic sentence: when every candidate
            # was skipped, *why* is the entire content of the answer, and
            # "nothing left to try" would send the reader to the logs to find
            # the sentence that is already available here.
            self._stopped = skipped or "no further candidate remains in the chain"
        return None
