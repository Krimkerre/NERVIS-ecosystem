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
| A custom OpenAI-compatible base URL is a first-class configuration, asked for rather than guessed. | setting `clarvis.chat.baseUrl.custom`; `needsUrl` in `src/model/providers.ts` |
| **Tool support is probed, not asserted from model-family heuristics.** `supportsTools()` sends a real one-tool, one-token request — "the only honest test". Model-family recognition exists for *defaults only* (`plan.md` M8j). | `src/model/OpenAiCompatibleProvider.ts:162` |
| `[DONE]` and `reasoning_content` are already handled at the provider layer. Reasoning leaking into visible or spoken output is treated as a **defect**. | `src/model/OpenAiCompatibleProvider.ts`, `src/model/reasoning.ts` |
| Provider credentials use VS Code `SecretStorage` / `context.secrets` — never settings, workspace state, logs, NERVIS or telemetry. BYO-key; no Clarvis account. | `src/extension.ts`, `src/model/ModelService.ts` |
| Capabilities are probed and features degrade when unavailable. | `src/model/` |
| Multiple VS Code windows are separate Clarvis lifetimes and workspace states. | `plan.md` §7 |
| A durable run ledger records what the agent did, and "why did you do that" is answered from it. | `src/agent/runLedger.ts` |

## 3.1 Corrections to the source drafts

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

**Every Bridge request is authenticated.** The Bridge generates a token when it starts, holds it
for the extension host's lifetime, and requires it on every request including
`/ecosystem/events`. The token is handed to NERVIS at registration and never written to a log,
an event, a trace or a diagnostic packet.

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
clarvis.events@1
clarvis.diagnostics.summary@1
clarvis.logs.reference@1        only where M13 log tailing exists and is approved
clarvis.ravis_provider@1
```

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

## 6.8 Relationship to M13 raw logs

M13 tails VS Code logs into `.clarvis/vscode.log` behind a Clarvis security gate, and is built.
**The ecosystem must not treat that text file as the primary status contract.** Structured
Bridge events and status are the normal integration; M13 stays an explicitly approved raw
diagnostic aid with workspace and privacy consequences.

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
