"""Who may call the relay (design §3.5.1, §3.5.5; `conventions.json` → `identity_rule`).

**Two dependencies, and never both on one route.**

- `require_agent_client` runs first on every agent-session route, GETs included, and on the
  project-lock routes R4 adds. It admits only a client credential of an application in
  `agent_client_applications` (Clarvis). Anonymous callers, **every admin credential** — NERVIS's
  `admin.launcher` and the menu bar's `admin.owner_cli` included — and the `nervis` and `launcher`
  applications are refused with 403 `AGENT_CLIENT_NOT_ALLOWED`. It runs before the session token
  is looked at, so **a NERVIS or admin credential holding a valid token is still refused**.
- `require_owner_stop_caller` guards only `POST /api/v1/agent-sessions/{sid}/owner-stop`, on its
  own router (`routes.py`). It admits only an **admin** credential of an application in
  `agent_owner_stop_applications` (`owner_cli`, `launcher`), and refuses a request carrying a
  session token with 400 `TOKEN_NOT_ACCEPTED`, so the owner's capability and a window's never mix.

`tests/test_agent_sessions_identity.py` walks the application's routes and fails if any other
agent-session route lacks the first dependency, or if the owner Stop route has it.

Attributes are read directly from the resolved identity, never through `getattr(…, False)`: a
missing security predicate must raise, not quietly permit (`identity.py`, `is_anonymous`).
"""

from __future__ import annotations

from fastapi import Request

from ravis.agent import refusals
from ravis.config import Settings

TOKEN_HEADER = "x-agent-session-token"
#: Refused even if an operator lists them: NERVIS never starts, reads, steers or answers a task.
NEVER_CLIENTS = frozenset({"nervis", "launcher"})


def _settings(request: Request) -> Settings:
    return request.app.state.codex_service.settings  # type: ignore[no-any-return]


def require_agent_client(request: Request) -> None:
    """A Clarvis client credential, or 403 — in the contract's order, before any token."""
    identity = request.state.identity
    if identity.is_anonymous:
        raise refusals.client_not_allowed()
    if identity.may_write_configuration:
        raise refusals.client_not_allowed(refusals.ADMIN_REFUSED)
    if identity.application_id in NEVER_CLIENTS:
        raise refusals.client_not_allowed()
    if identity.application_id not in _settings(request).agent_client_applications:
        raise refusals.client_not_allowed()


def require_owner_stop_caller(request: Request) -> None:
    """The owner Stop's caller: an admin credential of an owner-stop application, and no token."""
    identity = request.state.identity
    allowed = _settings(request).agent_owner_stop_applications
    if not identity.may_write_configuration or identity.application_id not in allowed:
        raise refusals.owner_stop_not_allowed()
    if TOKEN_HEADER in request.headers:
        raise refusals.token_not_accepted()
