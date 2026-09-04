#!/usr/bin/env python3
"""No product imports another product (§3).

**The check §3 said existed.** Three products and the protocol package share one
repository, and §3 is blunt about what that costs: *"nothing about a repository
boundary enforces a module boundary"*. Its answer is a prohibition — shared
database tables, provider clients, routing engines and benchmark logic —
"enforced by an import check in CI rather than by the filesystem, which makes it
a rule that is actually verified instead of merely implied."

There was no such check. The rule held because nobody had broken it yet, which
is exactly the state §3 wrote that sentence to avoid, and it is the same shape as
the four dead definitions `check_dead_code.py` exists to find: a claim in a
document, and nothing underneath.

**What is allowed, and why the list is short.** A product may import itself and
`ecosystem_protocol`, which is the shared *contract* — envelopes, capability
states, correlation IDs, the canonical routes. That package is allowed to import
none of them: a protocol that reaches into a consumer is no longer a contract,
it is a dependency in the wrong direction, and the next thing it grows is a
special case for one product.

`tools/` is exempt and has to be. `conformance_check.py` drives all three
applications in one process on purpose — a cross-service conformance suite owned
by one service is a suite that service can quietly relax — and a gate that
forbade it would be forbidding its own sibling.

**Import statements only, not the whole surface.** A product reaching another
through `importlib`, a subprocess or an HTTP call is not what this finds, and
two of those three are how the ecosystem is *supposed* to communicate. What it
finds is the compile-time coupling that makes independent buildability a fiction,
which is §3's actual subject.
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PRODUCTS = ("nervis", "ravis", "sirvis")

#: What every package may reach for regardless. The contract, and itself.
SHARED = "ecosystem_protocol"

SKIP_PARTS = {".venv", "__pycache__", ".git", "node_modules", "build", "dist"}


def imported(path: pathlib.Path) -> set[str]:
    """Top-level module names this file imports.

    Both statement forms, and only the first segment: `from ravis.sessions import
    SESSION_HEADER` is a dependency on RAVIS whatever it takes out of it.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        # `level` is non-zero for a relative import, which cannot cross a
        # package boundary and is therefore never what this looks for.
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found


def sources(package: str) -> list[pathlib.Path]:
    """Every Python file a package ships or tests with."""
    found: list[pathlib.Path] = []
    for where in ("src", "tests"):
        base = ROOT / package / where
        if not base.is_dir():
            continue
        found += [p for p in sorted(base.rglob("*.py")) if not SKIP_PARTS & set(p.parts)]
    return found


def crossings(package: str, forbidden: set[str]) -> list[str]:
    findings: list[str] = []
    for path in sources(package):
        for name in sorted(imported(path) & forbidden):
            findings.append(f"{path.relative_to(ROOT)} imports `{name}`")
    return findings


def main() -> int:
    findings: list[str] = []
    for product in PRODUCTS:
        findings += crossings(product, {p for p in PRODUCTS if p != product})
    # The protocol package answers to all three and depends on none: a contract
    # that imports a consumer has stopped being a contract.
    findings += crossings("protocol", set(PRODUCTS))

    if findings:
        print("a product reached across a boundary §3 prohibits:\n")
        for finding in findings:
            print(f"  • {finding}")
        print(
            f"\nEach product keeps its own domain models and owns its adapters; only"
            f"\n`{SHARED}` is shared, and it is a contract rather than a framework."
            "\nTalk to a peer over HTTP, or move the shape into the protocol package"
            "\nif it is genuinely part of the contract."
        )
        return 1
    checked = sum(len(sources(p)) for p in (*PRODUCTS, "protocol"))
    print(
        f"{checked} files across {len(PRODUCTS)} products and the protocol package: "
        f"nothing imports a peer"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
