"""§4.4 — a non-loopback bind fails to start without both TLS and a credential."""

from __future__ import annotations

from ravis.config import Settings, inspect_configuration


def _bound_to(host: str, **overrides: object) -> Settings:
    return Settings(host=host, database_path=":memory:", _env_file=None, **overrides)  # type: ignore[call-arg,arg-type]


def test_loopback_bind_needs_nothing_extra() -> None:
    """The ordinary local case must stay frictionless, or people work around it."""
    report = inspect_configuration(_bound_to("127.0.0.1"))

    assert report.is_startable() is True


def test_non_loopback_bind_without_credential_or_tls_is_refused() -> None:
    report = inspect_configuration(_bound_to("0.0.0.0"))

    assert report.is_startable() is False


def test_credential_without_tls_is_still_refused() -> None:
    """The trap this rule exists to close.

    A credential-only bind passes every other check and publishes the model
    registry to the network in cleartext, which is why §4.4 says TLS *and*
    authentication — both, not either.
    """
    report = inspect_configuration(_bound_to("0.0.0.0", client_credential="s3cret"))

    assert report.is_startable() is False
    assert any("TLS" in finding.message for finding in report.fatal_findings)


def test_tls_without_credential_is_refused() -> None:
    report = inspect_configuration(
        _bound_to("0.0.0.0", tls_certificate_path="/tmp/c.pem", tls_key_path="/tmp/k.pem")
    )

    assert report.is_startable() is False
    assert any(finding.setting == "client_credential" for finding in report.fatal_findings)


def test_both_together_are_accepted() -> None:
    report = inspect_configuration(
        _bound_to(
            "0.0.0.0",
            client_credential="s3cret",
            tls_certificate_path="/tmp/c.pem",
            tls_key_path="/tmp/k.pem",
        )
    )

    assert report.is_startable() is True


def test_the_refusal_names_what_is_missing() -> None:
    """An operator needs to be told what to add, not merely that it is wrong."""
    report = inspect_configuration(_bound_to("0.0.0.0"))

    settings_named = {finding.setting for finding in report.fatal_findings}
    assert "client_credential" in settings_named
    assert "tls_certificate_path" in settings_named
