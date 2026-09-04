#!/usr/bin/env python3
"""Fail the build when a specification's milestone table has gone malformed.

Each of the three specifications ends in a milestone table and a stage-mapping
table, and together they are the answer to "what is left to build". STATUS.md
derives that answer by reading them, so a defect here is not cosmetic — it
propagates into the file a cold session trusts.

Two defects, both observed, neither caught by anything:

**A row with the wrong number of columns.** A commit adding NERVIS's M26 lost
the newline before it, concatenating three milestone rows into one line of nine
cells. Markdown renders the first three and silently drops the rest, so M26 had
no visible row for a week and part of M25's acceptance was invisible with it.
Nothing looked wrong, because the table still rendered — that is precisely why
this needs a machine to check it.

**A milestone assigned to no stage.** SIRVIS's mapping table carries the line
*"listed so no milestone is silently unassigned"*, which is a promise the file
makes about itself and could not keep: M22b had shipped and appeared in neither
a stage row nor the deferred row. A promise nothing verifies is a comment.

What this deliberately does not check is whether a tick is *deserved* — that
needs the code, and `check_status.py` covers the version-and-tests side. This
gate only asks that the tables be readable and complete, which is the part a
human eye reliably misses.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS = ("NERVIS.md", "RAVIS.md", "SIRVIS.md")

# A milestone row opens with a bolded identifier: `| **M12** ✅ | ...`. The
# suffix allows `M5a`, `M15b`, `M22b` and friends, which are real milestones and
# not typos.
ROW = re.compile(r"^\|\s*\*\*(M\d+[a-z]?)\*\*")
MAPPING_HEADING = re.compile(r"^##\s+\d+\.\d+\s+Ecosystem gate mapping")

# §14.8's four states, and the binary tick they replace.
#
# **A ratchet, not a sweep.** 64 rows still carry `✅`, and converting them all
# would mean asserting a state for each — that a milestone is merely
# IMPLEMENTED, or AUTOMATED VERIFIED, or actually LIVE VERIFIED. §16 item 11 is
# explicit that those may be applied "only to rows independently reverified as
# wrong, not the audit's list at face value", and §14.6 records what happens
# here when a number is copied rather than run. Relabelling 64 milestones from a
# desk would be exactly that, at scale.
#
# So the count may fall and may not rise, the same shape as the complexity and
# liveness ratchets. A row touched for any other reason gets its real state on
# the way past, and the ceiling comes down with it.
STATES = ("IMPLEMENTED", "AUTOMATED VERIFIED", "LIVE VERIFIED", "BLOCKED")
LEGACY_TICK = re.compile(r"^\|\s*\*\*M\d+[a-z]?\*\*\s*✅")
LEGACY_CEILING = 64


def _milestone_table(lines: list[str]) -> list[tuple[int, str]]:
    """Every milestone row, as (line number, raw line)."""
    return [(n, line) for n, line in enumerate(lines, 1) if ROW.match(line)]


def _columns(line: str) -> int:
    """Cells in a pipe table row.

    A trailing pipe is conventional here, so a well-formed three-column row
    reads as four pipes and the count is pipes minus one.
    """
    return line.rstrip().count("|") - 1


def _mapping_text(lines: list[str]) -> str:
    """Everything under the gate-mapping heading, up to the next heading."""
    out: list[str] = []
    inside = False
    for line in lines:
        if MAPPING_HEADING.match(line):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside:
            out.append(line)
    return "\n".join(out)


def _shape_faults(spec: str, rows: list[tuple[int, str]]) -> list[str]:
    """Rows whose column count is not the table's own."""
    if not rows:
        return [f"{spec}: no milestone table found at all"]
    widths = [_columns(line) for _, line in rows]
    expected = max(set(widths), key=widths.count)
    return [
        f"{spec}:{n} — {_columns(line)} columns where the table has {expected}. "
        f"Markdown drops the extra cells silently, so whatever is in them is "
        f"invisible: {line[:70].strip()}…"
        for n, line in rows
        if _columns(line) != expected
    ]


def _unassigned(spec: str, rows: list[tuple[int, str]], mapping: str) -> list[str]:
    """Milestones the stage mapping never names.

    Matched on a word boundary so `M2` does not satisfy itself by appearing
    inside `M22b`.

    A lettered half counts. RAVIS's M18 is one row describing two halves that
    were deliberately scheduled apart — M18a at Stage 3, M18b at Stage 7 — so
    the mapping names the halves and never the whole. That is the mapping being
    more precise than the milestone table, not a gap, and a gate that called it
    one would be teaching people to write `M18` somewhere to keep it quiet.
    """
    if not mapping:
        return [f"{spec}: no gate-mapping section found"]
    return [
        f"{spec}: {name} appears in no stage row and no deferred row. The mapping "
        f"exists so nothing is silently unassigned; this one is."
        for name in (ROW.match(line).group(1) for _, line in rows)  # type: ignore[union-attr]
        if not re.search(rf"\b{name}[a-z]?\b", mapping)
    ]


def _state_faults(spec: str, rows: list[tuple[int, str]]) -> tuple[list[str], int]:
    """Rows using a completion marker §14.8 does not define, and the legacy count.

    Two different things, and only one of them is a fault. A row still carrying
    `✅` is *old*, and the ratchet above says why that is tolerated. A row
    carrying some fifth word is *wrong*: §14.7 lets a product add detail beside
    the required state and not invent a softer one, and a state nobody defined
    is exactly the optimistic checkmark §14.8 replaced, respelled.
    """
    faults: list[str] = []
    legacy = 0
    for number, line in rows:
        if LEGACY_TICK.match(line):
            legacy += 1
            continue
        # The marker is whatever sits between the identifier and the first pipe.
        marker = line.split("**", 2)[-1].split("|")[0].strip()
        if not marker or marker.startswith("*("):
            continue  # unstarted, or an annotation like *(stretch)*
        if not any(marker.startswith(state) for state in STATES):
            faults.append(
                f"{spec}:{number}: {marker!r} is not one of §14.8's states "
                f"({', '.join(STATES)})"
            )
    return faults, legacy


def main() -> int:
    problems: list[str] = []
    legacy_rows = 0
    for spec in SPECS:
        path = ROOT / spec
        if not path.exists():
            problems.append(f"{spec} is missing")
            continue
        lines = path.read_text(encoding="utf-8").split("\n")
        rows = _milestone_table(lines)
        problems.extend(_shape_faults(spec, rows))
        problems.extend(_unassigned(spec, rows, _mapping_text(lines)))
        faults, legacy = _state_faults(spec, rows)
        problems.extend(faults)
        legacy_rows += legacy

    if problems:
        print("the build plans have drifted:\n")
        for problem in problems:
            print(f"  • {problem}")
        print(
            "\nSTATUS.md derives what is left to build by reading these tables, "
            "so a defect here reaches the file a cold session trusts."
        )
        return 1
    if legacy_rows > LEGACY_CEILING:
        print(f"{legacy_rows} milestone rows still carry the binary ✅, above the "
              f"ceiling of {LEGACY_CEILING}.\n")
        print("§14.8 replaced it with four states because one symbol cannot say")
        print("whether code exists, a test passed, or somebody watched it work.")
        print("Give the new row its real state; never raise this number.")
        return 1
    if legacy_rows < LEGACY_CEILING:
        print(f"{legacy_rows} rows still carry ✅ — below the ceiling of "
              f"{LEGACY_CEILING}. Lower LEGACY_CEILING in {Path(__file__).name}.")
        return 1
    total = sum(len(_milestone_table((ROOT / s).read_text(encoding="utf-8").split("\n")))
                for s in SPECS)
    print(f"{total} milestone rows across {len(SPECS)} specs: every row is the table's "
          "own width, every milestone is assigned to a stage or deferred by name, "
          f"and {legacy_rows} still carry the pre-§14.8 tick")
    return 0


if __name__ == "__main__":
    sys.exit(main())
