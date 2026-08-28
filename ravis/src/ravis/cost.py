"""Pricing, usage records and estimated cost (§14).

The whole module is arranged around one sentence in §14: **never present an
estimated cost as an invoice.** RAVIS never sees a bill. It sees token counts a
provider reported and a price a provider published, multiplies them, and the
result is an *estimate* — close, usually, and wrong in exactly the cases that
matter, because a provider's own accounting applies discounts, minimums,
cache-hit rules and rounding that RAVIS is not told about.

So every figure this module produces carries how it was arrived at, and the one
value it never produces is `BILLED`. A cost with no price is `None` rather than
zero, for the same reason `Usage` leaves an unreported token count `None`: a
free model and an unpriced one are different facts, and a dashboard rendering
"€0.00" for the second would be a confident lie.

**Double counting is prevented structurally rather than by care.** A usage
record is written from one place — the point where an attempt is recorded as
having succeeded — so a retry that failed writes nothing, and a fallback that
worked writes exactly one record naming the model that actually served. §14's
gate asks for retry, fallback and partial-stream cases; none of them can double
count something that is only ever written once.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from ravis.core.responses import Usage

# Prices are published per token and are unreadable at that scale — OpenRouter
# ships `"0.0000004"` — so everything here is per million tokens.
PER_MILLION = 1_000_000


class CostState(str, Enum):
    """How much authority a cost figure carries.

    **There is deliberately no `BILLED`.** RAVIS never sees an invoice, and a
    state it could not honestly set would eventually be set anyway — by
    somebody reasonably assuming that a state exists because something reaches
    it. If RAVIS ever ingests real billing, that is the moment to add it.
    """

    # RAVIS multiplied a published price by reported tokens. Its own arithmetic.
    ESTIMATED = "ESTIMATED"
    # The provider stated what this call cost. Stronger than RAVIS's own
    # multiplication and still not an invoice: a per-call figure precedes
    # credits, minimums, negotiated rates and whatever the monthly statement
    # actually reconciles to. §14 asks for "estimated-versus-billed status",
    # and this is the honest middle the spec is pointing at — RAVIS did not
    # compute it, and RAVIS did not receive a bill for it either.
    REPORTED = "REPORTED"
    # The tokens are known and no price is; or a price is known and the tokens
    # are not. Both are unknown *cost*, and collapsing them into zero is the
    # failure §14 names.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Price:
    """What one model costs, split the way §14 asks for it.

    Input and output are separate because they are separately priced almost
    everywhere and differ by an order of magnitude — a summed figure orders
    models correctly for *ranking*, which is what `price_per_million` is for,
    and is useless for computing what a request actually cost.

    `source` and `captured_at` are §14's "price-source version and time". A cost
    computed from a price nobody can date is not auditable, and pricing changes
    without announcement.
    """

    input_per_million: float
    output_per_million: float
    currency: str = "USD"
    cached_input_per_million: float | None = None
    source: str = ""
    captured_at: float = 0.0

    @property
    def free(self) -> bool:
        """Whether this model costs nothing per token.

        A real answer rather than an absence: local runtimes and OpenRouter's
        `:free` tier both genuinely cost nothing, which is the strongest thing a
        router can know about cost.
        """
        return self.input_per_million == 0.0 and self.output_per_million == 0.0


def estimate(price: Price | None, usage: Usage | None) -> tuple[float | None, CostState]:
    """What this call plausibly cost, and how much that figure is worth.

    Returns `UNKNOWN` with `None` unless *both* halves are present. A price
    without tokens and tokens without a price are equally unanswerable, and
    filling either gap with a zero would produce a number that looks measured.

    Cached input is charged at its own rate when the provider both publishes one
    and reports the count; otherwise cached tokens are charged as ordinary
    input, which is what a provider that does not distinguish them is doing.
    """
    # **The provider's own figure wins when it publishes one.** OpenRouter
    # returns `cost` on every usage frame, and it is arrived at with knowledge
    # RAVIS does not have — per-request routing to a particular upstream, a
    # negotiated rate, a promotional tier. Preferring RAVIS's multiplication
    # over it would be preferring the weaker of two available answers.
    if usage is not None and usage.reported_cost is not None:
        return usage.reported_cost, CostState.REPORTED
    if price is None or usage is None:
        return None, CostState.UNKNOWN
    if usage.input_tokens is None and usage.output_tokens is None:
        return None, CostState.UNKNOWN

    cached = usage.cached_input_tokens or 0
    plain_input = max((usage.input_tokens or 0) - cached, 0)
    total = plain_input * price.input_per_million / PER_MILLION
    total += (usage.output_tokens or 0) * price.output_per_million / PER_MILLION
    cached_rate = (
        price.cached_input_per_million
        if price.cached_input_per_million is not None
        else price.input_per_million
    )
    total += cached * cached_rate / PER_MILLION
    return total, CostState.ESTIMATED


@dataclass(frozen=True)
class UsageRecord:
    """One completed call, as §14 lists it.

    Written once, at the point an attempt is recorded as having succeeded, which
    is what makes double counting structural rather than something to be careful
    about. A failed attempt writes nothing; a fallback writes one record naming
    the model that actually answered.

    Carries no prompt and no completion — §14's request logging defaults to
    metadata only, and there is nowhere here to put content.
    """

    model: str
    provider: str
    application_id: str
    usage: Usage | None = None
    cost: float | None = None
    cost_state: CostState = CostState.UNKNOWN
    currency: str | None = None
    # §14's "price-source version and time", carried on the record rather than
    # looked up later: a price that has since changed must not silently restate
    # what an old call cost.
    price_source: str = ""
    price_captured_at: float = 0.0
    latency_ms: float | None = None
    request_id: str = ""
    session_id: str = ""
    decision_id: str = ""
    # Which pool served it, so spend can be attributed to the thing an operator
    # configured rather than only to a model id they never chose.
    pool: str = ""
    at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "application_id": self.application_id,
            "input_tokens": self.usage.input_tokens if self.usage else None,
            "output_tokens": self.usage.output_tokens if self.usage else None,
            "cached_input_tokens": self.usage.cached_input_tokens if self.usage else None,
            "reasoning_tokens": self.usage.reasoning_tokens if self.usage else None,
            "cost": self.cost,
            "cost_state": self.cost_state.value,
            "currency": self.currency,
            "price_source": self.price_source or None,
            # §14 asks for price-source *version and time*, and the time was
            # being recorded and never published — which makes it unauditable,
            # which is the only reason to record it. Caught by the dead-code
            # gate reporting the field as written but never read.
            "price_captured_at": self.price_captured_at or None,
            "latency_ms": self.latency_ms,
            "request_id": self.request_id or None,
            "session_id": self.session_id or None,
            "decision_id": self.decision_id or None,
            "pool": self.pool or None,
            "at": self.at,
        }


class PriceBook:
    """Every price RAVIS currently believes, by model.

    Populated from provider catalogues on the refresh that already runs, so a
    price is never older than the catalogue it came from. Held in memory: a
    stale price is worse than an absent one, and re-reading it costs a catalogue
    fetch that happens anyway.
    """

    def __init__(self, clock: Any = time.time) -> None:
        self._prices: dict[str, Price] = {}
        self._clock = clock

    def record(self, model: str, price: Price) -> None:
        self._prices[model] = price

    def price_of(self, model: str) -> Price | None:
        return self._prices.get(model)

    def known(self) -> int:
        return len(self._prices)


class UsageLedger:
    """What RAVIS has spent, as far as it can tell.

    Bounded and in memory, like the decision log and for the same reason: these
    are diagnostic rather than business state, and §17's storage model is not
    the authority on anybody's bill. **A budget computed from this is a budget
    computed from what RAVIS observed**, which is the only thing it can honestly
    offer and is stated wherever the figure is shown.
    """

    def __init__(self, capacity: int = 5_000, clock: Any = time.time) -> None:
        self._records: list[UsageRecord] = []
        self._capacity = capacity
        self._clock = clock

    def record(self, entry: UsageRecord) -> UsageRecord:
        stamped = entry if entry.at else _stamped(entry, self._clock())
        self._records.append(stamped)
        if len(self._records) > self._capacity:
            del self._records[: len(self._records) - self._capacity]
        return stamped

    def recent(self, limit: int = 100) -> list[UsageRecord]:
        return list(reversed(self._records[-limit:]))

    def since(self, cutoff: float) -> list[UsageRecord]:
        return [record for record in self._records if record.at >= cutoff]

    def spend(self, records: Iterable[UsageRecord] | None = None) -> tuple[float, int, int]:
        """(estimated total, records priced, records not priced).

        Three values rather than one, because a total without the count it rests
        on invites being read as complete. Twelve euros across forty calls, of
        which nine had no price, is a different statement from twelve euros
        across forty calls — and §14's rule about invoices is exactly about not
        letting the first be read as the second.
        """
        total = 0.0
        priced = unpriced = 0
        for record in self._records if records is None else records:
            if record.cost is None:
                unpriced += 1
                continue
            total += record.cost
            priced += 1
        return total, priced, unpriced


def _stamped(entry: UsageRecord, now: float) -> UsageRecord:
    """`entry` with a timestamp, since the record is frozen."""
    return replace(entry, at=now)


# ── Budgets (§14) ───────────────────────────────────────────────────────────


class BudgetBand(str, Enum):
    """§14's four bands, named rather than numbered.

    The thresholds are the specification's own — 70%, 90%, 100% — and the
    behaviour attached to each is what the routing engine reads.
    """

    NORMAL = "NORMAL"
    PREFER_CHEAPER = "PREFER_CHEAPER"
    STRONG_PENALTY = "STRONG_PENALTY"
    EXHAUSTED = "EXHAUSTED"


@dataclass(frozen=True)
class Budget:
    """A spending limit and what happens as it is approached.

    `hard` decides only what the last band does. §14 says *paid APIs blocked if
    hard* — so a soft budget at 100% still routes, still prefers the cheapest
    thing available, and still reports that it is over. Blocking by default
    would turn a figure RAVIS admits is an estimate into an outage.
    """

    limit: float
    currency: str = "USD"
    period: str = "monthly"
    hard: bool = False

    def band(self, spent: float) -> BudgetBand:
        """Which of §14's four bands this spend falls in."""
        if self.limit <= 0:
            return BudgetBand.NORMAL
        used = spent / self.limit
        if used >= 1.0:
            return BudgetBand.EXHAUSTED
        if used >= 0.9:
            return BudgetBand.STRONG_PENALTY
        if used >= 0.7:
            return BudgetBand.PREFER_CHEAPER
        return BudgetBand.NORMAL


# How long each period covers, in seconds. Calendar months vary and this is
# deliberately not a calendar: a rolling window needs no timezone, no
# month-length table and no answer to what happens on the 31st, and §14 asks for
# a spending limit rather than for an accounting period that must reconcile
# with a provider's own statement.
PERIOD_SECONDS = {
    "daily": 24 * 3600.0,
    "weekly": 7 * 24 * 3600.0,
    "monthly": 30 * 24 * 3600.0,
}


def band_for(
    budget: Budget | None, ledger: UsageLedger, now: float
) -> tuple[BudgetBand, float, int]:
    """The current band, the spend behind it, and how many calls went unpriced.

    The unpriced count travels with the band because §14 requires budget
    constraints to "fail predictably when a price is unavailable": a budget
    computed from forty calls of which nine had no price is not a budget anybody
    should be blocked by, and the caller needs to see that rather than infer it.
    """
    if budget is None:
        return BudgetBand.NORMAL, 0.0, 0
    window = PERIOD_SECONDS.get(budget.period, PERIOD_SECONDS["monthly"])
    records = ledger.since(now - window)
    spent, _priced, unpriced = ledger.spend(records)
    return budget.band(spent), spent, unpriced


def budget_from(settings: Any) -> Budget | None:
    """The configured budget, or None when there is not one.

    None rather than a zero-limit `Budget`, because the two would route
    differently in the worst possible direction: a limit of zero puts the first
    request of the day straight into the exhausted band, and an operator who
    has configured nothing has not asked for that.
    """
    limit = float(getattr(settings, "budget_limit", 0.0) or 0.0)
    if limit <= 0:
        return None
    return Budget(
        limit=limit,
        currency=str(getattr(settings, "budget_currency", "USD")),
        period=str(getattr(settings, "budget_period", "monthly")),
        hard=bool(getattr(settings, "budget_hard", False)),
    )
