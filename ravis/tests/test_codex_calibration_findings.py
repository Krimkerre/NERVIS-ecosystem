"""Cal-2: calibration made to match what the real run found and what the owner decided (§10.4).

Every run here is against the fake app-server's calibration half — never a real Codex, never an
outside host. The rules held:

- **K3 proves the approved-sites allowlist**: a listed site answers; an unlisted one is refused with
  the proxy's fixed line, which RAVIS's detection names; adding it the way the owner's "allow" does,
  while the turn runs, reaches that same thread; loopback and a never-added site stay refused; and
  the site list is written back afterwards. Codex reporting the add overridden fails K3. (Each of
  (a)–(e) broken alone is in `test_codex_calibration.py`'s fault table.)
- **K7 passes only when nothing is left open**: Codex doesn't resolve an interrupted turn's request,
  so RAVIS answers it with cancel when the turn ends; left unanswered, K7 fails.
- **K8 records a Codex that never asks for permissions**, and that doesn't keep a full run from
  proving the rules; an inconclusive or failed K8 still does.
- **The candidate profile** is the syntax Codex 0.154.0 accepted, with R3's sites.
"""

from __future__ import annotations

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

SITES_KEY = "permissions.clarvis_run.network.domains"


def only(view: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The one scenario a run asked: its view, and its findings from the summary."""
    [scenario] = view["scenarios"]
    return scenario, summary(view)["scenarios"][0]["findings"]


# ── K3: the approved-sites allowlist ────────────────────────────────────────


def test_k3_proves_the_allowlist_and_a_site_added_while_the_task_runs(tmp_path: Path) -> None:
    with calibrating(tmp_path) as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert scenario["verdict"] == "passed", scenario
    outcomes = {key: findings[key] for key in ("listed", "before", "after", "loopback",
                                               "never_added")}
    assert outcomes == {"listed": "reached", "before": "blocked", "after": "reached",
                        "loopback": "blocked", "never_added": "blocked"}
    assert (findings["added_live"], findings["asked_again_after_adding"]) == (True, True)
    assert (findings["site_list_put_back"], findings["loopback_paths_reached"]) == (True, [])
    # Exactly one exact host upserted live, then the list written back as it was — nothing else.
    writes = [record["params"] for record in rig.server.records("config_written")]
    assert writes == [
        {"edits": [{"keyPath": SITES_KEY, "mergeStrategy": "upsert",
                    "value": {"example.com": "allow"}}], "reloadUserConfig": True},
        {"edits": [{"keyPath": SITES_KEY, "mergeStrategy": "replace", "value": {}}],
         "reloadUserConfig": True},
    ]


def test_k3_adds_only_a_site_ravis_detection_named(tmp_path: Path) -> None:
    with calibrating(tmp_path, "site_block_line_changed") as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], findings["before"]) == ("failed", "failed"), scenario
    assert findings["added_live"] is False
    assert rig.server.records("config_written") == []


def test_k3_fails_when_codex_reports_the_added_site_overridden(tmp_path: Path) -> None:
    with calibrating(tmp_path, batch_write_status="okOverridden") as (rig, client, a, b):
        start(client, rig, a, b, ["K3"])
        view = finished(client, rig)

    scenario, findings = only(view)
    assert (scenario["verdict"], "(overridden)" in scenario["detail"]) == ("failed", True), scenario
    assert (findings["added_live"], findings["after"]) == (False, "blocked")
    assert view["result"]["strict_rules_proven"] is False


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


def test_the_candidate_profile_is_the_syntax_codex_accepted_with_r3s_sites() -> None:
    [option, setting] = CANDIDATE_PROFILE["flags"]

    assert option == "-c" and checked_profile(CANDIDATE_PROFILE) == CANDIDATE_PROFILE
    # Codex refuses "**/.run" as a filesystem key of its own: the project's folder is nested.
    assert '":project_roots"={"."="write", ".run"="deny", "**/.run"="deny"}' in setting
    assert setting.count('"**/.run"') == 1
    # The network section is R3's own, with every approved site, and K3's listed site among them.
    section = network_profile_flags("clarvis_run")[1].split("=", 1)[1]
    assert f"network={section}, filesystem=" in setting
    assert all(f'"{site}"="allow"' in setting for site in DEFAULT_ALLOWED_SITES)
    assert LISTED_SITE in DEFAULT_ALLOWED_SITES
