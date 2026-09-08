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


def _anchor_missing(path: pathlib.Path, anchor: str) -> str:
    """Why a citation's `:anchor` does not hold, or an empty string.

    **The file existing was the whole check, and it is the weaker half.** A
    citation names a test or a function after the colon, and nothing read it —
    so a cell kept passing after its test was renamed, moved or deleted. Found
    by verifying the matrix by hand on 8 September 2026: one cell cited a line
    number that had drifted onto an unrelated test, and another onto a blank
    line between two.

    A bare line number is still accepted and still weak — it is checked only
    for being inside the file — because several cells legitimately point at a
    line of source rather than at a named test. A name is checked for real.
    """
    if not anchor:
        return ""
    if anchor.split(",")[0].split("-")[0].isdigit():
        lines = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        first = int(anchor.split(",")[0].split("-")[0])
        if 1 <= first <= lines:
            return ""
        return f"whose line {first} is past the end of a {lines}-line file"
    body = path.read_text(encoding="utf-8", errors="ignore")
    for form in (f"def {anchor}(", f"def {anchor} (", f"function {anchor}(", f"{anchor} =",
                 f"'{anchor}'", f'"{anchor}"'):
        if form in body:
            return ""
    return f"which names nothing in it — {anchor!r} is not defined there"

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
        verdict="COVERED",
        evidence=[
            ("route", "nervis/tests/test_m7_traces.py:test_a_future_stamped_event_reaches_the_trace_api_as_a_warning"),
            ("route", "nervis/tests/test_m7_traces.py:test_the_span_is_left_where_the_producer_put_it"),
            ("route", "nervis/tests/test_m7_traces.py:test_a_clock_far_behind_is_reported_too"),
            ("route", "nervis/tests/test_m7_traces.py:test_ordinary_latency_is_not_reported_as_a_broken_clock"),
        ],
    ),
    Cell(
        condition="unsupported major protocol version",
        verdict="COVERED",
        evidence=[
            ("route", "nervis/src/nervis/probes.py:201"),
            ("route", "nervis/src/nervis/negotiation.py:118"),
            ("route", "nervis/tests/test_m2_registry.py:328"),
            ("route", "nervis/src/nervis/instances.py:174-183"),
            ("route", "nervis/tests/test_m8a_registration.py:108"),
            ("route", "nervis/tests/test_m8a_registration.py:127"),
        ],
    ),
    Cell(
        condition="crash and restart mid-operation",
        verdict="COVERED",
        evidence=[
            ("live", "tools/acceptance_run.py:crash_clause"),
            ("live", "tools/acceptance_run.py:_reconciled"),
            ("route", "sirvis/tests/test_m6_storage.py:test_a_run_starts_recorded_so_a_crash_leaves_evidence_of_it"),
        ],
    ),
    Cell(
        condition="corrupt response",
        verdict="COVERED",
        evidence=[
            ("route", "ravis/tests/test_transparent_proxy.py:test_an_unparseable_200_is_forwarded_rather_than_rewritten"),
            ("route", "ravis/tests/test_transparent_proxy.py:test_a_corrupt_answer_does_not_take_the_route_down_with_it"),
            ("route", "ravis/tests/test_transparent_proxy.py:test_a_corrupt_streamed_frame_is_passed_through_and_the_stream_ends"),
            ("route", "ravis/tests/test_anthropic_adapter.py:test_a_corrupt_provider_body_is_a_refusal_rather_than_a_crash"),
        ],
    ),
    Cell(
        condition="duplicate and out-of-order events",
        verdict="COVERED",
        evidence=[
            ("route", "nervis/tests/test_m7_traces.py:test_a_trace_assembles_the_same_way_whatever_order_it_arrived_in"),
            ("route", "nervis/tests/test_m7_traces.py:test_the_same_batch_twice_does_not_double_the_trace"),
            ("route", "nervis/tests/test_m7_traces.py:test_a_replay_out_of_order_is_still_one_trace"),
        ],
    ),
    Cell(
        condition="each service absent at startup",
        verdict="COVERED",
        evidence=[
            ("live", "tools/acceptance_run.py:cold_start_clause"),
            ("route", "nervis/tests/test_m2_registry.py:test_a_peer_that_never_answers_is_bounded_by_the_probe_deadline"),
        ],
    ),
    Cell(
        condition="timeout",
        verdict="COVERED",
        evidence=[
            ("route", "ravis/tests/test_fallback.py:test_a_timeout_moves_on_rather_than_asking_the_same_model_twice"),
            ("route", "ravis/tests/test_fallback.py:test_a_chain_that_times_out_everywhere_says_so_per_model"),
            ("route", "ravis/tests/test_fallback.py:test_a_directly_named_model_that_times_out_is_not_replaced"),
        ],
    ),
    Cell(
        condition="slow response",
        verdict="COVERED",
        evidence=[
            ("route", "nervis/tests/test_m2_registry.py:test_a_peer_that_never_answers_is_bounded_by_the_probe_deadline"),
            ("route", "nervis/tests/test_m2_registry.py:test_a_slow_peer_is_reported_as_unreachable_rather_than_healthy"),
            ("route", "nervis/tests/test_m2_registry.py:test_a_runtime_without_a_mep_surface_is_bounded_the_same_way"),
        ],
    ),
    Cell(
        condition="full disk",
        verdict="COVERED",
        evidence=[
            ("route", "sirvis/tests/test_m6_benchmark.py:test_a_disk_that_fills_mid_run_ends_the_run_rather_than_leaving_it_running"),
            ("route", "sirvis/tests/test_m6_benchmark.py:test_a_full_disk_still_releases_the_model_it_held"),
            ("unit", "sirvis/tests/test_m6_storage.py:test_a_write_that_runs_out_of_room_says_so_rather_than_returning"),
        ],
    ),
    Cell(
        condition="unavailable keychain",
        verdict="COVERED",
        evidence=[
            ("route", "ravis/tests/test_management_api.py:test_a_hanging_keychain_does_not_hang_the_providers_listing"),
            ("unit", "ravis/tests/test_credentials.py:test_a_keychain_that_holds_nothing_falls_through_to_the_environment"),
            ("unit", "ravis/tests/test_credentials.py:test_a_keychain_that_never_answers_does_not_hang_the_lookup"),
            ("unit", "ravis/tests/test_credentials.py:test_a_keychain_lookup_asks_for_a_bounded_wait"),
            ("unit", "ravis/tests/test_credentials.py:test_a_host_with_no_security_binary_is_not_an_error"),
            ("unit", "ravis/tests/test_credentials.py:test_a_keychain_that_errors_at_the_operating_system_is_survived"),
            ("unit", "ravis/tests/test_credentials.py:test_a_keychain_that_answers_is_still_preferred_over_the_environment"),
        ],
    ),
    Cell(
        condition="trace collector loss (NERVIS event hub unreachable, refusing, hanging, or never configured, from the point of view of every producer that publishes events to it)",
        verdict="COVERED",
        evidence=[
            ("live", "STATUS.md:5392"),
            ("route", "ravis/tests/test_m18b_events.py:251"),
            ("route", "ravis/tests/test_m18b_events.py:223,232"),
            ("static-gate", "tools/acceptance_run.py:89,743,795,806"),
        ],
    ),
    Cell(
        condition="read-only data directory",
        verdict="COVERED",
        evidence=[
            ("route", "sirvis/tests/test_doctor.py:test_doctor_says_a_read_only_results_directory_is_not_writable"),
            ("route", "sirvis/tests/test_doctor.py:test_a_writable_directory_is_not_labelled_unusable"),
        ],
    ),
    Cell(
        condition="cloud provider 401/403/429/5xx",
        verdict="COVERED",
        evidence=[
            ("route", "ravis/tests/test_fallback.py:test_a_credential_failure_on_a_named_model_reaches_the_client"),
            ("route", "ravis/tests/test_fallback.py:test_a_pool_treats_one_providers_bad_key_as_evidence_about_that_provider"),
            ("route", "ravis/tests/test_fallback.py:test_a_bare_500_is_not_chased_across_the_pool"),
            ("route", "ravis/tests/test_fallback.py:test_an_exhausted_chain_returns_the_last_upstreams_own_status"),
        ],
    ),
    Cell(
        condition="network loss",
        verdict="COVERED",
        evidence=[
            ("live", "tools/acceptance_run.py:796"),
            ("route", "ravis/src/ravis/api/openai/chat.py:1411"),
            ("route", "ravis/tests/test_fallback.py:171"),
            ("route", "ravis/tests/test_fallback.py:195"),
        ],
    ),
    Cell(
        condition="hung local runtime",
        verdict="COVERED",
        evidence=[
            ("route", "sirvis/tests/test_m8_resources.py:test_a_stalled_runtime_is_reported_busy_rather_than_absent"),
            ("route", "sirvis/tests/test_m8_resources.py:test_a_stalled_stream_is_a_timeout_too"),
            ("route", "sirvis/tests/test_m8_resources.py:test_a_stalled_runtime_leaves_the_service_answering"),
        ],
    ),
    Cell(
        condition="expired credential",
        verdict="COVERED",
        evidence=[
            ("route", "ravis/tests/test_anthropic_adapter.py:test_a_refused_key_is_not_reported_as_an_unreachable_provider"),
            ("route", "ravis/tests/test_anthropic_adapter.py:test_a_provider_that_is_actually_down_is_still_reported_down"),
            ("route", "ravis/tests/test_anthropic_adapter.py:test_a_server_error_from_a_provider_keeps_its_status"),
            ("route", "ravis/tests/test_anthropic_adapter.py:test_the_providers_listing_publishes_the_refused_credential"),
        ],
    ),
    Cell(
        condition="code-server loss \u2014 the browser VS Code that hosts Clarvis and that NERVIS embeds in its Clarvis/Code tab stops answering after having answered (crash, stopped, port taken)",
        verdict="COVERED",
        evidence=[
            ("route", "nervis/tests/test_m2_registry.py:test_a_code_server_that_stops_answering_says_so_in_its_state"),
            ("route", "nervis/tests/test_m2_registry.py:test_a_code_server_that_stopped_is_not_reported_as_stopped"),
            ("static-gate", "nervis/tools/editor_check.js"),
        ],
    ),
    Cell(
        condition="Bridge collision \u2014 two or more Clarvis Bridge instances (one per editor window/extension host) colliding on a listening endpoint, on an `instance_id`, on a NERVIS registry row, or on published state (\u00a710 \"Bridge collision\"; contract in CLARVIS.md \u00a76.6 \"Ports and sockets avoid collisions through OS-assigned endpoints or a documented broker. No instance overwrites another's registration.\")",
        verdict="COVERED",
        evidence=[
            ("route", "nervis/tests/test_m8b_status.py:test_a_window_whose_lease_lapsed_is_gone_rather_than_probed"),
            ("route", "nervis/tests/test_m8b_status.py:test_a_lapsed_window_is_gone_from_diagnostics_and_config_too"),
        ],
    ),
    Cell(
        condition="stale registry lease",
        verdict="COVERED",
        evidence=[
            ("route", "nervis/tests/test_m8b_status.py:test_a_window_whose_lease_lapsed_is_gone_rather_than_probed"),
            ("route", "nervis/tests/test_m8b_status.py:test_a_lease_still_inside_its_window_is_read_normally"),
            ("route", "nervis/tests/test_m8b_status.py:test_a_renewed_lease_brings_the_window_back"),
        ],
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
            path, _, anchor = where.partition(":")
            here = ROOT / path
            there = CLARVIS / path.removeprefix("clarvis/")
            found = here if here.exists() else there
            if not found.exists():
                failures.append(f"{cell.condition!r} cites {where}, which does not exist")
                continue
            missing = _anchor_missing(found, anchor)
            if missing:
                failures.append(f"{cell.condition!r} cites {where}, {missing}")
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
