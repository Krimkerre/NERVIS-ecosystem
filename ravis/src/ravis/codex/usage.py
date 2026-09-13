"""The ChatGPT plan's allowance as Codex reports it: its windows, its limits, when each resets.

Owner requirement (`design/codex-engine/constraints.md`): the menu bar and the dashboard show the
subscription's **remaining usage**, and it is **an allowance, not money** — so it is never mixed
with RAVIS's estimated API spend and never shown as a $0 cost (runbook §2.2 invariant 5).

**Where the figures come from** (design §3.3). One Codex process serves everything, so it is also
the one place to ask:
- `account/rateLimits/read` answers `{rateLimits: RateLimitSnapshot, accountId?, …}`;
- while turns run, Codex pushes `account/rateLimits/updated {rateLimits}`, a **sparse** update:
  "nullable account metadata may be unavailable in a rolling update and does not clear a
  previously observed value" (the schema's own words), so a missing or null field keeps what the
  last reading said.

**The rules this module keeps** (design §3.3, `codex-state.json`'s `usage_rules`):
- **Unknown is never zero.** No reading yet means `known: false`, no windows, no percentages.
- `remaining_percent = max(0, 100 - round(usedPercent))`, labelled from `windowDurationMins`.
- **Stale** after 30 minutes without a reading while no turn runs.
- **Used up** (`quota_exhausted`) when any of: `rateLimitReachedType` is set; a window is at 100% or
  more with a reset still ahead; the individual limit has nothing left; `spendControlReached` is
  true. **Credits alone never count** (review M6), and throttling never does: a turn retried on
  `rateLimitExceeded` is OpenAI asking Codex to slow down, not the plan running out (design §9).

**`resetsAt` is whole Unix seconds.** The schema gives it as an int64 without a unit; seconds is
Codex's convention where a name carries no `Ms` suffix (`currentTimeAt` says so in words), and
`tests/test_codex_usage.py` pins it. A value that isn't a plausible time in seconds is treated as
an unknown reset — never as a wrong one — and calibration confirms the unit against a real reading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

logger = logging.getLogger("ravis")

#: A reading older than this, while no turn runs, is stale (design §3.3).
STALE_AFTER = timedelta(minutes=30)
#: The latest reset time read as plausible whole seconds: the year 3000.
_LATEST_PLAUSIBLE_SECONDS = 32503680000

Source = Literal["read", "notification"]


@dataclass(frozen=True)
class Window:
    """One allowance window: `primary` (the short one) or `secondary` (the long one)."""

    id: str
    used_percent: int
    duration_minutes: int | None
    resets_at: datetime | None

    @property
    def remaining_percent(self) -> int:
        return max(0, 100 - round(self.used_percent))


@dataclass(frozen=True)
class IndividualLimit:
    """A per-member spend-control limit a workspace can set; `remaining_percent` decides."""

    limit: Any
    used: Any
    remaining_percent: float | None
    resets_at: datetime | None


@dataclass(frozen=True)
class Credits:
    """Purchased credits. Shown, and never counted towards "used up" on their own (review M6)."""

    has_credits: bool
    unlimited: bool
    balance: str | None


@dataclass(frozen=True)
class Usage:
    """The last known allowance. `observed_at` None is "unknown", which is never zero."""

    source: Source | None = None
    observed_at: datetime | None = None
    limit_reached: str | None = None
    spend_control_reached: bool | None = None
    primary: Window | None = None
    secondary: Window | None = None
    individual_limit: IndividualLimit | None = None
    credits: Credits | None = None
    #: The account id a read named: the fingerprint's source when the account has no email.
    account_id: str | None = None

    @property
    def known(self) -> bool:
        return self.observed_at is not None

    @property
    def windows(self) -> tuple[Window, ...]:
        return tuple(window for window in (self.primary, self.secondary) if window is not None)


UNKNOWN = Usage()


def from_read(result: object, now: datetime) -> Usage:
    """The allowance from an `account/rateLimits/read` answer; unknown if it carried none."""
    if not isinstance(result, dict) or not isinstance(result.get("rateLimits"), dict):
        return UNKNOWN
    account_id = result.get("accountId")
    return replace(
        _snapshot(result["rateLimits"]),
        source="read",
        observed_at=now,
        account_id=account_id if isinstance(account_id, str) else None,
    )


def merged(previous: Usage, update: object, now: datetime) -> Usage:
    """`previous` with an `account/rateLimits/updated` notification merged in, sparsely."""
    if not isinstance(update, dict) or not isinstance(update.get("rateLimits"), dict):
        return previous
    carried = _snapshot(update["rateLimits"])
    fields = {
        name: getattr(carried, name)
        for name in (
            "limit_reached", "spend_control_reached", "primary", "secondary",
            "individual_limit", "credits",
        )
        if getattr(carried, name) is not None
    }
    return replace(previous, source="notification", observed_at=now, **fields)


def _snapshot(raw: dict[str, Any]) -> Usage:
    reached = raw.get("rateLimitReachedType")
    spend = raw.get("spendControlReached")
    return Usage(
        limit_reached=reached if isinstance(reached, str) else None,
        spend_control_reached=spend if isinstance(spend, bool) else None,
        primary=_window("primary", raw.get("primary")),
        secondary=_window("secondary", raw.get("secondary")),
        individual_limit=_individual_limit(raw.get("individualLimit")),
        credits=_credits(raw.get("credits")),
    )


def _window(window_id: str, raw: object) -> Window | None:
    if not isinstance(raw, dict) or not _is_int(raw.get("usedPercent")):
        return None
    duration = raw.get("windowDurationMins")
    return Window(
        id=window_id,
        used_percent=raw["usedPercent"],
        duration_minutes=duration if _is_int(duration) else None,
        resets_at=reset_time(raw.get("resetsAt")),
    )


def _individual_limit(raw: object) -> IndividualLimit | None:
    if not isinstance(raw, dict):
        return None
    remaining = raw.get("remainingPercent")
    return IndividualLimit(
        limit=raw.get("limit"),
        used=raw.get("used"),
        remaining_percent=remaining if _is_number(remaining) else None,
        resets_at=reset_time(raw.get("resetsAt")),
    )


def _credits(raw: object) -> Credits | None:
    if not isinstance(raw, dict):
        return None
    balance = raw.get("balance")
    return Credits(
        has_credits=raw.get("hasCredits") is True,
        unlimited=raw.get("unlimited") is True,
        balance=balance if isinstance(balance, str) else None,
    )


def reset_time(value: object) -> datetime | None:
    """A `resetsAt` value — whole Unix seconds — as a time; None when absent or implausible."""
    if not _is_int(value):
        return None
    if not 0 < value < _LATEST_PLAUSIBLE_SECONDS:  # type: ignore[operator]
        logger.warning("codex: a resetsAt of %r is not a time in seconds; reset unknown", value)
        return None
    return datetime.fromtimestamp(value, UTC)  # type: ignore[arg-type]


def is_exhausted(usage: Usage, now: datetime) -> bool:
    """Whether the plan's allowance is used up, by the four rules above. Credits never decide."""
    if usage.limit_reached is not None or usage.spend_control_reached is True:
        return True
    limit = usage.individual_limit
    if limit is not None and limit.remaining_percent is not None and limit.remaining_percent <= 0:
        return True
    return any(
        window.used_percent >= 100 and window.resets_at is not None and window.resets_at > now
        for window in usage.windows
    )


def next_reset(usage: Usage, now: datetime) -> datetime | None:
    """The soonest reset still ahead: when a used-up allowance may be back, worth reading then."""
    times = [window.resets_at for window in usage.windows]
    if usage.individual_limit is not None:
        times.append(usage.individual_limit.resets_at)
    ahead = [moment for moment in times if moment is not None and moment > now]
    return min(ahead, default=None)


def is_stale(usage: Usage, now: datetime, *, turns_active: bool) -> bool:
    """A reading older than 30 minutes while no turn runs (and so no notification can arrive)."""
    observed = usage.observed_at
    return observed is not None and not turns_active and now - observed > STALE_AFTER


def window_label(window: Window) -> str:
    """`5-hour window`, `weekly window` and so on, from the window's length (design §3.3)."""
    minutes = window.duration_minutes
    if minutes is None or minutes <= 0:
        return f"{window.id} window"
    for size, one, many in (
        (10080, "weekly window", "week"),
        (1440, "daily window", "day"),
        (60, "1-hour window", "hour"),
    ):
        if minutes % size == 0:
            count = minutes // size
            return one if count == 1 else f"{count}-{many} window"
    return f"{minutes}-minute window"


def usage_body(usage: Usage, now: datetime, *, turns_active: bool) -> dict[str, Any]:
    """The `usage` block of `GET /api/v1/codex`, shaped as `codex-state.json` shows it."""
    known = usage.known
    return {
        "known": known,
        "source": usage.source,
        "observed_at": iso(usage.observed_at),
        "stale": is_stale(usage, now, turns_active=turns_active),
        "allowance_not_cost": True,
        "limit_reached": usage.limit_reached,
        "spend_control_reached": usage.spend_control_reached,
        "windows": [_window_body(window) for window in usage.windows] if known else [],
        "individual_limit": _limit_body(usage.individual_limit),
        "credits": _credits_body(usage.credits),
    }


def _window_body(window: Window) -> dict[str, Any]:
    return {
        "id": window.id,
        "label": window_label(window),
        "duration_minutes": window.duration_minutes,
        "used_percent": window.used_percent,
        "remaining_percent": window.remaining_percent,
        "resets_at": iso(window.resets_at),
    }


def _limit_body(limit: IndividualLimit | None) -> dict[str, Any] | None:
    if limit is None:
        return None
    return {
        "limit": limit.limit,
        "used": limit.used,
        "remaining_percent": limit.remaining_percent,
        "resets_at": iso(limit.resets_at),
    }


def _credits_body(credits: Credits | None) -> dict[str, Any] | None:
    if credits is None:
        return None
    return {
        "has_credits": credits.has_credits,
        "unlimited": credits.unlimited,
        "balance": credits.balance,
    }


def iso(moment: datetime | None) -> str | None:
    """ISO-8601 UTC with a `Z`, to the second, as every Codex route writes times."""
    if moment is None:
        return None
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)
