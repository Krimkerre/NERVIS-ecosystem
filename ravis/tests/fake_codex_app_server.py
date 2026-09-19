"""FakeCodexAppServer: a stand-in `codex app-server` for RAVIS's tests (runbook §7). Not Codex.

RAVIS runs this as a real program, through the fake `codex` script in `tests/codex_fakes.py`, so the
supervisor, the connection and the routes meet a real process with real pipes — nothing in RAVIS is
patched to reach it. It answers in the shapes the protocol probe recorded against Codex 0.154.0
(`codex-probe-transcript.jsonl`, brief §10): newline-delimited JSON-RPC without `"jsonrpc"`,
`emittedAtMs` on every notification, `-32600` for a request before `initialize`, no answer at all
to a malformed line.

**What a test controls.**
- A *scenario*, read at start: the account, the allowance, and which failures to act out — exit at
  start, never answer some methods (`silent_methods`) or refuse them at once (`refused_methods`),
  answer `initialize` as another version or home, refuse the sign-in as "already in use", and how
  a re-test turn behaves.
- *Commands* dropped into a control folder while it runs, carried out in order and deleted first
  (so a restarted fake never repeats one): send a notification, crash, stop answering, complete or
  fail the sign-in, swap the account, change the allowance.

**What a test reads.** Every line it received, and its arguments and environment at start, as JSON
lines in its log.

Nothing here touches a real Codex home or account: the "account" is a scenario value, and the
sandbox it pretends to enforce only ever reads or writes files the test created under `tmp_path`.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sys
import threading
import time
import types
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Run as a program, so its own folder is on sys.path: calibration's half lives beside it.
import fake_codex_calibration as calibration
import fake_codex_relay as relay
import fake_codex_skills as skills

SCENARIO = json.loads(Path(os.environ["FAKE_CODEX_SCENARIO"]).read_text())
CONTROL = Path(os.environ["FAKE_CODEX_CONTROL"])
LOG = Path(os.environ["FAKE_CODEX_LOG"])
VERSION = SCENARIO.get("user_agent_version", "0.154.0")
COMMAND_LINE = re.compile(r"^(\d)\. (.+)$")


def _model(model_id: str, name: str, *, default: bool, hidden: bool = False) -> dict[str, Any]:
    return {
        "id": model_id, "model": model_id, "displayName": name, "hidden": hidden,
        "isDefault": default, "defaultReasoningEffort": "low",
        "supportedReasoningEfforts": [
            {"reasoningEffort": "low", "description": "Fast responses with lighter reasoning"},
            {"reasoningEffort": "medium", "description": "Balances speed and reasoning depth"},
        ],
    }


MODELS = [
    _model("gpt-6-astra", "GPT-6-Astra", default=True),
    _model("gpt-5.6-sol", "GPT-5.6-Sol", default=False),
    _model("hidden-model", "Hidden", default=False, hidden=True),
]

output_lock = threading.Lock()
state: dict[str, Any] = {
    "initialized": False,
    "silent": set(SCENARIO.get("silent_methods", [])),
    "account": SCENARIO.get("account"),
    "account_id": SCENARIO.get("account_id"),
    "rate_limits": SCENARIO.get("rate_limits"),
    "login_id": None,
    "threads": {},
    "interrupts": {},
}
answers: dict[Any, dict[str, Any]] = {}
answered = threading.Condition()
server_ids = iter(range(1, 1_000_000))


def log(kind: str, **facts: Any) -> None:
    with LOG.open("a") as handle:
        handle.write(json.dumps({"kind": kind, **facts}) + "\n")


def send(message: dict[str, Any]) -> None:
    with output_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def notify(method: str, params: dict[str, Any]) -> None:
    send({"method": method, "params": params, "emittedAtMs": int(time.time() * 1000)})


def notify_soon(method: str, params: dict[str, Any]) -> None:
    """Notify just after the answer that caused it, as Codex orders them."""
    threading.Timer(0.01, notify, args=(method, params)).start()


def ask(method: str, params: dict[str, Any], seconds: float = 30.0) -> dict[str, Any] | None:
    """Send one of Codex's requests to RAVIS and wait for its answer."""
    request_id = f"srv-{next(server_ids)}"
    send({"id": request_id, "method": method, "params": params})
    deadline = time.monotonic() + seconds
    with answered:
        while request_id not in answers:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            answered.wait(left)
        return answers.pop(request_id)


def error(request_id: Any, code: int, message: str) -> None:
    send({"error": {"code": code, "message": message}, "id": request_id})


# ── Account and sign-in ──────────────────────────────────────────────────────


def account_read(_params: dict[str, Any]) -> dict[str, Any]:
    return {"account": state["account"], "requiresOpenaiAuth": True}


def rate_limits_read(_params: dict[str, Any]) -> dict[str, Any] | str:
    if state["account"] is None:
        return "codex account authentication required to read rate limits"
    return {
        "rateLimits": state["rate_limits"] or {"primary": None, "secondary": None},
        "rateLimitsByLimitId": None,
        "accountId": state["account_id"],
        "ordinaryUsageAllowed": True,
    }


def login_start(_params: dict[str, Any]) -> dict[str, Any] | str:
    if SCENARIO.get("login_error"):
        return str(SCENARIO["login_error"])
    login_id = str(uuid.uuid4())
    state["login_id"] = login_id
    port = SCENARIO.get("login_port", 1455)
    expires = SCENARIO.get("login_expires_after")
    if expires is not None:
        threading.Timer(float(expires), login_timed_out, args=(login_id,)).start()
    return {
        "type": "chatgpt",
        "loginId": login_id,
        "authUrl": (
            "https://auth.openai.com/oauth/authorize?response_type=code&client_id=<redacted>"
            f"&redirect_uri=http://localhost:{port}/auth/callback&state=<redacted>"
        ),
    }


def login_timed_out(login_id: str) -> None:
    if state["login_id"] == login_id:
        state["login_id"] = None
        notify("account/login/completed", {
            "loginId": login_id, "success": False, "error": "Login timed out",
            "onboardingEntrypoint": None,
        })


def login_cancel(params: dict[str, Any]) -> dict[str, Any]:
    if state["login_id"] is None or params.get("loginId") != state["login_id"]:
        return {"status": "notFound"}
    login_id, state["login_id"] = state["login_id"], None
    notify_soon("account/login/completed", {
        "loginId": login_id, "success": False,
        "error": "Login server error: Login was not completed", "onboardingEntrypoint": None,
    })
    return {"status": "canceled"}


def logout(_params: dict[str, Any]) -> dict[str, Any]:
    state["account"] = None
    notify_soon("account/updated", {"authMode": None, "planType": None})
    return {}


# ── Commands and turns: the re-test's shapes ─────────────────────────────────


def profile_refused(name: object) -> str | None:
    """A profile Codex wasn't started with is refused, as a missing config entry would be."""
    if name is None or any(str(name) in argument for argument in sys.argv):
        return None
    return f"permission profile `{name}` is not defined"


def run_command(text: str, escalated: bool = False) -> tuple[int, str]:
    """Pretend to run one of the re-test's commands, holding or leaking as the scenario says."""
    sandbox = SCENARIO.get("sandbox", "holds")
    leaks = sandbox == "leaks" or (escalated and sandbox == "escalation_leaks")
    if not leaks:
        return 1, "Operation not permitted"
    write = re.search(r"> (\S+)", text)
    if write:
        Path(write.group(1).strip("'\"")).write_text("ravis-reproof\n")
        return 0, ""
    read = re.search(r"(?:cat|head -c \d+) (\S+)", text)
    if read:
        return 0, Path(read.group(1).strip("'\"")).read_text()
    return 0, ""


def command_exec(params: dict[str, Any]) -> dict[str, Any] | str:
    refused = profile_refused(params.get("permissionProfile"))
    if refused:
        return refused
    if SCENARIO.get("calibration"):
        return calibration.command_exec(API, params)
    status, output = run_command(" ".join(str(part) for part in params.get("command", [])))
    return {
        "exitCode": status, "stdout": "" if status else output, "stderr": output if status else "",
    }


def thread_start(params: dict[str, Any]) -> dict[str, Any] | str:
    refused = profile_refused(params.get("permissions"))
    if refused:
        return refused
    thread_id = str(uuid.uuid4())
    state["threads"][thread_id] = {
        "cwd": params.get("cwd"), "roots": params.get("runtimeWorkspaceRoots"),
        "policy": params.get("approvalPolicy"), "ephemeral": params.get("ephemeral"),
        "config": params.get("config") or {},
    }
    thread = {"id": thread_id, "cwd": params.get("cwd"), "status": {"type": "idle"}, "turns": []}
    notify_soon("thread/started", {"thread": thread})
    return {
        "thread": thread, "model": "gpt-6-astra", "modelProvider": "openai",
        "cwd": params.get("cwd"), "approvalPolicy": params.get("approvalPolicy"),
        "approvalsReviewer": "user", "sandbox": {"type": "workspaceWrite"},
    }


def turn_start(params: dict[str, Any]) -> dict[str, Any] | str:
    thread_id = params.get("threadId")
    if thread_id not in state["threads"]:
        return f"thread not found: {thread_id}"
    if state["threads"][thread_id].get("unloaded"):
        # As Codex does: a thread it has unloaded must be resumed before it takes a turn.
        return f"thread not loaded: {thread_id}"
    # A thread with a turn has a rollout, so Codex can unload it and resume it from disk.
    state["threads"][thread_id]["had_turn"] = True
    turn_id = str(uuid.uuid4())
    state["interrupts"][turn_id] = threading.Event()
    parts = [part.get("text", "") for part in params.get("input", []) if isinstance(part, dict)]
    arguments = (thread_id, turn_id, "\n".join(parts), params)
    threading.Thread(target=scripted_turn, args=arguments, daemon=True).start()
    return {"turn": {"id": turn_id, "status": "inProgress", "items": []}}


def turn_interrupt(params: dict[str, Any]) -> dict[str, Any]:
    event = state["interrupts"].get(params.get("turnId"))
    if event is not None:
        event.set()
    return {}


def scripted_turn(thread_id: str, turn_id: str, prompt: str, params: dict[str, Any]) -> None:
    turn = {"id": turn_id, "status": "inProgress", "items": []}
    notify("turn/started", {"threadId": thread_id, "turn": turn})
    script = SCENARIO.get("turn_script", "obedient")
    stop = state["interrupts"][turn_id]
    error = None
    if prompt.startswith("This is RAVIS's calibration"):
        calibration.turn(API, thread_id, turn_id, prompt, params, stop)
    elif prompt.startswith("RELAY"):
        error = relay.turn(API, thread_id, turn_id, prompt, stop)
    elif script == "loops":
        loop_forever(thread_id, turn_id, stop)
    elif script == "sleeps":
        stop.wait(120)
    else:
        obey(thread_id, turn_id, prompt, script, stop)
    status = "interrupted" if stop.is_set() else "failed" if error else "completed"
    ended = {"id": turn_id, "status": status, "items": [], "error": error}
    notify("turn/completed", {"threadId": thread_id, "turn": ended})


def loop_forever(thread_id: str, turn_id: str, stop: threading.Event) -> None:
    cwd = state["threads"][thread_id]["cwd"]
    while not stop.wait(0.01):
        item = {
            "type": "commandExecution", "id": str(uuid.uuid4()), "command": "echo again",
            "cwd": cwd, "status": "inProgress", "commandActions": [],
        }
        notify("item/started", {
            "threadId": thread_id, "turnId": turn_id, "item": item, "startedAtMs": 0,
        })


def obey(thread_id: str, turn_id: str, prompt: str, script: str, stop: threading.Event) -> None:
    cwd = state["threads"][thread_id]["cwd"]
    matches = [COMMAND_LINE.match(line) for line in prompt.splitlines()]
    commands = [(int(found.group(1)), found.group(2)) for found in matches if found]
    if script == "wanders":
        approval(thread_id, turn_id, "rm -rf ~/Documents", cwd)
    if script == "other_thread":
        answer = ask("item/commandExecution/requestApproval", {
            "threadId": str(uuid.uuid4()), "turnId": turn_id, "itemId": "x", "startedAtMs": 0,
            "command": commands[0][1] if commands else "true", "cwd": cwd,
        })
        log("other_thread_answer", answer=answer)
    for index, command in commands:
        if stop.is_set():
            return
        asked_cwd = "/somewhere/else" if script == "wrong_cwd" and index == 1 else cwd
        # As Codex 0.155.1 showed them on a Linux laptop whose login shell is fish: a command with a
        # redirect arrives inside the bash it falls back to (19 September 2026).
        shown = linux_bash(command) if script == "linux_bash" and ">" in command else command
        decision = approval(thread_id, turn_id, shown, asked_cwd)
        complete(thread_id, turn_id, index, command, cwd, decision, shown)
        if script == "stutters" and index == 2:
            approval(thread_id, turn_id, command, cwd)


def linux_bash(command: str) -> str:
    """`command` as Codex showed it on Linux: in `/usr/bin/bash -lc "…"`, double-quote escaped."""
    escaped = "".join("\\" + c if c in '\\"$`' else c for c in command)
    return f'/usr/bin/bash -lc "{escaped}"'


def complete(
    thread_id: str, turn_id: str, index: int, command: str, cwd: str, decision: str | None,
    shown: str | None = None,
) -> None:
    item: dict[str, Any] = {
        "type": "commandExecution", "id": f"item-{index}", "command": shown or command, "cwd": cwd,
        "commandActions": [],
    }
    if decision != "accept":
        item.update(status="declined", exitCode=None, aggregatedOutput=None)
    else:
        status, output = run_command(command, escalated=index in (2, 4))
        item.update(
            status="completed" if status == 0 else "failed", exitCode=status,
            aggregatedOutput=output,
        )
    notify("item/completed", {
        "threadId": thread_id, "turnId": turn_id, "item": item, "completedAtMs": 0,
    })


def approval(thread_id: str, turn_id: str, command: str, cwd: str) -> str | None:
    item_id = str(uuid.uuid4())
    started = {
        "type": "commandExecution", "id": item_id, "command": command, "cwd": cwd,
        "status": "inProgress", "commandActions": [],
    }
    notify("item/started", {
        "threadId": thread_id, "turnId": turn_id, "startedAtMs": 0, "item": started,
    })
    answer = ask("item/commandExecution/requestApproval", {
        "threadId": thread_id, "turnId": turn_id, "itemId": item_id, "startedAtMs": 0,
        "command": command, "cwd": cwd,
    })
    log("approval_answer", command=command, answer=answer)
    result = (answer or {}).get("result") or {}
    decision = result.get("decision")
    return decision if isinstance(decision, str) else None


# ── The conversation ─────────────────────────────────────────────────────────


def model_list(_params: dict[str, Any]) -> dict[str, Any]:
    return {"data": MODELS, "nextCursor": None}


def loaded_list(_params: dict[str, Any]) -> dict[str, Any]:
    return {"data": list(state["threads"]), "nextCursor": None}


def unsubscribe(_params: dict[str, Any]) -> dict[str, Any]:
    return {"status": "unsubscribed"}


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any] | str]] = {
    "account/read": account_read,
    "account/rateLimits/read": rate_limits_read,
    "account/login/start": login_start,
    "account/login/cancel": login_cancel,
    "account/logout": logout,
    "model/list": model_list,
    "thread/loaded/list": loaded_list,
    "thread/unsubscribe": unsubscribe,
    "command/exec": command_exec,
    "thread/start": thread_start,
    "turn/start": turn_start,
    "turn/interrupt": turn_interrupt,
}
#: What calibration's half may use of this fake.
API = types.SimpleNamespace(
    notify=notify, send=send, log=log, state=state, scenario=SCENARIO, answers=answers,
    answered=answered, server_ids=server_ids,
)
HANDLERS.update(calibration.handlers(API))
# Codex's skills (`fake_codex_skills.py`): listed, extra roots, switched.
HANDLERS.update(skills.handlers(API))
# The pretend proxy the relay half's `fetch` goes through, kept by the calibration half.
API.site_allowed = functools.partial(calibration.site_allowed, API)
API.user_sites = functools.partial(calibration.user_sites, API)
API.site_blocked = calibration.SITE_BLOCKED
# The agent-session relay's half, registered last: its `turn/steer` can be told to refuse.
HANDLERS.update(relay.handlers(API))


def initialize(request_id: Any, params: dict[str, Any]) -> None:
    if state["initialized"]:
        error(request_id, -32600, "Already initialized")
        return
    if SCENARIO.get("initialize_silent"):
        return
    state["initialized"] = True
    client = params.get("clientInfo", {})
    name, client_version = client.get("name", "client"), client.get("version", "0")
    send({"id": request_id, "result": {
        "userAgent": f"{name}/{VERSION} (Mac OS 27.0.0; arm64) unknown ({name}; {client_version})",
        "codexHome": SCENARIO.get("codex_home") or os.environ.get("CODEX_HOME"),
        "platformFamily": "unix", "platformOs": "macos",
    }})


def handle(message: dict[str, Any]) -> None:
    method, request_id = message.get("method"), message.get("id")
    if method is None:
        with answered:
            answers[request_id] = message
            answered.notify_all()
    elif request_id is None:
        notification(method)
    elif state["silent"] == {"*"} or method in state["silent"]:
        return
    elif method == "initialize":
        initialize(request_id, message.get("params") or {})
    else:
        respond(request_id, method, message.get("params") or {})


def notification(method: str) -> None:
    if method == "initialized":
        notify("remoteControl/status/changed", {
            "status": "disabled", "serverName": "<fake>", "installationId": "fake",
            "environmentId": None,
        })


def respond(request_id: Any, method: str, params: dict[str, Any]) -> None:
    if not state["initialized"]:
        error(request_id, -32600, "Not initialized")
        return
    handler = HANDLERS.get(method)
    if handler is None or method in SCENARIO.get("refused_methods", ()):
        error(request_id, -32600, f"Invalid request: unknown variant `{method}`, expected one of …")
        return
    result = handler(params)
    if isinstance(result, str):
        error(request_id, -32600, result)
    else:
        send({"id": request_id, "result": result})


# ── Commands from the test ───────────────────────────────────────────────────


def carry_out(command: dict[str, Any]) -> None:
    action = command["do"]
    simple = SIMPLE_COMMANDS.get(action)
    if simple is not None:
        simple(command)
    elif action == "crash":
        os._exit(command.get("status", 1))
    elif action == "stop_answering":
        state["silent"] = {"*"}
    elif action == "notify":
        notify(command["method"], command.get("params", {}))
    elif action == "login_complete":
        complete_login(command)
    elif action == "set_account":
        set_account(command)


def interrupt_turns(_command: dict[str, Any]) -> None:
    """Codex ending its turns by itself, without RAVIS asking."""
    for event in state["interrupts"].values():
        event.set()


SIMPLE_COMMANDS: dict[str, Callable[[dict[str, Any]], None]] = {
    "set_rate_limits": lambda command: state.update(rate_limits=command["rate_limits"]),
    "interrupt_turns": interrupt_turns,
    # Stop answering one method from now on, as a Codex stuck on it would; everything else answers.
    "silence": lambda command: state["silent"].add(command["method"]),
}


def set_account(command: dict[str, Any]) -> None:
    state["account"] = command.get("account")
    state["account_id"] = command.get("account_id", state["account_id"])
    account = state["account"] or {}
    notify("account/updated", {
        "authMode": "chatgpt" if account else None, "planType": account.get("planType"),
    })


def complete_login(command: dict[str, Any]) -> None:
    login_id, state["login_id"] = state["login_id"], None
    success = command.get("success", True)
    if success:
        state["account"] = command.get("account")
        state["account_id"] = command.get("account_id", state["account_id"])
    notify("account/login/completed", {
        "loginId": login_id, "success": success, "error": command.get("error"),
        "onboardingEntrypoint": None,
    })
    if success:
        account = state["account"] or {}
        notify("account/updated", {"authMode": "chatgpt", "planType": account.get("planType")})


def watch_control() -> None:
    while True:
        for path in sorted(CONTROL.glob("*.json")):
            try:
                command = json.loads(path.read_text())
                path.unlink()
                carry_out(command)
            except Exception as failure:  # noqa: BLE001 — a test double reports, never dies quietly
                log("control_failed", command=path.name, failure=repr(failure))
        time.sleep(0.01)


def main() -> None:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("FAKE_CODEX_")
    }
    log("start", argv=sys.argv[1:], env=environment)
    if "exit_on_start" in SCENARIO:
        sys.stderr.write("FakeCodexAppServer: exiting at start, as scripted\n")
        sys.exit(SCENARIO["exit_on_start"])
    if calibration.profile_rejected(API):
        sys.stderr.write("Error loading config: invalid type for permissions.clarvis_run\n")
        sys.exit(1)
    threading.Thread(target=watch_control, daemon=True).start()
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            sys.stderr.write("ERROR Failed to deserialize JSONRPCMessage\n")
            continue
        log("received", message=message)
        if isinstance(message, dict):
            handle(message)
    log("stdin_closed")
    calibration.end_processes()


if __name__ == "__main__":
    main()
