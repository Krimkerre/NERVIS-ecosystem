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

from dataclasses import dataclass

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
    api_key: str

    @property
    def is_configured(self) -> bool:
        """False when no upstream has been set.

        Not an error state. `/v1/models` must still answer 200 for an
        availability probe (§5.0.1), so an unconfigured RAVIS is a RAVIS with an
        empty catalogue rather than a broken one.
        """
        return bool(self.base_url)

    def url_for(self, path: str) -> str:
        """Join the base to an endpoint path without doubling the separator."""
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


def upstream_from(settings: Settings) -> Upstream:
    """Read the configured upstream out of settings."""
    return Upstream(base_url=settings.upstream_base_url, api_key=settings.upstream_api_key)


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
    if upstream.api_key:
        forwarded["authorization"] = f"Bearer {upstream.api_key}"
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
