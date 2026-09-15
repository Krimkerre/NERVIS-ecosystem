# CLARVIS

**Clippy-Like, A Rather Very Intelligent System**

> Clippy's presence. Jarvis's competence. A butler's disdain. The workspace-bound coding agent
> inside VS Code. **CLARVIS acts.**

**Status:** Canonical Clarvis *ecosystem* specification
**Consolidates:** `CLARVIS_ECOSYSTEM_ADDENDUM.md`, `CLARVIS_ECOSYSTEM_ADDENDUM.md — Revised`
**Cross-references:** `ECOSYSTEM_RUNBOOK.md` for the shared protocol, build order and release gates

> **`clarvis/plan.md` remains the only normative source for Clarvis's product behaviour.**
> This document adds ecosystem contracts on top of it. It does not rewrite Clarvis, and it
> does not override its lifecycle, containment, security or approval guarantees. Where an
> ecosystem addition conflicts with an established Clarvis guarantee, **the Clarvis guarantee
> wins and integration stops for redesign.**

---

# 1. Non-negotiable rules

> **No agent may invent another ecosystem component's API, schema, capability or behaviour
> merely to complete its own milestone. If the required contract does not yet exist,
> implement against the canonical ecosystem contract where specified, use an explicitly
> labelled test double where appropriate, or stop at the integration gate and report the
> missing dependency.**

Clarvis owns coding-agent behaviour inside one workspace. It does not own model selection
across vendors (RAVIS), benchmark truth (SIRVIS) or ecosystem visualisation (NERVIS).

**Engineering standards are `ECOSYSTEM_RUNBOOK.md` §14** — complexity ceiling, naming, comments,
error handling and tests — with the TypeScript column of §14.1 as the enforced set. Clarvis is
the one product that already exists as code, so the standard applies **to new and changed code
rather than as a retrofit**: the boy-scout rule, not a rewrite. §14 must never be cited as a
reason to touch a source-established invariant listed in §3, and a §14 rule that would require
such a change is a STOP item, not a refactor.

---

# 2. The headline: RAVIS needs no Clarvis feature

Initial RAVIS support is **configuration-only**:

```text
Provider:  Custom (OpenAI-compatible)
Base URL:  http://127.0.0.1:<ravis-port>/v1
Chat:      ravis/clarvis-chat
Coding:    ravis/clarvis-agent
```

**No new provider implementation is added for RAVIS.** RAVIS is responsible for conforming to
Clarvis's existing expectations; Clarvis is never changed to compensate for a broken proxy.

> The best RAVIS integration is one in which Clarvis does not know RAVIS is special. From
> Clarvis's perspective, **RAVIS is just a very good OpenAI-compatible server.**

**12 September 2026 — Clarvis now carries RAVIS-aware source, and whether that fits this
section is the owner's call.** Clarvis 0.15.2 added `src/model/ravisCredential.ts`. When the
ecosystem launcher starts code-server with `CLARVIS_RAVIS_CREDENTIAL` set, `ModelService` offers
that token as the key for a `ravis/` model at a loopback address whenever no key is stored in the
editor, so RAVIS counts Clarvis as a named caller (E-C8's 0.15.2 entry says why). No provider was
added — the token is only the key the existing adapter sends — but it is Clarvis code that knows
RAVIS by name, so *"Clarvis does not know RAVIS is special"* above and §8.1's *"no Clarvis source
change"* no longer describe the code as it is. §3.1's record of 23 August is unaffected. This note
records the fact; it does not decide whether this is an acceptable exception or should move out of
Clarvis.

**13 September 2026 — Codex tasks.** Clarvis recognises `ravis/clarvis-codex` (confirmed by RAVIS's
`/api/v1/codex`) and runs it through RAVIS's agent-session relay, not through chat completions. No
`ProviderId` is added. Runbook §2.2 records the decision. **Built in Clarvis 0.17.0:** it landed as E-C9
(§5.5, §8).

---

# 3. Source-established invariants — must not regress

These were **verified against Clarvis source**, not inferred from documentation. Each is a
regression requirement, not an ecosystem proposal.

| Invariant | Evidence |
|---|---|
| One VS Code-family extension, bound to one editor window and workspace. Activates with its extension host and dies with it. **Not a system-wide daemon.** **Exception, decided 13 September 2026 and built in 0.17.0 (E-C9; runbook §2.2):** a Codex task runs in RAVIS and can outlive the window; Clarvis's own engine still lives and dies with its extension host. | `plan.md` §7; `docs/CURRENT_STATE.md` |
| Privileged behaviour lives in the extension host. The webview is presentation and message input — **not an authority boundary** — talking to the host over a narrow `postMessage` bridge. | `src/panels/`, `src/extension.ts` |
| Clarvis's own file, read, search and edit tools stay inside the opened workspace, resolving and checking paths including escape and symlink cases. `isInside()` probes the real filesystem for case sensitivity rather than inferring it from `process.platform` — a fix for a real macOS containment escape on case-sensitive APFS. | `src/agent/`, `docs/build-log.md` |
| Commands are separately approval-gated and OS-sandboxed. Network is denied by default and opened only for gate categories that cannot work without it. **The command boundary is more nuanced than tool containment and must keep being stated honestly.** **Since 0.17.0 (E-C9):** these are the boundaries of Clarvis's own agent loop. A Codex task's commands and edits are Codex's own. They run inside Codex's safety box: writes only in the project and its task temp folder, internet only for the sites the owner allowed, and reads refused for the named key and password files by a permission profile — **proven for Codex 0.154.0 by calibration (K5), and re-tested after every Codex update before a task may start.** Clarvis sees what Codex asks, not what it runs without asking. | `src/agent/tools/sandbox*.ts`, `src/agent/Gate.ts` |
| Risky, destructive and outward-facing operations stop at approval gates, and so does changing this computer's languages and tools. Anything outside the workspace is **refused**, not made approvable. A sensitive-file read gate (`.env`, credentials, private keys) fires in every mode, and so does the question a command's missing dependency raises. **Since 0.17.0 (E-C9):** for Codex tasks the deny-list adds warnings to relayed command approvals. Writes outside the workspace can only be declined (RAVIS enforces it). The sensitive-file gate's equivalent is the permission profile. | `src/agent/Gate.ts`, `src/agent/sensitivePath.ts`, `src/agent/missingDependency.ts` |
| Clarvis acts only when asked. Unsolicited observation never becomes a code change. | `plan.md` §4 |
| `ModelProvider` abstracts completion, streaming, tool support and model listing. Providers include direct cloud, local hosts, and arbitrary OpenAI-compatible endpoints. | `src/model/ModelProvider.ts`, `src/model/OpenAiCompatibleProvider.ts` |
| **Chat and coding/agent roles use independently configured providers and models.** | settings `clarvis.chat.provider` / `clarvis.chat.model` and `clarvis.agent.provider` / `clarvis.agent.model` |
| **A turn's *mode* does not choose the role — a turn that writes does.** `AgentRunner.loop` selects `role: 'chat'` for a read-only turn (capped at 10 steps, `tools: readOnlyTools()`) and `role: 'agent'` only when the turn may mutate the workspace. So "agent mode" answering a question about a file runs on the **chat** model, with tools, and only an edit reaches the agent model. | `src/agent/AgentRunner.ts:195,199,430` |
| Provider and model inherit *independently*: an unset `clarvis.agent.provider` falls back to chat's, and a model name is only carried across when both roles share a provider. | `src/model/roles.ts`, `resolveRole` |
| A provider's base URL is keyed by **provider, not role** — `chat.baseUrl.${spec.id}` — so both roles share one endpoint and differ only by model. | `src/model/ModelService.ts:74`, `:173` |
| Clarvis builds the API path itself: `${baseUrl}/v1/models`, `${baseUrl}/v1/chat/completions`. A configured base URL must **not** end in `/v1`. | `src/model/OpenAiCompatibleProvider.ts:117`, `:164` |
| The `custom` provider declares `needsKey: false` and `needsUrl: true`, and its default model is the placeholder `local-model` — which it will send before a model is chosen. | `src/model/providers.ts:102-110` |
| A custom OpenAI-compatible base URL is a first-class configuration, asked for rather than guessed. | setting `clarvis.chat.baseUrl.custom`; `needsUrl` in `src/model/providers.ts` |
| **Tool support is probed, not asserted from model-family heuristics.** `supportsTools()` sends a real one-tool, one-token request — "the only honest test". Model-family recognition exists for *defaults only* (`plan.md` M8j). **Since 0.17.0 (E-C9):** `ravis/clarvis-codex` is not probed; RAVIS's `/api/v1/codex` is its probe. | `src/model/OpenAiCompatibleProvider.ts:162` |
| `[DONE]` and `reasoning_content` are already handled at the provider layer. Reasoning leaking into visible or spoken output is treated as a **defect**. | `src/model/OpenAiCompatibleProvider.ts`, `src/model/reasoning.ts` |
| Provider credentials use VS Code `SecretStorage` / `context.secrets` — never settings, workspace state, logs, NERVIS or telemetry. BYO-key; no Clarvis account. | `src/extension.ts`, `src/model/ModelService.ts` |
| Capabilities are probed and features degrade when unavailable. | `src/model/` |
| Multiple VS Code windows are separate Clarvis lifetimes and workspace states. | `plan.md` §7 |
| A durable run ledger records what the agent did, and "why did you do that" is answered from it. | `src/agent/runLedger.ts` |
| **Since 0.17.0 (E-C9): writing runs in one checkout are exclusive across windows and hosts**, whichever engine, through RAVIS's project lock with a lock file in the checkout as the floor. Read-only answers are not locked. | §5.5; planned for `src/engine/lock/`, which does not exist yet |

## 3.1 What the first live integration established

Verified on 2026-08-23 by pointing an unmodified Clarvis at a running RAVIS
(runbook Stage 3 / RAVIS M9), configured through Clarvis's own provider UI
rather than by editing `settings.json`. **No Clarvis source change was made or
needed**, which is the claim §2 rests the whole build order on.

Nineteen route decisions across one session: `ravis/clarvis-chat` succeeded
seven times and was cancelled four, `ravis/clarvis-agent` succeeded five. No
failure of any class, no interrupted stream, every circuit closed.

Three things this surfaced that reading the source had not:

- **The role split is triggered by writing, not by the UI mode.** Asking the
  agent to *read* a file produced `ravis/clarvis-chat` requests carrying tool
  definitions — correct per `AgentRunner.ts`, and the opposite of what anyone
  testing this would predict. It is now an invariant in the table above,
  because it decides what a tester has to do to exercise the agent pool at all.
- **Clarvis issues paired requests**, roughly a second apart, of which the
  second carries tools. Budget and rate-limit expectations should assume
  two calls per visible reply rather than one.
- **The agent loop's context grows across steps** — 2136 → 7032 → 7039
  estimated tokens over five sequential completions — which is what makes
  `ravis/clarvis-agent`'s 32K minimum-context invariant load-bearing rather
  than decorative.

## 3.2 Where the extension stands, 2026-08-31

§3.1 records the first integration, on 2026-08-23, when the Bridge did not yet
exist. It does now, and the releases since are its follow-through. Read off the
extension itself rather than from a plan: **version 0.11.2**, 29 commands, 23
settings, VS Code engine `^1.93.0`, MEP protocol `1.0.0`, API version `1`.

**M14 — the NERVIS Bridge — was signed off on 29 Aug**, and the four releases
after it are the parts §6 of this document specifies:

| release | what it closed |
|---|---|
| 0.10.0 | sends the session and trace RAVIS joins on (§6.5) |
| 0.10.1 | chat and the agent hold separate sessions, so a long run does not drag the conversation onto its model |
| 0.11.0 | publishes its events to NERVIS, with the trace on them (§6.4) |
| 0.11.1 | the envelope NERVIS's hub actually requires — the first version was accepted by the specification and rejected by the implementation |
| 0.11.2 | one chat turn names a single trace from start to finish |

**The agent's tool set is ten and closed**: `readFile`, `listFiles`, `search`,
`applyEdit`, `writeFile`, `runCommand`, `readDiagnostics`, `gitStatus`,
`gitDiff`, and since Clarvis 0.17.5 `readSkill`, offered only to a run of
Clarvis's own engine whose instructions list the owner's skills (§5.6). A
read-only run is handed only the reading tools, never `readSkill`, so §7.3's
restraint is a property of what the model receives rather than an instruction
it is asked to respect.

**`clarvis.status.read@1` is the only capability declared `available`.** The
event stream works — it heartbeats, replays a backlog on reconnect, and carries
§6.4's families — and is declared honestly as not-available because **nothing
consumes it**: NERVIS's dashboard reads `/v1/status` per instance and its event
screen shows its own hub. §4.1 forbids advertising an operation that has not
passed conformance, and a capability nothing has ever read has not been through
one. This is the rule working, not a defect.

Milestone status belongs to `plan.md` §7 in the Clarvis repository and is not
restated here beyond this: M0–M9 are built and shipped, plus M9d2, M9d3, M9h and
M13; M9g and M10 are designed and not built; M11 is the release gate rather
than a future milestone. **Where this section and `plan.md` §7 disagree, §7 is
right and this is stale.**

**Corrected and brought forward, 12 September 2026.** Two statements above were already wrong
on 31 August, and the rest has moved on. Re-read from the extension today:

- **Version 0.15.4.** Still 29 commands, 23 settings, VS Code engine `^1.93.0`, MEP protocol
  `1.0.0`, API version `1`, and the same nine tools. The release table above stops at 0.11.2
  and is not extended here: the ecosystem's `RELEASES.md` carries Clarvis's notes from 0.12.3
  to 0.15.4.
- **`clarvis.status.read@1` is not the only capability declared `available`.**
  `clarvis.config.summary@1` has been `available` since it was added on 30 August, and
  `clarvis.voice@1`, added on 29 August, is resolved at startup — `available` on a desktop host
  with voice on, `degraded` where the panel plays the speech, `unavailable` with voice off
  (`src/bridge/protocol.ts`).
- **`clarvis.events@1` is declared `degraded`, not unavailable**, and has been since 29 August:
  served and conformant, with the reason that nothing subscribes to it yet. The argument in that
  paragraph stands; the state it names was wrong.
- **The built list misses M14 and half of M8i.** M14, the Bridge, is the milestone this section
  opens with. M8i's first half — reasoning blocks stripped at the provider
  (`src/model/reasoning.ts`) — shipped on 20 August and is ticked in `plan.md`'s M11 checklist.
  M8j is not built: `plan.md` signed it off as deferred until M9h needs it.

## 3.3 Corrections to the source drafts

- **M13 live VS Code log tailing is built, not planned.** The source addenda describe it as a
  planned milestone. It ships today behind a user approval prompt that names the risk
  explicitly, writing to `.clarvis/vscode.log` — `src/logtailing/logTailing.ts`, commands
  `clarvis.startLogTailing` / `clarvis.stopLogTailing`. The ecosystem consequence is unchanged:
  it is a security-gated raw diagnostic aid, **never the primary status contract**.
- **M11 is an active release gate**, not a future milestone. Ecosystem work must not disturb it.

---

# 4. Proposed architecture and strict non-goals

```text
Clarvis extension host → RAVIS OpenAI-compatible gateway → providers/runtimes
        │
        └→ optional Clarvis Bridge → NERVIS
```

**Proposed additions:** RAVIS as a preferred generic OpenAI-compatible provider; independent
`ravis/clarvis-chat` and `ravis/clarvis-agent` roles; an optional extension-host-scoped Bridge
for health, capability, status, events and diagnostics; shared correlation IDs and redacted
ecosystem events; evidence-based code-server support for selected versions.

**Non-goals — none of these may be built:**

- No NERVIS-level filesystem, command, SecretStorage or approval authority inside Clarvis.
- No always-on Clarvis daemon, and no lifecycle independent of the editor window — **except Codex
  tasks, which by the owner's decision of 13 September 2026 run in RAVIS, may outlive the window
  that started them, and are reattached by any Clarvis window of that workspace** (decided, not
  built: E-C9). Clarvis's own engine keeps the window's lifecycle.
- No raw VS Code event firehose that forces NERVIS to recreate Clarvis's interpretation.
- No requirement that RAVIS, NERVIS or SIRVIS be installed for direct Clarvis operation.
- No claim that the current `.vsix` supports code-server until the matrix passes.

---

# 5. RAVIS provider integration

Implement RAVIS through the **established generic OpenAI-compatible provider surface**, unless
a demonstrated incompatibility requires a narrowly scoped adapter. Configuration covers
endpoint, an authentication reference or local credential if required, selected chat role,
selected agent role, timeout, and TLS/local-trust policy.

Client model IDs are `ravis/clarvis-chat` and `ravis/clarvis-agent`. **Verified 2026-08-22:
Clarvis accepts them.** The `clarvis.{chat,agent}.model` settings are free strings with no enum
or pattern; `src/model/openaiCatalog.ts` filters only non-chat families, none of which these
match; and `describeOwner` treats a `vendor/` prefix as the owner label, so they display as
"by ravis". The picker's first entry is always free-text ("Type a model name… — anything,
including models not listed"), so no listed-or-not ID is unreachable.

The obligations this creates run the other way, onto RAVIS — chiefly that an **unauthenticated**
`GET /v1/models` must answer 200 within 2 seconds, because Clarvis's `custom` provider declares
`needsKey: false` and its availability probe sends no headers. A 401 there reads as *offline*.
See `RAVIS.md` §5.0.1 for the full list. **If RAVIS ever publishes a different canonical wire
identifier, RAVIS adapts — Clarvis is not changed to suit it.**

**Clarvis continues to own:** deciding whether a request is an answer or an action; tool
execution and workspace validation for its own engine; approval gating, checkpoints, Stop
behaviour and the agent loop; **the choice of coding engine; for Codex tasks, the approval and
question interface, steering and Stop requests, git branches and commits, the checkpoint and
switching**; conversation and workspace state; and user-facing narration.

**RAVIS owns** route selection and provider credentials, **and for Codex: hosting the Codex
process, agent sessions, the relay and the project lock (runbook §2.2)**. **RAVIS never receives
permission to execute a Clarvis tool** — it may only return model output and tool-call requests for
Clarvis to validate and run. **A Codex task's tools are Codex's own, run under Codex's sandbox and
permission profile. RAVIS relays each of Codex's approval requests to Clarvis and never grants one
(its file-rules re-test on its own throwaway folders is not a task; runbook §2.2). It may only
decline and pause under the stated unanswered-request policy. Only a Clarvis window holding the
task's session token may answer.**

*The Codex parts of these two paragraphs were decided on 13 September 2026 and are not built
(E-C9, §5.5).*

## 5.1 Separate roles stay authoritative

Chat, planning and personality requests map to the RAVIS chat profile. Coding-agent and
tool-loop requests map to the agent profile. **Switching one must not change the other.** A
live thread survives a route or provider change under existing Clarvis semantics.

The agent role requires confirmed tool support. **An auth, rate-limit or network failure must
never be cached as "model lacks tools."**

Do not replace Clarvis's explicit configuration with heuristics. Tools being present may help
RAVIS understand a request, but Clarvis's configured model and pool remain the preferred role
signal.

**Role gate:** the trace shows the correct profile for each role; chat works when the agent
profile is unavailable; an agent start refuses cleanly when tool capability is unknown or
absent; streaming, stop, retry and error presentation match direct-provider behaviour.

## 5.2 Capability probing stays authoritative

Clarvis probes rather than trusting family names. **Do not add static ecosystem metadata inside
Clarvis that bypasses this principle.** RAVIS's `clarvis-agent` pool must satisfy the probe
rather than asking Clarvis to trust the pool's name.

`ravis/clarvis-codex` is the one engine Clarvis recognises by id, confirmed by RAVIS's `/api/v1/codex`. A
404 there refuses the run. *(Decided 13 September 2026; not built, E-C9.)*

## 5.3 Credential boundary

Clarvis stores only the credential needed to reach RAVIS, if RAVIS requires one. Cloud provider
keys move to RAVIS secure storage. Existing direct-provider keys stay in Clarvis SecretStorage
for fallback. **Never migrate or delete credentials automatically.**

Bridge and NERVIS endpoints must not expose secret presence beyond a coarse
"provider configured" capability where genuinely needed. Logs and events redact headers,
endpoint tokens, prompt and source content, raw paths and model responses by default.

## 5.4 Standalone and fallback

Clarvis starts with the Bridge disabled or NERVIS absent. RAVIS loss falls back to the
established direct-provider path where configured and where user policy permits. **Fallback
never crosses a privacy or cost boundary silently.** If the agent requires tools and only a
non-tool fallback exists, **refuse the agent run while leaving chat usable.**

No RAVIS, SIRVIS, NERVIS or collector availability problem may break activation, local
observation, workspace memory, direct-provider settings or safe teardown.

## 5.5 Codex tasks

**Decided by the owner on 13 September 2026 (runbook §2.2); specified here, in `clarvis/plan.md`
M15 and in RAVIS's contract fixtures; built in Clarvis 0.17.0 (E-C9), not yet walked live.** Clarvis gains a second coding engine,
OpenAI's Codex, which RAVIS runs. This is what Clarvis owes that arrangement. RAVIS's side is
`RAVIS.md` §15.1.2, and the shapes are the fixtures in `ravis/tests/fixtures/relay-contract/`, of
which Clarvis keeps a hash-checked copy in `src/test/fixtures/`. The engine's id is `ravis/clarvis-codex`,
named like the Clarvis pools; it was `ravis/codex` until the owner renamed it on 13 September 2026
(Clarvis 0.16.1, RAVIS 0.23.11), with no alias.

- **Selection.** Codex runs only when the effective coding model is exactly `ravis/clarvis-codex` — chosen
  in user scope, or recorded by Clarvis as the override for one unsettled task — the base URL is
  loopback, and the workspace is trusted. `ravis/clarvis-codex/<x>` is refused, and so is `ravis/clarvis-codex` set
  by a repository's `.vscode/settings.json`. Clarvis 0.17.0 and later sends `X-Clarvis-Engines:
  codex` when listing models, and only then does RAVIS list the id. It is never a chat model: read-only
  answers refuse it, and RAVIS's `/v1` answers it with 400.
- **Readiness.** Before a task starts, `GET /api/v1/codex` must read `state: signed_in`, a runtime
  verdict of `tested` or `accepted`, `strict_rules: proven` and a running process. Otherwise the run
  is refused with the state's reason and, where it helps, a **Sign in** line pointing at the
  dashboard's Codex card, with the address shown as text too. A refusal never moves the task to a
  paid engine.
- **The relay client.** Actions are JSON POSTs carrying the `Idempotency-Key` RAVIS requires; events
  arrive over SSE with `Last-Event-ID` resume, and a `409 EVENT_CURSOR_EXPIRED` is recovered from a
  snapshot. Each session's capability token is kept in
  `~/.local/share/clarvis/agent-sessions/<sha256(root realpath)>.json` (folder 0700, file 0600,
  written atomically, deleted when the session ends). code-server windows present the launcher's
  `client.clarvis` credential from their environment; desktop VS Code reads the same credential from
  the file named in a machine-scoped setting, `clarvis.ravis.credentialFile`.
- **Reattach, from either host.** A task lives in RAVIS, so closing or reloading the window does not
  stop it. On activation Clarvis lists the workspace's sessions, reattaches with its stored cursor or
  a snapshot, and asks any question still waiting. A missing token offers **Reconnect**, which
  reissues it once no window has been attached for 60 s. A window counts as attached only while its
  chat panel is connected — the webview pings every 10 s, the host posts presence every 20 s, and a
  panel silent for 25 s is reported detached — because code-server keeps a closed tab's extension
  host alive for 3 hours.
- **Approvals and questions.** Clarvis renders only the decisions RAVIS allows for each request, one
  question at a time, first in first out. After any await the decision is re-evaluated right before
  the POST, whose key is `<request id>:<window id>`. `REQUEST_ALREADY_RESOLVED` clears the question
  and says who answered; `SESSION_STOPPING` clears it; `DECISION_NOT_ALLOWED` redraws from the
  returned list. There is no "don't ask again". Unattended mode answers on its own only a command
  with no gate category, no network and no escalation, or a file change inside the workspace, and
  only while a window is attached; RAVIS approves nothing while none is. An unanswered question waits
  2 hours with a window attached or 30 minutes without one; then RAVIS answers it with its stop
  response, pauses the task and keeps the thread. The mapping from Clarvis's modes to Codex's
  approval settings lives in RAVIS, measured by calibration. **As built in 0.17.0:** a site ask waits
  behind Codex's own requests, since only those hold Codex up; typed words that aren't an answer are
  steered to Codex and the question stays on screen; "Stop the run" does the Stop button's stop (an
  interrupt) rather than posting a `stop` decision; and files are copied for undo before an approved
  change, added files and rename targets included.
- **Stop.** Stop clears the showing question and the queue locally at once, before any network
  call, then posts `interrupt`, retrying while RAVIS is unreachable and saying so. RAVIS answers every
  open request with its stop response before interrupting, refuses later answers with `409
  SESSION_STOPPING`, and confirms the task's processes are gone. Only then does one window claim the
  settle and save; `leftover` processes block saving until they are stopped. A stop from the menu
  bar or the dashboard arrives as `stopped_by`, and the window says "Stopped from the menu bar." or
  "Stopped from the dashboard."
- **Steering and feedback.** Text typed while a turn runs is steered into it, or queued by RAVIS for
  the next turn on a race. Text typed while stopping, switching or detached — or refused with `409
  SESSION_STOPPING` or `PROJECT_LOCKED` — goes into the checkpoint's `latestFeedback`, and is always
  included in the next turn or in the destination engine's first brief.
- **Bookkeeping and settle.** `STEP:` lines, the run ledger and the checks Codex ran are rebuilt from
  relayed completed items. RAVIS enforces the step cap, so it holds while detached. Git stays
  Clarvis's: after a stop, a completion, a pause or an uncertain end, one attached window claims the
  settle, reconciles git, commits on the task branch, saves the checkpoint and posts `settle`. RAVIS
  never runs git.
- **A folder without git** (owner's decision, 14 September 2026; built in Clarvis 0.17.2). A task
  branch needs a repository with a commit, so a folder that isn't one, or whose repository has no
  commit, refuses the task. When git is installed and RAVIS would take the folder, Clarvis offers **Set
  up git here** in the chat, even if Clarvis's own `git init` offer was declined earlier. It runs `git
  init` and an empty first commit that takes nothing already staged, then carries the refused task on
  without its being retyped. Typing "git init", "set it up" or "yes" answers it the same way and is
  never sent to Codex. Before offering, Clarvis reads `GET /api/v1/agent-sessions?workspace_root=…`;
  a `422 WORKSPACE_ROOT_NOT_ALLOWED` gives RAVIS's reason and nothing is offered. A failed setup
  removes the `.git` it had just made and says why; git not installed gets install advice and no
  button; Workspace Trust and every other refusal come first. The earlier decline is forgotten only
  once git is really set up. Clarvis's own pre-run offer is asked for its own engine only. Task
  folders NERVIS hands over start as repositories with the brief committed (NERVIS 0.29.3), so they
  never meet this.
- **Build on earlier work, or start fresh** (both engines; Codex from Clarvis 0.17.3, Clarvis's own
  engine from 0.17.4; the owner's decisions of 15 September 2026, "Ask each time"). Before a task
  starts, Clarvis looks for work an earlier run of the same engine left in the project on a branch
  that still exists and isn't merged into the project's trunk (the plan's declared trunk, else the
  branch a new task starts from, never a literal `main`). When there is some, the owner is asked each
  time: **Build on `<branch>`** or **Start fresh from `<trunk>`**, a new branch from the trunk that
  never stacks on a `clarvis/*` branch. Typed answers work like the buttons and never reach the
  model; unanswered, stopped or a mode switch runs nothing; Unattended doesn't ask, and picks and
  says which: it builds on the window's branch when that is left work, and otherwise starts fresh.
  Each engine offers only its own left work, and a branch both engines left is offered by both.
  - **Codex:** left work is a RAVIS session that is `idle`, whose key this Mac's token file holds and
    RAVIS accepts (at most three offered, the window's branch first, then the most recent). **Build
    on** picks that session back up with a `continue` turn carrying the new request, on its own
    branch, after giving it the chat's current mode, so Codex adds to its own work and keeps the
    conversation; the earlier session stays idle on Start fresh. A session without a stored key isn't
    offered, and no token is reissued to find out.
  - **Clarvis's own engine:** left work is remembered in `<git dir>/clarvis-left-work.json` (0600,
    never committed, shared by code-server and desktop VS Code), written whole under the project lock
    at the end of each run that ends on its own branch: one entry per branch, newest first, at most
    20, each with `branch`, `taskId`, `task`, a redacted `summary`, `startedFrom`, `headCommit`,
    `files`, `inFlightAtStart`, `uncommitted` (with SHA-256, or null), `endedAt` and `host`. A run
    counts as left while its branch exists and still holds that tip, and either has commits not in
    the trunk or is the window's branch with the run's files still uncommitted as it left them.
    **Build on** runs the new task on that branch, on top of that work, told the earlier task, its
    closing summary and the branch's commit subjects. Tasks started from the command palette don't
    ask and aren't remembered; only chat runs are.
  - **The earlier run's own files are committed first** when the window is on its branch, with a chat
    line ("Committed the last run's N files to `<branch>` so this run builds on them", or "… before
    starting fresh"); files the owner had in flight, or changed since, are never swept in.
  - **Switching branches**, for either engine: ignored files don't count; untracked files the other
    branch doesn't have come along, named in a line of their own; tracked changes, or untracked files
    that would clash, refuse the switch, naming the files.
- **The checkpoint** is `<git_dir>/clarvis-task-checkpoint.json` (0600, never committed;
  `<root>/.clarvis/task-checkpoint.json` without git), because `workspaceState` is per host and a task
  moves between hosts. It is written atomically, only while holding the project lock, at most 64 KB,
  with no secrets, and is never sent to NERVIS or the Bridge.
- **Branch continuation and switching.** `Clarvis: Switch coding engine` moves a task between Codex
  and Clarvis's own engine in both directions, after a confirmation naming what it spends — API
  providers one way, the ChatGPT plan's allowance the other. The project lock stays held, in
  `transferring`, until the destination has started; the destination continues on the same task
  branch; pending approvals are never answered for the destination. A Codex session left idle by a
  switch is reused on the way back, never ended or archived.
- **One writer: the lock and the fence.** RAVIS's project lock is authoritative while RAVIS is up; a
  lock file in the checkout, `<git_dir>/clarvis-engine.lock`, is the floor when it is not. Both
  engines take it — **a behaviour change for Clarvis's own writing runs** — and read-only answers take
  none. Holders are judged by the shared lock rule: `gone` only when the holder's process is not
  running or its start time differs; `unresponsive` when the heartbeat is over 90 s old and the
  observer has been awake at least 90 s; `alive` otherwise — so a stale heartbeat after a sleep never
  makes a live holder dead. A Codex holder is attached to, never taken over. A Clarvis-engine holder
  that is `gone` is reconciled; one that is `unresponsive`, or alive and waiting, may be taken over
  after a confirmation, its running command killed with its whole process group first. A holder that
  lost the lock — `409 LEASE_REVOKED`, or a lock file no longer naming its window, never RAVIS merely
  being unreachable — never commits, writes the checkpoint or releases.
- **The safety box.** Codex's commands run in Codex's sandbox, not Clarvis's: writes only in the
  project and the task's temp folder, internet only for the sites the owner allowed, and the key and
  password files — RAVIS's credentials, the launcher's `.run` folder, Clarvis's session tokens,
  Codex's own sign-in, `~/.ssh`, `~/.aws`, `~/.config/gh` — denied by a permission profile. **The
  profile is proven for Codex 0.154.0** (calibration, 14 September 2026); after every Codex update
  RAVIS refuses to start a Codex task until the new version is accepted and re-tested. Clarvis sees what Codex asks, not what it runs without asking; the
  deny-list only adds warnings to relayed approvals, and writes outside the workspace can only be
  declined. Workspace Trust still refuses untrusted folders, and RAVIS refuses folders outside its
  allowed roots — including, by the owner's decision, NERVIS-ecosystem, Clarvis's own repository and
  the coding folder itself.
- **Websites** (the owner's decisions, 14 September 2026). Before a task starts, Clarvis scans the
  project — registry settings in `.npmrc`, `pip.conf`, `pyproject.toml` and `requirements*.txt`, Cargo's
  registries, `Gemfile` sources, `.gitmodules` and URLs in the brief — for hosts Codex doesn't allow yet,
  and asks once, at most 20, with **Allow and start**, **Start without them** and **Cancel**, through
  `POST /api/v1/codex/sites`. Mid-task, the sites one turn was blocked from come as one card
  (`group_id`): **Allow** or **Keep blocked** per host, plus **Allow all**. RAVIS reopens the task's
  thread so a newly allowed site works; meanwhile a status line above the prompt says "Reconnecting
  Codex…", cleared on `site.reopened`, and `site.reopen_incomplete` says an allowed site may still be
  blocked. Once every host is decided, only the window that decided the last one sends `carry_on`,
  after the work is saved.
- **Model, effort and allowance.** The bowtie beside the chat box opens a menu: **API config** (the
  models menu it used to open) and a Codex section with Codex's model, its effort and one allowance
  line (the tightest window; the rest in a tooltip). The pick is kept in `clarvis.codex.model` and
  `clarvis.codex.effort` (application scope, read from user settings only), checked against what Codex
  lists, and applies to the next task. The chat says which model and effort a task runs with, and
  falls back to the default when a pick is no longer listed.
- **Out of scope.** Switching engines automatically when the allowance runs out; Codex for chat,
  planning or the interview; remote hosts; any NERVIS control of a task other than Stop; Codex on the
  ecosystem's own repositories.

## 5.6 Skills for Clarvis's own engine

**Decided by the owner on 15 September 2026; RAVIS 0.27.0's `skills.json`; built in Clarvis 0.17.5.**
RAVIS alone knows which skills exist and which are switched on for the models that aren't Codex; the
owner switches them on NERVIS's Skills page.

- **The list.** Read once at a run's start with Clarvis's client credential
  (`GET /api/v1/skills/models`) and added as a capped section after Clarvis's own rules: each
  skill's name, id and a description cut at 160 characters, about 1,500 characters in all. Nothing is
  added when no skill is on, or when RAVIS isn't the run's provider. A switch counts from the next
  run. The section tells the model to read a skill that fits with `readSkill` and follow its
  instructions (steps, format and style) for how it does the parts of the task the skill covers,
  unless the owner's request or plan.md's conventions say otherwise; a skill never widens the task
  (Clarvis 0.17.6).
- **Reading a skill.** `readSkill` (`GET /api/v1/skills/models/read`) runs in the extension host,
  never through `runCommand`. Its text arrives as the skill's instructions: followed for how the
  covered parts of the task are done, after the owner's request and plan.md's conventions, never
  widening the task, and never as the owner's message (Clarvis 0.17.6). It
  only reads and needs no step approval; a run's first eight reads don't count against the step cap.
  A refused read is a tool result, never a failed run.
- **Precedence.** Skills can't override Clarvis's rules: step approvals, the command gate, Workspace
  Trust, protected paths and the mode still apply, and a skill's commands run only through the normal
  command tool with its approvals.
- **Failure.** When RAVIS or the skills list can't be read, the run goes on without skills; the chat
  says so once, only when skills were on last time, and one log line records it.
- **Scope.** Coding runs of Clarvis's own engine only: not answers, plain chat, the tool check or
  background calls. Codex gets its skills from RAVIS directly (§5.5). Clarvis keeps no skill text
  past the run, and nothing about skills reaches the Bridge.

**Skills as slash commands (Clarvis 0.17.7).** Decided by the owner on 15 September 2026.

- **Naming a skill.** In Clarvis's chat, `/skill-name <request>` uses a switched-on skill, and
  `/skill <name or id> <request>` always reaches one. Built-in commands win over a skill of the same
  name, their aliases included; two skills sharing a name are reached by their full id.
- **Checked at send time** against `GET /api/v1/skills/models`, with Clarvis's client credential.
- **Refusals.** An unknown command, a skill with no request, a skills list RAVIS can't give, or a skill
  typed while planning, during a run or while a question waits gets one line, and nothing runs.
- **Clarvis's own engine** reads the skill's `SKILL.md` with `GET /api/v1/skills/models/read` before
  the run or answer starts. It is placed after Clarvis's own rules, at most 6,000 characters, framed
  as the skill's instructions, with the rest readable through `readSkill`; a failed read runs nothing.
- **Codex** receives `$name <request>`, Codex's own way of naming a skill, and applies the skill only
  if it is switched on for Codex, which Clarvis can't see and says so. Codex notices a `$name` mention
  anywhere in a task's text, not only at the start (Codex 0.154.0, `extract_tool_mentions`): a task or
  queued feedback that contains `$word` matching a skill switched on for Codex, such as a shell
  snippet with `$deploy`, makes Codex use that skill uninvited. Names Codex can't mention (characters
  outside letters, digits, `_`, `-`, `:`, or a common environment-variable name such as PATH or HOME)
  are refused by Clarvis with one line instead of being sent.
- **`/help` and `/manual`.** `/help` lists the commands and the switched-on skills, and `/manual`
  stays a built-in.
- **The pop-up.** Typing `/` shows matching commands and skills. Its list is read again when the
  panel opens, when the window gets focus, when a setting changes, and at most once a minute while
  typing.
- **No new RAVIS or NERVIS route.**

---

# 6. The optional Clarvis Bridge

The Bridge conforms to the MEP (`ECOSYSTEM_RUNBOOK.md` §4): health, identity, capabilities,
version, event envelope, trace/request/session IDs and error envelope. **Because Clarvis is not
a daemon, Bridge endpoints exist only while that extension host is alive.**

## 6.1 Identity

```text
service_type:  clarvis
service_id:    stable installation-scoped ID, in extension global state
instance_id:   unique per extension-host lifetime
machine_id:    opaque, resettable, installation-scoped, locally generated,
               NOT hardware-derived
workspace_id:  opaque, salted, scoped for local correlation
versions:      Clarvis build / API / protocol
host_kind:     vscode | vscodium | code-server | other proven-compatible host
```

**The raw workspace path and name are private by default.** If user-visible workspace labelling
is enabled, publish a deliberately selected label *separately* from the opaque ID.

**Every Bridge request is authenticated, with the token NERVIS issues at registration.** The
Bridge registers, receives a token in the registration response, holds it for the extension
host's lifetime, and requires it on every request including `/ecosystem/events`. NERVIS presents
the same token when it reads the Bridge. It is never written to a log, an event, a trace or a
diagnostic packet.

> **Amended 29 Aug, and the original direction is recorded because the reasoning still applies.**
> This said the *Bridge* generated the token and handed it to NERVIS at registration. NERVIS's
> receiving half had already shipped refusing exactly that: its registration allowlist has no
> field for a registrant's token, above a comment stating that secrets are stored separately and
> *"the strongest form of separately is not at all"*. Written as it was, a Bridge would register
> successfully and then be unreadable by the only service meant to read it.
>
> Inverting the issuer costs nothing the argument below depends on. Registration is already
> gated by NERVIS's enrolment secret, a `0600` file beside its database — so a process that
> cannot read that file cannot obtain a token, cannot be registered, and cannot have its port
> read. And a local reader without the token still gets nothing from the Bridge, which is the
> same hole read backwards. What changes is only who mints it, and it removes a second secret
> from a system that already had one for this purpose.

Two things force this, from opposite directions. NERVIS refuses to register a service that
cannot authenticate — §5.1 requires an authentication reference and forbids accepting an
unauthenticated process's claimed service type — so an unauthenticated Bridge makes Stage 8
unreachable without NERVIS abandoning its own rule. And the Bridge's port is allocated per
extension host and discovered through registration rather than fixed, so **any local process can
bind the port a Bridge would have used and impersonate a Clarvis instance**, feeding fabricated
`clarvis.gate.requested` and `clarvis.agent.*` events into the operator's dashboard. That is the
runbook's "malicious service registration" case, and the same hole read backwards exposes the
Bridge's own redacted status to any local reader.

The token authenticates the *channel*; it grants nothing beyond what §6.2 already publishes.
**A holder of the Bridge token still cannot resolve a gate, run a tool or execute a command**
(§6.7).

## 6.2 Capabilities

```text
clarvis.status.read@1
clarvis.config.summary@1
clarvis.events@1
clarvis.diagnostics.summary@1
clarvis.logs.reference@1        only where M13 log tailing exists and is approved
clarvis.ravis_provider@1
clarvis.voice@1                 as built, 29 August 2026 — see the note below
```

**As built, 12 September 2026: a seventh capability, and the states in force.** `clarvis.voice@1`
was added on 29 August because runbook Stage 9's exit asks that *"voice limitations are advertised
through capabilities"*, and nothing advertised voice at all. It is resolved at startup —
`available` on a desktop host with voice on, `degraded` where the panel plays the speech (a browser
workbench or a remote host), `unavailable` with voice off. Of the rest, `status.read@1` and
`config.summary@1` are `available`, `events@1` is `degraded` because nothing subscribes to it, and
`diagnostics.summary@1`, `logs.reference@1` and `ravis_provider@1` are `unavailable`, each with its
reason (`src/bridge/protocol.ts`).

**`clarvis.config.summary@1` is the read that exists so a write does not have to.**
§6.7 forbids NERVIS changing a Clarvis setting and the Bridge has no write path to extend, so
the useful half of *"can the dashboard manage Clarvis"* is answered by publishing the
configuration instead of accepting one: the values in force, and the setting id for each, so an
operator is told where to change a thing rather than sent to look for it.

It publishes an **allowlist**, and the list is the security argument rather than a convenience:

- model names, provider ids, modes, booleans and the theme id — values whose purpose is to be
  shown to the person who set them;
- **never a path.** Whether an enrolment secret is configured travels; the path to it does not;
- **never a URL somebody typed.** A base URL can carry a token in a query string and can name an
  internal host, so what travels is *loopback*, *remote* or *unreadable* — the fact a peer wants,
  without the string;
- **nothing from SecretStorage**, which is not settings at all and so is not a rule anybody has
  to remember to apply.

A setting that does not fit that description is not added to the list. A summary is a document
about configuration, and §6.4's prohibitions apply to it exactly as they apply to an event.

> **There is no `clarvis.gates.approve`, and no unrestricted tool or command capability.**

## 6.3 Status model

```text
GET {bridge}/v1/status        interpreted state for this extension host
GET {bridge}/ecosystem/*      identity, health, capabilities, version, events (MEP §4.1)
```

One instance, one answer: `/v1/status` describes **the extension host serving it** and never
aggregates two windows (§6.6). It is read-only — there is no write path on the Bridge, and
§6.7 is why. Authentication per §6.1 applies to both surfaces.

Expose interpreted, bounded state — never raw editor internals:

- lifecycle and readiness, current mode;
- one of `idle`, `chatting`, `agent_running`, `waiting_for_approval`, `stopping`, `failed`;
- current run or task opaque ID, step counts *if genuinely known*, elapsed time *if measured*;
- aggregate diagnostic counts, build and test outcome;
- the current RAVIS route reference — not credentials, not the full prompt;
- a recent event cursor and an optional log reference.

**Unknown values stay unknown. Never invent a duration, step total, branch, task or model
route.** NERVIS reads status; it never becomes the owner of Clarvis state.

**As built, 12 September 2026: `/v1/status` carries less than this list.** It returns `state` (one
of the six above), `activity_id`, `steps_taken`, `elapsed_ms`, `awaiting` — a gate's category — and
`observed_at`, and nothing else (`src/bridge/activity.ts`; `statusBody` in `protocol.ts`). Two
items are published elsewhere: readiness on `/ecosystem/health`, which reports listening and
registered, and the chat mode with each role's provider and model on `GET /v1/config`
(`clarvis.config.summary@1`). Not published anywhere yet: aggregate diagnostic counts, build and
test outcome, the current RAVIS route reference, a recent event cursor and a log reference.

## 6.4 Events

```text
clarvis.lifecycle.ready/stopping        clarvis.gate.requested/resolved
clarvis.chat.started/completed/failed/cancelled
clarvis.agent.started/step/completed/failed/cancelled
clarvis.tool.started/completed/failed/refused
clarvis.diagnostic.changed              clarvis.task.started/completed
clarvis.model.requested/completed/failed
clarvis.capability.changed
```

Default payloads carry identifiers, categories, state, counts, timings and result classes —
**not** source code, terminal output, prompt or response text, command strings, file contents,
raw paths, secrets or approval details. A user-enabled diagnostic export may add content under
explicit scope, preview, retention and revocation controls.

## 6.5 Tracing

Propagate `trace_id`, `request_id`, `session_id` and `workspace_id` from Clarvis to RAVIS, and
attach them to model and agent events.

**Tracing is observability. It must not change Clarvis's execution semantics.** A trace ID never
authorizes a tool or a gate. Telemetry failure can never delay or fail editor work; buffering is
bounded.

**As built, 12 September 2026: two of the four identifiers travel.** An OpenAI-compatible model
request carries `traceparent` — the trace id, in W3C form — and `x-session-id`, and nothing else
from this list: no request id and no workspace id (`lineageHeaders` in `src/model/lineage.ts`,
called only from `OpenAiCompatibleProvider.ts`, so a request through the Anthropic adapter carries
neither). Chat and agent events carry the trace; model events are not emitted at all (E-C4).

## 6.6 Multiple simultaneous instances

Each editor window registers separately, identified by `(service_id, instance_id)` plus the
opaque workspace ID. Ports and sockets avoid collisions through OS-assigned endpoints or a
documented broker. **No instance overwrites another's registration.**

**Isolation gates:** two workspaces can use different chat/agent profiles, modes, histories,
keys and gate states; events and status from one never appear under the other; closing or
reloading one expires only its registration; the same workspace opened twice is still two
instance lifetimes and is presented honestly.

## 6.7 No NERVIS safety bypass

NERVIS may display Clarvis status, diagnostics, separately approved logs, and the fact that a
gate awaits the user. It **may not**: approve or refuse gates on the user's behalf; invoke file
tools or terminal commands; expand the workspace root; silently change unattended mode or
safety settings; read SecretStorage or model prompts and source by default; or keep Clarvis
running after the extension host closes.

A future remote-control contract would require its own threat model, authentication,
presence/consent model, granular operations, audit, revocation and Clarvis-plan approval. Until
then, **an agent asked to add such control must stop.**

**E-C8 is not that contract, and the distinction is the direction of the verb.** NERVIS may
write a task brief into the workspace, because writing a file into a directory it already writes
to is not control of anything — Clarvis reads it with the flow that reads any plan, a person
approves it in the editor, and every tool call passes the same gates. What is still forbidden is
NERVIS *starting* that run, or reaching past the approval to the tool. The test that keeps the
two apart: **with the Bridge stopped, E-C8 still works.** Anything that stops working without the
Bridge is remote control wearing a different name, and this clause applies to it.

One threat the contract would have to answer, recorded here because it is easy to miss: §6.1
notes that any local process can bind the port a Bridge would have used and impersonate a Clarvis
instance. A write path inverts that — a fake *NERVIS* could push tasks into the editor — so the
contract needs mutual authentication rather than the single Bridge token, which authenticates
only the channel.

**Codex tasks** (decided 13 September 2026; not built, E-C9). NERVIS may show that a Codex task is
running or waiting for an answer: project name, state and age only. It may not answer, steer or
start one, and RAVIS refuses NERVIS's credentials on those routes. After the owner confirms, it may
stop one through RAVIS's stop-only owner route. Stopping controls nothing else, and the attached
Clarvis window says where the stop came from. Starting or cancelling a sign-in, confirming the
account and accepting a Codex version through RAVIS are runtime setup, not control of a task.
NERVIS never starts the file-rules re-test; the owner starts it from the menu bar.

## 6.8 Relationship to M13 raw logs

M13 tails VS Code logs into `.clarvis/vscode.log` behind a Clarvis security gate, and is built.
**The ecosystem must not treat that text file as the primary status contract.** Structured
Bridge events and status are the normal integration; M13 stays an explicitly approved raw
diagnostic aid with workspace and privacy consequences.

## 6.9 If a management API is ever wanted — recorded, not authorised

**This section is not the contract §6.7 asks for, and nothing in it permits building
one.** §6.7 still stands in full: an agent asked to add such control must stop. What
follows is the analysis from one such conversation, written down so the next person
starts from it rather than re-deriving it — including whoever asks again in a year.

The question was reasonable and is worth stating fairly: NERVIS, RAVIS, SIRVIS and
Clarvis are one ecosystem built by one person, so a settings API between them does
not feel like *external* control. The answer is that the trust boundary is not
organisational. It is the loopback port, and §6.1 already records why: any local
process can bind the port a Bridge would have used.

### What changes, and in which direction

Sorting Clarvis's settings by consequence turns out to be more useful than sorting
them by subject, and it produces three tiers.

**Preference-shaped — a contract could plausibly cover these.** `chat.provider`,
`chat.model`, `agent.provider`, `agent.model`, `voice.enabled`,
`voice.selectedVoice`, `voice.fishAudio.engine`, `voice.trimLongReplies`,
`model.tuneLocalLoads`, `watch.minDurationSeconds`. NERVIS already *reads* most of
these through §6.2's config summary. Getting one wrong makes Clarvis worse, not more
dangerous.

**Consent-shaped — per-change approval in the editor, never a silent write.**
`chat.baseUrl.*` reads like a preference and behaves like exfiltration: it decides
which host receives the prompts. `agent.maxStepsPerTask` decides how long an agent
runs with nobody watching. `voice.dailyRequestCap` is a spending decision in one
direction only. `chat.mode` set to `unattended` is the thing §6.7 names outright —
and *silently* is the load-bearing word in that clause.

**Refused however good the contract.** Everything §6.7 lists, plus three settings
that are not obviously in its list and belong there:

| setting | why it can never be remote |
|---|---|
| `bridge.enrollmentSecretPath` | the credential authenticating registration. NERVIS choosing its own trust anchor is the definition of having none |
| `bridge.nervisUrl` | NERVIS pointing Clarvis at a *different* NERVIS. One compromised instance hands the editor to the next |
| `bridge.enabled` | turning on its own visibility. If NERVIS can un-hide itself, switching it off stops meaning anything |

These three are what make a management API bootstrap itself: every other setting is a
consequence, and these are the authority.

**And a workspace is the second adversary for the same three.** The list above was
written against NERVIS; an audit found the identical authority reachable from a much
cheaper direction. Until 4 Sep these were ordinary window-scoped settings, so a
repository's own `.vscode/settings.json` could turn the Bridge on, name any URL as
NERVIS, and name any file as the enrolment secret — whose contents are then sent to
that URL as a bearer token. Opening a folder was the entire attack.

They are `scope: "machine"` now, which is VS Code refusing the workspace value before
Clarvis ever reads it — the same declaration `chat.baseUrl.*` already carried for the
same reason. Three narrower checks sit behind it, because a scope declaration is one
mechanism and this is a credential leaving the machine: the Bridge refuses to start in
an untrusted workspace at all, `nervisUrl` is rejected unless it is loopback, and the
enrolment secret must be a regular file at `0600` — never a symlink, never a directory.
The path may be logged and the contents never are.

> **Verified in a real editor, because no unit test in either repository can reach
> this.** The guard is VS Code's own scope enforcement. A fixture repository asking for
> all three at once was opened untrusted (the Bridge stayed inert) and then trusted and
> reloaded — the branch that matters, since trust is given routinely and the trust check
> then stops helping. Trusted, the Bridge ran on loopback with the real `0600` secret and
> registered normally, and the hostile secret path never reached the file check at all:
> `/etc/hosts` is `0644` and would have produced a refusal line, which is absent.
> Recorded in `STATUS.md`, 2026-09-04.

### The rule worth keeping, if the list is ever forgotten

**NERVIS may narrow what Clarvis will do; it may never widen it.** Lowering a step
cap, a spend cap or a voice budget is safe in a way that raising the same number is
not — the same setting, the same call, the opposite blast radius. A contract written
in terms of *settings* has to enumerate that case forever. One written in terms of
*direction* gets it for free, including for settings nobody has invented yet.

### Mutual authentication is not one requirement of six

§6.7 lists threat model, authentication, presence and consent, granular operations,
audit, revocation and Clarvis-plan approval. Authentication is not one item among
them; it gates every other tier, including the harmless-looking one.

Today §6.1's weakness means something local can impersonate *Clarvis* and feed NERVIS
a false reading — the cost is a wrong dashboard. A write path inverts the direction:
the same weakness lets something impersonate **NERVIS** and change which host an
editor sends its prompts to. Until Clarvis can prove *which* NERVIS is speaking, tier
one is not the safe part of the design — it is the part an attacker would target
first, because it is the part nobody would guard.

### What already exists, and why it is not this

E-C8 covers most of the practical want without any of this: NERVIS writes a task
brief into the workspace, Clarvis offers it as a build with the prompt visible and
editable, and a person starts the run. §6.7's test separates them cleanly — **with
the Bridge stopped, E-C8 still works.** Any settings write path fails that test by
construction, which is what makes it a contract question rather than a feature.

---

# 7. code-server compatibility

Treat this as a **compatibility spike, not an assumed target.** Architectural feasibility is
high — code-server runs a real Node extension host, and it runs locally on the same Mac, so
localhost RAVIS, localhost LM Studio, macOS subprocesses and the workspace filesystem may all
work naturally. **But verify empirically.** Current `.vsix` compatibility is unproven from the
README and plan alone.

## 7.1 Test matrix

Inspect and test: manifest engine, activation, `extensionKind`, and main/browser entrypoints;
Node and native dependencies; desktop paths, URI schemes, workspace FS and global storage;
extension activation; webview CSP, resources, messaging, focus, clipboard and streaming; chat;
agent; tasks; terminal shell execution; debug sessions and events; diagnostics; Git, worktree,
checkpoint and undo flows; workspace containment; `child_process` and the command sandbox;
audio playback and capture; SecretStorage persistence, encryption and availability;
localhost RAVIS; localhost LM Studio; install, update, rollback, reload, multi-window and
Bridge teardown; and the NERVIS reverse proxy, WebSockets, origins, auth and base path.

**Matrix axes:** Clarvis version × code-server version × OS/architecture × browser × direct vs
NERVIS-proxied × feature. **Cell values:** `PASS`, `PASS_WITH_LIMITATION`, `FAIL`, `NOT_TESTED`
— each linked to evidence.

**Spike exit:** install, activate, chat, agent, stream, stop, tool, gate, workspace boundary,
SecretStorage, persistence and teardown all pass on at least one declared supported
combination; voice limitations are advertised through capabilities and do not block core
support; unsupported combinations are never offered as supported.

## 7.2 SecretStorage caveat

**Do not assume code-server's `context.secrets` is backed by the macOS Keychain.** Verify its
storage behaviour. If it is file- or server-backed, document that clearly and label it
differently from desktop VS Code — never identically unless proven equivalent.

RAVIS integration reduces the exposure, because normal cloud provider keys move to RAVIS.
Clarvis-specific credentials such as voice-provider keys still require review.

## 7.3 Do not contort Clarvis

If Clarvis works in code-server but the framing is awkward, the correct response is **open a
separate browser tab**, not rewrite Clarvis. NERVIS embedding is not a Clarvis requirement;
both an embedded Code tab and an external-tab launcher are acceptable products.

**Do not fork code-server by default** — see `ECOSYSTEM_RUNBOOK.md` §6.2 Stage 9 for the fork
policy and the two host-level limitations that could ever justify one.

## 7.4 Visual identity

Clarvis's character does **not** depend on modifying the editor. In descending order of value
per unit of risk:

1. **Clarvis's own webview** — the avatar and chat surface, already fully owned, styled from
   `media/chat.css` read at render time. This is where the character actually lives.
2. **NERVIS chrome** around an embedded Code tab — pure NERVIS CSS, zero VS Code risk.
3. **A theme extension** — `contributes.themes` plus product- and file-icon themes, declared in
   Clarvis's own `package.json`. Ships in the existing `.vsix`, applies identically on desktop
   VS Code, VSCodium and code-server, and survives upstream updates.
4. **Workbench CSS/JS injection** — *avoid*. It trips VS Code's integrity check and re-breaks on
   every upgrade, because it patches a file the updater owns.

Any theming is presentation only. **It must not alter containment, gates or provider
behaviour**, and it must be verified against the same regression suite as any other change.

---

# 8. Milestones and falsifiable gates

Stated as ecosystem milestones **outside** the `plan.md` M-numbering, which continues to own
Clarvis's product milestones.

### E-C0 IMPLEMENTED — Invariant baseline

Pin the source `plan.md` / README version. Run established Clarvis tests and manual gates.

**Exit:** lifecycle, containment, gates, provider switching, chat/agent separation,
SecretStorage and teardown evidence captured. §3 of this document has a passing check or a
named manual check behind every row.

**Where it stands, 12 September 2026.** The invariants are in the code that ships, and §3 names
the source for each row, but the exit asks for a passing check or a named manual check behind
every row, and §3 cites files rather than checks. Some rows have a test that would serve —
`src/test/containment.spec.ts` for containment, `src/agent/gate/injection.test.ts` for the command
gate — but nobody has walked the table pairing each row with one, and `STATUS.md` has no Stage 0
baseline record.

### E-C1 LIVE VERIFIED — RAVIS provider *(configuration only)*

Configure RAVIS through the generic OpenAI-compatible adapter. **No source modification.**

**Exit:** Custom provider connects to RAVIS; chat works; agent tools work; separate chat and
agent pools work; Stop cancels. No Clarvis safety code moves into the adapter.

**Where it stands, 12 September 2026.** Runbook Stage 3, run on 23 August against a running RAVIS
(§3.1; `STATUS.md`, "M9, and what it actually proved"): an unmodified Clarvis set up through its
own provider UI, chat streaming, five sequential tool-calling agent steps with a real write, the
two pools resolving independently, Stop cancelling upstream work, and no Clarvis source changed.
One gap that run named itself: both pools resolved to the same model, so separate pools reaching
*different* models is unproven. Clarvis 0.15.2 has since added RAVIS-aware source for the key it
sends; §2's note records that.

### E-C2 — Role profiles, fallback and RAVIS regression fixture

Independent RAVIS chat and agent settings, explicit direct-provider fallback, and preserved
source fixtures or probes that let RAVIS validate its own Clarvis compatibility.

**Exit:** role isolation, tool capability, policy refusal, stream, stop, error and fallback
tests pass. **No new runtime dependency is introduced.**

**Where it stands, 12 September 2026 — left without a state, because one clause has nothing
behind it.** The fixture half is AUTOMATED VERIFIED on RAVIS's side (`RAVIS.md` M2,
`ravis conformance clarvis`); separate chat and agent roles have run live since Stage 3; and no
runtime dependency was added — `package.json` still declares none. But Clarvis has no explicit
direct-provider fallback: nothing in `src/model/` moves a request to another provider when RAVIS
fails. The one thing there named `fallback` (`ModelService.ts`, since 0.15.2) is the launcher's
RAVIS credential used when no key is stored, not a provider fallback.

### E-C3 LIVE VERIFIED — Bridge protocol

Opt-in, extension-host-scoped metadata and status endpoints.

**Exit:** MEP conformance; collision, disablement, start/reload/close, privacy and two-window
tests pass; Bridge absence does not affect Clarvis; no safety-gate bypass; no workspace escape;
no secrets emitted; **an unauthenticated caller is refused on every endpoint including the event
stream, and the token never appears in a log, event or trace.**

**Where it stands, 12 September 2026 — with two clauses uncovered.** Run against a real NERVIS on
29 and 30 August (`STATUS.md`, "Stage 8 — the Clarvis Bridge, built and driven end to end"): an
unauthenticated read refused with `401`, a `POST` answered `405`, two windows and then four
registering on distinct OS-assigned ports with distinct instance IDs, closing one removing only its
registration, and — in a real extension host — the Bridge off by default binding nothing and
writing nothing (`src/test/bridgeDisabled.spec.ts`). `src/bridge/server.test.ts` covers the token
on every route including the event stream, every non-GET refused, and no route that could approve
a gate. What nothing covers: **reload** has no test and no recorded run, and **nothing checks that
the token never appears in a log**.

### E-C4 AUTOMATED VERIFIED — Events and traces

Structured redacted events, correlation propagated through RAVIS.

**Exit:** event schema, ordering, dedup, redaction and bounded-buffer tests pass; a cross-service
trace resolves; normal Clarvis behaviour is unchanged; **tracing failure never blocks an agent
run**; an unauthenticated subscriber receives no events, and a process impersonating a Bridge on
a free port cannot register with NERVIS.

**Where it stands, 12 September 2026 — with two clauses unmet.** The suite covers Clarvis's side
of the exit: monotonic event ids, replay of only what a reconnecting client missed, a bounded
buffer that drops the oldest and counts the overflow, primitive-only payloads that cannot carry a
command, path or secret, a stream closed to an unauthenticated subscriber, emitting that never
throws, and a refusing or absent hub reported rather than thrown (`src/bridge/events.test.ts`,
`server.test.ts`, `publish.test.ts`, `eventForwarding.test.ts`). The impersonation clause rests on
NERVIS's enrolment secret and is not assessed here. Unmet: **a cross-service trace has never been
seen whole** — Clarvis to RAVIS to a provider needs an editor window publishing into it
(`STATUS.md`, the scenario table under "The build order, unambiguously", and the 8 September
Diagnostics entry). And **§6.4's list is only partly emitted**: lifecycle, gate, chat, agent and
capability events exist; `clarvis.tool.*`, `clarvis.diagnostic.changed`, `clarvis.task.*` and
`clarvis.model.*` do not (`src/bridge/events.ts`).

### E-C5 LIVE VERIFIED — NERVIS visibility

Register real Clarvis instances; display only published status and capabilities.

**Exit:** NERVIS cannot approve gates or reach tools or secrets; disconnect and reconnect are
accurate.

**Where it stands, 12 September 2026.** NERVIS's half of this, `NERVIS.md` M8b, is LIVE VERIFIED.
On 29 August NERVIS read two real Bridges' `/v1/status` with the tokens it had issued them and drew
*waiting for you · sensitive_read* for one and *answering* for the other; a write was refused with
`405`, because the Bridge has no write path by which to reach a gate, a tool or a secret; and
closing one window removed only its registration. On 30 August four registrations renewed their
leases on four independent clocks across a full 45-second window (`STATUS.md`, Stage 8). Reconnect
was not run as a step of its own; what covers it is that renewal and the re-registration tests in
`src/bridge/Bridge.test.ts`.

### E-C6 — code-server spike and supported path

Complete the matrix. **Fix only reproduced incompatibilities**; do not silently patch around
unsupported host behaviour.

**Exit:** supported cells pass all core and security tests, direct and proxied; limitations are
published.

**Where it stands, 12 September 2026 — left without a state.** The matrix exists
(`clarvis/docs/code-server-matrix.md`: 39 `PASS`, 16 `PASS_WITH_LIMITATION`, 0 `FAIL`,
1 `NOT_TESTED`), but it was graded on Clarvis 0.0.1 on 29–30 August — only the multiple-window and
rollback cells were run later, on 0.12.6 — and Clarvis is at 0.15.4. Its one `NOT_TESTED` cell is
Bridge teardown under code-server, and teardown is on the list runbook Stage 9 requires to pass.
The proxied cells were graded through a spike proxy that does none of NERVIS's security work, not
through NERVIS's own `/code/` route, which shipped on 9 September (`NERVIS.md` M14). The matrix
itself still declares no combination supported.

### E-C7 — Release regression

Established Clarvis release gates plus ecosystem degradation, upgrade, rollback and recovery.

**Exit:** with the Bridge and RAVIS disabled, established standalone behaviour is byte-for-byte
what it was; rollback to the prior `.vsix` succeeds with workspace data and SecretStorage
intact.

**Where it stands, 12 September 2026 — left without a state.** Rollback is LIVE VERIFIED: on
6 September Clarvis 0.12.6 was force-downgraded to 0.12.3 in a daily-driver VS Code workspace, and
SecretStorage and the conversation store both came through byte-for-byte (`STATUS.md`, "§15's
code-server item"). Nothing records the rest: an ecosystem degradation sweep, an upgrade with data
and keys checked afterwards, the standalone comparison with RAVIS disabled as well as the Bridge
(the Bridge-off half is `bridgeDisabled.spec.ts`, under E-C3), or recovery. Clarvis's own release
gates are open too: `plan.md`'s M11 exit checklist and its v1 release bar still carry unticked
items.

### E-C8 IMPLEMENTED — Receiving a task from NERVIS *(paired with NERVIS M27)*

Recognise a handoff file NERVIS wrote into the workspace — `clarvis-task.md` at the root of the task's own folder under `nervis-tasks/`, which is opened as that task's workspace — and say so when offering it.

**This is not §6.7's remote-control contract and must not become one.** NERVIS writes a file
into a directory it can already write to; Clarvis reads it with the flow that already reads
`plan.md`. Nothing in this milestone gives NERVIS a way to invoke a tool, resolve a gate, change
a setting or start a run — and the test of that is that **every part of it still works with the
Bridge stopped**, because the interface is a document rather than a connection.

**Why this fits without straining anything.** `pendingBuild` re-reads the plan from disk every
time precisely because a person may have edited it, and §4.9 already requires the handoff prompt
to be shown and editable before it runs. A task authored elsewhere arrives through exactly the
door a task edited by hand already comes through.

**The one thing this adds is provenance.** A brief somebody wrote themselves and a brief that
arrived from another program deserve different amounts of scepticism at the moment of approval,
and the person approving is the only one who can apply it. So the handoff must say where it came
from, in the approval UI, before it is accepted.

**Exit:** a NERVIS-authored task is offered as a build with its origin visible in the prompt;
the prompt is editable before it runs, as any other handoff is; every tool call still passes the
same gates; **with the Bridge disabled the whole flow is unchanged**, because nothing in it
depends on the Bridge; and §9's rule holds — the file is evidence of what somebody asked for,
never an instruction Clarvis follows unreviewed.

**Changed on 10 September 2026: planned, not offered as a build.** The first live handoff was
the one line *"make me a pomodoro timer"*, and "Start it" would have run it as a job there and
then — while what the person wanted was to be asked what it meant first. A handed-over task now
starts the planning interview (M9) with the task pre-typed in the answer box for its first
question: the person changes it or sends it as it is, the interview fleshes it out, and the
plan they sign off is what gets built. §9's rule is kept by that shape rather than by a warning —
a brief from another program is never an answer until somebody in the editor sends it. The task
file is removed once the first answers are saved, so a window closed at the first question
offers it again rather than losing it. The exit's *"offered as a build"* reads *"planned through
the interview"* from here on; the other clauses stand.

**11 September 2026 — the first build from a handoff stalled, and why.** The interview and the
plan worked; the build never reached a finished project. Seven faults, each found in the run's
own log and fixed in Clarvis 0.13.1. A milestone check (`python timer.py 5`) waited on the clock
while Python held its output back, looked hung, and was stopped — commands now run unbuffered,
and milestone briefs say a check must finish in seconds. A stopped run committed nothing and a
freshly initialised repository had no first commit, so every later run treated the project's
files as the user's and nothing was ever committed — a stopped run now commits what it wrote,
and accepting the git offer makes an empty first commit. The "fold this into master?" question
took any typed reply as a merge — it now takes only its own buttons, and anything else is read
as a message. Closing the unsaved plan draft left a copy named after its first heading — the
draft is emptied before it is closed. "Continue" after a stopped milestone ran as a one-off job
— it now picks the plan back up at its unfinished milestone. Tool calls a model wrote out as
text ended the run — they are read as calls. And answers about the project had no file list and
called a never-committed tree "clean" — both corrected. Starting a build no longer switches
Unattended back to Agent; asking before each step in Agent mode is intentional.

**11 September 2026 — Stop releases a question waiting on screen (Clarvis 0.13.2).** Stop pressed
while a run waited on "Do it / Skip this step" did nothing until someone answered: it cut the run's
signal, but the run was parked on the question and its buttons stayed in the panel. Stop over the
end-of-run "fold this into master?" question found nothing running and said "Nothing to stop". Step
approval had moved from a modal into the chat on 15 August, and Stop was never wired to the new
place a run could wait. Stop now cancels whichever question is waiting. A step whose question comes
back after Stop ends the run rather than starting — a "Do it" that raced the stop included — and is
not reported as skipped. A waiting landing question counts as something to stop even with no run in
progress, and the work stays on its branch. Switching to Unattended still answers a waiting step
"Do it". Covered by `stopDecision.test.ts`; not yet seen in a live run.

**11 September 2026 — a missing dependency is a question, in every mode (Clarvis 0.14.0).** The
next build from a handoff stalled on something no plan could have known: the interview chose
Python with tkinter, and this computer's Python was built without it. The command said so in one
line — `No module named '_tkinter'` — and the run read past it. It tried to reinstall the
machine's Python with pyenv (no rule named pyenv, so nothing asked first), ticked steps whose
checks had never run, rewrote the plan to call them untestable, and chat later described the
project as written in Go. Tkinter was the trigger, not the fault: a missing piece of the machine
was treated as something to work around, when it is a question for the person whose machine it
is. Now a failed command whose output says something is missing — a Python module or one of
Python's own parts, a Node package, a Ruby gem, a Go module, a program, a system library or
header, Apple's command line tools — stops the run and asks, in Agent and Unattended alike, the
way the deny-list does: install it into this project (offered only where that is possible), I'll
install it myself, find another way, or stop. Installing it yourself, stopping, or closing the
question ends the run with nothing ticked and nothing offered for landing or review; finding
another way carries on under an instruction not to install anything, change the machine, edit the
plan or tick a step. Changing the machine's languages and tools — pyenv, asdf, nvm, rustup, conda,
pipx, brew upgrade, apt and their like — is a gate category of its own, toolchain, that always
asks and cannot be undone. What was found missing is remembered for three days and given to chat's
answers, along with the project's files two levels deep and the language the plan chose. A reply
that only announced its step is asked, once, to carry it out. Both milestone briefs now say a step
whose check did not pass is never ticked or reworded — left as it is, with the reason said.

**11 September 2026 — a message typed during a run reaches the model (Clarvis 0.14.1).** Typing
while a run is going is meant to redirect it: the message waits for the current step to finish,
then goes to the model with that step's tool results, framed so it outranks the plan in flight.
It never arrived. A turn carrying tool results went to Anthropic as `tool_result` blocks alone and
to OpenAI-compatible providers as `role: 'tool'` messages alone, so the redirect was logged, taken
off the queue and never read, and the run carried on the old way. It now goes as text after the
results in the same turn for Anthropic, and as a user message after the last tool message for
OpenAI-compatible providers — the order each requires — while a step nobody interrupted sends the
results alone, as before. Covered by `toolResultTurn.test.ts`; not yet seen in a live run.

**11 September 2026 — "find another way" leads somewhere (Clarvis 0.15.0).** The first live run of
0.14.1 met the missing tkinter and asked, as designed. The answer was "Find another way"; the model
said the plan could not work without tkinter and stopped — and the run still ended as a finished
milestone: it committed a stray main.py, offered to merge it, and the merge put it on master.
"Continue building" then got back the step and its check copied out of plan.md and nothing else,
which the nudge for a bare announcement let through. Now a run that found something missing and
never got past it — the command that found it never succeeded afterwards — is blocked whatever the
answer was: nothing is ticked, landed or offered for review. Told to find another way when the plan
depends on the missing piece, the model proposes the smallest change to the plan that avoids it and
stops, and Clarvis offers three answers: change the plan like that, I'll install it myself, or leave
it for now. Changing the plan starts a run that rewrites only what depended on the missing piece —
every other line, tick and result stays — and then builds the unfinished milestone. "Continue
building" while that proposal stands asks the same question instead of starting a run. Any step
announcement in a reply with no tool calls now gets the one nudge to carry it out.

**11 September 2026 — Stop stops what a command started (Clarvis 0.15.1).** The first live run of
0.15.0 got as far as changing the plan, then ran `cd src && python3 main.py` — a stub that prints a
line and loops for ever. Stop did nothing visible, and Ctrl+C in the browser could not reach it.
Clarvis killed the shell it had spawned and nothing else: the Python process that shell launched
lived on with the output pipe open, and a command counted as finished only once every copy of its
output had closed, so the run waited on it — and would have waited past the ten-minute limit, whose
kill had the same blind spot. Ending that one Python process by hand let the stop complete at once.
A command now runs as the leader of its own process group, and Stop and the time limit stop the
whole group. A command counts as finished when it exits: whatever it left running in its group is
stopped, the model is told a check has to end on its own, and output still open two seconds later
is closed. Windows has no process groups and keeps the old behaviour.

**12 September 2026 — Clarvis names itself to RAVIS (Clarvis 0.15.2).** RAVIS gives an unnamed
caller sixty requests a minute and a named one six hundred, and every unnamed caller on the machine
shares the sixty. Measured on 11 September, the NERVIS dashboard open in a browser reads RAVIS
directly about twenty-four times a minute, and Clarvis called RAVIS unnamed — so a build had roughly
thirty-six a minute before RAVIS turned it away. None of Clarvis's 224 recorded completions had been
refused, but two model-list reads had. The ecosystem launcher now mints a client credential for
Clarvis the way it does for NERVIS, stores it in RAVIS as `client.clarvis`, and starts code-server
with it in `CLARVIS_RAVIS_CREDENTIAL`. Clarvis presents a key stored in the editor first and that
credential only as the fallback — and only to a RAVIS on a loopback address, for a `ravis/` model,
so it cannot be sent to another host or another provider. Desktop VS Code is not started by the
launcher, so there Clarvis stays unnamed unless a key is stored.

**12 September 2026 — the briefing names project files only (Clarvis 0.15.3).** Opened in code-server
with nothing in the editor, Clarvis kept greeting with "You were last in settings.json". The recent-
files list already refused the editor's own settings, but only at desktop VS Code's `Code/User/`;
code-server keeps them under `code-server/User/`, so every setting Clarvis wrote was recorded as the
user's work and carried into the next window. A saved file now counts as what you were working on
only if it is inside the workspace folder — one rule for every editor's settings folder rather than a
list that is always one editor short — and the stored list is filtered the same way on start, so the
stale entry drops out without anyone clearing it. With no folder open, the named exclusions apply as
before, and `code-server/User/` is now among them.

**12 September 2026 — this folder's repository, never the first one found (Clarvis 0.15.4).** With
`nervis-tasks` open — a folder of task folders with no repository of its own — the greeting named
`pomodoro-timer`'s branch and untracked file as this folder's. The editor's Git extension scans
subfolders, and Clarvis took the first repository it was handed in eight places: the briefing, commit
noticing, the review wizard, an agent run's branch and commits, the branch-flow check and plan commit,
the agent's git tools, and the shared helper behind branch switching and plain git status. Every one of
them now takes the deepest repository whose root contains the open folder, and none when no repository
does — so a folder that is not a repository is treated as one that is not, and a run started there can
no longer branch or commit inside a subfolder's project.

**Why this is IMPLEMENTED rather than verified, and what changed on 5 September.**
Reverifying against §14.8 found the exit's first clause — *"a NERVIS-authored task is offered
as a build with its origin visible in the prompt"* — failing three separate ways, all the same
shape: the handoff was read correctly, phrased correctly, and reached nothing. It was
*announced* rather than offered, arming no state, so the only way to act on a task somebody
had just handed over was to retype it. The file was deleted at the moment of asking, so the
document the exit promises is *"editable before it runs"* was gone by the time anybody read
the invitation. And the offer went through the voice pinning a filename the sentence never
contained, so every rewrite of it was rejected — silently, after the model call was paid for.

All three are closed. The task arms an offer and posts choices; the file survives until the
answer and is re-read from disk on *yes*, so an edit counts; the offer is delivered as
written, which is the only way provenance is safe from a paraphrase.

**The row stays at IMPLEMENTED because of what is still untested, and §14.8 lets the
sub-claims be named rather than averaged** (`RAVIS.md`'s M19 has this shape). What is
AUTOMATED VERIFIED: the startup ordering, including that declining to plan a project is not
an answer about a task somebody just sent; the four-way answer decision, including a task
withdrawn between question and answer; where the offer sits among the others; the offer's own
text; and — in real VS Code, where it had no test at all — reading and clearing the file.
What is not: the three lines inside `ChatService` that arm the offer and post its buttons.
`ChatService` takes twelve constructor dependencies and nothing in the repository builds one
in a test, so covering that link is a harness of its own rather than an assertion, and
claiming the clause without it would be the optimism §14.8 exists to end.

**Corrected 12 September 2026: the offer described in the last three paragraphs was removed in
Clarvis 0.13.0.** Since 10 September a handed-over task is not offered as a build with choices: it
starts the planning interview with the brief pre-typed in the first answer box, and the task file
is cleared once the first answers are saved rather than surviving until an answer
(`planNervisTask` and `interviewMemoryFor` in `src/chat/ChatService.ts`). Commit `99d8566` deleted
`nervisOffer.ts` and its tests, the handoff entry in `OFFER_ORDER`, and `ChatService`'s offer state
and answer handler — so the four-way answer decision, the offer's place among the others and the
three untested lines that armed it no longer exist. What carries the clause now:
`startupOffer.test.ts` still decides when a waiting task is taken up on opening;
`nervisHandoff.test.ts` checks that the message leads with where the task came from;
`Interview.test.ts` checks the brief waits in the answer box and is never taken as the answer; and
`src/test/nervisHandoffFile.spec.ts` reads and clears the file in real VS Code. Not tested:
`planNervisTask` itself, and the wrapper that clears the file after the first save.

**The other two clauses are unevidenced rather than contradicted.** *"Every tool call still
passes the same gates"* is now stronger than it was — approval is forced on for a handed-over
task whatever the mode says, because a brief that arrived from another program has had no
human hand on it — and nothing tests that it is. *"With the Bridge disabled the whole flow is
unchanged"* is structurally true, `nervisHandoff.ts` importing nothing at all, with no test
asserting it as a property on the reading side.

**Corrected 12 September 2026: approval is no longer forced on.** That rule went with the offer in
0.13.0 — `99d8566` removed *"Approval is forced on, whatever the mode says"* and its
`setStepApproval(true, () => true)`. A build from a handed-over task now runs like any build from
an approved plan: step approval follows the mode (`asksFirst` in `src/chat/modes.ts`), so Agent
asks before each step and Unattended does not. The gates that ask in every mode — the deny-list,
the sensitive-file read and a missing dependency — and the workspace boundary are unchanged.

**Paired NERVIS M27 carries the identical clause and is IMPLEMENTED for the same reason**: its
own exit reads *"Clarvis offers it as a build with the prompt visible and editable"*, and
NERVIS's suite cannot reach an editor to test it. The pair needs one live run — a real NERVIS
write, a real editor window, the offer observed in the panel and answered — which is what
would move both to LIVE VERIFIED.

**12 September 2026: the live run has happened, and the row stays IMPLEMENTED.** On 10 September a
real NERVIS handoff reached a real editor, which offered it as a build; on 11 September, after
0.13.0, a handoff went through the interview to an approved plan with three milestones
(`STATUS.md`, "Named task folders, and Clarvis plans a handed-over task" and "The first build from a
handoff, and Clarvis 0.13.1"). Neither record says whether the provenance line was seen in the
panel, and that is the first clause's point, so the runs are not yet evidence for LIVE VERIFIED. A
next handoff that records it would be.

### E-C9 — Codex tasks through RAVIS *(paired with RAVIS M29 and NERVIS M28)*

Choose `ravis/clarvis-codex` as the coding model and a build runs as a Codex task in RAVIS: progress,
approvals and questions in the chat, steering, Stop and the milestone machinery unchanged. The task
survives a closed window and is reattached from either host. Manual switching to Clarvis's own
engine and back continues on the same branch through a checkpoint in the git folder, under RAVIS's
project lock. (The id was `ravis/codex` until Clarvis 0.16.1; renamed by the owner on 13 September 2026.)

**Exit** (against `FakeRavisRelay` from the shared fixtures, then live):
- **Stop:** clears the question at once and never lets a late answer start a step; nothing is saved
  before processes are confirmed gone.
- **Questions:** overlapping requests are asked one at a time.
- **Feedback:** text typed during a switch or while detached reaches the next turn.
- **Reattach:** from desktop VS Code, replays and answers a waiting approval.
- **Settle:** one window claims it.
- **Takeover:** a taken-over Clarvis-engine run writes nothing.
- **Refusals:** an untested version, unproven file rules, a changed account or an exhausted
  allowance refuses to start, with the reason, and never moves to a paid engine.

**Where it stands, 14 September 2026 — AUTOMATED VERIFIED, not LIVE VERIFIED.** Decided by the owner
and signed off as `plan.md` M15 on 13 September, with §5.5 as its contract. C1, C2a, C3 and C2b+ —
approvals, sites asked before and during a task, and the bowtie menu's Codex model, effort and
allowance — ship in Clarvis 0.17.0, installed in desktop VS Code and code-server. Every exit item
above is exercised by Clarvis's own suite against `FakeRavisRelay`, built from the shared fixtures
whose copy `src/test/codexContractFixtures.test.ts` holds to their manifest (1,917 tests and 24 host
specs pass, run locally; the clean-clone gate has not been run for it). RAVIS 0.25 serves the
contract and calibration proved Codex 0.154.0's file rules, but no real task has run yet: the live
walk with the owner is next. The steps are in `plan.md` M15, and their place in the build in `STATUS.md`.

---

## 8.1 Ecosystem stage mapping

The runbook's §6.2 stages are the ecosystem's schedule, and it names the mapping tables in
`RAVIS.md` §20.1, `SIRVIS.md` §21.2 and `NERVIS.md` §21.1. This is Clarvis's, and it exists so
that the one product that already runs as code is not the one product absent from the build
order.

| Runbook stage | Lands in |
|---|---|
| Stage 0 — baseline and invariant lock | E-C0 |
| Stage 1 — shared protocol | The Bridge contract **on paper only** — schema and contract fixtures, no daemon. The runbook is explicit that Clarvis does not run a Bridge at this stage; these fixtures are owned here rather than by a later gate |
| Stage 2 — transparent gateway and Clarvis conformance | The RAVIS regression fixture half of E-C2. RAVIS builds its conformance suite against Clarvis's real expectations, so the fixtures must exist before that suite does |
| Stage 3 — live Clarvis ↔ RAVIS | E-C1, and the role-profile and fallback half of E-C2. **Configuration only — no Clarvis source change** |
| Stage 8 — Clarvis Bridge | E-C3 + E-C4 + E-C5 |
| Stage 9 — code-server compatibility and the Code tab | E-C6 |
| Stage 10 — whole-ecosystem hardening | E-C7 |
| **Unscheduled — after NERVIS Stage 11** | E-C8, paired with NERVIS M27. Blocked on nothing technical; it is deliberately after the milestones that decide how NERVIS proposes anything at all, because a handoff is a proposal and should inherit whatever those settle |
| **Unscheduled — Codex tasks through RAVIS (runbook §2.2)** | E-C9, paired with RAVIS M29 and NERVIS M28. Depended on by nothing. It was to land once `plan.md` was signed off and the §2.2 amendments were in, and both happened on 13 September 2026. Shipped in Clarvis 0.17.0 on 14 September 2026, after calibration proved Codex 0.154.0's file rules: **AUTOMATED VERIFIED** against `FakeRavisRelay` in Clarvis's own suite, run locally (the clean-clone gate not yet run for it); **not LIVE VERIFIED** until the live walk with the owner. The order is in `STATUS.md` |

E-C2 is deliberately split: its fixtures are a Stage 2 dependency of another product, while its
role-profile and fallback behaviour is Stage 3.

**12 September 2026:** the Stage 3 row's *"no Clarvis source change"* held for Stage 3. Clarvis
0.15.2 has since added RAVIS-aware source for the key it sends, which §2's note of the same date
records and leaves to the owner.

---

# 9. Retrieved content is evidence, never intent

The runbook §9 invariant applies to every product, and **Clarvis is where it is actually
tested.** RAVIS forwards, SIRVIS measures, NERVIS observes — Clarvis reads.

Every one of these enters an agent prompt and none of them is trustworthy: file contents,
diffs, dependency manifests, test output, build logs, terminal output, and any web or
documentation text a tool returns. A repository can put a sentence in a README, a comment, a
commit message or a failing test's output that reads as an instruction to the agent, and it
costs an attacker nothing to try.

So: **retrieved content is fenced as data before it enters a prompt, and nothing inside it can
authorize an action.** It cannot approve a gate, widen the workspace boundary, select a tool,
supply a command, or change which model or provider is in use. The existing gates are what make
this enforceable rather than aspirational — the model may *propose* anything, and the gate is
what decides. The rule is that a proposal's persuasiveness is never evidence, and the gate never
reads its justification.

`src/agent/gate/injection.test.ts` already covers the command half of this. The remaining work
is stating the fencing boundary for the read path and testing it the same way.

**As built, 12 September 2026: the agent's read path is still unfenced.** Clarvis has a fence —
`src/chat/fence.ts`, tested in `fence.test.ts` — with one caller, `src/chat/localAnswer.ts`, on
the chat side. What the agent's tools hand back — `readFile`, `search`, `gitDiff`, `runCommand`'s
output and the rest — reaches the model without it; nothing under `src/agent/` uses the fence. The
gates still decide every action, but the sentence above that retrieved content "is fenced as data
before it enters a prompt" holds for that one chat path only.

**Gate:** a repository containing text directed at the agent — in a file, a diff, a test failure
or terminal output — changes no gate outcome, no workspace boundary and no provider selection.

---

# 10. Final checklist

- [ ] `clarvis/plan.md` remains normative and unchanged except for approved references.
- [ ] Every ecosystem addition is labelled *proposed* until accepted.
- [ ] Extension-host and window lifecycle preserved.
- [ ] Workspace containment, command sandbox and approval/refusal gates pass unchanged.
- [ ] The provider abstraction remains the integration layer — no RAVIS-specific provider.
- [ ] Chat and agent roles remain independently configured.
- [ ] SecretStorage boundaries pass direct, RAVIS and code-server tests.
- [ ] RAVIS profile IDs are confirmed against the real picker, not guessed.
- [ ] Bridge is optional, extension-host-scoped, redacted and MEP-conformant.
- [ ] Multiple instances are isolated.
- [ ] NERVIS has no safety bypass.
- [ ] Standalone and direct-provider fallback is explicit and policy-safe.
- [ ] code-server support is matrix-evidenced, not assumed.
- [ ] Any theming is presentation-only and regression-tested.
- [ ] Full existing and ecosystem regression and recovery gates pass.
- [ ] No unresolved STOP item remains.

**Where the checklist stands, 12 September 2026 — no box ticked, because a tick needs a recorded
check and this is a reading of the records.**

- **Recorded evidence supports:** extension-host and window lifecycle (`bridgeDisabled.spec.ts` in
  a real host; windows opened and closed against NERVIS on 29–30 August); workspace containment,
  sandbox and gates (`src/test/containment.spec.ts` in a real host,
  `src/agent/gate/injection.test.ts`); chat and agent roles independently configured (Stage 3,
  live); RAVIS profile IDs chosen through Clarvis's own provider UI rather than typed into settings
  (Stage 3); the Bridge optional, extension-host-scoped and redacted (E-C3, E-C4); multiple
  instances isolated (four windows, 30 August); and no NERVIS safety bypass (no write path; a
  `POST` answers `405`).
- **Nothing supports yet:** SecretStorage tested across direct, RAVIS and code-server — only the
  desktop rollback of 6 September and the 0.0.1 matrix cells exist; an explicit direct-provider
  fallback, which does not exist (E-C2); code-server support evidenced by the matrix, which was
  graded on 0.0.1 and still has a `NOT_TESTED` cell (E-C6); and the full regression and recovery
  gates (E-C7).
- **Not assessed here:** `plan.md` changed only by approved references, additions labelled
  *proposed*, MEP conformance as a whole, theming, open STOP items, and "no RAVIS-specific
  provider", which turns on §2's note and is the owner's call.

---

# 11. Conflicts resolved in this consolidation

| Conflict | Sources | Resolution |
|---|---|---|
| M13 log tailing status | Both addenda call it planned | **Corrected: it is built and gated.** Verified in `src/logtailing/logTailing.ts` |
| Milestone naming | Original used E-C0…E-C7; revised used C-E1…C-E5 | E-C numbering retained (it covers more ground); the revised set folds in |
| Depth | The revised addendum dropped the non-invention rule, the identity contract and the isolation gates | Original retained as the spine; the revised draft's sharper framing of "configuration-only" adopted as §2 |
| Claimed vs verified behaviour | Both drafts cited Clarvis behaviour through dead `fileciteturn…` markers | Every claim re-verified against source; §3 cites real files and settings. Unverifiable claims removed |
