"""Where credentials come from, and why they cannot be printed.

M10's acceptance criterion is one line — *secrets absent from all outputs and
logs* — and the cheapest way to fail it is a diagnostic that formats a settings
object, or a log line added six months from now by someone who did not know.

So the guarantee is carried by a **type** rather than by discipline. `Secret`
refuses to render itself: `repr`, `str`, f-strings and `%` formatting all yield
a redaction, and the value comes out only through an explicit `reveal()` that a
reviewer can grep for. `json.dumps` raises on it rather than serialising it,
which is the right failure — loud, at the boundary, in a test.

**Reading only.** RAVIS looks credentials up; it never writes them. The
dashboard has said since the prototype that credentials are not editable from a
screen, and a service that could write to the Keychain would be a service whose
compromise rewrites the Keychain. An operator installs one with:

    security add-generic-password -s ravis -a anthropic -w

**Order: Keychain, then environment.** Environment variables are how every
deployment written before M10 supplies its keys, so they keep working — but the
source travels with the value, which is what lets a diagnostic say *where* a
credential came from without saying what it is.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum

# `security` is not on non-macOS hosts, and CI runs on Linux. Absence is not an
# error: the store falls through to the environment, which is exactly what a
# Linux deployment would want anyway.
SECURITY_BIN = "/usr/bin/security"

# A lookup must not be able to hang startup. The Keychain can prompt when a
# item's ACL demands it, and a prompt on a headless box waits forever.
LOOKUP_TIMEOUT_SECONDS = 5.0


class CredentialSource(str, Enum):
    """Where a credential was found. Safe to print — it names no value."""

    KEYCHAIN = "keychain"
    ENVIRONMENT = "environment"
    ABSENT = "absent"


@dataclass(frozen=True)
class Secret:
    """A credential that will not render itself.

    The field is private and excluded from the generated `repr`, and `__repr__`
    is defined anyway so that a future `repr=True` cannot quietly undo it.
    """

    _value: str = field(repr=False)
    source: CredentialSource

    def reveal(self) -> str:
        """The actual credential.

        Named to be conspicuous. Every call site is a place a secret leaves the
        type's protection, so `grep -rn reveal()` is the audit.
        """
        return self._value

    def __repr__(self) -> str:
        return f"<Secret source={self.source.value} length={len(self._value)}>"

    def __str__(self) -> str:
        return self.__repr__()

    def __format__(self, spec: str) -> str:
        """f-strings and `format()` route here, not to `__str__`, when a format
        spec is present. Both must redact or the guarantee has a hole."""
        del spec
        return self.__repr__()

    def __bool__(self) -> bool:
        return bool(self._value)


@dataclass(frozen=True)
class CredentialStatus:
    """What a diagnostic may say about a credential (§9.7).

    Deliberately not a `Secret` with the value blanked: a type that *cannot*
    hold a value is a stronger promise than one that holds a value it intends
    not to show.
    """

    name: str
    source: CredentialSource
    configured: bool

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "source": self.source.value, "configured": self.configured}


class CredentialStore:
    """Resolves a credential by name, Keychain first."""

    def __init__(
        self,
        *,
        service: str = "ravis",
        allow_environment: bool = True,
        environment: dict[str, str] | None = None,
        keychain: bool = True,
        keychain_path: str | None = None,
    ) -> None:
        self._service = service
        self._allow_environment = allow_environment
        self._environment = environment if environment is not None else dict(os.environ)
        self._keychain = keychain
        # A specific keychain file rather than the default search list. Exists so
        # the lookup can be exercised against a throwaway keychain without
        # writing anything into the operator's login keychain — a credential
        # path nobody has ever run is not a credential path.
        self._keychain_path = keychain_path

    def resolve(self, name: str, *, env_var: str | None = None) -> Secret:
        """One credential, or an absent `Secret` — never `None`.

        Absence is a `Secret` with an empty value rather than `None` so that a
        caller cannot skip the type by testing for `None` and then formatting
        the result of the other branch. `bool(secret)` answers "is it set".
        """
        found = self._from_keychain(name)
        if found is not None:
            return Secret(found, CredentialSource.KEYCHAIN)
        if self._allow_environment:
            value = self._environment.get(env_var or _default_env_var(name), "")
            if value:
                return Secret(value, CredentialSource.ENVIRONMENT)
        return Secret("", CredentialSource.ABSENT)

    def status(self, name: str, *, env_var: str | None = None) -> CredentialStatus:
        """Whether a credential is available, and from where. Never the value."""
        secret = self.resolve(name, env_var=env_var)
        return CredentialStatus(
            name=name, source=secret.source, configured=bool(secret)
        )

    def _from_keychain(self, name: str) -> str | None:
        """Ask the macOS Keychain, or give up quietly.

        Every failure returns `None`: a missing item exits non-zero, a
        non-macOS host has no `security` at all, and a prompt that never gets
        answered hits the timeout. None of those is an error worth raising —
        the environment is checked next, and a credential that is genuinely
        absent is reported as absent rather than as a fault.
        """
        if not self._keychain or not shutil.which(SECURITY_BIN):
            return None
        try:
            command = [SECURITY_BIN, "find-generic-password",
                       "-s", self._service, "-a", name, "-w"]
            if self._keychain_path:
                command.append(self._keychain_path)
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=LOOKUP_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None


def _default_env_var(name: str) -> str:
    """The environment variable a credential falls back to.

    `anthropic` -> `RAVIS_ANTHROPIC_API_KEY`, matching the settings names that
    already exist so that M10 changes where a credential may come from without
    changing what a deployment has to be called.
    """
    return f"RAVIS_{name.replace('-', '_').upper()}_API_KEY"
