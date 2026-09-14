"""The agent-session half of FakeCodexAppServer: turns acted out line by line. Not Codex.

RAVIS's relay tests start a task whose first text begins with `RELAY`; the fake app-server hands
that turn here, and each following line is one thing Codex does, in the shapes of Codex 0.154.0's
schema (`codex-app-server-ts-0.154.0-alpha.6.2`):

| line | what the fake does |
|---|---|
| `say <text>` | an agent message: started, two deltas, completed |
| `run <command> => <output>` | a command that needs no approval: started, its output, completed |
| `fetch <host>` | a command reaching a site through Codex's pretend proxy: through, or blocked |
| `ask <command>` | a command approval in the project; runs it on accept, stops on cancel |
| `ask-network <host>` | a network approval, with the amendment Codex would propose for the host |
| `ask-outside <command>` | the same, in a folder outside the project |
| `edit <path>` | a file change and its approval |
| `edit-unseen <path>` | a file-change approval with no item saying what it writes |
| `grant <path>` | a permissions request to read a path |
| `question <id>`, `secret <id>` | a question, or one marked secret |
| `elicit` | an MCP server asking for input |
| `plan <step>`, `reroute` | a plan update; a model reroute |
| `steps <n>` | n commands completed back to back |
| `spawn` | a long-running command process carrying this project's sandbox root parameter |
| `fail <codexErrorInfo>` | the turn fails with that error |
| `wait` | the turn waits until it is interrupted |

Every answer RAVIS gives is logged (`relay_answer`), so a test can check exactly what reached
"Codex" — and an interrupted turn leaves its request open, as the real Codex does (K7).
`turn/steer` refuses while the scenario says `steer_refused`, and `thread/turns/list`
answers one completed turn whose command printed something that looks like a key, and
`config/batchWrite` answers as Codex 0.154.0 does (Cal-3): `okOverridden` when a launch flag already
sets the key it writes — a `domains` table inside the profile's `-c` network section — and `ok`
otherwise, unless the scenario says how Codex answers the owner's sites: `site_add_status` for an
upsert without a wildcard (every site write but RAVIS's defaults), `site_replace_status` for a
`replace`.

A relay thread reads the site list when it loads, as the calibration half's threads do: its first
turn, or the first after a resume reloads it (`sites_at_load`). So `fetch` reaches a site allowed
later only once RAVIS has reopened the thread — or never, with `thread_never_unloads`.
"""

from __future__ import annotations

import functools
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

LOOKS_LIKE_A_KEY = "token: " + "A" * 40


def handlers(api: Any) -> dict[str, Any]:
    return {
        "turn/steer": functools.partial(_steer, api),
        "thread/turns/list": functools.partial(_turns_list, api),
        "config/batchWrite": functools.partial(_batch_write, api),
    }


def _batch_write(api: Any, params: dict[str, Any]) -> dict[str, Any]:
    """A configuration write, as RAVIS writes its default sites or adds one; logged with status."""
    status = _write_status(api, params)
    api.log("config_written", params=params, status=status)
    return {"status": status, "version": "sha256:fake",
            "filePath": "<CODEX_HOME>/config.toml", "overriddenMetadata": None}


def _write_status(api: Any, params: dict[str, Any]) -> str:
    edits = [edit for edit in params.get("edits") or [] if isinstance(edit, dict)]
    if any(_launch_sets(str(edit.get("keyPath", ""))) for edit in edits):
        return "okOverridden"  # the command-line layer outranks the user configuration
    if len(edits) != 1:
        return "ok"
    strategy, sites = edits[0].get("mergeStrategy"), edits[0].get("value") or {}
    if strategy == "replace":
        return str(api.scenario.get("site_replace_status", "ok"))
    # RAVIS's defaults are the only write carrying a wildcard; every other upsert is the owner's.
    owners = strategy == "upsert" and not any(str(site).startswith("*.") for site in sites)
    return str(api.scenario.get("site_add_status", "ok")) if owners else "ok"


def _launch_sets(key_path: str) -> bool:
    """Whether a `-c` launch flag sets `key_path`: the key, or a table above it naming its leaf."""
    leaf = key_path.rpartition(".")[2]
    for argument in sys.argv:
        setting, _, value = argument.partition("=")
        if setting == key_path:
            return True
        if key_path.startswith(setting + ".") and re.search(rf"\b{re.escape(leaf)}\s*=", value):
            return True
    return False


def _steer(api: Any, params: dict[str, Any]) -> dict[str, Any] | str:
    api.log("steered", params=params)
    if api.scenario.get("steer_refused"):
        return "no active turn to steer"
    return {"turnId": params.get("expectedTurnId")}


def _turns_list(api: Any, params: dict[str, Any]) -> dict[str, Any] | str:
    thread = api.state["threads"].get(params.get("threadId"))
    if thread is None:
        return "thread not found"
    items = [
        {"type": "agentMessage", "id": "msg-history-1", "text": "STEP: 1. Look", "phase": None},
        {"type": "commandExecution", "id": "cmd-history-1", "command": "cat settings.txt",
         "cwd": thread["cwd"], "status": "completed", "commandActions": [],
         "aggregatedOutput": LOOKS_LIKE_A_KEY, "exitCode": 0, "durationMs": 3},
    ]
    turn = {"id": "turn-history-1", "items": items, "itemsView": "summary", "status": "completed",
            "error": None, "startedAt": 0, "completedAt": 1, "durationMs": 1000}
    return {"data": [turn], "nextCursor": None, "backwardsCursor": None}


def turn(api: Any, thread_id: str, turn_id: str, prompt: str, stop: threading.Event
         ) -> dict[str, Any] | None:
    """Act out the turn's lines; the turn's error when a line fails it, else None."""
    thread = api.state["threads"][thread_id]
    # The site list this load of the thread reads: set by its first turn after it was loaded.
    thread.setdefault("sites_at_load", dict(api.user_sites()))
    context = {"api": api, "thread_id": thread_id, "turn_id": turn_id, "stop": stop,
               "cwd": thread["cwd"]}
    for line in prompt.splitlines()[1:]:
        if stop.is_set():
            return None
        word, _, rest = line.strip().partition(" ")
        action = ACTIONS.get(word)
        if action is None:
            continue
        error = action(context, rest)
        if error is not None:
            return error
    return None


# ── Items ────────────────────────────────────────────────────────────────────


def _notify(context: dict[str, Any], method: str, **fields: Any) -> None:
    context["api"].notify(method, {"threadId": context["thread_id"],
                                   "turnId": context["turn_id"], **fields})


def _started(context: dict[str, Any], item: dict[str, Any]) -> None:
    _notify(context, "item/started", item=item, startedAtMs=0)


def _completed(context: dict[str, Any], item: dict[str, Any]) -> None:
    _notify(context, "item/completed", item=item, completedAtMs=0)


def _command_item(context: dict[str, Any], command: str, cwd: str | None = None
                  ) -> dict[str, Any]:
    return {"type": "commandExecution", "id": f"cmd-{uuid.uuid4().hex[:8]}", "command": command,
            "cwd": cwd or context["cwd"], "status": "inProgress", "commandActions": [],
            "processId": None, "aggregatedOutput": None, "exitCode": None, "durationMs": None}


def _finish_command(context: dict[str, Any], item: dict[str, Any], output: str | None) -> None:
    if output is None:
        item.update(status="declined")
    else:
        _notify(context, "item/commandExecution/outputDelta", itemId=item["id"], delta=output)
        item.update(status="completed", aggregatedOutput=output, exitCode=0, durationMs=5)
    _completed(context, item)


def _ask(context: dict[str, Any], method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Ask RAVIS and wait for its answer — or, once the turn is interrupted, stop waiting.

    **As the real Codex does** (calibration K7, `cal_d2185ed08f50`): an interrupted turn ends at
    once *without* resolving its open request — no `serverRequest/resolved` — so RAVIS must answer
    and publish every open request itself. An answer is followed by `serverRequest/resolved`.
    """
    api = context["api"]
    request_id = f"srv-{next(api.server_ids)}"
    full = {"threadId": context["thread_id"], "turnId": context["turn_id"], "startedAtMs": 0,
            **params}
    api.send({"id": request_id, "method": method, "params": full})
    deadline = time.monotonic() + 30.0
    with api.answered:
        while request_id not in api.answers:
            if context["stop"].is_set() or time.monotonic() > deadline:
                api.log("relay_left_open", method=method, request_id=request_id)
                return None
            api.answered.wait(0.05)
        answer = api.answers.pop(request_id)
    resolved = {"threadId": context["thread_id"], "requestId": request_id}
    api.notify("serverRequest/resolved", resolved)
    result = answer.get("result") if isinstance(answer.get("result"), dict) else None
    api.log("relay_answer", method=method, request_id=request_id, answer=result)
    return result


def _decision(answer: dict[str, Any] | None, context: dict[str, Any]) -> str | None:
    decision = (answer or {}).get("decision")
    if decision == "cancel":
        # Codex ends the turn when a request is cancelled.
        context["stop"].set()
    return decision if isinstance(decision, str) else None


# ── Actions ──────────────────────────────────────────────────────────────────


def say(context: dict[str, Any], text: str) -> None:
    item = {"type": "agentMessage", "id": f"msg-{uuid.uuid4().hex[:8]}", "text": "",
            "phase": None}
    _started(context, item)
    half = len(text) // 2
    for piece in (text[:half], text[half:]):
        _notify(context, "item/agentMessage/delta", itemId=item["id"], delta=piece)
    _completed(context, {**item, "text": text})


def run(context: dict[str, Any], rest: str) -> None:
    command, _, output = rest.partition(" => ")
    output = LOOKS_LIKE_A_KEY if output == "KEY" else output
    item = _command_item(context, command)
    _started(context, item)
    _finish_command(context, item, output)


def fetch(context: dict[str, Any], host: str) -> None:
    """A command reaching a site through Codex's pretend proxy (the calibration half's).

    Through when the thread's list, as it was when the thread loaded, allows the host; otherwise the
    proxy's fixed blocked line, which RAVIS turns into a site ask.
    """
    api = context["api"]
    item = _command_item(context, f"curl -sI https://{host}")
    _started(context, item)
    reached = api.site_allowed(api.state["threads"][context["thread_id"]], host)
    _finish_command(context, item,
                    f"HTTP/2 200 from {host}" if reached else api.site_blocked.format(host=host))


def ask(context: dict[str, Any], command: str, cwd: str | None = None) -> None:
    item = _command_item(context, command, cwd)
    _started(context, item)
    answer = _ask(context, "item/commandExecution/requestApproval", {
        "kind": "command", "itemId": item["id"], "environmentId": None, "command": command,
        "cwd": cwd or context["cwd"], "reason": "The task needs it.",
    })
    decision = _decision(answer, context)
    _finish_command(context, item, f"ran: {command}" if decision == "accept" else None)


def ask_network(context: dict[str, Any], host: str) -> None:
    """A network approval in the schema's shape, with a proposed amendment (K3 saw none)."""
    item = _command_item(context, f"curl https://{host}")
    _started(context, item)
    amendment = {"host": host, "action": "allow"}
    answer = _ask(context, "item/commandExecution/requestApproval", {
        "kind": "command", "itemId": item["id"], "environmentId": None,
        "command": item["command"], "cwd": context["cwd"], "reason": f"Reach {host}.",
        "networkApprovalContext": {"host": host, "protocol": "https"},
        "proposedNetworkPolicyAmendments": [amendment],
        "availableDecisions": [
            "accept", {"applyNetworkPolicyAmendment": {"network_policy_amendment": amendment}},
            "decline", "cancel",
        ],
    })
    decision = (answer or {}).get("decision")
    ran = decision not in (None, "decline", "cancel")
    _finish_command(context, item, "HTTP 200" if ran else None)


def ask_outside(context: dict[str, Any], command: str) -> None:
    ask(context, command, cwd=str(Path(context["cwd"]).parent))


def edit(context: dict[str, Any], path: str) -> None:
    changes = [{"path": path, "kind": {"type": "update", "move_path": None},
                "diff": "@@ -1 +1,2 @@\n-old\n+new\n+more"}]
    item = {"type": "fileChange", "id": f"patch-{uuid.uuid4().hex[:8]}", "changes": changes,
            "status": "inProgress"}
    _started(context, item)
    answer = _ask(context, "item/fileChange/requestApproval",
                  {"itemId": item["id"], "reason": "Add the flag.", "grantRoot": None})
    decision = _decision(answer, context)
    _completed(context, {**item, "status": "completed" if decision == "accept" else "declined"})


def edit_unseen(context: dict[str, Any], path: str) -> None:
    """A file-change approval whose item never says what it writes (calibration K12's order)."""
    answer = _ask(context, "item/fileChange/requestApproval",
                  {"itemId": f"patch-{uuid.uuid4().hex[:8]}", "reason": f"Change {path}.",
                   "grantRoot": None})
    _decision(answer, context)


def grant(context: dict[str, Any], path: str) -> None:
    _ask(context, "item/permissions/requestApproval", {
        "itemId": f"perm-{uuid.uuid4().hex[:8]}", "environmentId": None, "cwd": context["cwd"],
        "reason": "Read a file.",
        "permissions": {"network": None, "fileSystem": {"read": [path], "write": None}},
    })


def _question(context: dict[str, Any], question_id: str, secret: bool) -> None:
    options = [{"label": "Yes", "description": ""}, {"label": "No", "description": ""}]
    _ask(context, "item/tool/requestUserInput", {
        "itemId": f"ask-{uuid.uuid4().hex[:8]}", "isBlocking": True, "autoResolutionMs": None,
        "questions": [{"id": question_id, "header": "Log timestamps",
                       "question": "Should --utc also change the log timestamps?",
                       "isOther": True, "isSecret": secret, "options": options}],
    })


def question(context: dict[str, Any], question_id: str) -> None:
    _question(context, question_id, secret=False)


def secret(context: dict[str, Any], question_id: str) -> None:
    _question(context, question_id, secret=True)


def elicit(context: dict[str, Any], _rest: str) -> None:
    _ask(context, "mcpServer/elicitation/request", {"serverName": "fake", "message": "Token?"})


def plan(context: dict[str, Any], step: str) -> None:
    _notify(context, "turn/plan/updated", explanation=None,
            plan=[{"step": step, "status": "inProgress"}])


def reroute(context: dict[str, Any], _rest: str) -> None:
    _notify(context, "model/rerouted", fromModel="gpt-6-astra", toModel="gpt-6-astra-mini",
            reason="highRiskCyberActivity")


def steps(context: dict[str, Any], count: str) -> None:
    for index in range(int(count)):
        if context["stop"].is_set():
            return
        item = _command_item(context, f"echo step {index}")
        _started(context, item)
        _finish_command(context, item, f"step {index}")
        time.sleep(0.02)


def spawn(context: dict[str, Any], _rest: str) -> None:
    """A long-running command, shaped like a real Codex child (calibration K6, Codex 0.154.0).

    Codex runs `/bin/zsh -lc '<command>'` through `sandbox-exec` in a pseudo-terminal: its own
    session, started in the project's folder, a child of the app-server — and because the wrapper
    replaces itself, **no sandbox parameter shows in its arguments**. A shell with a `sleep` child.
    """
    root = Path(context["cwd"]).resolve()
    process = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 60; true"], cwd=root, start_new_session=True,
    )
    context["api"].log("relay_spawned", pid=process.pid, root=str(root))
    # Collected when it exits, as Codex collects its commands, so it never lingers as a zombie.
    threading.Thread(target=process.wait, daemon=True).start()


def fail(_context: dict[str, Any], info: str) -> dict[str, Any]:
    return {"message": "Codex failed as scripted.", "codexErrorInfo": info,
            "additionalDetails": None, "misalignment": None}


def wait(context: dict[str, Any], _rest: str) -> None:
    context["stop"].wait(60)


ACTIONS: dict[str, Any] = {
    "say": say, "run": run, "fetch": fetch, "ask": ask, "ask-network": ask_network,
    "ask-outside": ask_outside, "edit": edit,
    "edit-unseen": edit_unseen, "grant": grant,
    "question": question, "secret": secret, "elicit": elicit, "plan": plan, "reroute": reroute,
    "steps": steps, "spawn": spawn, "fail": fail, "wait": wait,
}
