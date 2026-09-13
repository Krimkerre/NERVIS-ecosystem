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
