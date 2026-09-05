#!/usr/bin/env python3
"""Every shipped version has release notes (§15).

**Five components carried version numbers that nothing explained.** §15 asks that
"compatibility matrix, operator runbook, release notes and known limitations are
published", and there were none — at any version, for any of them. `ravis 0.21.2`
told an operator that something had changed twice since `0.21.0` and nothing
about what.

**The versions are read from each component's own manifest, never from a list
here.** That is the same rule `check_degradation.py` follows about §10 and
`check_status.py` follows about test counts: a second copy of a fact is a copy
that goes stale, and the copy is always the one nobody re-reads. A version bump
with no note fails this gate at the moment it is made, which is the only moment
the change is still in somebody's head.

**Only the current version is required.** `RELEASES.md` starts where it starts —
versions before it shipped without notes, and reconstructing them from commit
messages would produce a document that reads like a record and is a guess. The
history that exists is `STATUS.md` and the git log. Requiring every past version
would force exactly the invention §14.6 was written about; requiring the current
one makes the record grow forward and lets no new gap open.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NOTES = ROOT / "RELEASES.md"
CLARVIS = ROOT.parent / "clarvis"

#: Where each component states the version it ships, and the heading it uses
#: here. The heading is checked as a whole so a note filed under the wrong
#: component cannot satisfy another's requirement.
COMPONENTS = (
    ("Clarvis", CLARVIS / "package.json"),
    ("NERVIS", ROOT / "nervis" / "pyproject.toml"),
    ("RAVIS", ROOT / "ravis" / "pyproject.toml"),
    ("SIRVIS", ROOT / "sirvis" / "pyproject.toml"),
    ("ecosystem-protocol", ROOT / "protocol" / "pyproject.toml"),
)


def shipped(manifest: pathlib.Path) -> str:
    """The version this component declares, from the file that declares it."""
    if not manifest.is_file():
        return ""
    if manifest.suffix == ".json":
        return str(json.loads(manifest.read_text()).get("version", ""))
    found = re.search(r'^version\s*=\s*"([^"]+)"', manifest.read_text(), re.MULTILINE)
    return found.group(1) if found else ""


def main() -> int:
    if not NOTES.is_file():
        print(f"{NOTES.name} does not exist; §15 asks for published release notes")
        return 1

    text = NOTES.read_text(encoding="utf-8")
    failures: list[str] = []
    described: list[str] = []

    for name, manifest in COMPONENTS:
        version = shipped(manifest)
        if not version:
            failures.append(f"{name} declares no version in {manifest.name}")
            continue
        # The component's own top heading, or a later `### <version>` under it.
        heading = f"## {name} — {version}"
        earlier = f"### {version}"
        if heading in text:
            described.append(f"{name} {version}")
        elif earlier in text:
            failures.append(
                f"{name} {version} is filed as a past release (`{earlier}`) while it is the "
                f"version being shipped; the current one belongs in the component's heading")
        else:
            failures.append(
                f"{name} ships {version} and {NOTES.name} has no `{heading}` — a version "
                f"number nothing explains is what this gate exists to prevent")

    # A note for a version nobody ships is the mirror defect: usually a typo, and
    # always a claim about something that does not exist.
    for name, manifest in COMPONENTS:
        version = shipped(manifest)
        for claimed in re.findall(rf"^## {re.escape(name)} — (\S+)$", text, re.MULTILINE):
            if version and claimed != version:
                failures.append(
                    f"{NOTES.name} heads {name}'s notes with {claimed}, and {name} ships "
                    f"{version}")

    if failures:
        print("the release notes and the shipped versions disagree:\n")
        for failure in failures:
            print(f"  • {failure}")
        return 1
    print(f"{len(described)} components ship a version with notes: {', '.join(described)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
