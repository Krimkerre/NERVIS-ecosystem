"""Codex's approvals and questions, as the windows see them (design §3.5.2, §5.2).

When Codex needs a decision it sends RAVIS a request on its thread. RAVIS turns it into a
`RequestView` (`agent-sessions.json` → `request_view_examples`) and **computes which decisions a
window may send** — enforced when the answer arrives, so no client can grant more than RAVIS
offers:

| request | offered |
|---|---|
| a command, a file change, a permission grant | `once`, `skip`, `stop` |
| …that writes outside the project, or touches a denied path | only `skip`, `stop` |
| a question | `answer`, `stop` |
| a question marked secret | nothing: RAVIS answers it empty itself (`policy_secret`) |

Codex's `acceptForSession` ("don't ask again") and a permission grant for the whole session are
never offered in this delivery (review AH3).

**Paths** in a payload are relative to the project root; a path outside it is absolute and also
listed in `outside_workspace`. Nothing here is stored: the request's row keeps its kind and how it
ended, and the payload lives in memory until the request is resolved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ravis.agent.calibration_dependent import NETWORK_GRANTS_OFFERED
from ravis.agent.redact import Redactor

#: Codex's request methods, by the kind the relay names them.
KINDS = {
    "item/commandExecution/requestApproval": "command",
    "item/fileChange/requestApproval": "fileChange",
    "item/permissions/requestApproval": "permissions",
    "item/tool/requestUserInput": "question",
}
ONCE_SKIP_STOP = ["once", "skip", "stop"]
SKIP_STOP = ["skip", "stop"]
ANSWER_STOP = ["answer", "stop"]
DECISION_KINDS = frozenset({"once", "skip", "stop", "answer", "allow_site", "keep_blocked"})


def mapping(value: object) -> dict[str, Any]:
    """`value` when it is a JSON object, else an empty one: Codex's optional fields, read safely."""
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class PathContext:
    """What a request's paths are judged against: the task's root and the denied paths."""

    root: Path
    denied: tuple[Path, ...]
    redactor: Redactor

    def resolve(self, value: str) -> Path:
        path = Path(value)
        return (path if path.is_absolute() else self.root / path).resolve()

    def shown(self, path: Path) -> str:
        """Relative to the root when inside it (`.` for the root), else absolute."""
        if path == self.root:
            return "."
        return str(path.relative_to(self.root)) if self.root in path.parents else str(path)

    def outside(self, path: Path) -> bool:
        return not (path == self.root or self.root in path.parents)

    def is_denied(self, path: Path) -> bool:
        return any(path == denied or denied in path.parents for denied in self.denied)


def is_secret_question(params: dict[str, Any]) -> bool:
    questions = params.get("questions")
    return isinstance(questions, list) and any(
        isinstance(question, dict) and question.get("isSecret") is True for question in questions
    )


def payload_and_decisions(
    kind: str, params: dict[str, Any], context: PathContext, changes: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    """A request's payload and the decisions RAVIS offers for it."""
    if kind == "command":
        return _command(params, context)
    if kind == "fileChange":
        return _file_change(params, context, changes)
    if kind == "permissions":
        return _permissions(params, context)
    return _question(params), list(ANSWER_STOP)


def _command(params: dict[str, Any], context: PathContext) -> tuple[dict[str, Any], list[str]]:
    cwd = context.resolve(str(params.get("cwd") or context.root))
    extra = _filesystem(params.get("additionalPermissions"))
    extra_paths = [context.resolve(path) for path in (*extra["read"], *extra["write"])]
    network = params.get("networkApprovalContext")
    payload = {
        "command": params.get("command") or "",
        "cwd": context.shown(cwd),
        "reason": params.get("reason"),
        "network": (
            {"host": network.get("host"), "protocol": network.get("protocol")}
            if isinstance(network, dict) else None
        ),
        "escalation": params.get("additionalPermissions"),
        "gate_hint": None,
    }
    outside_writes = context.outside(cwd) or any(
        context.outside(context.resolve(path)) for path in extra["write"]
    )
    denied = context.redactor.names_denied(params.get("command"), params.get("commandActions"))
    denied = denied or any(context.is_denied(path) for path in extra_paths)
    # No path that grants network (calibration K3): a command asking for network — a network
    # approval, or extra permissions with network enabled — may only be declined.
    wants_network = mapping(mapping(params.get("additionalPermissions")).get("network"))
    asks = isinstance(network, dict) or wants_network.get("enabled") is True
    asks_network = asks and not NETWORK_GRANTS_OFFERED
    refused = outside_writes or denied or asks_network
    return payload, list(SKIP_STOP if refused else ONCE_SKIP_STOP)


def _file_change(
    params: dict[str, Any], context: PathContext, changes: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    files, outside, denied = [], [], False
    for change in changes:
        path = context.resolve(str(change.get("path", "")))
        entry = file_entry(change, context)
        files.append(entry)
        moved = entry.get("moved_to")
        targets = [path, *([context.resolve(moved)] if moved else [])]
        outside += [str(target) for target in targets if context.outside(target)]
        denied = denied or any(context.is_denied(target) for target in targets)
    payload: dict[str, Any] = {"files": files, "reason": params.get("reason")}
    grant = params.get("grantRoot")
    if isinstance(grant, str) and grant:
        payload["grant_root"] = grant
        if context.outside(context.resolve(grant)):
            outside.append(str(context.resolve(grant)))
    payload["outside_workspace"] = list(dict.fromkeys(outside))
    unknown = not changes  # nothing said what it would write (calibration K12)
    return payload, list(SKIP_STOP if outside or denied or unknown else ONCE_SKIP_STOP)


def file_entry(change: dict[str, Any], context: PathContext) -> dict[str, Any]:
    """A Codex `FileUpdateChange` as `{path, change, moved_to?, added, removed}`."""
    kind = mapping(change.get("kind"))
    word = str(kind.get("type", "update"))
    moved = kind.get("move_path")
    entry: dict[str, Any] = {"path": context.shown(context.resolve(str(change.get("path", ""))))}
    if word == "update" and isinstance(moved, str) and moved:
        entry.update(change="rename", moved_to=context.shown(context.resolve(moved)))
    else:
        entry["change"] = word if word in ("add", "delete") else "update"
    added, removed = _counted(change.get("diff"), word)
    entry.update(added=added, removed=removed)
    return entry


def _counted(diff: object, word: str) -> tuple[int, int]:
    """Lines added and removed, from a unified diff (or a whole file, for an add or a delete)."""
    lines = diff.splitlines() if isinstance(diff, str) else []
    if word in ("add", "delete") and not any(line.startswith(("+", "-")) for line in lines):
        return (len(lines), 0) if word == "add" else (0, len(lines))
    added = sum(1 for line in lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in lines if line.startswith("-") and not line.startswith("---"))
    return added, removed


def _permissions(params: dict[str, Any], context: PathContext) -> tuple[dict[str, Any], list[str]]:
    asked = mapping(params.get("permissions"))
    filesystem = _filesystem(asked.get("fileSystem"))
    read = [context.resolve(path) for path in filesystem["read"]]
    write = [context.resolve(path) for path in filesystem["write"]]
    outside = [str(path) for path in (*read, *write) if context.outside(path)]
    denied = [str(path) for path in (*read, *write) if context.is_denied(path)]
    payload = {
        "read": [context.shown(path) for path in read],
        "write": [context.shown(path) for path in write],
        "network": bool(asked.get("network")),
        "reason": params.get("reason"),
        "outside_workspace": list(dict.fromkeys(outside)),
        "denied": list(dict.fromkeys(denied)),
    }
    outside_writes = any(context.outside(path) for path in write)
    asks_network = bool(asked.get("network")) and not NETWORK_GRANTS_OFFERED
    refused = outside_writes or bool(denied) or asks_network
    return payload, list(SKIP_STOP if refused else ONCE_SKIP_STOP)


def _filesystem(value: object) -> dict[str, list[str]]:
    """`{read, write}` path lists from an additional or requested file-system permission."""
    source = value if isinstance(value, dict) else {}
    filesystem = source.get("fileSystem", source)
    filesystem = filesystem if isinstance(filesystem, dict) else {}
    return {
        side: [path for path in (filesystem.get(side) or []) if isinstance(path, str)]
        for side in ("read", "write")
    }


def _question(params: dict[str, Any]) -> dict[str, Any]:
    questions = []
    for question in params.get("questions") or []:
        if not isinstance(question, dict):
            continue
        options = question.get("options") or []
        questions.append({
            "id": question.get("id"),
            "header": question.get("header"),
            "question": question.get("question"),
            "options": [
                option.get("label") if isinstance(option, dict) else option for option in options
            ],
            "allow_other": question.get("isOther") is True,
        })
    return {"questions": questions}


def decision_from(body: dict[str, Any]) -> dict[str, Any] | None:
    """The body's `decision`, if it is one: `{kind, text?, answers?}` with a known kind."""
    decision = body.get("decision")
    if not isinstance(decision, dict) or decision.get("kind") not in DECISION_KINDS:
        return None
    text, answers = decision.get("text"), decision.get("answers")
    if text is not None and not isinstance(text, str):
        return None
    if answers is not None and not isinstance(answers, dict):
        return None
    return {"kind": decision["kind"], "text": text, "answers": answers}
