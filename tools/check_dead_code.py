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
- `may_declare_background_calls`, an application-identity **field** describing a
  trust boundary RAVIS checks nowhere. Found only because NERVIS M4 went looking
  for it — this gate swept functions and classes, and a field is a claim too.

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

# **Code only.** Markdown is deliberately absent, and that is a correction.
#
# The first version of this gate counted a Markdown mention as a reference,
# reasoning that "a name in a specification is claimed, and the gap between
# claimed and called is what this finds". That is exactly backwards: a name
# appearing *only* in prose is the strongest evidence it is dead, and counting
# it live meant **writing about a dead definition silenced the gate**. Proved on
# the first run after the rule changed — `may_declare_background_calls` went
# unreported because STATUS.md's entry about it being dead was itself the second
# reference.
#
# HTML stays, because the dashboard's inline script genuinely calls things and
# reads field names as JSON keys. TOML and YAML stay for console-script entry
# points and CI invocations.
SEARCHED = ("*.py", "*.html", "*.toml", "*.yml")
SKIP_PARTS = {".venv", "__pycache__", ".git", "node_modules"}


def haystack() -> str:
    """Everything a reference could plausibly live in — code, not prose.

    See `SEARCHED` for why documentation is excluded: counting it made the gate
    silenceable by describing the problem.
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
    """(kind, name, lineno) for module-level defs and classes, methods and fields.

    **Annotated class attributes count**, which is what a dataclass field and a
    pydantic setting both are. `may_declare_background_calls` was declared on
    RAVIS's application identity, documented as a trust boundary, and read
    nowhere — a claim in exactly the same sense as an uncalled function, and one
    this gate walked straight past while it swept only callables.

    Plain assignments are skipped, because that is what an enum member is
    (`TEXT = "text"`) and enum members are reached through their class.
    """
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
                elif isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
                    found.append((f"field on {node.name}", member.target.id, member.lineno))
    return found


def attribute_reads() -> set[str]:
    """Every attribute name *read* anywhere in the repository's Python.

    Fields cannot be judged the way functions are. `may_declare_background_calls`
    appears three times — its declaration and two constructor keywords — so a
    textual count calls it referenced while nothing ever asks what it holds.
    Setting a value is not using it.

    So a field is live when something **reads** it: `x.field` in a load context,
    which is what `ast.Attribute` with `ctx=Load` means. Keyword arguments are
    `ast.keyword` and deliberately do not count.
    """
    reads: set[str] = set()
    # **Source only, not tests.** A test that asserts a field's value proves the
    # constructor sets it; it does not prove anything consults it.
    # `may_declare_background_calls` was read by exactly one line —
    # `assert identity.may_declare_background_calls is False` — while the trust
    # boundary it describes was enforced nowhere. Counting that as a use makes
    # the gate blind to the case it exists for: asserted, never acted on.
    for path in (p for package in PACKAGES for p in (ROOT / package / "src").rglob("*.py")):
        if SKIP_PARTS & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                reads.add(node.attr)
            # `getattr(x, "field")` is a read the AST spells differently.
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "getattr" and len(node.args) >= 2:
                    if isinstance(node.args[1], ast.Constant) and isinstance(
                        node.args[1].value, str
                    ):
                        reads.add(node.args[1].value)
    return reads


def serialised_wholesale() -> set[str]:
    """Classes whose fields leave through a generic serialiser.

    `asdict(self)`, `model_dump()` and `dict(row)` read every field without
    naming one, so their fields have no attribute read to find and are not dead.
    Exempting the class is cruder than tracking the call, and crude in the safe
    direction: it under-reports rather than accusing working code.
    """
    exempt: set[str] = set()
    for package in PACKAGES:
        for path in sorted((ROOT / package / "src").rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                body = ast.unparse(node)
                if any(mark in body for mark in ("asdict(", "model_dump(", "__dict__")):
                    exempt.add(node.name)
    return exempt


def python_findings(text: str) -> list[str]:
    counted: dict[str, int] = {}
    findings: list[str] = []
    reads = attribute_reads()
    exempt = serialised_wholesale()
    # Field names also travel as JSON keys into the dashboard and the
    # documentation, where they are read by something that is not Python.
    outside = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for pattern in ("*.html", "*.toml", "*.yml")
        for path in ROOT.rglob(pattern)
        if not SKIP_PARTS & set(path.parts)
    )
    for package in PACKAGES:
        for path in sorted((ROOT / package / "src").rglob("*.py")):
            for kind, name, lineno in definitions(path):
                if name.startswith(EXEMPT_PREFIX) or name in EXEMPT_EXACT:
                    continue
                where = path.relative_to(ROOT)
                if kind.startswith("field on "):
                    owner = kind.removeprefix("field on ")
                    if owner in exempt or name in reads:
                        continue
                    if re.search(rf"\b{re.escape(name)}\b", outside):
                        continue
                    findings.append(f"{where}:{lineno}  {name} ({kind}, never read)")
                    continue
                if name not in counted:
                    counted[name] = len(re.findall(rf"\b{re.escape(name)}\b", text))
                if counted[name] <= 1:
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
        names.update(match.group(1) for match in re.finditer(pattern, script, re.MULTILINE))
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
