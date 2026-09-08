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


# Where `_store` puts its file, per test. Set by the autouse fixture below.
_CONFIG_HOME: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _never_the_operators_own_credentials(tmp_path: Path) -> None:
    """Point every store built here at a throwaway directory.

    `config_directory` promises this in its own docstring — *"resolved from the
    environment rather than hard-coded so a test never touches a real home
    directory"* — and the mechanism works. What did not was the calling
    convention: `_store(environment={})` passes an environment with no
    `XDG_CONFIG_HOME` in it, which falls through to `~/.config/ravis`, so this
    file has always read and written the operator's actual credentials.

    The symptom was a test that passed on a clean machine and failed on a
    working one: `test_status_of_an_absent_credential_says_absent` asserts that
    `google` is absent, and it is absent right up until somebody configures
    Google. A suite whose result depends on the developer's own config is
    reporting on the wrong thing.
    """
    _CONFIG_HOME["path"] = str(tmp_path)


def _store(**kwargs: object) -> CredentialStore:
    kwargs.setdefault("keychain", False)
    # Injected into whatever environment the caller asked for, rather than
    # replacing it: several tests below supply `RAVIS_*_API_KEY` deliberately
    # and still must not see the real config directory.
    environment = dict(kwargs.get("environment") or {})
    environment.setdefault("XDG_CONFIG_HOME", _CONFIG_HOME["path"])
    kwargs["environment"] = environment
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


# ── 4. The file backend, which is the part that works on every OS ────────────


import os  # noqa: E402
import stat  # noqa: E402
from pathlib import Path  # noqa: E402

from ravis.credentials import CredentialFile, config_directory  # noqa: E402


def _file_store(tmp_path: Path, **kwargs: object) -> CredentialStore:
    kwargs.setdefault("keychain", False)
    kwargs.setdefault("environment", {})
    return CredentialStore(file=CredentialFile(tmp_path / "credentials.json"), **kwargs)  # type: ignore[arg-type]


def test_a_stored_credential_comes_back(tmp_path: Path) -> None:
    store = _file_store(tmp_path)

    store.store("anthropic", VALUE)

    secret = store.resolve("anthropic")
    assert secret.source is CredentialSource.FILE
    assert secret.reveal() == VALUE


def test_the_file_is_private_and_so_is_its_directory(tmp_path: Path) -> None:
    """The whole security model. `os.open` with an explicit mode rather than
    write-then-chmod, so there is no window where the umask decides."""
    store = _file_store(tmp_path / "nested")
    store.store("anthropic", VALUE)

    path = tmp_path / "nested" / "credentials.json"
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0


def test_the_file_wins_over_the_environment(tmp_path: Path) -> None:
    """It is what the UI writes, and the most recent explicit action should win."""
    store = _file_store(tmp_path, environment={"RAVIS_ANTHROPIC_API_KEY": "from-env"})
    store.store("anthropic", VALUE)

    assert store.resolve("anthropic").source is CredentialSource.FILE


def test_forgetting_does_not_reach_into_the_environment(tmp_path: Path) -> None:
    """Deleting from a store RAVIS does not own would be a surprise, so the
    credential is still configured afterwards — from the lower source."""
    store = _file_store(tmp_path, environment={"RAVIS_ANTHROPIC_API_KEY": "from-env"})
    store.store("anthropic", VALUE)

    status = store.forget("anthropic")

    assert status.configured
    assert status.source is CredentialSource.ENVIRONMENT


def test_an_empty_credential_is_refused(tmp_path: Path) -> None:
    """An empty value reads identically to an absent one, so storing it makes
    "I set it and it says absent" a supportable bug report."""
    with pytest.raises(ValueError, match="empty"):
        _file_store(tmp_path).store("anthropic", "   ")


def test_a_malformed_file_reads_as_absent_rather_than_raising(tmp_path: Path) -> None:
    """Fails closed and stays repairable, instead of taking startup down."""
    path = tmp_path / "credentials.json"
    path.write_text("{ not json", encoding="utf-8")

    assert _file_store(tmp_path).resolve("anthropic").source is CredentialSource.ABSENT


def test_loose_permissions_are_reported_not_enforced(tmp_path: Path) -> None:
    """Refusing would be stronger and is what ssh does, but it locks an operator
    out of their own service over a bit they can fix."""
    store = _file_store(tmp_path)
    store.store("anthropic", VALUE)
    path = tmp_path / "credentials.json"
    os.chmod(path, 0o644)

    status = store.status("anthropic")

    assert status.configured, "still readable"
    assert not status.file_is_private


def test_the_write_leaves_no_temporary_behind(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    store.store("anthropic", VALUE)

    assert [p.name for p in tmp_path.iterdir()] == ["credentials.json"]


def test_known_lists_names_and_never_values(tmp_path: Path) -> None:
    store = _file_store(tmp_path)
    store.store("anthropic", VALUE)
    store.store("google", "AIza-other")

    assert store.known() == ["anthropic", "google"]


def test_the_config_directory_follows_the_platform() -> None:
    """Resolved from the environment so a test never touches a real home."""
    assert config_directory({"XDG_CONFIG_HOME": "/xdg"}) == Path("/xdg/ravis")


# ── §10: a keychain that is missing, erroring, hung or prompting ─────────────
#
# Every test above builds the store with `keychain=False`, which is right for
# them and left the whole Keychain branch — the fallthrough, the timeout, the
# non-zero exit — asserted nowhere. The four ways it can fail are one branch
# each, and each returns `None` so the environment is consulted next.
#
# `subprocess.run` is patched rather than a real `security` invoked: the point
# is what the store does with each outcome, and a test that shelled out would
# depend on the machine's own Keychain contents and prompt somebody.


def _keychain_that(behaviour: object, monkeypatch: pytest.MonkeyPatch) -> CredentialStore:
    """A store whose Keychain lookup does whatever `behaviour` does."""
    import subprocess

    from ravis import credentials as module

    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/security")

    def run(*_: object, **__: object) -> object:
        if isinstance(behaviour, BaseException):
            raise behaviour
        return behaviour

    monkeypatch.setattr(subprocess, "run", run)
    return _store(keychain=True, environment={"RAVIS_ANTHROPIC_API_KEY": VALUE})


class _Completed:
    """What `subprocess.run` returns, in the two shapes that matter."""

    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_a_keychain_that_holds_nothing_falls_through_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary case on a Mac with no item stored: `security` exits
    non-zero, and an absent credential is absent rather than a fault."""
    store = _keychain_that(_Completed(44), monkeypatch)

    secret = store.resolve("anthropic")

    assert secret.reveal() == VALUE
    assert secret.source is CredentialSource.ENVIRONMENT


def test_a_keychain_that_never_answers_does_not_hang_the_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**The prompt nobody answers.** `security` can put a dialog on a screen
    the operator is not looking at, and a lookup that waited for it would hang
    whatever asked — a routing decision, a health probe, a startup. The five
    second timeout is what prevents that, and it was code-only until now."""
    import subprocess

    store = _keychain_that(
        subprocess.TimeoutExpired(cmd="security", timeout=5.0), monkeypatch
    )

    secret = store.resolve("anthropic")

    assert secret.reveal() == VALUE
    assert secret.source is CredentialSource.ENVIRONMENT


def test_a_keychain_lookup_asks_for_a_bounded_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """And the bound is real rather than inherited. A `subprocess.run` with no
    `timeout` waits forever, which is the failure the test above describes and
    cannot itself detect — it patches the call that would have hung."""
    import subprocess

    from ravis import credentials as module

    seen: dict[str, object] = {}
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/bin/security")

    def run(*_: object, **kwargs: object) -> object:
        seen.update(kwargs)
        return _Completed(44)

    monkeypatch.setattr(subprocess, "run", run)
    _store(keychain=True).resolve("anthropic")

    assert seen.get("timeout") == module.LOOKUP_TIMEOUT_SECONDS


def test_a_host_with_no_security_binary_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux, and every container. There is no Keychain to ask, which is a
    fact about the host rather than a failure of the store."""
    from ravis import credentials as module

    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    store = _store(keychain=True, environment={"RAVIS_ANTHROPIC_API_KEY": VALUE})

    secret = store.resolve("anthropic")

    assert secret.source is CredentialSource.ENVIRONMENT


def test_a_keychain_that_errors_at_the_operating_system_is_survived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The binary is there and the call itself fails — a broken install, a
    sandbox that refuses the exec, a keychain file that will not open."""
    store = _keychain_that(OSError("cannot execute security"), monkeypatch)

    secret = store.resolve("anthropic")

    assert secret.source is CredentialSource.ENVIRONMENT


def test_a_keychain_that_answers_is_still_preferred_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The falsifier for all five. A store that always fell through would pass
    every test above while never reading a Keychain at all."""
    store = _keychain_that(_Completed(0, "from-the-keychain\n"), monkeypatch)

    secret = store.resolve("anthropic")

    assert secret.reveal() == "from-the-keychain"
    assert secret.source is CredentialSource.KEYCHAIN
