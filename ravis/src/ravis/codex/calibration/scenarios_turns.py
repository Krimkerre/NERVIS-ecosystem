"""The questions about turns and processes: K4, K11, K12, K7, K6 and K13 (design §10.4).

**K6 is the one that signals processes.** It uses `process_table.py`: a snapshot of the process
table, each of Codex's processes attributed to project A or B by the rule a task's Stop uses (R4) —
RAVIS's own app-server's tree, each command root's working folder read with `lsof` while its
project's turn runs, parents, terminals, and pid plus start time — and the end sequence for A's
alone; then it checks every A process is gone and every B process still runs, and ends B's the same
way whatever happened. The first real run (`cal_d2185ed08f50`) found nothing by the sandbox's
arguments: `sandbox-exec` replaces itself with the command, so they never show. A process no rule
finds, or both projects claim, is reported and never signalled; nothing is ever signalled by group.

**One command per project, its long-runners all alive at once** (Cal-3). Run `cal_330b7525d115`
attributed every process it saw, with none unattributed, but K6 waited for four processes per
project that could never exist together: `python3 -m http.server 0 --bind 127.0.0.1` failed at once,
because the sandbox forbids listening on a socket (a sandbox rule, not a defect); `script -q
/dev/null sleep 600` ran in the foreground for 600 s, so the turn's later commands never started;
and `sleep 600 &` ended at once, leaving its `sleep` reparented away from Codex. So each project
runs one listed command that starts three long-runners in the background and waits for them.

**K7 is judged on RAVIS's side** (Cal-2). Codex 0.154.0 ends an interrupted turn within moments but
never resolves the request it had open (`cal_d2185ed08f50`), so K7 passes when the turn ends
`interrupted` within the cap and nothing is left open: RAVIS answered the request itself — the
harness's stop response when the turn ends, as R3's task does — or Codex let go of it. Codex's own
`serverRequest/resolved` isn't required. K3, the network, has its own module
(`scenarios_network.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ravis.codex.calibration.harness import (
    Listed,
    ScenarioContext,
    ScenarioResult,
    Session,
    command_prompt,
)
from ravis.codex.calibration.scenarios import GRANULAR_NO_SANDBOX_APPROVAL, q
from ravis.codex.process_table import (
    Attributed,
    ProcessRow,
    TaskClaim,
    attribute_processes,
    descendants,
    end_processes,
    read_cwds,
    snapshot,
)
from ravis.codex.rpc import CodexRpcError, CodexUnavailableError

#: K6's long-runners: a plain background process, one under a terminal of its own, and a
#: script-language one. No listener: the sandbox refuses to let a command bind a socket.
K6_LONG_RUNNERS = (
    "sleep 600",
    "script -q /dev/null sleep 600",
    "python3 -c 'import time; time.sleep(600)'",
)
#: The one listed command each project runs: every long-runner in the background, then `wait`, so
#: all of them exist together and the shell stays their parent.
K6_COMMAND = "".join(f"{runner} & " for runner in K6_LONG_RUNNERS) + "wait"
K6_COMMANDS = (K6_COMMAND,)
#: How many of a project's processes K6 waits to see before it stops A: the shell that waits,
#: `sleep`, `script` and the `sleep` it runs as its child, and `python3`. Never more than the one
#: command starts (`test_codex_calibration_findings.py` checks that).
K6_PROCESSES_PER_PROJECT = 5
#: K6's long-running commands, as the process table shows them.
OUR_COMMANDS = re.compile(r"sleep 600|time\.sleep\(600\)")


# ── One turn of listed commands (K4) ─────────────────────────────────────────


async def _turn(
    session: Session,
    ctx: ScenarioContext,
    scenario: str,
    commands: list[Listed],
    *,
    key: str = "A",
    approval_policy: Any = "untrusted",
) -> str | bool:
    """One thread in project A, one turn of listed commands: a cap or refusal's words, or True."""
    try:
        await session.start_thread(key, ctx.plan.project_a, approval_policy=approval_policy)
        session.expect(key, commands)
        await session.start_turn(key, command_prompt(scenario, commands))
        capped = await session.run_turns([key])
    except CodexRpcError as refusal:
        return f"Codex refused a request: {refusal.message}"
    if capped:
        return capped
    return f"Codex didn't keep to the list: {session.off_list[0]}" if session.off_list else True


# ── K4: escalation (recorded) ────────────────────────────────────────────────


async def k4(ctx: ScenarioContext) -> ScenarioResult:
    """Whether Codex asks to escalate, what it shows, and whether granular settings prevent it."""
    session = ctx.session("K4")
    shown: list[list[str]] = []
    session.observers.append(lambda item: _note_permissions(item.params, shown))
    findings: dict[str, Any] = {}
    plan = ctx.plan
    try:
        for key, policy in (("untrusted", "untrusted"), ("granular", GRANULAR_NO_SANDBOX_APPROVAL)):
            outside = plan.target(plan.project_a.root.parent / plan.name("K4", key))
            command = Listed(f"printf 'k4\\n' > {q(outside)}", escalated=True)
            before = len(session.approvals)
            capped = await _turn(session, ctx, "K4", [command], key=key, approval_policy=policy)
            findings[key] = {
                "asked": len(session.approvals) > before,
                "ran_outside_the_box": outside.exists(),
                "finished": capped is True,
            }
    finally:
        await session.close()
    findings["additional_permissions_shown"] = shown
    findings["asked_with_additional_permissions"] = any(
        approval.additional_permissions for approval in session.approvals
    )
    findings["granular_prevents_leaving_the_box"] = not findings["granular"]["ran_outside_the_box"]
    return session.result("recorded", "How Codex asks to leave the box is recorded.", **findings)


def _note_permissions(params: dict[str, Any], shown: list[list[str]]) -> None:
    extra = params.get("additionalPermissions")
    if isinstance(extra, dict):
        shown.append(sorted(extra))


# ── K11: "for the session" (recorded) ────────────────────────────────────────


async def k11(ctx: ScenarioContext) -> ScenarioResult:
    """Whether an `acceptForSession` grant survives the turn, a steer, or a new turn."""
    session = ctx.session("K11")
    plan = ctx.plan
    target = plan.target(plan.project_a.root / plan.name("K11", "log.txt"))
    text = f"printf 'k11\\n' >> {q(target.name)}"
    stages: dict[str, Any] = {}
    try:
        await session.start_thread("A", plan.project_a)
        session.expect("A", [Listed(text, decision="acceptForSession")])
        await session.start_turn("A", command_prompt("K11", [Listed(text), Listed(text)]))
        await session.run_turns(["A"])
        stages["same_turn"] = _asks(session, text) < 2
        stages["new_turn"] = await _stage(session, text, steer=False)
        stages["after_a_steer"] = await _stage(session, text, steer=True)
    except CodexRpcError as refusal:
        stages["refused"] = refusal.message
    finally:
        await session.close()
    stages["after_a_settle"] = "not measured: a settle is Clarvis's commit, in the live test"
    return session.result(
        "recorded", "How long a 'for the session' approval lasts is recorded.",
        grant_survives=stages,
    )


async def _stage(session: Session, text: str, *, steer: bool) -> bool:
    """Run the command in a new turn (steered once, if asked); whether Codex didn't ask again."""
    asked = _asks(session, text)
    turn_id = await session.start_turn("A", command_prompt("K11", [Listed(text)]))
    if steer:
        await session.request("turn/steer", {
            "threadId": session.threads["A"].thread_id, "expectedTurnId": turn_id,
            "input": [{"type": "text", "text": "Carry on with the list."}],
        })
    await session.run_turns(["A"])
    return _asks(session, text) == asked


def _asks(session: Session, text: str) -> int:
    return sum(
        1 for approval in session.approvals if approval.thread == "A" and approval.command == text
    )


# ── K12: event order (recorded) ──────────────────────────────────────────────


async def k12(ctx: ScenarioContext) -> ScenarioResult:
    """item/started → requestApproval → serverRequest/resolved → item/completed, in two threads."""
    session = ctx.session("K12")
    plan = ctx.plan
    try:
        for project in plan.projects:
            target = plan.target(project.root / plan.name("K12", project.label.lower()))
            command = Listed(f"printf 'k12\\n' > {q(target.name)}")
            await session.start_thread(project.label, project)
            session.expect(project.label, [command])
            await session.start_turn(project.label, command_prompt("K12", [command]))
        capped = await session.run_turns(["A", "B"])
    except CodexRpcError as refusal:
        return session.result("inconclusive", f"Codex refused a request: {refusal.message}")
    finally:
        await session.close()
    order = {key: _in_order(session, key) for key in ("A", "B")}
    merged = sorted(
        (event[0], key) for key in ("A", "B") for event in session.threads[key].events
    )
    switches = sum(1 for (_, one), (_, two) in zip(merged, merged[1:], strict=False) if one != two)
    return session.result(
        "recorded" if capped is None else "inconclusive",
        capped or "The order of Codex's events across two tasks is recorded.",
        in_order=order, threads_interleaved=switches > 1,
    )


def _in_order(session: Session, key: str) -> dict[str, Any]:
    wanted = (
        "item/started", "item/commandExecution/requestApproval", "serverRequest/resolved",
        "item/completed",
    )
    events = session.threads[key].events
    seen = [next((order for order, method, _ in events if method == name), None) for name in wanted]
    present = [order for order in seen if order is not None]
    return {"all_seen": None not in seen, "in_order": present == sorted(present)}


# ── K7: interrupt ────────────────────────────────────────────────────────────


async def k7(ctx: ScenarioContext) -> ScenarioResult:
    """A stopped turn ends `interrupted`, soon, and nothing it had open is left unanswered."""
    session = ctx.session("K7")
    command = Listed("sleep 30")
    timings = ctx.timings
    try:
        await session.start_thread("A", ctx.plan.project_a)
        session.expect("A", [command], hold_open=True)
        await session.start_turn("A", command_prompt("K7", [command]))
        asked = await session.drive(lambda: bool(session.pending), timings.turn_cap_seconds)
        if not asked:
            await session.interrupt("A")
            return session.result("inconclusive", "Codex never asked, so no request was open.")
        open_requests = list(session.pending)
        seconds = await session.interrupt("A", wait=timings.stop_cap_seconds)
        # Looked at before `close`, which cancels whatever is still open whatever K7 finds.
        left_open = [request for request in open_requests if request in session.pending]
    except CodexRpcError as refusal:
        return session.result("inconclusive", f"Codex refused a request: {refusal.message}")
    finally:
        await session.close()
    return _k7_verdict(ctx, session, open_requests, left_open, seconds)


def _k7_verdict(
    ctx: ScenarioContext,
    session: Session,
    open_requests: list[Any],
    left_open: list[Any],
    seconds: float | None,
) -> ScenarioResult:
    log = session.threads["A"]
    answered = {
        str(request): session.resolved_by_ravis[request]
        for request in open_requests if request in session.resolved_by_ravis
    }
    findings = {
        "seconds_to_end": seconds, "status": log.turn_status, "answered_by_ravis": answered,
        "resolved_by_codex": [str(request) for request in open_requests if request in log.resolved],
        "left_open": len(left_open),
    }
    cap = ctx.timings.stop_cap_seconds
    if seconds is None:
        return session.result("failed", f"The turn didn't end within {cap:g} s.", **findings)
    if log.turn_status != "interrupted":
        return session.result("failed", f"The turn ended {log.turn_status}.", **findings)
    if left_open:
        return session.result(
            "failed", "The turn ended with its approval still open: nothing answered it.",
            **findings,
        )
    who = "RAVIS answered its open request" if answered else "Codex let go of its open request"
    return session.result(
        "passed", f"The turn ended {seconds:.1f} s after it was stopped, and {who}.", **findings
    )


# ── K6: per-task clean-up in one shared process ──────────────────────────────


async def k6(ctx: ScenarioContext) -> ScenarioResult:
    """Stop A's commands: every A process gone, every B process alive; B's ended afterwards."""
    session = ctx.session("K6")
    commands = [Listed(text) for text in K6_COMMANDS]
    found: tuple[dict[int, Attributed], list[ProcessRow]] = ({}, [])
    # Both turns start after this moment: a command root's folder counts only for what began since.
    began = datetime.now(UTC)
    try:
        for project in ctx.plan.projects:
            await session.start_thread(project.label, project)
            session.expect(project.label, commands)
            await session.start_turn(project.label, command_prompt("K6", commands))
        found, started = await _wait_for_processes(ctx, session, K6_PROCESSES_PER_PROJECT, began)
        return await _stop_a(ctx, session, found, started)
    except (CodexRpcError, CodexUnavailableError) as refusal:
        return session.result("inconclusive", f"Codex refused a request: {refusal}")
    finally:
        await _end_everything(ctx, session, found)
        await session.close()


async def _wait_for_processes(
    ctx: ScenarioContext, session: Session, expected: int, began: datetime
) -> tuple[tuple[dict[int, Attributed], list[ProcessRow]], bool]:
    """Answer Codex and look at the process table until both projects show their commands."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ctx.timings.processes_start_seconds
    while True:
        await session.drive(lambda: False, 0.2)
        found = await _attributed(ctx, session, began)
        owners = [entry.owner for entry in found[0].values()]
        if all(owners.count(key) >= expected for key in ("A", "B")):
            return found, True
        if loop.time() >= deadline:
            return found, False


async def _attributed(
    ctx: ScenarioContext, session: Session, began: datetime
) -> tuple[dict[int, Attributed], list[ProcessRow]]:
    """(each project's processes, everything else of Codex's) by a task's Stop rule (R4).

    A process both projects claim is put with the unattributed: reported, never signalled.
    """
    pid = ctx.codex.app_server_pid()
    rows = await asyncio.to_thread(snapshot)
    if pid is None or rows is None:
        return {}, []
    folders = await asyncio.to_thread(read_cwds, [row.pid for row in descendants(rows, pid)])
    tasks = {key: TaskClaim(Path(os.path.realpath(log.project.root)), began)
             for key, log in session.threads.items()}
    result = attribute_processes(rows, app_server_pid=pid, tasks=tasks, cwds=folders or {},
                                 terminals=await _terminal_pids(session))
    return result.attributed, [*result.unattributed, *(a.row for a in result.ambiguous.values())]


async def _terminal_pids(session: Session) -> dict[int, str]:
    """Each background terminal's `osPid`, by project (Codex 0.154.0 lists none)."""
    terminals: dict[int, str] = {}
    for key, log in session.threads.items():
        listed = await session.request(
            "thread/backgroundTerminals/list", {"threadId": log.thread_id}
        )
        for terminal in _terminals(listed):
            if isinstance(terminal.get("osPid"), int):
                terminals[terminal["osPid"]] = key
    return terminals


async def _stop_a(
    ctx: ScenarioContext,
    session: Session,
    found: tuple[dict[int, Attributed], list[ProcessRow]],
    started: bool,
) -> ScenarioResult:
    attributed, unattributed = found
    a_rows = [entry.row for entry in attributed.values() if entry.owner == "A"]
    b_rows = [entry.row for entry in attributed.values() if entry.owner == "B"]
    strays = [row for row in unattributed if OUR_COMMANDS.search(row.args)]
    findings: dict[str, Any] = {
        "expected_per_project": K6_PROCESSES_PER_PROJECT,
        "found_per_project": {"A": len(a_rows), "B": len(b_rows)},
        "attribution": [
            {"project": entry.owner, "rule": entry.rule, "comm": entry.row.comm}
            for entry in attributed.values()
        ],
        "unattributed_commands": [row.comm for row in strays],
        "sandbox_arguments_seen": next(
            (entry.row.args for entry in attributed.values() if entry.rule == "sandbox_root"), None
        ),
    }
    if not started or not a_rows or not b_rows:
        return session.result(
            "inconclusive", "Calibration couldn't find both projects' commands running.", **findings
        )
    a_left = await _end_sequence(ctx, session, "A", a_rows)
    after = await asyncio.to_thread(snapshot) or []
    alive = {(row.pid, row.start) for row in after}
    b_gone = [row.comm for row in b_rows if (row.pid, row.start) not in alive]
    findings.update(a_survivors=[row.comm for row in a_left], b_stopped_with_a=b_gone)
    if a_left:
        return session.result("failed", "Some of project A's commands survived Stop.", **findings)
    if b_gone:
        return session.result("failed", "Stopping project A also stopped project B's.", **findings)
    if strays:
        return session.result(
            "failed", "A command no attribution rule found would survive its Stop.", **findings
        )
    return session.result(
        "passed", "Stopping project A ended all of A's commands and none of B's.", **findings
    )


async def _end_sequence(
    ctx: ScenarioContext, session: Session, key: str, rows: list[ProcessRow]
) -> list[ProcessRow]:
    """Design §4.5's end sequence for one thread: interrupt, terminate terminals, TERM, KILL."""
    log = session.threads[key]
    await session.interrupt(key)
    with contextlib.suppress(CodexRpcError, CodexUnavailableError):
        listed = await session.request(
            "thread/backgroundTerminals/list", {"threadId": log.thread_id}
        )
        for terminal in _terminals(listed):
            await session.request(
                "thread/backgroundTerminals/terminate",
                {"threadId": log.thread_id, "processId": str(terminal.get("processId"))},
            )
    return await end_processes(
        rows, term_wait=ctx.timings.term_wait_seconds, confirm_wait=ctx.timings.confirm_seconds
    )


async def _end_everything(
    ctx: ScenarioContext, session: Session, found: tuple[dict[int, Attributed], list[ProcessRow]]
) -> None:
    """Whatever happened, B's commands (and any A left) are ended before the scenario returns."""
    attributed, unattributed = found
    rows = [entry.row for entry in attributed.values()]
    rows += [row for row in unattributed if OUR_COMMANDS.search(row.args)]
    for key in session.threads:
        with contextlib.suppress(CodexRpcError, CodexUnavailableError):
            await _end_sequence(ctx, session, key, [])
    if rows:
        await end_processes(
            rows, term_wait=ctx.timings.term_wait_seconds, confirm_wait=ctx.timings.confirm_seconds
        )


def _terminals(listed: object) -> list[dict[str, Any]]:
    data = listed.get("data") if isinstance(listed, dict) else None
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


# ── K13: archive, unarchive, resume ──────────────────────────────────────────


async def k13(ctx: ScenarioContext) -> ScenarioResult:
    """Archive a finished thread, unarchive and resume it; a new turn sees the earlier history."""
    session = ctx.session("K13")
    plan, project = ctx.plan, ctx.plan.project_a
    try:
        log = await session.start_thread("A", project, ephemeral=False)
        session.expect("A")
        await session.start_turn("A", (
            f"This is RAVIS's calibration, question K13. Remember the word {plan.codeword}. "
            "Reply with just OK. Run no commands."
        ))
        if await session.run_turns(["A"]):
            return session.result("inconclusive", "The first turn didn't finish.")
        for method in ("thread/archive", "thread/unarchive"):
            await session.request(method, {"threadId": log.thread_id})
        await session.request("thread/resume", _resume_params(ctx, log.thread_id, project.root))
        await session.start_turn("A", (
            "This is RAVIS's calibration, question K13. What word did I ask you to remember? "
            "Reply with just that word. Run no commands."
        ))
        capped = await session.run_turns(["A"])
    except CodexRpcError as refusal:
        return session.result("failed", f"Codex refused to archive, unarchive or resume: "
                                        f"{refusal.message}")
    finally:
        await _archive_quietly(session)
        await session.close()
    if capped:
        return session.result("inconclusive", capped)
    if plan.codeword not in " ".join(session.threads["A"].agent_text[-1:]):
        return session.result("failed", "The resumed task didn't see its earlier history.")
    return session.result(
        "passed", "An archived task came back, resumed in RAVIS's process, and remembered."
    )


def _resume_params(ctx: ScenarioContext, thread_id: str, root: Path) -> dict[str, Any]:
    return {
        "threadId": thread_id, "cwd": str(root), "approvalPolicy": "untrusted",
        "permissions": ctx.profile_name, "runtimeWorkspaceRoots": [str(root)],
    }


async def _archive_quietly(session: Session) -> None:
    """Leave K13's thread archived, out of the way of live tasks."""
    log = session.threads.get("A")
    if log is not None:
        with contextlib.suppress(CodexRpcError, CodexUnavailableError):
            await session.codex.request("thread/archive", {"threadId": log.thread_id}, timeout=5.0)
