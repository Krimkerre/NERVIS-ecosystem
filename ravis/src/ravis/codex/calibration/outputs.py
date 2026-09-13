"""What a calibration run leaves behind: transcripts, a results summary, and the pin's entry.

**Redacted transcripts** go in `ravis/tests/fixtures/codex/calibration/<version>-<run>/`, one JSON
Lines file per scenario, for RAVIS's fake to be checked against and Clarvis to copy the relay-level
ones (design §10.4). Before anything is written:
- tokens are scrubbed — `sk-…`, `gh?_…`, anything shaped like a JWT, and 32 or more token characters
  after `token`, `key` or `secret`;
- email addresses become `<email>`, and every account, user or organisation id — by field name, and
  by shape (`user-…`, `org-…`, `acct_…`) — becomes `<account-id>`;
- the run's decoy marker becomes `<decoy-marker>`, and the two projects, RAVIS's Codex home and the
  owner's home folder are replaced by `<project-a>`, `<project-b>`, `<codex-home>` and `~`, so a
  transcript names no person and replays on any Mac.

**The results summary** (`summary.json`) holds every scenario's verdict, detail and findings, the
decision, the owner's questions, the final mapping from modes to Codex's approval settings for R3
and C2b, and a paragraph a STATUS record can be written from (`status_record`).

**The pin's entry** goes into `tested_runtimes.json` **only when the decision proves the strict
rules** — a full run with every must-pass question passed. It sets `file_rules_profile` (the flags
with their placeholders, never this Mac's folders) and marks the build's entry
`strict_rules_proven: true`. A failed, inconclusive or partial run writes nothing there.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravis.codex.calibration.harness import ScenarioResult
from ravis.codex.calibration.plan import BY_ID, SCENARIO_IDS, CalibrationPlan

TOKEN_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
)
AFTER_A_WORD = re.compile(r"(?i)((?:token|key|secret)[\"'\s:=]{1,4})[A-Za-z0-9_\-]{32,}")
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
ACCOUNT_ID = re.compile(r"\b(?:user|org|acct|account)[-_][A-Za-z0-9]{8,}\b")
ACCOUNT_FIELDS = frozenset({
    "accountId", "account_id", "userId", "user_id", "organizationId", "organization_id",
    "chatgpt_account_id", "chatgptAccountId", "workspaceId", "workspace_id",
})
GATE_SCENARIOS = ("K5", "K5a")


@dataclass(frozen=True)
class Redactor:
    """Replaces the run's own values first, then anything shaped like a secret or a person."""

    replacements: tuple[tuple[str, str], ...]

    def text(self, value: str) -> str:
        for found, placed in self.replacements:
            value = value.replace(found, placed)
        for pattern in TOKEN_PATTERNS:
            value = pattern.sub("<redacted-token>", value)
        value = AFTER_A_WORD.sub(r"\1<redacted-token>", value)
        value = EMAIL.sub("<email>", value)
        return ACCOUNT_ID.sub("<account-id>", value)

    def value(self, value: object) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, Mapping):
            return {
                self.text(str(key)): "<account-id>" if key in ACCOUNT_FIELDS else self.value(item)
                for key, item in value.items()
            }
        if isinstance(value, list | tuple):
            return [self.value(item) for item in value]
        return value


def redactor_for(plan: CalibrationPlan, codex_home: Path) -> Redactor:
    """The run's own values, longest first so a project inside the home folder keeps its name."""
    pairs = {
        plan.marker: "<decoy-marker>",
        str(plan.project_a.root): "<project-a>",
        str(plan.project_b.root): "<project-b>",
        str(codex_home): "<codex-home>",
        str(plan.decoy_folder): "<decoy-folder>",
        str(Path.home()): "~",
    }
    ordered = sorted(pairs.items(), key=lambda pair: len(pair[0]), reverse=True)
    return Redactor(tuple((found, placed) for found, placed in ordered if found))


def write_transcripts(
    folder: Path, results: Iterable[ScenarioResult], redactor: Redactor
) -> list[str]:
    """One redacted JSON Lines file per scenario that ran; the file names written."""
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for result in results:
        if not result.transcript:
            continue
        lines = (json.dumps(redactor.value(entry), sort_keys=True) for entry in result.transcript)
        (folder / f"{result.id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(f"{result.id}.jsonl")
    return written


@dataclass(frozen=True)
class Decision:
    strict_rules_proven: bool
    sentence: str
    owner_decision_needed: bool
    owner_questions: tuple[str, ...]
    protected_repositories: str


def decide(results: Mapping[str, ScenarioResult], selected: tuple[str, ...]) -> Decision:
    """Proven only by a full run in which every must-pass question passed."""
    must_pass = [key for key in selected if BY_ID[key].kind == "must_pass"]
    not_passed = [key for key in must_pass if results[key].verdict != "passed"]
    gate_failed = [
        key for key in GATE_SCENARIOS if key in results and results[key].verdict == "failed"
    ]
    full = set(selected) == set(SCENARIO_IDS)
    questions = tuple(
        str(result.findings["owner_question"])
        for result in results.values() if result.findings.get("owner_question")
    )
    protected = _protected(results.get("K5c"))
    if gate_failed:
        sentence = (
            f"The strict file rules didn't hold ({', '.join(gate_failed)}). They stay unproven, no "
            "Codex task may start, and decision D2 goes back to the owner."
        )
        return Decision(False, sentence, True, questions, protected)
    if not full:
        sentence = (
            f"This run asked only {', '.join(selected)}. Only a full run can prove the strict file "
            "rules, so they stay unproven."
        )
        return Decision(False, sentence, False, questions, protected)
    if not_passed:
        sentence = (
            f"{', '.join(not_passed)} didn't pass, so the strict file rules stay unproven. Look at "
            "what they found, then run calibration again."
        )
        return Decision(False, sentence, False, questions, protected)
    sentence = (
        "Every must-pass question passed: the strict file rules are proven on this Codex build, "
        "and its profile is written into tested_runtimes.json."
    )
    return Decision(True, sentence, False, questions, protected)


def _protected(result: ScenarioResult | None) -> str:
    if result is None or result.verdict not in ("passed", "failed"):
        return "not tested: the ecosystem's own repositories stay refused"
    if result.verdict == "failed":
        return "stay refused: deny didn't beat write inside a project"
    return "may be allowed later, by an exact listing: deny beat write inside a project"


def mode_mapping(results: Mapping[str, ScenarioResult]) -> dict[str, Any]:
    """The mapping from modes to Codex's approval settings that R3 and C2b use (design §4.9)."""
    k1, k4 = results.get("K1"), results.get("K4")
    pair = k1.findings.get("working_pair") if k1 is not None else None
    granular = k4 is not None and k4.findings.get("granular_prevents_leaving_the_box") is True
    box = pair.get("box") if isinstance(pair, dict) else None
    asks = pair.get("approvalPolicy") if isinstance(pair, dict) else None
    no_escalation = {
        "granular": {
            "sandbox_approval": False, "rules": True, "mcp_elicitations": False,
            "request_permissions": False, "skill_approval": False,
        }
    }
    return {
        "measured": pair is not None,
        "agent_and_auto": {
            "approvalPolicy": no_escalation if granular else (asks or "untrusted"),
            "box": box or "workspace",
        },
        "unattended": {
            "approvalPolicy": no_escalation if granular else "on-request",
            "box": box or "workspace",
        },
        "approvalsReviewer": "user",
    }


@dataclass(frozen=True)
class BuildFacts:
    sha256: str
    version: str
    stable_tree: str | None
    experimental_tree: str | None


def summary(
    *,
    run_id: str,
    build: BuildFacts,
    profile: Mapping[str, Any],
    selected: tuple[str, ...],
    results: Mapping[str, ScenarioResult],
    decision: Decision,
    transcripts: list[str],
) -> dict[str, Any]:
    body = {
        "format": 1,
        "run_id": run_id,
        "finished_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "codex": {
            "version": build.version, "sha256": build.sha256,
            "stable_tree": build.stable_tree, "experimental_tree": build.experimental_tree,
        },
        "profile": dict(profile),
        "full_run": set(selected) == set(SCENARIO_IDS),
        "scenarios": [
            {
                "id": key, "question": BY_ID[key].question, "kind": BY_ID[key].kind,
                "verdict": results[key].verdict, "detail": results[key].detail,
                "findings": results[key].findings,
            }
            for key in selected
        ],
        "decision": {
            "strict_rules_proven": decision.strict_rules_proven,
            "sentence": decision.sentence,
            "owner_decision_needed": decision.owner_decision_needed,
            "owner_questions": list(decision.owner_questions),
            "protected_repositories": decision.protected_repositories,
        },
        "mode_mapping": mode_mapping(results),
        "transcripts": transcripts,
    }
    body["status_record"] = status_record(body)
    return body


def status_record(body: Mapping[str, Any]) -> str:
    """A plain paragraph for STATUS.md's calibration record."""
    counts: dict[str, int] = {}
    for scenario in body["scenarios"]:
        counts[scenario["verdict"]] = counts.get(scenario["verdict"], 0) + 1
    tally = ", ".join(f"{count} {verdict}" for verdict, count in sorted(counts.items()))
    decision = body["decision"]
    lines = [
        f"Calibration {body['run_id']} on Codex {body['codex']['version']} "
        f"(sha256 {str(body['codex']['sha256'])[:12]}…), "
        f"{'a full run' if body['full_run'] else 'a partial run'}: {tally}.",
        decision["sentence"],
        f"The ecosystem's own repositories {decision['protected_repositories']}.",
    ]
    failed = [s for s in body["scenarios"] if s["verdict"] in ("failed", "inconclusive")]
    lines += [f"{s['id']} {s['verdict']}: {s['detail']}" for s in failed]
    lines += [f"For the owner: {question}" for question in decision["owner_questions"]]
    return " ".join(lines)


def write_summary(folder: Path, body: Mapping[str, Any]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "summary.json"
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_pin_entry(
    pin: Path, *, build: BuildFacts, profile: Mapping[str, Any], summary_name: str
) -> None:
    """Record the proof in the pin: the profile's flags, and the build proven. Atomic."""
    document = json.loads(pin.read_text(encoding="utf-8"))
    document["file_rules_profile"] = {"name": profile["name"], "flags": list(profile["flags"])}
    tested = document.setdefault("tested", [])
    entry = next((item for item in tested if item.get("sha256") == build.sha256), None)
    if entry is None:
        # An accepted build calibrated: pinned by its hashes. Its per-definition hashes aren't
        # generated here (build-notes: `schema_report.definition_record`), so they read null.
        entry = {
            "codex_version": build.version, "source": "accepted build, calibrated",
            "sha256": build.sha256, "stable_tree": build.stable_tree,
            "experimental_tree": build.experimental_tree, "definitions": None,
        }
        tested.append(entry)
    entry["strict_rules_proven"] = True
    entry["calibrated_on"] = datetime.now(UTC).strftime("%Y-%m-%d")
    entry["calibration"] = summary_name
    temporary = pin.with_name(pin.name + ".tmp")
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, pin)
