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

## Clarvis — 0.17.19

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Every model request says which conversation it belongs to.** Clarvis's quick check of whether a
  model can use tools is a real (one-word) request, and it went to RAVIS unlabelled, so RAVIS filed it
  under no conversation. It now carries the chat's or agent's session like everything else.

### 0.17.18

- **A closed window really does leave NERVIS's list at once.** 0.17.17's fix didn't hold when tried
  live: the editor runs two shutdown steps together, and the second let the first finish before
  Clarvis had said goodbye to NERVIS. Both now wait for the same goodbye.

### 0.17.17

Five fixes from the owner's hands-on session in code-server on 17 September 2026.

- **Trusting a folder takes effect at once.** The Bridge and the branch helper used to stay off until
  the window was reloaded, even after you trusted the folder.
- **NERVIS shows how long a task or chat took.** A finished run was reported as taking 0 ms.
- **Undo works after a Codex task.** A change Codex made without asking you first had no copy to go
  back to, so **Clarvis: Undo Last Agent Run** said there was nothing to undo; the copy now comes from
  the commit the task started on. What undo did — or that there was nothing — is also said in the
  Clarvis chat, not only in a notification that is easy to miss.
- **Clarvis: Turn the Bridge On or Off.** code-server's settings screen doesn't show the Bridge
  setting; this command switches it and offers to reload the window. A project still can't switch it on.
- **A closed window leaves NERVIS's list at once.** Clarvis now waits for its goodbye to NERVIS
  before the editor shuts it down; before, the window showed as live for up to 45 seconds.

### 0.17.16

Text inside your project can no longer pass itself off as instructions to Clarvis's agent.

- **What the agent reads is marked as data:** file contents, file lists, search results, command
  output, the editor's problems, and git status and diffs reach the model inside a fence that says it
  is information from the project, not orders. A file that tries to fake the fence has the fake
  removed.
- **Clarvis's own messages stay outside the fence,** so its notes to the model (exit codes, "don't
  retry this") still count. Skills you switched on also stay outside; they are your instructions.
- **Approvals are unchanged:** they already never read what a tool returned.

### 0.17.15

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

Clarvis's status report to NERVIS says more.

- **Now included when known:** the editor's problem counts, how the last build and test run ended,
  the last AI request (model, provider, how it ended, and its request id), the task handed over from
  NERVIS and its stage, and how many events Clarvis has published.
- **Builds and tests are only VS Code's own:** tasks VS Code files as a build or a test. A command typed
  in a terminal isn't counted, because telling a test from anything else by its words would be a guess.

### 0.17.14

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

Clarvis tells NERVIS how far a task NERVIS handed over has got.

- **Handed-over tasks are followed:** when you open a task NERVIS handed over, Clarvis reports it as
  being planned, then being built and paused around each build run, and done once the whole plan is
  built. Only the task's id and stage are sent, never its words.
- **Remembered across reloads:** the handover note is deleted once planning starts, so Clarvis keeps
  the task's id for that project folder itself.

### 0.17.13

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

Codex's approvals reach RAVIS in the order Codex asked.

- **Fixed: answers could overtake each other.** When Codex asked several things at once and Clarvis
  answered them itself (Unattended), a slow answer could reach RAVIS after the next one. Clarvis now
  waits for each answer to be received before sending the next. A file change is still copied for undo
  before its own answer.

### 0.17.12

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

A Clarvis model request and RAVIS's record of it share one request id in NERVIS.

- **The request id is on the event itself**, where RAVIS puts its own, so NERVIS stores Clarvis's
  "model request finished" event and RAVIS's route decision under the same id. It used to sit only
  inside the event's details.

### 0.17.11

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

Clarvis tells NERVIS about its model requests, its tool use and the editor's problem counts.

- **Model requests:** each one is reported when it is made and when it ends: chat, coding, titles
  and the voice check alike. The report names the model, the provider, how long it took, how much
  came back and whether it answered, was stopped or failed, and carries the same request id RAVIS
  records. It never includes what was asked or answered, or why something failed. A stop counts as
  stopped, not as a failure.
- **Tool use:** each tool call is reported by the tool's name, whether it changes files, its number
  in the run and how long it took, or why it was refused (an unknown tool, bad arguments, or another
  window holding the project). File paths, commands and search text are never included. A call that
  asked you something is reported as finished whichever way you answered, so NERVIS can't tell a
  refusal from an approval.
- **Problem counts:** errors, warnings, information and hints, plus how many files have any. No file
  names are sent. They're sent once the editor settles, at most every 30 seconds, and only when they
  change.
- **Kept light for NERVIS:** only the end of each model request and tool call is sent on to NERVIS
  (the start stays on Clarvis's own event stream), so one coding run stays inside the flood guard's
  allowance.
- **Not yet:** Clarvis's "task" events. What a task means for Clarvis hasn't been decided.

### 0.17.10

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

Clarvis's activity lines up exactly with RAVIS's in NERVIS.

- **Clarvis's events name their conversation.** A chat turn or a task now tells NERVIS which
  conversation it belongs to, the same one its requests to RAVIS name, so NERVIS can file both
  together. A Codex task has no such conversation in Clarvis and names none.
- **Times to the millisecond.** Clarvis stamped its events to the whole second, so on NERVIS's trace
  view its bar could sit up to a second off, and a chat turn looked as if it ended before the RAVIS
  call inside it.

### 0.17.9

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

Every question Clarvis sends a model now carries its own id.

- **Each model request names itself** with a fresh `x-request-id`, next to the trace and conversation
  ids it already sent. RAVIS keeps that id in its route decision, events and logs, so one request
  can be followed from Clarvis to RAVIS to the provider. This made the first whole Clarvis → RAVIS →
  provider trace visible in NERVIS.
- **Anthropic requests carry the same ids.** They carried none before. They go straight to Anthropic,
  so nothing in the ecosystem reads them yet.
- **Not sent: a workspace id.** No header for it has been agreed and RAVIS doesn't read one.

### 0.17.8

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

The `/` pop-up highlights the command you actually typed.

- **`/clear` no longer fills in `/clearkey`.** `/clearkey` is listed above `/clear` and starts with the
  same letters, so typing `/clear` in full left that one highlighted and Enter filled it in — one more
  Enter would have removed your stored Fish Audio key. A command typed out in full now takes the
  highlight, wherever it sits in the list, and Enter runs it. A partly typed word still takes the first
  row and completes as before.

### 0.17.7

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Skills as slash commands.** In Clarvis's chat, `/skill-name <request>` uses a switched-on skill,
  and `/skill <name or id> <request>` always reaches one. Built-in commands win over a skill of the
  same name. Two skills sharing a name are reached by their full id. The request keeps the capitals
  you typed.
- **Clarvis's own engine loads the skill's instructions before it starts**, capped at 6,000
  characters, with the rest readable through `readSkill`. About 1,600 tokens per step at the cap,
  recorded in the log. With Codex, the job goes to Codex as `$name <request>`, Codex's own way of
  naming a skill. Codex uses it only if the skill is switched on for Codex on the Skills page.
- **A pop-up when you type `/`** shows matching commands and skills, with arrow keys, Enter or Tab,
  and Escape. `/help` lists the commands and your switched-on skills.
- **Nothing runs by mistake.** An unknown command, a skill with no request, a skills list RAVIS
  can't give, or a skill typed while planning, during a run or while a question waits gets one line,
  and nothing runs. Enter no longer sends while an input method is composing.

### 0.17.6

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Clarvis follows a skill it picks itself.** In the 15 September live test, Clarvis's own engine
  read a changelog skill it chose on its own and then wrote the changelog in its usual layout; named
  in the task, the skill was followed. A skill it reads is now presented as instructions to follow
  for how it does the parts of the task the skill covers (steps, format, structure, style), unless
  the owner's request or plan.md's conventions say otherwise. A skill never widens the task, isn't a
  message from you, and changes none of Clarvis's rules: approvals still apply to anything it says
  to run.

### 0.17.5

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Clarvis's own engine uses your skills.** At the start of a coding run that goes through RAVIS,
  Clarvis reads the skills switched on for "Other models" on NERVIS's Skills page and adds a short
  list to the agent's instructions: name, id and a description cut at 160 characters, capped at
  about 1,500 characters. The agent reads a skill's full instructions with the new `readSkill` tool
  only when one fits. Nothing is added when no skill is on. A switch counts from the next run.
- **Skills can't override Clarvis's rules.** Skill text arrives as reference material, not as your
  message; step approvals, the command gate, Workspace Trust, protected paths and the mode still
  apply, and a skill's commands run only through the normal command tool with its approvals.
  `readSkill` only reads, and a run's first eight reads don't count against the step cap.
- **If RAVIS or the skills list can't be read,** the run goes on without skills. The chat says so
  once, only when skills were on last time.
- **Not covered:** plain chat and read-only answers get no skills.

### 0.17.4

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Clarvis's own engine asks too.** When its earlier run left work on a branch that isn't merged, a
  new task first asks **Build on `<branch>`** (the new task runs on that branch, on top of that work,
  told the earlier task, its closing summary and the branch's commit subjects) or **Start fresh from
  `<trunk>`** (a new branch from the trunk; it never stacks on a `clarvis/*` branch). Typed answers
  work like the buttons and never reach the model; unanswered means nothing runs; Unattended picks
  and says which. The own engine remembers left work in a small file in the project's git folder,
  never committed, shared by code-server and desktop VS Code.
- **The earlier run's own files are committed first when the window is on its branch,** with a chat
  line ("Committed the last run's N files to `<branch>` so this run builds on them", or "… before
  starting fresh"). Files you had in flight, or changed since, are never swept in.
- **Switching branches is friendlier, for both engines.** Ignored files don't count. Untracked files
  the other branch doesn't have come along, named in their own line (for example `__pycache__/`).
  Tracked changes, or untracked files that would clash, still refuse, naming the files. Clarvis
  0.17.3 refused on any untracked file, and didn't check Codex's Start fresh.
- **Not covered:** tasks started from the command palette don't ask and aren't remembered; only chat
  runs are.

### 0.17.3

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **A Codex follow-up asks whether to build on the work Codex left.** In the live test on 15
  September, Codex built a greeter on its own branch and the owner chose "Leave it there". The next
  request ("Also add a --shout option…") then started a new Codex task on a new branch from `master`,
  so Codex never saw its own greeter and wrote a second one beside it. Now, when earlier Codex work is
  still on a branch that isn't merged, Clarvis asks first: **Build on `<branch>`** picks that Codex
  task back up on its branch, so Codex adds to its own work and keeps the earlier conversation, or
  **Start fresh from `<trunk>`** starts a new task as before. The branch named is the project's own
  trunk, never assumed to be `main`. Typing "continue" or "start fresh" works like the buttons and is
  never sent to Codex. Unanswered means nothing runs. Unattended doesn't ask: it builds on the branch
  the window is on when that holds Codex's work, otherwise starts fresh, and says which. Switching to
  another branch is refused while you have uncommitted changes, naming the files.
- **Known gap, unchanged:** Clarvis's own engine still starts every task from the trunk, so the same
  follow-up with it doesn't see the earlier run's branch.

### 0.17.2

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Codex offers to set git up when a folder has none.** Codex saves its work as commits on a branch of
  its own, so it can't start in a folder that isn't a git repository. On 14 September that became a loop:
  the owner had once said no to Clarvis's own `git init` offer, so Codex refused, nothing offered a way
  out, and typing "git init then" went to Codex as a new task and was refused again. Now the refusal
  comes with **Set up git here**, even after that earlier no. It runs `git init` and makes an empty
  first commit that takes none of your files, then the task you asked for carries on without being
  retyped. Typing "git init", "set it up" or "yes" does the same and is never sent to Codex. RAVIS is
  asked first whether it would take the folder at all, so a protected repository gets RAVIS's reason
  and no offer. A failed setup removes the half-made `.git` and says why; a missing git name and email
  comes with the two commands that fix it.
- **Clarvis's own pre-run git question is only asked for Clarvis's own engine.** It says declining is
  fine, which isn't true for Codex. **Ask About Git Setup Again** works as before.

### 0.17.1

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Project ideas after "I don't know" work again.** In plan mode, answering "I don't know" to what
  you want to build asks the model for four ideas. That request had the same six-second deadline as a
  short question, and on 14 September it came back with no complete idea twice; the interview then
  ended without a word, so the next "I don't know" went to ordinary chat. The idea list now gets 20
  seconds, and if ideas still can't be had, Clarvis says so and asks what you're building instead of
  stopping. The log now says whether the request ran out of time or answered in the wrong shape.
- **Inline code in the chat shows as code again.** Text between backticks in a chat message had stayed
  plain since the chat script became its own file, because it looked for the characters `\u0060`
  instead of a backtick. Markup a model sends still stays plain text.

### 0.17.0

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **Codex, OpenAI's coding agent, can build your projects** (E-C9, `plan.md` M15). Choose
  `ravis/clarvis-codex` as the coding model and a build runs as a Codex task in RAVIS, on your
  ChatGPT plan rather than an API key. The task keeps going if the window closes, and code-server or
  desktop VS Code picks it up again. Progress, Codex's questions, steering and Stop all happen in the
  chat, as with Clarvis's own engine, and **Clarvis: Switch coding engine** moves a task between
  Codex and Clarvis's own engine on the same branch. It needs RAVIS 0.25 and NERVIS 0.29; desktop VS
  Code also needs `clarvis.ravis.credentialFile` set once.
- **Codex's questions come one at a time**, with only the answers RAVIS allows, and never a "don't
  ask again". Unattended mode answers on its own only a harmless command or a change inside the
  project, and only while the Clarvis panel is open.
- **Websites:** before a Codex task starts, Clarvis looks for the sites the project will need and asks
  once to allow them. Sites Codex is blocked from mid-task come as one card with **Allow**, **Keep
  blocked** and **Allow all**. While RAVIS reopens the task so a newly allowed site works, the chat
  shows "Reconnecting Codex…".
- **The bowtie beside the chat box is now a menu.** **API config** does what the bowtie did. Below
  it, a Codex section picks Codex's model and effort, and shows how much of the plan's allowance is
  left. A pick applies to the next task.
- **After a run on a temp branch, Clarvis asks once where the work goes:** the "N files changed, on a
  temp branch — Keep it" toast no longer follows the merge / show / leave question, so answering
  "Leave it there" is no longer followed by an offer to merge the same work, including after a run
  that stopped at its step limit.

### 0.16.2

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **A command is no longer stopped as "operates on a disk or partition" for mentioning `dd`.**
  The disk rule matched the word anywhere, including `<dd>` in a `grep` pattern, and a harmless
  localhost check was stopped twice as CANNOT BE UNDONE. `mkfs`, `fdisk`, `diskutil` and `dd` now
  stop a command only where the shell would run them — at the start, after `sudo`, `env` and the
  like, after a path, or inside `$(…)`.
- **A voice rewrite has to still be the line.** A lead-in ending in `:` must still end in one, and a
  reply about being asked to rewrite something is refused. The build offer's "This is what I would
  be handing myself:" came back finished with an invented sentence, and once as a remark about the
  instruction. That lead-in is now also told it introduces the task shown below it.
- **Builds check a server in-process.** Nothing a build runs can listen on a port, localhost
  included, so a check that started a server and curled it failed twice before the run found
  another way. The agent, the build task and the milestone planner now say so up front, and a
  refused `bind` or `listen` gets its own note instead of one blaming write access. The sandbox is
  unchanged: allowing a port was tried and ruled out, because macOS could not keep that port off
  the network.
- **A build that runs out of steps says so.** A run that used all its steps with the milestone
  half done still opened with "Milestone finished" and offered to update the plan. It now says
  "Stopped at the step limit", leaves `plan.md` as it is, and a stray `STEP:` line no longer
  reaches the chat after it.
- **An agreed safety fix is planned from the first milestone.** An answer like "1 but use bcrypt"
  reached the planner without the finding it settled or what "1" meant, and milestone one was
  planned as the plain-text password check with the fix put off to milestone two. The planner now
  gets the finding and the option, an agreed point wins over an earlier interview answer, and a
  safety point applies from the first milestone that touches it.
- **`writeFile` can write an empty file, and `applyEdit` can delete text.** Both were refused as a
  missing argument. An empty path, search string or command is still refused.
- **Branch flow keeps an old trunk as a step only when that branch exists.** Answering "It's the
  trunk" for `master` wrote `integration: main` and "`main` kept as a step" about a branch that had
  never existed.
- **A new plan declares the repository's own branch as its trunk**, read when the draft is
  written, so a project on `master` is no longer asked where `master` fits. With no repository it
  still says `main`.
- **One slow readiness check no longer means "no model configured".** A check that times out
  within a minute of one that answered still counts as ready; a real error or an endpoint that
  never answered does not.
- **A model that reasons gets three times the deadline** on the opening line, interview questions,
  the gap review, milestone planning, plan revision and the milestone record, once it has been seen
  reasoning in the session — including OpenRouter's `reasoning` field, which Clarvis did not read.
  The dialog rewrite, intent routing, quips and the mid-run scope check keep their deadlines.
- **Clarvis's copy of RAVIS's relay-contract fixtures matches RAVIS R3** (NERVIS-ecosystem
  `f5d8a34`), byte for byte.

### 0.16.1

**Protocol:** MEP 1.0.0 · **Ships as:** `.vsix`, installed into VS Code and code-server

- **The Codex engine's id is now `ravis/clarvis-codex`**, named like the two Clarvis pools,
  `ravis/clarvis-agent` and `ravis/clarvis-chat`. It was `ravis/codex`. To run a build on Codex,
  choose `ravis/clarvis-codex` as the coding model; the old id is no longer recognised, and there is
  no alias for it.
- **Clarvis's copy of RAVIS's contract fixtures carries the new id**, byte for byte the same as
  RAVIS's. Nothing else changed: the `X-Clarvis-Engines: codex` header, the Codex engine's files and
  every route are as they were.

### 0.16.0

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

## NERVIS — 0.34.37

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS 0.30.0+, SIRVIS 0.19.5+, Clarvis Bridge, code-server
· **Runs on:** macOS, Linux, Windows through WSL

- **NERVIS runs on Linux, and has a tray icon there.** It had Linux code paths from the start and
  had never been run on Linux. Now it has: every test suite passes on Ubuntu, and on an Ubuntu
  desktop the new tray icon — opened from the applications menu — started the whole stack. The tray
  has the Mac menu bar app's menu line for line: the services, LM Studio's models loaded through
  SIRVIS, Codex and its tasks, the machine's figures, the dashboard, and Quit. A Linux menu can't
  colour text, so the dots are coloured circles and a busy figure carries ⚠.
- **`./install.sh` installs everything**, on macOS and on Linux with apt, dnf or pacman: system
  packages, the services' environment, Ollama with its embedding model, code-server, Clarvis built
  and installed into code-server, and NERVIS on the desktop. Tried on Debian 12, Fedora, Arch and an
  Ubuntu desktop. On Windows it runs inside WSL.
- **The launcher opens the dashboard on Linux.** NERVIS refused the `HEAD` request a Linux desktop
  sends before opening a link, so the browser never opened — and the error printed the dashboard's
  address, token included, into the launcher's log. The page answers `HEAD` now.
- **A network share mounts on Linux too**, through the desktop's own mounter and the password its
  keyring already holds, as the Mac uses the Keychain.
- **Codex is not available on Linux yet**, and says why: RAVIS runs Codex only after checking
  OpenAI's Apple signature, which Linux programs don't carry.

### 0.34.36

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS 0.30.0+, SIRVIS 0.19.5+, Clarvis Bridge, code-server

- **Knows about RAVIS 0.30.** The version window was closed at 0.29, so the new RAVIS would have
  been reported as a peer NERVIS cannot vouch for. Chat's knowledge of RAVIS is current with it:
  what routing now does with a busy provider, that route decisions are kept for thirty days and
  can be routed again against today, and that `/v1/responses` works and what it refuses.
- **The page checks keep running the dashboard's own code, on purpose.** The alternative — reading
  it without running it — was an open question since a security scan; it is decided and the
  reasoning is written where the question gets asked, at the top of `nervis/tools/page_context.js`.
  Nothing about how the checks run changed.

### 0.34.35

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS 0.29.3+, SIRVIS 0.19.5+, Clarvis Bridge, code-server

- **Copying a folder no longer turns a shortcut into a copy of what it points at.** A link inside a
  copied folder was followed, so a file from outside the workspace landed inside it as an ordinary
  file the file manager would then list and serve. Links are copied as links, and the workspace still
  refuses to read through one. The same check now applies to attachments carried between
  conversations.

### 0.34.34

- **A private choice for unattended work is kept by every step of the fallback.** Titles, folder
  names and the PDF glance used to try the model that had just answered your chat before falling
  back to this machine — so with unattended work set to Private or Local and a remote chat model, a
  conversation's opening could reach a free remote model. The borrowed step is dropped for those two
  settings, and the request now tells RAVIS the work must stay on this machine rather than only that
  it must be free.
- **The commit hook runs the dashboard checks with the same containment the clean-clone check uses.**
  Those checks execute the dashboard's own script, and the hook — the path taken on every commit —
  was running them unrestricted. One policy file now serves both, with a test that the hook uses it.

### 0.34.33

Two ways a delete could reach outside the workspace, from a review of the base applications.

- **Deleting a conversation's attachments can no longer be pointed at the room they sit in.** An id
  of `..` passed the name check and unlinked the files beside the attachment folder. The delete now
  makes the same check the code that *creates* an attachment folder always made.
- **The trash cannot carry a delete out of the workspace.** A `.trash` that is a symbolic link is
  refused rather than followed, for the delete and for the fortnightly sweep; a conversation's
  attachment folder that is a link is left alone as well.
- **Two files of one name deleted in the same second both survive.** The trashed name carried whole
  seconds only, so the second delete replaced the first one's only copy.

### 0.34.32

- **A chat turn that fails says what happened and what to do.** "NERVIS is not answering, so this
  message was not sent. Try again in a moment." instead of `NERVIS is not reachable: load failed` —
  which is still there under **Technical details**. Seven kinds are worded: NERVIS unreachable, no
  model available, a model RAVIS paused after failures, a stream refused with no reason, a key
  refused, rate limiting, and a timeout. Anything else keeps the plain fallback with its own text
  underneath, so a failure nobody has worded yet is never swallowed.
- **A download that failed says so in words**, with LM Studio's reason folded under it, and the
  Failed tile points at the row instead of carrying the raw text.

### 0.34.31

- **Failures say what to do, with the real words one click away.** "Start it on this machine to use
  its models" rather than `catalogue: All connection attempts failed` — and that exact text is still
  there, under **Technical details**, because deciding whether to restart something needs both. On
  the overview's *What you can do now* and RAVIS's *What you can use*.

### 0.34.30

- **SIRVIS's dashboard keeps the answers and folds away the apparatus.** What is installed, what is
  loaded, what memory it holds, what was measured and the recent evidence all stay; the suite's
  envelope, the runtime set's table and its validity card appear with **Advanced controls**. Testing
  models and reading results are what SIRVIS is for, so none of that moved.

### 0.34.29

- **Pools say what they are for.** RAVIS names each pool and describes it, and the dashboard was
  dropping both — so a pool showed its constraints and never its purpose. The name, the id and the
  description now sit together, with the constraints unchanged beside them.
- **RAVIS's settings screen starts with what you can change** — keys, which models a pool may use, a
  provider on or off, what it costs — each linking to the screen that does it. The serving surface,
  the listen address, what a port change breaks and the endpoint lists appear with **Advanced
  controls**, because RAVIS publishes no settings endpoint and they are facts rather than controls.
- **Spending is unchanged**, deliberately: it already keeps estimates, unpriced calls and a real zero
  apart, and says it is never an invoice.

### 0.34.28

- **RAVIS's Codex card and "What you can use" sit side by side** in everyday mode, half the row each.

### 0.34.27

- **RAVIS's dashboard says what you can actually use.** One row per provider: ready with the number of
  models it offers, switched off, no key yet, a key the provider refused, not running, or paused after
  repeated failures — a choice and a fault worded differently, with links to Credentials and Pools. The
  route decision, the constraint funnel, the attempts and the recent decisions appear when **Advanced
  controls** is on. The four headline tiles are unchanged.
- **The Codex card sits beside the services tile** on NERVIS's overview in everyday mode, instead of
  leaving three quarters of the row empty.

### 0.34.26

- **The overview answers "what can I do now".** Chat, Files, Notifications and the editor each say
  whether they are ready, from the service each one actually needs — not one green light for the
  ecosystem. Codex, anything down, and recent activity stay where they were; the service registry's
  protocol and capability columns, the ecosystem map, the editor windows, the cross-service trace,
  the routing diagnostics and the throughput figures are shown when **Advanced controls** is on.
- **Never hidden, whatever the switch says:** events held back by the flood guard, and a sender NERVIS
  refused. Blocked work and a refused permission are not detail.

### 0.34.25

- **An everyday dashboard, with the technical screens one click away.** NERVIS, RAVIS and SIRVIS each
  show a shorter menu until you press **Advanced controls** at the foot of that application's menu
  (also a row in its settings). Each remembers its own answer; none of them is on by default. All four
  tabs stay where they were, the editor is untouched, and a link to a technical screen still opens it —
  labelled, without switching that application over.
- **Settings say when they were not saved.** A preference that NERVIS refused, or that never reached it,
  used to look saved until the next reload. It now says so and puts the control back.
- **The sidebar no longer claims "Local ecosystem ● Connected"** whatever is true — that text was fixed
  in the page and could contradict the live status beside it. The menu bar's live status is unchanged.

- **The menu bar's LM Studio submenu has "Quit LM Studio"**, shown while LM Studio is running.
  With a model loaded it asks first, naming the models, because quitting unloads them and stops
  whatever was using them. It quits LM Studio the way LM Studio's own Quit does.

### 0.34.23

- **Stopping the stack no longer files "code-server has stopped answering".** The launcher stops
  code-server just before NERVIS, and NERVIS sometimes noticed in between. A "stopped answering"
  note now waits until the service is still down at a later check, at least 15 seconds on; a
  service back by then files nothing, neither the outage nor a "back to healthy". The event log
  still records every change at once.
- **A failure rehearsal**, `tools/failure_rehearsal.py`, starts the stack with each service held
  off in turn and kills RAVIS, NERVIS and code-server outright, checking that the rest carry on and
  that NERVIS reports each state truthfully (operator runbook).

### 0.34.22

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS 0.29.2+, SIRVIS 0.19.4+, Clarvis Bridge, code-server

- **The Traces screen loads again.** 0.34.20's two log searches read up to 20,000 lines of every
  log line by line and took about three seconds, longer than the page waits, so the screen said the
  trace list did not answer. Lines that cannot match are now skipped before being read in full:
  on this machine the searches went from about three seconds to 0.04 each.

### 0.34.21

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS 0.29.2+, SIRVIS 0.19.4+, Clarvis Bridge, code-server

- **0.34.20's window now opens for traces somebody has already looked at.** Opening a trace leaves
  a log line naming its id, and NERVIS took that as the trace's own lines, so it never looked for
  lines written near the trace — and then said, wrongly, that no line had a time. Those look-ups
  are now skipped before the window is read, and an empty result says no line was written nearby.

### 0.34.20

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS 0.29.2+, SIRVIS 0.19.4+, Clarvis Bridge, code-server

- **"From around the same time" now means it.** When no log line carries a trace's id, the Traces
  screen offers lines written near the trace instead. It used to show each log's newest lines —
  from whenever, since log lines said nothing about when they were written — so a trace from
  yesterday showed today's lines. Log lines now carry a time (ecosystem-protocol 0.2.3), and only
  lines written within 2 seconds of the trace are offered, each with its time.
- **Said plainly when there are none.** If no line was written near the trace, or the lines read
  carry no time (anything logged before this release), the card says "No log line can be tied to
  this trace" and why.

### 0.34.19

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS 0.29.2+, SIRVIS 0.19.4+, Clarvis Bridge, code-server

- **Looking at a trace no longer adds to it.** The Traces screen lists log lines that belong to the
  trace shown. It counted NERVIS's own record of the dashboard asking for that trace, so opening
  an old trace showed only those reads. Requests that merely look a trace up are now left out.

### 0.34.18

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS 0.29.2+, SIRVIS 0.19.4+, Clarvis Bridge, code-server

Events have to prove who sent them (the security review's S7).

- **Before:** any program on this Mac could send NERVIS events, which NERVIS stored, showed and
  could turn into notifications.
- **Now:** each batch of events must carry a pass — RAVIS's or SIRVIS's own secret from the
  launcher, or an editor window's registration token — and every event in it must name that
  sender. A batch without one is refused and nothing of it is kept. Editor windows already sent
  their token, so Clarvis needs no update.
- **Said on screen:** if RAVIS, SIRVIS or an editor window keeps being refused, the Events screen
  and the Overview say so, with what to do. RAVIS or SIRVIS started without the launcher is the
  likely cause.
- **Needs RAVIS 0.29.2 and SIRVIS 0.19.4**, which send their secret. Older ones have their events
  refused.

### 0.34.17

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Every change made through the dashboard needs its security token.** Until now only settings
  that change RAVIS, service control, the voice key and a few others asked for it. Now every
  change NERVIS accepts does — chat, notifications, workspace files, settings import, background
  work, recall, voice profiles and the rest — and a change added later is covered automatically.
  The page sends the token with every change it makes, so nothing looks different.
- **Not the page's, so not covered:** events and registration sent by the services and editor
  windows keep their own rules, and so does SIRVIS's recommendation, which only reads.
- **If NERVIS restarted since you opened the page**, a change is refused with a message saying
  to reload the page.

### 0.34.16

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **"Ready" means ready.** When the launcher starts the stack it now waits for each service's
  health address to answer with success. Before, any answer counted, so a service answering
  with an error, or a different program holding its port, showed as ready. One that answers
  with an error is now named with the error code.
- **SIRVIS's readiness was being checked at an address SIRVIS doesn't have.** It answered "not
  found", which counted as ready under the old rule. The launcher now asks SIRVIS's real health
  address.

### 0.34.15

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

Two fixes from the first security review (`design/security/review-2026-09-16.md`).

- **Service control needs the dashboard's control token.** Switching service control on,
  setting how a service is started, starting, stopping or restarting it, and clearing its
  failure lock were only protected against other websites. The setting for how a service is
  started names a program to run, so all four now also need the token the dashboard page
  carries, like NERVIS's RAVIS settings already did.
- **So does the Fish Audio key.** Storing or removing it now needs the same token, as RAVIS
  already requires for provider keys.
- **Refusals are said.** If NERVIS restarted since the page was opened, these controls now say
  to reload the page instead of silently doing nothing, and removing the voice key no longer
  reports success when it was refused.

### 0.34.14

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The launcher switches on the commit checks.** Git runs the dashboard's page checks before a
  commit only in a copy of the repository where they have been switched on, and a fresh copy
  never was. Starting the stack now switches them on (`git config core.hooksPath tools/githooks`)
  and says so. A hooks setting you chose yourself is left alone.

### 0.34.13

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

The launcher (`tools/run.py`, used by the start and stop launchers and the menu bar app) now starts
and stops the services in the runbook's order.

- **Start:** SIRVIS, then Ollama, RAVIS, NERVIS and code-server, each launched only once the one
  before it answers or its 30-second wait runs out. A service that never answers is named, and the
  rest still start. Starting can take a few seconds longer, since services no longer boot at once.
- **Stop:** code-server first, then NERVIS, RAVIS, SIRVIS, and Ollama last. It used to go
  alphabetically, which stopped code-server last and Ollama before the services using it.

### 0.34.12

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

The Notifications tab now tells you about more than services going up and down.

- **New notices:**
  - a benchmark finished or failed;
  - requests through RAVIS are failing;
  - Clarvis has been waiting on your approval for over two minutes;
  - the RAVIS budget moved up a level;
  - this Mac is short of memory;
  - it is swapping heavily.
- **Kept quiet:** each notice is filed once when its condition starts, and not again until the
  condition has ended or a quiet period has passed.
- **Nothing new is asked of the services:** the notices come from events NERVIS already receives
  and readings it already takes.

### 0.34.11

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

A code-server upgrade is checked in seconds instead of hours.

- **New: `tools/code_server_upgrade_check.py`.** It compares the parts of code-server the editor
  tests depend on with the version they were run on, runs Clarvis's editor tests on the exact VS Code
  version the new code-server contains, and checks the live editor and any open window. A file that
  changed names the tests to re-run by hand.
- **A checked version is no longer "untested":** a pass is recorded, and Diagnostics shows that
  code-server version as checked, saying what was and wasn't re-checked. code-server 4.137.0 passed.
- **For operators:** "Upgrading code-server" in `OPERATOR_RUNBOOK.md`.

### 0.34.10

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

An editor window's card on Diagnostics → Clarvis shows what the window reports beyond its state.

- **New rows, each only when the window reported it:** problems, build, tests, last request, task and
  events. A build or test that failed is flagged. Needs Clarvis 0.17.15.
- **Each value is checked:** a count must be a whole number, a result one of the known words, an id the
  shape its owner makes; anything else is left out.

### 0.34.9

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

NERVIS shows every task it handed to Clarvis and how far each has got.

- **"Handed over to Clarvis":** a new card on Diagnostics → Clarvis lists each handover's folder and
  whether it is waiting to be opened, being planned, being built, paused, or done. It shows even when
  no editor window is open, and covers every window that worked on the task. Needs Clarvis 0.17.14.
- **Each handover has an id**, written into the task note and into NERVIS's record of the handover.
- **Fixed: handovers were recorded as a SIRVIS benchmark operation.** They are now recorded as
  Clarvis's.
- **For operators:** `GET /api/v1/handovers`, and a new dashboard gate, `handovers_check.js`.

### 0.34.8

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

The "RAVIS load now" card shows how models slow each other down when loaded together.

- **Models measured together:** for each pair SIRVIS measured, the two models and their roles, when
  it was measured, and per condition how much slower (or faster) each got to its first word and its
  output, in words, plus the least memory left and SIRVIS's notes. A pair older than RAVIS's
  evidence window, or one that didn't fit together, is flagged. With no measured pair, or SIRVIS
  not read, the card says so. Needs RAVIS 0.29.1.
- **Ages in hours and days** on that card, not only seconds and minutes.
- **For operators:** `ravis_load_check.js` covers the new rows.

### 0.34.7

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

RAVIS → Diagnostics no longer reports problems it doesn't have.

- **Fixed: "upstream not answering · models visible 0".** RAVIS's health reading covers only its first
  provider (LM Studio here, switched off), not RAVIS as a whole. The rows now say "first provider",
  name it, and point to Provider health for the rest.
- **Fixed: "Verdict FAIL" for a check nobody ran.** The Clarvis conformance suite runs from the command
  line and no result reaches the page, so the tiles now say "not run here". A real result would still
  show PASS or FAIL. The out-of-date "seventeen checks" and "passes today" are gone too.
- **Fixed: "null" breakers and 0% error rates.** A provider RAVIS hasn't called since it started now
  shows "not probed" and no rate. The note underneath counts those providers and names any that aren't
  answering, instead of claiming everything is healthy.
- **For operators:** a new dashboard gate, `ravis_readings_check.js`, and `reachable` now travels with
  each provider row the page reads (`shaping_golden.json` updated).

### 0.34.6

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

RAVIS → Diagnostics shows how busy RAVIS is.

- **A "RAVIS load now" card:** requests running (and how many on this machine), each provider and
  model with its count, attempts and the most at once since RAVIS started, memory and whether it is
  under pressure, providers that pushed back with a rate limit or overload, and the limits providers
  stated with how old each reading is. What RAVIS can't know is said in words, and zero shows as 0.
- **Shown only for a RAVIS that reports it** (0.29.0 and later). NERVIS now supports RAVIS up to
  0.29.x.
- **For operators:** a new dashboard gate, `ravis_load_check.js`.

### 0.34.5

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

The Diagnostics page no longer says "no packet" while showing one.

- **Fixed: a banner that was always there.** "No diagnostic packet has been assembled" was shown every
  time, even after **Analyze trace…** had built one and it was on screen as "What would be sent". The
  banner now shows only while there is no packet, and says how to build one: from the trace you last
  opened on the Traces screen, or from the most recent events. Nothing is sent until you press
  **Send this to RAVIS**.
- **For operators:** page only. A new dashboard gate, `diagnose_check.js`, covers the three states.

### 0.34.4

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

Diagnostics is fully available.

- **A whole trace, Clarvis → RAVIS → provider, has been seen.** A question asked in Clarvis's chat in
  code-server showed on the Traces screen as a Clarvis lane and a RAVIS lane under one trace, with
  RAVIS's call to Anthropic among its log lines. That was the one thing Diagnostics was waiting for,
  so it now reports itself available instead of degraded.
- **For operators:** `nervis.diagnostics@1` is `available`. No route or page change.

### 0.34.3

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

What you type while a reply is coming in stays in the chat box.

- **Fixed: your next message was wiped when a reply finished.** If you started typing while NERVIS
  was still answering, the chat box went empty and lost the cursor the moment the reply was done.
  Now the text, the cursor and any selection stay where they were. If you had clicked somewhere
  else meanwhile, NERVIS doesn't pull the cursor back into the box. A message you've sent still
  never comes back into it.
- **For operators:** page only. `slash_check.js` now rebuilds the chat box on every paint, the way
  a browser does, and types while a reply is read; `docs/PITFALLS.md` §6e has the rule.

### 0.34.2

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

The `/` pop-up highlights the command you actually typed.

- **A name typed out in full takes the highlight**, even when another name that starts with the same
  letters is listed above it — `/pdf` with `/pdf-pages` above it, say. Enter then runs what you typed
  instead of filling in the other one. A partly typed name still takes the first row, as before.
- **Why it changed:** Clarvis's chat box had this for real with `/clearkey` listed above `/clear`, so
  typing `/clear` and pressing Enter filled in `/clearkey`. Clarvis 0.17.8 fixes it there; this is the
  same fix in NERVIS chat, before a skill name can cause it here.

### 0.34.1

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

One Enter runs a command you have typed out in full.

- **The `/` pop-up no longer takes that Enter.** Type `/help` and press Enter once and it runs,
  the way Clarvis's chat box does. The pop-up still fills a command in when what you typed is only
  part of one, such as `/he`, and Tab, the arrow keys, Escape and a click are unchanged.
- **For operators:** page only, no route or RAVIS change. The `slash_check.js` dashboard gate covers
  both cases.

### 0.34.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

Slash commands in NERVIS chat, as you decided on 15 September.

- **Ask for a skill by its name.** Type `/changelog-generator write a short changelog for this week`
  and chat uses that skill for the answer, whatever the question's words would have picked.
  `/skill <name or id> <request>` always reaches a skill: use it for a skill with the same name as a
  command, or give the full id, such as `nervis/pdf`, when two switched-on skills share a name. Your
  request keeps the capitals you typed, and the conversation shows the message as you typed it.
- **Only skills switched on for Other models count.** A switched-off skill stays off, even typed.
  NERVIS checks the skill with RAVIS when you send. If it was switched off meanwhile, RAVIS doesn't
  answer, or its instructions can't be read, chat says so in one line and doesn't ask a model.
- **Three commands of its own.** `/help` lists the commands and your switched-on skills. `/clear`
  starts a new conversation, like New chat, and the old one stays under History. `/model` says which
  model answers and opens the model picker; `/model <pool or model id>` sets it for this conversation
  only, and only to something the picker offers.
- **A pop-up when you type `/`** at the start of the chat box shows matching commands and skills,
  each with a line saying what it does, filtered as you type. Arrow keys move, Enter or Tab fills it
  in, Escape closes it, and a click works too.
- **Nothing is sent by mistake.** A name nothing has, a skill with nothing to do, or a skills list
  NERVIS couldn't read gets one line in the conversation. The page answers it, doesn't read it aloud,
  and doesn't keep it after a reload. A skill typed while a reply's offer, such as Save / No thanks,
  is still waiting gets "Answer the question first; the skill can wait." `/clear` and `/model <id>`
  wait until a reply finishes or its offer is answered. Enter no longer sends while an input method
  is still composing characters.
- **For operators:** a new read route, `GET /api/v1/chat/skills`, lists the switched-on skills for
  the page, and `POST /api/v1/chat` takes an optional `skill` id, checked with RAVIS at send time. No
  RAVIS change. A new dashboard gate, `slash_check.js`, covers the commands and the pop-up.

### 0.33.1

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

Fixes for what you found using Browse on 15 September.

- **skills.sh no longer looks empty.** With **All sources** chosen, typing 2 letters or more also
  searches skills.sh, once you stop typing or press Enter, and its results show in a group of their
  own: "From skills.sh, uncurated, ranked by installs". With fewer letters the page says "skills.sh
  needs a search of at least 2 letters." instead of showing nothing, and so it does with skills.sh
  chosen on its own. When RAVIS or skills.sh refuses a search, the page says why in plain words.
- **Skills that can't be installed here are hidden.** Entries such as VoltAgent's links to skills
  kept outside GitHub no longer fill the list. One quiet line says how many are hidden, and **Show
  them** brings them back; this browser remembers your choice.
- **The filter finds skills on every page.** It filters as you type, across everything Browse has
  loaded rather than only the skills showing, and the cursor stays in the box while the list changes.
- **Gentle on skills.sh.** A search waits until you stop typing, a search overtaken by newer words is
  cancelled, the same words aren't asked twice, and RAVIS 0.28.1 remembers each search for 5
  minutes. After an install the search is asked again from that memory, so the installed badge shows.
- **For operators:** RAVIS 0.28.1 adds the remembered searches and passes on skills.sh's refusals;
  with RAVIS 0.28.0 the page works the same, but every search reaches skills.sh. The skill store gate
  covers the new behaviour, and `page_context.js` can start a page with saved browser settings.

### 0.33.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Install skills from the Skills page.** **Install skill…** takes a GitHub link to a skill's folder
  or a zip file of up to 8 MB. You see a review first: the name, what it does, its license, where it
  came from, every file with scripts marked, and the whole SKILL.md. **Install** puts it in NERVIS's
  skills folder, switched off for Codex and for the other models until you switch it on.
- **Update… and Remove… on every skill you installed.** Each says where it came from, the commit and
  the date. Update… shows what changed, with SKILL.md compared line by line, and says whether the
  switches go off again (they do when SKILL.md or a script changed). Remove… asks once more, never
  with a browser pop-up, then moves the skill's folder to the Trash.
- **Browse** the marketplace: anthropics/skills, openai/skills (marked deprecated by its owner),
  ComposioHQ/awesome-claude-skills, the VoltAgent/awesome-agent-skills link list and skills.sh's
  search (both marked uncurated), with a source filter, a words filter, each skill's license, an
  installed badge and **Review and install**. Add your own GitHub repositories, link lists or websites
  with an Agent Skills index; hide the sources NERVIS comes with; remove your own.
- **Plain words when something goes wrong**, RAVIS's own first — such as "GitHub is rate-limiting
  RAVIS; try again at 22:30" — or that a RAVIS older than 0.28.0 doesn't have the skill store yet.
- **For operators:** twelve new control routes under `/api/v1/ravis/skills/` forward to RAVIS
  0.28.0's skill store with NERVIS's RAVIS admin credential, each carrying only its own fields; a zip
  file is forwarded as its bytes and refused over 8 MB before RAVIS is asked. A new dashboard gate,
  `skill_store_check.js`. The rest of NERVIS works with an older RAVIS, and the page says the store
  needs 0.28.0. Tested against RAVIS's contract fixtures only.

### 0.32.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Skills have a page of their own.** NERVIS → Skills, in the menu between System and Settings,
  lists every skill: the ones in NERVIS's skills folder, your personal skills, and the ones built
  into Codex. Each shows what it is for and where it lives, with two switches: one for Codex, and one
  for the other models, which are Clarvis's own engine and NERVIS chat.
- **Where they start.** Skills in NERVIS's folder are on for both; your personal skills are off for
  both, and so is any you add later; Codex's built-in skills are for Codex only.
- **One click switches it.** The page says "switching on…" until RAVIS answers, then shows the new
  state, or says plainly why it didn't change. A change counts for Codex from a task's next start or
  reopen, and for chat from your next message.
- **The Codex card on RAVIS → Dashboard no longer lists skills.** It has a line that opens the Skills
  page instead.
- **NERVIS chat uses skills.** Before it asks the model, NERVIS reads which skills are switched on for
  the other models, and when your question names one or clearly matches what one is for, it reads
  that skill's instructions. The model gets the short list and that one skill, after a sentence
  saying NERVIS's own rules come first, so a skill can't override them. If a skill can't be read,
  chat says so and answers anyway. Nothing is added when no skill is on.
- **For operators:** the page and chat's skills need RAVIS 0.27.0; with an older RAVIS the page says
  so and chat adds nothing. The control route `POST /api/v1/ravis/skills` replaces
  `POST /api/v1/ravis/codex/skills`, and a new dashboard gate, `skills_check.js`, holds the page. No
  database change. Tested against RAVIS's contract fixtures; not yet tried live.

### 0.31.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **One service can no longer fill the event hub and wipe everyone else's history.** On 14 September a
  RAVIS bug (fixed in RAVIS 0.26.1) sent about a hundred events a second for eight minutes. NERVIS keeps
  at most 50,000 events, so the loop pushed out every older one — the whole history from 4 to 14
  September. NERVIS now has a flood guard. The owner decided how it should behave on 15 September.
- **Identical events are stored once, with a count.** When a service sends the same event again within
  a minute, NERVIS adds one to the stored event's count and notes when it last arrived, instead of
  storing a copy. An event that belongs to a request's trace is merged only with a true copy, so a
  trace keeps every step.
- **Each service has a speed limit:** 120 events at once, then 12 a minute. Past that, NERVIS keeps
  only the newest event of each kind waiting and stores it once that kind has been quiet for five
  seconds, so the dashboard and the alarms show the state a burst ended on. Measured against real
  traffic, the busiest service normally sends about 24 events in its busiest minute.
- **Each service has a daily share of the store:** 5% of it, which is 2,500 events a day, seven times
  the busiest real day. Even sending at that limit for all fourteen days, one service fills at most 70%
  of the store, so on its own it can't push a quiet service's events out. The share is counted from
  what is stored, so restarting NERVIS doesn't reset it.
- **Services see no change.** Sending events gets the same answer as before (202, every event counted
  as accepted), so a service stuck in a loop isn't pushed into retrying harder.
- **NERVIS says when the guard kicked in.** It records a `nervis.events.flood_guarded` event when the
  guard starts on a service (a warning, which chat reports as something that went wrong) and another
  when it lets go, with how many events it held back. Those events never count against any service's
  limits. If NERVIS stops while the guard is on, it stores what it was holding and says it stopped.
- **The dashboard says it too.** The Events screen and the Overview's Recent events card say which
  service the guard is holding back, since when and how many so far, and name any guard that ended in
  the last day. An event that arrived more than once shows its count, such as "×49,850 · last 23:39:17".
- **An open Events screen isn't cut off by a flood any more**: live screens receive the same thinned
  feed that is stored.
- **For operators:** the numbers are settings — `NERVIS_EVENT_SOURCE_BURST`,
  `NERVIS_EVENT_SOURCE_PER_MINUTE`, `NERVIS_EVENT_SOURCE_DAILY_SHARE`, `NERVIS_EVENT_COLLAPSE_SECONDS`,
  `NERVIS_EVENT_GUARD_QUIET_SECONDS` and `NERVIS_EVENT_GUARD_RELEASE_SECONDS`. `GET /api/v1/events`
  gains a `guard` field, and an event read back can carry `_repeats` and `_last_received_at`. The
  database gains two columns (migration 12), and NERVIS backs the database up before adding them. The
  event envelope services send is unchanged, so no service needs updating.
- **What this doesn't do:** the flood events already stored on 14 September stay until they are
  removed, and the history they pushed out is not recovered. While a service is flooding, a different
  event of the same kind from that same service can be replaced by a newer one before it is stored;
  the guard's count of what it held back includes it.

### 0.30.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Choose which skills Codex may use, on RAVIS → Dashboard's Codex card.** A new **Skills** list
  sits between the allowed sites and the version. It shows every skill Codex can use, grouped by
  where it comes from — NERVIS's own skills folder, your personal skills, or built into Codex — with
  its short description, whether it is on, and **Switch on** or **Switch off**. One line on top says
  where NERVIS's skills folder is and that a change counts from a Codex task's next start or reopen.
  This follows the first live Codex test, where Codex ran one of your own skills that nobody asked for.
- **What starts on and off is RAVIS's** (RAVIS 0.26.0): skills in NERVIS's folder and Codex's
  built-in skills start on; your personal skills start off, and so does any you add later.
- **A switch shows that it is working** ("switching off…") until RAVIS answers, then the list as Codex
  holds it. If Codex doesn't take the change, the card says so in plain words and the skill stays as
  it was; if RAVIS can't apply your choices at all, the card shows why no Codex task can start.
- **One click, no pop-up.** A switch doesn't ask for a second click, because clicking again switches
  it back; every other change on the card still takes two.
- **NERVIS forwards a switch to RAVIS** with its RAVIS admin credential after checking the page's
  control token, and sends only the skill's path and on or off. The list is read through the existing
  relay.
- **The launcher tells RAVIS where NERVIS's skills folder is**: `clarvis/skills` in the NERVIS
  workspace, the same workspace it gives NERVIS. An operator's own `RAVIS_CODEX_SKILLS_FOLDER` wins.
- **NERVIS accepts RAVIS 0.26.** Its supported RAVIS window now runs from 0.22.0 to 0.26.999.
- **What this changes for you:** Codex can no longer be started on "NERVIS workspace" or on its
  "clarvis" folder as a whole (RAVIS 0.26.0); task folders under `nervis-tasks` and every other
  project work as before.

### 0.29.3

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **A task handed to Clarvis starts in a git repository.** The new folder under `nervis-tasks/` is
  set up as a git repository on the branch `main`, with the task file `clarvis-task.md` as its first
  commit, so a Codex task started there never stops at "Codex needs this folder to be a git
  repository". The owner decided this on 14 September 2026.
- **Your own git name and email sign that first commit** when git has them. When it has none, the
  commit is signed `NERVIS <nervis@localhost>` for that one commit, and no git settings change.
- **Git can't stop a hand-over.** If git isn't installed or fails, the folder and task file are
  written exactly as before, the problem is logged, and the hand-over's answer says the folder isn't
  a git repository yet (Clarvis offers to set one up). The chat card itself still reads as before.
- **Only the new folder is touched.** Git runs in the folder NERVIS has just made and nowhere else:
  not in a task folder from an earlier version, and not in a repository the workspace sits inside.

### 0.29.2

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **NERVIS accepts Clarvis 0.17.** Its supported Clarvis window now runs from 0.16.0 to 0.17.999, so
  the Clarvis that brings Codex tasks isn't shown as an incompatible peer. Nothing else changed.

### 0.29.1

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The menu bar's Codex allowance is said once.** The Codex line already reads, for example, "Codex ·
  25% left · resets Sun 20 Sep 00:43", and the line under it repeated the same words for the plan's
  weekly window. The owner asked for the second line to go, so the menu now shows only the Codex line.
  It always names the limit with the least left. The dashboard's Codex card still lists every limit.
  The "allowance not read yet" line went too, since the Codex line already says that. Nothing else
  changed.

### 0.29.0

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **RAVIS → Dashboard has a Codex card.** It lists each Codex task RAVIS is running or holding: the
  project folder, its state in words, how long it has waited, the model and effort it runs at, and
  "reconnecting" while RAVIS reopens a task so a site you just allowed can be reached (the last three
  need RAVIS 0.25.1).
- **Stop… is the only thing the dashboard can do to a Codex task.** It sits beside a running or
  waiting task and takes two clicks. It stops Codex's current step and its commands and keeps the
  work in the project; it never approves, answers, steers or starts anything. If the task moved on
  since the page read it, nothing is stopped and the card says the task changed. A retried click is
  never acted on twice: the page sends RAVIS the same Idempotency-Key.
- **The card lists the sites Codex may reach.** RAVIS's defaults are folded away and can't be
  removed; each site you allowed in Clarvis has Remove, in two clicks. A removed site stops reaching
  Codex conversations that start or reopen afterwards.
- **A new Codex version can be accepted from the card.** When Homebrew installs a build RAVIS hasn't
  tested, Check this version shows RAVIS's seven checks and what changed, and Use this version…
  accepts it, in two clicks. Accepting doesn't start the file-rules re-test and spends none of your
  plan's allowance; tasks stay paused until the re-test, which starts only from the menu bar.
- **The Overview's Codex line counts the tasks** — "2 tasks · 1 waiting for your answer" — and opens
  the card.
- **The menu bar app has a Codex line** under the model runtimes, which opens the card: the state or
  the task count, each task with its wait, and the allowance. Each task's submenu has Stop this
  task…, behind a confirmation naming its folder; Re-test the file rules… appears for an accepted
  build whose rules aren't proven, and says first that it uses one short Codex turn of your
  allowance; Sign in to Codex… appears while Codex is signed out. The line is never red. The app in
  /Applications shows it once it has been rebuilt.
- Under the hood: four control routes (a task's Stop, removing a site, the version report and
  accepting a version), each checking the page's control token and presenting NERVIS's RAVIS admin
  key. A Stop's task id and Idempotency-Key and a site's name are checked before anything reaches
  RAVIS. `tools/run.py status --json` carries each task's model, effort and reconnecting, whether an
  account is signed in, and the Codex build's version and verdict.

### 0.28.19

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **NERVIS accepts RAVIS 0.25.** Its supported window for RAVIS now runs to 0.25.999, so a RAVIS on
  0.25.0 isn't shown as an incompatible peer. Nothing else changed.

### 0.28.18

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **NERVIS accepts the Clarvis and SIRVIS you actually run.** Its supported windows were last set
  for Clarvis 0.11–0.12 and SIRVIS 0.15–0.16, while this Mac runs Clarvis 0.16.2 and SIRVIS 0.19.3
  every day, so `tools/check_compatibility.py` had failed for weeks. The windows are now Clarvis
  0.15.0 … 0.16.999 and SIRVIS 0.18.0 … 0.19.999: the shipped minor plus the one below, the same rule
  the RAVIS window follows. Set at the owner's word on 14 September 2026. Nothing else changed.

### 0.28.17

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The download link in chat works again.** After a save, an export or an annotated copy, the
  bubble's "download" link answered 404: it asked for `chat.pdf` while the file sat at
  `export/chat.pdf`. The file itself was always written to the right place. It broke when the
  workspace got its four rooms (9 September), not when the workspace moved. The command's answer
  now carries `file.download`, the address the documents route really finds the file at, and the
  link uses it. When there is no such address (a file type the route doesn't serve, or an export
  room outside the workspace), the bubble shows no link instead of a dead one. Links in bubbles
  already on screen before the update are still wrong; export again to get a working one.

### 0.28.16

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **NERVIS accepts RAVIS 0.24.** Its supported window for RAVIS now runs to 0.24.999, so a RAVIS on
  0.24.0 isn't shown as an incompatible peer. Nothing else changed.

### 0.28.15

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Codex is on RAVIS → Pools now**, as a read-only **Clarvis Codex · `ravis/clarvis-codex`** row
  straight after the two Clarvis pools. The owner looked for Codex there and took it for unbuilt. The
  row says what Codex is — it runs coding tasks through your ChatGPT plan, not chats, and has no
  fallback — and shows its state as a chip ("paused"), what is left of the tightest allowance window
  ("44% left") with its reset, and the link to Credentials when signed out. Its question mark opens
  the same details as the Dashboard's Codex tile.
- **It is not a pool**, so it can't be picked or curated, and the Pools tile doesn't count it. It is
  composed on the dashboard from RAVIS's `GET /api/v1/codex`, not from the pools list. When RAVIS
  doesn't answer, the row shows "—" and why.
- The Codex engine's id is `ravis/clarvis-codex` everywhere the dashboard and its knowledge files
  name it (it was `ravis/codex`). NERVIS's own control routes, `/api/v1/ravis/codex/…`, keep their
  paths.
- **The morning digest no longer loads a local model by surprise.** It asked `ravis/free-api` for
  at most 300 tokens, and the free reasoning model spent all 300 thinking and returned no text, so
  on 12 and 13 September the digest fell back to `ravis/local` and loaded a model nobody asked
  for. It now allows 1000 tokens (`DIGEST_MAX_TOKENS`), enough to think and still write the note;
  the fallback is left for a free tier that is down. A test holds the budget at 1000 or more.

### 0.28.14

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The Codex tile on RAVIS → Dashboard is compact now**, about the height of Spend beside it. It
  shows what is left ("44% left", or unknown, or the state), one line with the tightest window and
  when it resets ("weekly · resets in 6 days"), a small chip when Codex isn't ready ("paused") or the
  reading is stale, and the sign-in link when signed out.
- **The rest is in a tooltip** that opens when you hover the tile or tab to its question mark:
  RAVIS's reason, the plan and account, every window with its exact reset day and time, how long ago
  a stale reading was taken, and that this is the plan's allowance, not money. The Overview's Codex
  line is unchanged.

### 0.28.13

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **How much of your ChatGPT plan Codex has left, on the dashboard.** RAVIS → Dashboard's headline
  row is now Decisions, Local, Spend and **Codex**. The Codex tile takes the Active profile tile's
  place; the active profile is still shown on RAVIS → Settings.
  - It leads with what is left in the window that runs out first ("62% left"), then lists each
    window — the 5-hour and the weekly one — with what is left and when it resets, in this Mac's
    time and as "in 2 h 10 min". It also shows Codex's state with RAVIS's reason, and the plan.
  - An allowance RAVIS hasn't read yet reads **unknown**, never 0%. A reading RAVIS marks stale
    says **stale** and how long ago it was read. The tile shows no money: it is the plan's
    allowance, and says so.
  - Signed out, it names the state and links to RAVIS → Credentials to sign in.
- **A Codex line on the Overview**, under the headline tiles: Codex's state and what is left in
  the tightest window, with its reset. Tile and line both go absent while RAVIS isn't answering.
- **Fixed: the provider key rows** on RAVIS → Providers and RAVIS → Credentials showed the code
  `&#9679;` as text before a stored key's source, where a dot belonged. The page check now refuses
  that mistake anywhere in the dashboard.

### 0.28.12

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **Sign Codex in to your ChatGPT plan from the dashboard.** RAVIS → Credentials has a new card,
  *ChatGPT subscription (Codex)*, under the provider keys (the first part of the Codex engine's
  increment N2).
  - Signed out, **Sign in with ChatGPT** opens OpenAI's sign-in page in a new tab. The card then
    shows that a sign-in is waiting, a link to open the page again, the time left before it
    expires, and **Cancel**, and it notices by itself when you have finished.
  - Signed in, it shows the account as a hint (like `o…@example.com`) and the plan, with
    **Sign out…**, which takes two clicks. When Codex is signed in to a different account from the
    one you confirmed, it asks **This is my account** or Sign out.
  - A sign-in that didn't finish — the page expired, it failed, or RAVIS restarted while it
    waited — says why, with **Try again**. When another program holds Codex's sign-in ports
    (1455 and 1457), it says so, and that the ChatGPT app's own Codex sign-in uses the same two.
  - While Codex isn't installed, isn't available or its process is restarting, the card shows
    RAVIS's reason and no button. It needs RAVIS 0.23.9; an older RAVIS is named as one that
    doesn't offer the sign-in yet.
- NERVIS forwards the five calls behind the card — start, read, cancel, sign out, confirm the
  account — to RAVIS with its RAVIS admin credential, after checking the page's control token, and
  passes RAVIS's answers and refusals back unchanged. The sign-in page's address stays out of
  RAVIS's public state, is marked for the browser not to keep, and NERVIS never logs it.

### 0.28.11

**Protocol:** MEP 1.0.0 · **Speaks to:** RAVIS, SIRVIS, Clarvis Bridge, code-server

- **The launcher's half of Codex in the menu bar** (the Codex engine's increment N1a). Nothing new
  shows in the menu bar app yet: it draws Codex once RAVIS reports it and the app is rebuilt for it.
  - `tools/run.py status --json` carries a `codex` entry for the menu's Codex line: Codex's state,
    what is left of the ChatGPT plan's allowance in each window and when it resets, and the Codex
    tasks running, each with what its Stop needs. It is read from RAVIS, and an allowance RAVIS
    doesn't know is never shown as a figure. While RAVIS isn't answering, the entry says Codex's
    state is not known; a RAVIS that doesn't report Codex yet — every RAVIS today — gives no entry.
  - Four commands for the menu: `run.py codex sign-in`, which opens the sign-in page and never
    prints its address; `codex cancel-sign-in`; `codex stop <task> --project <folder> --turn
    <turn>`; and `codex reprove`, which re-tests Codex's file rules. They work once RAVIS serves
    sign-in and the re-test, in its next Codex increment, and the task Stop, in the one after.
  - Starting the stack makes a second RAVIS admin key, the owner's command-line key
    (`admin.owner_cli`, in `.run/ravis-owner.token`). Only `codex stop` and `codex reprove` use it,
    and it is never given to NERVIS or to any other service.
  - Launching RAVIS tells it where Homebrew keeps Codex (`RAVIS_CODEX_EXECUTABLE`, the link Homebrew
    moves on every upgrade), and which folders Codex tasks may work in and must stay out of
    (`RAVIS_AGENT_ALLOWED_ROOTS`, `RAVIS_AGENT_DENIED_PATHS` and `RAVIS_AGENT_PROTECTED_REPOSITORIES`,
    each a JSON list; folders you list yourself are kept, after the launcher's). RAVIS 0.23.8 uses
    the first and ignores the others until it runs Codex tasks.

### 0.28.10

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

## RAVIS — 0.30.2

**Protocol:** MEP 1.0.0 · **Ships as:** `ravis serve` · **Runs on:** macOS, Linux

- **On Linux, a Codex task can find its own processes.** Linux reports when a process started up to
  two seconds early — it rounds down twice — and the rule that gives a process to a task allowed
  one, so on Linux a task's commands looked older than the task and belonged to nobody. A Stop would
  then have had nothing to stop. The allowance is two seconds on Linux, one on the Mac.
- **Codex's calibration works on Linux.** One of its test commands used the Mac's spelling of
  `script`, which Linux's refuses, so the check could never pass there.
- **No test contacts a real provider any more.** 167 tests asked Anthropic and Google for their model
  lists at start-up — without a key, since the test suite has kept the Keychain switched off since an
  earlier incident — and now every test fails that connects anywhere off the machine.

### 0.30.1

**Protocol:** MEP 1.0.0 · **Ships as:** `ravis serve`

- **Route explanations are stored packed, and the limit is smaller.** Driving the new surfaces
  against a real catalogue showed what a stored explanation actually costs: 87 KB, because it names
  every one of 538 candidates and 537 exclusions with its reason. Yesterday's limit of fifty
  thousand of those would have been several gigabytes. They are compressed now — about 7 KB each —
  and the limit is twenty thousand, so the most this can take is roughly 140 MB. **Upgrading
  applies migration 16**, which rebuilds the table and drops whatever it held; only route decisions
  recorded in the last day are affected, and the launcher backs the database up first.

### 0.30.0

**Protocol:** MEP 1.0.0 · **Ships as:** `ravis serve` · **Adds:** `POST /v1/responses` and a route-decision replay

Four decisions the owner took after the base review, built.

- **Route decisions are kept for thirty days instead of until the next restart.** A link to a
  decision used to stop working after two hundred requests, and a restart emptied the list. The
  whole explanation is stored now — every candidate considered, every exclusion and its reason, and
  what the request required — so a question about yesterday has an answer. There is a limit of
  thirty days and twenty thousand records, whichever comes first, so it cannot grow forever —
  about 140 MB, measured against this machine's own catalogue rather than guessed at.
  **Upgrading applies migrations 15 and 16**, and the launcher backs the database up first. The
  second rebuilds the table the first created, so any decisions recorded in between are dropped;
  nothing else is touched.
- **A busy provider is ranked lower.** RAVIS has been reading what providers say about their own
  limits and doing nothing with it. Now a provider that refused a request in the last minute, or
  that states it has almost no allowance left, loses its place to an equally suitable model
  somewhere else. Nothing is ever refused because of this, nothing queues, and the route explanation
  says when it happened. A local runtime that is already generating is **not** counted as busy, on
  purpose: it would send the next request to a paid provider, and that is your decision to make.
- **You can ask what RAVIS would choose today.** `GET /api/v1/route-decisions/{id}/replay` runs an
  old decision again against the models, prices and health of right now, and says whether the answer
  would be different. It contacts nobody and costs nothing.
- **`POST /v1/responses` works**, for clients that speak OpenAI's newer shape. It is translated into
  the path RAVIS already routes, so it obeys exactly the same policies. Streaming, server-stored
  answers and provider-run tools such as web search are **refused with a message saying so** rather
  than quietly ignored. Advertised as degraded, because it has no conformance suite of its own yet.

### 0.29.3

**Protocol:** MEP 1.0.0 · **Ships as:** `ravis serve`

Two ways a skill install could differ from the skill reviewed, from a review of the base applications.

- **A package holding two names one disk cannot tell apart is refused.** `SKILL.md` and `skill.md`
  are the same file on macOS, so a package with both showed one text in the preview and installed the
  other — and the update comparison, seeing `SKILL.md` unchanged, left the engine switches on. After
  staging, RAVIS also reads the folder back and refuses to install anything that is not what the
  package held.
- **Cancelling a review deletes only a review RAVIS minted.** An id nobody issued used to delete
  whatever folder its name pointed at, and answered as though it had done nothing.

### 0.29.2

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Sends its events secret to NERVIS.** NERVIS 0.34.18 refuses events that don't prove their
  sender; the launcher now gives RAVIS a secret for this (`RAVIS_NERVIS_EVENTS_SECRET`), and RAVIS
  sends it with every batch. If NERVIS refuses it, RAVIS's log says so.

### 0.29.1

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

RAVIS now also reports how models slow each other down when loaded together.

- **Measured pairs from SIRVIS:** when SIRVIS has benchmarked two models loaded at once (a Runtime
  Set), RAVIS's health report now includes it: which model played which role, how much slower each
  got in each condition, the least memory left, SIRVIS's notes, and whether the two failed to fit.
  RAVIS already fetched these measurements and was discarding them; nothing new is asked of SIRVIS.
- **Honest about age and absence:** each measurement shows its age now, one older than RAVIS's
  30-day evidence window is marked rather than hidden, and all of them disappear when SIRVIS stops
  answering. Only pairs of models RAVIS can currently reach are included.
- **Still only a reading:** none of this changes which model RAVIS picks.

### 0.29.0

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

RAVIS now shows how busy it is.

- **What's running right now:** how many requests RAVIS has in progress, per provider and model,
  and how many of them run on this machine, plus the most it has had at once since it started.
- **What providers say about their limits:** when OpenAI, Anthropic or another provider says how
  many requests or tokens you have left, or asks RAVIS to wait, RAVIS keeps the latest figure and
  how old it is.
- **Which providers pushed back:** each provider that answered "too many requests" or "overloaded",
  when it last did, and how often.
- **Memory**, as RAVIS already measured it for routing.
- **Said plainly:** RAVIS holds no queue of its own, the local model servers don't report theirs,
  and RAVIS doesn't read provider balances or SIRVIS's measurements of models slowing each other
  down yet.
- **Nothing changes in how RAVIS picks a model.** These are readings; using them to steer requests is
  a decision still to be made.
- **For operators:** `/api/v1/health` has a new `load` section. No migration.

### 0.28.4

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

Codex tasks whose project you deleted no longer hang around.

- **A finished Codex task is closed once its project is gone.** Clarvis keeps a finished task open
  for follow-up questions and only closes it from its own editor window. Delete the project, and the
  task stayed open in RAVIS for good, with nothing able to close it. RAVIS now closes such a task
  itself, when it starts and once an hour. Codex's record of the conversation is kept.
- **Careful about what "gone" means.** A task that still has something running or waiting is left
  alone, and so is a project on a disk that isn't plugged in right now.

### 0.28.3

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

RAVIS now counts what streamed replies from OpenAI and LM Studio use.

- **Fixed: OpenAI replies were recorded as costing nothing.** OpenAI, and LM Studio on your own
  machine, only report how many tokens a streamed reply used when the request asks. Nothing asked,
  so your two gpt-4.1 test messages were written down with no token counts and no cost, and the
  budget read them as free. RAVIS now asks on every streamed request that didn't say either way.
- **What apps receive changes by one final message:** the token counts, with no text in it.
  OpenRouter already sends exactly that to NERVIS and Clarvis, and both simply skip it; new tests in
  NERVIS and Clarvis pin that. A request that set this itself, either way, is passed on untouched.
- **A provider that won't take the question still answers.** RAVIS asks again without it, and
  doesn't ask that model again for 30 minutes. A setting an app sent itself is never removed.
- **Dated OpenAI models find their price.** A rate written for `gpt-4o` now also prices
  `gpt-4o-2024-08-06`, as `claude-haiku-4-5` already priced `claude-haiku-4-5-20251001`.
- **Still to do by you, if you want OpenAI spend in the budget:** gpt-4.1 has no rate in
  `~/.config/ravis/prices.json`, so its calls now show their tokens but still no cost. The file has
  rates for gpt-4o, the gpt-5 family, o1 and o3-mini.
- **For operators:** the added field is `stream_options.include_usage`, merged into any
  `stream_options` the client sent; a request that doesn't stream is not touched.

### 0.28.2

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

A chat message no longer fails because RAVIS was trying out a model, or because one model
refuses a setting.

- **Fixed: OpenAI models couldn't answer NERVIS chat.** NERVIS sends RAVIS a few instructions of
  its own with each message (whether to try untested models), and RAVIS passed them on to the
  provider. OpenAI refuses anything it doesn't recognise, so NERVIS chat messages sent to an
  OpenAI model failed; RAVIS has no record of one ever succeeding. RAVIS now keeps its own
  instructions to itself.
- **A model that refuses a tuning setting is asked again without it.** NERVIS asks every model
  not to spend time "thinking" before an ordinary reply, and gpt-4.1 doesn't take that setting.
  When a provider names settings it won't take, and they only tune the answer (thinking,
  temperature and the like), RAVIS asks the same model once more without them, and leaves them out
  for that model for the next 30 minutes. A spending limit, tools, the answer's format or stop
  words are never dropped; a model refusing one of those hands the message to the next model.
- **Trying out a model never costs you the message.** When RAVIS picks an untested model on
  purpose to measure it and that model fails, the model you'd normally get answers instead, and the
  failed one isn't tried out again for 30 minutes. A safety refusal still stops, as always.
- **For operators:** a route decision's `execution` now lists `dropped_parameters` and
  `held_from_exploration`. OpenAI's "Unrecognized request argument(s) supplied" is classed as
  `unsupported_parameter`. Both new memories live in RAVIS's memory beside the circuit breakers and
  use the tool-refusal window (`RAVIS_TOOL_REFUSAL_SUPPRESSION_SECONDS`, 30 minutes by default).
  No NERVIS change.

### 0.28.1

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

Fixes for what you found using Browse on 15 September.

- **skills.sh is never asked for fewer than 2 letters.** It refuses such a search, so RAVIS answers
  "skills.sh needs a search of at least 2 letters." without asking it. When skills.sh refuses a
  search for another reason, RAVIS passes on its own words.
- **A search is remembered for 5 minutes**, whatever its capital letters, so typing back to the same
  words doesn't ask skills.sh again. Only answers RAVIS could read are remembered, and whether each
  result is installed is checked fresh every time.
- **Installing from skills.sh works whatever the letter case.** skills.sh writes repository names in
  lower case (composiohq/awesome-claude-skills, where GitHub has ComposioHQ). RAVIS finds the skill's
  folder anyway, and shows it as installed in the search and in the source's list.
- **Big collections list their own skills first.** A source still lists at most 300 skills, but the
  ones at the top of its repository now come before those in sub-folders, so
  ComposioHQ/awesome-claude-skills's own skills, such as changelog-generator, are no longer pushed
  out by its hundreds of generated `composio-skills` folders. The source's line says how many were
  left out. The limit stays 300, since every skill listed is read once a day.

### 0.28.0

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Install skills, after a review.** From a GitHub link to a skill's folder, a zip file, or a
  website that publishes an Agent Skills index. RAVIS fetches the skill into a staging folder of its
  own first and shows a review: name, description, license, the files with scripts marked, and the
  SKILL.md text. Nothing lands in NERVIS's skills folder until you confirm.
- **Checked against the Agent Skills specification** (agentskills.io) and RAVIS's own rules: a
  proper name that matches its folder, a description, no paths leading out of the skill, no links or
  special files, size limits (an 8 MB archive, 5 MB a file, 20 MB and 300 files in all), and one
  skill at a time.
- **Installed skills arrive switched off** for Codex and for the other models, until you switch
  them on. Skills you copy into the folder yourself still start on.
- **Update** reads the skill again from where it came from and shows what changed, with a SKILL.md
  diff. If SKILL.md or a script changed, both switches go off again; other changes keep them. It
  never updates by itself.
- **Remove** moves the skill's folder to the Trash, and only for skills RAVIS installed. Finder's Put
  Back doesn't know where it came from: drag it back into the folder if you change your mind.
- **A marketplace:** anthropics/skills, openai/skills (deprecated by its owner),
  ComposioHQ/awesome-claude-skills, the VoltAgent/awesome-agent-skills link list (uncurated),
  skills.sh's search (uncurated, ranked by installs), and GitHub repositories, link lists and
  websites you add. Listings are kept a day; a link list's links are looked up only when shown.
- **Careful on the network.** Only https, only to GitHub, skills.sh and websites you added, redirects
  only within those, with timeouts and size limits, and never a token. When GitHub is rate-limiting
  RAVIS, it says when to try again.
- **For operators:** database migration 14 (backed up first); a new setting, `skills_sh_url`
  (default `https://skills.sh`); new routes under `/api/v1/skills/installs`,
  `/api/v1/skills/previews` and `/api/v1/skills/market`, for NERVIS alone, described in the new
  fixture `tests/fixtures/skill-store/contract.json`; Clarvis's contract fixtures are unchanged.
  Tested against a fake GitHub, fake websites and one recorded skills.sh answer only; NERVIS's page
  for it comes in NERVIS 0.33.0.

### 0.27.0

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Skills aren't only for Codex any more.** A skill is a folder of instructions for one kind of
  task, with a `SKILL.md` file inside. The owner decided on 15 September that Clarvis's own engine
  and NERVIS chat should be able to use them too.
- **Each skill now has two switches:** one for Codex, and one for the other models (Clarvis's own
  engine and NERVIS chat together). So a skill can be on for Codex and off for chat, or the other way
  round.
- **Where they start.** Skills in NERVIS's own skills folder start on for both. Your personal skills
  (`~/.agents/skills`) start off for both, and so does any you add later. Codex's built-in skills are
  for Codex only.
- **Every switch you already made for Codex is kept**, exactly as it was, and Codex's skills are
  still applied the same way.
- **RAVIS reads the skill folders itself**, so the list and the other models' switches work even
  while Codex isn't running. It matches its list to Codex's by where each skill really is on disk.
- **Careful with what it hands out.** A skill's text becomes instructions to a model, so RAVIS only
  ever hands out a skill that is switched on. It never reads through a shortcut leading out of a
  skills folder, and never hands out a file over 64 KB, a hidden file, or anything that isn't text.
  A skill with a broken header is listed with the problem in plain words, and never handed out. Each
  read is logged with its name and size, never with what the file says.
- **The README in NERVIS's skills folder** now points to the new Skills page, unless you had edited
  it; an edited one is left alone.
- **For operators:** database migration 13 (backed up first); a new setting, `skills_personal_folder`
  (default `~/.agents/skills`); new routes `GET` and `POST /api/v1/skills`,
  `GET /api/v1/skills/models` and `GET /api/v1/skills/models/read`, described in the new contract
  fixture `skills.json`. `/api/v1/codex/skills` answers as before, for NERVIS 0.31. PyYAML is now a
  declared dependency; it was already installed. Tested against the fake Codex only; NERVIS's Skills
  page and NERVIS chat's use of skills come in NERVIS 0.32.0, and Clarvis's own engine later.

### 0.26.3

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Finished Codex tasks no longer leave their temp folder behind.** 0.26.2 removed a task's temp
  folder (`.clarvis/tmp/<task id>` inside the project) only when the task ended. But Clarvis never
  ends a finished task: it keeps it open for follow-ups. So the folders stayed, as the live tests'
  projects `live-test-a` and `live-test-c` showed.
- **The folder now goes whenever a task has nothing running.** Once a task's work is saved and it is
  waiting for a follow-up, RAVIS removes its temp folder, and `.clarvis/tmp` and `.clarvis` too if
  RAVIS made them and nothing else is in them. The task stays open.
- **It comes back before Codex does anything more.** Before a follow-up turn, and before RAVIS
  reconnects a task to Codex (after a restart, or so a newly allowed website gets through), RAVIS
  makes the folder again. If it can't, the turn is refused with a message saying why, instead of
  starting without a temp folder.
- **Kept whenever something could still use it:** while a turn runs or is being stopped, while
  commands aren't confirmed stopped, and while work waits to be saved — for this task, and for any
  other task of the same project using that folder.
- **The same care as before.** Only that folder, only inside the project, no shortcut followed out of
  it (now when making the folder, too), and Clarvis's own lock and checkpoint files keep `.clarvis`.
  A removal that fails is logged and tried again later, never the task's error.
- **On its next start** RAVIS removes the folders of the tasks already waiting for a follow-up, so
  the live tests' leftover folders go then.
- **For operators:** no database change. Tested against the fake Codex only.

### 0.26.2

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **A Codex task's temp folder is removed when the task is over.** Each task's commands get a temp
  folder inside the project, `.clarvis/tmp/<task id>`, and RAVIS never removed it: after the live test,
  the project `live-test-a` still held one. Now it goes when the task ends, or when its start fails,
  and so do `.clarvis/tmp` and `.clarvis` if RAVIS made them and nothing else is in them.
- **Only that folder, and only inside the project.** RAVIS never follows a shortcut (a symbolic link)
  out of the project and removes nothing else. Clarvis's own lock and checkpoint files keep `.clarvis`
  in place, and a folder another unfinished task of the same project uses stays until that task ends.
- **A task that can still carry on keeps its folder.** Saving a task's work and keeping it removes
  nothing; ending it does.
- **Resuming a task makes its folder again.** A resumed task's commands keep using the temp folder its
  first run had, so RAVIS makes that folder again when the task resumes.
- **If removing fails, the task isn't affected.** RAVIS logs it and tries again the next time it
  starts. That start also removes the temp folders of tasks that ended before this version — only the
  folders, because which `.clarvis` folders RAVIS had made wasn't recorded then.
- **For operators:** the database gains three columns (migration 12), backed up first
  (`ravis.db.v11.bak`); rolling back loses only those records.
- **Not covered:** a task left waiting that nobody ends keeps its folder, because RAVIS keeps such a
  task able to carry on. Tested against the fake Codex only.

### 0.26.1

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Codex tasks start again after 0.26.0 got stuck "switching skills".** After the restart on
  0.26.0, RAVIS said "Codex found a change in its skills, and RAVIS is switching them" and never
  finished, so no Codex task could start. It also sent NERVIS a state change about three times a
  second and filled its log. The cause: telling Codex where NERVIS's skills folder is makes Codex
  say its skills changed, and RAVIS told it again every time it checked the skills, so each check
  started the next. RAVIS now tells each Codex process once, checks once more when Codex answers,
  and stops. A skill file you really change is still noticed, and a restarted Codex is told again.
- **A task started just after RAVIS or Codex starts isn't turned away any more.** Codex's answer to
  RAVIS telling it about the folder briefly made RAVIS say it was switching skills, and a task asked
  for in that moment was refused. That answer no longer holds tasks back, because no skill changed.
- Nothing else changed.

### 0.26.0

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Codex only uses the skills you chose.** In the first live Codex test, Codex ran one of your own
  skills, "graphify", that nobody asked for: it made a folder in the project and used some of the
  plan's allowance. Codex found it because RAVIS starts Codex with your real home folder, which npm,
  pip and git need. Now RAVIS decides which skills Codex may use, each time Codex starts:
  - skills in NERVIS's own skills folder, `clarvis/skills` in the NERVIS workspace, are **on** (RAVIS
    makes the folder, with a short README, if it isn't there);
  - Codex's built-in skills are **on**;
  - your personal skills (`~/.agents/skills`) are **off**, and so is any you add later.

  Any of them can be switched on or off; NERVIS's Codex card gets the switches in NERVIS 0.30.0.
- **Your switches are kept by RAVIS**, in its database (migration 11), and put back into Codex at
  every start and whenever Codex says a skill file changed. If Codex won't take them, no Codex task
  starts and `GET /api/v1/codex` says why, so a skill you switched off is never quietly on.
- **New routes:** `GET /api/v1/codex/skills` (NERVIS's reads, or an admin credential) lists every
  skill with where it comes from and whether it is on; `POST /api/v1/codex/skills` (an admin
  credential) switches one, named by the path RAVIS listed.
- **What this changes for you: Codex can no longer be started on "NERVIS workspace" or on its
  "clarvis" folder as a whole**, so no Codex task can write a skill into that folder. Task folders
  under `nervis-tasks` and every other project work as before.
- **A new setting**, `RAVIS_CODEX_SKILLS_FOLDER`, names the folder. The launcher sets it from the
  NERVIS workspace from NERVIS 0.30.0; RAVIS's own default is
  `~/Documents/coding/NERVIS workspace/clarvis/skills`. A value outside the coding folder, the coding
  folder itself, or one inside `~/.codex`, RAVIS's Codex home or the ecosystem's own repositories
  stops Codex tasks, and the reason is shown.
- **Known limits.** A change counts from a Codex task's next start or reopen. Not yet checked with a
  real Codex task: that a switched-off skill is really left out of what Codex reads. Clarvis's own
  engine (not Codex), with the NERVIS workspace open, can still write a skill into the folder, and
  Codex would then have it on.
- The record of Codex 0.154.0 now lists the three skills requests and the skills-changed notification;
  rebuilt from the real binary, it gained only their definitions, and 0.154.0 still passes. Clarvis's
  copy of the contract needs re-syncing.

### 0.25.2

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **A new Codex build that drops a request RAVIS depends on can no longer be accepted.** Before a
  new Codex version is used, RAVIS checks that it still has every request RAVIS sends. Three were
  missing from that list — reading and writing Codex's settings (the allowed sites) and listing its
  permission profiles (the file-rules check) — so a build without one would have passed, then broken
  the sites or the file-rules check with nothing saying why. They are listed now; the record of Codex
  0.154.0 was rebuilt from the real binary, gaining only those three, and 0.154.0 still passes. A
  test now fails whenever RAVIS's code sends Codex a request the list doesn't hold.
- **The Codex contract says what adding sites refuses with.** `POST /api/v1/codex/sites` refuses
  every caller but Clarvis with `AGENT_CLIENT_NOT_ALLOWED`, as the code always did; the contract's
  summary said `FORBIDDEN`, which only removing a site uses. Adding sites while Codex isn't running
  now has its own example, the 503 `CODEX_RUNTIME_UNAVAILABLE` RAVIS sends. Clarvis's copy of the
  contract needs re-syncing.
- Nothing else changed.

### 0.25.1

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **The Codex task list says which model and effort each task runs with, and whether it is
  reconnecting.** `GET /api/v1/codex` → `runs` now carries each task's `model`, `effort` and
  `reopening` (the sites it is reconnecting for, and since when), so NERVIS's Codex card can show
  them without reading the task itself, which NERVIS may not do. Clarvis's own runs show none of the
  three. Only model names and website names are added: still no folder path, request text or command.
- Nothing else changed.

### 0.25.0

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Sites can be allowed before a Codex task starts.** Clarvis can ask the owner about the sites a task
  will probably need — a package registry, a model download — and add them before Codex begins, so the
  task doesn't stop halfway to ask. RAVIS checks every name first and adds nothing if one isn't a plain
  public website name (no wildcards, IP addresses or local names). The sites Codex may reach can be
  listed, NERVIS's Codex card will be able to remove one the owner added, and RAVIS's own default
  sites can't be removed. (`GET`, `POST` and `DELETE /api/v1/codex/sites`.)
- **A task reaches a site allowed while it runs, without losing its progress.** An open Codex
  conversation never sees a site allowed later, so when a step ends on a blocked site RAVIS now lets go
  of the conversation at once and reopens it as soon as Codex has let go of it (about a minute), while
  the owner decides. A "carry on" sent meanwhile waits for that and then starts. If Codex still holds
  the conversation after two minutes, RAVIS carries on anyway and says the site may still be blocked.
  Stopping, switching engines and saving the work all still work meanwhile. Codex is also told to stop
  and say which site it needs, instead of looking for a way around the block.
- **Blocked sites are asked about together.** Every site one step was blocked from comes in one group,
  so Clarvis can show them on one card with "Allow all".
- **Each task can run at a chosen effort.** A task can name how hard Codex thinks, from the levels
  Codex offers that model. An effort or a model Codex doesn't offer is refused before anything starts;
  the effort is sent with every step and shown with the task.
- **The database moves to version 10** (each task's effort). RAVIS backs it up first
  (`ravis.db.v9.bak`); going back to RAVIS 0.24 means restoring that backup.

### 0.24.5

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Codex's file rules are proven, so Codex is no longer paused.** On 14 September 2026 the eighth
  calibration run (`cal_faf8dcc6b0f1`, the network and stop questions) passed: the allowed site
  reached the task once the Codex conversation was reopened from disk, after Codex had let go of it
  (about a minute). The full run that followed (`cal_5a1d6ecc33b4`) passed all ten must-pass
  questions and recorded the other six. RAVIS now ships the proven profile in `tested_runtimes.json`,
  so Codex 0.154.0 starts with the strict file rules. The run's transcripts are kept in
  `ravis/tests/fixtures/codex/calibration/0.154.0-cal_5a1d6ecc33b4`, checked to hold no home folder,
  email address or token.
- **After the owner allows a site, a task carries on by reopening its Codex conversation**: the step
  that was running, and the next step in the same open conversation, can't reach the new site. This
  is what Clarvis's site ask (C2b) must do.
- Nothing else changed.

### 0.24.4

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Calibration tries two more ways to carry a task on after a site is allowed** (Cal-5). The seventh
  run (`cal_85aa0ece0f52`) passed the stop question, but the allowed site stayed blocked in the task's
  next step too: a Codex conversation that is already open keeps the list of sites it started with.
  With the owner's go-ahead, the network question now tries, in order: the next step without RAVIS's
  per-step sandbox setting; the same conversation reopened from disk once Codex has let go of it
  (about 50 seconds); and a copy of the conversation with its history. It stops at the first that
  works and says which. A copy that runs without the file rules fails the question. Checked by
  `ravis/tests/test_codex_calibration_findings.py`.
- Nothing outside calibration changed.

### 0.24.3

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Calibration accepts a site that works from the task's next step** (Cal-4). The sixth calibration
  run (`cal_ed672bf12c6f`) found that Codex takes a site the owner allows (it answers "ok"), but a
  step already running keeps the list of sites it started with, so the site works only from the next
  step. The owner decided on 14 September 2026 that this is enough, because the task carries on in
  the same Codex conversation and nothing is lost. The network question now tries the site again in
  the running step and, if it's still blocked there, once more in the task's next step, and says
  which one it worked in. Checked by `ravis/tests/test_codex_calibration_findings.py`.
- **Calibration's stop question no longer counts helpers that end on their own.** The same run
  failed it as "stopping project A also stopped project B's", yet all of B's long-running commands
  were still running; what had gone were three short-lived shells caught while B was still starting.
  The question now waits for, and judges, only the long-running commands it started. Stop itself is
  unchanged.
- Nothing outside calibration changed.

### 0.24.2

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **A site the owner allows now really gets added** (Cal-3). The fifth calibration run
  (`cal_330b7525d115`) found Codex answering every site RAVIS added with "overridden", so the site
  stayed blocked. The cause: RAVIS started Codex with the list of allowed sites among its start-up
  options, and Codex lets a start-up option outrank anything written later. Codex now starts with
  its network proxy on and no site list, and the moment its process is ready, and again after every
  restart, RAVIS writes the default sites (the package registries and GitHub) into Codex's own
  settings, the same way an allowed site is added. Sites the owner added earlier stay. Checked
  without a model on Codex 0.154.0: that write is taken, the site answers at once, and a site never
  written stays blocked.
- **Until Codex has taken those sites, it takes no task.** If Codex doesn't take them, RAVIS's Codex
  state says so in plain words, a new task is refused as "Codex isn't ready", the log records it,
  and RAVIS tries again at Codex's next start. A profile pinned or named with a site list in it has
  that list taken out before Codex starts. Checked by `ravis/tests/test_codex_default_sites.py`.
- **Calibration's network question now uses Codex's real start-up state** and fails naming
  "overridden" if a write is ever overridden again. The fake Codex in the tests now overrides a site
  write whenever its start-up options carry a site list, as the real one does, so this bug coming
  back fails a test.
- **Calibration's stop question can now see everything it waits for.** It used to wait for four
  processes per project that could never all be running: a small web server the sandbox forbids,
  a command that held the turn for ten minutes, and one that ended at once. Each project now runs
  one command that starts three long-running processes in the background and waits for them, and
  the question waits for the five processes that command really makes. Checked by
  `ravis/tests/test_codex_calibration_findings.py`.

### 0.24.1

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **`openai/gpt-4.1-mini` is ruled out of coding work** (owner, 13 September 2026). During a
  Clarvis build walkthrough its tests passed, but it skipped ticking plan steps. The three coding
  pools, `ravis/clarvis-agent`, `ravis/coding` and `ravis/agent`, never pick it now. It still
  serves the chat pools, and an explicit direct address such as
  `ravis/openrouter/openai/gpt-4.1-mini` still reaches it, because a direct pick is the caller's
  deliberate choice and RAVIS can't tell a coding request from a chat one by the id alone.
- **The owner's word outranks everything else a pool weighs.** A new per-pool `owner_excluded` list
  is checked before a measurement can admit a build, so a passing trial can't bring the model back.
  It also applies to the fallback that otherwise takes every candidate, and to an operator's picks.
  The route's explanation names the model as "ruled out of this pool by the owner" instead of
  leaving it silently absent. Checked by `ravis/tests/test_pool_owner_exclusion.py`.

### 0.24.0

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Clarvis's own coding runs take the project lock through RAVIS** (M29's fourth increment, R4).
  `/api/v1/project-locks` now answers: a window takes a free project with a lease, heartbeats it with
  the command it has running, and releases it only once its processes are confirmed gone. A project
  a Codex task holds is refused with the task to join instead, and so is a folder inside or around a
  locked one. NERVIS and admin credentials are refused on every lock route, as on the task routes.
- **Taking a project over from another window follows the rules, and stops what it was running.**
  Only a Clarvis window can be taken over, only for the exact folder it holds, without confirmation
  only when it is gone, and never while it is alive and working. RAVIS first cuts the old window off
  — its next heartbeat is told it lost the lock — then stops its running command with the whole
  process group and every child, and hands over only once that is confirmed. If it can't confirm,
  the project stays locked and names what is still running.
- **Switching a task between Codex and Clarvis's own engine hands the lock over.** A transfer
  reserves the project for the other engine for 15 minutes; RAVIS stops writing its lock file so the
  window can put its own in place; an expired transfer releases nothing.
- **RAVIS knows which of Codex's processes belong to which task.** Calibration showed the sandbox's
  arguments never appear in the process table, so RAVIS now looks every two seconds at the processes
  under its own Codex process, and gives each command to the project whose folder it runs in while
  that project's turn runs; a command's children stay its own wherever they go. Each process is
  recorded by its pid and start time, in the database and in the project's lock file, so a Stop, a
  restart or a window can end exactly those. A process two tasks could claim is reported, never
  killed. Calibration's K6 uses the same rule.
- **A restart recovers cleanly.** Before answering any task or lock route, RAVIS ends only the
  processes it recorded, marks interrupted tasks uncertain, and settles each lock: a lock file naming
  RAVIS's previous run is rewritten and kept; one naming a Clarvis window is left untouched, and the
  Codex task waits (`LOCK_SUPERSEDED`) until that window lets go or registers its lock — after which a
  window can save the task's work.
- **Shutdown interrupts running turns** and ends what their commands left. **A new Codex build no
  longer waits on a question nobody answers:** ten minutes after it appears, a turn only waiting for
  an answer is paused (`paused_for_update`), and no new task starts until the new build runs.
- **Codex's own history of a task is deleted after 90 days unused**, unless a Clarvis checkpoint in
  the project still names it.
- `GET /api/v1/codex` gives named callers the current account's fingerprint (`fingerprint_sha256`),
  so Clarvis can tell whether a saved task was started under the same account.
- **Migration 9** records RAVIS's own runs, one row per task process, and Codex threads past their
  task's records. It backs the database up first (`ravis.db.v8.bak`).
- **Checked:** ruff, strict mypy and the full RAVIS suite (1582); each new guard broken in turn and
  caught.

### 0.23.16

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Calibration now checks what the owner decided, so the next full run can prove the file rules**
  (Cal-2). The first real run (`cal_d2185ed08f50`) failed K3 and K7 and left K8 inconclusive, against
  rules the owner has since replaced. This release rewrites those three questions to the decided
  behaviour; K6 is unchanged.
- **K3 now proves the approved-sites allowlist**, in one running task: a site on the default list
  (`registry.npmjs.org`) answers; `example.com` is refused with the proxy's fixed line, and RAVIS's
  site detection names it; calibration adds `example.com` exactly as the owner's "allow" does, and
  the same task then reaches it with no restart; a loopback address and `example.org`, never added,
  stay refused. The Codex site list is written back afterwards, so the next run finds `example.com`
  refused again; if it can't be, the owner is told how to remove it.
- **K7 passes when a stopped turn leaves nothing open.** Codex doesn't resolve the request an
  interrupted turn had open, so calibration answers it with cancel when the turn ends, as a task
  does. K7 passes once the turn ended interrupted within the cap and nothing was left unanswered.
- **K8 records a Codex that never asks for permissions** instead of calling it inconclusive, and that
  no longer keeps a full run from proving the rules. If Codex does ask, the empty grant must still
  grant nothing.
- **The candidate profile works without an override file.** It is the syntax Codex 0.154.0 accepted
  — the `.run` deny nested under the project roots, since Codex refuses `"**/.run"` on its own — with
  its network section built from R3's default sites. A full pass pins this profile.
- **Checked:** ruff, strict mypy and the full RAVIS suite (1543); each new check broken in turn and
  caught.

### 0.23.15

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Clarvis can run a coding task on Codex through RAVIS** (M29's third increment, R3). A Clarvis
  window creates a task on `/api/v1/agent-sessions`, follows it on the task's resumable event stream,
  answers Codex's approvals and questions with the decisions RAVIS allows, steers it, stops it and
  saves its work; the task keeps running with no editor open and another window can reattach. Only a
  Clarvis credential holding the task's token may do any of it: NERVIS, the launcher and every admin
  credential are refused on every route, a valid token or not. The menu bar and the dashboard get
  one route of their own, `owner-stop`, which stops a task after confirming its folder and turn and
  does nothing else.
- **Tasks stay paused until the file rules are proven.** Creating a task, and every turn, is refused
  with 409 `CODEX_NOT_READY` while the running Codex build's strict file rules aren't proven.
- **Built to calibration run `cal_d2185ed08f50`.** Every mode uses the granular approval policy it
  measured. Because an interrupted Codex turn leaves its open request unresolved (K7), RAVIS answers
  and publishes every open request itself whenever a turn ends. A file change is offered only once
  its item says what it would write (K12). All of it sits in `agent/calibration_dependent.py`.
- **Codex tasks reach the internet through approved sites, never through approvals.** Calibration
  proved that on Codex 0.154.0 an approval never opens the network, so a network approval is
  offered only skip or stop. When Codex's network proxy blocks a site a command tried to reach,
  RAVIS asks the owner through Clarvis — one site at a time, each once per task, storing only the
  host — with a `site` request, a `site.blocked` event and the decisions `allow_site` and
  `keep_blocked`. Codex now starts with its network proxy on and a default list of package
  registries, GitHub and JavaScript runtimes (`DEFAULT_ALLOWED_SITES`). Allowing a site adds that
  exact host to Codex's list while it runs (`config/batchWrite`), with no restart and no task
  losing progress, and sends `site.allowed` so Clarvis can have Codex retry; a write Codex
  overrides or refuses is 409 `SITE_NOT_ADDED`, and the site stays blocked. Never a wildcard, an IP
  address or a local name. Not yet verified: that an added site reaches a turn already running.
- **Withdrawn: Codex's two permission-request features** (`features.exec_permission_approvals` and
  `features.request_permissions_tool`, added to `FIXED_FLAGS` in 0.23.14). With them on, the K3,
  K4 and K8 re-test (`cal_f4552084e0e0`) still saw no network approval, no `additionalPermissions`
  and no permissions request; with the network proxy also on (`cal_8cfcf81b2d04`) Codex sent only
  plain command approvals, and after an accept the proxy blocked local addresses outright and an
  unlisted domain without a prompt. The features only added Codex's "under-development features"
  warning, so they're gone.
- **New records, metadata only.** Migration 8 adds the agent-session, turn, request, process,
  project-lock and kept-answer tables, backed up first to `ravis.db.v7.bak`. No prompt, approval,
  command or output text is stored; relayed content lives in memory and is gone 30 minutes after a
  task ends.
- **Not in this release:** the `/api/v1/project-locks` routes, restart reconciliation and process
  recording (R4). `GET /api/v1/codex` now lists running tasks (`runs`), with ids and turns only for
  a named caller, and `ravis.agent_sessions@1` is declared.
- **The contract fixtures changed:** the account fingerprint in every session view, the invalid-mode
  refusal's code, the open points this increment decided, the `site` request kind with its event,
  examples and `SITE_NOT_ADDED`, and the `site.allowed` event, and a network approval offered only skip or stop. Clarvis's copy needs the same.
- **The test suite can no longer open the checkout's own `ravis.db`.** A full run from `ravis/`
  built apps with the default relative `database_path`, opened the live RAVIS's database and
  migrated it to schema 8, which the running RAVIS refused (restored from `ravis.db.v7.bak`).
  `tests/conftest.py` now points every default at an in-memory database before any test is
  collected and fails a run that creates the checkout's file; the tests that built apps with
  defaults name `:memory:`; `tests/test_no_checkout_database.py` guards both.
- **Checked:** ruff, strict mypy and the full RAVIS suite (1528).

### 0.23.14

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Codex may ask for a command's extra permissions, such as the network, for approval.**
  Calibration run 1 (13 September 2026) found three things: no command reached the network,
  approved or not (K3); Codex never asked to leave the box with extra permissions (K4); and it
  never requested permissions (K8). The owner chose network after approval over no network at
  all. Codex 0.154.0's `experimentalFeature/list` shows why nothing asked: both
  `exec_permission_approvals` and `request_permissions_tool` are off by default. RAVIS now starts
  Codex with both switched on (`FIXED_FLAGS`), so Codex can ask to run one command with
  `additional_permissions`, like `network.enabled`, and that arrives as an approval.
- **Both features are marked "underDevelopment" in this Codex build.** A new build's acceptance
  check and a calibration re-test have to confirm they still behave. K3, K4 and K8 are re-asked
  on this build next.

### 0.23.13

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Codex starts with a file-rules profile now.** Codex 0.154.0 refuses to start when its
  configuration defines a `[permissions]` profile and nothing chooses one: "config defines
  `[permissions]` profiles but does not set `default_permissions`". The first restart with
  calibration switched on hit exactly that. Codex exited at every start, the supervisor kept
  restarting it, and the Codex card said the process wasn't running. `pin.file_rules_profile`
  now appends `-c default_permissions="<profile name>"` to every profile RAVIS launches Codex
  with, calibration's candidate and the pinned profile alike. A profile's own flags still
  configure only `permissions.<name>`.
- **A profile name that isn't a plain identifier is refused.** It's written inside a TOML
  string, so a quote in it could have set something else. Only ASCII letters, digits, `_` and
  `-`, up to 64 characters, are accepted.
- **Checked:** `ravis/tests/test_codex_profile_flags.py` (2 tests), plus ruff, strict mypy and the
  full RAVIS suite (1468).

### 0.23.12

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **A test that failed some of the time is stable, and nothing in RAVIS was broken.**
  `test_five_failures_in_the_window_stop_the_restarts` failed 3 of 3 runs on 006556e and 1 of 3
  on d1ca70b, at the test rig's read of `GET /api/v1/codex`. The read answered **429
  RATE_LIMITED**, not a crash. The rig polled every 20 ms as an anonymous caller, RAVIS allows
  an anonymous caller 60 requests a minute, and waiting through five failed starts and their
  back-off crossed that in about a second. The endpoint answered correctly throughout.
- **The rig now reads Codex's state as NERVIS does, with NERVIS's named credential**
  (`ravis/tests/codex_rig.py`, `state_of`). No assertion changed. The test the anonymous view
  matters for still reads anonymously. It passed 10 of 10 in a row, and the full suite passes
  with 1466 tests.
- **Real readers were never affected.** The launcher and NERVIS read `GET /api/v1/codex` with
  NERVIS's credential, which has the named caller's allowance.

### 0.23.11

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **The Codex engine's id is `ravis/clarvis-codex` now**, renamed from `ravis/codex` so it sits with
  `ravis/clarvis-agent` and `ravis/clarvis-chat` (owner decision, 13 September 2026). `/v1/models`
  lists the new id, still only for a client sending `X-Clarvis-Engines: codex`. A chat or embeddings
  request naming it, or any id under `ravis/clarvis-codex/`, gets the same 400, and its message names
  the new id. `GET /api/v1/codex` and `ravis.codex_runtime@1` report it as `backend_id`, and the
  Clarvis conformance check sends it.
- **There is no alias.** `ravis/codex` is now an ordinary model name RAVIS doesn't know. The Clarvis
  0.16.0 installed in code-server still uses it until Clarvis 0.16.1 is installed.
- **The contract fixtures and their manifest carry the new id.** Route paths, error codes, the
  `X-Clarvis-Engines: codex` header value and the capability names are unchanged. The new id is
  listed after RAVIS's next restart.

### 0.23.10

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **Calibration can now be run, with the owner present — it hasn't been yet.** It is the test that
  proves Codex's stricter file rules on the Codex build RAVIS runs, before any real Codex task may
  start. It asks sixteen questions of RAVIS's own Codex process on two throwaway git projects: can a
  command read decoy key files, write outside its project or reach the network without approval,
  does Codex ask before changing a file, does stopping one project's commands leave the other's
  running, and more. Only a full run in which every must-pass question passes writes the file-rules
  profile into `tested_runtimes.json` and marks the build proven; if the decoy questions fail, the
  rules stay unproven and the decision goes back to the owner.
- **It exists only while RAVIS runs with `RAVIS_CODEX_CALIBRATION=1`**, which the launcher never
  sets: `POST` and `GET /api/v1/codex/calibration/runs` answer only the owner's command-line
  credential, and need an `Idempotency-Key`. Start it from a terminal with
  `tools/run.py codex calibrate --project-a PATH --project-b PATH --prepare`: it makes each project a
  git project with a base commit, refuses a project inside NERVIS-ecosystem or clarvis, asks before
  using the plan's allowance, and prints each question's result as it goes.
- **What a run leaves:** redacted transcripts and a results summary under
  `ravis/tests/fixtures/codex/calibration/` (no tokens, emails, account ids or personal paths), and,
  only on a full pass, the pin entry. Decoy files, their markers and every file a question made are
  removed, and both projects' lock files are released.
- **Underneath, for the Codex tasks to come:** RAVIS's side of the shared lock rule and the checkout
  lock file (the same case table Clarvis follows), and working out which of Codex's processes belong
  to which project, so stopping one never touches another's.
- **New settings:** `RAVIS_CODEX_CALIBRATION` (false), `RAVIS_CODEX_CALIBRATION_PROFILE` and
  `RAVIS_CODEX_CALIBRATION_OUTPUT` (empty), and `RAVIS_AGENT_PROTECTED_REPOSITORIES`, which the
  launcher already passed and RAVIS now reads.

### 0.23.9

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **RAVIS now runs Codex's app-server itself, one for the whole Mac** — only for a Codex build that is
  tested or that you accepted, in RAVIS's own Codex folder (`~/.local/share/ravis-codex`), never the
  ChatGPT app's `~/.codex`. If it crashes or stops answering, RAVIS restarts it (after 1 s, 5 s, 30 s,
  then every 5 minutes) and stops trying after five failures in half an hour. A new Codex on disk is
  swapped in only while nothing is running on it. Codex tasks themselves are not built yet.
- **Codex signs in to your ChatGPT plan through RAVIS.** `POST /api/v1/codex/sign-in` gives the
  browser page to open; RAVIS says plainly when another program (such as the ChatGPT app) holds both
  sign-in ports, when Codex doesn't answer, when the ten minutes run out, and when a RAVIS restart cut
  a sign-in off. Cancelling, signing out and confirming the account are there too (admin credential,
  audited). A different ChatGPT account from the one you confirmed pauses Codex until you confirm it;
  only a hint of the email (`o…@example.com`) is ever shown, and no token.
- **`GET /api/v1/codex` reports Codex's state and your plan's remaining allowance**, from memory, to
  any caller: what is left in each window and when it resets, "unknown" rather than zero when Codex
  hasn't said, and "used up" kept apart from being throttled. RAVIS reads the allowance every 15
  minutes while Codex is idle, and never starts a model call to do it.
- **A Codex build RAVIS hasn't tested can be checked and accepted** (`GET /api/v1/codex/version-check`,
  `POST /api/v1/codex/accept-version`): seven checks, run in a throwaway folder. An accepted build
  still pauses tasks until the file-rules re-test proves its rules; that re-test
  (`POST /api/v1/codex/reprove`) starts only with the owner's command-line credential, from the menu
  bar, and not before calibration has fixed the file-rules profile.
- **The Clarvis conformance gate now has 24 checks**: the new one proves `ravis/codex` is refused as a
  chat model before any upstream is asked.
- **New settings:** `RAVIS_CODEX_USAGE_REFRESH_SECONDS` (900) and `RAVIS_CODEX_REPROOF_APPLICATIONS`
  (`["owner_cli"]`). **Capability:** `ravis.codex_runtime@1` is advertised available.

### 0.23.8

**Protocol:** MEP 1.0.0 · **Reads:** SIRVIS evidence · **Serves:** OpenAI-compatible chat

- **`ravis/codex` is refused as a chat model.** A chat or embeddings request naming `ravis/codex`, or
  any id under `ravis/codex/`, now gets a 400 saying it is the Codex coding engine and that nothing was
  run — before routing, before any provider is called, and whether or not Codex is set up.
- **`ravis/codex` is listed only for a client that asks for it.** `/v1/models` shows it straight after the
  pools when Codex is enabled and the request carries `X-Clarvis-Engines: codex`, which Clarvis 0.17.0
  and later will send; every other client sees the list unchanged. Running Codex tasks is not built yet.
- **RAVIS checks the Codex on this Mac once, in the background, when it starts:** Homebrew's link,
  OpenAI's signature, and the build's fingerprint and protocol schemas against the pinned Homebrew Codex
  0.154.0. That build is reported as paused for re-testing until calibration proves its file rules.
  Codex runs only in a throwaway folder deleted straight afterwards, never in `~/.codex`, and what the
  check found is written to RAVIS's log.
- **New settings:** `RAVIS_CODEX_ENABLED` (unset: on when Codex is installed), `RAVIS_CODEX_EXECUTABLE`
  (unset: ask `brew --prefix`), `RAVIS_CODEX_HOME` and `RAVIS_CODEX_EXPECTED_TEAM_ID`. `ravis doctor`
  notes a missing executable, Homebrew's versioned copy, or a Codex home inside `~/.codex` or RAVIS's
  configuration folder, and never refuses to serve over any of them.

### 0.23.7

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

## SIRVIS — 0.19.5

**Protocol:** MEP 1.0.0 · **Ships as:** `sirvis serve`

Three findings from a review of the base applications.

- **Giving up on a model load no longer ties up the slot.** If the only caller waiting for a load
  cancelled — a closed browser tab, a stopped benchmark — the load kept its place in the manager's
  count for good, and every other model was refused as "at capacity" until that same one was asked
  for again. The load now clears its own place when it finishes, whoever is still waiting.
- **Reading the evidence list page by page no longer skips results.** Results are ordered by when
  they were measured, recorded to the second, and a run writes all of its results at once — so every
  result sharing a second with the end of a page was passed over and never appeared on a later one.
- **The newest trial decides whether a model can use tools.** The recommendation screen judged
  eligibility on the *oldest* verdict while scoring on the newest, so a model fixed since a bad run
  stayed excluded, and one that had regressed stayed recommendable.

### 0.19.4

**Protocol:** MEP 1.0.0 · **Measures:** local models through LM Studio · **Browses:** Hugging Face

- **Sends its events secret to NERVIS**, from the service and from the benchmark command, as RAVIS
  0.29.2 does (`SIRVIS_NERVIS_EVENTS_SECRET`, given by the launcher).

### 0.19.3

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

## ecosystem-protocol — 0.2.3

**Protocol:** MEP 1.0.0 · **Consumed by:** SIRVIS, RAVIS, NERVIS

- **Every log line says when it was written.** `JsonLineFormatter` writes `time` first, in UTC to
  the millisecond (`2026-09-17T12:00:00.000Z`), the shape of an event's `occurred_at`. SIRVIS,
  RAVIS and NERVIS pick it up without a release of their own.

### 0.2.2

**Protocol:** MEP 1.0.0 · **Consumed by:** SIRVIS, RAVIS, NERVIS

- **The event publisher can present a secret.** `EventPublisher(secret=…)` sends it as a bearer
  token with every batch, and logs — once per power of two, never the secret — when the collector
  refuses it (401 or 403), saying whether no secret was configured or the one held is wrong.

### 0.2.1

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
- **NERVIS's flood guard keeps the last event of each kind, not every one** (0.31.0). While a
  service sends faster than NERVIS keeps events, a different event of the same kind from that same
  service — an error between two state changes, say — can be replaced by a newer one before it is
  stored. The guard's own event counts it among those held back. Other services are unaffected.
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
