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


def test_anonymous_may_not_declare_background_calls(settings: Settings) -> None:
    """The background marker lowers cost, so it needs an authenticated caller."""
    identity = resolve_identity({}, settings)

    assert identity.may_declare_background_calls is False


def test_anonymous_gets_the_stricter_rate_limit(settings: Settings) -> None:
    identity = resolve_identity({}, settings)

    assert identity.rate_limit_per_minute == settings.anonymous_rate_limit_per_minute


def test_anonymous_may_not_raise_its_privacy_level(settings: Settings) -> None:
    identity = resolve_identity({}, settings)

    assert identity.max_privacy_level == "NORMAL"


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
