"""The single upstream M1 forwards to, and the pooled client that reaches it.

RAVIS.md §6 calls this Path A — transparent forwarding — and its rule is the
whole design: **preserve the wire representation as far as practical.** When the
upstream already speaks the protocol the client expects, parsing the traffic and
re-serialising it buys nothing and risks everything, because the high-value
surfaces are exactly the fragile ones: tool-call indexes and IDs, fragmented
JSON arguments, reasoning fields, finish reasons, usage chunks and `[DONE]`.

So this module moves bytes. It does not interpret them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from ravis.config import Settings

# Headers that belong to *this* hop and must not be copied to the next one.
# Content-Length is recomputed by httpx; the hop-by-hop headers are meaningless
# to the upstream and actively harmful if forwarded (a stale Content-Length
# truncates a body, a forwarded Host reaches the wrong virtual host).
HOP_BY_HOP_HEADERS = frozenset(
    {"host", "content-length", "connection", "keep-alive", "transfer-encoding", "upgrade"}
)


@dataclass(frozen=True)
class Upstream:
    """Where requests go, and how to authenticate to it.

    One of these at M1. The plural arrives with the provider adapters at M8; the
    shape is deliberately the same so that adding them does not rewrite the
    forwarder.
    """

    base_url: str
    # **The credential written into the declaration, which is rarely the one to
    # send.** Named `declared_key` rather than `api_key` because the short name
    # was a trap: it reads as "the credential", four separate places tested it
    # for truthiness to decide whether to authenticate, and once credentials
    # moved into the store this field became empty for exactly the providers the
    # store exists to serve. Each site failed silently and only when that one
    # path was exercised against a real provider — the catalogue read
    # authenticated while the chat forward did not, twice, in different files.
    #
    # `key()` is the only correct read. The rename is what makes a wrong one a
    # type error instead of a 401 an hour later.
    declared_key: str
    # Where this upstream's OpenAI-shaped API lives under `base_url`.
    #
    # `/v1` for OpenAI itself and for everything that copied it, which is why it
    # was hardcoded at four call sites until a provider disagreed. Google's
    # OpenAI-compatible surface is at `/v1beta/openai`, so its chat endpoint is
    # `/v1beta/openai/chat/completions` with no `/v1` anywhere — a difference
    # that is one string rather than an adapter.
    #
    # Deliberately not applied by `url_for`, which stays literal: Ollama reads
    # `/api/show` and LM Studio its own native paths, and silently versioning
    # those would break the two upstreams this project actually runs on.
    api_root: str = "/v1"
    # Where to look the credential up, each time one is needed.
    #
    # **Resolved per request rather than at startup**, which is the difference
    # between a settings screen that works and one that lies. Credentials were
    # read once while building this object, so a key typed into the Credentials
    # screen did nothing until RAVIS was restarted — and nothing on the screen
    # said so. `ProviderState` already set the precedent for the enable toggle,
    # for the same reason: *"a toggle takes effect on the next request instead
    # of the next restart"*.
    #
    # A lookup is a small file read. If that ever shows up in a latency profile
    # the fix is a cache with an invalidation hook, not a startup snapshot.
    credential: Callable[[], str] | None = field(
        default=None, compare=False, repr=False
    )

    @property
    def is_configured(self) -> bool:
        """False when no upstream has been set.

        Not an error state. `/v1/models` must still answer 200 for an
        availability probe (§5.0.1), so an unconfigured RAVIS is a RAVIS with an
        empty catalogue rather than a broken one.
        """
        return bool(self.base_url)

    def key(self) -> str:
        """The credential to authenticate with, as of right now.

        Falls back to `api_key` — the value written into the declaration, which
        is how every deployment predating the credential store supplies one.
        """
        if self.credential is not None:
            resolved = self.credential()
            if resolved:
                return resolved
        return self.declared_key

    def url_for(self, path: str) -> str:
        """Join the base to an endpoint path without doubling the separator.

        Literal. A native path (`/api/show`) reaches the upstream as written.
        """
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def api_url(self, path: str) -> str:
        """An OpenAI-shaped endpoint, under whatever root this upstream uses.

        Callers pass `/models` and `/chat/completions` — the path *within* the
        API — rather than `/v1/models`, so a provider that roots its API
        somewhere else needs no branch anywhere.
        """
        return self.url_for(f"{self.api_root.rstrip('/')}/{path.lstrip('/')}")


def upstream_from(settings: Settings) -> Upstream:
    """Read the configured upstream out of settings."""
    return Upstream(
        base_url=settings.upstream_base_url, declared_key=settings.upstream_api_key
    )


def forwardable_headers(incoming: dict[str, str], upstream: Upstream) -> dict[str, str]:
    """The headers to send onward, with this hop's own removed.

    The client's Authorization is deliberately **not** forwarded. A client's
    credential authenticates it to RAVIS (§9.6.0); the upstream's credential is
    RAVIS's own and comes from configuration. Passing the caller's token through
    would let any local process borrow whatever the caller happened to hold, and
    would leak that token to a third party.
    """
    forwarded = {
        name: value
        for name, value in incoming.items()
        if name.lower() not in HOP_BY_HOP_HEADERS and name.lower() != "authorization"
    }
    # `key()`, not `api_key`. The field holds only what a declaration wrote;
    # a credential from the store lives behind the resolver, so testing the
    # field meant the transparent forward sent no Authorization header at all
    # for exactly the providers the store exists to serve. The catalogue read
    # authenticated and the chat forward did not, which is a confusing shape of
    # broken: the provider lists its models and then refuses every request.
    key = upstream.key()
    if key:
        forwarded["authorization"] = f"Bearer {key}"
    return forwarded


def create_client(settings: Settings) -> httpx.AsyncClient:
    """One pooled client for the process lifetime.

    Pooled because a fresh connection per request pays a TCP and TLS handshake
    on every call, which shows up directly in time-to-first-token — the number a
    user actually feels. Created once at startup and closed at shutdown, never
    per request.
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.upstream_timeout_seconds, connect=10.0),
        # Follow no redirects: an upstream that redirects is either
        # misconfigured or trying to move us somewhere, and silently following
        # would forward the caller's payload to an address nobody configured.
        follow_redirects=False,
    )
