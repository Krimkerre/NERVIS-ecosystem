"""§9.6.0 — identity comes from a credential, never from a claim."""

from __future__ import annotations

from ravis.config import Settings
from ravis.identity import resolve_identity


def _configured(credential: str) -> Settings:
    return Settings(client_credential=credential, database_path=":memory:", _env_file=None)  # type: ignore[call-arg]


def test_no_credential_resolves_to_anonymous(settings: Settings) -> None:
    """Unauthenticated is not refused — §5.0.1 needs an unauthenticated 200."""
    identity = resolve_identity({}, settings)

    assert identity.application_id == "anonymous"


def test_the_identity_claims_no_boundary_it_does_not_enforce(settings: Settings) -> None:
    """Two fields were removed from `ClientApplication`, and this pins that.

    `may_declare_background_calls` and `max_privacy_level` recorded §9.6.1's
    background marker and §9.6.0's privacy ceiling. Both were set on every
    identity and read by nothing except the two tests that asserted the
    constructor had set them — which is the shape the dead-code gate exists to
    catch: asserted, never acted on.

    A field describing an unenforced trust boundary reads as protection. Neither
    can be enforced before M16, because neither the background-call class nor
    the privacy ladder exists to enforce them against, so they return with the
    engine that checks them.
    """
    identity = resolve_identity({}, settings)

    assert not hasattr(identity, "may_declare_background_calls")
    assert not hasattr(identity, "max_privacy_level")


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
