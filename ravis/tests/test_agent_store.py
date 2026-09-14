"""The relay's pieces on their own: migration 8, retention, request views, redaction and more.

- **Migration 8** is backed up first (`ravis.db.v7.bak`) and can be rolled back to 7 from it, and
  so is **migration 10** (each task's effort, R5) to 9.
- **Codex is told to stop at a blocked site** and say which it needs, rather than work around it.
- **Retention** deletes sessions, turns and requests 30 days after they ended, process rows 24 hours
  after they were confirmed gone, and kept answers after 24 hours — and nothing live.
- **Request views** built from Codex's own request shapes are the contract's examples, decisions
  included; no answer ever grants for the whole session or opens the network.
- **A blocked site** is read from Codex's proxy's own line, and nothing else.
- **Redaction** hides output that looks like a key or names a denied path.
- **Nested roots** follow the contract's cases; turn failures read as the owner would say them.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.agent_rig import fixture

from ravis.agent import calibration_dependent as calibrated
from ravis.agent import requests as requests_module
from ravis.agent.calibration_dependent import (
    APPROVAL_POLICY,
    DEFAULT_ALLOWED_SITES,
    codex_answer,
    network_profile_flags,
)
from ravis.agent.redact import HIDDEN, Redactor
from ravis.agent.requests import PathContext, payload_and_decisions
from ravis.agent.roots import related
from ravis.agent.sites import DECISIONS as SITE_DECISIONS
from ravis.agent.sites import blocked_hosts, plain_site, protocol_for, site_payload
from ravis.agent.skills import SkillChoices
from ravis.agent.store import AgentStore
from ravis.agent.translate import turn_error
from ravis.codex.lock_file import iso
from ravis.storage import database as storage
from ravis.storage.database import prepare_database, restore_backup

EXAMPLE_ROOT = Path("/Users/owner/Documents/coding/add-utc-demo")


def test_migration_8_is_backed_up_first_and_rolls_back_to_7(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ravis.db"
    with monkeypatch.context() as patched:
        patched.setattr(storage, "MIGRATIONS", storage.MIGRATIONS[:7])
        assert prepare_database(str(path)).schema_version == 7
    migrated = prepare_database(str(path))
    assert migrated.schema_version == storage.MIGRATIONS[-1][0]
    assert (tmp_path / "ravis.db.v7.bak").exists()
    tables = {row[0] for row in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"agent_session", "agent_request", "agent_turn", "agent_process", "project_lock",
            "agent_idempotency", "ravis_instance", "agent_thread"} <= tables
    assert restore_backup(path, 7) == 7
    restored = {row[0] for row in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "agent_session" not in restored


def test_migration_9_is_backed_up_first_and_records_each_process_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4's migration, on a throwaway database only: instances, kept threads, one row a process."""
    path = tmp_path / "ravis.db"
    with monkeypatch.context() as patched:
        patched.setattr(storage, "MIGRATIONS", storage.MIGRATIONS[:8])
        assert prepare_database(str(path)).schema_version == 8
    store = AgentStore(prepare_database(str(path)))
    assert (tmp_path / "ravis.db.v8.bak").exists()
    for _ in range(2):
        store.record_process("as_1", "turn-1", (4411, "Sun Sep 13 05:10:02 2026"), "sleep",
                             "command_cwd")
    store.record_process("as_1", "turn-1", (4411, "Sun Sep 13 06:00:00 2026"), "sleep", "parent")
    assert len(store.live_processes("as_1")) == 2
    store.confirm_gone("as_1", [(4411, "Sun Sep 13 05:10:02 2026")])
    assert [row["start_time"] for row in store.live_processes()] == ["Sun Sep 13 06:00:00 2026"]
    for number in range(storage_instances_over := 25):
        store.record_instance(1000 + number, "Sun Sep 13 05:10:02 2026")
    assert len(store.instances()) == 20 and store.instances()[0]["pid"] == 1000 + 24
    assert storage_instances_over == 25
    assert restore_backup(path, 8) == 8
    assert "ravis_instance" not in {row[0] for row in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_migration_10_is_backed_up_first_and_adds_each_tasks_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "ravis.db"
    with monkeypatch.context() as patched:
        patched.setattr(storage, "MIGRATIONS", storage.MIGRATIONS[:9])
        before = prepare_database(str(path))
        assert before.schema_version == 9
        AgentStore(before).insert_session(_session("as_before", None))
    store = AgentStore(prepare_database(str(path)))
    assert (tmp_path / "ravis.db.v9.bak").exists()
    assert store.session("as_before")["effort"] is None  # type: ignore[index]
    store.insert_session({**_session("as_after", None), "effort": "medium"})
    assert store.session("as_after")["effort"] == "medium"  # type: ignore[index]
    assert restore_backup(path, 9) == 9
    columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(agent_session)")}
    assert "effort" not in columns and "model" in columns


def test_migration_11_is_backed_up_first_and_keeps_the_owners_skill_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's skill switches live in RAVIS's database and outlive RAVIS itself: opened again,
    the database still holds them; rolled back to 10, the table is gone."""
    path = tmp_path / "ravis.db"
    with monkeypatch.context() as patched:
        patched.setattr(storage, "MIGRATIONS", storage.MIGRATIONS[:10])
        assert prepare_database(str(path)).schema_version == 10
    choices = SkillChoices(prepare_database(str(path)))
    assert (tmp_path / "ravis.db.v10.bak").exists()
    graphify = "/Users/owner/.agents/skills/graphify/SKILL.md"
    assert choices.get(graphify) is None
    choices.choose(graphify, True)
    choices.choose("/Users/owner/.codex/skills/.system/imagegen/SKILL.md", False)
    choices.choose(graphify, False)
    reopened = SkillChoices(prepare_database(str(path)))
    assert reopened.get(graphify) is False
    assert reopened.get("/Users/owner/.codex/skills/.system/imagegen/SKILL.md") is False
    reopened.forget(graphify)
    assert SkillChoices(prepare_database(str(path))).get(graphify) is None
    assert restore_backup(path, 10) == 10
    assert "codex_skill_choice" not in {row[0] for row in sqlite3.connect(path).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_codex_is_told_to_stop_at_a_blocked_site_and_say_which_it_needs() -> None:
    [line] = [line for line in calibrated.DEVELOPER_INSTRUCTIONS.splitlines()
              if "Network access to" in line]
    assert "was blocked" in line and "workaround" in line
    assert "end the turn, saying which site is needed and why" in line
    started = calibrated.thread_start_params(EXAMPLE_ROOT, "agent", "clarvis_run", "", "as_1")
    resumed = calibrated.thread_resume_params("thread-1", EXAMPLE_ROOT, "agent", "clarvis_run")
    assert line in started["developerInstructions"] and line in resumed["developerInstructions"]


def _session(session_id: str, ended_at: str | None) -> dict[str, Any]:
    return {
        "id": session_id, "application_id": "clarvis", "workspace_root": "/p",
        "workspace_root_hash": "sha256:x", "workspace_name": "p", "clarvis_task_id": "t",
        "mode": "agent", "file_rules": "strict", "state": "ended" if ended_at else "running",
        "token_sha256": "0" * 64, "trace_id": "t", "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z", "ended_at": ended_at,
    }


def test_retention_deletes_what_the_design_keeps_no_longer_and_nothing_live() -> None:
    now = datetime(2026, 9, 13, tzinfo=UTC)
    store = AgentStore(prepare_database(":memory:"), now=lambda: now)
    ages = (("old", now - timedelta(days=31)), ("recent", now - timedelta(days=2)), ("live", None))
    for session_id, ended in ages:
        store.insert_session(_session(session_id, iso(ended) if ended else None))
        store.insert_turn(session_id, "turn", "brief")
        store.insert_request(f"rq_{session_id}", session_id, "turn", "srv-1", "command")
    db = store._database.connection
    db.execute("INSERT INTO agent_process VALUES ('live', 't', 1, 's', 'sh', 'terminal', ?, ?)",
               (iso(now), iso(now - timedelta(hours=25))))
    db.execute("INSERT INTO agent_idempotency VALUES ('s', 'k', 'b', 200, '{}', ?)",
               (iso(now - timedelta(hours=25)),))
    store.enforce_retention()
    assert {row["id"] for row in store.live_sessions()} == {"live"}
    assert store.session("old") is None and store.session("recent") is not None
    assert store.request("rq_old") is None and store.request("rq_live") is not None
    assert db.execute("SELECT COUNT(*) FROM agent_process").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM agent_idempotency").fetchone()[0] == 0


def _context() -> PathContext:
    denied = (Path("/Users/owner/.ssh"),)
    return PathContext(EXAMPLE_ROOT, denied, Redactor(denied, Path("/Users/owner")))


def _diff(added: int, removed: int) -> str:
    return "\n".join(["@@"] + ["-x"] * removed + ["+y"] * added)


CODEX_REQUESTS: dict[str, tuple[str, dict[str, Any], list[dict[str, Any]]]] = {
    "command": ("command", {
        "command": "npm install left-pad", "cwd": str(EXAMPLE_ROOT / "web"),
        "reason": "The build needs this package.",
    }, []),
    "fileChange": ("fileChange", {"reason": "Add the --utc flag and document it."}, [
        {"path": "src/app.ts", "kind": {"type": "update", "move_path": None}, "diff": _diff(12, 3)},
        {"path": "README.md", "kind": {"type": "update", "move_path": "docs/README.md"},
         "diff": ""},
        {"path": "/Users/owner/.zshrc", "kind": {"type": "update", "move_path": None},
         "diff": _diff(1, 0)},
    ]),
    "permissions": ("permissions", {
        "reason": "Read the SSH configuration.",
        "permissions": {"network": None, "fileSystem": {"read": ["/Users/owner/.ssh/config"],
                                                         "write": None}},
    }, []),
    "question": ("question", {"questions": [{
        "id": "q1", "header": "Log timestamps",
        "question": "Should --utc also change the log timestamps?", "isOther": True,
        "isSecret": False, "options": [{"label": "Yes", "description": ""},
                                       {"label": "No", "description": ""}],
    }]}, []),
}


def test_request_views_from_codexs_shapes_are_the_contracts_examples() -> None:
    for shown in fixture("agent-sessions.json")["request_view_examples"]:
        if shown["kind"] == "site":
            assert (site_payload("pypi.org", "https"), list(SITE_DECISIONS)) == (
                shown["payload"], shown["allowed_decisions"])
            continue
        kind, params, changes = CODEX_REQUESTS[shown["kind"]]
        payload, allowed = payload_and_decisions(kind, params, _context(), changes)
        assert (payload, allowed) == (shown["payload"], shown["allowed_decisions"]), kind


def test_no_answer_grants_for_the_session_or_opens_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = {"command": "npm i", "cwd": str(EXAMPLE_ROOT),
             "additionalPermissions": {"network": {"enabled": True}}}
    context = {"command": "npm i", "cwd": str(EXAMPLE_ROOT),
               "networkApprovalContext": {"host": "registry.npmjs.org", "protocol": "https"}}
    asked = {"permissions": {"network": {"enabled": True}, "fileSystem": {"read": ["a"]}}}
    # Approvals never open the network (the owner's decision): only skipped or stopped, and a grant
    # never carries network; internet access comes from the approved-sites allowlist instead.
    assert payload_and_decisions("command", extra, _context(), [])[1] == ["skip", "stop"]
    assert payload_and_decisions("command", context, _context(), [])[1] == ["skip", "stop"]
    assert payload_and_decisions("permissions", asked, _context(), [])[1] == ["skip", "stop"]
    assert codex_answer("permissions", {"kind": "once"}, asked) == {
        "permissions": {"fileSystem": {"read": ["a"]}}, "scope": "turn"}
    # The switch, if the owner ever turned it: offered once, and a grant for its turn only.
    monkeypatch.setattr(calibrated, "NETWORK_GRANTS_OFFERED", True)
    monkeypatch.setattr(requests_module, "NETWORK_GRANTS_OFFERED", True)
    assert payload_and_decisions("command", extra, _context(), [])[1] == ["once", "skip", "stop"]
    assert codex_answer("permissions", {"kind": "once"}, asked) == {
        "permissions": asked["permissions"], "scope": "turn"}
    for kind in ("command", "fileChange"):
        for word in ("once", "skip", "stop"):
            assert codex_answer(kind, {"kind": word}, {})["decision"] != "acceptForSession"
    assert all(policy not in ("never", "danger-full-access") for policy in APPROVAL_POLICY.values())


def test_output_that_looks_like_a_key_or_names_a_denied_path_is_hidden() -> None:
    home = Path("/Users/owner")
    redactor = Redactor((home / ".ssh", home / ".config" / "ravis"), home)
    for output in ("sk-" + "a" * 20, "ghp_" + "b" * 24, "eyJabcdefgh.ijklmnopq.rstuvwxyz",
                   "api_key = " + "c" * 40, "SECRET: " + "d" * 32):
        assert redactor.output(output, hidden=False) == HIDDEN, output
    assert redactor.output("3 passed in 0.41s", hidden=False) == "3 passed in 0.41s"
    assert redactor.names_denied("cat ~/.ssh/id_rsa")
    assert redactor.names_denied("ls", [{"type": "read", "path": "/Users/owner/.config/ravis/x"}])
    assert not redactor.names_denied("cat README.md")
    assert redactor.tail("x" * 2000, hidden=False) == "x" * 1500


def test_nested_roots_follow_the_contracts_cases() -> None:
    for case in fixture("project-locks.json")["nested_root_cases"]:
        requested, locked = Path(case["requested"]), Path(case["locked"])
        assert (requested != locked and related(requested, locked)) is case["nested"], case


def test_turn_failures_read_as_the_owner_would_say_them() -> None:
    cases = {
        "usageLimitExceeded": "quota_exhausted", "unauthorized": "sign_in_expired",
        "rateLimitExceeded": "throttled", "other": "other",
    }
    for info, kind in cases.items():
        assert turn_error({"message": "m", "codexErrorInfo": info})["kind"] == kind  # type: ignore[index]
    throttled = turn_error({"message": "m", "codexErrorInfo": {
        "responseTooManyFailedAttempts": {"httpStatusCode": 429}}})
    assert throttled == {"kind": "throttled", "http_status": 429,
                         "message": "OpenAI kept refusing Codex for now (too many requests)."}
    lost = turn_error({"message": "m", "codexErrorInfo": {
        "httpConnectionFailed": {"httpStatusCode": None}}})
    assert lost is not None and lost["kind"] == "connection_lost"
    assert turn_error(None) is None


def test_a_blocked_site_is_read_from_the_proxys_own_line_and_nothing_else() -> None:
    line = ('Network access to "{}" was blocked: domain is not on the allowlist for the current '
            "sandbox mode.")
    output = "\n".join((f"curl: (56) {line.format('PyPI.org')}", line.format("pypi.org"),
                         line.format("evil com"), line.format("github.com")))
    assert blocked_hosts(output) == ["pypi.org", "github.com"]
    assert blocked_hosts("curl: (6) Could not resolve host: pypi.org") == []
    assert blocked_hosts(None) == []
    assert protocol_for("pypi.org", "pip download https://pypi.org/simple") == "https"
    assert protocol_for("pypi.org", "curl http://PYPI.org:80") == "http"
    assert protocol_for("pypi.org", "ping pypi.org.evil") is None


def test_only_an_exact_public_host_can_be_allowed_and_the_defaults_are_the_owners() -> None:
    assert plain_site(" PyPI.org. ") == "pypi.org"
    for refused_host in ("127.0.0.1", "10.0.0.1", "::1", "localhost", "printer.local",
                         "box.lan", "*.github.com", "pypi.org:443", "https://pypi.org", "pypi",
                         "pypi.org/simple", ""):
        assert plain_site(refused_host) is None, refused_host
    wildcards = [site for site in DEFAULT_ALLOWED_SITES if site.startswith("*.")]
    assert wildcards == ["*.crates.io", "*.githubusercontent.com"]
    assert all(plain_site(site) == site for site in DEFAULT_ALLOWED_SITES if site not in wildcards)
    # The proxy only: the sites are written once Codex runs, never at launch (Cal-3).
    assert network_profile_flags("clarvis_run") == (
        "-c", 'permissions.clarvis_run.network={enabled=true, mode="limited"}')
