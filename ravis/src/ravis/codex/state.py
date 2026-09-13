"""Codex's one state word, and the body `GET /api/v1/codex` answers with (design §3.3).

**Exactly one state, and the earlier row wins** when several apply (`codex-state.json`):

| state | when |
|---|---|
| `checking` | before the first check after startup |
| `not_installed` | the Homebrew link doesn't lead to a file |
| `not_available` | switched off, bad signature, refused home, check failed, wrong process (§4.1) |
| `untested_version` | neither tested nor accepted; or its file rules unproven (decision D2) |
| `runtime_down` | the app-server is starting, restarting, failed to start or without its sites |
| `signed_out` | no account, after an admin sign-out or no sign-in ever |
| `sign_in_expired` | no account, after a sign-in RAVIS didn't end |
| `account_changed` | the account's fingerprint isn't the confirmed one |
| `quota_exhausted` | the plan's allowance is used up (`usage.py`) |
| `signed_in` | otherwise |

Everything here is computed from values the Codex service already holds, so the route answers in
well under the 1.5 s the launcher and NERVIS allow it, and never waits for the Codex process.

**`runs`** lists live agent sessions and Clarvis-engine project locks (design §3.3), from
`agent/sessions.py`: each task's folder name, state and times for everyone, and its `id` and
current or last `turn_id` only for a named caller — the pair the owner Stop's confirmation sends
back. Clarvis-engine locks arrive with M29's fourth increment's routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ravis.codex.account import Account, plan_name
from ravis.codex.runtime import RuntimeReport
from ravis.codex.usage import UNKNOWN, Usage, is_exhausted, iso, next_reset, usage_body

BACKEND_ID = "ravis/clarvis-codex"
#: English day and month names, so a reason reads the same whatever this Mac's locale is: a Dutch
#: locale once turned `ps` dates into "zo 13 sep." (C1's notes).
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#: Where RAVIS's default sites stand for the running process (Cal-3, `agent/sites.py`): not yet
#: written, written, not needed (no file-rules profile to write them into), or else the word
#: `SiteAllowlist.allow_defaults` gave for Codex not taking them — `overridden`, `not_written` or
#: `codex_did_not_answer`.
SITES_PENDING = "pending"
SITES_WRITTEN = "written"
SITES_NOT_NEEDED = "not_needed"


@dataclass(frozen=True)
class ProcessFacts:
    """What the supervisor says about the one Codex process."""

    state: str
    since: datetime | None
    restarts_24h: int
    active_turns: int
    running_sha256: str | None
    failure: str | None
    identity_failure: str | None


@dataclass(frozen=True)
class ModelFacts:
    """One model Codex offers this account, as `model/list` describes it (brief §4)."""

    id: str
    display_name: str
    is_default: bool
    default_effort: str | None
    efforts: tuple[str, ...]


@dataclass(frozen=True)
class Reading:
    """Everything the state and the body are computed from, taken at one moment."""

    report: RuntimeReport
    process: ProcessFacts
    #: Whether the running process has answered `account/read` yet. Until it has, RAVIS doesn't
    #: know signed in from signed out, and says the process is still starting.
    account_read: bool
    account: Account | None
    confirmed_fingerprint: str | None
    confirmed_plan: str | None
    signed_out_on_purpose: bool
    usage: Usage
    models: tuple[ModelFacts, ...]
    sign_in: dict[str, Any]
    home_fingerprint: str
    now: datetime
    #: `SITES_PENDING`, `SITES_WRITTEN`, `SITES_NOT_NEEDED`, or why Codex didn't take the sites.
    default_sites: str


def decide(reading: Reading) -> tuple[str, str]:
    """The state word and the sentence that explains it, by the table above."""
    for row in (_runtime_row, _process_row, _account_row):
        found = row(reading)
        if found is not None:
            return found
    return _allowance_row(reading)


def _runtime_row(reading: Reading) -> tuple[str, str] | None:
    report = reading.report
    if report.state in ("checking", "not_installed", "not_available"):
        return report.state, report.reason
    if reading.process.identity_failure is not None:
        return "not_available", reading.process.identity_failure
    if report.state == "untested_version":
        running = reading.process.running_sha256
        if running is not None and running != report.installed_sha256:
            return "untested_version", (
                f"Codex changed (now {report.version}) and needs re-testing before new work."
            )
        return "untested_version", report.reason
    return None


def _process_row(reading: Reading) -> tuple[str, str] | None:
    process = reading.process
    if process.state == "running" and reading.account_read:
        return _sites_row(reading.default_sites)
    if process.state == "failed":
        return "runtime_down", (
            "Codex's process failed five times in 30 minutes, so RAVIS has stopped restarting it "
            f"until Codex changes or RAVIS restarts. Last: {process.failure}"
        )
    if process.state == "restarting" and process.failure:
        return "runtime_down", f"Codex's process is restarting after: {process.failure}"
    return "runtime_down", "Codex's process is starting."


def _sites_row(sites: str) -> tuple[str, str] | None:
    """A running Codex takes no task until it has RAVIS's default sites (Cal-3)."""
    if sites in (SITES_WRITTEN, SITES_NOT_NEEDED):
        return None
    if sites == SITES_PENDING:
        return "runtime_down", "Codex's process is starting."
    return "runtime_down", (
        f"Codex's process is running, but it didn't take RAVIS's default sites ({sites}), so no "
        "task can start. RAVIS writes them again the next time Codex starts."
    )


def _account_row(reading: Reading) -> tuple[str, str] | None:
    account = reading.account
    if account is None:
        if reading.confirmed_fingerprint is not None and not reading.signed_out_on_purpose:
            return "sign_in_expired", (
                "OpenAI signed Codex out; sign in again from the dashboard or the menu bar."
            )
        return "signed_out", "Codex is signed out."
    if account.fingerprint != reading.confirmed_fingerprint:
        return "account_changed", (
            "Codex is signed in to a different account than the one you confirmed."
        )
    return None


def _allowance_row(reading: Reading) -> tuple[str, str]:
    if is_exhausted(reading.usage, reading.now):
        return "quota_exhausted", used_up_reason(reading.usage, reading.now)
    plan = plan_name(reading.account.plan if reading.account else None)
    if plan is None:
        return "signed_in", "Codex is signed in with ChatGPT."
    return "signed_in", f"Codex is signed in with a ChatGPT {plan} plan."


def used_up_reason(usage: Usage, now: datetime) -> str:
    """"The ChatGPT plan's allowance is used up until 04:30." — with the reset when it's known."""
    reset = next_reset(usage, now)
    until = f" until {reset_phrase(reset, now)}" if reset is not None else ""
    return f"The ChatGPT plan's allowance is used up{until}."


def reset_phrase(moment: datetime, now: datetime) -> str:
    """`04:30` today, `Thu 17 Sep 11:00` on another day — in this Mac's own time zone."""
    local, today = moment.astimezone(), now.astimezone()
    clock = f"{local:%H:%M}"
    if local.date() == today.date():
        return clock
    return f"{DAYS[local.weekday()]} {local.day} {MONTHS[local.month - 1]} {clock}"


def codex_body(
    reading: Reading, *, revision: int, runs: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The whole `GET /api/v1/codex` body, in `codex-state.json`'s shape and key order."""
    state, reason = decide(reading)
    signed_in = reading.account is not None
    return {
        "backend_id": BACKEND_ID,
        "execution": "delegated_agent",
        "roles": ["agent"],
        "state": state,
        "reason": reason,
        "revision": revision,
        "runtime": _runtime_block(reading),
        "home": {"fingerprint": reading.home_fingerprint},
        "account": _account_block(reading),
        "usage": usage_body(
            reading.usage if signed_in else UNKNOWN,
            reading.now,
            turns_active=reading.process.active_turns > 0,
        ),
        "runs": runs or [],
        "models": [_model_block(model) for model in reading.models] if signed_in else [],
        "sign_in": reading.sign_in,
    }


def _runtime_block(reading: Reading) -> dict[str, Any]:
    report, process = reading.report, reading.process
    return {
        "source": report.source,
        "version": report.version,
        "installed_sha256": report.installed_sha256,
        "running_sha256": process.running_sha256,
        "team_id": report.team_id,
        "verdict": report.verdict,
        "strict_rules": report.strict_rules,
        "schema": {
            "stable_tree": report.stable_tree,
            "experimental_tree": report.experimental_tree,
        },
        "process": {
            "state": process.state,
            "since": iso(process.since),
            "restarts_24h": process.restarts_24h,
            "active_turns": process.active_turns,
        },
        "checked_at": report.checked_at,
    }


def _account_block(reading: Reading) -> dict[str, Any] | None:
    account = reading.account
    if account is None:
        return None
    matches = account.fingerprint == reading.confirmed_fingerprint
    return {
        "signed_in": True,
        "auth_mode": account.auth_mode,
        "plan": account.plan,
        "email_hint": account.email_hint,
        "fingerprint": account.strength,
        # The fingerprint itself, so a window can compare a checkpoint's account with the current
        # one (C3); a sha256, never the account. Named callers only (`CodexService.snapshot`).
        "fingerprint_sha256": account.fingerprint,
        "fingerprint_matches": matches,
        # A plan change is a notice only (design §3.4), and only means something for one account.
        "plan_changed": matches
        and reading.confirmed_plan is not None
        and account.plan != reading.confirmed_plan,
        "checked_at": iso(account.checked_at),
    }


def _model_block(model: ModelFacts) -> dict[str, Any]:
    return {
        "id": model.id,
        "display_name": model.display_name,
        "is_default": model.is_default,
        "default_effort": model.default_effort,
        "efforts": list(model.efforts),
    }


def models_from_list(result: object) -> tuple[ModelFacts, ...]:
    """The visible models of a `model/list` answer; hidden ones are Codex's to keep hidden."""
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        return ()
    return tuple(
        _model_facts(entry)
        for entry in data
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) and not entry.get("hidden")
    )


def _model_facts(entry: dict[str, Any]) -> ModelFacts:
    efforts = entry.get("supportedReasoningEfforts")
    effort_names = tuple(
        effort["reasoningEffort"]
        for effort in (efforts if isinstance(efforts, list) else [])
        if isinstance(effort, dict) and isinstance(effort.get("reasoningEffort"), str)
    )
    default_effort = entry.get("defaultReasoningEffort")
    display = entry.get("displayName")
    return ModelFacts(
        id=entry["id"],
        display_name=display if isinstance(display, str) else entry["id"],
        is_default=entry.get("isDefault") is True,
        default_effort=default_effort if isinstance(default_effort, str) else None,
        efforts=effort_names,
    )
