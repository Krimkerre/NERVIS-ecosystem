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

**Version 0.11.2**, 29 commands, 23 settings, VS Code ^1.93.

Milestones M0–M9 are built and shipped, plus M9d2, M9d3, M9h and M13. **M14 —
the NERVIS Bridge — was signed off on 29 Aug**; the recent releases are its
follow-through: 0.10.0 sends the session and trace RAVIS joins on, 0.10.1 gives
chat and the agent their own sessions, 0.11.0 publishes events to NERVIS with
the trace on them, 0.11.2 makes one chat turn name a single trace start to
finish.

Designed and **not built**: M9g (project notes the user writes), M10 (voice
input), M12 (Tutor Mode). M11 is the release gate rather than a future
milestone.

Clarvis's own build plan is the authority on milestone status, and anything here
that disagrees with it is stale. That is a note for whoever maintains this file,
not something to tell somebody who asked a question.
