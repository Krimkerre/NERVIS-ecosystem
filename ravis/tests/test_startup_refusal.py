"""§4.4 — a non-loopback bind fails to start without both TLS and a credential."""

from __future__ import annotations

from ravis.config import Settings, inspect_configuration


def _bound_to(host: str, **overrides: object) -> Settings:
    return Settings(host=host, database_path=":memory:", _env_file=None, **overrides)  # type: ignore[call-arg,arg-type]


def test_a_default_context_of_zero_or_less_refuses_to_start() -> None:
    """Both runtimes' default contexts must be a positive number of tokens.

    A negative one made every model its runtime had not loaded too small for any
    request, and zero was quietly swapped for the built-in default — either way the
    operator's number and what RAVIS routed on disagreed without a word. Refused
    before serving, naming the variable to fix.
    """
    for setting in ("ollama_default_context", "lmstudio_default_context"):
        for value in (-8192, 0):
            report = inspect_configuration(_bound_to("127.0.0.1", **{setting: value}))

            assert report.is_startable() is False
            [finding] = report.fatal_findings
            assert finding.setting == setting
            assert f"RAVIS_{setting.upper()}" in finding.message and str(value) in finding.message


def test_the_default_contexts_as_shipped_start() -> None:
    report = inspect_configuration(_bound_to("127.0.0.1", lmstudio_default_context=1))

    assert report.is_startable() is True
    assert not [f for f in report.findings if f.setting.endswith("default_context")]


def test_loopback_bind_needs_nothing_extra() -> None:
    """The ordinary local case must stay frictionless, or people work around it."""
    report = inspect_configuration(_bound_to("127.0.0.1"))

    assert report.is_startable() is True


def test_non_loopback_bind_without_credential_or_tls_is_refused() -> None:
    report = inspect_configuration(_bound_to("0.0.0.0"))

    assert report.is_startable() is False


def test_a_credential_alone_does_not_open_a_remote_bind() -> None:
    """Was: refused because TLS was missing. Now: refused because it is remote.

    The question is still worth asking — a credential-only bind was the original
    trap, publishing the model registry to the network in cleartext — but the
    reason it fails no longer depends on which piece is absent.
    """
    report = inspect_configuration(_bound_to("0.0.0.0", client_credential="s3cret"))

    assert report.is_startable() is False


def test_a_bare_remote_bind_is_refused() -> None:
    """The mirror case, and the one that named the defect.

    This used to configure a certificate and a key and assert the bind was still
    refused *for want of a credential*. Both settings are gone now — nothing read
    them, and `check_dead_code.py` calls a field like that one that "makes
    something look implemented" — so the case it covered is structurally
    impossible rather than merely tested.
    """
    report = inspect_configuration(
        _bound_to("0.0.0.0")
    )

    assert report.is_startable() is False


def test_the_refusal_names_the_bind_rather_than_a_missing_piece() -> None:
    """Deliberately no longer a shopping list.

    This asserted that the refusal named `client_credential` and
    `tls_certificate_path`, so an operator would supply both and arrive at a
    bind that starts and still serves in cleartext. Telling somebody what is
    missing is right whenever supplying it would help; here it would not, so the
    finding names the host and points at the work instead.
    """
    report = inspect_configuration(_bound_to("0.0.0.0"))

    settings_named = {finding.setting for finding in report.fatal_findings}
    assert settings_named == {"host"}
    # It points at runbook §9, which records that nothing is building remote access
    # yet. Until 12 September 2026 it pointed at a §16 that had been retired.
    assert any("§9" in finding.message for finding in report.fatal_findings)


def test_a_fully_configured_remote_bind_is_refused_too() -> None:
    """The rule this file encoded was satisfiable and still not safe.

    `cli.py` calls `uvicorn.run` without `ssl_certfile` or `ssl_keyfile`, so a
    bind that names a certificate and a key serves plain HTTP anyway — the
    configuration is validated and never applied. An external audit found the
    same shape in all three services.

    Until remote operation is built and proven end to end (`ECOSYSTEM_RUNBOOK.md`
    §16 item 2 lists what that takes), a non-loopback bind is refused whatever it
    is configured with. Refusing is the honest failure: a remote mode that looks
    encrypted and is not is worse than none.
    """
    report = inspect_configuration(
        _bound_to("0.0.0.0", client_credential="s3cret")
    )

    assert report.is_startable() is False
