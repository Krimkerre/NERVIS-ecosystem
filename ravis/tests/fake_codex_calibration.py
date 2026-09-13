"""The fake app-server's calibration half: a pretend sandbox, pretend turns (runbook §7). Not Codex.

`fake_codex_app_server.py` hands a calibration prompt, and every calibration-only method, to this
module. It acts out what calibration asks of a real Codex, closely enough that each scenario's pass
**and** fail path can be driven from a test:

- **A pretend sandbox.** Reads are refused under any path the profile's `-c` flags deny (read back
  from the fake's own command line, as `"<path>"="deny"` pairs, `**/name` globs included); writes
  are allowed only inside the thread's workspace roots — the explicit ones, else its `cwd`. Commands
  are recognised by shape (`cat`, `head -c`, `printf … >`, `mkdir -p`, `curl`, `git commit`,
  `sh -c`) and carried out for real, but only on files under the test's own folder.
- **Pretend turns.** Numbered commands are asked about one at a time, with `additionalPermissions`
  for the escalated ones, a network approval for `curl`, `serverRequest/resolved` after each answer;
  a file to create asks for a file-change approval; a permissions line asks for a grant; a word to
  remember is remembered on the thread and answered back after a resume.
- **Real long-running processes for K6**, `sh -c 'sleep 600; true'` each tagged with its project's
  `WRITABLE_ROOT_0=<root>` or listed as a background terminal, all ended when the fake exits.

**Faults** (`calibration_faults` in the scenario) switch one behaviour to the failing one:
`plugins_on`, `exec_leaks`, `decoy_readable_escalated`, `roots_from_cwd`, `deny_loses_to_write`,
`untrusted_skips_file_approval`, `never_asks_file_approval`, `approved_escapes_box`,
`thread_tmpdir_ignored`, `network_open`, `empty_grant_grants`, `git_blocked`, `interrupt_ignored`,
`stop_kills_other_project`, `resume_forgets`, `profile_rejected`.
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DENY = re.compile(r'"([^"]+)"\s*=\s*"deny"')
NUMBERED = re.compile(r"^(\d+)\. (.+)$")
ESCALATE = re.compile(r"^Ask for escalated permissions when you run commands (.+)\.$")
GRANT = re.compile(r"^Before running command (\d+), ask for permission to read (\S+) with")
CREATE = re.compile(r"Create the file (\S+) containing the line (\S+),")
REMEMBER = re.compile(r"Remember the word (\S+)\.")
LONG_RUNNING = {
    "sleep 600 &": ("ravis-fake-background", False, True),
    "script -q /dev/null sleep 600": ("ravis-fake-pty", True, False),
    "python3 -m http.server 0 --bind 127.0.0.1": ("ravis-fake-server", True, True),
    "sleep 600": ("ravis-fake-foreground", True, False),
}
PROCESSES: list[dict[str, Any]] = []


def faults(api: Any) -> set[str]:
    return set(api.scenario.get("calibration_faults", []))


def profile_rejected(api: Any) -> bool:
    return "profile_rejected" in faults(api) and any("permissions." in a for a in sys.argv)


# ── The pretend sandbox ──────────────────────────────────────────────────────


def denied(path: Path) -> bool:
    for pattern in DENY.findall(" ".join(sys.argv)):
        if pattern.startswith("**/") and pattern[3:] in path.parts:
            return True
        folder = Path(pattern)
        if path == folder or folder in path.parents:
            return True
    return False


def inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def roots(api: Any, thread: dict[str, Any]) -> list[Path]:
    explicit = thread.get("roots")
    if explicit:
        return [Path(root).resolve() for root in explicit]
    cwd = Path(thread["cwd"]).resolve()
    return [cwd.parent] if "roots_from_cwd" in faults(api) else [cwd]


def may_read(api: Any, thread: dict[str, Any], path: Path, escalated: bool) -> bool:
    found = faults(api)
    if not denied(path) or thread.get("leaks") or str(path) in thread.get("granted", ()):
        return True
    if "decoy_readable_escalated" in found and escalated:
        return True
    return "deny_loses_to_write" in found and any(inside(path, r) for r in roots(api, thread))


def may_write(api: Any, thread: dict[str, Any], path: Path, approved: bool) -> bool:
    found = faults(api)
    if thread.get("leaks") or ("approved_escapes_box" in found and approved):
        return True
    within = any(inside(path, root) for root in roots(api, thread))
    if denied(path):
        return within and "deny_loses_to_write" in found
    return within


def run(api: Any, thread: dict[str, Any], text: str, *, escalated: bool, approved: bool,
        network: bool) -> tuple[int, str]:
    """Carry out one command's text in the pretend sandbox: (exit code, output)."""
    if " && " in text:
        results = [run(api, thread, part, escalated=escalated, approved=approved, network=network)
                   for part in text.split(" && ")]
        return max(code for code, _ in results), "".join(output for _, output in results)
    words = shlex.split(text)
    cwd = Path(thread["cwd"])
    kind = _kind(words)
    if kind == "shell":
        return run(api, thread, words[2], escalated=escalated, approved=approved, network=network)
    if kind == "read":
        return _read(api, thread, (cwd / words[-1]).resolve(), escalated)
    if kind == "write":
        return _write(api, thread, cwd, words, approved)
    return _other(api, words, network)


def _kind(words: list[str]) -> str:
    if words[:2] in (["/bin/sh", "-c"], ["sh", "-c"]):
        return "shell"
    if words and (Path(words[0]).name == "cat" or words[:2] == ["head", "-c"]):
        return "read"
    if words and words[0] in ("printf", "mkdir"):
        return "write"
    return "other"


def _read(api: Any, thread: dict[str, Any], path: Path, escalated: bool) -> tuple[int, str]:
    if not may_read(api, thread, path, escalated):
        return 1, "Operation not permitted"
    return (0, path.read_text()) if path.exists() else (1, "No such file or directory")


def _write(api: Any, thread: dict[str, Any], cwd: Path, words: list[str], approved: bool
           ) -> tuple[int, str]:
    if words[0] == "mkdir":
        target = (cwd / words[-1]).resolve()
        if not may_write(api, thread, target, approved):
            return 1, "Operation not permitted"
        target.mkdir(parents=True, exist_ok=True)
        return 0, ""
    if words[1:2] == ["%s\\n"]:
        return 0, _tmpdir(api, thread) + "\n"
    arrow = ">>" if ">>" in words else ">"
    target = (cwd / words[words.index(arrow) + 1]).resolve()
    if not may_write(api, thread, target, approved):
        return 1, "Operation not permitted"
    with target.open("a" if arrow == ">>" else "w") as handle:
        handle.write(words[1].replace("\\n", "\n"))
    return 0, ""


def _tmpdir(api: Any, thread: dict[str, Any]) -> str:
    policy = (thread.get("config") or {}).get("shell_environment_policy", {})
    given = policy.get("set", {}).get("TMPDIR")
    if given and "thread_tmpdir_ignored" not in faults(api):
        return str(given)
    return os.environ.get("TMPDIR", "")


def _other(api: Any, words: list[str], network: bool) -> tuple[int, str]:
    if words and words[0] == "curl":
        return _curl(api, words, network)
    if words and words[0] == "git":
        return (128, "Operation not permitted") if "git_blocked" in faults(api) else (0, "")
    return 0, ""


def _curl(api: Any, words: list[str], network: bool) -> tuple[int, str]:
    if not (network or "network_open" in faults(api)):
        return 6, "Could not resolve host"
    url = next(word for word in words if word.startswith("http"))
    if not url.startswith("http://127.0.0.1"):
        return 0, "200"  # an outside host is never contacted by a test
    with urllib.request.urlopen(url, timeout=3) as answer:  # noqa: S310 — the test's own listener
        return 0, answer.read().decode()


# ── Asking RAVIS ─────────────────────────────────────────────────────────────


def ask_until(api: Any, method: str, params: dict[str, Any], stop: threading.Event
              ) -> dict[str, Any] | None:
    """Ask RAVIS and wait for the answer; a stopped turn resolves the request instead."""
    request_id = f"srv-{next(api.server_ids)}"
    api.send({"id": request_id, "method": method, "params": params})
    deadline = time.monotonic() + 30.0
    with api.answered:
        while request_id not in api.answers:
            if stop.is_set() and "interrupt_ignored" not in faults(api):
                api.notify("serverRequest/resolved",
                           {"threadId": params["threadId"], "requestId": request_id})
                return None
            if time.monotonic() > deadline:
                return None
            api.answered.wait(0.05)
        answer = api.answers.pop(request_id)
    api.notify("serverRequest/resolved", {"threadId": params["threadId"], "requestId": request_id})
    return answer.get("result") if isinstance(answer.get("result"), dict) else None


# ── Turns ────────────────────────────────────────────────────────────────────


def turn(api: Any, thread_id: str, turn_id: str, prompt: str, params: dict[str, Any],
         stop: threading.Event) -> None:
    thread = api.state["threads"][thread_id]
    lines = prompt.splitlines()
    escalated = {int(n) for line in lines if (m := ESCALATE.match(line))
                 for n in re.findall(r"\d+", m.group(1))}
    context = {"api": api, "thread": thread, "thread_id": thread_id, "turn_id": turn_id,
               "stop": stop, "policy": params.get("approvalPolicy") or thread["policy"]}
    _conversation(context, prompt)
    grants = {int(m.group(1)): m.group(2) for line in lines if (m := GRANT.match(line))}
    for line in lines:
        numbered = NUMBERED.match(line)
        if numbered is None or stop.is_set():
            continue
        index = int(numbered.group(1))
        if index in grants:
            _ask_grant(context, grants[index])
        _command(context, numbered.group(2), index in escalated)


def _conversation(context: dict[str, Any], prompt: str) -> None:
    api, thread = context["api"], context["thread"]
    created, remember = CREATE.search(prompt), REMEMBER.search(prompt)
    if created:
        _file_change(context, created.group(1), created.group(2))
    if remember:
        thread["remembered"] = remember.group(1)
        _say(context, "OK")
    if "What word did I ask you to remember?" in prompt:
        _say(context, thread.get("remembered") or "I don't know.")
    del api


def _say(context: dict[str, Any], text: str) -> None:
    item = {"type": "agentMessage", "id": str(uuid.uuid4()), "text": text}
    context["api"].notify("item/completed", {
        "threadId": context["thread_id"], "turnId": context["turn_id"], "item": item,
    })


def _file_change(context: dict[str, Any], name: str, content: str) -> None:
    api, thread = context["api"], context["thread"]
    path = Path(thread["cwd"]) / name
    item = {"type": "fileChange", "id": str(uuid.uuid4()),
            "changes": [{"path": str(path), "kind": "add"}], "status": "inProgress"}
    base = {"threadId": context["thread_id"], "turnId": context["turn_id"]}
    api.notify("item/started", {**base, "item": item})
    found = faults(api)
    asks = "never_asks_file_approval" not in found and not (
        "untrusted_skips_file_approval" in found and context["policy"] == "untrusted")
    if asks:
        answer = ask_until(api, "item/fileChange/requestApproval",
                           {**base, "itemId": item["id"], "startedAtMs": 0}, context["stop"])
        if (answer or {}).get("decision") != "accept":
            api.notify("item/completed", {**base, "item": {**item, "status": "declined"}})
            return
    path.write_text(content + "\n")
    api.notify("item/completed", {**base, "item": {**item, "status": "completed"}})


def _ask_grant(context: dict[str, Any], path: str) -> None:
    api, thread = context["api"], context["thread"]
    params = {"threadId": context["thread_id"], "turnId": context["turn_id"], "itemId": "grant",
              "cwd": thread["cwd"], "startedAtMs": 0,
              "permissions": {"fileSystem": {"read": [path]}}}
    answer = ask_until(api, "item/permissions/requestApproval", params, context["stop"]) or {}
    granted = ((answer.get("permissions") or {}).get("fileSystem") or {}).get("read") or []
    if "empty_grant_grants" in faults(api):
        granted = [path]
    thread.setdefault("granted", set()).update(granted)


def _command(context: dict[str, Any], text: str, escalated: bool) -> None:
    api, thread = context["api"], context["thread"]
    base = {"threadId": context["thread_id"], "turnId": context["turn_id"]}
    item = {"type": "commandExecution", "id": str(uuid.uuid4()), "command": text,
            "cwd": thread["cwd"], "status": "inProgress", "commandActions": []}
    api.notify("item/started", {**base, "item": item, "startedAtMs": 0})
    decision = _approval(context, item, text, escalated)
    if decision not in ("accept", "acceptForSession"):
        api.notify("item/completed", {**base, "item": {**item, "status": "declined"}})
        return
    network = text.startswith("curl") and _network_approval(context, item, text)
    if text in LONG_RUNNING:
        _spawn(context, item, text)
    code, output = run(api, thread, text, escalated=escalated, approved=True, network=network)
    done = {**item, "status": "completed" if code == 0 else "failed", "exitCode": code,
            "aggregatedOutput": output}
    api.notify("item/completed", {**base, "item": done, "completedAtMs": 0})


def _approval(context: dict[str, Any], item: dict[str, Any], text: str, escalated: bool
              ) -> str | None:
    api, thread, policy = context["api"], context["thread"], context["policy"]
    if text in thread.setdefault("session_grants", set()):
        return "accept"
    if isinstance(policy, dict) and escalated:
        return "accept"  # granular sandbox_approval:false: runs in the box, unasked
    if policy == "on-request" and not escalated:
        return "accept"
    params = {"threadId": context["thread_id"], "turnId": context["turn_id"],
              "itemId": item["id"], "startedAtMs": 0, "command": text, "cwd": thread["cwd"]}
    if escalated:
        params["additionalPermissions"] = {"fileSystem": {"write": ["/"]}}
    decision = (ask_until(api, "item/commandExecution/requestApproval", params,
                          context["stop"]) or {}).get("decision")
    if decision == "acceptForSession":
        thread["session_grants"].add(text)
    return decision if isinstance(decision, str) else None


def _network_approval(context: dict[str, Any], item: dict[str, Any], text: str) -> bool:
    if "network_open" in faults(context["api"]):
        return True
    host = re.search(r"https?://([^/:]+)", text)
    params = {"threadId": context["thread_id"], "turnId": context["turn_id"],
              "itemId": item["id"], "startedAtMs": 0, "command": text,
              "cwd": context["thread"]["cwd"],
              "networkApprovalContext": {"host": host.group(1) if host else "", "protocol": "http"}}
    answer = ask_until(context["api"], "item/commandExecution/requestApproval", params,
                       context["stop"]) or {}
    return answer.get("decision") == "accept"


def _spawn(context: dict[str, Any], item: dict[str, Any], text: str) -> None:
    tag, terminal, sandboxed = LONG_RUNNING[text]
    thread = context["thread"]
    extra = [f"WRITABLE_ROOT_0={Path(thread['cwd']).resolve()}"] if sandboxed else []
    process = subprocess.Popen(["/bin/sh", "-c", "sleep 600; true", tag, *extra],
                               start_new_session=True)
    record = {"thread_id": context["thread_id"], "process": process, "terminal": terminal,
              "processId": str(uuid.uuid4()), "command": text, "itemId": item["id"]}
    PROCESSES.append(record)
    context["api"].log("spawned", pid=process.pid)
    _start_reaper()
    if text == "sleep 600":
        while process.poll() is None and not context["stop"].wait(0.05):
            continue


_REAPER: list[threading.Thread] = []


def _start_reaper() -> None:
    """Collect each spawned command once it exits, as Codex does, so none lingers as a zombie."""
    if _REAPER:
        return

    def reap() -> None:
        while True:
            for record in list(PROCESSES):
                record["process"].poll()
            time.sleep(0.05)

    _REAPER.append(threading.Thread(target=reap, daemon=True))
    _REAPER[0].start()


def end_processes() -> None:
    for record in PROCESSES:
        if record["process"].poll() is None:
            try:
                os.killpg(record["process"].pid, signal.SIGKILL)
            except OSError:
                continue


# ── Calibration-only methods ─────────────────────────────────────────────────


def _config_read(api: Any, _params: dict[str, Any]) -> dict[str, Any]:
    on = "plugins_on" in faults(api)
    origin = "user" if on else "sessionFlags"
    return {"config": {"features": {"plugins": on}},
            "origins": {"features.plugins": {"name": {"type": origin}, "version": "x"}}}


def _archive(api: Any, params: dict[str, Any]) -> dict[str, Any] | str:
    thread = api.state["threads"].get(params.get("threadId"))
    if thread is None or thread.get("ephemeral"):
        return "thread cannot be archived"
    thread["archived"] = True
    api.notify("thread/archived", {"threadId": params["threadId"]})
    return {}


def _unarchive(api: Any, params: dict[str, Any]) -> dict[str, Any] | str:
    thread = api.state["threads"].get(params.get("threadId"))
    if thread is None:
        return "thread not found"
    thread["archived"] = False
    return {"thread": {"id": params["threadId"], "cwd": thread["cwd"]}}


def _resume(api: Any, params: dict[str, Any]) -> dict[str, Any] | str:
    thread = api.state["threads"].get(params.get("threadId"))
    if thread is None or thread.get("archived"):
        return "thread not found or archived"
    if "resume_forgets" in faults(api):
        thread.pop("remembered", None)
    thread["roots"] = params.get("runtimeWorkspaceRoots") or thread.get("roots")
    return {"thread": {"id": params["threadId"], "cwd": thread["cwd"]}, "cwd": thread["cwd"],
            "model": "gpt-6-astra", "modelProvider": "openai", "approvalPolicy": "untrusted",
            "approvalsReviewer": "user", "sandbox": {"type": "workspaceWrite"}}


def _terminals(_api: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"data": [
        {"processId": r["processId"], "osPid": r["process"].pid, "command": r["command"],
         "cwd": "", "itemId": r["itemId"]}
        for r in PROCESSES
        if r["thread_id"] == params.get("threadId") and r["terminal"]
        and r["process"].poll() is None
    ], "nextCursor": None}


def _terminate(api: Any, params: dict[str, Any]) -> dict[str, Any]:
    spread = "stop_kills_other_project" in faults(api)
    for record in PROCESSES:
        mine = record["processId"] == params.get("processId")
        other = spread and record["thread_id"] != params.get("threadId")
        if (mine or other) and record["process"].poll() is None:
            os.killpg(record["process"].pid, signal.SIGTERM)
    return {}


def _steer(_api: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {"turnId": params.get("expectedTurnId")}


METHODS = {
    "config/read": _config_read, "thread/archive": _archive, "thread/unarchive": _unarchive,
    "thread/resume": _resume, "thread/backgroundTerminals/list": _terminals,
    "thread/backgroundTerminals/terminate": _terminate, "turn/steer": _steer,
}


def handlers(api: Any) -> dict[str, Any]:
    return {method: functools.partial(handler, api) for method, handler in METHODS.items()}


def command_exec(api: Any, params: dict[str, Any]) -> dict[str, Any]:
    """`command/exec` in calibration: the pretend sandbox, the cwd its only root."""
    thread = {"cwd": params.get("cwd"), "roots": [params.get("cwd")],
              "leaks": "exec_leaks" in faults(api)}
    text = shlex.join(str(part) for part in params.get("command", []))
    code, output = run(api, thread, text, escalated=False, approved=False, network=False)
    return {"exitCode": code, "stdout": "" if code else output, "stderr": output if code else ""}
