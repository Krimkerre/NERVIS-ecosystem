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

---

# 3. Source-established invariants — must not regress

These were **verified against Clarvis source**, not inferred from documentation. Each is a
regression requirement, not an ecosystem proposal.

| Invariant | Evidence |
|---|---|
| One VS Code-family extension, bound to one editor window and workspace. Activates with its extension host and dies with it. **Not a system-wide daemon.** | `plan.md` §7; `docs/CURRENT_STATE.md` |
| Privileged behaviour lives in the extension host. The webview is presentation and message input — **not an authority boundary** — talking to the host over a narrow `postMessage` bridge. | `src/panels/`, `src/extension.ts` |
| Clarvis's own file, read, search and edit tools stay inside the opened workspace, resolving and checking paths including escape and symlink cases. `isInside()` probes the real filesystem for case sensitivity rather than inferring it from `process.platform` — a fix for a real macOS containment escape on case-sensitive APFS. | `src/agent/`, `docs/build-log.md` |
| Commands are separately approval-gated and OS-sandboxed. Network is denied by default and opened only for gate categories that cannot work without it. **The command boundary is more nuanced than tool containment and must keep being stated honestly.** | `src/agent/tools/sandbox*.ts`, `src/agent/Gate.ts` |
| Risky, destructive and outward-facing operations stop at approval gates. Anything outside the workspace is **refused**, not made approvable. A sensitive-file read gate (`.env`, credentials, private keys) fires in every mode. | `src/agent/Gate.ts`, `src/agent/sensitivePath.ts` |
| Clarvis acts only when asked. Unsolicited observation never becomes a code change. | `plan.md` §4 |
| `ModelProvider` abstracts completion, streaming, tool support and model listing. Providers include direct cloud, local hosts, and arbitrary OpenAI-compatible endpoints. | `src/model/ModelProvider.ts`, `src/model/OpenAiCompatibleProvider.ts` |
| **Chat and coding/agent roles use independently configured providers and models.** | settings `clarvis.chat.provider` / `clarvis.chat.model` and `clarvis.agent.provider` / `clarvis.agent.model` |
| **A turn's *mode* does not choose the role — a turn that writes does.** `AgentRunner.loop` selects `role: 'chat'` for a read-only turn (capped at 10 steps, `tools: readOnlyTools()`) and `role: 'agent'` only when the turn may mutate the workspace. So "agent mode" answering a question about a file runs on the **chat** model, with tools, and only an edit reaches the agent model. | `src/agent/AgentRunner.ts:195,199,430` |
| Provider and model inherit *independently*: an unset `clarvis.agent.provider` falls back to chat's, and a model name is only carried across when both roles share a provider. | `src/model/roles.ts`, `resolveRole` |
| A provider's base URL is keyed by **provider, not role** — `chat.baseUrl.${spec.id}` — so both roles share one endpoint and differ only by model. | `src/model/ModelService.ts:74`, `:173` |
| Clarvis builds the API path itself: `${baseUrl}/v1/models`, `${baseUrl}/v1/chat/completions`. A configured base URL must **not** end in `/v1`. | `src/model/OpenAiCompatibleProvider.ts:117`, `:164` |
| The `custom` provider declares `needsKey: false` and `needsUrl: true`, and its default model is the placeholder `local-model` — which it will send before a model is chosen. | `src/model/providers.ts:102-110` |
| A custom OpenAI-compatible base URL is a first-class configuration, asked for rather than guessed. | setting `clarvis.chat.baseUrl.custom`; `needsUrl` in `src/model/providers.ts` |
| **Tool support is probed, not asserted from model-family heuristics.** `supportsTools()` sends a real one-tool, one-token request — "the only honest test". Model-family recognition exists for *defaults only* (`plan.md` M8j). | `src/model/OpenAiCompatibleProvider.ts:162` |
| `[DONE]` and `reasoning_content` are already handled at the provider layer. Reasoning leaking into visible or spoken output is treated as a **defect**. | `src/model/OpenAiCompatibleProvider.ts`, `src/model/reasoning.ts` |
| Provider credentials use VS Code `SecretStorage` / `context.secrets` — never settings, workspace state, logs, NERVIS or telemetry. BYO-key; no Clarvis account. | `src/extension.ts`, `src/model/ModelService.ts` |
| Capabilities are probed and features degrade when unavailable. | `src/model/` |
| Multiple VS Code windows are separate Clarvis lifetimes and workspace states. | `plan.md` §7 |
| A durable run ledger records what the agent did, and "why did you do that" is answered from it. | `src/agent/runLedger.ts` |

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

**The agent's tool set is nine and closed**: `readFile`, `listFiles`, `search`,
`applyEdit`, `writeFile`, `runCommand`, `readDiagnostics`, `gitStatus`,
`gitDiff`. A read-only run is handed only the reading tools, so §7.3's restraint
is a property of what the model receives rather than an instruction it is asked
to respect.

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
- No always-on Clarvis daemon, and no lifecycle independent of the editor window.
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
execution and workspace validation; approval gating, checkpoints, Stop behaviour and the agent
loop; conversation and workspace state; and user-facing narration.

**RAVIS owns** route selection and provider credentials. **RAVIS never receives permission to
execute a Clarvis tool** — it may only return model output and tool-call requests for Clarvis
to validate and run.

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
```

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

### E-C0 — Invariant baseline

Pin the source `plan.md` / README version. Run established Clarvis tests and manual gates.

**Exit:** lifecycle, containment, gates, provider switching, chat/agent separation,
SecretStorage and teardown evidence captured. §3 of this document has a passing check or a
named manual check behind every row.

### E-C1 — RAVIS provider *(configuration only)*

Configure RAVIS through the generic OpenAI-compatible adapter. **No source modification.**

**Exit:** Custom provider connects to RAVIS; chat works; agent tools work; separate chat and
agent pools work; Stop cancels. No Clarvis safety code moves into the adapter.

### E-C2 — Role profiles, fallback and RAVIS regression fixture

Independent RAVIS chat and agent settings, explicit direct-provider fallback, and preserved
source fixtures or probes that let RAVIS validate its own Clarvis compatibility.

**Exit:** role isolation, tool capability, policy refusal, stream, stop, error and fallback
tests pass. **No new runtime dependency is introduced.**

### E-C3 — Bridge protocol

Opt-in, extension-host-scoped metadata and status endpoints.

**Exit:** MEP conformance; collision, disablement, start/reload/close, privacy and two-window
tests pass; Bridge absence does not affect Clarvis; no safety-gate bypass; no workspace escape;
no secrets emitted; **an unauthenticated caller is refused on every endpoint including the event
stream, and the token never appears in a log, event or trace.**

### E-C4 — Events and traces

Structured redacted events, correlation propagated through RAVIS.

**Exit:** event schema, ordering, dedup, redaction and bounded-buffer tests pass; a cross-service
trace resolves; normal Clarvis behaviour is unchanged; **tracing failure never blocks an agent
run**; an unauthenticated subscriber receives no events, and a process impersonating a Bridge on
a free port cannot register with NERVIS.

### E-C5 — NERVIS visibility

Register real Clarvis instances; display only published status and capabilities.

**Exit:** NERVIS cannot approve gates or reach tools or secrets; disconnect and reconnect are
accurate.

### E-C6 — code-server spike and supported path

Complete the matrix. **Fix only reproduced incompatibilities**; do not silently patch around
unsupported host behaviour.

**Exit:** supported cells pass all core and security tests, direct and proxied; limitations are
published.

### E-C7 — Release regression

Established Clarvis release gates plus ecosystem degradation, upgrade, rollback and recovery.

**Exit:** with the Bridge and RAVIS disabled, established standalone behaviour is byte-for-byte
what it was; rollback to the prior `.vsix` succeeds with workspace data and SecretStorage
intact.

### E-C8 IMPLEMENTED — Receiving a task from NERVIS *(paired with NERVIS M27)*

Recognise a handoff file NERVIS wrote into the workspace, and say so when offering it.

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

**The other two clauses are unevidenced rather than contradicted.** *"Every tool call still
passes the same gates"* is now stronger than it was — approval is forced on for a handed-over
task whatever the mode says, because a brief that arrived from another program has had no
human hand on it — and nothing tests that it is. *"With the Bridge disabled the whole flow is
unchanged"* is structurally true, `nervisHandoff.ts` importing nothing at all, with no test
asserting it as a property on the reading side.

**Paired NERVIS M27 carries the identical clause and is IMPLEMENTED for the same reason**: its
own exit reads *"Clarvis offers it as a build with the prompt visible and editable"*, and
NERVIS's suite cannot reach an editor to test it. The pair needs one live run — a real NERVIS
write, a real editor window, the offer observed in the panel and answered — which is what
would move both to LIVE VERIFIED.

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

E-C2 is deliberately split: its fixtures are a Stage 2 dependency of another product, while its
role-profile and fallback behaviour is Stage 3.

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

---

# 11. Conflicts resolved in this consolidation

| Conflict | Sources | Resolution |
|---|---|---|
| M13 log tailing status | Both addenda call it planned | **Corrected: it is built and gated.** Verified in `src/logtailing/logTailing.ts` |
| Milestone naming | Original used E-C0…E-C7; revised used C-E1…C-E5 | E-C numbering retained (it covers more ground); the revised set folds in |
| Depth | The revised addendum dropped the non-invention rule, the identity contract and the isolation gates | Original retained as the spine; the revised draft's sharper framing of "configuration-only" adopted as §2 |
| Claimed vs verified behaviour | Both drafts cited Clarvis behaviour through dead `fileciteturn…` markers | Every claim re-verified against source; §3 cites real files and settings. Unverifiable claims removed |
