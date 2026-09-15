# CLARVIS — what it does and how it decides

Clarvis is the coding agent inside VS Code: a sarcastic butler that watches
builds and errors, and — when asked — plans and builds projects. It ships as a
`.vsix` extension, is bound to one workspace folder, and dies when the window
closes.

It is the only part of the ecosystem that **acts on a repository**, which is why
almost everything below is about restraint.

## What it can do to a workspace

Ten tools, and no others: `readFile`, `listFiles`, `search`, `applyEdit`,
`writeFile`, `runCommand`, `readDiagnostics`, `gitStatus`, `gitDiff`, and `readSkill`.
`readSkill` lets Clarvis's own engine read the full instructions of a skill the owner switched
on for the other models (on NERVIS's Skills page), through RAVIS, when a task fits one, and
Clarvis follows the skill for how it does the parts of the task it covers, unless the owner's
request or plan.md's conventions say otherwise, without doing more than was asked. It only reads
and never runs anything. A run's first eight skill reads don't count against the step cap; later
ones do. Released in Clarvis 0.17.5; followed as instructions since Clarvis 0.17.6.

A read-only run is given only the reading tools, so "look but do not touch" is
enforced by what the model is handed rather than by asking it nicely.

**Bound to one folder.** Paths are workspace-relative and a path resolving
outside the workspace root is refused. The extension holds no ability to reach
another window's folder, and NERVIS is forbidden from holding the workspace root
at all.

**A run can be undone.** Checkpoints are taken so an agent run can be reviewed
and reversed, and there are commands for exactly that: review the run, show the
last summary, undo it.

**It stops at what is missing.** When a command fails because something is not on
this computer — a Python module such as tkinter, a package, a program, a system
library — Clarvis stops and asks what to do, in every mode including Unattended:
install it into the project (only offered when that is possible), install it
yourself, find another way, or stop. It never reinstalls a language or changes
how the computer is set up on its own, and the commands that would (pyenv, nvm,
conda, brew upgrade and the like) always ask first. A run that stopped this way
ticks nothing off. Chat remembers what was missing for three days, so it cannot
explain the gap away. Added 11 September 2026 in Clarvis 0.14.0, after a pomodoro
build tried to reinstall Python when tkinter was missing.

If you answer "find another way" and the plan cannot work without the missing
piece, Clarvis suggests the smallest change to the plan that avoids it and asks
before touching anything: change the plan like that, install it yourself, or
leave it. Say yes and it rewrites only the parts of the plan that needed it,
keeps every other line and tick, and carries on building. While it is stuck,
nothing is merged or ticked off, and "continue building" asks the same question
again rather than running into the same wall. Added in Clarvis 0.15.0, the same
evening.

**Stop means stop, even while it is asking.** Pressing Stop while Clarvis waits
on a question — "Do it / Skip this step", or "fold this into master?" at the end
of a run — cancels the question and ends there: the step does not start, even if
"Do it" was pressed at the same moment, and work already done stays on its
branch. Before Clarvis 0.13.2 (11 September 2026) a stop only took effect once
somebody answered.

Stop also ends whatever a command started. A program Clarvis launched to check
its work — a timer that never exits, a server — used to keep running after Stop,
and the run sat waiting on it. Now Stop and the ten-minute limit end everything
the command started, and a check that leaves a program running has it stopped,
with the model told to check it in a way that finishes. Fixed in Clarvis 0.15.1,
11 September 2026.

**Typing during a run steers it.** A message typed while Clarvis is working waits
for the current step to finish, then reaches the model with that step's results,
framed to take priority over what it was about to do — so "no, use the other
library" changes course at the next step. Before Clarvis 0.14.1 (11 September
2026) those messages were logged and never delivered, and the run carried on the
old way.

**It identifies itself to RAVIS.** Started by the ecosystem launcher, the editor
gives Clarvis a credential RAVIS knows as clarvis, so Clarvis gets its own six
hundred requests a minute instead of sharing sixty with every unnamed caller —
the NERVIS dashboard open in a browser uses about twenty-four of those on its
own. The credential is only ever sent to the RAVIS on this machine. In desktop
VS Code, which the launcher does not start, Clarvis stays unnamed unless a key
is stored. Added in Clarvis 0.15.2, 12 September 2026.

## How it chooses a model

Chat and the agent are **separate roles with separate settings** — provider and
model for each — because a conversation and a repository-editing run want
different things. Pointed at RAVIS, they map to `ravis/clarvis-chat` and
`ravis/clarvis-agent`: the chat pool treats tool support as optional, the agent
pool requires it.

They also hold **separate sessions**, so a long agent run does not drag the
conversation onto its model, and RAVIS's affinity keeps each on one build.

Providers it can address directly: LM Studio, Ollama, OpenAI, Anthropic,
OpenRouter, or a custom base URL — each with its own configurable endpoint.

## The Bridge

When enabled, Clarvis registers with NERVIS and serves a small status surface,
so the dashboard can show a live editor. It is **off unless configured**, needs
an enrollment secret, and binds a dynamic port.

What it publishes is deliberately thin: identity, health, capabilities, version,
events, `/v1/status`, and `/v1/config` — a summary of its settings with the setting
id for each, which never includes a path, an address somebody typed, or a key. What it does **not** accept is any instruction from
NERVIS. Registration lets NERVIS *see* an editor; there is no path by which
NERVIS can drive one, and adding remote control is explicitly out of bounds.

Registration also checks the Bridge's declared major protocol version (added
6 September 2026, closing a real gap — the two peer-to-peer read paths
already checked this, registration did not): a claim naming an unsupported
major is refused outright rather than accepted, since registering is the one
moment NERVIS can answer synchronously. An older Bridge that sends no
`protocol_version` at all is still accepted — silence isn't a claim.

## Voice

Off unless configured. A daily request cap, a chosen voice and engine, and a
setting that trims a spoken reply past about twenty seconds to its opening and
closing lines — because a two-minute monologue is not a butler, it is a hostage
video.

## What an operator can ask it for

Through the dashboard: which editor windows are registered, what each reports
about itself, and its events. Clarvis answers about itself; it is not a surface
NERVIS queries for workspace contents.

## Slash commands for skills in its chat

Since Clarvis 0.17.7 (15 September 2026), Clarvis's chat has slash commands for
skills. `/skill-name` and a request uses a switched-on skill, and `/skill` with a
name or id and a request always reaches one. A built-in command wins over a skill
of the same name, and two skills sharing a name are reached by their full id.
Typing `/` opens a pop-up of matching commands and skills, and `/help` lists the
commands and the switched-on skills. With Clarvis's own engine the skill's
instructions are read before it starts; with Codex the request goes to Codex as
`$name`, which Codex uses only if the skill is switched on for Codex. An unknown
command, a skill with no request, or a skill typed while planning, during a run or
while a question waits gets one line, and nothing runs.

## Current state, as of this writing

**Version 0.15.4**, 29 commands, 23 settings, VS Code ^1.93.

Milestones M0–M9 are built and shipped, plus M9d2, M9d3, M9h, M13 and the first
half of M8i (reasoning stripped out of replies). **M14 — the NERVIS Bridge — was
signed off on 29 Aug**; 0.10.x and 0.11.x were its follow-through: sending the
session and trace RAVIS joins on, giving chat and the agent their own sessions,
and publishing events to NERVIS with the trace on them.

**Running Clarvis inside the browser editor (code-server) is NERVIS's own M15,
which NERVIS's plan marks IMPLEMENTED.** The evidence behind it is Clarvis's
compatibility matrix, whose cells are graded `PASS`, `PASS_WITH_LIMITATION`,
`FAIL` or `NOT_TESTED` — grades for single checks, not the state of a milestone.
It stands at 39 `PASS` / 16 `PASS_WITH_LIMITATION` / 0 `FAIL` / 1 `NOT_TESTED`
(Bridge teardown under code-server), but nearly all of those cells were run
against Clarvis 0.0.1 at the end of August; only the multiple-window and rollback
checks were run later, on 0.12.6. So the matrix describes that early version
rather than today's. Managing code-server — starting it, serving it through
NERVIS's own proxy at `/code/`, and installing Clarvis into it — is NERVIS's own
M14 (the Code tab), which NERVIS's plan also marks IMPLEMENTED; the proxy shipped
on 9 September 2026.

Rollback — reinstalling a prior `.vsix` over the current one — was proven
safe for real on 6 September 2026: both the stored provider key and the
conversation history survived a version downgrade byte-for-byte on a live
workspace, not a fixture.

Designed and **not built**: M9g (project notes the user writes) and M10 (voice
input). M11 is the release gate rather than a future milestone.

Clarvis's own build plan is the authority on milestone status, and anything here
that disagrees with it is stale. That is a note for whoever maintains this file,
not something to tell somebody who asked a question.

## Its settings, and who may change them

Clarvis is configured through VS Code's own settings, all under `clarvis.`.
**NERVIS can name any of these exactly and change none of them.** Naming the
right key is the useful thing it can do; guessing one is worse than saying "look
in Settings", because somebody will go and search for it.

How it picks and runs a model: `clarvis.chat.provider`, `clarvis.chat.model`,
`clarvis.chat.mode`, `clarvis.agent.provider`, `clarvis.agent.model`,
`clarvis.agent.maxStepsPerTask`, `clarvis.model.tuneLocalLoads`. The step limit
is the one people ask about — it is `clarvis.agent.maxStepsPerTask`, and it
caps how many steps one agent task may take.

Where requests go: `clarvis.chat.baseUrl.anthropic`,
`clarvis.chat.baseUrl.openai`, `clarvis.chat.baseUrl.openrouter`,
`clarvis.chat.baseUrl.ollama`, `clarvis.chat.baseUrl.lmstudio`,
`clarvis.chat.baseUrl.custom`. These decide which host receives the prompts, so
they look like preferences and behave like something much bigger.

Talking to NERVIS: `clarvis.bridge.enabled`, `clarvis.bridge.nervisUrl`,
`clarvis.bridge.enrollmentSecretPath`. These three are the ones NERVIS must
never be able to change even in theory — they are what decides whether NERVIS is
trusted at all, so letting it edit them would let it grant itself trust.

Voice: `clarvis.voice.enabled`, `clarvis.voice.selectedVoice`,
`clarvis.voice.fishAudio.engine`, `clarvis.voice.trimLongReplies`,
`clarvis.voice.dailyRequestCap`, `clarvis.voice.savedVoices`. And
`clarvis.watch.minDurationSeconds` sets how long a build must run before Clarvis
comments on it.

API keys are not settings. They live in the editor's own secret store, and
neither NERVIS nor anything it can reach may read them.
