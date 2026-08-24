"""M10's one-line criterion: secrets absent from all outputs and logs.

Most of these are about a type refusing to render itself. They look repetitive
on purpose — every one is a distinct way Python turns an object into text, and a
guarantee that holds for `repr` but not for `%` formatting is not a guarantee.
"""

from __future__ import annotations

import json

import pytest

from ravis.credentials import (
    CredentialSource,
    CredentialStatus,
    CredentialStore,
    Secret,
)

VALUE = "sk-do-not-print-me-0123456789"


def _secret() -> Secret:
    return Secret(VALUE, CredentialSource.KEYCHAIN)


# ── 1. Every path from object to text ────────────────────────────────────────


@pytest.mark.parametrize(
    "render",
    [
        repr,
        str,
        lambda s: f"{s}",
        lambda s: f"{s!s}",
        lambda s: f"{s!r}",
        lambda s: f"{s:>40}",
        lambda s: "%s" % (s,),
        lambda s: "{}".format(s),  # noqa: UP032 - the .format path is the point
        lambda s: " ".join([str(s)]),
    ],
)
def test_no_rendering_path_reveals_the_value(render) -> None:  # type: ignore[no-untyped-def]
    rendered = render(_secret())

    assert VALUE not in rendered
    assert "Secret" in rendered


def test_the_redaction_still_says_something_useful() -> None:
    """A diagnostic needs to distinguish "absent" from "set but wrong", so the
    source and the length are safe to show and worth showing."""
    rendered = repr(_secret())

    assert "keychain" in rendered
    assert str(len(VALUE)) in rendered


def test_it_is_not_json_serialisable() -> None:
    """Loud at the boundary beats a credential in a response body."""
    with pytest.raises(TypeError):
        json.dumps({"key": _secret()})


def test_a_containing_dataclass_repr_does_not_leak_it() -> None:
    """The field is excluded from the generated repr *and* __repr__ is defined,
    so re-enabling one does not silently undo the other."""
    assert VALUE not in repr({"credential": _secret()})


def test_reveal_is_the_only_way_out() -> None:
    assert _secret().reveal() == VALUE


# ── 2. Resolution order ──────────────────────────────────────────────────────


def _store(**kwargs: object) -> CredentialStore:
    kwargs.setdefault("keychain", False)
    return CredentialStore(**kwargs)  # type: ignore[arg-type]


def test_the_environment_supplies_a_credential_when_the_keychain_has_none() -> None:
    """Every deployment written before M10 does it this way and keeps working."""
    store = _store(environment={"RAVIS_ANTHROPIC_API_KEY": VALUE})

    secret = store.resolve("anthropic")

    assert secret.source is CredentialSource.ENVIRONMENT
    assert secret.reveal() == VALUE


def test_an_explicit_env_var_name_overrides_the_derived_one() -> None:
    store = _store(environment={"CUSTOM_KEY": VALUE})

    assert store.resolve("anthropic", env_var="CUSTOM_KEY").reveal() == VALUE


def test_the_environment_can_be_refused() -> None:
    """An operator who has moved to the Keychain can shut the fallback off, and
    a provider whose credential is only in the environment then reads absent —
    which fails closed rather than reaching for the weaker source."""
    store = _store(allow_environment=False, environment={"RAVIS_ANTHROPIC_API_KEY": VALUE})

    assert store.resolve("anthropic").source is CredentialSource.ABSENT


def test_absence_is_a_secret_not_a_none() -> None:
    """So a caller cannot test for None, take the other branch, and format it."""
    secret = _store(environment={}).resolve("anthropic")

    assert isinstance(secret, Secret)
    assert not secret
    assert secret.source is CredentialSource.ABSENT


# ── 3. What a diagnostic is allowed to hold ──────────────────────────────────


def test_status_reports_availability_without_carrying_a_value() -> None:
    store = _store(environment={"RAVIS_ANTHROPIC_API_KEY": VALUE})

    status = store.status("anthropic")

    assert status == CredentialStatus("anthropic", CredentialSource.ENVIRONMENT, True)
    assert VALUE not in json.dumps(status.as_dict())


def test_status_of_an_absent_credential_says_absent() -> None:
    status = _store(environment={}).status("google")

    assert not status.configured
    assert status.source is CredentialSource.ABSENT


def test_a_status_cannot_be_made_to_hold_a_secret() -> None:
    """The stronger promise: not a value it declines to show, but no field for
    one at all."""
    assert "value" not in CredentialStatus.__dataclass_fields__
    assert not any(
        isinstance(v, str) and VALUE in v
        for v in _store(environment={"RAVIS_ANTHROPIC_API_KEY": VALUE})
        .status("anthropic")
        .as_dict()
        .values()
    )
