#!/usr/bin/env python3
"""Nothing tracked by git matches a `.gitignore` pattern meant to keep it out.

A Claude Security scan found `nervis/nervis.enrollment` — the bearer secret
`nervis/src/nervis/enrollment.py`'s `load_or_create()` mints on first run and
`api/instances.py` trusts as the sole credential gating
`POST /api/v1/registry/instances` — committed and sitting in this
repository's history (CWE-798). `*.enrollment` had already been added to
`.gitignore`, at M8a, after the same mistake happened once before — but a
`.gitignore` pattern only ever stops a *future* `git add` from tracking a
path; it has no retroactive effect on one git already tracks; the file
stayed committed regardless of the pattern naming it.

So the pattern's own presence was never proof the mistake could not recur —
it only proved someone had written down that it should not. This is the
check that makes it provable: every file `git` actually tracks, tested
against `.gitignore` as if it were untracked (`--no-index`, since ordinary
`check-ignore` does not apply exclude patterns to a path already in the
index — the same reason `.gitignore` alone could not have caught the file
this script was written to find). A hit here means a path both git tracks
and `.gitignore` says should never be tracked — a contradiction between the
committed state and the committed rule about it, on any file, secret or not.

This does not remove a secret already sitting in history, and does not
rotate one — a scan does not decide to rewrite published history or
invalidate a live credential out from under whatever is currently trusting
it; both are the repository owner's call, made once, deliberately.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-c", "-z"],
        capture_output=True, check=True, text=True,
    )
    return [line for line in result.stdout.split("\0") if line]


def tracked_and_ignored(paths: list[str]) -> list[str]:
    """Which of `paths` also match a `.gitignore` pattern.

    `--no-index` is load-bearing: without it, `check-ignore` treats a path
    already in the index as exempt from exclude patterns by definition, which
    is exactly the blind spot this check exists to close.
    """
    if not paths:
        return []
    result = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "--no-index", "-z", "--stdin"],
        input="\0".join(paths), capture_output=True, text=True,
    )
    # 0 = at least one match, 1 = none, anything else is check-ignore itself
    # failing (a malformed .gitignore, say) — not this script's to interpret.
    if result.returncode not in (0, 1):
        print(result.stderr, file=sys.stderr, end="")
        raise subprocess.CalledProcessError(result.returncode, result.args)
    return [line for line in result.stdout.split("\0") if line]


def main() -> int:
    offenders = tracked_and_ignored(tracked_files())
    if offenders:
        print(
            f"{len(offenders)} file(s) are both tracked by git and matched by "
            "a .gitignore pattern meant to keep them untracked:\n"
        )
        for path in offenders:
            print(f"  • {path}")
        print(
            "\n.gitignore never untracks a path retroactively — only `git rm "
            "--cached <path>` does. If any of these hold a secret already "
            "committed to history, removing it from tracking is not enough on "
            "its own; that needs a deliberate decision about rewriting history "
            "and rotating the credential."
        )
        return 1
    print(f"{len(tracked_files())} tracked files: none match a .gitignore pattern")
    return 0


if __name__ == "__main__":
    sys.exit(main())
