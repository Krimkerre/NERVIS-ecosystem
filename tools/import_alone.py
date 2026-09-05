#!/usr/bin/env python3
"""Import every module of one package, with only that package installed (§3).

**The property the repository split used to buy.** §3 gave up two repositories
for one and said so plainly: *"The property that matters is independent
buildability, not repository count"*, kept by "separate packages, separate entry
points, separate databases". `check_imports.py` proves no product *imports* a
peer. This proves the other half — that a product's declared dependencies are
enough to load it — and the two are not the same question. A package can name no
sibling and still be unbuildable alone by forgetting `httpx`.

**It has happened here, and the fix left a note.** `ravis/pyproject.toml` carries
one: *"Imported since M0 (`app.py` reads its correlation helpers) and never
declared — RAVIS was getting the shared package by accident of the
environment."* That is exactly the failure mode, and it survived from M0 until
somebody happened to look, because every environment anybody ran had the shared
package in it.

**Which is what the clean-clone gate could not see.** It builds one virtual
environment and installs all four packages into it, then runs each suite there.
A package that forgot to declare `psutil` passes: a sibling brought it. So the
gate that exists to prove independent buildability was the one place the
dependency graph was guaranteed to be complete.

**Importing rather than testing, and that is deliberate.** Running each suite in
its own environment would take four times as long and answer a broader question
badly; walking every module and importing it finds an undeclared dependency at
the moment it is missing, which is what this is for. A dependency used only
inside a function body is not caught, and that is a known limit rather than an
oversight — the ones that bite are imported at module load, because that is when
the service fails to start.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: import_alone.py <package>")
        return 2
    name = sys.argv[1]
    try:
        package = importlib.import_module(name)
    except Exception as refusal:  # noqa: BLE001 — the failure is the finding
        print(f"{name} does not import at all: {type(refusal).__name__}: {refusal}")
        return 1

    broken: list[str] = []
    seen = 0
    for module in pkgutil.walk_packages(package.__path__, prefix=f"{name}."):
        # **`__main__` is meant to run when imported** — that is how `python -m
        # nervis` works, and every service here has one for a reason its own
        # docstring records: a console script covers the installed case and
        # nothing covers the module case. Importing it would run the CLI, which
        # is the module behaving correctly and this check behaving badly.
        if module.name.endswith(".__main__"):
            continue
        seen += 1
        try:
            importlib.import_module(module.name)
        except Exception as refusal:  # noqa: BLE001 — including ImportError
            broken.append(f"{module.name}: {type(refusal).__name__}: {refusal}")

    if broken:
        print(f"{name} cannot be loaded from its own declarations alone:\n")
        for failure in broken:
            print(f"  • {failure}")
        print(
            "\nEither the import is wrong or `pyproject.toml` is missing a dependency."
            "\nA package that only loads because a sibling installed something is a"
            "\npackage that stops loading the day somebody installs it on its own (§3)."
        )
        return 1
    print(f"{name}: {seen} modules import with only its own declarations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
