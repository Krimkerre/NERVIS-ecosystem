"""Checking a Codex build RAVIS hasn't tested, so the owner can decide to use it (§3.4, §4.3).

When Homebrew brings a Codex RAVIS has no record of, new Codex work pauses (`untested_version`).
The owner can look at what changed — `GET /api/v1/codex/version-check` — and accept the build —
`POST /api/v1/codex/accept-version`. **Acceptance is not a test**, and the design is blunt about
it: an accepted build is always recorded with its file rules unproven, and stays paused until the
file-rules re-test proves them on that exact binary (review AM1, `reprove.py`).

**The seven checks.** The first six must all pass before a build can be accepted; the seventh only
says whether the file rules' surface still looks the same (design §4.3):

| # | check | how |
|---|---|---|
| 1 | `signature` | where it came from (`vouched`) — nothing else runs if this fails |
| 2 | `sha256_installed` | the binary still has the sha256 the startup check read |
| 3 | `version_parse` | `codex --version` prints `codex-cli <version>` |
| 4 | `schema_generation` | both schema trees generate, 30 s each |
| 5 | `used_methods_present` | every method the pin lists is in both combined bundles |
| 6 | `handshake` | a throwaway app-server answers `initialize` and `model/list` |
| 7 | `strict_rules` | (a) the surface's definitions hash as pinned; (b) the profile loads |

Check 1 is `codesign` and OpenAI's team on a Mac; on Linux, Arch's package or OpenAI's npm build,
file by file (`linux_origin.py`).

**Everything runs in a throwaway Codex home** inside `~/.local/share/ravis-codex-scratch/`, deleted
when the check ends, with `HOME` and `TMPDIR` inside it too. Check 6 starts an app-server there with
no sign-in, so there is never a second authenticated Codex process on RAVIS's real home (§4.3). It
also carries `features.plugins=false`, so it fetches no plugin catalogue.

**Check 7(b) waits for calibration.** It starts the throwaway app-server with the `clarvis_run`
profile's flags and `--strict-config` and asks `permissionProfile/list` whether the profile is
there. Those flags are fixed at calibration (`pin.py`); until then 7(b) can't run, and says so.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, TypeVar

from ravis.codex.pin import FileRulesProfile, definitions_file, read_pin, used_surface
from ravis.codex.rpc import (
    LINE_LIMIT_BYTES,
    METHOD_NOT_FOUND,
    CodexRpcError,
    CodexUnavailableError,
    Connection,
    RequestId,
)
from ravis.codex.runtime import (
    SCHEMA_TREES,
    SCRATCH_FOLDER,
    CodexRuntimeError,
    file_sha256,
    generate_schema_tree,
    parsed_version,
    remove_folder,
    run_codex,
    scratch_workdir,
    tested_builds,
    throwaway_environment,
    tree_sha256,
    vouched,
)
from ravis.codex.schema_report import (
    BundleFacts,
    DefinitionRecord,
    missing_methods,
    protocol_comparison,
    read_bundle,
    surface_changes,
)
from ravis.config import Settings, data_directory

#: The checks, in the order the design numbers them; the first six refuse acceptance.
CHECK_NAMES = (
    "signature",
    "sha256_installed",
    "version_parse",
    "schema_generation",
    "used_methods_present",
    "handshake",
    "strict_rules",
)
REFUSING_CHECKS = frozenset(CHECK_NAMES[:6])
#: The throwaway app-server's flags: no credential store but a file, no analytics, no plugins.
SCRATCH_FLAGS = (
    "-c", 'cli_auth_credentials_store="file"',
    "-c", "analytics.enabled=false",
    "-c", "features.plugins=false",
)

_Result = TypeVar("_Result")


@dataclass(frozen=True)
class HandshakeTimings:
    """The throwaway app-server's deadlines (design §4.8: 15 s for initialize and model/list)."""

    initialize_seconds: float = 15.0
    call_seconds: float = 15.0
    exit_seconds: float = 3.0


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str | None = None

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"name": self.name, "ok": self.ok}
        if self.detail is not None:
            body["detail"] = self.detail
        return body


@dataclass(frozen=True)
class VersionCheck:
    """One version check's findings about one binary."""

    sha256: str | None
    version: str | None
    stable_tree: str | None
    experimental_tree: str | None
    checks: tuple[Check, ...]
    protocol: dict[str, Any]
    checked_at: str

    @property
    def acceptable(self) -> bool:
        """Checks 1-6 all passed. Check 7 never refuses acceptance."""
        return all(check.ok for check in self.checks if check.name in REFUSING_CHECKS)

    def body(self, verdict: str | None, strict_rules: str | None) -> dict[str, Any]:
        """`GET /api/v1/codex/version-check`'s body, in `codex-admin.json`'s shape."""
        return {
            "version_check": {
                "sha256": self.sha256,
                "version": self.version,
                "verdict": verdict,
                "strict_rules": strict_rules,
                "checks": [check.body() for check in self.checks],
                "protocol": self.protocol,
                "checked_at": self.checked_at,
            }
        }


@dataclass(frozen=True)
class _Build:
    """What the throwaway runs read: the version and both trees, each with its own check."""

    version: str | None
    version_check: Check
    trees: dict[str, str]
    bundles: dict[str, BundleFacts]
    schema_check: Check


async def check_version(
    target: Path,
    settings: Settings,
    *,
    installed_sha256: str | None,
    codesign: str,
    pin: Traversable,
    profile: FileRulesProfile | None,
    timings: HandshakeTimings = HandshakeTimings(),
) -> VersionCheck:
    """Run the seven checks on `target`, the file the startup check found. Never raises."""
    signature = await asyncio.to_thread(_signature_check, target, settings, codesign)
    if not signature.ok:
        return _unrun(signature, installed_sha256)
    sha256 = await asyncio.to_thread(file_sha256, target)
    same = Check(
        "sha256_installed",
        sha256 == installed_sha256,
        None if sha256 == installed_sha256 else "the binary changed since RAVIS last checked it",
    )
    workdir = scratch_workdir(data_directory() / SCRATCH_FOLDER)
    try:
        environment = throwaway_environment(workdir)
        build = await asyncio.to_thread(_generate, target, workdir, environment)
        handshake = await _handshake(target, environment, workdir, timings)
        pinned, record = _pinned_record(pin)
        strict = await _strict_rules(target, environment, workdir, build, record, profile, timings)
    finally:
        remove_folder(workdir)
    checks = (signature, same, build.version_check, build.schema_check,
              _used_methods(build), handshake, strict)
    return VersionCheck(
        sha256=sha256,
        version=build.version,
        stable_tree=build.trees.get("stable"),
        experimental_tree=build.trees.get("experimental"),
        checks=checks,
        protocol=_protocol(build, pinned, record),
        checked_at=_now(),
    )


def _signature_check(target: Path, settings: Settings, codesign: str) -> Check:
    try:
        vouched(target, settings, codesign)
    except CodexRuntimeError as refusal:
        return Check("signature", False, refusal.reason)
    return Check("signature", True)


def _unrun(signature: Check, installed_sha256: str | None) -> VersionCheck:
    """A binary OpenAI didn't sign is never run, so checks 2-7 are reported as not run."""
    skipped = tuple(
        Check(name, False, "not run: the signature check failed") for name in CHECK_NAMES[1:]
    )
    return VersionCheck(
        sha256=installed_sha256,
        version=None,
        stable_tree=None,
        experimental_tree=None,
        checks=(signature, *skipped),
        protocol=_empty_protocol(),
        checked_at=_now(),
    )


def _generate(target: Path, workdir: Path, environment: dict[str, str]) -> _Build:
    """Checks 3 and 4, in the throwaway home: the version, and both schema trees read."""
    try:
        version: str | None = parsed_version(
            run_codex(target, ["--version"], environment, workdir).stdout
        )
        version_check = Check("version_parse", True)
    except CodexRuntimeError as refusal:
        version, version_check = None, Check("version_parse", False, refusal.reason)
    trees: dict[str, str] = {}
    bundles: dict[str, BundleFacts] = {}
    try:
        for name, flags in SCHEMA_TREES:
            folder = generate_schema_tree(target, workdir / name, flags, environment)
            trees[name], bundles[name] = tree_sha256(folder), read_bundle(folder)
    except CodexRuntimeError as refusal:
        failed = Check("schema_generation", False, refusal.reason)
        return _Build(version, version_check, {}, {}, failed)
    except (OSError, ValueError) as unreadable:
        detail = f"a generated schema can't be read: {unreadable}"
        return _Build(version, version_check, {}, {}, Check("schema_generation", False, detail))
    return _Build(version, version_check, trees, bundles, Check("schema_generation", True))


def _used_methods(build: _Build) -> Check:
    """Check 5: every used method in both bundles. The surface's own methods are check 7's."""
    if len(build.bundles) < len(SCHEMA_TREES):
        return Check("used_methods_present", False, "not run: the schema trees didn't generate")
    used = used_surface(read_pin())
    missing = sorted(
        {method for bundle in build.bundles.values() for method in missing_methods(bundle, used)}
    )
    if missing:
        return Check("used_methods_present", False, "missing: " + ", ".join(missing))
    return Check("used_methods_present", True)


def _pinned_record(pin: Traversable) -> tuple[dict[str, Any] | None, DefinitionRecord | None]:
    """The most recent tested entry, and its definition hashes when it has them."""
    try:
        tested = tested_builds(pin)
    except CodexRuntimeError:
        return None, None
    if not tested:
        return None, None
    entry = tested[-1]
    return entry, DefinitionRecord.from_json(definitions_file(entry, pin))


def _protocol(
    build: _Build, pinned: dict[str, Any] | None, record: DefinitionRecord | None
) -> dict[str, Any]:
    """The report's `protocol` block: what changed against the pinned build."""
    experimental = build.bundles.get("experimental")
    if experimental is None or pinned is None:
        return _empty_protocol()
    return {
        "stable_tree_changed": build.trees.get("stable") != pinned.get("stable_tree"),
        "experimental_tree_changed": build.trees.get("experimental")
        != pinned.get("experimental_tree"),
        **protocol_comparison(record, experimental, used_surface(read_pin())),
    }


def _empty_protocol() -> dict[str, Any]:
    return {
        "stable_tree_changed": None,
        "experimental_tree_changed": None,
        "strict_rules_surface_changed": None,
        "used_methods_missing": [],
        "used_definitions_changed": None,
        "other_definitions_changed": None,
    }


async def _handshake(
    target: Path, environment: dict[str, str], workdir: Path, timings: HandshakeTimings
) -> Check:
    """Check 6: a throwaway app-server, signed in to nothing, answers initialize and model/list."""

    async def list_models(connection: Connection) -> None:
        await connection.request("model/list", {"limit": 1}, timeout=timings.call_seconds)

    try:
        await _scratch_session(target, environment, workdir, (), list_models, timings)
    except (CodexRpcError, CodexUnavailableError, OSError) as failure:
        return Check("handshake", False, f"the throwaway app-server didn't answer: {failure}")
    return Check("handshake", True, "initialize and model/list answered on a scratch home")


async def _strict_rules(
    target: Path,
    environment: dict[str, str],
    workdir: Path,
    build: _Build,
    record: DefinitionRecord | None,
    profile: FileRulesProfile | None,
    timings: HandshakeTimings,
) -> Check:
    """Check 7: (a) the file rules' surface is unchanged; (b) the calibrated profile loads."""
    experimental = build.bundles.get("experimental")
    if experimental is None or record is None:
        return Check("strict_rules", False, "no pinned definitions to compare the surface with")
    changed = surface_changes(record, experimental, used_surface(read_pin()))
    if changed:
        return Check("strict_rules", False, ", ".join(changed) + " changed")
    if profile is None:
        return Check(
            "strict_rules",
            False,
            "the surface is unchanged; whether the clarvis_run profile loads can't be checked "
            "until calibration fixes its flags",
        )
    return await _profile_loads(target, environment, workdir, profile, timings)


async def _profile_loads(
    target: Path,
    environment: dict[str, str],
    workdir: Path,
    profile: FileRulesProfile,
    timings: HandshakeTimings,
) -> Check:
    async def profiles(connection: Connection) -> Any:
        return await connection.request("permissionProfile/list", {}, timeout=timings.call_seconds)

    flags = (*profile.flags, "--strict-config")
    try:
        listed = await _scratch_session(target, environment, workdir, flags, profiles, timings)
    except (CodexRpcError, CodexUnavailableError, OSError) as failure:
        return Check("strict_rules", False, f"the {profile.name} profile didn't load: {failure}")
    names = {
        entry.get("name") or entry.get("id")
        for entry in (listed.get("data") if isinstance(listed, dict) else None) or []
        if isinstance(entry, dict)
    }
    if profile.name not in names:
        return Check("strict_rules", False, f"Codex doesn't list the {profile.name} profile")
    return Check("strict_rules", True, f"the surface is unchanged and {profile.name} loads")


async def _scratch_session(
    target: Path,
    environment: dict[str, str],
    workdir: Path,
    extra_flags: tuple[str, ...],
    calls: Callable[[Connection], Awaitable[_Result]],
    timings: HandshakeTimings,
) -> _Result:
    """Start a throwaway app-server, initialize it, make `calls`, and end it however they went."""
    process = await asyncio.create_subprocess_exec(
        str(target), "app-server", "--listen", "stdio://", *SCRATCH_FLAGS, *extra_flags,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=environment,
        cwd=str(workdir),
        start_new_session=True,
        limit=LINE_LIMIT_BYTES,
    )
    connection = Connection(
        process.stdout,  # type: ignore[arg-type]
        process.stdin,  # type: ignore[arg-type]
        on_notification=_ignored,
        on_request=_refused,
    )
    connection.start()
    introduction = {
        "clientInfo": {"name": "ravis", "version": "check"},
        "capabilities": {"experimentalApi": True},
    }
    try:
        await connection.request("initialize", introduction, timeout=timings.initialize_seconds)
        connection.notify("initialized")
        return await calls(connection)
    finally:
        connection.close_input()
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timings.exit_seconds):
                await process.wait()
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        await connection.stop("the throwaway app-server was ended")


def _ignored(_method: str, _params: dict[str, Any]) -> None:
    """The throwaway app-server's notifications mean nothing to a version check."""


def _refused(
    connection: Connection, request_id: RequestId, method: str, _params: dict[str, Any]
) -> None:
    connection.refuse(request_id, METHOD_NOT_FOUND, f"a version check answers no {method}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
