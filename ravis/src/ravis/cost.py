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

import json
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any

from ravis.core.responses import Usage
from ravis.credentials import config_directory
from ravis.storage.database import Database

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
    # **Every priced component must be known, not merely one of them.** Gemini
    # sometimes reports `usageMetadata` with a prompt count and no completion
    # count, and charging for the half that arrived produced a figure labelled
    # ESTIMATED that silently *understated* the call — which is the direction
    # that matters, because a budget reads an understatement as room left.
    #
    # A component priced at zero is exempt: a free model's missing output count
    # cannot change what it cost.
    if usage.input_tokens is None and price.input_per_million:
        return None, CostState.UNKNOWN
    if usage.output_tokens is None and price.output_per_million:
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


# A trailing release date on a model id, as Anthropic dates its builds: `-20251001`.
_DATED_SUFFIX = re.compile(r"-\d{8}$")


def price_from_book(candidates: Mapping[str, Any], book: PriceBook | None) -> None:
    """Give each unpriced candidate the price the book holds for it, for ranking.

    **Only where nothing was published.** A catalogue's own price stays as it is; this
    fills the gap for the providers that publish no pricing at all — Anthropic, OpenAI
    and Google, whose rates live in the operator's prices.json. Found on 12 September
    2026: the book already held Anthropic's rates for the spend screen and ranking never
    read them, so every direct Claude build tied as unpriced, and the tie fell to
    alphabetical order — the oldest build first.

    Writing onto the records is safe because they are rebuilt on every routing pass.
    """
    if book is None:
        return
    for model, known in candidates.items():
        if known.price_per_million is not None:
            continue
        price = book.price_of(model)
        if price is None:
            continue
        known.price_per_million = price.input_per_million + price.output_per_million
        if known.price is None:
            known.price = price


class PriceBook:
    """Every price RAVIS currently believes, by model.

    Populated from provider catalogues on the refresh that already runs, so a
    price is never older than the catalogue it came from. Held in memory: a
    stale price is worse than an absent one, and re-reading it costs a catalogue
    fetch that happens anyway.

    **That first sentence was false until 9 September 2026** -- and false in the
    quiet direction, which is why it is called out here rather than silently
    corrected. `record` had no callers anywhere in the tree: the book was filled
    once, at startup, from `prices.json`, and OpenRouter's published rates were
    parsed on every catalogue refresh and then dropped on the floor. Every
    number on the spend screen was therefore as old as the process. The refresh
    now calls `restate` and `record`, which is what makes the paragraph above
    describe the code.
    """

    def __init__(self, clock: Any = time.time) -> None:
        self._prices: dict[str, Price] = {}
        self._clock = clock

    def record(self, model: str, price: Price) -> None:
        """Take a catalogue price, unless an operator has stated one.

        The operator wins. A catalogue price is what a provider publishes for
        anybody; an operator writing a price down is stating what *they* pay,
        which is the number their budget is actually spent against.
        """
        if self._prices.get(model, price).source == "operator":
            return
        self._prices[model] = price

    def state(self, model: str, price: Price) -> None:
        """Record an operator-stated price, which nothing else overwrites."""
        self._prices[model] = price

    def restate(self, stated: Mapping[str, Price]) -> None:
        """Replace the whole operator-stated layer with what the file now says.

        Deliberately not `state` in a loop. `state` only ever adds, so a rate
        the operator *deleted* would stay in force until the next restart, and
        the file and the spend figure would disagree with nothing to show which
        was current. A withdrawn price falls back to whatever the catalogue
        publishes, or to `UNKNOWN` -- honest, where the stale number was not.

        Catalogue prices are left alone. The refresh that calls this re-records
        them immediately afterwards, and clearing them here would open a window
        where a call in flight costs `UNKNOWN` for no reason.
        """
        for model, held in list(self._prices.items()):
            if held.source == "operator" and model not in stated:
                del self._prices[model]
        for model, price in stated.items():
            self._prices[model] = price

    def price_of(self, model: str) -> Price | None:
        """The price RAVIS believes for a model, under its own id or its undated one.

        **A dated build falls back to the undated name.** Anthropic's catalogue names a
        build `claude-haiku-4-5-20251001`, and an operator writes its price the way the
        vendor's pricing page names it, `claude-haiku-4-5`. Found on 12 September 2026,
        when ranking began to read these prices: every dated build would otherwise have
        stayed unpriced, on the spend screen as much as in ranking.
        """
        found = self._prices.get(model)
        if found is not None:
            return found
        undated = _DATED_SUFFIX.sub("", model)
        return self._prices.get(undated) if undated != model else None

    def known(self) -> int:
        return len(self._prices)


# How long a usage record is kept on disk. The monthly budget reads thirty days back
# (`PERIOD_SECONDS`), so a record has to outlive that; ninety leaves a quarter to look
# back over and keeps the table from growing without end.
USAGE_RETENTION_SECONDS = 90 * 24 * 3600.0


class UsageLedger:
    """What RAVIS has spent, as far as it can tell.

    **Persisted when given a database, since 12 September 2026.** It was bounded and
    in memory, like the decision log, on the reasoning that usage is diagnostic rather
    than business state. Every restart then emptied the spend screen, and the monthly
    budget with it: a restart was enough to make a month's spending read as none. §17's
    storage model already lists `UsageRecord`. Memory stays the working set, bounded as
    before; the database is what a restart reads it back from. A record carries no
    prompt and no completion, so storing it stores neither.

    **A budget computed from this is a budget computed from what RAVIS observed**,
    which is the only thing it can honestly offer and is stated wherever the figure is
    shown.
    """

    def __init__(
        self,
        capacity: int = 5_000,
        clock: Any = time.time,
        database: Database | None = None,
        retention_seconds: float = USAGE_RETENTION_SECONDS,
    ) -> None:
        self._capacity = capacity
        self._clock = clock
        self._database = database
        self._records: list[UsageRecord] = (
            self._reload(database, retention_seconds) if database is not None else []
        )

    def record(self, entry: UsageRecord) -> UsageRecord:
        stamped = entry if entry.at else _stamped(entry, self._clock())
        self._records.append(stamped)
        if len(self._records) > self._capacity:
            del self._records[: len(self._records) - self._capacity]
        if self._database is not None:
            self._database.connection.execute(_INSERT_USAGE, _usage_row(stamped))
        return stamped

    def _reload(self, database: Database, retention_seconds: float) -> list[UsageRecord]:
        """The newest records inside retention, oldest first; older rows are dropped."""
        connection = database.connection
        cutoff = self._clock() - retention_seconds
        connection.execute("DELETE FROM usage_record WHERE at < ?", (cutoff,))
        rows = connection.execute(
            "SELECT * FROM usage_record ORDER BY at DESC, rowid DESC LIMIT ?",
            (self._capacity,),
        ).fetchall()
        return [_usage_from_row(row) for row in reversed(rows)]

    def recent(self, limit: int = 100) -> list[UsageRecord]:
        return list(reversed(self._records[-limit:]))

    def since(self, cutoff: float) -> list[UsageRecord]:
        """Every record at or after `cutoff`, oldest first.

        From the database when there is one: memory holds the newest few thousand,
        and a month's budget or a page of daily totals must not quietly stop where
        that bound happens to fall.
        """
        if self._database is None:
            return [record for record in self._records if record.at >= cutoff]
        rows = self._database.connection.execute(
            "SELECT * FROM usage_record WHERE at >= ? ORDER BY at, rowid", (cutoff,)
        ).fetchall()
        return [_usage_from_row(row) for row in rows]

    def spend(self, records: Iterable[UsageRecord] | None = None) -> tuple[float, int, int]:
        """(estimated total, records priced, records not priced).

        Three values rather than one, because a total without the count it rests
        on invites being read as complete. Twelve euros across forty calls, of
        which nine had no price, is a different statement from twelve euros
        across forty calls — and §14's rule about invoices is exactly about not
        letting the first be read as the second.

        The total is only meaningful when every record shares a currency; see
        `currencies` for what a caller must check before rendering it.
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

    def currencies(self, records: Iterable[UsageRecord] | None = None) -> set[str]:
        """Every currency the priced records are stated in.

        **`spend` adds `record.cost` without looking at `record.currency`**, and
        the usage endpoint labelled the result `"USD"` unconditionally. An
        operator pricing anything in another currency — or mixing a EUR contract
        with OpenRouter's USD catalogue figures — got euros and dollars added
        into one number and rendered with a dollar sign, and the same untyped
        total is what a budget band is compared against.

        Reported rather than converted: a rate nobody supplied is not something
        this service may invent, and §14's whole posture is that an uncertain
        figure says so instead of looking confident.
        """
        source = self._records if records is None else records
        return {record.currency for record in source
                if record.cost is not None and record.currency}


def _stamped(entry: UsageRecord, now: float) -> UsageRecord:
    """`entry` with a timestamp, since the record is frozen."""
    return replace(entry, at=now)


_INSERT_USAGE = (
    "INSERT INTO usage_record (at, model, provider, application_id, input_tokens,"
    " output_tokens, cached_input_tokens, reasoning_tokens, reported_cost, cost,"
    " cost_state, currency, price_source, price_captured_at, latency_ms, request_id,"
    " session_id, decision_id, pool) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
    " ?, ?, ?, ?)"
)


def _usage_row(record: UsageRecord) -> tuple[Any, ...]:
    """One record as a `usage_record` row, in `_INSERT_USAGE`'s column order."""
    usage = record.usage
    counts = (
        (usage.input_tokens, usage.output_tokens, usage.cached_input_tokens,
         usage.reasoning_tokens, usage.reported_cost)
        if usage is not None
        else (None, None, None, None, None)
    )
    return (
        record.at, record.model, record.provider, record.application_id, *counts,
        record.cost, record.cost_state.value, record.currency, record.price_source,
        record.price_captured_at, record.latency_ms, record.request_id,
        record.session_id, record.decision_id, record.pool,
    )


def _usage_from_row(row: Any) -> UsageRecord:
    """A stored row as the record it was.

    `usage` comes back None where every count was unreported, which is how an empty
    `Usage` and no `Usage` already read everywhere a record is shown.
    """
    counts = {
        name: row[name]
        for name in ("input_tokens", "output_tokens", "cached_input_tokens",
                     "reasoning_tokens", "reported_cost")
    }
    return UsageRecord(
        model=row["model"],
        provider=row["provider"],
        application_id=row["application_id"],
        usage=Usage(**counts) if any(value is not None for value in counts.values()) else None,
        cost=row["cost"],
        cost_state=CostState(row["cost_state"]),
        currency=row["currency"],
        price_source=row["price_source"],
        price_captured_at=row["price_captured_at"],
        latency_ms=row["latency_ms"],
        request_id=row["request_id"],
        session_id=row["session_id"],
        decision_id=row["decision_id"],
        pool=row["pool"],
        at=row["at"],
    )


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


class PriceConfigurationError(ValueError):
    """A price file that cannot be read as prices.

    Its own type for the same reason `PolicyConfigurationError` has one: a
    malformed price file must not fail open into "everything is unpriced",
    because unpriced is also what a budget treats as unconstrained.
    """


def load_prices(
    path: Path | None = None, environment: dict[str, str] | None = None
) -> dict[str, Price]:
    """Operator-stated prices, keyed by model id.

    **This exists because three of the four providers publish none.** OpenRouter
    ships per-token figures on every catalogue entry; OpenAI, Anthropic and
    Google ship catalogues with no pricing at all, so a call to any of them is
    `UNKNOWN` no matter how carefully the engine multiplies. An operator knows
    what they are paying — it is on their own contract — and stating it is
    configuration rather than invention.

    Deliberately *not* a table of published rates shipped inside RAVIS. A
    hardcoded price is a number that goes stale silently and that nobody can
    date, which is precisely what §14's price-source version and time exist to
    prevent. A file the operator wrote has both: the source is them, and the
    time is when they wrote it.

    A malformed file raises rather than yielding nothing. Failing open here
    would silently return every paid model to `UNKNOWN`, which also reads to a
    budget as "nothing has been spent".
    """
    location = path or (config_directory(environment) / "prices.json")
    try:
        with location.open(encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as failure:
        raise PriceConfigurationError(f"{location} could not be read: {failure}") from failure
    if not isinstance(payload, dict):
        raise PriceConfigurationError(f"{location} must contain an object of model → price")
    captured = location.stat().st_mtime
    return {
        str(model): _price_from(entry, f"{location}: {model}", captured)
        for model, entry in payload.items()
    }


def _price_from(entry: Any, where: str, captured_at: float) -> Price:
    """One `{input, output}` object, per million tokens.

    Per *million* rather than per token, because that is the unit a human reads
    off a pricing page — asking somebody to write `0.0000004` invites a lost
    zero, and a lost zero here is an order of magnitude on somebody's budget.
    """
    if not isinstance(entry, dict):
        raise PriceConfigurationError(f"{where} must be an object")
    unknown = set(entry) - {"input", "output", "cached_input", "currency"}
    if unknown:
        raise PriceConfigurationError(f"{where}: unrecognised {sorted(unknown)}")
    # **`cached_input` is parsed here, with the other two.** It used to be
    # converted in the `Price(...)` call below, outside this guard: a
    # non-numeric value raised a bare `ValueError`, and `PriceConfigurationError`
    # is a *subclass* of `ValueError` rather than the other way round, so
    # `ravis doctor`'s `except PriceConfigurationError` did not catch it. A
    # typo'd rate therefore made `serve` refuse to start -- `create_app` loads
    # the same file -- and then killed the one command whose docstring says it
    # exists "because the service will not start", with a traceback instead of
    # the line naming the entry. It also escaped the negativity check, so a
    # negative cached rate was accepted and lowered every cached call.
    cached = entry.get("cached_input")
    try:
        given_input = float(entry["input"])
        given_output = float(entry["output"])
        given_cached = None if cached is None else float(cached)
    except (KeyError, TypeError, ValueError) as failure:
        raise PriceConfigurationError(
            f"{where}: needs numeric 'input' and 'output', per million tokens"
            " (and a numeric 'cached_input' where one is stated)"
        ) from failure
    if given_input < 0 or given_output < 0 or (given_cached is not None and given_cached < 0):
        raise PriceConfigurationError(f"{where}: a price cannot be negative")
    return Price(
        input_per_million=given_input,
        output_per_million=given_output,
        cached_input_per_million=given_cached,
        currency=str(entry.get("currency", "USD")),
        source="operator",
        captured_at=captured_at,
    )
