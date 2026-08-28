"""Routing sessions — correlation across related requests (§12.1).

A session is what lets RAVIS answer "what has this conversation been using", and
what sticky routing reads to avoid swapping models mid-conversation for no
reason. §12.1 lists what it stores; this module stores exactly that and nothing
resembling a prompt.

**A session ID is correlation data, never authorization.** The runbook §4.3 says
so of every ID it defines, and it matters more here than for `request_id`
because a session *influences routing*. What it can influence is bounded by
construction: stickiness is a preference applied among candidates that already
passed every hard filter, so presenting somebody else's session ID can at most
express a preference for a model the caller was already allowed to reach. It can
never widen policy, and §14's rule 14 stays true.

**Sessions are scoped to an application and never merge across one.** §12.1's
warning is specific — *cross-workspace Clarvis sessions must never merge because
display names match* — so nothing here keys on a label. The stored key is the
application's own identity plus the ID the client supplied, which means two
applications sending the identical string get two sessions and cannot see each
other's routing. Within one application the client owns distinctness, and that
is a real limit rather than a hidden one: §9.7 forbids RAVIS from handling
workspace identifiers at all, so it cannot tell two workspaces apart itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ravis.storage.database import Database

# The runbook §4.3 header. Read case-insensitively by the caller, like every
# other header in this service.
SESSION_HEADER = "x-session-id"

# How long a session survives without a request before it stops being used for
# affinity. Long enough to cover a pause for thought in a conversation, short
# enough that yesterday's model choice does not silently steer today's — an
# expired session is *not* deleted here, because §12.1 asks for correlation and
# a record that vanishes cannot correlate anything after the fact.
DEFAULT_IDLE_SECONDS = 3600.0

# How long an idle session is kept before deletion. Separate from affinity
# expiry on purpose: one governs routing, the other governs storage, and
# collapsing them would mean either routing on stale state or losing the
# correlation a person is trying to read.
DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600.0

# The longest client-supplied ID accepted. An ID is an opaque correlation key,
# so nothing needs to parse it — but it is stored, and unbounded text from a
# request is how a table becomes a place to put things.
MAX_ID_LENGTH = 200

# How many of an application's sessions must exist before their length is
# treated as a fact about it. Below this the answer is "unknown", which routes
# exactly as RAVIS did before §12.2's tradeoff existed — the same floor
# `Observations` puts under a latency median, and for the same reason: a median
# of two is two numbers.
MINIMUM_SESSIONS = 3


@dataclass(frozen=True)
class RoutingSession:
    """§12.1's record, and only what it lists.

    Frozen: a session is read during routing and written after it, and code that
    mutates one mid-decision would make the explanation disagree with what
    happened.

    **No prompt, no response, no message count.** §12.1 is explicit that a
    session does not imply RAVIS stores conversation content, and the surest way
    to keep that true is to have nowhere to put it.
    """

    session_id: str
    application_id: str
    # What the client addressed — a pool, or a model named directly.
    pool: str = ""
    # What the router actually selected, which is what stickiness reads.
    model: str = ""
    provider: str = ""
    # §5.4: every session records the revision it used, so a consumer can tell
    # that a pool's behaviour changed under a running conversation.
    pool_revision: str = ""
    profile: str = ""
    # §12.1's "cache state" — whether the provider is holding a prompt cache for
    # this conversation. Nothing sets it yet: no adapter reports one, and a
    # field that always says `False` would read as "no cache" rather than "never
    # asked". Absent is the honest value until a provider tells us.
    cache_state: str | None = None
    created_at: float = 0.0
    last_activity: float = 0.0
    # How many requests this session has made, which is §12.2's "expected
    # session length" — as an observation rather than a forecast. RAVIS has no
    # model of how long a conversation will run and inventing one would be the
    # guess §9.4 forbids, so the tradeoff reads what has happened instead of
    # predicting what will. §12.1's field list does not name this; §12.2
    # requires it, and a count is metadata rather than conversation content.
    requests: int = 0

    def idle_for(self, now: float) -> float:
        return max(0.0, now - self.last_activity)

    def is_fresh(self, now: float, idle_seconds: float = DEFAULT_IDLE_SECONDS) -> bool:
        """Whether this session may still steer routing.

        A stale session is not an error and is not deleted — it simply stops
        being a reason to prefer a model. Yesterday's choice should not quietly
        decide today's, and the record still answers "what did this use".
        """
        return self.idle_for(now) <= idle_seconds

    def as_dict(self) -> dict[str, Any]:
        """The read shape for `/api/v1/sessions/{session_id}`.

        `cache_state` is emitted as `null` rather than omitted when unknown, so
        a consumer can distinguish "no cache" from "not reported" — the same
        tri-state rule the capability records follow.
        """
        return {
            "session_id": self.session_id,
            "application_id": self.application_id,
            "pool": self.pool,
            "model": self.model,
            "provider": self.provider,
            "pool_revision": self.pool_revision,
            "profile": self.profile,
            "cache_state": self.cache_state,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "requests": self.requests,
        }


def session_key(application_id: str, supplied: str) -> str:
    """The stored identity of a session: the application's, plus the client's.

    Composite rather than the client's string alone, which is the whole of
    §12.1's isolation requirement. Two applications that both call their session
    `main` have two sessions, and neither can read or steer the other's.

    The separator is a character no caller can put in either half — the
    application ID comes from a credential name and the supplied half is
    sanitised — so the composite cannot be forged by embedding one in the other.
    """
    return f"{application_id}\x1f{_sanitised(supplied)}"


def _sanitised(supplied: str) -> str:
    """A client-supplied ID, trimmed and bounded, or empty if it is unusable.

    Control characters are stripped rather than rejected. This value is echoed
    back on a read endpoint and written to logs, and a newline in it would let a
    caller forge a log line — a small thing that is free to prevent here and
    awkward to prevent everywhere it is printed.
    """
    cleaned = "".join(character for character in supplied.strip() if character.isprintable())
    return cleaned[:MAX_ID_LENGTH]


class SessionStore:
    """Sessions, persisted, with affinity and retention read on the way past.

    **Persisted rather than in memory**, unlike the route-decision log. §12.1's
    gate names restart explicitly, and a session that forgets its model on
    restart would swap the model under a conversation that is still going —
    which is the exact churn stickiness exists to prevent.
    """

    def __init__(
        self,
        database: Database,
        *,
        clock: Any = time.time,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
    ) -> None:
        self._database = database
        self._clock = clock
        self._idle_seconds = idle_seconds
        self._retention_seconds = retention_seconds

    def get(self, application_id: str, supplied: str) -> RoutingSession | None:
        """The session this caller is continuing, or None.

        None for an absent, empty or unrecognised ID — all three mean "no
        session to continue", and distinguishing them here would tell a caller
        whether somebody else's session ID exists.
        """
        if not _sanitised(supplied):
            return None
        key = session_key(application_id, supplied)
        row = self._database.connection.execute(
            "SELECT * FROM routing_session WHERE session_key = ?", (key,)
        ).fetchone()
        return _from_row(row) if row is not None else None

    def affinity(self, application_id: str, supplied: str) -> RoutingSession | None:
        """The session, but only while it is fresh enough to steer routing.

        Separate from `get` because the two questions differ: a management
        screen asking what a session used should see an old one, and the router
        asking what to prefer should not.
        """
        session = self.get(application_id, supplied)
        if session is None or not session.is_fresh(self._clock(), self._idle_seconds):
            return None
        return session

    def record(
        self,
        application_id: str,
        supplied: str,
        *,
        pool: str,
        model: str,
        provider: str,
        pool_revision: str = "",
        profile: str = "",
    ) -> RoutingSession | None:
        """Write what this request routed to, creating the session if needed.

        Returns None when the caller supplied no usable ID: a session nobody can
        name again is a row that can only ever be written, and §12.1 wants
        correlation rather than a log.
        """
        cleaned = _sanitised(supplied)
        if not cleaned:
            return None
        now = self._clock()
        key = session_key(application_id, supplied)
        existing = self.get(application_id, supplied)
        session = RoutingSession(
            session_id=cleaned,
            application_id=application_id,
            pool=pool,
            model=model,
            provider=provider,
            pool_revision=pool_revision,
            profile=profile,
            created_at=existing.created_at if existing else now,
            last_activity=now,
            cache_state=existing.cache_state if existing else None,
            requests=(existing.requests if existing else 0) + 1,
        )
        with self._database.connection as connection:
            connection.execute(
                """
                INSERT INTO routing_session (
                    session_key, session_id, application_id, pool, model, provider,
                    pool_revision, profile, cache_state, created_at, last_activity,
                    requests
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    pool = excluded.pool,
                    model = excluded.model,
                    provider = excluded.provider,
                    pool_revision = excluded.pool_revision,
                    profile = excluded.profile,
                    last_activity = excluded.last_activity,
                    requests = excluded.requests
                """,
                (
                    key, session.session_id, session.application_id, session.pool,
                    session.model, session.provider, session.pool_revision,
                    session.profile, session.cache_state, session.created_at,
                    session.last_activity, session.requests,
                ),
            )
        return session

    def touch(self, application_id: str, supplied: str) -> None:
        """Mark a session active without changing what it routed to.

        For a request that reached an existing session but produced no new
        selection — a refusal, or a background call exempt from affinity. Its
        absence would let a busy conversation expire while it was being used.
        """
        if not _sanitised(supplied):
            return
        with self._database.connection as connection:
            connection.execute(
                "UPDATE routing_session SET last_activity = ? WHERE session_key = ?",
                (self._clock(), session_key(application_id, supplied)),
            )

    def typical_length(
        self, application_id: str, minimum_sessions: int = MINIMUM_SESSIONS
    ) -> int | None:
        """How many requests this application's sessions usually make, or None.

        **This is §12.2's "expected session length", and it has to come from
        history rather than from the session in front of us.** Session affinity
        settles a conversation on its model at the *first* request, which is
        exactly when the current session's own count is 1 and says nothing. A
        rule reading that count would decline the load on request one, decline
        it again on request two, and then switch models halfway through a long
        conversation — the churn stickiness exists to prevent, arrived at by the
        feature meant to avoid a load.

        So the estimate is the median request count of this application's
        completed sessions: a measurement of how this client actually behaves,
        available at the moment the decision is made.

        **None until there is enough history**, and None means "do not change
        the route" rather than "assume short". An application nobody has watched
        yet gets the behaviour it had before this existed, which is the only
        honest answer and the same rule `Observations` applies to a model it has
        barely timed.

        The median rather than the mean, because one abandoned session of 400
        requests should not convince RAVIS that every session is long.
        """
        rows = self._database.connection.execute(
            """
            SELECT requests FROM routing_session
             WHERE application_id = ? AND requests > 0
             ORDER BY requests
            """,
            (application_id,),
        ).fetchall()
        counts = [int(row["requests"]) for row in rows]
        if len(counts) < minimum_sessions:
            return None
        return counts[len(counts) // 2]

    def enforce_retention(self) -> int:
        """Delete sessions idle past the retention window. Returns how many.

        Bounded and indexed, run on the same timer as everything else that
        prunes. Retention is *documented* rather than merely applied — §12.1's
        gate asks for it, and a window nobody can state is not a policy.
        """
        cutoff = self._clock() - self._retention_seconds
        with self._database.connection as connection:
            cursor = connection.execute(
                "DELETE FROM routing_session WHERE last_activity < ?", (cutoff,)
            )
        return int(cursor.rowcount or 0)

    def for_application(self, application_id: str, limit: int = 50) -> list[RoutingSession]:
        """Recent sessions belonging to one application, newest first.

        Scoped to the application rather than listing everything, because the
        isolation this module exists to provide would be pointless if one screen
        served every application's sessions in a single list.
        """
        rows = self._database.connection.execute(
            """
            SELECT * FROM routing_session
             WHERE application_id = ?
             ORDER BY last_activity DESC
             LIMIT ?
            """,
            (application_id, limit),
        )
        return [_from_row(row) for row in rows]


def _from_row(row: Any) -> RoutingSession:
    return RoutingSession(
        session_id=row["session_id"],
        application_id=row["application_id"],
        pool=row["pool"],
        model=row["model"],
        provider=row["provider"],
        pool_revision=row["pool_revision"],
        profile=row["profile"],
        cache_state=row["cache_state"],
        created_at=float(row["created_at"]),
        last_activity=float(row["last_activity"]),
        requests=int(row["requests"]),
    )
