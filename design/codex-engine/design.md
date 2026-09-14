> **Working design record, 13 September 2026.** Kept so the Codex engine build (RAVIS M29, Clarvis E-C9,
> NERVIS M28) can be continued by any session. It is not the contract: the canonical text is
> `ECOSYSTEM_RUNBOOK.md` §2.2, `RAVIS.md` §15.1.2, `CLARVIS.md` §5.5 and `NERVIS.md`, and where they
> differ they win. Retire this folder when those milestones close.

# Codex as an optional coding engine: design (architecture A, as decided)

**History of this document, 13 September 2026:**
- Written, then revised twice against `codex-design-review.md` (the first review, then its re-check).
- **Reworked for the owner's decisions** (appended to `codex-design-constraints.md`, about 05:00):
  - **D1 — RAVIS runs Codex** (architecture A);
  - **D2 — stricter file rules,** gated on calibration;
  - **D3 — keep Codex's history** in RAVIS's Codex folder;
  - **D4 — the Homebrew stable Codex;**
  - **D5 — no terms-check gate.**
- The previously recommended Clarvis-hosted shape is summarised in the appendix as the considered alternative.
- §14 records what changed, and how each review lesson carries over.

This is a design only. Nothing was built and no repository was edited.

**Sources.** Every claim was checked read-only against the sources named here.

| Checkout | Commit |
|---|---|
| `NERVIS-ecosystem` | `94c3565` |
| `clarvis` | `90df7be` (0.16.0) |

The Codex facts come from:
- `codex-protocol-brief.md` and the probe transcript;
- the generated schemas (stable and experimental combined bundles);
- the TypeScript bindings and a strings dump of the binary.

Only read-only checks were run: `codesign -dv`, `shasum`, `lsof`, `pgrep`, `ls`. Codex was not started, `~/.codex` and credential files were not read, and no model or network call was made.

**Tags used below:**
- **(verified)**: read in source, in a schema or on this Mac.
- **(strings)**: found in the binary's text, not proof of behaviour.
- **(unverified)**: must be established before being relied on, mostly in calibration (§10.4).

**The chosen Codex copy (D4, verified).**
- **Path:** `$(brew --prefix)/bin/codex`, today `/opt/homebrew/bin/codex` → `/opt/homebrew/Caskroom/codex/0.154.0/bin/codex`.
- **Build:** stable 0.154.0, signed `Developer ID Application: OpenAI OpCo, LLC (2DC432GLL2)`. Its stable schema is byte-identical to the ChatGPT app's `0.154.0-alpha.6.2` (brief §0).
- **Provenance:** the cask was installed at 02:26 on 13 September. The owner says it is theirs, or to keep it.
- **Not used:** the ChatGPT app's own copy (`/Applications/ChatGPT.app/Contents/Resources/codex`, alpha, self-updating). The app still runs its own Codex server (pid 48758) against `~/.codex`, and nothing here touches that.

---

## 1. Plain summary

1. You pick **Codex** as the coding model in Clarvis, in desktop VS Code or in the browser editor.
2. **RAVIS runs Codex** on this Mac, on your ChatGPT plan, and passes every step, question and approval to Clarvis.
3. You watch progress and answer Codex's questions in the Clarvis chat, just as with Clarvis's own engine.
4. **A Codex task keeps running if you close or reload the editor.** Open the project again, in either editor, and Clarvis reconnects, catches you up and shows any question still waiting.
5. If a question waits while no editor is open, the menu bar and the dashboard say so. After 30 minutes Codex pauses that step and keeps its conversation, so you can carry on later.
6. Anything you type while it works reaches Codex. **Stop**, from any editor showing the task or from the menu bar or dashboard, clears the question at once. RAVIS then makes sure Codex, and every command it started for that project, has stopped before anything is saved.
7. **Only one engine writes to a project at a time.** RAVIS keeps that lock for Codex and for Clarvis's own engine, and you can move an unfinished task between them in both directions, on the same branch.
8. You sign in once, in your browser, from the menu bar or the dashboard. Codex uses the stable copy installed through Homebrew and its own sign-in, separate from the ChatGPT app's.
9. **Codex's commands can't read your key and password files, can't write outside the project, and need your OK for the internet.** The file rules are switched on only after a test proves they hold.
10. The menu bar and the dashboard show whether Codex is ready, your plan's remaining allowance, and which tasks are running or waiting for you. Each running task has a **Stop** button there, which asks you to confirm first. Nothing there can approve or start work.
11. Whenever the Codex version changes, new tasks pause. You can accept the new version after an automatic check, but tasks start again only once a short re-test proves the key-file rules still hold on it. You start that re-test from the menu bar. It uses one short Codex turn from your plan's allowance, and RAVIS allows only its four fixed test commands, in a throwaway folder.
12. **What this shape means:** Codex tasks need RAVIS. If RAVIS, or its one Codex process, restarts, every running Codex task is cut off at once and has to be checked and continued.

---

## 2. Architecture

### 2.1 The decision

**Architecture A (owner decision D1):**
- **RAVIS owns one long-lived `codex app-server` child.** That single process hosts every Codex thread on the Mac, for runs, sign-in and usage.
- **RAVIS keeps agent-session records** and enforces **one writer per project** for both engines.
- **RAVIS relays** events, approvals, questions, steer and stop to Clarvis, over server-sent events (SSE) plus JSON POSTs.
- **Clarvis** keeps the task, the chat, the approval UI, git (branches and commits), the checkpoint, and its own engine.
- **Tasks outlive the editor window**, and windows reattach to them.

```text
Clarvis window (code-server or desktop)  ──JSON POST──▶  RAVIS  ──stdio JSON-RPC──▶  codex app-server (one process)
          ▲                                               │  agent-session store (SQLite)          │ threads: project A, project B, …
          └──────────── SSE events (resumable) ◀──────────┘  project locks · relay · supervisor     │ commands (sandboxed)
NERVIS menu bar / dashboard ◀── GET /api/v1/codex (metadata only: state, allowance, running tasks) ──┘
```

**Why one process and not one per task:**
1. **Codex allows one stdio client per process** (brief §1). RAVIS is that client for all threads: thread events and server requests carry `threadId`, so RAVIS routes them to the right session.
2. **One process on the Codex home removes the two-processes refresh race.** Sign-in, rate-limit reads and turns all share one token cache (review H3, which no longer applies).
3. **Rate-limit notifications from every turn** arrive on the same connection that serves the menu bar and the dashboard.
4. **The cost:** a crash or hang of that process cuts off every project's running turn at once (R1).
   - One child per task would contain a crash to one project.
   - It would bring back concurrent token refresh across processes, which the binary handles only by a guarded reload with no cross-process lock (strings).
   - It would also repeat the plugin-catalogue start-up for every task.
   - The design takes the shared-process risk and makes recovery explicit (§4.4, §9).

### 2.2 Facts that shape A

1. **Closing stdin ends the process** within about 10 ms when idle (probe). With running turns, shutdown is bounded to 10 s (source), so **every running Codex task dies when RAVIS dies.** Commands a turn started may outlive it (§4.5).
2. **Nothing on the protocol times out an approval, permission request or question** (brief §7). RAVIS therefore needs an explicit unanswered-request policy (§4.8).
3. **Thread events go to connections subscribed to the thread,** and `thread/start` and `thread/resume` subscribe automatically (brief §6). With RAVIS as the only client, it receives everything.
   - **Backpressure:** internal channels hold 128 messages, and a full intake answers `-32001` (brief §1).
4. **Background terminals persist across turns.** The experimental `thread/backgroundTerminals/list {threadId}` returns `ThreadBackgroundTerminal {command, cwd, itemId, processId, osPid}`, and `…/terminate {threadId, processId}` ends one (verified, experimental schema).
   - **The sandbox wrapper:** foreground commands run under `/usr/bin/sandbox-exec` with `WRITABLE_ROOT` and `READABLE_ROOT` parameters (strings).
   - Both are the basis of per-project cleanup (§4.5).
5. **Sign-in binds `127.0.0.1:1455`** (fallback 1457) in the app-server process (brief §3).
6. **Under code-server, a closed tab keeps its extension host for 3 hours** (`reconnectionGraceTime` default `108e5` ms, verified). Because tasks live in RAVIS, a closed tab blocks no other window. But the lingering host would look "attached", so attachment is judged by the Clarvis panel's own heartbeat, not by the host (§3.5.4; review AH2).
7. **All code-server Clarvis windows present one RAVIS credential, `client.clarvis`** (`tools/run.py:932-946`). Desktop VS Code presents none (`clarvis/src/model/ravisCredential.ts:26-39`).
   - The application id can't tell windows or workspaces apart, hence **per-session capability tokens** bound to a workspace root (§3.5).
8. **NERVIS relays any GET under RAVIS's `/api/v1/` with its client credential** (`nervis/src/nervis/peers/ravis.py:214-230`; application `nervis`). Agent-session routes must therefore refuse NERVIS explicitly, GETs included. The one exception is the stop-only owner route (§3.5.5).
9. **WebSockets skip RAVIS's admission layer** (`admission.py:66-68`, `app.py:475`), and the runbook makes SSE canonical (`ECOSYSTEM_RUNBOOK.md:168-172`). The relay uses SSE plus JSON POST.
10. **RAVIS has no long-lived child and no agent-session store today.** The next migration number is 8 (RAVIS map §4, §7).
11. **The specs, as written, forbid A.** They must be amended first (§8):
    - `ECOSYSTEM_RUNBOOK.md:45-46`;
    - `RAVIS.md:24-27`, `:1205`, `:1327`;
    - `CLARVIS.md:209`, `:236-242`.
12. **Codex's sandbox (stable schema, verified):**
    - **Temp folders:** `workspaceWrite` leaves `/tmp` and `$TMPDIR` writable unless `excludeSlashTmp` and `excludeTmpdirEnvVar` are set.
    - **Network:** off by default, but approvable per command (`networkApprovalContext`).
    - **Reads:** everywhere.
    - **Restricted reads** need a permission profile: `[permissions.<id>]` filesystem entries with `access: read|write|deny`, selected by the experimental `thread/start.permissions`. The binary mentions an `escalatable` flag (strings).
    - **Escalation:** the model can ask for escalation (`require_escalated`, strings). Whether an approved command stays sandboxed is unverified.
13. **Secrets a Codex command could otherwise read** (paths from source; contents never opened):
    - `~/.config/ravis/credentials.json` (every provider key, `ravis/src/ravis/credentials.py:286`);
    - the launcher's `.run/` files (`ravis-admin.token`, `ravis-owner.token`, `nervis-admin.token`, `nervis-benchmark.token`, `dashboard.token`, `nervis-ravis.token`, `clarvis-ravis.token`, `code-server.password`, `code-server.yaml`);
    - Codex's own `auth.json`, and `~/.codex/`;
    - `~/.ssh`, `~/.aws`, `~/.config/gh`;
    - **the session-token files** this design creates (§3.5).
14. **Credential tiers are not a boundary against local programs** (verified by the review):
    - NERVIS's control token is served in `/index.html`;
    - the launcher's tokens are ordinary 0600 files.

    Admin-only routes are for UX and audit. The session token protects against **other clients and windows**, not against a determined program running as the owner.

### 2.3 How A handles the questions that decided the comparison

| Question | Under A |
|---|---|
| One writer | RAVIS's central project lock for both engines, with a lock file in the checkout as the floor when RAVIS is down (§6.3). Nothing is committed, checkpointed or unlocked before the engine's processes are confirmed gone (review B1, N1, N2) |
| Stop | Any attached window posts `interrupt`, and the owner can also stop a task from the menu bar or dashboard through a stop-only route (§3.5.5). RAVIS resolves pending requests with stop answers **before** interrupting, refuses late answers, kills the task's commands, confirms, then reports (§5.3) |
| Steer | Posted to RAVIS, which calls `turn/steer`; on a race it queues the text into the next turn. Text typed while detached, or during a switch, goes into the checkpoint's feedback list (§5.4; review H8) |
| Window closed or reloaded | The task continues. Any window of that workspace, on either host, lists its sessions and reattaches with replay (§5.7) |
| RAVIS restart | Codex's process dies; running turns become `uncertain`. Recorded command processes are killed on start-up; a window reconciles git and offers to continue (§4.4) |
| Approvals | Only a Clarvis window holding the session token, and presenting an allowed client credential, may answer. NERVIS and admin credentials are refused explicitly (§3.5) |
| Sign-in | From RAVIS's one process, started from the menu bar or dashboard; Clarvis links there (§3.4) |
| Usage | Same process: turn notifications while tasks run, a read every 15 minutes when idle (§3.3) |
| Experimental surface | Permission profiles and background-terminal cleanup (both needed by D2 and §4.5), behind a version pin that covers them (§4.3) |
| Drift from updates | Pinned by sha256 plus both schema trees. Any change pauses new tasks; the running process is restarted on a new binary only when idle (§4.3, §4.4) |
| Testing | A Python fake app-server for RAVIS; a fake RAVIS relay for Clarvis built from the contract fixtures; fakes scripted from calibration transcripts (§10) |

### 2.4 What the owner accepted with A (plain)

- **Codex tasks need RAVIS running.** Clarvis's own engine does not.
- **One Codex process serves every project.** If it, or RAVIS, restarts, all running Codex tasks are cut off together, then checked and continued.
- **Tasks can run while no editor is open.** Questions wait, visible on the menu bar and the dashboard, and are paused after 30 minutes. Any task can be stopped from the menu bar or dashboard.
- **RAVIS gains a new relay surface** with its own tokens, plus a store of task records.
- **RAVIS's "no conversation content" rule gets a stated exception:** content passes through RAVIS's memory while it is relayed, and Codex keeps its history in RAVIS's Codex folder (D3).

---
## 3. Contracts

**Conventions for every RAVIS route below:**
- JSON bodies, `Content-Type: application/json` (the form types are refused, `admission.py:164-190`).
- Errors use the MEP envelope, `{"error":{"code","message","retryable","details","request_id","trace_id"}}` (runbook §4.5).
- Timestamps are ISO-8601 UTC.
- **Nothing in these routes, in RAVIS's logs or in MEP events carries prompt text, approval text, commands, file contents or diffs,** except the relay stream and snapshot sent to a session-token holder (§3.5), which carry what the window must render.

### 3.1 The `ravis/codex` catalogue entry

> **Renamed, 13 September 2026:** the id is now `ravis/clarvis-codex` (owner decision, to match `ravis/clarvis-agent` and `ravis/clarvis-chat`; RAVIS 0.23.11, NERVIS 0.28.15, Clarvis 0.16.1; no alias). This design keeps the id it was written with; `build-notes.md` → *Rename* says what changed.

**What `/v1/models` lists.** Exactly `{"id": "ravis/codex", "object": "model", "owned_by": "ravis"}`, in **both** builders:
- `merged_catalogue` (`transparent.py:245-264`);
- `ModelRegistry.as_openai_list` (`registry.py:173-185`).

It sits after the pools, and only when both hold:
1. `settings.codex_enabled` resolves true (resolved once at startup).
2. The request carries `X-Clarvis-Engines: codex`. Clarvis 0.17.0+ sends it to a loopback base URL on `listModels`, so an old Clarvis never sees the entry (review M13).

**Rules:**
- **No extra keys.** Listing reads cached values and one header, and starts nothing.
- It is never a pool member, candidate, fallback or tool-trial target, and is absent from `/api/v1/models`.
- **Tests:** both builders, with and without the header, and with no upstream configured.
- **Clarvis's filter:** `openaiCatalog.test.ts` asserts `buildOpenAiCatalog` keeps `ravis/codex`. No Python port of `NOT_CHAT` (review L8).

**Capability.** `ravis.codex_runtime@1` and `ravis.agent_sessions@1`, both declared `available`. Their constraints are `{"backend_id":"ravis/codex","roles":["agent"],"state_endpoint":"/api/v1/codex","relay":"/api/v1/agent-sessions"}`. Never readiness checks.

### 3.2 The refusal on `/v1/chat/completions` and `/v1/embeddings`

**Which ids.** `model == "ravis/codex"`, or `model.startswith("ravis/codex/")`, whether or not Codex is enabled.

**Where it runs.** `agent_backend_refusal(parsed)` in `ravis/src/ravis/api/openai/agent_backends.py`, called inside `_inspect` (`chat.py:690`) and from embeddings. This is before the disabled-provider check, the upstream check, routing, events and sessions.

**Response.** **400**, via `_openai_error` (`chat.py:2164-2166`):

```json
{"error": {"message": "ravis/codex is the Codex coding engine, not a chat model. It runs inside Clarvis 0.17.0 or later when chosen as the coding model. Nothing was run.",
  "type": "agent_backend_not_a_chat_model", "param": null, "code": "agent_backend_not_a_chat_model"}}
```

**Why 400** (`OpenAiCompatibleProvider.ts:30-56`, `ModelProvider.ts:158-193`):
- The chat probe caches "no tools", which is true for this id.
- The agent run ends "The model gave up".
- There is no retry loop. 404 would contradict the listing, 401/403 would blame the key, and 429/5xx would retry forever.

**Residual.** An old Clarvis whose user types the id by hand still makes an empty branch before the 400 (R11).

**Conformance.** `EXPECTED_CHECKS` goes from 23 to **24**.

### 3.3 Codex state, allowance and running tasks: `GET /api/v1/codex`

**Access and speed.**
- **Who may read:** any caller, including NERVIS's relay.
- **Speed:** answered from an in-memory snapshot within about 50 ms. It never awaits the child.
- **Always 200** while RAVIS is up.
- **Metadata only.**

```json
{
  "backend_id": "ravis/codex", "execution": "delegated_agent", "roles": ["agent"],
  "state": "signed_in", "reason": "Codex is signed in with a ChatGPT Plus plan.", "revision": 42,
  "runtime": {"source": "homebrew", "version": "0.154.0", "installed_sha256": "…", "running_sha256": "…",
              "team_id": "2DC432GLL2", "verdict": "tested", "strict_rules": "proven",
              "schema": {"stable_tree": "…", "experimental_tree": "…"},
              "process": {"state": "running", "since": "…", "restarts_24h": 0, "active_turns": 2},
              "checked_at": "…"},
  "home": {"fingerprint": "sha256:…"},
  "account": {"signed_in": true, "auth_mode": "chatgpt", "plan": "plus", "email_hint": "o…@example.com",
              "fingerprint": "strong", "fingerprint_matches": true, "plan_changed": false, "checked_at": "…"},
  "usage": {"known": true, "source": "notification", "observed_at": "…", "stale": false, "allowance_not_cost": true,
            "limit_reached": null, "spend_control_reached": null,
            "windows": [{"id": "primary", "label": "5-hour window", "duration_minutes": 300, "used_percent": 38,
                         "remaining_percent": 62, "resets_at": "…"},
                        {"id": "secondary", "label": "weekly window", "duration_minutes": 10080, "used_percent": 20,
                         "remaining_percent": 80, "resets_at": "…"}],
            "individual_limit": null, "credits": {"has_credits": false, "unlimited": false, "balance": null}},
  "runs": [{"id": "as_…", "turn_id": "…", "project": "add-utc-demo", "state": "waiting_on_you", "since": "…", "age_minutes": 42,
            "waiting_minutes": 12, "attached_windows": 0, "paused_reason": null}],
  "models": [{"id": "gpt-6-astra", "display_name": "…", "is_default": true, "default_effort": "low",
              "efforts": ["low", "medium", "high"]}],
  "sign_in": {"state": "idle", "started_at": null, "expires_at": null, "error": null}
}
```

**`runs`** lists live agent sessions and Clarvis-engine project locks. Every caller gets `since` (the start time). Only named callers (any client or admin credential, including NERVIS's relay and the launcher) also get each Codex run's `id` and current (or last) `turn_id`. The Stop confirmation sends back the `id`, `project` and `turn_id` (§3.5.5).
- `project` is the **basename** of the workspace root only; no path, request text or command.
- `state` is one of `running`, `waiting_on_you`, `paused_unanswered`, `paused_for_update`, `completed_needs_review`, `uncertain`, `leftover`, `clarvis_engine`.

**`state`: exactly one value.** Earlier rows win when several apply.

| `state` | When | Shown as |
|---|---|---|
| `checking` | Before the first check after startup | "checking…" |
| `not_installed` | The configured path (the Homebrew link) doesn't resolve to a file | "not installed" |
| `not_available` | Disabled, wrong signature or team, home inside `~/.codex` or RAVIS's config folder, check failed | "not available" + reason |
| `untested_version` | The **installed** sha256 is in neither the tested nor the accepted list; or strict rules are on and `strict_rules` is `unproven` (§4.3) | "paused — Codex needs re-testing" |
| `runtime_down` | The app-server is restarting or failed to start (§4.4) | "Codex process restarting" |
| `signed_out` | `account: null`, after an admin sign-out or no sign-in ever | "signed out" |
| `sign_in_expired` | `account: null` after a sign-in RAVIS didn't end | "sign-in expired" |
| `account_changed` | The fingerprint differs from the confirmed one (§3.4) | "different account — confirm on the dashboard" |
| `quota_exhausted` | Any of: `rateLimitReachedType` non-null; a window at `usedPercent >= 100` with a future reset; `individualLimit.remainingPercent <= 0`; `spendControlReached == true`. **Credits alone never count** (review M6) | "allowance used up · resets 04:30" |
| `signed_in` | Otherwise | "62% left · resets 04:30", or "usage unknown" |

**Usage rules** (one process, so no report endpoint).
- **While any turn is active:** `account/rateLimits/updated` notifications are merged sparsely (`source:"notification"`), and polling pauses.
- **When no turn is active:** `account/rateLimits/read` every 15 minutes, plus one read 30 s after the last active turn ends.
- **Unknown is never 0.** `usage.known:false` → `windows: []` and no percentages.
- **Stale.** `usage.stale` is true after 30 minutes without a reading while idle.
- **Windows.** `remaining_percent = max(0, 100 - round(usedPercent))`. Labels come from `windowDurationMins`, and the `resetsAt` unit is pinned by a fixture test.

### 3.4 Sign-in, account confirmation, version acceptance

**Access.**
- Every mutating route requires **`admin.`** (`ravis/src/ravis/api/management/credentials.py:140`, `_may_write`). Anonymous and `client.*` get **403 `FORBIDDEN`**. This is **UX and audit, not a boundary** (§2.2 fact 14).
- **Clarvis's part:** a chat line with a **Sign in** link to the dashboard's Codex card (`http://127.0.0.1:8790/#/ravis/Dashboard`). The address also shows as text, because `openExternal` is unverified under code-server.

**Account-swap detection** (review H4, N10).
- On sign-in, RAVIS stores `account_fingerprint = sha256("ravis-codex-account:" + lowercase(email))`. If `email` is null, it uses `sha256` of the rate-limit read's `accountId`; else a `weak` plan-only fingerprint, flagged on the card.
- A different fingerprint → `account_changed`:
  - new sessions and new turns are refused (409 `CODEX_NOT_READY`);
  - the dashboard asks **This is my account** / **Sign out**.
- A plan change is a notice only.
- **What it catches:** accidental swaps, not a determined local program.

**`POST /api/v1/codex/sign-in`** (admin). Body `{}` or `{"method":"browser"}`; another method → 422.

| Outcome | Status and body |
|---|---|
| Started | **202** `{"sign_in":{"state":"waiting_for_browser","auth_url":"https://auth.openai.com/oauth/authorize?…","callback_port":1455,"started_at":"…","expires_at":"<+10 min>"}}` |
| Already waiting | **200**, same body |
| `not_installed` / `not_available` / `runtime_down` | **409 `CODEX_NOT_AVAILABLE`** |
| `untested_version` | **409 `CODEX_UNTESTED_VERSION`** |
| Already signed in | **409 `CODEX_ALREADY_SIGNED_IN`**, "Sign out first." |
| A Codex turn is active | **409 `CODEX_RUN_IN_PROGRESS`**, "Codex is working on a task; sign in after it pauses." |
| Ports 1455 and 1457 both held, or Codex says "already in use" | **409 `CODEX_SIGN_IN_PORT_BUSY`**, "Another program is holding the sign-in ports 1455 and 1457." (review L1) |
| `account/login/start` timed out (10 s) | **503 `CODEX_RUNTIME_UNAVAILABLE`**, `retryable:true` |

**How a sign-in proceeds.**
- **Completion:** `account/login/completed {success:true}` → `account/read` → fingerprint stored → `model/list` → usage read → `signed_in`.
- **A RAVIS restart during a sign-in** ends it: the next start reads `sign_in.state:"failed", error:"RAVIS restarted during sign-in"`.

**Other routes** (all admin, all audited):
- `GET /api/v1/codex/sign-in` → the start body while waiting (so the dashboard can reopen the link); otherwise idle or failed.
- `DELETE /api/v1/codex/sign-in` → `account/login/cancel`, 200 with `cancelled: true|false`.
- `POST /api/v1/codex/sign-out` → **409 `CODEX_RUN_IN_PROGRESS`** while any session has an active turn or a pending settle (review AL4). Idle sessions don't block. Otherwise `account/logout`, fingerprint cleared, 200 with the full `GET` body.
- `POST /api/v1/codex/account/confirm` with body `{"email_hint":"…"}` → 200, or **409 `CODEX_ACCOUNT_MOVED`**.
- `GET /api/v1/codex/version-check` → 200 with the report below.
- `POST /api/v1/codex/accept-version` with body `{"sha256":"…"}` → 200 (`runtime.verdict:"accepted"`). **409 `CODEX_HASH_MISMATCH`** if it isn't the installed binary; **409 `CODEX_VERSION_CHECK_FAILED`** if any of checks 1-6 failed. An accepted version is always recorded `strict_rules:"unproven"`; check 7 only tells whether the profile loads at all (§4.3).
- `DELETE /api/v1/codex/accept-version/{sha256}` → 200.
- `POST /api/v1/codex/reprove` (with `Idempotency-Key`) → **202** `{reproof:{state:"running"}}`. This is **the file-rules re-test** (final check F-A3).
  - **Who may start it: the owner, from the menu bar only.**
    - The path: NERVIS menu → Codex → **Re-test the file rules…** → a confirmation → `run.py codex reprove` (§3.7).
    - The route accepts only `admin.` credentials whose application is in `settings.codex_reproof_applications`, default `["owner_cli"]`. The launcher mints that credential into `.run/ravis-owner.token` and **never gives it to NERVIS**.
    - NERVIS's `admin.launcher`, every client credential and anonymous callers get **403 `REPROOF_NOT_ALLOWED`**.
    - No NERVIS route forwards here (§3.8), and the dashboard has no button, so **NERVIS never starts Codex work**.
  - **Where it runs:** inside RAVIS's one process, only while no Codex task is live (else 409 `CODEX_RUN_IN_PROGRESS`). It uses two throwaway folders RAVIS creates under `~/.local/share/ravis-codex-reproof/<n>/` and deletes after recording the result: never a project, no git, no network. Each holds a decoy key file (named like a real one, with a known marker, in a decoy folder on the deny list; §10.4).
  - **What it runs:**
    1. **K5a, no model:** `command/exec` in each folder tries to read the decoy key file and to write outside the folder. Both must be refused.
    2. **K5b, one short model turn, stated plainly:**
       - In folder A, with a thread open in folder B, Codex gets one fixed prompt: run exactly four commands, one at a time, and report each result.
       - The four commands: read the decoy key file, and write one line outside the folder, each once as an ordinary command and once asking for escalated permissions.
       - Caps: lowest effort (`low`), the default model, at most 12 steps and 5 minutes.
       - It uses a little of the ChatGPT plan's allowance; the menu bar shows the allowance before and after.
  - **Who answers its approvals: RAVIS's re-test harness, from that fixed list, and only in its own throwaway thread.**
    - When Codex asks to run one of the four commands (the command text matches exactly and the folder is A), the harness allows it once. Anything else is declined, and the result is `inconclusive`.
    - The owner's confirmation in the menu bar is the go-ahead for exactly that list.
    - **This is the only place RAVIS answers an approval.** The harness can answer only for thread ids it created, never for an agent session (a test pins it), and every answer is audited.
  - **Result:**
    - `proven` only when every refusal held and the decoy's marker never appeared in any output; it records `strict_rules_proven` for this sha256.
    - `failed` (a rule didn't hold) keeps `unproven`, and D2 returns to the owner.
    - `inconclusive` (Codex didn't follow the list, or a cap was hit) keeps `unproven`, and the owner can try again.
  - `GET /api/v1/codex/reprove` returns the last result to any named caller. Audited: `ravis.codex.reproof_started {application_id}`, `…reproof_approval_answered {command_index}`, `…reproof_finished {result}` (§4.3, §10.4).

```json
{"version_check": {"sha256": "…", "version": "0.155.0", "verdict": "untested", "strict_rules": "unproven",
  "checks": [{"name": "signature", "ok": true}, {"name": "sha256_installed", "ok": true}, {"name": "version_parse", "ok": true},
             {"name": "schema_generation", "ok": true}, {"name": "used_methods_present", "ok": true},
             {"name": "handshake", "ok": true, "detail": "initialize and model/list answered on a scratch home"},
             {"name": "strict_rules", "ok": false, "detail": "AdditionalPermissionProfile changed"}],
  "protocol": {"stable_tree_changed": true, "experimental_tree_changed": true, "strict_rules_surface_changed": true,
               "used_methods_missing": [], "used_definitions_changed": ["ThreadItem"], "other_definitions_changed": 14},
  "checked_at": "…"}}
```

---
### 3.5 The relay API: agent sessions

**Transport.** Server-sent events (SSE) for events, JSON POST for actions. No WebSockets (§2.2 fact 9). Every route is under `/api/v1/agent-sessions`.

#### 3.5.1 Identity and authority

**Client credential.** Every route, **GETs included**, runs one dependency, `require_agent_client(request)`, before anything else:

| Caller | Result |
|---|---|
| `identity.is_anonymous` | **403 `AGENT_CLIENT_NOT_ALLOWED`**: "A Clarvis credential is required." |
| `identity.may_write_configuration` (any `admin.` credential, including NERVIS's `admin.launcher`) | **403 `AGENT_CLIENT_NOT_ALLOWED`**: "Administrative credentials may not start, read, steer, stop or answer Codex tasks." |
| `application_id ∈ {"nervis", "launcher"}` | **403 `AGENT_CLIENT_NOT_ALLOWED`** |
| `application_id ∉ settings.agent_client_applications` (default `["clarvis"]`) | **403 `AGENT_CLIENT_NOT_ALLOWED`** |

- These refusals run **before** the session token is examined, so **a NERVIS credential holding a valid token is still refused.**
- **The one exception is the owner Stop route** (§3.5.5), mounted on a separate router with its own dependency. A route-table test fails if any other agent-session or project-lock route lacks `require_agent_client`, or if the owner Stop route has it.
- Attributes are read directly, never through `getattr(…, False)` (`ravis/src/ravis/api/management/credentials.py:166-169`).
- **Desktop VS Code** presents the same `client.clarvis` secret. It reads it from the file named in a new machine-scoped, untrusted-restricted setting, `clarvis.ravis.credentialFile` (set once to `<NERVIS-ecosystem>/.run/clarvis-ravis.token`). code-server keeps the launcher's environment variable.

**Session capability token.**
- `X-Agent-Session-Token: ast_<43 base64url chars>` (256 bits), returned **once** at creation.
- RAVIS stores only its sha256 and compares in constant time.
- Required on every route with `{sid}`. **Missing or wrong → 404 `AGENT_SESSION_NOT_FOUND`**, so a session's existence doesn't leak.
- **Clarvis keeps it** in `~/.local/share/clarvis/agent-sessions/<sha256(root realpath)>.json` (folder 0700, file 0600, written atomically, deleted when the session ends). Both hosts on this Mac read it, which is what lets desktop VS Code reattach to a session code-server started. Codex's commands are denied read access (§4.9).
- **Recovery:** `POST /api/v1/agent-sessions/{sid}/reissue-token` (client credential, no token). Its body `{"workspace_root"}` must equal the session's root, and it is allowed only when no window has been attached for 60 s. It returns `{session_token}`, invalidates the old one, and is audited. That is no weaker than reading the token file.

**Workspace root.** Create, list and lock calls take `workspace_root`. RAVIS resolves `realpath` and requires an existing directory inside one of `settings.agent_allowed_roots` (default `["~/Documents/coding"]`; the launcher appends extras). It refuses, with **422 `WORKSPACE_ROOT_NOT_ALLOWED`** and `details.reason`:
- `$HOME` itself;
- anything inside `~/.config`, `~/.local/share`, `~/.ssh`, `~/.aws`, `~/Library`, `~/.codex`;
- any configured `.run` folder;
- **an `agent_allowed_roots` entry itself** (for example `~/Documents/coding`, which would let one task write into every project; review AH5);
- **a folder that is, contains or lies inside a protected repository** (`agent_protected_repositories`: by default RAVIS's own checkout and its sibling `clarvis`, which the launcher also passes, so the protection holds when RAVIS starts without the launcher), or that contains a denied path, **unless that exact folder is listed in `agent_allowed_roots` with `"allow_protected": true`**. The owner decided these stay refused (below).

A session stores the realpath, and **no later call can change it.**

**Git folder, validated without running git** (review AM9). Create and lock calls carry `git_dir`, which Clarvis reads with its own git API. RAVIS accepts it only if:
- `<root>/.git` is a directory and `git_dir` is its realpath; or
- `<root>/.git` is a file `gitdir: <path>`, that path resolves to `<main>/.git/worktrees/<name>`, and that folder's `gitdir` file points back to `<root>/.git`.

Anything else → **422 `GIT_DIR_NOT_ALLOWED`**. A root without `.git` uses `<root>/.clarvis/`. RAVIS still never runs git.

#### Decided: Codex does not work on the ecosystem's own repositories

*Owner decision (b), 13 September 2026.*

- **The rule:** a root that is, contains or lies inside `NERVIS-ecosystem` or `clarvis`, or that is the coding folder itself, is refused with 422 `WORKSPACE_ROOT_NOT_ALLOWED {reason:"protected_repository"}` or `{reason:"allowed_roots_entry"}`.
- **How the owner could allow one later** (documented, not enabled):
  1. The owner lists each exact checkout in `agent_allowed_roots` with `"allow_protected": true`. The coding folder itself stays refused.
  2. The deny list gains in-repository paths: `.run/`, `*.db`, `*.db-wal`, `*.db-shm`, `*.bak`, `.env*`, `nervis.enrollment`.
  3. **Calibration K5c must prove that "deny" beats "write" inside the project root** before such a root is accepted. A failure keeps them refused and is reported to the owner.
  4. STATUS and the live test note the consequence: a stack restart after changing RAVIS's own source (the owner's standing rule) cuts off any Codex task, including one working on RAVIS.

**Idempotency.**
- `Idempotency-Key` (1-128 printable characters) is **required** on create, `turns`, `steer`, `answer`, `settle`, `reissue-token`, and the lock routes `takeover` and `transfer` (review AM8).
- **Scope:** stored per (session or root, `window_id` from the body, key), so two windows never share a key (review AM6).
- **A retry after a lost response replays the original result:** a retried `settle` returns the settled view rather than `409 CLAIM_INVALID`; a retried `transfer` returns the same token; a retried `reissue-token` returns the same new token.
- Kept for 24 h, with the body's sha256 and the response (ids and states only).
- Same key and body → the original response. Same key, other body → **422 `IDEMPOTENCY_KEY_REUSED`**. Missing → **428 `IDEMPOTENCY_KEY_REQUIRED`**.

#### 3.5.2 Shapes

**`SessionView`** (token holders only):

```json
{"id": "as_01J9…", "state": "waiting_on_you",
 "workspace": {"root": "/Users/…/add-utc-demo", "name": "add-utc-demo"},
 "clarvis_task_id": "8b1c…", "mode": "agent", "file_rules": "strict",
 "codex": {"thread_id": "…", "active_turn_id": "…", "model": "gpt-6-astra", "runtime_sha256": "…"},
 "branch": {"name": "clarvis/add-utc", "head_commit_at_start": "…"},
 "pending_requests": [ "<RequestView>" ],
 "queued_feedback": 0,
 "attached_windows": [{"id": "…", "host": "desktop", "since": "…"}],
 "processes": {"attributed": 2, "confirmed_gone": false, "leftover": []},
 "lock": {"id": "pl_…", "state": "running"},
 "settle": {"needed": false, "claimed_by": null},
 "created_at": "…", "updated_at": "…", "last_event_id": 1234}
```

**Session `state`:**

| State | Meaning |
|---|---|
| `starting` | Being created |
| `running` | A turn is active |
| `waiting_on_you` | A turn is active and a request is open |
| `stopping` | Interrupt and process cleanup in progress |
| `stopped` | Turn interrupted, processes confirmed gone; **needs settle** |
| `leftover` | Processes not confirmed gone; locks held |
| `paused_unanswered` | The unanswered policy fired (§4.8); needs settle |
| `paused_for_update` | A new Codex binary appeared while the turn only waited for an answer (§4.4); needs settle |
| `completed_needs_review` | Turn completed, processes confirmed gone; needs settle |
| `idle` | Settled; thread kept; can continue |
| `uncertain` | The runtime died mid-turn; needs settle |
| `failed` | — |
| `ended` | — |

**`RequestView`** (token holders only):

```json
{"id": "rq_…", "kind": "command", "turn_id": "…", "item_id": "…", "opened_at": "…",
 "payload": {"command": "npm install left-pad", "cwd": "web", "reason": "…",
             "network": {"host": "registry.npmjs.org", "protocol": "https"},
             "escalation": null, "gate_hint": null},
 "allowed_decisions": ["once", "skip", "stop"]}
```

**Other kinds' payloads:**
- `fileChange`: `{files:[{path, change:"add|delete|update|rename", moved_to?, added, removed}], reason, grant_root?, outside_workspace:[…]}`.
- `permissions`: `{read, write, network, reason, outside_workspace, denied}`.
- `question`: `{questions:[{id, header, question, options, allow_other}]}`.

Paths are relative to the root; outside paths are absolute and listed.

**`allowed_decisions` is computed by RAVIS** (the §5.2 rules, enforced server-side so no client can grant more):
- **`forRun` (Codex's `acceptForSession`, "don't ask again") and permissions `scope:"session"` are never offered in this delivery** (review AH3). Codex describes them as a session-scoped approval cache, which in RAVIS's long-lived process would outlive the turn and the owner's presence. Calibration K11 records the real scope; offering them later needs proof that they end with the turn;
- writes outside the root, or a denied path → only `skip` and `stop`;
- a `secret` question is never offered: RAVIS answers it empty itself, and emits `request.resolved {by:"policy_secret"}`.

#### 3.5.3 Endpoints

| Method and path | Needs | Body | Success | Errors |
|---|---|---|---|---|
| `POST /api/v1/agent-sessions` | client, Idempotency-Key | `CreateSession` (below) | **201** `{session, session_token, events_url}` | 403 `AGENT_CLIENT_NOT_ALLOWED`; 422 `WORKSPACE_ROOT_NOT_ALLOWED`; 409 `PROJECT_LOCKED {lock}`; 409 `NESTED_PROJECT_LOCKED {lock}`; 409 `CODEX_NOT_READY {state, reason}`; 409 `CODEX_SESSION_LIMIT` (default 3 live); 409 `LOCK_TRANSFER_INVALID`; 503 `CODEX_RUNTIME_UNAVAILABLE`; 428 / 422 idempotency |
| `GET /api/v1/agent-sessions?workspace_root=` | client | — | **200** `{items:[{id, state, clarvis_task_id, created_at, updated_at, waiting_on_you, attached_windows}]}` (no tokens, no payloads) | 403; 422 |
| `GET /api/v1/agent-sessions/{sid}` | token | — | **200** `SessionView` with full `pending_requests` | 403; 404 |
| `GET /api/v1/agent-sessions/{sid}/events` | token | `Last-Event-ID` or `?after=`; `?window_id=&host=` | **200** `text/event-stream` (§3.5.4) | 403; 404; **409 `EVENT_CURSOR_EXPIRED` `{oldest_event_id}`** |
| `GET /api/v1/agent-sessions/{sid}/transcript?limit=50` | token | — | **200** `{turns:[…]}`, read through from Codex's `thread/turns/list` (summary view); nothing stored | 403; 404; 503 |
| `POST …/{sid}/turns` | token, Idempotency-Key | `{text, kind:"continue"\|"carry_on"\|"catch_up", lock?:{transfer_token}}` | **202** `{turn:{state:"starting"}}` | 409 `TURN_ACTIVE` (steer instead); 409 `SESSION_STOPPING`; 409 `SETTLE_FIRST`; 409 `CODEX_NOT_READY`; **409 `PROJECT_LOCKED {lock}`**, `NESTED_PROJECT_LOCKED`, `LOCK_SUPERSEDED` or `LOCK_TRANSFER_INVALID` (turns need the lock, below) |
| `POST …/{sid}/steer` | token, Idempotency-Key | `{text, expected_turn_id?}` | **202** `{delivered:"steered"\|"queued"}` | 422 `EMPTY_STEER`; 409 `SESSION_STOPPING`; **409 `PROJECT_LOCKED {lock}`** when no turn is active and the session doesn't hold the lock (nothing is queued) |
| `POST …/{sid}/interrupt` | token | `{reason:"stop"\|"switch"\|"scope_change"}` | **202** `{state:"stopping"}`; repeats also 202 | 404 |
| `POST …/{sid}/requests/{rid}/answer` | token, Idempotency-Key | `{decision:{kind:"once"\|"skip"\|"stop"\|"answer", text?, answers?}}` | **200** `{resolved:true, decision_kind}` | 404 `REQUEST_NOT_FOUND`; 409 `REQUEST_ALREADY_RESOLVED {by}`; 409 `SESSION_STOPPING`; 422 `DECISION_NOT_ALLOWED {allowed_decisions}` |
| `POST …/{sid}/owner-stop` | **admin credential only** (the menu bar's `owner_cli`, or NERVIS's control route); **no token**; Idempotency-Key | `{source:"menu_bar"\|"dashboard", confirm:{project, turn_id}}` | **202** `{state:"stopping"}` | 403 `OWNER_STOP_NOT_ALLOWED`; 400 `TOKEN_NOT_ACCEPTED`; 404; 409 `CONFIRMATION_MISMATCH`; 409 `NOTHING_RUNNING`; 429 (§3.5.5) |
| `POST …/{sid}/presence` | token | `{window_id, host, panel_connected}` (every 20 s while the panel pings; §3.5.4) | **204** | 404 |
| `POST …/{sid}/mode` | token | `{mode:"agent"\|"auto"\|"unattended"}` | **200** `{mode, applies_from:"next_turn"}` | 422 |
| `POST …/{sid}/leftover` | token | `{action:"stop_them"}` | **200** `{processes_confirmed_gone}` | 409 `NO_LEFTOVER` |
| `POST …/{sid}/settle-claim` | token | `{window_id}` | **200** `{claim_id, expires_at:+5 min}` | 409 `SETTLE_CLAIMED {window}`; 409 `PROCESSES_NOT_CONFIRMED_GONE`; 409 `NOTHING_TO_SETTLE` |
| `POST …/{sid}/settle` | token, Idempotency-Key | `{claim_id, commit, checkpoint_saved:true, next:"idle"\|"end"\|"transfer"}` | **200** `SessionView` | 409 `CLAIM_INVALID`; 409 `PROCESSES_NOT_CONFIRMED_GONE` |
| `POST …/{sid}/cancel` | token | `{}` | **202** `{state:"stopping", end_after_settle:true}` | 404 |
| `DELETE /api/v1/agent-sessions/{sid}` | token | — | **200** `{state:"ended"}` (thread kept, D3) | 409 `SETTLE_FIRST`; 409 `TURN_ACTIVE` |
| `POST …/{sid}/reissue-token` | client (no token), Idempotency-Key | `{workspace_root}` | **200** `{session_token}` | 404; 409 `WINDOW_ATTACHED` |

**Serialisation** (review AM7). Every mutating session route except `presence` (`turns`, `steer`, `interrupt`, `answer`, `mode`, `leftover`, `settle-claim`, `settle`, `cancel`, `DELETE`, `owner-stop`) runs under one per-session action lock. `interrupt` and `cancel` set `stopping` inside that lock, before any answer that arrived at the same moment can be forwarded to Codex.

**Turns need the project lock** (final check F-A1). A Codex session may start a turn only while it holds the project lock for its root: RAVIS's lock row names this session and isn't `superseded`, and the checkout lock file names RAVIS. The check runs under the action lock for:
- `POST …/turns`;
- a `steer` that finds no active turn;
- a queued steer about to start its follow-on turn.

A session without the lock may take it in the same request, atomically: with `lock.transfer_token` (a switch back, §6.2), or, when nothing holds the root or a nested root, by creating the lock and the checkout lock file as `create` does. Otherwise **409 `PROJECT_LOCKED {lock}`** (or `NESTED_PROJECT_LOCKED`, `LOCK_SUPERSEDED`, `LOCK_TRANSFER_INVALID`), and nothing starts.
- So a Codex session left `idle` after a switch can't write while Clarvis's own engine holds the project, whichever window attaches to it.
- A queued steer that finds the lock gone starts nothing: RAVIS emits `feedback {text, how:"not_delivered"}`, and Clarvis keeps the text in the checkpoint's `latestFeedback`.

**`CreateSession`:**

```json
{"workspace_root": "/Users/…/add-utc-demo", "clarvis_task_id": "8b1c…",
 "window": {"id": "…", "host": "code-server"}, "mode": "agent", "model": "",
 "branch": {"name": "clarvis/add-utc", "head_commit": "…"}, "git_dir": "/Users/…/add-utc-demo/.git",
 "start": {"kind": "brief", "text": "<nextMilestoneTask + rendered checkpoint>"},
 "lock": {"transfer_token": null},
 "limits": {"max_steps": 25}}
```

- `limits.max_steps` comes from Clarvis's `clarvis.agent.maxStepsPerTask`. RAVIS enforces it, even while no window is attached (§5.5).

- `start` may instead be `{"kind":"resume", "thread_id":"…", "catch_up_text":"…"}` (§6.2).
- **The body carries no sandbox, permission-profile or approval-policy fields.** RAVIS derives them from `mode`, the owner's D2 and the calibration record (§4.9), so a client can't weaken them.
- Creation acquires the project lock (§3.6) atomically, or takes it over from `lock.transfer_token`.

**Settle** is how git gets committed while RAVIS runs Codex: **Clarvis commits; RAVIS never runs git.**
- **When a session needs it:** after `stopped`, `completed_needs_review`, `paused_unanswered`, `paused_for_update` or `uncertain`.
- **Order:** one attached window claims it, reconciles git, commits on the task branch, saves the checkpoint, then posts `settle`.
- **Guard:** RAVIS refuses a claim until the task's processes are confirmed gone (review B1).
- **One committer:** only one window holds a claim, so two windows never commit.

#### 3.5.4 The event stream

**Frames:**

```text
retry: 3000

id: 1235
event: request.opened
data: {"session_id":"as_…","request":{…RequestView…}}

: heartbeat
```

- **Ids** are per-session, monotonic integers. A heartbeat comment arrives every 15 s.
- **First connection without a cursor:** the stream begins with `event: snapshot` (a `SessionView` with pending requests), then live events.
- **With `Last-Event-ID`, or `?after=`:** events after that id are replayed, then live events.

**Replay buffer.**
- **Two buffers per session, in RAVIS's memory** (review AM12):
  - **deltas** (`agent.delta`, `command.output`): the last 2,000, or 8 MB;
  - **every other event,** kept for the session's life, capped at 20,000 or 32 MB, with the oldest `item.started` and `usage.updated` events dropped first.
- **Resume inside the kept range.** A cursor inside the non-delta range replays every non-delta event after it, with a `deltas_skipped` marker where deltas were evicted, instead of failing.
- **Snapshots** include the last 200 `item.completed` events, so progress and the file-change ledger can be rebuilt.
- **Lifetime:** kept until 30 minutes after the session ends. **Never written to disk.**
- **Cursor too old** (older than the kept non-delta events): **409 `EVENT_CURSOR_EXPIRED`** before streaming (runbook §4.1). The client reads the snapshot and reconnects with `?after=<snapshot.last_event_id>`.

**Attachment.**
- **A window counts as attached only while its Clarvis panel is connected** (review AH2), not merely while its extension host lives, because code-server keeps a host for 3 hours after its tab closes.
  - The chat webview (`media/chat.js`) pings the extension host every 10 s. While those pings are fresh, the host posts `POST …/{sid}/presence {window_id, host, panel_connected:true}` every 20 s.
  - When the pings stop for 25 s (a tab closed, a panel disposed), the host posts `panel_connected:false` and closes the stream.
  - **When the pings resume** (the tab reopened, same extension host, no reactivation), the host posts `panel_connected:true` at once and reopens the stream from its stored cursor, or from a snapshot (F-A8).
  - RAVIS counts a window as attached while its last `panel_connected:true` is under 45 s old.
- `attached_windows` and an `attached` event follow.
- **Closing a stream never stops a task.**

| Event | `data` (besides `session_id`) |
|---|---|
| `snapshot` | `SessionView` |
| `session.state` | `{state, reason?, stopped_by?:"window"\|"menu_bar"\|"dashboard", processes:{confirmed_gone, leftover:[{pid, comm, started_at}]}}` |
| `turn.started` / `turn.completed` | `{turn_id, kind}` / `{turn_id, status:"completed"\|"interrupted"\|"failed", error?:{kind, http_status?, message}, processes_confirmed_gone}` |
| `item.started` / `item.completed` | `{turn_id, item}`. `item` is normalised: `agentMessage {id, text, phase}`; `commandExecution {id, command, cwd, exit_code, duration_ms, output_tail}` (redacted, ≤ 1,500 characters); `fileChange {id, status, changes:[{path, change, moved_to?, added, removed}]}` |
| `agent.delta` / `command.output` | `{turn_id, item_id, text}` (output redacted) |
| `plan.updated` | `{turn_id, steps:[{step, status}]}` |
| `request.opened` / `request.resolved` | `{request}` / `{request_id, by:"window"\|"stop"\|"policy_timeout"\|"policy_secret"\|"policy_elicitation"\|"owner_stop"\|"turn_ended", decision_kind}` |
| `feedback` | `{text, how:"steered"\|"queued"\|"delivered_in_turn"\|"not_delivered"}` |
| `usage.updated` | `{windows, limit_reached}` |
| `model.rerouted`, `warning` | `{from, to, reason}`, `{message}` |
| `attached` | `{windows:[{id, host, since}]}` |
| `lock.changed`, `session.ended` | `{state}`, `{final_state}` |

#### 3.5.5 The owner Stop route (owner decision (a))

**What it is.** `POST /api/v1/agent-sessions/{sid}/owner-stop` is the one agent-session route that doesn't belong to a Clarvis window. It stops a Codex task and does nothing else.

**How it is kept apart from every other session route.**
- **Its own router and dependency.** It is mounted on a separate `APIRouter` with `require_owner_stop_caller`, never `require_agent_client`. A route-table test walks the app: every other `/api/v1/agent-sessions*` and `/api/v1/project-locks*` route must carry `require_agent_client`, and this one must not. The same test covers the admin routes that start Codex work (`/api/v1/codex/reprove`, and `/api/v1/codex/calibration/runs` when enabled): each must carry `require_owner_cli` and refuse `admin.launcher` (F-A3).
- **Who may call it:** only an **`admin.`** credential whose application is in `settings.agent_owner_stop_applications` (default `["owner_cli", "launcher"]`):
  - `admin.owner_cli`, which the menu bar uses through `run.py` (§3.7);
  - `admin.launcher`, which NERVIS holds as its RAVIS admin credential for control routes.

  Separate applications give the menu bar and the dashboard separate rate limits and audit identities (F-A11).
- **Refused:**
  - anonymous and every `client.*` credential, including `client.clarvis` (windows use `interrupt`) and `client.nervis` → **403 `OWNER_STOP_NOT_ALLOWED`**;
  - any request carrying `X-Agent-Session-Token` → **400 `TOKEN_NOT_ACCEPTED`**. The owner capability and the window capability never mix, and NERVIS never handles a token.
- **It grants nothing else.** The dependency opens no other route: answering, approving, steering, starting, continuing, changing mode, settling, ending, reading events or transcripts, and reissuing tokens all keep refusing admin, NERVIS and launcher credentials (§3.5.1).

**Request.**
- Header: `Idempotency-Key`.
- Body: `{"source":"menu_bar"|"dashboard", "confirm":{"project":"add-utc-demo","turn_id":"019…"}}`.
- **`confirm`** must equal the session's folder name and its current (or last) turn id, as listed in `GET /api/v1/codex` `runs[]`. A session reused after a switch keeps its start time, so the turn id is what tells a later turn apart (F-A10). Otherwise **409 `CONFIRMATION_MISMATCH`**, so a stale menu or page can't stop a different task, and nobody can stop a task by id alone.
- **`source`** is recorded for the owner's information; it grants nothing.

**What it does,** under the session's action lock (§3.5.3), exactly as a window's Stop (§5.3):
1. Session `stopping`, with `stopped_by = source`.
2. Every open request is answered with its stop response, with `request.resolved {by:"owner_stop"}`. Later answers get 409 `SESSION_STOPPING`.
3. The end sequence for **that task only** (§4.5): interrupt, background terminals, per-process kills by pid and start time, confirmation.
4. Session `stopped`: paused, thread kept, work left in the project for a Clarvis window to review and save. Or `leftover`, naming the processes.

**Answers:**
- **202** `{state:"stopping"}` at step 1. The final state arrives on the event stream and in `GET /api/v1/codex`.
- **409 `NOTHING_RUNNING`** (with the current state) when the session is `idle`, `stopped`, `paused_*`, `completed_needs_review` or `ended`.
- **404** for an unknown id.
- A retried `Idempotency-Key` replays the 202.
- **429** above 10 calls a minute per application. The menu bar (`owner_cli`) and NERVIS (`launcher`) are counted separately; `source` is informational only.

**How attached windows learn of it.** RAVIS emits on the session's stream:
- `session.state {state:"stopping", stopped_by:"menu_bar"|"dashboard"}`;
- `request.resolved {request_id, by:"owner_stop"}` for each open request;
- `session.state {state:"stopped"|"leftover", stopped_by, processes}`.

Each attached Clarvis window clears its questions (`stopWaiting`), says **"Stopped from the menu bar."** or **"Stopped from the dashboard."**, and settles as after its own Stop, with one window claiming. A window that attaches later sees "This task was stopped from the dashboard at 01:40. Review and save its work?"

**Audit and events.** Audit `ravis.agent_session.owner_stopped {session_id, source, application_id}`; MEP event `ravis.agent_session.state_changed` with `reason_code:"owner_stop"`. No project path in either.

**Security.**
- The launcher's two admin keys are 0600 files, and NERVIS's control token is served to any local page load (§2.2 fact 14). So a program running as the owner could **stop** tasks: a denial of service only, since the route can't write, approve, answer or start.
- The confirmation's folder name and turn id must match, which blocks blind stops by id.

### 3.6 The project lock API (both engines)

**Scope.** A Codex session holds its project's lock itself (taken at create). Clarvis's own engine takes its lock through these routes. All require the client credential (§3.5.1).

**`LockView`:**

```json
{"id": "pl_…", "workspace": {"name": "add-utc-demo", "root_hash": "sha256:…"},
 "holder": {"kind": "codex_session", "session_id": "as_…", "window_id": null, "host": "ravis", "since": "…"},
 "state": "running", "waiting_on_you": true, "heartbeat_age_seconds": 4,
 "verdict": "alive", "taken_over_from": null}
```

- `holder.kind` is `codex_session` or `clarvis_run`.
- `state` is `running`, `stopping`, `transferring`, `leftover` or `superseded` (the restart adoption rule, §6.3).
- `verdict` is `alive`, `unresponsive` or `gone`, by the shared lock rule (§6.3).

| Method and path | Needs | Body | Success | Errors |
|---|---|---|---|---|
| `POST /api/v1/project-locks` | client, Idempotency-Key | `{workspace_root, clarvis_task_id, holder:{kind:"clarvis_run", window_id, host, pid, pid_start}, git_dir, transfer_token?}` | **201** `{lock, lease_token:"lk_…"}` | 409 `PROJECT_LOCKED {lock, takeover_allowed, attach_session_id?}`; 409 `NESTED_PROJECT_LOCKED {lock}` (an ancestor or descendant root is locked); 422 roots |
| `POST …/{lid}/heartbeat` | `X-Lock-Lease` | `{waiting_on_you, state, running_command}` (every 15 s; `running_command` is `{pid, pgid, start, comm}` or null) | **200** `{lock}` | **409 `LEASE_REVOKED {taken_over_by}`** |
| `POST …/{lid}/release` | lease | `{processes_confirmed_gone:true}` | **200** | 409 `PROCESSES_NOT_CONFIRMED_GONE` (body false, or `state:leftover`) |
| `POST …/{lid}/takeover` | client, Idempotency-Key, no lease | `{workspace_root, window:{id, host, pid, pid_start}, confirm:{holder_window_id, holder_since}}` | **200** `{lock, lease_token}` | 409 `HOLDER_ACTIVE`; 409 **`ATTACH_INSTEAD {session_id}`** (Codex sessions are attached, never taken over); 422 `CONFIRMATION_MISMATCH` |
| `POST …/{lid}/transfer` | lease, **or** the holding session's token; Idempotency-Key | `{to:"codex_session"\|"clarvis_run"}` | **200** `{transfer_token, expires_at:+15 min}`; state `transferring` | 409 `PROCESSES_NOT_CONFIRMED_GONE` |
| `GET /api/v1/project-locks?workspace_root=` | client | — | **200** `{lock\|null}` | 403; 422 |

**Takeover rules** (only for `clarvis_run` holders):

| Holder verdict | Takeover |
|---|---|
| `gone` | Allowed, no confirmation |
| `unresponsive`, or `alive` with `waiting_on_you` | Only with `confirm` matching the holder's window id and `since` |
| `alive` and working | **409 `HOLDER_ACTIVE`** |

- **After a takeover** the old lease is revoked, and its next heartbeat gets `LEASE_REVOKED` (the fence, §6.3).
- **A transfer token that expires** leaves the lock with the source holder in `stopped`. It is **never** released by expiry.

### 3.7 Launcher additions (`tools/run.py`)

**`status --json` gains `codex`** (§7.1), read from `GET /api/v1/codex` with `nervis_ravis_credential()` and a 1.5-second timeout. It includes the `runs` list.

**Commands:**
- **`codex sign-in`:** `.run/ravis-admin.token` → POST → `open <auth_url>`. Prints the state, never the URL. Exit codes: 0 started, 3 already signed in, 4 not installed or untested, 5 port busy, 6 RAVIS down, 7 turn active.
- **`codex cancel-sign-in`.**
- **`codex stop <id> --project <name> --turn <turn_id>`:**
  - reads `.run/ravis-owner.token` (below);
  - `POST /api/v1/agent-sessions/<id>/owner-stop` with `source:"menu_bar"`, the confirmation and a fresh `Idempotency-Key`;
  - prints `{"state":"stopping"}`, never the key.
  - Exit codes: 0 stopping; 6 RAVIS down; 8 confirmation mismatch; 9 not found or nothing running; 10 refused.
- **`codex reprove`** (§3.4):
  - reads `.run/ravis-owner.token`;
  - `POST /api/v1/codex/reprove` with a fresh `Idempotency-Key`, then polls `GET /api/v1/codex/reprove` every 5 s for up to 6 minutes;
  - prints the result word, never the key.
  - Exit codes: 0 proven; 6 RAVIS down; 7 a task is running; 10 refused; 11 failed; 12 inconclusive.

**The owner's command-line credential** (final check F-A3, F-A11).
- Before RAVIS starts, the launcher mints a second admin credential, `admin.owner_cli`, into `.run/ravis-owner.token` (0600). It teaches it to RAVIS with `ravis credential admin.owner_cli`, as it does `admin.launcher` today (`tools/run.py:966-1012`).
- **It is never put in NERVIS's environment.** Only `codex stop` and `codex reprove` use it.
- NERVIS keeps `admin.launcher` (`NERVIS_RAVIS_ADMIN_CREDENTIAL`, `tools/run.py:400`) for its control routes.

**RAVIS environment:**
- `RAVIS_CODEX_EXECUTABLE="$(brew --prefix)/bin/codex"`: resolved once per start; the versioned `Caskroom` path is never stored (review N6).
- `RAVIS_CODEX_HOME`.
- `RAVIS_AGENT_ALLOWED_ROOTS`: the owner's coding folder, plus any folder the owner names.
- `RAVIS_AGENT_DENIED_PATHS="<ROOT>/.run"`.
- `RAVIS_AGENT_PROTECTED_REPOSITORIES="<ROOT>:<clarvis checkout>"`.

**Clarvis gets no Codex paths:** Clarvis never runs Codex.

### 3.8 NERVIS control routes

These are unchanged in kind. Each checks the control token, then calls `ravis_peer.configure(…, ravis_admin_credential)` (the `lift_ravis_suppression` pattern, `nervis/src/nervis/api/routes.py:566-591`).

**One change to `configure`** (final check F-A2). Its signature, `configure(client, entry, method, path, credential, body=None)` in `nervis/src/nervis/peers/ravis.py:144-151`, can't send a header, and RAVIS requires `Idempotency-Key` on the owner Stop route.
- It gains a keyword-only `headers: Mapping[str, str] | None = None`, added to the request beside NERVIS's own headers.
- A key named `authorization`, in any case, raises `ValueError`, so a caller can't replace the admin credential.
- Only the task Stop route passes `headers`.

| NERVIS route | RAVIS call |
|---|---|
| `POST /api/v1/ravis/codex/sign-in` | `POST /api/v1/codex/sign-in` |
| `DELETE /api/v1/ravis/codex/sign-in` | `DELETE /api/v1/codex/sign-in` |
| `POST /api/v1/ravis/codex/sign-out` | `POST /api/v1/codex/sign-out` |
| `POST /api/v1/ravis/codex/account/confirm` | `POST /api/v1/codex/account/confirm` |
| `GET /api/v1/ravis/codex/version-check` | `GET /api/v1/codex/version-check` |
| `POST /api/v1/ravis/codex/accept-version` | `POST /api/v1/codex/accept-version` |
| `POST /api/v1/ravis/codex/runs/{sid}/stop` | `POST /api/v1/agent-sessions/{sid}/owner-stop`, with `source:"dashboard"` and the page's `Idempotency-Key`, passed through `headers`. `sid` must match `as_[0-9A-Za-z]{10,40}` and the key `^[A-Za-z0-9_-]{16,128}$`; otherwise 400 without forwarding |

**There is deliberately no other NERVIS route for agent sessions or project locks.** The task Stop above is the only one, and it reaches only the owner Stop route (§3.5.5). **Nor is there one for anything that starts Codex work:** no NERVIS route reaches `/api/v1/codex/reprove` or the calibration route, and RAVIS refuses NERVIS's credential there (§3.4, F-A3). RAVIS would refuse NERVIS's credentials anyway (§3.5.1). Reads of `/api/v1/codex` use the existing relay.

### 3.9 Events and audit

**MEP events** (to NERVIS's hub). **State names, ids and counts only; never request text, commands, paths or feedback:**
- `ravis.codex.state_changed` `{from, to, reason_code}`
- `ravis.codex.sign_in_started`
- `ravis.agent_session.started` `{session_id, mode}`
- `ravis.agent_session.state_changed` `{session_id, from, to}`
- `ravis.agent_session.request_opened` `{session_id, request_id, kind}`
- `ravis.agent_session.request_resolved` `{session_id, request_id, by, decision_kind}`
- `ravis.agent_session.state_changed` carries `reason_code:"owner_stop"` when the owner Stop route stopped the task
- `ravis.project_lock.changed` `{lock_id, state, holder_kind}`

**Trace ids.** Each session stores a trace id minted at creation, so events emitted from the child's reader task carry one (RAVIS map §5.2: an empty `trace_id` is dropped).

**Audit entries:**
- `ravis.codex.sign_in_started`, `…sign_in_cancelled`, `…signed_out`, `…account_confirmed`, `…version_accepted`, `…version_acceptance_revoked`, `…reproof_started`, `…reproof_approval_answered`, `…reproof_finished`
- `ravis.agent_session.token_reissued`, `ravis.agent_session.owner_stopped`
- `ravis.project_lock.taken_over`

---
## 4. Runtime management

### 4.1 Executable: the Homebrew link, and checks (D4; review N6)

**RAVIS settings** (`config.py`, `# ── Codex (M29) ──`, prefix `RAVIS_`):

| Setting | Default | Notes |
|---|---|---|
| `codex_enabled: bool \| None` | `None` | On if the executable resolves at startup |
| `codex_executable: str` | `""` | The launcher sets `$(brew --prefix)/bin/codex`. Empty → RAVIS runs `brew --prefix` once at startup, if `brew` is on `PATH`. **Never** from HTTP or the catalogue; never the `Caskroom` path |
| `codex_home: str` | `""` | Empty → `data_directory()/"ravis-codex"` (§4.2) |
| `codex_expected_team_id: str` | `"2DC432GLL2"` | |
| `codex_usage_refresh_seconds: int` | `900` | Idle polling; paused while turns run (§3.3) |
| `agent_client_applications: list[str]` | `["clarvis"]` | §3.5.1 |
| `agent_owner_stop_applications: list[str]` | `["owner_cli", "launcher"]` | §3.5.5: the menu bar through `run.py`, and NERVIS's control route |
| `codex_reproof_applications: list[str]` | `["owner_cli"]` | §3.4: the menu bar through `run.py` only; never NERVIS's `launcher` |
| `agent_allowed_roots: list[str]` | `["~/Documents/coding"]` | §3.5.1 |
| `agent_denied_paths: list[str]` | `[]` | Added to the built-in deny list (§4.9) |
| `agent_protected_repositories: list[str]` | RAVIS's own checkout (the nearest folder above its source that contains `.git`, found without running git) and that folder's sibling `clarvis` when present; the launcher passes the same two explicitly (F-A12) | Roots refused unless listed with `allow_protected` (§3.5.1; refused by owner decision, none listed) |
| `agent_max_live_sessions: int` | `3` | Counts sessions with an active turn or a pending request; idle and paused sessions don't count (§3.5.3) |
| `agent_unanswered_detached_minutes: int` | `30` | §4.8 |
| `agent_unanswered_attached_minutes: int` | `120` | §4.8 |
| `agent_history_retention_days: int` | `90` | §4.10 |

**The executable check.** Run at startup, every 60 s by `stat`, and on any `(dev, ino, size, mtime)` change of the resolved target:
1. **`realpath` of the configured link, re-resolved every time.**
   - A `brew upgrade` that re-points the link reads as a new sha256, so `untested_version`, not `not_installed`.
   - A general "upgrade everything" upgrades Codex too.
   - Must be a regular file, not group- or world-writable. Missing → `not_installed`.
2. **Signature.** `codesign --verify --strict`, and `TeamIdentifier=2DC432GLL2`. Otherwise `not_available`.
3. **sha256** (`installed_sha256`), cached by `(dev, ino, size, mtime)`, off the event loop.
4. **Verdict:** `tested` if the sha256 is in `tested_runtimes.json`; `accepted` if it is in the owner's accepted list; else `untested`, with the protocol report computed in the background.
5. **Running versus installed.** The child records `running_sha256` when it starts. If `installed_sha256 ≠ running_sha256`, **new sessions and new turns are refused** with 409 `CODEX_NOT_READY` ("Codex changed on disk"). Running turns finish, and the child is replaced only when idle (§4.4).
   - Why: helper links in `tmp/arg0` point at the binary path, so an update mid-run can swap them (review M16).
6. **At `initialize`:** the version in `userAgent` must equal `--version`, and `codexHome` must equal the home's realpath. A mismatch ends the child and marks the runtime `not_available`.

### 4.2 Codex home location (review H1)

| Path | Owner | Contents |
|---|---|---|
| `~/.local/share/ravis-codex/` (0700) | **Codex** | `auth.json`, `sessions/` and `archived_sessions/` rollouts (kept, D3), SQLite state, `skills/.system`, `tmp/arg0`, `.tmp/` |
| `~/.local/share/ravis-codex-scratch/` (0700) | **RAVIS** | Throwaway homes for version checks (§4.3); deleted after each check |
| `~/.config/ravis/codex-state.json` (0600) | **RAVIS** | Account fingerprint, accepted versions (`{sha256, version, stable_tree, experimental_tree, strict_rules, accepted_at, accepted_by, protocol_summary}`), `signed_out_on_purpose`, last usage |
| `ravis.db` tables from migration 8 | **RAVIS** | Agent sessions, requests (ids only), turns, processes, project locks, idempotency (§4.10) |
| `~/.local/share/clarvis/agent-sessions/` (0700) | **Clarvis** | Session tokens per workspace (§3.5.1) |

**What the location does and doesn't do.**
- The home is outside `~/.config/ravis/`. **Moving it doesn't stop reads;** only the permission profile's deny entries do (§4.9).
- Doctor refuses a home equal to or inside `~/.codex` or `config_directory()`, by path comparison only.
- RAVIS tests add `XDG_DATA_HOME` to the autouse fixture.

### 4.3 Version pinning and acceptance (review M1, M5, N4; the owner's rule)

**The rule.** RAVIS "checks the version each time it starts and says plainly when a new one needs re-testing, instead of guessing." **Any binary whose sha256 is neither tested nor accepted is `untested_version`,** and new tasks pause.

**`ravis/src/ravis/codex/tested_runtimes.json`** (committed):

```json
{"format": 3,
 "used_methods": {"client_requests": ["initialize", "account/read", "account/login/start", "account/login/cancel",
                   "account/logout", "account/rateLimits/read", "model/list", "config/read", "config/batchWrite",
                   "permissionProfile/list", "thread/start", "thread/resume",
                   "thread/turns/list", "thread/loaded/list", "thread/unsubscribe", "thread/archive", "thread/unarchive", "thread/delete", "command/exec",
                   "turn/start", "turn/steer", "turn/interrupt"],
                  "server_notifications": ["account/login/completed", "account/updated", "account/rateLimits/updated",
                   "error", "turn/started", "turn/completed", "item/started", "item/completed",
                   "item/agentMessage/delta", "item/commandExecution/outputDelta", "item/fileChange/patchUpdated",
                   "turn/diff/updated", "turn/plan/updated", "thread/status/changed", "thread/tokenUsage/updated",
                   "serverRequest/resolved", "model/rerouted", "warning", "modelProvider/authRecoveryStarted",
                   "modelProvider/authRecoveryCompleted"],
                  "server_requests": ["item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                   "item/permissions/requestApproval", "item/tool/requestUserInput", "mcpServer/elicitation/request"]},
 "strict_rules_surface": {"experimental_definitions": ["ThreadStartParams", "CommandExecutionRequestApprovalParams",
                   "AdditionalPermissionProfile", "AdditionalFileSystemPermissions", "AdditionalNetworkPermissions",
                   "FileSystemSandboxEntry", "FileSystemAccessMode", "FileSystemPath", "FileSystemSpecialPath",
                   "ActivePermissionProfile", "PermissionProfileListResponse", "PermissionProfileSummary",
                   "ThreadBackgroundTerminal", "ThreadBackgroundTerminalsListResponse", "ThreadResumeParams", "TurnStartParams", "CommandExecParams"],
                  "experimental_methods": ["permissionProfile/list", "thread/backgroundTerminals/list",
                   "thread/backgroundTerminals/terminate"]},
 "tested": [{"codex_version": "0.154.0", "source": "homebrew cask",
             "sha256": "<recorded at calibration from the installed cask binary>",
             "stable_tree": "b32fa164e4bedf854688c5e2dcee10b3da22c0306dd7eda148ee9eeed00dfe93",
             "experimental_tree": "<recorded at calibration>",
             "definitions": "schemas/0.154.0.definition-hashes.json",
             "strict_rules_proven": "<true only if calibration K5 passed on this binary>",
             "tested_on": "<calibration date>", "evidence": "STATUS.md calibration record"}]}
```

**Pinned values.**
- **Stable tree:** equal to the alpha's `b32fa164…` (brief §0).
- **Experimental tree and the cask's sha256:** recorded at calibration, not assumed.
- **`definition-hashes.json`:** built from both **combined** bundles, which carry `ServerRequest`. A fixture test checks that every used method resolves.

**What "use this version" checks before recording** (checks 1-6 refuse; check 7 marks `strict_rules`):

| # | Check |
|---|---|
| 1 | Signature and team id |
| 2 | sha256 equals the installed binary |
| 3 | `--version` parses |
| 4 | Both schema trees generate (30-second timeout each) |
| 5 | No used method is missing (combined bundles) |
| 6 | **Handshake on a scratch home:** a throwaway process on `~/.local/share/ravis-codex-scratch/<n>` answers `initialize` and `model/list`. No sign-in, so there is **no second authenticated process** on the real home |
| 7 | **The strict-rules surface:** (a) every `strict_rules_surface` entry has the same hash; (b) the scratch process started with the same flag syntax and representative paths (the real project root and desktop `deniedPaths` come from Clarvis at run time), the `clarvis_run` profile flags and `--strict-config` accepts the profile (`permissionProfile/list` shows it allowed, and `config/read` echoes its deny entries). Failure → `strict_rules:"unproven"` |

**`strict_rules`.**
- A tested entry is `proven` only with K5.
- **An accepted version is always `unproven`** (review AM1). Check 7 only tells whether the `clarvis_run` profile loads at all; schema equality proves nothing about how the sandbox behaves.
- **Only the re-proof makes a version `proven`** (`POST /api/v1/codex/reprove`, §3.4). The owner starts it from the menu bar. It runs K5a with no model, and K5b with one short, capped turn whose four listed commands RAVIS's re-test harness allows, inside RAVIS's one process, then records `strict_rules_proven` for that sha256.
- **With D2 in force, an `unproven` version is `untested_version` for new tasks.** Accepting an update therefore never starts a task until the file rules are re-proven on that exact binary.

**What acceptance does not establish.** Behaviour. The dashboard says so before the button, and every task on an accepted version opens with "Running Codex 0.155.0, which you accepted without re-testing."

**Drift alarm.** A `-32600` `Invalid request: unknown variant` or `invalid type` fails the call, marks the session's turn `failed` ("protocol mismatch"), and re-runs the check.

### 4.4 The app-server supervisor (`ravis/src/ravis/codex/supervisor.py`)

**One child for the whole Mac.**

**Command:**

```text
<executable> app-server --listen stdio:// -c cli_auth_credentials_store="file" -c analytics.enabled=false
  -c features.plugins=false -c sandbox_workspace_write.exclude_slash_tmp=true
  -c sandbox_workspace_write.exclude_tmpdir_env_var=true  <the clarvis_run profile flags, §4.9>
```

**Environment** (allow-list):
- `PATH`: RAVIS's, plus `$(brew --prefix)/bin`.
- `HOME`: the real one, which commands need for npm, pip and git.
- `CODEX_HOME`, `LANG`, `LC_*`, `USER`, `LOGNAME`, `SHELL`, `TERM=dumb`, and `TMPDIR=~/.local/share/ravis-codex/tmp`.
- **Removed:** every `RAVIS_*`, `NERVIS_*`, `CLARVIS_*`, `OPENAI_*` variable, and anything matching `/(TOKEN|SECRET|KEY|CREDENTIAL|PASSWORD)/i`.

**Temp folder per task.** Each thread gets its temp folder inside its project: `config: {"shell_environment_policy.set.TMPDIR": "<root>/.clarvis/tmp/<sid>"}` on `thread/start`. The key is **unverified**; calibration K2b checks it. If it fails, commands get no writable temp folder, and the failure is recorded and put to the owner.

**Spawn and initialize.**
- `asyncio.create_subprocess_exec(..., start_new_session=True)`: its own session and process group, so signals meant for RAVIS don't hit it. The pid and start time are recorded.
- `initialize`: `clientInfo {name:"ravis", title:"RAVIS", version}` and `capabilities {experimentalApi:true}` (needed for permission profiles and background terminals; the pin covers the experimental surface).

**Start.** At RAVIS startup, after the executable check passes (`tested` or `accepted`, with `proven` strict rules under D2). Before that, the state reads `untested_version`, `not_installed` or `not_available`, and **sign-in is also unavailable**, since sign-in runs in this process.

**Routing.**
- Every notification and server request is routed by `threadId` to its session. `account/*` notifications go to `CodexState`.
- Unknown threads, `item/tool/call`, `account/chatgptAuthTokens/refresh`, `attestation/generate`, `applyPatchApproval` and `execCommandApproval` → JSON-RPC error `-32601`.
- `currentTime/read` (experimental, now possible) → `{currentTimeAt: now}`.
- `mcpServer/elicitation/request` → `{action:"decline"}`, with `request.resolved {by:"policy_elicitation"}` (review AL2).

**I/O discipline, so one stalled pipe or slow client can't freeze every project** (review AM3):
- **One reader task** reads the child's stdout, parses each line and only enqueues. It never awaits a database write, an SSE write or a subprocess.
- **Per-session queues.** Each session has a bounded queue (1,000 messages) drained by its own consumer task, which translates, writes the database and fans out to SSE. An overflowing queue first coalesces deltas, then marks that session's stream cursor as expired, so its windows reload the snapshot. **It never pushes back into the pipe.**
- **One writer task** owns the child's stdin: a bounded queue (256), each write with a 5-second timeout. A write blocked for 10 s triggers the hang path.
- **Slow SSE clients** get a bounded send buffer (1 MB); an overflowing client is disconnected and resumes from its cursor.
- **Sampling off the event loop.** `ps` and `lsof` run in a thread executor, with a 2-second timeout per sample.
- **Watchdogs:** the reader's last-read time, the writer's queue age and each consumer's queue age. A consumer stalled for 30 s is restarted without touching the child.

**Health.**
- **Liveness:** the process exit or stdout EOF event.
- **Responsiveness:** every 60 s when no request is in flight, `thread/loaded/list {limit:1}` with a 10-second timeout. It is answered locally, with no network, so a network blip can't restart the child and lose a sign-in (review AL6).
- Three consecutive failures with no active turn → restart. With active turns → two minutes of failures, then treated as a crash.
- **Load:** `-32001` answers back off 0.5, 1, 2 s, then fail that request.

**Crash (the child exits or hangs).**
1. Every session with an active turn becomes `uncertain`. Its open requests resolve as `turn_ended`, and `turn.completed {status:"failed", error:{kind:"runtime_crashed"}}` is emitted.
2. **Every recorded command process of those sessions is killed** (§4.5), and exit confirmed. Unconfirmed → `leftover`.
3. Restart with backoff 1 s, 5 s, 30 s, 5 min; `runtime.process.state:"restarting"`. After 5 failures in 30 minutes → `runtime_down`, until the check passes again.
4. **Threads are not resumed automatically.** Continuing a session (§5.7) calls `thread/resume` in the new child.

**RAVIS restart, including `kill -9`.**
- The child gets EOF and exits (its turns die). On startup, the lifespan reconciliation runs **before** routes serve:
  1. **Sessions** in `starting`, `running`, `waiting_on_you` or `stopping` → `uncertain`; their turns → `uncertain`; their open request rows → resolved `turn_ended`.
  2. **Processes:** every `agent_process` row still alive with a matching start time is killed and confirmed (§4.5). Failures → `leftover`.
  3. **Locks, by the restart adoption rule** (§6.3; review AB1). For each RAVIS-held project lock in the database, RAVIS reads that checkout's lock file:
     - **The file still names RAVIS's previous instance** (the pid and start time in `ravis_instance`) → RAVIS rewrites it with its new pid and start time, and keeps the lock.
     - **The file names anyone else** (a Clarvis window, whatever its verdict) → RAVIS **does not touch the file**. It marks its own lock `superseded`, keeps the session `uncertain`, and refuses `settle-claim`, `turns` and `resume` with **409 `LOCK_SUPERSEDED`** until the window releases or RAVIS adopts the window's lock.
     - **The file is missing and nothing else holds the root** → RAVIS recreates it atomically. If a window wins that create, the previous case applies.
  4. A new child starts.
- **Attached windows** reconnect; RAVIS answers with a `snapshot` (the replay buffer was memory-only).
- **A sign-in in progress is lost** (§3.4).

**A new binary on disk.** No new sessions or turns. Running turns finish. **A turn that is only waiting for an answer doesn't hold everyone up** (review AL5): 10 minutes after the change is noticed, such a turn is paused like the unanswered policy (stop answers, end sequence, thread kept, session `paused_for_update`). Turns actively working may finish. **When no turn is active:** the child is stopped by the end sequence (close stdin → 10 s → SIGTERM → 3 s → SIGKILL → confirm), and a new child starts only if the new binary is `tested` or `accepted` (and `proven` under D2). Otherwise `untested_version`: idle sessions stay idle until the owner accepts or the version is tested.

**RAVIS shutdown** (the lifespan `finally`): interrupt every active turn in parallel (5 s each) → `uncertain` → the end sequence → kill recorded command processes → confirm.

### 4.5 Per-task command processes in a shared Codex process (review H2)

**The problem.**
- Every command Codex runs is a descendant of the one app-server, and **the app-server's process group spans all projects.** A process-group kill is never used.
- Codex runs commands through `/usr/bin/sandbox-exec` (with `WRITABLE_ROOT` parameters, strings) and through pseudo-terminals, which usually start their own session.

**Attribution: which session a process belongs to.**
1. **Background terminals** (experimental, verified schema): after each `commandExecution` item and every 5 s during a turn, RAVIS calls `thread/backgroundTerminals/list {threadId}`. Each `osPid` maps directly to the thread, and so to the session (`attribution:"terminal"`). `osPid` is optional: a terminal listed without one contributes nothing, and its processes must be found by the sandbox-root or parent rules, or reported as leftovers.
2. **OS sampling** every 2 s while any turn is active: `ps -A -o pid=,ppid=,pgid=,lstart=,comm=,args=` gives the app-server's descendant tree. A descendant is attributed to session S when any of these holds:
   - its nearest `/usr/bin/sandbox-exec` ancestor's arguments contain a `WRITABLE_ROOT…=<S.root>` parameter (`attribution:"sandbox_root"`; the exact argument format is confirmed in calibration K6);
   - its working folder (`lsof -a -d cwd -Fn -p <pid>`, sampled for new pids only) is inside `S.root`. **On its own this counts only as `attribution:"cwd_only"`, which is ambiguous: reported, never killed** (review AM5). A command in session A that `cd`s into project B must not make B's Stop kill it;
   - its parent is already attributed to S (`attribution:"parent"`).
3. **Recording.** Attributed processes are written to `agent_process {session_id, turn_id, pid, start_time, comm, attribution}`. They survive a RAVIS restart.
4. **Ambiguity.** A process that matches no session, or matches two (nested roots can't both be locked, §3.6), is **never killed automatically**. It is reported as a leftover with its `comm`.

**The end sequence for one session's commands.** Used for Stop, switch, cancel, the unanswered policy, crash cleanup and restart cleanup:

| Step | Action |
|---|---|
| 1 | `turn/interrupt` (if a turn is active; 5 s), then wait up to 3 s for `turn/completed` |
| 2 | `thread/backgroundTerminals/terminate {threadId, processId}` for each listed terminal (3 s) |
| 3 | SIGTERM each recorded process **individually**, only if its start time still matches |
| 4 | Wait 2 s |
| 5 | SIGKILL the same set |
| 6 | **Confirm**, polling up to 3 s: no recorded process with a matching start time is alive, and a fresh `backgroundTerminals/list` for the thread is empty |

- **After step 6:** `processes_confirmed_gone:true` → `stopped`, or `completed_needs_review` for a completed turn.
- **Otherwise:** `leftover`, naming the processes.
- **Timing:** at most about **15 s** (5 + 3 + 2 + 2 + 3), excluding the terminate call.
- **On normal completion** the same steps 2-6 run. That ends a dev server or watcher the task started, and the settle message says so (review N11).
- **Kills use strong attribution only.** Signals go only to processes attributed to this session in one of these ways, each with a matching start time later than the session's current turn start:
  - by its thread (`terminal`);
  - by its sandbox root (`sandbox_root`);
  - through the process tree from such a process (`parent`).

  Never by `cwd_only`, never by process group. So another project's commands are not signalled, unless Codex itself launched them under this thread's sandbox root, which K6 checks.
- **Residual gap:** a process started and orphaned between two samples, outside a sandbox wrapper and outside the root. Calibration K6 measures it.

### 4.6 Sign-in port conflicts

- **Before `account/login/start`:** bind-test 127.0.0.1:1455 and 1457. Both busy → 409 `CODEX_SIGN_IN_PORT_BUSY`; one busy → proceed. Codex's own "already in use" → 409.
- **Only the one RAVIS child ever signs in.**
- **A sign-in while a turn is active is refused** (409 `CODEX_RUN_IN_PROGRESS`), so a login server never runs beside command processes.

### 4.7 Plugin marketplace download

- **Mitigation:** `-c features.plugins=false` on the child.
- **R1 verification** under `tools/no-network.sb` with a scratch home: no `.tmp/plugins`, no fetch logged.
- **If the flag doesn't hold:** one fetch per child start (RAVIS start or crash restart), stated in `RAVIS.md` §14.

### 4.8 Timeouts, and the unanswered-request policy

**Requests RAVIS sends to Codex:**

| Request | Timeout | On timeout |
|---|---|---|
| `initialize` | 15 s | Kill the child, back off |
| `account/read` | 10 s | Stale state |
| `account/rateLimits/read` | 15 s | Stale state |
| `model/list` | 15 s | Keep the last list |
| `account/login/*`, `account/logout` | 10 s | 503 |
| `thread/start` / `thread/resume` | 30 s | Session `failed` ("Codex didn't start"), lock released after confirm |
| `turn/start` | 30 s | End sequence; `uncertain` |
| `turn/steer` | 10 s | Queue for the next turn |
| `turn/interrupt` | 5 s | Continue the end sequence |
| `thread/turns/list` | 15 s | 503 to the caller |
| `backgroundTerminals/*` | 3 s | Continue with OS kills |

**Requests Codex sends (approvals, permissions, questions): the policy the protocol lacks.** Settled defaults, adjustable in RAVIS settings:
- **Any window attached** (its Clarvis panel connected, by the panel heartbeat in §3.5.4, not merely its extension host): the request waits. Clarvis nudges aloud (the existing `waitingNudge`), and the menu and card show "waiting for your answer". After **2 hours** unanswered, the policy fires.
- **No window attached:** the menu bar and the dashboard show "waiting for your answer · N min" at once. After **30 minutes** the policy fires.
- **When the policy fires:**
  - RAVIS answers the request with its **stop** response: `cancel` for commands and file changes, `{permissions:{}}` for permissions, `{answers:{}}` for questions;
  - it runs the end sequence (§4.5), and the session becomes `paused_unanswered`;
  - **the thread is kept,** and `request.resolved {by:"policy_timeout"}` is emitted;
  - the next window to attach sees "Codex waited 30 minutes for your answer to run `npm install`, then paused. Nothing ran. Continue?"
- **RAVIS never answers `accept`, `acceptForSession` or a grant on the owner's behalf.** The policy only declines and stops.
- **Unattended mode while detached:** RAVIS does not auto-approve anything. Clarvis's narrow auto-answer (§5.2) needs Clarvis's command classifier and runs only in an attached window.

### 4.9 Codex's safety box: the permission profile, gated on calibration (D2; review B3, H1)

**Profile `clarvis_run`,** defined on the child's command line with `-c` flags. The exact TOML syntax is fixed in calibration from the binary's validation errors and `config/read`. Its intent:
- **Extends** the built-in workspace profile.
- **Write:** `project_roots`, **with the roots set explicitly per thread** (review AM4). Every `thread/start`, `thread/resume` and `turn/start` carries the experimental `runtimeWorkspaceRoots:[root]`, pinned in `strict_rules_surface`, so a shared process never infers a root from `cwd`. K5 tests both, with two projects at once.
- **Deny read and write,** marked not escalatable where the format allows:
  - `~/.config/ravis/` (credentials and codex state) and `~/.config/code-server/`;
  - `<RAVIS_AGENT_DENIED_PATHS>` (the launcher's `.run`) and every `agent_denied_paths` entry;
  - `~/.local/share/clarvis/` (session tokens);
  - `~/.local/share/ravis-codex/auth.json`, `sessions/`, `archived_sessions/`;
  - `~/.codex/`, `~/.ssh/`, `~/.aws/`, `~/.config/gh/`, `~/.netrc`.
- **`tmpdir` and `slash_tmp`** are not writable.

**Gate (D2).**
- **Until calibration K5 proves the rules** (decoys unreadable to ordinary **and** approved or escalated commands, per-thread roots honoured), `strict_rules` is `unproven`, and **RAVIS refuses to create Codex sessions** with 409 `CODEX_NOT_READY {reason:"strict_file_rules_unproven"}`.
- **If K5 fails, the question returns to the owner** (the decision says so). Nothing silently falls back to standard rules.

**Per-thread settings, derived by RAVIS from `mode`** (provisional until calibration K1 and K4):

| Mode | `approvalPolicy` | Box | `approvalsReviewer` |
|---|---|---|---|
| `agent`, `auto` (asks first) | `"untrusted"` (or the granular form with `sandbox_approval:false` if K4 shows it forbids leaving the box) | `permissions:"clarvis_run"` on `thread/start` (never combined with `sandbox`) | `"user"` |
| `unattended` | `"on-request"` (same substitution) | same | `"user"` |

- **Every `turn/start`** also carries `sandboxPolicy {type:"workspaceWrite", writableRoots:[root], networkAccess:false, excludeSlashTmp:true, excludeTmpdirEnvVar:true}`, unless calibration shows it conflicts with the profile.
- **Never** `danger-full-access` or `approvalPolicy:"never"`.
- **Internet** is only per approved command, never for the whole run.

**`developerInstructions`** (set by RAVIS on `thread/start`):

```text
You are Clarvis's Codex engine working in the project at the working directory.
- Work only inside this project. Never open credential, key, token or .env files, or anything under ~/.config, ~/.ssh, ~/.aws or a .run folder.
- Use the TMPDIR you were given for temporary files.
- Do not run git commands that change branches, commits, the index or remotes. Clarvis manages git.
- Before each step of the plan, write "STEP: <n>. <what>" on its own line.
- Run the checks the plan lists and report each command with its real output.
- If you need a decision, ask with request_user_input rather than guessing.
```

**Output redaction** happens in RAVIS, before relaying, and again in Clarvis before storing. Command output isn't relayed in `command.output`, `output_tail` or transcripts when:
- the command or `commandActions[].path` names a denied path, or
- the output matches a token pattern (`sk-`, `ghp_`, a JWT shape, 32+ base64url characters after `token`, `key` or `secret`).

It is replaced by "[output hidden: it may contain a secret]". Codex's own rollouts are outside RAVIS's control, which is why the history folder is denied to commands.

### 4.10 The agent-session store, retention, and the metadata-only exception

**Migration 8** (`storage/database.py`, appended to `MIGRATIONS`, with a `.v8.bak` backup first). Store classes shaped like `SessionStore`, with an injected clock. **Not `routing_session`.**

| Table | Columns |
|---|---|
| `agent_session` | `id` PK, `application_id`, `workspace_root` (realpath), `workspace_root_hash`, `workspace_name`, `clarvis_task_id`, `engine`, `codex_thread_id`, `active_turn_id`, `model`, `mode`, `file_rules`, `state`, `token_sha256`, `trace_id`, `runtime_sha256`, `account_fingerprint`, `branch_name`, `head_commit_at_start`, `last_event_id`, `created_at`, `updated_at`, `ended_at` |
| `agent_request` | `id` PK, `session_id`, `turn_id`, `codex_request_id`, `kind`, `opened_at`, `resolved_at`, `resolved_by`, `decision_kind`. **No payload text** |
| `agent_turn` | `id`, `session_id`, `kind`, `started_at`, `ended_at`, `status`, `uncertain` |
| `agent_process` | `session_id`, `turn_id`, `pid`, `start_time`, `comm`, `attribution`, `recorded_at`, `confirmed_gone_at` |
| `project_lock` | `id` PK, `workspace_root`, `root_hash` UNIQUE, `holder_kind`, `holder_session_id`, `holder_window_id`, `holder_host`, `holder_pid`, `holder_pid_start`, `lease_sha256`, `state`, `waiting_on_you`, `heartbeat_at`, `acquired_at`, `taken_over_from`, `transfer_token_sha256`, `transfer_expires_at` |
| `agent_idempotency` | `scope`, `key`, `body_sha256`, `status`, `response_json` (ids and states only), `created_at` |

**Retention** (settled defaults):
- **`agent_session`, `agent_turn`, `agent_request`:** kept 30 days after `ended_at`, then deleted.
- **`agent_process`:** rows deleted 24 h after `confirmed_gone_at`.
- **`agent_idempotency`:** 24 h.
- **`project_lock`:** a row exists only while held.
- **Codex's own threads (content, D3):**
  - archived with `thread/archive` only when a session is explicitly ended or cancelled, **never on a switch or a settle**, and unarchived with `thread/unarchive` before any later `thread/resume` (review AH4; calibration K13);
  - deleted with `thread/delete` after `agent_history_retention_days` (90) without use, unless a live Clarvis checkpoint still names the thread;
  - sweeps run on the existing retention timer (`app.py:691-716`).

**The metadata-only rule and its exception** (`RAVIS.md:1205`):
- **RAVIS's database, logs, audit and MEP events stay metadata-only:** ids, states, kinds, counts, timestamps, the workspace realpath and basename. **Never prompt, agent text, approval text, commands, output or diffs.**
- **The exception**, by owner decision D3 and the A architecture:
  1. Content passes through RAVIS's **memory** while it is relayed: the per-session SSE replay buffers (the last 2,000 deltas or 8 MB, plus every other event for the session's life, up to 20,000 events or 32 MB; §3.5.4), never persisted, gone 30 minutes after the session ends.
  2. **Codex's own conversation history** is written by Codex into RAVIS's Codex folder and kept for the retention above.
  3. **`workspace_root`** is stored, because Codex must run there. That is a narrow exception to `RAVIS.md` §9.7, as `sessions.py:20-23` reads it.

---
## 5. Clarvis engine seam

**Rules for all new code.**
- New modules are vscode-free unless the design says otherwise.
- `eslint.config.mjs` overrides `complexity` to 8 for `src/engine/**` (review L4).
- `RunSession.close` and `AgentRunner.runGated`, already at 15, gain no branches.
- **The approval mapping is provisional until calibration** (§10.4; review H5).

### 5.1 Engine resolution, the factory, and the remote Codex runner

**`src/engine/engineChoice.ts`** (pure). This is carried over, with these rules:
- **Codex** only when all hold: the effective coding model is exactly `ravis/codex`, from user scope or a Clarvis-recorded override; the base URL is loopback; the workspace is trusted.
- **`ravis/codex/<x>`** → refuse.
- **`ravis/codex` from a repository's `.vscode/settings.json`** → refuse, with "Choose it yourself under Models".
- **The override `clarvis.engine.override`** `{taskId, modelId, since}` applies only to its own unsettled task, and is cleared on settle, on a model pick or on a new task. The picker shows the effective engine (review M4).

**`src/engine/CodingRun.ts`:**

```ts
export interface CodingRun {
  readonly engine: 'clarvis' | 'codex';
  run(task: string, signal: AbortSignal): AsyncIterable<AgentEvent>;
  interject(text: string): void;
  drainInterjections(): string[];                          // review H8
  readonly result: { commits: string[]; files: string[] };
  readonly blocked: boolean;
  readonly branches: { working?: string; startedFrom?: string };
  readonly stillMissing: MissingDependency | undefined;
  readonly codexSession?: { id: string; threadId?: string };
}
```

**`RemoteCodexRunner`** (`src/engine/codex/RemoteCodexRunner.ts`: thin vscode glue over the pure `src/engine/codex/runCore.ts`, which takes an injected `RelayClient`). It has two entry points:
1. **`run(task, signal)`, a new task:**
   1. Refuse, before touching anything, if the folder isn't trusted; no RAVIS credential is available; or `GET /api/v1/codex` isn't ready (`state == signed_in`, `verdict ∈ {tested, accepted}`, `strict_rules == "proven"`, `runtime.process.state == "running"`). Each refusal comes with its reason and, where relevant, the **Sign in** link.
   2. `Checkpoint.begin` and `captureAll(branch.atRisk())`, then `AgentBranch.begin(task)`, or **`AgentBranch.continueOn(branch, headCommit)`** on a switch (B2).
   3. `POST /api/v1/agent-sessions` with `Idempotency-Key = sha256(taskId + ":" + windowId + ":" + attempt)`. Save the token in the token file (§3.5.1). Open the event stream with `window_id`.
   4. Run the event loop (below).
2. **`attach(summary)`, reattaching** (§5.7): read the token, then open the stream with the stored `Last-Event-ID` (workspaceState key `clarvis.codex.lastEvent.<sid>` for this host), or without a cursor to receive a `snapshot`. Then run the same loop.

**The event loop.** Relayed events become `AgentEvent`s (§5.5):
- `request.opened` → `askEngine` (§5.2) → `POST …/answer`.
- `interject` → `POST …/steer` (§5.4).
- Stop → `POST …/interrupt` (§5.3).
- A state needing settle → settle (§6.2).
- The loop ends when the session reaches `idle` or `ended`, **or when the window closes**. A closed window leaves the task running in RAVIS.

**`AgentRunner` changes** (small, all review-driven):
1. `readonly engine = 'clarvis' as const`.
2. `drainInterjections()` returns `this.interjections.splice(0)` (H8; `AgentRunner.ts:654-659` takes them only inside the loop).
3. The constructor option `continueOn?: BranchContinuation`, passed to `protect()`, which calls `branch.continueOn` instead of `branch.begin` (B2).
4. **The fence** (N3, AH1). A `stillHolds: () => Promise<boolean>` constructor option is checked before every mutating tool call in `dispatch`, and before `stopped()` or `completedRun()` commit.
   - It is false only on `409 LEASE_REVOKED`, or when the lock file no longer names this window. RAVIS being unreachable is not a loss (§6.3).
   - While a command runs, `runGated` reports its pid, process group and start time for the heartbeat's `running_command` (AM11, F-A4). Clarvis spawns commands detached, so the group id equals the pid. A lost lease → the run ends with "Another window took over this task; I've left everything as it was", writing nothing.

**`AgentBranch.continueOn(branch, headCommit)`** (B2, N9):
- **Refuses** (`isolated:false`, with advice) when the branch is missing, or its tip is neither `headCommit` nor a descendant of it.
- **Otherwise:**
  - checks out the existing branch (no `createBranch`);
  - leaves `clarvis.agent.baseBranch` unchanged;
  - sets `created` to the branch and `previousBranch` to the remembered base, so `discardIfEmpty` (`AgentBranch.ts:247-275`) and undo (`:303-315`) read `base..branch` correctly.
- **Pure decision** in `branchNames.ts` as `continuationDecision(existing, tip, headCommit, isAncestor)`.

**The factory: `src/chat/codingRunFactory.ts`.** `createCodingRun(choice, deps)` → `AgentRunner` or `RemoteCodexRunner`.

**`RunSession.ts` changes:**
- **Lines 264-278:** the factory replaces `new AgentRunner`. Line 198 `running?: CodingRun`, line 374 `wrapUp(runner: CodingRun)`.
- **New `askEngine`** (§5.2) and **`attach(summary)`** (§5.7).
- **Clarvis-engine writing runs** take the project lock before the factory, in this order:
  1. the checkout lock file (§6.3);
  2. `POST /api/v1/project-locks`, or the file alone if RAVIS is unreachable;
  3. a heartbeat every 15 s that feeds `stillHolds`.
- **Codex runs** get their lock from session creation.
- **The lock is released** only after the run's processes are confirmed gone. **Never from `finally` while `checkpoint.transfer` is set:** the switch owns release (N2).
- **Read-only answers (`Replier`) take no lock** (review M8). Stated as a behaviour change.

### 5.2 Approvals and questions in Clarvis

**Where they come from.** Requests arrive as `request.opened` with a `RequestView` and **RAVIS's `allowed_decisions`**. Clarvis only renders what RAVIS allows: server-side policy, client-side presentation.

**One question at a time** (review M2).
- `RunSession.askEngine` keeps a first-in-first-out queue. `PendingChoice.ask()` cancels whatever is pending (`PendingChoice.ts:52-65`), so only the head is shown.
- `request.resolved` (answered in another window, Stop, the policy, turn end) removes a queued or showing entry. A showing one is cleared with a line: "Answered in the other editor." / "Stopped." / "Codex paused after waiting."
- **Stop** resolves the whole queue locally at once.

**Answering** (review M3).
- After any await (including `checkpoint.capture` for file changes), the decision is re-evaluated: `engineDecisionAfterAsking(answer, stopped)`, where `stopped` means a local Stop was pressed or a `session.state` of `stopping` has arrived. That is the last step before the POST.
- **`Idempotency-Key = rid + ":" + window_id`** (review AM6), so a retry from the same window is safe, and a second window never collides with the first. RAVIS checks the request's resolution **before** idempotent replay across windows, so another window gets `409 REQUEST_ALREADY_RESOLVED`, never `422 IDEMPOTENCY_KEY_REUSED` or someone else's 200.
- **Answers to errors:**
  - `409 REQUEST_ALREADY_RESOLVED` → clear, with who resolved it;
  - `409 SESSION_STOPPING` → clear;
  - `422 DECISION_NOT_ALLOWED` → re-render from the returned `allowed_decisions`.

**How requests are shown.** Labels come from `allowed_decisions`, and the chat line and detail show the real scope:

| Kind | Chat line and detail | Button labels → decision |
|---|---|---|
| `command` | "Codex wants to run a command." `` `npm test` `` · in `./web` · reason; `Gate.classifyCommand`'s `explainGate` sentence when a category applies; "…and it needs the internet for this command: host (https)" when `network`; "…and it asks to step outside Codex's safety box: …" when `escalation` | **Run it** (`once`) · **Skip it** (`skip`) · **Stop the run** (`stop`) |
| `fileChange` | "Codex wants to change 3 files." `update src/app.ts (+12 −3)`, `add …`, `rename a → b`, `delete …`; reason. Outside-project paths: "Codex wants to write outside this project (…). Clarvis never lets an engine do that." | **Apply** · **Don't apply** · **Stop the run**. Only **Don't apply** / **Stop** when outside. Before `once`: `checkpoint.capture(path)` per updated, deleted or renamed path, then re-evaluate |
| `permissions` | "Codex asks for more access." `read: …`, `write: …` (outside and denied marked), `internet: on` | **Allow for this step** · **Don't allow** · **Stop the run** |
| `question` | `header`: `question` | Each option · typing, when `allow_other` · **Stop the run** |
| (secret question) | Already answered empty by RAVIS: "Codex asked for a secret (header). Clarvis doesn't pass secrets through the chat." | — |

**No "don't ask again"** (review AH3). There is no "for the rest of this run" button, because Codex's session-wide approval cache would outlive the turn in RAVIS's long-lived process.

**Unattended mode, attached only.** `askEngine` answers `once` without asking only for a `command` with no gate category, no network and no escalation, and a `fileChange` inside the workspace, when `once ∈ allowed_decisions`. Everything else asks. **Detached, RAVIS never auto-approves** (§4.8).

**Several attached windows** each show the question. The first answer wins, and the others are cleared by `request.resolved`.

**Bridge.** `whileAwaiting(activity, 'command' | 'step' | 'other', …)`: the category only.

### 5.3 Stop over the relay (coding-87)

**What must hold:**
1. Stop releases the question at once.
2. A late answer never starts a step.

**Stop, pressed in any window attached to the session:**
1. **At once, locally:** `busy.stop()` → `runs.stopWaiting()` cancels the showing question and clears the queue. Chat: "Stopping Codex…". Interjections go to `latestFeedback`.
2. **`POST …/interrupt {reason:"stop"}`**, retried every 2 s for up to 10 s on a network error.
   - If RAVIS stays unreachable, the chat says "RAVIS isn't answering, so Codex can't be told to stop from here yet. I'll keep trying." It retries every 5 s and shows the state on the menu bar.
   - That is an honest limit of A: a RAVIS that isn't answering can't be reached by anyone. If RAVIS itself is down, Codex is already stopped (§4.4).
3. **RAVIS, in this order, under the session's action lock.** One per-session action lock serialises every mutating session route listed in §3.5.3 (review AM7, F-A7); tested with concurrent POSTs from two windows.
   1. session `stopping`;
   2. **every open request for the session is answered with its stop response first** (`cancel`, `{permissions:{}}`, `{answers:{}}`), and `request.resolved {by:"stop"}` is emitted;
   3. **from here, any answer POST gets `409 SESSION_STOPPING` and is never forwarded to Codex;**
   4. the end sequence (§4.5): interrupt, terminals, per-process kills, **confirm**;
   5. `session.state stopped`, or `leftover` naming the processes.
4. **Clarvis on `stopped`:** `settle-claim` → git reconciliation → commit on the task branch → save the checkpoint → `settle {next:"idle"}` → `done` "Stopped." with the files. Only the window holding the claim commits.
5. **Clarvis on `leftover`** (review N1): nothing is committed, and both locks stay held. The chat names the processes, with **Stop them** (`POST …/leftover`) or **Keep waiting**. RAVIS re-checks every 5 s, and the flow continues at step 4 once they are gone.

**Stop from the menu bar or dashboard** (§3.5.5). No window starts it. When RAVIS's stream says `stopped_by:"menu_bar"` or `"dashboard"`:
- each attached window runs step 1 locally (clears questions, `stopWaiting`);
- it says "Stopped from the menu bar." or "Stopped from the dashboard.";
- it continues at step 4 or 5.

A window that wasn't attached shows the line when it next attaches.

**Tests** (`runCore.test.ts`, against `FakeRavisRelay`):
- Stop clears the question before any network call.
- An owner Stop (`stopped_by:"dashboard"`) clears the question, shows its line, and settles.
- An answer raced after Stop sends nothing, or gets 409, and never an accept.
- An unreachable RAVIS keeps retrying and says so.
- `leftover` blocks settle.

**RAVIS tests** (`test_agent_sessions_stop.py`):
- Open requests are resolved before `turn/interrupt`.
- Answers after `stopping` → 409.
- Only this session's recorded processes are signalled.
- Settle is refused until confirmed.

### 5.4 Steering and feedback (coding-6c; review H8)

- **Attached, turn active:**
  - `RunSession.redirect` → `RemoteCodexRunner.interject(text)` → `POST …/steer` (`Idempotency-Key` a new uuid).
  - RAVIS calls `turn/steer {expectedTurnId}`. On a race (no active turn, a mismatch, not steerable) it **queues the text server-side** and starts the next turn with it when the current one completes, unless the session is stopping or no longer holds the project lock (§3.5.3). In that case the text comes back as `feedback {how:"not_delivered"}` and goes to `latestFeedback`.
  - Because the queue lives in RAVIS, this still works if the window detaches right after typing.
  - The `feedback` event shows "Passed to Codex" in every attached window.
- **Empty text** sends nothing.
- **While stopping, stopped, settling or switching:**
  - `ChatService.runTook` checks first: if the task's checkpoint has `transfer` set, or the session isn't `running`, the text is appended to the checkpoint's `latestFeedback`. The chat says "Noted — I'll pass that on when the task continues."
  - A `409 SESSION_STOPPING` or `409 PROJECT_LOCKED` from steer does the same.
  - A `409 PROJECT_LOCKED` from `turns` says "Clarvis's own engine is working on this project; Codex can continue after a switch back."
- **Detached** (RAVIS unreachable, or the stream down and not yet reattached): typed text → `latestFeedback`. On reattach it is sent as a steer if a turn is active, or included in the next turn's text.
- **Clarvis's own engine:** `drainInterjections()` at every stop → `latestFeedback`.
- **The destination's first brief, or the next turn,** always includes undelivered `latestFeedback`, which is then marked delivered.

### 5.5 Milestone bookkeeping from relayed events

| Needed | From the relay |
|---|---|
| `STEP:` lines | `item.completed` `agentMessage.text`, as one `text` event per message. `agent.delta` goes to the terminal only |
| Ledger `detail` | Per completed `fileChange` change, keyed `(item.id, path)`: `applyEdit: path`, `writeFile: path` (add), `deleteFile: path`. Per completed `commandExecution`: `runCommand: <command> → exit <code>`, `quiet` for read-only actions |
| `result.files` | Completed file-change paths **plus** git reconciliation at settle (`git status --porcelain=v1 -z`, `git diff --name-only <base>..`), within the root, excluding `.clarvis/tmp` |
| `result.commits` | The settle commit(s) |
| Check output | The final `agentMessage`, plus "Checks Codex ran:" from completed `commandExecution` items matching a `- Check:` line or `detectTestCommand`: real exit codes and redacted output tails |
| Step cap | **Enforced by RAVIS,** so it holds while detached: `CreateSession.limits.max_steps` (from `clarvis.agent.maxStepsPerTask`) counts completed command and file-change items. At the cap RAVIS runs the end sequence and emits `turn.completed {status:"interrupted", error:{kind:"step_cap"}}`. Clarvis says "Say carry on…"; carry on → `POST …/turns {kind:"carry_on"}` |
| `blocked` | True for failure classes (§9); false for a user Stop |
| Detached completion | `completed_needs_review`: the next attached window settles and offers the milestone review as today (`wrapUp`) |
| Run record | `RunRecord` gains `engine` and `codexSessionId` |

### 5.6 Clarvis's safety layers under A (corrected facts, review B3)

| Clarvis layer | Under Codex |
|---|---|
| Modes (`chat`, `plan` can't edit) | Apply: no session is created |
| Workspace Trust | `run` refuses in an untrusted folder; RAVIS also refuses roots outside `agent_allowed_roots` |
| Step approval and the deny-list (`Gate`) | Replaced by relayed approvals; `Gate` only adds warning lines. **Commands Codex runs without asking are not seen beforehand** |
| OS sandbox | Codex's box (§4.9): writes only in the project and its temp folder; internet only per approved command; **reads restricted by the profile's deny entries** once calibration proves them (D2) |
| Sensitive-file read gate | The profile's deny entries (D2), plus redaction |
| Containment of writes | The profile plus RAVIS's policy: outside-root writes can only be declined |
| Checkpoint and undo | `begin` + `captureAll` at task start; `capture(path)` before each accepted file change **while attached**; while detached, the task branch is the undo |
| Branch isolation | `begin`/`continueOn` at start, commit at settle. HEAD found off the task branch at settle → `uncertain` and a chat line |
| Branch-flow protection, Bridge category-only | Apply |
| Step cap | Enforced by RAVIS (§5.5) |

**The mode mapping lives in RAVIS** (§4.9). Clarvis sends `mode` at create and on change (`POST …/mode`, effective from the next turn).

### 5.7 Reattach on activation, from either host

**On activation,** with a workspace folder open (desktop VS Code or code-server):
1. **Credential:** code-server uses its environment; desktop reads `clarvis.ravis.credentialFile`. None on desktop → one chat line: "To see Codex tasks from this editor, point Clarvis at RAVIS's key once," with an **Open setting** button. Nothing else happens.
2. **List:** `GET /api/v1/agent-sessions?workspace_root=<root>`. RAVIS unreachable → "RAVIS isn't answering; Codex tasks for this project can't be shown yet," retried every 30 s.
3. **For each live session,** read its token from `~/.local/share/clarvis/agent-sessions/<hash>.json`.
   - **Token missing** → offer **Reconnect** (`reissue-token`, allowed once no window has been attached for 60 s).
   - **Otherwise** → `RunSession.attach(summary)`, then `RemoteCodexRunner.attach` with the stored cursor, or a snapshot.
4. **Chat** (for example): "Codex is still working on milestone 2 in this project (started 01:12 in the browser editor; one question is waiting). Reconnected." Pending requests are then asked (§5.2).
5. **A session needing settle** → "Codex finished (or was stopped) while no editor was open. Review and save its work now?" → settle-claim → reconcile → commit → checkpoint → settle → the milestone offer.
6. **A session paused because another editor held the checkout lock** (`uncertain`, RAVIS's lock `superseded`, §6.3), **when that editor is `gone`** by the shared lock rule (final check F-A9):
   - the window reconciles that holder as in §6.3: it kills the recorded `running_command` group and leftovers, and confirms;
   - it takes the lock file and registers it with `adopt_file_lock:true`, and RAVIS adopts it, replacing the superseded row;
   - it settles the Codex session as in step 5, then releases the lock or continues.

   The card and menu stop saying another editor is working.

**Presence** (review AH2). While attached, the window keeps posting `presence` from its panel's 10-second pings. When the panel goes away (for example a closed code-server tab), it posts `panel_connected:false` and closes the stream, so RAVIS's 30-minute rule starts even though the extension host lives on. When the panel comes back, it posts presence and reopens the stream at once (§3.5.4).

**Two windows at once,** one on each host, both attach. Questions show in both, the first answer wins, and the settle claim keeps commits to one window.

### 5.8 The three places that construct runners, and the files

| Site | Change |
|---|---|
| `src/chat/RunSession.ts:264-278` | Factory; Clarvis-engine lock through RAVIS plus the file floor; `attach()` |
| `src/extension.ts:999-1009` (`clarvis.runTask`) | `resolveEngine`; Codex → "Codex runs from the Clarvis panel, where its questions can be answered." Lock around the `AgentRunner` run, with the fence |
| `src/chat/Replier.ts:72` / `:205` (read-only answers) | Chat guard only ("Codex is a coding engine, not a chat model…"); **no lock** |

**Files:**

| File | Purpose |
|---|---|
| `src/engine/relay/{relayClient,sseReader,tokenStore,credentialFile,presence}.ts` and a 10-second ping in `media/chat.js` | HTTP with idempotency; SSE with `Last-Event-ID`, heartbeats and cursor-expired recovery; the 0600 token file; desktop credential |
| `src/engine/codex/{runCore,translate,approvals,redact}.ts` | Pure core, event translation, approval rendering, redaction before storing |
| `src/engine/codex/RemoteCodexRunner.ts` | vscode glue |
| `src/engine/lock/{lockClient,fileLock,lockRule}.ts` | RAVIS lock API client; checkout lock file; the shared rule (`lock-rule-cases.json`) |
| `src/engine/{engineChoice,CodingRun,checkpoint,transfer}.ts` | Engine choice, the run surface, the checkpoint, switching |
| `src/agent/{AgentBranch,AgentRunner,branchNames}.ts` | `continueOn`, `engine`, `drainInterjections`, `stillHolds`; `continuationDecision` |
| `src/chat/{codingRunFactory,EngineSwitch}.ts` | Factory, switch command and offers |
| `RunSession.ts`, `ChatService.ts` (`runTook` routing, activation reattach), `stopDecision.ts` (`engineDecisionAfterAsking`), `modelPickers.ts`, `OpenAiCompatibleProvider.ts` (`X-Clarvis-Engines`), `openaiCatalog.test.ts`, `runLedger.ts`, `eslint.config.mjs`, `package.json` (`clarvis.codex.model`, `clarvis.ravis.credentialFile`, `clarvis.switchEngine`, version 0.17.0) | As described |
| `src/test/fakes/FakeRavisRelay.ts` | A labelled test double implementing §3.5 and §3.6 from the shared contract fixtures |

---
## 6. Checkpoint, switching and one writer

### 6.1 The checkpoint, shared by both hosts

**Where it lives.** Under A the task can move between hosts: started in code-server, reattached in desktop VS Code. VS Code's `workspaceState` is per host, so **the checkpoint lives in a file in the checkout's git folder**:
- **Path:** `<git_dir>/clarvis-task-checkpoint.json` (the validated git folder, §3.5.1), mode 0600, never committed; or `<root>/.clarvis/task-checkpoint.json` without git.
- **Writes:** atomic (write, then rename), and only while the writer holds the project lock (checked through the lease or session token).
- **Contents:** no secrets or tokens. It is never sent to NERVIS or the Bridge.

```ts
export interface EngineCheckpoint {
  version: 3;
  taskId: string; savedAt: string; savedByHost: 'desktop' | 'code-server';
  engine: 'clarvis' | 'codex';
  status: 'running' | 'settled' | 'interrupted' | 'uncertain' | 'transferring' | 'leftover';
  plan: { file: 'plan.md'; milestone?: { index: number; title: string }; uncheckedSteps: string[]; nextAction?: string };
  requirements: { exclusions: string[]; rejected: string[] };
  git: { branch?: string; baseBranch?: string; baseCommit?: string; headCommit?: string;
         dirty: string[]; diffStat: { path: string; added: number; removed: number }[] };
  changedFiles: string[];
  checks: { command: string; exitCode: number | null; engine: 'clarvis' | 'codex'; ranAt: string; outputTail: string }[];
  latestFeedback: { text: string; typedAt: string; host: string; delivered: boolean }[];
  unresolvedQuestions: { engine: 'clarvis' | 'codex'; summary: string }[];
  uncertainOperations: { kind: 'command' | 'fileChange'; summary: string; state: 'unknown' | 'failed' }[];
  leftoverProcesses?: { pid: number; start: string; comm: string }[];
  previousModel?: string;
  lock?: { id: string; kind: 'codex_session' | 'clarvis_run' };
  codexSession?: { id: string; threadId?: string; lastTurnId?: string;
                   lastTurnStatus: 'completed' | 'interrupted' | 'failed' | 'unknown';
                   runtimeSha256: string; model?: string; sawHeadCommit?: string; accountFingerprint: string };
  transfer?: { from: 'clarvis' | 'codex'; to: 'clarvis' | 'codex'; toModel: string; startedAt: string;
               step: 'stopping' | 'confirming' | 'settling' | 'saved' | 'starting' | 'started' | 'failed'; error?: string };
}
```

**Limits.** At most 64 KB, with every list capped and output tails redacted.

**Stale data.** A checkpoint that disagrees with RAVIS's session or lock is shown with "This may be a little out of date" (review L9).

### 6.2 Switching, both ways, through RAVIS's lock (review B1, B2, H8, N1, N2)

**Throughout a switch:**
- **The project lock stays held,** in `state: "transferring"`, by the source holder until the destination has started.
- **Anything typed goes into `latestFeedback`.**
- **Pending approvals are never answered for the destination.** They become `unresolvedQuestions`.
- **Entry points:** `Clarvis: Switch coding engine`, or the offer after a stopped or failed task.

**Codex → Clarvis's own engine** (for example `ravis/clarvis-agent`):
1. **Confirm:** "Stop Codex and continue this task with ravis/clarvis-agent? That uses your API providers, which RAVIS counts as spend." No paid work without this click.
2. **`transfer.step = 'stopping'`:** `POST /api/v1/project-locks/{lid}/transfer {to:"clarvis_run"}` with the session token. The lock goes to `transferring` and a `transfer_token` is returned. Then `POST …/interrupt {reason:"switch"}`.
3. **`'confirming'`:** wait for `session.state stopped`.
   - **`leftover`** → **Stop them**, or **Cancel the switch**. **Cancelling keeps the locks,** in `leftover`, until the processes are confirmed gone. A destination never starts beside a leftover (N2).
4. **`'settling'`:** settle-claim → git reconciliation → commit "Codex's work on <task> (stopped for a switch)" → record `headCommit`, `diffStat`, `changedFiles` and `codexSession.sawHeadCommit` → save the checkpoint → `settle {next:"transfer"}`.
5. **`'saved'`:** `previousModel = 'ravis/codex'`; override → the destination.
6. **`'starting'`:**
   - take the checkout lock file (§6.3);
   - `POST /api/v1/project-locks {transfer_token, holder:{kind:"clarvis_run", …}}` moves the lock atomically to this window;
   - start `AgentRunner` with `continueOn: {branch, headCommit}`, the fence, and the brief `nextMilestoneTask` + `renderCheckpoint`, which includes every undelivered `latestFeedback` entry.
7. **`'started'`** once `continueOn` succeeds and the first model call is under way. The Codex session is settled with `next:"transfer"` and stays `idle`, without the lock. **It can't start a turn, or be steered into one, until it holds the lock again** (§3.5.3, F-A1). **It is neither ended nor archived** (review AH4), so the same session and thread continue after a switch back. Delivered feedback is marked.

**Clarvis's own engine → Codex:**
1. **Confirm:** "Stop and continue this task with Codex? That uses your ChatGPT plan's allowance." Refused up front if `GET /api/v1/codex` isn't ready.
2. **`'stopping'`:** `POST /api/v1/project-locks/{lid}/transfer {to:"codex_session"}` with the lease, then the existing Stop (`busy.stop()`, `stopWaiting()`).
3. **`'confirming'`:** wait until the `AgentRunner` run has returned (its `finally` ran, so its in-process command finished; timeout 30 s → the switch fails). **The `finally` does not release the lock, because `transfer` is set** (N2).
4. **`'settling'`:** `drainInterjections()` → `latestFeedback`; a showing step question → `unresolvedQuestions`; a tool call in flight → `uncertainOperations`. Then git reconciliation, `headCommit`, save the checkpoint.
5. **`'saved'`:** `previousModel`; override → `ravis/codex`.
6. **`'starting'`:** `AgentBranch.continueOn(branch, headCommit)`, then:
   - **if the task's earlier Codex session is still `idle`** (the normal case after a switch, AH4): `POST /api/v1/agent-sessions/{sid}/turns {kind:"catch_up", text, lock:{transfer_token}}` on that same session, with the catch-up text below;
   - **otherwise** `POST /api/v1/agent-sessions` with `lock.transfer_token` and either:
   - **`start.kind:"resume"`** with `thread_id` and the catch-up text, when all hold: D3 (kept); `codexSession.threadId` exists; RAVIS's account fingerprint equals `codexSession.accountFingerprint`; the runtime verdict is `tested` or `accepted`, with `strict_rules` proven. RAVIS calls `thread/unarchive` first if the thread was archived. The catch-up text: "While you were stopped, another engine worked on this project. Changes since commit <sawHeadCommit> (now <HEAD>): <diffStat>. Checks: <checks>. Not done and uncertain: <uncertainOperations> — check before repeating. Open questions: <unresolvedQuestions>. What the user said meanwhile: <latestFeedback>. Re-read any file before editing it.";
   - **otherwise `start.kind:"brief"`**, and "Codex is starting fresh from the saved checkpoint."

   RAVIS moves the lock to the new session and rewrites the checkout lock file with RAVIS as holder. This window then drops its own lock file.
7. **`'started'`** on `turn.started`: `transfer` cleared, feedback marked delivered.

**A failure at any step** before `'started'` → `transfer.step = 'failed'`.
- The lock stays with the source in `stopped`, and is **released only after every process of both engines is confirmed gone** and the settle is done.
- The chat offers "Try again" or "Stay with <source>".
- An expired `transfer_token` never releases anything.

### 6.3 One writer per project, enforced centrally by RAVIS (review B1, H2, H7, M8, M9, N1-N3, N5)

**Two layers.**

| Layer | What it is | Why |
|---|---|---|
| **RAVIS project lock** (authoritative while RAVIS is up) | The `project_lock` table and the API (§3.6). Codex sessions hold it from create; Clarvis-engine writing runs take it through the API | One place that lists holders for the menu and dashboard, arbitrates takeovers and transfers, and refuses nested roots |
| **The checkout lock file** (the floor) | `<git_dir>/clarvis-engine.lock` (the git folder Clarvis sends and RAVIS validates without running git, §3.5.1), or `<root>/.clarvis/engine.lock` without git, created atomically with `open(path,'wx',0o600)`, written **by the process that actually writes**: RAVIS for Codex sessions (its pid and start time), the window for Clarvis-engine runs | Keeps Clarvis's own engine safe when RAVIS is down, and lets RAVIS and Clarvis agree without a network call |

**Lock file contents:**

```json
{"version": 3, "ravis_lock_id": "pl_…", "taskId": "…", "engine": "codex", "state": "running",
 "holder": {"kind": "codex_session", "session_id": "as_…", "pid": 4411, "pid_start": "Sat Sep 13 05:10:02 2026",
            "window_id": null, "host": "ravis", "since": "…"},
 "heartbeatAt": "…", "waitingOnYou": true, "leftover": [], "takenOverFrom": null}
```

**Acquire, in this order:**
1. Create the file.
2. Obtain the RAVIS lock. On refusal (`PROJECT_LOCKED`, `NESTED_PROJECT_LOCKED`), delete the file and refuse.
3. Heartbeat both every 15 s.

**Release, in reverse order, and only after the holder's processes are confirmed gone and any settle is done:**
- **never while a switch holds the lock** (§6.2);
- **never by a holder that has lost it.**

**The shared lock rule** (N3, N5). Specified once and implemented twice against one case table:
- **Code:** `judgeLock(lock, probe, observerAwakeSeconds) → 'alive' | 'unresponsive' | 'gone'`, in `src/engine/lock/lockRule.ts` and `ravis/src/ravis/codex/lock_rule.py`.
- **Tests:** both are checked against `lock-rule-cases.json`, copied into each repository with a hash check.

The three verdicts:
- **`gone`:** the holder's pid isn't running, **or** `ps -o lstart= -p <pid>` differs from `pid_start`. **Only `gone` counts as dead.**
- **`unresponsive`:** pid and start time match, but the heartbeat is older than 90 s **and** the observer has been awake at least 90 s since the Mac last woke (`kern.waketime`, else `kern.boottime`). **A stale heartbeat after a sleep never makes a live holder dead.**
- **`alive`:** otherwise.

RAVIS uses the rule for `project_lock` rows (heartbeats it receives, pids it can probe on the same Mac) and for the file. Clarvis uses it for the file.

**The restart adoption rule** (review AB1; part of the rule, with cases in `lock-rule-cases.json`):
- **RAVIS records its own instance** (pid and process start time) in `ravis_instance` at every start.
- **On startup, RAVIS may rewrite a checkout lock file only if the file still names RAVIS's *previous* instance.**
- **If the file names anyone else, whatever the verdict,** RAVIS leaves it untouched: it marks its database lock `superseded`, keeps the Codex session `uncertain`, and answers `settle-claim`, `turns` and `resume` with **409 `LOCK_SUPERSEDED`**.
- **When the window releases,** or registers its lock with `adopt_file_lock:true`, RAVIS replaces the superseded row. The Codex session can then be settled, by a window holding the lock, and continued.
- **Cases:**
  - previous instance → rewrite;
  - a window `alive`, `unresponsive` or `gone` → superseded, file untouched;
  - missing file and nothing else holding the root → atomic create;
  - a create race lost to a window → superseded.

**When a window wants to write and finds a lock:**

| Holder | Verdict | What happens |
|---|---|---|
| Codex session | any | **Not taken over: attached.** Any window of the workspace joins the session (§5.7) and can stop it. Switching is §6.2 |
| Clarvis-engine run | `gone` | Reconciled: RAVIS, or the window if RAVIS is down, kills the recorded leftovers, and `running_command`'s whole process group and descendants (below), and confirms. The new holder **continues the same task** with `continueOn` and commits the leftovers first on that branch (review AL3). The lock is replaced |
| Clarvis-engine run | `alive`, working | Refused: "Another Clarvis window (code-server, since 01:12, active 7 s ago) is working on this project. Stop it there first." |
| Clarvis-engine run | `alive` and `waitingOnYou`, or `unresponsive` | **Take over, with a confirmation naming the window and its age** (`POST …/takeover` with `confirm`). The old lease is revoked, and the holder's `running_command`, with its whole process group and descendants, is killed and confirmed gone before the new holder writes (review AM11, F-A4) |

**The fence** (N3). **A holder that lost its lock never commits, never writes the checkpoint, and never releases or rewrites a lock.**
- `stillHolds()` is checked before every mutating tool call and before any commit or checkpoint write.
- **Lost means evidence of loss, never silence** (review AH1). `stillHolds()` is false only when:
  - a heartbeat answered **`409 LEASE_REVOKED`** (a synchronous heartbeat is sent right before every commit), **or**
  - the checkout lock file, read before every mutating tool call and every commit, no longer names this window.
- **RAVIS unreachable is not loss.** A network error or timeout leaves the run going on the lock file alone: it keeps heartbeating the file, retries RAVIS every 15 s, and re-registers (`POST /api/v1/project-locks` with `adopt_file_lock:true`) when RAVIS answers.
- A `409 LEASE_REVOKED` ends the run: "Another window took over this task; I've left everything as it was."
- **A command still running in the taken-over window is recorded and killed** (review AM11).
  - Every heartbeat, and the lock file, carries `running_command`: the `{pid, pgid, start, comm}` of the sandboxed command `AgentRunner.runGated` has spawned, or null. Clarvis spawns it detached, in its own process group, so `pgid` equals `pid` (`clarvis/src/agent/tools/commandTools.ts:106-121`).
  - **Before the new holder writes anything, the takeover kills the command's whole process group and its descendants** (final check F-A4). Killing only the pid would leave children writing, such as `npm test` workers. RAVIS does this, or the taking window when RAVIS is down (Clarvis's `stopProcessGroup` plus the same checks):
    1. **Snapshot:** `ps -A -o pid=,ppid=,pgid=,lstart=,comm=`.
    2. **Is it still that group?**
       - Yes, if the leader (`pid` = `pgid`) is alive with the recorded start time.
       - Yes, if no process has `pid` = `pgid` any more. A group id isn't reused while members remain, so they belong to the original group.
       - No, if a process with that pid has a different start time. Then nothing is signalled by group, and any survivors are reported.
    3. **Targets:** every process in that group, plus every descendant (by `ppid`) of the leader or of a group member, including ones that moved to their own group or session. Each must have started at or after the recorded start time.
    4. **Signal:** SIGTERM to the group (`kill(-pgid)`) and to each other target → 2 s → SIGKILL the same set.
    5. **Confirm,** polling up to 3 s, that no target with a matching start time is alive. Otherwise the takeover stays `leftover` and names them, and the new holder writes nothing.
  - **This is not the Codex rule.** Codex's commands share the one app-server's group across projects, so Codex kills never use a group (§4.5). A Clarvis-engine command's group is its own.
  - **Residual gap:** a child that fully detached itself (reparented to `launchd`) before the snapshot isn't found, as in §4.5.
- **The new holder continues the same task** (review AL3). It inherits the task id and branch from the checkpoint, starts with `continueOn`, and commits the old holder's uncommitted edits as its first step on that branch, never as the user's own work in a new task.

**RAVIS down:**
- **No Codex session can exist:** its process died with RAVIS (§4.4).
- **Clarvis's own engine runs on the file floor alone**, with the shared rule. On a `gone` RAVIS holder whose session was running, the window kills the processes recorded in the file (RAVIS writes the Codex family into the lock file at each sample, as well as into `agent_process`), confirms, and reconciles.
- **When RAVIS returns,** a window holding a file lock registers it (`POST /api/v1/project-locks` with `adopt_file_lock:true`), and RAVIS adopts it. Any RAVIS lock for that root is `superseded` by the restart adoption rule above, so the uncertain Codex session waits until the window's run ends.
- **Never at the same time:** a Clarvis-engine run and a Codex session can't hold the same checkout, because both paths create the same file first.

**Across projects.**
- One lock per realpath root.
- An ancestor or descendant root that is already locked is refused (`NESTED_PROJECT_LOCKED`).
- Codex commands attributed to one session are never signalled for another (§4.5).

### 6.4 Pending approvals

- **Stop (from an editor, the menu bar or the dashboard), switch, cancel, crash:** every open request is resolved with its stop response by RAVIS **before** the turn is interrupted; late answers get 409. Each is recorded in `unresolvedQuestions`.
- **Never carried to the other engine**, never answered `accept` by anything but a token-holding Clarvis window.
- **Unanswered:** the §4.8 policy. Waiting shown at once when no window is attached; paused after 30 minutes detached or 2 hours attached; the thread is kept.
- **Uncertain commands** are listed and never re-run automatically.
- **Quota "Wait"** (review M15) ends the turn, keeps the session `idle` with its thread, and shows the reset time. An attached window reminds at the reset.

---

## 7. Menu bar and dashboard

### 7.1 `tools/run.py status --json` and the menu

**`status_report()`** (`tools/run.py:1631-1677`) gains `"codex"`: RAVIS's reading trimmed; `{"state":"ravis_not_answering"}`; or `null` on a 404.

```json
"codex": {
  "state": "signed_in", "reason": "Codex is signed in with a ChatGPT Plus plan.", "plan": "plus",
  "usage_known": true, "stale": false,
  "windows": [{"label": "5-hour window", "remaining_percent": 62, "resets_at": "…"},
              {"label": "weekly window", "remaining_percent": 80, "resets_at": "…"}],
  "runs": [{"id": "as_…", "turn_id": "…", "project": "add-utc-demo", "state": "waiting_on_you", "since": "…", "age_minutes": 42,
            "waiting_minutes": 12, "attached_windows": 0}],
  "sign_in_waiting": false, "address": "http://127.0.0.1:8790/#/ravis/Dashboard"
}
```

**`NERVISMenu.swift`.** `StackReport` gains an optional `codex` (with a `Run` list), drawn after the runtimes in the **Models** section.

**The row:**

| Situation | Dot | Row text |
|---|---|---|
| Any run `waiting_on_you` | orange | `Codex · 2 tasks · 1 waiting for your answer` |
| Runs, none waiting | blue | `Codex · 2 tasks running` |
| Any `paused_unanswered`, `paused_for_update` or `completed_needs_review` | orange | `Codex · 1 task needs you` |
| `signed_in`, no runs | green | `Codex · 62% left · resets 04:30` |
| `quota_exhausted` | orange | `allowance used up · resets 04:30` |
| `signed_out` / `sign_in_expired` / `account_changed` / `untested_version` | orange | The state sentence |
| `not_installed` / `not_available` / `runtime_down` / `checking` / `ravis_not_answering` | grey | The state sentence |

- **Never red.**
- **Detail lines:** one per task ("add-utc-demo — waiting for your answer · 12 min, no editor open"), then the allowance windows.
- **Submenu:**
  - **Open the Codex card**;
  - **Sign in to Codex…** / **Cancel sign-in** (`run.py codex …`);
  - **Re-test the file rules…** (§3.4), shown while Codex is paused for re-testing:
    - a confirmation: "Re-test Codex's file rules? Codex runs four fixed test commands in a throwaway folder, and RAVIS allows exactly those. It uses one short Codex turn from your plan's allowance, at most 5 minutes.";
    - then `run.py codex reprove`, and the result in plain words;
  - one submenu per task: **Open the Codex card** and **Stop this task…** (§7.4).
- **Stop is the only task control; there are no approve, answer or steer items.**

**Tests** in `nervis/tests/test_launcher_status.py`: `runs`, `ravis_not_answering`, `null`, sign-in without printing the URL, `codex stop` and `codex reprove` (the owner key, `source:"menu_bar"`, confirmation fields; the owner key never in NERVIS's environment), exit codes. Rebuild with `build_app.sh`, then swap with `kill -9` and `open`.

### 7.2 Dashboard: RAVIS → Dashboard card, and the Overview line

**Reader.** `API.ravis.codex()` via `live('ravis','/api/v1/codex', _codexMock, adaptCodex)`, with a live-shaped mock that uses real strings.

**Card: `codexCard`,** after the headline tiles, `class="card full${liveClass}"`, titled "Codex — ChatGPT plan allowance and tasks":
- **State chip,** reason, plan.
- **Running tasks table:** project, state chip (`running` · `waiting for your answer` · `paused — waited 30 min` · `paused — Codex updated` · `finished — needs review` · `uncertain` · `leftover`), started, waiting time, editors attached.
  - **Each running row has a Stop… button** (§7.4).
  - **Help text:** "Answer a Codex task in Clarvis: open this project in VS Code or the browser editor and Clarvis reconnects."
- **Allowance rows:** bar, `62% left · resets in 2 h 10 min (04:30)`. Unknown → no bar, no "%". "This is your ChatGPT plan's allowance, not money."
- **Runtime line:** `Codex 0.154.0 (Homebrew) · tested · file rules proven`.
  - While unproven: "file rules not yet re-tested. Re-test from the menu bar: NERVIS → Codex → Re-test the file rules…"
  - **No re-test button:** NERVIS never starts Codex work (§3.4).
- **Buttons,** through the control routes (§3.8): **Sign in** (a blank tab opened synchronously in the click, then its location set, with a link fallback; review M12), **Cancel sign-in**, **Sign out…** (disabled while tasks run), **This is my account / Sign out**, **Check this version** (the report in plain words, including whether the file rules stay proven) → **Use this version**.

**Stop is the only task control on the dashboard** (owner decision (a); §7.4).
- **No approve, answer, steer, start or settle controls:** by design (`CLARVIS.md` §6.7; runbook §2.2).
- **Stop** goes through NERVIS's control route to RAVIS's owner Stop route (§3.5.5), never through a session token.

**Overview line**, under the headline tiles: "Codex · 2 tasks · 1 waiting for your answer · 62% left in the 5-hour window". Wrapped in `cell(…,'ravis')`.

### 7.3 Gates

| Gate | Requirement |
|---|---|
| `check.py`, `render_check.js` (38 screens), `empty_world_check.js`, `outcome_check.js`, `sandbox_check.js`, `check_clean_clone.sh` | As before; the card draws its absent states |
| `injection_check.js` | 63 methods; project names, states and report strings escaped |
| `complexity_check.js` | ≤ 13 per function (`codexStateChip`, `codexRunRow`, `codexRunsTable`, `codexWindowRow`, `codexButtons`, `codexVersionPanel`, `codexCard`, `codexOverviewLine`) |
| `liveness_check.js` | CEILING 47, via a derived class |
| `shaping_check.js` | A `codex` entry in `RAVIS_READS`; `"Dashboard"` in live screens. **Message the peer session flagged for `nervis/tools` before editing** (review M11) |
| **new `codex_check.js`** | Asserts: (1) every state and each run state draws its words; (2) unknown usage draws no `%`; (3) no currency or Spend wording in the card; (4) 404, slow and unreachable give the absent card; (5) sign-in opens a tab synchronously before the POST resolves; (6) the version report renders; (7) **Stop is the only task control:** each running row renders **Stop…**; a stubbed `confirm` returning false sends nothing; true sends exactly one POST to `/api/v1/ravis/codex/runs/<id>/stop` with the control header, an `Idempotency-Key` and `{project, turn_id}`; a retried click reuses the key; a 409 `CONFIRMATION_MISMATCH` redraws "The task changed; refresh", and a 403 names the admin key; **no element posts to `agent-sessions`, `project-locks` or `/api/v1/ravis/codex/reprove`, and no button reads Approve, Allow, Run, Answer, Steer or Re-test** (a negative assertion over the rendered HTML and the stubbed fetch log); (8) the Overview line shows the run count and goes absent with RAVIS. Proven to fail with the card removed |
| `knowledge_check.py` | Name the new paths only once RAVIS declares them |

### 7.4 Stop from the menu bar and dashboard (owner decision (a))

**Menu bar.** Each running Codex task's submenu has **Stop this task…**.
- **Confirmation** (`NSAlert`): "Stop Codex's task in add-utc-demo? It started at 01:12 and is waiting for your answer. Codex's work so far stays in the project; open the project in an editor to review and save it." Buttons: **Stop task** / **Cancel**.
- **Stop task** → `launcher.capture(["codex","stop", id, "--project", project, "--turn", turn_id])`, using the values from the last `status --json`, then a refresh.
- Exit 8 shows "That task changed; the menu has been refreshed."

**Dashboard.** Each running row in the Codex card has **Stop…**, the same pattern as RAVIS Diagnostics' **Lift**:
1. `confirm("Stop Codex's task in add-utc-demo, started 01:12? …")`;
2. `POST /api/v1/ravis/codex/runs/{sid}/stop` with `control_headers()`, a fresh `Idempotency-Key` (the same one if the click is retried) and `{project, turn_id}`;
3. NERVIS checks its control token, validates the id and the key, and forwards with its RAVIS admin credential, `source:"dashboard"` and the same `Idempotency-Key` (§3.8), so a retry is replayed rather than repeated;
4. the row shows "stopping…", then "stopped — open the project to review" on the next read.

A 403 names the missing admin key; a 409 mismatch says "The task changed; refresh."

**What Stop does, and doesn't do** (§3.5.5). It stops and pauses the task: interrupt, that task's commands killed and confirmed gone, thread kept. It never approves, answers, starts, steers or saves.

---
## 8. Spec amendments for A

**Order and scale.** These land in Increment 0, before any code (runbook §3 step 1: the protocol change proposal is §2.2 below, together with the contract fixtures). They are larger than B's, because RAVIS now brokers agent sessions. Wording in quote blocks is proposed exactly.

### 8.1 `ECOSYSTEM_RUNBOOK.md`

**§2, RAVIS row.** Replace *Owns* (`:45`) with:
> Provider adaptation, eligibility and routing, virtual profiles, local-runtime coordination, route explanations, sessions, usage and cost; **brokering Codex agent sessions — hosting the optional Codex runtime with its sign-in, version pinning and allowance, relaying each task's events, approvals, questions, steer and stop to Clarvis, and the project write lock both coding engines use (§2.2)**

Replace *Does not own* with:
> Benchmarking hardware, **deciding approvals or answering questions for the owner, Clarvis's own tools, git branches and commits**, displaying the ecosystem

**§2, CLARVIS row.** Replace *Owns* (`:46`) with:
> Coding-agent behaviour inside one workspace: tools, approval gates, agent loop, conversation and workspace state; **the choice of coding engine; for Codex tasks, the approval and question interface, git branches and commits, the task checkpoint and switching between engines (§2.2)**

Append to *Does not own*:
> , hosting the Codex runtime

**§2, NERVIS row.** Append to *Does not own* (`:47`):
> ; starting, steering or answering any coding engine's work (it may ask RAVIS to stop a Codex task once the owner confirms, §2.2)

**New §2.2**, after §2.1:

> ### 2.2 Codex agent sessions, brokered by RAVIS
>
> **Decided by the owner, 13 September 2026.** Clarvis may run a coding task on OpenAI's Codex (`codex app-server`, the Homebrew stable build) on the owner's ChatGPT plan. **RAVIS runs the Codex process and keeps the task.** Clarvis windows start it, answer it, steer it, stop it, and reattach to it after closing. It is optional; every product works without it.
>
> **Who owns what.**
> - **RAVIS** owns one long-lived Codex process: its home (outside RAVIS's configuration folder), sign-in, version pin and allowance reading. It owns the agent-session records and the relay (SSE events and JSON actions), the per-task command clean-up, the unanswered-request policy, and the project write lock for both engines.
> - **Clarvis** owns the engine choice, the approval and question interface, git (branches, commits, reconciliation), the task checkpoint, switching, and its own engine.
> - **NERVIS** shows Codex's state, allowance and running tasks, and starts or cancels a sign-in, confirms the account or accepts a version through RAVIS. **It never starts Codex work (a task or the file-rules re-test), and never steers or answers a task.** After the owner confirms, it may stop one through RAVIS's owner Stop route, as the menu bar may through the launcher.
>
> **Identifiers.**
> - Catalogue id `ravis/codex`, listed only with `X-Clarvis-Engines: codex`, never a pool member or fallback; `/v1` refuses it with 400 `agent_backend_not_a_chat_model`.
> - `GET /api/v1/codex` (any caller).
> - Admin, for UX and audit: `/api/v1/codex/sign-in`, `/sign-out`, `/account/confirm`, `/version-check`, `/accept-version`. `/api/v1/codex/reprove` accepts only the owner's command-line credential (`admin.owner_cli`), which NERVIS never holds.
> - Clarvis client plus session token: `/api/v1/agent-sessions…` (§3.5 of the Codex design), **except** `POST /api/v1/agent-sessions/{sid}/owner-stop`, which takes only the owner's command-line credential or NERVIS's admin credential, no token, and a confirmation of the task's folder name and current turn.
> - Clarvis client plus lease: `/api/v1/project-locks…`.
> - Capabilities `ravis.codex_runtime@1` and `ravis.agent_sessions@1`.
> - Shared fixtures: `relay-contract/*.json` and `lock-rule-cases.json`, owned by RAVIS and copied into Clarvis with a hash check.
>
> **Invariants.**
> 1. **One writer per project.** A central RAVIS lock, plus a lock file in the checkout. It is released only after the engine's processes are confirmed gone and its work is settled. A switch holds it until the destination starts. A Codex session starts a turn only while it holds the lock. A holder that lost the lock never commits, saves the checkpoint or releases.
> 2. **Approvals and questions are answered only by a Clarvis window holding the task's session token** and an allowed client credential. NERVIS and administrative credentials are refused on every session route except the stop-only owner route. RAVIS never approves a task's request, and its unanswered-request policy may only decline and pause. **The one exception is the file-rules re-test,** which the owner starts from the menu bar: RAVIS's harness allows only that test's four listed commands, in its own throwaway folders, never in a task.
> 3. **Stop resolves open requests before interrupting;** an answer after Stop never starts a step.
> 4. **Feedback typed by the owner reaches the engine,** or the task checkpoint when no engine can take it.
> 5. **The allowance is not money.** No automatic move to a paid engine.
> 6. **Any Codex binary not recorded as tested or accepted pauses new tasks.** Strict file rules must be proven for the running version before any task starts.
> 7. **A change of ChatGPT account pauses new tasks** until confirmed.
> 8. **RAVIS's records, logs and events stay metadata-only,** except: content held in memory while relayed (bounded, never persisted); Codex's own history in RAVIS's Codex folder (owner decision); and the task's workspace path.
>
> **Out of scope.** Automatic switching when the quota runs out; Codex for chat, planning or the interview; remote hosts; other applications using agent sessions; any dashboard or menu control of tasks other than Stop; Codex on the ecosystem's own repositories or on the coding folder itself.
>
> **Lands with** RAVIS M29, Clarvis E-C9 and NERVIS M28, after a calibration step against the real runtime.
>
> **Exit.** On this Mac, a Codex task started in code-server:
> - shows progress, an approval and a steer;
> - keeps running when the browser tab is closed, and is reattached from desktop VS Code, where a waiting approval is answered;
> - stops with its processes confirmed gone before anything is saved;
> - moves to `ravis/clarvis-agent` and back on the same branch, with typed feedback, and no two engines ever write at once.
>
> The menu bar and the dashboard show the task and the allowance, and each stops a task after a confirmation; the attached editor then says where the stop came from. NERVIS's credentials are refused on every other session route.

### 8.2 `CLARVIS.md`

**§2**, append:
> **13 September 2026 — Codex tasks.** Clarvis recognises `ravis/codex` (confirmed by RAVIS's `/api/v1/codex`) and runs it through RAVIS's agent-session relay, not through chat completions. No `ProviderId` is added. Runbook §2.2 records the decision.

**§3 invariants:**
- Row `:77`, append: "**Exception (runbook §2.2):** a Codex task runs in RAVIS and can outlive the window; Clarvis's own engine still lives and dies with its extension host."
- Row `:80`, append: "These are the boundaries of Clarvis's own agent loop. A Codex task's commands and edits are Codex's own. They run inside Codex's safety box: writes only in the project and its task temp folder, internet only for a command the owner approves, and **reads refused for the named key and password files by a permission profile that calibration proved on <date>**. Clarvis sees what Codex asks, not what it runs without asking."
- Row `:81`, append: "For Codex tasks the deny-list adds warnings to relayed command approvals. Writes outside the workspace can only be declined (RAVIS enforces it). The sensitive-file gate's equivalent is the permission profile."
- Row `:91`, append: "`ravis/codex` is not probed; RAVIS's `/api/v1/codex` is its probe."
- New row: "| **Writing runs in one checkout are exclusive across windows and hosts**, whichever engine, through RAVIS's project lock with a lock file in the checkout as the floor (since E-C9). Read-only answers are not locked. | `src/engine/lock/` |"

**§4 non-goals**, replace the second bullet (`:209`) with:
> - No always-on Clarvis daemon, and no lifecycle independent of the editor window — **except Codex tasks, which by the owner's decision of 13 September 2026 run in RAVIS, may outlive the window that started them, and are reattached by any Clarvis window of that workspace.** Clarvis's own engine keeps the window's lifecycle.

**§5**, replace `:236-242` with:
> **Clarvis continues to own:** deciding whether a request is an answer or an action; tool execution and workspace validation for its own engine; approval gating, checkpoints, Stop behaviour and the agent loop; **the choice of coding engine; for Codex tasks, the approval and question interface, steering and Stop requests, git branches and commits, the checkpoint and switching**; conversation and workspace state; and user-facing narration.
>
> **RAVIS owns** route selection and provider credentials, **and for Codex: hosting the Codex process, agent sessions, the relay and the project lock (runbook §2.2)**. **RAVIS never receives permission to execute a Clarvis tool** — it may only return model output and tool-call requests for Clarvis to validate and run. **A Codex task's tools are Codex's own, run under Codex's sandbox and permission profile. RAVIS relays each of Codex's approval requests to Clarvis and never grants one (its file-rules re-test on its own throwaway folders is not a task; runbook §2.2). It may only decline and pause under the stated unanswered-request policy. Only a Clarvis window holding the task's session token may answer.**

**§5.2**, append:
> `ravis/codex` is the one engine Clarvis recognises by id, confirmed by RAVIS's `/api/v1/codex`. A 404 there refuses the run.

**New §5.5 "Codex tasks."** Selection, readiness, the relay client, reattach from either host, approvals (one at a time, RAVIS's allowed decisions), Stop over the relay, steering and feedback, bookkeeping and settle, the checkpoint file in the git folder, branch continuation, the lock and fence, the safety box facts and the calibration record, and what is out of scope.

**§6.7**, append:
> NERVIS may show that a Codex task is running or waiting for an answer: project name, state and age only. It may not answer, steer or start one, and RAVIS refuses NERVIS's credentials on those routes. After the owner confirms, it may stop one through RAVIS's stop-only owner route. Stopping controls nothing else, and the attached Clarvis window says where the stop came from. Starting or cancelling a sign-in, confirming the account and accepting a Codex version through RAVIS are runtime setup, not control of a task. NERVIS never starts the file-rules re-test; the owner starts it from the menu bar.

**§8**, new milestone after E-C8:
> ### E-C9 — Codex tasks through RAVIS *(paired with RAVIS M29 and NERVIS M28)*
> Choose `ravis/codex` as the coding model and a build runs as a Codex task in RAVIS: progress, approvals and questions in the chat, steering, Stop and the milestone machinery unchanged. The task survives a closed window and is reattached from either host. Manual switching to Clarvis's own engine and back continues on the same branch through a checkpoint in the git folder, under RAVIS's project lock.
> **Exit** (against `FakeRavisRelay` from the shared fixtures, then live):
> - **Stop:** clears the question at once and never lets a late answer start a step; nothing is saved before processes are confirmed gone.
> - **Questions:** overlapping requests are asked one at a time.
> - **Feedback:** text typed during a switch or while detached reaches the next turn.
> - **Reattach:** from desktop VS Code, replays and answers a waiting approval.
> - **Settle:** one window claims it.
> - **Takeover:** a taken-over Clarvis-engine run writes nothing.
> - **Refusals:** an untested version, unproven file rules, a changed account or an exhausted allowance refuses to start, with the reason, and never moves to a paid engine.

**§8.1**, new row:
> | **Unscheduled — Codex tasks through RAVIS (runbook §2.2)** | E-C9, paired with RAVIS M29 and NERVIS M28. Depended on by nothing; lands once `plan.md` is signed off and the §2.2 amendments are in |

### 8.3 `RAVIS.md`

**§1**, replace `:24-27` with:
> RAVIS owns provider adaptation, eligibility, routing, virtual profiles, local-runtime coordination, route explanations, sessions, and usage and cost accounting, **and brokers Codex agent sessions: it hosts the optional Codex runtime and relays each task between Codex and Clarvis under the project write lock (runbook §2.2)**. It does **not** benchmark hardware (SIRVIS), decide approvals, run Clarvis's tools or manage git (Clarvis), or supervise and display the ecosystem (NERVIS). **Codex edits workspaces as the agent RAVIS hosts; RAVIS's own logic never does.**

**§2**, append after `:53`:
> Codex tasks are the one place where RAVIS is not "just a very good OpenAI-compatible server". They are brokered agent sessions on a separate relay (§15.1, runbook §2.2), and `/v1` refuses `ravis/codex`.

**§4.1** rows:
> | `ravis.codex_runtime@1` | `/api/v1/codex` and its routes pass their tests. Never a readiness check |
> | `ravis.agent_sessions@1` | The relay and project-lock routes pass their contract tests against the shared fixtures, including NERVIS and admin refusals |

**§4.3 / §5.0.1**:
> `ravis/codex` is listed as `{id, object, owned_by}` only, after the pools, in both builders, while Codex is enabled and the request carries `X-Clarvis-Engines: codex`.

**§6**, append:
> A Codex agent session is neither Path A nor Path B. It isn't a completion; it is relayed on `/api/v1/agent-sessions` (§15.1).

**§9.7**, append:
> Agent sessions store the workspace's real path, because the hosted Codex process must run there. It is never placed in route explanations or events. `GET /api/v1/codex` lists running tasks by folder name only, never a path, for the menu bar and dashboard on this machine (review AL1).

**§10**, after `:988-989`:
> An agent backend (`ravis/codex`) is never a candidate, a pool member or a fallback target.

**§11**, append:
> **Agent-session streams** (since M29) are per-session SSE, with integer event ids, `Last-Event-ID` resume and bounded in-memory replay buffers (the last 2,000 deltas or 8 MB, and up to 20,000 other events or 32 MB for the session's life). Past the kept range → `409 EVENT_CURSOR_EXPIRED`, and the client reads the snapshot. Closing a stream never stops the task. The `/ecosystem/events` stream is unchanged.

**§12.1**, append:
> Agent sessions are separate records (migration 8), never `routing_session`. Restart, expiry and concurrency tests preserve session isolation, token binding, the one-writer lock and process clean-up.

**§14**, after `:1205-1206`:
> **The exception for Codex tasks** (owner decision, runbook §2.2 invariant 8). RAVIS's database, logs, audit and events stay metadata-only. Content passes through RAVIS's memory while it is relayed to the session-token holder (bounded, never persisted). Codex writes its own conversation history into RAVIS's Codex folder (`~/.local/share/ravis-codex`), kept up to 90 days without use and denied to Codex's commands. Codex fetches OpenAI's plugin catalogue at start unless `features.plugins=false` stops it (recorded in STATUS).

**§15.1**, replace `:1327` with:
> **Runtime control has no RAVIS endpoint until an ownership contract adds one.** Runbook §2.2 adds them, **for Codex only**: the Codex runtime routes (`/api/v1/codex…`, admin for changes, for UX and audit); the agent-session relay (`/api/v1/agent-sessions…`, Clarvis client credential plus session token, NERVIS and admin refused, except the stop-only `owner-stop`, which takes only the launcher's admin credential and a confirmation); and the project lock (`/api/v1/project-locks…`, Clarvis client plus lease). The shapes are in the Codex design §3.5-3.6, and the fixtures are shared with Clarvis.

**§15.2** events: `ravis.codex.state_changed`, `ravis.codex.sign_in_started`, `ravis.agent_session.started`, `ravis.agent_session.state_changed`, `ravis.agent_session.request_opened`, `ravis.agent_session.request_resolved`, `ravis.project_lock.changed`. **Metadata only.**

**§16**:
> **Without Codex** — `ravis/codex` is not listed, session creation is refused with the state's reason, the lock API still serves Clarvis's own engine, and nothing else changes.

**§17**: migration 8 tables `agent_session`, `agent_request`, `agent_turn`, `agent_process`, `project_lock`, `agent_idempotency`, with the retention in the Codex design §4.10.

**§18**: modules `src/ravis/codex/` (runtime, supervisor, state, lock rule) and `src/ravis/agent/` (store, relay routes, policy, attribution).

**§20** row:
> | **M29** | **Codex agent sessions, brokered.** One supervised Codex process with a pinned, calibrated version and a proven permission profile; sign-in and allowance; agent-session records; the SSE relay with tokens, idempotency and NERVIS or admin refusals; per-task command clean-up; the unanswered-request policy; the project lock for both engines; `/v1` refusal and conditional listing | Contract tests against the shared fixtures; a crash or restart marks running turns uncertain and kills only recorded processes; answers after Stop are refused; NERVIS and admin credentials get 403 on every session route except `owner-stop`, even with a token; `owner-stop` stops only, needs a matching confirmation, and refuses client credentials and tokens; a stale lock after sleep isn't treated as dead; untested versions and unproven rules refuse new tasks; conformance counts 24 checks; calibration and one live task recorded in STATUS |

**§20.1** row:
> | **Unscheduled — Codex tasks through RAVIS (runbook §2.2)** | M29, paired with Clarvis E-C9 and NERVIS M28. May land at any time once the contract is accepted |

### 8.4 `NERVIS.md`

**§8**, after **Spending**:
> **Codex** (M28): the RAVIS Dashboard carries a Codex card and the Overview a one-line summary, from RAVIS's `/api/v1/codex` through the relay. They show the ChatGPT plan's allowance per window and its reset (never as money), and which Codex tasks are running or waiting for an answer (project name, state and age). **Stop is their only task control:** after a confirmation, a control route forwards to RAVIS's stop-only owner route with NERVIS's RAVIS admin credential. Answering and steering happen in Clarvis, and RAVIS refuses NERVIS's credentials on those routes. Sign in, cancel, sign out, confirming the account and accepting a version are control routes presenting NERVIS's RAVIS admin credential. The file-rules re-test is not: it starts only from the menu bar.

**§6**: the Overview line.
**§16.2**: RAVIS absent → the card and line go absent.

**§21** row:
> | **M28** | **Codex's allowance and tasks, observed** | Card and Overview line draw every state and task state; Stop, with confirmation, is the only task control (a gate asserts it); popup-safe sign-in; the version report; the menu bar's Models line shows tasks waiting for an answer |

**§21.1**: the unscheduled row.

### 8.5 `STATUS.md`

**Next table**, row 17:
> | 17 | **Codex tasks through RAVIS — RAVIS M29, Clarvis E-C9, NERVIS M28** | Designed 13 September 2026; architecture and the four other questions decided by the owner that morning (`codex-design.md`). Waiting on the §2.2 amendments and Clarvis `plan.md` sign-off. Nothing built |

**Decisions**: record the five decisions as made, with the date, and remove them from the open list.

### 8.6 Clarvis `plan.md`: what goes in for sign-off

**New milestone M15, "Codex tasks through RAVIS"** (Plan Mode; `clarvis/AGENTS.md:18-39`):
1. **Goal and scope** from §1; the exclusions.
2. **§4.6 amendments:**
   - Model access: `ravis/codex` and its model choice.
   - Tools: Codex's own, and what Clarvis sees.
   - Gates: relayed approvals, one at a time, RAVIS's allowed decisions, provisional until calibration.
   - Undo: the branch, plus capture before accepted edits while attached.
   - Branch isolation: continuation on a switch; commits at settle.
   - Privacy: code and prompts go to OpenAI; the denied files; content passing through RAVIS; Codex's history kept in RAVIS's folder.
   - Cost: allowance, not money.
3. **§4.x new: tasks outliving the window.** Reattach from either host, the token file, the credential file on desktop, and the unanswered-request policy.
4. **Steps with `- Check:` lines** for C1, C2a, C2b, C3.
5. **The checkpoint file, the lock and the fence,** and the behaviour change for Clarvis's own writing runs.
6. **Acceptance:** the E-C9 exit.
7. **Risks:** §13.
8. **Docs in the same pass:** `docs/CURRENT_STATE.md`, `media/MANUAL.md`, `README.md`, `docs/risks.md`, `docs/verification.md`, and the Bridge hint wording.

---
## 9. Failure states under A

**How failures are detected.** Codex reports failures coarsely (brief §8), so each row names its signal and what it must not be confused with.
- **RAVIS's classifier:** `ravis/src/ravis/agent/failures.py`, which sets `turn.completed.error.kind`.
- **Clarvis's wording:** `translate.ts`.

| State | Detected by | What the owner sees | Never happens |
|---|---|---|---|
| **RAVIS down before a task starts** | `GET /api/v1/codex` fails or takes more than 1.5 s | "RAVIS isn't answering, so Codex can't start. Clarvis's own engine still works." | Clarvis's own engine is unaffected (lock file floor, §6.3) |
| **RAVIS down or restarted mid-turn** | Clarvis: the stream drops and reconnects fail. RAVIS on start: sessions in running states → `uncertain` (§4.4) | While down: "RAVIS stopped answering; Codex's step may have been cut off. I'll reconnect when it's back." After restart, reattach: "RAVIS restarted while Codex was working. The last step may not have finished; its changes are in the project. Review and save, then continue or switch." | No automatic continue; nothing committed or unlocked before recorded processes are confirmed gone |
| **Codex process crash or hang** (RAVIS up) | Child exit or EOF; health failures (§4.4) | Every running task, in every project: "Codex's process stopped unexpectedly …" (as above). Menu and card: "Codex process restarting" | Other projects' commands aren't signalled (per-session attribution); no retry of a turn |
| **Event stream disconnect** | `sseReader` sees the socket close or heartbeats stop (45 s) | Nothing, if reconnect (2, 5, 10, 30 s backoff) with `Last-Event-ID` succeeds; a snapshot on `409 EVENT_CURSOR_EXPIRED` | The task isn't affected; answers still POST if RAVIS is reachable |
| **Window closed mid-task** | The stream closes; no window attached after 60 s | Menu and card: "add-utc-demo — running, no editor open" or "waiting for your answer · N min" | The task isn't stopped |
| **Approval unanswered** | §4.8 timers | Paused after 30 min detached or 2 h attached: "Codex waited for your answer to run `npm install`, then paused. Nothing ran." | Never answered `accept`; the thread is kept |
| **Leftover processes** | End-sequence confirmation fails (§4.5) | "Something Codex started is still running: `node` (pid 51310, started 01:12)." **Stop them** / **Keep waiting**. Menu: "a process Codex started is still running" | No commit, checkpoint write or unlock while it lives (N1) |
| **Quota exhausted mid-turn** | Turn `failed` with `usageLimitExceeded`; RAVIS reads rate limits at once (same process) and sets `quota_exhausted` by §3.3's rules | "Codex has used this ChatGPT plan's allowance until 04:30. Its work so far is in the project; review and save." Then **Wait** (session `idle`, reminder at the reset) or **Switch…** (with the cost confirmation) | No automatic paid engine; not inferred from a bare 429; new sessions refused until the reset |
| **Throttled** | `willRetry:true` with `rateLimitExceeded`; final `{"responseTooManyFailedAttempts":{"httpStatusCode":429}}` | Terminal while retrying; then "OpenAI kept refusing Codex for now (too many requests)." | Never shown as quota |
| **Expired sign-in mid-turn** | `modelProvider/authRecovery*` failing, then `unauthorized`; `account/read` → null → `sign_in_expired` | "OpenAI signed Codex out. Its work so far is in the project." A **Sign in** link to the dashboard. New turns refused until signed in | Not blamed on RAVIS keys; no loop |
| **Account changed** | Fingerprint mismatch (§3.4) | "Codex is signed in to a different account than the one you confirmed." New sessions and turns refused; a resume across accounts refused | No task on an unconfirmed account |
| **Model unavailable** | Pre-flight against `model/list`; mid-turn `other` shown as Codex's words; `model.rerouted` shown | "Codex doesn't offer gpt-x on this account any more." | Not guessed from `other` |
| **Not installed** | The Homebrew link doesn't resolve | Menu and card grey: "not installed" | Listing and discovery never block |
| **Untested version, or binary changed while running** | Installed sha256 not tested or accepted; installed ≠ running | New tasks and turns: "Codex changed (now 0.155.0) and needs re-testing before new work." Running turns finish; the child is replaced when idle (§4.4) | An unrecorded version never starts a turn |
| **Strict file rules unproven** | `strict_rules != "proven"` | "The rules that keep Codex away from your key files aren't proven for this Codex version, so Codex is paused. Re-test from the menu bar: NERVIS → Codex → Re-test the file rules…" Before calibration: "not yet calibrated" | Never silently run with standard rules |
| **Protocol drift** | `-32600 Invalid request: unknown variant` or `invalid type` | "Codex's protocol changed mid-task." Turn `failed`, checkpoint `uncertain` | No guessing |
| **Project locked** | 409 `PROJECT_LOCKED` or `NESTED_PROJECT_LOCKED`, including a turn or steer on a Codex session that doesn't hold the lock (§3.5.3) | Codex holder: "Codex is already working on this project; reconnecting to it." Clarvis-engine holder: refused, or **Take over** with confirmation (§6.3) | No second writer; no silent takeover |
| **RAVIS restarts while a window holds the checkout lock** (review AB1) | Startup reconciliation finds a lock file that doesn't name RAVIS's previous instance | Card and menu: "add-utc-demo — Codex task paused: another editor is working on this project." A reattaching window sees "Codex's task is paused until Clarvis's own run here finishes." If that editor is gone, the next window to open the project clears its lock and offers review (§5.7) | RAVIS never rewrites that file; no settle, turn or resume (409 `LOCK_SUPERSEDED`) until the window releases and RAVIS adopts its lock |
| **RAVIS unreachable during a Clarvis-engine run** (review AH1) | Heartbeat network errors or timeouts | Nothing in the run, just a log line. The run continues on the lock file and re-registers when RAVIS returns | Never treated as a lost lease; the run isn't ended |
| **Stopped from the menu bar or dashboard** | The owner Stop route (§3.5.5) | Attached windows: "Stopped from the menu bar." or "Stopped from the dashboard." A later window: "This task was stopped from the dashboard at 01:40." Then review and save as after any Stop | Never an approval, answer or start; never another task's commands; nothing saved before processes are confirmed gone |
| **Taken over** (Clarvis-engine holder) | Heartbeat 409 `LEASE_REVOKED`, or `stillHolds()` false | "Another window took over this task; I've left everything as it was." | The taken-over window never commits, saves or releases |
| **Session token lost** | Token file missing while RAVIS lists a session | **Reconnect** (reissue once no window has been attached for 60 s) | No answer or stop from a window without a token; the menu bar and dashboard can still stop the task (§3.5.5) |
| **RAVIS restart during a sign-in** | The sign-in state isn't restored | Card: "Sign-in didn't complete (RAVIS restarted). Try again." | The previous account is never replaced |
| **Codex ↔ OpenAI connection loss** | `willRetry:true` with connection or stream failures; final failure without a 429 | "Codex couldn't reach OpenAI. Its work so far is in the project." | Not shown as a crash or quota |

**Never mixed streams.** One session is one engine and one thread. Switching is a new run after confirmation, on the same branch, with its own record.

---

## 10. Build plan

**Sizes** are estimates in lines: source / tests, fakes and fixtures / prose. "Sessions" means agent working sessions.

### 10.1 Sequencing rules (review H5, H6, M10, M11, N7)

**Tracks.** The owner commits straight to main, and the pre-commit hook snapshots the whole index. So **one agent per repository at a time**, with the two repositories as parallel tracks:

| Track | Order |
|---|---|
| **Ecosystem** (RAVIS, NERVIS, launcher, specs) | I0 → R1 → N1a → R2 → **Cal** → R3 → R4 → N1b → N2 |
| **Clarvis** | C1 → C2a → C2b (after Cal) → C3 |

**Contract first.**
- I0 writes the relay and lock contract fixtures (`ravis/tests/fixtures/relay-contract/*.json`, `lock-rule-cases.json`). They carry the three patches from the final check: the lock check on `turns` and `steer` (F-A1), the `Idempotency-Key` through NERVIS's Stop route (F-A2), and the re-test's credential and approval boundary (F-A3).
- The Clarvis agent copies them, with a hash check, and builds `FakeRavisRelay` from them. C1 and C2a can then proceed during R1-R3.
- End-to-end host tests and the live test wait for R4 and C3.

**Every commit green,** with one named exception:
- Each agent updates **its own** test-count line in `STATUS.md` and **its own** product's `RELEASES.md` section in the same commit.
- **Paired commits:** RAVIS 0.23.7 → 0.24.0, NERVIS's RAVIS window `maximum="0.24.999"` and `## RAVIS — 0.24.0` in one commit at the end of R4; NERVIS 0.29.0 with its section at the end of N2.
- **The exception:** Clarvis 0.17.0's `RELEASES.md` section is written **by the ecosystem-track agent** in the sitting of the Clarvis bump, and `check_releases.py` is the one gate expected red between those two commits.

**Calibration is its own step,** in the ecosystem track, after R2 and N1a and before R3's approval policy and C2b. Its STATUS record, tested entry and fixtures are committed by the ecosystem agent; the Clarvis agent copies the transcripts.

**Peers:**
- **nervis-ecosystem-fc:** before the first Clarvis edit (C1), and before package or install.
- **The session flagged for `nervis/tools`:** before N2.

**Coordination commands.** `git status` right before every bump, commit and package; explicit paths only.

### 10.2 Increment 0: amendments and contract fixtures

- **Files:** `ECOSYSTEM_RUNBOOK.md` §2 and §2.2, `CLARVIS.md`, `RAVIS.md`, `NERVIS.md`, `STATUS.md` (§8), Clarvis `plan.md` M15 (Plan Mode), and the fixtures (each endpoint's request and response, every error code, SSE frames, `lock-rule-cases.json`).
- **Gates:** `check_plans.py`, `check_status.py`.
- **Order:** `plan.md` sign-off first.
- **Size:** – / 360 / 1,300; 1.5 sessions.

### 10.3 Ecosystem track

**R1: runtime check, pin, catalogue, refusal.**
- **Source:** `config.py` (settings, `data_directory()`, doctor); `codex/{runtime,schema_report}.py` (Homebrew link resolution, signature, sha256, trees, definition hashes, `strict_rules_surface`, acceptance checks 1-5 and 7a); `codex/tested_runtimes.json`; `api/openai/agent_backends.py`; `chat.py`, `embeddings.py`; `transparent.py`, `registry.py` (header); `ecosystem/capabilities.py`; conformance (+1); `ravis codex preflight`.
- **Tests:** a stub executable with recorded trees; the link re-pointed → `untested_version`; all used methods resolve in the combined bundles; catalogue with and without the header and with no upstream; the 400 body and no route event.
- **Verification:** the plugins flag under `no-network.sb`.
- **Size:** 800 / 950 / –; 1 session.

**N1a: launcher.** `tools/run.py`: `brew --prefix` resolution; `RAVIS_CODEX_*` and `RAVIS_AGENT_*` environment; the `status --json` `codex` block with `runs` (ids, turn ids and start times); `codex sign-in`, `cancel-sign-in`, **`codex stop`** and **`codex reprove`**; the owner's command-line credential (§3.7). Tests in `test_launcher_status.py`, including stop and reprove: the owner key sent, `source:"menu_bar"`, confirmation fields, exit codes, key never printed, and **the owner key absent from NERVIS's environment**. **Size:** 290 / 350 / –; 0.5 session.

**R2: the Codex process, sign-in, allowance, acceptance.**
- **Source:**
  - `codex/{rpc,io,supervisor,state,usage,sign_in,account,acceptance,reprove,calibration}.py`: the single child; the I/O discipline (reader, writer and per-session queues, watchdogs; AM3); routing by `threadId`; a local health probe; crash and restart backoff; the binary swap when idle; the scratch-home handshake and check 7b; the re-proof route; the dev-only calibration route (AM2);
  - **`agent/{lock_rule,lockfile}.py` with the shared `lock-rule-cases.json`,** moved here from R4 because calibration needs them;
  - `api/management/codex.py`; `app.py` lifespan; audit and events.
- **Tests:**
  - `FakeCodexAppServer` (Python; account subset plus scripted thread and turn skeletons, from the probe transcript);
  - supervisor crash → every active turn `uncertain`;
  - binary change → no new turns, swap when idle;
  - usage from notifications, idle polling paused and resumed;
  - fingerprint, including a null email;
  - sign-in 409s including turn active and port busy;
  - acceptance checks 1-7;
  - **the re-test** (F-A3):
    - only `admin.owner_cli` may start it; NERVIS's `admin.launcher`, clients and anonymous → 403;
    - refused while a task is live;
    - the harness answers only for thread ids it created, and only the four listed commands, declining anything else;
    - `proven`, `failed` and `inconclusive` against `FakeCodexAppServer` scripts;
    - the step and time caps.
- **Size:** 2,190 / 2,420 / –; 3 sessions.

**Cal: the calibration step** (§10.4). **Size:** 400 / – / 150; 1 session with the owner.

**R3: agent-session store and relay.**
- **Source:**
  - migration 8 and stores;
  - `agent/{routes,identity,tokens,roots,idempotency,policy,unanswered,translate,redact,step_cap,events}.py`;
  - SSE with replay buffer and cursor expiry;
  - all §3.5 endpoints;
  - MEP events without content.
- **Tests:**
  - identity: anonymous, admin, `nervis` and `launcher` refused **even with a valid token**, and on GETs;
  - wrong token → 404;
  - roots refused;
  - idempotency replay and reuse;
  - `allowed_decisions` enforced (no `forRun` or session scope at all, outside writes only skip or stop, secret questions auto-empty);
  - answers after `stopping` → 409;
  - open requests resolved before `turn/interrupt`;
  - replay after reconnect and cursor expiry;
  - unanswered policy at 30 min detached and 2 h attached (fake clock), thread kept;
  - step cap while detached;
  - redaction;
  - settle claim exclusivity;
  - reissue-token rules;
  - migration backup and restore.
- **Also, from the architecture-A review,** each with tests, including two concurrent windows:
  - presence (AH2);
  - idempotency per window, and on settle, transfer, takeover and reissue (AM6, AM8);
  - the per-session action lock (AM7);
  - git-folder validation (AM9);
  - protected roots (AH5);
  - split replay buffers (AM12);
  - session reuse on a switch, and unarchive before resume (AH4);
  - elicitation decline (AL2);
  - no `forRun` (AH3);
  - **turns need the lock** (F-A1):
    - `turns`, and a steer that would start a turn, on a session left idle by a switch → 409 `PROJECT_LOCKED` while a Clarvis-engine run holds the root;
    - with a transfer token, or on a free root, the lock is taken atomically;
    - a queued steer whose session lost the lock starts nothing and emits `not_delivered`;
  - `paused_for_update` in the state table, `runs[].state` and the settle list (F-A5);
  - **the owner Stop route** (§3.5.5), with tests:
    - it accepts only `admin.owner_cli` and `admin.launcher`, each rate-limited separately, refusing `client.clarvis`, `client.nervis` and anonymous (403) and a session-token header (400);
    - a confirmation mismatch → 409; nothing running → 409;
    - open requests are resolved `owner_stop` before the interrupt, and later answers get 409;
    - only that task's processes are killed, and confirmed gone;
    - `stopped_by` arrives on the stream; audit; rate limit; idempotent replay;
    - **the route-table test:** every other agent-session and project-lock route carries `require_agent_client`, and this one doesn't; the routes that start Codex work (`reprove`, calibration) carry `require_owner_cli` (F-A3).
- **Size:** 2,980 / 3,580 / –; 4 sessions.

**R4: project lock, process clean-up, restart reconciliation, the RAVIS bump.**
- **Source:** `agent/{locks,attribution,cleanup,reconcile}.py` (the lock rule and lock file are in R2); §3.6 routes; the checkout lock file for Codex sessions; the restart adoption rule (AB1); the `running_command` group-and-descendants kill at takeover (AM11, F-A4); adopting a `gone` superseding holder's file lock (F-A9); strong-attribution-only kills (AM5); the update-pending pause (AL5); the paired 0.24.0 commit.
- **Tests:**
  - `lock_rule` against the shared cases, including sleep with an old heartbeat → `unresponsive`, not `gone`;
  - takeover only for `clarvis_run`, with confirmation; Codex holders → `ATTACH_INSTEAD`;
  - fence: `LEASE_REVOKED`;
  - transfer tokens and expiry that never releases;
  - nested roots;
  - attribution with two fake projects (sandbox-root, cwd and parent rules; ambiguous never killed);
  - Codex processes: kills by pid and start time, never by group;
  - confirmation;
  - restart reconciliation kills only recorded processes;
  - adopting a Clarvis file lock after RAVIS returns;
  - a superseded Codex session whose file holder is `gone`: a window's `adopt_file_lock:true` replaces the superseded row, and settle is then allowed (F-A9);
  - **restart with a window holding the file** (AB1): the database lock becomes `superseded`, the file is untouched, `settle-claim`, `turns` and `resume` get 409 `LOCK_SUPERSEDED` until the window releases, then RAVIS adopts; matching cases in `lock-rule-cases.json`;
  - a `cwd_only` process is never killed;
  - **a takeover kills `running_command`'s whole group and descendants** (F-A4):
    - a fixture spawns a detached shell with a background child and a grandchild in its own process group, and all are gone after the takeover;
    - a leader that already exited still has its group killed;
    - a pid reused by a different group is never signalled;
    - an unconfirmed kill leaves the takeover `leftover`.
- **Gates:** the full RAVIS set plus `check_clean_clone.sh` (warn the owner of its duration), `check_compatibility.py`, `check_releases.py`.
- **Size:** 1,490 / 1,720 / –; 2.5 sessions.

**N1b: menu bar** (tasks, waiting, states, **Stop this task…** with its `NSAlert` confirmation, §7.4, and **Re-test the file rules…** with its confirmation, §3.4). Checked by building and by live step 12a. **Size:** 310 / – / –; 0.5 session.

**N2: dashboard, control routes, gate, the NERVIS bump.**
- **Files:** §7.2-7.4; **seven control routes**, including the task Stop (`/api/v1/ravis/codex/runs/{sid}/stop`: control token checked, `sid` and `Idempotency-Key` validated, forwarded with the admin credential, `source:"dashboard"` and the key); the keyword-only `headers` argument on `ravis_peer.configure` (F-A2); `codex_check.js` with the Stop cases and the "no other control" assertion (§7.3, item 7); PITFALLS; knowledge files.
- **NERVIS pytest:**
  - the Stop route requires the control token, refuses a malformed id without forwarding, and passes RAVIS's status and body through;
  - **a fake RAVIS transport receives exactly the page's `Idempotency-Key`**; a missing or malformed key → 400 without forwarding (F-A2);
  - `headers` can't carry `authorization`;
  - no NERVIS route forwards to `/api/v1/codex/reprove` (F-A3).
- **Size:** 720 / 530 / 100; 1.5 sessions.

### 10.4 The calibration step (review H5, B3, H2; D2's gate)

**Needs:**
- R2, meaning the supervisor and sign-in, with the stack restarted on the R2 build;
- N1a, meaning the menu sign-in.

**Runs inside RAVIS's one Codex process** (review AM2).
- `ravis codex calibrate` calls an admin-only route, `POST /api/v1/codex/calibration/runs`, which exists only while `RAVIS_CODEX_CALIBRATION=1`. No second Codex process ever runs on the home.
- Like the re-test, the route accepts only the owner's command-line credential (`codex_reproof_applications`, §3.4). Calibration's approvals are answered by the same harness, from each scenario's fixed list, with the owner present.
- The scenarios hold the checkout lock files of **two throwaway git projects** the owner names, through R2's lock-file module and the shared lock rule.
- **Allowance:** it uses plan allowance, so the owner gives the go-ahead. **No terms gate** (D5).
- **Decoy secret files only:** named like the real ones, with a known marker, under a decoy folder on the deny list. Real secrets are never read.

| # | Question | Pass criterion, and consequence |
|---|---|---|
| K1 | File-change approvals under `untrusted` with the profile | Sent before each edit → keep. Otherwise try `on-request` with a read-only box, and record the working pair |
| K2 | An **approved** command stays in the box | Writes to `../outside.txt`, `/tmp/k2` and the process `$TMPDIR` fail; a write in `<root>/.clarvis/tmp/<sid>` succeeds |
| K2b | Per-thread temp folder | `config.shell_environment_policy.set.TMPDIR` on `thread/start` takes effect for commands; else record it and ask the owner |
| K3 | Network | Loopback and one outside host unreachable without approval; reachable only for the approved command |
| K4 | Escalation | Record whether Codex asks, what `additionalPermissions` shows, whether granular `sandbox_approval:false` prevents it, and where an approved escalated command ran |
| K5 | **The strict profile, two projects at once** | The `-c` syntax accepted; decoys unreadable to ordinary **and** approved or escalated commands in **both** projects; each thread's writes confined to its own root, **with and without explicit `runtimeWorkspaceRoots`** (AM4). **Failure → `strict_rules` stays `unproven`, no tasks, and D2 returns to the owner** |
| K5a | **Model-free decoy check** (re-proof, part one) | `command/exec` with `permissionProfile:"clarvis_run"` and each project's cwd reads the decoy key file and writes outside the root: both refused. No model and no allowance; reused by the re-proof of an accepted version |
| K5c | **"Deny" beats "write" inside the root** (review AH5) | A decoy `.run/decoy.token` inside project A, on the deny list: an ordinary, an approved and an escalated command in A all fail to read or overwrite it. Kept for the day the owner lists one of those repositories (§3.5.1); a failure keeps it refused |
| K6 | **Per-task clean-up in one shared process** | In project A: a foreground `sleep 600`, a backgrounded one, a PTY `script -q /dev/null sleep 600` and a server-like process; in project B, the same. Stop A → every A process gone, **every B process alive**. Record which attribution rule found each, and the sandbox-exec argument format |
| K7 | Interrupt | Timing; open requests resolved; `turn/completed interrupted` |
| K8 | Permissions denial | `{permissions:{}}` grants nothing |
| K9 | `.git` writability | Whether a command can commit inside the box |
| K10 | Plugins | `features.plugins=false` holds |
| K11 | `acceptForSession` scope | Record whether a "for the session" grant survives the turn, a settle, a steer or a new turn in the same process. Not offered in this delivery either way (AH3); K11 only informs a later change |
| K12 | Event order | item/started → requestApproval → serverRequest/resolved → item/completed, and interleaving across two threads |
| K13 | **Archive, unarchive, resume** (review AH4) | Archive a finished thread, `thread/unarchive` it, `thread/resume` it in RAVIS's process, and run a turn that sees the earlier history |

**Outputs:**
- the STATUS record;
- the `tested_runtimes.json` entry (the cask's sha256, experimental tree, `strict_rules_proven`);
- redacted transcripts under `ravis/tests/fixtures/codex/calibration/` (RAVIS's fake replays them; Clarvis copies the relay-level ones);
- the final mode and approval mapping for R3 and C2b.

### 10.5 Clarvis track

**C1: relay and lock clients, fakes.**
- **Source:** `src/engine/relay/*` (idempotent HTTP, `sseReader` with resume and 409 recovery, `tokenStore` with mode 0600, `credentialFile`); `src/engine/lock/*` (`lockClient`, `fileLock`, `lockRule.ts` with the shared cases); `src/test/fakes/FakeRavisRelay.ts`, built from the fixtures; the eslint override.
- **Tests:** SSE resume and cursor expiry; token file permissions; `lockRule` cases; file-lock atomic create; `FakeRavisRelay` responses validated against the fixtures.
- **Size:** 1,100 / 1,300 / –; 1.5 sessions.

**C2a: remote runner, reattach, Stop, steer, factory.**
- **Source:** `RemoteCodexRunner` and `runCore` (run, attach, translate, Stop and steer semantics, settle flow); `engineChoice`; `CodingRun`; the factory; `RunSession` (factory, attach, Clarvis-engine lock with the fence); `ChatService` (activation reattach, `runTook` routing); `extension.ts` and `Replier.ts` guards; pickers; the provider header; `AgentRunner` (`engine`, `drainInterjections`, `stillHolds`).
- **Tests:**
  - Stop clears locally before the network, and never sends a late accept;
  - steer race → queued;
  - feedback typed while stopping, switching or detached → `latestFeedback`, then delivered;
  - reattach from a second "host" with a stored cursor and with a snapshot;
  - two windows: first answer wins, one settle claim;
  - a completed-while-detached settle;
  - fence on `LEASE_REVOKED`;
  - host spec for the factory and the palette guard;
  - a panel whose pings stop and resume without activation: `panel_connected:false`, then `true`, and the stream reopened from the cursor (F-A8);
  - reattach with a `gone` window superseding RAVIS's lock: reconcile, adopt, settle (F-A9);
  - `409 PROJECT_LOCKED` from `turns` or `steer` → the chat line, and the text kept in `latestFeedback` (F-A1).
- **Also:** the panel ping and `presence` (AH2); the file-fallback fence (AH1); per-window answer keys (AM6); `running_command` with `pgid` in heartbeats, and the group kill when RAVIS is down (AM11, F-A4); `git_dir` at create (AM9); the "Stopped from the menu bar/dashboard" handling of `stopped_by` (§5.3).
- **Size:** 1,980 / 2,270 / –; 3 sessions.

**C2b: approvals** (after Cal). `approvals.ts`: FIFO, re-evaluation before POST, rendering from `allowed_decisions`, the Unattended-attached narrow auto-answer, fakes updated from the transcripts. **Size:** 480 / 580 / –; 1 session (no "don't ask again" labels, AH3).

**C3: checkpoint, switching, branch continuation.**
- **Source:** the checkpoint file in the git folder; `transfer` both ways through the lock API; `EngineSwitch`; `AgentBranch.continueOn` with `created` and `previousBranch`; `continuationDecision`; the Wait reminder.
- **Tests:**
  - `branchNames.test.ts` continuation cases;
  - **host spec `branchContinuation.spec.ts`** on a fixture repository: after a Codex commit on `clarvis/x`, Clarvis's engine continues on `clarvis/x` with the commit present and the base still `main`;
  - `transfer.test.ts`: the lock held through settle and start; leftover → cancel keeps the locks; a failure releases only after confirmation; resume versus fresh by fingerprint and verdict.
- **Also:** reuse of the idle Codex session on a switch back (AH4); a takeover continuing the same task (AL3).
- **Size:** 1,110 / 1,330 / –; 2 sessions.

### 10.6 Packaging, install, rollback, disable (review L7)

**Install** (after R4, N2, C3):
1. Restart the stack on RAVIS 0.24.0 (reinstall editable) and NERVIS 0.29.0.
2. Rebuild the menu app and swap it (`kill -9`, then `open`).
3. **Clarvis:**
   1. package `clarvis-0.16.0.vsix` from `90df7be` as the rollback copy;
   2. message nervis-ecosystem-fc (0.17.0 also delivers 0.16.0's unwalked planning changes);
   3. `git status`, then the bump plus RELEASES pairing;
   4. `npm run check`, `npm run test:host`, `npm run package`;
   5. `code --install-extension clarvis-0.17.0.vsix --force` and `~/.local/bin/code-server --install-extension clarvis-0.17.0.vsix --force`;
   6. byte-compare `dist/extension.js` in both hosts;
   7. reload.
4. **Desktop VS Code:** the owner sets `clarvis.ravis.credentialFile` once (live step 0).

**Disable** without uninstalling: `RAVIS_CODEX_ENABLED=false` and restart.
- Session creation is refused.
- Existing sessions can still be settled and ended.
- The lock API keeps serving Clarvis's own engine.

**Roll back:**
- **Clarvis:** reinstall the kept vsix in both hosts.
- **RAVIS and NERVIS:** `git revert` the M29 and M28 commits.
- **RAVIS's database:** migration 8 makes the database newer than the old build, which refuses it. Restore the `.v8.bak` taken before migrating with `ravis restore-database`. Agent-session rows are lost; nothing else is.

**Remove Codex's data** (the owner's own action):
1. Sign out.
2. Delete `~/.local/share/ravis-codex/`, `…/ravis-codex-scratch/`, `…/ravis-codex-reproof/`, `~/.local/share/clarvis/agent-sessions/` and `~/.config/ravis/codex-state.json`.
3. Per project, once no task holds it: `clarvis-task-checkpoint.json` and `clarvis-engine.lock` in `.git/`, and `.clarvis/tmp/`.

### 10.7 Totals

| Increment | Lines (src / tests, fakes, fixtures / prose) | Sessions |
|---|---|---|
| I0 amendments and contract fixtures | – / 400 / 1,340 | 1.5 |
| R1 runtime check, pin, catalogue, refusal | 800 / 950 / – | 1 |
| N1a launcher (with `codex stop`, `codex reprove`, the owner credential) | 290 / 350 / – | 0.5 |
| R2 Codex process, I/O discipline, lock rule, sign-in, allowance, acceptance, re-test | 2,190 / 2,420 / – | 3 |
| Cal calibration with the owner (inside RAVIS's process) | 350 / – / 150 | 1 |
| R3 session store and relay (with presence, action lock, idempotency, roots, buffers, owner Stop, turn lock check) | 3,030 / 3,670 / – | 4 |
| R4 project lock, clean-up, group kill at takeover, restart adoption, bump | 1,490 / 1,720 / – | 2.5 |
| N1b menu bar (with Stop and Re-test) | 310 / – / – | 0.5 |
| N2 dashboard, routes, Stop with its key, gate, bump | 720 / 530 / 100 | 1.5 |
| C1 relay and lock clients, fakes | 1,100 / 1,300 / – | 1.5 |
| C2a remote runner, reattach, presence, Stop, steer, factory | 1,980 / 2,270 / – | 3 |
| C2b approvals | 480 / 580 / – | 1 |
| C3 checkpoint, switching, branch continuation | 1,110 / 1,330 / – | 2 |
| P packaging, install, docs | – / – / 250 | 0.5 |
| L live test with the owner | – | 1.5 |
| **Total** | **≈ 13,850 / ≈ 15,520 / ≈ 1,840 — about 31,200** | **≈ 25** |

The totals include the owner Stop (decision (a)) and the final-check patches (§14.7). Allowing a protected repository later (§3.5.1) would add about 150 test lines.

**Wall clock.** The ecosystem track is the long pole (about 14 sessions after I0, including Cal). The Clarvis track (about 7.5 sessions) runs alongside it, against `FakeRavisRelay`, and waits only for Cal (before C2b) and for R3 and R4 (end-to-end).

---
## 11. Live test plan with the owner

**Setup:**
- **Where:** this Mac.
- **Versions:** RAVIS 0.24.0 and NERVIS 0.29.0 running; Clarvis 0.17.0 installed in both hosts and byte-verified; the menu app rebuilt.
- **Calibration done:** K5 proven, so the stricter file rules are on.
- **Projects:** two throwaway git projects in folders the owner names (ask before creating them; not in `~` without saying), added to `RAVIS_AGENT_ALLOWED_ROOTS`.
- **Desktop VS Code:** the owner points `clarvis.ravis.credentialFile` at the launcher's Clarvis key file once. The tester never opens that file.
- **No terms check** (D5).
- **Allowance:** **Allowance** marks steps that use the ChatGPT plan; "API spend" marks the step that uses paid providers.
- **The watcher** says what it's about to do before each step, and checks processes with `ps` where noted.

| # | Step | What should be seen | Allowance |
|---|---|---|---|
| 0 | Stack up; menu bar; dashboard | Menu: **Codex · signed out**. Card: "signed out", no tasks. Overview line present | none |
| 1 | Terminal: `POST /v1/chat/completions` with `ravis/codex`; `GET /v1/models` with and without `X-Clarvis-Engines: codex`; `GET http://127.0.0.1:8790/api/v1/relay/ravis/api/v1/agent-sessions?workspace_root=<A>` (through NERVIS) | 400 `agent_backend_not_a_chat_model`; the entry listed only with the header; **403 `AGENT_CLIENT_NOT_ALLOWED`** through NERVIS's relay | none |
| 2 | Menu → Codex → **Sign in to Codex…**; the owner signs in in the browser | The allowance appears on the menu and the card; version `0.154.0 (Homebrew) · tested · file rules proven` | none |
| 3 | Clarvis in code-server, project A: Models → Coding model → `ravis/codex` | The picker shows the state; the chat picker has no `ravis/codex` | none |
| 4 | **Agent** mode: "Build milestone 1: `hello.py` printing today's date, and `test_hello.py`; run the check." | A file-change approval listing both files → **Apply**; a command approval for `python3 -m pytest -q` → **Run it**; progress; menu "Codex · 1 task running" | **yes** |
| 5 | Start "Add a `--utc` flag with a test." While it works, type "Use datetime.timezone.utc, not pytz." | "Passed to Codex"; the code uses `datetime.timezone.utc` | **yes** |
| 6 | **When the next approval appears, close the code-server browser tab** | Menu within about a minute (the panel heartbeat stops; §3.5.4): "add-utc-demo — waiting for your answer · no editor open". The card shows the same. The task keeps its thread (it isn't paused before 30 minutes) | **yes** (the step in flight) |
| 7 | **Open project A in desktop VS Code** | "Codex is still working on milestone 2 … one question is waiting. Reconnected." The **same approval** appears with its real scope → answer it → Codex continues. The card shows 1 editor attached | **yes** |
| 8 | Reopen the code-server tab; then, at the next approval, **press Stop in desktop VS Code** | Both windows show the question; Stop clears it at once in both; RAVIS confirms the task's processes gone (watcher: `ps`); **one** window settles (one commit on the branch); "Stopped." | **yes** (part) |
| 9 | Desktop: **Switch engine** → `ravis/clarvis-agent` → confirm the cost; **while the switch runs, type** "Also update the README." | The lock moves to Clarvis's engine (card: `clarvis_engine`); the brief includes the checkpoint **and** the typed sentence; `git log` shows the work continuing on the **same** branch on top of Codex's commit; RAVIS Spend rises; the Codex allowance doesn't move | API spend |
| 10 | Stop it after one edit; **Switch engine** → Codex → confirm the allowance sentence | The same Codex session (idle since the switch, never archived) gets the lock back and **continues its thread**, with "what changed since commit X", finishes, runs the check; one settle | **yes** |
| 11 | **Two projects:** start a short Codex task in project B (code-server) while A runs a long command (desktop); **Stop A** | A's processes gone; **B's command processes still running** (watcher: `ps`); B completes | **yes** |
| 12 | **Unanswered policy,** with `RAVIS_AGENT_UNANSWERED_DETACHED_MINUTES=2` set for this step (RAVIS restarted to apply it, then restored): start a task, close every editor at the first approval | After 2 min: menu and card "paused — waited for your answer"; reopening shows "Codex waited … then paused. Nothing ran." → **Continue** | **yes** (small) |
| 12a | **Stop from the menu bar:** start a short Codex task in project A with the code-server panel open; menu → Codex → add-utc-demo → **Stop this task…** → confirm | The code-server chat shows "Stopped from the menu bar." at once, any question cleared. RAVIS confirms the task's processes are gone (watcher: `ps`), and the window settles as usual. Choosing the same menu entry again after the task ended: "That task changed; the menu has been refreshed." | **yes** (small) |
| 12b | **Stop from the dashboard:** start another short task, then close every editor; dashboard → RAVIS → Codex card → the task's **Stop…** → confirm | The row shows "stopping…", then "stopped — open the project to review". Reopening the project shows "This task was stopped from the dashboard at …" and offers review and save | **yes** (small) |
| 13 | *(Optional)* restart RAVIS through the launcher during a short turn | Menu "Codex process restarting"; the reopened window says the step may have been cut off; review and save; continue | **yes** (small) |
| 14 | **Proxied origin:** open code-server through NERVIS's `/code/` route on project A; reattach; answer one approval | Works, or record "proxied origin unsupported for this delivery" in STATUS | **yes** (small) |
| 15 | Menu bar and dashboard | Allowance lower than at step 2; the task list is right; **Stop is the only task control on the menu bar and dashboard; no approve, answer or steer control anywhere**; Spend reflects only step 9 | none |
| 16 | Cleanup | Ask before deleting the two projects; the owner decides on sign-out; restore any test settings; say where Codex's history for these tasks lives | none |

**Recorded afterwards** in STATUS under M29, E-C9 and M28: which steps ran live, the reattach and cross-host answer (7), the Stop confirmation (8), the two-project clean-up (11), the policy (12), the proxied result (14), and the allowance before and after.

---

## 12. Decisions the owner must make

**No owner decisions remain.** On 13 September 2026 the owner decided D1-D5, and then both follow-up questions:
- **(a) Yes:** a Stop button on the menu bar and dashboard (§3.5.5, §7.4).
- **(b) No:** Codex doesn't work on NERVIS-ecosystem, clarvis or the coding folder itself (§3.5.1). **D2 comes back to the owner only if calibration, or a later re-test, shows the stricter file rules don't hold** (§10.4, K5; §3.4).

**Settled defaults the owner may want to glance at** (each can be changed in a setting):
- **Waiting for an answer:** a question waits 30 minutes with no editor open, or 2 hours with one open. Then Codex pauses that step, keeps its conversation, and runs nothing.
- **At most three** Codex tasks at once on this Mac.
- **Where Codex may work:** only in project folders under `~/Documents/coding`, plus any folder you add. Never that folder itself, and never NERVIS-ecosystem or clarvis (your decision). One could be allowed later by listing it explicitly (§3.5.1).
- **No "don't ask again":** in modes that ask, every command or change Codex brings is asked about. Codex's "for the rest of the session" memory would outlive the step in RAVIS's long-lived process.
- **History:** Codex's conversation history is kept for 90 days after it was last used.
- **Re-testing after a Codex update** is started by you from the menu bar, never from the dashboard. It uses one short, capped Codex turn, and RAVIS allows only its fixed test commands, in a throwaway folder.
- **The menu bar and dashboard show tasks and offer one control, Stop,** after a confirmation. Answering and steering happen in Clarvis, from any editor that reconnects.
- **Desktop VS Code** needs Clarvis pointed at RAVIS's key file once, to see and answer Codex tasks.
- **Unattended mode** never approves anything by itself while no editor is open.
- **Clarvis's own writing runs** now also take the one-writer lock, so two editors can't build in one project at the same time.

---

## 13. Risks and unknowns under A

1. **R1. One Codex process serves every project.** A crash, a hang, or a RAVIS restart (including the stack restart after source changes) cuts off every running Codex task at once. Recovery is review, save and continue, never automatic.
2. **R2. Work continues with no editor open.**
   - Questions wait, and may pause work after the stated time.
   - In modes that don't ask, Codex keeps changing files while nobody watches.
   - Finished work stays uncommitted until an editor settles it.
   - The owner has to notice the menu bar.
3. **R3. Per-task clean-up inside a shared process depends on attribution.**
   - Background-terminal listing is experimental.
   - The sandbox arguments and working-folder rules are heuristics.
   - A command started and orphaned between samples can survive.
   - An ambiguous process, including one matched only by working folder, is never killed automatically, so leftovers will be reported more often than cleaned (AM5).
   - K6 measures it with two projects.
4. **R4. The relay is new, security-sensitive surface.**
   - Session tokens sit in a user file that any program running as the owner can read; the permission profile keeps Codex's commands away from it.
   - NERVIS's GET relay covers `/api/v1/`, so the explicit refusals must hold on every route, and a gate test pins them.
   - A bug could let the wrong window answer.
   - The owner Stop route accepts the launcher's admin keys, and the re-test accepts the owner's. Any program running as the owner can read those files, so such a program could stop tasks or spend a little allowance on a re-test. It could never write to a project, approve a task's request or start a task.
   - Relayed content passes through RAVIS's memory.
5. **R5. Unobserved Codex behaviour, experimental dependencies, and re-proofs.**
   - **Every Codex update needs a short re-proof** of the key-file rules, using a little allowance, before tasks run again, because accepted versions are always unproven (AM1). The owner starts it from the menu bar. If Codex doesn't follow its fixed commands, the result is inconclusive and the owner tries again.
   - Still unproven until calibration: "deny" beating "write" inside a root (K5c), resuming an archived thread (K13), and how long the approval cache lasts (K11).
   - Unproven until calibration: permission profiles resolving per thread in one process (K5), escalation semantics (K4), a per-thread temp folder (K2b), event interleaving across threads (K12).
   - A K5 failure stops Codex tasks and returns D2 to the owner.
   - Relying on experimental Codex features makes a version change more likely to pause work.
6. **R6. RAVIS becomes required for Codex coding.** Its uptime and restart habits now affect running tasks.
7. **R7. Load in one process.** Several concurrent turns share 128-message queues (`-32001` when full). The three-task limit is a guess.
8. **R8. Attachment is self-reported.** The Clarvis panel's heartbeat decides "attached". A program running as the owner that holds the Clarvis key and a session token could keep a task looking attached, delaying the 30-minute rule; the 2-hour attached limit still applies.
9. **R9. Sign-in.** Another program holding ports 1455 and 1457; device-code sign-in unprobed (so remote code-server is unsupported); a RAVIS restart during a sign-in loses it.
10. **R10. Usage reads.** OpenAI's tolerance for `/wham/usage` polling is unknown.
11. **R11. An old Clarvis with a hand-typed `ravis/codex`** still makes an empty branch before the 400.
12. **R12. Codex's commands use the real `HOME`,** so other Codex-related setup under `~/.agents` may be picked up. Not traced.
13. **R13. The plugin marketplace download** at each Codex process start, unless the flag holds (K10).
14. **R14. The proxied code-server origin** may not support the panel (step 14 decides).
15. **R15. Rollback of RAVIS needs a database restore** (migration 8), losing agent-session records.

---

## 14. Changelog

### 14.1 Rework for the owner's decisions (this pass)

- **Architecture:** A replaces B.
  - One supervised Codex process in RAVIS hosts every task.
  - A new agent-session store (migration 8) and an SSE plus JSON relay with per-session tokens, workspace-root binding, idempotency, and explicit NERVIS and admin refusals.
  - A central project lock with a lock file in the checkout as the floor.
  - Per-task command clean-up by attribution.
  - An unanswered-request policy; reattach from either host; settle, so git stays in Clarvis.
- **Decisions applied:**
  - **D2:** the strict profile, now the only mode, gated by K5.
  - **D3:** history kept, with retention, and the metadata-only exception written down.
  - **D4:** the Homebrew link through `brew --prefix`; the ChatGPT-app copy is dropped.
  - **D5:** no terms gate in the build or the live test.
- **Removed as no longer needed:** Clarvis spawning Codex; RAVIS's separate account process and its hand-over flag; the usage-report endpoint; Clarvis-side Codex executable and home settings; the Mac-wide run lock file.
- **§12:** no open decisions; defaults listed.
- **§10:** re-estimated at about 26,400 lines over about 22.5 sessions. The B design was about 17,000.
- **The B design** is condensed into the appendix.

### 14.2 How each review lesson carries over

| Id | Under A |
|---|---|
| B1 | **Kept.** End sequence with confirmed exit before settle, commit, checkpoint or unlock; the switch holds the lock until the destination starts (§4.5, §5.3, §6.2) |
| B2 | **Kept.** `AgentBranch.continueOn` with tests (§5.1, C3) |
| B3 | **Kept.** Sandbox facts, temp excludes, per-command internet, the permission profile, K2-K5 (§2.2, §4.9) |
| H1 | **Kept.** Home outside the config folder; secrets named; deny list now includes the session-token folder (§4.2, §4.9) |
| H2 | **Changed.** Per-session attribution inside one shared process; kills by pid and start time, never by group; K6 with two projects (§4.5) |
| H3 | **Resolved by A.** One process on the home, so no refresh race between processes |
| H4 | **Kept.** Admin routes are UX and audit; account fingerprint (§3.4) |
| H5 | **Kept.** Calibration as its own step before the approval work (§10.4) |
| H6 | **Kept.** Own STATUS and RELEASES lines; paired bumps; one named exception (§10.1) |
| H7 | **Changed.** A closed tab blocks nobody; reattach from either host; takeover only for Clarvis-engine holders; tasks and ages shown (§5.7, §6.3, §7) |
| H8 | **Kept.** Feedback while stopping, switching or detached goes into the checkpoint; the steer queue is now server-side (§5.4) |
| M1, M5 | **Kept.** Strict pin on sha256 plus both trees; combined bundles (§4.3) |
| M2, M3 | **Kept.** FIFO questions; decision re-evaluated right before the POST (§5.2) |
| M4 | **Kept.** Override per task (§5.1) |
| M6 | **Kept.** Credits never exhaust on their own (§3.3) |
| M7 | **Resolved by A.** No usage report; desktop reaches the relay through a credential file |
| M8, M9 | **Kept.** No lock for read-only answers; the shared lock rule (§6.3) |
| M10, M11 | **Kept.** One agent per repository; message the `nervis/tools` peer (§10.1) |
| M12 | **Kept.** Popup-safe sign-in (§7.2) |
| M13 | **Kept.** Listing only with the header (§3.1) |
| M14 | **Kept.** Live step 14 |
| M15 | **Kept.** Wait defined; tasks and waiting time on the menu and card |
| M16 | **Kept.** Installed versus running sha256; the process swapped when idle (§4.1, §4.4) |
| L1, L4, L5, L6, L8, L11 | **Kept** |
| L2 | **Updated.** Events and audit list for A (§3.9) |
| L3 | **No longer applies** (no usage report); failure kinds sit in `turn.completed.error` |
| L7 | **Updated.** Rollback now includes the database restore (§10.6) |
| L9 | **Kept.** The checkpoint file's "may be out of date" note |
| L10 | **Resolved.** Decisions made; the live plan is fixed |
| N1, N2 | **Kept.** No save-and-release beside leftovers; cancel keeps the locks; `finally` never releases during a switch (§5.3, §6.2) |
| N3 | **Kept.** The fence and the sleep rule (§6.3) |
| N4 | **Kept.** Check 7 and `strict_rules`; with D2 in force, unproven means no tasks (§4.3) |
| N5 | **Changed.** One shared rule used by RAVIS and Clarvis; the account-process hand-over race is gone with one process |
| N6 | **Kept.** `brew --prefix` link (§4.1) |
| N7 | **Kept.** Calibration writes and the Clarvis RELEASES entry by the ecosystem agent |
| N8 | **Changed.** RAVIS's fake comes from the probe and calibration transcripts; Clarvis's `FakeRavisRelay` comes from the contract fixtures |
| N9, N10, N11 | **Kept** |

### 14.3 Earlier passes

The first revision (38 findings) and the second (N1-N11) were applied to the B design on 13 September 2026. Their fixes are carried over as listed above; B-specific mechanisms are summarised in the appendix.

### 14.4 Residuals left on purpose

- **An old Clarvis's hand-typed id** still makes an empty branch (R11).
- **A process orphaned between two samples** can escape clean-up (R3; K6 measures it).
- **A RAVIS that is running but not answering** can't be reached to stop a task. Clarvis says so and keeps trying. If RAVIS is fully down, Codex has already stopped.
- **Fixed since:** a command running at a Clarvis-engine takeover is now recorded and killed (AM11).

---

### 14.5 Revision against the architecture-A review (24 findings)

| Id | What changed |
|---|---|
| **AB1** | Restart adoption rule: RAVIS rewrites a checkout lock file only if it names RAVIS's previous instance. Otherwise its lock is `superseded` and the Codex session waits (409 `LOCK_SUPERSEDED`) until the window releases. In the shared rule's cases, tested in R4 (§4.4, §6.3, §9) |
| **AH1** | Only `409 LEASE_REVOKED`, or a lock file not naming the window, ends a Clarvis-engine run. With RAVIS unreachable the run continues on the file and re-registers (§5.1, §6.3, §9) |
| **AH2** | Attached means the Clarvis panel's heartbeat through `presence`, not the extension host. The 30-minute and 2-hour rules and live step 6 follow (§2.2, §3.5.4, §4.8, §5.7, §11) |
| **AH3** | `forRun` and `scope:"session"` are never offered. Chosen over resetting at every save, because the cache also spans steers and continued turns inside one session, and a reset would still rely on an unverified lifetime. K11 only records it (§3.5.2, §5.2, §10.4) |
| **AH4** | A switch leaves the Codex session `idle`, not ended or archived; switching back reuses it. Archive only on an explicit end. `thread/unarchive` before any resume, pinned in the used methods; calibration K13 (§4.3, §4.10, §6.2, §11) |
| **AH5** | Roots equal to an allowed-roots entry, or touching a protected repository or denied path, are refused unless listed with `allow_protected`. K5c tests "deny beats write". Asked as decision (b); later answered no, see §14.6 (§3.5.1, §4.1, §10.4, §12) |
| AM1 | Accepted versions always `unproven`; check 7 only tests loading. The re-proof (K5a without a model, K5b with a short turn) makes a version `proven`. Surface adds `ThreadResumeParams`, `TurnStartParams` and `CommandExecParams` (§3.4, §4.3) |
| AM2 | Calibration runs inside RAVIS's one process through a dev-only admin route; the lock rule and lock file move to R2 (§10.3, §10.4) |
| AM3 | I/O discipline: reader task, per-session bounded queues, writer task with timeouts, bounded SSE buffers, sampling in a thread executor, watchdogs (§4.4) |
| AM4 | `runtimeWorkspaceRoots:[root]` on every thread and turn; pinned; tested in K5 (§4.9) |
| AM5 | Working folder alone is ambiguous and never killed. Kills only by thread, sandbox root or process tree, with start time. Missing `osPid` handled (§4.5) |
| AM6 | Answer key `rid:window_id`; resolution checked before replay across windows (§3.5.1, §5.2) |
| AM7 | A per-session action lock for mutating routes; `stopping` set inside it (§3.5.3, §5.3) |
| AM8 | `Idempotency-Key` on settle, transfer, takeover and reissue-token, with replay semantics (§3.5.1, §3.6) |
| AM9 | `git_dir` sent by Clarvis and validated by RAVIS without git (§3.5.1, §6.1, §6.3) |
| AM10 | Asked as decision (a): stop-only from the menu bar or dashboard, with a minimal admin design; later answered yes, see §14.6 (§3.5.5, §7.4, §12) |
| AM11 | `running_command` in heartbeats and the lock file; killed and confirmed at a takeover before the new holder writes (§3.6, §5.1, §6.3) |
| AM12 | Separate delta and non-delta buffers; non-delta kept for the session; snapshots carry recent items (§3.5.4) |
| AL1 | Folder names in `/api/v1/codex` aligned with the `RAVIS.md` §9.7 amendment (§8.3) |
| AL2 | Elicitation declined by policy (§4.4) |
| AL3 | A takeover continues the same task with `continueOn` and commits leftovers first (§6.3) |
| AL4 | Sign-out blocked only by an active turn or a pending settle (§3.4) |
| AL5 | A turn only waiting for an answer is paused 10 minutes into a pending binary swap (§4.4) |
| AL6 | Local health probe `thread/loaded/list` (§4.4) |

**Restored from the final check:** accepted versions are always unproven (AM1), and a command running in a taken-over window is recorded and killed (AM11).

**Size:** about 29,700 lines over about 24 sessions (was about 26,400 over 22.5). Decision (a) was later answered yes (§14.6).

**Left as is:**
- **Attachment is self-reported** by the panel heartbeat (R8). Stronger proof would need a browser-side secret, which code-server's architecture doesn't offer the extension.
- **A process orphaned between samples, or matched only by working folder,** is reported, not killed (R3). Killing on weaker evidence risks another project's work.
- **An old Clarvis's hand-typed id** still makes an empty branch (R11). RAVIS can't prevent a client's own setup.

### 14.6 The owner's answers to the two follow-up questions

| Question | Answer | What changed |
|---|---|---|
| **(a)** Stop from the menu bar or dashboard | **Yes** | New §3.5.5: `POST /api/v1/agent-sessions/{sid}/owner-stop`, admin-only (`admin.launcher`), no session token (400 if sent), confirmation of folder name and start time (409 on mismatch), stop only (interrupt, kill that task's commands, pause, keep the thread). It sits on its own router and dependency, with a route-table test that every other session and lock route keeps refusing NERVIS, admin and launcher credentials. Attached windows get `stopped_by` and say "Stopped from the menu bar/dashboard". Added: the launcher `codex stop`, the menu `NSAlert`, the dashboard confirm and control route, `runs[].id` for named callers, the audit entry, a §9 row, gate case 7, build items (R3, N1a, N1b, N2, C2a) and live steps 12a and 12b. Spec wording updated in runbook §2 and §2.2, `CLARVIS.md` §6.7, `RAVIS.md` §15.1 and M29, and `NERVIS.md` §8 and M28 |
| **(b)** Codex on the ecosystem's own repositories | **No** | The default stands: NERVIS-ecosystem, clarvis and the coding folder itself are refused. The explicit per-folder listing (`allow_protected`) is documented as the way to allow one later, and calibration K5c is kept for that day. "Pending" markers removed |

§12 now states that no owner decisions remain, and §1 mentions the Stop button. **Size:** about 30,400 lines over about 24.5 sessions (was about 29,700 over 24).

### 14.7 Final check of A: three contract patches, the takeover kill, and the lows

The final check (`codex-design-review.md`, "Final check of A") found nothing blocking. Everything it raised is folded in here, so Increment 0's fixtures start complete.

| Finding | What changed |
|---|---|
| **F-A1** (high) An idle session after a switch could start a turn without the lock | `turns`, a steer that finds no active turn, and a queued steer's follow-on turn all need the session to hold the project lock, or to take it atomically (a transfer token, or a free root). Otherwise 409 `PROJECT_LOCKED`. A queued steer that lost the lock emits `feedback {how:"not_delivered"}`, which goes to `latestFeedback`. Changed: §3.5.3, §3.5.4, §5.4, §6.2, §9, invariant 1; I0 fixtures; R3 and C2a tests |
| **F-A2** (medium) The dashboard Stop couldn't carry its idempotency key | `ravis_peer.configure` gains a keyword-only `headers` argument, which can never carry `authorization`. NERVIS validates the page's `Idempotency-Key` and forwards it, and a pytest asserts RAVIS receives it. Changed: §3.8, §7.3, §7.4, N2 |
| **F-A3** (medium) The re-proof was admin-started Codex work NERVIS could trigger | **Trigger:** the owner, from the menu bar only (`run.py codex reprove`), with a new launcher credential, `admin.owner_cli`, that is never given to NERVIS. NERVIS's credential gets 403, and its re-test route and dashboard button are gone. **Approvals:** RAVIS's re-test harness allows only the four listed commands, in its own throwaway thread; this is a stated, tested exception to invariant 2. **Model turn:** one fixed prompt, `low` effort, at most 12 steps and 5 minutes. **Results:** `proven`, `failed` or `inconclusive`. Calibration's route takes the same credential. Changed: §1, §3.4, §3.7, §3.8, §3.9, §4.1, §4.3, §7.1-7.3, §8, §9, §10.4, §12, §13; R2, N1a, N1b, N2 |
| **F-A4** (medium) A takeover killed only the command's pid | `running_command` carries `pgid`. The takeover checks the group is still the recorded one, kills the whole group and its descendants, and confirms; if it can't confirm, the takeover stays `leftover`. Changed: §3.6, §5.1, §6.3; R4, C2a |
| F-A5 | `paused_for_update` added to the session states, `runs[].state`, the settle list, the menu row and the card's chips |
| F-A6 | §4.10 and the `RAVIS.md` §11 amendment state both buffers' bounds |
| F-A7 | §5.3 points to §3.5.3's list of serialised routes |
| F-A8 | Resumed panel pings reopen the stream and post presence at once (§3.5.4, §5.7); a C2a test |
| F-A9 | Reattach reconciles a `gone` window that superseded RAVIS's lock, adopts its lock file, then settles (§5.7, §9; R4, C2a) |
| F-A10 | The Stop confirmation carries the current `turn_id`, not the session's start time |
| F-A11 | The menu bar uses `admin.owner_cli` and NERVIS uses `admin.launcher`, so their Stop rate limits and audit identities are separate |
| F-A12 | `agent_protected_repositories` defaults to RAVIS's own checkout and its sibling `clarvis`, even without the launcher |
| AM1 wording | Check 7(b) says "the same flag syntax and representative paths", not "the exact run flags" |

**Left for the build, not the contract:**
- how reliably Codex follows the re-test's fixed list within its caps (measured in Cal; an `inconclusive` result just means trying again);
- a command child that fully detached itself before the takeover's snapshot can't be found, the same residual gap as §4.5 (measured in R4).

**Size:** about 31,200 lines over about 25 sessions (was about 30,400 over 24.5).

---

## Appendix: considered alternative, Clarvis hosts Codex (not chosen)

**What it was.**
- **Runs in the window.** Clarvis's extension host started one `codex app-server` per run, on RAVIS's Codex home.
- **RAVIS managed only the runtime:** a short-lived account process for sign-in and allowance, the version pin, and the `ravis/codex` entry and refusal.
- **Approvals never left the window,** a run ended when its window reloaded or closed, and Stop was an in-process interrupt followed by a guaranteed kill.
- **One writer** came from a lock file in the checkout plus a Mac-wide lock, both judged by the same pid, start-time and heartbeat rule.
- **No relay, no session store, no project-lock API in RAVIS.** Estimated at about 17,000 lines.

**Why it was recommended.**
- Stop and approvals couldn't depend on RAVIS being up.
- NERVIS or admin credentials could never answer, by construction rather than by refusal checks.
- No task could keep working unattended.
- The spec changes were narrower, and RAVIS stayed out of workspaces.

**Why it was not chosen.** The owner wanted what the handoff described (D1): Codex tasks managed through RAVIS, running on when an editor closes, and reattached from any editor. B could not offer that without becoming A.

**What its reviews contributed to A:** the confirmed-exit ordering; branch continuation; the corrected sandbox facts and the permission profile; the named secrets; the shared lock rule, fence and sleep caveat; feedback across switches; the strict version pin with its strict-rules check; the Homebrew link; account-change detection; calibration as its own step; the file-ownership and version-pairing rules; and the dashboard gates.
