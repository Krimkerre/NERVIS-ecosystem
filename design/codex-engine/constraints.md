> **Working design record, 13 September 2026.** Kept so the Codex engine build (RAVIS M29, Clarvis E-C9,
> NERVIS M28) can be continued by any session. It is not the contract: the canonical text is
> `ECOSYSTEM_RUNBOOK.md` §2.2, `RAVIS.md` §15.1.2, `CLARVIS.md` §5.5 and `NERVIS.md`, and where they
> differ they win. Retire this folder when those milestones close.

# Codex engine — constraints gathered before design

## Owner's decisions (13 Sep 2026)
- ~~Runtime~~ (superseded by D4 below — the Homebrew stable Codex): `/Applications/ChatGPT.app/Contents/Resources/codex` (codex-cli 0.153.4, ChatGPT.app 26.903.61454).
  No separate install. It updates with the ChatGPT app → RAVIS checks the version at start and reports an
  untested version plainly instead of guessing.
- Sign-in: RAVIS's Codex uses its own CODEX_HOME and its own ChatGPT sign-in. Never read or use `~/.codex`
  (the ChatGPT app's Codex home, signed in with ChatGPT).
- Scope of first delivery: Codex selectable as the coding engine in Clarvis (progress, approvals, questions,
  stop, steering) PLUS manual switching of an unfinished task Codex → Clarvis/RAVIS engine and back, one
  writer at a time. Automatic quota-triggered switching is later; paid fallback never silent.

## Owner's added requirement (13 Sep 2026) — Codex in the menu bar app
- The NERVIS menu bar app's "Models" section (today: Ollama, LM Studio) gains a **Codex** entry showing the
  **remaining usage** of the ChatGPT subscription when RAVIS's Codex is signed in with ChatGPT.
- The menu reads only `tools/run.py status --json`, so the launcher must get Codex's state from RAVIS (the
  broker owns the Codex home and runtime) — never by reading `~/.codex` or starting its own Codex.
- States to show honestly: not installed / untested version / signed out / signed in with remaining usage per
  window and when it resets / usage unknown. Reading usage must not start a model call; the menu is read
  every few seconds, so RAVIS serves a cached figure (from Codex's rate-limit notifications and occasional
  reads) rather than asking Codex per menu open.
- **Also on the NERVIS dashboard** (asked right after): the same Codex state and remaining usage, read from
  the same RAVIS endpoint through NERVIS's relay (the page reads RAVIS via `/api/v1/relay/ravis/...`). Keep
  subscription usage visibly separate from RAVIS's estimated API spend — it is an allowance, not a $0 cost.
  Pick the screen in the design (RAVIS's screens are the natural home since RAVIS brokers Codex; consider a
  line on NERVIS Overview too) and wire it to the real endpoint, with a dashboard gate.

## From the handoff (`~/Downloads/claude-handoff-ravis-codex-pool.md`)
- Selectable id `ravis/codex`; RAVIS brokers official `codex app-server`; Clarvis owns task, interaction,
  requirements and the transfer checkpoint; RAVIS relays permission requests and never grants them.
- Never in a fallback chain; old clients sending `ravis/codex` to `/v1/chat/completions` get an explicit
  unsupported error before anything executes; never fake tool-call compatibility to pass the probe.
- Discovery stays fast and cached: listing never triggers sign-in, launch, paid call or remote wait. Both
  single-upstream and merged catalog paths. Runtime missing / signed out / unavailable / quota exhausted
  distinguishable.
- Separate agent-session records (not `RoutingSession`); owner, workspace, Clarvis task id, native thread,
  active turn, model, state, pending requests; thread ids are not authorization; one writer per task.
- Quota exhaustion distinct from throttling, invalid credentials, model unavailable, runtime failure,
  connection loss; don't infer quota from every 429; subscription usage is not a $0 API completion.
- Chat, planning, interview stay on their providers; unsupported roles return a clear message without
  starting an agent or billing a fallback.
- Checkpoint lives in Clarvis task state, not NERVIS's 2,000-char brief. Transfer: stop new work, settle or
  interrupt, reconcile effects, save checkpoint, start destination under its own permissions; don't replay
  uncertain commands; tell a resumed Codex thread what the other engine changed.
- Exclusions: no token proxy, no browser scraping, no Responses endpoint, no auto credit purchase, no mixed
  API/Codex retry chain, no replacement of Clarvis's runner or code-server, no NERVIS approval/remote exec.

## From peer session coding-6c (13 Sep 2026)
- Mid-run user interjections must reach the engine. Clarvis's AgentRunner pushes
  `{ role: 'user', content: redirect, toolResults }` — the interjection rides as `content` on the tool-result
  turn. Until Clarvis 0.14.1 both serialisers dropped it (redirects logged, never reached the model).
  A Codex engine needs the equivalent (turn steering, or queued into the next turn) or it silently
  reintroduces that bug. Reference shapes: `clarvis/src/model/toolResultTurn.test.ts` (empty content adds
  nothing).
- A session may be working in `NERVIS-ecosystem/nervis/tools` (shaping_check.js task flagged 11 Sep).

## From peer session coding-87 (13 Sep 2026) — Stop semantics
- Stop must release any question a run is waiting on: `RunSession.stopWaiting()` cancels the pending
  question and `ChatService.stop()` calls it.
- An answer arriving after Stop must not start the step: AgentRunner's `runCalls` checks
  `stepAfterAsking(approved, signal.aborted)` after the step question.
- Decisions are pure functions in `src/chat/stopDecision.ts` (tested in `stopDecision.test.ts`); 0.16.0 added
  planning pause there.
- If the Codex engine adds its own approval waits or streams steps from RAVIS, Stop must reach those too,
  or the old bug returns: buttons stay on screen and the stop lands only once someone answers.

## From peer session nervis-ecosystem-fc (13 Sep 2026) — Clarvis 0.16.0 changes and traps
- Clarvis HEAD 90df7be (0.16.0). Planning split: everything after the interview moved from
  `src/planning/PlanningFlow.ts` to `src/planning/planReview.ts` (no vscode import; tested in
  `planReview.test.ts`); PlanningLines, StartBuild, InterviewMemory moved there. PlanningIO gained
  `readDocument()` and a `PlanningPaused` error (a stop throws it out of any question).
  `InterviewMemory.save()`/`InterviewSnapshot` take an optional `draft`. Analysis.ts: `AnalysisResult.problem`,
  `PlannedMilestones`, new `revisePlan()`. Milestone one's build task comes from `nextMilestoneTask` (read from
  the written plan.md); `handoffTask` only for the no-plan case. ChatService: `stopFromChat`/`stopReply` can
  return 'paused'; interview offer armed for the whole planning session; `PlanningChatIO` takes the draft.
- Not touched by them: ModelService, model pickers, RunSession, AgentRunner, run ledger; nothing in ravis/,
  RAVIS.md, CLARVIS.md, ECOSYSTEM_RUNBOOK.md, STATUS.md.
- Trap: bumping Clarvis past 0.16.0 needs `## Clarvis — <new>` in RELEASES.md with 0.16.0 moved to
  `### 0.16.0`, or `tools/check_releases.py` fails.
- Trap: Clarvis eslint complexity limit is 15; `Interview.continueInterview` and `PlanWriter.renderPlan` are
  at 15, `Interview.resolveProjectName` at 14.
- Their live walkthrough is not running or scheduled (waits on the owner); Clarvis work need not wait for it.
  Message nervis-ecosystem-fc before the first Clarvis edit and before any package/install; they will
  message before touching Clarvis if the walkthrough starts or needs a fix.

## Owner's design decisions (13 Sep 2026, ~05:00) — these override the design's recommendations
- **D1 — RAVIS runs Codex** (architecture A, the handoff's broker shape), NOT the recommended Clarvis-hosted
  shape. RAVIS owns the app-server process, relays steps, events, approvals, questions, steer and stop to
  Clarvis; keeps agent-session records; enforces one writer per project; tasks outlive a closed or reloaded
  window and are reattached. Do not re-argue B.
- **D2 — stricter file rules:** Codex permission profile denying key and password files, switched on only if
  calibration proves the rules hold; if they don't, the question returns to the owner.
- **D3 — keep Codex's conversation history** in RAVIS's own Codex folder (an exception to RAVIS's
  metadata-only rule for that folder).
- **D4 — the Homebrew stable Codex** (cask 0.154.0, installed 02:26 13 Sep; the owner says it's theirs or
  to keep it), resolved through `brew --prefix`, pinned by schema hash; replaces the ChatGPT-app copy.
- **D5 — go ahead without checking OpenAI's terms.** Remove any terms-check gate from the build and live test.
- **Stop button (asked after the A review):** yes — a running Codex task can be stopped from the menu bar and
  the dashboard; stop only, never approve, answer, start or steer.
- **Ecosystem's own repositories:** refused by default (NERVIS-ecosystem, clarvis, the coding folder itself);
  the owner can allow a folder later by listing it.

## From nervis-ecosystem-fc (13 Sep 2026, build start) — the planning build handoff contract a remote runner must keep
- startPlanning's startBuild callback calls `setFromPlan(true, steps)` then `runs.run(task)`; steps are the
  milestone-1 step texts from the written plan.md (`planUpdate.milestoneChecklist`); task is `nextMilestoneTask`,
  which tells the agent to output `STEP: <the step, copied from the plan>` before each step and to tick steps in plan.md.
- RunSession matches those STEP lines against the steps to drive the progress bar; `recordMilestone` ticks plan.md
  lines when the run settles.
- A Codex runner must pass STEP lines back the same way (from Codex agent-message text), or progress and ticking break
  silently. The per-step approval gate (`setStepApproval` / `asksFirst`) and Stop (`busy.stop` + `runs.stopWaiting`)
  sit on the same seam.
