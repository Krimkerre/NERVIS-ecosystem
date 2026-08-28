"""§9.6.0 — identity comes from a credential, never from a claim."""

from __future__ import annotations

from ravis.config import Settings
from ravis.core.capabilities import ModelCapabilities
from ravis.identity import resolve_identity
from ravis.policy import PrivacyLevel, RoutingPolicy, effective_policy, policy_exclusions


def _configured(credential: str) -> Settings:
    return Settings(client_credential=credential, database_path=":memory:", _env_file=None)  # type: ignore[call-arg]


def test_no_credential_resolves_to_anonymous(settings: Settings) -> None:
    """Unauthenticated is not refused — §5.0.1 needs an unauthenticated 200."""
    identity = resolve_identity({}, settings)

    assert identity.application_id == "anonymous"


def test_the_background_permission_changes_what_a_request_may_reach(settings: Settings) -> None:
    """The field is back, and this is what makes it honest.

    `may_declare_background_calls` and `max_privacy_level` were *removed* at M0
    and this test asserted their absence, because both were set on every
    identity and read by nothing but a constructor assertion — the shape the
    dead-code gate exists to catch. A field describing an unenforced trust
    boundary reads as protection.

    So the test inverts rather than disappears: it no longer asks whether the
    attribute exists, which is what it could check before and what proved
    nothing. It asks whether flipping the permission changes which providers a
    request may select — because that, and only that, is what the field claims.
    """
    marked = {"background": True}
    anonymous = resolve_identity({}, settings)
    assert anonymous.may_declare_background_calls is False

    unprivileged = effective_policy(
        RoutingPolicy(),
        metadata=marked,
        may_declare_background=anonymous.may_declare_background_calls,
        ceiling=anonymous.max_privacy_level,
    )
    privileged = effective_policy(
        RoutingPolicy(),
        metadata=marked,
        may_declare_background=True,
        ceiling=PrivacyLevel.NORMAL,
    )

    # Same marker, same request, two identities — and the difference is not
    # cosmetic: one of these excludes every paid provider and the other does not.
    assert unprivileged.background is False
    assert privileged.background is True

    paid = {"gpt-4o-mini": ModelCapabilities(model_id="gpt-4o-mini")}
    refused = policy_exclusions(
        privileged, paid, provider_of=lambda _: "openai", remote=frozenset(paid)
    )
    allowed = policy_exclusions(
        unprivileged, paid, provider_of=lambda _: "openai", remote=frozenset(paid)
    )
    assert "gpt-4o-mini" in refused
    assert allowed == {}


def test_the_privacy_ceiling_cannot_be_loosened_by_a_request(settings: Settings) -> None:
    """A request may tighten its privacy level and may never loosen it.

    The direction is chosen rather than read — `policy.py` says why — and it is
    the half that makes `max_privacy_level` a boundary instead of a default. If
    a request could name `NORMAL` and get it, `LOCAL_ONLY` would be advice.
    """
    identity = resolve_identity({}, settings)
    strict = RoutingPolicy(privacy=PrivacyLevel.LOCAL_ONLY)

    loosened = effective_policy(
        strict,
        metadata={"privacy": "NORMAL"},
        may_declare_background=identity.may_declare_background_calls,
        ceiling=identity.max_privacy_level,
    )
    tightened = effective_policy(
        RoutingPolicy(privacy=PrivacyLevel.NORMAL),
        metadata={"privacy": "LOCAL_ONLY"},
        may_declare_background=False,
        ceiling=PrivacyLevel.NORMAL,
    )

    assert loosened.privacy is PrivacyLevel.LOCAL_ONLY, "a request must not relax policy"
    assert tightened.privacy is PrivacyLevel.LOCAL_ONLY, "a request may always tighten"


def test_anonymous_gets_the_stricter_rate_limit(settings: Settings) -> None:
    identity = resolve_identity({}, settings)

    assert identity.rate_limit_per_minute == settings.anonymous_rate_limit_per_minute



def test_a_claimed_identity_header_grants_nothing() -> None:
    """The runbook forbids trusting X-Ecosystem-Actor from an unauthenticated caller."""
    settings = _configured("real-secret")

    identity = resolve_identity({"x-ecosystem-actor": "clarvis"}, settings)

    assert identity.application_id == "anonymous"


def test_a_wrong_credential_resolves_to_anonymous_rather_than_failing() -> None:
    """Keeps the unauthenticated 200 true even for a caller that guessed wrong."""
    settings = _configured("real-secret")

    identity = resolve_identity({"authorization": "Bearer wrong-secret"}, settings)

    assert identity.application_id == "anonymous"


def test_the_configured_credential_resolves_to_a_named_application() -> None:
    settings = _configured("real-secret")

    identity = resolve_identity({"authorization": "Bearer real-secret"}, settings)

    assert identity.application_id == "configured"
