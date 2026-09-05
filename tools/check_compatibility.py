#!/usr/bin/env python3
"""The compatibility matrix §12 asks to be published, and kept true.

**§12: "Publish compatibility matrices and minimum/maximum peer versions."**
There were none. Four products shipping on independent cadences, every peer's
`build_version` read and displayed, and no statement anywhere about which
combinations were meant to work together.

`nervis/src/nervis/compatibility.py` declares the windows and NERVIS answers them
on every `/api/v1/services` row. This prints the matrix and holds it to two
facts it cannot check about itself:

1. **Every peer ships inside its own window.** A maximum that falls behind what a
   peer actually ships means the ecosystem is describing last month's pairing —
   and the version that would notice is exactly the one nobody re-reads.
2. **§12's rolling-upgrade clause survives the arithmetic.** *"NERVIS must
   tolerate peers one supported minor behind"*: a window narrower than a minor
   cannot contain the upgrade it exists to permit, whatever the intention was
   when it was written.

**The matrix is printed rather than filed.** A table in a document is a second
copy of the windows, and the copy goes stale — which is the defect this whole
file is about. `RELEASES.md` points here instead.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervis" / "src"))

from nervis.compatibility import (
    CANNOT_BE_JUDGED,
    SUPPORTED_PEERS,
    supported,
)

#: Where each peer states the version it ships. Read from the manifest rather
#: than from a table here, for the reason the whole file exists.
MANIFESTS = {
    "ravis": ROOT / "ravis" / "pyproject.toml",
    "sirvis": ROOT / "sirvis" / "pyproject.toml",
    "clarvis": ROOT.parent / "clarvis" / "package.json",
}


def shipped(peer: str) -> str:
    manifest = MANIFESTS.get(peer)
    if manifest is None or not manifest.is_file():
        return ""
    if manifest.suffix == ".json":
        return str(json.loads(manifest.read_text()).get("version", ""))
    found = re.search(r'^version\s*=\s*"([^"]+)"', manifest.read_text(), re.MULTILINE)
    return found.group(1) if found else ""


def main() -> int:
    failures: list[str] = []
    print("What this NERVIS supports its peers at (§12):\n")
    print(f"  {'peer':<10} {'supported range':<22} {'ships':<10} verdict")

    for peer in sorted(SUPPORTED_PEERS):
        window = SUPPORTED_PEERS[peer]
        version = shipped(peer)
        answer = supported(peer, version)
        span = f"{window.minimum} … {window.maximum}"
        print(f"  {peer:<10} {span:<22} {version or '—':<10} "
              f"{'inside' if answer.supported else 'OUTSIDE'}")
        if version and not answer.supported:
            failures.append(
                f"{peer} ships {version} and this NERVIS declares {span} — the ecosystem is "
                f"describing a pairing it does not ship ({answer.reason})")
        if not version:
            failures.append(f"{peer} declares a window and no manifest states what it ships")
        low = tuple(int(part) for part in window.minimum.split("."))
        high = tuple(int(part) for part in window.maximum.split("."))
        if low[0] != high[0] or high[1] - low[1] < 1:
            failures.append(
                f"{peer}'s window {span} is under a minor wide; §12 requires tolerating a "
                f"peer one supported minor behind, and this cannot contain one")

    for peer, why in sorted(CANNOT_BE_JUDGED.items()):
        print(f"  {peer:<10} {'—':<22} {shipped(peer) or '—':<10} not judged: {why}")

    if failures:
        print("\nthe declared windows and the shipped versions disagree:\n")
        for failure in failures:
            print(f"  • {failure}")
        return 1
    print("\nEvery peer ships inside the window NERVIS declares for it, and every window is "
          "at least a minor wide.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
