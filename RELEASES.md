# Release notes

**Status:** Per-component release notes for the five things this ecosystem ships
**Audience:** Operators upgrading, and anyone asking what changed between two versions
**Enforced by:** `tools/check_releases.py` — every shipped version has an entry here, or the gate fails

§15 asks that "compatibility matrix, operator runbook, release notes and known limitations
are published". There were no release notes, for any of the five, at any version. Five
components carried version numbers that nothing explained: `ravis 0.21.2` told an operator
that something changed twice since `0.21.0` and nothing about what.

**This file starts where it starts, and does not invent what came before.** Versions before
the ones below shipped without notes, and reconstructing them from commit messages months
later would produce a document that reads like a record and is a guess — §14.6 has one
incident of a number copied rather than run, and this would be the same mistake in prose.
What happened before is in `STATUS.md`, which is contemporaneous, and in the git history.
The gate below only requires an entry for the version each component currently declares, so
the record grows forward from here and no gap is papered over.

**Versions are per component, deliberately.** §3 keeps the four products independently
buildable and shipped on their own cadence; one ecosystem-wide version number would be a
fifth thing to keep in step with four others, and the first release where they disagreed
would make it a lie. What ties them together is the protocol version, which is recorded on
every entry.

---

## Clarvis — 0.16.0

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Feedback on a drafted plan changes the plan.** Keep Refining added a note and redrew the
  same plan, so "remove cloud sync" sat under the cloud-sync scope and step it contradicted,
  and the build was still told `Scope: …cloud sync`. The model now rewrites only the sections
  the feedback affects, those replace the old ones in the draft as it currently reads, and
  Clarvis says which changed. Anything said at the approval question that is not one of its
  buttons is taken the same way; it used to end planning unapproved. A change to §0 or Branch
  flow, a section the draft does not have, a reply cut off before it finished, or one that
  would leave nothing to build is refused, and the draft stays as it was. With no model the
  feedback goes under Notes, and that is said.
- **Approve writes the draft as it reads in the editor.** A hand edit used to be thrown away:
  Approve wrote the text that had been generated. An edit made while a revision is being
  written wins, and the revision is not applied.
- **Milestone one is built from the written plan.md**, the way every later milestone already
  was, so a step deleted from the draft by hand is not built. A build is offered only when
  milestone one has steps and at least one check, and steps without a check are named in the
  offer. Only Start Building starts it — any other reply, a question included, used to start
  the build.
- **Stop pauses planning, and cancelling never decides.** Cancelling a finding accepted it with
  its first fix; Stop at "Go with that?" put the options back; Stop at a finding, the name
  picker or a follow-up recorded a default and asked the next question; and "stop" typed while
  a model was working answered "Nothing to stop". Each of these now pauses, with nothing
  decided and nothing started. The drafted plan is saved with the interview, so carrying on
  goes back to the same draft rather than running the review again. A model call already
  running when Stop is pressed is not cut off: it finishes within its own time limit and is
  ignored.
- **A review that did not finish no longer reads as a clean one.** No model, a timeout, a
  failed call and an unreadable reply all came back as "no findings", and a refusal was parsed
  as a milestone's only step. A stage that did not finish now asks Try Again or Go On Without
  It, and the draft says what is missing. **Operator note:** the review prompt now asks for an
  explicit `NO-FINDINGS`, so a model that answers a clean review with silence shows as a
  review that did not finish.
- **A number picks an option only on its own.** `1 but keep offline support` was read as
  button 1 — at the approval question, Approve — and `1.5` as option 1. Both are now read as
  what was typed.
- No summary document opens beside plan.md once the plan is approved.
- Checked: types, lint and 1,447 tests, including the whole stretch from the review to the
  offer to build driven end to end under `node --test` for the first time; each of 20 new
  guards, removed from the compiled code, fails its test. **Not yet walked live:** the revision
  and `NO-FINDINGS` prompts against a real model, and the draft read-back and Stop in VS Code
  and code-server.

### 0.15.4

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

0.12.9 to 0.15.4 shipped without notes on 10-12 September 2026, while this file's Clarvis
head stood at 0.12.8. Each is summarised here from its own commits in the Clarvis repository —
the one that bumps it, which names the version, and any committed since the bump before —
and from nothing else.

- **Git answers come from the repository the open folder is in, not the first one found.**
  With a folder open that has no repository of its own but holds projects that do — a folder
  of task folders — the Git extension, which scans subfolders, found one of those projects,
  and the briefing named that project's branch and untracked file as this folder's. Eight
  places took the first repository found: the briefing, commit noticing, the review wizard,
  an agent run's branch and commits, the branch-flow check and plan commit, the agent's git
  tools, and branch switching and plain git status. All of them now use the deepest
  repository whose root contains the open folder, and none at all when no repository does;
  the check for a git problem counts only that one.

### 0.15.3

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **"You were last in" names only project files.** In code-server the editor's own settings
  live under `code-server/User/`, which the exclusion for `Code/User` never matched. Every
  setting Clarvis wrote saved that file, and the briefing opened the next window with "You
  were last in settings.json" about a project with nothing open. A path outside the open
  workspace folders is no longer remembered; with no folder open the named exclusions apply
  as before, and `code-server/User` is now one of them. The stored list is filtered the same
  way, so the stale entry is dropped on the next start.

### 0.15.2

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Clarvis names itself to RAVIS with the launcher's credential.** Every unnamed caller on a
  machine shares RAVIS's sixty requests a minute, and a NERVIS dashboard open in a browser
  reads RAVIS about twenty-four times a minute, leaving a Clarvis build roughly thirty-six
  before RAVIS turns it away. The ecosystem launcher sets `CLARVIS_RAVIS_CREDENTIAL` for
  code-server — NERVIS already presents a credential the launcher mints — and Clarvis now
  presents it, so RAVIS knows it as the client `clarvis`, with six hundred a minute. It is
  sent only for a `ravis/` model on a loopback address: another host, another model or an
  empty variable gets nothing, and a stored key still comes first.

### 0.15.1

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Stop ends what a command started, not only its shell.** The first live run of 0.15.0 ran
  `cd src && python3 main.py`, a stub that loops for ever, and Stop did nothing: it killed
  the shell Clarvis had spawned, the program that shell launched lived on holding the output
  open, and the command only counted as finished once every copy of its output had closed.
  The ten-minute time limit had the same blind spot. Commands now run as the leader of their
  own process group, and Stop and the time limit end the whole group. Windows has no process
  groups and keeps the old behaviour.
- **A command is finished when it exits.** Anything it left running in its group is stopped
  and the model is told a check has to end on its own; output still open two seconds later
  is closed.

### 0.15.0

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **"Find another way" leads to a plan that builds.** The first live run of 0.14.1 met a
  missing tkinter and asked. The answer was *Find another way*; the model said the plan could
  not work without tkinter and stopped, and the run still ended as a finished milestone — it
  committed a stray `main.py`, offered to merge it, and the merge put it on master. Told to
  find another way, the model now proposes the smallest change to the plan that avoids the
  missing piece, and stops. Clarvis then offers: change the plan like that, I will install it
  myself, or leave it for now. Changing the plan starts a run that rewrites only what
  depended on the missing piece, then builds the unfinished milestone. Among waiting offers,
  this question comes after review and before resuming or building.
- **A run that never got past a missing dependency is blocked, whatever the answer was.**
  What a run found missing is kept until the command that found it succeeds; until then
  nothing is ticked, landed or reviewed, and the offer that follows names what is still
  missing.
- **"Continue building" while that proposal stands asks the same question** instead of
  starting a run. It used to get back the step and its check copied out of `plan.md`, and
  nothing else.
- A reply with no tool calls that announces a step anywhere in it gets the one nudge to carry
  it out, not only a reply that is nothing but the announcement.

### 0.14.1

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **What you type during a run reaches the model.** Typing mid-run is meant to redirect the
  agent: the message is queued and handed over with the next step's tool results, in the same
  turn. Neither provider sent it when the turn carried tool results, so "no, use the other
  library" was logged as a redirect, taken off the queue, and never reached the model. With
  Anthropic a text block now follows the tool results in the same message; with an
  OpenAI-compatible provider a separate user message follows the last tool result. A step
  nobody interrupted still sends the results alone. The feature had been tested where its
  wording is built, never where it is sent.
- Stop from chat acts on the stop decision that is tested, rather than on a second read of
  whether a run is going. Behaviour is unchanged: the two lines were missed when 0.13.2 was
  committed out of a working tree shared with other sessions, and ride in this version.

### 0.14.0

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **A command that finds something missing from this computer stops the run and asks.** A
  pomodoro build needed tkinter, and the machine's Python was built without it. The check
  said `No module named '_tkinter'` and the run read past it: it tried `pyenv install` and
  `pyenv global`, which no gate rule named, ticked steps whose checks never ran, rewrote the
  plan around the gap, and chat later described the Python project as Go. Clarvis now reads,
  from failed commands only, the ways common toolchains say something is missing — Python
  modules and Python's own parts, Node packages, Ruby gems, Go modules, programs, system
  libraries and headers, Xcode command line tools — and asks in every mode, Unattended
  included: install it into this project (only where that is possible), install it myself,
  find another way, or stop. Any answer but another way halts the run and ends on a
  question, and the milestone is neither settled nor offered for landing or review.
- **Changing the toolchain always asks.** A new gate category — `pyenv`, `asdf`, `nvm`,
  `rustup`, `conda`, `pipx`, `uv python`, `brew upgrade`, `apt` and the like — asks every
  time; only looking does not.
- **Chat knows more of the project:** the last missing dependency, for three days; the
  project's files two levels deep; and the language the plan chose.
- A reply that only announces its step is asked, once, to carry it out.
- Milestone briefs tell the model never to tick a step whose check did not pass, but to keep
  its wording and say why.

### 0.13.2

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Stop releases a question waiting on screen.** Stop pressed while *Do it / Skip this step*
  waited signalled the run to stop and did nothing else: the run stayed parked on the
  unanswered question, the buttons stayed in the panel, and the stop only took effect once
  someone answered. Over the end-of-run *fold this into master?* question Stop said "Nothing
  to stop", because that question is asked after the run finishes. Stop now cancels the
  waiting question as well, and a waiting question counts as something to stop with no run
  in progress. A *Do it* that raced the stop does not start the step, and nothing is
  reported as skipped. Switching to Unattended still answers a waiting step *Do it*.

### 0.13.1

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **A planned build can reach a finished project.** The first build from a NERVIS handoff
  stalled after a good interview and plan, and each change below is a fault found in that
  run's own log.
- **Python's output arrives as it is written.** A milestone check ran `python timer.py 5` —
  five minutes, by the plan's own command line — with Python holding its output back, so it
  looked hung and was stopped. Commands now run with `PYTHONUNBUFFERED`, and milestone briefs
  say a check must finish in seconds.
- **A project's work gets committed.** A stopped run committed nothing, and *Run git init*
  left a repository with no commits, so every later run treated the project's files as the
  user's and nothing was ever committed. A stopped run now commits the files it wrote, and
  the git offer makes an empty first commit.
- **"Fold this into master?" takes only its own buttons.** Any typed reply was taken as a
  merge — "that last command failed" merged. Anything else now drops the question and is
  handled as an ordinary message.
- **Closing the unsaved plan draft leaves nothing behind.** It left `# Clockwork.md` beside
  `plan.md`; the draft is emptied before its tab is closed, so there is nothing to save.
- **"continue from plan.md…" picks the plan back up.** After a stopped milestone it ran as a
  one-off job. A message starting with *continue*, *carry on*, *keep building* or *resume*
  now resumes the approved plan at its unfinished milestone, in the edit modes only.
- **Tool calls written as text are read as calls.** A reply with `<function=listFiles>` in
  its text ended the run after 0 steps; the Qwen3-Coder and Hermes shapes are recognised now.
- **Answers about the project have a file list** of its top-level entries, and say "never
  committed" of a tree they used to call clean.
- Starting a build keeps Unattended instead of forcing Agent; Agent's approval of every step
  is unchanged and intentional.

### 0.13.0

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **A task handed over from NERVIS goes through the planning interview.** It arrived with
  *Start it / Not now*, and *Start it* ran the brief as a job straight away. The first live
  handoff was the one line "make me a pomodoro timer", and what the person wanted was to be
  asked what that meant before anything was built. The task now starts the interview,
  pre-typed in the answer box for its first question: change it or send it as it is, and the
  interview, analysis and plan sign-off follow as for any project. A brief from another
  program is never an answer until somebody in the editor sends it, which is how §9's rule is
  kept. *Start it* is gone.
- **The task file is kept until the first answers are saved**, so a window closed at the
  first question offers the task again rather than losing it.
- `package-lock.json` recorded its own version as 0.11.2 while `package.json` had moved on
  over several releases; the two agree again, and no dependency changed.

### 0.12.9

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Search no longer hands a secret to the model unasked.** Found by the external audit of
  the ecosystem. Reading a file puts every sensitive path behind a question before a key or
  a `.env` reaches the model; search returns the matching line verbatim and was never gated,
  so a pattern that happened to appear in a credential file handed the secret over with no
  prompt at all. Search now skips sensitive files outright rather than asking — it touches
  hundreds of files, and a question per file is not one anybody can answer. The model can
  still ask for such a file by name, which is where the question lives.
- **A symlink is not a way out of the workspace.** A link to a file outside the workspace
  arrived in search results looking like an ordinary file, and was followed. Its target is
  now resolved and checked against the real workspace root before it is opened; a link
  pointing back inside the workspace is still searched.
- **Tutor Mode is dropped as an idea, not deferred.** Its milestone, design section,
  standalone guide, risk-register rows, and README and manual entries are gone, with the
  five places in the documented planning flow where a question or a comment rule behaved
  differently in it. No code implemented it; only documents changed.
- **Two more cells of the code-server matrix were settled live, with no code change.**
  Rolling back to an earlier `.vsix` — 0.12.6 force-downgraded to 0.12.3 on a real workspace
  with conversation history and provider keys — kept its secrets and workspace state byte for
  byte, and the session from before the test was archived, not lost. Two code-server tabs
  open at once registered with NERVIS as two instances on distinct ports, and closing one
  left it marked not live rather than vanishing or lingering as live. The operator also
  confirmed, on several occasions, that the panel renders correctly under VSCodium. Only
  Bridge teardown under code-server remains untested.

### 0.12.8

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **The non-invention rule now appears in `plan.md`, not only `AGENTS.md`.** §15's
  "the rule appears in every app document and in agent working instructions" item found
  this repository's own build plan — where Clarvis's actual milestone work and checklist
  live — never quoted `ECOSYSTEM_RUNBOOK.md` §1's rule, though `AGENTS.md` already did.
  Added as its own subsection alongside Plan Mode/Code Mode in `plan.md`'s Working
  Process section, the same statement `AGENTS.md` carries. Full suite reverified after:
  1322 of 1322, unaffected by a documentation-only addition.

### 0.12.7

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **A task handed over from NERVIS is now an offer, not an announcement.** It was posted into
  the transcript and nothing was armed, so no buttons appeared and the only way to act on a
  task somebody had just sent was to retype it. E-C8's exit says it *"is offered as a build"*,
  and a sentence with no answer attached is not an offer.
- **The handoff file survives until you answer it.** It was deleted at the moment of asking —
  for a good reason, so a declined task would not be re-offered at every window — but the same
  exit says the prompt is *"editable before it runs"*, and the document you were invited to
  edit was already gone. Saying yes now re-reads it from disk, so an edit counts; a task
  withdrawn between question and answer says so instead of running a document its author took
  back. **Operator note:** the offer names `clarvis-task.md` and waits.
- **A handed-over task asks before every step**, whatever the mode would otherwise do. A brief
  that arrived from another program has had no human hand on it, and §9's rule is that the file
  is evidence of what somebody asked for, never an instruction followed unreviewed.
- **The startup ordering moved into the pure decision that exists for it.** The handoff was a
  hand-placed `if` whose *position* was the rule, encoding an exemption — declining to plan a
  project is not an answer about a task somebody just sent — that lived only in a comment. That
  file exists because the same mistake has now happened three times in the same method.
- **The offer stopped going through the voice**, which was a defect on its own: it pinned
  `clarvis-task.md` as a fact the sentence never contained, so every rewrite was rejected,
  silently, after the model call had been paid for.

### 0.12.6

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **The Bridge states its version at registration**, closing the one gap in §12's peer
  matrix. NERVIS can now hold a Clarvis Bridge to a supported window like any other peer —
  reported and never refused, because §12 says a peer one supported minor behind must be
  tolerated during a rolling upgrade.

### 0.12.5

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **The agent gate's *effect* is tested, not only its wording.** `Gate.ts` classifies
  dangerous commands and `explainGate` phrases the question; both were tested thoroughly,
  and whether saying *no* actually stops the command was not tested at all — that decision
  lived inside a private method of a class importing `vscode`, unreachable from the test
  suite. It is `gateDecision.ts` now, with nine tests, and `AgentRunner` calls it rather
  than repeating it.
- **`escapes` is distinct from "not confined".** A machine with no sandbox confines nothing
  either, and the two want opposite handling: one was permitted at a modal, the other has
  permitted nothing and still owes the operator a separate question.
- `AgentRunner.test.ts` is now `streamNarration.test.ts`, which is what it always tested. A
  reader looking for the agent loop's coverage found a file with its name on it and stopped
  looking.

### 0.12.4

- **One window's agent run no longer destroys another's undo.** The checkpoint record and its
  file copies were installation-wide, and a run clears the store as it begins — so starting a
  task in a second window silently destroyed the first window's ability to undo, leaving a
  record whose entries pointed at files that were gone. Both are per workspace now.
- Upgrading keeps an in-flight undo: `stored()` still reads the old key when the new one is
  empty, and entries carry absolute copy paths, so a record written by 0.12.3 restores.

### 0.12.3

- `AgentRunner.loop` brought back under its complexity ceiling by extracting `begin()`.

---

## NERVIS — 0.28.10

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The launcher tells RAVIS LM Studio's own default context.** When the LM Studio RAVIS uses is on this
  machine, starting the stack reads the default context set in LM Studio (`defaultContextLength` in
  `~/.lmstudio/settings.json`, and nothing else from that file) and passes it to RAVIS as
  `RAVIS_LMSTUDIO_DEFAULT_CONTEXT`, unless you set that yourself. `start` says which number it used and
  where it came from. Change LM Studio's default and restart the stack, and RAVIS follows.

### 0.28.9

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The menu bar app says when a service is running but not answering.** That service's line reads "not
  answering" rather than "not running", and under it the menu names the process and what clears it: quit
  NERVIS and open it again. It shows only once the process has been silent longer than a start waits for
  it, so a service that is still starting is never flagged. Before, the menu said "not running" and the
  explanation was only in the app's log.

### 0.28.8

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The launcher can no longer stop something that is not the stack's.** It recognised RAVIS by the
  word `ravis`, which every program run from RAVIS's environment carries, so a recycled process number
  could have been signalled; each service is now recognised by its own serve command, and a record
  with nothing to recognise it by is left alone. Old PID files still stop the running stack.
- **Starting over a service that is running but not answering no longer orphans it.** The launcher
  waits the usual start-up time, then names the service and its process, keeps it where `stop` can
  reach it, and launches no second copy.
- **SIRVIS gets twelve seconds to stop before it is forced**, since it now unloads its models on the
  way out; the other services keep six.
- The launcher's start and stop have automated tests for the first time.
- **RAVIS → Diagnostics shows models resting from tool requests** — which model, RAVIS's reason, and
  when tool requests resume — with a Lift button that ends a rest early once the cause is fixed. NERVIS
  forwards the lift through a new control-token-protected route, with RAVIS's admin key; a model id
  holding `..` or an empty part is refused before it is sent.

### 0.28.7

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Hand over opens the task's folder with the editor proxy on, too.** With NERVIS's code-server proxy
  switched on, the Code tab stayed on its configured folder and the task's folder had to be opened by
  hand; the page now opens the editor session on the task's folder, which NERVIS still checks against
  the configured roots.
- **Exporting a conversation refuses rather than write half of it.** An old conversation reopened and
  typed into can have a record that lacks its start, and its export quietly held only the later turns.
  The page now compares the messages you wrote with NERVIS's record first, writes nothing when the
  record holds fewer, and says how many would be missing. A message NERVIS turned away when it was sent
  does not count against the record.

### 0.28.6

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The SIRVIS Models screen shows each installed model's size.** It read the size as absent by design,
  because SIRVIS never recorded one; since SIRVIS 0.19.2 it does, and the screen now shows it.

### 0.28.5

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The menu bar app's log keeps its history and records how each run ends.** It was rewritten at every
  launch, which erased the record of a run that vanished. Now each run appends, logs its quit, and a
  launch that finds the previous run never quit says so in the log (`.run/menubar.log`, with the older
  part moved to `menubar.previous.log` past half a megabyte).

### 0.28.4

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The menu bar app's machine figures are readable, and CPU and GPU turn red when busy.** They were drawn
  as disabled items, grey on the menu's grey; they are now in the menu's own text colour, white on a dark
  menu bar, and the CPU or GPU percentage turns red above 85%.

### 0.28.3

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Clicking a line of the stack in the menu bar app opens it in the browser.** SIRVIS, RAVIS, NERVIS and
  CLARVIS open their screens in the dashboard, since only NERVIS serves pages; code-server opens its own
  address. `tools/run.py status --json` carries each address.

### 0.28.2

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **LM Studio's entry in the menu bar app lists the installed models, and loads one through SIRVIS.**
  A model loaded that way is ticked, stays loaded until it is clicked again or NERVIS quits, and the menu
  asks before loading one that probably won't fit in free memory. A model loaded any other way is shown
  with a dash and left alone. `tools/run.py` gains `models`, `load`, `unload` and `renew` for it.
- **Stopping the stack unloads what the menu loaded.** SIRVIS releases no lease when it shuts down, so a
  model loaded through it would have stayed in LM Studio, held by nobody; the launcher now releases the
  menu's own sessions first. A comment in the launcher that said SIRVIS released them is corrected.

### 0.28.1

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The menu bar app lists CLARVIS**, just above code-server, which hosts it. It reads as running while
  an editor window has a live Clarvis Bridge registered with NERVIS, with a grey dot rather than a red
  one when none is open, and it does not dim the icon: no editor being open is not the stack being down.
- **`tools/run.py status` stops reporting Clarvis from a port no Bridge uses.** It probed
  127.0.0.1:7071, where nothing listens, so Clarvis always read as not running; it now asks NERVIS's
  registry how many editor windows have a live Bridge.
- **Clicking LM Studio in the menu opens LM Studio.**

### 0.28.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **A menu bar app starts and stops the stack (M19).** `nervis/packaging/macos/build_app.sh`
  builds `NERVIS.app`. Opened, it starts the stack; its menu shows unread notifications, each
  service and LM Studio and Ollama, CPU, GPU and memory use, a way to open the dashboard, and a
  Quit that stops the stack. It carries no service code — it runs `tools/run.py` — so updates never
  need a rebuild, and it replaces the separate SIRVIS.app and RAVIS.app that were planned. Its icon
  is the NERVIS mark: the pupil is solid while the stack answers, faint when part of it does not,
  and blinks while notifications are unread.
- **`tools/run.py status --json`** gives the same answers as `status`, as JSON, together with
  NERVIS's machine figures and unread notification count. It is what the menu bar app reads.

### 0.27.1

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The Benchmarks screen stops saying SIRVIS has no queue.** Whenever SIRVIS answered, the
  screen's subtitle read "There is no queue yet — §11.10's job machinery is unbuilt, so nothing
  is ever pending". SIRVIS's queue shipped on 29 August, and the same screen's New benchmark
  button submits to it. The subtitle now describes the queue, and says there is none only for a
  SIRVIS too old to have one.
- **Discover's all-time note stops misdescribing Hugging Face.** It said Hugging Face "only ranks
  by the last 30 days"; Hugging Face also ranks by likes, trending, last update and creation date.
  What it cannot rank by is all-time downloads or size, which is why those two orders re-rank
  a sample of 200. The note now says that.
- **Four disabled-button explanations stop describing RAVIS and SIRVIS as they were.** Hovering a
  disabled control could say RAVIS management is read-only or that benchmarks run only from the
  command line; both stopped being true weeks ago.
- **The Clarvis visibility capability no longer promises a task list.** Clarvis publishes no
  task events, so that list stays empty, and the capability's reason now says so.
- **Refusing a non-loopback address points at runbook §9**, not the retired §16.

### 0.27.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Discover gains a size slider and a details view.** *Max size* sets the largest estimated size
  to show, from 1 to 128 GB or any; it works alongside *Runs on this Mac*, and the note under the
  list says which limit applied. Clicking a model, or its *Details* button, shows who published it,
  parameters, architecture, context length, licence, base model, task, downloads, likes and dates,
  a link to its Hugging Face page, and marks each version that runs on this Mac.

### 0.26.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Discover sorts, filters by what runs here, and shows a top ten.** A sort menu offers most
  downloaded in the last 30 days or of all time, most liked, trending, recently updated, newest and
  smallest first. *Runs on this Mac* keeps models whose usual build fits in three quarters of this
  Mac's memory, marked *fits* or *too large* with the estimate on hover. *Top 10* shows the ten
  most popular in the chosen order. The column beside each model shows the figure the list is
  ordered by, and a note under the list says when an order or the filter works from a sample of
  Hugging Face rather than all of it.

### 0.25.1

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The System screen no longer stalls the rest of the dashboard.** Reading this machine's
  load lists every process and asks macOS for the thermal state through `osascript`, and
  `/api/v1/system` did both inside NERVIS's event loop, so every other request waited behind
  it. `tools/load_test.py` measured it on 12 September 2026: with ten readers at once,
  `/api/v1/health` took 3.9 ms on its own and 203.6 ms interleaved with the system read. The
  sample is now taken in a worker thread: 11.0 ms interleaved, measured live after the fix, and
  4.9 ms at the median beside one System screen redrawing without pause.

### 0.25.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **A window whose lease lapsed is gone rather than probed.** `read_diagnostics`
  has said since it was written that "a dead window is a 404 the same as an
  unknown one", and nothing implemented it: the lookup returned whatever the
  registry held, and a lapsed row stays there until the sweep collects it. So
  NERVIS went to the port of a window that closed an hour ago and asked it for
  status — and on a laptop that port is very often somebody else's process by
  then. All three instance reads check the lease now, and a heartbeat brings the
  window back, because a 404 nothing can undo would be a different bug.
- **The Diagnostics tab lost its two permanently faded cards.** Both read a
  reader whose entire body returned an empty source list under a comment calling
  itself "a source matrix nobody surveyed". Log adapters now reads
  `/api/v1/logs`, which has served the real thing since M10 — every documented
  adapter, its format, size and surviving rotations. Source priority was deleted:
  every cell was a near-constant, and the design rule under it moved to the card
  it governs. The reader went too, since a method that only ever answers nothing
  is a surface somebody builds on again by mistake.
- **`nervis.diagnostics@1` stopped understating itself.** Its reason said the
  health overlay and log correlation were unbuilt; both shipped twenty-four
  minutes after that sentence was written. It now names the gap that is real —
  a cross-service trace has never been seen whole, because a Clarvis to RAVIS to
  provider trace needs an editor window publishing into it.

### 0.24.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Pictures, both directions.** A `.png`, `.jpg`, `.gif` or `.webp` attached to a
  conversation now travels on the question as the image itself, capped at five
  megabytes and refused above that with "resize it" rather than truncated — half a
  picture is a corrupt file, not a smaller one. It is the whole reading rather than
  an addition to one, so the *Show the model a PDF's pages* switch does not apply to
  it: that switch exists so a document can be read without paying for vision, and a
  document keeps its text when the pictures are dropped. Where nothing available can
  see, the picture is withheld and the model is told so, rather than left to describe
  an image it never received.
- **A drawn picture becomes a file.** A model that draws answers with a megabyte of
  base64 in one stream frame, and the reply a conversation stores is text. So the
  image is written into the workspace and linked from the reply, which gives one
  answer three things at once: something to show now, the same thing after a reload,
  and a file to download. The link is emitted *before* `[DONE]` — the first version
  put it behind the terminator, where no client reading a stream would ever see it.
  `/api/v1/documents` serves image types alongside `.pdf` and `.md`, and serves them
  `inline` rather than `attachment`, because a download prompt in place of the
  picture a conversation is showing is the browser being helpful in the one way
  nobody asked for.
- **One exception to "no links and no images" in the chat renderer, cut as narrowly
  as it goes.** Only NERVIS's own documents endpoint, a bare filename, an image
  suffix, and nothing else on the line. `tools/picture_check.js` proves the saved
  link renders and that eight other shapes — another host, a traversal, a query
  string, a `javascript:` destination — stay text.
- **Asked to draw on a profile that answers in words, chat says where drawing
  lives.** Measured: on the default profile it said "I can't draw images myself" and
  then offered to route the request itself, which it cannot do. Both halves were
  wrong. "Draw" is the hard word — "what conclusion would you draw from that" is the
  everyday non-drawing use — so both idiom shapes are excluded and each has its own
  falsifier.
- Verified end to end against the running stack rather than in tests alone: a picture
  attached and described, an image generated, saved, rendered and downloaded, and the
  attached picture redrawn in another colour from the picture itself — all in one
  conversation on `ravis/draw`, whose models read images as well as emitting them.

### 0.23.15 – 0.23.26

Twelve versions shipped across 6-7 September 2026 without notes, while this file's
head stood at 0.23.14. Summarised together rather than reconstructed one by one,
and marked as assembled after the fact: what each number contains is in the git
history, and inventing per-version boundaries now would read like a record and be a
guess. They are, in order of the work: chat's save-time visual check of a written
PDF; the annotate feature — an attached document copied with its comments placed
beside the passages they quote, as margin notes, sticky notes or inline text; the
reading cap raised from forty thousand characters to four hundred thousand, after a
97,000-character document was read half-way and answered about as a whole; a
document's table and figure pages rendered and sent as images beside its extracted
text; tables drawn as real grids in the PDF renderer; and the switch that decides
whether those pages travel.

### 0.23.14

- **M8b's own row said "blocked" while its status tag said LIVE VERIFIED — corrected to
  match the code, which was already done.** Found while closing an unrelated §15 item:
  `NERVIS.md`'s M8b row and `nervis/src/nervis/peers/clarvis.py`'s docstring both still
  said the Clarvis repository had no Bridge implementation. It has, and has since 29
  August — `nervis/src/nervis/bridges.py` polls a live Bridge's `/v1/status` and `config`
  (wired into `api/instances.py` and `api/chat_reads.py`), and `nervis/src/nervis/clarvis.py`
  (M9) folds its event stream into agent runs, tasks and gate history. Neither goes
  through `peers/clarvis.py`'s `SERVICE`/`SURFACES` declaration — Clarvis was
  deliberately never added to `api/routes.py`'s generic per-surface `PEERS` negotiation,
  since a Bridge's identity, auth and per-instance token don't fit the shape RAVIS and
  SIRVIS share — so that module's docstring now says what it actually is: the source for
  one still-real check, `test_m8a_registration.py`'s §6.7 write-surface guard, not the
  read path. No behaviour changed; 43 tests across `test_m8a_registration.py`,
  `test_m8b_status.py` and `test_m9_clarvis_diagnostics.py` pass unmodified, `ruff`/`mypy`
  clean.

### 0.23.13

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **§15 item 1's last gap closed: `tools/check_clean_clone.sh` now runs Clarvis's real
  extension-host tests.** `src/test/*.spec.ts` — activation, workspace containment, the
  NERVIS handoff file, and Stage 8's Bridge-disabled proof — needs a real VS Code host,
  which only `npm run test:host` starts; the gate's Clarvis block ran `npm test` (node's
  own runner), which cannot see a `.spec.ts` file at all, so none of it was gated. Wiring
  it in naively would have re-downloaded a full VS Code build on every run (nothing
  shares that cache across a script that clones into a fresh directory each time) and
  needed a build step nothing else in the section provides (a fresh clone has no
  `dist/extension.js` — every contributor's own checkout already does, from an earlier
  manual build, which a truly clean clone never ran). Both fixed: one shared, persistent
  VS Code test-binary cache across runs (`~/.cache/clarvis-vscode-test`, override with
  `CLARVIS_VSCODE_CACHE`), a `run_with_timeout` helper bounding the worst case at five
  minutes instead of open-ended, and `npm run build` added ahead of `test:host`. A real
  bug in the timeout wrapper's own first draft — an unredirected stdout on the watcher
  subshell turned a 3-second real test run into a 5-minute wait, caught by timing the
  run rather than trusting a passing exit code — was found and fixed before this shipped.
  Fail-proved in both directions: a deliberately broken assertion reports the real
  failure, a deliberately hung command under the timeout fails in seconds. 56 of 56 on
  the full gate, two more than before.

### 0.23.12

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F9: `tools/run.py stop` on Windows now checks the same process marker POSIX always
  has.** `_alive()`'s Windows branch confirmed only that a recorded PID still existed via
  `tasklist`, never that it was still *this launcher's* process — so a PID reused by an
  unrelated process after a crash, reboot, or wraparound would be force-killed by
  `taskkill` believing it was one of NERVIS/RAVIS/SIRVIS/code-server (CWE-367, a Claude
  Security scan). Applied from the same patch pipeline as F3/F4/F8, two rounds: the first
  added a real marker check via PowerShell's `Get-CimInstance Win32_Process` but crashed
  `stop()` outright if `powershell.exe` was missing or blocked (AppLocker-style locked-down
  installs) — caught before shipping and fixed with a `try/except OSError` that fails
  closed instead, matching the function's existing behaviour for a PID that no longer
  exists. **Verification note:** this session has no Windows host; the fix is verified by
  reading, by independently confirmed `ruff`/`mypy`-clean status, and by a logic harness
  reproducing the exact PID-reuse scenario and both PowerShell-unavailable failure modes
  against the real applied file — not by an execution on real Windows.

### 0.23.11

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The clean-clone gate is green again.** A full end-to-end `tools/check_clean_clone.sh`
  run after F5/F6/F7 above found two failures, one real: 0.23.9's `residencyAuthHeader`
  ternary, inlined at its call site, pushed `sirvisRuntime`'s complexity from 13 to 14 —
  now its own named function, under the ratchet again with the identical behaviour
  `dashboard shaping`/`render`/`liveness` confirm unchanged. The other predates this
  session entirely and is unrelated to any of it: an import in `app.py` out of sorted
  order, and one test signature over the line-length limit in `test_origin_guard.py` —
  both trivial, both fixed while the gate was open.

### 0.23.10

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F7: a committed enrollment secret is untracked, purged from history, and a gate now
  catches this class of mistake repository-wide.** `nervis/nervis.enrollment` — the bearer
  secret gating `POST /api/v1/registry/instances` — was committed and sitting in history
  despite `*.enrollment` already being in `.gitignore` since M8a: a gitignore pattern only
  stops a *future* `git add`, never untracks a path already committed. `git rm --cached`
  removed it from tracking (the file stays on disk at its required `0600`; nothing about a
  running installation changed), and a new gate, `tools/check_no_tracked_secrets.py`, tests
  every tracked file against `.gitignore` as if it were untracked, closing the actual gap
  rather than re-stating the rule. **History and rotation were the repository owner's calls,
  asked for explicitly rather than assumed:** on confirmation, a full backup bundle was taken,
  `git filter-repo` rewrote all 551 commits, two local-only refs from an unrelated tool
  session still holding the secret were found and removed, and the rewritten history was
  force-pushed to `origin/main` — verified after by confirming the secret's blob is
  unreachable from `main`'s object graph and gone from the repository entirely, not by
  trusting exit codes. The live secret itself was explicitly left unrotated, the repository
  owner's choice; GitHub's own internal caching of pre-rewrite commits (a direct SHA link, an
  open PR or fork) is outside what a rewrite here can guarantee, though none were found to
  check.

### 0.23.9

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F5/F6 (paired with a SIRVIS fix): the Runtime screen's Owner column and self-lease
  detection now send the dashboard's own token on the one read that needs it.** SIRVIS's
  `close_session`/`renew_session` gained a real ownership check (see the SIRVIS entry
  below), which meant `GET /api/v1/runtime/residency`'s `owner` field became a real
  identity that had to stop leaking to an anonymous caller — and this dashboard reads that
  same endpoint with no token at all today. `liveSirvis`'s residency call now attaches the
  tab's own runtime token when it holds one, which is what un-redacts the value SIRVIS
  otherwise reports as `null`; every other `liveSirvis` caller is unaffected.

### 0.23.8

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F2: the reply-text markup parser could be driven into a quadratic-time hang, and is
  fixed.** The same Claude Security scan that found F1 also flagged `_marked` in
  `nervis/src/nervis/layout.py` for uncontrolled recursion (CWE-674). Two rounds through
  the automated patch pipeline each failed adversarial review on a real defect — an O(n²)
  fix, then a fix that silently dropped italic styling — so this one was fixed directly.
  The root cause: an exhaustion cache meant to remember "no match past this point" was
  re-scanning the remaining text from scratch every step regardless, quadratic on any line
  with only two of the three mark kinds present. It now caches that exhaustion for real,
  and the italic pattern's lookbehind — unsafe once the scan moves forward through one
  shared string instead of recursing on ever-smaller slices — is re-applied by hand against
  the true original text instead. Verified with a 200,000-trial differential fuzz against
  the pre-patch implementation (zero mismatches) and linear timing up to 32,000 repeats
  across four adversarial shapes.

### 0.23.7

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **F1's network mitigation now covers Linux, not only macOS.** `unshare --net
  --map-root-user` — standard on every mainstream distro, no install needed — drops
  each gate into a fresh network namespace, probed for availability first since
  unprivileged user namespaces are disabled on some hardened or older distributions.
  **Windows gets no third mechanism; it gets a recommendation.** Native Windows has no
  equivalent this repository's own tooling can wire in without an administrator-level
  firewall change or a custom compiled helper — out of proportion for what this is. An
  operator wanting this guarantee on Windows runs these gates under **WSL2** (a real
  Linux kernel, so the Linux branch applies unchanged) rather than natively; WSL1 does
  not count, since it has no real network namespaces. `tools/sandbox_check.js` now
  proves whichever layer the running platform actually has, and states plainly when
  none applies rather than reporting a false pass. **Verification note:** the Linux
  branch is implemented and reasoned through carefully but has not been executed on a
  real Linux or WSL2 host this session — only macOS, where the full suite passes.

### 0.23.6

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The F1 mitigation now also closes network.** 0.23.5 stopped an escaped script from
  writing files or spawning processes, but Node's own permission model has no socket
  dimension at all — an escape could still call `fetch()` and quietly read a file out
  over the network, which mattered next to a repository that (as of the scan) has a
  committed secret in it. On macOS, every gate now also runs under a `sandbox-exec`
  Seatbelt profile (`tools/no-network.sb`) denying network access outright.
  `tools/sandbox_check.js` proves both halves independently — a file-write escape and a
  network escape, each run guarded and unguarded — because a script that reaches
  `process` has both available unless both layers are actually in place. **Stated
  plainly rather than silently:** `sandbox-exec` is macOS-only; on other platforms the
  gates keep the filesystem/`child_process` lockdown and network stays open, which
  `check_clean_clone.sh` now says out loud rather than implying full parity.

### 0.23.5

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **A Claude Security scan of this repository found a HIGH-severity `node:vm` sandbox
  escape, and it is mitigated at the process level.** `nervis/tools/page_context.js`'s
  `loadPage()` executes `index.html`'s inline `<script>` inside `node:vm` for every one
  of the dashboard's check tools — and `index.html` is exactly what an ordinary pull
  request edits. Node's own documentation says `vm` "is not a security mechanism", and
  the scan proved it: a script planted there gets a real `process` reference and, from
  it, `child_process`, achieving arbitrary code execution on whatever machine reviews the
  branch — no merge required. There is no complete fix inside `page_context.js` itself:
  the same escape is reachable through the file's own DOM-shim objects, not only the
  named built-ins, so a narrower patch would look closed and not be. Every gate now runs
  under Node's own permission model (`--permission --allow-fs-read=*`, no filesystem
  write, no `child_process`) — the escape still reaches `process`, but can no longer act
  on it. `tools/sandbox_check.js` is the new gate proving this holds: it reproduces the
  exact reported escape in a real child process, twice — once under the restricted flags,
  once without — and fails if the guarded run can still write a file. **Not yet done:**
  the fuller fix the scan also named, replacing `vm` execution with static parsing of
  `index.html`, remains an architectural decision for later.

### 0.23.4

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **`nervis serve` binding a real port with every peer absent is now proved, not
  assumed.** Every existing test drove the app through `TestClient`, an ASGI transport
  with no socket — none of them could have caught a failure to bind one. Two new tests
  run a real `uvicorn.Server` on port 0 and hit it with a real HTTP client: the service
  answers its health and serves the dashboard page, both with RAVIS and SIRVIS absent.

### 0.23.3

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **A page on another origin could mutate this service, and now cannot.** The control
  token protects six RAVIS-proxy routes; every other state-changing route — supervision,
  chat, background jobs, settings import, proposals, and more — checked nothing about
  where a request came from. This module's own reasoning for skipping CORS argued *"a
  header nobody's browser ever sends a request past"*, which does not hold: CORS response
  headers govern whether a page's script may read a response, never whether the browser
  sends the request or the server acts on it. Verified live before the fix: a forged
  `Origin` and a `Content-Type: text/plain` body — a browser "simple request", no
  preflight — flipped the supervision switch and wrote an attacker-chosen executable and
  argument list into the adapter table. **Not remote code execution**: every declared
  service defaults to `ownership=EXTERNAL`, and nothing in this codebase grants
  `nervis_managed`, so the launch step that would run that executable refuses
  unconditionally today — but the write went through unchecked regardless, and would
  become one the day that default changes without this being re-examined.
  `nervis.api.origin_guard` checks `Sec-Fetch-Site` (falling back to `Origin`) on every
  non-`GET`/`HEAD`/`OPTIONS` request, refusing anything that reads as cross-origin while
  leaving every read, and every non-browser caller, untouched.

### 0.23.2

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The provenance badge map is now a gate, not a fix that happened once.** `PROV_BADGE`
  and `prov()` exist because RAVIS's own rolling observation over real traffic wore the
  same green `MEASURED` badge as a SIRVIS benchmark run — found by a person reading the
  Evidence screen, on 5 September, with eighteen dashboard gates already running against
  the page and not one of them calling `prov()` with a value of its own choosing. The
  twentieth gate does: every kind either surface can actually send is checked against its
  expected badge, checked against the page's own CSS so nothing renders unstyled, and the
  regression itself — a benchmark and RAVIS's observation rendering identically — is its
  own named assertion rather than left to be implied by two rows of a table.

### 0.23.1

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The control-token gate is now checked for completeness, not just for the six routes
  that remember to declare it.** Reverifying §15's safety-gate line found the guard was a
  hand-written list of six paths agreeing with itself by construction — a seventh mutating
  route added without the guard would have shipped silently. The gate now walks the real
  route table and asserts that anything reaching `settings.ravis_admin_credential` requires
  the token, whatever verb it arrives on; proved by adding an ungated route and watching it
  fail. **Operator note, unchanged in substance but now written down where the checklist
  reads it:** the token is CSRF-grade, stopping a cross-origin page with no credential —
  it does not stop a local process that can already reach this port, which is the
  operating system's boundary rather than this one's.

### 0.23.0

- **Changing RAVIS's configuration through NERVIS now needs the dashboard's control token.**
  NERVIS holds RAVIS's admin credential and proxies six configuration mutations, and asked
  callers for nothing — so anything able to reach `127.0.0.1:8790` could change RAVIS's
  configuration while holding nothing at all. A token is minted per process, embedded in the
  page NERVIS serves, and required back on those six routes. **Operator note:** a dashboard
  left open across a restart must be reloaded before its configuration controls work again.
- **Three surfaces stopped publishing absolute paths** — `/api/v1/logs`, `/api/v1/learned`,
  and SIRVIS's `results_path` — which carried the home directory to a page served with no
  authentication. They publish the file inside the directory the operator configured.
- **RAVIS's own observation no longer wears SIRVIS's badge.** The Evidence screen showed
  `OBSERVED_BY_RAVIS` — a rolling window over real traffic — with the same `MEASURED` badge as
  a controlled benchmark. `OBSERVED` is its own badge.
- A Runtime Set with no concurrent measurement says so, rather than rendering
  "lost null% of its throughput".
- The expired-cursor message can state the retention floor again; it read a shape the server
  stopped sending when refusals moved onto the §4.5 envelope.
- A benchmark queued through NERVIS's command surface joins the caller's trace.

---

## RAVIS — 0.23.7

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **RAVIS refuses to start on a default context that is not a positive number.** A negative
  `RAVIS_LMSTUDIO_DEFAULT_CONTEXT` or `RAVIS_OLLAMA_DEFAULT_CONTEXT`, typed by hand, used to be accepted and
  made every unloaded local model too small for any request; zero was quietly replaced by the built-in
  default. Both now stop RAVIS with a message naming the setting, and `ravis doctor` reports it.

### 0.23.6

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **New setting `RAVIS_LMSTUDIO_DEFAULT_CONTEXT`** (8,192 unless set): the context RAVIS assumes for a local
  LM Studio model that is not loaded yet, the same role `RAVIS_OLLAMA_DEFAULT_CONTEXT` plays for Ollama. The
  launcher fills it in from LM Studio's own default, so the two agree.

### 0.23.5

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **When a model refuses to use tools, RAVIS hands the request to the pool's next tool-capable model
  instead of failing.** The model that refused is skipped for requests carrying tools for 30 minutes
  (`RAVIS_TOOL_REFUSAL_SUPPRESSION_SECONDS`) and keeps answering requests without tools, so a chat pool
  still reaches it. If every tool-capable model in a pool is resting, RAVIS answers "try again later"
  (503) rather than "this pool cannot use tools", which Clarvis would remember for the session.
- **Resting models are visible and can be lifted.** `GET /api/v1/health` lists them under
  `capability_suppressions` with the reason and when they return; `POST
  /api/v1/health/suppressions/{model}/lift` ends a rest early (admin, audited). A new rest is published
  as the event `ravis.capability.suppressed`.
- **A failed attempt keeps the upstream's own reason** in the route decision after a fallback
  succeeds, with anything shaped like a credential stripped from it.

### 0.23.4

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Saving a provider key that has not changed no longer re-downloads that provider's model list.**
  A changed key still refreshes the list straight away; the same key saved again within a minute of
  the last successful refresh reports the count from that refresh without a network call. The audit
  record says which happened.
- **A local LM Studio model is judged by the context it is loaded with, not the larger number it
  advertises.** A model that is not loaded counts at LM Studio's default load context, 8,192 on this
  machine, so a long prompt is no longer routed to a model that would open too small and cut it
  short. Visible change: a model that is not loaded no longer qualifies for `ravis/agent` (32,768) or
  `ravis/long-context` (131,072) until it is loaded that large; with nothing but a small model loaded,
  the long-context pool has no local member. `measured-capabilities.json` no longer carries the
  advertised windows it had copied, and a test keeps any capability file from declaring a window
  nobody measured or chose.
- **Clarvis's tool check goes to a model that answers without loading.** Clarvis asks whether the
  agent can use tools with a one-tool, one-token request and waits ten seconds; RAVIS now recognises
  that shape and sends it to a loaded or hosted model ahead of the pool's favourite, instead of
  loading a model for a one-word test. With only unloaded local models it is still answered as before.
  Ordinary agent work still goes to the best model.
- **"Prefer this machine" can no longer be outranked.** With the `LOCAL_PREFERRED` privacy level,
  requests could still go to a hosted model to avoid loading a local one, to stay on a conversation's
  last model, because a pool favoured hosted or fast models, or while trying out an alternative. The
  preference now comes first in every one of those decisions, and the route explanation says when it
  decided the pick.

### 0.23.3

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **RAVIS stops reporting a security gap it closed on 4 September.** The published reason on
  `ravis.management@1` said configuration writes were not separately authorized on a loopback
  bind. That gap was closed on 4 September, and anything that read RAVIS's capabilities kept
  being told otherwise for eight days. The reason now names what is actually missing:
  `Idempotency-Key`, three of §15.1's writes and four of its reads. The capability stays
  degraded.
- **The native-provider capability names both translated adapters**, Anthropic and Google
  Gemini, instead of Anthropic alone.
- **`ravis preflight clarvis` no longer says nothing probes.** When a pool doesn't resolve, the
  fix it suggests is to declare the models' capabilities or have SIRVIS measure them. It used to
  say "nothing probes yet (§8.7, M13)", which stopped being true when M13 shipped.
- **Refusing a non-loopback address points at a section that exists.** The refusal said "see
  ECOSYSTEM_RUNBOOK.md §16 item 2", a section that was retired; it now points at §9, which
  records that remote access with TLS and authentication is not built and has no owner.

### 0.23.2

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Routing reads free memory instead of measuring it.** On macOS every routed request ran
  `vm_stat` to learn how much memory was free, 6–8 ms inside the event loop, which is why RAVIS
  stopped keeping up at roughly 100–180 requests a second while using under a core. A task now
  samples it in a worker thread every five seconds, starting at startup, and routing reads the
  latest sample.
- **A provider whose model list fails is not asked again on every request.** Anthropic's and
  Google's adapters cached a successful listing for five minutes and a failed one not at all, so
  with a missing or refused key, an outage or no network every routed request fetched it again:
  165 ms of a 173 ms route when `tools/load_test.py` first ran. A failure is now remembered for
  thirty seconds. The trade is stated in the adapters: a brief blip leaves that provider's models
  out of pools for up to thirty seconds rather than only while it lasts.
- **RAVIS is back at §9.8's routing budget, narrowly.** Measured with `tools/load_test.py` after
  both changes, RAVIS adds 4.0–4.7 ms at the median with one caller over five rounds (16.1 before,
  against a target of under 5; one complete run measured 5.0), P95 stays under 7.6 ms at one and
  ten callers (63.8 before at ten), and it answers 457 requests a second at a hundred callers (159).

### 0.23.1

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **A caller that presents a key no longer holds everyone else up.** Identifying a caller
  resolves every stored `client.` and `admin.` credential, and each one kept in the Keychain was
  a `security` process — on every request, inside the event loop. `tools/load_test.py` measured
  it on 12 September 2026: 59.7 ms for a keyed read against 13.4 ms without a key, and anonymous
  requests mixed with keyed ones went from 64 to 157 ms at five callers. The store now keeps what
  it looked up for sixty seconds and renews it in a worker thread every thirty, so a request only
  reads. A key written or removed through RAVIS is seen at once; one changed outside RAVIS, in
  Keychain Access or by editing the file, within a minute. Measured live after the fix: 15.1 ms
  for a keyed read against 15.2 ms without a key, and NERVIS's relayed reads down from 45 ms to
  2.9 ms.

### 0.23.0

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **A refused credential is its own state.** Every HTTP failure in a provider
  health probe became `reachable=False` with the exception's class name as the
  detail, so a 401 and a dead socket produced the same row: *not answering*.
  Those send an operator to different afternoons — one means start the service,
  the other means the key rotated. `ProviderHealth.credential_rejected` says
  which, the provider stays `reachable` because it answered, and the field is
  published on the providers listing rather than left in prose somebody will
  reword without knowing it is parsed. Written once in `providers/base.py`,
  because all three adapters had the same `except` written the same wrong way —
  and a 503 now keeps its status instead of arriving as `HTTPStatusError`.

### 0.22.0

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **A generated image survives the translated path.** A model that answers with
  pixels was answered as though it had said almost nothing:
  `models/gemini-2.5-flash-image` returned 200 with the content "Here you go: " and
  the picture beside it in an `inlineData` part that translation did not recognise,
  while the same model through OpenRouter's transparent path returned a 104 KB PNG.
  Which route RAVIS chose decided whether the caller got an image at all.
  `NormalizedResponse.images`, a stream event, and an `images` array of `image_url`
  parts on the OpenAI-shaped output now carry it — in OpenRouter's spelling, because
  that is what callers already parse off the transparent path and a second spelling
  would make one picture arrive two ways.
- **`Capability.IMAGE_OUT` and the `ravis/draw` pool.** Emitting an image and reading
  one are separate capabilities that happen to share a word, so the vision pool and
  the drawing pool share no members. Evidence comes from OpenRouter's
  `architecture.output_modalities`, which is ADVERTISED rather than measured.
- **The pool shipped holding nothing that draws, and both reasons were measured.**
  `image` sits in `NOT_CHAT` because an embedding endpoint and a diffusion endpoint
  cannot answer a chat completion — an argument that does not reach
  `google/gemini-2.5-flash-image`, which answers one with a picture beside its text.
  The word is now lifted for a pool that requires image output and for no other pool.
  What was left was OpenRouter's auto-routers, which advertise `image` because
  something behind them can draw and then pick the model themselves: asked for a red
  circle, `openrouter/auto` chose `z-ai/glm-5.2` and answered in words, so it is
  excluded by name. The native Google models stay out on purpose — that catalogue
  publishes no output modalities at all, so the capability is UNKNOWN and fails
  closed, while naming one of those models directly still works.

### 0.21.7

Shipped without notes on 7 September 2026, summarised here from the commit: RAVIS
reported a context window Ollama never served. Ollama publishes the architecture's
maximum — 128,000 for `qwen2.5vl` — while loading the model at its own default, so a
25,000-token request was handed to a model holding 4,096 and answered as though it
had read everything. `/api/ps` reports the window a resident model actually has, and
a cold model is reported at the default it will be loaded with rather than at its
ceiling.

### 0.21.6

- **F4: the credential listing no longer names identity credentials.** `GET
  /api/v1/providers/credentials` has no authorization guard by design (it needs to answer
  before anyone has exchanged a token), and it merged every stored credential name —
  including the `client.*`/`admin.*` bearer credentials `identity.py` uses to grant
  `may_write_credentials`/`may_write_configuration` — into its response with no filtering.
  Any unauthenticated caller could enumerate which application identities exist, and which
  one held admin rights: reconnaissance a Claude Security scan flagged as CWE-200. Applied
  from the same twice-verified patch pipeline as F3 and re-verified in this working tree:
  `list_credentials` now excludes `CLIENT_PREFIX`/`ADMIN_PREFIX` names (the real constants
  `identity.py`'s own authentication logic uses, not redeclared) before merging with
  `KNOWN_PROVIDERS`. Every legitimate provider row is unaffected; 18 targeted tests plus the
  full 969-test suite pass, `ruff`/`mypy` clean.

### 0.21.5

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **F3: a client-controlled model string could redirect the outbound Gemini request path,
  and now cannot.** `GoogleAdapter._path()` spliced `request.requested_model` — a string
  the router never validates for a translated provider like this one — straight into the
  outbound URL for both `complete()` and `_stream()`, so a shaped value such as
  `../../../etc/passwd` or one carrying `?`/CRLF could alter which path actually gets
  requested on Google's own host, using RAVIS's configured credential. A Claude Security
  scan found it; the suggested-patches job produced the fix, and it earned its patch file
  by passing an independent verifier and adversarial pass before this session applied it:
  `_path()` now rejects anything outside an allow-list of the shape a real Gemini model
  name takes (`[A-Za-z0-9][A-Za-z0-9._-]*`, no `/`, `%`, `?`, `#`, `:`, whitespace, or
  control characters — checked, not assumed, against Unicode-bypass and ReDoS shapes),
  raising the existing `TranslationError` (already mapped to a 400) instead. Every
  legitimate call shape resolves exactly as before; 28 targeted tests plus the full
  969-test suite pass, `ruff`/`mypy` clean.
- **`ravis serve` binding a real port is now tested — M0's last untested clause.**
  §15's "products build and run independently" line found it: `RAVIS.md`'s M0 was
  IMPLEMENTED rather than AUTOMATED VERIFIED specifically because "`ravis serve`
  binding a port alone is tested nowhere". A new test runs a real `uvicorn.Server`
  on port 0 with no upstream configured and drives it with a real HTTP client. M0
  moves to AUTOMATED VERIFIED.

### 0.21.3

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **The translated-path (Path B) disconnect test could not fail, and now can.** It asserted
  the adapter's generator closed, which its own `finally` guarantees whether the stream was
  abandoned early or drained to the end — so a proxy that ignored a client disconnect and
  read every remaining event anyway would have passed unchanged. Rewritten to drive the relay
  directly and count how many of the adapter's events were actually produced, mirroring the
  transparent path's `frames_pulled` check. Proved against an injected eager-buffering bug
  that pulled all fifty events before yielding the first frame: the new assertion catches it,
  the old one would not have.

### 0.21.2

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Usage and upstream errors are in the conformance suite.** §15 lists five things it must
  pass and the suite had fixtures for three. A usage frame carries an empty `choices` and an
  upstream error carries none, so a proxy dropping either looked correct to every check.
- **A hard constraint is now proven to refuse at the route**, not only in the engine —
  including that a policy-refused request never reaches the upstream, which is what
  `LOCAL_ONLY` actually promises.
- The correlation context reaches log lines this codebase did not write: an outbound `httpx`
  call now names the inbound request that caused it.
- `restore-database --version` answers with its own sentence instead of a traceback.

---

## SIRVIS — 0.19.3

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio · **Browses:** Hugging Face

- **Stopping SIRVIS frees the memory its models were using.** When it stops it releases every session and
  unloads every model it loaded — for the menu bar app, the dashboard, RAVIS or a benchmark — and leaves a
  model loaded by hand in LM Studio alone. The whole stop fits inside the launcher's twelve-second wait:
  one second for requests still running, six for the unloads, which run at once so one that hangs does not
  hold up the rest, and three for events. Its log names each release and anything that may still be loaded.
- **SIRVIS keeps answering while a model loads or a benchmark runs.** Loading a model, listing LM Studio's
  models and the machine readings taken during a benchmark all held SIRVIS up for as long as they took, so
  the dashboard and menu bar could show it down mid-load. They run off to the side now.

### 0.19.2

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio · **Browses:** Hugging Face

- **Installed models carry their size.** `/api/v1/models` reported `installed_size_bytes: null` for every
  model, because LM Studio's HTTP catalogue has no sizes. SIRVIS now takes each build's size from LM
  Studio's own CLI listing, matched by its key or by family, format and quantization, and leaves a model
  without a size rather than give it a sibling's — a GGUF and an MLX of the same weights differ.

### 0.19.1

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio · **Browses:** Hugging Face

- **The API document names the build that serves it.** `/openapi.json` reported version 0.0.1
  for the whole life of the service. `/ecosystem/version` read the package's version, but the
  API document was given a fixed version of its own. It now reads the package too, and a test
  holds the two together.
- **The events capability mentions downloads.** Downloads have published their start and end
  since M11, and the capability's reason now says so.
- **Refusing a non-loopback address points at a section that exists** — runbook §9, not the
  retired §16.

### 0.19.0

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio · **Browses:** Hugging Face

- **A size cap.** `max_bytes` on `GET /api/v1/catalog` keeps rows whose estimated size is at or
  under it. With `fits=true` as well the smaller limit applies, and the answer names the limit it
  used (`size_limit_bytes`) and how many rows it hid as too large or unsized.
- **A model read says more.** `GET /api/v1/catalog/{owner}/{name}` now carries the author, the
  licence and base model — from the model card, or its tags when the card says nothing — the model
  type, task, library and creation date, the parameter count checked against the name the way a
  search checks it, and a link to the model's page built from the checked repository id. Each
  version says whether its real file size fits in three quarters of this machine's memory.

### 0.18.0

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio · **Browses:** Hugging Face

- **Search in seven orders.** `GET /api/v1/catalog` takes `sort`: `downloads` (Hugging Face's own
  count, which covers the last thirty days), `downloads_all_time`, `likes`, `trending`, `updated`,
  `created` and `smallest`. Hugging Face sorts by five of them. It refuses `downloadsAllTime`
  (HTTP 400, measured 12 September 2026) and has no size to sort by, so for those two SIRVIS reads
  the 200 most downloaded matches and orders them itself — under all time, a model nobody
  downloads any more does not appear.
- **What runs on this machine.** Every row now carries the model's parameter count, an estimated
  size for its usual build — Q4_K_M for GGUF, the precision in its name for MLX — and whether that
  fits in three quarters of this machine's memory. `fits=true` keeps only those, reading a wider
  page first, and says how many it hid as too large and how many had no size to judge. The
  estimate comes with the search itself, so it costs no extra call; the real file sizes are still
  read per model. The parameter count is checked against the size in the repository's name, and
  the name wins when the two disagree tenfold: a GGUF split into shards reports only its first
  shard, which made a 27B model sort as a 1.6 MB download that fits anything.
- **A top ten** is `limit=10` with an empty search, in any of the orders.

### 0.17.0

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio · **Browses:** Hugging Face

- **A model browser (M11).** `GET /api/v1/catalog` searches Hugging Face's public API for GGUF
  or MLX builds without a token, and `GET /api/v1/catalog/{repo}` lists one version per
  quantization with its size — split files counted together, projector files left out — and
  marks the versions LM Studio already has. Installed models are read from both of LM Studio's
  listings, because `lms ls --variants` omits every model downloaded by link, the only kind this
  release downloads. A repository Hugging Face does not have answers `MODEL_NOT_FOUND`, not
  `CATALOG_UNAVAILABLE`: Hugging Face reports a missing model as 401 "Invalid username or
  password", and that was first read as the catalogue being down.
- **Downloads that survive a restart.** `POST /api/v1/downloads` hands the download to LM Studio
  and records the job in SIRVIS's own table, which a background watcher advances until it
  completes or fails. The disk is checked first: a download that does not fit is refused with
  `DISK_SPACE`, and one that would leave under 20 GB free or use more than half the free space
  is held until confirmed. A version already installed is recorded as already present, a gated
  model is shown but not offered, and starting a download needs the admin scope. There is no
  cancel or pause, because LM Studio publishes neither. Two new capabilities, `sirvis.catalog.read`
  and `sirvis.downloads`.

### 0.16.0

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- **A hung runtime is reported busy rather than absent.** Every HTTP failure in
  the LM Studio adapter raised `RuntimeUnavailableError`, while the CLI path had
  drawn the distinction since it was written, in its own words: "the obvious
  response to unreachable is to retry immediately, which is the worst possible
  response to a load already underway." `RuntimeTimeoutError` and its `TIMEOUT`
  code both already existed; only the HTTP branch never raised them. Fixed on
  the request and streaming paths alike, since a fix to one would have looked
  done.
- **A disk that fills mid-run ends the run.** Measured rather than assumed: a
  results write raising `ENOSPC` was caught by nothing, so the row stayed
  `running / preparing` for a run that had ended while the model lease was
  released correctly. A run listed as running is the state an operator acts on
  by waiting for it. `OSError` now ends a run the way a runtime failure does, on
  the success path too — a run that measured everything and could not write it
  down is a failed run, not a successful one pointing at absent evidence — and
  the failure handler's own writes are best-effort, because it writes to the
  disk that may be the broken thing.

### 0.15.8

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- **F8: a caller-supplied model name or configuration value could no longer smuggle a flag
  into the `lms` CLI.** `LMStudioAdapter.load()`/`unload()` built `lms`'s argv straight from
  caller-controlled `model_key`, `context_length` and `gpu_offload` — JSON body fields from
  `POST /api/v1/runtime/sessions`/`POST /api/v1/runtime-sets` — with no check that any of
  them couldn't be read as one of `lms`'s own options rather than the value it named
  (CWE-88, a Claude Security scan). Applied from the same twice-verified patch pipeline as
  F5/F6 and re-verified in this working tree: a new `_as_lms_argument()` helper refuses any
  value beginning with `-` with `InvalidConfigurationError` (mapped to a 422) before it ever
  reaches `subprocess.run`, applied at every dynamic argv slot in both methods. Ordinary
  values reach `lms` exactly as before; 23 targeted tests plus the full 469-test suite pass,
  `ruff`/`mypy` clean.

### 0.15.7

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- **F5/F6: any RUNTIME-scoped caller could release or renew a session it did not open,
  and now cannot.** A Claude Security scan found `close_session`/`renew_session` checked
  only that the caller held *some* `Scope.RUNTIME` token, never whether it was the one
  that opened the session — and session ids are not secret, since the unauthenticated
  `GET /api/v1/runtime/residency` lists every live one by design. Sent through the
  automated patch pipeline, it took two rounds and a second objection (the pipeline
  declines rather than trying a third time blind), so this was fixed directly. `Lease.owner`
  is now the caller's real, server-verified identity rather than a self-declared request-body
  string, and close/renew require it to match (an `Scope.ADMIN` caller, or a session that no
  longer exists, is let through). **That fix's own first attempt introduced a real
  regression**, caught by its own adversarial verifier: binding `owner` to a real identity
  meant the intentionally-open residency read started disclosing every RUNTIME token's true
  label to an anonymous caller — the kind of fact `GET /tokens` already admin-gates for the
  same reason. `residency()` now takes a `reveal_owner` flag; `read_residency` sets it from
  whether the request carries *any* valid token (no particular scope required — this is not
  an authorization check), so an anonymous caller gets `owner: null` on every lease and a
  credentialed one — including NERVIS's own dashboard, now sending its token on this one
  read (see the NERVIS entry) — sees the real value exactly as before.

### 0.15.6

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- **`sirvis serve` binding a real port with no runtime present is now proved.** The
  existing standalone test drives the app through `TestClient`, which has no socket
  to fail to bind; a new one runs a real `uvicorn.Server` on port 0 and reaches it
  with a real HTTP client, LM Studio genuinely absent.

### 0.15.5

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio

- `restore-database --version` answers with its own sentence instead of a traceback.
- `results_path` publishes the results directory rather than the machine's layout.
- Evidence records carry `requested_configuration` beside the effective one, so a run that
  could not get the context it asked for says so.

---

## ecosystem-protocol — 0.2.1

**Protocol:** MEP 1.0.0 · **Consumed by:** SIRVIS, RAVIS, NERVIS

- **`session_id` reached the log record and stopped there.** `carrying()` has accepted it
  since the event envelope needed it, and `CorrelationFilter` copied it onto every record
  the same way it copies `request_id` — but `JsonLineFormatter`'s promotion tuple never
  named it, so it never reached the written line. The one service that already sends it
  (RAVIS, from `x-session-id`) now has it in its own logs. **Not closed by this:** SIRVIS
  and NERVIS still never read the header into their own correlation context at all, so
  §4.3's "logs carry ... session_id" is still only true for requests RAVIS serves.

### 0.2.0

**Protocol:** MEP 1.0.0 · **Consumed by:** SIRVIS, RAVIS, NERVIS

- **The MEP schemas exist.** `/ecosystem/version` had published `schema_versions` since Stage
  1 with no schemas behind it. Six are released under `protocol/schemas/`, generated from the
  models so they cannot drift from the code, with twenty-three conformance fixtures.
- **The event envelope carries the six §4.4 fields it was missing** — `event_version`,
  `subject`, `request_id`, `session_id` and `privacy`. `session_id` had a column, a reader and
  no writer anywhere.
- **Every log line written while serving a request names that request.** The formatter had
  always promoted the correlation IDs; nothing ever set them.
- **The event publisher backs off.** A failed batch was re-posted every two seconds forever,
  at a fixed interval, so two producers that lost the same collector retried in lockstep.
  Bounded now, capped at thirty seconds, and jittered.

---

## Known limitations

Published as gates rather than as a list, so they are counted rather than remembered:

- `tools/check_degradation.py` — §10's nineteen failure conditions, each scored, each with the
  gap that remains. Every one currently reads `PARTIAL`.
- `tools/pairwise_check.py` — §13.4's evidence states. Two are deterministic and *not*
  distinguishable: RAVIS cannot tell a withdrawn measurement from one that never existed, nor
  a runtime that could not be asked from a build with nothing measured.
- `tools/check_plans.py` — every milestone now carries one of §14.8's four states, and the
  states are themselves the limitation. Fifteen are **IMPLEMENTED**: code on the shipping path
  with no test exercising the row's own acceptance criterion. Twenty-nine are **AUTOMATED
  VERIFIED**: tested, never demonstrated against running services. Twenty-one are **LIVE
  VERIFIED**. The checkmark they replaced could not have told an operator which of the three
  they were reading, and for sixty-five milestones it did not.
- `clarvis/docs/code-server-matrix.md` — graded against Clarvis 0.0.1 and not re-run since.
- **`data residency`, listed in `RAVIS.md` §9.2 as a hard routing constraint, is unimplemented.**
  No provider declares a jurisdiction, `RoutingPolicy` carries no region field, and nothing
  refuses a route on it. The other ten hard constraints in that table are real; this one was
  advertised without a line saying otherwise, which the soft column's unimplemented entries
  already had and the hard column now has too.
- **NERVIS's control token is CSRF-grade, not authentication.** It stops a page on
  another origin from driving RAVIS's six proxied configuration mutations with no
  credential at all — the gap RAVIS's own stabilization work found. It does not stop a
  local process that can already reach NERVIS's port, because the token sits in the page
  NERVIS serves with no login of its own; a reader of that page could already do anything
  the page can do. `tools/check_clean_clone.sh` (`nervis pytest`) now also asserts the
  gated set is complete — every route reading `settings.ravis_admin_credential` requires
  the token — derived from the route table rather than a hand-kept list, so a new
  mutation added without the guard fails the build rather than shipping quietly.

## Compatibility

`tools/check_compatibility.py` prints the matrix and holds it to what the peers actually
ship. It is not reproduced here: a table in a document is a second copy of the windows
`nervis/src/nervis/compatibility.py` declares, and the copy is the one that goes stale.

NERVIS answers the window on every `/api/v1/services` row, beside the version it judges —
**reported, never refused.** §12 says NERVIS must *tolerate* a peer one supported minor
behind during a rolling upgrade, and an upgrade happens one service at a time: a check that
refused would turn the ordering of one into an outage.

**All three peers are judged, since Clarvis 0.12.6.** The Bridge was the one peer NERVIS
could not hold to a window: the others state a version on `/ecosystem/identity`, and an
extension host has no such surface, so its claim is the only place a version can arrive — and
it carried none. It sends `build_version` now, taken from the value the Bridge already
publishes about itself rather than read a second time, and NERVIS answers the window beside
it on every instance row.
