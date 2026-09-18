"""Where a Codex on Linux came from, checked before RAVIS runs it (owner decision, 18 Sep 2026).

On a Mac, Apple's signature answers "did OpenAI make this file?" (`runtime.verified_team`). No
Linux binary carries one, so RAVIS accepts Codex from exactly two places, each with a check that
can fail:

1. **Arch's own package, `openai-codex`** (Arch, CachyOS, EndeavourOS, Manjaro…). pacman checked
   the package's signature against Arch's keyring when it installed it. RAVIS asks pacman that the
   file belongs to that package (`pacman -Qqo`) and that none of the package's files changed since
   (`pacman -Qkk`, which compares every file's SHA-256 with the package's own list). Measured on 18
   September 2026: one byte appended to `/usr/bin/codex` fails it with "SHA256 checksum mismatch".
   Arch builds this Codex from OpenAI's source itself, so its fingerprint differs from OpenAI's
   npm build; each is a separate build to accept.

2. **npm, in NERVIS's own folder** (`~/.local/share/nervis/codex`, which `install.sh` makes) —
   every other Linux, and Windows through WSL. OpenAI publishes Codex to npm from GitHub Actions
   with a provenance record. Before trusting a build RAVIS makes three checks, all against
   registry.npmjs.org itself, never a mirror from the owner's npm settings:
   - `npm audit signatures`: every package in the folder carries npm's registry signature *and* a
     verified provenance attestation — two of two, or the check fails;
   - the platform package's lock entry came from OpenAI's tarball address, and the tarball npm
     serves now has exactly the integrity the lock recorded;
   - **every file** in the installed platform package matches the same file in that tarball, and
     the tarball's `package.json` names github.com/openai/codex. Every file, not only `codex`:
     the package ships its own sandbox program, `codex-resources/bwrap`, which Codex runs to fence
     in its commands, so a changed `bwrap` matters as much as a changed `codex`.

   Needs registry.npmjs.org, so it runs when RAVIS starts and when the file changes — Codex needs
   the network to work anyway. An npm older than 9.5 can't check attestations and is refused by
   name.

Neither check says a build is *tested*; the fingerprint pin and the owner's acceptance still decide
that (`runtime.judged`). They say only where the file came from — what `codesign` says on a Mac.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tarfile
from pathlib import Path
from typing import IO, Any

from ravis.codex.runtime import (
    CodexRuntimeError,
    _detail,
    _run,
    remove_folder,
    scratch_workdir,
)
from ravis.config import data_directory

#: Always the system's own pacman, never one found on PATH — as with `codesign` on a Mac.
PACMAN = "/usr/bin/pacman"
ARCH_PACKAGE = "openai-codex"
#: Where Arch's package puts Codex.
ARCH_EXECUTABLE = Path("/usr/bin/codex")

NPM_PACKAGE = "@openai/codex"
#: The registry every npm check is made against, whatever the owner's `.npmrc` says.
NPM_REGISTRY = "https://registry.npmjs.org/"
#: The only address a platform package may have been fetched from.
NPM_TARBALLS = "https://registry.npmjs.org/@openai/codex/-/"
#: What the tarball's `package.json` must name as its source.
OPENAI_REPOSITORY = "github.com/openai/codex"

#: pacman -Qkk hashes about 330 MB; npm pack may download 135 MB when its cache is cold.
PACMAN_TIMEOUT_SECONDS = 120.0
NPM_TIMEOUT_SECONDS = 600.0

AUDITED = re.compile(r"audited (\d+) packages?")
ATTESTED = re.compile(r"(\d+) packages? ha(?:s|ve) (?:a )?verified attestations?")


def npm_folder() -> Path:
    """NERVIS's own npm install of Codex, made by `install.sh`."""
    return data_directory() / "nervis" / "codex"


def npm_link() -> Path:
    """The link `install.sh` points at the native `codex` inside the npm folder.

    npm's own `.bin/codex` is a Node script that starts the binary; RAVIS runs the binary itself,
    so the installer re-points this link on every upgrade, as Homebrew does its link on a Mac.
    """
    return npm_folder() / "codex"


def npm_programs() -> tuple[Path, ...]:
    """Where RAVIS looks for npm, in order: the installer's private Node, then the system's.

    Never PATH, for the reason `codesign` is never taken from PATH: the program that vouches for
    Codex must not be one a stray PATH entry could replace.
    """
    return (
        data_directory() / "nervis" / "node" / "bin" / "npm",
        Path("/usr/bin/npm"),
        Path("/usr/local/bin/npm"),
    )


def default_link() -> Path:
    """Arch's `/usr/bin/codex` when pacman says it's `openai-codex`'s; otherwise the npm link."""
    if ARCH_EXECUTABLE.is_file() and _package_owning(ARCH_EXECUTABLE) == ARCH_PACKAGE:
        return ARCH_EXECUTABLE
    return npm_link()


def vouched(target: Path, scratch_root: Path) -> str:
    """Refuse a Codex RAVIS can't trace to Arch's package or OpenAI's npm build; name its source."""
    if _package_owning(target) == ARCH_PACKAGE:
        _pacman_unaltered()
        return "pacman"
    folder = npm_folder().resolve()
    if target.is_relative_to(folder):
        _npm_vouched(target, folder, scratch_root)
        return "npm"
    raise CodexRuntimeError(
        "not_available",
        f"{target} came from neither Arch's {ARCH_PACKAGE} package nor NERVIS's npm install "
        f"({npm_folder()}), the two places RAVIS can check a Codex on Linux; run ./install.sh",
    )


# ── Arch ────────────────────────────────────────────────────────────────────────


def _package_owning(target: Path) -> str | None:
    """The pacman package that owns `target`; None without pacman, or when none owns it."""
    if not Path(PACMAN).is_file():
        return None
    answered = _run([PACMAN, "-Qqo", str(target)], timeout=PACMAN_TIMEOUT_SECONDS)
    return answered.stdout.strip() if answered.returncode == 0 else None


def _pacman_unaltered() -> None:
    checked = _run([PACMAN, "-Qkk", ARCH_PACKAGE], timeout=PACMAN_TIMEOUT_SECONDS)
    if checked.returncode != 0:
        # pacman names each altered file on standard error, as a warning line.
        raise CodexRuntimeError(
            "not_available",
            f"pacman says files of {ARCH_PACKAGE} changed since it was installed: "
            f"{_detail(checked.stderr or checked.stdout)}",
        )


# ── npm ─────────────────────────────────────────────────────────────────────────


def _npm_vouched(target: Path, folder: Path, scratch_root: Path) -> None:
    package, version = _platform_package(target, folder)
    integrity = _locked_integrity(folder, package.name, version)
    npm = _npm_program()
    _signatures_and_attestations(npm, folder)
    workdir = scratch_workdir(scratch_root)
    try:
        tarball = _packed(npm, folder, version, integrity, workdir)
        _same_files(package, tarball)
    finally:
        remove_folder(workdir)


def _platform_package(target: Path, folder: Path) -> tuple[Path, str]:
    """The installed platform package `target` belongs to, and its version (`0.155.0-linux-x64`)."""
    parts = target.relative_to(folder).parts
    if (
        len(parts) < 5
        or parts[:2] != ("node_modules", "@openai")
        or not parts[2].startswith("codex-linux-")
        or parts[3] != "vendor"
    ):
        raise CodexRuntimeError(
            "not_available",
            f"{target} is in NERVIS's npm folder but not inside Codex's Linux package",
        )
    package = folder.joinpath(*parts[:3])
    manifest = _json(package / "package.json")
    if manifest.get("name") != NPM_PACKAGE or not isinstance(manifest.get("version"), str):
        raise CodexRuntimeError(
            "not_available",
            f"{package} doesn't say it is {NPM_PACKAGE}; reinstall with ./install.sh",
        )
    return package, manifest["version"]


def _locked_integrity(folder: Path, directory: str, version: str) -> str:
    """The integrity npm recorded when it installed the platform package, from OpenAI's address."""
    entry = (
        _json(folder / "package-lock.json")
        .get("packages", {})
        .get(f"node_modules/@openai/{directory}", {})
    )
    resolved, integrity = entry.get("resolved", ""), entry.get("integrity", "")
    if (
        entry.get("name") != NPM_PACKAGE
        or entry.get("version") != version
        or not str(resolved).startswith(NPM_TARBALLS)
        or not str(integrity).startswith("sha512-")
    ):
        raise CodexRuntimeError(
            "not_available",
            f"the npm lock in {folder} doesn't record {NPM_PACKAGE}@{version} from "
            f"registry.npmjs.org; reinstall with ./install.sh",
        )
    return str(integrity)


def _npm_program() -> Path:
    for candidate in npm_programs():
        if candidate.is_file():
            return candidate
    raise CodexRuntimeError(
        "not_available",
        "no npm to check Codex's signatures with; ./install.sh puts one in "
        f"{npm_programs()[0].parent}",
    )


def _npm(
    npm: Path, arguments: list[str], folder: Path, workdir: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run npm against the public registry, with the owner's home (for npm's cache) and no PATH
    but npm's own folder and the system's."""
    environment = {
        "PATH": f"{npm.parent}:/usr/bin:/bin",
        "HOME": os.environ.get("HOME", str(Path.home())),
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "NPM_CONFIG_FUND": "false",
    }
    return _run(
        [str(npm), *arguments, f"--registry={NPM_REGISTRY}"],
        timeout=NPM_TIMEOUT_SECONDS,
        env=environment,
        cwd=workdir or folder,
    )


def _signatures_and_attestations(npm: Path, folder: Path) -> None:
    audited = _npm(npm, ["audit", "signatures"], folder)
    printed = audited.stdout + audited.stderr
    if audited.returncode != 0:
        raise CodexRuntimeError(
            "not_available",
            f"npm couldn't confirm the signatures of the Codex packages: {_detail(printed)}",
        )
    total, attested = AUDITED.search(printed), ATTESTED.search(printed)
    if total is None or attested is None or attested.group(1) != total.group(1):
        raise CodexRuntimeError(
            "not_available",
            "npm didn't confirm a provenance attestation for every Codex package "
            f"({attested.group(1) if attested else 0} of {total.group(1) if total else '?'}); "
            "an npm older than 9.5 can't check them",
        )


def _packed(npm: Path, folder: Path, version: str, integrity: str, workdir: Path) -> Path:
    """Fetch the platform package's tarball as npm serves it; hold it to the lock's integrity."""
    packed = _npm(
        npm,
        ["pack", f"{NPM_PACKAGE}@{version}", "--json", "--pack-destination", str(workdir)],
        folder,
        workdir,
    )
    try:
        (answer,) = json.loads(packed.stdout)
        filename, served = answer["filename"], answer["integrity"]
    except (ValueError, KeyError, TypeError):
        raise CodexRuntimeError(
            "not_available",
            f"npm couldn't fetch {NPM_PACKAGE}@{version} to compare: {_detail(packed.stderr)}",
        ) from None
    if served != integrity:
        raise CodexRuntimeError(
            "not_available",
            f"the {NPM_PACKAGE}@{version} npm serves isn't the one installed (integrity differs)",
        )
    return workdir / Path(filename).name


def _same_files(package: Path, tarball: Path) -> None:
    """Every file in the installed package equals the tarball's, and nothing is added or missing."""
    packed: dict[str, str] = {}
    repository = ""
    with tarfile.open(tarball, "r:gz") as archive:
        for member in archive:
            if member.isdir():
                continue
            name = member.name.removeprefix("package/")
            source = archive.extractfile(member) if member.isfile() else None
            if name == member.name or source is None:
                raise CodexRuntimeError(
                    "not_available", f"the Codex tarball holds something unexpected: {member.name}"
                )
            with source:
                if name == "package.json":
                    raw = source.read()
                    repository = str(json.loads(raw).get("repository", {}).get("url", ""))
                    packed[name] = hashlib.sha256(raw).hexdigest()
                else:
                    packed[name] = _digest(source)
    if OPENAI_REPOSITORY not in repository:
        raise CodexRuntimeError(
            "not_available", f"the Codex package names {repository or 'no'} source repository"
        )
    installed = _installed_files(package)
    differing = sorted(
        name for name in packed.keys() | installed.keys() if packed.get(name) != installed.get(name)
    )
    if differing:
        raise CodexRuntimeError(
            "not_available",
            f"{len(differing)} installed Codex file(s) differ from OpenAI's package, "
            f"e.g. {', '.join(differing[:3])}; reinstall with ./install.sh",
        )


def _digest(source: IO[bytes]) -> str:
    """The SHA-256 of a tarball member, read in 1 MiB pieces: the binary alone is 230 MB."""
    digest = hashlib.sha256()
    while piece := source.read(1 << 20):
        digest.update(piece)
    return digest.hexdigest()


def _installed_files(package: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for folder, directories, files in os.walk(package):
        for name in (*directories, *files):
            path = Path(folder) / name
            if path.is_symlink():
                raise CodexRuntimeError(
                    "not_available", f"{path} is a link; OpenAI's Codex package holds none"
                )
        for name in files:
            path = Path(folder) / name
            with path.open("rb") as binary:
                found[path.relative_to(package).as_posix()] = hashlib.file_digest(
                    binary, "sha256"
                ).hexdigest()
    return found


def _json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise CodexRuntimeError(
            "not_available", f"{path} can't be read; reinstall Codex with ./install.sh"
        ) from None
    return loaded if isinstance(loaded, dict) else {}
