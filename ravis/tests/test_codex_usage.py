"""The ChatGPT plan's allowance, and who the account is (design §3.3, §3.4).

The rules held here are the contract's (`codex-state.json`'s `usage_rules` and `states`): unknown
is never zero; `remaining_percent = max(0, 100 - round(usedPercent))`, labelled from the window's
length; stale after 30 minutes without a reading while idle; used up only by the four named rules,
and never by credits alone (review M6) or by throttling (design §9).

**The unit of `resetsAt` is pinned here**, as the design asks: whole Unix seconds. The fixture's
`2026-09-13T04:30:00Z` is 1789273800 seconds. Calibration confirms it against a real reading.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ravis.codex.account import account_fingerprint, account_from_read, email_hint
from ravis.codex.usage import (
    UNKNOWN,
    from_read,
    is_exhausted,
    is_stale,
    merged,
    next_reset,
    reset_time,
    usage_body,
    window_label,
)

NOW = datetime(2026, 9, 13, 1, 54, tzinfo=UTC)
#: `2026-09-13T04:30:00Z` and `2026-09-17T09:00:00Z`, the fixture's two resets, as whole seconds.
PRIMARY_RESET = 1789273800
SECONDARY_RESET = 1789635600


def read(**snapshot: Any) -> dict[str, Any]:
    """An `account/rateLimits/read` answer, in the schema's shape."""
    rate_limits = {
        "limitId": "codex",
        "primary": {"usedPercent": 38, "windowDurationMins": 300, "resetsAt": PRIMARY_RESET},
        "secondary": {
            "usedPercent": 20, "windowDurationMins": 10080, "resetsAt": SECONDARY_RESET,
        },
        "credits": {"hasCredits": False, "unlimited": False, "balance": None},
        "individualLimit": None,
        "rateLimitReachedType": None,
        "spendControlReached": None,
        **snapshot,
    }
    return {"rateLimits": rate_limits, "accountId": "acct-1", "ordinaryUsageAllowed": True}


def test_a_read_becomes_the_fixtures_usage_block() -> None:
    body = usage_body(from_read(read(), NOW), NOW, turns_active=False)

    assert body == {
        "known": True,
        "source": "read",
        "observed_at": "2026-09-13T01:54:00Z",
        "stale": False,
        "allowance_not_cost": True,
        "limit_reached": None,
        "spend_control_reached": None,
        "windows": [
            {"id": "primary", "label": "5-hour window", "duration_minutes": 300,
             "used_percent": 38, "remaining_percent": 62, "resets_at": "2026-09-13T04:30:00Z"},
            {"id": "secondary", "label": "weekly window", "duration_minutes": 10080,
             "used_percent": 20, "remaining_percent": 80, "resets_at": "2026-09-17T09:00:00Z"},
        ],
        "individual_limit": None,
        "credits": {"has_credits": False, "unlimited": False, "balance": None},
    }


def test_reset_times_are_whole_unix_seconds() -> None:
    assert reset_time(PRIMARY_RESET) == datetime(2026, 9, 13, 4, 30, tzinfo=UTC)


def test_a_reset_that_is_no_plausible_time_in_seconds_is_unknown_not_wrong() -> None:
    """A millisecond value would read as the year 58,000; unknown is the honest reading."""
    assert reset_time(PRIMARY_RESET * 1000) is None
    assert reset_time(True) is None
    assert reset_time(None) is None


def test_unknown_usage_is_never_zero() -> None:
    body = usage_body(UNKNOWN, NOW, turns_active=False)

    assert body["known"] is False
    assert body["windows"] == []
    assert body["credits"] is None
    assert "remaining_percent" not in str(body)


def test_remaining_is_rounded_and_never_below_zero() -> None:
    over = from_read(read(primary={"usedPercent": 130, "windowDurationMins": 300}), NOW)
    near = from_read(read(primary={"usedPercent": 37, "windowDurationMins": 300}), NOW)

    assert over.windows[0].remaining_percent == 0
    assert near.windows[0].remaining_percent == 63


def test_window_labels_come_from_the_windows_length() -> None:
    def labelled(minutes: int | None) -> str:
        return window_label(from_read(read(primary={
            "usedPercent": 1, "windowDurationMins": minutes,
        }), NOW).windows[0])

    assert labelled(300) == "5-hour window"
    assert labelled(10080) == "weekly window"
    assert labelled(20160) == "2-week window"
    assert labelled(1440) == "daily window"
    assert labelled(90) == "90-minute window"
    assert labelled(None) == "primary window"


def test_a_notification_is_merged_sparsely_and_its_nulls_clear_nothing() -> None:
    """The schema: nullable fields "may be unavailable in a rolling update" and don't clear."""
    before = from_read(read(rateLimitReachedType="rate_limit_reached"), NOW)
    later = NOW + timedelta(minutes=3)
    update = {"rateLimits": {
        "primary": {"usedPercent": 45, "windowDurationMins": 300, "resetsAt": PRIMARY_RESET},
        "secondary": None, "credits": None, "rateLimitReachedType": None,
    }}

    after = merged(before, update, later)

    assert after.source == "notification"
    assert after.observed_at == later
    assert after.primary is not None and after.primary.used_percent == 45
    assert after.secondary == before.secondary
    assert after.credits == before.credits
    assert after.limit_reached == "rate_limit_reached"


def test_each_of_the_four_rules_means_used_up() -> None:
    assert is_exhausted(from_read(read(rateLimitReachedType="rate_limit_reached"), NOW), NOW)
    assert is_exhausted(from_read(read(spendControlReached=True), NOW), NOW)
    assert is_exhausted(from_read(read(individualLimit={
        "limit": 10, "used": 10, "remainingPercent": 0, "resetsAt": SECONDARY_RESET,
    }), NOW), NOW)
    assert is_exhausted(from_read(read(primary={
        "usedPercent": 100, "windowDurationMins": 300, "resetsAt": PRIMARY_RESET,
    }), NOW), NOW)


def test_credits_alone_and_a_full_window_already_reset_are_not_used_up() -> None:
    no_credits = read(credits={"hasCredits": False, "unlimited": False, "balance": "0"})
    reset_passed = read(primary={
        "usedPercent": 100, "windowDurationMins": 300, "resetsAt": PRIMARY_RESET,
    })

    assert not is_exhausted(from_read(no_credits, NOW), NOW)
    assert not is_exhausted(from_read(reset_passed, NOW), datetime(2026, 9, 13, 5, tzinfo=UTC))
    assert not is_exhausted(UNKNOWN, NOW)


def test_the_next_reset_is_the_soonest_one_still_ahead() -> None:
    usage = from_read(read(), NOW)

    assert next_reset(usage, NOW) == datetime(2026, 9, 13, 4, 30, tzinfo=UTC)
    assert next_reset(usage, datetime(2026, 9, 14, tzinfo=UTC)) == datetime(
        2026, 9, 17, 9, tzinfo=UTC
    )


def test_a_reading_goes_stale_after_thirty_idle_minutes_but_not_while_turns_run() -> None:
    usage = from_read(read(), NOW)
    later = NOW + timedelta(minutes=31)

    assert not is_stale(usage, NOW + timedelta(minutes=29), turns_active=False)
    assert is_stale(usage, later, turns_active=False)
    assert not is_stale(usage, later, turns_active=True)
    assert not is_stale(UNKNOWN, later, turns_active=False)


# ── Who the account is ─────────────────────────────────────────────────────


def test_the_fingerprint_is_the_sha256_of_the_lowercased_email_with_its_prefix() -> None:
    import hashlib

    expected = hashlib.sha256(b"ravis-codex-account:owner@example.com").hexdigest()

    assert account_fingerprint("chatgpt", "Owner@Example.com", "acct-1", "plus") == (
        expected, "strong",
    )


def test_with_no_email_the_fingerprint_is_the_account_ids_and_still_strong() -> None:
    import hashlib

    assert account_fingerprint("chatgpt", None, "acct-1", "plus") == (
        hashlib.sha256(b"acct-1").hexdigest(), "strong",
    )


def test_with_neither_the_fingerprint_is_weak_and_plan_only() -> None:
    fingerprint, strength = account_fingerprint("chatgpt", None, None, "plus")

    assert strength == "weak"
    assert fingerprint == account_fingerprint("chatgpt", None, None, "plus")[0]
    assert fingerprint != account_fingerprint("chatgpt", None, None, "pro")[0]


def test_the_email_hint_shows_the_first_letter_and_the_domain_only() -> None:
    assert email_hint("owner@example.com") == "o…@example.com"
    assert email_hint(None) is None


def test_a_signed_out_answer_is_no_account_and_a_signed_in_one_keeps_no_email_in_view() -> None:
    signed_in = {"account": {"type": "chatgpt", "email": "owner@example.com", "planType": "plus"}}

    account = account_from_read(signed_in, None, NOW)

    assert account_from_read({"account": None, "requiresOpenaiAuth": True}, None, NOW) is None
    assert account is not None
    assert (account.auth_mode, account.plan, account.email_hint) == (
        "chatgpt", "plus", "o…@example.com",
    )
