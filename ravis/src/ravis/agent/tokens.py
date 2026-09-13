"""Session tokens and the relay's ids (design §3.5.1; `conventions.json` headers, identifiers).

**A session token is a capability.** `X-Agent-Session-Token: ast_<43 base64url characters>` — 256
random bits — is handed out once, at creation (and by `reissue-token`), and is what lets a window
read, answer, steer or stop that one task. RAVIS keeps only its sha256 and compares in constant
time, so its database never holds a usable token (design §3.5.1).

**Ids** carry a prefix saying what they name (`as_` a session, `rq_` a request, `pl_` a project
lock), then ten characters of time and sixteen of randomness in Crockford's base32 — sortable by
creation, unguessable, and inside NERVIS's `^as_[0-9A-Za-z]{10,40}$` for its Stop route.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

TOKEN_PREFIX = "ast_"
#: Crockford's base32: no I, L, O or U, so an id read aloud or retyped can't be mistaken.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_token() -> str:
    """A fresh session token: `ast_` and 32 random bytes as 43 base64url characters."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(presented: str | None, stored_sha256: str) -> bool:
    """Whether a presented token is the one whose sha256 is stored, in constant time."""
    if not presented:
        return False
    return hmac.compare_digest(token_sha256(presented), stored_sha256)


def new_id(prefix: str, now: float | None = None) -> str:
    """`as_`, `rq_` or `pl_`, then a time-ordered, random 26-character id."""
    milliseconds = int((time.time() if now is None else now) * 1000)
    stamp = "".join(ALPHABET[(milliseconds >> shift) & 31] for shift in range(45, -1, -5))
    return prefix + stamp + "".join(secrets.choice(ALPHABET) for _ in range(16))
