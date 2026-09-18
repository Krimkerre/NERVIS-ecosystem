"""Where a Codex on Linux came from: Arch's package, or OpenAI's npm build (`linux_origin.py`).

Every test here stands in for Linux (`CODEX_PLATFORM = "linux"`). pacman and npm are small fake
programs RAVIS runs *as programs*, like the fake `codesign` in the Mac tests: pacman answers who
owns a file and whether the package's files changed; npm answers `audit signatures` with the text
the real npm 10 prints, and `pack` with a real tarball built from the same files a test installs,
so the file-by-file comparison runs against a real archive.

The folders all live under the suite's throwaway `XDG_DATA_HOME`, which is also where RAVIS looks
for the installer's private npm, so the fake npm needs no setting to be found.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import shlex
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.codex_fakes import FakeCodex, pin_entry, write_pin

from ravis.codex import linux_origin
from ravis.codex.runtime import CodexRuntime, CodexRuntimeError, locate_executable
from ravis.config import Settings, data_directory

PLATFORM_PACKAGE = "codex-linux-x64"
VERSION = "0.155.0-linux-x64"
TRIPLE = "x86_64-unknown-linux-musl"
ALL_ATTESTED = (
    "audited 2 packages in 1s\n\n2 packages have verified registry signatures\n\n"
    "2 packages have verified attestations\n"
)


@pytest.fixture(autouse=True)
def _stand_in_for_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("ravis.config.CODEX_PLATFORM", "linux")
    # No pacman unless a test installs the fake one; never the machine's own.
    monkeypatch.setattr(linux_origin, "PACMAN", str(tmp_path / "no-pacman"))
    monkeypatch.setattr(linux_origin, "ARCH_EXECUTABLE", tmp_path / "usr-bin" / "codex")


def _settings(**overrides: object) -> Settings:
    return Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
        **{"codex_enabled": None, **overrides},
    )


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


# ── The npm install, as install.sh leaves it ────────────────────────────────


@dataclass
class NpmInstall:
    """NERVIS's npm folder with Codex's two packages, the lock, the link, and a fake npm."""

    folder: Path
    package: Path
    codex: FakeCodex
    tarball: Path
    audit_output: Path

    @property
    def binary(self) -> Path:
        return self.package / "vendor" / TRIPLE / "bin" / "codex"

    @property
    def bwrap(self) -> Path:
        return self.package / "vendor" / TRIPLE / "codex-resources" / "bwrap"

    def pack(self, *, repository: str = "git+https://github.com/openai/codex.git") -> None:
        """Build the tarball npm will serve from the installed files, and lock its integrity."""
        manifest = {
            "name": "@openai/codex",
            "version": VERSION,
            "repository": {"type": "git", "url": repository},
        }
        (self.package / "package.json").write_text(json.dumps(manifest))
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for path in sorted(self.package.rglob("*")):
                if path.is_file():
                    data = path.read_bytes()
                    member = tarfile.TarInfo("package/" + path.relative_to(self.package).as_posix())
                    member.size, member.mode = len(data), 0o755
                    archive.addfile(member, io.BytesIO(data))
        self.tarball.write_bytes(buffer.getvalue())
        self.lock(integrity=_integrity(buffer.getvalue()))

    def lock(
        self,
        *,
        integrity: str,
        resolved: str = f"https://registry.npmjs.org/@openai/codex/-/codex-{VERSION}.tgz",
    ) -> None:
        entry = {
            "name": "@openai/codex",
            "version": VERSION,
            "resolved": resolved,
            "integrity": integrity,
        }
        lock = {"packages": {f"node_modules/@openai/{PLATFORM_PACKAGE}": entry}}
        (self.folder / "package-lock.json").write_text(json.dumps(lock))

    def audit_says(self, text: str, *, status: int = 0) -> None:
        self.audit_output.write_text(json.dumps({"text": text, "status": status}))


def _integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


@pytest.fixture
def npm_install(tmp_path: Path) -> NpmInstall:
    folder = linux_origin.npm_folder()
    package = folder / "node_modules" / "@openai" / PLATFORM_PACKAGE
    package.mkdir(parents=True)
    codex = FakeCodex.install(package / "vendor" / TRIPLE / "bin" / "codex", version="0.155.0")
    bwrap = package / "vendor" / TRIPLE / "codex-resources" / "bwrap"
    bwrap.parent.mkdir(parents=True)
    bwrap.write_bytes(b"the sandbox program\n")
    linux_origin.npm_link().symlink_to(codex.path)
    install = NpmInstall(folder, package, codex, tmp_path / "served.tgz", tmp_path / "audit.json")
    install.pack()
    install.audit_says(ALL_ATTESTED)
    _fake_npm(linux_origin.npm_programs()[0], install)
    return install


def _fake_npm(path: Path, install: NpmInstall) -> None:
    """npm's two commands the check runs: `audit signatures`, and `pack <spec> --json …`."""
    program = f"""
import hashlib, base64, json, shutil, sys
arguments = sys.argv[1:]
assert arguments[-1] == "--registry=https://registry.npmjs.org/", arguments
if arguments[:2] == ["audit", "signatures"]:
    said = json.load(open({str(install.audit_output)!r}))
    print(said["text"])
    sys.exit(said["status"])
if arguments[0] == "pack":
    assert arguments[1] == "@openai/codex@{VERSION}", arguments
    destination = arguments[arguments.index("--pack-destination") + 1]
    data = open({str(install.tarball)!r}, "rb").read()
    shutil.copy({str(install.tarball)!r}, destination + "/openai-codex-{VERSION}.tgz")
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
    print(json.dumps([{{"filename": "openai-codex-{VERSION}.tgz", "integrity": integrity}}]))
    sys.exit(0)
sys.exit("fake npm: not implemented: " + " ".join(arguments))
"""
    _script(path, f'exec {shlex.quote(sys.executable)} -c {shlex.quote(program)} "$@"\n')


def _vouched(install: NpmInstall) -> str:
    return linux_origin.vouched(install.binary.resolve(), data_directory() / "scratch")


def _refused(install: NpmInstall) -> str:
    with pytest.raises(CodexRuntimeError) as refused:
        _vouched(install)
    assert refused.value.state == "not_available"
    return refused.value.reason


# ── npm ─────────────────────────────────────────────────────────────────────


def test_openai_s_npm_build_with_every_file_matching_is_vouched_for(
    npm_install: NpmInstall,
) -> None:
    assert _vouched(npm_install) == "npm"
    # The downloaded tarball went into a throwaway folder that is gone again.
    assert list((data_directory() / "scratch").iterdir()) == []


def test_a_changed_sandbox_program_is_refused_and_named(npm_install: NpmInstall) -> None:
    """Not only `codex` itself: the package's own `bwrap` fences in Codex's commands."""
    npm_install.bwrap.write_bytes(b"something else\n")

    reason = _refused(npm_install)

    assert "1 installed Codex file(s) differ" in reason
    assert f"vendor/{TRIPLE}/codex-resources/bwrap" in reason


def test_a_file_added_to_the_package_is_refused(npm_install: NpmInstall) -> None:
    (npm_install.package / "vendor" / TRIPLE / "codex-resources" / "extra.so").write_bytes(b"x")

    assert "extra.so" in _refused(npm_install)


def test_a_link_inside_the_package_is_refused(npm_install: NpmInstall, tmp_path: Path) -> None:
    (npm_install.package / "vendor" / TRIPLE / "elsewhere").symlink_to(tmp_path)

    assert "is a link" in _refused(npm_install)


def test_a_package_missing_its_provenance_attestation_is_refused(
    npm_install: NpmInstall,
) -> None:
    npm_install.audit_says(
        "audited 2 packages in 1s\n\n2 packages have verified registry signatures\n\n"
        "1 package has a verified attestation\n"
    )

    assert "(1 of 2)" in _refused(npm_install)


def test_an_npm_too_old_to_check_attestations_is_refused_by_name(
    npm_install: NpmInstall,
) -> None:
    npm_install.audit_says(
        "audited 2 packages in 0s\n\n2 packages have verified registry signatures"
    )

    assert "older than 9.5" in _refused(npm_install)


def test_a_failed_signature_audit_is_refused_with_npm_s_words(npm_install: NpmInstall) -> None:
    npm_install.audit_says("1 package has an invalid registry signature", status=1)

    assert "invalid registry signature" in _refused(npm_install)


def test_a_served_tarball_that_is_not_the_locked_one_is_refused(npm_install: NpmInstall) -> None:
    npm_install.lock(integrity=_integrity(b"another tarball"))

    assert "integrity differs" in _refused(npm_install)


def test_a_package_fetched_from_anywhere_but_openai_s_address_is_refused(
    npm_install: NpmInstall,
) -> None:
    npm_install.lock(
        integrity=_integrity(npm_install.tarball.read_bytes()),
        resolved="https://mirror.example/@openai/codex/-/codex.tgz",
    )

    assert "from registry.npmjs.org" in _refused(npm_install)


def test_a_package_naming_another_source_repository_is_refused(npm_install: NpmInstall) -> None:
    npm_install.pack(repository="git+https://github.com/someone/codex-fork.git")

    assert "someone/codex-fork" in _refused(npm_install)


def test_without_any_npm_the_reason_says_where_the_installer_puts_one(
    npm_install: NpmInstall, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(linux_origin, "npm_programs", lambda: (tmp_path / "none" / "npm",))

    assert "no npm to check" in _refused(npm_install)


def test_a_codex_from_neither_place_is_refused_with_the_way_to_fix_it(tmp_path: Path) -> None:
    stray = FakeCodex.install(tmp_path / "opt" / "codex")

    with pytest.raises(CodexRuntimeError) as refused:
        linux_origin.vouched(stray.path, tmp_path / "scratch")

    assert "neither Arch's openai-codex package nor NERVIS's npm install" in refused.value.reason
    assert "./install.sh" in refused.value.reason


# ── Arch ────────────────────────────────────────────────────────────────────


def _fake_pacman(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, owns: Path, altered: bool
) -> Path:
    asked = tmp_path / "pacman-asked.log"
    body = f"""echo "$*" >> {shlex.quote(str(asked))}
if [ "$1" = "-Qqo" ]; then
  [ "$2" = {shlex.quote(str(owns))} ] && echo openai-codex && exit 0
  echo "error: No package owns $2" >&2; exit 1
fi
if [ "$1" = "-Qkk" ] && [ "$2" = openai-codex ]; then
  if {"true" if altered else "false"}; then
    echo "warning: openai-codex: /usr/bin/codex (SHA256 checksum mismatch)" >&2; exit 1
  fi
  echo "openai-codex: 15 total files, 0 altered files"; exit 0
fi
exit 2
"""
    pacman = _script(tmp_path / "pacman", body)
    monkeypatch.setattr(linux_origin, "PACMAN", str(pacman))
    return asked


def test_arch_s_package_unaltered_is_vouched_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = FakeCodex.install(linux_origin.ARCH_EXECUTABLE)
    asked = _fake_pacman(tmp_path, monkeypatch, owns=codex.path, altered=False)

    assert linux_origin.vouched(codex.path, tmp_path / "scratch") == "pacman"
    assert asked.read_text().splitlines() == [f"-Qqo {codex.path}", "-Qkk openai-codex"]


def test_arch_s_package_with_a_changed_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = FakeCodex.install(linux_origin.ARCH_EXECUTABLE)
    _fake_pacman(tmp_path, monkeypatch, owns=codex.path, altered=True)

    with pytest.raises(CodexRuntimeError) as refused:
        linux_origin.vouched(codex.path, tmp_path / "scratch")

    assert "SHA256 checksum mismatch" in refused.value.reason


def test_the_executable_is_arch_s_when_pacman_owns_it_and_the_npm_link_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = FakeCodex.install(linux_origin.ARCH_EXECUTABLE)
    assert linux_origin.default_link() == linux_origin.npm_link()  # no pacman at all

    _fake_pacman(tmp_path, monkeypatch, owns=tmp_path / "something-else", altered=False)
    assert linux_origin.default_link() == linux_origin.npm_link()  # pacman, not its file

    _fake_pacman(tmp_path, monkeypatch, owns=codex.path, altered=False)
    assert locate_executable(_settings()) == codex.path.resolve()


def test_with_nothing_installed_codex_is_not_installed() -> None:
    with pytest.raises(CodexRuntimeError) as refused:
        locate_executable(_settings())

    assert refused.value.state == "not_installed"


# ── The whole startup check ─────────────────────────────────────────────────


def test_the_startup_check_follows_the_npm_link_and_reports_npm_with_no_apple_team(
    npm_install: NpmInstall, tmp_path: Path
) -> None:
    pin = write_pin(
        tmp_path / "pin.json", pin_entry(npm_install.codex, proven=True, codex_version="0.155.0")
    )
    runtime = CodexRuntime(_settings(), codesign=str(tmp_path / "no-codesign"), pin=pin)

    asyncio.run(runtime.check())

    assert runtime.report.state is None, runtime.report.reason
    assert runtime.report.source == "npm"
    assert runtime.report.team_id is None
    assert runtime.report.version == "0.155.0"
    assert runtime.executable == npm_install.binary.resolve()
