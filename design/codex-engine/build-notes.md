# Codex build — notes handed between increments

> Working record beside `design.md`: what each landed increment told the next ones. Overridden by the canonical documents.

## Rename: `ravis/codex` → `ravis/clarvis-codex` (13 Sep 2026; RAVIS 0.23.11, NERVIS 0.28.15, Clarvis 0.16.1)
- Owner decision: the Codex engine's model id matches the Clarvis pools, `ravis/clarvis-agent` and `ravis/clarvis-chat`.
  No alias for the old id; nothing installed depends on it for a running task.
- Renamed: RAVIS's `CODEX_BACKEND_ID` and `codex/state.py`'s `BACKEND_ID`, the listing, the 400 refusal (message and
  the `ravis/clarvis-codex/…` prefix), conformance's 24th check, `ravis.codex_runtime@1`'s `backend_id`, the
  relay-contract fixtures and `codex-contract.sha256`; the dashboard's gate fixture and the knowledge files. Clarvis's
  constant, fixtures copy, tests, `plan.md` M15 and `docs/CURRENT_STATE.md` follow in Clarvis 0.16.1.
- Unchanged: package and folder paths (`ravis.codex`, `ravis/src/ravis/codex/`, `src/engine/codex/`), the
  `X-Clarvis-Engines: codex` header value, capability names, error codes and route paths — NERVIS's
  `/api/v1/ravis/codex/…` control routes included. The notes below keep the old id where they record what was built
  and verified then; `design.md`, `constraints.md` and `review.md` keep it too.
- **Not live until restarts and a reinstall:** RAVIS lists only the new id after its next restart. The Clarvis 0.16.0
  installed in code-server still uses `ravis/codex` until 0.16.1 is packaged and installed; meanwhile its old id
  is neither listed nor refused by a restarted RAVIS, so choosing Codex there won't work.
- Same commit: RAVIS → Pools gained a read-only Clarvis Codex row (the owner looked for Codex there), composed in
  `nervis/index.html` from `GET /api/v1/codex` with the Codex tile's helpers; `nervis/tools/codex_check.js` asserts it.

## From C1 (clarvis 9274815, 13 Sep 2026)
For C2a (Clarvis runner):
- An idempotency key belongs to one attempt: a new settle claim needs a new settle key.
- Keep reading the event stream while waiting on a person; blocking inside the loop trips the 45 s silence check.
- Store the stream cursor per host.
- A process-probe result of `undefined` means "don't judge".
- Taking over a `gone` holder's lock file is left to C2a.
- The fake relay keeps only identity, tokens, keys, leases and streams; the session state machine is C2a's to add.
Fixture ambiguities C1 resolved by following the fixtures:
- Cursor-expiry recovery = session read, then `?after=`; the design's "last 200 completed items" in a snapshot has no
  field, so a recovering window can't rebuild progress (decide in R3/C2a).
- `deltas_skipped` has no example; unknown events pass through untouched.
- Idempotency replay of errors unspecified; the fake replays successes only.
- 429 listed only for the owner Stop route; any 429 is treated as `throttled`.
- Event-id monotonicity across a RAVIS restart unspecified.
- Lock-file heartbeat rewrite format unspecified.

For R2/R4 (RAVIS):
- `ps -o lstart=` output is locale-dependent (a Dutch locale printed "zo 13 sep."): run with `LC_ALL=C` and the same
  time zone, compare with whitespace collapsed, or a live holder looks gone.
- Keep event ids rising across restarts, or Clarvis drops new events as duplicates.
- Accept both `Last-Event-ID` and `?after=`.
- Report an exhausted allowance as 409 `CODEX_NOT_READY`, never 429.
- Allow trailing spaces in the lock file; treat an unreadable one as unknown, not lost.

## From R1 (ecosystem 2ed38a4, 13 Sep 2026)
For R2:
- The runtime report's `state` is `None` when the check passes; R2's state rows start at `runtime_down`.
- The verdict is tested/untested only; the accepted-versions list is R2's.
- Not built: the 60-second re-check, `running_sha256`, a sweep of leftover scratch folders (a `kill -9` mid-check
  leaves one behind); the pin file lacks used methods, the strict-rules surface and definition hashes;
  `codex-code-mode-host` and `codex-path/rg` in the cask are not pinned.
- Conformance count: the refusal fixture and the M29 row say 24 once the refusal lands; R1 kept 23 — reconcile.
- The design's R1 row also lists both capabilities, the acceptance checks and `ravis codex preflight`: not built.
- Live-verified after restart (13 Sep 11:12): runtime check pinned Homebrew 0.154.0 (file rules unproven → paused);
  `/v1/models` lists `ravis/codex` only with `X-Clarvis-Engines: codex`; chat completions for `ravis/codex` and
  `ravis/codex/anything` → 400 `agent_backend_not_a_chat_model`; `~/.local/share/ravis-codex-scratch` created, empty.

## From N2's first part (ecosystem e173c9a, b3a15d4 and the compact tile; NERVIS 0.28.12–0.28.14, 13 Sep 2026)
For N2 (the rest):
- Still to build: the dashboard's task **Stop**. It needs R3's task list (`runs` with `id` and `turn_id` for named
  callers), the keyword-only `headers` argument on `ravis_peer.configure` (F-A2, for the page's `Idempotency-Key`)
  and the NERVIS route `POST /api/v1/ravis/codex/runs/{sid}/stop`. Also unbuilt: the version check and accept routes
  and buttons, and the task table.
- Built: the sign-in on RAVIS → Credentials (five control routes, including a gated `GET` of the waiting sign-in),
  the Codex allowance tile beside Spend in place of the Active profile tile (compact, details in a tooltip) and the
  Overview line; all gated by `nervis/tools/codex_check.js`. `configure` gained a keyword-only `timeout`, not
  `headers`.

## From Cal (ecosystem, RAVIS 0.23.10, 13 Sep 2026) — the harness is built; the real run is not
How to start the real run (owner present, after the stack runs this build):
- Stop the stack and start it with `RAVIS_CODEX_CALIBRATION=1` in RAVIS's environment (the launcher
  never sets it). RAVIS's Codex then starts with the profile under test: the pinned one, else
  `plan.CANDIDATE_PROFILE`, else a JSON file named in `RAVIS_CODEX_CALIBRATION_PROFILE`. The
  sign-in survives the restart (file store).
- `tools/run.py codex calibrate --project-a "<coding>/NERVIS workspace/clarvis/nervis-tasks/codex-calibration-a"
  --project-b "<…>/codex-calibration-b" --prepare`. `--prepare` makes each a git project with a
  base commit; RAVIS makes the decoys and markers at the start and removes them. It asks for the
  allowance go-ahead, prints each question, ends with the result and the outputs folder. `--only
  K10,K5a` runs the model-free pair first, without the go-ahead.
- Afterwards restart the stack without the variable. On a full pass the checkout's
  `ravis/src/ravis/codex/tested_runtimes.json` has been rewritten (profile, `strict_rules_proven`):
  commit it with `ravis/tests/fixtures/codex/calibration/<version>-<run>/`, and write STATUS's
  record from `summary.json` → `status_record`.
Owner present for: the go-ahead; every question but K10 and K5a runs model turns; K3 lets Codex's
approved command reach `example.com`; K9 makes an empty commit in project A; K6 starts `sleep 600`,
`script -q /dev/null sleep 600` and `python3 -m http.server` in both projects and stops them.
What the real run must confirm (the fake follows guesses from the schema and Codex's strings):
- The candidate profile's TOML. A rejection fails K5 in Codex's words (`supervisor.start_error`);
  fix it in a profile file and run again.
- Approval `command`: plain or `bash -lc`-wrapped (unwrapped, recorded as `wrapped_commands_seen`).
- A network approval arrives as a command approval with `networkApprovalContext` (K3).
- `config/read` shows `features.plugins` or its `sessionFlags` origin (K10); `config/read` isn't in
  the pin's used methods.
- `thread/backgroundTerminals/list` gives `osPid`s, and the `WRITABLE_ROOT…=` argument format (K6).
- `serverRequest/resolved` follows each answer and an interrupt's open request (K7, K12).
- `thread/archive` needs a non-ephemeral thread; `thread/resume` takes `permissions` and roots
  (K13); `turn/steer` takes `expectedTurnId` (K11); how granular `sandbox_approval:false` behaves (K4).
Not built: the fake replaying calibration's transcripts; definition hashes for a calibrated build
that isn't pinned (its new entry gets `definitions: null`); the `cwd_only` attribution rule (never
used for kills); K11's settle stage (live test); a run surviving a RAVIS restart (it ends; decoys are
swept at the next start and the next run adopts the lock files).
For R3/R4: `codex/lock_rule.py`, `codex/lock_file.py` and `codex/process_table.py` (zombies are not
survivors) are ready to reuse; `agent_protected_repositories` is now a RAVIS setting.
For C2b: the modes' approval settings come from `summary.json` → `mode_mapping`.

## From R2 (ecosystem, RAVIS 0.23.9, 13 Sep 2026)
For Cal:
- Build the dev-only calibration route first (`POST /api/v1/codex/calibration/runs`, only while
  `RAVIS_CODEX_CALIBRATION=1`, `require_owner_cli`): not built in R2. `reprove.py`'s harness is the
  shape to reuse (threads it creates, a fixed list per scenario, answers audited).
- Write the `clarvis_run` flags into `tested_runtimes.json` → `file_rules_profile`
  (`{"name", "flags"}`; placeholders `{user_home}`, `{ravis_config}`, `{codex_home}`,
  `{reproof_decoys}`). While it is null the process starts without a profile, check 7b can't run,
  and reprove refuses with 409 `CODEX_NOT_READY`.
- Today's pin (tested, unproven) reads `untested_version`, but the process runs, so sign-in works.
  Only a build in neither list gets no process.
- Confirm on real readings: `resetsAt` is whole seconds (pinned in `test_codex_usage.py`); approval
  `command` is the plain text the prompt gave (the harness matches exactly — a `bash -lc` wrapper
  makes every re-test inconclusive); how escalation is asked; `features.plugins=false` holds (K10);
  `thread/start` takes `permissions`, `runtimeWorkspaceRoots` and `ephemeral` together.
- A new tested entry's hashes: `schema_report.definition_record(read_bundle(<experimental tree>),
  used_surface(read_pin()), codex_version=…, experimental_tree=…)`.
For N2:
- Control routes forward with the admin credential (`ravis_peer.configure`): `POST
  /api/v1/codex/sign-in {"method":"browser"}` → 202 started / 200 already waiting, both
  `{"sign_in": {state, auth_url, callback_port, started_at, expires_at}}`; `GET` the same route to
  reopen the page (§3.8's table has no control route for it; re-POSTing also returns it);
  `DELETE` → `{"cancelled": bool}`; `POST /sign-out {}` and `POST /account/confirm {"email_hint":
  <the hint shown, or null>}` → the full state; version-check, accept-version, revoke as fixed.
- Render from `GET /api/v1/codex` (GET relay): `state`, `reason`, `account.email_hint` (never an
  email or token), `sign_in {state: idle|waiting_for_browser|failed, started_at, expires_at,
  error}` (no address), `usage.known:false` → no bar. `runs` is `[]` until R3.
- New catalogued codes: `SIGN_IN_METHOD_NOT_SUPPORTED` (422), `INVALID_REQUEST_BODY` (422).
For N1b:
- `codex sign-in`, `cancel-sign-in` and `status --json` now answer. Re-test refusals the launcher
  doesn't map come as 409 `CODEX_NOT_READY` (not calibrated, signed out, allowance used up) → exit
  1 with RAVIS's sentence: show it.
For R3:
- Hold a thread with `MessageRouter.hold(thread_id, Inbox)`; requests for an unheld thread are
  refused `-32601`. `ActiveTurns` counts turns from Codex's notifications.
- Add `runs` and its named-caller filter, `CODEX_NOT_READY {details.reason}` on session create
  (an exhausted allowance is 409, never 429), and put `relay` back into `ravis.codex_runtime@1`'s
  constraints when declaring `ravis.agent_sessions@1`.
- On Python 3.11 `asyncio.wait_for` swallowed a cancel and hung `Connection.stop()`: use
  `asyncio.timeout`, as all of `ravis/codex/` now does.

## From nervis-ecosystem-fc (13 Sep 2026, before C2a) — ChatService Stop wiring (untested in 0.16.0)
- `stop()` = `busy.stop()` → `runs.stopWaiting()` → `planningIO?.cancel()`; keep the planning cancel and the order.
- `stopFromChat()` passes `planning: Boolean(this.planningIO)` to `stopReply`; `'paused'` wins (PLANNING_PAUSED_LINE
  instead of "Stopped."); `'silent'` still applies while a run is going.
- `planningTook` arms the interview offer whenever `planningIO` exists; a typed "stop" during planning goes through
  `interviewTook` → `stopFromChat`.
- `startBuild` clears `planningIO` before the run, so a Stop during a build never reaches planning.
- Only pure halves are tested (`stopDecision.test.ts`, `PlanningChatIO.test.ts`); any change to `stop()`/`stopFromChat()`
  must prove planning still pauses on a typed stop.

## Owner requirement added 13 Sep 2026 (during the build) — sign-in button on RAVIS's credentials screen
- The NERVIS dashboard's RAVIS credentials screen (where provider API keys are saved) gets a button to sign in to
  the ChatGPT subscription for RAVIS's Codex, alongside the sign-in on the menu bar and the Codex card.
- For R2: the sign-in start / status / cancel (and sign-out) routes must be reachable from that screen the way
  credential writes already are — through a NERVIS control-token route forwarding NERVIS's admin credential — and
  the screen must show the sign-in state (signed out / signing in with the browser link / signed in as which
  account / expired), never a token.
- For N2: build the button and state on the credentials screen (plus the planned Codex card and Overview line),
  wired to the real routes, with a dashboard gate case covering each state and escaping.

## From N1a (ecosystem 93831f8, 13 Sep 2026)
For N1b (menu bar):
- `status --json` `codex` can be null (RAVIS up, no Codex state served yet): draw no line then. With RAVIS down it is
  `{"state":"ravis_not_answering", "usage_known":false, "windows":[], "runs":null, …}` — `runs` null, never "no tasks".
- Offer Stop only for a run that has an `id` (Clarvis-engine runs have none).
- `run.py codex …` exit codes: 1 = an answer the contract doesn't name (incl. "this RAVIS doesn't offer that yet"),
  2 = bad arguments, 10 = a refusal. `reprove` can take up to 6 minutes — run it in the background.
For R2:
- The launcher treats a 404 from the Codex state route as "no Codex state", uses a 1.5 s timeout, matches refusals on
  error codes; sign-in posts `{"method":"browser"}` and opens the page only for an `https` address; the re-test is
  polled until it reports `"finished"` with a result.
- `admin.owner_cli` (minted and taught to RAVIS at `start`; never in a service's environment) must be recognised by the
  `owner_cli` application name. R3 must declare the agent folder settings as JSON lists.
- Not passed by the launcher: the Codex home (RAVIS's default is the designed folder and RAVIS owns it).
