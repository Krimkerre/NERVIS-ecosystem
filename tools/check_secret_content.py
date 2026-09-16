#!/usr/bin/env python3
"""No key, token or private key goes into a commit (runbook §9's secret scan).

`check_no_tracked_secrets.py` stops a *file* that should never be tracked. It cannot see a key
pasted into a file that should be — a provider key in a test, a launcher token in a note — and
until 16 September 2026 nothing could (`design/security/review-2026-09-16.md`, S5). This reads
the lines a commit adds and refuses the commit when one carries:

* **a key in a shape its issuer uses** — OpenAI, Anthropic, OpenRouter and DeepSeek (`sk-…`),
  GitHub, AWS, Google, Hugging Face, Slack, Stripe, xAI, Groq — or a private-key block; or
* **one of this machine's own secrets**, by value: every launcher token and password in
  `.run/`, and NERVIS's enrollment secrets. Those are random strings with no shape a pattern
  could find, and they are the secrets this repository sits next to.

**It never prints what it found**, only the file, the line and what kind of secret it looks
like, so the refusal cannot become the leak. The secrets it reads from `.run/` stay in memory.

A line that must carry something key-shaped — a documented fake in a test — says so with
`secret-scan: allow` on the same line.

    python3 tools/check_secret_content.py          # the staged changes (the pre-commit hook)
    python3 tools/check_secret_content.py --all    # every tracked file (the clean-clone gate)

Exit 0 clean, 1 something found, 2 could not read what git holds.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW = "secret-scan: allow"

#: (what it looks like, pattern). Each needs enough random characters after its prefix that a
#: placeholder such as `sk-...` or `ghp_xxx` in documentation does not match.
SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("an Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{32,}")),
    ("an OpenAI-style key (sk-)", re.compile(r"\bsk-(?!ant-)(?:proj-|or-v1-)?[A-Za-z0-9_-]{32,}")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})")),
    ("an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("a Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("a Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{20,}")),
    ("a Stripe live key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}")),
    ("an xAI key", re.compile(r"\bxai-[A-Za-z0-9]{40,}")),
    ("a Groq key", re.compile(r"\bgsk_[A-Za-z0-9]{40,}")),
    ("a private key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY(?: BLOCK)?-----")),
)

#: This machine's own secrets, by where the launcher and NERVIS keep them. Values shorter than
#: this are ignored: a stray short file must not turn every common word into a finding.
MIN_SECRET_LENGTH = 16


def local_secrets(root: Path = ROOT) -> list[str]:
    """The values of this machine's launcher tokens, passwords and enrollment secrets."""
    files: list[Path] = []
    run = root / ".run"
    if run.is_dir():
        files += [*run.glob("*.token"), *run.glob("*.password")]
    files += [*root.glob("*.enrollment"), *root.glob("*/*.enrollment")]
    values = set()
    for path in files:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if len(value) >= MIN_SECRET_LENGTH:
            values.add(value)
    return sorted(values)


def findings(lines: Iterable[tuple[str, int, str]], secrets: list[str]) -> Iterator[str]:
    """One report per suspicious line, naming the file, the line and the kind — never the value."""
    for path, number, text in lines:
        if ALLOW in text:
            continue
        kinds = [kind for kind, shape in SHAPES if shape.search(text)]
        if any(secret in text for secret in secrets):
            kinds.append("one of this machine's own secrets (.run/ or an enrollment file)")
        if kinds:
            yield f"{path}:{number}: looks like {', '.join(kinds)}"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout


def staged_lines() -> Iterator[tuple[str, int, str]]:
    """Every line the staged commit adds, with its file and its new line number."""
    path, number = "", 0
    for line in _git("diff", "--cached", "--no-color", "--unified=0", "--no-ext-diff").splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            number = int(match.group(1)) if match else 0
        elif line.startswith("+") and path:
            yield path, number, line[1:]
            number += 1


def tracked_lines() -> Iterator[tuple[str, int, str]]:
    """Every line of every tracked text file."""
    for path in _git("ls-files", "-z").split("\0"):
        if not path:
            continue
        try:
            data = (ROOT / path).read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # binary
        for number, text in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            yield path, number, text


def main(argv: list[str]) -> int:
    whole = "--all" in argv
    try:
        found = list(findings(tracked_lines() if whole else staged_lines(), local_secrets()))
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"secret scan could not read what git holds: {error}")
        return 2
    if not found:
        print(f"No keys or tokens in {'the tracked files' if whole else 'the staged changes'}.")
        return 0
    print(f"{len(found)} line(s) look like a secret; nothing of them is printed here:")
    for line in found:
        print(f"  {line}")
    print(
        "Take the secret out (and treat it as leaked if it was ever pushed). A deliberate fake "
        f"can stay if its line says `{ALLOW}`."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
