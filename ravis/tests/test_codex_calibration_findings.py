"""Cal-2: calibration made to match what the real run found and what the owner decided (§10.4).

Every run here is against the fake app-server's calibration half — never a real Codex, never an
outside host. The rules held:

- **K3 proves the approved-sites allowlist**: a listed site answers; an unlisted one is refused with
  the proxy's fixed line, which RAVIS's detection names; adding it the way the owner's "allow" does,
  while the turn runs, reaches the task — in the running turn, its next turn, the thread reopened
  from disk, or a copy of it under the same profile (Cal-5); loopback and a never-added site stay
  refused; and
  the site list is written back afterwards. Codex reporting the add overridden fails K3, and so does
  a launch that carries a site list again (Cal-3): the fake then overrides every site write, as
  Codex did in run `cal_330b7525d115`. (Each of (a)–(e) broken alone is in
  `test_codex_calibration.py`'s fault table.)
- **K6 runs one command per project** whose long-runners all exist at once (Cal-3), waits for the
  processes that command starts, and never for more; a start-up helper that isn't one of its
  long-runners is neither counted nor judged (Cal-4).
- **K7 passes only when nothing is left open**: Codex doesn't resolve an interrupted turn's request,
  so RAVIS answers it with cancel when the turn ends; left unanswered, K7 fails.
- **K8 records a Codex that never asks for permissions**, and that doesn't keep a full run from
  proving the rules; an inconclusive or failed K8 still does.
- **The candidate profile** is the syntax Codex 0.154.0 accepted, with R3's sites.
"""

from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from tests.codex_rig import eventually
from tests.test_codex_calibration import calibrating, finished, start, summary

from ravis.agent.calibration_dependent import DEFAULT_ALLOWED_SITES, network_profile_flags
from ravis.codex.calibration.harness import ScenarioResult, Session
from ravis.codex.calibration.outputs import decide
from ravis.codex.calibration.plan import CANDIDATE_PROFILE, SCENARIOS, checked_profile
from ravis.codex.calibration.scenarios_network import LISTED_SITE
from ravis.codex.calibration.scenarios_turns import (
    K6_COMMANDS,
    K6_PROCESSES_PER_PROJECT,
    long_runners,
)
from ravis.codex.process_table import Attributed, ProcessRow
from ravis.codex.service import CodexService

SITES_KEY = "permissions.clarvis_run.network.domains"
DEFAULTS = {site: "allow" for site in DEFAULT_ALLOWED_SITES}
#: The launch flag run 5 started Codex with, in spirit: the network section with a site list.
SITES_AT_LAUNCH = ("-c", 'permissions.clarvis_run.network={enabled=true, mode="limited", '
                         'domains={"registry.npmjs.org"="allow"}}')


def only(view: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The one scenario a run asked: its view, and its findings from the summary."""
    [scenario] = view["scenarios"]
    return scenario, summary(view)["scenarios"][0]["findings"]


# ── K3: the approved-sites allowlist ────────────────────────────────────────


def test_k3_proves_the_allowlist_and_a_site_added_while_the_task_runs(tmp_path: Path) -> None:
    # The fake keeps a loaded thread's sites, as Codex 0.154.0 did in `cal_ed672bf12c6f` and
    # `cal_85aa0ece0f52`: the site answers once the thread is reopened from disk (Cal-5).
    with calibrating(tmp_path) as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert scenario["verdict"] == "passed", scenario
    assert "reached in the same task reopened" in scenario["detail"]
    outcomes = {key: findings[key] for key in ("listed", "before", "after", "next_step",
                                               "reopened", "forked", "loopback", "never_added")}
    assert outcomes == {"listed": "reached", "before": "blocked", "after": "blocked",
                        "next_step": "blocked", "reopened": "reached", "forked": "not_run",
                        "loopback": "blocked", "never_added": "blocked"}
    assert (findings["reached_in"], findings["notes"]) == (
        "reopened", "reopened: unloaded after 0 s")
    assert (findings["added_live"], findings["asked_again_after_adding"]) == (True, True)
    assert (findings["site_list_put_back"], findings["loopback_paths_reached"]) == (True, [])
    assert findings["default_sites"] == "written"
    # The default sites written when Codex became ready, exactly one exact host upserted live, then
    # the list written back as it was before K3 — the defaults — and nothing else, each taken.
    writes = [(record["params"], record["status"])
              for record in rig.server.records("config_written")]
    assert writes == [
        ({"edits": [{"keyPath": SITES_KEY, "mergeStrategy": "upsert", "value": DEFAULTS}],
          "reloadUserConfig": True}, "ok"),
        ({"edits": [{"keyPath": SITES_KEY, "mergeStrategy": "upsert",
                     "value": {"example.com": "allow"}}], "reloadUserConfig": True}, "ok"),
        ({"edits": [{"keyPath": SITES_KEY, "mergeStrategy": "replace", "value": DEFAULTS}],
          "reloadUserConfig": True}, "ok"),
    ]


def test_k3_asks_no_next_step_when_the_running_turn_already_reaches_the_site(
    tmp_path: Path,
) -> None:
    with calibrating(tmp_path, "site_add_reaches_running_turn") as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], "reached in that same step" in scenario["detail"]) == (
        "passed", True), scenario
    assert (findings["after"], findings["reached_in"]) == ("reached", "same step")
    assert (findings["next_step"], findings["reopened"], findings["forked"]) == (
        "not_run", "not_run", "not_run")


def test_k3_stops_at_the_next_step_when_a_new_turn_sees_the_site(tmp_path: Path) -> None:
    with calibrating(tmp_path, "site_add_reaches_next_turn") as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], "reached in the task's next step" in scenario["detail"]) == (
        "passed", True), scenario
    assert (findings["after"], findings["next_step"], findings["reopened"]) == (
        "blocked", "reached", "not_run")
    assert findings["reached_in"] == "next step"
    # The first turn carries the design's box; the next step leaves the thread's profile to govern.
    assert [record["sandbox_policy"] for record in rig.server.records("calibration_turn")] == [
        True, False]


def test_k3_copies_the_task_when_codex_never_unloads_the_thread(tmp_path: Path) -> None:
    with calibrating(tmp_path, "thread_never_unloads") as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], "reached in a copy of the task" in scenario["detail"]) == (
        "passed", True), scenario
    assert (findings["next_step"], findings["reopened"]) == ("blocked", "blocked")
    assert (findings["forked"], findings["reached_in"], findings["fork_profile"]) == (
        "reached", "copy", "clarvis_run")
    assert "still loaded after" in findings["notes"]


def test_k3_fails_when_the_copy_that_reached_the_site_dropped_the_file_rules(
    tmp_path: Path,
) -> None:
    with calibrating(tmp_path, "thread_never_unloads", "fork_drops_profile") as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], "ran under workspace, not clarvis_run" in scenario["detail"]) == (
        "failed", True), scenario
    assert (findings["forked"], findings["fork_profile"]) == ("reached", "workspace")


def test_k3_adds_only_a_site_ravis_detection_named(tmp_path: Path) -> None:
    with calibrating(tmp_path, "site_block_line_changed") as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], findings["before"]) == ("failed", "failed"), scenario
    assert findings["added_live"] is False
    # Only the default sites written at the start: nothing added, so nothing put back.
    assert len(rig.server.records("config_written")) == 1


def test_k3_fails_when_codex_reports_the_added_site_overridden(tmp_path: Path) -> None:
    with calibrating(tmp_path, site_add_status="okOverridden") as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], "(overridden)" in scenario["detail"]) == ("failed", True), scenario
    assert (findings["default_sites"], findings["add_refused"]) == ("written", "overridden")
    assert (findings["added_live"], findings["after"]) == (False, "blocked")
    assert view["result"]["strict_rules_proven"] is False


def test_k3_fails_saying_overridden_when_the_launch_flags_carry_sites_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Run 5's bug put back: a site list among the launch flags, which the fake, like Codex, lets
    # outrank every site write.
    launched = CodexService._profile_flags
    monkeypatch.setattr(CodexService, "_profile_flags",
                        lambda self: (*launched(self), *SITES_AT_LAUNCH))

    with calibrating(tmp_path) as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    # Judged by the start-up write first: the list Codex really started with.
    started_without = "default sites when its process started (overridden)"
    assert (scenario["verdict"], started_without in scenario["detail"]) == (
        "failed", True), scenario
    assert (findings["default_sites"], findings["add_refused"]) == ("overridden", "overridden")
    assert findings["added_live"] is False
    assert {record["status"] for record in rig.server.records("config_written")} == {
        "okOverridden"}


# ── K6: one command per project, every long-runner alive at once ────────────


def test_k6_waits_for_no_more_processes_than_its_one_command_starts() -> None:
    [command] = K6_COMMANDS
    *jobs, last = command.split(" & ")
    assert last == "wait" and len(jobs) == 3, command
    # No listener: the sandbox refuses to let a command bind a socket.
    assert "http.server" not in command and "--bind" not in command
    # The shell that waits, each background job, and the child `script` runs under its terminal.
    started = 1 + sum(2 if job.startswith("script ") else 1 for job in jobs)
    assert 0 < K6_PROCESSES_PER_PROJECT <= started


def test_k6_passes_when_stopping_a_ends_all_of_as_processes_and_none_of_bs(
    tmp_path: Path,
) -> None:
    with calibrating(tmp_path) as (rig, client, a, b):
        start(client, rig, a, b, ["K6"])
        view = finished(client, rig)
        spawned = rig.server.records("spawned")

    scenario, findings = only(view)
    assert scenario["verdict"] == "passed", scenario
    expected = K6_PROCESSES_PER_PROJECT
    assert Counter(entry["project"] for entry in findings["attribution"]) == {
        "A": expected, "B": expected}
    assert findings["found_per_project"] == {"A": expected, "B": expected}
    assert (findings["a_survivors"], findings["b_stopped_with_a"]) == ([], [])
    assert findings["unattributed_commands"] == []
    # One command root per project, and B's ended afterwards too.
    assert len(spawned) == 2
    for record in spawned:
        assert subprocess.run(["ps", "-p", str(record["pid"])], capture_output=True).returncode == 1


def test_k6_fails_when_stopping_a_also_stops_bs_processes(tmp_path: Path) -> None:
    with calibrating(tmp_path, "stop_kills_other_project") as (rig, client, a, b):
        start(client, rig, a, b, ["K6"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], "also stopped project B" in scenario["detail"]) == (
        "failed", True), scenario
    assert findings["found_per_project"] == {"A": K6_PROCESSES_PER_PROJECT,
                                             "B": K6_PROCESSES_PER_PROJECT}
    assert findings["b_stopped_with_a"]


def test_k6_counts_and_judges_only_its_own_long_runners() -> None:
    """Run `cal_ed672bf12c6f`: three `(bash)` rows under B, caught while B was still starting, were
    gone after A's Stop while all five of B's long-runners ran on. That isn't B stopped with A."""

    def row(pid: int, args: str) -> ProcessRow:
        return ProcessRow(pid=pid, ppid=1, start="Mon Sep 14 14:14:43 2026", args=args)

    b = [row(10, f"/bin/zsh -lc {K6_COMMANDS[0]}"), row(11, "sleep 600"),
         row(12, "script -q /dev/null sleep 600"), row(13, "sleep 600"),
         row(14, "python3 -c import time; time.sleep(600)"),
         row(15, "(bash)"), row(16, "(bash)"), row(17, "(bash)")]
    attributed = {entry.pid: Attributed(row=entry, owner="B", rule="parent") for entry in b}

    assert [entry.pid for entry in long_runners(attributed, "B")] == [10, 11, 12, 13, 14]
    assert len(long_runners(attributed, "B")) == K6_PROCESSES_PER_PROJECT
    assert long_runners(attributed, "A") == []


# ── K7: what an interrupted turn leaves open ────────────────────────────────


def test_k7_passes_when_ravis_answers_the_request_codex_left_open(tmp_path: Path) -> None:
    with calibrating(tmp_path) as (rig, client, a, b):
        start(client, rig, a, b, ["K7"])
        view = finished(client, rig)
        late = eventually(lambda: rig.server.records("answered_after_interrupt"),
                          what="the answer the fake got after the interrupt")

    scenario, findings = only(view)
    assert (scenario["verdict"], "RAVIS answered" in scenario["detail"]) == ("passed", True)
    assert list(findings["answered_by_ravis"].values()) == [{"decision": "cancel"}]
    assert (findings["resolved_by_codex"], findings["left_open"]) == ([], 0)
    assert [(entry["method"], entry["result"]) for entry in late] == [
        ("item/commandExecution/requestApproval", {"decision": "cancel"})
    ]
    transcript = (Path(view["result"]["outputs"]) / "K7.jsonl").read_text()
    assert "serverRequest/resolved" not in transcript and '"turn_ended"' in transcript


def test_k7_fails_when_the_turn_ends_with_its_request_left_unanswered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Session, "_resolve_open", lambda _self, _key: None)

    with calibrating(tmp_path) as (rig, client, a, b):
        start(client, rig, a, b, ["K7"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], "still open" in scenario["detail"]) == ("failed", True), scenario
    assert (findings["answered_by_ravis"], findings["left_open"]) == ({}, 1)


# ── K8: a permission request Codex never sends ──────────────────────────────


def test_k8_records_a_codex_that_never_asks_and_a_full_run_can_still_prove(
    tmp_path: Path,
) -> None:
    with calibrating(tmp_path, "never_asks_permissions") as (rig, client, a, b):
        start(client, rig, a, b, ["K8"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert scenario["verdict"] == "recorded", scenario
    assert findings["permissions_asked"] is False

    def results(**changed: str) -> dict[str, ScenarioResult]:
        return {
            spec.id: ScenarioResult(
                spec.id, changed.get(spec.id, "recorded" if spec.kind == "record" else "passed"), ""
            )  # type: ignore[arg-type]
            for spec in SCENARIOS
        }

    everything = tuple(spec.id for spec in SCENARIOS)
    assert decide(results(K8="recorded"), everything).strict_rules_proven is True
    for changed in ({"K8": "inconclusive"}, {"K8": "failed"}, {"K7": "recorded"},
                    {"K3": "recorded"}):
        assert decide(results(**changed), everything).strict_rules_proven is False, changed


# ── The candidate profile ───────────────────────────────────────────────────


def test_the_candidate_profile_is_the_syntax_codex_accepted_with_r3s_network_and_no_sites() -> None:
    [option, setting] = CANDIDATE_PROFILE["flags"]

    assert option == "-c" and checked_profile(CANDIDATE_PROFILE) == CANDIDATE_PROFILE
    # Codex refuses "**/.run" as a filesystem key of its own: the project's folder is nested.
    assert '":project_roots"={"."="write", ".run"="deny", "**/.run"="deny"}' in setting
    assert setting.count('"**/.run"') == 1
    # The network section is R3's own — the proxy only. The sites, K3's listed one among them, are
    # written once Codex runs, so a launch flag never outranks them (Cal-3).
    section = network_profile_flags("clarvis_run")[1].split("=", 1)[1]
    assert f"network={section}, filesystem=" in setting
    assert "domains" not in setting
    assert LISTED_SITE in DEFAULT_ALLOWED_SITES
