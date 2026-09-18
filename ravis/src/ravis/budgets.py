"""Spending limits, as many as the owner sets: overall, per application, per provider (§14).

§14 asks for budgets that are "daily, weekly, monthly, per application, per provider". Until
18 September 2026 RAVIS had one, set by `RAVIS_BUDGET_*` (`cost.budget_from`), and nothing an
owner could change without editing the environment and restarting. This module keeps that one —
it still counts, shown as "set in configuration" and changed only where it was set — and adds any
number the owner sets from NERVIS, kept in `budgets.json` beside RAVIS's other settings.

**Each budget covers a slice of the spending ledger** and nothing else:
- `all` — everything RAVIS spent in the budget's window;
- `application` — what one client application's requests spent (`clarvis`, `nervis`, …);
- `provider` — what was spent at one provider (`anthropic`, `openai`, …).

**What a budget does depends on its slice**, and that is the design's one real decision:
- An `all` or `application` budget is a fact about *the request*: the worst band among the ones
  that cover it becomes the request's band (`RoutingPolicy.budget_band`), exactly as the single
  budget always worked — lean cheaper at 70 %, harder at 90 %, and at 100 % block paid providers
  if the budget is hard.
- A `provider` budget is a fact about *one provider's models*: at 70 % and 90 % those models rank
  lower (`RoutingPolicy.budget_pressure`), and at 100 % of a hard one they are refused
  (`RoutingPolicy.providers_over_budget`). Every other provider routes as before. Turning a
  provider budget into a request-wide band would make Anthropic's limit push Clarvis off OpenAI.

**Money is compared in the budget's own currency.** A call priced in another currency is counted
beside it as not comparable, never converted — the same rule the usage endpoint follows: a rate
nobody supplied is not something RAVIS may invent. A budget whose calls went unpriced says how
many, because §14 wants it to "fail predictably when a price is unavailable".

**A malformed file refuses to start RAVIS**, as a malformed `prices.json` does. The permissive
reading — no budgets — is the dangerous direction here: it is the one that spends.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ravis.cost import (
    PERIOD_SECONDS,
    Budget,
    BudgetBand,
    UsageLedger,
    UsageRecord,
)
from ravis.credentials import config_directory

SCOPES = ("all", "application", "provider")
PERIODS = tuple(PERIOD_SECONDS)
#: Three capital letters: USD, EUR. Checked so a typo reads as a typo, not as a currency no call
#: is ever priced in — which would leave the budget quietly counting nothing.
CURRENCY = re.compile(r"^[A-Z]{3}$")
#: Application and provider names as RAVIS spells them: lower case, digits, `-`, `_`, `.`.
TARGET = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
#: The id of the budget `RAVIS_BUDGET_*` sets. Never stored in the file, never changed from it.
CONFIGURED_ID = "configured"
BUDGET_ID = re.compile(r"^bud_[0-9a-f]{8}$")
#: No owner needs more; a list this long is a client that looped.
MAX_BUDGETS = 50

#: The bands in order of severity, so "the worst of several" is a `max`.
SEVERITY = {
    BudgetBand.NORMAL: 0,
    BudgetBand.PREFER_CHEAPER: 1,
    BudgetBand.STRONG_PENALTY: 2,
    BudgetBand.EXHAUSTED: 3,
}


class BudgetConfigurationError(ValueError):
    """A budget that can't be one: a bad field in a request, or a malformed `budgets.json`."""


@dataclass(frozen=True)
class BudgetRule:
    """One spending limit, and the slice of the ledger it covers."""

    budget_id: str
    scope: str
    target: str
    limit: float
    currency: str = "USD"
    period: str = "monthly"
    hard: bool = False
    # Set by `RAVIS_BUDGET_*` rather than stored: shown, counted, never changed from NERVIS.
    configured: bool = False

    def covers(self, record: UsageRecord) -> bool:
        if self.scope == "application":
            return record.application_id == self.target
        if self.scope == "provider":
            return record.provider == self.target
        return True

    @property
    def label(self) -> str:
        """How an explanation names it: "the monthly budget", "clarvis's weekly budget"."""
        if self.scope == "all":
            return f"the {self.period} budget"
        return f"{self.target}'s {self.period} budget"

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget_id": self.budget_id,
            "scope": self.scope,
            "target": self.target,
            "limit": self.limit,
            "currency": self.currency,
            "period": self.period,
            "hard": self.hard,
            "configured": self.configured,
        }

    def stored(self) -> dict[str, Any]:
        """What `budgets.json` keeps: everything but the flag only configuration sets."""
        kept = self.as_dict()
        kept.pop("configured")
        return kept


@dataclass(frozen=True)
class BudgetStatus:
    """Where one budget stands now: its band, and the evidence the band rests on."""

    rule: BudgetRule
    band: BudgetBand
    spent: float
    calls_priced: int
    calls_unpriced: int
    # Priced, but in another currency than the budget's, so not added and not converted.
    calls_other_currency: int

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.rule.as_dict(),
            "label": self.rule.label,
            "band": self.band.value,
            "spent_estimated": round(self.spent, 9),
            "used_fraction": round(self.spent / self.rule.limit, 4) if self.rule.limit else None,
            "calls_priced": self.calls_priced,
            "calls_unpriced": self.calls_unpriced,
            "calls_other_currency": self.calls_other_currency,
        }


def status_of(rule: BudgetRule, records: Iterable[UsageRecord]) -> BudgetStatus:
    """The band `rule` is in, from the records inside its window (the caller's to choose)."""
    spent = 0.0
    priced = unpriced = other = 0
    for record in records:
        if not rule.covers(record):
            continue
        if record.cost is None:
            unpriced += 1
        elif (record.currency or "USD") != rule.currency:
            other += 1
        else:
            spent += record.cost
            priced += 1
    band = Budget(limit=rule.limit, currency=rule.currency, period=rule.period,
                  hard=rule.hard).band(spent)
    return BudgetStatus(rule, band, spent, priced, unpriced, other)


@dataclass(frozen=True)
class RequestBudgets:
    """What the budgets say about one request, in the shape routing policy takes."""

    band: BudgetBand = BudgetBand.NORMAL
    # Whether a budget at the request's band is hard — only then does 100 % block.
    hard: bool = False
    # The budgets holding the request at its band, for the explanation.
    labels: tuple[str, ...] = ()
    # Provider → (band, hard, label), for provider budgets outside the normal band.
    providers: Mapping[str, tuple[BudgetBand, bool, str]] = field(default_factory=dict)


@dataclass
class BudgetBook:
    """The configured budget, and the owner's, persisted as ordinary configuration.

    Kept in memory and written through: RAVIS is the only writer, through `PUT
    /api/v1/budgets`, so reading the file on every routed request would pay for a question
    whose answer only changes when RAVIS itself changes it.
    """

    path: Path
    configured: BudgetRule | None = None
    _rules: list[BudgetRule] = field(default_factory=list)

    @staticmethod
    def load(settings: Any, environment: dict[str, str] | None = None) -> BudgetBook:
        """The book for these settings. Raises `BudgetConfigurationError` on a malformed file."""
        book = BudgetBook(config_directory(environment) / "budgets.json",
                          configured=_configured_rule(settings))
        book._rules = book._read()
        return book

    def rules(self) -> list[BudgetRule]:
        """Every budget in force: the configured one first, then the owner's in their order."""
        return ([self.configured] if self.configured else []) + list(self._rules)

    def revision(self) -> str:
        """A hash of the owner's budgets, for `If-Match`: two open editors can't lose an edit."""
        stored = json.dumps([rule.stored() for rule in self._rules], sort_keys=True)
        return hashlib.sha256(stored.encode("utf-8")).hexdigest()[:16]

    def replace(self, given: Any) -> list[BudgetRule]:
        """Validate `given` — the whole list — and store it in place of the owner's budgets."""
        rules = _rules_from(given, where="", existing=self._rules)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump([rule.stored() for rule in rules], handle, indent=2)
            handle.write("\n")
        os.replace(temporary, self.path)
        self._rules = rules
        return rules

    def statuses(self, ledger: UsageLedger, now: float) -> list[BudgetStatus]:
        """Every budget's standing, each over its own window."""
        windows: dict[str, list[UsageRecord]] = {}
        found = []
        for rule in self.rules():
            if rule.period not in windows:
                windows[rule.period] = ledger.since(now - PERIOD_SECONDS[rule.period])
            found.append(status_of(rule, windows[rule.period]))
        return found

    def for_request(self, application: str, ledger: UsageLedger, now: float) -> RequestBudgets:
        """The band the budgets put this application's request in, and the providers' bands."""
        worst = BudgetBand.NORMAL
        holding: list[BudgetStatus] = []
        providers: dict[str, tuple[BudgetBand, bool, str]] = {}
        for status in self.statuses(ledger, now):
            rule = status.rule
            if rule.scope == "provider":
                if status.band is not BudgetBand.NORMAL:
                    providers[rule.target] = _worse(providers.get(rule.target), status)
                continue
            if rule.scope == "application" and rule.target != application:
                continue
            if SEVERITY[status.band] > SEVERITY[worst]:
                worst, holding = status.band, [status]
            elif status.band is worst and worst is not BudgetBand.NORMAL:
                holding.append(status)
        return RequestBudgets(
            band=worst,
            hard=any(status.rule.hard for status in holding),
            labels=tuple(status.rule.label for status in holding),
            providers=providers,
        )

    def _read(self) -> list[BudgetRule]:
        try:
            with self.path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as failure:
            raise BudgetConfigurationError(f"{self.path} could not be read: {failure}") from failure
        return _rules_from(payload, where=str(self.path), existing=None)


def _worse(
    held: tuple[BudgetBand, bool, str] | None, status: BudgetStatus
) -> tuple[BudgetBand, bool, str]:
    """Of two budgets on one provider, the one further along; at a tie, the hard one."""
    offered = (status.band, status.rule.hard, status.rule.label)
    if held is None or SEVERITY[status.band] > SEVERITY[held[0]]:
        return offered
    if status.band is held[0] and status.rule.hard and not held[1]:
        return offered
    return held


def _configured_rule(settings: Any) -> BudgetRule | None:
    from ravis.cost import budget_from

    budget = budget_from(settings)
    if budget is None:
        return None
    period = budget.period if budget.period in PERIOD_SECONDS else "monthly"
    return BudgetRule(CONFIGURED_ID, "all", "", budget.limit, budget.currency.upper(), period,
                      budget.hard, configured=True)


def _rules_from(
    payload: Any, *, where: str, existing: list[BudgetRule] | None
) -> list[BudgetRule]:
    """The budgets in `payload`, validated. `existing` is None for RAVIS's own file, whose ids
    are RAVIS's; for a request, only ids RAVIS already gave out are kept."""
    if not isinstance(payload, list):
        raise BudgetConfigurationError(f"{where or 'The budgets'} must be a list of budgets")
    if len(payload) > MAX_BUDGETS:
        raise BudgetConfigurationError(f"At most {MAX_BUDGETS} budgets")
    known = None if existing is None else {rule.budget_id for rule in existing}
    rules: list[BudgetRule] = []
    for position, entry in enumerate(payload, start=1):
        rule = _rule_from(entry, f"{where}, budget {position}" if where else f"Budget {position}",
                          known)
        if any(rule.budget_id == other.budget_id for other in rules):
            rule = BudgetRule(_new_id(), rule.scope, rule.target, rule.limit, rule.currency,
                              rule.period, rule.hard)
        if any(_same_slice(rule, other) for other in rules):
            raise BudgetConfigurationError(
                f"{where + ', b' if where else 'B'}udget {position}: {rule.label} is set twice; "
                "one limit per slice and period"
            )
        rules.append(rule)
    return rules


def _same_slice(one: BudgetRule, other: BudgetRule) -> bool:
    return (one.scope, one.target, one.period) == (other.scope, other.target, other.period)


def _rule_from(entry: Any, where: str, known: set[str] | None) -> BudgetRule:
    if not isinstance(entry, Mapping):
        raise BudgetConfigurationError(f"{where} must be an object")
    unknown = set(entry) - {"budget_id", "scope", "target", "limit", "currency", "period",
                            "hard", "configured", "label"}
    if unknown:
        raise BudgetConfigurationError(f"{where}: unrecognised {sorted(unknown)}")
    scope, target = _slice_from(entry, where)
    currency = str(entry.get("currency") or "USD").upper()
    if not CURRENCY.match(currency):
        raise BudgetConfigurationError(f"{where}: currency is three letters, such as USD or EUR")
    period = entry.get("period", "monthly")
    if period not in PERIODS:
        raise BudgetConfigurationError(f"{where}: period must be one of {', '.join(PERIODS)}")
    hard = entry.get("hard", False)
    if not isinstance(hard, bool):
        raise BudgetConfigurationError(f"{where}: hard is true or false")
    return BudgetRule(_id_from(entry.get("budget_id"), known), scope, target,
                      _limit_from(entry, where), currency, period, hard)


def _slice_from(entry: Mapping[str, Any], where: str) -> tuple[str, str]:
    scope = entry.get("scope", "all")
    if scope not in SCOPES:
        raise BudgetConfigurationError(f"{where}: scope must be one of {', '.join(SCOPES)}")
    target = str(entry.get("target") or "") if scope != "all" else ""
    if scope != "all" and not TARGET.match(target):
        raise BudgetConfigurationError(
            f"{where}: a {scope} budget names the {scope} it covers, in lower case"
        )
    return str(scope), target


def _limit_from(entry: Mapping[str, Any], where: str) -> float:
    try:
        limit = float(entry["limit"])
    except (KeyError, TypeError, ValueError):
        raise BudgetConfigurationError(f"{where}: needs a numeric 'limit'") from None
    # Zero is refused rather than read as "no budget", as the environment reads it: in a list
    # somebody edits, a zero is far more likely a slip than a way of deleting the line.
    if not 0 < limit < float("inf"):
        raise BudgetConfigurationError(f"{where}: the limit must be more than zero")
    return limit


def _id_from(given: Any, known: set[str] | None) -> str:
    """An id RAVIS gave out before is kept; anything else gets a fresh one, so a client can't
    take the configured budget's id or pick ids that collide."""
    if not isinstance(given, str):
        return _new_id()
    ours = given in known if known is not None else bool(BUDGET_ID.match(given))
    return given if ours else _new_id()


def _new_id() -> str:
    return "bud_" + secrets.token_hex(4)
