"""**Everything that waits on calibration, in one place** (design §4.9, §10.4; STATUS.md "Cal").

Calibration — the run with the owner that asks sixteen questions of the real Codex — ran for
Codex 0.154.0 as `cal_d2185ed08f50` (13 September 2026). Its `summary.json` decided what this
module holds, and this is the one place to change when a later run decides otherwise:

- **the approval settings per mode** (`mode_mapping`; K1, K4): measured, below;
- **no network through approvals** (none ever opens it; allowed sites come from `sites.py`);
- **`thread/start` and `thread/resume`**: the profile, explicit workspace roots and a
  non-ephemeral thread, with archive, unarchive and resume in RAVIS's process (K13 passed) and the
  per-task `TMPDIR` taking effect (K2b);
- **answers**: K8 was inconclusive — Codex sent no permission request, so nothing relies on one —
  and K11 found a "for the session" grant outlives its turn, so it is never offered.

Two findings shape `session.py` instead. **K7 failed**: an interrupted turn leaves its open
request unresolved, so RAVIS answers and publishes every open request itself whenever a turn ends.
**K12**: events can arrive out of order, so a file change is offered only once its item says what
it would write.

**Why none of this can start a real task early.** Every session and turn is refused with 409
`CODEX_NOT_READY` while the running build's strict file rules aren't `proven` (`sessions.py`,
`readiness`); only the pinned calibration record or the re-test proves them.

**What never changes with calibration** (the owner's D2 and review AH3):
- no client can weaken any of this: the create body carries no sandbox, profile or policy field;
- never `danger-full-access`, never `approvalPolicy: "never"`;
- Codex's "don't ask again" (`acceptForSession`) and a permission grant for the whole session are
  **never offered** — `DECISIONS` has no way to produce either;
- no approval grants network, to a command, a turn or the run; sites are the owner's to allow.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

# ── The approval settings per mode (calibration K1, K4 → `summary.json` → `mode_mapping`) ──────

#: **Measured** by calibration run `cal_d2185ed08f50` on Codex 0.154.0 (13 September 2026,
#: `summary.json` → `mode_mapping`, `measured: true`): K4 found that Codex's granular policy with
#: `sandbox_approval: false` keeps even an approved command inside the box, so every mode uses it.
#: The design's provisional pair (`untrusted`, `on-request`) is what this replaced. Unattended mode
#: differs from agent and auto only in Clarvis's own auto-answer, never in what Codex may do.
NO_ESCALATION: dict[str, Any] = {
    "granular": {
        "sandbox_approval": False, "rules": True, "mcp_elicitations": False,
        "request_permissions": False, "skill_approval": False,
    }
}
APPROVAL_POLICY: dict[str, Any] = {
    "agent": NO_ESCALATION,
    "auto": NO_ESCALATION,
    "unattended": NO_ESCALATION,
}
APPROVALS_REVIEWER = "user"
MODES = tuple(APPROVAL_POLICY)
#: **No network through approvals** — the owner's decision, 13 September 2026. Calibration proved
#: that on Codex 0.154.0 an approval never opens the network, in `untrusted` and `on-request` alike:
#: K3 failed in `cal_d2185ed08f50`, in `cal_f4552084e0e0` with Codex's permission-request features
#: on, and in `cal_8cfcf81b2d04` behind the network proxy, which blocks an unlisted host with a
#: fixed line and local addresses outright. So a request asking for network
#: (`networkApprovalContext`, or `additionalPermissions.network.enabled`) may only be skipped or
#: stopped, and a grant never carries network; both fields are still parsed, defensively. **Internet
#: access comes from an approved-sites allowlist instead** — the proxy's `network={enabled=true,
#: mode="limited"}` at launch, and the sites written into Codex's own configuration once it runs —
#: with a per-site ask of the owner for a blocked host (`sites.py`).
NETWORK_GRANTS_OFFERED = False

#: **The sites Codex's commands may reach from the start** (the owner's decision, 13 September
#: 2026). RAVIS writes them into the profile's `network.domains` in its own Codex home each time
#: Codex's process becomes ready (`SiteAllowlist.allow_defaults`, Cal-3); the owner adds more
#: through the per-site ask (`sites.py`). Verified without a model on 0.154.0: an exact host
#: matches only itself (`github.com` doesn't cover `api.github.com`), and
#: `*.githubusercontent.com` matches `raw.` and `objects.githubusercontent.com` but not
#: `github.com`. A site ask never adds a wildcard; these two are the only ones, and they are the
#: owner's.
DEFAULT_ALLOWED_SITES: tuple[str, ...] = (
    # npm and yarn
    "registry.npmjs.org", "registry.yarnpkg.com", "repo.yarnpkg.com",
    # Python
    "pypi.org", "files.pythonhosted.org",
    # Rust
    "crates.io", "*.crates.io", "static.rust-lang.org",
    # Go
    "proxy.golang.org", "sum.golang.org",
    # Ruby
    "rubygems.org", "index.rubygems.org",
    # Java
    "repo.maven.apache.org", "repo1.maven.org", "plugins.gradle.org", "services.gradle.org",
    "downloads.gradle.org",
    # GitHub
    "github.com", "api.github.com", "codeload.github.com", "*.githubusercontent.com",
    # JavaScript runtimes
    "nodejs.org", "deno.land", "jsr.io",
)
#: **Never a site list at launch** (Cal-3; run `cal_330b7525d115`, RAVIS 0.24.1). A `-c` flag is
#: Codex's command-line layer, and it outranks the user configuration: with `domains={…}` among the
#: launch flags, every `config/batchWrite` to `permissions.<profile>.network.domains` came back
#: `okOverridden` and the added site stayed blocked. Verified without a model on Codex 0.154.0: with
#: no `domains` at launch the same upsert answers `ok`, the site answers at once, and a host never
#: written stays blocked. So the launch flags carry the proxy only, and the sites are written after.
#: **A running turn doesn't see it** (run `cal_ed672bf12c6f`): the add answers `ok`, and the turn
#: already running stays blocked. The owner accepted a site counting from the thread's next turn
#: (14 September 2026), and K3 checks that it does (Cal-4).

#: A profile's site table inside a TOML inline table: its values are `"host"="allow"`, never braces.
_DOMAINS = r"\bdomains\s*=\s*\{[^{}]*\}"


def network_domains_key(profile: str) -> str:
    """Where a profile's allowed sites live in Codex's configuration."""
    return f"permissions.{profile}.network.domains"


def network_profile_flags(profile: str) -> tuple[str, str]:
    """The `-c` pair giving a profile its network section: the proxy on, limited, no sites."""
    return ("-c", f'permissions.{profile}.network={{enabled=true, mode="limited"}}')


def without_network_domains(flags: Sequence[str], profile: str) -> list[str]:
    """A profile's `-c` flags with any site list taken out, so a site write is never overridden.

    A stored or owner-named profile may still carry `domains={…}` inside its network section, or a
    flag of its own for `permissions.<profile>.network.domains`: the first is cut out of the
    setting, the second pair dropped. Nothing else in a flag changes.
    """
    kept: list[str] = []
    for option, value in zip(flags[::2], flags[1::2], strict=False):
        if value.startswith(network_domains_key(profile) + "="):
            continue
        # The table after another key, before another, or alone — in that order.
        for pattern in (rf",\s*{_DOMAINS}", rf"{_DOMAINS}\s*,\s*", _DOMAINS):
            value = re.sub(pattern, "", value)
        kept += [option, value]
    return kept

# ── The thread's box (calibration K2b, K5, K13) ──────────────────────────────────────────────────

#: Non-ephemeral, so `thread/archive` works when a task ends and `thread/resume` after a switch
#: (K13; the Cal notes: "`thread/archive` needs a non-ephemeral thread").
EPHEMERAL_THREADS = False
#: The per-task temp folder, inside the project (K2b: `shell_environment_policy.set.TMPDIR` works).
TMP_FOLDER = Path(".clarvis") / "tmp"

DEVELOPER_INSTRUCTIONS = "\n".join((
    "You are Clarvis's Codex engine working in the project at the working directory.",
    "- Work only inside this project. Never open credential, key, token or .env files, or anything "
    "under ~/.config, ~/.ssh, ~/.aws or a .run folder.",
    "- Use the TMPDIR you were given for temporary files.",
    "- Do not run git commands that change branches, commits, the index or remotes. Clarvis "
    "manages git.",
    '- Before each step of the plan, write "STEP: <n>. <what>" on its own line.',
    "- Run the checks the plan lists and report each command with its real output.",
    "- If you need a decision, ask with request_user_input rather than guessing.",
))


def tmp_folder(root: Path, session_id: str) -> Path:
    return root / TMP_FOLDER / session_id


def _box(root: Path) -> dict[str, Any]:
    """The sandbox every `turn/start` carries, unless calibration shows it conflicts (§4.9)."""
    return {
        "type": "workspaceWrite",
        "writableRoots": [str(root)],
        "networkAccess": False,
        "excludeSlashTmp": True,
        "excludeTmpdirEnvVar": True,
    }


def thread_start_params(
    root: Path, mode: str, profile: str, model: str, session_id: str
) -> dict[str, Any]:
    """`thread/start` for a new task: the profile, explicit roots (AM4), never a client's box."""
    params: dict[str, Any] = {
        "cwd": str(root),
        "approvalPolicy": APPROVAL_POLICY[mode],
        "approvalsReviewer": APPROVALS_REVIEWER,
        "permissions": profile,
        "runtimeWorkspaceRoots": [str(root)],
        "ephemeral": EPHEMERAL_THREADS,
        "developerInstructions": DEVELOPER_INSTRUCTIONS,
        "config": {"shell_environment_policy.set.TMPDIR": str(tmp_folder(root, session_id))},
    }
    if model:
        params["model"] = model
    return params


def thread_resume_params(thread_id: str, root: Path, mode: str, profile: str) -> dict[str, Any]:
    """`thread/resume` after a switch back or a restart: the same box as a new thread (K13)."""
    return {
        "threadId": thread_id,
        "cwd": str(root),
        "approvalPolicy": APPROVAL_POLICY[mode],
        "approvalsReviewer": APPROVALS_REVIEWER,
        "permissions": profile,
        "runtimeWorkspaceRoots": [str(root)],
        "developerInstructions": DEVELOPER_INSTRUCTIONS,
    }


def turn_start_params(thread_id: str, root: Path, mode: str, text: str) -> dict[str, Any]:
    """`turn/start`: the mode as it is now (a change applies from the next turn), roots and box."""
    return {
        "threadId": thread_id,
        "input": [{"type": "text", "text": text}],
        "approvalPolicy": APPROVAL_POLICY[mode],
        "runtimeWorkspaceRoots": [str(root)],
        "sandboxPolicy": _box(root),
    }


# ── Answers (calibration K8, K11) ────────────────────────────────────────────────────────────────

#: A window's decision as Codex's, for command and file-change approvals. `acceptForSession` is
#: deliberately absent (AH3): K11 found a "for the session" grant survives a steer and a new
#: turn, so offering it would outlive the owner's presence.
DECISIONS = {"once": "accept", "skip": "decline", "stop": "cancel"}
#: What RAVIS answers when a request can't wait any longer — Stop, the unanswered policy, a secret
#: question — per kind (`agent-sessions.json` → `stop_responses`; K8 saw no permission request).
STOP_RESPONSES: dict[str, dict[str, Any]] = {
    "command": {"decision": "cancel"},
    "fileChange": {"decision": "cancel"},
    "permissions": {"permissions": {}},
    "question": {"answers": {}},
}


def codex_answer(
    kind: str, decision: dict[str, Any], codex_params: dict[str, Any]
) -> dict[str, Any]:
    """The answer Codex gets for a window's allowed decision on one request."""
    word = decision["kind"]
    if kind in ("command", "fileChange"):
        return {"decision": DECISIONS[word]}
    if kind == "permissions":
        if word != "once":
            return {"permissions": {}}
        # Exactly what Codex asked for, for this turn only — never the session (AH3).
        asked = codex_params.get("permissions")
        granted = {
            key: value for key, value in (asked or {}).items()
            if value is not None and (key != "network" or NETWORK_GRANTS_OFFERED)
        }
        return {"permissions": granted, "scope": "turn"}
    if word == "stop":
        return STOP_RESPONSES["question"]
    return {"answers": _answers(decision, codex_params)}


def _answers(decision: dict[str, Any], codex_params: dict[str, Any]) -> dict[str, Any]:
    """`{question id: {answers: [...]}}` from the window's `answers`, or its `text` for one."""
    given = decision.get("answers")
    if isinstance(given, dict):
        return {
            str(key): {"answers": value if isinstance(value, list) else [str(value)]}
            for key, value in given.items()
        }
    questions = codex_params.get("questions") or []
    text = decision.get("text")
    if isinstance(text, str) and len(questions) == 1 and isinstance(questions[0], dict):
        return {str(questions[0].get("id")): {"answers": [text]}}
    return {}
