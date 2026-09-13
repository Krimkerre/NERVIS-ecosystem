"""Signing RAVIS's Codex in to a ChatGPT plan, in the browser (design §3.4, §4.6; brief §3).

**How Codex's browser sign-in works** (brief §3, observed in the probe):
1. `account/login/start {type: "chatgpt"}` answers at once with a `loginId` and an `authUrl` on
   `https://auth.openai.com/…`, whose `redirect_uri` is `http://localhost:1455/auth/callback`.
2. The app-server process itself listens on `127.0.0.1:1455` — or `1457` if 1455 is taken, and
   fails with "already in use" if both are. It never opens a browser: RAVIS hands the address to
   whoever asked (the launcher opens it; the dashboard shows it as a link).
3. `account/login/completed {loginId, success, error}` says how it ended. Codex abandons a sign-in
   after **10 minutes**, and `account/login/cancel {loginId}` ends one early.

**Ports.** The ChatGPT app runs its own Codex on this Mac, and its sign-in uses the same two ports.
So before starting, RAVIS tries to bind each: both taken is reported plainly — "Another program is
holding the sign-in ports 1455 and 1457" — rather than as whatever error Codex would give; one
taken is fine, since Codex falls back (design §4.6, review L1).

**What can be shown, and to whom.** The address is a live way into the sign-in until it ends, so it
is served only on the admin route (`GET`/`POST /api/v1/codex/sign-in`), which the dashboard
reaches through NERVIS's control route. The public state (`GET /api/v1/codex`) says only whether a
sign-in waits, when it started and when it expires.

**A RAVIS restart loses a sign-in**, because the listener lived in the Codex process RAVIS was
running. RAVIS notes a sign-in as started in its record, and the next start reports it as failed
with "RAVIS restarted during sign-in" (design §3.4), instead of pretending none was under way.
"""

from __future__ import annotations

import contextlib
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

from ravis.codex.usage import iso

#: The ports Codex's sign-in listens on: the first, and its fallback (brief §3).
SIGN_IN_PORTS = (1455, 1457)
#: Codex abandons a sign-in after this long (brief §3, `LOGIN_CHATGPT_TIMEOUT`).
SIGN_IN_LIFETIME = timedelta(minutes=10)
#: What a restart leaves behind, word for word as the contract gives it (`codex-admin.json`).
RESTARTED_DURING_SIGN_IN = "RAVIS restarted during sign-in"
#: What a sign-in left waiting past its ten minutes says.
SIGN_IN_EXPIRED = "The sign-in page expired after 10 minutes; start the sign-in again."

SignInState = Literal["idle", "waiting_for_browser", "failed"]
Outcome = Literal["signed_in", "failed", "ignored"]


def busy_ports(ports: tuple[int, ...] = SIGN_IN_PORTS, host: str = "127.0.0.1") -> list[int]:
    """Which of `ports` another program is holding, found by trying to bind each and letting go.

    No `SO_REUSEADDR`: any listener on the port, on this address or on all of them, makes the bind
    fail, which is the conflict Codex itself would hit.
    """
    busy = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
            except OSError:
                busy.append(port)
    return busy


def callback_port(auth_url: str, fallback: int) -> int:
    """The port the sign-in page will call back on, read from its `redirect_uri`."""
    with contextlib.suppress(ValueError, IndexError):
        redirect = parse_qs(urlsplit(auth_url).query).get("redirect_uri", [""])[0]
        port = urlsplit(redirect).port
        if port is not None:
            return port
    return fallback


@dataclass
class SignIn:
    """The one sign-in RAVIS may have under way, and how the last one ended."""

    state: SignInState = "idle"
    login_id: str | None = None
    auth_url: str | None = None
    callback_port: int | None = None
    started_at: datetime | None = None
    expires_at: datetime | None = None
    error: str | None = None

    @property
    def waiting(self) -> bool:
        return self.state == "waiting_for_browser"

    def begin(
        self,
        login_id: str,
        auth_url: str,
        port: int,
        now: datetime,
        lifetime: timedelta = SIGN_IN_LIFETIME,
    ) -> None:
        self.state = "waiting_for_browser"
        self.login_id = login_id
        self.auth_url = auth_url
        self.callback_port = port
        self.started_at = now
        self.expires_at = now + lifetime
        self.error = None

    def completed(self, params: dict[str, Any]) -> Outcome:
        """Apply `account/login/completed`; `ignored` when it isn't about the sign-in under way.

        A cancelled or expired sign-in has already left `waiting_for_browser`, so Codex's "not
        completed" notice for it is ignored here; so is one for an older login id that arrives after
        a new sign-in began.
        """
        login_id = params.get("loginId")
        if not self.waiting or (isinstance(login_id, str) and login_id != self.login_id):
            return "ignored"
        if params.get("success") is True:
            self._clear("idle", None)
            return "signed_in"
        error = params.get("error")
        self.fail(error if isinstance(error, str) and error else "The sign-in did not complete.")
        return "failed"

    def cancelled(self) -> None:
        self._clear("idle", None)

    def fail(self, error: str) -> None:
        self._clear("failed", error[:300])

    def is_expired(self, now: datetime) -> bool:
        return self.waiting and self.expires_at is not None and now >= self.expires_at

    def expire(self) -> str | None:
        """End a sign-in whose ten minutes are up; the login id Codex should still cancel.

        Its "not completed" notice, when Codex sends one, is then ignored like a cancellation's.
        """
        login_id = self.login_id
        self.fail(SIGN_IN_EXPIRED)
        return login_id

    def public_view(self) -> dict[str, Any]:
        """The `sign_in` block of `GET /api/v1/codex`: never the address."""
        return {
            "state": self.state,
            "started_at": iso(self.started_at) if self.waiting else None,
            "expires_at": iso(self.expires_at) if self.waiting else None,
            "error": self.error,
        }

    def admin_view(self) -> dict[str, Any]:
        """The admin route's `sign_in` block: the address and port while a browser is awaited."""
        if not self.waiting:
            return self.public_view()
        return {
            "state": self.state,
            "auth_url": self.auth_url,
            "callback_port": self.callback_port,
            "started_at": iso(self.started_at),
            "expires_at": iso(self.expires_at),
        }

    def _clear(self, state: SignInState, error: str | None) -> None:
        self.state = state
        self.login_id = None
        self.auth_url = None
        self.callback_port = None
        self.started_at = None
        self.expires_at = None
        self.error = error
