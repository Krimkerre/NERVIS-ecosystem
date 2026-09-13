"""Who RAVIS's Codex is signed in as, reduced to what RAVIS may keep and show (design §3.4).

`account/read` answers `{account, requiresOpenaiAuth}`, where a ChatGPT sign-in is
`{type: "chatgpt", email, planType}` (brief §3). RAVIS needs two things from it, and must not
keep the rest:

- **Which account, as a fingerprint**, so a swap is noticed (runbook §2.2 invariant 7: a change of
  ChatGPT account pauses new tasks until the owner confirms it). The fingerprint is
  `sha256("ravis-codex-account:" + lowercase(email))`. When the email is null it is the sha256 of
  the account id the allowance read names; when neither exists it is a **weak**, plan-only
  fingerprint, and the dashboard says so (review H4, N10). It catches an accidental swap, not a
  determined local program — RAVIS's admin routes are UX and audit, not a boundary (§2.2 fact 14).
- **An email hint** for the screen: `o…@example.com`, the first letter and the domain.

**The email itself lives only in memory**, as long as the answer it came in. It is never written
to disk, to a log, to an event, or into any response: the credentials screen and the Codex card
show the hint, and nothing shows a token, because RAVIS never has one (Codex keeps its own
`auth.json`, which RAVIS never opens).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Strength = Literal["strong", "weak"]

#: Codex's account types (brief §3) and the `auth_mode` word the state route uses for each.
AUTH_MODES = {"chatgpt": "chatgpt", "apiKey": "apikey", "amazonBedrock": "bedrock"}
#: How each plan is named in a sentence; a plan not listed is named from its own word.
PLAN_NAMES = {
    "free": "Free", "go": "Go", "plus": "Plus", "pro": "Pro", "prolite": "Pro Lite",
    "team": "Team", "business": "Business", "enterprise": "Enterprise", "edu": "Edu",
}


@dataclass(frozen=True)
class Account:
    """The signed-in account, as far as RAVIS may know it."""

    auth_mode: str
    plan: str | None
    #: Memory only — see the module docstring.
    email: str | None
    fingerprint: str
    strength: Strength
    checked_at: datetime

    @property
    def email_hint(self) -> str | None:
        return email_hint(self.email)


def email_hint(email: str | None) -> str | None:
    """`o…@example.com` for `owner@example.com`: enough to recognise, too little to use."""
    if not email:
        return None
    local, at, domain = email.partition("@")
    return f"{local[:1]}…{at}{domain}"


def needs_account_id(result: object) -> bool:
    """Whether a signed-in answer has no email, so the fingerprint needs the allowance's id."""
    account = result.get("account") if isinstance(result, dict) else None
    return isinstance(account, dict) and not account.get("email")


def account_from_read(result: object, account_id: str | None, now: datetime) -> Account | None:
    """The account an `account/read` answer names; None when Codex is signed out."""
    account = result.get("account") if isinstance(result, dict) else None
    if not isinstance(account, dict):
        return None
    kind = str(account.get("type") or "unknown")
    email = account.get("email") if isinstance(account.get("email"), str) else None
    plan = account.get("planType") if isinstance(account.get("planType"), str) else None
    fingerprint, strength = account_fingerprint(kind, email, account_id, plan)
    return Account(
        auth_mode=AUTH_MODES.get(kind, kind),
        plan=plan,
        email=email,
        fingerprint=fingerprint,
        strength=strength,
        checked_at=now,
    )


def account_fingerprint(
    kind: str, email: str | None, account_id: str | None, plan: str | None
) -> tuple[str, Strength]:
    """The fingerprint and how much it proves, by the rule in the module docstring."""
    if email:
        return _sha256("ravis-codex-account:" + email.lower()), "strong"
    if account_id:
        return _sha256(account_id), "strong"
    return _sha256(f"ravis-codex-plan:{kind}:{plan}"), "weak"


def plan_name(plan: str | None) -> str | None:
    """`Plus` for `plus`; a plan Codex adds later reads from its own word."""
    if plan is None:
        return None
    return PLAN_NAMES.get(plan, plan.replace("_", " ").title())


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
