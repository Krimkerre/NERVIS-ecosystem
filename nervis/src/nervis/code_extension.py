"""§13.5's Clarvis half: which extension the editor has, and putting one there.

**Why NERVIS does this at all.** The launcher printed a `code-server
--install-extension …` line for somebody to copy, which is fine until the
extension is the thing the Code tab exists to show — then "the editor is
running and the panel is missing" is a state the dashboard can see and cannot
fix, and the fix is a command in a different window. §13.5 asks for the VSIX
path and automatic install and update as *settings*, which is the same
observation written as configuration.

**What it will not do.** No shell, no free-form command, no argument that comes
from a request. The binary is the configured one or the one on `PATH`; the
extension is the configured `.vsix` and nothing else; the two verbs are list
and install. That is the whole surface, and it is deliberately smaller than
`supervision.py`'s — this is not a second way to run processes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("nervis")

#: How long either CLI call may take. code-server's own startup dominates both,
#: and an unbounded wait here would hang a route rather than a shell.
TIMEOUT_SECONDS = 45.0

#: The extension NERVIS installs. Named rather than derived from the file, so a
#: `.vsix` that turns out to be something else is a refusal instead of a
#: surprise installation.
CLARVIS_ID = "krimkerre.clarvis"


class ExtensionError(Exception):
    """Something about the binary, the file, or the editor's answer."""


@dataclass(frozen=True)
class Extension:
    """One installed extension, as code-server reports it."""

    identifier: str
    version: str


def binary(configured: str = "") -> str:
    """The code-server executable, or the refusal that says there is none.

    Configuration first, `PATH` second — the same order `tools/run.py` uses, so
    a machine where the launcher finds code-server is a machine where this
    does. Resolved and checked for executability before it is ever run, because
    a recorded identity that a shell would resolve later is not an identity
    (`supervision.py` makes the same argument at greater length).
    """
    found = configured.strip() or shutil.which("code-server") or ""
    if not found:
        raise ExtensionError(
            "no code-server binary is configured and none is on PATH"
        )
    resolved = os.path.realpath(found)
    if not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        raise ExtensionError(f"{found} is not an executable file NERVIS can run")
    return resolved


def offered(vsix: str) -> Extension:
    """What the configured `.vsix` contains, read out of the file itself.

    A `.vsix` is a zip with the extension's own `package.json` inside it, so the
    version is a fact about the artefact rather than about its filename — and
    filenames are exactly where this kind of thing goes stale, since a rebuild
    that keeps the name changes everything except the name.
    """
    path = Path(vsix).expanduser()
    if not path.is_file():
        raise ExtensionError(f"no extension package at {vsix}")
    try:
        with zipfile.ZipFile(path) as bundle:
            manifest = json.loads(bundle.read("extension/package.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as failure:
        raise ExtensionError(f"{vsix} is not a readable .vsix: {failure}") from failure
    publisher = str(manifest.get("publisher") or "")
    name = str(manifest.get("name") or "")
    version = str(manifest.get("version") or "")
    if not publisher or not name or not version:
        raise ExtensionError(f"{vsix} does not name a publisher, extension and version")
    return Extension(identifier=f"{publisher}.{name}", version=version)


def installed(executable: str) -> dict[str, str]:
    """Every extension the editor holds, as identifier → version.

    A read, and treated as one: it changes nothing, so it is safe to call on
    every render of the tab that displays it.
    """
    listed = _run(executable, "--list-extensions", "--show-versions")
    found: dict[str, str] = {}
    for line in listed.splitlines():
        identifier, _, version = line.strip().partition("@")
        if identifier:
            found[identifier] = version
    return found


def install(executable: str, vsix: str) -> Extension:
    """Put the configured package in the editor, replacing what is there.

    `--force` because this is also the *update* path: without it code-server
    declines when any version is already installed, which would make "update"
    mean "uninstall first" and give a half-installed editor a way to exist.
    """
    wanted = offered(vsix)
    if wanted.identifier != CLARVIS_ID:
        raise ExtensionError(
            f"{vsix} contains {wanted.identifier}, not {CLARVIS_ID}; NERVIS installs "
            "the Clarvis extension and nothing else"
        )
    _run(executable, "--install-extension", str(Path(vsix).expanduser()), "--force")
    return wanted


def due(executable: str, vsix: str) -> str:
    """Why an install is needed, or empty when it is not.

    A reason rather than a boolean, for the same argument the session store
    makes: "not installed" and "an older version is installed" call for
    different words on a screen, and a single `True` makes the screen guess.
    """
    wanted = offered(vsix)
    held = installed(executable).get(wanted.identifier, "")
    if not held:
        return f"{wanted.identifier} is not installed in this editor"
    if held != wanted.version:
        return f"the editor has {wanted.identifier} {held}; the package offers {wanted.version}"
    return ""


def ensure(configured_binary: str, vsix: str) -> str:
    """Install if needed, and say what happened. Never raises.

    **Called at startup when the operator asked for automatic updates**, which
    is the one place where failing loudly would be wrong: a missing binary or an
    unreadable package is a reason for the Code tab to say something, not a
    reason for NERVIS not to start. The outcome is returned so the caller can
    log it once rather than discovering it per request.
    """
    try:
        executable = binary(configured_binary)
        reason = due(executable, vsix)
        if not reason:
            return ""
        installed_now = install(executable, vsix)
        return f"installed {installed_now.identifier} {installed_now.version}: {reason}"
    except ExtensionError as failure:
        return f"could not install the Clarvis extension: {failure}"


def _run(executable: str, *args: str) -> str:
    """One code-server CLI call, bounded, with no shell anywhere near it."""
    try:
        finished = subprocess.run(  # noqa: S603 - argv list, no shell, resolved executable
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as failure:
        raise ExtensionError(f"code-server could not be run: {failure}") from failure
    if finished.returncode != 0:
        # The editor's own words, trimmed. Its stderr says things like "Extension
        # 'x' not found", which is more use than a return code.
        detail = (finished.stderr or finished.stdout or "").strip().splitlines()
        raise ExtensionError(detail[-1] if detail else f"exit status {finished.returncode}")
    return finished.stdout
