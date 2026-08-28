"""Where credentials come from, and why they cannot be printed.

M10's acceptance criterion is one line — *secrets absent from all outputs and
logs* — and the cheapest way to fail it is a diagnostic that formats a settings
object, or a log line added six months from now by someone who did not know.

So the guarantee is carried by a **type** rather than by discipline. `Secret`
refuses to render itself: `repr`, `str`, f-strings and `%` formatting all yield
a redaction, and the value comes out only through an explicit `reveal()` that a
reviewer can grep for. `json.dumps` raises on it rather than serialising it,
which is the right failure — loud, at the boundary, in a test.

**The store is a file, and the file is the portable part.** A credential has to
be enterable, and it has to be enterable on every OS RAVIS runs on — so the
writable backend is a JSON file at mode `0600` in the user's config directory,
which is what the AWS CLI, Docker, `gh` and npm all do. Stdlib only: a native
credential-vault dependency buys encryption at rest and costs a build
requirement on three platforms, and the file is protected by the same filesystem
permissions that protect the private keys already sitting next to it.

macOS Keychain is kept as a **read** source for operators who prefer it, and is
deliberately not a write target. `security` takes the password as a command-line
argument, which publishes it in the process list to everything running as that
user — brief, but a real window, and there is no stdin form. Writing to the file
has no such window.

**Order: file, then Keychain, then environment.** The file is first because it
is what the UI writes, and the most recent explicit action should win.
Environment variables are how every deployment written before M10 supplies its
keys, so they keep working — but the source travels with the value, which lets a
diagnostic say *where* a credential came from without saying what it is.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# `security` is not on non-macOS hosts, and CI runs on Linux. Absence is not an
# error: the store falls through to the environment, which is exactly what a
# Linux deployment would want anyway.
SECURITY_BIN = "/usr/bin/security"

# A lookup must not be able to hang startup. The Keychain can prompt when a
# item's ACL demands it, and a prompt on a headless box waits forever.
LOOKUP_TIMEOUT_SECONDS = 5.0


class CredentialSource(str, Enum):
    """Where a credential was found. Safe to print — it names no value."""

    FILE = "file"
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
    file_is_private: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source.value,
            "configured": self.configured,
            "file_is_private": self.file_is_private,
        }


def config_directory(environment: dict[str, str] | None = None) -> Path:
    """Where RAVIS keeps per-user state, on whichever OS this is.

    Windows uses `%APPDATA%`, everything else follows XDG with the same
    `~/.config` default that Linux and macOS both tolerate. Resolved from the
    environment rather than hard-coded so a test never touches a real home
    directory.
    """
    env = environment if environment is not None else dict(os.environ)
    if sys.platform == "win32":
        base = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ravis"


class CredentialFile:
    """Credentials on disk, readable only by their owner.

    Mode `0600` is the whole security model, and it is the same one protecting
    `~/.ssh/id_ed25519` and `~/.aws/credentials`. Encryption at rest would need
    a key, and a key that RAVIS can read unattended is a key an attacker with
    the same file access can read too — so it would move the problem rather than
    solve it, at the cost of a native dependency on three platforms.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, str]:
        """Every stored credential, or an empty mapping.

        A malformed or unreadable file yields nothing rather than raising. The
        next source is tried, and a credential that cannot be read is reported
        absent — which fails closed — instead of taking the service down at
        startup over a file the operator can repair.
        """
        try:
            with self.path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(k): str(v) for k, v in payload.items() if isinstance(v, str)}

    def write(self, values: dict[str, str]) -> None:
        """Replace the file, atomically, never leaving it world-readable.

        `os.open` with an explicit mode creates the temporary file already
        private: writing then chmod-ing leaves a window in which the credentials
        exist at the umask's discretion. `os.replace` is atomic within a
        directory, so a reader sees either the old file or the new one and never
        a half-written one.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(values, handle, indent=2, sort_keys=True)
                handle.write("\n")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        os.replace(temporary, self.path)

    def is_private(self) -> bool:
        """False when anyone but the owner can read it.

        Reported rather than enforced. Refusing to read would be the stronger
        stance and is what ssh does, but it locks an operator out of their own
        service over a permission bit they can fix — so this surfaces in
        `status()` and the file is still read.
        """
        try:
            mode = self.path.stat().st_mode
        except OSError:
            return True
        return not mode & (stat.S_IRWXG | stat.S_IRWXO)


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
        file: CredentialFile | None = None,
    ) -> None:
        self._service = service
        self._allow_environment = allow_environment
        self._environment = environment if environment is not None else dict(os.environ)
        self._keychain = keychain
        self._file = file or CredentialFile(
            config_directory(self._environment) / "credentials.json"
        )
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
        stored = self._file.read().get(name)
        if stored:
            return Secret(stored, CredentialSource.FILE)
        found = self._from_keychain(name)
        if found is not None:
            return Secret(found, CredentialSource.KEYCHAIN)
        if self._allow_environment:
            value = self._environment.get(env_var or _default_env_var(name), "")
            if value:
                return Secret(value, CredentialSource.ENVIRONMENT)
        return Secret("", CredentialSource.ABSENT)

    def names(self, prefix: str = "") -> list[str]:
        """Stored credential names, optionally filtered by prefix.

        **File-backed only, and that is a real limit rather than an oversight.**
        A Keychain cannot be enumerated by name without asking the user to
        approve a search, and the environment has no list of "credentials" to
        walk — only variables that happen to match a naming convention. So this
        answers "which credentials were written *here*", which is the question
        the client-identity lookup needs, and callers must not read it as "every
        credential that exists".

        Sorted so a caller iterating it behaves the same on every machine.
        """
        return sorted(name for name in self._file.read() if name.startswith(prefix))

    def status(self, name: str, *, env_var: str | None = None) -> CredentialStatus:
        """Whether a credential is available, and from where. Never the value."""
        secret = self.resolve(name, env_var=env_var)
        return CredentialStatus(
            name=name,
            source=secret.source,
            configured=bool(secret),
            # Only meaningful for a credential actually coming from the file;
            # reporting it otherwise would put a warning on a screen about a
            # file that is not being used.
            file_is_private=(
                self._file.is_private() if secret.source is CredentialSource.FILE else True
            ),
        )

    def store(self, name: str, secret: str) -> CredentialStatus:
        """Write one credential, replacing any previous value.

        Takes the raw string rather than a `Secret` because this is where a
        credential *enters* the type — everything after this point holds a
        `Secret` and cannot print it.

        Refuses an empty value rather than storing one: an empty credential is
        indistinguishable from an absent one at every later read, so writing it
        would make "I set it and it says absent" a supportable bug report.
        """
        if not secret.strip():
            raise ValueError("refusing to store an empty credential")
        values = self._file.read()
        values[name] = secret
        self._file.write(values)
        return self.status(name)

    def forget(self, name: str) -> CredentialStatus:
        """Remove one credential from the file.

        Only from the file. A credential in the environment or the Keychain was
        put there by something outside RAVIS, and deleting from a store this
        service does not own would be a surprise — so `status()` afterwards may
        still report it configured, from a lower-precedence source, which is the
        truth rather than a failure to delete.
        """
        values = self._file.read()
        values.pop(name, None)
        self._file.write(values)
        return self.status(name)

    def known(self) -> list[str]:
        """Names present in the file. Never values."""
        return sorted(self._file.read())

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


def credential_for(store: CredentialStore, name: str, configured: str = "") -> str:
    """The credential a request path should actually use.

    **The store existed and nothing asked it.** M10 built `resolve()`, the file,
    the Keychain read and the precedence order — and then every adapter went on
    reading its own settings field, so a key typed into the Credentials screen
    was written, reported as configured, and never sent to anything. The screen
    was not wrong about having stored it; it was wrong by implication about what
    storing it would do.

    `configured` is the settings field that used to be the only source, and it
    is the *fallback* rather than the winner: the store's own order already puts
    the environment last, so a deployment that supplies `RAVIS_ANTHROPIC_API_KEY`
    keeps working unchanged, and an operator who then types a key into the
    screen gets the one they just typed. The most recent explicit action wins,
    which is the same rule the store already documents for file-over-environment.

    Returns the raw string because that is what an adapter puts in a header, and
    this is the boundary where a `Secret` is deliberately opened. Every call is
    a `reveal()` a reviewer can grep for.
    """
    secret = store.resolve(name)
    if secret:
        return secret.reveal()
    return configured


def _default_env_var(name: str) -> str:
    """The environment variable a credential falls back to.

    `anthropic` -> `RAVIS_ANTHROPIC_API_KEY`, matching the settings names that
    already exist so that M10 changes where a credential may come from without
    changing what a deployment has to be called.
    """
    return f"RAVIS_{name.replace('-', '_').upper()}_API_KEY"
