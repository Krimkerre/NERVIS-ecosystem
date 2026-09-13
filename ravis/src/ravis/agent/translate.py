"""Codex's notifications as relay events, and failures in words the owner can read (design §3.5.4).

The relay stream is not Codex's protocol passed through. It is a smaller, fixed vocabulary
(`event-stream.json` → `events`) that Clarvis renders without knowing Codex's schema, so a Codex
update changes this module, not Clarvis:

| Codex | relay event |
|---|---|
| `item/started`, `item/completed` of a message, command or file change | `item.*`, normalised |
| `item/agentMessage/delta` | `agent.delta` |
| `item/commandExecution/outputDelta` | `command.output`, redacted |
| `turn/plan/updated` | `plan.updated` |
| `model/rerouted` | `model.rerouted` |
| `error` that Codex will retry | `warning` |

Other item types — Codex's reasoning, the user's own message, tool calls — aren't relayed.

**Turn failures** (design §9) become `turn.completed.error {kind, http_status?, message}`, with a
sentence C2a can show as it stands ("make turn-failure `error.message` owner-readable"). Codex's
own words appear only for a failure it names `other`, where nothing better is known.
"""

from __future__ import annotations

from typing import Any

from ravis.agent.redact import Redactor, command_hidden
from ravis.agent.requests import PathContext, file_entry

#: The items a task's step cap counts (design §5.5): its actions on the project.
STEP_TYPES = frozenset({"commandExecution", "fileChange"})
PLAN_STATUS = {"pending": "pending", "inProgress": "in_progress", "completed": "completed"}
FILE_STATUS = {"inProgress": "in_progress"}

#: `codexErrorInfo` → (kind, sentence). Checked in order; the first match wins.
FAILURES: tuple[tuple[str, str, str], ...] = (
    ("usageLimitExceeded", "quota_exhausted",
     "Codex has used this ChatGPT plan's allowance. Its work so far is in the project; review "
     "and save."),
    ("unauthorized", "sign_in_expired",
     "OpenAI signed Codex out. Its work so far is in the project."),
    ("rateLimitExceeded", "throttled", "OpenAI kept refusing Codex for now (too many requests)."),
    ("contextWindowExceeded", "context_window",
     "Codex's conversation grew too long for the model. Its work so far is in the project."),
    ("sandboxError", "sandbox_error", "Codex's safety box refused a step, so the turn ended."),
    ("serverOverloaded", "openai_unavailable",
     "OpenAI's servers were too busy for Codex. Its work so far is in the project."),
    ("internalServerError", "openai_unavailable",
     "OpenAI's servers failed while Codex worked. Its work so far is in the project."),
)
CONNECTION_FAILURES = frozenset({
    "httpConnectionFailed", "responseStreamConnectionFailed", "responseStreamDisconnected",
    "responseTooManyFailedAttempts",
})
CONNECTION_LOST = "Codex couldn't reach OpenAI. Its work so far is in the project."
RUNTIME_CRASHED = {
    "kind": "runtime_crashed",
    "message": "Codex's process stopped unexpectedly; the last step may not have finished. Its "
    "changes are in the project.",
}


def normalise_item(
    item: dict[str, Any], context: PathContext, *, completed: bool
) -> dict[str, Any] | None:
    """A relayed item (`normalised_items`), or None for a type the relay doesn't carry."""
    kind = item.get("type")
    if kind == "agentMessage":
        return {"type": kind, "id": item.get("id"), "text": item.get("text") or "",
                "phase": item.get("phase")}
    if kind == "commandExecution":
        return _command(item, context, completed=completed)
    if kind == "fileChange":
        status = str(item.get("status") or ("completed" if completed else "inProgress"))
        return {
            "type": kind, "id": item.get("id"), "status": FILE_STATUS.get(status, status),
            "changes": [
                file_entry(change, context)
                for change in item.get("changes") or [] if isinstance(change, dict)
            ],
        }
    return None


def _command(item: dict[str, Any], context: PathContext, *, completed: bool) -> dict[str, Any]:
    redactor: Redactor = context.redactor
    cwd = context.resolve(str(item.get("cwd") or context.root))
    return {
        "type": "commandExecution",
        "id": item.get("id"),
        "command": item.get("command") or "",
        "cwd": context.shown(cwd),
        "exit_code": item.get("exitCode") if completed else None,
        "duration_ms": item.get("durationMs") if completed else None,
        "output_tail": (
            redactor.tail(item.get("aggregatedOutput"), hidden=command_hidden(redactor, item))
            if completed else ""
        ),
    }


def plan_steps(params: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"step": step.get("step"), "status": PLAN_STATUS.get(str(step.get("status")), "pending")}
        for step in params.get("plan") or [] if isinstance(step, dict)
    ]


def turn_error(error: object) -> dict[str, Any] | None:
    """`turn.completed.error` for Codex's `TurnError`, or None when there was none."""
    if not isinstance(error, dict):
        return None
    info = error.get("codexErrorInfo")
    if isinstance(info, dict):
        name = next(iter(info), None)
    else:
        name = info if isinstance(info, str) else None
    status = _http_status(info)
    if name in CONNECTION_FAILURES:
        kind, message = ("throttled", FAILURES[2][2]) if status == 429 else ("connection_lost",
                                                                            CONNECTION_LOST)
    else:
        found = next((row for row in FAILURES if row[0] == name), None)
        kind, message = (found[1], found[2]) if found else ("other", _other(error))
    body: dict[str, Any] = {"kind": kind, "message": message}
    if status is not None:
        body["http_status"] = status
    return body


def _http_status(info: object) -> int | None:
    if not isinstance(info, dict):
        return None
    inner = next(iter(info.values()), None)
    status = inner.get("httpStatusCode") if isinstance(inner, dict) else None
    return status if isinstance(status, int) else None


def _other(error: dict[str, Any]) -> str:
    said = error.get("message")
    if isinstance(said, str) and said.strip():
        return f"Codex stopped with an error: {said.strip()}"
    return "Codex stopped with an error it didn't describe."


def step_cap_error(limit: int) -> dict[str, Any]:
    return {"kind": "step_cap", "message": f"The step cap of {limit} was reached."}


def turn_start_error(detail: str) -> dict[str, Any]:
    return {"kind": "turn_start_failed", "message": f"Codex didn't start the turn: {detail}"}
