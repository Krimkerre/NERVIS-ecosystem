#!/usr/bin/env python3
"""Known security holes in the code the ecosystem depends on (runbook §9).

**Nothing checked them until 16 September 2026.** The clean-clone gate installs npm
packages with `--no-audit`, and nothing looked at the Python ones at all. This asks the
public advisory databases about every package the running stack and Clarvis use:

* **Python** — the one environment the launcher starts SIRVIS, RAVIS and NERVIS from
  (`ravis/.venv`, see `tools/run.py`), through `pip-audit`. The ecosystem's own
  packages are installed editable and are skipped: they are not on PyPI, and a
  same-named stranger there must never be mistaken for them.
* **npm** — NERVIS's page-check tooling, and Clarvis. Clarvis is read twice: what the
  installed extension ships (`--omit=dev`), and the build and test tools beside it.

**What fails.** Any finding in the Python environment, NERVIS's tooling, or what
Clarvis ships. Findings in Clarvis's build and test tools are listed but do not fail:
they run only on Clarvis's own source, never on anything a user or a model supplies,
and their fixes are often major upgrades that need their own release.

`pip-audit` lives in its own environment so its dependencies never change the
services' ones:

    python3 -m venv tools/.venv && tools/.venv/bin/python -m pip install pip-audit

It only reads: nothing is installed, upgraded or written, apart from pip-audit's cache.
Needs the network. Exit 0 clean, 1 findings, 2 could not check.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CLARVIS = ROOT.parent / "clarvis"
SERVICES_PYTHON = ROOT / "ravis" / ".venv" / "bin" / "python"
PIP_AUDIT = ROOT / "tools" / ".venv" / "bin" / "pip-audit"


def python_findings() -> list[str]:
    """One line per known hole in the services' Python environment."""
    tool = str(PIP_AUDIT) if PIP_AUDIT.exists() else shutil.which("pip-audit")
    if not tool:
        raise RuntimeError("pip-audit is not installed; see this script's docstring")
    # PIPAPI_PYTHON_LOCATION points pip-audit at another interpreter's packages.
    env = {**os.environ, "PIPAPI_PYTHON_LOCATION": str(SERVICES_PYTHON)}
    run = subprocess.run(
        [tool, "--skip-editable", "--progress-spinner", "off", "-f", "json"],
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
        check=False,
    )
    try:
        report = json.loads(run.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"pip-audit gave no report: {run.stderr.strip()[-300:]}"
        ) from error
    lines = set()  # pip-audit can list one advisory twice
    for package in report.get("dependencies", []):
        for hole in package.get("vulns", []):
            fix = ", ".join(hole.get("fix_versions") or []) or "none yet"
            lines.add(
                f"{package['name']} {package['version']}: {hole['id']} (fixed in {fix})"
            )
    return sorted(lines)


def npm_findings(folder: pathlib.Path, *extra: str) -> list[str]:
    """One line per vulnerable npm package in `folder`'s lockfile."""
    run = subprocess.run(
        ["npm", "audit", "--json", *extra],
        cwd=folder,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    try:
        report = json.loads(run.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"npm audit in {folder.name} gave no report") from error
    if "vulnerabilities" not in report:
        raise RuntimeError(f"npm audit in {folder.name}: {report.get('error', report)}")
    lines = []
    for name, entry in sorted(report["vulnerabilities"].items()):
        fix = entry.get("fixAvailable")
        if isinstance(fix, dict):
            major = ", a major change" if fix.get("isSemVerMajor") else ""
            fix_words = f"fix: {fix['name']} {fix['version']}{major}"
        else:
            fix_words = "fix: npm audit fix" if fix else "no fix yet"
        lines.append(f"{name} ({entry['severity']}, {entry['range']}; {fix_words})")
    return lines


def main() -> int:
    checks = [
        ("Python packages the services run on", True, python_findings),
        (
            "NERVIS page-check tooling (npm)",
            True,
            lambda: npm_findings(ROOT / "nervis"),
        ),
    ]
    if (CLARVIS / "package-lock.json").exists():
        checks += [
            (
                "What Clarvis ships (npm)",
                True,
                lambda: npm_findings(CLARVIS, "--omit=dev"),
            ),
            (
                "Clarvis's build and test tools (npm)",
                False,
                lambda: npm_findings(CLARVIS),
            ),
        ]
    else:
        print(f"Clarvis not checked: no checkout at {CLARVIS}")

    failed = broken = False
    shipped_clarvis: set[str] = set()
    for title, blocking, check in checks:
        try:
            lines = check()
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            print(f"✗ {title}: could not check — {error}")
            broken = True
            continue
        if title.startswith("What Clarvis ships"):
            shipped_clarvis = set(lines)
        elif not blocking:
            # The full audit repeats what ships; list only what the tools add.
            lines = [line for line in lines if line not in shipped_clarvis]
        mark = "✓" if not lines else ("✗" if blocking else "!")
        print(f"{mark} {title}: {len(lines)} known hole(s)")
        for line in lines:
            print(f"    {line}")
        failed |= blocking and bool(lines)
    return 2 if broken else 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
