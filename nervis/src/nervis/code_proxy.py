"""Sessions for the embedded editor: who opened one, on which workspace, and for how long.

Split from the routes because a session is a *policy* object — it decides what
`/code/` will carry and when it stops carrying it — and because the routes are
long enough already with the header and redirect rules in them.

**A session is deliberate, per workspace, and short-lived.** §13.3 asks for
"authenticated access and explicit workspace selection", which is two
requirements in one sentence: something must be presented, and it must name
what is being opened. A cookie that meant "this browser may reach code-server"
and nothing else would satisfy the first and quietly drop the second — the
workspace would come from wherever code-server happened to be pointing, which
is exactly the alternate-filesystem-authority §13.3 refuses NERVIS.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from nervis.errors import InvalidConfigurationError, UnauthorizedError

#: The cookie the browser carries back. Named for the service rather than for
#: the tab, because a second embedded surface would want its own rather than to
#: inherit this one's rights.
COOKIE = "nervis_code"

#: How long a session survives with nothing happening on it. §13.3 asks for an
#: idle timeout and does not name a figure. Thirty minutes is the span over
#: which an editor left open on a desk stops being "in use" and starts being
#: "still logged in" — long enough that reading a document does not end it,
#: short enough that a walked-away-from laptop is not an open editor.
IDLE_SECONDS = 30 * 60.0

#: The ceiling regardless of activity. A session kept alive by its own traffic
#: is still a session nobody re-authorised, and §13.3 asks for *both* timeouts
#: rather than either. Eight hours is a working day: long enough never to
#: interrupt one, short enough that yesterday's session is not today's.
SESSION_SECONDS = 8 * 60 * 60.0


@dataclass(frozen=True)
class Session:
    """One authorised view of one workspace."""

    token: str
    workspace: str
    opened_at: float
    #: Mutable inside a frozen dataclass on purpose: every proxied byte touches
    #: this, and rebuilding the record per request would make the store the
    #: thing under contention rather than the thing being read.
    seen_at: list[float] = field(default_factory=list)

    def touch(self, now: float) -> None:
        self.seen_at.clear()
        self.seen_at.append(now)

    def last_seen(self) -> float:
        return self.seen_at[-1] if self.seen_at else self.opened_at

    def expiry(self, now: float) -> str:
        """Why this session is over, or empty while it is not.

        The *reason* rather than a boolean, because "you were idle" and "eight
        hours have passed" call for different things from the person reading
        it, and a single `False` would make the screen guess which.
        """
        if now - self.opened_at >= SESSION_SECONDS:
            return "this session reached its maximum age and was closed"
        if now - self.last_seen() >= IDLE_SECONDS:
            return "this session was idle and was closed"
        return ""

    def as_dict(self, now: float) -> dict[str, object]:
        """What the screen shows: §13.3's "visible connection state".

        Never the token. The value that authorises the connection is not part
        of describing it, and a screen that printed it would put it in every
        screenshot of a support ticket.
        """
        return {
            "open": True,
            "workspace": self.workspace,
            "opened_at": self.opened_at,
            "last_seen": self.last_seen(),
            "idle_seconds_remaining": max(0.0, IDLE_SECONDS - (now - self.last_seen())),
            "session_seconds_remaining": max(0.0, SESSION_SECONDS - (now - self.opened_at)),
        }


class Sessions:
    """Every open editor session, keyed by the token its browser holds.

    In memory, and deliberately: a session that survived a restart would be one
    nobody re-authorised after the thing being proxied had changed underneath
    it. Restarting NERVIS closing the editor tab is the honest behaviour.
    """

    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._now = now or time.time

    def open(self, workspace: str, roots: list[str]) -> Session:
        """Authorise one workspace, or say why not.

        The workspace is checked against the configured roots here rather than
        at the route, because this is the object that will be asked about it
        later and a check that lives beside the data cannot be forgotten by a
        second caller.
        """
        chosen = _within(workspace, roots)
        token = secrets.token_urlsafe(32)
        session = Session(token=token, workspace=chosen, opened_at=self._now())
        session.touch(self._now())
        self._sessions[token] = session
        return session

    def holding(self, token: str) -> Session:
        """The session this token opens, or the refusal that says it does not.

        Expiry is enforced *here* rather than swept on a timer, so a token
        cannot be valid for the gap between its deadline and the next sweep.
        The record is dropped as it is refused: a stale token that keeps being
        presented must not keep an entry alive.
        """
        if not token:
            raise UnauthorizedError("no editor session was presented")
        found = self._sessions.get(token)
        if found is None:
            raise UnauthorizedError("that editor session is not open")
        over = found.expiry(self._now())
        if over:
            del self._sessions[token]
            raise UnauthorizedError(over)
        found.touch(self._now())
        return found

    def close(self, token: str) -> bool:
        """End one session. True if there was one, so a caller can be honest.

        Idempotent by design: closing an editor tab twice is a person clicking
        twice, not an error, and §4.2's argument about cancellation applies
        unchanged.
        """
        return self._sessions.pop(token, None) is not None

    def state(self, token: str) -> dict[str, object]:
        """The connection state for the screen, without raising.

        A closed session is a state to *render*, not an error: the tab asks
        what is going on and "nothing is open" is a complete answer.
        """
        found = self._sessions.get(token or "")
        if found is None:
            return {"open": False, "reason": "no editor session is open"}
        over = found.expiry(self._now())
        if over:
            del self._sessions[token]
            return {"open": False, "reason": over}
        return found.as_dict(self._now())


def _within(workspace: str, roots: list[str]) -> str:
    """The workspace as an absolute path, if a configured root contains it.

    **Resolved before comparing, and compared as paths rather than strings.**
    `/home/me/work-secrets` starts with `/home/me/work`, so a prefix test on
    text admits a sibling directory whose name happens to share an opening —
    and `..` in the middle of an otherwise-matching path defeats it outright.
    `Path.resolve` collapses both, and `relative_to` asks the question the
    setting actually means: is this inside that.
    """
    if not roots:
        raise InvalidConfigurationError(
            "no workspace root is configured; the editor has nothing it may open"
        )
    if not workspace:
        raise InvalidConfigurationError("a workspace must be named to open the editor")
    wanted = Path(workspace).expanduser().resolve()
    for root in roots:
        base = Path(root).expanduser().resolve()
        if wanted == base or base in wanted.parents:
            return str(wanted)
    raise InvalidConfigurationError(
        f"{workspace} is not inside any configured workspace root"
    )
