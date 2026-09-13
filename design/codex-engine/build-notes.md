# Codex build — notes handed between increments

> Working record beside `design.md`: what each landed increment told the next ones. Overridden by the canonical documents.

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
