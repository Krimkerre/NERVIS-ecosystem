# Codex build — notes handed between increments

> Working record beside `design.md`: what each landed increment told the next ones. Overridden by the canonical documents.

## From N2b and N1b (ecosystem, NERVIS 0.29.0, 14 September 2026) — the Codex card, the version routes and the menu bar's Codex line
Built against RAVIS 0.25.1 (R5b), whose `runs[]` carry `model`, `effort` and `reopening` because NERVIS
asked for them: every agent-session read refuses NERVIS.
- **Built — NERVIS's routes** (`nervis/src/nervis/api/routes.py`): four control routes behind
  `require_control`, each through `_codex_control` with NERVIS's RAVIS admin credential and `no-store`.
  `POST /api/v1/ravis/codex/runs/{sid}/stop` → `owner-stop`: `sid` held to `as_[0-9A-Za-z]{10,40}` and
  `Idempotency-Key` to `[A-Za-z0-9_-]{16,128}`, each else 400 without forwarding, in the contract's words;
  the body built from the page's `project` and `turn_id` only, with `source: dashboard`; the page's key
  forwarded. `DELETE /api/v1/ravis/codex/sites/{host}`: a host name's shape (labels of letters, digits and
  inner hyphens, at most 253), else 400. `GET /api/v1/ravis/codex/version-check` and
  `POST /api/v1/ravis/codex/accept-version`, waiting 210 s (`CODEX_VERSION_TIMEOUT_SECONDS`), since RAVIS
  may run the checks then: three runs of the build at 30 s and two throwaway starts at 15 s a call.
  `ravis_peer.configure` gained keyword-only `headers`; a key named `authorization` in any case raises
  `ValueError` before anything is sent, and `_credential_call` writes the credential after them anyway.
- **Built — the Codex card** (`nervis/index.html`: `codexCard`, `CODEX_CARD`) under RAVIS → Dashboard's
  headline tiles. Tasks: every `run_states` word said in words, the wait, `model · effort` (a null is
  Codex's default; a RAVIS that sends neither draws nothing), *reconnecting* with its hosts, editors, and
  **Stop…**. Allowed sites: `API.ravis.codexSites()`, a plain fetch outside `live()`, so its 503 while
  Codex isn't running never sets `OUTCOME.ravis` for the other RAVIS cards; **Remove** beside `added`
  only; the defaults in a closed `<details>`. Version: the build's words, **Check this version** → each
  check worded and what changed → **Use this version…**. The Overview's line counts the tasks, and
  `codexOpenCard()` opens the screen and scrolls to the card. The tile and the Credentials card are
  unchanged.
- **Built — the launcher** (`tools/run.py`): `CODEX_RUN_FIELDS` gained `model`, `effort` and
  `reopening` (cut to `{since}`: the menu prints no hosts); the block gained `signed_in` and
  `runtime {version, verdict, strict_rules}`; all None when RAVIS couldn't be asked.
- **Built — the menu** (`nervis/packaging/macos/NERVISMenu.swift`): `StackReport.Codex`, decoded with
  `try?` so an entry the app can't read costs the Codex line only; `CodexLine` (words, tone, offers) and
  `CodexWords` (dialogs and exit codes). The row opens `address` and is never red; a line per task
  holds that task's submenu (Open the Codex card, model and effort, reconnecting, then **Stop this
  task…** or why there is nothing to stop); the allowance per window; **Re-test the file rules…** for
  `verdict: accepted` with unproven rules, and otherwise, for `untested`, **Accept Codex … on the Codex
  card first…**; **Sign in to Codex…** / **Cancel the Codex sign-in**. Every completion goes through
  `launcher.capture`, which hands back on the run loop, never `DispatchQueue.main`.
- **Decided here:**
  - Stop, Remove and Use this version take two clicks (the page's `armed` rule), not `confirm()`: the
    lead agreed, since `codex_check.js` already failed any `confirm()` on the Codex card. The armed
    button names the folder. The Idempotency-Key is kept per `id|turn|folder` for the page's life, so a
    retry after a lost answer reuses it and another task or turn gets its own.
  - Stop only beside `running` or `waiting_on_you` with an id, turn and folder (RAVIS's `_confirmed`
    stops starting, running, waiting and stopping sessions, which it lists as those two). A task this
    page stopped reads "stopping…" while RAVIS lists it working on that turn, then "stopped — open the
    project in an editor to review it" when RAVIS lists it `completed_needs_review`.
  - The version report is offered for `untested` and `accepted` builds, Use this version only for
    `untested` with checks 1 to 6 passed, and a report is dropped once `installed_sha256` moves. The
    card says before the button that accepting neither starts the re-test nor spends allowance
    (`codex-admin.json`: acceptance records `unproven`; `reprove` is `owner_cli` only).
  - The re-test in the menu is offered only for `accepted` builds: RAVIS's `_reproof_refusal` refuses
    every other verdict, and the menu points an untested build to the card instead.
  - The menu's Codex row opens the card, as the brief asks, so the task controls sit in each task
    line's submenu rather than the row's (an item with a submenu can't also be clicked). It also carries
    Sign in and Cancel the sign-in, from design §7.1's submenu.
  - NERVIS forwards a site's name only in host-name shape, so no wildcard default can be sent; RAVIS
    still words everything else about a host.
- **Tests:** `nervis/tests/test_ravis_codex_card.py` (new: `owner-stop.json`'s `nervis_route` examples and
  every owner Stop answer replayed; the key's and the id's shapes at both ends; source and
  confirmation; `configure`'s headers; the route table and NERVIS's source scanned for agent-session and
  project-lock routes; NERVIS's Codex routes equal to `nervis_control_routes`; sites; the version
  routes' wait; `no-store`; no credential), `test_control_token.py` (the four routes) and
  `test_launcher_codex.py` (the new fields, a RAVIS before 0.25.1, nothing past the listed fields
  printed, `signed_in`). NERVIS has 1430 tests, the repository 3652. `nervis/tools/codex_check.js` gained
  the card's cases, numbered 8 to 12 in its header, reading `codex-state.json` and `codex-admin.json`.
- **Guard proof:** 61 of 61. 29 breaks of the page, each caught by `codex_check.js`; 19 of the routes and
  6 of the launcher, each caught by pytest — the first run missed `signed_in` hard-coded true, so a test
  with a signed-out reading was added, and the break was then caught; and 7 of the menu, each compiled
  into the scratch folder and seen in its `--print-menu` output, since the Swift has no test suite.
- **Unverified:** nothing ran against the live RAVIS or a real Codex (no stop, no removal, no version
  check or acceptance). The menu app was compiled with `swiftc` into the scratch folder and its menu
  printed from recorded status answers; it was not built into a bundle or opened, so its dialogs, the
  run-loop completions and the six-minute re-test wait weren't exercised. The version check's real
  duration against the 210 s wait is unmeasured, and the card wasn't looked at in a browser.
- **For P (packaging):** reinstall NERVIS editable (its version changed) and restart the stack, so
  NERVIS serves 0.29.0; `tools/run.py`'s changes take effect at once. Rebuild the menu app with
  `nervis/packaging/macos/build_app.sh`, which also refreshes `/Applications/NERVIS.app`, then `kill -9`
  the running app's PID and `open /Applications/NERVIS.app`; the stack stays up.

## From R5b (ecosystem, RAVIS 0.25.1, 14 September 2026) — a task's model, effort and reconnecting in `runs`
NERVIS's task card couldn't show a Codex task's model, effort or reconnecting state: every
agent-session read refuses NERVIS, and `nervis_control_routes.never` rules out new routes for it.
- **Built:** each `GET /api/v1/codex` → `runs[]` entry gains `model`, `effort` and `reopening` — the
  same values as `SessionView` → `codex.model`, `codex.effort` and `codex.reopening`
  (`{group_id, hosts, since}` or null) — in `AgentSession.run_entry`; a Clarvis-engine entry carries
  three nulls (`AgentSessions.runs`). `codex-state.json` → `runs_rules` and both `runs` examples
  (named and anonymous) say so; `codex-contract.sha256` regenerated. RAVIS 0.25.1.
- **Decided:** every caller gets the three fields, anonymous readers included, like `project` and
  `state` (the coordinator asked for them on each entry): model ids and site names, never a path,
  request text or command. `revision` moves when a reopen starts or ends, since the body changes.
- **Tests:** a Codex run shows its model and effort and a null `reopening`, and a Clarvis-engine run
  three nulls, for NERVIS's credential and an anonymous reader (`test_agent_sessions_create.py`);
  `reopening` equals the task's own view while its thread reopens and is null once the reopen ended
  (`test_agent_sessions_reopen.py`). Guard proof: each field broken on its own in a snapshot, and its
  test failed.
- **For Clarvis:** sync `codex-state.json` and the manifest; Clarvis reads nothing new from `runs`.
- **For N2b:** show `runs[].model`, `runs[].effort` (null: the model's default) and, while
  `runs[].reopening` isn't null, that the task is reconnecting for `reopening.hosts`.

## Owner decisions 14 Sep 2026 (during C2b+) — the bowtie fold-out, and Clarvis's differences from CLARVIS.md §5.5
- **The bowtie becomes a fold-out menu** (the `#clarvis-models` button left of Clarvis's chat box). First item
  **API config** (the owner renamed it from "Change API"): exactly what the bowtie did, the models menu through
  the same `models` message. Under it a **Codex** section: the Codex models RAVIS lists, an effort control over
  that model's efforts with its default marked, the note that a bigger model or higher effort uses the plan's
  allowance faster, and **one allowance line** (the owner's addition): the tightest window's percentage left and
  its reset in local time, the rest in a tooltip, "not known right now", a stale figure marked old, used up, and
  RAVIS not answering each said plainly. The controls are disabled unless the coding model is
  `ravis/clarvis-codex`; the allowance shows either way. Choices live in the owner's settings only
  (`clarvis.codex.model`, `clarvis.codex.effort`, application scope). Built in clarvis `47dbc75`.
- **Changing model or effort during a task:** the owner set that question aside, so a change applies to the next
  task and the section says so while one runs. (Codex's `turn/start` does accept `model` and `effort` "for this
  turn and subsequent turns", should the owner want it later.)
- **Clarvis C2b differs from CLARVIS.md §5.5's wording, to write into CLARVIS.md at the end:**
  - a site ask waits behind Codex's own requests, since only those hold Codex up;
  - "Stop the run" performs the Stop button's stop (interrupt) rather than posting a `stop` decision;
  - typed words that aren't an answer are steered to Codex, and the request stays on screen;
  - undo copies also cover added files and rename targets.

## From R5 (ecosystem, RAVIS 0.25.0 and NERVIS 0.28.19, 14 September 2026) — sites before and during a task, and each task's effort
Contract first (`3c63125`), then the code (`bc1a103`), the release pairing
(`3376190`) and these notes.
- **Built — option 1:** `GET`, `POST` and `DELETE /api/v1/codex/sites` (`api/management/codex.py`)
  over `SiteAllowlist.listed`, `allow` and `remove` (`agent/sites.py`). `added` is the user layer of
  `config/read {includeLayers: true}` (`user_sites`, moved here from calibration's
  `scenarios_network.py`, which imports it) without the defaults, sorted. `POST` checks every host with
  `site_refusal` (`not_a_host_name`, `wildcard`, `ip_address`, `local_name`) before anything, reads the
  list (503 when it can't), then writes one upsert; `DELETE` refuses a default before reading
  (`default_site`, the wildcard defaults included), writes nothing for a host that isn't added, else one
  `replace` of the rest. Audited `ravis.codex.sites_allowed {hosts}` and `ravis.codex.site_removed
  {host}`. Every site write — these, a site ask's and the defaults' — holds one lock (per event loop),
  so a removal's read-then-replace can't drop a site allowed meanwhile, and a removed host leaves
  `add`'s cache.
- **Built — option 2:** the `DEVELOPER_INSTRUCTIONS` line; `SiteAsks` (a group per turn, `sg_…`, a
  group's asks open together, one group open at a time); `agent/reopen.py` (`Reopening`, `let_go`,
  `unloaded`, `still_loaded`) and the session's reopen. A turn that ends with asks of its group still
  open, or with a site allowed since the thread loaded, sends `thread/unsubscribe` at once, emits
  `site.reopening {group_id, hosts}`, shows `codex.reopening {group_id, hosts, since}`, asks
  `thread/loaded/list` every 2 s for up to 120 s (`SessionTimings.unload_poll_seconds`,
  `unload_cap_seconds`, `unsubscribe_seconds`, `loaded_list_seconds`), then emits `site.reopened` or
  `site.reopen_incomplete` and resumes with `thread_resume_params` unless skipped. A `turns` call
  meanwhile is 202 and waits in `starting` (a failed resume fails that turn); a Stop, `cancel`, an
  `interrupt` of any reason and every settle skip the resume; a Stop also drops the waiting turn;
  ending the task, or Codex's process ending (which emits `site.reopened`: every thread went with it),
  stops the wait.
- **Built — effort:** migration 10 (`agent_session.effort`, backed up as `ravis.db.v9.bak` by the
  migration runner); `CreateSession.effort` (text or null; empty is none) and a named model checked
  against `CodexService.models()` at create, after readiness and under the create lock; `effort` on
  every `turn/start`, absent when the task has none; `SessionView` → `codex.effort`.
- **Decided here** (the contract's `open_points` too):
  - A settle skips the resume, as the owner's notes say, but never the wait, and the next turn resumes
    the thread on demand. Clarvis settles every turn, so the eager resume mostly runs for a task nobody
    settles. `site.reopened` therefore marks Codex letting go — the moment a new load sees the sites —
    not the resume. It is a third event beside the two the notes name, so Clarvis knows when to drop
    "Reconnecting Codex…".
  - **A site allowed after the thread was resumed reopens it again** (at once when no turn runs, else
    when the turn ends), and `start_turn` never starts a turn in a thread loaded before the task's last
    allowed site. Without it, an owner deciding after the minute would carry on in a stale thread.
  - Asks open a group at a time: the old one-at-a-time rule, per group.
  - A named model is refused when `model/list` doesn't offer it (422 `MODEL_NOT_OFFERED`): an effort can
    only be checked against a listed model, and the owner-requirement note's "an unknown model is refused
    by the `model/list` pre-flight" wasn't so in code. With no model named, the effort is checked against
    the default model (else the first listed); before `model/list` has answered, 503.
  - Callers: `GET` Clarvis's and NERVIS's clients and any admin (403 `FORBIDDEN` otherwise); `POST`
    `require_agent_client` (403 `AGENT_CLIENT_NOT_ALLOWED`); `DELETE` admin (403 `FORBIDDEN`). New codes
    `SITES_REFUSED` (422), `SITE_NOT_REMOVED` (409), `EFFORT_NOT_OFFERED` and `MODEL_NOT_OFFERED` (422);
    `POST`'s `SITE_NOT_ADDED` details are `{hosts, reason}`. At most 20 hosts a `POST`.
  - Nothing of a reopen is stored: a restart restarts Codex, so the next turn resumes the thread, and a
    turn waiting for the reopen becomes `uncertain`, like any turn a restart cut off.
  - `_hold_thread` no longer cancels a consumer that is busy with a message; it finishes it and stops.
    A reopen's resume can land while the turn's end is still running in that consumer.
- **Fakes:** the relay half's `fetch <host>` goes through the calibration half's proxy
  (`site_allowed`), and relay threads record `sites_at_load` at their first turn; `thread/unsubscribe`
  unloads any thread that has had a turn (`had_turn`, set at `turn/start`), at once or after
  `unload_after_seconds`, and a resume cancels a pending unload; `turn/start` refuses an unloaded thread;
  `site_add_status` covers every upsert without a wildcard and `site_replace_status` a `replace`; a
  scenario's `refused_methods` are refused at once.
- **Tests:** `test_codex_sites.py`, `test_agent_sessions_reopen.py`, the effort tests in
  `test_agent_sessions_create.py`, migration 10 and the instruction line in `test_agent_store.py`, the
  restart case in `test_agent_reconcile.py`, the grouped site test in `test_agent_sessions_turns.py`, and
  the route tables in `test_codex_reprove.py` and `test_management_api.py`. RAVIS has 1624
  tests, the repository 3558.
- **Guard proof:** each new guard broken on its own in a snapshot copy, and its test failed every
  time — 28 of 28: POST checks every host before it writes; DELETE never removes a default; POST
  refuses a write Codex didn't take (SITE_NOT_ADDED); DELETE refuses a write Codex didn't take
  (SITE_NOT_REMOVED); GET sites: Clarvis, NERVIS or admin only; POST sites: Clarvis's client
  credential only; DELETE sites: admin only; site writes wait for one another; a removed site is
  forgotten, so allowing it again writes it; an unreadable list is 503, never empty; a turn ending
  with site asks open starts the reopen; the resume waits until Codex unloaded the thread; the cap
  says site.reopen_incomplete; a turn asked for while reopening waits for the resume; a Stop, a
  switch or a settle skip the resume; a settle skips the resume; a Stop drops the turn waiting for
  the reopen; one turn's asks open together, not one at a time; every site one command was blocked
  from is asked; one group is open at a time; site requests carry group_id; no turn in a thread
  loaded before the last allowed site; a model and an effort are checked at create; effort is sent
  on every turn/start; SessionView shows the effort; Codex is told to stop at a blocked site;
  migration 10 adds each task's effort; after a restart the next turn resumes the thread. The first
  try at "one turn's asks open together" broke something that couldn't change the result (asks block
  one command at a time, so trimming each opening to one ask still opened them all); with the
  one-at-a-time rule put back instead it was caught, and the first break became a new test and guard
  for one command blocked from several sites.
- **Unverified (no real Codex ran):** the reopen through the relay on real Codex (calibration measured
  about 60 s in its own harness); `effort` changing what Codex does; `config/read`'s layers read by
  production code (calibration read them the same way); `thread/loaded/list` paging (a `cursor` parameter
  is assumed; real Codex answered in one page); `config/read` and `config/batchWrite` still aren't in
  the pin's used methods. The consumer hand-off has no deterministic test. The live database moves to
  version 10 at the next restart.
- **For C2b+ (Clarvis):** sync the fixtures byte-identical from `3c63125` (manifest
  regenerated) and build against `agent-sessions.json` → `reopening`, the site ask's `group_id`,
  `site.reopening`, `site.reopened` and `site.reopen_incomplete`, `codex.reopening` and `codex.effort`,
  `CreateSession.effort`, the sites routes in `codex-admin.json` and the new codes. Send `carry_on` once
  the group is decided, whenever that is: RAVIS holds the turn until the thread is resumed.
  Fingerprints are bare hex.
- **For N2b (NERVIS):** read `GET /api/v1/codex/sites` through the GET relay; remove with
  `DELETE /api/v1/codex/sites/{host}` through a control route carrying the admin credential
  (`codex-admin.json` → `nervis_control_routes`); a default answers 409 `SITE_NOT_REMOVED`
  `{reason: default_site}`.

## From calibration runs 8 and 9 (ecosystem, RAVIS 0.24.5, 14 Sep 2026) — the file rules are proven
- Run 8 (`cal_faf8dcc6b0f1`, 0.24.4, K3+K6): both passed. K3 `reached_in: reopened`: the next step (no
  `sandboxPolicy`) was blocked; unsubscribe, unloaded after about 60 s, `thread/resume` with the profile,
  reached. The copy wasn't needed, so `thread/fork` stays unproven on real Codex.
- Run 9, the full run (`cal_5a1d6ecc33b4`): K10, K5a, K5, K5c, K1, K2, K3 (reopened, 60 s), K7, K6, K13
  passed; K2b, K4, K8, K9, K11, K12 recorded. `strict_rules_proven: true`; `tested_runtimes.json` carries
  `file_rules_profile` (the candidate profile, placeholders intact) and the entry's `calibrated_on` and
  `calibration`. Transcripts committed under `ravis/tests/fixtures/codex/calibration/0.154.0-cal_5a1d6ecc33b4`
  (checked: no home paths, emails or tokens; the one `@` is a diff hunk). `test_codex_runtime.py`'s
  committed-pin test expects the proven entry and reads that summary. Allowance: 25% of the weekly window
  left afterwards (32% before run 6).
- The summary's protected-repositories line says they "may be allowed later, by an exact listing: deny beat
  write inside a project". The owner's default stands: the ecosystem's own repositories stay refused.
- **For C2b (overrides Cal-4's and Cal-5's notes):** after the owner allows a site, carry the task on by
  reopening its thread: let the turn end, `thread/unsubscribe`, wait until `thread/loaded/list` drops it
  (about 60 s; show the owner that it's reconnecting), `thread/resume` with the profile and roots, then the
  next turn. A new turn in the still-loaded thread doesn't see the site.

## From Cal-5 (ecosystem, RAVIS 0.24.4, 14 Sep 2026) — K3 tries reopening and copying the task
Run 7 (`cal_85aa0ece0f52`, RAVIS 0.24.3, `--only K3,K6`): **K6 passed.** K3 failed: add `ok`, the running
turn blocked, and the next turn in the same thread blocked too, with the fixed line. So **Cal-4's note for
C2b below is wrong**: a new turn in the same loaded thread doesn't see the site. The next-turn `turn/start`
still carried RAVIS's per-turn `sandboxPolicy` (production's `calibration_dependent` sends one too).
- **Model-free facts (throwaway `CODEX_HOME`, Codex 0.154.0):** `thread/shellCommand` exists but runs
  unsandboxed, so it can't test the proxy. `thread/unsubscribe` answers `unsubscribed`, the thread stays in
  `thread/loaded/list`, and `thread/closed` arrives after about 50 s. `thread/resume` and `thread/fork` need
  a rollout ("no rollout found" before a thread's first turn). The experimental schema lets `thread/fork`
  and `thread/resume` take `permissions` and `runtimeWorkspaceRoots`, and fork answers
  `activePermissionProfile`.
- **Built (owner go-ahead for one run trying three ways, then the full run):** K3's thread is non-ephemeral;
  after the running turn's retry is refused, `WAYS` in order, stopping at the first that reaches example.com:
  `next_step` (`turn/start` with `box=None`, no `sandboxPolicy`), `reopened` (unsubscribe, poll
  `thread/loaded/list` up to `CalibrationTimings.unload_seconds` = 75, `thread/resume` with the profile),
  `forked` (`thread/fork` with the profile and roots, held as `A-copy`). Each has its own command
  (`/next-step`, `/reopened`, `/copy`); a refused way is noted and skipped. Facts: `reopened`, `forked`,
  `fork_profile`, `notes`; a copy that reached it under another profile fails; none reached with a way not
  run is inconclusive; all three blocked fails. `reached_in`: `same step` / `next step` / `reopened` /
  `copy`. K3's threads are archived afterwards.
- **Fakes:** a thread's sites are read at its first turn after loading (`sites_at_load`); calibration
  overrides `thread/unsubscribe` (unloads a thread that has had a calibration turn), `thread/loaded/list`
  and adds `thread/fork`; `_resume` reloads an unloaded thread's sites. Faults `site_add_reaches_next_turn`,
  `thread_never_unloads`, `fork_drops_profile`; `site_add_not_live` now also holds for copies.
- **For C2b:** use whichever way run 8's `reached_in` names. Unverified until then.

## From Cal-4 (ecosystem, RAVIS 0.24.3, 14 Sep 2026) — an added site counts from the next step; K6 judges long-runners
Run 6 (`cal_ed672bf12c6f`, RAVIS 0.24.2, `--only K3,K6`) failed both. (The first attempt that day was
refused before starting: RAVIS had been restarted at 13:46 without `RAVIS_CODEX_CALIBRATION=1`.)
- **K3:** default sites written, the add of example.com answered `ok` (not overridden), and the retry in
  the running turn was still blocked with the fixed line: Codex 0.154.0's proxy keeps the site list a turn
  started with. **Owner decision (14 Sep 2026): an allowed site working from the task's next step is
  enough.** Built: `K3Commands.next_step` (`curl … https://example.com/next-step`); when the add went
  through and `after` isn't reached, K3 starts one more turn in thread A with that one command;
  `K3Facts.next_step`; inconclusive if that turn didn't run it, failed if it is refused too; findings add
  `reached_in` (`same step` / `next step` / null). **For C2b and whatever resumes a task after an allow:**
  the site only counts in a new turn of the same thread, so an allow must lead to a follow-up turn (the
  owner's "keep progress in Codex"), never a retry inside the running one.
- **K6:** all five of B's long-runners survived A's Stop; what was gone were three `(bash)` rows attributed
  to B by parent, caught while B was still starting (A's `python3` already showed as `Python`, B's still
  as `python3`). `ps` prints `(name)` for a process whose arguments it couldn't read; an exited, unreaped
  child shows `<defunct>` instead (checked on this Mac). Built: `scenarios_turns.long_runners(attributed,
  owner)`, the rows matching `OUR_COMMANDS`; the wait and `found_per_project` count those, and B is judged
  on those. A's Stop still signals every A-attributed row; R4's production attribution is unchanged.
- **Fakes:** a thread's commands see the sites as of its turn's start (`sites_at_turn_start`), so the default
  fake behaves like 0.154.0; `site_add_reaches_running_turn` gives the old live behaviour and
  `site_add_not_live` keeps a thread to its first turn's sites. Tests: K3 by the next step, K3 in the same
  step (no second turn), K6's long-runner count; the fault table's `site_add_not_live` now fails "next step
  either".
- Run 6's transcripts are in the session scratchpad, not the repo. Unverified until the next real run: the
  next-step turn on real Codex, and K6's long-runner count on real Codex.

## From Cal-3 (ecosystem, RAVIS 0.24.2, 13 Sep 2026) — sites written at ready, K6 one command
Run 5 (`cal_330b7525d115`, RAVIS 0.24.1) passed every file-rule check, K7 and K13; two failed, each with a
confirmed cause.
- **K3: `add_refused: "overridden"`, `added_live: false`**, example.com still blocked after the add
  (listed site reached, example.com blocked before, loopback and never-added blocked). Cause:
  `network_profile_flags` put `network={…, domains={…}}` in the launch flags; that `-c` (CLI) layer covers
  `permissions.clarvis_run.network.domains`, so `config/batchWrite` to it answers `okOverridden`. Verified
  model-free on 0.154.0 with a throwaway app-server launched with `permissions.clarvis_run={extends=
  ":workspace", network={enabled=true, mode="limited"}, filesystem={…}}` (no `domains`),
  `features.network_proxy=true`, `default_permissions="clarvis_run"`: npm and example.com blocked; upsert
  `{"registry.npmjs.org":"allow"}` with `reloadUserConfig` → `ok`, npm 200, example.com blocked; upsert
  `{"example.com":"allow"}` → `ok`, both 200, example.org blocked.
- **Built:** `network_profile_flags(profile)` is `network={enabled=true, mode="limited"}` only;
  `calibration_dependent.without_network_domains` strips a `domains={…}` table (or a `…network.domains=`
  pair) and is applied in `pin.file_rules_profile` (every launch) and `plan.checked_profile` (owner file,
  pin, candidate; so `write_pin_entry` never pins one). `service._profile_pinned` compares checked forms.
  `SiteAllowlist._upsert` is the one write; `add` and the new `allow_defaults` use it. `CodexService.
  _process_ready` bumps a process generation, sets `state.SITES_PENDING` and writes the defaults in the
  background (`SITES_NOT_NEEDED` with no profile); a stale answer is ignored; not `ok` → the refusal word
  (`overridden`, `not_written`, `codex_did_not_answer`), `logger.error`, and `state._sites_row` makes a
  running, account-read process `runtime_down` with a plain reason, so `readiness()` refuses tasks with
  that state and sentence (no fixture change: the conventions allow the state's sentence). `_process_ended`
  resets to pending; the next ready retries. `ScenarioContext.default_sites` exposes it; K3 waits up to
  `request_seconds` for it and fails `({default_sites})` before its other checks, after the
  reached-too-much failures.
- **K6: attribution worked** (zero unattributed: `script` A/B and `sleep` B by `command_cwd`, `sleep` A/B by
  `parent`) but it waited for `len(K6_COMMANDS)` = 4 per project, never possible: `python3 -m http.server`
  fails at once (the sandbox forbids binding, a sandbox rule), `script -q /dev/null sleep 600` holds the
  turn 600 s so later commands never start, `sleep 600 &` exits leaving `sleep` reparented. **Built:** one
  command, `K6_COMMAND` = `sleep 600 & script -q /dev/null sleep 600 & python3 -c 'import time;
  time.sleep(600)' & wait`; `K6_PROCESSES_PER_PROJECT = 5` (shell, sleep, script + child, python3);
  findings add `expected_per_project` and `found_per_project`. Pass rule unchanged.
- **Fakes:** the relay half answers a batchWrite `okOverridden` when any argv `-c` setting is the key or a
  table above it naming its leaf (`domains=`), else `ok`, except `site_add_status` for a one-site upsert
  (replaces `batch_write_status`); `config_written` logs the status and the calibration half applies only
  `ok`. K6's fake: one root `/bin/sh -c "sleep 600 & /bin/sh -c 'sleep 600; true' & sleep 600 & wait"` per
  project, foreground until stopped, a background terminal.
- Tests: `test_codex_default_sites.py` (stripping; defaults at every start and never at launch; overridden
  → not ready, 409, logged, retried) and in `test_codex_calibration_findings.py` K3 with launch sites
  (fails `overridden`), K6 pass, fail, and the count bound. Relay-contract fixtures unchanged.
- Unverified until the next real run: K3 (c) live on a loaded thread, K6's five processes on real Codex
  (a `python3` shim that spawns rather than execs would only add a process), and the ready-time write on
  the live stack (RAVIS not restarted by this build).

## From R4 (ecosystem, RAVIS 0.24.0 and NERVIS 0.28.16, 13 Sep 2026) — the project lock, clean-up, restarts
Built: `agent/lock_routes.py` and `lock_api.py` (the six routes), `group_kill.py`, `attribution.py`,
`reconcile.py`; `cleanup.py` and `locks.py` rebuilt on them; migration 9 (`ravis_instance`, `agent_thread`,
one `agent_process` row per task process); shutdown interrupts; `paused_for_update`; the 90-day
`thread/delete` sweep; `account.fingerprint_sha256` in `GET /api/v1/codex` for named callers.
**K6's attribution rule** (K6 found nothing in `cal_d2185ed08f50`): every command is `/bin/zsh -lc` under
`sandbox-exec`, which replaces itself, so no `WRITABLE_ROOT` ever shows; `processId` isn't a pid; terminals
list `osPid: null`; `ps -E` shows no other process's environment on macOS. So a process gathers claims —
a terminal's `osPid`, a sandbox argument (`-DWRITABLE_ROOT_<n>=<root>`), an earlier look by pid+start
(survives reparenting), its parent, or, for a command root (a descendant of RAVIS's app-server whose parent
has no task), its `lsof` folder inside a root whose turn started before it (1 s slack). One claim:
attributed; two: ambiguous (reported, keeps the task `leftover`, signalled only by the owner's **Stop
them**); none: unattributed. Residual gap: a command root that `cd`s into *another running* task's root
before its first look. K6 passes the scenario's start as both turns' start; the fake's K6 processes run
in the thread's folder, in their own session, with no sandbox argument and `osPid: null`.
For C3/C2a (Clarvis): the decisions are in `project-locks.json` → `decided_in_r4` and `conventions.json` →
`open_points`. The lock file's `leftover` entry is `{pid, start, comm}` (`lock-rule-cases.json` →
`lock_file_leftover`); a window finding a gone RAVIS holder may end exactly those, individually, never by
group. Leases and transfer tokens are `lk_…` and `tt_…`; release answers `{lock: null}`; a takeover that
can't confirm the kill is 409 `PROCESSES_NOT_CONFIRMED_GONE {leftover}`; every agent-session and lock route
is 503 `CODEX_RUNTIME_UNAVAILABLE` (retryable) until restart reconciliation has run. Account fingerprints:
RAVIS serves the bare sha256 hex in both `SessionView` and `GET /api/v1/codex`, while the fixtures'
examples show `sha256:…` (already so in R3) — compare the two RAVIS values as they come.
Not built or unverified: shutdown's interrupts (1.5 s) and kills (1 + 1 s) are shorter than the design's
5 s, so RAVIS ends inside the launcher's six seconds; whatever they cut off, the next start ends. No real
Codex was sampled: K6's rule is proven against recorded `ps`/`lsof` formats and a K6-shaped table, and
needs the next calibration run.

## From Cal-2 (ecosystem, RAVIS 0.23.16, 13 Sep 2026) — calibration matched to the real run
Changed: `calibration/plan.py` (`CANDIDATE_PROFILE`, `ScenarioSpec.may_go_unasked`), `outputs.satisfied`,
`harness.py` (`Session._resolve_open`, `order`), K3 moved to the new `scenarios_network.py`, K7 in
`scenarios_turns.py`, K8 in `scenarios.py`; `tests/fake_codex_calibration.py` and
`tests/test_codex_calibration_findings.py`.
- **Profile:** byte-identical to the working `clarvis_run-profile-allowlist.json`:
  `":project_roots"={"."="write", ".run"="deny", "**/.run"="deny"}` (as `cal_d2185ed08f50` ran it, with
  K5, K5a and K5c passing) and `network=` taken from R3's `network_profile_flags` value. No override file.
  The structured `glob_pattern` form was not tried.
- **K3 (a–e):** one `untrusted` thread, five `curl`s. The drive stops at command 2's `item/completed`;
  `SiteAllowlist.add("example.com")` runs only if `blocked_hosts` named it; only then is command 3's
  approval answered. `asked_again_after_adding` compares transcript order (inconclusive if Codex ran it
  unasked). Before the turn `config/read {includeLayers:true}` reads the user layer's
  `permissions.clarvis_run.network.domains`; afterwards a `config/batchWrite` `replace` writes it back
  (`{}` when there was none). Unreadable layers → not put back → `owner_question`. An add answered
  `okOverridden` fails K3 with `(overridden)`.
- **K7:** on `turn/completed` the harness answers each request it holds for that thread with
  `STOP_RESPONSES[kind]` (`resolved_by_ravis`). K7 passes when the turn ended `interrupted` within the cap
  and nothing is left open, checked before `close` (which cancels whatever remains). Codex letting go
  itself also counts; `serverRequest/resolved` isn't required.
- **K8:** never asked → `recorded` (`permissions_asked: false`); `decide` accepts `recorded` only for a
  spec with `may_go_unasked`, which is K8 alone.
- **The fake:** the relay half still answers `config/batchWrite` (registered last), so the calibration
  half wraps `api.log` and applies each `config_written` edit whose `batch_write_status` is `ok`. No
  network approvals any more; an interrupt leaves the request open and logs `answered_after_interrupt`.
  New faults: `loopback_open`, `listed_site_blocked`, `site_block_line_changed`, `site_add_not_live`,
  `add_opens_every_site`, `retry_runs_unasked`, `site_left_from_an_earlier_run`, `never_asks_permissions`.
Still unverified until the next real run: an added site reaching a loaded thread (K3 c), and Codex
answering the add `ok` rather than `okOverridden` while its launch flags carry `domains`.
For R4: K6 untouched. For whoever touches launch flags: a pinned profile now carries its own network
section, and `service._profile_flags` appends `network_profile_flags` again outside calibration — an
identical second override that calibration itself doesn't exercise.

## From R3 (ecosystem, RAVIS 0.23.15, 13 Sep 2026) — the agent-session relay
Built: migration 8 and `ravis/src/ravis/agent/`; every `/api/v1/agent-sessions` route and the stop-only
owner route on its own router; SSE with the two replay buffers and cursor expiry; identity, tokens,
roots, durable idempotency, allowed decisions, the unanswered policy, the step cap, redaction, presence,
the action lock; `runs` in `GET /api/v1/codex`; `ravis.agent_sessions@1` and the `relay` constraint.
Calibration `cal_d2185ed08f50` decided (all in `agent/calibration_dependent.py`, the one place):
- Every mode uses the measured granular policy (`mode_mapping`). Non-ephemeral threads (K13).
- **No network through approvals; approved sites instead** (the owner's final decision, after two reversals
  the same day). `NETWORK_GRANTS_OFFERED = False`: a request carrying `networkApprovalContext` or
  `additionalPermissions.network.enabled`, or a grant asking for network, offers only skip/stop (both fields
  parsed defensively; no `applyNetworkPolicyAmendment` path). **Approvals never open the network on 0.154.0**,
  in `untrusted` and `on-request` alike: K3 failed in `cal_d2185ed08f50`; again in `cal_f4552084e0e0` with
  `exec_permission_approvals` and `request_permissions_tool` on (no network approval asked, no
  `additionalPermissions`, no permissions request); and with `features.network_proxy` and `domains={}`
  (`cal_8cfcf81b2d04`) Codex sent only plain command approvals (`availableDecisions`: accept,
  acceptWithExecpolicyAmendment, cancel) — after an accept **the proxy blocked local addresses outright**
  ("local/private network addresses are blocked", `baseline_policy`) and **an unlisted domain without a
  prompt**, with the fixed line `Network access to "<host>" was blocked: domain is not on the allowlist for the
  current sandbox mode.`; `thread/start` said `networkAccess: true`, then `thread/settings/updated` showed
  `activePermissionProfile: null`, `networkAccess: false`. **A fixed allowlist works model-free**:
  `network={enabled=true, mode="limited", domains={"host"="allow"}}` with `features.network_proxy=true` → HTTP
  200 (`domains={}`, or `mode="full"` with none → 403; unknown network keys ignored). The decision: an
  approved-sites allowlist pre-populated with likely hosts (registries, GitHub), plus a per-site ask. **Built
  in R3** (`agent/sites.py`): the blocked line on a completed command → `site.blocked {turn_id, item_id, host,
  protocol}` and a `site` request (`{host, protocol}`; `allow_site` / `keep_blocked`) through the answer
  route, one open at a time, each host once per task, never pausing the task or counted by the policy, open
  past the turn's end, resolved `turn_ended` when the task ends, audited `ravis.agent_session.site_decided`
  with the host only (stored on `agent_request.host`). **Applied live** (verified model-free):
  `SiteAllowlist.add` sends `config/batchWrite {edits:[{keyPath:"permissions.<profile>.network.domains",
  mergeStrategy:"upsert", value:{host:"allow"}}], reloadUserConfig:true}` over RAVIS's one connection; anything
  but `status:"ok"` (`okOverridden` included, or an error) is 409 `SITE_NOT_ADDED` (request stays open);
  idempotent per host; exact plain hosts only (never a wildcard, IP, `localhost`, `*.local`); then
  `site.allowed {request_id, host}`. `features.network_proxy=true` is in `FIXED_FLAGS`, and the pinned
  profile gets `network={enabled=true, mode="limited", domains={DEFAULT_ALLOWED_SITES}}` at launch (not in
  calibration mode). **Unverified:** an added site reaching a turn already running, and a live write not
  being overridden by the launch-time `domains`. The two permission-request
  features were withdrawn from `FIXED_FLAGS` again. Calibration's K3 criterion, K7/K8 and K6 come in a
  separate calibration increment.
- K7 (an interrupt leaves the request open): every way a turn ends answers and publishes each open request
  itself — Stop, owner Stop, step cap, the policy, a crash, and a turn Codex ended on its own.
- K12 (events out of order): a file-change approval waits up to 2 s for its item; one whose item never
  said what it writes is offered only skip/stop. K8: nothing relies on a permissions request arriving.
Decided here (recorded in `conventions.json` → `open_points`): a replayed create/reissue returns the same
token in the same run (memory only), a reissue-style new token after a restart; settle `idle` releases
the lock and its file, `transfer` keeps both, `end` archives; malformed bodies are 422
`INVALID_REQUEST_BODY`; the folder reasons; the `deltas_skipped` frame (no id, `{session_id, after}`); no
recent-items list in the snapshot (fixtures followed). `interrupt` on a task not running answers 202
with its current state. `runs[].state`: `starting`/`stopping` → `running`, `stopped` →
`completed_needs_review` with `paused_reason: "stopped"`.
For R4 (the seams are `agent/locks.py` and `agent/cleanup.py`):
- The transfer route must set the row's `transfer_token_sha256`, `transfer_expires_at` and
  `state: transferring`; R3 validates exactly those and treats `transferring` as not holding (409
  `PROJECT_LOCKED`). When a window takes the lock with a token, R3's session still holds a descriptor on
  RAVIS's lock file: abandon it (`HeldLockFile.abandon`) and let the window replace the file.
- C3's asks left for R4: `LOCK_TRANSFER_INVALID` on `POST /project-locks`; who rewrites the checkout lock
  file at a window's token take; nested-root takeover; the lock file's `leftover` entry shape (R3 writes
  `leftover: []` and shows processes as `{pid, comm, started_at}`); a lock file missing while
  `takeover_allowed` is served; the transfer, GET and takeover routes with `root_hash` and confirmation.
- R3 builds only the narrowest adoption (a gone previous RAVIS's file is replaced). Superseded rows are read
  (409 `LOCK_SUPERSEDED`), never written. Restart: live tasks become `uncertain` and their processes are
  ended by sandbox root; recording `agent_process` rows, the 2 s sampling and restart kills are R4's.
- Not built: `paused_for_update` (a binary swap with a turn only waiting), `ravis.project_lock.taken_over`,
  the 90-day `thread/delete` sweep, interrupting turns at shutdown (they die with Codex's process).
For C3: `SessionView.codex.account_fingerprint` is the sha256 fingerprint of the account the task was
created under. `GET /api/v1/codex` still shows only its strength and `fingerprint_matches`, so comparing
a checkpoint with the *current* account needs that hash added there (a contract change).
For C2a/C2b: a `session.state` settle state follows every turn unless a queued steer starts the follow-on
turn; `feedback` echoes the text as sent; secret questions and elicitations are never offered.
Fixtures changed: `agent-sessions.json` (the account fingerprint in every session view; the invalid
mode example's code; the `site` kind, rules, example and answers; network examples back to none), `event-stream.json` (the fingerprint in the snapshots), `conventions.json` (folder
reasons, `INVALID_REQUEST_BODY`'s reach, the open points decided, `SITE_NOT_ADDED`) and the
manifest; `event-stream.json` also gained `site.blocked` and `site.allowed`. Clarvis's copy
needs the same.

## From C2a (clarvis 29a5d4f, af46119, ceb383c — 13 Sep 2026)
Built: `CodingRun` surface (engine, run, interject, drainInterjections, result, blocked, branches, stillMissing,
codexSession); `chooseEngine` (Codex only for exactly `ravis/codex` in user settings, loopback RAVIS, trusted folder);
chat run builds either engine, palette never Codex, answer path refuses Codex. Stop releases every question in the same
tick before the interrupt; late answers never sent. Steer via relay, kept and redelivered when it can't. Each Codex
message → one text event (STEP matching unchanged). File changes recorded once per item+path. Clarvis's own writing
runs now take the lock file then RAVIS's lease (15 s heartbeats); fence trips only on revoked lease or a lock file
naming another window. ChatService.ts: only a panel-ping hook and a reattach delegate added; stop()/stopFromChat()
untouched. Behaviour change once installed: two editors can't build one project at once; folders without git get
`.clarvis/`. Until C2b, Codex's requests are declined where RAVIS allows declining and questions wait.
For R3 (RAVIS relay):
- Emit a `session.state` needing a save (e.g. `completed_needs_review`) after every turn, failed ones included; the
  runner settles only on that.
- Include `branch` in snapshots and session views.
- Echo steered text exactly in `feedback` events.
- Make turn-failure `error.message` owner-readable (kinds other than `step_cap` not fixed yet).
- A 422 from the lock routes leaves Clarvis's engine on the lock file alone.
- The settle sends `checkpoint_saved: true` before C3's checkpoint exists.
- The fixtures' file-change item has no `moved_to`.
Open in Clarvis: C2a — reconcile a gone window (F-A9), the command group kill, wiring "carry on" to `continueTurn`;
C2b — approvals UI (after calibration); C3 — checkpoint, switching, `continueOn`, takeover with confirmation.
Not run: `npm run test:host` (network lookup of VS Code's version); `src/test/engineChoice.spec.ts` written for it.

## Coordination (13 Sep 2026) — the peer's hasGit fix in Clarvis
- The owner approved nervis-ecosystem-fc's `hasGit` fix, to start after C3 lands. On my "C3 landed" message it edits
  `src/planning/workspaceResearch.ts` (read `hasGit` from unfiltered entries, keep the '.clarvis' line), its test, and a
  short signed-off plan.md entry; explicit paths; no bump/RELEASES/package/install without checking with me.
- Before launching the next Clarvis agent after C3 (C2b), wait for its "landed" message, and `git pull`/check clarvis HEAD.

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

## Owner decisions 14 Sep 2026 (after calibration) — allowing sites before and during a task, and the finish plan
The owner chose two of four proposals for making "allow a site" smoother (the other two, a thread copy and
waiting for a Codex release that applies sites live, stay open). Calibration fixed the facts: a thread reads
the site list when it loads; a running thread never sees a later add; a reopened thread does (about 60 s for
Codex to unload it after `thread/unsubscribe`).

**Option 1 — sites allowed before a task starts need no pause.**
- **RAVIS:** `GET /api/v1/codex/sites` (Clarvis and NERVIS callers) → `{defaults: [...DEFAULT_ALLOWED_SITES],
  added: [host, ...]}`, `added` being the profile's user-configuration sites minus the defaults (`config/read`
  layers, as K3 reads them). `POST /api/v1/codex/sites {hosts: [...]}` (Clarvis callers; the owner's click in
  Clarvis): every host checked with `plain_site` first (a refused host is named with why, and nothing is
  written), then one `SiteAllowlist` upsert, audited `ravis.codex.sites_allowed {hosts}`; 409 `SITE_NOT_ADDED`
  unless Codex answers `ok`. `DELETE /api/v1/codex/sites/{host}` (admin: NERVIS's Codex card) removes an added
  site, never a default, with one `replace` write of the rest; it reaches new and reopened threads only.
- **Clarvis, before `CreateSession`:** looks for the hosts the task will likely need — registry settings in
  `.npmrc`, `pip.conf`, `pyproject.toml` and `requirements*.txt` (`--index-url`, `--extra-index-url`),
  `Cargo.toml` and `.cargo/config.toml` registries, `Gemfile` `source`, `.gitmodules` URLs, and URLs in the
  brief — drops the ones already on RAVIS's list, and if any remain asks once: "This task may need a.com and
  b.org. Allow them before Codex starts?" with **Allow and start**, **Start without them** and **Cancel**. Exact
  hosts only, the same rules RAVIS applies.

**Option 2 — a site blocked mid-task: the reopen starts while the owner decides, and asks come grouped.**
- **Codex is told to stop:** `DEVELOPER_INSTRUCTIONS` gains a line — when a command is refused with the
  "Network access to … was blocked" line, don't look for another source or a workaround; end the turn saying
  which site is needed and why.
- **RAVIS:** when a turn ends with site asks open from it, RAVIS at once sends `thread/unsubscribe`, marks the
  session reopening and emits `site.reopening {hosts}`, polling `thread/loaded/list` every 2 s. The site asks
  opened in that turn form one group (`group_id` on each site request). A `turns` call accepted while reopening
  answers 202 and starts once Codex has unloaded the thread and RAVIS has resumed it (`thread/resume` with the
  profile and roots, as after a restart). If Codex hasn't unloaded it after 120 s, RAVIS resumes anyway and
  emits `site.reopen_incomplete`, so Clarvis can say a newly allowed site may still be blocked. Stop, switch
  and settle work as ever while reopening (the resume is skipped). Restart reconciliation resumes a reopening
  session like any other.
- **Clarvis:** shows a group as one card — each host with **Allow** or **Keep blocked**, plus **Allow all** —
  and, while RAVIS reopens, the chat line "Reconnecting Codex so newly allowed sites work (up to a minute)…".
  Once every ask in the group is decided it sends `carry_on` with "The owner allowed X; Y stays blocked. Carry
  on where you stopped."

**Model and effort** (the requirement below): `CreateSession.effort` beside the existing `model`, checked
against that model's `efforts` in `model/list`; `agent_session.effort` (migration 10); `effort` on every
`turn/start`; `SessionView.effort`. Clarvis's Codex model and effort picker sits beside the engine choice and
is fixed while a task runs; NERVIS's task card shows both.

**Contract loose end:** the account fingerprint. RAVIS serves bare sha256 hex; the fixtures' `sha256:…`
examples change to bare hex (the R3 and R4 notes), and Clarvis's copy is synced.

**The finish plan, in order** — one agent per repository at a time:
1. **R5 (RAVIS):** the contract first (fixtures and their hash, `RAVIS.md` §15.1.2, committed before code),
   then option 1's routes, option 2's reopening, effort, and the fingerprint fixtures; RAVIS 0.25.0, with
   NERVIS's RAVIS window to 0.25.999 in the same pairing.
2. **C2b+ (Clarvis), from R5's contract commit:** approvals (design §10.5 C2b: FIFO, re-evaluation before
   POST, rendering from `allowed_decisions`, the narrow Unattended auto-answer), the pre-task site ask, grouped
   mid-task site asks with the reconnecting line and `carry_on`, and the Codex model and effort picker;
   fixtures synced byte-identical.
3. **N2b and N1b (NERVIS, after R5):** the Codex card's task list with **Stop** (owner-stop through a NERVIS
   control route forwarding `Idempotency-Key`), the Overview line, the allowed-sites list with **Remove**,
   each task's model and effort, `codex_check.js`; the menu bar's Codex line with **Stop this task…** and
   **Re-test the file rules…**, each behind a confirmation; NERVIS 0.29.0.
4. **P (packaging):** restart the stack, rebuild and swap the menu app, package Clarvis 0.17.0, install it in
   both hosts and byte-compare `dist/extension.js`.
5. **L (live test)** with the owner.

## Owner requirement added 14 Sep 2026 (during calibration) — choose Codex's model and effort per task
- Clarvis's Codex picker lets the owner choose, for each task, **which Codex model** and **how hard it thinks**
  (effort: the levels `model/list` gives that model, e.g. `low` / `medium` / `high`). Default: the plan's
  default model at its `default_effort`. The picker says a bigger model or higher effort uses the plan's
  allowance faster.
- RAVIS today: `CreateSession.model` is optional and passed to `thread/start` (`thread_start_params`); an
  unknown model is refused by the `model/list` pre-flight. **Effort isn't designed yet.** To add: an optional
  `CreateSession.effort`, checked against that model's `efforts` in `model/list` (refused with a plain
  sentence otherwise), stored on the `agent_session` row, sent as `effort` on every `turn/start` of the task,
  and shown in `SessionView` beside `model`. Both are fixed for the task's life (a new task to change them).
- Ships with the Clarvis Codex picker increment, after calibration unpauses Codex; NERVIS's Codex task card
  shows the model and effort a task runs with.

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
