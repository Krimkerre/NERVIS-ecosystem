"""Test doubles for the programs the Codex runtime check runs (runbook §7).

The check runs three programs: `brew --prefix`, `codesign` and Codex itself. A test may run none of
the real ones — the real Codex writes into whatever home it is handed, and the real `codesign`
would refuse an unsigned script — so each double here is a small shell script that the check runs
*as a program*, exactly as it runs the real one. Nothing in the check is patched to reach them.

Named `Fake…`, as §7 asks. A command-line program has no identity endpoint to report
`test_double: true` from, so the fake Codex writes it into every schema file it generates instead,
where a schema read by mistake would say what it is. Each double does only what the check asks of
it, plus the one failure a test needs.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ravis.codex.runtime import tree_sha256

#: OpenAI's Apple team: the fake `codesign` vouches for it unless told another.
PINNED_TEAM = "2DC432GLL2"

#: What the fake Codex generates, by path under `--out`. Nested, so a hash that skipped folders
#: would be caught.
STABLE_SCHEMA = {
    "codex_app_server_protocol.schemas.json": '{"test_double": true, "tree": "stable"}',
    "v2/ThreadStartParams.json": '{"test_double": true, "title": "ThreadStartParams"}',
}
#: `--experimental` adds to the stable tree, as the real one does.
EXPERIMENTAL_SCHEMA = {
    **STABLE_SCHEMA,
    "v2/PermissionProfileListResponse.json": '{"test_double": true, "experimental": true}',
}


def _script(path: Path, body: str) -> Path:
    """Write an executable `/bin/sh` script, mode 0755 like an installed program."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def _writes(files: dict[str, str]) -> str:
    """Shell lines that write `files` under the folder named in `$out`."""
    lines = []
    for name, content in files.items():
        target = f'"$out"/{shlex.quote(name)}'
        lines.append(f'  /bin/mkdir -p "$(/usr/bin/dirname {target})"')
        lines.append(f"  printf '%s' {shlex.quote(content)} > {target}")
    return "\n".join(lines)


@dataclass(frozen=True)
class CodexRun:
    """One run of the fake Codex, as that run saw its own environment."""

    arguments: str
    codex_home: str
    home: str
    tmpdir: str


@dataclass(frozen=True)
class FakeCodex:
    """A stand-in `codex` answering `--version` and `app-server generate-json-schema`.

    Every run appends a line to `log` — its arguments, then `CODEX_HOME`, `HOME` and `TMPDIR` as
    it saw them — so a test can say where Codex ran and how often. `build` is written into the
    script, which makes two builds two files with different sha256s, as two releases are.
    """

    path: Path
    log: Path

    @classmethod
    def install(
        cls,
        path: Path,
        *,
        build: str = "one",
        version: str = "0.154.0",
        fail_schema: bool = False,
    ) -> FakeCodex:
        log = path.parent / f".{path.name}-{build}-runs.log"
        failure = (
            '  echo "FakeCodex: schema generation fails, as scripted" >&2\n  exit 7\n'
            if fail_schema
            else ""
        )
        experimental_only = {
            name: content
            for name, content in EXPERIMENTAL_SCHEMA.items()
            if name not in STABLE_SCHEMA
        }
        body = f"""# FakeCodex, build {build}: a test double (runbook §7), not Codex.
printf '%s\\t%s\\t%s\\t%s\\n' "$*" "$CODEX_HOME" "$HOME" "$TMPDIR" >> {shlex.quote(str(log))}
if [ -n "$CODEX_HOME" ]; then /bin/mkdir -p "$CODEX_HOME/tmp/arg0"; fi
if [ "$1" = "--version" ]; then
  echo 'codex-cli {version}'
  exit 0
fi
if [ "$1" = "app-server" ] && [ "$2" = "generate-json-schema" ] && [ "$3" = "--out" ]; then
{failure}  out="$4"
{_writes(STABLE_SCHEMA)}
  if [ "$5" = "--experimental" ]; then
{_writes(experimental_only)}
  fi
  exit 0
fi
echo "FakeCodex does not implement: $*" >&2
exit 2
"""
        return cls(_script(path, body), log)

    def runs(self) -> list[CodexRun]:
        """Every run so far, oldest first; empty when Codex was never run."""
        if not self.log.exists():
            return []
        return [CodexRun(*line.split("\t")) for line in self.log.read_text().splitlines()]


@dataclass(frozen=True)
class FakeCodesign:
    """A stand-in `/usr/bin/codesign`, satisfied only by the requirement that names `team`.

    It matches the whole argument list the check sends — `--verify --strict`, Apple's anchor and
    the team — so a check that dropped any part of the requirement is refused here.
    """

    path: Path

    @classmethod
    def install(cls, path: Path, *, team: str = PINNED_TEAM) -> FakeCodesign:
        accepted = (
            "--verify --strict -R=anchor apple generic and certificate leaf[subject.OU] = "
            f'"{team}" '
        )
        body = (
            f'case "$*" in\n  {shlex.quote(accepted)}*) exit 0 ;;\nesac\n'
            'echo "test-requirement: code failed to satisfy specified code requirement(s)" >&2\n'
            "exit 3\n"
        )
        return cls(_script(path, body))


@dataclass(frozen=True)
class FakeBrew:
    """A stand-in `brew` for PATH: answers `--prefix`, and notes each time it is asked anything."""

    path: Path
    asked: Path

    @classmethod
    def install(cls, folder: Path, *, prefix: Path) -> FakeBrew:
        asked = folder / "brew-asked.log"
        body = (
            f'echo "$*" >> {shlex.quote(str(asked))}\n'
            f'if [ "$1" = "--prefix" ]; then echo {shlex.quote(str(prefix))}; exit 0; fi\n'
            "exit 1\n"
        )
        return cls(_script(folder / "brew", body), asked)


def homebrew_codex(prefix: Path, *, version: str = "0.154.0", build: str = "one") -> FakeCodex:
    """A Homebrew prefix with Codex laid out as its cask lays it out: a copy, and a link to it."""
    copy = FakeCodex.install(
        prefix / "Caskroom" / "codex" / version / "bin" / "codex", build=build, version=version
    )
    point_homebrew_link(prefix, copy)
    return copy


def point_homebrew_link(prefix: Path, codex: FakeCodex) -> Path:
    """Point Homebrew's `bin/codex` at `codex`, as `brew upgrade` re-points it."""
    link = prefix / "bin" / "codex"
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    link.symlink_to(codex.path)
    return link


def schema_tree_sha256(files: dict[str, str]) -> str:
    """The tree hash of a generated tree holding `files`, as the check computes it."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for name, content in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(content)
        return tree_sha256(root)


def pin_entry(codex: FakeCodex, *, proven: bool = False, **changed: Any) -> dict[str, Any]:
    """A `tested_runtimes.json` entry pinning this fake build, with any field changed."""
    return {
        "codex_version": "0.154.0",
        "source": "test double",
        "sha256": hashlib.sha256(codex.path.read_bytes()).hexdigest(),
        "stable_tree": schema_tree_sha256(STABLE_SCHEMA),
        "experimental_tree": schema_tree_sha256(EXPERIMENTAL_SCHEMA),
        "strict_rules_proven": proven,
        **changed,
    }


def write_pin(path: Path, *entries: dict[str, Any]) -> Path:
    """A pin file in `tested_runtimes.json`'s format, holding `entries`."""
    path.write_text(json.dumps({"format": 3, "tested": list(entries)}))
    return path
