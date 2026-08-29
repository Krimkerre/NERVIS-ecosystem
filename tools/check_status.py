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
# happened when the protocol package and SIRVIS arrived, and again at NERVIS M0.
PACKAGES = ("protocol", "ravis", "sirvis", "nervis")


def _python() -> str:
    """The interpreter that has every package installed.

    A local checkout keeps one venv in `ravis/.venv` and installs the siblings
    into it; CI installs all three into the runner's own environment. Preferring
    the venv when it exists makes the same command work in both without a flag.

    Whichever it is, it must have *all* of `PACKAGES` importable — collecting a
    suite whose package is missing yields -1, which is a hard failure by design
    rather than a smaller number.
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
    per_file = re.findall(r"^\S+: (\d+)$", result.stdout, re.MULTILINE)
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


def check_package_counts(text: str, failures: list[str]) -> None:
    """Each package's own asserted count, not just the total.

    **The total hid three wrong numbers.** The verify block names a count per
    package — `cd sirvis && … # 407 tests` — and nothing checked them, so an
    editing session that kept the *total* right while moving numbers between
    lines passed this gate with protocol, sirvis and nervis all misreported. A
    reader running the command in front of them would have been the one to find
    it, which is the failure mode this whole file exists to prevent.
    """
    for package, stated in re.findall(r"cd (\w+)\s+&&[^#\n]*#\s*(\d+) tests", text):
        actual = _counted_in(ROOT / package)
        if actual < 0:
            failures.append(f"STATUS.md names a test count for {package}, which cannot be collected")
        elif int(stated) != actual:
            failures.append(
                f"STATUS.md says {stated} tests for {package}; it has {actual}."
            )


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


# Paths belonging to a sibling repository. Clarvis and its first-run benchmark
# apparatus are checked out separately — CI clones only this repository — so
# these cannot be verified from here and are out of scope rather than broken. Stated explicitly so the exemption reads as a
# decision: a typo inside one of these would go uncaught, which is the price of
# not failing every CI run on a directory that is legitimately absent.
SIBLING_REPOSITORIES = ("clarvis/", "clarvis-firstrun/")


def check_referenced_paths(text: str, failures: list[str]) -> None:
    """Every path STATUS.md names *within this repository* must exist.

    Catches the other common drift: a file renamed or moved while the status
    file still sends a reader to where it used to be.
    """
    for path in sorted(set(re.findall(r"`([\w./-]+\.(?:md|py|toml|yml))`", text))):
        if path.startswith(("../", "http")) or path.startswith(SIBLING_REPOSITORIES):
            continue
        # A bare module name resolves inside *any* package's source tree, not
        # just RAVIS's. It was `RAVIS / "src/ravis"` alone, which meant a
        # NERVIS module named in STATUS.md failed this check for existing in
        # the wrong package — a false alarm on the gate that is supposed to
        # catch real drift, and the fastest way to teach somebody to ignore it.
        candidates = [
            ROOT / path,
            *(ROOT / pkg / path for pkg in PACKAGES),
            *(ROOT / pkg / "src" / pkg / path for pkg in PACKAGES),
            *(ROOT / pkg / "src" / f"ecosystem_{pkg}" / path for pkg in PACKAGES),
        ]
        if not any(candidate.exists() for candidate in candidates):
            failures.append(f"STATUS.md references `{path}`, which does not exist")


def check_next_milestone_is_not_already_done(text: str, failures: list[str]) -> None:
    """A milestone cannot be both finished and next.

    The single most misleading state this file can reach, and the cheapest to
    detect: the Done table and the Next table must not name the same milestone.
    """
    done_section = text.split("### Next")[0]
    next_section = text.split("### Next")[1].split("### After that")[0] if "### Next" in text else ""
    done = set(_milestones(done_section))
    upcoming = set(_milestones(next_section))
    for milestone in sorted(_collisions(done, upcoming)):
        # A milestone deliberately split across stages is named as split on at
        # least one side — "**M14** *(observation half)*" in Done, "**The rest
        # of M14**" in Next. That is a disclosed decision rather than a
        # contradiction, and failing on it would train whoever hits it to
        # weaken the check. An *undisclosed* repeat still fails.
        if _is_declared_split(milestone, done_section, next_section):
            continue
        failures.append(f"{milestone} is listed as both done and next")


def _collisions(done: set[str], upcoming: set[str]) -> set[str]:
    """The milestones named on both sides, tolerating one written bare.

    A plain set intersection misses the case this file's own docstring warns
    about — Done writing "RAVIS M8" while Next writes "M8" — and the previous
    pattern only ever passed that self-test by accident, because its phantom
    spans happened to also emit a bare id from the prose nearby. Removing the
    phantom removed the accident and exposed the real gap.

    So a bare `Mn` matches a `SERVICE Mn`, while two *different* services' Mn
    never match each other: SIRVIS M10 and RAVIS M10 are genuinely different
    milestones, which is why the service prefix is kept in the first place.
    A bare id that matches something done is worth reporting even when the
    author meant another service's — the ambiguity is the problem.
    """
    found = set()
    for name in upcoming:
        for other in done:
            if name == other or _same_number(name, other):
                found.add(name)
    return found


def _same_number(one: str, other: str) -> bool:
    """Whether these name the same milestone with only one side qualified."""
    bare_one, bare_other = one.split()[-1], other.split()[-1]
    if bare_one != bare_other:
        return False
    return " " not in one or " " not in other


# Words that mark a milestone as knowingly split rather than accidentally
# repeated. Deliberately short: anything longer becomes a way to silence the
# check by phrasing.
SPLIT_MARKERS = ("rest of", "half", "remaining")

# The milestone's own name, plus any italic qualifier immediately after it —
# "**The rest of M14**", "**M14** *(observation half)*". The marker has to live
# *there* and not merely somewhere in the row: an ordinary description like
# "the remaining native adapters" contains one of these words and would
# otherwise excuse the repeat it sits next to. That is not hypothetical; it is
# what this check did on its first working draft.
_NAMED = re.compile(r"\*\*([^*]*?\b%s\b[^*]*?)\*\*(\s*\*\([^)]*\)\*)?")


def _is_declared_split(milestone: str, done_section: str, next_section: str) -> bool:
    """Whether *every* row in Next that names this milestone declares the split.

    **Row-wise, not section-wide, and the difference is the whole check.** This
    scanned both sections and returned True on the first marker it found
    anywhere — so once one row legitimately declared a split, every other repeat
    of that milestone was excused, including an undisclosed one. The self-test
    caught it the moment a real split was declared for M16: it injects a bare
    `**M16**` into Next and expects a failure, and got none, because a genuine
    "remaining half of RAVIS M16" two rows below was answering for it.

    The Next row is the one claiming work remains, so it is the row that has to
    say which part. A marker on the Done side alone excuses nothing.
    """
    del done_section  # The claim being checked is made in Next.
    bare = re.escape(milestone.split()[-1])
    pattern = re.compile(_NAMED.pattern % bare)
    naming = [
        line for line in next_section.splitlines()
        if line.lstrip().startswith("|") and pattern.search(line)
    ]
    if not naming:
        return False
    return all(
        any(
            any(marker in (name + " " + (qualifier or "")).lower() for marker in SPLIT_MARKERS)
            for name, qualifier in pattern.findall(line)
        )
        for line in naming
    )


# A milestone id as it is actually written in this file: bolded, and almost
# always qualified — "**RAVIS M7**", "**SIRVIS M10**", "**The rest of M14**",
# "**M14** *(observation half)*". The first version of this required the bold
# span to hold a bare id and nothing else, which no row in the Next table has
# ever been written as — so the check ran on every push and could not fire, on
# real content or on a deliberate regression. STATUS.md meanwhile advertised it
# as enforced. Found by an audit, not by the gate.
# The alternation puts the qualified form first *and* the lazy prefix stops at a
# word boundary, so "**RAVIS M8**" yields "RAVIS M8" rather than a bare "M8".
# Getting that wrong is not cosmetic: Done wrote "RAVIS M8" while Next wrote
# "M8", the two sets never intersected, and the check stayed silent — the same
# failure in a new place.
# **Bold spans are paired first, then searched.** Requiring a closing `**` in one
# pattern is not enough and the previous version of this comment said it was: the
# *closing* delimiter of one span pairs with the *opening* delimiter of the next,
# forming a phantom span over the plain text between them. That is how "M11" was
# read out of the row whose bold name is "Stage 6 — NERVIS core" — its
# description mentions M11, and the phantom span reached from that row's closer
# to the following row's opener.
#
# `findall` consumes matches left to right without overlapping, so pairing the
# spans first makes a delimiter belong to exactly one of them.
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MILESTONE = re.compile(
    r"\b((?:RAVIS|SIRVIS|NERVIS)\s+M\d+[ab]?|M\d+[ab]?)\b"
)


def _milestones(section: str) -> list[str]:
    """Every milestone a section names, normalised to `SERVICE Mn` or `Mn`.

    Normalised because "**M8**" and "**RAVIS M8**" are the same milestone, and a
    check that treated them as different would miss exactly the contradiction it
    exists to catch. Service prefixes are kept where present so that SIRVIS M10
    and RAVIS M10 — genuinely different milestones — never collide.

    **Table rows only**, which is what this check has always claimed to compare:
    the Done table against the Next table. Reading bold anywhere also read bold
    *prose*, and a sentence like "**M15's acceptance, met.**" put a bare M15
    into the Done set — which then collided with "RAVIS M15" in Next and
    reported a contradiction that existed only in a comment about a different
    service's milestone.
    """
    return [
        " ".join(found.split())
        for line in section.splitlines()
        if line.lstrip().startswith("|")
        for span in _BOLD.findall(line)
        for found in _MILESTONE.findall(span)
    ]


def main() -> int:
    if not STATUS.exists():
        print("STATUS.md is missing — it is the file a cold session reads first", file=sys.stderr)
        return 1
    text = STATUS.read_text()
    failures: list[str] = self_test()
    check_numbers(text, failures)
    check_package_counts(text, failures)
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


def self_test() -> list[str]:
    """Prove the milestone check can actually fail.

    This exists because the check could not. It ran on every push for months
    with a regex that no row in the Next table has ever matched, so it passed
    unconditionally while STATUS.md advertised it as enforced — a gate nobody
    tested, guarding a file everybody believes.

    Positive cases matter more than the negative one here: a checker that only
    ever sees passing input is indistinguishable from a checker that cannot
    fail.
    """
    text = STATUS.read_text(encoding="utf-8")
    problems: list[str] = []

    clean: list[str] = []
    check_next_milestone_is_not_already_done(text, clean)
    if clean:
        problems.append(f"self-test: real STATUS.md should be clean, got {clean}")

    # A milestone written into Next that the Done table already claims, in the
    # table's own convention and in the bare form.
    # Anchored on *whichever* milestone is first in Next rather than on its
    # name. The name was hard-coded, which made finishing that milestone break
    # the self-test — so the gate that exists to catch a half-updated STATUS.md
    # had to be edited by hand every time STATUS.md was correctly updated.
    for injected in ("**RAVIS M8**", "**M16**"):
        head, _, tail = text.partition("### Next")
        # Scoped to the Next section: the Done table has a row 1 too, and
        # rewriting *that* one injects the milestone into the wrong table, so
        # the checker sees nothing wrong and the self-test silently passes
        # nothing. Which is how this was found.
        row = head + "### Next" + re.sub(
            r"\| 1 \| \*\*[^|*]+\*\*", f"| 1 | {injected}", tail, count=1
        )
        caught: list[str] = []
        check_next_milestone_is_not_already_done(row, caught)
        if not caught:
            problems.append(f"self-test: {injected} in Next was not caught")

    # A milestone named only in a row's *description* is not a claim that it is
    # next, and must not be read as one. This is the phantom-span case above,
    # written as a fixture so the pairing cannot regress into the old pattern.
    prose_only = (
        "### Done\n| 1 | **M11** | done |\n"
        "### Next\n| 1 | **Stage 6** | M11 + M15 |\n| 2 | **M99** | later |\n"
        "### After that\n"
    )
    misread: list[str] = []
    check_next_milestone_is_not_already_done(prose_only, misread)
    if misread:
        problems.append(f"self-test: a milestone named only in prose was read as next: {misread}")

    # Bold prose is commentary, not a claim that a milestone is done. The Done
    # section is full of it, and reading it as a declaration invents collisions
    # between services that share a milestone number.
    prose_bold = (
        "### Done\n| 1 | **SIRVIS M15** | done |\nSome note: **M15's acceptance, met.**\n"
        "### Next\n| 1 | **RAVIS M15** | later |\n### After that\n"
    )
    invented: list[str] = []
    check_next_milestone_is_not_already_done(prose_bold, invented)
    if invented:
        problems.append(f"self-test: bold prose was read as a milestone claim: {invented}")

    # A declared split must not be reported: it is a recorded decision. Both
    # halves are exercised against a written-out example rather than against
    # whatever STATUS.md happens to contain — the live document was the fixture
    # until M14 finished and left the Next table, at which point the test
    # asserted something no longer present and failed for the one reason a gate
    # must not: the project moving forward correctly.
    split_done = "### Done\n| 1 | **M14** *(observation half)* | done |\n"
    if not _is_declared_split(
        "M14", split_done, "### Next\n| 1 | **The rest of M14** | later |\n"
    ):
        problems.append("self-test: a declared split should be tolerated")
    if _is_declared_split("M14", split_done, "### Next\n| 1 | **M14** | later |\n"):
        problems.append("self-test: an undisclosed repeat should not be tolerated")
    return problems


if __name__ == "__main__":
    raise SystemExit(main())
