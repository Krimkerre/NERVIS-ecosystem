"""Authentication and origin validation for the SIRVIS API (§4.5).

§4.5 calls a local API token "required, not optional", and the reasoning is
worth restating because "it is only on loopback" is the intuition it exists to
correct:

    SIRVIS's mutating surface starts multi-gigabyte downloads, loads and evicts
    models other clients hold, and saturates the machine with benchmark work;
    loopback is not a boundary against another local process.

There are two independent checks and they answer different questions.

**The token asks "do you hold the key?"** Every mutating endpoint requires one
carrying the right scope, on loopback included. Reads stay open where a peer
needs them to negotiate, which is how RAVIS looks SIRVIS up before anybody has
exchanged anything.

**The origin check asks "did you actually mean to send this?"** The two are not
redundant, and §4.5's second paragraph is why: a browser already carries the
user's credentials and attaches them automatically. A page the user happens to
be visiting can make their browser POST to `127.0.0.1:8721`, and the browser
will present the token correctly on behalf of an instruction the user never
gave. The key gets used, properly, by the wrong party's intent — so a second
question has to be asked, and it has to be one the browser answers truthfully
about itself.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import Request

from sirvis.config import Settings
from sirvis.errors import (
    AuthenticationRequiredError,
    ForbiddenError,
    UnsupportedMediaTypeError,
)
from sirvis.storage import Database

# The label the first token is minted under, so `sirvis token` can say whether
# one already exists without being able to show its value.
BOOTSTRAP_LABEL = "bootstrap"

# 32 bytes of urandom, URL-safe: long enough that guessing is not a strategy,
# short enough to paste into a config file without wrapping.
TOKEN_BYTES = 32

# Methods that carry no body and change nothing. They still need the token and
# the origin check where an endpoint is protected — only the CSRF content-type
# requirement is meaningless for them.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class Scope(str, Enum):
    """§4.5's four scopes, from least to most dangerous.

    Separate rather than one all-or-nothing token because what they guard costs
    different amounts. `benchmark` spends time; `runtime` spends memory and can
    evict a model another client is mid-request against; `admin` changes how the
    service behaves for everyone. A dashboard that draws graphs should not be
    able to do the third because it needed the first.
    """

    READ = "read"
    BENCHMARK = "benchmark"
    RUNTIME = "runtime"
    ADMIN = "admin"


@dataclass(frozen=True)
class Caller:
    """Who is asking, as far as SIRVIS can tell.

    Anonymous is a real answer rather than an error: reads are open, so most
    callers are legitimately unidentified and only become interesting when they
    try to change something.
    """

    label: str
    scopes: frozenset[Scope]

    @property
    def is_anonymous(self) -> bool:
        return not self.scopes

    def permits(self, required: Scope) -> bool:
        """Whether this caller may perform an operation needing `required`.

        `admin` implies everything — a convenience whose cost is that a leaked
        admin token is total, which is exactly why the other three exist and why
        anything automated should hold the narrowest one that works.
        """
        return Scope.ADMIN in self.scopes or required in self.scopes


ANONYMOUS = Caller(label="anonymous", scopes=frozenset())


def hash_token(token: str) -> str:
    """The stored form of a token.

    SHA-256 without a salt, deliberately. Salting defends against precomputation
    over *guessable* secrets; these are 32 bytes of urandom, where precomputation
    is not the threat and a per-row salt would make lookup by hash impossible
    without scanning every row. What matters is that the plaintext cannot be
    recovered from the database.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_token(database: Database, label: str, scopes: set[Scope]) -> str:
    """Create a token, store only its hash, and return the plaintext **once**.

    The return value is the only time this string exists outside the caller's
    hands. That is the point: a credential the service can reproduce later is a
    credential a database leak reproduces for somebody else.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    with database.connection as connection:
        connection.execute(
            "INSERT INTO api_token (token_hash, label, scopes) VALUES (?, ?, ?)",
            (hash_token(token), label, " ".join(sorted(s.value for s in scopes))),
        )
    return token


def ensure_bootstrap_token(database: Database) -> str | None:
    """Mint the first admin token if none exists; return None when one does.

    §4.5 requires a token, and a service that refused to start without one would
    be unusable on a fresh install — the operator would need a token in order to
    obtain a token. So the first run mints one and `sirvis token` reveals it,
    which is the shape Jupyter and similar local services use.

    None when a bootstrap token already exists: minting a second one silently
    would leave the first working and unaccounted for.
    """
    row = database.connection.execute(
        "SELECT 1 FROM api_token WHERE label = ?", (BOOTSTRAP_LABEL,)
    ).fetchone()
    if row is not None:
        return None
    return mint_token(database, BOOTSTRAP_LABEL, {Scope.ADMIN})


def resolve_caller(database: Database, headers: dict[str, str]) -> Caller:
    """Identify the caller from its bearer token, or report anonymous.

    Never raises on a bad token. An unrecognised credential and no credential
    are the same thing here — an unidentified caller — and whether that is
    acceptable belongs to the endpoint, which knows whether it is about to
    change something.
    """
    presented = _bearer(headers)
    if not presented:
        return ANONYMOUS
    row = database.connection.execute(
        "SELECT label, scopes FROM api_token WHERE token_hash = ?", (hash_token(presented),)
    ).fetchone()
    if row is None:
        return ANONYMOUS
    scopes = frozenset(Scope(value) for value in str(row["scopes"]).split() if value)
    return Caller(label=str(row["label"]), scopes=scopes)


def _bearer(headers: dict[str, str]) -> str:
    """The token from an `Authorization: Bearer …` header, or empty."""
    scheme, _, value = headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def require(request: Request, scope: Scope) -> Caller:
    """Authorise one mutating request, or refuse it.

    Both §4.5 checks, origin first: a rejected origin is a configuration problem
    the operator can see and fix, and reporting it before the token check gives
    the more useful of the two errors.
    """
    headers = {key.lower(): value for key, value in request.headers.items()}
    settings: Settings = request.app.state.settings

    check_origin(headers, settings)
    # The content-type check is a CSRF defence for requests that carry a body.
    # Applying it to a GET was a bug: a GET has no content type to require, so
    # an authorised-looking read was refused with 415 — an answer about the
    # media type, for a request whose actual problem was a missing token.
    if request.method not in SAFE_METHODS:
        check_content_type(headers)

    caller = resolve_caller(request.app.state.database, headers)
    # The message says what was *required*, never what was presented. Telling a
    # caller its token was nearly right, or that one exists under some other
    # label, helps nobody who is allowed to be here.
    if caller.is_anonymous:
        raise AuthenticationRequiredError(
            f"a token with {scope.value} scope is required", required_scope=scope.value
        )
    if not caller.permits(scope):
        raise ForbiddenError(
            f"{scope.value} scope is required", required_scope=scope.value
        )
    return caller


def check_origin(headers: dict[str, str], settings: Settings) -> None:
    """Refuse a browser request from an origin nobody allow-listed (§4.5).

    No `Origin` header means no browser sent this. Command-line clients and SDKs
    do not set one and they are the ordinary caller, so absence is permitted
    while a *present but unlisted* origin is refused. That asymmetry is the
    whole check: the attack requires a browser, and a browser always identifies
    itself here whether it wants to or not.
    """
    origin = headers.get("origin", "")
    if not origin:
        return
    if origin not in settings.allowed_origins:
        raise ForbiddenError("origin is not allow-listed", origin=origin)


def check_content_type(headers: dict[str, str]) -> None:
    """Require a content type a cross-origin form cannot set (§4.5).

    A `<form>` on a hostile page can POST anywhere without permission, but only
    as `application/x-www-form-urlencoded`, `multipart/form-data` or
    `text/plain` — the three "simple" types needing no preflight. Requiring JSON
    forces the browser to ask SIRVIS first, and that question is one
    `check_origin` gets to answer.

    §4.5 also asks for a CSRF token. There is none yet, and pretending otherwise
    would be worse than the gap: a CSRF token must be bound to a session, and
    SIRVIS has no sessions until the dashboard at M14. Recorded as a deviation
    rather than stubbed into something that looks like protection.
    """
    content_type = headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise UnsupportedMediaTypeError(
            "mutations must be sent as application/json", received=content_type or None
        )


def redacted(value: str | None) -> str:
    """How a credential may appear in any output at all.

    Never the value, and never a prefix of it either — a few characters of a
    secret narrows a search. Presence or absence is the most a log, an error or
    an API response is allowed to say.
    """
    return "configured" if value else "not configured"


def token_summary(database: Database) -> list[dict[str, Any]]:
    """What tokens exist, without any means of using them."""
    rows = database.connection.execute(
        "SELECT label, scopes, created_at, last_used FROM api_token ORDER BY created_at"
    ).fetchall()
    return [
        {
            "label": row["label"],
            "scopes": str(row["scopes"]).split(),
            "created_at": row["created_at"],
            "last_used": row["last_used"],
        }
        for row in rows
    ]
