#!/usr/bin/env python3
"""The knowledge files may not name things that do not exist.

**A fourth kind of document is a fourth thing that can rot.** There is already a
specification saying what each service should do, a `STATUS.md` saying what is
built, and the code saying what is true. `nervis/knowledge/` adds a fourth: short
notes written for the question an operator actually asks, which chat retrieves
and quotes.

Prose cannot be checked. A *claim* can, and the claims that matter here are the
concrete ones — a pool, an endpoint, a capability, a tool. If a pool is renamed
and these notes still teach the old name, chat will confidently give an operator
a name that no longer routes. So every one of them is verified against the code
that defines it, and a rename fails this gate rather than surfacing months later
in an answer.

What is deliberately **not** checked is the prose around them. "Cost first for a
pool that asked to be cheap" is a description of a judgement, and a gate that
tried to verify it would be a test of wording. The line is: names are checked,
explanations are reviewed by people.

    tools/knowledge_check.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE = ROOT / "nervis" / "knowledge"

failures: list[str] = []


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _pools_that_exist() -> set[str]:
    """Pool ids, from the file that defines them rather than a running service.

    A gate that needed the stack up would be a gate nobody could run in a clean
    clone, which is where it matters most.
    """
    source = _read(ROOT / "ravis" / "src" / "ravis" / "core" / "pools.py")
    return set(re.findall(r'pool_id\s*=\s*"(ravis/[a-z0-9-]+)"', source))


def _clarvis_tools() -> set[str]:
    registry = ROOT.parent / "clarvis" / "src" / "agent" / "toolRegistry.ts"
    if not registry.is_file():
        return set()          # a checkout without the sibling repository
    body = _read(registry)
    union = re.search(r"export type ToolName =(.*?);", body, re.DOTALL)
    return set(re.findall(r"'([A-Za-z]+)'", union.group(1))) if union else set()


def _clarvis_settings() -> set[str]:
    """Every setting the extension actually contributes.

    Added because chat invented one. Asked to raise the agent step limit it
    correctly refused — NERVIS may not change a Clarvis setting — and then named
    `clarvis.agent.stepLimit`, which does not exist; the real one is
    `clarvis.agent.maxStepsPerTask`. A confident, precise, wrong instruction is
    worse than "look in Settings", and it is exactly the failure mode of a
    control plane whose whole remaining value is naming the thing it may not
    touch.
    """
    manifest = ROOT.parent / "clarvis" / "package.json"
    if not manifest.is_file():
        return set()          # a checkout without the sibling repository
    contributed = json.loads(_read(manifest)).get("contributes", {}).get("configuration", {})
    sections = contributed if isinstance(contributed, list) else [contributed]
    return {name for section in sections for name in section.get("properties", {})}


def _endpoints_that_exist() -> set[str]:
    """Every route anything in the ecosystem declares.

    **Four services, and one of them is not Python.** Scanning only the FastAPI
    routers reported `/v1/status` as served by nothing — which is exactly the
    kind of confident wrongness this gate exists to prevent, produced by the
    gate itself. Clarvis's Bridge serves it from a `switch` in TypeScript.

    **A second gap, same shape.** A router whose whole path lives in its own
    `prefix=` — `APIRouter(prefix="/api/v1/registry/instances")` with routes
    declared against `""` — reported `/api/v1/registry/instances` as served by
    nothing too, because a bare `@router.get("")` carries no path of its own to
    find. Read per file rather than globally: two routers in one file could
    otherwise have their prefixes and suffixes cross-multiplied into paths
    nothing serves.
    """
    found: set[str] = set()
    for package in ("nervis", "ravis", "sirvis"):
        for source in (ROOT / package / "src").rglob("*.py"):
            text = _read(source)
            prefix_match = re.search(r'APIRouter\(\s*prefix="([^"]*)"', text)
            prefix = prefix_match.group(1) if prefix_match else ""
            for suffix in re.findall(r'@router\.\w+\(\s*"([^"]*)"', text):
                found.add(prefix + suffix)
    bridge = ROOT.parent / "clarvis" / "src" / "bridge"
    if bridge.is_dir():
        for source in bridge.glob("*.ts"):
            found.update(re.findall(r"case '(/[a-z0-9/_.-]+)':", _read(source)))
    return found


pools = _pools_that_exist()
tools = _clarvis_tools()
settings = _clarvis_settings()
endpoints = _endpoints_that_exist()

if not pools:
    failures.append("could not read any pool ids from ravis/core/pools.py — "
                    "this gate would pass vacuously, which is worse than failing")

for path in sorted(KNOWLEDGE.glob("*.md")):
    text = _read(path)
    where = path.name

    for named in sorted(set(re.findall(r"`(ravis/[a-z0-9-]+)`", text))):
        if named not in pools:
            failures.append(
                f"{where} names the pool {named!r}, which no longer exists. "
                f"Chat would hand an operator a pool that does not route."
            )

    # Endpoints are written as `/api/v1/...` or `/ecosystem/...`.
    for named in sorted(set(re.findall(r"`(/(?:api/v1|ecosystem|v1)/[a-z0-9/_{}-]+)`", text))):
        tail = named.split("/api/v1")[-1] if "/api/v1" in named else named
        if tail not in endpoints and named not in endpoints:
            failures.append(f"{where} names the endpoint {named!r}, which nothing serves.")

    # A setting named in a knowledge file is an instruction somebody will follow
    # into VS Code's settings editor, so it has to be the real key.
    if settings:
        for named in sorted(set(re.findall(r"`(clarvis\.[A-Za-z0-9.]+)`", text))):
            if named not in settings:
                failures.append(
                    f"{where} names the setting {named!r}, which the extension does "
                    f"not contribute. Chat quotes these verbatim to an operator."
                )

    if tools and where == "clarvis.md":
        claimed = set(re.findall(r"`([a-zA-Z]+)`", text)) & {
            t for t in tools} | (set(re.findall(r"`(readFile|writeFile|applyEdit|runCommand"
                                                r"|listFiles|search|readDiagnostics|gitStatus"
                                                r"|gitDiff)`", text)))
        missing = sorted(claimed - tools)
        if missing:
            failures.append(f"{where} names Clarvis tools that do not exist: {missing}")
        # And the other direction: a tool that exists and is not documented is a
        # capability an operator cannot learn about from chat.
        undocumented = sorted(tools - claimed)
        if undocumented:
            failures.append(
                f"{where} does not mention the Clarvis tools {undocumented}. The file "
                f"claims to list what the agent can do to a workspace, and a tool "
                f"missing from that list is one nobody is warned about."
            )

if failures:
    print("The knowledge files name things that do not exist:\n", file=sys.stderr)
    for failure in failures:
        print("  • " + failure, file=sys.stderr)
    print("\nThese files are quoted to operators by chat. A stale name in them is a "
          "wrong answer delivered confidently.", file=sys.stderr)
    raise SystemExit(1)

print(f"knowledge files check out: {len(pools)} pools, {len(tools)} Clarvis tools, "
      f"{len(endpoints)} endpoints and {len(settings)} Clarvis settings available to name")
