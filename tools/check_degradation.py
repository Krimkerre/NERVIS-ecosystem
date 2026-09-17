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

**A verdict alone was too coarse, and every cell was flattered by it.** COVERED
once meant "the condition is handled and the six outcomes hold under it", which
is a large claim to rest on a verdict — and a hand verification on 8 September
2026, one reader per cell reading the cited tests, found nearly every cell's
evidence proving one or two of the six. The condition was handled; the claim was
bigger than the proof.

So a cell now names the outcomes it *establishes* and states, in `residual`,
what is handled and unproved. COVERED means "these outcomes are proved and the
rest are named", never "nothing is left" — and the summary prints coverage per
outcome, which is the number that says whether §15's degradation item ("The
failure/degradation matrix passes with no unsafe failover") can be signed.
Reading it on 12 September 2026: all nineteen cells say COVERED, truthfulness
holds under 16 of the 19 conditions, and bounded queues under 6, the fewest of
the six — a fact about the matrix that no count of verdicts could have shown.

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

#: §10's six required outcomes, as ids a cell can name.
#:
#: **A verdict alone was too coarse, and it flattered every cell.** COVERED
#: asserted that all six hold under the condition, and a hand verification on
#: 8 September 2026 — one reader per cell, reading the cited tests — found that
#: nearly every cell's evidence proved one or two of them. The condition was
#: handled; the claim was broader than the proof. So a cell now names the
#: outcomes it actually establishes, and `residual` says what is handled but
#: unproved. COVERED means "these outcomes are proved and the rest are named",
#: not "nothing is left".
#:
#: Ordered as §10 lists them, and read from the runbook rather than trusted
#: here: `_required_outcomes` parses that list, and an outcome added there and
#: not here fails the gate the same way a condition does.
OUTCOMES = (
    ("truthful", "Capability and readiness become truthful"),
    ("no_unsafe_failover", "No automatic failover crosses"),
    ("bounded_retries", "Retriable operations are bounded"),
    ("bounded_queues", "Queues are bounded and observable"),
    ("idempotent_recovery", "Recovery is idempotent"),
    ("standalone", "Standalone product behaviour stays usable"),
)


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
    #: Which of §10's six outcomes this cell's evidence actually establishes.
    outcomes: tuple[str, ...] = ()
    #: What is handled but not proved, in the cell's own words. Required
    #: whenever `outcomes` is short of all six, because a cell that names two
    #: outcomes and says nothing about the other four reads as though the other
    #: four did not apply.
    residual: str = ""
    gap: str = ""
    closes_with: str = ""
    #: Outcomes that cannot arise under this condition, each with the reason (17 September 2026).
    #: "No automatic failover crosses a constraint" was counted under 7 of 19 conditions, and most
    #: of the other twelve cannot make anything fail over at all; a count that did not say so
    #: could not tell "unproved" from "impossible". A reason is required, and an outcome cannot be
    #: both established and impossible.
    cannot_arise: dict[str, str] = field(default_factory=dict)


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
        outcomes=(
            "truthful",
        ),
        residual=
            "Proved on NERVIS's trace surface only. Nothing exercises NERVIS's own wall clo"
            "ck stepping — leases and event retention both read it — and nothing exercises "
            "RAVIS's budget band, which derives its window from `time.time()`; a forward st"
            "ep there shortens the spend window and can re-admit paid providers the cost co"
            "nstraint had excluded. The behind-direction deadband is 120s, so a producer 3-"
            "119s slow is reported as nothing at all.",
        cannot_arise={"no_unsafe_failover": (
            "No request fails because a clock is wrong: skew moves timestamps and lease ages, and RAVIS's attempt chain only moves on when an upstream attempt fails."
        )},
    ),
    Cell(
        condition="unsupported major protocol version",
        verdict="COVERED",
        evidence=[
            ("unit", "nervis/src/nervis/probes.py:201"),
            ("unit", "nervis/src/nervis/negotiation.py:118"),
            ("route", "nervis/tests/test_m2_registry.py:328"),
            ("unit", "nervis/src/nervis/instances.py:174-183"),
            ("route", "nervis/tests/test_m8a_registration.py:108"),
            ("route", "nervis/tests/test_m8a_registration.py:127"),
        ],
        outcomes=(
            "truthful",
        ),
        residual=
            "Proved where a version is *presented*: a peer's probe and a Bridge's registrat"
            "ion claim. Nothing exercises a peer whose major changes mid-session, and nothi"
            "ng covers RAVIS reading SIRVIS evidence across an unsupported major.",
        cannot_arise={"no_unsafe_failover": (
            "A version refusal happens between services, never on a request's attempt chain: RAVIS reading an unreadable SIRVIS routes without its evidence, and NERVIS or a Bridge refuses the peer; nothing is re-sent elsewhere."
        )},
    ),
    Cell(
        condition="crash and restart mid-operation",
        verdict="COVERED",
        evidence=[
            ("live", "tools/acceptance_run.py:crash_clause"),
            ("live", "tools/acceptance_run.py:_reconciled"),
            ("live", "tools/failure_rehearsal.py:crash_and_restart"),
            ("unit", "sirvis/tests/test_m6_storage.py:test_a_run_starts_recorded_so_a_crash_leaves_evidence_of_it"),
            ("route", "sirvis/tests/test_m14_queue.py:test_a_job_interrupted_by_a_restart_is_not_quietly_run_again"),
        ],
        outcomes=(
            "truthful",
            "idempotent_recovery",
            "bounded_retries",
            "standalone",
        ),
        residual=
            "Proved for SIRVIS, live, with a real SIGKILL during a benchmark: the run is "
            "reconciled and the job ends rather than hanging — and, across two applications "
            "over one database file, that the next process does not quietly run the "
            "interrupted job again, which would spend the machine on work nobody was told "
            "had restarted. **RAVIS, NERVIS and code-server were killed outright on 17 September "
            "2026** (`tools/failure_rehearsal.py`), each while the stack was otherwise at work: "
            "NERVIS reported RAVIS and code-server unreachable after 20 s, the other services "
            "kept answering their own reads throughout, the launcher brought each back, the "
            "killed service's database passed SQLite's integrity check and NERVIS's stored "
            "events did not shrink. Not shown: RAVIS or NERVIS killed with a request of their "
            "own in flight — nothing was in flight, by the tool's own precondition.",
        cannot_arise={"no_unsafe_failover": (
            'A service that dies takes its in-flight requests with it: RAVIS keeps no queue (`load.queue` in its health says so) and holds nothing to re-send after a restart, so nothing can be moved to another model.'
        )},
    ),
    Cell(
        condition="corrupt response",
        verdict="COVERED",
        evidence=[
            ("route", "ravis/tests/test_transparent_proxy.py:test_an_unparseable_200_is_forwarded_rather_than_rewritten"),
            ("route", "ravis/tests/test_transparent_proxy.py:test_a_corrupt_answer_does_not_take_the_route_down_with_it"),
            ("route", "ravis/tests/test_transparent_proxy.py:test_a_corrupt_streamed_frame_is_passed_through_and_the_stream_ends"),
            ("route", "ravis/tests/test_anthropic_adapter.py:test_a_corrupt_provider_body_is_a_refusal_rather_than_a_crash"),
            ("route", "ravis/tests/test_fallback.py:test_an_unrecognisable_refusal_stops_the_chain_rather_than_shopping_around"),
        ],
        outcomes=(
            "no_unsafe_failover",
            "standalone",
            "bounded_retries",
        ),
        residual=
            "A corrupt body is forwarded rather than rewritten, the process serves the "
            "next request, and a body whose words match no marker table stops the chain at "
            "one attempt rather than shopping the same request around for a provider that "
            "says yes. Capability and readiness are not shown to become truthful under it "
            "— a provider answering garbage still reads as reachable, which is a reading "
            "nobody has corrected.",
    ),
    Cell(
        condition="duplicate and out-of-order events",
        verdict="COVERED",
        evidence=[
            ("route", "nervis/tests/test_m7_traces.py:test_a_trace_assembles_the_same_way_whatever_order_it_arrived_in"),
            ("route", "nervis/tests/test_m7_traces.py:test_the_same_batch_twice_does_not_double_the_trace"),
            ("route", "nervis/tests/test_m7_traces.py:test_a_replay_out_of_order_is_still_one_trace"),
            ("route", "nervis/tests/test_m6_events.py:test_a_replaying_producer_cannot_grow_the_hub_without_bound"),
            ("unit", "nervis/tests/test_m6_events.py:test_retention_drops_by_age_and_by_count"),
        ],
        outcomes=(
            "idempotent_recovery",
            "bounded_queues",
        ),
        residual=
            "Proved for transport duplicates — the identical envelope re-POSTed, deduped "
            "on `event_id` — and for the storage a replaying producer would otherwise "
            "grow without bound. A producer that rebuilds the same logical event with a "
            "fresh id is not deduped and nothing tests that, which is the shape a "
            "retrying publisher actually produces.",
        cannot_arise={"no_unsafe_failover": (
            "Events are telemetry to NERVIS's hub; no routing decision reads them, so no duplicate or reordering can move a request."
        )},
    ),
    Cell(
        condition="each service absent at startup",
        verdict="COVERED",
        evidence=[
            ("live", "tools/acceptance_run.py:cold_start_clause"),
            ("live", "tools/failure_rehearsal.py:absent_at_startup"),
            ("unit", "ravis/tests/test_capability_filtering.py:test_local_only_fails_closed_rather_than_reaching_for_cloud"),
            ("unit", "ravis/tests/test_policy.py:test_a_no_route_is_never_quietly_substituted"),
            ("unit", "nervis/tests/test_m2_registry.py:test_a_peer_that_never_answers_is_bounded_by_the_probe_deadline"),
            ("unit", "nervis/tests/test_m2_registry.py:test_a_peer_that_is_simply_absent_costs_one_request_a_pass"),
            ("unit", "protocol/tests/test_event_publisher.py:test_the_buffer_is_bounded_and_a_drop_is_counted"),
            ("unit", "protocol/tests/test_event_publisher.py:test_a_drop_is_reported_without_touching_the_product_s_health"),
        ],
        outcomes=(
            "truthful",
            "standalone",
            "bounded_queues",
            "bounded_retries",
            "no_unsafe_failover",
        ),
        residual=
            "Proved live for one pair — NERVIS started with SIRVIS absent — at the "
            "publisher, which buffers to a bound and counts what it drops while its "
            "collector has never answered, and at the probe, where a peer that is simply "
            "not there costs one connection attempt a pass rather than the four reads a "
            "full pass makes, however long it has been absent. **Every starting order since "
            "17 September 2026** (`tools/failure_rehearsal.py`): with SIRVIS, RAVIS or NERVIS "
            "held off its port, the launcher named it not ready and the rest ready, the rest "
            "answered their own reads, NERVIS reported the missing one unreachable (31 s for a "
            "port that times out, 22 s for RAVIS, inside a probe interval plus deadline), and "
            "each came back healthy in NERVIS within 20 s of being let start. A local runtime "
            "absent leaves only hosted candidates, and a LOCAL_ONLY request is then refused rather "
            "than answered from the cloud, never quietly substituted — proved at the router, not "
            "yet with a runtime actually stopped. Not shown: queues under that condition beyond "
            "the publisher's.",
    ),
    Cell(
        condition="timeout",
        verdict="COVERED",
        evidence=[
            ("route", "ravis/tests/test_fallback.py:test_a_timeout_moves_on_rather_than_asking_the_same_model_twice"),
            ("route", "ravis/tests/test_fallback.py:test_a_chain_that_times_out_everywhere_says_so_per_model"),
            ("route", "ravis/tests/test_fallback.py:test_a_directly_named_model_that_times_out_is_not_replaced"),
            ("route", "ravis/tests/test_fallback.py:test_a_local_model_that_hangs_is_not_replaced_by_a_cloud_one"),
            ("route", "ravis/tests/test_fallback.py:test_the_cloud_model_would_otherwise_have_answered"),
            ("route", "ravis/tests/test_management_api.py:test_the_decision_log_is_bounded_and_says_where_it_starts"),
            ("route", "ravis/tests/test_management_api.py:test_a_decision_pushed_out_by_the_bound_reads_as_aged_out"),
        ],
        outcomes=(
            "bounded_retries",
            "idempotent_recovery",
            "bounded_queues",
            "no_unsafe_failover",
        ),
        residual=
            "A timed-out target is asked once and the chain moves on, per model; a model "
            "the caller named is not replaced; a local model that hangs is not answered "
            "from the cloud, with the control beside it showing that same cloud model "
            "answering when no pool forbids it; and the decision log an operator reads "
            "under load is bounded and says so when an id has aged out. What is not shown "
            "is a service's own routes staying usable while a timeout is in flight.",
    ),
    Cell(
        condition="slow response",
        verdict="COVERED",
        evidence=[
            ("unit", "nervis/tests/test_m2_registry.py:test_a_peer_that_never_answers_is_bounded_by_the_probe_deadline"),
            ("unit", "nervis/tests/test_m2_registry.py:test_a_slow_peer_is_reported_as_unreachable_rather_than_healthy"),
            ("unit", "nervis/tests/test_m2_registry.py:test_a_runtime_without_a_mep_surface_is_bounded_the_same_way"),
            ("route", "nervis/tests/test_m2_registry.py:test_the_services_route_still_answers_while_a_peer_is_slow"),
            ("route", "nervis/tests/test_m6_events.py:test_ingestion_keeps_answering_while_a_subscriber_is_full"),
            ("unit", "nervis/tests/test_m6_events.py:test_a_subscriber_that_falls_behind_is_dropped_not_waited_for"),
        ],
        outcomes=(
            "truthful",
            "bounded_retries",
            "bounded_queues",
        ),
        residual=
            "The probe's deadline is asserted on both branches, the services listing "
            "answers while every peer is quiet, and a reader that stops reading fills its "
            "buffer without holding up ingestion. What is not shown is a *partially* slow "
            "world — one peer slow, the rest healthy — or that a slow peer cannot delay "
            "another peer's row.",
        cannot_arise={"no_unsafe_failover": (
            'A reply that is slow but inside its deadline changes nothing; past the deadline it is the timeout condition, which establishes this outcome.'
        )},
    ),
    Cell(
        condition="full disk",
        verdict="COVERED",
        evidence=[
            ("unit", "sirvis/tests/test_m6_benchmark.py:test_a_disk_that_fills_mid_run_ends_the_run_rather_than_leaving_it_running"),
            ("unit", "sirvis/tests/test_m6_benchmark.py:test_a_full_disk_still_releases_the_model_it_held"),
            ("route", "sirvis/tests/test_m6_api.py:test_a_full_disk_does_not_stop_the_service"),
            ("unit", "sirvis/tests/test_m6_storage.py:test_a_write_that_runs_out_of_room_says_so_rather_than_returning"),
        ],
        outcomes=(
            "truthful",
            "idempotent_recovery",
            "standalone",
        ),
        residual=
            "A run that cannot write ends as failed rather than staying `running`, the "
            "lease is released, and the service keeps answering while every results write "
            "raises `ENOSPC`. No admission check considers free space, so a run that cannot "
            "possibly finish still starts; and the release is proved by a patch applied "
            "after `_execute` has already released it, which is weaker evidence than it "
            "reads.",
        cannot_arise={"no_unsafe_failover": (
            "RAVIS writes usage only after an attempt has succeeded (`call.note_usage` follows `chain.succeeded`), so a failed write cannot become a failed attempt and move the chain on; SIRVIS's and NERVIS's writes are not on a request's route at all."
        )},
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
            ("unit", "ravis/tests/test_translated_in_pools.py:test_a_provider_with_no_credential_contributes_nothing"),
            ("unit", "ravis/tests/test_routing.py:test_a_direct_address_to_a_missing_model_is_a_no_route"),
            ("unit", "ravis/tests/test_policy.py:test_a_no_route_is_never_quietly_substituted"),
        ],
        outcomes=(
            "truthful",
            "standalone",
            "no_unsafe_failover",
        ),
        residual=
            "Six of the seven citations are unit-level, and deliberately: the four ways a l"
            "ookup fails are branches of one function. The route entry shows the providers "
            "listing still answers, but it can pass without entering the Keychain branch at"
            " all. Nothing shows what a *provider* does when its credential is unreachable "
            "rather than absent. What stands in for it: an unreadable keychain is read as no "
            "stored key, a provider with no usable key offers no models and is not even asked "
            "for them, a request naming one of its models is then a no-route, and a pool is "
            "routed only within its policy — never quietly substituted. A transparent upstream "
            "whose key vanishes answers 401, which the expired-credential cell covers.",
    ),
    Cell(
        condition="trace collector loss (NERVIS event hub unreachable, refusing, hanging, or never configured, from the point of view of every producer that publishes events to it)",
        verdict="COVERED",
        evidence=[
            ("live", "STATUS.md:5392"),
            ("route", "ravis/tests/test_m18b_events.py:test_a_dead_collector_never_becomes_the_service_s_own_unreadiness"),
            ("route", "ravis/tests/test_m18b_events.py:test_a_collector_that_refuses_does_not_reach_the_caller"),
            ("route", "ravis/tests/test_m18b_events.py:test_routing_works_with_no_collector_configured"),
            ("unit", "protocol/tests/test_event_publisher.py:test_a_drop_is_reported_without_touching_the_product_s_health"),
            ("unit", "protocol/tests/test_event_publisher.py:test_a_5xx_is_retried_and_a_202_is_not"),
            ("unit", "protocol/tests/test_event_publisher.py:test_a_failed_flush_preserves_order"),
            ("unit", "protocol/tests/test_event_publisher.py:test_a_stable_id_makes_a_retry_one_event_rather_than_two"),
            ("static-gate", "tools/acceptance_run.py:89,743,795,806"),
        ],
        outcomes=(
            "bounded_queues",
            "standalone",
            "truthful",
            "idempotent_recovery",
        ),
        residual=
            "Proved where it matters most: a dead collector never becomes the producer's "
            "own unreadiness — read back off `/ecosystem/health` after a real overflow, not "
            "argued from a comment — the publisher's queue is bounded, what it dropped is "
            "counted and reportable rather than silently gone, and what survived is "
            "re-sent in order when the hub returns, under an id derived from the event "
            "rather than minted per attempt, so the retry is one event at the hub instead "
            "of two. The acceptance-run citation beside it is a clause label rather than a "
            "check of collector loss, and is kept only as a pointer.",
        cannot_arise={"no_unsafe_failover": (
            "Publishing to NERVIS is fire-and-forget on its own task and never on a request's route, so a lost collector cannot fail an attempt."
        )},
    ),
    Cell(
        condition="read-only data directory",
        verdict="COVERED",
        evidence=[
            ("manual", "sirvis/tests/test_doctor.py:test_doctor_says_a_read_only_results_directory_is_not_writable"),
            ("manual", "sirvis/tests/test_doctor.py:test_a_writable_directory_is_not_labelled_unusable"),
            ("route", "sirvis/tests/test_m6_api.py:test_a_read_only_results_directory_does_not_stop_the_service"),
        ],
        outcomes=(
            "truthful",
            "standalone",
        ),
        residual=
            "`doctor` says a results directory is not writable and refuses to run as root "
            "where the permission bits would not apply, and the service itself keeps "
            "answering with its results directory read-only. What a *write* does under the "
            "condition is proved for the disk being full rather than for the directory "
            "being read-only, which are two different `OSError`s reaching the same code.",
        cannot_arise={"no_unsafe_failover": (
            'The same as a full disk: a refused write reaches no attempt chain, because RAVIS writes usage only after an attempt has succeeded.'
        )},
    ),
    Cell(
        condition="cloud provider 401/403/429/5xx",
        verdict="COVERED",
        evidence=[
            ("route", "ravis/tests/test_fallback.py:test_a_credential_failure_on_a_named_model_reaches_the_client"),
            ("route", "ravis/tests/test_fallback.py:test_a_pool_treats_one_providers_bad_key_as_evidence_about_that_provider"),
            ("route", "ravis/tests/test_fallback.py:test_a_bare_500_is_not_chased_across_the_pool"),
            ("route", "ravis/tests/test_fallback.py:test_an_exhausted_chain_returns_the_last_upstreams_own_status"),
            ("route", "ravis/tests/test_fallback.py:test_the_retry_budget_caps_how_many_models_are_tried"),
            ("route", "ravis/tests/test_fallback.py:test_a_credential_failure_is_visible_on_the_health_surface"),
        ],
        outcomes=(
            "no_unsafe_failover",
            "bounded_retries",
            "truthful",
        ),
        residual=
            "A credential failure reaches the client on a named model and is final "
            "regardless of what the body says, a bare 500 is not chased, a pool of "
            "candidates all answering 503 stops at the configured attempt count with an "
            "eligible model still untried, and the health surface a person actually reads "
            "shows the refused model as never having worked while leaving both breakers "
            "shut — a wrong key is configuration, not an outage to route around. What is "
            "not shown is the jitter §10 asks for: the 429 evidence proves status "
            "pass-through rather than a bounded, jittered retry.",
    ),
    Cell(
        condition="network loss",
        verdict="COVERED",
        evidence=[
            ("live", "tools/acceptance_run.py:796"),
            ("route", "ravis/src/ravis/api/openai/chat.py:1411"),
            ("route", "ravis/tests/test_fallback.py:test_a_connection_that_never_completes_still_falls_back"),
            ("route", "ravis/tests/test_fallback.py:test_a_connection_that_never_completes_still_falls_back_while_streaming"),
            ("route", "ravis/tests/test_fallback.py:test_a_world_where_nothing_connects_stops_at_the_budget_not_at_the_pool"),
            ("route", "ravis/tests/test_fallback.py:test_a_failing_local_model_is_not_replaced_by_a_cloud_one"),
            ("route", "ravis/tests/test_fallback.py:test_a_target_that_never_connected_says_so_rather_than_reading_untried"),
            ("unit", "ravis/tests/test_reliability.py:test_the_budget_stops_the_chain_on_attempts"),
            ("unit", "protocol/tests/test_event_publisher.py:test_the_drain_is_bounded_when_the_collector_hangs"),
            ("unit", "protocol/tests/test_event_publisher.py:test_the_oldest_is_dropped_not_the_newest"),
        ],
        outcomes=(
            "no_unsafe_failover",
            "idempotent_recovery",
            "bounded_queues",
            "bounded_retries",
            "truthful",
        ),
        residual=
            "A connection that never opened falls through to the next candidate, a "
            "failure after the request left is no longer re-sent to the same target, the "
            "publisher's buffer stays bounded while the collector is unreachable, dropping "
            "oldest-first, an unreachable local model is refused rather than answered "
            "from the cloud, and a world where *nothing* connects stops at the retry budget "
            "rather than walking the pool — the same-target retry a connection failure buys "
            "is counted against that budget like any other attempt — and the target that "
            "never connected reads as failed for that reason rather than as untried, with "
            "no invented latency for a connection that never opened. Loss *mid-stream* on "
            "the transparent path is not exercised at the route, and no test drops a "
            "connection between two services.",
    ),
    Cell(
        condition="hung local runtime",
        verdict="COVERED",
        evidence=[
            ("route", "sirvis/tests/test_m8_resources.py:test_a_stalled_runtime_is_reported_busy_rather_than_absent"),
            ("route", "sirvis/tests/test_m8_resources.py:test_a_stalled_stream_is_a_timeout_too"),
            ("route", "sirvis/tests/test_m8_resources.py:test_a_stalled_runtime_leaves_the_service_answering"),
            ("route", "ravis/tests/test_fallback.py:test_a_runtime_that_accepts_and_never_answers_is_not_asked_forever"),
            ("unit", "ravis/tests/test_reliability.py:test_the_budget_stops_the_chain_on_elapsed_time"),
            ("route", "ravis/tests/test_fallback.py:test_a_local_model_that_hangs_is_not_replaced_by_a_cloud_one"),
        ],
        outcomes=(
            "truthful",
            "standalone",
            "bounded_retries",
            "no_unsafe_failover",
        ),
        residual=
            "A stalled runtime is reported busy rather than absent, on both the request "
            "and streaming paths, SIRVIS keeps answering, and on RAVIS's side of the same "
            "socket a runtime that accepts and never answers is asked to the budget and no "
            "further — a timeout buys no same-target retry, so the third eligible candidate "
            "is never reached — and a hung *local* model is refused rather than answered "
            "from a cloud one that is configured, eligible and reachable. Two of the SIRVIS "
            "citations are adapter-level; its route "
            "entry shows the listing answers but not that a *benchmark* against a hung "
            "runtime ends, and the elapsed-time ceiling is proved on the budget itself "
            "rather than through a route.",
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
        outcomes=(
            "truthful",
            "no_unsafe_failover",
        ),
        residual=
            "A refused key is its own state and reaches the providers listing, and the fail"
            "ure class forbids falling back on a named model. The pool path deliberately do"
            "es fall back, which is defensible and is not the same claim; nothing proves a "
            "rotation is noticed without a restart.",
    ),
    Cell(
        condition="code-server loss \u2014 the browser VS Code that hosts Clarvis and that NERVIS embeds in its Clarvis/Code tab stops answering after having answered (crash, stopped, port taken)",
        verdict="COVERED",
        evidence=[
            ("unit", "nervis/tests/test_m2_registry.py:test_a_code_server_that_stops_answering_says_so_in_its_state"),
            ("unit", "nervis/tests/test_m2_registry.py:test_a_code_server_that_stopped_is_not_reported_as_stopped"),
            ("route", "nervis/tests/test_m2_registry.py:test_the_services_route_reports_code_server_as_unreachable"),
            ("static-gate", "nervis/tools/editor_check.js"),
            ("live", "tools/failure_rehearsal.py:crash_and_restart"),
        ],
        outcomes=(
            "truthful",
            "standalone",
        ),
        residual=
            "The registry state changes, the services listing publishes it, and the editor "
            "tab refuses to frame an editor that is not answering — live since 17 September "
            "2026, when code-server was killed and NERVIS reported it unreachable after 20 s "
            "while SIRVIS, RAVIS and NERVIS kept answering. The derived capability d"
            "eliberately survives the loss, so anything gating on the capability rather tha"
            "n the state would still be wrong.",
        cannot_arise={"no_unsafe_failover": (
            'code-server hosts the editor; no request is routed through it and nothing chooses another editor when it goes.'
        )},
    ),
    Cell(
        condition="Bridge collision \u2014 two or more Clarvis Bridge instances (one per editor window/extension host) colliding on a listening endpoint, on an `instance_id`, on a NERVIS registry row, or on published state (\u00a710 \"Bridge collision\"; contract in CLARVIS.md \u00a76.6 \"Ports and sockets avoid collisions through OS-assigned endpoints or a documented broker. No instance overwrites another's registration.\")",
        verdict="COVERED",
        evidence=[
            ("route", "nervis/tests/test_m8b_status.py:test_a_window_whose_lease_lapsed_is_gone_rather_than_probed"),
            ("route", "nervis/tests/test_m8b_status.py:test_a_lapsed_window_is_gone_from_diagnostics_and_config_too"),
            ("route", "nervis/tests/test_m8b_status.py:test_a_window_still_answering_is_not_displaced_by_a_second_claim"),
        ],
        outcomes=(
            "truthful",
            "no_unsafe_failover",
            "idempotent_recovery",
        ),
        residual=
            "Proved for the dead-Bridge half — a lapsed window is gone rather than probed "
            "— for the isolation half, since an event claiming a registered window must now "
            "present that window's token, and for the collision itself at the registry: a "
            "second claim on an id that is still answering is refused with a 409 and the "
            "window holding it keeps its lease, so the recovery path a restarted Bridge "
            "takes cannot be turned against a live one. Two live windows contesting one "
            "*port* are not exercised here; that half is covered by Clarvis's own suite "
            "rather than by this matrix.",
    ),
    Cell(
        condition="stale registry lease",
        verdict="COVERED",
        evidence=[
            ("route", "nervis/tests/test_m8b_status.py:test_a_window_whose_lease_lapsed_is_gone_rather_than_probed"),
            ("route", "nervis/tests/test_m8b_status.py:test_a_lease_still_inside_its_window_is_read_normally"),
            ("route", "nervis/tests/test_m8b_status.py:test_a_renewed_lease_brings_the_window_back"),
            ("route", "nervis/tests/test_m8b_status.py:test_a_window_that_comes_back_re_registers_into_the_same_row"),
            ("route", "nervis/tests/test_m8b_status.py:test_a_window_still_answering_is_not_displaced_by_a_second_claim"),
        ],
        outcomes=(
            "truthful",
            "idempotent_recovery",
        ),
        residual=
            "Proved for the Clarvis instance lease at the route, in both directions — "
            "lapsed is a 404, a heartbeat brings the window back — and for the recovery a "
            "Bridge that lost its token actually takes: registering the same id again "
            "leaves one row rather than a row per restart, re-arms the lease under a new "
            "token, retires the old one, and is refused outright while the id is still "
            "answering. The §5.1 *service* registry keeps its own staleness window and no "
            "cell cites it.",
        cannot_arise={"no_unsafe_failover": (
            "A lapsed lease changes what NERVIS lists and probes; NERVIS routes no requests, and Clarvis's never pass through its registry."
        )},
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


def _scope_failures(cell: Cell) -> list[str]:
    """Whether a cell's claim is the size of its evidence.

    Three rules, each from a way the matrix was found flattering itself on
    8 September 2026:

    - An outcome it does not name is one it does not claim, so the names have
      to be real ones.
    - A cell claiming every outcome and citing two tests is the original
      problem restated, so a short list must carry a `residual` saying what is
      handled and unproved. A cell naming all six needs none.
    - `route` means "the application answered" in this file's own words, and
      several cells used it for tests that call a helper directly. A route
      citation has to name a test file that actually drives a client.
    """
    problems = []
    known = {name for name, _ in OUTCOMES}
    for outcome in cell.outcomes:
        if outcome not in known:
            problems.append(f"{cell.condition!r} claims outcome {outcome!r}, which §10 does not list")
    for outcome, why in cell.cannot_arise.items():
        if outcome not in known:
            problems.append(f"{cell.condition!r} rules out {outcome!r}, which §10 does not list")
        if outcome in cell.outcomes:
            problems.append(f"{cell.condition!r} both establishes and rules out {outcome!r}")
        if len(why.split()) < 6:
            problems.append(f"{cell.condition!r} rules out {outcome!r} without saying why")
    if cell.verdict == "COVERED" and not cell.outcomes:
        problems.append(f"{cell.condition!r} is COVERED and names no outcome it establishes")
    if cell.outcomes and len(cell.outcomes) + len(cell.cannot_arise) < len(OUTCOMES) \
            and not cell.residual:
        problems.append(f"{cell.condition!r} establishes {len(cell.outcomes)} of "
                        f"{len(OUTCOMES)} outcomes and says nothing about the rest")
    for kind, where in cell.evidence:
        if kind != "route":
            continue
        path = where.partition(":")[0]
        found = ROOT / path if (ROOT / path).exists() else CLARVIS / path.removeprefix("clarvis/")
        if not found.exists():
            continue
        body = found.read_text(encoding="utf-8", errors="ignore")
        if "TestClient" not in body and "client." not in body and "api." not in body:
            problems.append(f"{cell.condition!r} cites {where} as route evidence, and nothing in "
                            "that file drives a client — 'route' means the application answered")
    return problems


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
        failures.extend(_scope_failures(cell))

    for outcome, _ in OUTCOMES:
        if not any(outcome in cell.outcomes for cell in covered):
            failures.append(f"no cell establishes §10's {outcome!r} outcome under any condition")

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
    print()
    for outcome, sentence in OUTCOMES:
        holding = [cell for cell in covered if outcome in cell.outcomes]
        possible = [cell for cell in covered if outcome not in cell.cannot_arise]
        ruled_out = len(covered) - len(possible)
        print(f"  {len(holding):>2}/{len(possible)} conditions establish  {sentence}…"
              + (f"  ({ruled_out} where it cannot arise)" if ruled_out else ""))
    if len(thin) < WITHOUT_LIVE_EVIDENCE:
        print(f"\n{len(thin)} cells rest on unit tests alone; the ceiling is "
              f"{WITHOUT_LIVE_EVIDENCE} and can come down.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
