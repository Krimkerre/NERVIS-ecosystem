#!/usr/bin/env python3
"""Which wording of RAVIS's Codex file rules this machine's Codex sandbox can start with.

Written 19 September 2026 for Linux, where Codex 0.155.1's bwrap sandbox fails to start when
the rules hide two or more single files (openai/codex#43929: "bwrap: Can't write data to file
…: Bad file descriptor"). RAVIS's rules hide two: Codex's `auth.json` and `~/.netrc`.

**Safe to run anywhere.** No account, no network, no ChatGPT turn: `codex sandbox` runs one
local command under the rules. Codex is pointed at a throwaway settings folder, so the real
Codex folders are never read or written, and the "sign-in file" it tries to read is a fake one
made here. Everything lives in a temporary folder removed at the end.

For each wording it prints whether the sandbox started and whether the fake sign-in file was
still hidden from the command — a wording that starts but leaks is no good.

Usage:  python3 tools/codex_sandbox_probe.py [path/to/codex]
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PIN = Path(__file__).resolve().parents[1] / "ravis/src/ravis/codex/tested_runtimes.json"
SECRET = "probe-secret-value"


def profile_flag() -> str:
    """The `clarvis_run` profile's `-c` value, exactly as RAVIS pins it."""
    flags = json.loads(PIN.read_text())["file_rules_profile"]["flags"]
    return flags[flags.index("-c") + 1]


def wording(base: str, drop: tuple[str, ...], places: dict[str, str]) -> str:
    """The profile with the named rules taken out and the placeholders filled in."""
    text = base
    for rule in drop:
        text = text.replace(f', "{rule}"="deny"', "").replace(f'"{rule}"="deny", ', "")
    for name, value in places.items():
        text = text.replace(name, value)
    return text


def main() -> int:
    codex = sys.argv[1] if len(sys.argv) > 1 else shutil.which("codex")
    if not codex:
        print("No codex found. Give its path: python3 tools/codex_sandbox_probe.py /path/to/codex")
        return 2
    version = subprocess.run([codex, "--version"], capture_output=True, text=True,
                             check=False).stdout.strip()
    print(f"{version} at {codex}\n")
    base = profile_flag()
    variants = [
        ("all rules, as RAVIS writes them now", ()),
        ("without the ~/.netrc rule", ("{user_home}/.netrc",)),
        ("without the auth.json rule", ("{codex_home}/auth.json",)),
        ("without either single-file rule", ("{user_home}/.netrc", "{codex_home}/auth.json")),
    ]
    with tempfile.TemporaryDirectory(prefix="ravis-probe-") as temporary:
        root = Path(temporary)
        settings, home, project = root / "settings", root / "codex-home", root / "project"
        for folder in (settings, home, project, root / "ravis-config", root / "decoys"):
            folder.mkdir()
        (home / "auth.json").write_text(SECRET)
        places = {"{user_home}": str(Path.home()), "{ravis_config}": str(root / "ravis-config"),
                  "{codex_home}": str(home), "{reproof_decoys}": str(root / "decoys")}
        for label, drop in variants:
            command = [codex, "sandbox", "-c", f"permissions.clarvis_run={_value(base)}",
                       "-P", "clarvis_run", "-C", str(project), "--",
                       "sh", "-c", f"cat {home / 'auth.json'} 2>/dev/null; echo started"]
            command[3] = wording(command[3], drop, places)
            ran = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False,
                                 env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(Path.home()),
                                      "CODEX_HOME": str(settings)})
            said = ran.stdout + ran.stderr
            started = "started" in ran.stdout
            leaked = SECRET in ran.stdout
            verdict = ("STARTS and hides the sign-in file" if started and not leaked
                       else "STARTS but LEAKS the sign-in file" if started
                       else "does NOT start")
            print(f"- {label}: {verdict}")
            if not started:
                print("    " + (said.strip().splitlines() or ["(no output)"])[-1][:200])
    return 0


def _value(flag: str) -> str:
    """The part after `permissions.clarvis_run=` in the pinned flag."""
    return flag.split("=", 1)[1]


if __name__ == "__main__":
    sys.exit(main())
