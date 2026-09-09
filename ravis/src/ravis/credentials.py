"""Where credentials come from, and why they cannot be printed.

M10's acceptance criterion is one line — *secrets absent from all outputs and
logs* — and the cheapest way to fail it is a diagnostic that formats a settings
object, or a log line added six months from now by someone who did not know.

So the guarantee is carried by a **type** rather than by discipline. `Secret`
refuses to render itself: `repr`, `str`, f-strings and `%` formatting all yield
a redaction, and the value comes out only through an explicit `reveal()` that a
reviewer can grep for. `json.dumps` raises on it rather than serialising it,
which is the right failure — loud, at the boundary, in a test.

**The store is the platform's keyring, and the file is what portability costs.**
A credential has to be enterable on every OS RAVIS runs on, and not every OS has
a keyring RAVIS can reach without a dependency — so the keyring is written where
one exists and a JSON file at mode `0600` carries the rest, which is what the
AWS CLI, Docker, `gh` and npm all do. Stdlib only either way: the keyring is
reached through the tool the platform already ships, and the file is protected
by the same permissions that protect the private keys already beside it.

*The file used to be the only writable backend, and this paragraph used to argue
for that as a considered choice. It was — right up until the reason underneath
it turned out to be false; see the note below on `security -i`.*

**A platform keyring is written first, and the file is what happens when there
is none.** This was the other way round until 9 September 2026, on the stated
grounds that `security` takes the password as a command-line argument and "there
is no stdin form" — which an external audit's finding sent somebody to check, and
which is false: `security -i` reads whole commands from standard input, so the
secret never reaches `argv` and never appears in `ps`. Linux's `secret-tool
store` reads the secret from stdin outright. Windows has no such tool, and there
the file is still the answer.

**Every keyring write is read back before it is believed.** macOS's interactive
parser does its own unquoting and eats a backslash; a credential silently stored
wrong fails later as an authentication error, which is the worst way to find out.
So the write is verified against what was asked for, and anything that does not
match exactly falls back to the file — reported, not swallowed.

**Order: file, then keyring, then environment.** The file is still first, because
a keyring write removes the file's copy: after one, the file has nothing to say
about that name, and before one — every credential saved by an older build — the
file is the only place it lives.
Environment variables are how every deployment written before M10 supplies its
keys, so they keep working — but the source travels with the value, which lets a
diagnostic say *where* a credential came from without saying what it is.
"""

from __future__ import annotations

import contextlib
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

# Linux's equivalent, from libsecret-tools. Resolved by name rather than by
# absolute path because distributions disagree about where it lives, and its
# absence is the ordinary case on a server with no session bus.
SECRET_TOOL_BIN = "secret-tool"

#: Set to `0`, `false` or `no` to leave this machine's keyring alone entirely —
#: not written, not deleted from, and not read. Exists for two
#: reasons that are the same reason: a shared or headless machine where a
#: keyring prompt has nobody to answer it, and this repository's own test suite,
#: which drives the real store and would otherwise write into the operator's
#: login keychain on every run. A test that reaches outside its temporary
#: directory can damage the machine it is checking.
KEYRING_SWITCH = "RAVIS_CREDENTIAL_KEYRING"

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
        # **Off means off — reads included.** This gated writes alone at first,
        # on the reading that a keyring an operator filled by hand should stay
        # readable. That left the switch unable to do the one job it was added
        # for: a test suite that builds the real application still saw every
        # credential on the machine, and pools full of real hosted models sent
        # fixture-shaped requests to paid providers. "Do not use this machine's
        # keyring" has to mean all of it, or it is not a boundary.
        self._keyring_allowed = str(
            self._environment.get(KEYRING_SWITCH, "1")
        ).strip().lower() not in {"0", "false", "no", "off"}

    def resolve(self, name: str, *, env_var: str | None = None) -> Secret:
        """One credential, or an absent `Secret` — never `None`.

        Absence is a `Secret` with an empty value rather than `None` so that a
        caller cannot skip the type by testing for `None` and then formatting
        the result of the other branch. `bool(secret)` answers "is it set".
        """
        stored = self._file.read().get(name)
        if stored:
            return Secret(stored, CredentialSource.FILE)
        found = self._from_keyring(name)
        if found is not None:
            return Secret(found, CredentialSource.KEYCHAIN)
        if self._allow_environment:
            value = self._environment.get(env_var or _default_env_var(name), "")
            if value:
                return Secret(value, CredentialSource.ENVIRONMENT)
        return Secret("", CredentialSource.ABSENT)

    def names(self, prefix: str = "") -> list[str]:
        """Stored credential names, optionally filtered by prefix.

        **Names this store wrote, wherever it put the value.** A keyring cannot
        be enumerated without asking the user to approve a search, and the
        environment has no list of "credentials" to walk — only variables that
        happen to match a naming convention. So a keyring write records the
        *name* in an index beside the credential file, and this answers "which
        credentials were written here", which is the question the client
        identity lookup asks. Callers must not read it as "every credential that
        exists".

        **The index is why the identity lookup still works.** Client and admin
        identities are matched by walking these names and comparing each value
        against the presented token — so a credential whose name vanished when
        its value moved to the keyring is a credential nobody can authenticate
        with. That is exactly what happened when the keyring write landed
        without this, and `test_management_api.py` caught it as a 403.

        Sorted so a caller iterating it behaves the same on every machine.
        """
        held = set(self._file.read()) | set(self._indexed())
        return sorted(name for name in held if name.startswith(prefix))

    def _index_path(self) -> Path:
        """Beside the credential file, holding names and never a value."""
        return self._file.path.with_name("credentials.keyring.json")

    def _indexed(self) -> list[str]:
        """Names this store put in the platform keyring.

        Unreadable, absent or malformed is an empty list: the index is a
        convenience for enumeration, and a store that refused to start because
        of it would be a store that fails closed on something that holds no
        secret.
        """
        try:
            held = json.loads(self._index_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [str(name) for name in held] if isinstance(held, list) else []

    def _index_write(self, names: list[str]) -> None:
        """`0600` like the credential file, though it holds only names — a list
        of which providers an operator has keys for is worth no less protection
        than the directory it sits in already gives."""
        path = self._index_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(sorted(set(names)), handle, indent=2)
            handle.write("\n")

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
        if self._to_keyring(name, secret):
            # One home per credential. Leaving the old file entry would shadow
            # the keyring on the next read — the file is consulted first — so a
            # successful keyring write is also the file entry's removal.
            values = self._file.read()
            if values.pop(name, None) is not None:
                self._file.write(values)
            self._index_write([*self._indexed(), name])
            return self.status(name)
        values = self._file.read()
        values[name] = secret
        self._file.write(values)
        return self.status(name)

    def forget(self, name: str) -> CredentialStatus:
        """Remove one credential from the file and from RAVIS's own keyring item.

        **The keyring half is new, and it is scoped.** Only items under this
        store's service name are removed — those are the ones `store()` wrote.
        A credential in the environment, or one an operator put in their keyring
        under a different service, was placed there by something outside RAVIS
        and deleting it would be a surprise, so `status()` afterwards may still
        report the name configured from a lower-precedence source. That is the
        truth rather than a failure to delete.
        """
        values = self._file.read()
        values.pop(name, None)
        self._file.write(values)
        indexed = self._indexed()
        if name in indexed:
            self._index_write([held for held in indexed if held != name])
        self._from_keyring_forget(name)
        return self.status(name)

    def known(self) -> list[str]:
        """Names this store wrote, in the file or in the keyring. Never values."""
        return self.names()

    def _from_keyring(self, name: str) -> str | None:
        """This machine's keyring, whichever one it has.

        `CredentialSource.KEYCHAIN` names the result on every platform. The
        value is on the wire and in stored diagnostics, so renaming it would
        break readers to say "Secret Service" on Linux — the source means "the
        platform's keyring" and the docstrings say which one that is.
        """
        if not self._keychain or not self._keyring_allowed:
            return None
        if sys.platform == "darwin":
            return self._from_keychain(name)
        return self._from_secret_tool(name)

    def _from_secret_tool(self, name: str) -> str | None:
        """Ask libsecret's Secret Service, or give up quietly.

        Same contract as the macOS half: absent tool, absent item, no session
        bus and a timeout are all `None`, because each means the same thing to
        the caller — look somewhere else.
        """
        tool = shutil.which(SECRET_TOOL_BIN)
        if not tool:
            return None
        try:
            completed = subprocess.run(
                [tool, "lookup", "service", self._service, "account", name],
                capture_output=True,
                text=True,
                timeout=LOOKUP_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        # `secret-tool lookup` writes the secret with no trailing newline and
        # exits non-zero when it finds nothing.
        return completed.stdout if completed.returncode == 0 and completed.stdout else None

    def _to_keyring(self, name: str, secret: str) -> bool:
        """Put one credential in the platform keyring. True only if it is there.

        **Verified rather than trusted**, and the verification is the point. On
        macOS the secret travels inside a command line that `security -i` parses
        itself, and that parser unquotes: a backslash in a credential arrives
        without it. A silently mangled credential fails later as an
        authentication error against a provider, which is the most expensive way
        to discover a storage bug. So the value is read straight back and
        compared, and anything short of an exact match is treated as no write at
        all — the caller falls through to the file, which cannot mangle
        anything.

        Returns False on every platform without a keyring tool, which is the
        ordinary case on a server and on Windows.
        """
        if not self._keychain or not self._keyring_allowed:
            return False
        if not self._write_command(name, secret):
            return False
        return self._from_keyring(name) == secret

    def _write_command(self, name: str, secret: str) -> bool:
        """The platform's own way of taking a secret on standard input.

        Two shapes, because the tools differ: `security` reads whole *commands*
        from stdin and needs the value quoted inside one, while `secret-tool`
        reads the *secret itself* from stdin and needs no quoting at all. In
        neither does the credential reach `argv`, which is the property this is
        for — `ps` shows the tool and its flags to every process running as this
        user, and a password among them is a real, if brief, window.
        """
        if sys.platform == "darwin" and shutil.which(SECURITY_BIN):
            quoted = "'" + secret.replace("'", "'\''") + "'"
            command = f"add-generic-password -U -s {self._service} -a {name} -w {quoted}"
            if self._keychain_path:
                command += f" {self._keychain_path}"
            return self._piped([SECURITY_BIN, "-i"], command + "\n")
        tool = shutil.which(SECRET_TOOL_BIN)
        if tool:
            return self._piped(
                [tool, "store", "--label", f"{self._service}: {name}",
                 "service", self._service, "account", name],
                secret,
            )
        return False

    def _piped(self, command: list[str], written: str) -> bool:
        """Run one keyring tool with `written` on its standard input.

        Every failure is False rather than an exception: a locked keychain, a
        Linux box with no session bus, a tool that prompts and is never
        answered. None of those is a fault in RAVIS, and each has the same
        answer — write the file instead and say so.
        """
        try:
            completed = subprocess.run(
                command,
                input=written,
                capture_output=True,
                text=True,
                timeout=LOOKUP_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    def _from_keyring_forget(self, name: str) -> None:
        """Remove this store's own keyring item, if the platform has one.

        **Gated on the same switch as writing, and that omission cost a key.**
        `RAVIS_CREDENTIAL_KEYRING=0` means "do not touch this machine's keyring",
        and a delete touches it as surely as a write does — but this checked only
        whether a keyring existed, so a `forget()` from a store that was never
        allowed to write there could still remove an item somebody's own machine
        depended on. An OpenAI key went missing that way, on the machine this was
        built on, with no copy left to restore from.
        """
        if not self._keychain or not self._keyring_allowed:
            return
        if sys.platform == "darwin" and shutil.which(SECURITY_BIN):
            command = [SECURITY_BIN, "delete-generic-password",
                       "-s", self._service, "-a", name]
            if self._keychain_path:
                command.append(self._keychain_path)
        else:
            tool = shutil.which(SECRET_TOOL_BIN)
            if not tool:
                return
            command = [tool, "clear", "service", self._service, "account", name]
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(command, capture_output=True, text=True,
                           timeout=LOOKUP_TIMEOUT_SECONDS, check=False)

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
