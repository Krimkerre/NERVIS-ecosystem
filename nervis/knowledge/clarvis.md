# CLARVIS — what it does and how it decides

Clarvis is the coding agent inside VS Code: a sarcastic butler that watches
builds and errors, and — when asked — plans and builds projects. It ships as a
`.vsix` extension, is bound to one workspace folder, and dies when the window
closes.

It is the only part of the ecosystem that **acts on a repository**, which is why
almost everything below is about restraint.

## What it can do to a workspace

Nine tools, and no others: `readFile`, `listFiles`, `search`, `applyEdit`,
`writeFile`, `runCommand`, `readDiagnostics`, `gitStatus`, `gitDiff`.

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

**Stop means stop, even while it is asking.** Pressing Stop while Clarvis waits
on a question — "Do it / Skip this step", or "fold this into master?" at the end
of a run — cancels the question and ends there: the step does not start, even if
"Do it" was pressed at the same moment, and work already done stays on its
branch. Before Clarvis 0.13.2 (11 September 2026) a stop only took effect once
somebody answered.

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
events, and `/v1/status`. What it does **not** accept is any instruction from
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

## Current state, as of this writing

**Version 0.14.1**, 29 commands, 23 settings, VS Code ^1.93.

Milestones M0–M9 are built and shipped, plus M9d2, M9d3, M9h and M13. **M14 —
the NERVIS Bridge — was signed off on 29 Aug**; 0.10.x and 0.11.x were its
follow-through: sending the session and trace RAVIS joins on, giving chat and
the agent their own sessions, and publishing events to NERVIS with the trace
on them. **M15 — browser (code-server) compatibility — is graded
`PASS_WITH_LIMITATION`**: the existing `.vsix` still works there, desktop
regression holds, and the matrix stands at 39 `PASS` / 16
`PASS_WITH_LIMITATION` / 0 `FAIL` / 1 `NOT_TESTED` (Bridge teardown under
code-server — the rest of the matrix's gaps closed 6 September). What M15
does not yet cover is code-server *management* — that is M14's sibling
milestone in NERVIS's own plan, not Clarvis's, and has no status tag because
it has not been built.

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
