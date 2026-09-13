> **Working design record, 13 September 2026.** Kept so the Codex engine build (RAVIS M29, Clarvis E-C9,
> NERVIS M28) can be continued by any session. It is not the contract: the canonical text is
> `ECOSYSTEM_RUNBOOK.md` §2.2, `RAVIS.md` §15.1.2, `CLARVIS.md` §5.5 and `NERVIS.md`, and where they
> differ they win. Retire this folder when those milestones close.

# Review of `codex-design.md` (Codex engine, architecture B)

Reviewed 13 September 2026 against `NERVIS-ecosystem` 94c3565 and `clarvis` 90df7be. Both working trees were clean at review time. Read-only: no repository edits, Codex was not run, `~/.codex` and credential files were not opened. Line numbers `Lnnn` refer to `codex-design.md`.

Evidence tags: **CONFIRMED** (read in source, schema or on this Mac), **WRONG** (source or schema contradicts the document), **UNVERIFIABLE** (cannot be settled read-only).

## Counts

| Severity | Count |
|---|---|
| Blocking | 3 |
| High | 8 |
| Medium | 16 |
| Low | 11 |
| **Total** | **38** |

---

## Blocking

### B1. Stop and transfer release the lock and commit while Codex is still alive
- **Where:** §5.3 step 6 (L815-819) settles by reconciling git, committing, saving the checkpoint and **releasing the locks**. §4.4 "End" (L596) closes stdin, waits 2 s and SIGTERMs the group only *after* the run settles, with no SIGKILL. §6.2 steps 2-3 (L1027-1028) set the lock to `transferring` and then run §5.3, which releases it.
- **Consequences:**
  - A second window or the destination engine can take the lock while the old Codex process and its commands still exist. That breaks invariant 1 (L1253) and the owner's "one writer".
  - The git commit and checkpoint are made while Codex may still be flushing. Commands started by Codex can also still be running: background terminals persist across turns, and their cleanup methods `thread/backgroundTerminals/*` are experimental (brief L110).
  - The "gone within 10 seconds" claim (L821, E-C9 exit L1315) holds only on the hung-interrupt path (step 5). **WRONG** as a general statement.
- **Fix:** Reorder to interrupt → kill the process group and wait for exit (with SIGKILL escalation) → reconcile → commit → checkpoint → release the lock. A transfer keeps the lock until the destination has started.

### B2. A switch cannot continue on the same branch with today's `AgentBranch`
- **Where:** §6.2 step 7 (L1039): "Same run branch. A branch already present is continued by checkout, not recreated." §5.1 (L700) says `AgentRunner`'s only change is the `engine` field. C2 lists `AgentRunner.ts (engine field only)` (L1525), and C3 (L1568-1571) does not touch `AgentBranch`.
- **Evidence (CONFIRMED):**
  - `clarvis/src/agent/AgentBranch.ts:90-121`: `begin()` always mints a new name (`branchNameFor`) and calls `createBranch(name, true, ref)`.
  - `branchNames.ts:59-61`: `isRealBase` is false for any `clarvis/…` head, so `baseFor` returns the *remembered base*. `startAt(name, base, base)` then checks out a new branch **from the base branch**.
  - Result: after Codex → `ravis/clarvis-agent`, Clarvis's engine starts on a fresh branch off `main`, without Codex's committed work in the tree. The checkpoint text meanwhile tells it those files changed.
- **Fix:** Add a "continue this branch at `checkpoint.git.headCommit`" mode to `AgentBranch`/`AgentRunner` (and to the Codex path at L903). List it in C3 with a test, and amend §5.1 L700 and C2 L1525.

### B3. Decision D4 and the spec text rest on wrong or unestablished sandbox facts
- **Where:** D4 (L1696-1701) says Codex "can only write inside the project, has no network". The §8.2 `CLARVIS.md` row :80 text (L1277) says "writes only inside the workspace, network off". §5.6 (L898-899) says reads have "No equivalent". D4's third option (L1699) is "don't build until Codex can be told not to read sensitive files".
- **Evidence:**
  - **Writes, WRONG.** In the stable schema, `SandboxPolicy.workspaceWrite` has `excludeSlashTmp` and `excludeTmpdirEnvVar`, both defaulting to **false** (`codex-schema-0.154.0-alpha.6.2/codex_app_server_protocol.v2.schemas.json`, `SandboxPolicy`). The design's `sandboxPolicy {type:"workspaceWrite", writableRoots:[root], networkAccess:false}` (L898) omits both, so `/tmp` and `$TMPDIR` are writable. `TMPDIR` is also passed through (L587).
  - **Network, overstated.** It is off by default, but a command approval can carry `networkApprovalContext`, and **Run it** grants it (L769).
  - **Reads, "no equivalent" is WRONG in substance.**
    - The binary says "workspaceWrite.readOnlyAccess is no longer supported; use permissionProfile for restricted reads" (`codex-strings.txt`).
    - The experimental schema has `FileSystemSandboxEntry {access: "read"|"write"|"deny", path}`, special paths (`project_roots`, `tmpdir`, `slash_tmp`, `minimal`) and `thread/start.permissions` ("Named profile id … Cannot be combined with `sandbox`").
    - A read-restricting mechanism exists behind `experimentalApi` or config profiles. The design never evaluated it.
  - **Approved commands, UNVERIFIABLE and high-risk.**
    - The binary carries `sandbox_permissions` (19 hits) and `require_escalated` (15).
    - The field that would tell the client a request is an escalation (`additionalPermissions`) is experimental (brief L142, L558).
    - Nothing establishes that **Run it**, or Unattended's automatic `once` (L777-779), runs the command *inside* the sandbox rather than escalated.
- **Fix:**
  - Correct D4 and the L1277 wording.
  - Send `excludeSlashTmp:true, excludeTmpdirEnvVar:true`.
  - Add a calibration item proving an approved command cannot write outside the workspace.
  - Offer "use Codex's permission profile with deny entries" as a D4 option.

---

## High

### H1. RAVIS's own secrets sit one `cat` away from Codex, and the design never names them
- **Where:** §4.2 (L482) puts CODEX_HOME at `~/.config/ravis/codex`. §4.4 (L588) strips environment variables and says "Codex's commands must not be able to read the launcher's token". R1 (L1714-1717) mentions only `.env` and key files generically.
- **Evidence (CONFIRMED):**
  - RAVIS stores every provider API key in `config_directory()/"credentials.json"`, i.e. `~/.config/ravis/credentials.json`, a sibling of the Codex home (`ravis/src/ravis/credentials.py:173-186, 285-286`).
  - The launcher keeps `.run/ravis-admin.token`, `nervis-admin.token`, `clarvis-ravis.token` and `nervis-ravis.token` as ordinary 0600 files (`tools/run.py:70, 911, 929, 946`). Same-user reads succeed, and the sandbox allows reads anywhere.
  - Codex's own `auth.json` (the ChatGPT refresh token) is in the same home.
  - Command output reaches the Clarvis terminal, the ledger `detail`, the checkpoint's `outputTail` (L995) and Codex's rollouts.
- **Fix:** Move CODEX_HOME outside RAVIS's config folder. Name these files in R1, D4 and `CLARVIS.md` §5.5. Deny them through a permission profile if B3's option is taken. Redact known secret paths from ledger and checkpoint output.

### H2. Leftover commands are never killed once Codex itself has exited
- **Where:** §6.3 step 3 (L1079) kills the group only if `codex.pgid` is alive **and** `ps -o command= -p <pid>` matches the executable. R5 (L1729) relies on this takeover.
- **Evidence:**
  - On reload or crash, Codex exits on stdin EOF (brief L66-68, **CONFIRMED** for an idle process). Its leader pid is then dead, `ps -p` prints nothing, and a surviving child in the group is skipped. This is a logic error.
  - Codex runs commands through PTYs (`openpty`, `portable_pty` in the strings dump). A PTY child normally starts its own session, so `kill(-pgid)` may miss it (**UNVERIFIABLE**).
- **Fix:** Record the pgid and spawn time. Kill every process whose pgid matches (`pgrep -g`) and whose start time is after `since`. Add a calibration check that a PTY or background command dies with the group.

### H3. Two Codex processes can refresh one sign-in at the same moment
- **Where:** Fact 10 (L85-90) says "verified in the binary". §4.4 timers (L574-577) run RAVIS's permanent account child every 5 min (`account/rateLimits/read`) and every 15 min (`account/read`), including while a Clarvis run process uses the same home. R4 (L1724-1727).
- **Evidence:** **Partly CONFIRMED.** The string "Skipping token refresh because auth changed after guarded reload." exists (20 hits), and so does "refresh token was already used" (1). No cross-process lock on `auth.json` was found. String presence is not behaviour, and two processes reading the same refresh token before either writes is exactly the case a reload-then-compare does not close. The outcome would be a mid-run sign-out.
- **Fix:** RAVIS suspends authenticated reads while `<CODEX_HOME>/.clarvis/run.lock` exists (Clarvis already forwards the pushes), or makes the account child short-lived and on demand.

### H4. D2's security argument does not match the real trust boundary
- **Where:** D2 (L1684-1688) and §3.4 (L318-322) say only admin holders (menu bar, dashboard) may start sign-in, because "any program holding Clarvis's ordinary key could then start a sign-in".
- **Evidence (CONFIRMED):**
  - NERVIS writes its control token into `/index.html` for any loopback GET (`nervis/src/nervis/web.py:51-79`, `index.html:5, 2173, 2193`), and `require_control` checks only that token (`api/control.py:40-51`). Any same-user process can therefore call the NERVIS sign-in, sign-out and allow-untested routes (§3.8).
  - `.run/ravis-admin.token` is a readable file.
  - Any local process can spawn `codex app-server` on the known CODEX_HOME and log in directly.
  - "Admin-only" is therefore a UX choice, not a protection against a local program.
- **Fix:** Restate D2 as UX and audit. If an account swap is the concern, detect it (account id or plan change → refuse runs until the owner confirms) instead of relying on credential tiers.

### H5. The calibration turn inside C2 depends on work that is not done yet
- **Where:** C2 calibration (L1539-1546), placed in Wave 2 parallel with R2, N1b and N2 (L1502).
- **What it needs:**
  - R2's sign-in routes, a sign-in UI (N1a or N2), and a stack restarted on RAVIS 0.24.0.
  - RAVIS answering `signed_in`, because Clarvis refuses to start without it (§4.1 step 5, L470-473).
  - A runnable Clarvis, meaning an Extension Development Host or an install. An install hits the 0.16.0 trap and the peer-message rule (L1611).
- **Knock-on:** Its answers can rewrite §5.2 and §5.6 (L918-922), which C2 is building and testing at the same moment. N2 also waits on R2 (knowledge files and the compatibility bump, L1559-1560).
- **Fix:** Make calibration a separate step after R2, N1a and a dev-host run, and before C2's approvals mapping. Mark the mapping provisional until then.

### H6. Integrator-only files contradict the per-agent gates
- **Where:** §10.4 (L1586) gives `STATUS.md` and `RELEASES.md` to one integrator at the end of each wave. But R1's gates include `check_status.py` and the "23 checks → 25" edit at `STATUS.md:36` (L1480).
- **Evidence (CONFIRMED):**
  - `tools/check_status.py:81-109` counts real tests against `STATUS.md`.
  - `tools/check_releases.py` fails the moment a manifest version changes without its `## <Product> — <version>` heading.
  - A RAVIS bump also fails `check_compatibility.py` until NERVIS moves its window (`nervis/src/nervis/compatibility.py:61`, max `0.23.999`).
  - Under "commit straight to main", no agent can commit green.
- **Fix:** Each agent edits its own test-count line and RELEASES section, and bumps are paired in one commit. Or name the gates that run only at integration.

### H7. "Closing the window stops Codex" is unverified for code-server
- **Where:** §1 item 11 (L47), §2.4 (L135), the `CLARVIS.md` §4 amendment (L1282), and §6.3 liveness (L1077).
- **Evidence:** **UNVERIFIABLE** here. code-server's server has a `reconnection-grace-time` option (`~/.local/lib/code-server-4.135.0/lib/vscode/out/server-main.js`), so a closed browser tab does not immediately end the extension host. A run waiting on an approval then keeps a live `extensionHostPid`. Other windows get "Stop it there first" for a window nobody can reach. Live step 10 (L1663) tests reload only.
- **Fix:** Add a live step "close the tab during an approval". Show the lock holder and its age on the card, and allow a confirmed takeover.

### H8. Typed feedback is lost at the switch boundary (coding-6c)
- **Where:** §6.2 Clarvis → Codex step 3 (L1046) records only step questions. Codex → Clarvis captures only Codex's queue (L1028).
- **Evidence (CONFIRMED):**
  - `RunSession.redirect` returns `false` when no run is set (`RunSession.ts:213-217`), so text typed between stop and destination start becomes a chat question.
  - Interjections still queued inside `AgentRunner` at Stop are never taken (`takeInterjections` runs only inside the loop, `AgentRunner.ts:654-659`), yet the design says `AgentRunner` gains only `engine`.
- **Fix:** While `checkpoint.transfer` is set, route interjections into `latestFeedback`. Expose `AgentRunner`'s untaken interjections on stop.

---

## Medium

### M1. The subset hash reads a bundle that lacks server requests
- **Where:** §4.3 (L521-529).
- **Evidence (WRONG):** `codex_app_server_protocol.v2.schemas.json` has no `ServerRequest` definition. It is in `codex_app_server_protocol.schemas.json` and `ServerRequest.json`, each with 10 methods. By the design's own guard (L528), every non-exact runtime becomes `none`, so D5's recommended mode can never match.
- **Fix:** Build the subset from the combined bundle, with a fixture test that all 37 listed methods resolve for the tested runtime.

### M2. One question slot, several Codex requests
- **Where:** L746, L787-790.
- **Evidence (CONFIRMED):** `PendingChoice.ask()` cancels the pending question first (`clarvis/src/chat/PendingChoice.ts:52-65`). A second open request therefore silently resolves the first as `undefined`. `engineDecisionAfterAsking` (L757-760) turns that into `skip`, so `decline` (or empty answers) is sent without the user choosing.
- **Fix:** Queue engine questions first-in first-out in `askEngine`, and test two overlapping approvals.

### M3. Stop can race the file snapshot before `accept`
- **Where:** L785 and L811.
- **Evidence:** `checkpoint.capture` is async (`Checkpoint.ts:135`). If the Stop check runs before the capture, a Stop during the capture still sends `accept`.
- **Fix:** Evaluate `engineDecisionAfterAsking(answer, signal.aborted)` immediately before the write, after every await, and add that test.

### M4. The engine override is sticky and can make later work paid
- **Where:** L1016, L1035; resolution rules L676-683.
- **Problem:** `clarvis.engine.override` has no clearing rule and no stated precedence over the user setting. After one confirmed Codex → API switch, later tasks in that project run on paid API while the picker still shows `ravis/codex`.
- **Fix:** Key the override to the checkpoint's `taskId`, clear it when the milestone settles or the user picks a model, and show the effective engine in the picker.

### M5. D5 contradicts the owner's standing decision, and the alpha is not raised
- **Where:** D5 (L1703-1708).
- **Problem:**
  - The owner decided "reports an untested version plainly instead of guessing" (constraints L5-6). The recommended "subset" mode runs a different binary as "newer, compatible", a state that no table draws (L283-291, L1138-1148, L1179).
  - The tested runtime is `0.154.0-alpha.6.2`, while the owner's decision named 0.153.4.
- **Fix:** Default to strict. If subset stays, add a visible "compatible, not tested" state, and ask the owner to accept an alpha runtime.

### M6. "Credits depleted" is undefined
- **Where:** L290.
- **Evidence (CONFIRMED):** `CreditsSnapshot {hasCredits, unlimited, balance}`. The design's own signed-in Plus example shows `has_credits:false` (L272). Read literally, that marks a normal plan `quota_exhausted`. `spendControlReached` and `individualLimit` are ignored.
- **Fix:** Define depletion only by `rateLimitReachedType ∈ {workspace_owner_credits_depleted, workspace_member_credits_depleted}`, and decide what `spendControlReached` means.

### M7. Desktop VS Code usage reports get 403
- **Where:** §3.6 (L369) and §5.7 (L959).
- **Evidence (CONFIRMED):** `launcherCredential` returns a token only from `CLARVIS_RAVIS_CREDENTIAL` (`clarvis/src/model/ravisCredential.ts:26-39`). The launcher injects it into code-server only (`tools/run.py:538-540`). Desktop Clarvis is anonymous, so its reports are refused and its quota failures never trigger RAVIS's re-read.
- **Fix:** Accept number-only anonymous reports (they already cannot set a blocking state), or give desktop VS Code a credential path.

### M8. The new workspace lock changes existing Clarvis behaviour
- **Where:** §6.3 table (L1064) says every run from "all three construction sites" takes the lock. §5.7 (L950) gives `Replier` only a chat guard.
- **Evidence (CONFIRMED):** The third site is the tool-using chat answer (`clarvis/src/chat/Replier.ts:205`). A question asked in another window would be refused during any build.
- **Fix:** Exclude read-only answers, and list "Clarvis's own engine now takes a checkout lock" as an explicit behaviour change or owner decision.

### M9. Stale-lock detection uses the pid alone
- **Where:** L1077.
- **Problem:** After an extension-host crash or a reboot, a reused pid keeps the lock "alive" indefinitely.
- **Fix:** Store the process start time and a heartbeat mtime, and treat any mismatch as a dead holder.

### M10. Parallel agents in one checkout share a git index
- **Where:** Waves 1-2 (L1461, L1502), §10.4 (L1591).
- **Evidence (CONFIRMED):** `tools/githooks/pre-commit` snapshots the whole index (`git checkout-index --all`), and `check_clean_clone.sh` tests the tree. One agent's staged or half-done files enter another agent's checks. "Park and wait" makes the waves serial anyway.
- **Fix:** One git worktree per agent, or declare the waves sequential per repository.

### M11. A peer session may be editing `shaping_check.js`
- **Where:** N2 edits `nervis/tools/shaping_check.js` (L1204, L1557).
- **Problem:** Constraints L55 flag another session's task in that folder. §10.4 does not mention it.
- **Fix:** Message that session before N2 starts.

### M12. The dashboard's Sign in will be popup-blocked
- **Where:** L1181.
- **Problem:** `window.open(auth_url)` runs after an awaited POST, which is outside the click's user activation in Safari and often in Chrome.
- **Fix:** Open a blank tab synchronously on click, then set its location; or show the link.

### M13. Old-client refusal still leaves a workspace action
- **Where:** The handoff requires "causes no workspace action" (handoff L156). The design accepts an empty branch plus snapshot (L220-224, R10 L1738).
- **Evidence:** CONFIRMED in `AgentRunner.ts:551-565, 289-302`.
- **Problem:** No alternative is offered, such as listing `ravis/codex` only to clients that identify as Clarvis ≥ 0.17 (the id is confirmed through `/api/v1/codex` anyway).
- **Fix:** Evaluate conditional listing, or put the side effect to the owner as a decision.

### M14. The proxied code-server origin is not tested
- **Where:** The handoff asks to preserve "direct/proxied browser origin" (handoff L164). The live plan runs direct code-server only (L1646).
- **Evidence:** NERVIS has a code relay (`nervis/src/nervis/api/code.py:271`).
- **Fix:** Add a live step through the proxied origin, or state that it is unsupported.

### M15. "Wait" and the unanswered approval hold the Mac-wide lock
- **Where:** L1428 and L642-644.
- **Problem:** "Wait" has no defined behaviour. A run left waiting on an approval blocks every other Codex run on the Mac with no visible age.
- **Fix:** Define Wait (end the run, keep the checkpoint, remind at reset), and show the lock's age on the menu and card.

### M16. A ChatGPT app update during a run
- **Where:** R2 (L1718-1720).
- **Evidence (UNVERIFIABLE):** The arg0 symlinks (`apply_patch`, `codex-execve-wrapper`) point at the binary path (brief L78). An update mid-run can make patch or exec helpers run the new binary, or fail while the path is being replaced.
- **Fix:** Note it in R2. Treat a `fileChange failed` plus a sha256 change as a protocol mismatch.

---

## Low

- **L1.** The port-busy text blames the ChatGPT app (L333, L606). Unverified: its Codex (pid 48758, `codex -c features.code_mode_host=true app-server …`) shows no login, and ports 1455/1457 are free now. **Fix:** say "another program holds the sign-in ports".
- **L2.** §8.3 events (L1349-1350) list `ravis.codex.signed_out`, which §3 and §4 never emit, and `ravis.codex.untested_allowed`, which §3.4 (L361) records as an audit entry. **Fix:** align the lists.
- **L3.** `turn_failure.codex_error_info` (L384) lists `responseTooManyFailedAttempts` as a string, but it is an object variant (brief L545). **Fix:** carry the variant key plus `httpStatusCode`.
- **L4.** "New code ≤ 8" (L655, L1547) is not enforced: `clarvis/eslint.config.mjs:31` is 15, while runbook `:975` says 8. **Fix:** add an eslint override at 8 for `src/engine/**`.
- **L5.** The owner's "not installed" state (constraints L18) is folded into `not_available` (L1146). **Fix:** use a distinct reason line for a missing executable.
- **L6.** `_may_write` is cited as `credentials.py:140` (L92, L319); it is `ravis/src/ravis/api/management/credentials.py:140`, and two `credentials.py` exist. **Fix:** give the full path.
- **L7.** There is no rollback or disable procedure, which the handoff's completion report requires (handoff L178). **Fix:** add `RAVIS_CODEX_ENABLED=false`, reinstalling the previous Clarvis vsix, and removing CODEX_HOME.
- **L8.** Porting `NOT_CHAT` into Python (L228) duplicates a TypeScript list that will drift. **Fix:** use a shared fixture of ids instead.
- **L9.** The checkpoint written to workspaceState at every request (L1010-1014) is async, so a crash can leave the reload message wrong (L1087). **Fix:** say "may be stale" when the lock and checkpoint disagree.
- **L10.** §1, §3.4 and live step 8 (L1661) assume D1-D3's recommendations. **Fix:** note which live steps change if the owner chooses otherwise.
- **L11.** Sign-out from NERVIS during a run makes that run fail (L354), yet the new NERVIS row says it does not own "starting, stopping or answering any coding engine's work" (L1231). **Fix:** refuse sign-out while `run.lock` is held, or reword the row.

---

## Lens 2: load-bearing claims checked

| Claim | Verdict | Evidence |
|---|---|---|
| "Codex and anything it started are gone within 10 seconds" (L821, L1315) | **WRONG** as written; **UNVERIFIABLE** for PTY and background children | The success path kills the group only after settle, with no SIGKILL (L596, B1). The stdin-close exit was observed idle only; thread shutdown is bounded to 10 s (brief L66-68). PTY strings are present |
| "Codex rechecks the sign-in file before refreshing (verified in the binary)" (L86) | **Partly CONFIRMED** (string only) | 20 hits for the guarded-reload string; "refresh token was already used" is present; no cross-process lock found |
| The ChatGPT app keeps its own Codex server running | **CONFIRMED** | `pgrep`: pid 48758 `…/Resources/codex -c features.code_mode_host=true app-server --analytics-default-enabled …` |
| …and whether it holds 1455/1457 | **CONFIRMED not now**; "while it signs in" **UNVERIFIABLE** | `lsof` shows no listeners on 1455 or 1457 |
| NERVIS reads RAVIS with an ordinary credential | **CONFIRMED** | `peers/reader.py:85-96` returns `ravis_client_credential`. NERVIS also holds admin: `tools/run.py:400` and `:1006` (`admin.launcher`), used at `api/routes.py:480-588` |
| Sandbox: writes only in the project | **WRONG** | `excludeSlashTmp` and `excludeTmpdirEnvVar` default false (schema) |
| Sandbox: no network | **Overstated** | Default off, but approvable per command (`networkApprovalContext`) |
| Sandbox: reads anywhere, "no equivalent" | Reads **CONFIRMED**; "no equivalent" **WRONG** | Experimental permission profiles with `deny` entries; the binary names `permissionProfile` for restricted reads |
| 400 for old Clarvis versus the probe caching rule | **CONFIRMED** | `probeAnswered(400)` is true, so `false` is cached (`OpenAiCompatibleProvider.ts:54-56, 224-231`; `ModelService.ts:119-133`). Only `Replier.ts:72` probes. The agent path ends "The model gave up" (`AgentRunner.ts:637`). `_inspect` runs first (`chat.py:246-249`), before the disabled, upstream and route checks (`:258-270`) |
| The three runner construction sites | **CONFIRMED** | `extension.ts:999`, `RunSession.ts:264`, `Replier.ts:205`. See M8 for the lock inconsistency |
| Schema-hash pinning | Tree hash **CONFIRMED**; subset algorithm **WRONG** file | Recomputed `b32fa164…` (0.153.4 gives `363c314d…`). All used client methods and notifications are in the stable schema; the server requests are missing from the v2 bundle (M1) |
| Usage and rate limits: read versus push | **CONFIRMED** consistent; credits rule **undefined** | `account/rateLimits/read` is stable, and pushes arrive only in the turn's own process (brief L264). `wham/usage` appears in the strings (3 hits). `resetsAt` is int64 with its unit pinned by a fixture, as designed. See M6 |
| Other anchors | **CONFIRMED** | `index.html` `nervis:{` 3304, `liveRavisDashboard` 9433, `LIVE_TIMEOUT_MS=1500` 1639. Gates: complexity limit 13, liveness ceiling 47, shaping live screens `["Providers","Routes"]`, `RAVIS_READS` at 349. Relay prefix `api/v1/`. Swift at 80, 831, 866, 880, 888, with a 10 s tick. RAVIS: ruff 8, mypy strict, `EXPECTED_CHECKS` = 23, `STATUS.md:36`. Versions 0.23.7, 0.28.10, 0.16.0 (lock root matches), compatibility max 0.23.999. Both hosts on 0.15.4. `tools/no-network.sb` exists. The Clarvis Bridge server is GET-only (`src/bridge/server.ts:184`) |

## Lens 1: requirements coverage

| Requirement | Status | Note |
|---|---|---|
| Runtime path; version check; untested reported plainly | Partly | M5 |
| Own CODEX_HOME, never `~/.codex` | Covered | Path comparison only |
| Codex selectable, with progress, approvals, questions, Stop, steering | Covered with gaps | M2, M3 |
| Manual switching both ways, checkpoint, one writer | **Partly** | B1, B2, H8 |
| Stop releases questions; a late answer never starts a step (coding-87) | Covered with gaps | M2, M3 |
| Mid-run feedback reaches the engine (coding-6c) | Covered in-run; **missing at the switch** | H8 |
| Old clients refused before anything executes | Covered in RAVIS; partly for "no workspace action" | M13 |
| Discovery never triggers sign-in, launch or paid calls; both catalogue paths | Covered | `models.py` is cache-only. Add a test with no upstream configured |
| Quota distinct from throttling; not inferred from a 429 | Covered | M6 |
| No silent paid fallback | Covered, with the sticky-override hole | M4 |
| Menu bar Codex line | Covered | L5 |
| Dashboard card and Overview line with a gate | Covered | M12 |
| Session records (owner, workspace, task, thread, turn, pending) | Partly | Held in Clarvis's checkpoint and lock, not in RAVIS; depends on D1 |
| Relay the sign-in URL or device flow to Clarvis | Partly | D2 sends the user to NERVIS instead |
| No abandoned writer after a declared stop | **Partly** | B1, H2 |
| Verify code-server, direct and proxied | Partly | M14, H7 |
| Rollback and disable procedure | Missing | L7 |
| Peer traps: RELEASES heading, eslint 15, message before edit or package | Covered | Except H6 and M11 |

## Lens 3: security summary
- **Approvals:** no path lets NERVIS, RAVIS or admin answer one (**CONFIRMED**). RAVIS has no run routes, the Clarvis Bridge is GET-only, and NERVIS's control routes cover only sign-in.
- **Residual gaps:**
  - The "admin-only" boundary is illusory (H4).
  - Secrets are readable (H1, B3).
  - Approved commands may escalate (B3).
  - Leftover processes (H2).
- **Executable path:** comes from local configuration only, and is checked against RAVIS's recorded signature and sha256 (L436, L451-455). Covered.
- **CODEX_HOME:** mode 0700 is stated, but its placement inside RAVIS's config folder is the problem (H1).
- **Port conflict:** detected by a bind test plus Codex's error text; acceptable (L1).

## Lens 4: failure and concurrency summary
- **Two app-servers on one home:** H3.
- **One run per Mac, and a crash:** H2, M9, H7.
- **Reload mid-turn:** H2, L9.
- **RAVIS restart mid-sign-in:** disclosed (R8) and acceptable. A restart during a run also adds a second account process (H3).
- **ChatGPT update between check and run:** covered by sha256 plus `userAgent`. Mid-run: M16.
- **Quota exhausted mid-turn:** covered. Rule gap M6; "Wait" undefined (M15).
- **Approval timeouts:** none by design. The consequences are H7 and M15.
- **Stop racing an approval:** M2, M3.
- **Checkpoint written while Codex still flushes:** B1.

## Lens 7: owner decisions
- **D1:** a real decision. It overturns the handoff's architecture, and the document says so. Fine.
- **D2:** a real decision, but its security justification is wrong (H4).
- **D3:** a real decision.
- **D4:** miscast. Wrong facts, and the permission-profile option is missing (B3).
- **D5:** partly settled already by the owner ("instead of guessing"), and the recommended mode cannot work as specified (M5, M1).
- **Missing decisions:**
  - Accept an **alpha** runtime.
  - Clarvis's own engine and its read-only answers gain a checkout lock (M8).
  - How long a switch's model override lasts (M4).
  - Accept old Clarvis's empty branch, or list conditionally (M13).
  - Acknowledge R12: `clarvis` and `ravis` sent as the originator to OpenAI.
  - Whether RAVIS polls OpenAI's usage endpoint every 5 minutes around the clock, or only while a run, menu or dashboard is active (R9).
  - Whether desktop VS Code is in the first delivery (M7).

## Verdict
**Ready for the owner after fixes, not a redesign.** Architecture B holds up: approvals stay structurally inside the window, and Stop does not depend on RAVIS. The three blockers are fixable in the document:
- the Stop and transfer ordering;
- a branch-continuation change the build plan omits;
- a corrected factual basis for D4.

The high items should be settled before sign-off, because four of them (H1, H3, H4, H7) change what the owner is being asked to accept.

---

## Re-check of the revised design (13 September 2026, file dated 04:29, 1,960 lines)

Same rules as before: read-only, `~/.codex` not read, Codex not run, no network. Lines `Lnnn` refer to the **revised** `codex-design.md`. The whole body was read, not only the §14 changelog.

### 1. Were the findings fixed in the body?

| Finding | Verdict | Where in the body | Remaining gap |
|---|---|---|---|
| **B1** Stop and transfer ordering | **PARTLY** | End sequence with confirmed exit before any commit, save or unlock (§4.4 L627-638). §5.3 steps 5-6 (L869-874). The switch holds the workspace lock (§6.2 L1078, L1085-1086). Release only after confirmation (§6.3 L1171). 15-second timing (L885, E-C9 L1387) | The automatic path is fixed, but two owner-choice branches can still unlock beside a live process (N1, N2) |
| **B2** Branch continuation | **FIXED** | `AgentBranch.continueOn` with refusal cases (§5.1 L773-784). Used in both directions (§6.2 L1097, L1113). Built in C3, with a host spec on a fixture repository (L1685-1690) | Low: N9 |
| **B3** Sandbox facts and D4 framing | **FIXED** | Fact 12 (L109-114); temp excludes on the command line and every turn (L603-605, L683). Network asked per command (L833). Standard versus strict rules (§4.8). Calibration K2-K5 (L1650-1654). D2 options (L1806-1817). `CLARVIS.md` row :80 corrected (L1362) | The strict option has a new hole in version acceptance (N4) |
| **H1** Secrets within reach | **FIXED** | Home moved to `~/.local/share/ravis-codex`, stated honestly (L502, L507-509). Secrets named (L115-119). Deny list (L695-700). Redaction (L709-713). Invariant 8 (L1347) | Low: on desktop VS Code, the `.run` deny path must be set by hand (L697). Rollouts in the home are not denied |
| **H2** Leftover processes | **FIXED**, residual disclosed | Family tree plus process group with start times (L619-625). Confirmation (L636). Takeover kills the recorded family (L1159). C1 tests (L1616). K6 (L1654) | Low: `pgrep -g <pgid>` members at takeover (L1159) have no recorded start time; after a reboot a reused group id could match. Kill only members started after `since` |
| **H3** Two Codex processes on one home | **PARTLY** | Short-lived account child, never while the lock exists (L578-595). Clarvis takes the lock, then waits for `account_process: idle` (L585) | Four holes, listed as N5 |
| **H4** D2 security rationale | **FIXED** | Fact 11 (L104-108). "UX and audit" (L321-323, L1337). Account fingerprint detection (L326-333) | Low: N10 |
| **H5** Calibration dependency | **FIXED** | Its own step after R2, N1a and C1, before C2b (L1637-1668). No install needed (`codexCalibrate.ts`, L1642). §5.2 and §5.6 marked provisional (L722) | Low: N7, N8 |
| **H6** File ownership versus gates | **FIXED** | Each agent edits its own STATUS line and RELEASES section, with paired bumps (L1519-1531) | Low: "every commit green" (L1519) sits next to a named red `check_releases` window (L1531) |
| **H7** Closed code-server tab | **FIXED** | Grace time 3 h (L90), **CONFIRMED**: `server-main.js` has `reconnectionGraceTime(){return G$(this.args["reconnection-grace-time"],108e5)}`; my earlier "not confirmed" came from a failed regex. Heartbeat, `waitingOnYou` and takeover (L1147-1169). Live step 11 (L1766) | New contradictions in the takeover itself (N3) |
| **H8** Feedback lost at a switch | **FIXED** | `runTook` routing (L911; `ChatService.ts:277` **CONFIRMED**). `drainInterjections` (L764, L775; `AgentRunner.ts:161` **CONFIRMED**). §6.2 (L1093, L1098, L1110). Tests (L919-920, L1691) | — |
| **Subset-hash WRONG claim** | **FIXED** | The gate is removed; definition hashes come from the **combined** bundles (L16, L545-548); fixture test that all 37 methods resolve (L547, L1562). **CONFIRMED:** the stable combined bundle has `ServerRequest` (10 methods), the experimental one 11 | Check 5 covers stable methods only (N4) |

### 2. New contradictions or errors

- **N1 (High). "Leave it running and save anyway" unlocks beside a live writer.**
  - L881 runs step 6, which releases the locks (L874), yet the same line says "the lock records `leftover: [pid]`". §9 (L1495) and R4 (L1864) promise no unlock beside a leftover.
  - One click therefore lets another window start writing while that process runs.
  - *Fix:* this button saves but keeps both locks in a `leftover` state until the process is gone, and the card shows it.
- **N2 (Medium). Switch failure paths release locks too early.**
  - "Cancel the switch" with a leftover (L1086) falls under "Nothing is running, the locks are released" (L1124).
  - Clarvis → Codex waits for `AgentRunner`'s run to return (L1109), but `RunSession`'s `finally` releases the workspace lock (L792). The "a transfer keeps them" exemption appears only in `CodexRunner`'s Stop (L874).
  - *Fix:* state both exemptions explicitly: leftovers keep the locks on cancel, and `finally` skips release while `transfer` is set.
- **N3 (High). The takeover and liveness rules can create two writers.**
  - (a) L1168 says a taken-over Clarvis-engine holder's "`stopped()` commit still runs on its own branch". L1169 says a returning holder "never commits … over a lock that no longer names it". In one checkout, that commit lands on whatever the new holder has checked out.
  - (b) A heartbeat older than 90 s alone makes a holder dead (L1152-1155, "any of these"). After the Mac sleeps, every heartbeat is stale on wake while pid and start time still match, so another window can kill a live run.
  - *Fix:* a holder that lost the lock writes nothing to git (checkpoint note only). A stale heartbeat counts only together with a dead pid or a start-time mismatch, or only after 90 s of the observer's own uptime since wake.
- **N4 (High). Version acceptance cannot see a broken strict profile.** This is the permission-profile option plus the six checks.
  - Check 5 (L565) examines only `used_methods` (L525-535, stable methods).
  - The strict rules depend on experimental surface: `thread/start.permissions`, `additionalPermissions`, `availableDecisions`, `thread/backgroundTerminals/terminate`. They also depend on `-c` profile syntax that no schema describes (L694).
  - Check 6's handshake starts the **account** child (L566, L587) without the profile flags.
  - An accepted update could stop enforcing deny rules while the owner believes strict rules are on. K5's decoy proof (L1654) is not repeated for accepted versions.
  - *Fix:* with strict rules on, refuse acceptance when any experimental definition used has changed. Make check 6 start a child with the exact run flags plus `--strict-config`, and confirm the profile via `config/read`. Or require K5 again.
- **N5 (Medium). RAVIS and Clarvis judge the Mac-wide lock differently.**
  - (a) RAVIS pauses on the lock file *existing* (L584, L1133). Clarvis judges it by pid, start time and heartbeat (L1152-1155), and `deactivate()` does not release it (L1171). A stale lock after a quit or crash therefore blocks usage reads, sign-in and sign-out (409 `CODEX_RUN_IN_PROGRESS`, L344, L356) and acceptance check 6 (L566) until some window reconciles.
  - (b) Handoff race: `account_process` reads "running" only while the process exists (L287). RAVIS must report non-idle from *before* its pre-spawn lock check, or Clarvis can see "idle" in the gap.
  - (c) The calibration harness runs Codex on RAVIS's home (L1642) without taking the lock or waiting for idle.
  - (d) The lock watcher is every 2 s in L585 but every 10 s in L594.
  - *Fix:* RAVIS applies the same liveness rule and shows "stale lock". Add a `starting` flag set before the check. The harness uses `engineLock`. Pick one interval.
- **N6 (Medium). The Homebrew path is versioned.** L460 configures `/opt/homebrew/Caskroom/codex/0.154.0/bin/codex`. A `brew upgrade` (including a bulk upgrade of everything outdated) removes that folder, so Codex reads `not_installed` instead of `untested_version`. D4's "each update is a download you'd approve" (L1831) holds only if the owner never runs a general `brew upgrade`. *Fix:* configure `/opt/homebrew/bin/codex` with a realpath check, and say plainly that a general upgrade updates Codex too.
- **N7 (Low).** Calibration writes `STATUS.md` and `tested_runtimes.json` (L1663-1666), and the Clarvis RELEASES commit (L1530) goes into the ecosystem repository, while N1b and N2 run there (L1740). That contradicts "each repository has one agent at a time" (L1512). *Fix:* hand those edits to the ecosystem-track agent.
- **N8 (Low).** §2.3 (L140) says both fakes are scripted from calibration transcripts, but R2's fake comes from the probe transcript (L1581) and is built before calibration.
- **N9 (Low).** `continueOn` must set `created` and `previousBranch` (to the base). Otherwise `discardIfEmpty` (range `previousBranch..created`, `AgentBranch.ts:247-275, 303-315`) and undo can misread a continued run.
- **N10 (Low).** `Account.email` can be null (brief §3), which leaves the fingerprint undefined. The confirm route is as reachable to a local program as sign-in (fact 11), so detection catches accidental swaps, not a local attacker; say so.
- **N11 (Low).** On normal completion the end sequence kills everything Codex started, including a dev server the task asked for. State it.

**Checked and consistent:**
- Stop timing sums correctly (5+3+2+2+3).
- Invariants, the E-C9 exit and §9 match §5.3 except N1 and N2.
- The strict profile is not combined with `sandbox` (L702, L1119).
- Leftover-wording sweep: no stale "subset", "10 seconds", "25 checks", "allow-untested" or old home path remains in the body (hits were only in the changelog and figures).
- New anchors **CONFIRMED:** `OpenAiCompatibleProvider.listModels` (~L160, `/v1/models` with `headers`); `.run/code-server.password` and `code-server.yaml` (`tools/run.py:218, 238`).

### 3. The Homebrew claim ("the stable Homebrew copy already installed on this Mac, same OpenAI signature")

- **What is installed (CONFIRMED):**
  - Cask `codex` 0.154.0 at `/opt/homebrew/Caskroom/codex/0.154.0`, from `homebrew/cask` (API), Homebrew 6.0.22, `installed_on_request: true`.
  - `codex-package.json` `"version": "0.154.0"`.
  - `codesign`: `Developer ID Application: OpenAI OpCo, LLC (2DC432GLL2)`, signed 10 Sep 2026 00:13:46.
  - Mode `-rwxr-xr-x`.
- **When:** today, **13 September 02:26:32-35 local**.
  - `INSTALL_RECEIPT.json` `"time": 1789259195` is 00:26:35 UTC.
  - The metadata folder is named `20260913002633.150` (UTC).
  - The download-cache link `codex--0.154.0.tar.gz` was created 02:26:32; the tarball's own mtime (10 Sep 00:35) is the server's date.
  - `/opt/homebrew/bin/codex` was created 02:26:33.
- **Timeline:**
  - The coordinator's check at **00:25:22** found "codex: not on PATH".
  - The install happened at **02:26**.
  - The first use visible in the scratchpad is `codex-schema-brew-0.154.0` and `schemadump_s.py` at **02:36:19**.
  - The brief (02:58) cites "Homebrew codex-cli 0.154.0" without saying who installed it.
- **By what (UNVERIFIABLE):**
  - None of today's 20 Claude Code transcripts under `~/.claude/projects` holds a Bash command that installs Codex. Their brew-related commands are read-only checks at 00:25, 02:30 and 02:31, plus this review's.
  - Homebrew keeps no cask install log.
  - So no agent action on record installed it; a person, or something outside these transcripts, did. The coordinator has already asked the owner.
- **Consequence:** the sentence is literally true but misleading. The copy appeared during tonight's work, two hours after the owner chose the ChatGPT app copy, with unconfirmed provenance. *Fix:* D4 should say it was installed at 02:26 today by an unconfirmed party, and recommend it only once the owner confirms it is theirs. Also N6.

### 4. The decision list

**Yes.** §12 has exactly five decisions (D1-D5, L1794-1847). Each has plain-language options, a marked recommendation, a "Why the recommendation" line and "If you choose otherwise". The former decisions are listed as stated defaults (L1784-1792).
- **Caveat:** D4's recommendation depends on the Homebrew provenance question and on N6.
- **Minor:** "Clarvis's own writing runs also take the one-writer lock" (L1789) is a behaviour change the owner should notice even as a default.

### Verdict

**Not yet ready for the owner; one more small pass, no redesign.**
- Eleven of the twelve checked items are fixed or fixed with small residuals; B1 and H3 are partly fixed.
- N1 and N3 reopen the one-writer promise through a button and through the takeover rules.
- N4 undermines the recommended strict file rules after any accepted update.
- N5 lets a stale lock silently pause RAVIS.
- D4 needs the Homebrew provenance stated before it is recommended.
- All of these are local text and logic fixes.

---

## Final check (13 September 2026, file dated 04:48, 2,069 lines)

Same rules: read-only, `~/.codex` not read, Codex not run, no network. `Lnnn` refers to this revision. Only the five questions asked were checked.

### 1. Re-check findings: are they fixed in the body?

| Item | Verdict | Line(s) |
|---|---|---|
| N1 "Leave it running" unlock | **FIXED** | L920-926: only **Stop it** and **Keep waiting**; the locks stay in `leftover`, with "no way to save and release while the process lives". §9 L1567; R4 L1946 |
| N2 Switch failure paths | **FIXED** | Cancel keeps both locks (L1131-1133). A failure releases only when every process is gone (L1171-1172). `RunSession`'s `finally` never releases during a transfer (L836, L1235) |
| N3 Takeover commit and sleep kill | **FIXED** as reported | Fence (L1230). Guard before every write (L1226). `gone` needs pid or start-time evidence, and a stale heartbeat alone never kills (L1206-1207, L1212). New gaps listed in item 2 |
| N4 Acceptance versus strict rules | **PARTLY** | Check 7 (L589) and `proven`/`unproven` (L591-596) are wired into §3.4 (L380-391), §4.1 (L506) and §9 (L1564). Item 3 lists the problems |
| N5 Lock semantics and handover | **FIXED** | Shared rule (L1201-1212). A stale lock never pauses RAVIS (L614-615, L296-297, L356, L368). `starting` set before the check (L288, L618-622). Harness takes the lock (L1239, L1714). One 2 s watcher (L631). Low residual in item 2 |
| N6 Versioned Homebrew path | **FIXED** | L475 stores `$(brew --prefix)/bin/codex`; L494 re-resolves `realpath`; header L37-40 |
| N7 Ecosystem writes from the Clarvis track | **FIXED** | L1588, L1602 |
| N8 Fakes wording | **FIXED** | L140 |
| N9 `continueOn` fields | **FIXED** | L825 |
| N10 Null email | **FIXED** | L338-339 |
| N11 Completion kills servers | **FIXED** | L677 |
| Residual lows | **FIXED** | `pgrep -g` members only if started after `since` (L1216). Desktop `.run` path and Codex `sessions/` denied (L740-741). The "every commit green" exception is named (L1591) |

### 2. New contradictions in the lock rule

**Sound and CONFIRMED:**
- The `starting` handshake. Each side sets its own marker before checking the other's: Clarvis creates the lock before reading `account_process`, and RAVIS sets `starting` before reading the lock (L618-621), so one always sees the other.
- The 90-second wake rule is coherent, and its source exists on this Mac: `sysctl kern.waketime` answers.
- RAVIS and Clarvis agree on the verdicts (L1182, L1211, L295).

**Gaps:**
- **F1 (Medium). A Mac-wide takeover across projects has nowhere to reconcile.**
  - The lock records no workspace root (L1186-1193), yet case 4 tells the taking-over window to "commit on the holder's branch, checkpoint `interrupted`" (L1224).
  - From project X it can neither commit in project Y's repository nor write Y's workspace state. Clarvis → Codex offers exactly this takeover (L1154).
  - Y's own window, when it returns, is fenced (L1230). Its reload rule covers only "its own previous `windowId`" (L1242), so Y's uncommitted Codex changes and its `running` checkpoint are orphaned.
  - *Fix:* keep the holder's root in the lock (a local file; RAVIS still never serves it). A cross-project takeover only kills, confirms and releases. The fence applies only while a *live* holder of the **same checkout** is named, so Y reconciles when it returns.
- **F2 (Low).** L1228 says the new holder "commits them on the task's branch when it starts its own run". If that run is a new task, `AgentBranch.begin` treats the leftover files as the user's in-flight work (`noteTheirWork`) and branches from the base. *Fix:* a takeover continues the same task with `continueOn`, or commits the leftovers on the old branch first.
- **F3 (Low).** RAVIS judges only the extension-host holder. A `gone` holder whose recorded `codex.pid` (matching start time) is still shutting down reads `stale_lock` (L615). *Fix:* treat the lock as live while that pid exists.
- **F4 (Low).** The release rule keys on `checkpoint.transfer` (L1235), but checkpoint writes are asynchronous (§6.1). *Fix:* use the in-memory switch state.
- **F5 (Low).** The lock's `holder` fields assume an extension host (`extensionHostPid`, `windowId`), but the calibration harness also holds the lock (L1182, L1239). *Fix:* define the harness's values.

### 3. Is check 7 coherent?

**PARTLY.**
- **Contradiction (Medium).**
  - L593 makes an **accepted** version `proven` when check 7 passes and the surface hashes match, but L595 says "only a calibration re-run with K5 makes a version `proven` again".
  - Check 7 proves schema and profile syntax, not enforcement. The design's own rule is that "a new build can change behaviour that no schema shows: … sandbox" (L535), and acceptance "doesn't prove … the safety box behave as before" (L598). So L596's "an accepted update can never silently weaken the protection of key files" is not established.
  - *Fix:* add check 7(c), a model-free enforcement probe. `command/exec` is a **stable** method, and `CommandExecParams` has an experimental `permissionProfile` field (verified). Run it under `clarvis_run` to read a decoy in a denied path and write outside the root, expecting both to fail. Or reserve `proven` for K5 and call check 7's result `syntax_ok`.
- **Wording (Low).**
  - "The exact run flags" cannot be exact in RAVIS: the profile's project root and desktop `deniedPaths` live in Clarvis (L733-741). Say "the same syntax with representative paths".
  - In the combined bundle, most listed definitions sit under `definitions.v2`, but `AdditionalPermissionProfile` exists only at the top level and not in the v2 bundle (verified). Confirm it is the type that v2's `additionalPermissions` uses.
  - Add `ThreadResumeParams`: its experimental `permissions` field is used at L1166.

### 4. Does D4 carry the Homebrew caveat accurately?

**Mostly.**
- The header (L38) is exact: "installed … at 02:26 local by an unconfirmed party: no record of this work shows the install".
- The update behaviour (L1905, L475, L494), the three options, and the recommendation conditioned on the owner confirming it or agreeing to keep it (L1908) are accurate.
- **One overstatement:** L1902 says "Nothing in tonight's work installed it." The evidence is only that no record shows an install. *Fix:* "No record of tonight's work shows it being installed, and it isn't known whether you did."

### 5. A tool call already running in a taken-over Clarvis-engine window (R7, L1229, L1953, L2068)

**Not blocking, but a small design change is warranted rather than accepting it as is.**
- **When it can happen.** Takeover is offered only while the holder is parked on a question or `unresponsive` (L1223). A parked run has no tool running, because the step question comes before `dispatch` (`AgentRunner.ts:678`). So the gap arises only when:
  - an answer races the confirmation, or
  - an unresponsive extension host has already spawned a command.
- **How bad it is.** Edits (`applyEdit`, `writeFile`) finish at once. `runCommand` can run up to **10 minutes** (`commandTools.ts`, `COMMAND_TIMEOUT_MS = 10 * 60 * 1000`) and can write files throughout, while the new holder starts working in the same checkout. That breaks invariant 1 as worded (L1340).
- **The stated reason for leaving it doesn't hold.** The design says stopping it "would need control of another window's process, which this design deliberately doesn't have" (L2068). But the design already kills another window's **Codex** family by recorded pid and start time (L1224). Clarvis's commands are spawned `detached`, in their own process group, and are already killed by group on abort (`commandTools.ts`, `stopProcessGroup`).
- **Fix (small).** While `runCommand` runs, write `{pid, pgid, start}` into the workspace lock with the heartbeat. A takeover kills that group with the same start-time check and confirms exit before the new holder writes.
- **Minimum if not changed.** Invariant 1, the E-C9 exit and the takeover confirmation must say: "a command the other window already started may keep running for up to 10 minutes".

### Final verdict

**Nothing blocking. Ready for the owner once two short text fixes land:**
- check 7's `proven` meaning (item 3);
- D4's first sentence (item 4).

F1, the command recording in item 5, and the low items don't change any owner decision. Fold them into the design before C2a and C3 implement the locks.

---

## Review of architecture A (13 September 2026, `codex-design.md` dated 06:23, 1,980 lines)

Reviewed as a fresh design against the owner's decisions (`codex-design-constraints.md` L86-97), the handoff, the peer constraints, the protocol brief, the generated schemas and read-only source. Same rules as before: no edits, `~/.codex` not read, Codex not run, no network. Lines `Lnnn` refer to this document.

### Counts

| Severity | Count |
|---|---|
| Blocking | 1 |
| High | 5 |
| Medium | 12 |
| Low | 6 |
| **Total** | **24** |

### Facts checked for this review

- **The relay's NERVIS refusal can work as specified (CONFIRMED).**
  - The launcher stores NERVIS's and Clarvis's keys in RAVIS as `client.nervis` and `client.clarvis` (`tools/run.py:1018-1040`), and its own as `admin.launcher` (`:1006`).
  - `identity.py` derives the application id from the name after `client.` or `admin.` (L145-184), sets `may_write_configuration` for `admin.` (L184), and has an `is_anonymous` check (L110-122).
  - A legacy single `client_credential` resolves to application `configured` (L264). That is not in `agent_client_applications`, so it is refused too.
- **NERVIS's relay** (`nervis/src/nervis/peers/ravis.py:251-282`) only reads (GET), sends only `Authorization: Bearer <NERVIS's key>`, forwards no browser headers, and reads the whole body with a 5-second timeout. The explicit NERVIS refusal therefore holds for reads through it, and an SSE stream through NERVIS would only time out.
- **RAVIS internals (CONFIRMED):**
  - WebSockets bypass admission (`admission.py`: `scope["type"] != "http"`).
  - The runbook makes SSE canonical and requires `409 EVENT_CURSOR_EXPIRED` for a stale cursor (`ECOSYSTEM_RUNBOOK.md:165-169, 202-205`).
  - Seven migrations exist, so the next is 8. `ravis restore-database` exists (`cli.py:47, 63`). The retention timer is at `app.py:691-700`.
- **Protocol (schema):**
  - One stdio client can host many threads with per-thread `cwd`, `sandbox` and `config` (stable `thread/start`), and `cwd` and `sandboxPolicy` per turn (stable `turn/start`).
  - All five server requests require `threadId` (`CommandExecutionRequestApprovalParams`, `FileChangeRequestApprovalParams`, `PermissionsRequestApprovalParams`, `ToolRequestUserInputParams`, `McpServerElicitationRequestParams`). **CONFIRMED.**
  - `permissions` (a named profile id) and `runtimeWorkspaceRoots` are **experimental** fields on `thread/start`, `thread/resume` and `turn/start`.
  - `thread/backgroundTerminals/list|terminate|clean` take `threadId`, and `ThreadBackgroundTerminal.osPid` is **optional**.
  - `thread/archive`, `thread/unarchive`, `thread/delete` and `thread/unsubscribe` are stable.
  - `acceptForSession` is described as "future prompts in the same session-scoped approval cache should run without prompting".
- **Binary strings:** `shell_environment_policy` (24 hits), `default_permissions` (27), `project_roots` (18), "failed to locate archived thread id", and an `unarchive_thread` source path.

---

### Blocking

#### AB1. A RAVIS restart can overwrite a live window's checkout lock, giving two writers
- **Where:** §4.4 restart step 3 (L739): "RAVIS-held project locks are kept (state from the DB). Their checkout lock files are **rewritten** with RAVIS's new pid and start time." Against §6.3 (L1253-1254).
- **The sequence:**
  1. While RAVIS is down, its file holder is `gone`.
  2. A window kills the recorded Codex processes, reconciles, replaces the lock file with itself, and runs Clarvis's own engine.
  3. When RAVIS returns, a window's file lock is supposed to be "adopted".
  4. But the startup reconciliation, which runs **before routes serve** (L736), has already rewritten that file to name RAVIS. The DB still says the `uncertain` Codex session holds the project.
  5. Another window can then attach, settle or continue that session while the first window's engine is still writing.
- **Why it blocks:** it breaks invariant 1 (L1390) on a path the owner hits routinely, since the stack restarts after every source change.
- *Fix:* at startup, rewrite a lock file only if it still names the previous RAVIS pid and start time. If a live window holds it, mark the DB lock superseded, keep the session `uncertain`, and refuse settle and continue until that window releases and RAVIS adopts its lock.

---

### High

#### AH1. The fence treats "RAVIS unreachable" as "lock lost"
- **Where:** §6.3 (L1247). `stillHolds()` requires "the last heartbeat succeeded within 20 s, **and** a synchronous heartbeat succeeds right before a commit".
- **Problem:** any RAVIS restart or brief outage makes a Clarvis-engine run end with "Another window took over this task; I've left everything as it was", uncommitted. That contradicts "Clarvis's own engine runs on the file floor alone" (L1253) and §9's "Clarvis's own engine is unaffected" (L1553).
- *Fix:* only `409 LEASE_REVOKED`, or a lock file that no longer names this window, ends the run. When RAVIS is unreachable, fall back to checking the file, and re-register when RAVIS returns.

#### AH2. A closed code-server tab still counts as an attached editor
- **Where:** attachment is "an open stream with `window_id` … plus 60 s grace" (L476). But the stream is held by the **extension host**, which code-server keeps alive for 3 hours after the tab closes (L98, verified `108e5`).
- **Consequences:**
  - The detached 30-minute policy never fires; the attached 2-hour policy applies instead (L812-813).
  - The menu and card report an editor attached, and the parked session keeps counting toward the three-session limit.
  - Live step 6's expectation, "no editor open" within 10 s (L1821), will fail. §2.2 fact 6's "blocks nobody" is wrong for the policy and the limit.
- *Fix:* count a window as attached only while its Clarvis panel is visible or its browser client is connected, sent as a UI heartbeat. Close the stream when no client is connected.

#### AH3. "Don't ask again this run" outlives the run in a long-lived process
- **Where:** RAVIS still offers `forRun` (L403-405), which maps to Codex `acceptForSession` for commands and file changes and `scope:"session"` for permissions ("Allow for this task", L996).
- **Problem:** the schema describes a *session-scoped approval cache*. Under A, RAVIS stays subscribed to the thread until the session ends, so the cache lives across settle → `idle` → continue, carry on, detach and reattach. That could be days later, with no editor open. Under B, the process ending bounded it. K11 only "records" the scope (L1708).
- *Fix:* under A, don't offer `forRun` or `session` scope until K11 proves the grant ends with the turn. Or reset the thread (unsubscribe and resume from the rollout) at every settle.

#### AH4. Archiving on session end defeats the D3 resume path
- **Where:** a Codex → Clarvis switch ends the session (`DELETE`, L1178), and ending a session calls `thread/archive` (L887). A later Clarvis → Codex switch resumes `codexSession.threadId` (L1187).
- **Problem:**
  - The binary reports "failed to locate archived thread id" and has a separate `unarchive_thread` path.
  - `thread/unarchive` is stable but absent from `used_methods` (L638-641).
  - Live step 10 expects "Codex resumes its thread" (L1825).
  - The resume will likely fail and fall back to a fresh start, silently losing what D3 was chosen for.
- *Fix:* don't end or archive on a switch (settle `next:"transfer"` leaves the session `idle`), or unarchive before resuming. Pin `thread/unarchive`, and add a calibration item that resumes an archived thread.

#### AH5. The allowed roots include the ecosystem's own repositories and the coding folder itself
- **Where:** the default allowed root is `~/Documents/coding` (L338, L596). Only a root that *is* a `.run` folder is refused (L341). That folder contains `NERVIS-ecosystem` (with `.run/` tokens and RAVIS's source) and `clarvis`, and a root of `~/Documents/coding` itself is accepted.
- **Problem:** a session rooted there gets `write: project_roots` over paths the profile also denies, and K5's criteria (L1702) never test that **deny wins over write inside the root**. A session rooted at the coding folder can write into every project.
- *Fix:* refuse a root equal to an allowed-roots entry itself or containing any denied path (such as `.run`), unless the owner lists it explicitly. Add "deny beats write inside the root" to K5.

---

### Medium

- **AM1. The final check's item 3 was lost in the rewrite.**
  - L685-686 still makes an accepted version `proven` from surface hashes plus check 7, and claims "an accepted update can never silently weaken key-file protection". Schema equality does not prove sandbox behaviour.
  - "The exact run flags" (L681) remains, `AdditionalPermissionProfile` is still listed (L651), and `ThreadResumeParams` is still missing even though A resumes threads with the profile (L1187).
  - *Fix:* add a model-free check that runs a command (`command/exec`, a stable method with an experimental `permissionProfile` field) under `clarvis_run` against a decoy key file, or reserve `proven` for K5. Add `ThreadResumeParams`.
- **AM2. Calibration brings back two Codex processes on the real home.** `codex_calibrate.py` drives "the real Homebrew Codex directly through R2's client" (L1691) while RAVIS's child runs on the same home. That is the H3 refresh race A says it removed (L80, L1917). *Fix:* calibrate through RAVIS's own child (a dev-only admin route or CLI), or stop the child during calibration.
- **AM3. One pipe carries every project, and stdio writes block** when the queue is full (brief §1). A stalled RAVIS reader (SSE writes, DB writes, `ps`/`lsof` sampling on the event loop) stalls every project, and the health rule (L727) can then declare a crash. *Fix:* use a dedicated reader task that only parses and enqueues. Give each session a bounded queue that overflows into cursor expiry, never back into the pipe, and keep sampling off the event loop.
- **AM4. Per-thread roots for the profile rest on an assumption.** The design assumes `project_roots` means each thread's `cwd` (L826). The experimental `runtimeWorkspaceRoots` on `thread/start` and `turn/start` exists and isn't considered. *Fix:* set `runtimeWorkspaceRoots:[root]` explicitly per thread, pin it in `strict_rules_surface`, and test both in K5.
- **AM5. Attribution by working folder can hit the wrong project.**
  - A command that changes into another allowed project's folder is attributed to that session, so stopping B could kill A's process. The claim "another project's commands are never signalled" (L778) is overstated.
  - `osPid` is optional, so attribution via background terminals can come back empty.
  - *Fix:* treat a match on working folder alone as ambiguous (report it, never kill), prefer the sandbox-root, terminal and parent rules, and handle a missing `osPid`.
- **AM6. Two windows answering the same request collide on the idempotency key.** `Idempotency-Key = rid` (L984). A second window choosing a different decision gets `422 IDEMPOTENCY_KEY_REUSED`, not `409 REQUEST_ALREADY_RESOLVED`. The same decision replays a 200 as though it had answered. Clarvis's error handling (L985-988) covers neither. *Fix:* key on `rid:window_id`, or scope idempotency per window.
- **AM7. Stop versus an answer from another window isn't serialised.** RAVIS must set `stopping` before forwarding any answer. The design orders the steps (L1017-1020) but never specifies per-session serialisation, so window 2's accept can reach Codex after window 1's interrupt has arrived. *Fix:* put answer, interrupt, cancel and settle through one per-session lock, and test with concurrent POSTs.
- **AM8. Some retryable actions aren't idempotent.**
  - A retried `settle` after a lost response gets `409 CLAIM_INVALID` although the commit happened.
  - A retried `transfer` mints a second token.
  - A retried `reissue-token` invalidates the token just issued.
  - `takeover` has no key.
  - The handoff asks for idempotency wherever retries happen. *Fix:* require `Idempotency-Key` on `settle`, `transfer`, `takeover` and `reissue-token`.
- **AM9. "RAVIS never runs git" (L446) contradicts RAVIS writing the checkout lock at `$(git rev-parse --git-dir)`** (L1205, L739, L1190). A worktree's `.git` is a file that can point elsewhere, including somewhere the repository chooses. *Fix:* Clarvis sends the git dir at creation, and RAVIS validates it (the root's own `.git` directory, or a worktree gitdir under the main repository) without running git.
- **AM10. A detached task that asks nothing can't be stopped without opening an editor.**
  - There is no dashboard or menu Stop (L1328-1331), and the unanswered policy only fires on questions.
  - R2 (L1858) admits that in modes that don't ask, Codex keeps changing files while nobody watches.
  - §12 says no decisions remain, yet this is the owner's trade-off.
  - *Fix:* put it to the owner (stop from the menu or dashboard through a RAVIS admin route, versus editor-only), or pause a detached run that asks nothing after N minutes.
- **AM11. The final check's item 5 is still open.** A Clarvis-engine command already running at a takeover keeps going for up to 10 minutes (L1249, R8 L1877). *Fix:* send `running_command {pid, start}` in the lock heartbeat (L515), and have the takeover kill it by start time and confirm before the new holder writes.
- **AM12. Delta events can push the replay buffer past its limit within seconds.**
  - 2,000 events or 8 MB (L471) includes `agent.delta` and `command.output`, so a noisy build fills it in seconds.
  - A short reconnect then gets 409, and the snapshot has no item history (`STEP:` progress, file-change ledger lines).
  - *Fix:* evict deltas separately, keep every non-delta event for the session's life, or coalesce deltas.

### Low

- **AL1.** `GET /api/v1/codex` shows project names (basenames) to anonymous callers and NERVIS (L202, L235), while the RAVIS.md §9.7 amendment says "basename at most, on RAVIS's own screens" (L1473). *Fix:* align the wording, or return `runs` to named clients only.
- **AL2.** `mcpServer/elicitation/request` has no mapping under A (RequestView kinds L396-399; routing L720). *Fix:* RAVIS answers `decline` and emits `request.resolved {by:"policy"}`.
- **AL3.** The final check's F2 is still open: "the new holder commits them on the task branch when it starts" (L1241, L1249). A **new** task's `AgentBranch.begin` treats those files as the user's own work. *Fix:* a takeover continues the same task with `continueOn`, or commits the leftovers first.
- **AL4.** Sign-out is refused "while any session is live" (L294), and D3 keeps sessions `idle` indefinitely. *Fix:* "live" means an active turn or a pending settle.
- **AL5.** After a `brew upgrade`, no project can start a turn until every turn ends (L611, L744). One question left waiting (up to 2-3 hours) holds everyone. *Fix:* keep serving new turns on the running tested binary until the swap, or state the wait.
- **AL6.** The health probe is `model/list {limit:1}` (L725), which may reach the network. A network blip counts as unresponsiveness and can restart the child, losing a sign-in in progress. *Fix:* probe with a local call such as `thread/loaded/list`.

---

### Lens 1: requirements and decisions

| Item | Status | Note |
|---|---|---|
| **D1** RAVIS runs Codex, relays everything, keeps records, one writer, reattach | Covered, with holes | AB1, AH1, AH2 |
| **D2** strict file rules gated on calibration; back to the owner if they fail | Covered | L835-837, K5. AH5, AM1, AM4 |
| **D3** keep history in RAVIS's Codex folder (metadata-only exception) | **Partly** | Resume is defeated by archiving (AH4). 90-day deletion is listed as a default (L1845) |
| **D4** Homebrew stable via `brew --prefix`, schema-pinned | Covered | L36-38, L541, L604-607 |
| **D5** no terms gate | Covered | L1692, L1809 |
| Menu bar Codex line; dashboard card and Overview line with a gate | Covered | §7; the gate asserts no control (L1344) |
| Handoff: separate session records, token authority, root validation, idempotency, quota classes, `/v1` refusal, discovery never starts anything | Covered, with gaps | AM6, AM8, AM9 |
| Old clients refused before execution | Covered | Residual R11 |
| coding-87 (Stop releases questions; a late answer never starts a step) | Covered | Cross-window gaps AM6, AM7 |
| coding-6c (feedback reaches the engine) | Covered | Server-side steer queue (L1042) |
| Peer traps (RELEASES heading, eslint 15, message before edit or package, `nervis/tools` peer) | Covered | L1600, L904, L1605-1606, L1343 |

### Lens 2: relay security

- **Holds:**
  - Anonymous, admin, `nervis` and `launcher` are refused on every route before the token is checked, GETs included (L318-327). The source supports this identity split.
  - NERVIS's relay can't carry a token, and would be refused anyway.
  - A wrong token gives 404. Tokens are stored hashed and compared in constant time.
  - `allowed_decisions` is computed by RAVIS.
  - RAVIS never grants; the unanswered policy and secret questions only decline or leave answers empty (L406, L819).
- **Gaps:**
  - AH3: a remembered grant acts without a current editor's consent.
  - AH5: roots.
  - AM9: gitdir.
  - AM6 and AM8: idempotency.
  - AL1.
- **Disclosed and acceptable:** token files and the `client.clarvis` key are readable by any program running as the owner (L124, R4). Codex's commands are denied them only once K5 proves the profile.

### Lens 3: concurrency and failure

- **Shared process:** a crash marks sessions `uncertain` and kills recorded processes (L729-734). AM3 (backpressure), AM5 (attribution), AL6 (probe).
- **Central lock with the file floor:** AB1 (restart overwrite), AH1 (fence versus outage).
  - The sleep rule carries over correctly (L1231). A switch holds the lock through a transfer token that never releases on expiry (L530, L1196).
  - A taken-over holder never writes, except the running tool call (AM11).
- **Reattach and replay:** `409 EVENT_CURSOR_EXPIRED`, then snapshot, then `?after=` is coherent (L473). AM12 (overflow).
- **Unanswered policy:** 30 min / 2 h is well defined, but the attachment signal is wrong (AH2).
- **Stop racing an answer:** within one window this is covered (L983, L1017-1020); across windows AM7 applies.

### Lens 4: protocol

- **CONFIRMED:** many threads per stdio client with per-thread `cwd` and `sandbox`, and every server request carries `threadId`.
- **UNVERIFIED, and correctly gated by calibration:**
  - per-thread roots for the permission profile (AM4);
  - a per-thread `TMPDIR` via `shell_environment_policy` (K2b);
  - resuming an archived thread (AH4);
  - how long an approval cache lasts (AH3);
  - the `sandbox-exec` argument format (K6).

### Lens 5: earlier lessons under A

| Lessons | Status |
|---|---|
| B1, B2, B3, H1, H4-H6, H8, N1, N2, N6 | Kept |
| H2 | Changed, with AM5 |
| H3 | Resolved, except during calibration (AM2) |
| H7 | Changed, but reintroduced as AH2 |
| N3 | Kept, but broken by AH1 |
| N4 | Kept, but the final check's item 3 was lost (AM1) |
| N5 | Kept |
| Final check F1 and F3-F5 | Moot under A |
| Final check F2 | Still open (AL3) |
| Final check item 4 (D4 wording) | Resolved |
| Final check item 5 | Still open (AM11) |

### Lens 6: contradictions and build plan

- **Contradictions:**
  - "RAVIS never runs git" versus the lock file in the git dir (AM9).
  - The restart rewrite versus file adoption (AB1).
  - "Clarvis's own engine unaffected" versus the fence (AH1).
  - "A closed tab blocks nobody" versus attachment (AH2).
  - Archive on end versus resume (AH4).
- **Build plan:** contract-first fixtures let the Clarvis track proceed against `FakeRavisRelay` (L1592-1595).
  - Calibration comes before R4, yet uses checkout lock files (L1691). It needs its own minimal lock or R4's module.
  - AM2 applies to calibration.
  - Gates and file ownership are consistent with earlier passes. The owner's restart-after-source-changes rule will cut off running Codex tasks during R3 and R4; that is stated as R1.

### Lens 7: section 12

"None remain" is mostly defensible, since D1-D5 are made. Two items are genuinely the owner's call:
- **AM10:** no way to stop a detached task outside an editor.
- **AH5:** whether the ecosystem's own repositories, and the coding folder as a whole, are allowed Codex roots.

History deletion after 90 days goes beyond "keep" (D3), but it is listed as a changeable default (L1845). Leaving it there is acceptable.

### Verdict

**Not ready for the owner yet: one focused revision, no redesign.**
- AB1 must be fixed before sign-off.
- AH1-AH5 change behaviour the owner has already approved: that Clarvis's own engine keeps working without RAVIS, that a closed tab counts as no editor, that approvals stay scoped to their run, that the same Codex conversation resumes, and where Codex may work.
- The medium items can land in the same pass.
- AM10 and AH5 should go to the owner as two short questions.

---

## Final check of A (13 September 2026, `codex-design.md` dated 08:06, 2,232 lines)

Same rules: read-only, `~/.codex` not read, Codex not run, no network. Only the four questions asked were checked. `Lnnn` refers to this revision.

### 1. Fix verdicts

| Item | Verdict | Line(s) | Note |
|---|---|---|---|
| **AB1** Restart overwrite | **FIXED** | §4.4 L840-843; restart adoption rule §6.3 L1361-1370; §9 L1727; R4 test L1858 | Low gap F-A9 |
| **AH1** Fence versus outage | **FIXED** | L1059-1061, L1383-1386, §9 L1728 | |
| **AH2** Attachment | **FIXED** | Panel pings plus `presence` (L509-513, L921, L1216); live step 6 (L2015); R8 (L2080) | Low gap F-A8 |
| **AH3** No "don't ask again" | **FIXED** | L430, L446, L1105-1111, K11 L1897, §12 L2043 | |
| **AH4** Archive versus resume | **FIXED** | L996, L1301, L1310-1312; `thread/unarchive` pinned (§4.3 used methods); K13 L1899; live step 10 L2019 | **Introduces F-A1** |
| **AH5** Roots | **FIXED** | L343-367; K5c L1891; §12 L2042; launcher env L631 | Low gap F-A12 |
| **AM1** (restored) Accepted versions always unproven | **FIXED** | L776-778; surface adds `ThreadResumeParams`, `TurnStartParams`, `CommandExecParams` | The re-proof has its own issue (F-A3); "exact run flags" wording remains at L772 |
| **AM11** (restored) Running command at takeover | **PARTLY** | `running_command` in heartbeat and file, killed at takeover (L596, L1061, L1377-1390, R4 L1859) | Kills only the pid, not its group (F-A4) |
| AM2 Calibration in RAVIS's process | **FIXED** | L1876-1878, R2 L1789 | |
| AM3 I/O discipline | **FIXED** | L816-822 | |
| AM4 `runtimeWorkspaceRoots` | **FIXED** | L935, K5 L1889 | |
| AM5 Strong attribution only | **FIXED** | L859-887 | |
| AM6 Per-window answer keys | **FIXED** | L371, L1095 | |
| AM7 Action lock | **FIXED** | L457, L1130 | Lists differ (F-A7) |
| AM8 Idempotency on retries | **FIXED** | L370-372, L455, L598-599 | |
| AM9 `git_dir` validation | **FIXED** | L352-356, L1330 | |
| AM10 | **RESOLVED** | Owner decision (a): §3.5.5, §7.4 | |
| AM12 Split buffers | **FIXED** | L500-507 | Stale sizes remain elsewhere (F-A6) |
| AL1 | **FIXED** | L234-236 | |
| AL2 | **FIXED** | L814 | |
| AL3 | **FIXED** | L1377, L1391 | |
| AL4 | **FIXED** | L294 | |
| AL5 | **FIXED** | L848 | New state unlisted (F-A5) |
| AL6 | **FIXED** | L826 | |

### 2. Security of the stop-only route (§3.5.5 L532-574, §3.7 L620-624, §3.8 L648, §7.4 L1487-1502)

- **Does it do anything beyond stop? No.**
  - It sets `stopping` under the action lock, answers open requests only with stop responses (`cancel`, `{permissions:{}}`, empty answers), runs that task's end sequence, and leaves the session `stopped` (needing settle) or `leftover` (L550-554).
  - It cannot resume, answer affirmatively, settle, delete, change mode, read events or transcripts, or reissue a token (L542). A session-token header is refused with 400 (L541).
  - Calling it on `leftover`, `uncertain` or `starting` isn't refused (L558); it just re-runs the stop. That is harmless.
- **Is it the only session route accepting admin credentials? Yes, within `/api/v1/agent-sessions*` and `/api/v1/project-locks*`.**
  - Only `admin.` credentials whose application is in `agent_owner_stop_applications` (default `launcher`) are accepted. NERVIS gets there only by presenting `admin.launcher`, which the launcher hands it (`tools/run.py:400`). `client.nervis`, `client.clarvis` and anonymous get 403 (L538-540).
  - **The route-table test is real for those two prefixes:** it walks the app and asserts the dependency is present or absent (L537, R3 L1842), and identity tests (L1812, L1836-1841) back it up.
  - **But it doesn't cover admin routes elsewhere that drive Codex:** `POST /api/v1/codex/reprove` runs a model turn and is on the dashboard (L299-300, L647, L1467), and `POST /api/v1/codex/calibration/runs` exists when enabled (L1877). See F-A3.
- **Does the confirmation stop a stale menu from stopping the wrong task? Mostly.**
  - Folder name plus start time must match, else 409 (L547). That blocks a stop aimed at a different or later session, and a stop by id alone.
  - A session reused after a switch back (AH4) keeps its start time, so a stale entry can stop a later turn of that same task (F-A10).
- **What can a local program do?** It can read `.run/ravis-admin.token`, or take NERVIS's control token from `/index.html`, and **stop tasks**. With a named key it can read the ids and start times from `GET /api/v1/codex`.
  - This is disclosed as a denial of service only (L572-574, R4 L2070).
  - Not disclosed: the same key can exhaust the shared 10-per-minute limit that the owner's own menu and dashboard use (F-A11), and start re-proof turns that spend allowance (F-A3).
- **Is the dashboard path consistent with Lift? In shape, yes.** Compared with `lift_ravis_suppression` (`nervis/src/nervis/api/routes.py:563-591`), both:
  - use `dependencies=[Depends(require_control)]`;
  - validate the id before forwarding, with 400 on failure (`suppression_lift_path` versus the `as_[0-9A-Za-z]{10,40}` pattern);
  - call `ravis_peer.configure(…, ravis_admin_credential)`;
  - pass RAVIS's status and body back untouched.

  **But `configure(client, entry, method, path, credential, body)` has no headers parameter** (`peers/ravis.py:144-151`), while RAVIS requires `Idempotency-Key` on this route (L545). As written, the dashboard Stop gets `428 IDEMPOTENCY_KEY_REQUIRED` (F-A2).

### 3. New contradictions or gaps from the last two passes

- **F-A1 (High). A reused idle Codex session can start a turn without holding the project lock.**
  - After a switch, the Codex session "stays `idle`, without the lock" (L1301).
  - `POST …/turns` accepts an **optional** `lock:{transfer_token}` and lists no lock check among its errors (L443). `steer` on a session with no active turn queues text "and starts the next turn" (L1163).
  - A second window that attaches to that idle session could continue Codex while Clarvis's own engine holds the project. That breaks invariant 1 (L1547), and the contract fixtures (I0) would encode the omission.
  - *Fix:* `turns` (and any steer that would start a turn) must already hold the project lock, or take it atomically with a transfer token or from a free lock; otherwise 409 `PROJECT_LOCKED`. Add it to the I0 fixtures and an R3 test.
- **F-A2 (Medium). The dashboard Stop can't send `Idempotency-Key` through NERVIS's existing peer call.**
  - `ravis_peer.configure` has no headers parameter, and the NERVIS pytest (L1867) doesn't check that a key reaches RAVIS.
  - *Fix:* NERVIS mints the key, or forwards a validated one, through a new `headers` argument, and a test asserts RAVIS receives it.
- **F-A3 (Medium). The re-proof is admin-started Codex work outside the agent-session boundary.**
  - `POST /api/v1/codex/reprove` runs "K5b with one short turn" (L300, L777). NERVIS forwards it (L647), and the dashboard shows it (L1467). That contradicts "NERVIS never starts … a task" (runbook §2.2 L1535, `CLARVIS.md` §6.7 L1594).
  - Nothing says who answers K5b's approvals. K5's standard needs approved **and** escalated commands (L1889):
    - if RAVIS auto-accepts them, invariant 2's "RAVIS never approves" (L1548) is breached without a stated exception;
    - if nobody does, `proven` after a re-proof rests on weaker evidence than K5.
  - The route-table test doesn't list these admin routes.
  - *Fix:* write the re-proof down as a scoped exception (runtime setup, scratch projects, decoy commands only, audited) in invariant 2 and the NERVIS rows. Specify K5b's approvals. Extend the route test to every admin route that can start Codex work.
- **F-A4 (Medium). A takeover kills only `running_command.pid`.** Clarvis spawns commands `detached` in their own process group and stops them by group (`clarvis/src/agent/tools/commandTools.ts`, `stopProcessGroup`), so killing the pid can leave children writing (`npm test` workers, for example). *Fix:* record the pgid (equal to the pid for a detached spawn) and kill the group, after checking the leader's start time.
- **F-A5 (Low).** The new state `paused_for_update` (L848) is missing from the session state table (L395-410), the `runs[].state` list (L237) and the menu rows (L1436-1444). *Fix:* add it, or reuse `paused_unanswered` with a reason.
- **F-A6 (Low).** Old buffer sizes remain in §4.10's metadata-only exception (L1003) and the `RAVIS.md` §11 amendment (L1636): "2,000 events or 8 MB". The non-delta buffer now keeps content for the session's life, up to 20,000 events or 32 MB (L503), so the stated exception understates what RAVIS holds in memory. *Fix:* update both places.
- **F-A7 (Low).** The serialised routes differ between L457 (which includes `leftover`, `DELETE` and `owner-stop`) and L1130 (which omits them). *Fix:* point L1130 at L457.
- **F-A8 (Low).** When a code-server tab reopens (same extension host, no reactivation), the host must reopen the stream as soon as the panel's pings resume. Only closing is specified (L511-512), yet live step 8 relies on reopening. *Fix:* specify it and test it in C2a.
- **F-A9 (Low). A session can stay "superseded" with no editor able to clear it.** This happens when the window named in the checkout lock file is `gone`. The reattach flow (§5.7) doesn't reconcile that holder, so the card keeps saying "another editor is working on this project" (L1727) when none is. *Fix:* on reattach, reconcile a `gone` file holder, take the file, have RAVIS adopt it, then settle.
- **F-A10 (Low).** The owner Stop confirmation uses the session start time (L547), which a reused session keeps. *Fix:* confirm the current turn's start time or id.
- **F-A11 (Low).** The owner Stop rate limit (10 a minute per application, L561) is shared by the menu bar and NERVIS (both `launcher`). Audit can't tell them apart except by the self-declared `source`. *Fix:* state it, or give NERVIS its own admin application for this route.
- **F-A12 (Low).** `agent_protected_repositories` has no default when RAVIS starts without the launcher (L348, L631), so the protection lapses. *Fix:* default to RAVIS's own checkout and its sibling `clarvis`.

### 4. Ready?

**Yes for the owner to approve:**
- architecture A, the five decisions and both follow-up answers are reflected;
- AB1 and AH1-AH5 are fixed;
- the stop-only route cannot approve, answer, start, steer, settle, delete or reissue anything.

**Building can start with Increment 0, provided its contract fixtures carry three patches:**
- F-A1: the lock check on `turns` and `steer`;
- F-A2: the idempotency header through NERVIS;
- F-A3: the re-proof boundary and who answers its approvals.

F-A4 and the low items can land in R3, R4, N2 and C2a as they're built.
