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
from ravis.credentials import CredentialStore
from ravis.policy import PrivacyLevel

# The header a client presents its credential in. Bearer is used because every
# OpenAI-compatible client already knows how to send it, which is what lets an
# unmodified Clarvis authenticate without a code change (RAVIS.md §8).
CREDENTIAL_HEADER = "authorization"
BEARER_PREFIX = "bearer "

# The application id every unauthenticated caller resolves to. Named rather than
# spelled inline, because it is a security boundary compared against in more than
# one module and a typo in either copy fails open.
ANONYMOUS_APPLICATION_ID = "anonymous"


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

    # §15.1's separate authorization: whether this caller may write or delete a
    # provider credential. False everywhere except an `admin.`-prefixed match,
    # including for an ordinary authenticated client — that is the whole point
    # of the clause. Calling the gateway and re-pointing the keys it calls with
    # are different powers, and one must not imply the other.
    #
    # Enforced in `api/management/credentials.py` on every bind rather than on
    # non-loopback ones. A boundary that a default install does not apply is a
    # field describing an unenforced boundary, which the note above says is
    # worse than no field at all.
    may_write_credentials: bool = False

    # §16 item 4's separate authorization for *configuration*: enabling a
    # provider, narrowing a catalogue, re-pointing a pool. Granted by the same
    # `admin.` prefix as `may_write_credentials` and kept as its own field
    # because they are different powers that happen to share a grantor today —
    # a future operator role that may toggle a provider but never touch a key
    # changes one of these and not the other.
    #
    # False for an ordinary client credential, which is the point. Clarvis holds
    # one; a bug in an agent loop must not be able to disable a provider for
    # every other client on the machine.
    may_write_configuration: bool = False

    # The most *permissive* privacy posture this identity may operate at
    # (§9.6.0's "no privacy level above NORMAL" for anonymous). A request may
    # tighten past it and may never loosen below it — see `policy.py`, which
    # documents why that direction was chosen rather than read off the spec.
    max_privacy_level: PrivacyLevel = PrivacyLevel.NORMAL

    @property
    def is_anonymous(self) -> bool:
        """Whether this caller presented no usable credential.

        A real property rather than a convention, because a caller of it was
        already written against the name and got `False` forever:
        `management/credentials.py` guarded credential writes with
        `getattr(identity, "is_anonymous", False)` and this class has never had
        the attribute, so on a published bind the guard was unreachable and an
        unauthenticated caller could write provider keys. `getattr` with a
        default turns a missing security predicate into a silent permit; the
        call site now reads the attribute directly, so a rename raises instead.
        """
        return self.application_id == ANONYMOUS_APPLICATION_ID


def anonymous_identity(settings: Settings) -> ClientApplication:
    """The identity every unauthenticated caller resolves to.

    Least-privileged by construction: the strictest rate limit, no background
    marker, and no privacy level above NORMAL. Note this is a real identity and
    not a null — absence is a domain value here (runbook §14.4), and giving it a
    name means the rest of the system never has to ask whether it exists.
    """
    return ClientApplication(
        application_id=ANONYMOUS_APPLICATION_ID,
        label=ANONYMOUS_APPLICATION_ID,
        rate_limit_per_minute=settings.anonymous_rate_limit_per_minute,
        # Both spelled out rather than left to the field defaults. They *are*
        # the defaults, and that is exactly why an unauthenticated caller's
        # privileges should be readable here without opening the dataclass.
        may_declare_background_calls=False,
        max_privacy_level=PrivacyLevel.NORMAL,
    )


def _named_application(
    presented: str, credentials: CredentialStore | None, settings: Settings
) -> ClientApplication | None:
    """Match the presented token against each stored `client.*` credential.

    Every candidate is compared even after one matches, so the work does not
    depend on *which* credential was presented. The early return would leak the
    position of a matching name through timing — a small signal, and this is the
    function that decides what policy applies.

    Returns `None` rather than `anonymous` when nothing matches, so the caller
    can still try the legacy single credential. Absence here means "not a named
    application", not "not authenticated".
    """
    if credentials is None:
        return None
    matched: str | None = None
    administrative = False
    for prefix in (CLIENT_PREFIX, ADMIN_PREFIX):
        for name in credentials.names(prefix):
            secret = credentials.resolve(name)
            if secret and hmac.compare_digest(presented, secret.reveal()):
                matched = name[len(prefix):]
                administrative = administrative or prefix == ADMIN_PREFIX
    if not matched:
        return None
    return ClientApplication(
        application_id=matched,
        label=matched,
        rate_limit_per_minute=settings.rate_limit_per_minute,
        # §9.6.1: the marker is honoured from an authenticated identity. What it
        # then *buys* is decided by policy, which is keyed to this id — so an
        # application that may declare background calls still reaches only the
        # providers its policy allows.
        may_declare_background_calls=True,
        # An `admin.` credential is a client credential that may also change
        # keys — never a separate kind of caller, so everything else about it
        # (rate limit, policy, privacy ceiling) is resolved exactly as before.
        may_write_credentials=administrative,
        may_write_configuration=administrative,
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


# The prefix marking a stored credential as a *client* identity rather than a
# provider key. Both live in the same 0600 file and they are entirely different
# things — one authenticates RAVIS to a provider, the other authenticates a
# caller to RAVIS — so the namespace is separated rather than left to whoever
# names the next credential.
CLIENT_PREFIX = "client."

# **A separate authorization for changing credentials (§15.1).**
#
# The clause asks that the ability to rewrite provider keys not come free with
# the ability to call the gateway, and until now it did: `_may_write` returned
# early on a loopback bind, which is the default deployment, so any local
# process — or any page the browser was visiting — could re-point every key.
#
# A second prefix rather than a permission model, because the ecosystem already
# works this way and it is the smaller true thing: SIRVIS mints a `benchmark`
# token and a separate `admin` one, and NERVIS holds both, *"so this install can
# queue work and still be unable to erase the results of it"*. A credential
# named `admin.<who>` is the same idea one service along.
ADMIN_PREFIX = "admin."


def resolve_identity(
    headers: dict[str, str],
    settings: Settings,
    credentials: CredentialStore | None = None,
) -> ClientApplication:
    """Resolve the caller to exactly one application, or to `anonymous`.

    Comparison is constant-time. The values being compared are of attacker-chosen
    length, so a plain `==` would leak the configured credential's length through
    timing — small, but free to avoid, and this is the function guarding every
    policy decision in the service.

    **The lookup M0 promised.** Its comment said "once M10 brings a credential
    store, this grows a lookup and the rest of the service does not change", and
    that is what this is: a credential stored as `client.<application>` resolves
    the caller to `<application>`, so §9.6's per-application policy has an
    identity to key on. Before it, every authenticated caller was one identity
    called `configured`, and a policy for NERVIS would have applied to Clarvis
    too — which is not policy, it is a global setting with a misleading name.

    The single `client_credential` setting still resolves to `configured`,
    checked after the named ones. Every deployment written before this has one,
    and taking it away would turn an authenticated caller into an anonymous one
    at the exact moment its rate limit tightened.
    """
    presented = _presented_credential(headers)
    if not presented:
        return anonymous_identity(settings)
    named = _named_application(presented, credentials, settings)
    if named is not None:
        return named
    if not settings.client_credential:
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
