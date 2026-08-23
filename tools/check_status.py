#!/usr/bin/env python3
"""Fail the build when STATUS.md has drifted from the repository.

STATUS.md is the file a cold session reads to learn where the build is. That
makes it the most damaging file to be wrong: a stale specification misleads
someone who is checking, but a stale status file misleads someone who is not.

The gate is deliberately narrow. It checks only claims that are **falsifiable**
— numbers and paths — rather than trying to judge whether the prose is current.
The design bet is that this is enough, because of one observation:

    **Finishing a milestone always adds tests.**

So an asserted test count is a proxy for "was this file updated when the last
milestone landed?" that costs nothing and produces no false positives on a typo
fix, a comment, or a refactor that leaves the suite the same size. A build that
fails here has almost always added capability without saying so.

What this deliberately does *not* do is require STATUS.md to change whenever
`ravis/src` does. That rule fires on every comment edit, and a gate people learn
to satisfy mechanically is worse than no gate — the whole point is that the file
stays *true*, not that it stays *touched*.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / "STATUS.md"
RAVIS = ROOT / "ravis"

# Every package with a suite. Counted together because STATUS.md states one
# number for the repository, and a gate that counted only the first package
# would go quiet the moment work moved to another one — which is exactly what
# happened when the protocol package and SIRVIS arrived.
PACKAGES = ("protocol", "ravis", "sirvis")


def _python() -> str:
    """The interpreter that has RAVIS installed.

    A local checkout keeps it in `ravis/.venv`; CI installs into the runner's
    own environment and puts the console scripts on PATH. Preferring the venv
    when it exists makes the same command work in both without a flag.
    """
    venv = RAVIS / ".venv/bin/python"
    return str(venv) if venv.exists() else sys.executable


def _ravis_cli() -> str:
    venv = RAVIS / ".venv/bin/ravis"
    return str(venv) if venv.exists() else "ravis"


def counted_tests() -> int:
    """How many tests this repository contains, across every package.

    Returns -1 when any package fails to report, rather than a partial sum: a
    number that is quietly missing a suite is worse than an obvious failure,
    because STATUS.md would then be "corrected" to the wrong figure.
    """
    total = 0
    for package in PACKAGES:
        counted = _counted_in(ROOT / package)
        if counted < 0:
            return -1
        total += counted
    return total


def _counted_in(package: Path) -> int:
    """Collect one package's suite, or -1 when it cannot be counted."""
    if not (package / "tests").is_dir():
        return 0
    result = subprocess.run(
        [_python(), "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=package, capture_output=True, text=True, check=False,
    )
    # `-q` is in addopts, so pytest prints a per-file tally rather than a
    # total. Summing those is more robust than fighting the quiet flag, and it
    # fails loudly if the format changes rather than silently passing.
    per_file = re.findall(r"^\S+: (\d+)$", result.stdout, re.M)
    return sum(int(count) for count in per_file) if per_file else -1


def counted_conformance_checks() -> int:
    """How many checks the Clarvis suite actually runs."""
    result = subprocess.run(
        [_ravis_cli(), "conformance", "clarvis"],
        cwd=RAVIS, capture_output=True, text=True, check=False,
    )
    return result.stdout.count("[PASS]") + result.stdout.count("[FAIL]")


def claimed(pattern: str, text: str) -> int:
    """The number STATUS.md asserts, or -1 when it asserts none."""
    match = re.search(pattern, text)
    return int(match.group(1)) if match else -1


def check_numbers(text: str, failures: list[str]) -> None:
    """Compare every number STATUS.md asserts against the real one."""
    for label, pattern, actual in (
        ("test count", r"(\d+) tests?, no network", counted_tests()),
        ("test count (verify block)", r"(\d+) passing", counted_tests()),
        ("conformance checks", r"release gate — (\d+) checks", counted_conformance_checks()),
    ):
        stated = claimed(pattern, text)
        if stated == -1:
            failures.append(f"STATUS.md no longer states the {label} — the claim was removed")
        elif stated != actual:
            failures.append(
                f"STATUS.md says {stated} for {label}; the repository has {actual}. "
                "Something landed without updating it."
            )


# Paths belonging to a sibling repository. Clarvis is checked out separately —
# CI clones only this repository — so these cannot be verified from here and are
# out of scope rather than broken. Stated explicitly so the exemption reads as a
# decision: a typo inside one of these would go uncaught, which is the price of
# not failing every CI run on a directory that is legitimately absent.
SIBLING_REPOSITORIES = ("clarvis/",)


def check_referenced_paths(text: str, failures: list[str]) -> None:
    """Every path STATUS.md names *within this repository* must exist.

    Catches the other common drift: a file renamed or moved while the status
    file still sends a reader to where it used to be.
    """
    for path in sorted(set(re.findall(r"`([\w./-]+\.(?:md|py|toml|yml))`", text))):
        if path.startswith(("../", "http")) or path.startswith(SIBLING_REPOSITORIES):
            continue
        candidates = [ROOT / path, *(ROOT / pkg / path for pkg in PACKAGES),
                      RAVIS / "src/ravis" / path]
        if not any(candidate.exists() for candidate in candidates):
            failures.append(f"STATUS.md references `{path}`, which does not exist")


def check_next_milestone_is_not_already_done(text: str, failures: list[str]) -> None:
    """A milestone cannot be both finished and next.

    The single most misleading state this file can reach, and the cheapest to
    detect: the Done table and the Next table must not name the same milestone.
    """
    done_section = text.split("### Next")[0]
    next_section = text.split("### Next")[1].split("### After that")[0] if "### Next" in text else ""
    done = set(re.findall(r"\*\*(M\d+[ab]?)\*\*", done_section))
    upcoming = set(re.findall(r"\*\*(M\d+[ab]?)\*\*", next_section))
    for milestone in sorted(done & upcoming):
        failures.append(f"{milestone} is listed as both done and next")


def main() -> int:
    if not STATUS.exists():
        print("STATUS.md is missing — it is the file a cold session reads first", file=sys.stderr)
        return 1
    text = STATUS.read_text()
    failures: list[str] = []
    check_numbers(text, failures)
    check_referenced_paths(text, failures)
    check_next_milestone_is_not_already_done(text, failures)

    if failures:
        print("STATUS.md has drifted:\n", file=sys.stderr)
        for failure in failures:
            print(f"  • {failure}", file=sys.stderr)
        print(
            "\nUpdating STATUS.md is part of finishing a milestone. A status file "
            "that drifts is worse than none, because it is believed.",
            file=sys.stderr,
        )
        return 1
    print("STATUS.md matches the repository")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
