"""Is the Codex on this Mac the build RAVIS pinned? Checked once at startup, off the request path.

Runbook §2.2, invariant 6: **any Codex binary not recorded as tested or accepted pauses new
tasks.** Homebrew replaces Codex whenever the owner upgrades, and a new build can change the
protocol RAVIS speaks to it or the sandbox rules that keep its commands away from key and password
files. So RAVIS never assumes which Codex it has: it looks once per start and says plainly what it
found.

The check stops at the first row of the Codex state table that applies — an earlier row wins
(`tests/fixtures/relay-contract/codex-state.json`):

1. **Switched off** (`RAVIS_CODEX_ENABLED=false`): `not_available`, and nothing is run.
2. **Where the executable is:** the configured path, or else Homebrew's link,
   `$(brew --prefix)/bin/codex`, followed to the file it points at. No file: `not_installed`.
3. **Whether to trust it:** the Codex home is neither the ChatGPT app's `~/.codex` nor inside
   RAVIS's configuration folder; the configured path doesn't name Homebrew's versioned copy;
   nobody else can change the file; and OpenAI's Apple team signed it. Otherwise: `not_available`.
4. **Which build it is:** its sha256, the version it prints, and one hash for each of the two
   protocol schema trees it generates — run in a throwaway Codex home deleted afterwards.
5. **Whether that build is pinned:** all three hashes match an entry in `tested_runtimes.json`.
   A build that isn't pinned — or is pinned, but without file rules calibration has proven (owner
   decision D2) — is `untested_version`.

**Discovery never runs any of this.** `/v1/models` reads `CodexRuntime.enabled`, a kept answer,
because Clarvis probes that endpoint with a two-second timeout and reads slowness as "offline"
(RAVIS.md §4.3). The check runs in worker threads, started by the lifespan (`app.py`).

**What follows the last row is not here yet:** the Codex process, sign-in, the allowance and
accepting a new build are R2 in `STATUS.md`'s Codex build plan, which starts from this report.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Literal

from ravis.config import Settings, codex_home_refusal, data_directory, names_caskroom_copy

logger = logging.getLogger("ravis")

#: The rows of the Codex state table this check can end on. The rows after them — the process,
#: the account, the allowance — are R2's, and R2 reads this report before deciding them.
RuntimeState = Literal["checking", "not_installed", "not_available", "untested_version"]

#: The pin, committed beside this module and shipped inside the package.
TESTED_RUNTIMES: Traversable = resources.files("ravis.codex") / "tested_runtimes.json"
#: The pin's format, as the design numbers it (`design/codex-engine/design.md` §4.3).
PIN_FORMAT = 3

#: Always the system's own `codesign`, never one found on PATH: the program that vouches for
#: Codex must not be one a stray PATH entry could replace.
CODESIGN = "/usr/bin/codesign"
#: An Apple team identifier: ten capital letters and digits. Checked before it goes into the
#: signature requirement, so a mistyped setting reads as a mistyped setting, not as a
#: requirement `codesign` cannot parse.
TEAM_ID = re.compile(r"^[A-Z0-9]{10}$")

#: The folder, in the data directory, that holds the throwaway Codex homes (design §4.2). Each
#: check makes one inside it and deletes it when the check ends.
SCRATCH_FOLDER = "ravis-codex-scratch"
#: All that `codex --version` prints: `codex-cli 0.154.0`.
VERSION_LINE = re.compile(r"^codex-cli (\d+\.\d+\.\d+\S*)$")
#: The two schema trees, by the name the pin records each under, and the flags that generate it.
SCHEMA_TREES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stable", ()),
    ("experimental", ("--experimental",)),
)

#: `brew --prefix` answered in about 10 ms here; ten seconds is a Homebrew that is stuck.
BREW_TIMEOUT_SECONDS = 10.0
#: `codesign --verify` reads the whole binary, about 220 MB: a third of a second here.
SIGNATURE_TIMEOUT_SECONDS = 60.0
#: Each Codex run took 30-40 ms here; the design allows 30 s per schema tree (design §4.3).
CODEX_TIMEOUT_SECONDS = 30.0
#: How much of a failing program's error output a reason carries.
DETAIL_CHARACTERS = 200


class CodexRuntimeError(Exception):
    """The check stopped at a row of the state table, and says which row and why.

    Raised inside the check and caught once, in `CodexRuntime.check`, where it becomes the report
    (runbook §14.4: raise with context inside, translate once at the edge).
    """

    def __init__(self, state: RuntimeState, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


@dataclass(frozen=True)
class RuntimeReport:
    """What the last check found: a state, a reason a person can read, and the build's facts.

    `state` is `None` when none of this check's rows applies — the executable was found, trusted
    and pinned, with its file rules proven — so the rows after them decide. Each fact is `None`
    until the check has read it, never a guess.
    """

    state: RuntimeState | None
    reason: str
    source: str | None = None
    version: str | None = None
    installed_sha256: str | None = None
    team_id: str | None = None
    verdict: Literal["tested", "untested"] | None = None
    strict_rules: Literal["proven", "unproven"] | None = None
    stable_tree: str | None = None
    experimental_tree: str | None = None
    checked_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The state and reason, with the facts in the shape of `GET /api/v1/codex`'s `runtime`.

        R2 serves that block (`codex-state.json`). Until then the startup log prints this, so an
        operator can read what the check found without a route to ask.
        """
        return {
            "state": self.state,
            "reason": self.reason,
            "runtime": {
                "source": self.source,
                "version": self.version,
                "installed_sha256": self.installed_sha256,
                "team_id": self.team_id,
                "verdict": self.verdict,
                "strict_rules": self.strict_rules,
                "schema": {
                    "stable_tree": self.stable_tree,
                    "experimental_tree": self.experimental_tree,
                },
                "checked_at": self.checked_at,
            },
        }


def locate_executable(settings: Settings) -> Path:
    """The file Codex's executable leads to, followed afresh every time it is asked.

    Following the link each time is what makes `brew upgrade` read as a *new build* — the link now
    leads to a file with another sha256, which is `untested_version` — rather than as Codex gone.
    The path comes from settings or from Homebrew and nowhere else: never from a request, and
    never from the catalogue (design §4.1).
    """
    link = _configured_or_homebrew_link(settings)
    target = link.resolve()
    if not target.is_file():
        raise CodexRuntimeError("not_installed", f"{link} does not lead to a file")
    return target


def _configured_or_homebrew_link(settings: Settings) -> Path:
    """`RAVIS_CODEX_EXECUTABLE` when it is set, and otherwise Homebrew's `<prefix>/bin/codex`.

    Homebrew is asked for its prefix rather than assumed to be `/opt/homebrew`, which is only where
    it lives on Apple silicon (owner decision D4; design review N6).
    """
    if settings.codex_executable:
        return Path(settings.codex_executable).expanduser()
    brew = shutil.which("brew")
    if brew is None:
        raise CodexRuntimeError(
            "not_installed", "RAVIS_CODEX_EXECUTABLE is not set and Homebrew is not on PATH"
        )
    answered = _run([brew, "--prefix"], timeout=BREW_TIMEOUT_SECONDS)
    prefix = answered.stdout.strip()
    if answered.returncode != 0 or not prefix:
        raise CodexRuntimeError(
            "not_available", f"brew --prefix did not name a prefix: {_detail(answered.stderr)}"
        )
    return Path(prefix) / "bin" / "codex"


def inspect_executable(
    target: Path,
    settings: Settings,
    *,
    codesign: str = CODESIGN,
    pin: Traversable = TESTED_RUNTIMES,
) -> RuntimeReport:
    """Everything after finding the file: whether to trust it, which build, whether it is pinned."""
    _refuse_untrusted_location(target, settings)
    team = _verified_team(target, settings.codex_expected_team_id, codesign)
    with target.open("rb") as binary:
        installed_sha256 = hashlib.file_digest(binary, "sha256").hexdigest()
    version, trees = _read_build(target, data_directory() / SCRATCH_FOLDER)
    entry = _pinned_entry(_tested_builds(pin), installed_sha256, trees)
    verdict: Literal["tested", "untested"] = "untested" if entry is None else "tested"
    proven = entry is not None and entry.get("strict_rules_proven") is True
    state, reason = _runtime_row(version, verdict, proven)
    return RuntimeReport(
        state=state,
        reason=reason,
        # A cask's copy lives in Homebrew's `Caskroom`; anything else was named by hand.
        source="homebrew" if "Caskroom" in target.parts else "configured",
        version=version,
        installed_sha256=installed_sha256,
        team_id=team,
        verdict=verdict,
        strict_rules="proven" if proven else "unproven",
        stable_tree=trees["stable"],
        experimental_tree=trees["experimental"],
        checked_at=_now(),
    )


def _refuse_untrusted_location(target: Path, settings: Settings) -> None:
    """The refusals that need no program run: the home, the Caskroom path, the file's mode."""
    home = codex_home_refusal(settings)
    if home is not None:
        raise CodexRuntimeError("not_available", home)
    if names_caskroom_copy(settings.codex_executable):
        raise CodexRuntimeError(
            "not_available",
            f"RAVIS_CODEX_EXECUTABLE names Homebrew's versioned copy "
            f"({settings.codex_executable}); name the link, $(brew --prefix)/bin/codex, so an "
            "upgrade reads as a new build to re-test",
        )
    # A file other users can rewrite could be swapped between this check and its run.
    if target.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise CodexRuntimeError(
            "not_available", f"{target} can be changed by other users of this Mac"
        )


def _verified_team(target: Path, team: str, codesign: str) -> str:
    """Refuse a binary OpenAI's Apple team did not sign, and return the team that did.

    One `codesign` run checks it all: the signature is intact (`--strict`), and it satisfies a
    requirement naming Apple's certificate chain (`anchor apple generic`) and the team on the
    signing certificate. Measured on the installed Codex 0.154.0: the right team passes, and
    another team, or one of Apple's own programs, fails with status 3.
    """
    if not TEAM_ID.match(team):
        raise CodexRuntimeError(
            "not_available", f"RAVIS_CODEX_EXPECTED_TEAM_ID {team!r} is not an Apple team id"
        )
    requirement = f'anchor apple generic and certificate leaf[subject.OU] = "{team}"'
    checked = _run(
        [codesign, "--verify", "--strict", f"-R={requirement}", str(target)],
        timeout=SIGNATURE_TIMEOUT_SECONDS,
    )
    if checked.returncode != 0:
        raise CodexRuntimeError(
            "not_available",
            f"{target.name} is not signed by Apple team {team}: {_detail(checked.stderr)}",
        )
    return team


def _read_build(executable: Path, scratch_root: Path) -> tuple[str, dict[str, str]]:
    """The build's version and schema-tree hashes, read in a throwaway Codex home.

    Every Codex run writes into its Codex home — even `--help` leaves `tmp/arg0` behind — so none
    of these may run against a real one: not RAVIS's own, where the sign-in will live, and never
    the ChatGPT app's `~/.codex`. `HOME` and `TMPDIR` point into the same throwaway folder, the
    environment carries nothing else, and the folder is deleted however the runs end. Measured on
    13 September 2026 under a sandbox denying network access and every write outside the folder:
    both generations succeeded, and all Codex left in its home was an empty `tmp/arg0`.
    """
    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="check-", dir=scratch_root))
    try:
        environment = _throwaway_environment(workdir)
        version = _parsed_version(_codex(executable, ["--version"], environment, workdir).stdout)
        trees = {
            name: _schema_tree(executable, workdir / name, flags, environment)
            for name, flags in SCHEMA_TREES
        }
        return version, trees
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _throwaway_environment(workdir: Path) -> dict[str, str]:
    """The whole environment a Codex run gets: the system PATH, and homes inside `workdir`."""
    home = workdir / "home"
    throwaway_codex_home = home / "codex-home"
    temporary = home / "tmp"
    throwaway_codex_home.mkdir(parents=True)
    temporary.mkdir()
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "CODEX_HOME": str(throwaway_codex_home),
        "TMPDIR": str(temporary),
    }


def _codex(
    executable: Path, arguments: list[str], environment: dict[str, str], workdir: Path
) -> subprocess.CompletedProcess[str]:
    """Run Codex once in the throwaway home, and refuse the build when the run fails."""
    finished = _run(
        [str(executable), *arguments],
        timeout=CODEX_TIMEOUT_SECONDS,
        env=environment,
        cwd=workdir,
    )
    if finished.returncode != 0:
        command = " ".join(arguments[:2])
        raise CodexRuntimeError(
            "not_available",
            f"codex {command} exited with status {finished.returncode}: "
            f"{_detail(finished.stderr)}",
        )
    return finished


def _parsed_version(printed: str) -> str:
    """`0.154.0` from `codex-cli 0.154.0`; anything else leaves the build unidentified."""
    matched = VERSION_LINE.match(printed.strip())
    if matched is None:
        raise CodexRuntimeError(
            "not_available", f"codex --version printed {printed.strip()[:DETAIL_CHARACTERS]!r}"
        )
    return matched.group(1)


def _schema_tree(
    executable: Path, out: Path, flags: tuple[str, ...], environment: dict[str, str]
) -> str:
    """Generate one schema tree into `out`, and return its hash."""
    _codex(
        executable,
        ["app-server", "generate-json-schema", "--out", str(out), *flags],
        environment,
        out.parent,
    )
    if not out.is_dir() or not any(path.is_file() for path in out.rglob("*")):
        raise CodexRuntimeError("not_available", f"codex generated no {out.name} schema")
    return tree_sha256(out)


def tree_sha256(folder: Path) -> str:
    """One hash for a whole generated schema tree, computed the way the pinned values were.

    The value `find . -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256` prints
    inside `folder`, which is how the reference trees were recorded — so the pin can be checked
    by hand. Each regular file becomes a `<sha256>  ./<path>` line; the lines are ordered by the
    bytes of the path, as `sort -z` orders them in the C locale (this Mac's default locale gave
    the same hashes for Codex's trees); and the hash is of those lines. Symbolic links are left
    out, as `find -type f` leaves them out.
    """
    paths = sorted(
        ("./" + path.relative_to(folder).as_posix()).encode()
        for path in folder.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    manifest = hashlib.sha256()
    for relative in paths:
        digest = hashlib.sha256((folder / relative.decode()[2:]).read_bytes()).hexdigest()
        manifest.update(digest.encode() + b"  " + relative + b"\n")
    return manifest.hexdigest()


def _tested_builds(pin: Traversable) -> list[dict[str, Any]]:
    """The entries of `tested_runtimes.json`, or `not_available` when the record can't be read.

    The file is committed with the code, so an unreadable one is a broken RAVIS build — but it is
    reported as the check failing rather than raised into the lifespan, because RAVIS without
    Codex is a working RAVIS (runbook §2.2: "every product works without it").
    """
    try:
        document = json.loads(pin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        raise CodexRuntimeError(
            "not_available", f"the record of tested Codex builds can't be read: {failure}"
        ) from None
    tested = document.get("tested") if isinstance(document, dict) else None
    if not isinstance(tested, list) or document.get("format") != PIN_FORMAT:
        raise CodexRuntimeError(
            "not_available", f"the record of tested Codex builds is not format {PIN_FORMAT}"
        )
    return [entry for entry in tested if isinstance(entry, dict)]


def _pinned_entry(
    tested: list[dict[str, Any]], installed_sha256: str, trees: dict[str, str]
) -> dict[str, Any] | None:
    """The entry this build matches on all three hashes, or `None` when it matches none.

    All three rather than the sha256 alone (design review M1, M5): the sha256 says which file
    runs, and the trees say which protocol it speaks, which is what a later build's report is
    compared against. `None` is the ordinary answer for a build RAVIS hasn't tested, not a failure.
    """
    for entry in tested:
        if (
            entry.get("sha256") == installed_sha256
            and entry.get("stable_tree") == trees["stable"]
            and entry.get("experimental_tree") == trees["experimental"]
        ):
            return entry
    return None


def _runtime_row(
    version: str, verdict: Literal["tested", "untested"], proven: bool
) -> tuple[RuntimeState | None, str]:
    """Which of this check's rows the build lands on, and the reason to show for it.

    Owner decision D2 puts the stricter file rules in force for every task, so a pinned build
    whose rules calibration hasn't proven pauses new work just as an unknown build does. It
    differs only in the verdict it reports (runbook §2.2, invariant 6).
    """
    if verdict == "untested":
        return "untested_version", (
            f"Codex {version} is not a build RAVIS has tested, so new Codex work stays paused "
            "until it is re-tested."
        )
    if not proven:
        return "untested_version", (
            f"Codex {version} is the pinned build, but its file rules haven't been proven on it "
            "yet, so new Codex work stays paused until calibration proves them."
        )
    return None, f"Codex {version} is the pinned build, and its file rules are proven."


def _run(
    argv: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a program to the end, within a deadline, with nothing on its standard input.

    A program that cannot start or does not finish is the state table's "the check failed", so
    both are `not_available`, naming the program. A program that ran and failed is its caller's
    to judge, since what its exit status means differs from program to program.
    """
    name = Path(argv[0]).name
    try:
        return subprocess.run(
            argv,
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise CodexRuntimeError(
            "not_available", f"{name} did not finish within {timeout:.0f} s"
        ) from None
    except OSError as failure:
        raise CodexRuntimeError(
            "not_available", f"{name} could not be run: {failure.strerror or failure}"
        ) from None


def _detail(output: str) -> str:
    """The last line of a program's error output, cut short: enough to say why it failed."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return lines[-1][:DETAIL_CHARACTERS] if lines else "no error output"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class CodexRuntime:
    """The one kept answer to "can RAVIS use Codex?", filled in by one check at startup.

    Built with the application and run by its lifespan (`app.py`) in worker threads, so neither
    building the app nor any request ever runs a program. `report` starts at `checking` and is
    replaced once; `enabled` is what `/v1/models` reads.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        codesign: str = CODESIGN,
        pin: Traversable = TESTED_RUNTIMES,
    ) -> None:
        self._settings = settings
        self._codesign = codesign
        self._pin = pin
        # The file the executable led to when this start's check looked; None before it has
        # looked, and when it found nothing.
        self.executable: Path | None = None
        self.report = RuntimeReport(
            state="checking", reason="Codex has not been checked since RAVIS started."
        )

    @property
    def enabled(self) -> bool:
        """Whether `ravis/codex` is offered at all (RAVIS.md §4.3; runbook §2.2).

        The setting, when it is set. Otherwise, whether the executable led to a file when this
        start's check looked — so before the check has looked, and when it found nothing, the
        answer is no. A kept value, because `/v1/models` reads it and must never wait.
        """
        if self._settings.codex_enabled is not None:
            return self._settings.codex_enabled
        return self.executable is not None

    async def check(self) -> None:
        """Run the check once, off the event loop, and keep what it found. Never raises.

        Cancellation at shutdown is the one thing that passes through, as it should. A worker
        thread already running a program finishes it, and still deletes its throwaway home.
        """
        try:
            self.report = await self._checked()
        except CodexRuntimeError as refusal:
            self.report = RuntimeReport(
                state=refusal.state, reason=refusal.reason, checked_at=_now()
            )
        except Exception as failure:  # noqa: BLE001 — the state table's "the check failed"
            logger.exception("codex: the runtime check failed")
            self.report = RuntimeReport(
                state="not_available",
                reason=f"the Codex check failed: {failure}",
                checked_at=_now(),
            )
        logger.info("codex runtime check: %s", json.dumps(self.report.as_dict(), sort_keys=True))

    async def _checked(self) -> RuntimeReport:
        if self._settings.codex_enabled is False:
            raise CodexRuntimeError(
                "not_available", "Codex is switched off (RAVIS_CODEX_ENABLED=false)."
            )
        self.executable = await asyncio.to_thread(locate_executable, self._settings)
        return await asyncio.to_thread(
            inspect_executable,
            self.executable,
            self._settings,
            codesign=self._codesign,
            pin=self._pin,
        )
