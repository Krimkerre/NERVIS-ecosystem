"""Codex agent sessions and the project lock (M29's increments R3 and R4, and R5's additions).

RAVIS runs one Codex process for the whole Mac (`ravis.codex`). This package is what turns it into
tasks a Clarvis window can start, follow, answer, steer, stop and reattach to after closing
(runbook §2.2, RAVIS.md §15.1.2, design §3.5). The contract — every route's shape, every error
code and the event stream — is the fixtures in `tests/fixtures/relay-contract/`.

**What lives where:**

| module | what it holds |
|---|---|
| `routes.py` | the HTTP edge, `/api/v1/agent-sessions…`; the owner Stop on its own router |
| `identity.py` | who may call: Clarvis's client credential; an admin one for the owner Stop |
| `tokens.py` | session tokens (only their sha256 is kept) and the `as_`/`rq_`/`pl_` ids |
| `roots.py` | which folders may hold a task, and the git-folder rule, checked without running git |
| `idempotency.py` | a retried request answered once, from the `agent_idempotency` table |
| `sessions.py` | every task: create, look up, list, the periodic tick, Codex's process ending |
| `session.py` | one task's state machine: turns, requests, steering, Stop, settle, presence |
| `requests.py` | Codex's approvals and questions as `RequestView`s, and the decisions allowed |
| `translate.py` | Codex's notifications as relay events; turn failures in the owner's words |
| `events.py` | each task's event ids, its two replay buffers and the SSE frames |
| `redact.py` | command output that might hold a secret, hidden before it is relayed |
| `sites.py` | blocked sites asked of the owner, in groups; the allowlist, added live or ahead |
| `reopen.py` | letting go of a task's thread until Codex unloads it, after blocked sites |
| `store.py` | migrations 8 to 10: sessions, turns, requests, processes, locks, threads |
| `calibration_dependent.py` | **every value calibration's `summary.json` replaces**, in one place |
| `locks.py` | the project lock as a Codex session holds it, and the views both engines share |
| `lock_api.py` | Clarvis's own runs taking the lock: acquire, adopt, takeover, transfer |
| `lock_routes.py` | the HTTP edge, `/api/v1/project-locks…` |
| `group_kill.py` | a taken-over window's running command, stopped with its group and descendants |
| `reconcile.py` | the restart adoption rule for the locks a Codex session held |
| `attribution.py` | looking at Codex's processes every 2 s while a turn runs, and recording them |
| `cleanup.py` | ending one task's command processes and confirming they are gone |

**Nothing here lets a real task start before calibration passes.** Creating a session and starting
a turn both need Codex's state to be `signed_in` *and* its strict file rules `proven`; until
calibration (or the re-test) proves them, both are refused with 409 `CODEX_NOT_READY`
(`sessions.py`, `readiness`).

**What M29's fourth increment (R4) added:** the `/api/v1/project-locks` routes (create, heartbeat,
release, takeover, transfer, read); per-task process attribution by Codex's process tree, each
command's folder and pid-plus-start identity, recorded in `agent_process` and in the checkout lock
file; restart reconciliation with the adoption rule; the group-and-descendants kill at a takeover;
interrupting turns at shutdown; `paused_for_update`; and the 90-day sweep of Codex's threads.

**What R5 added:** the sites the owner allows before a task (`GET`, `POST` and `DELETE
/api/v1/codex/sites`, served from `api/management/codex.py` over `sites.py`'s allowlist); site asks
grouped by the turn that blocked them; reopening a task's thread when a turn ends with site asks
open, or with a site allowed since the thread loaded (`reopen.py`), a turn asked for meanwhile
starting after the resume; and each task's effort, checked against `model/list` and sent on every
`turn/start` (migration 10).
"""
