"""Who is calling, established from a credential rather than from a claim.

RAVIS.md §9.6.0 makes this a security contract rather than a configuration
detail, and the reason is worth understanding before changing anything here.

Almost every policy in RAVIS hangs off the answer: routing defaults, budgets,
the privacy level, which providers are reachable, how much logging happens, the
§4.4 rate limit, and whether the §9.6.1 background-call marker is honoured. So
if a caller could simply *say* who it was, any local process could announce
itself as Clarvis, add the background marker, and inherit Clarvis's privacy
level, provider allow-list and budget relief. At that point RAVIS.md §14's
"privacy constraints can never be overridden by score" stops being a boundary
and becomes a suggestion.

Hence the two rules this module exists to enforce:

  1. An identity comes from a presented credential. Never from a request field,
     a user agent, or the X-Ecosystem-Actor header — the runbook §4.3 forbids
     trusting that header from an unauthenticated caller, and RAVIS only ever
     *writes* it on management responses.
  2. An unauthenticated caller is not refused. §5.0.1 requires GET /v1/models to
     answer 200 without credentials, because Clarvis's availability probe sends
     no headers and reads a 401 as "offline". So no credential resolves to
     `anonymous`, which is least-privileged by construction.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from ravis.config import Settings
from ravis.policy import PrivacyLevel

# The header a client presents its credential in. Bearer is used because every
# OpenAI-compatible client already knows how to send it, which is what lets an
# unmodified Clarvis authenticate without a code change (RAVIS.md §8).
CREDENTIAL_HEADER = "authorization"
BEARER_PREFIX = "bearer "


@dataclass(frozen=True)
class ClientApplication:
    """A resolved caller, and what it is allowed to do.

    Frozen because an identity must not be mutated after resolution: code that
    adjusts privileges mid-request is exactly the confused-deputy shape the
    runbook §9 warns about.
    """

    application_id: str
    label: str
    rate_limit_per_minute: int

    # **Both fields are back, and both are now read on the routing path.** They
    # were removed once, deliberately: they recorded §9.6.1's background marker
    # and §9.6.0's privacy ceiling, both trust boundaries, and nothing but a
    # constructor test read either. A field describing an unenforced boundary is
    # worse than no field, because it reads as protection.
    #
    # What makes them honest now is `policy.py`. `effective_policy` reads the
    # ceiling to floor a request's privacy level, and `policy_exclusions`
    # applies the background class as a hard exclusion — so a change to either
    # value changes which models a request may reach, which is the only thing
    # that makes a permission field true.

    # Whether §9.6.1's background marker is honoured from this caller. False for
    # `anonymous`: the marker buys cost and rate-limit relief, so honouring it
    # from an unauthenticated caller would let any local process claim the
    # relief by asserting it.
    may_declare_background_calls: bool = False

    # The most *permissive* privacy posture this identity may operate at
    # (§9.6.0's "no privacy level above NORMAL" for anonymous). A request may
    # tighten past it and may never loosen below it — see `policy.py`, which
    # documents why that direction was chosen rather than read off the spec.
    max_privacy_level: PrivacyLevel = PrivacyLevel.NORMAL


def anonymous_identity(settings: Settings) -> ClientApplication:
    """The identity every unauthenticated caller resolves to.

    Least-privileged by construction: the strictest rate limit, no background
    marker, and no privacy level above NORMAL. Note this is a real identity and
    not a null — absence is a domain value here (runbook §14.4), and giving it a
    name means the rest of the system never has to ask whether it exists.
    """
    return ClientApplication(
        application_id="anonymous",
        label="anonymous",
        rate_limit_per_minute=settings.anonymous_rate_limit_per_minute,
        # Both spelled out rather than left to the field defaults. They *are*
        # the defaults, and that is exactly why an unauthenticated caller's
        # privileges should be readable here without opening the dataclass.
        may_declare_background_calls=False,
        max_privacy_level=PrivacyLevel.NORMAL,
    )


def _presented_credential(headers: dict[str, str]) -> str:
    """Pull the bearer token out of the Authorization header, if there is one.

    Returns an empty string rather than None when absent: the caller's next step
    is a comparison either way, and an empty string compares safely.
    """
    raw = headers.get(CREDENTIAL_HEADER, "")
    if not raw.lower().startswith(BEARER_PREFIX):
        return ""
    return raw[len(BEARER_PREFIX):].strip()


def resolve_identity(headers: dict[str, str], settings: Settings) -> ClientApplication:
    """Resolve the caller to exactly one application, or to `anonymous`.

    Comparison is constant-time. The values being compared are of attacker-chosen
    length, so a plain `==` would leak the configured credential's length through
    timing — small, but free to avoid, and this is the function guarding every
    policy decision in the service.

    M0 knows one configured credential. Once M10 brings a credential store, this
    grows a lookup and the rest of the service does not change, which is the
    reason every caller depends on this function rather than reading the header.
    """
    presented = _presented_credential(headers)
    if not presented or not settings.client_credential:
        return anonymous_identity(settings)
    if not hmac.compare_digest(presented, settings.client_credential):
        # A wrong credential is not an error, it is an unrecognised caller. It
        # gets anonymous's privileges, not a 401 — which keeps §5.0.1's
        # unauthenticated 200 on /v1/models true for every caller, including one
        # that guessed wrong.
        return anonymous_identity(settings)
    return ClientApplication(
        application_id="configured",
        label="configured",
        rate_limit_per_minute=settings.rate_limit_per_minute,
        # An authenticated caller may declare background calls. This is the
        # single privilege the credential buys beyond the higher rate limit, and
        # §9.6.1 is explicit that it buys nothing else: no policy exemption, no
        # provider access, no management authority.
        may_declare_background_calls=True,
        max_privacy_level=PrivacyLevel.NORMAL,
    )
