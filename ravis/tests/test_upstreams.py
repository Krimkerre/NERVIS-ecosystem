"""Declaring more than one transparent upstream.

Most of this is about what RAVIS refuses. A malformed list is fatal where an
unrecognised `kind` is not, and the asymmetry is the point: a bad kind costs the
vendor metadata that kind would have read, while a list that cannot be parsed
leaves RAVIS not knowing where to send anything.
"""

from __future__ import annotations

import json

import pytest

from ravis.config import Settings, inspect_configuration
from ravis.upstreams import (
    DEFAULT_NAME,
    UpstreamConfigurationError,
    upstream_specs,
)


def _settings(**overrides: object) -> Settings:
    return Settings(database_path=":memory:", _env_file=None, **overrides)  # type: ignore[call-arg,arg-type]


PAIR = json.dumps(
    [
        {"name": "lmstudio", "base_url": "http://127.0.0.1:1234", "kind": "lmstudio"},
        {"name": "ollama", "base_url": "http://127.0.0.1:11434", "kind": "ollama"},
    ]
)


# ── The singular settings keep meaning what they meant ───────────────────────


def test_nothing_configured_is_no_upstreams_rather_than_an_error() -> None:
    """§5.0.1: an unconfigured RAVIS answers /v1/models, it does not fail."""
    assert upstream_specs(_settings()) == []


def test_the_singular_settings_still_declare_one_upstream() -> None:
    """Every deployment and test written before this describes exactly this."""
    specs = upstream_specs(_settings(upstream_base_url="http://one.invalid"))

    assert len(specs) == 1
    assert specs[0].name == DEFAULT_NAME
    assert specs[0].base_url == "http://one.invalid"


def test_the_singular_kind_is_carried_onto_the_spec() -> None:
    specs = upstream_specs(
        _settings(upstream_base_url="http://one.invalid", upstream_kind="lmstudio")
    )

    assert specs[0].kind == "lmstudio"


def test_the_list_replaces_the_singular_settings_rather_than_adding_to_them() -> None:
    """One place to read to know what is configured."""
    specs = upstream_specs(_settings(upstream_base_url="http://ignored.invalid", upstreams=PAIR))

    assert [spec.name for spec in specs] == ["lmstudio", "ollama"]


# ── Order, because it decides ties ───────────────────────────────────────────


def test_declaration_order_is_preserved() -> None:
    """It is what breaks a tie when two upstreams serve the same model id, so
    sorting it into something prettier would silently change routing."""
    reversed_pair = json.dumps(list(reversed(json.loads(PAIR))))

    assert [spec.name for spec in upstream_specs(_settings(upstreams=reversed_pair))] == [
        "ollama",
        "lmstudio",
    ]


# ── What it refuses ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{not json", "not valid JSON"),
        ('{"name": "a"}', "must be a JSON list"),
        ("[3]", "entry 0 is not an object"),
        ('[{"base_url": "http://a.invalid"}]', "entry 0 has no name"),
        ('[{"name": "a"}]', "has no base_url"),
    ],
)
def test_a_declaration_it_will_not_guess_at_is_refused(raw: str, expected: str) -> None:
    with pytest.raises(UpstreamConfigurationError, match=expected):
        upstream_specs(_settings(upstreams=raw))


def test_a_duplicate_name_is_refused_rather_than_warned_about() -> None:
    """Names are addresses. Two upstreams sharing one makes half the addresses
    in the deployment unreachable, and which half depends on iteration order."""
    raw = json.dumps(
        [
            {"name": "local", "base_url": "http://a.invalid"},
            {"name": "local", "base_url": "http://b.invalid"},
        ]
    )

    with pytest.raises(UpstreamConfigurationError, match="duplicate upstream name"):
        upstream_specs(_settings(upstreams=raw))


def test_a_name_with_a_slash_is_refused() -> None:
    """`ravis/<name>/<model>` has three segments. A name with a slash in it
    could be declared and never addressed."""
    raw = json.dumps([{"name": "a/b", "base_url": "http://a.invalid"}])

    with pytest.raises(UpstreamConfigurationError, match="may not contain"):
        upstream_specs(_settings(upstreams=raw))


# ── What startup does about it ───────────────────────────────────────────────


def test_a_malformed_list_is_a_fatal_configuration_finding() -> None:
    report = inspect_configuration(_settings(upstreams="{not json"))

    fatal = [finding for finding in report.findings if finding.fatal]
    assert [finding.setting for finding in fatal] == ["upstreams"]


def test_several_upstreams_are_reported_without_being_a_problem() -> None:
    report = inspect_configuration(_settings(upstreams=PAIR))

    findings = [f for f in report.findings if f.setting == "upstreams"]
    assert findings and not findings[0].fatal
    assert "lmstudio (lmstudio)" in findings[0].message


def test_a_credential_is_never_reported_as_a_value() -> None:
    """§9.7 keeps credentials out of everything RAVIS reads back."""
    raw = json.dumps([{"name": "a", "base_url": "http://a.invalid", "api_key": "sk-secret"}])
    spec = upstream_specs(_settings(upstreams=raw))[0]

    assert spec.redacted() == {
        "name": "a",
        "base_url": "http://a.invalid",
        "kind": "generic",
        "api_key_configured": True,
    }
    assert "sk-secret" not in json.dumps(spec.redacted())
