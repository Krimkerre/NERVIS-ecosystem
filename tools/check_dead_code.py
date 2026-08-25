#!/usr/bin/env python3
"""Fail when a definition is written and never referenced.

Ruff catches unused imports and unused locals. It does not catch a function that
is defined, exported, documented and simply never called — and that shape has
bitten this repository four times:

- `capable()` in the dashboard, under a comment claiming controls were
  capability-driven. They were not, for two milestones.
- `UnsupportedProtocolVersionError`, defined for §4.2's structural refusal while
  nothing ever refused.
- `is_due_for_refresh`, whose existence made the catalogue look self-healing
  when the only thing refreshing it was a 300-second timer.
- `LoadFailedError` and `DeadlineExceededError`, so §4.3 published three codes
  and used one.

Every one made something *look* implemented. None of them failed a test, a lint
or a type check, because there is nothing wrong with the code — only with the
belief that it does something.

**Reference counting is textual, on purpose.** An AST call graph would miss how
this codebase actually reaches things: FastAPI decorators, `getattr`, pytest
collection, a name in a docstring that documents a real contract. A sweep that
is confidently wrong is worse than one a person can read.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGES = ("protocol", "ravis", "sirvis", "nervis")

# Names reached by machinery rather than by a caller. Underscore-prefixed names
# are already private and ruff's own rules cover the ones that matter.
EXEMPT_PREFIX = ("test_", "_")
EXEMPT_EXACT = {"main", "handle"}

# Decorators that register a function by reference. Its name is then legitimately
# written exactly once, and counting it as dead would bury the real findings
# under forty route handlers.
REGISTERED_BY = ("router.", "app.", "api.", "fixture", "validator", "property")

SEARCHED = ("*.py", "*.html", "*.md", "*.toml", "*.yml")
SKIP_PARTS = {".venv", "__pycache__", ".git", "node_modules"}


def haystack() -> str:
    """Everything a reference could plausibly live in, documentation included.

    Markdown counts. A name that appears only in a specification is not called,
    but it is *claimed*, and this exists to find the gap between the two.
    """
    parts: list[str] = []
    for pattern in SEARCHED:
        for path in ROOT.rglob(pattern):
            if SKIP_PARTS & set(path.parts):
                continue
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def registered(node: ast.AST) -> bool:
    """Whether a decorator reaches this by reference rather than by name."""
    for decorator in getattr(node, "decorator_list", []):
        if any(mark in ast.unparse(decorator) for mark in REGISTERED_BY):
            return True
    return False


def definitions(path: pathlib.Path) -> list[tuple[str, str, int]]:
    """(kind, name, lineno) for module-level defs and classes, and for methods."""
    found: list[tuple[str, str, int]] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not registered(node):
                found.append(("function", node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            found.append(("class", node.name, node.lineno))
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not registered(member):
                        found.append((f"method on {node.name}", member.name, member.lineno))
    return found


def python_findings(text: str) -> list[str]:
    counted: dict[str, int] = {}
    findings: list[str] = []
    for package in PACKAGES:
        for path in sorted((ROOT / package / "src").rglob("*.py")):
            for kind, name, lineno in definitions(path):
                if name.startswith(EXEMPT_PREFIX) or name in EXEMPT_EXACT:
                    continue
                if name not in counted:
                    counted[name] = len(re.findall(rf"\b{re.escape(name)}\b", text))
                if counted[name] <= 1:
                    where = path.relative_to(ROOT)
                    findings.append(f"{where}:{lineno}  {name} ({kind})")
    return findings


def dashboard_findings(text: str) -> list[str]:
    """The same question for the dashboard's inline script.

    Its own pass because the definitions are not Python, and because `capable()`
    — the finding that started all of this — lives there.
    """
    page = ROOT / "nervis" / "index.html"
    source = page.read_text(encoding="utf-8")
    script = source[source.index("<script>") :]
    names: set[str] = set()
    for pattern in (
        r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
        r"^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[\w,\s]*\)?\s*=>",
    ):
        names.update(match.group(1) for match in re.finditer(pattern, script, re.M))
    return [
        f"nervis/index.html  {name} (dashboard function)"
        for name in sorted(names)
        if len(re.findall(rf"\b{re.escape(name)}\b", text)) <= 1
    ]


def main() -> int:
    text = haystack()
    findings = python_findings(text) + dashboard_findings(text)
    if not findings:
        print("nothing is defined and unreferenced")
        return 0
    print("defined and referenced nowhere else:\n")
    for finding in findings:
        print(f"  • {finding}")
    print(
        "\nEach of these makes something look implemented. Wire it, delete it, or —"
        "\nif it is specified behaviour with no caller yet — give it a test, so the"
        "\nclaim is at least checked."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
