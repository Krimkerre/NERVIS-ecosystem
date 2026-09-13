"""Codex agent sessions: the relay Clarvis runs a coding task through (M29's third increment, R3).

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
| `sites.py` | a site Codex's network proxy blocked, asked of the owner; the allowlist, added live |
| `store.py` | migration 8's tables: sessions, turns, requests, locks, kept answers |
| `calibration_dependent.py` | **every value calibration's `summary.json` replaces**, in one place |
| `locks.py` | **the R4 seam:** the project lock as a Codex session needs it |
| `cleanup.py` | **the R4 seam:** ending one task's command processes and confirming they are gone |

**Nothing here lets a real task start before calibration passes.** Creating a session and starting
a turn both need Codex's state to be `signed_in` *and* its strict file rules `proven`; until
calibration (or the re-test) proves them, both are refused with 409 `CODEX_NOT_READY`
(`sessions.py`, `readiness`).

**What M29's fourth increment (R4) adds** on the two seams: the `/api/v1/project-locks` routes
(create, heartbeat, release, takeover, transfer, read), the restart adoption rule and
reconciliation, recording attributed processes in `agent_process` while turns run, and the
process-group kill at a takeover.
"""
