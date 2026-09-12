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

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ravis.core.capabilities import Capability
from ravis.observations import Observations
from ravis.reliability.failures import FailureClass, HealthScope
from ravis.reliability.health import HealthRegistry

# Anything credential-shaped in a failure's words, and what replaces it.
#
# **Why an attempt's detail needs this at all.** It carries the upstream's own
# error sentence, and that text spreads: into the route decision the management
# API serves, into the `ravis.request.completed` and `ravis.capability.suppressed`
# events NERVIS stores, into the exhaustion log line, and into a tool-refusal
# suppression's reason on `/api/v1/health`. An upstream answering a bad key is
# free to quote it back — OpenAI masks the middle, nothing obliges anyone else
# to — and the protocol's `redact_deep` only blanks values by *key name*, so a
# key inside a sentence would pass straight through it.
#
# The shapes follow NERVIS's log redaction (`nervis/src/nervis/logs.py`), with
# two deliberate differences. The long-run rule leaves out `-` and `.`, because
# a detail often names a model and `deepseek-r1-distill-llama-70b-instruct` is
# 43 characters of exactly that; a key made only of word characters — hex,
# base64 without padding, OpenRouter's 64-hex tail — is still caught. And the
# vendor prefixes a hosted provider actually issues are named, since those keys
# do contain hyphens.
_CREDENTIAL_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [redacted]"),
    (re.compile(r"\b(?:sk|xai)-[A-Za-z0-9._-]{8,}"), "[redacted]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{20,}"), "[redacted]"),
    (re.compile(r"\b(?:gsk|hf|ghp|github_pat)_[A-Za-z0-9_]{16,}"), "[redacted]"),
    (re.compile(r"\b[A-Za-z0-9_]{32,}={0,2}"), "[redacted]"),
    (re.compile(r"(?i)\b(api[-_]?key|token|secret|password)([\"'\s:=]+)[^\s\"',}]{6,}"),
     r"\1\2[redacted]"),
)


def without_credentials(text: str) -> str:
    """`text` with every credential-shaped run replaced by `[redacted]`.

    Applied where an attempt is recorded, which is the one place every path's
    failure text passes through — transparent and translated, streamed or not —
    so no caller has to remember to do it (runbook §9: redact at the producer).
    """
    for pattern, replacement in _CREDENTIAL_SHAPES:
        text = pattern.sub(replacement, text)
    return text


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
    """One target and what became of it — the history a failure explains with.

    **The measurements are metadata and never content.** §11.4 asks an inspector
    to show the upstream destination and the stream's own numbers; none of that
    requires a prompt or a completion, and this record deliberately holds
    neither. What a model was *asked* is not here and is not meant to be.

    `elapsed_ms` and `ttft_ms` were already computed on the success path to feed
    the health registry and then discarded. Keeping them costs two floats and is
    the difference between "this attempt succeeded" and "this attempt succeeded,
    first byte in 240 ms, done in 3.1 s".
    """

    model: str
    outcome: str
    detail: str = ""
    # Which upstream actually served it. A chain can cross providers, so the
    # destination belongs to the attempt rather than to the request.
    provider: str = ""
    # Wall time for this attempt alone, and time to first byte where there was
    # a stream. `None` means not measured rather than zero — a skipped candidate
    # never opened a connection, and 0 ms would read as an instant answer.
    elapsed_ms: float | None = None
    ttft_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "outcome": self.outcome,
            "detail": self.detail,
            "provider": self.provider,
            "elapsed_ms": self.elapsed_ms,
            "ttft_ms": self.ttft_ms,
        }


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
    # A provider *name*, or a resolver from model to provider name.
    #
    # It was a bare string until M8 made upstreams plural, and that quietly
    # became a routing bug rather than a naming one: a provider-scoped circuit
    # takes every model behind it out at once, so one shared label meant a
    # single failing upstream opened the breaker for **all** of them. The
    # comment on the old constant even said "M8 makes this plural, at which
    # point the adapter supplies the name" — and then M8 shipped without it.
    provider: str | Callable[[str], str]
    budget: RetryBudget = field(default_factory=RetryBudget)
    # Where a successful attempt's timings are written down so they outlive the
    # process. Optional so that every test constructing a chain keeps working
    # and so a deployment can turn the record off by not supplying one.
    observations: Observations | None = None
    # Whether the client named a pool rather than one model.
    #
    # It changes what a failure means. A direct address says *use this one*, so
    # a 401 against it is the answer and moving on would hide it. A pool says
    # *pick something that works*, and refusing sixty-six other candidates
    # because the first one's provider had a credential problem is the opposite
    # of what was asked for.
    from_pool: bool = False
    # Whether the request carries tools. A tool refusal only arms the
    # (model, tools) suppression when it does: a refusal "about tools" on a
    # request that sent none is a misreading of the upstream's words, and
    # acting on it would take a good model out of tool routing for half an
    # hour. The fallback still happens either way — it is the *memory* that
    # needs the proof.
    carries_tools: bool = False

    _queue: list[str] = field(default_factory=list, init=False)
    # Models this chain newly suppressed for tools, in order, so the closing
    # event funnel can publish each one once rather than once per refusal.
    _suppressed: list[str] = field(default_factory=list, init=False)
    _retry: str | None = field(default=None, init=False)
    # Targets that have already had their one same-target retry. Without this
    # the retry re-arms on every failure of the same class, and a target that
    # refuses every connection is offered again forever — the chain never
    # reaches its second candidate and the request never returns.
    _retried: set[str] = field(default_factory=set, init=False)
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

        **The budget governs a same-target retry exactly as it governs a
        fallback**, and it used to be bypassed by it. The retry was returned
        before any ceiling was consulted, so against an upstream refusing every
        connection the chain re-offered the same target indefinitely: the second
        candidate was never reached, `max_attempts` was never enforced, and the
        client waited on a loop that could not end. A retry is another attempt
        and another slice of someone's patience, which is precisely what the
        budget exists to bound.

        The fallback-eligibility half is deliberately *not* applied to a retry.
        It asks whether **another model** would fail the same way, which is a
        different question from whether this one deserves a second try.
        """
        if self._retry is not None:
            target, self._retry = self._retry, None
            return None if self._budget_spent() else target
        if not self._may_continue():
            return None
        return self._next_permitted()

    def provider_for(self, target: str) -> str:
        """Which provider's health this model's attempt belongs to.

        Every lifecycle method already receives the target, so the scope can
        always be resolved from the model rather than assumed for the request.
        """
        return self.provider(target) if callable(self.provider) else self.provider

    def begin(self, target: str) -> float:
        """Claim an attempt against both the model's and the provider's health."""
        self.health.of(HealthScope.PROVIDER, self.provider_for(target)).begin()
        return self.health.of(HealthScope.MODEL, target).begin()

    def succeeded(self, target: str, started_at: float, ttft: float | None = None) -> None:
        """Record a working attempt against every target it exercised.

        Both scopes are credited, because a successful call is evidence about
        the provider *and* about the model, and crediting only one leaves the
        other's circuit stuck open after a recovery.
        """
        self.health.of(HealthScope.MODEL, target).succeeded(started_at, ttft)
        self.health.of(HealthScope.PROVIDER, self.provider_for(target)).succeeded(started_at, ttft)
        # The same numbers, written somewhere they survive a restart. The
        # registry's windows are in memory, which is why an audit of the routing
        # path found four samples across six hundred models and concluded the
        # coverage was too thin to rank on — it was thin because it kept
        # starting over. In memory here too; `flush` is on a timer.
        if self.observations is not None:
            elapsed_ms = (self.health.clock() - started_at) * 1000
            self.observations.record(
                target, elapsed_ms, ttft * 1000 if ttft is not None else None
            )
        self._attempts.append(Attempt(
            model=target,
            outcome="succeeded",
            provider=self.provider_for(target),
            elapsed_ms=(self.health.clock() - started_at) * 1000,
            ttft_ms=ttft * 1000 if ttft is not None else None,
        ))
        self._last_class = None

    def failed(self, target: str, started_at: float, failure_class: FailureClass,
               detail: str = "") -> None:
        """Record a failed attempt and let its class decide what happens next.

        The policy is consulted here rather than by the caller, so that a new
        failure class cannot change behaviour by being handled inconsistently in
        two places.
        """
        # Stripped before it is kept anywhere: this text reaches the management
        # API, the event hub, the exhaustion log line and a suppression's reason.
        detail = without_credentials(detail)
        self.health.record(failure_class, target, self.provider_for(target), started_at)
        # Measured on failure too: "refused after 30 s" and "refused instantly"
        # are different faults, and the timing is the only thing that separates
        # a timeout from a rejection in the record.
        self._attempts.append(Attempt(
            target, failure_class.value, detail,
            provider=self.provider_for(target),
            elapsed_ms=(self.health.clock() - started_at) * 1000,
        ))
        self._last_class = failure_class
        self._suppress_if_tools_refused(target, failure_class, detail)
        # At most one same-target retry, ever. The policy says this class of
        # failure proves the request never arrived, which justifies a second
        # attempt — not an unbounded series of them. A target that has had its
        # retry falls through to the next candidate instead, which is the point
        # of having a chain.
        if failure_class.policy.retry_same_target and target not in self._retried:
            self._retried.add(target)
            self._retry = target

    def _suppress_if_tools_refused(self, target: str, failure_class: FailureClass,
                                   detail: str) -> None:
        """Keep a model that refused tools away from tool requests for a while.

        This is what makes the fallback safe to allow. Without it no circuit
        opens (the class is scoped NONE, correctly), the router re-picks the
        same refusing primary on every request, and each of Clarvis's per-turn
        requests becomes a silent double upstream call (STATUS.md recorded that
        flipping the fallback alone makes things worse, for exactly this).
        """
        if failure_class is not FailureClass.TOOL_INCOMPATIBILITY or not self.carries_tools:
            return
        if self.health.suppress(target, self.provider_for(target), Capability.TOOLS, detail):
            self._suppressed.append(target)

    def cancelled(self, target: str) -> None:
        """The client went away (§10: cancellation is not a retry).

        Recorded as an outcome, not as a failure. Nothing is counted against
        health — the model did nothing wrong and neither did the provider — and
        no fallback follows, because the chain is never consulted again.

        It has to be *recorded* rather than merely not-failed, though. Without
        this a cancelled request leaves its route decision looking exactly like
        one still in flight, and "the user pressed Stop" and "this has been
        hanging for four minutes" are the two readings a person most needs to
        tell apart.
        """
        self._attempts.append(Attempt(target, "cancelled", provider=self.provider_for(target)))
        self._stopped = "the client disconnected; cancellation is never a failure (§10)"

    def interrupted(self, target: str) -> None:
        """A stream that broke after the client already had bytes (§10).

        Terminal by construction. Recorded on both scopes because either could
        be the cause, and neither can be retried: the client holds a partial
        answer, and a second attempt would append a second answer to it.
        """
        self.health.of(HealthScope.MODEL, target).interrupted()
        self.health.of(HealthScope.PROVIDER, self.provider_for(target)).interrupted()
        self._attempts.append(Attempt(target, "stream_interrupted",
                                      provider=self.provider_for(target)))
        self._stopped = "the stream had already begun; a fallback would corrupt it"

    @property
    def attempts(self) -> list[Attempt]:
        return list(self._attempts)

    @property
    def suppressed(self) -> list[str]:
        """Models this request newly kept away from tool requests, in order."""
        return list(self._suppressed)

    @property
    def last_class(self) -> FailureClass:
        """The class the chain ended on, for the error the client receives."""
        return self._last_class or FailureClass.UNKNOWN

    def summary(self) -> dict[str, Any]:
        """The attempt history, in the shape §9.7 explanations are published in."""
        return {
            # The providers this chain actually touched, rather than one label
            # for the request: with plural upstreams a chain can cross them.
            "provider": self._providers_touched(),
            "attempts": [attempt.as_dict() for attempt in self._attempts],
            "stopped_because": self._stopped,
            "budget_unenforced": self.budget.unenforced,
            # Which refusals this request turned into a suppression, so a route
            # decision read later says why the next tool request skipped them.
            "suppressed": list(self._suppressed),
        }

    def _providers_touched(self) -> str:
        """Every provider this chain reached, in order, joined for the record."""
        if not callable(self.provider):
            return self.provider
        seen: list[str] = []
        for attempt in self._attempts:
            name = self.provider_for(attempt.model)
            if name not in seen:
                seen.append(name)
        return " · ".join(seen) or "none attempted"

    def exhausted_message(self) -> str:
        """One sentence saying why nothing answered.

        Names the number of attempts and the reason the chain stopped, because
        "all upstream attempts failed" without either is a message that sends
        the reader to the logs.
        """
        tried = ", ".join(
            f"{a.model} ({a.outcome}{' — ' + a.detail if a.detail else ''})" for a in self._attempts
        ) or "nothing"
        return f"No upstream attempt succeeded. Tried: {tried}. {self._stopped}".strip()

    def _budget_spent(self) -> bool:
        """Whether the chain has used up its allowance, recording why if so.

        Separate from `_may_continue` because a same-target retry has to consult
        this and must *not* consult the fallback-eligibility rule below.
        """
        spent = self.budget.exceeded_by(len(self._attempts), self.now() - self._started_at)
        if spent is not None:
            self._stopped = spent
            return True
        return False

    def _may_continue(self) -> bool:
        """Whether the chain is allowed another target at all."""
        if self._budget_spent():
            return False
        if self._last_class is not None and not self._may_fall_back(self._last_class):
            self._stopped = (
                f"{self._last_class.value} is not a fallback-eligible failure; "
                "another model would fail the same way"
            )
            return False
        return True

    def _may_fall_back(self, failure_class: FailureClass) -> bool:
        """Whether another candidate is worth trying after this failure.

        The class decides, with one exception that the class cannot see:
        **authentication, from a pool.**

        Its policy is `may_fall_back=False`, and for a direct address that is
        right — a fixable 401 naming the problem beats a no-route that does not.
        But a pool asked for something that works. Observed live: `ravis/balanced`
        selected a free Gemma model on OpenRouter, OpenRouter's own call to
        Google came back 401, and the chain stopped with sixty-six untried
        candidates and a credential error about somebody else's key. Nothing in
        that 401 was evidence about the other sixty-six.

        Every other class keeps its policy. An invalid request really would fail
        the same way everywhere, and spending a second model's time to prove it
        is what the flag exists to prevent — which is why one model refusing a
        *parameter* is its own class, `UNSUPPORTED_PARAMETER`, rather than this.
        """
        if failure_class is FailureClass.AUTHENTICATION and self.from_pool:
            return True
        return failure_class.policy.may_fall_back

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
            provider = self.provider_for(candidate)
            if not self.health.allows(HealthScope.PROVIDER, provider):
                # **Skip this candidate, not the chain.** This cleared the queue
                # and returned None, so one provider's open circuit abandoned
                # every remaining fallback — including candidates on entirely
                # different providers, and including local ones that cost
                # nothing and were never asked. A gateway whose whole job is to
                # have somewhere else to go answered 502 with its alternatives
                # unspent, which is the failure this layer exists to prevent.
                #
                # The model branch below already did the right thing. These two
                # cases differ in which circuit opened, not in what should
                # happen next.
                skipped = self.health.refusal(HealthScope.PROVIDER, provider)
                self._attempts.append(Attempt(candidate, "skipped", skipped,
                                              provider=self.provider_for(candidate)))
                continue
            if not self.health.allows(HealthScope.MODEL, candidate):
                skipped = self.health.refusal(HealthScope.MODEL, candidate)
                self._attempts.append(Attempt(candidate, "skipped", skipped,
                                              provider=self.provider_for(candidate)))
                continue
            return candidate
        if not self._stopped:
            # The last skip reason, not a generic sentence: when every candidate
            # was skipped, *why* is the entire content of the answer, and
            # "nothing left to try" would send the reader to the logs to find
            # the sentence that is already available here.
            self._stopped = skipped or "no further candidate remains in the chain"
        return None
