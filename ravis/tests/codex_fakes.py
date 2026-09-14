"""Test doubles for the programs RAVIS's Codex code runs (runbook §7).

The runtime check runs three programs: `brew --prefix`, `codesign` and Codex itself. The Codex
service runs one more: Codex's app-server, the long-lived process that signs in, reads the
allowance and runs the file-rules re-test. A test may run none of the real ones — the real Codex
writes into whatever home it is handed, signs in to a real account and spends a real allowance,
and the real `codesign` would refuse an unsigned script — so each double here is a small program
that RAVIS runs *as a program*, exactly as it runs the real one. Nothing in RAVIS is patched to
reach them.

Named `Fake…`, as §7 asks. A command-line program has no identity endpoint to report
`test_double: true` from, so the fake Codex writes it into every schema file it generates instead,
where a schema read by mistake would say what it is.

- **`FakeCodex`** answers `--version` and `app-server generate-json-schema`, and runs
  `app-server --listen` as `fake_codex_app_server.py` when a `FakeAppServer` is given.
- **Its schema trees carry combined bundles** naming every method RAVIS's pin lists, in the shape
  Codex's own bundles have, so the acceptance checks read something real. A test changes a
  definition, or drops a method, by passing its own bundle.
- **`FakeAppServer`** is the app-server's folder: the scenario it reads at each start, the control
  folder a test drops commands into while it runs, and the log of everything it received.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import sys
import tempfile
import time
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ravis.codex.pin import MESSAGE_KINDS, read_pin, used_surface
from ravis.codex.runtime import tree_sha256
from ravis.codex.schema_report import COMBINED_BUNDLE, UNIONS, definition_record, read_bundle

#: OpenAI's Apple team: the fake `codesign` vouches for it unless told another.
PINNED_TEAM = "2DC432GLL2"
#: The fake app-server program, run with this test run's own Python.
APP_SERVER_PROGRAM = Path(__file__).with_name("fake_codex_app_server.py")


def _definition_name(method: str) -> str:
    """`AccountRateLimitsRead` for `account/rateLimits/read`: a stand-in for Codex's own names."""
    return "".join(part[:1].upper() + part[1:] for part in method.split("/"))


def _variant(method: str) -> dict[str, Any]:
    return {
        "properties": {
            "method": {"enum": [method]},
            "params": {"$ref": f"#/definitions/v2/{_definition_name(method)}Params"},
        }
    }


def fake_bundle(
    *,
    experimental: bool,
    changed: Mapping[str, Any] | None = None,
    missing: Collection[str] = (),
) -> str:
    """A combined bundle naming every method the pin lists, as a generated tree carries one.

    `changed` replaces definitions by name (under `v2`); `missing` leaves methods out.
    """
    used = used_surface(read_pin())
    v2: dict[str, Any] = {"SharedShape": {"test_double": True, "title": "SharedShape"}}
    for kind in MESSAGE_KINDS:
        for method in used.methods[kind]:
            name = _definition_name(method)
            v2[f"{name}Params"] = {
                "title": f"{name}Params",
                "properties": {"shared": {"$ref": "#/definitions/v2/SharedShape"}},
            }
            v2[f"{name}Response"] = {"title": f"{name}Response"}
    if experimental:
        v2.update(
            {name: {"title": name, "experimental": True} for name in used.surface_definitions}
        )
    v2.update(changed or {})
    unions: dict[str, Any] = {}
    for kind, union in UNIONS.items():
        methods = [method for method in used.methods[kind] if method not in missing]
        if experimental and kind == "client_requests":
            # One variant for a method both lists hold, as Codex's own bundle has.
            methods.extend([method for method in used.surface_methods if method not in methods])
        unions[union] = {"oneOf": [_variant(method) for method in methods]}
    return json.dumps({"test_double": True, "definitions": {**unions, "v2": v2}}, sort_keys=True)


#: What the fake Codex generates, by path under `--out`. Nested, so a hash that skipped folders
#: would be caught.
STABLE_SCHEMA = {
    COMBINED_BUNDLE: fake_bundle(experimental=False),
    "v2/ThreadStartParams.json": '{"test_double": true, "title": "ThreadStartParams"}',
}
#: `--experimental` adds to the stable tree and rewrites the combined bundle, as the real one does.
EXPERIMENTAL_SCHEMA = {
    **STABLE_SCHEMA,
    COMBINED_BUNDLE: fake_bundle(experimental=True),
    "v2/PermissionProfileListResponse.json": '{"test_double": true, "experimental": true}',
}


def _script(path: Path, body: str) -> Path:
    """Write an executable `/bin/sh` script, mode 0755 like an installed program."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def _writes(files: Mapping[str, str]) -> str:
    """Shell lines that write `files` under the folder named in `$out`."""
    lines = []
    for name, content in files.items():
        target = f'"$out"/{shlex.quote(name)}'
        lines.append(f'  /bin/mkdir -p "$(/usr/bin/dirname {target})"')
        lines.append(f"  printf '%s' {shlex.quote(content)} > {target}")
    return "\n".join(lines)


@dataclass(frozen=True)
class FakeAppServer:
    """The fake app-server's folder: its scenario, the commands sent to it, and its log."""

    folder: Path

    @classmethod
    def create(cls, folder: Path, **scenario: Any) -> FakeAppServer:
        (folder / "control").mkdir(parents=True, exist_ok=True)
        server = cls(folder)
        server.set_scenario(**scenario)
        return server

    @property
    def scenario_path(self) -> Path:
        return self.folder / "scenario.json"

    @property
    def control(self) -> Path:
        return self.folder / "control"

    @property
    def log(self) -> Path:
        return self.folder / "log.jsonl"

    def set_scenario(self, **scenario: Any) -> None:
        """The scenario the *next* start reads; a running fake keeps the one it started with."""
        self.scenario_path.write_text(json.dumps(scenario))

    def send(self, do: str, **fields: Any) -> None:
        """Drop a command for the running fake, written whole before it can be seen."""
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex[:6]}.json"
        temporary = self.control / (name + ".part")
        temporary.write_text(json.dumps({"do": do, **fields}))
        temporary.rename(self.control / name)

    def records(self, kind: str) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        lines = (json.loads(line) for line in self.log.read_text().splitlines() if line)
        return [line for line in lines if line.get("kind") == kind]

    def received(self, method: str) -> list[dict[str, Any]]:
        """Every message RAVIS sent the fake with this method, oldest first."""
        return [
            record["message"]
            for record in self.records("received")
            if record["message"].get("method") == method
        ]

    def starts(self) -> list[dict[str, Any]]:
        return self.records("start")


@dataclass(frozen=True)
class CodexRun:
    """One run of the fake Codex, as that run saw its own environment."""

    arguments: str
    codex_home: str
    home: str
    tmpdir: str


@dataclass(frozen=True)
class FakeCodex:
    """A stand-in `codex`: `--version`, `app-server generate-json-schema`, and the app-server.

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
        app_server: FakeAppServer | None = None,
        experimental_schema: Mapping[str, str] = EXPERIMENTAL_SCHEMA,
    ) -> FakeCodex:
        log = path.parent / f".{path.name}-{build}-runs.log"
        failure = (
            '  echo "FakeCodex: schema generation fails, as scripted" >&2\n  exit 7\n'
            if fail_schema
            else ""
        )
        experimental_only = {
            name: content
            for name, content in experimental_schema.items()
            if STABLE_SCHEMA.get(name) != content
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
{_app_server_lines(app_server)}
echo "FakeCodex does not implement: $*" >&2
exit 2
"""
        return cls(_script(path, body), log)

    def runs(self) -> list[CodexRun]:
        """Every run so far, oldest first; empty when Codex was never run."""
        if not self.log.exists():
            return []
        return [CodexRun(*line.split("\t")) for line in self.log.read_text().splitlines()]


def _app_server_lines(server: FakeAppServer | None) -> str:
    """The script's `app-server --listen` branch: the fake app-server, with its folder named."""
    if server is None:
        return ""
    return f"""if [ "$1" = "app-server" ] && [ "$2" = "--listen" ]; then
  export FAKE_CODEX_SCENARIO={shlex.quote(str(server.scenario_path))}
  export FAKE_CODEX_CONTROL={shlex.quote(str(server.control))}
  export FAKE_CODEX_LOG={shlex.quote(str(server.log))}
  exec {shlex.quote(sys.executable)} {shlex.quote(str(APP_SERVER_PROGRAM))} "$@"
fi"""


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


def _tree(folder: Path, files: Mapping[str, str]) -> Path:
    for name, content in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(content)
    return folder


def schema_tree_sha256(files: Mapping[str, str]) -> str:
    """The tree hash of a generated tree holding `files`, as the check computes it."""
    with tempfile.TemporaryDirectory() as folder:
        return tree_sha256(_tree(Path(folder), files))


def write_definitions(
    path: Path, experimental_schema: Mapping[str, str] = EXPERIMENTAL_SCHEMA
) -> Path:
    """A definition-hashes file for a fake build, built the way the real one was."""
    with tempfile.TemporaryDirectory() as folder:
        tree = _tree(Path(folder), experimental_schema)
        record = definition_record(
            read_bundle(tree),
            used_surface(read_pin()),
            codex_version="0.154.0",
            experimental_tree=tree_sha256(tree),
        )
    path.write_text(json.dumps(record))
    return path


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


def write_pin(path: Path, *entries: dict[str, Any], **document: Any) -> Path:
    """A pin in `tested_runtimes.json`'s format: the committed one's methods, and `entries`."""
    committed = read_pin()
    pin = {
        "format": 3,
        "used_methods": committed["used_methods"],
        "strict_rules_surface": committed["strict_rules_surface"],
        "file_rules_profile": None,
        "tested": list(entries),
        **document,
    }
    path.write_text(json.dumps(pin))
    return path
