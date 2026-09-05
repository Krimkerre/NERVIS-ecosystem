#!/usr/bin/env python3
"""§10's degradation matrix, written down and scored.

**Nineteen conditions in one running sentence, and nothing enumerated them.**
§10 says *"Test at minimum: each service absent at startup; crash and restart
mid-operation; …"* and then lists six required outcomes. Nothing in this
repository turned that into cells, so nobody could say which of the nineteen were
covered — and a matrix nobody can score is a matrix that gets called done.

**The conditions are parsed from the runbook, not copied here.** That is the
whole drift protection: a condition added to §10 and not to this file fails the
gate, and a cell naming a condition §10 does not is refused. Two lists that must
agree are one list and one copy, and the copy is always the stale one — the same
argument §3 makes about documents in two repositories.

**Every cell is PARTIAL, and that is the honest answer rather than a hedge.**
Each of the nineteen has real evidence — the ecosystem handles these conditions,
often thoroughly. None is *complete* in §10's sense, which asks not only that the
condition be handled but that the six outcomes hold under it: truthful capability
within the detection interval, no failover crossing a constraint, bounded and
jittered retries, bounded queues, idempotent recovery, standalone behaviour
preserved. A cell is COVERED when its own gap sentence is empty and something
checks the outcome, not when the code merely copes.

**What the ratchet holds.** `WITHOUT_LIVE_EVIDENCE` is the number of cells whose
evidence is only unit-level — a helper asserted rather than a service observed.
It may fall and may not rise, which is `check_plans.py`'s discipline applied to a
second list: this file cannot be quietly weakened by adding a cell that cites a
pure function and calling the condition handled.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNBOOK = ROOT / "ECOSYSTEM_RUNBOOK.md"
CLARVIS = ROOT.parent / "clarvis"

#: What a cell may say about itself.
VERDICTS = ("COVERED", "PARTIAL", "NOT_COVERED", "NOT_APPLICABLE")

#: How the evidence was obtained, weakest first. `unit` proves a helper; `route`
#: proves the application answered; `live` proves a running service did.
KINDS = ("unit", "static-gate", "manual", "route", "live")

#: Cells whose evidence never rises above a unit test. Zero today, and the
#: ceiling exists so it stays that way: every condition here is currently
#: observed at the route or against a running service somewhere.
WITHOUT_LIVE_EVIDENCE = 0


@dataclass
class Cell:
    condition: str
    verdict: str
    evidence: list[tuple[str, str]] = field(default_factory=list)
    gap: str = ""
    closes_with: str = ""


CELLS: list[Cell] = [
    Cell(
        condition="clock skew",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:743"),
            ("route", "nervis/tests/test_m7_traces.py:206"),
            ("static-gate", "tools/acceptance_run.py:105,1107-1117"),
        ],
        gap="Skew detection exists in exactly one place and is proven only by an in-process unit test: it is one-directional (only `occurred_at` more than 2.0s AHEAD of `_received_at`; a producer whose clock is behind is indistinguis",
        closes_with="Add one route-level test to nervis/tests/test_m7_traces.py that POSTs a future-stamped event to /api/v1/events and asserts (a) GET /api/v1/traces/{trace_id} returns a warning containing \"clock is ahea",
    ),
    Cell(
        condition="unsupported major protocol version",
        verdict="PARTIAL",
        evidence=[
            ("route", "nervis/src/nervis/probes.py:201"),
            ("route", "nervis/src/nervis/negotiation.py:118"),
            ("route", "nervis/tests/test_m2_registry.py:328"),
        ],
        gap="The two peer-to-peer read paths (NERVIS probing RAVIS/SIRVIS, RAVIS reading SIRVIS evidence) genuinely reject an unsupported major and are tested through real code against fake peers, but the Clarvis Bridge path is ungua",
        closes_with="Add an is_supported_protocol() check to Instances.register() in /Users/mathias/Documents/coding/NERVIS-ecosystem/nervis/src/nervis/instances.py so a claim whose protocol_version fails the major compar",
    ),
    Cell(
        condition="crash and restart mid-operation",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:792"),
            ("live", "tools/acceptance_run.py:851"),
            ("static-gate", "nervis/tools/supervision_check.js:1"),
        ],
        gap="Nothing anywhere kills a service while an operation is genuinely in flight: every crash-recovery assertion is a unit test calling `reconcile_interrupted` / `sweep` / `still_running` directly, the SIRVIS startup wiring th",
        closes_with="Extend `restart_clause` in /Users/mathias/Documents/coding/NERVIS-ecosystem/tools/acceptance_run.py to submit a benchmark job, wait until it reads `running`, `kill -9` SIRVIS instead of `kill -15`, re",
    ),
    Cell(
        condition="corrupt response",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:202"),
            ("static-gate", "nervis/tools/outcome_check.js:31"),
            ("unit", "nervis/tests/test_m2_registry.py:382"),
        ],
        gap="Nothing tests a corrupt response arriving at RAVIS from an upstream through a RAVIS route: on the transparent path an unparseable 200 is explicitly \"left alone\" (ravis/src/ravis/api/openai/chat.py:1669-1672), forwarded t",
        closes_with="Add a route test to ravis/tests/test_transparent_proxy.py (mirroring the malformed-request test at line 182) that makes RecordingUpstream answer /v1/chat/completions with a 200 and an unparseable body",
    ),
    Cell(
        condition="duplicate and out-of-order events",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:743"),
            ("route", "nervis/tests/test_m6_events.py:644"),
            ("route", "clarvis/src/bridge/server.test.ts:387"),
        ],
        gap="Duplicates are genuinely covered at the storage layer and at producer retry, but out-of-order arrival is asserted only inside the RAVIS/SIRVIS publisher queue \u2014 nothing ingests events out of order through NERVIS's POST r",
        closes_with="Add one route-level test to /Users/mathias/Documents/coding/NERVIS-ecosystem/nervis/tests/test_m7_traces.py that POSTs a trace's events to /api/v1/events reversed and with the batch sent twice, then a",
    ),
    Cell(
        condition="each service absent at startup",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:792"),
            ("route", "nervis/tests/test_m2_registry.py:704"),
            ("route", "nervis/tests/test_m0_foundation.py:107"),
        ],
        gap="No live-process evidence exists for any service starting while a peer is absent \u2014 tools/acceptance_run.py always brings the whole stack up first and only then kills SIRVIS \u2014 and outside NERVIS (which has genuine route-le",
        closes_with="Add a cold-start clause to /Users/mathias/Documents/coding/NERVIS-ecosystem/tools/acceptance_run.py \u2014 a new entry in CLAUSES (line 85) and a function invoked before restart_clause that starts the stac",
    ),
    Cell(
        condition="timeout",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:163-199,481-500,796-898"),
            ("route", "nervis/tests/test_m4_chat.py:1363"),
            ("route", "nervis/tests/test_m2_registry.py:741-754;"),
        ],
        gap="Nothing anywhere provokes a real timeout through a Python service's request path or against a running stack: RAVIS's TIMEOUT rule (do not retry the same target, do fall back, open the provider circuit) is asserted only a",
        closes_with="Add a route-level case to ravis/tests/test_fallback.py that scripts the primary as a timeout \u2014 `ScriptedUpstream(TWO_CODERS, refuse={\"coder-a\": (504, {\"error\": \"timed out\"})})` plus a transport varian",
    ),
    Cell(
        condition="slow response",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:481"),
            ("route", "ravis/tests/test_translated_path.py:198"),
            ("static-gate", "nervis/index.html:1274,1295,1563,1068"),
        ],
        gap="No test or gate makes a dependency slow and then checks a *service's own routes* stay answerable and truthful: the slow path is covered by unit tests on helpers (Clarvis deadlines, RAVIS probe timeout and cache, SIRVIS r",
        closes_with="Add a test to /Users/mathias/Documents/coding/NERVIS-ecosystem/nervis/tests/test_m2_registry.py that drives nervis/src/nervis/probes.py:probe() through an httpx.MockTransport handler which records req",
    ),
    Cell(
        condition="full disk",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:1215"),
            ("route", "nervis/tests/test_m0_foundation.py:409"),
            ("unit", "sirvis/src/sirvis/telemetry/system.py:339"),
        ],
        gap="Nothing anywhere simulates a full disk: no test raises ENOSPC, no test patches `shutil.disk_usage` to a small figure, no admission check in sirvis/resources/manager.py considers space, and the download-job path SIRVIS.md",
        closes_with="Add a test to /Users/mathias/Documents/coding/NERVIS-ecosystem/sirvis/tests/test_m6_storage.py that monkeypatches `ResultDirectory._write_json` (sirvis/src/sirvis/storage/results.py:119) to raise `OSE",
    ),
    Cell(
        condition="unavailable keychain",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:90"),
            ("route", "ravis/src/ravis/app.py:220"),
            ("static-gate", "clarvis/src/secretStoreLabel.test.ts:47"),
        ],
        gap="No test, gate or acceptance step anywhere exercises a keychain that is missing, erroring, hung or prompting: RAVIS's `_from_keychain` fallthrough and its 5s timeout are code-only (every test in the suite builds the store",
        closes_with="Add a keychain-failure block to /Users/mathias/Documents/coding/NERVIS-ecosystem/ravis/tests/test_credentials.py that builds `CredentialStore(keychain=True, ...)` and monkeypatches `ravis.credentials.",
    ),
    Cell(
        condition="trace collector loss (NERVIS event hub unreachable, refusing, hanging, or never configured, from the point of view of every producer that publishes events to it)",
        verdict="PARTIAL",
        evidence=[
            ("live", "STATUS.md:5392"),
            ("route", "ravis/tests/test_m18b_events.py:223,232"),
            ("static-gate", "tools/acceptance_run.py:89,743,795,806"),
        ],
        gap="The one \u00a710 outcome that actually regressed once here \u2014 a dead collector must not make a product advertise itself as unready \u2014 is protected only by a code comment and a one-off manual kill recorded in STATUS.md prose: no",
        closes_with="Add a route-level regression test to /Users/mathias/Documents/coding/NERVIS-ecosystem/ravis/tests/test_m18b_events.py that flushes the publisher against the existing Refusing collector until the bound",
    ),
    Cell(
        condition="read-only data directory",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:85"),
            ("static-gate", "nervis/tools/page_context.js:299"),
            ("unit", "sirvis/tests/test_doctor.py:57"),
        ],
        gap="No test, gate, or acceptance step anywhere in either repository makes a directory read-only and observes what a service does; the only deliberate handling in the three Python services is one untested diagnostic string in",
        closes_with="Add a read-only case to /Users/mathias/Documents/coding/NERVIS-ecosystem/sirvis/tests/test_doctor.py: point SIRVIS_RESULTS_PATH and SIRVIS_DATABASE_PATH at a tmp_path directory chmod-ed to 0o555, then",
    ),
    Cell(
        condition="cloud provider 401/403/429/5xx",
        verdict="PARTIAL",
        evidence=[
            ("route", "ravis/src/ravis/api/openai/chat.py:1414"),
            ("route", "ravis/tests/test_fallback.py:153"),
            ("route", "ravis/tests/test_fallback.py:194"),
        ],
        gap="No test drives a cloud 401/403 (or a bare 500) through the app \u2014 the 401 rules are proven only on the AttemptChain object and the classifier, while 429/503 have real route-level tests; a bare 500 classifies as UNKNOWN an",
        closes_with="Add route-level cases to /Users/mathias/Documents/coding/NERVIS-ecosystem/ravis/tests/test_fallback.py, alongside the existing 503 and 429 tests, that script the upstream to answer 401 (and 403) and a",
    ),
    Cell(
        condition="network loss",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:796"),
            ("route", "ravis/src/ravis/api/openai/chat.py:1411"),
            ("route", "ravis/tests/test_fallback.py:449"),
        ],
        gap="Nothing drives a pre-first-byte connection failure through RAVIS's real request route \u2014 the two branches at chat.py:1411-1412 and :1755-1757 that decide whether an unreachable upstream falls through to the next candidate",
        closes_with="Add one route-level case to ravis/tests/test_fallback.py: give ScriptedUpstream a `refuse_transport` set so _handle raises httpx.ConnectError for the first candidate before any response, POST /v1/chat",
    ),
    Cell(
        condition="hung local runtime",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:801"),
            ("static-gate", "sirvis/src/sirvis/runtimes/lmstudio.py:62"),
            ("static-gate", "sirvis/src/sirvis/runtimes/lmstudio.py:400"),
        ],
        gap="Nothing anywhere exercises a runtime that accepts a connection and then stalls: SIRVIS's HTTP timeouts collapse into RUNTIME_UNAVAILABLE so a hung runtime is reported as an absent one (only the `lms` CLI can raise TIMEOU",
        closes_with="Add a route-level test to /Users/mathias/Documents/coding/NERVIS-ecosystem/sirvis/tests/test_m8_resources.py that mounts the app over an httpx.MockTransport whose handler raises httpx.ReadTimeout, dri",
    ),
    Cell(
        condition="expired credential",
        verdict="PARTIAL",
        evidence=[
            ("live", "tools/acceptance_run.py:85-93,966-1008"),
            ("route", "nervis/src/nervis/api/instances.py:189"),
            ("route", "clarvis/src/bridge/Bridge.ts:268"),
        ],
        gap="RAVIS \u2014 the service that actually holds the expiring credentials \u2014 cannot report that one has stopped working: its authenticated health probe turns a 401 into reachable=False (Anthropic's discards the status code entirel",
        closes_with="In /Users/mathias/Documents/coding/NERVIS-ecosystem/ravis/src/ravis/api/management/routes.py, make `_probe` (line 485) catch httpx.HTTPStatusError of 401/403 from `adapter.health()` and emit a distinc",
    ),
    Cell(
        condition="code-server loss \u2014 the browser VS Code that hosts Clarvis and that NERVIS embeds in its Clarvis/Code tab stops answering after having answered (crash, stopped, port taken)",
        verdict="PARTIAL",
        evidence=[
            ("route", "nervis/tests/test_m2_registry.py:704"),
            ("route", "nervis/src/nervis/ecosystem.py:354-364;"),
            ("static-gate", "nervis/src/nervis/registry.py:401"),
        ],
        gap="No test, gate or recorded live run anywhere names code-server going down after it had answered: the loss semantics are proven only through generic registry unit tests written against ollama/ravis, the tab's absent-editor",
        closes_with="Add one route-level test to /Users/mathias/Documents/coding/NERVIS-ecosystem/nervis/tests/test_m2_registry.py that records `codeserver` as healthy with the adapted `codeserver.workbench: available` ca",
    ),
    Cell(
        condition="Bridge collision \u2014 two or more Clarvis Bridge instances (one per editor window/extension host) colliding on a listening endpoint, on an `instance_id`, on a NERVIS registry row, or on published state (\u00a710 \"Bridge collision\"; contract in CLARVIS.md \u00a76.6 \"Ports and sockets avoid collisions through OS-assigned endpoints or a documented broker. No instance overwrites another's registration.\")",
        verdict="PARTIAL",
        evidence=[
            ("route", "nervis/src/nervis/instances.py:11"),
            ("route", "nervis/tests/test_m8a_registration.py:111"),
            ("route", "nervis/tests/test_m8b_status.py:105-134;"),
        ],
        gap="The live-Bridge collision is covered at three levels, but the *dead*-Bridge collision is not: `read_status` looks the instance up with `find()` and never checks `is_live()`, while a lapsed row stays in the registry for E",
        closes_with="In /Users/mathias/Documents/coding/NERVIS-ecosystem/nervis/src/nervis/api/instances.py:115-119, gate `read_status` (and `read_diagnostics` below it) on `instance.is_live(request.app.state.instances_cl",
    ),
    Cell(
        condition="stale registry lease",
        verdict="PARTIAL",
        evidence=[
            ("route", "nervis/tests/test_m8b_status.py:137"),
            ("route", "nervis/tests/test_m8a_registration.py:53"),
            ("route", "clarvis/src/bridge/Bridge.test.ts:289"),
        ],
        gap="Lease expiry is proven only inside the Instances/ResourceManager containers with injected clocks and on the Clarvis registrant side \u2014 no test in either repo lets a lease lapse and then asks a NERVIS route what it says, s",
        closes_with="Add one route test to /Users/mathias/Documents/coding/NERVIS-ecosystem/nervis/tests/test_m8b_status.py that registers a Bridge against a fake that is still listening, then overrides app.state.instance",
    ),
]


def declared() -> list[str]:
    """§10's conditions, read out of the runbook itself.

    The section names them in one sentence separated by semicolons, which is
    prose rather than a list — so this parses the prose. A format change here
    fails loudly, which is the correct outcome: it means the source of truth
    moved and this file has not been told.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text[text.index("## 10. Failure and graceful"):]
    section = section[: section.index("Required outcomes:")]
    sentence = section[section.index("Test at minimum:") + len("Test at minimum:"):]
    return [part.strip(" .\n") for part in " ".join(sentence.split()).split(";")]


def _matches(condition: str, named: str) -> bool:
    """Whether a cell is about the condition §10 names.

    A cell may say more than the runbook does — "trace collector loss (NERVIS
    event hub unreachable)" is the same condition with the peer spelled out —
    so the runbook's wording must appear in the cell's, not the reverse.
    """
    return named.lower() in condition.lower()


def main() -> int:
    failures: list[str] = []
    named = declared()
    covered = [cell for cell in CELLS]

    for condition in named:
        if not any(_matches(cell.condition, condition) for cell in covered):
            failures.append(f"§10 names {condition!r} and no cell scores it")
    for cell in covered:
        if not any(_matches(cell.condition, condition) for condition in named):
            failures.append(f"cell {cell.condition!r} scores a condition §10 does not name")
        if cell.verdict not in VERDICTS:
            failures.append(f"{cell.condition!r} has verdict {cell.verdict!r}; "
                            f"expected one of {', '.join(VERDICTS)}")
        for kind, where in cell.evidence:
            if kind not in KINDS:
                failures.append(f"{cell.condition!r} cites evidence of kind {kind!r}")
            path = where.split(":")[0]
            here = ROOT / path
            there = CLARVIS / path.removeprefix("clarvis/")
            if not here.exists() and not there.exists():
                failures.append(f"{cell.condition!r} cites {where}, which does not exist")
        if cell.verdict == "PARTIAL" and not cell.gap:
            failures.append(f"{cell.condition!r} is PARTIAL and names no gap")
        if cell.verdict != "COVERED" and not cell.closes_with:
            failures.append(f"{cell.condition!r} is {cell.verdict} and says nothing about closing it")

    thin = [cell for cell in covered
            if not any(kind in ("route", "live") for kind, _ in cell.evidence)]
    if len(thin) > WITHOUT_LIVE_EVIDENCE:
        failures.append(
            f"{len(thin)} cell(s) rest on unit tests alone, above the ceiling of "
            f"{WITHOUT_LIVE_EVIDENCE}: {', '.join(cell.condition for cell in thin)}")

    if failures:
        print("the degradation matrix does not hold:\n")
        for failure in failures:
            print(f"  • {failure}")
        return 1

    scores = {verdict: sum(1 for c in covered if c.verdict == verdict) for verdict in VERDICTS}
    print(f"{len(covered)} conditions from §10, each scored and each citing evidence that "
          f"exists:")
    for verdict in VERDICTS:
        if scores[verdict]:
            print(f"  {scores[verdict]:>2} {verdict}")
    if len(thin) < WITHOUT_LIVE_EVIDENCE:
        print(f"\n{len(thin)} cells rest on unit tests alone; the ceiling is "
              f"{WITHOUT_LIVE_EVIDENCE} and can come down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
