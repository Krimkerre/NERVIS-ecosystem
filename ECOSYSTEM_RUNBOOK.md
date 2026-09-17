# Ecosystem Runbook

**Status:** Canonical integration, build-order and release specification
**Audience:** Coding agents, maintainers, release operators
**Scope:** Cross-repository contracts, and the order in which four products become one operable ecosystem
**Supersedes:** `ECOSYSTEM_RUNBOOK.md` and `ECOSYSTEM_RUNBOOK.md — Revised Integration Order` (both source drafts)

> **SIRVIS knows. RAVIS chooses. CLARVIS acts. NERVIS connects.**

---

## 1. Authority and the non-invention rule

Each product's own build plan is authoritative for that product's internal behaviour.
This runbook is authoritative for anything crossing a product boundary: the shared
protocol, the build order, the integration gates, and the release sequence. Where the
two conflict, preserve the product's established safety boundary and stop for a contract
decision.

> **No agent may invent another ecosystem component's API, schema, capability, or
> behaviour merely to complete its own milestone. If the required contract does not yet
> exist, implement against the canonical contract where this runbook specifies one, use
> an explicitly labelled test double where that is allowed, or stop at the integration
> gate and report the missing dependency.**

An agent **MUST STOP** when a required path, field, state transition, security property,
capability identifier, protocol version or owner behaviour is absent from the shared
protocol or from the owning product's document. On stopping it records: the missing
contract, the consumer milestone that needs it, the expected owner, the evidence already
checked, and the smallest decision that would unblock it. A locally convenient shape is
not evidence.

Observed runtime behaviour outranks this document. When an integration behaves
differently from the plan: record the observed behaviour, add or update a regression
test, correct the contract, and do **not** fake the expected behaviour to satisfy a
checklist.

---

## 2. Product boundaries

| Product | Owns | Does not own |
|---|---|---|
| **SIRVIS** | Machine/runtime/model inventory, measured benchmark evidence, Runtime Sets, recommendations | Routing, chat, provider credentials, supervision |
| **RAVIS** | Provider adaptation, eligibility and routing, virtual profiles, local-runtime coordination, route explanations, sessions, usage and cost; **brokering Codex agent sessions — hosting the optional Codex runtime with its sign-in, version pinning and allowance, relaying each task's events, approvals, questions, steer and stop to Clarvis, and the project write lock both coding engines use (§2.2)** | Benchmarking hardware, **deciding approvals or answering questions for the owner, Clarvis's own tools, git branches and commits**, displaying the ecosystem |
| **CLARVIS** | Coding-agent behaviour inside one workspace: tools, approval gates, agent loop, conversation and workspace state; **the choice of coding engine; for Codex tasks, the approval and question interface, git branches and commits, the task checkpoint and switching between engines (§2.2)** | Model selection across vendors, benchmark truth, ecosystem visualisation, hosting the Codex runtime |
| **NERVIS** | Registry, dashboard, diagnostics, event hub, traces, general RAVIS-backed chat, control surfaces it is explicitly granted | Any peer's business logic; any peer's safety decisions; starting, steering or answering any coding engine's work (it may ask RAVIS to stop a Codex task once the owner confirms, §2.2) |

If two components appear to disagree about a fact, the owner in this table is
authoritative for that kind of fact.

### 2.1 Evidence about API-hosted models

**Deferred by decision, not omitted.** Nothing here is built and nothing should
be built before the milestones named at the end. It is written down now because
the decision is cheap to get wrong later, in the direction of measuring the
wrong thing.

**SIRVIS may produce evidence about hosted models, and it is capability evidence
only.** Tool-call reliability against real schemas and multi-step loops,
structured-output adherence, instruction following, long-context behaviour — the
questions whose answers change *which model a pool may use*. Throughput and
time-to-first-token may be recorded from the same trial and are **never
admissible as routing evidence**: measured across the public internet they
describe the network and the hour of day as much as the model, and a provider's
capacity is not a property of this machine. They are recorded at `ESTIMATED`
and a router must not rank on them.

The reason to measure at all is the one already proven on this hardware rather
than assumed. LM Studio's `tool_use` flag disagrees with executed tool-call
trials **in both directions**: three installed builds advertise nothing and pass
3/3, and `granite-4.0-h-tiny` advertises tool support in both packagings while
scoring 8/8 as GGUF against 1/8 as MLX. A hosted provider's documentation is an
advertisement of exactly the same kind. "Supports tools" and "survives five
sequential tool calls against a twelve-field schema" are different claims, and
only the second one decides whether an agent pool should route there.

**Identity is different, and the difference is the date.** SIRVIS.md §12.2 keys
evidence on a local build — machine, format, quantization, runtime — and a
hosted model has none of those. Hosted evidence is keyed on **provider + model
alias + observation date**, with the date part of the key rather than metadata
beside it: providers move weights behind a stable alias without notice, so a
hosted result is a *dated observation* and not a reproducible measurement.

Stale hosted evidence is **marked, never silently voided**. Expiring it into
`UNKNOWN` would let §9.1's fail-closed rule turn a working route into "no
candidates" overnight, which is a worse failure than acting on a claim from
three months ago. Past its horizon the claim stands, the route explanation says
how old it is, and re-measurement is scheduled rather than triggered by an
outage.

**SIRVIS never holds provider credentials, and this does not change that.** §2's
table gives credentials to RAVIS. A hosted trial is therefore driven *through*
RAVIS, addressing a model directly rather than through a pool — a directly named
model never falls back, so the trial measures the model it named rather than
whatever the chain reached. SIRVIS acquiring its own provider clients would
duplicate RAVIS's adapters, which §3 forbids for precisely this reason.

**It spends money, so it is never implicit.** Opt-in per provider, a stated
ceiling before the run, and the estimated cost shown alongside the expanded
trial count. No hosted trial runs as a side effect of anything else.

**Lands after SIRVIS M16 and RAVIS M4/M7** — it needs the evidence API RAVIS
reads and at least one hosted provider adapter to drive through. **Exit:** RAVIS
refuses to route an agent pool to a hosted model whose tool-call trial failed,
names the trial and its date in the route explanation, and leaves that same
model eligible for a chat pool.

### 2.2 Codex agent sessions, brokered by RAVIS

**Decided by the owner, 13 September 2026. Specified, not built.** This section, the product
documents' amendments (`RAVIS.md` §15.1.2 and M29, `CLARVIS.md` §5.5 and E-C9, `NERVIS.md` §8 and
M28), the contract fixtures and Clarvis's signed-off `plan.md` M15 landed that day as the build's
first increment. No route, process, control or lock described here exists yet. It is §3's change
order, steps 1 and 2: the proposal — owner RAVIS; consumers Clarvis, NERVIS and the launcher;
additive, since every product works without it; its security impact is invariants 2, 6 and 8
below — accepted by the owner, with its fixtures. `STATUS.md` carries the build order and the known
risks.

Clarvis may run a coding task on OpenAI's Codex (`codex app-server`, the Homebrew stable build) on
the owner's ChatGPT plan. **RAVIS runs the Codex process and keeps the task.** Clarvis windows start
it, answer it, steer it, stop it, and reattach to it after closing. It is optional; every product
works without it.

**Who owns what.**
- **RAVIS** owns one long-lived Codex process: its home (outside RAVIS's configuration folder),
  sign-in, version pin and allowance reading. It owns the agent-session records and the relay (SSE
  events and JSON actions), the per-task command clean-up, the unanswered-request policy, and the
  project write lock for both engines.
- **Clarvis** owns the engine choice, the approval and question interface, git (branches, commits,
  reconciliation), the task checkpoint, switching, and its own engine.
- **NERVIS** shows Codex's state, allowance and running tasks, and starts or cancels a sign-in,
  confirms the account or accepts a version through RAVIS. **It never starts Codex work (a task or
  the file-rules re-test), and never steers or answers a task.** After the owner confirms, it may
  stop one through RAVIS's owner Stop route, as the menu bar may through the launcher.

**Identifiers.**
- Catalogue id `ravis/clarvis-codex` (renamed from `ravis/codex` on 13 September 2026, owner decision, to
  match the Clarvis pools; no alias), listed only with `X-Clarvis-Engines: codex`, never a pool member or
  fallback; `/v1` refuses it with 400 `agent_backend_not_a_chat_model`.
- `GET /api/v1/codex` (any caller).
- Admin, for UX and audit: `/api/v1/codex/sign-in`, `/sign-out`, `/account/confirm`,
  `/version-check`, `/accept-version`. `/api/v1/codex/reprove` accepts only the owner's command-line
  credential (`admin.owner_cli`), which NERVIS never holds.
- Clarvis client plus session token: `/api/v1/agent-sessions…` (`RAVIS.md` §15.1.2), **except**
  `POST /api/v1/agent-sessions/{sid}/owner-stop`, which takes only the owner's command-line
  credential or NERVIS's admin credential, no token, and a confirmation of the task's folder name
  and current turn.
- Clarvis client plus lease: `/api/v1/project-locks…`.
- Capabilities `ravis.codex_runtime@1` and `ravis.agent_sessions@1`.
- Shared fixtures: `ravis/tests/fixtures/relay-contract/*.json` and
  `ravis/tests/fixtures/lock-rule-cases.json`, owned by RAVIS and copied into Clarvis
  (`src/test/fixtures/`) with a hash check against `codex-contract.sha256`.
- Transport: JSON POST for actions and server-sent events for each task's stream (§4), never
  WebSockets.

**Invariants.**
1. **One writer per project.** A central RAVIS lock, plus a lock file in the checkout. It is
   released only after the engine's processes are confirmed gone and its work is settled. A switch
   holds it until the destination starts. A Codex session starts a turn only while it holds the
   lock. A holder that lost the lock never commits, saves the checkpoint or releases.
2. **Approvals and questions are answered only by a Clarvis window holding the task's session
   token** and an allowed client credential. NERVIS and administrative credentials are refused on
   every session route except the stop-only owner route. RAVIS never approves a task's request, and
   its unanswered-request policy may only decline and pause. **The one exception is the file-rules
   re-test,** which the owner starts from the menu bar: RAVIS's harness allows only that test's four
   listed commands, in its own throwaway folders, never in a task.
3. **Stop resolves open requests before interrupting;** an answer after Stop never starts a step.
4. **Feedback typed by the owner reaches the engine,** or the task checkpoint when no engine can
   take it.
5. **The allowance is not money.** No automatic move to a paid engine.
6. **Any Codex binary not recorded as tested or accepted pauses new tasks.** Strict file rules must
   be proven for the running version before any task starts.
7. **A change of ChatGPT account pauses new tasks** until confirmed.
8. **RAVIS's records, logs and events stay metadata-only,** except: content held in memory while
   relayed (bounded, never persisted); Codex's own history in RAVIS's Codex folder (owner
   decision); and the task's workspace path.

**Out of scope.** Automatic switching when the quota runs out; Codex for chat, planning or the
interview; remote hosts; other applications using agent sessions; any dashboard or menu control of
tasks other than Stop; Codex on the ecosystem's own repositories or on the coding folder itself.

**Lands with** RAVIS M29, Clarvis E-C9 and NERVIS M28, after a calibration step against the real
runtime.

**Exit.** On this Mac, a Codex task started in code-server:
- shows progress, an approval and a steer;
- keeps running when the browser tab is closed, and is reattached from desktop VS Code, where a
  waiting approval is answered;
- stops with its processes confirmed gone before anything is saved;
- moves to `ravis/clarvis-agent` and back on the same branch, with typed feedback, and no two
  engines ever write at once.

The menu bar and the dashboard show the task and the allowance, and each stops a task after a
confirmation; the attached editor then says where the stop came from. NERVIS's credentials are
refused on every other session route.

---

## 3. Repository and change strategy

**Two repositories.** Clarvis is one: different language, different runtime, shipped as a
`.vsix` on its own cadence, and `clarvis/plan.md` is normative for it in a way no document here
is. SIRVIS, RAVIS and NERVIS share the second, alongside the protocol package and these
documents. The template used to live here too; its mock-only snapshot now sits outside this
repository, at `~/Documents/coding/nervis-template/` (`README.md`).

The property that matters is **independent buildability, not repository count**, and the second
repository preserves it explicitly: separate packages, separate entry points, separate
databases, and a standalone smoke test per product that requires no other service (§5). What it
removes is the coordination tax — a seven-step release dance for a protocol change, performed by
one developer against three consumers, is ceremony that gets skipped, and a skipped step is how
contracts drift.

That is not a hypothesis. The template kept a second copy of these documents and it drifted
within days; a consolidation audit then found twenty-nine contradictions among five documents
already sitting in one directory. Splitting the artifacts that must agree makes agreeing harder,
and nothing about a repository boundary enforces a module boundary.

So the constraints below are **unchanged and are now the load-bearing ones**, because they are
what the repository split used to be relied on for:

> It must not become a shared business-logic framework. Each product pins a released protocol
> version, keeps its own domain models, and owns its adapters. **Shared database tables,
> provider clients, routing engines and benchmark logic are prohibited** — enforced by an import
> check in CI rather than by the filesystem, which makes it a rule that is actually verified
> instead of merely implied.

Splitting later is cheap (`git subtree`, `git filter-repo`) and stays cheap for as long as those
constraints hold — which is the real test of whether they are holding.

Change order **within** the shared repository — one commit may span producer and consumer, so
the sequencing is about behaviour, not merges:

1. Write a protocol change proposal: owner, consumers, compatibility classification,
   security impact, examples, conformance tests.
2. Accept and release the protocol schema and its fixtures.
3. Update the owning producer behind a feature flag if the behaviour is additive but
   unfinished.
4. Publish a producer release and its capability declaration.
5. Update consumers to negotiate and use the capability.
6. Run pairwise gates, then whole-ecosystem gates.
7. Remove old behaviour only after the published deprecation window and rollback proof.

Never couple products by importing another's private types, in either layout. Generated
transport types from the protocol package are allowed. Shared database tables, provider clients,
routing engines and benchmark logic are not — sharing a repository makes these easier to reach
for, which is exactly why the import check is a gate rather than a convention.

Clarvis, being separate, follows the full seven steps above: its protocol version is pinned and
released, never coordinated by copying source.

---

## 4. Minimal Ecosystem Protocol (MEP)

JSON over loopback HTTP for request/response; **Server-Sent Events (SSE)** for the
canonical v1 event stream. A Unix-domain HTTP transport may be added later without
changing payloads. Default exposure is loopback only; no endpoint is remotely reachable
by default. WebSocket/JSONL adapters are extensions, never substitutes for the v1 SSE
contract.

**MEP is a contract between the services this ecosystem builds.** LM Studio, code-server and
Ollama are somebody else's programs and owe it nothing. An observer may *translate* what such a
service publishes in its own dialect into this vocabulary — see `NERVIS.md` §5.2.1 — under one
cross-product rule: **a translated observation is never presented as a published one.** It
carries a marker saying it was derived, it contains nothing the service did not actually answer,
and it never counts toward MEP conformance. A third-party service that is described accurately
is still a third-party service.

### 4.1 Required metadata endpoints

Every service endpoint exposes:

- **`GET /ecosystem/health`** → `{status, live, ready, checked_at, checks[]}`.
  `status` is `healthy|degraded|unhealthy`. Return 200 when live, 503 when not live.
  `ready` is independently truthful — a successful TCP connect is not readiness.
- **`GET /ecosystem/identity`** → `service_id`, `service_type`, `instance_id`,
  `machine_id`, `api_version`, `protocol_version`, `build_version`, `started_at`.
- **`GET /ecosystem/capabilities`** → `{revision, capabilities[]}`, each with a stable
  `id`, semantic `version`, state (`available|degraded|unavailable|disabled`),
  `constraints`, and optional `reason`.

  Written prose and UI refer to a capability as `<id>@<major>` — for example
  `ravis.openai_compatible.chat_completions@1`. That notation is shorthand for the pair
  `{id, version}` and is **never a wire value**: the `id` field carries the identifier
  alone, the `version` field carries the full semantic version, and `@<major>` names only
  the compatibility boundary a consumer negotiates against.
- **`GET /ecosystem/version`** → `{build_version, api_version, protocol_version,
  schema_versions, compatible_protocol:{min,max}}`. Must answer even when `ready` is false.
- **`GET /ecosystem/events`** → SSE stream. Accepts `Last-Event-ID`. Each frame uses
  `id: <event_id>`, `event: <event_type>`, and one JSON `data:` envelope. Comment
  heartbeat at least every 15 seconds. Reconnect advertises `retry: 3000`. A cursor
  outside retention returns 409 `EVENT_CURSOR_EXPIRED`. Per-subscriber buffers are
  bounded; on overflow, emit `ecosystem.stream.gap` where possible, then close.

Identity rules: `service_id` is a stable configured installation identity. `instance_id`
is unique per process or extension-host lifetime. `machine_id` is stable per local
installation, locally generated, opaque, non-hardware-derived and resettable — never a
serial number, MAC address, username or reversible fingerprint. Clarvis stores these in
extension global state and additionally reports a privacy-preserving `workspace_id`; it
must not publish a raw workspace path unless the user explicitly enables it.

All successful metadata responses are `application/json`, include `X-Request-ID`, and
return HTTP 200. Status mapping: validation 400, unauthenticated 401, unauthorized 403,
missing 404, conflict or cursor expiry 409, unsupported media 415, rate limit 429,
internal or unavailable 500/503 — all using the error envelope. Remote exposure requires
TLS plus bearer or mutual-TLS authentication; SSE uses the same authorization and origin
policy as HTTP and never accepts credentials in a query string.

### 4.2 Version rules

All public APIs and schemas use semantic versions. Major means incompatible, minor means
backward-compatible addition, patch means compatible correction. Consumers ignore unknown
optional fields, reject unsupported majors with a structured error, and never infer
behaviour from a build version. Capability versions are negotiated independently of API
versions.

### 4.3 Request context

Accept and propagate:

- `X-Request-ID` — unique per inbound request; create if absent.
- `traceparent` — W3C trace context; create a trace if absent.
- `X-Session-ID` — stable across related user interactions, where applicable.
- `X-Ecosystem-Actor` — authenticated local actor or service identity. Never trusted from
  an unauthenticated remote client.

Logs, events, route decisions, benchmark jobs and Clarvis runs carry `request_id`,
`trace_id`, `span_id` and `session_id` where applicable. **IDs are correlation data, not
authorization.** A trace ID never approves a gate, authorizes a tool, or elevates a caller.

### 4.4 Event envelope

```json
{
  "event_id": "01J...",
  "event_type": "ravis.route.selected",
  "event_version": "1.0.0",
  "occurred_at": "2026-08-22T12:00:00Z",
  "source": {"service_type":"ravis","service_id":"...","instance_id":"..."},
  "subject": {"type":"session","id":"..."},
  "trace_id": "...",
  "span_id": "...",
  "request_id": "...",
  "session_id": "...",
  "severity": "info",
  "data": {},
  "privacy": {"classification":"operational","redactions":[]}
}
```

Events are immutable facts. Event type plus version determines the `data` schema.
Producers redact secrets and prompt/file content by default. Consumers tolerate duplicates
by `event_id`. Ordering is guaranteed only within a declared source stream.

A collector may store identical events from one source once with a count, and may hold back
events from a source that sends faster than it keeps them, provided the newest event of each type
from that source is kept and the producer still gets its ordinary answer. Nothing about the
envelope changes for a producer. NERVIS's flood guard does exactly this, since 0.31.0, after one
producer's loop pushed every other service's history out of its store (`NERVIS.md` §11.1).

### 4.5 Error envelope

```json
{
  "error": {
    "code": "CAPABILITY_UNAVAILABLE",
    "message": "Human-readable summary",
    "retryable": false,
    "details": {},
    "request_id": "...",
    "trace_id": "..."
  }
}
```

Codes are stable and machine-readable. Messages contain no secrets. Validation errors
identify the rejected fields. Authentication and authorization failures do not disclose
whether a protected resource exists.

Note the one deliberate exception: **RAVIS's OpenAI-compatible endpoints return
OpenAI-compatible error shapes**, not this envelope, because clients parse them. The
envelope applies to RAVIS's own management API. Correlation IDs appear in headers either way.

### 4.6 Protocol conformance gate

Exit only when fixtures prove: valid and invalid payload handling; unknown optional-field
tolerance; unsupported-major rejection; ID propagation; event deduplication; secret
redaction; health/readiness truthfulness; consistent error envelopes. Freeze golden
fixtures for every released major and minor.

---

## 5. Prerequisites

Before integration work begins:

- Each repository has an owner, a clean build, unit tests, an integration-test command, a
  version source, release notes and rollback instructions.
- The current SIRVIS, RAVIS, Clarvis and NERVIS documents are pinned by commit or tag and
  referenced from the app docs in this set.
- Clarvis's source-established constraints are recorded as invariants with regression
  tests or named manual checks — see `CLARVIS.md` §3.
- Test machines cover the supported OS/architecture/runtime combinations; clock
  synchronisation is on, so traces can be compared.
- Local secrets use OS keychain or equivalent secure storage. Fixtures contain no
  production credentials.
- Ports, sockets, data directories, log retention and collision behaviour are documented.
  Default loopback ports, assigned here because they cross product boundaries:

  | Service | Default | Notes |
  |---|---|---|
  | NERVIS | `127.0.0.1:8711` | serves the dashboard and `/code/` when the Code tab is embedded |
  | SIRVIS | `127.0.0.1:8721` | |
  | RAVIS | `127.0.0.1:8731` | one port carries `/v1`, `/api/v1` and `/ecosystem` together (RAVIS §3) |
  | Clarvis Bridge | `127.0.0.1:7071` first instance, then the next free port | **per extension host, not per machine** — two workspaces run two Bridges, so the port is dynamic and discovered through registration, never assumed |
  | code-server | pinned by its own deployment | proxied, never assumed |

  A service whose default port is occupied fails to start with the conflict named; it does
  not silently pick another. Consumers read addresses from configuration or the registry —
  **a hardcoded peer port is an invented contract.**

**Prerequisite gate:** every repository builds independently, protocol conformance
fixtures pass in isolation, and a standalone smoke test of each product requires no other
service to be running.

---

## 6. Build order

### 6.1 Why this order, and where the source drafts disagreed

The two source runbooks proposed different orders, and the revised draft contradicted
itself: its §7 said to build the RAVIS transparent gateway before anything smart, while
its §19 release sequence put a SIRVIS alpha first. This document resolves that as follows.

**The riskiest unknown in the whole ecosystem is whether a proxy can sit between Clarvis
and a model provider without breaking streamed tool calls.** Everything else is
comparatively well understood. That risk is therefore retired first.

**SIRVIS is not on the critical path to a working ecosystem.** RAVIS is specified to
operate without SIRVIS, using provider metadata, its own observations and explicit
estimates. Building the evidence plane first produces evidence with no consumer. SIRVIS
has zero inbound dependencies, so it is the natural **parallel track** — it can start the
moment the protocol exists and must be finished before Stage 5, not before Stage 2.

**Clarvis needs no code change to reach Stage 3.** Its existing Custom
(OpenAI-compatible) provider and its separate chat/agent model settings are enough. This
was verified against source, not assumed — see `CLARVIS.md` §3.

### 6.2 Stages

Stages 4 and 2–3 may run concurrently. Everything else is sequential.

**Every stage lands something visible.** `nervis/index.html` already renders every screen
this ecosystem will have, against mock data shaped like the real responses, and each of its
`API` methods cites the endpoint it will call. So a stage's UI increment is not "build a
screen" — it is **replace one mock body with a `fetch` and watch the screen light up with real
data** (`nervis/docs/WIRING.md`). That costs minutes rather than a milestone, which is what
makes it reasonable to ask for at every stage rather than deferring the interface to the end.

This matters beyond morale. A screen driven by real data is a test no unit test replaces: it
is where a field that is missing, mistyped or silently empty becomes obvious. Each stage below
names its increment.

Each product document maps its own milestones onto these stages — `RAVIS.md` §20.1,
`SIRVIS.md` §21.2, `NERVIS.md` §21.1, `CLARVIS.md` §8.1. **Every milestone appears in exactly
one row of its product's table, including an explicit "unscheduled" row**, so that a milestone
nobody scheduled is a visible decision rather than an omission. **Where a product's milestone numbering and these
stages disagree about order, the stages win**; a milestone number is an identifier, not a
schedule.

#### Stage 0 — Baseline and invariant lock

Run existing tests in each repository and record known failures. Write the app documents
without changing product scope. Separate every proposed ecosystem addition from
source-established behaviour. Inventory ports, persistence, secrets, APIs and process
ownership.

**Exit:** reproducible baseline reports exist; Clarvis invariants have regression tests or
named manual checks; owners sign off each app document.

#### Stage 1 — Shared protocol

Release MEP schemas, examples, validators and language-neutral conformance fixtures.
Implement only the metadata endpoints, envelopes and correlation middleware in SIRVIS,
RAVIS and NERVIS. For Clarvis, design the equivalent Bridge contract on paper — do **not**
create an always-on daemon.

Keep this artifact small. It defines `ServiceIdentity`, `Health`, `Capabilities`, semantic
versions, `EventEnvelope`, trace identifiers and `ErrorEnvelope`, plus the identifier
vocabulary: `trace_id`, `request_id`, `session_id`, `job_id`, `run_id`, `workspace_id`.
Nothing else.

**Visible increment:** point `SERVICES` at each service's real `/ecosystem/health`. The
template's service tiles go live, and with them every absence path already built around them —
cycling one service down should darken exactly the surfaces that depend on it and nothing else.

**Exit:** SIRVIS, RAVIS and NERVIS pass live MEP conformance at one pinned protocol
version. Clarvis passes schema/contract fixtures only; live Bridge conformance is gated at
Stage 8. Mismatched-major tests fail cleanly.

#### Stage 2 — RAVIS transparent gateway and Clarvis conformance harness

Prove the boring path before anything intelligent:

```text
OpenAI-compatible client → RAVIS → OpenAI-compatible upstream
```

Required: `GET /v1/models` (cache-backed and cheap), `POST /v1/chat/completions`,
streaming, tools, cancellation. No native-provider translation. No routing intelligence.

Then build the Clarvis conformance harness (`ravis conformance clarvis`) covering fast
cached `/v1/models`, stream termination and `[DONE]`, fragmented tool-call arguments,
tool-call indexes and IDs, tool result IDs, `reasoning_content` preservation and client
cancellation.

**Tool-support probing, separate chat/agent pools and fallback are Stage 3 additions to the
same suite**, not Stage 2 scenarios — each needs the routing this stage forbids, and fallback
cannot be static configuration at all. Do not weaken the suite to fit the stage, and do not
pull routing forward to satisfy it; the transparent path is only useful as a control while it
stays unintelligent. `RAVIS.md` §8.8 carries the split.

**Visible increment:** `API.ravis.models()` reads the real `GET /v1/models`. The first screen
showing data nobody typed.

**Exit:** the Stage 2 half of the conformance suite passes against the transparent route. The goal is stated
precisely: *Clarvis cannot tell that an intermediary was inserted.*

#### Stage 3 — Live Clarvis ↔ RAVIS integration

Point unmodified Clarvis at RAVIS through its existing Custom (OpenAI-compatible)
provider. Configuration only.

**Visible increment:** the Routes and Pools screens read RAVIS's read-only management API, so a
real Clarvis request appears as a real route decision — pool, selected model, requirements, and
every excluded candidate with its reason. This is where the UI stops being a nicety: watching
*why* a route was chosen is how this stage's integration gets debugged.

**Exit:** chat streams correctly; agent tool calls survive fragmentation; Stop cancels
upstream work; chat and agent resolve to different models independently; the agent pool
never routes to a model without tools; fallback does not corrupt the stream. **No Clarvis
source modification is permitted to make this stage pass.**

#### Stage 4 — SIRVIS evidence plane *(parallel track; may start after Stage 1)*

Implement machine detection, runtime integration, model inventory, download/load/unload,
benchmark jobs and results, repeated measurements, provenance, Runtime Sets and
recommendations. Preserve standalone UI and CLI behaviour. Add NERVIS management
operations only where SIRVIS genuinely owns them.

Do not invent a large benchmark library up front. Where working benchmark tooling already
exists elsewhere in the ecosystem — Clarvis has measured local models against its two
roles — **wrap it before replacing it**, and convert its output into the canonical
evidence schema.

That tooling is `clarvis-firstrun/tools/`, named here so nobody rebuilds it from scratch
having failed to find it: `suite2.py` (timings at the character caps the product actually
reads to, plus tool-call trials across eight phrasings), `score.py` (accuracy by *executing*
generated code, and Clarvis's own grounding guard), `tokrate.py` (counted tokens from the
server's usage figures, generation rate separated from effective rate), `screen.py`
(pre-download screening by running a chat template rather than grepping it) and
`agentrole.js` (the working role, driven through Clarvis's real prompt and tool schemas).
The write-up of a real run, including the machine and runtime identity SIRVIS needs, is
`clarvis/docs/benchmarks.md`. What that tooling does **not** have is the instrument around
it — memory telemetry, thermal capture, validity classification, resource leases, a queue —
which is the part SIRVIS actually builds.

**Visible increment:** the Models, Benchmarks and Results screens read SIRVIS's real endpoints.
Provenance is already rendered there — it needs real records behind it, not new screens.

**Exit:** SIRVIS contract tests pass; `ESTIMATED` and `UNKNOWN` never silently become
`MEASURED`; an offline fixture produces deterministic results.

#### Stage 5 — RAVIS intelligence

Add, in this order: native-provider translation (Anthropic, Gemini) as a second execution
path that leaves the transparent path untouched; SIRVIS evidence ingestion keyed by
build/runtime/role rather than model name; local lifecycle decisions (use loaded, load
stronger, use cloud, evict idle, retain for expected session).

RAVIS makes lifecycle *policy* decisions. SIRVIS or the runtime manager performs the
*operations*. Ownership is explicit so RAVIS and NERVIS never fight over a runtime.

**Visible increment:** the Providers screen shows real per-provider health, breaker state and
protocol mode, and route explanations begin citing evidence instead of admitting they have none.

**Exit:** the transparent route still passes Clarvis conformance; translated adapters work
independently; no one-number-per-model shortcut exists; a SIRVIS benchmark result changes
a RAVIS preference; memory pressure produces a safe route change.

#### Stage 6 — NERVIS core

Implement the registry, capability negotiation, dashboard, general RAVIS-backed chat,
SIRVIS views and authorized control, diagnostics and narrowly scoped service supervision.
NERVIS calls only published contracts — never a peer's database, never reverse-engineered
private state.

**Wrap the prototype before replacing it**, exactly as Stage 4 says of the benchmark tooling.
`nervis/index.html` is not a sketch: it is the screen inventory, the degradation model
(`usable()` / `cell()`, absence rendered as a state rather than an error), the `API` method
signatures, and a data layer already exercised against a genuine LM Studio response — it is
what caught a capability filter that matched nothing. Rebuilding those from scratch discards
work that has already been validated, and rediscovers the pitfalls
`nervis/docs/PITFALLS.md` records.

**What must not be carried over is its render layer.** `NERVIS.md` §25 states which parts
transfer verbatim and which must be rebuilt; do not port `innerHTML` interpolation into a
surface that will hold provider names, model IDs and upstream error text.

**Visible increment:** the prototype stops being one — NERVIS serves the real UI, built from it
per `NERVIS.md` §25.

**Exit:** every tile and control is capability-driven; unavailable operations are disabled
with a stated reason; a NERVIS restart loses no owned product state; a product restart is
reflected without manual refresh.

#### Stage 7 — Events and tracing rollout

Roll out shared events and one `trace_id` per logical operation across RAVIS, SIRVIS and
NERVIS first. Clarvis joins at Stage 8. NERVIS correlates.

**Visible increment:** the Traces screen renders real spans, including the missing ones — a span
nobody received is reported missing rather than interpolated from its neighbours.

**Exit:** a real cross-service request produces linked spans; collector outage leaves every
product healthy; redaction and retention tests pass.

#### Stage 8 — Clarvis Bridge

Only now modify Clarvis. Add an optional, extension-host-scoped Bridge exposing health,
capabilities and structured redacted status and events. It starts and stops with the
extension host. Prefer interpreted states (`idle`, `agent_running`, `waiting_for_approval`,
`failed`) over replicating raw VS Code events.

**Exit:** two simultaneous Clarvis windows have distinct instance and workspace IDs and
isolated state; closing either removes only its registration; disabling the Bridge restores
exact standalone behaviour; no safety gate can be bypassed and no secret is emitted.

#### Stage 9 — code-server compatibility spike, then the Code tab

Test, do not assume, the current Clarvis `.vsix` under pinned code-server versions.
Classify each matrix cell `PASS`, `PASS_WITH_LIMITATION`, `FAIL` or `NOT_TESTED`, each
linked to evidence. A compatibility adapter may be proposed only after a failure is
reproduced.

Three outcomes are all acceptable products: embedded Code tab; embedded with limitations;
or NERVIS links out to a dedicated browser tab. **The Code tab must never block the core
ecosystem release, and Clarvis must never be rewritten because an iframe header is
inconvenient.**

**Do not fork code-server by default.** It is MIT licensed (as is `openvscode-server`,
which carries a smaller patch set against upstream), so forking is permitted — but a fork
buys a permanent rebase against upstream VS Code releases, and the extension host is
already real Node, so `child_process`, native modules, tasks, terminals, diagnostics and
Git are reachable through the ordinary extension API. Branding, base path and
authentication are configuration, not patches. Extensions install from a local `.vsix`
(`code-server --install-extension clarvis.vsix`), so the Open VSX / Marketplace split does
not apply.

A fork is justified only after a failure is reproduced in the matrix, and realistically
only for a **host** limitation the extension API cannot reach. Two candidates:

- **SecretStorage backing.** code-server does not provide macOS Keychain semantics for
  `context.secrets`. Measure what it does provide before proposing a patch — and note the
  cheaper mitigation: once RAVIS owns provider credentials, Clarvis holds far fewer
  secrets. Voice-provider credentials are the remaining case.
- **Audio.** Clarvis plays speech through a Node-side subprocess. In a browser the speaker
  and microphone belong to the browser, not the extension host, so a fork does not fix
  this — only routing audio through the webview does. Voice is a separately advertised
  capability and never a blocker for the Code tab.

**Exit:** at least one supported code-server/Clarvis/OS combination passes install,
activation, chat, agent, workspace boundary, gate, streaming, cancellation, persistence and
teardown, both direct and proxied. Unsupported combinations are blocked in the UI.

#### Stage 10 — Whole-ecosystem hardening

Acceptance, degradation, security, load, long-running, upgrade, downgrade, backup, rollback
and recovery suites. Freeze a release candidate only after evidence is attached.

**After Stage 10, the product documents schedule a Stage 11 — "an assistant that grows".**
This runbook never defined it; `NERVIS.md` §21.1 does, as NERVIS M21 → M25 in that order, with
`RAVIS.md` §20.1 placing M28 (`ravis/free-api`) beside it and `CLARVIS.md` §8.1 leaving E-C8, the
coding handoff, unscheduled until after it. `STATUS.md` records it closed on 1 September 2026,
with NERVIS M25. Its exit criteria are `NERVIS.md`'s, not this section's.

**Where the stages stand, 12 September 2026.** Each exit above, read as written rather than as
it was reported at the time, with the `STATUS.md` passage that records it:

- **Stage 0 — no record of its sign-off.** `STATUS.md` counts it complete ("Stages 0, 1, 2, 3
  and 4 are complete"), but nothing records the owners signing off each app document, which is
  one of its three exit conditions.
- **Stages 1, 2 and 4 — exits met.** Stage 1 when NERVIS M0 gave the third service its MEP
  surface; Stage 2 on 24 August with RAVIS M10 ("M10 is complete. Stage 2 is complete with
  it."); Stage 4 with SIRVIS M7's evidence rules and M15's recommendations.
- **Stage 3 — recorded met, with one clause observed only later.** The run against its exit
  criteria found them holding and named that both pools resolved to the same model that day, so
  "chat and agent resolve to different models independently" was not seen as two different
  models then. It was on 11 and 12 September 2026, when a request to `ravis/clarvis-agent` was
  answered by claude-sonnet-5 and one to `ravis/clarvis-chat` by claude-haiku-4-5. Fallback is proved by `ravis conformance clarvis` against a
  failing primary, not by a live failure.
- **Stages 5, 6, 7 and 8 — exits met.** Stage 5 when a SIRVIS result changed a RAVIS preference,
  and later all five criteria; Stage 6 criterion by criterion, with `NERVIS.md` §25's
  render-layer rebuild; Stage 7 against a collector killed mid-run; Stage 8 on 30 August, by a
  real-host standalone test and a four-window registry snapshot.
- **Stage 9 — exit not met as written.** `STATUS.md` called it met on 30 August. Its second
  clause holds: unsupported combinations are blocked in the UI, with NERVIS grading
  code-server's version `available`, `degraded` or `unavailable`. Its first does not. The list
  ends in teardown, and Bridge teardown under code-server is still `NOT_TESTED` in
  `clarvis/docs/code-server-matrix.md`. It asks for "both direct and proxied", and the proxied
  axis was graded through a throwaway spike proxy rather than NERVIS's own, which shipped on
  9 September and has not been graded against the matrix. And the matrix graded Clarvis 0.0.1,
  while 0.15.4 ships.
- **Stage 10 — open.** `STATUS.md` called it closed on §8's twelve-scenario acceptance list
  alone; the stage names ten suites and a frozen release candidate. The load suite ran on
  12 September (`tools/load_test.py`; the latest full run recorded passed five of six checks,
  missing §9.8's overhead budget by under a millisecond). The long-running suite
  (`tools/soak_test.py`) is built and has never run its length: the two runs recorded in
  `.run/soak/`, both 12 September, lasted three and five minutes. Rollback and recovery have
  never been rehearsed as one sequence. No release candidate is frozen — the repository has no
  tags. Four of §15's items are unchecked: code-server coverage, pairwise and E2E, the
  degradation matrix, and the rehearsals. And the milestones the product documents map to this
  stage carry no completion state: NERVIS M19 (`NERVIS.app`), RAVIS M20 (concurrency
  awareness), SIRVIS M22 (`SIRVIS.app`) and Clarvis E-C7 (release regression).

---

## 7. Test-double rules

A test double is permitted only after the contract it doubles exists. It must:

- be named `Fake…`, `Stub…` or `ContractTest…`, and report `test_double: true` in its identity;
- validate requests and responses against the canonical schemas;
- implement only the cases the current test requires;
- offer scripted failure, delay, disconnect, version-mismatch, duplicate-event and
  malformed-payload modes;
- never ship enabled in production, and never silently satisfy a readiness check;
- be replaced by a real producer for pairwise and ecosystem release gates.

Do not use a mock to define undocumented behaviour. If a test needs behaviour outside the
contract, stop and amend the owner's contract first.

---

## 8. Integration gates and end-to-end scenarios

Pairwise gates run before the full suite: SIRVIS→RAVIS, RAVIS→Clarvis, all services→NERVIS,
and NERVIS→code-server→Clarvis.

Required end-to-end scenarios:

1. Discover services, negotiate versions and capabilities, show truthful readiness.
2. SIRVIS inventories a local runtime, benchmarks a model and a Runtime Set, publishes
   `MEASURED` evidence.
3. RAVIS ingests that evidence, selects a route satisfying hard constraints, explains it,
   reports usage.
4. Clarvis chat uses `ravis/clarvis-chat`; an agent run uses `ravis/clarvis-agent`; both
   preserve one session and trace lineage.
5. A fragmented tool call completes correctly through RAVIS.
6. Clarvis Stop cancels upstream inference.
7. NERVIS shows the service chain and route without exposing prompt, source, key, raw path
   or benchmark-sensitive data by default.
8. A Clarvis action reaches an approval gate; NERVIS may display it but cannot approve or
   bypass it.
9. Two Clarvis instances operate concurrently with no workspace, session or event crossover.
10. RAVIS falls back when a local runtime fails, and records why. If privacy forbids cloud,
    it fails closed instead.
11. SIRVIS disappears mid-request; RAVIS continues with labelled stale or unknown evidence.
12. RAVIS outage leaves SIRVIS operational; SIRVIS outage leaves RAVIS operational.
13. NERVIS disappears; SIRVIS, RAVIS and Clarvis continue their standalone paths.
14. Clarvis still works through direct providers when configured that way.
15. NERVIS reconstructs a cross-service trace.
16. No API credential appears in any event or log output.

**E2E gate:** every scenario asserts over user-visible outcome, API response, event
sequence, trace linkage, redaction and persisted state — not screenshots alone.

**As built, 17 September 2026 — scenarios 13, 15 and 16 scored against the live stack.**

- **13 passes.** With only NERVIS stopped (11:56:46–11:57:22 UTC), SIRVIS and RAVIS kept their
  processes and reported `healthy`, `live` and `ready` throughout; SIRVIS served its system read
  and a recommendation, RAVIS its model list and an embedding through the local Ollama model, and
  code-server its health. The recommendation's event, published during the outage, reached NERVIS
  23 seconds after it came back, and nothing was refused. No editor window was open, so Clarvis
  was covered by its own suite (`clarvis/src/bridge/registration.test.ts`, "a NERVIS that is not
  running is an outcome, not an exception"); its chat and agent paths never pass through NERVIS.
- **15 passes.** NERVIS assembled trace `7886440e…` from 16 September — a Clarvis chat (3.0 s)
  around RAVIS's route to Claude Haiku 4.5 through Anthropic (2.6 s), joined by request id — with
  no warning and nothing partial, and the Traces screen drew both lanes with the services' health
  then and now. It also showed the only "correlated log lines" to be the dashboard's own reads of
  the trace; NERVIS 0.34.19 no longer counts a lookup of a trace as part of it
  (`nervis/src/nervis/unified.py`). Its fallback, lines "written during the same window", was the
  newest lines of each log whenever the trace happened, since no log line carried a time; since
  NERVIS 0.34.20 and ecosystem-protocol 0.2.3 lines carry one and only those within 2 s are
  offered.
- **16 passes.** Every event NERVIS holds (622, and 10 quarantined) and every service log
  (15 files in `.run/`, about 1.19 million lines, read by a script that printed locations only)
  carried no key in any issuer's shape and none of this machine's 11 launcher and enrollment
  secrets (`tools/check_secret_content.py`'s checks). Provider keys are checked by shape only —
  their stores are not read — and the Fish Audio key has no shape to check.
- **14 passes, on the record.** Clarvis predates this ecosystem: talking straight to a provider
  (`clarvis/src/model/AnthropicProvider.ts`, `OpenAiCompatibleProvider.ts`) was its only path
  before RAVIS existed and the owner used it daily, and it is still the path Clarvis takes when
  its provider settings name one — the owner's decision of 16 September 2026 (E-C2 not to be
  built) rests on it. No separate test call was made for this scoring.

---

## 8.9 Sketch — two machines, one ecosystem

**Not specified yet, and recorded because the constraints already are.** The
shape: a cheap box carrying a real GPU runs the models and the service that
measures them; a laptop runs the gateway and the control plane and routes to
them over the network.

    gpu box                          laptop
    ├── a model runtime (Ollama)  ←  RAVIS ── NERVIS
    └── SIRVIS                    ←──┘

What is already decided, so that fleshing this out is design work rather than
discovery:

**SIRVIS goes on the box with the models**, per §7 of `SIRVIS.md`: a runtime URL
means a runtime on this host, and a service measuring one machine while sampling
another's memory produces verdicts that are confidently inverted.

**RAVIS runs without SIRVIS.** §13.4 requires it — provider metadata and RAVIS's
own observations carry routing, with the degradation labelled. So the laptop
needs no benchmarking service of its own, and a box with no GPU has nothing for
one to measure. The one thing to watch is a model whose tool support is neither
measured nor declared: unknown fails closed, and the operator override is what
resolves it.

**A LAN model is remote, deliberately.** `ravis/local` promises *never leaves
this machine* and a private address is somebody else's computer, so the LAN
runtime is correctly excluded from `local` and `private`. Consequence worth
deciding rather than discovering: `ravis/private` therefore excludes the box
too, and there is no "my own network" tier — that would be a **new privacy
level**, not configuration.

**Cheapest and fastest stop being opposites.** A LAN runtime prices at zero like
any local one, so `ravis/cheap` resolves to the GPU rather than to a slow model
on the laptop's CPU. This is the machine-dependence RAVIS M26 was amended for.

**Exposure is gated, and asymmetrically.** SIRVIS refuses a non-loopback bind
outright — §9's rule below, enforced at startup. It formerly refused only a bind
lacking TLS or a credential, which closed the credential-only case and left the
one that mattered: a bind naming both started, and served every model on the
machine in cleartext anyway, because nothing passed the certificate to the
server. NERVIS's endpoint guard is loopback-only by
default and refuses a bare hostname outright, so the box is admitted by literal
address through `NERVIS_ALLOWED_HOSTS`. The model runtime itself carries no such
rule: it is an endpoint somebody chose to expose, not a control-plane service.
That asymmetry is intended and is the first thing to explain to whoever debugs
it.

**One SIRVIS to start with, and RAVIS M27 if that changes.** The box's measuring
service is the only one; the laptop's own models go unmeasured and are labelled
as such under §13.4. A second measuring service is not a second URL — RAVIS's
evidence store is keyed by build and role with the freshest record winning, so
two machines' answers about the same build would overwrite each other and route
traffic on a number taken elsewhere. `RAVIS.md` M27 carries that work.

**Open, and what fleshing it out means:** certificate handling for a home
network, whether "my LAN" earns a privacy tier, how the two halves are started
and stopped together, and what a trace looks like when it crosses a machine.

## 9. Security and privacy gates

- Bind locally by default. **Remote access is currently refused outright, in all three
  services**, because the rule this line used to state was satisfiable without being true:
  the TLS paths were validated at startup and never passed to the listener, so a bind that
  named a certificate and a key served cleartext. Remote returns as an explicit deployment
  choice carrying TLS *and* authentication once it has been built and proven — the
  requirement is unchanged, its enforcement is now honest about not existing yet.
  **As built, 12 September 2026: remote access with TLS and authentication is not built, and
  nothing owns building it.** This line used to hand it to "§16 item 2", and that list is gone
  — §15.1 records the item closing as loopback-only containment, not as remote access. No
  milestone in `NERVIS.md`, `RAVIS.md` or `SIRVIS.md` carries it, and §8.9's two-machine sketch
  lists certificate handling as open. What exists is the refusal: SIRVIS, RAVIS and NERVIS each
  refuse to start on a non-loopback bind (`_check_remote_exposure` in each service's
  `config.py`), whose message pointed at the retired "§16 item 2" until 12 September and now
  points here.
- Authenticate service-control operations separately from read-only diagnostics.
- Check what the ecosystem is built from for published holes. **As built, 16 September 2026:**
  `tools/check_dependencies.py` covers the services' Python environment, NERVIS's npm tooling
  and Clarvis (operator runbook, *Checking dependencies for known holes*). It is a step an
  operator runs, not a gate: it needs the network, and a hole published tonight would fail
  yesterday's commit.
- Provider credentials stay in their owning secure store. NERVIS never receives raw
  provider keys.
- Never log Authorization headers, secrets, full prompts, workspace contents, raw paths or
  model outputs by default.
- Clarvis's workspace boundary and approval gates remain authoritative through every path:
  direct, RAVIS, Bridge, NERVIS and code-server.
- Treat event and trace fields as potentially sensitive. Redact at the producer; enforce
  retention at the collector.
- Protect against SSRF, malicious service registration, replay, path traversal,
  cross-workspace confusion, origin and WebSocket abuse, and confused-deputy control requests.
- **Retrieved content is evidence, never intent.** Web results, RAG passages, uploaded files,
  tool output, log lines and model output inform an answer. **None of them can authorize an
  action, approve a gate, select a target or elevate a caller** — only an authenticated actor
  can. Text that reads as an instruction is still data. Every path that places retrieved
  content in a prompt passes it through the producer's fencing helper first. Ownership is
  named, so the rule has somewhere to be tested: **Clarvis** fences file, diff, tool, terminal
  and web content entering agent prompts (`CLARVIS.md` §9) and is where this is most exposed;
  **NERVIS** fences the AI-assisted diagnostic packet (`NERVIS.md` §11.5); **SIRVIS** fences
  generated output entering an external judge (`SIRVIS.md` §4.5); **RAVIS owns no such path and
  must not acquire one** (`RAVIS.md` §14). Each carries the negative test in its own release
  gate.
- **The owner enforces permission; the caller never asserts it.** A service that holds a
  resource applies its own checks to every request for it. A caller passing an identity is
  passing a claim, not a grant — Clarvis approves its own gates, NERVIS supervises only what
  it started, and RAVIS resolves its own credentials. **No component gains authority by being
  asked politely by another.**

**Security gate:** threat-model review complete; negative tests pass; privilege matrix
documented; secret scan and dependency checks pass; audit events exist for control actions
without leaking secrets. Negative tests cover both invariants above: retrieved text shaped like
an instruction changes nothing, and a caller-supplied identity grants nothing.

> The two invariants were named against the action-policy layer of Alexander Keisse's
> `ai-router` (<https://github.com/alexander-keisse>, MIT), which states them as operating
> rules for a gated action surface.

**As built, 16 September 2026 — reviewed, not yet signed.** The dependency check exists
(`tools/check_dependencies.py`; operator runbook, *Checking dependencies for known holes*). The
first threat-model review and privilege matrix are in `design/security/review-2026-09-16.md`,
with seven findings (S1–S7): NERVIS's supervision routes and other writes skipped the control token (fixed in NERVIS
0.34.15 and 0.34.17, which asks it of every write under `/api/v1/` except the services' own —
and event delivery, S7, which needs a sender's credential since NERVIS 0.34.18), credentials are owner-readable files by design, SIRVIS's
`admin` is total by design, there is no content-based secret scan, and remote access is not
built. S5 was closed the same evening: `tools/check_secret_content.py` scans each commit's added
lines (pre-commit hook) and every tracked line (clean-clone gate) for keys by shape and for this
machine's own secrets by value. The note below is what stood before that day.

**As built, 12 September 2026 — three parts of the security gate have nothing behind them.**
No dependency check runs anywhere: the clean-clone gate installs Node packages with
`npm ci --no-audit` (`tools/check_clean_clone.sh`), and no Python dependency audit exists in
either repository. No threat-model review is recorded — threat-model reasoning lives inside
product documents (Clarvis's `plan.md`, for one), but nothing records the review this gate asks
to be complete. And no privilege matrix is documented: searching both repositories finds the
phrase only where a specification requires one. The nearest thing to a secret scan is
`tools/check_no_tracked_secrets.py`, in the same gate, which checks that nothing git tracks
matches a `.gitignore` pattern meant to keep it out. The gate stays as written; this records
that it cannot yet be signed.

---

## 10. Failure and graceful-degradation matrix

Test at minimum: each service absent at startup; crash and restart mid-operation; slow
response; timeout; corrupt response; unsupported major; clock skew; duplicate and
out-of-order events; full disk; read-only data directory; unavailable keychain; expired
credential; hung local runtime; cloud provider 401/403/429/5xx; network loss; trace
collector loss; code-server loss; Bridge collision; stale registry lease.

Required outcomes:

- Capability and readiness become truthful within the documented detection interval.
- No automatic failover crosses a privacy, cost, residency or tool-capability constraint.
- Retriable operations are bounded and jittered; non-retriable errors are not looped.
- Queues are bounded and observable; telemetry loss never blocks core work.
- Recovery is idempotent — no duplicated jobs, charges, agent actions or approvals.
- Standalone product behaviour stays usable whenever that product's own dependencies are healthy.

**As built, 17 September 2026 — rehearsed live for two conditions across the whole stack.**
`tools/failure_rehearsal.py` held each of SIRVIS, RAVIS and NERVIS off its port while the launcher
started the rest, and killed RAVIS, NERVIS and code-server outright: the rest kept answering their
own reads, NERVIS reported the missing service unreachable in 20–31 s (its probe interval plus a
probe deadline), the launcher brought each back healthy with its database passing SQLite's
integrity check, and NERVIS's stored events never shrank. It also showed NERVIS filing an outage
note during an ordinary stop, which NERVIS 0.34.23 holds back until the outage is confirmed.
**Each cell now also says which outcomes cannot arise under its condition, and why** (the same
day, on the owner's word): "no automatic failover crosses a constraint" is established under all
9 conditions where a request can fall over to another model, and ruled out, with a reason, under
the other 10 (a full disk, clock skew, duplicate events and the like never fail a request's
attempt).

**Scored in `tools/check_degradation.py`, which parses the sentence above.** The conditions
were named here and enumerated nowhere, so which of them were covered was a question with no
answer — and a matrix nobody can score is one that gets called done. Each condition now
carries its evidence, the kind of evidence it is (a helper asserted, a route answered, a
running service observed), what is still missing, and the smallest step that would close it.
A condition added to this list and not to that file fails the gate; a cell naming a condition
this list does not is refused. **Each cell also names which of the outcomes above its evidence
establishes**, and says what is handled but unproved — because handling a condition is not the
same as holding six outcomes under it, and a single verdict cannot tell those apart. The gate
prints coverage per outcome, which is what the acceptance item below is actually asking about.

---

## 11. Observability rollout

In order: correlation IDs and redacted structured logs → standard events with local bounded
buffering → spans at service boundaries, then at key internal decisions → NERVIS event hub
and trace view → retention, export controls and sampling.

Do not begin with payload capture. Metrics include request rate, error rate and latency,
route outcomes, benchmark queue and runtime, runtime availability, event drops, registry
staleness, Clarvis run and gate states, and code-server proxy health. Every dashboard metric
links to its definition and source.

---

## 12. Release sequence

```text
ecosystem-protocol
   ↓
RAVIS alpha (transparent gateway + Clarvis conformance)
   ↓
Clarvis + RAVIS verified          ← first genuinely useful release
   ↓
SIRVIS alpha                       ← may be released earlier; blocks nothing until here
   ↓
RAVIS intelligence (translation + SIRVIS evidence + lifecycle)
   ↓
NERVIS alpha
   ↓
Clarvis Bridge
   ↓
code-server bundle (optional)
```

Use additive changes first. Publish compatibility matrices and minimum/maximum peer
versions. Canary on one machine, then a small multi-instance cohort, then general release.
NERVIS must tolerate peers one supported minor behind during a rolling upgrade.

### 12.1 Operational startup and verification sequence

1. Start SIRVIS. Wait for `live=true`; record readiness and capabilities. If SIRVIS is
   intentionally absent, record degraded mode before continuing.
2. Start local runtimes managed outside RAVIS and SIRVIS; verify their published owner
   reports readiness.
3. Start RAVIS. Verify identity, version and capabilities; provider credential status
   without revealing keys; SIRVIS ingestion status; and one non-billable or approved route
   test. A privacy-constrained no-route result is valid; an invented fallback is not.
4. Start NERVIS. Verify registry authentication, capability negotiation, SIRVIS and RAVIS
   tiles, the event SSE cursor, and a general chat smoke request if configured.
5. Open each VS Code-family workspace. Clarvis activates with its extension host. Verify
   its direct-provider baseline first, then the RAVIS chat and agent roles.
6. If enabled, each Clarvis Bridge starts with its extension host. Verify distinct instance
   and workspace IDs, then NERVIS registration, status and events.
7. If enabled and supported by the matrix, start code-server, then the NERVIS reverse proxy.
   Verify auth, WebSocket, Clarvis activation, workspace boundary and Stop/gate behaviour.
8. Run the whole-ecosystem smoke: SIRVIS evidence → RAVIS explained route → Clarvis
   chat/agent request → NERVIS trace. Confirm redaction and provenance.

At every step, an unsupported protocol major, a failed authentication, a false readiness or
a missing required capability is a **STOP** for that dependent integration. Independent
services may keep running in documented degraded mode.

Orderly shutdown reverses dependency use: stop accepting new NERVIS control and chat work;
drain and close code-server sessions; close Clarvis windows, which stops their Bridges; stop
NERVIS; drain or cancel RAVIS requests and stop RAVIS-owned runtimes; cancel or checkpoint
SIRVIS benchmark jobs; stop SIRVIS; then stop externally owned runtimes only through their
owner. Verify leases expire, queues drain or are marked, and no orphan process remains.

After an unclean restart: start authoritative data owners first, run integrity and migration
checks, reconcile in-flight SIRVIS jobs and RAVIS requests without replay, expire stale
registry and Bridge leases, then follow the normal sequence. **Never resume an agent action
or an approval automatically.**

**As built — the launcher follows the order, not yet every check.** `tools/run.py` is what
starts and stops the stack.

- **The order, since 16 September 2026 (NERVIS 0.34.13):** `start` brings up SIRVIS, Ollama,
  RAVIS, NERVIS and code-server in that order, each only once the one before it answers or its
  30-second wait has run out; one that never answers is named and the rest still start, as
  independent services may run degraded. `stop` takes down code-server, NERVIS, RAVIS, SIRVIS,
  then Ollama (`START_ORDER` and `STOP_ORDER`; `nervis/tests/test_launcher_lifecycle.py`). Until
  then `start` launched in table order without waiting — Ollama after NERVIS — and `stop` went
  alphabetically, code-server last and Ollama before RAVIS and SIRVIS.
- **Readiness, since 16 September 2026 (NERVIS 0.34.16):** "answers" means the service's health
  address answered 2xx (`ready`); another status is named. Until then any reply counted, a 404
  included, which hid that SIRVIS was being asked at `/v1/status`, a route it doesn't have — it
  is asked at `/ecosystem/health` now.
- **Still short of the steps above:** `start` does not read `live=true` in the body, record readiness or capabilities, stop on an unsupported
  protocol major or a failed authentication, or run step 8's whole-ecosystem smoke; `stop` does
  not drain code-server sessions or RAVIS requests beyond each service's own shutdown.

---

## 13. Rollback and recovery

- Preserve the prior executable, package and config. Configuration changes are atomic.
- Back up databases before migrations. Every migration has forward, compatibility and
  rollback/restore proof.
- Roll back consumers before removing producer capabilities. Never downgrade across an
  incompatible data migration without a restore.
- On a failed rollout: stop new work, preserve logs and traces, disable new capability
  flags, drain or mark jobs, restore compatible versions, verify health and data integrity,
  then resume.
- Clarvis rollback means reinstalling the prior `.vsix`. Workspace data and SecretStorage
  stay intact; migrations are backward-safe.
- RAVIS never reroutes queued requests during recovery unless policy permits and idempotency
  is proven.
- SIRVIS keeps benchmark evidence immutable. Recovery appends corrections; it never rewrites
  provenance.

**As built, 17 September 2026 — rehearsed.** The stack was rolled back from NERVIS 0.34.18,
RAVIS 0.29.2, SIRVIS 0.19.4 and protocol 0.2.2 to the previous released set and forward again,
with no lost rows, no rerun job, no secret in the output and Clarvis untouched, and each service's
`restore-database` was run against a damaged copy of its live database and brought it back whole
(operator runbook, *Rolling the stack back to an earlier release*). **Across a database format
change** too, the same day (`tools/upgrade_rehearsal.py`): each service's release before its newest
migration, on a copy of the backup the live service took before it, was upgraded, refused the
converted database, was restored exactly with its own `restore-database`, ran again and was
upgraded again — on copies, so the live data never crossed a format backwards. Clarvis's own
rollback was proven separately on 6 September.

**Recovery gate:** a rehearsed rollback returns the ecosystem to the last compatible set,
with no lost accepted work, no duplicated execution, no secret exposure and no weakened
Clarvis boundary.

---

## 14. Engineering standards

One standard for all four products, stated once here because a coding rule copied into four
documents drifts exactly like a contract copied into four documents. Product documents add
deltas; they do not restate this.

### 14.1 What is enforced, and by what

**A rule nobody checks is a preference.** Every mechanical rule below is a gate, and the gate
is the authority — not this prose. Each gate runs locally before every push
(`ruff`/`mypy`/`pytest`/`eslint`/`tsc`/`npm test`, per the table below) rather than in CI:
GitHub Actions is disabled on both repositories, deliberately, for cost —
`.github/workflows/checks.yml` and `clarvis/.github/workflows/ci.yml` name the same gates and
would run them automatically the day Actions is re-enabled, but until then "the gate ran and
passed" is established by running it, not by a workflow badge.

| Rule | Python | TypeScript |
|---|---|---|
| Cyclomatic complexity **≤ 8** per function | `ruff` `C901`, `max-complexity = 8` | `eslint` `complexity: ["error", 8]` |
| Line length ≤ 100 | `ruff` `E501`, `line-length = 100` | `eslint` `max-len` |
| Import order, unused names and arguments, obvious simplifications | `ruff` `I`, `F`, `ARG`, `SIM` | `eslint` `no-unused-vars` |
| Naming conventions | `ruff` `N` | `@typescript-eslint/naming-convention` |
| Types at every public boundary | `mypy --strict` on the protocol package and every adapter | `tsc --noEmit` |
| Tests pass without a live model, network or Redis | `pytest` | `npm test` |
| `STATUS.md` still matches the repository | `tools/check_status.py` | — |

Eight is deliberate rather than conventional. The branchiest code in this ecosystem is
eligibility filtering, capability negotiation and stream translation — precisely where a hidden
path is a compatibility bug rather than a style problem. **An exemption is a `# noqa: C901` with
a sentence saying why the branching is irreducible**, and a reviewer may reject the sentence.

Function length is guidance, not a gate: under twenty lines is a good target, but line count is
a poor proxy and complexity is the honest measure.

### 14.2 Naming, structure and simplicity

Intention-revealing names. Classes are nouns, methods are verbs. No `data`, `info`, `manager`,
`helper` or `utils` as a name — if that is the best name available, the thing does not have one
responsibility yet. No Hungarian notation, no type prefixes, no single-letter names outside a
comprehension or a coordinate.

A function does one thing at one level of abstraction, takes zero to two arguments (three at the
absolute limit), and **takes no boolean flag argument** — a flag means two functions sharing a
body. Commands change state and return nothing; queries return a value and change nothing. A
function's name is a promise about its side effects.

Prefer `dict` dispatch or `match` to a long conditional chain, and **do not build a class
hierarchy to avoid a three-branch conditional** — that trades a small smell for a large one.
DRY, YAGNI, KISS, and the boy-scout rule apply in that order of frequency: duplication is the
common failure, speculative generality the expensive one.

Composition over inheritance. Program against the protocol, not the implementation — the
`ProviderAdapter` and `RuntimeAdapter` protocols exist for this. Construction is separated from
use: adapters, clients and stores are injected, never constructed inside the function that uses
them, because that is what makes them testable without a live service.

### 14.3 Comments

**Write for the author six months from now, still learning this domain.** That is the standard
here, and it is deliberately not the industry default of "comment only where the why is
non-obvious". This ecosystem is being built as a way of learning it, so a file that only a
current expert can follow has failed even when every name is perfect.

What that means concretely:

- **Comment every public function and class**, and every non-trivial block, with what it does
  and why it exists — not only where the code is surprising.
- **Explain the domain, not just the code.** "Assemble fragmented tool-call arguments" says what
  the loop does. The comment that earns its place says *why the arguments arrive in fragments at
  all*, and what breaks downstream if they are reassembled wrongly. The second reader needs the
  concept; the first only needs the syntax, and they can already read it.
- **Cite the section that governs the rule.** Nearly every non-obvious constraint in this code
  exists because a specification demands it. A comment ending `— RAVIS.md §8.3` turns the
  codebase into something you can navigate back to the reasoning from, and turns a future
  "why is this here?" into a lookup instead of an excavation.
- **Record what went wrong.** A guard deserves the failure it prevents, in one sentence. The
  template's comments do this well — read a few before writing your first — and that record is
  what stops the guard being "simplified" away by someone who never saw the bug.
- **Name the concept**, so it is searchable later: write "prompt-injection fence", "eligibility
  filter", "transparent path" where those are what is happening.

What is still noise: restating syntax (`# increment the counter`), an apology for unclear code —
rename it instead — and a docstring that repeats the signature in prose.

**Never comment out code.** Delete it; git holds it, and git is the archive (§3). A
commented-out block in a file others copy from is how an undeclared contract gets built: this
project has already had one, a commented `fetch` describing a Clarvis Bridge write path that no
specification declared and that contradicted two of them.

### 14.4 Errors, absence and the wire

Internally, raise exceptions with context; do not return error codes or sentinel values, and do
not signal failure through a boolean. **At the boundary this inverts**: `/v1` returns
OpenAI-compatible error shapes and everything else returns the MEP envelope (§4.5), so an
exception is translated exactly once, at the edge, and no exception type is ever visible on the
wire. Write the failure path first when a call can fail.

**Absence is a domain value in this system, and must not be optimised away.** The blanket "never
return null" rule does not apply here and would do real damage: `SirvisModelRef` is null when
SIRVIS has not confirmed a build, evidence provenance is `UNKNOWN` rather than absent, a
capability lookup answers 404 rather than guessing, and a no-route decision is a first-class
explainable outcome. What the rule is actually protecting against still holds — never return
null to mean *empty* (return the empty collection) and never return null to mean *failure*
(raise). But where absence is a fact the caller must handle, make it explicit and typed, and
**keep it distinguishable from a guess.**

### 14.5 Tests

Fast, independent, repeatable, self-validating, written with the code. **No test touches a live
model, a network or a real service** — the conformance suites replay recorded fixtures, which is
what makes them runnable in seconds and trustworthy in CI.

Arrange, act, assert. One concept per test, which is not always one assertion: a stream
conformance test asserting an ordered sequence of events is testing one concept. Test names
state the behaviour and read as sentences — `cancellation_reaches_upstream_without_triggering_
fallback`, not `test_cancel_2`. Test code is production code and is reviewed as such.

### 14.6 Verify, don't assume

The gates in §14.1 catch mechanical error. This section is about the other kind — the mistake
that lints clean, types clean, and is confidently wrong. It cannot be a CI gate, so it is
written as behaviour instead, and every rule below comes from something that actually went
wrong in this repository rather than from general advice.

**The claim you cannot check is a claim you do not make.** State what you verified and how. If
something cannot be verified right now, say so and say what would settle it — an unverifiable
claim delivered confidently is worse than an open question, because the next person builds on it.

**Before editing, confirm the thing you are editing exists.** Every symbol, field, setting and
anchor. A capability check was once written against a `capable()` helper that did not exist in
the file; it would have thrown on first render. Grep for the definition, not just the usage.

**Before asserting a number, compute it.** `WIRING.md` documented a pool with eight members,
filtering on a field named `b.tools`. Executing the real data layer gave eleven builds, eight
advertising the capability, ten satisfying the invariant — and the documented predicate matched
**zero**, because the field had been renamed. Three numbers in one sentence, all wrong, in the
file implementers copy from.

**Before deleting, read the target and check what points at it.** Removing `archive/` meant
first confirming nothing loaded from it, that its claim of a byte-identical avatar was true, and
that no reference would dangle afterwards — two did, in `AGENTS.md`, and one of them was
instructing future agents to recreate the folder.

**Before acting on someone else's finding, verify it yourself.** An eight-way audit of this
repository produced forty-eight candidate findings; an adversarial pass refuted nineteen of
them. Confidence that has not survived an attempt to refute it is not evidence.

**After changing something, run the check.** Not before, not instead. And when a check reports
a problem in its own output, fix the check first — a checker that cries wolf teaches people to
ignore it, which is worse than having no checker.

**A slow right answer beats a fast wrong one**, because in a system of four products with
published contracts, a wrong answer does not stay local. It gets cited.

### 14.7 Where a product may differ

A product document may add rules and may tighten these. It may not loosen them silently: a
deliberate exception is written down in that document with its reason, and anything not written
down there is governed by this section. §14.6 is not among the things a product may loosen.

### 14.8 Milestone completion states

A checkmark that can mean three different things — code exists, a test passed, a person watched
it work — was the defect an external audit found repeatedly in this repository's own milestone
tables. One symbol cannot carry three claims; a reader who cannot tell which claim it is making
is reading a guess dressed as a fact.

Every milestone table row in `NERVIS.md`, `RAVIS.md`, `SIRVIS.md` and `CLARVIS.md` carries one
of four states, not a bare ✅ or ☐:

- **IMPLEMENTED** — the code exists on the path that ships. No automated test yet exercises the
  acceptance criterion.
- **AUTOMATED VERIFIED** — a test in this repository's own suite exercises the acceptance
  criterion and passes where the gates actually run. GitHub Actions is off on both
  repositories for good (§14.1), so that means two places rather than a workflow badge: the
  package's own suite, run locally, and `tools/check_clean_clone.sh`, which clones from the
  remote into a fresh directory and runs the suites there — the one run that fails on a file
  nobody committed or a dependency nobody declared. Not yet demonstrated against real running
  services.
- **LIVE VERIFIED** — demonstrated against this ecosystem's actual running services, not a
  fixture or a mock, with the evidence recorded — a `STATUS.md` entry, a trace, a transcript.
  The only state that satisfies §15's "reproducible evidence" bar.
- **BLOCKED** — cannot advance past its current state for a stated external reason: a dependency
  not yet built, an environment this repository cannot construct, another product's unshipped
  capability. The reason is written beside the row, not left implicit — "blocked" without a
  reason is indistinguishable from forgotten.

A row may split across sub-claims rather than force one state on all of them — `RAVIS.md`'s M19
already does this, marking three of four signals LIVE VERIFIED and naming throughput separately
as absent. This section generalises that shape; it isn't a new idea.

**Enforced by `tools/check_plans.py`**, which must reject any other marker in a milestone row.
Per §14.1, a state nobody checks is a preference — and an unchecked convention silently
downgrading to an optimistic checkmark is the exact failure this closes.

**Including `CLARVIS.md`, whose milestones are headings rather than table rows.** The gate read
the three specs and walked past the fourth document this section names, so the last surviving
`✅` in the ecosystem sat in the one file nothing checked. A rule that stops at a file format is
a rule with a hole in it; the shape differs and the requirement does not.

A product document may not soften this to a single completion mark for convenience (§14.7); it
may only add product-specific detail beside the required state.

---

## 15. Whole-ecosystem acceptance checklist

- [x] All four products build and run independently — §3 makes that "independent
      buildability, not repository count", and there are two repositories, not four.
      *Reverified: NERVIS, RAVIS and SIRVIS each now bind a real socket and answer a
      real HTTP request with every peer absent — `uvicorn.Server` on port 0, driven
      by a real client, not the ASGI transport every other test in each suite uses,
      which was never going to fail to bind anything. RAVIS's M0 moved from
      IMPLEMENTED to AUTOMATED VERIFIED on exactly this gap closing. Clarvis's own
      standalone proof (`bridgeDisabled.spec.ts`) sat outside `tools/check_clean_clone.sh`
      because `npm test` (node's own runner) never ran it — it needs a real extension
      host, which only `npm run test:host` (`@vscode/test-cli`) starts. Closed: the
      gate now runs `npm run build` (nothing else built the `dist/extension.js` a fresh
      clone needs to activate at all) then `npm run test:host` under a bounded timeout,
      sharing one persistent VS Code test-binary cache across runs so a fresh clone
      doesn't re-download it every time. Fail-proved directly: a deliberately broken
      assertion in the spec reports FAIL with the real Mocha failure output; a
      deliberately hung command under the new timeout wrapper reports FAIL in seconds,
      not the wrapper's own multi-minute ceiling.*
- [x] The non-invention rule appears in every app document and in agent working
      instructions. *Checked by reading every candidate, not assumed: this runbook,
      `RAVIS.md`, `SIRVIS.md`, `NERVIS.md`, `CLARVIS.md`, `AGENTS.md`,
      `nervis/AGENTS.md` and `clarvis/AGENTS.md` all already carried it. Only
      `clarvis/plan.md` — Clarvis's own build plan, where its milestone work and
      checklist actually live, cross-referenced from `clarvis/AGENTS.md` but never
      quoting the rule itself — did not. Added the same statement `AGENTS.md` already
      carries (Clarvis 0.12.8).*
- [x] MEP schemas, fixtures and versions are released and pinned. *`tools/schema_check.py`
      exists specifically for this item — its own docstring quotes it — and reverifying
      rather than trusting that found all three parts current: 6 released schemas
      (`protocol/schemas/*.json`, each `$id` pinned to `.../mep/1.0.0/...`) generated from
      and matching the live models, 23 fixtures behaving against them, and all three
      services' metadata routes satisfying what's released. `conformance_check.py`
      confirms the shared error envelope separately. Reverifying this item's own gates
      found one real, live drift it exists to catch: Clarvis's 0.12.8 (shipped earlier
      this session, closing a different §15 item) had no `## Clarvis — 0.12.8` release
      note, so `tools/check_releases.py` was failing. Added; all of `schema_check.py`,
      `conformance_check.py`, `check_releases.py` and `check_compatibility.py` pass.*
- [x] Health, identity, capabilities, version, events, traces, request/session IDs and error
      envelopes conform. *Health/identity/capabilities/version reconfirmed by rerunning
      `tools/schema_check.py` — 6 released schemas match the live models, 23 fixtures
      behave, all three services' metadata routes satisfy them. Error envelopes (with
      `request_id`/`trace_id` as required fields) reconfirmed by rerunning
      `tools/conformance_check.py`. Events, live trace propagation across a real
      three-service chain, and request/session-id correlation are what
      `tools/acceptance_run.py`'s live golden-path run checks — it needs the ecosystem
      actually running, a real model for SIRVIS to measure, and two clauses (Clarvis's
      agent task, undo) need a person at the editor, so this closes on the already-recorded
      evidence rather than a fresh run tonight: `STATUS.md`'s "The golden path, run against
      the running thing" (5 Sept) — every clause `PROVED` attended, including the live
      event stream read to its boundary before work starts (frames, not a replay), a trace
      joining spans from all three services with `partial=False`, and request/decision
      correlation by id.*
- [x] SIRVIS provenance distinguishes `MEASURED`, `ESTIMATED` and `UNKNOWN` end to end.
      *`nervis/tools/provenance_check.js` exists specifically for this item — its own
      comment quotes it — and reverifying rather than trusting found the full chain
      intact at all three layers. SIRVIS: `core/evidence.py`'s `EvidenceKind` lattice
      (`MEASURED`/`PARTIALLY_MEASURED`/`ESTIMATED`/`UNKNOWN`) and its `combine()`, which
      can only weaken and treats an empty input as `UNKNOWN` rather than a vacuous
      `MEASURED` — 58 tests across `test_m16_evidence_api.py`, `test_m7_evidence.py`,
      `test_validity_scope.py`. RAVIS: `evidence/sirvis.py`'s `EvidenceProvenance`
      distinguishes `MEASURED_BY_SIRVIS` from `OBSERVED_BY_RAVIS` — the exact conflation
      an external audit found live on 5 Sept, RAVIS's own rolling observation over real
      traffic wearing a benchmark's badge — 47 tests across `test_sirvis_evidence.py`,
      `test_evidence_validity.py`. NERVIS: `prov()` maps all 7 provenance kinds RAVIS or
      SIRVIS can send to 5 badges, every one styled in the dashboard's own CSS, checked
      against the enums that produce them rather than a second hand-kept list. All three
      layers reran clean.*
- [x] Runtime Sets and multi-model evidence are represented without invented semantics.
      *No single gate exists for this line the way `schema_check.py`/`provenance_check.js`
      do for their own items, so this closed on reading the actual representation logic
      rather than rerunning one script. Found the discipline real at every point checked:
      `core/runtime_sets.py`'s `estimate_fit` refuses to total a set's weight when any
      member's installed size is unrecorded — "a sum missing a term is not a sum" —
      rather than guessing; §10.1's rule that two independently-fitting models prove
      nothing about running together is a named test,
      `test_the_joint_term_is_zero_until_a_pair_has_been_measured`; swap is sampled from
      real `vm.swapusage` reads at baseline/load/run/unload, never estimated
      (`benchmarks/engine.py`'s `_swap_warnings`); and multi-model evidence still passes
      through `core/evidence.py`'s `combine()` lattice, which can only weaken. 67 tests
      across `sirvis/tests/test_m9_runtime_sets.py`, `test_m15_recommendations.py` and
      `test_m6_benchmark.py`, all passing. **Named, not hidden:** M15b (Runtime Set
      recommendation as a named output rather than M15's model-only ranking) has no
      status tag in `SIRVIS.md` — genuinely unbuilt — and its absence is itself represented
      honestly, refused with `UNSUPPORTED_PARAMETER` rather than answered with an invented
      ranking, which is this item's own rule applied to the gap.*
- [x] RAVIS enforces hard constraints before preferences, and explains routes and
      rejections. *`ravis/src/ravis/routing/engine.py`'s own docstring states the design
      this item asks for: "eligibility is a hard filter... this runs first and
      separately, and a candidate removed here is never reconsidered by scoring."
      Reverified rather than trusted: `test_a_direct_address_does_not_bypass_the_constraint`
      confirms even a direct-addressed request cannot route around a hard constraint;
      `test_the_upstream_is_never_contacted_for_a_refused_request` confirms the check
      happens before any network call, not merely before scoring in theory;
      `test_the_model_that_would_have_won_is_excluded_by_name_and_reason` and
      `test_every_exclusion_carries_a_reason` cover the rejection half. The winner's own
      explanation (`_selection_reason`) is designed the same way — "usually 'it sorted
      first'. Saying so is the point... claiming a quality judgement RAVIS has no
      evidence for would be the opaque magic §9.4 forbids" — and `routing/explain.py`'s
      `RouteDecision`/`ExcludedCandidate` carry that explanation as a stored, redacted
      object rather than a log line, reaching NERVIS and traces. 47 tests across
      `ravis/tests/test_hard_constraints_route.py` and `test_routing.py`, all passing.*
- [x] OpenAI-compatible streaming, cancellation, tools, errors and usage pass contract
      tests. *No single file is named for this line; the coverage is spread across five,
      each stating a distinct piece of the contract in its own docstring, reverified by
      rerunning all five rather than trusting a past pass. `test_translated_wire.py`
      (M3b, §6) asserts constructed stream frames — tool-call indexes, tool-call ids,
      fragmented JSON arguments, reasoning fields, finish reasons, usage chunks, `[DONE]`
      — against fixtures recorded from a real OpenAI-compatible server, "the point of the
      suite is to notice when RAVIS smooths something away." `test_translated_path.py`
      (M3b end to end) proves cancellation actually reaches the provider, not just the
      client's own connection. `test_transparent_proxy.py` (M1) proves the untranslated
      path forwards bytes unchanged, byte for byte. `test_fallback.py` (M12 end to end)
      states its own acceptance criterion outright: "none of it can corrupt a stream or
      turn a cancellation into a second request." `test_malformed_requests.py` covers
      errors, found originally by sending deliberately bad payloads at a running RAVIS
      rather than by reading the code. 70 tests total, all passing.*
- [x] `clarvis-chat` and `clarvis-agent` are independently configurable and tested.
      *`ravis/src/ravis/core/pools.py` defines them as genuinely separate
      `VirtualModelPool`s — distinct curated families, distinct evidence roles, and one
      structural difference that is the whole point: `clarvis-agent` makes tool support a
      hard invariant ("§5.1's hard invariant forbids admitting a non-tool-capable model
      however well it codes") while `clarvis-chat` treats it as optional unless the
      request itself asks for tools. Reverified rather than trusted: 32 tests, including
      `test_a_chat_pool_without_tools_in_the_request_does_not_require_them`
      (`test_capability_filtering.py`), which asserts the distinction rather than the
      declaration. `test_clarvis_conformance.py` runs `ravis conformance clarvis`
      (RAVIS.md §8.9's release gate) on every commit rather than only at release time, and
      asserts its Stage 2/Stage 3 requirements by name so deleting a check fails the test
      instead of quietly shrinking the gate. `test_clarvis_preflight.py` covers
      `ravis preflight clarvis`'s diagnosis of `clarvis-agent`'s own silent failure modes.
      All 32 tests pass.*
- [x] Clarvis lifecycle, workspace containment, approval gates, provider abstraction and
      SecretStorage are unchanged. *Reverified by rerunning Clarvis's own suites in full
      — `npm test` (1322/1322) and `npm run test:host` (16/16, the real-extension-host
      half `npm test` cannot reach) — rather than assuming the ecosystem work left them
      alone. Each named property has its own dedicated coverage inside that total:
      lifecycle in `src/test/activation.spec.ts` (real extension host — activates without
      throwing, every declared command registers); workspace containment in
      `src/test/containment.spec.ts` (a real workspace folder — a climbing relative path
      and a symlink pointing outside are both refused) and `src/agent/tools/
      workspacePaths.ts`; approval gates across eleven files under `src/agent/gate/` plus
      `gateDecision.test.ts` (148 tests — branch handling, dirty-tree detection, injection,
      test-command gating, review, registry); provider abstraction across nine files
      under `src/model/` (112 tests — model resolution, reasoning, tool probing, stream
      assembly, treated uniformly across LM Studio, OpenRouter and the rest); SecretStorage
      in `secretStoreLabel.test.ts`. Nothing here changed to make it pass.*
- [x] Multiple Clarvis instances remain isolated. *Enforced from both directions and
      tested on both. NERVIS's receiving side — `nervis/tests/test_m8a_registration.py`,
      the "Per-extension-host instances, with no cross-instance leakage" section —
      `test_two_extension_hosts_appear_as_two_instances`,
      `test_one_instances_token_does_not_work_on_another`,
      `test_a_live_instance_id_is_not_taken_over`, `test_deregistration_needs_the_
      instances_own_token`: 16/16 passing, reconfirmed. Clarvis's sending side —
      `identity.test.ts` — "two installations get different identities" and
      "different paths under one salt are different IDs" (`workspace_id` derived
      from path and salt, `instance_id` unique per extension-host lifetime), with
      "nothing here is derived from the machine" closing the path where shared
      machine-level state could otherwise leak across instances by accident. M9's
      diagnostics fold (`nervis/src/nervis/clarvis.py`) filters by `source.instance_id`
      rather than convention, so a window with no events gets an empty list, never
      another window's. Live-verified end to end on 29 Aug (`clarvis/plan.md` M14): two
      real Bridges against a real NERVIS took distinct instance and workspace IDs on
      distinct OS-assigned ports, and closing one removed only its own registration. Part
      of the full suites already reconfirmed for the prior item: 1322/1322 Clarvis,
      16/16 `test_m8a_registration.py`.*
- [x] NERVIS negotiates actual capabilities; no UI assumes a missing API.
      *`nervis/tools/capability_check.js`'s own comment quotes this line verbatim — built
      after `capable()` was found defined-and-never-called, "a fault this repository has
      now made twice." Reverified rather than trusted: the gate renders every screen
      against services that are up and advertise nothing, and asserts three things
      together — no screen throws, screens needing a capability render the withheld
      block with every acting control disabled and a reason, and the same screens with
      capabilities advertised render neither, so a gate that reported "withheld"
      unconditionally couldn't pass all three. Current run: 36 screens survive, 24 say
      so, none says so when advertised. `nervis/src/nervis/negotiation.py`'s §5.2 rules
      (unknown capabilities are unavailable, an unrecognised *optional* capability is
      ignored rather than a fault, an unsupported major marks the feature incompatible
      without disabling the whole dashboard) each have a named test in
      `nervis/tests/test_m2_registry.py` —
      `test_an_unadvertised_capability_is_unknown_rather_than_assumed_fine`,
      `test_a_capability_this_build_never_heard_of_is_ignored`,
      `test_an_unsupported_major_is_incompatible_not_unreachable`,
      `test_removing_one_capability_disables_only_its_operation`. 54 tests, all passing.*
- [x] NERVIS cannot bypass a Clarvis or RAVIS safety gate. *Reverified against the
      current code and its own tests, not just read. The Clarvis half is structural
      two ways over: the Bridge refuses every non-GET method before path matching
      (`clarvis/src/bridge/server.ts`), proved by `server.test.ts`'s "every method
      other than GET is refused, on every path" and "a write to an unknown path
      answers the same as a write to a known one" (the method check really does run
      before routing, not just refuse what it happens to recognise) — 1322 of 1322
      Clarvis tests passing; and NERVIS's own Bridge-reading code
      (`nervis/src/nervis/peers/clarvis.py`) declares exactly one surface, a GET, and
      has no write call anywhere to attempt one with. The RAVIS half is a control
      token required on the six configuration mutations NERVIS proxies
      (`nervis/src/nervis/api/control.py`), each of which still forwards RAVIS's own
      real admin credential on every call rather than substituting the page token for
      it (confirmed reading `set_ravis_credential` and its siblings in
      `nervis/src/nervis/api/routes.py`) — the token stops a cross-origin page from
      issuing them with no credential at all, RAVIS's own admin check is never
      skipped. `nervis/tests/test_control_token.py`'s
      `test_no_route_that_reads_the_ravis_admin_credential_is_left_ungated` walks the
      real route table rather than a hand-kept list, so a seventh mutation added
      without the guard fails the suite instead of shipping quietly — 22 of 22
      passing. It is not authentication: the token is minted per process and
      embedded in NERVIS's own unauthenticated page, so a local process able to
      reach NERVIS's port can read it and use it, the way it could already reach
      anything else that process can reach. That boundary is the operating system's,
      stated rather than assumed — see `nervis/src/nervis/api/control.py`'s own
      docstring.*
- [ ] code-server compatibility is evidenced for every supported matrix cell.
      *Still open, and for one reason fewer. §13.3's reverse proxy shipped
      9 September 2026 — `/code/`, per-workspace sessions and the ten-test
      security gate — so M14's proxy half is built and the Code tab is served
      through it rather than from code-server's own port. What is not closed is
      coverage: the proxied path's own browser and host matrix (`NERVIS.md`
      §13.3) has one graded row, Chromium on loopback http, and Clarvis is
      still installed by a command the launcher prints rather than by NERVIS.
      `clarvis/docs/code-server-matrix.md` is honest about the rest.
      **Firefox, on the default path, is covered by the owner's daily use**
      (17 September 2026): it is their main browser and practically the only
      way they have used Clarvis, and it works as intended. The default Code
      tab frames code-server at its own address (`code_proxy_enabled` is off
      unless set), so this does not grade the proxied path's Firefox row.
      The matrix itself is graded against Clarvis 0.0.1 while the product ships
      0.15.4 (12 September 2026), and of its 56 graded cells 1 remains
      `NOT_TESTED`: Bridge teardown under code-server. (56 is what its tally and
      its cell headings both count; its prose still says 51 original cells plus
      4 added by a coverage check, which is one short.) The other two were
      closed for real on 6 September 2026 — rollback to a prior `.vsix` (see the
      "Upgrade, downgrade, backup, rollback" item below) and multiple windows
      against one server (two live code-server tabs, two distinct `instance_id`s
      on two ports in NERVIS's own registry, and the closed tab correctly showing
      `live: false` rather than vanishing or lingering true) — moving the tally to
      39 `PASS` / 16 `PASS_WITH_LIMITATION` / 0 `FAIL` / 1 `NOT_TESTED`.*
- [ ] Pairwise and full E2E suites pass against real services.
      *Reverified 6 September 2026, and one real gap found. Of §8's four named
      pairwise gates, three have live evidence against the current (patched)
      code: SIRVIS→RAVIS (`tools/pairwise_check.py`, 13/13 real), RAVIS→Clarvis
      and all-services→NERVIS (`tools/acceptance_run.py --unattended`, run
      twice against the running stack — see below). The fourth,
      NERVIS→code-server→Clarvis, now has an implementation: §13.3's proxy
      shipped 9 September 2026 and `nervis.code_server_proxy` reports
      `degraded` — built, gate passing, one browser graded. The gate is not the
      pairwise evidence, though. What has been driven end to end is
      NERVIS→code-server: the login page framed through `/code/`, code-server's
      own redirect rewritten onto the proxy, a WebSocket upgraded through it.
      Clarvis's half still needs somebody at a keyboard to sign into the
      proxied editor and drive the extension. `tools/acceptance_run.py
      --unattended` passed every clause of its own that runs without a person —
      twice, after real routing/session security patches (F2–F9) landed since
      the last full pass — but the two clauses needing a person at an editor
      (Clarvis's contained task through the route; the Bridge-disabled path)
      were last proved attended on 5 September, before those patches, and were
      not reattended today.*

      ***Corrected 12 September 2026: that run is not a pass over §8's list.***
      *This item used to say the run proved every one of §8's sixteen scenarios
      an unattended run can reach. It was written to the stabilization track's
      golden-path sentence (§15.1) plus two of §10's conditions, not to §8, and
      mapped onto §8 its unattended steps reach scenarios 1–3, 7, 11, 15, most of
      16 (it reads NERVIS's event and log surfaces for credential-shaped text)
      and 12's SIRVIS-outage half; 4 is its person-dependent clause. It has no
      step at all for 5, 6, 8, 9, 10, 13, 14 or 12's RAVIS-outage half — and 5,
      10, that half of 12, and 13's SIRVIS and RAVIS side need no editor. The
      one-scenario-at-a-time scoring in `STATUS.md` ("Stage 10's acceptance
      list, scored honestly") covered twelve scenarios, on evidence from the
      suites and earlier live runs, while §8 lists sixteen; 13 (NERVIS disappears), 14
      (Clarvis on direct providers), 15 and 16 have never been scored that way.
      Not closeable until the fourth pairwise gate exists (M14), the two
      person-dependent scenarios are reproved against current code, and every
      one of §8's scenarios — not the twelve that were scored — has evidence
      against real services.*
- [x] The failure/degradation matrix passes with no unsafe failover.
      ***Signed 17 September 2026, by the owner's decision, on this sentence's own words.***
      *`tools/check_degradation.py` passes: 19 of 19 conditions handled with anchored evidence,
      and "no automatic failover crosses a constraint" established under all 9 conditions where
      a request can fall over to another model, with the reason it cannot arise written under
      each of the other 10. The two conditions most likely to break things were also rehearsed
      live across the whole stack (`tools/failure_rehearsal.py`). The note below read the item as
      asking for all six outcomes under every condition; the owner signed it on the failover
      clause it names. The other five still stand at: truthfulness under 16 of 19 conditions,
      standalone behaviour under 9, bounded retries and idempotent recovery under 8 each, and
      bounded queues under 6 — not ruled out anywhere yet — and remain worth raising.*

      *Re-scoped 8 September 2026, and still open — for a better reason than
      before. `tools/check_degradation.py` reads 19 of 19 conditions handled
      with evidence that exists and is anchored to real tests. What it also
      now reports, per outcome, is how thin that is. Raised twice on
      9 September, by building the missing proofs rather than by re-reading
      the old ones: truthfulness is established under 16 of 19 conditions,
      bounded retries, idempotent recovery and standalone behaviour under 8
      each, no unsafe failover under 7, and bounded queues under 6. Bounded
      queues stopped there because the remaining conditions have no queue to
      bound, and padding it would defeat the point of counting. This sentence
      asks for all six under every condition, so it cannot be signed on the
      strength of a verdict count.*

      *The second half of the sentence stopped being an assumption the same
      day. Nineteen readers each took one cell, read its cited tests and asked
      separately whether that failure could route around a constraint; three
      real ones came back and were fixed: every `httpx.TransportError` was
      taking `CONNECTION`'s one same-target retry, so a request that had
      already been sent could be sent again and billed twice; a 401 or 403
      whose body happened to match a model-unavailable phrase was reclassified
      into a class that may fall back, shopping a rejected credential to the
      next provider; and `/api/v1/events` stored a claimed
      `source.instance_id` unchecked, so an event could be filed under
      somebody else's editor window. A fourth finding was the gate itself: it
      validated the file path of a citation and never the test name after it,
      so a cell kept passing after its test was renamed.*

- [x] Upgrade, downgrade, backup, rollback and recovery rehearsals pass.
      ***Signed 17 September 2026, by the owner's decision.*** *Rollback and recovery were
      rehearsed live that day: SIRVIS, RAVIS, NERVIS and the protocol package rolled back to the
      previous release and forward again with no row lost, no job rerun and no secret printed, and
      each `restore-database` brought a damaged copy of its live database back whole (operator
      runbook, *Rolling the stack back to an earlier release*). Upgrade, downgrade and backup were
      rehearsed across each service's newest database format change, on copies of the backups the
      live services wrote before it (`tools/upgrade_rehearsal.py`): upgraded without loss, the older
      release refusing the converted data, restored exactly, run and upgraded again. Clarvis's
      rollback passed on 6 September (below). A downgrade across a format change was rehearsed on
      copies, not on the live databases, by design.*

      *Partial, before that: Clarvis's rollback rehearsal passed for real on 6 September 2026,
      against a live daily-driver VS Code workspace with real conversation history
      and real provider keys already in it. `krimkerre.clarvis@0.12.6` was
      force-downgraded to 0.12.3, the workspace reopened, and both stores were
      hashed and diffed rather than eyeballed: `context.secrets`
      (`clarvis.model.key.anthropic`/`.openai`/`.openrouter`,
      `clarvis.fishAudio.key`) came back byte-for-byte identical, and
      `context.workspaceState` (`clarvis.chat.current`/`clarvis.chat.history`) kept
      all 3 pre-existing history sessions intact with the pre-test current session
      correctly archived, not lost. Full detail and the desktop-vs-code-server
      caveat are in `clarvis/docs/code-server-matrix.md`'s rollback section, now
      graded `PASS`. The extension was restored to 0.12.6 afterward from a
      folder-level backup, confirmed byte-identical. This is one product's rollback
      story, not NERVIS/RAVIS/SIRVIS's or a recovery-from-backup rehearsal, so the
      item stays open.*
      *Extended, same day: NERVIS, RAVIS and SIRVIS each have a real
      `storage/database.py` backup/restore mechanism — SQLite's own online
      backup API, a `.v{N}.bak` beside the database before every migration
      (NERVIS's own `nervis.db.v6.bak` through `.v10.bak` sit on disk from real
      use), `restore_backup()` to put one back, and a build refusing a
      database from a newer schema rather than using it. 59 tests rerun clean
      across the three (`nervis/tests/test_storage_backup.py`,
      `ravis/tests/test_storage.py`, `sirvis/tests/test_storage_backup.py`,
      plus each product's CLI tests), covering exactly §13's "back up before
      migration" and "never downgrade across an incompatible migration without
      a restore." SIRVIS's immutable-provenance claim is what
      `tools/pairwise_check.py`'s tombstone cells already prove (13/13,
      reran under the pairwise/E2E item). Two clauses have no such evidence:
      §13's "RAVIS never reroutes queued requests during recovery" describes a
      persisted request queue RAVIS does not have — it holds a per-request,
      in-memory fallback chain (`ravis/src/ravis/reliability/attempts.py`),
      nothing that survives a restart to reroute — so the clause is
      architecturally moot rather than tested; and the full failed-rollout
      sequence (stop new work, preserve logs, disable flags, drain jobs,
      restore, verify, resume) has never been rehearsed as one drill, only in
      separately-tested pieces. Not closeable until that drill is run or the
      RAVIS clause is either dropped or given something to actually test.*
- [x] Compatibility matrix, operator runbook, release notes and known limitations are
      published. *Release notes are `RELEASES.md`, enforced by `tools/check_releases.py`:
      each component's version is read from its own manifest, so a bump with no note fails
      the gate. Known limitations are published as gates rather than as a list — §10's
      matrix, §13.4's pairwise states, and the milestone states themselves now that every
      row carries one — so they are counted rather than remembered. The compatibility matrix is `tools/check_compatibility.py`, which
      prints it and fails when a peer ships outside the window NERVIS declares — printed
      rather than filed, because a table in a document is a copy that goes stale. All three
      peers are judged, since Clarvis 0.12.6: the Bridge was the one that published no
      product version, and now claims one at registration.*
      *Reverified 6 September 2026: `tools/check_releases.py` and
      `tools/check_compatibility.py` both rerun clean — 5 components ship a
      version with notes, and all three peers ship inside the window this
      NERVIS declares. Known limitations' three gates (§10, §13.4, milestone
      states) were each independently reverified earlier in this same pass
      through §15. "Operator runbook" is the one clause of the four that had
      never actually been examined until now, and it does not have one
      dedicated document at the time: `README.md`'s "Running it" section and
      `ECOSYSTEM_RUNBOOK.md` itself covered getting the ecosystem running and
      reading its published state, but neither was named the operator
      runbook, and there was no troubleshooting or incident-response content
      anywhere — confirmed by grep, not assumed.
      Closed the same day: `OPERATOR_RUNBOOK.md` is now that document —
      health vocabulary and the Clarvis instance registry, the real
      `restore-database` CLI commands (verified working on all three Python
      services) plus the tested Clarvis rollback procedure, and a table
      covering all 19 of `tools/check_degradation.py`'s conditions with what
      an operator should actually do for each, honestly marked where the
      underlying evidence is designed-but-not-yet-proven-live rather than
      overstating it.*
- [x] §14's gates pass for every product before every push: complexity, lint, types and tests.
      *Reworded 6 September 2026 to match §14.1's own updated wording — these gates run
      locally, not in CI, because GitHub Actions is deliberately disabled on both
      repositories for cost (`gh api .../actions/permissions` on NERVIS-ecosystem and on
      `clarvis` both return `"enabled":false`). The prior wording asked for something the
      operator's own cost decision makes permanently false, which is what kept this line
      re-flagged by outside audits with nothing to act on. What the gates themselves show,
      reverified the same day: `ruff check` and `mypy` clean on all four Python components
      (`nervis`, `ravis`, `sirvis`, `protocol`), `eslint` and `tsc --noEmit` clean on
      Clarvis, and every test suite green — protocol 62, ravis 970, sirvis 469, nervis 891,
      Clarvis 1322, 3714 tests total, zero failures. `.github/workflows/checks.yml` and
      `clarvis/.github/workflows/ci.yml` name the identical gates and would run them
      automatically the day Actions is re-enabled.*

The ecosystem is accepted only when every checked item links to reproducible evidence.
"Implemented" without a passing exit criterion is not completion.

---

### 15.1 The stabilization track, and where it went

An independent security and correctness audit of the shipped ecosystem, 2026-09-03,
found twelve defects against contracts already written elsewhere in this document.
They were closed one narrow patch at a time between 3 and 5 September 2026, each with
the regression test that demonstrated the defect first, and this section held the list
while that was in progress.

**The list is gone because it was temporary and is finished, not because it was
abandoned.** Its items are cited by number across the product documents — "§16 item 5"
in `RAVIS.md` and `SIRVIS.md`, among others — and those citations still mean something:
item *n* of the stabilization track, whose evidence is the `STATUS.md` entry of that
date. Nothing else in this runbook is written to expire, and a temporary section that
outlives its work becomes exactly the drift §14 exists to prevent.

What it closed, in one line each: the Clarvis Bridge trust boundary; loopback-only
containment across all three Python services; zero HTML injection in the dashboard;
administration separated from inference in RAVIS; `Host`/`Origin` validation; complete
input validation before routing or benchmarking; truthful effective conditions on
measured evidence; one fencing helper for every model-facing evidence block; a real
`/ecosystem/events` stream with replay and expiry; one conformance suite all three
services pass on their own routes; every gate green with milestone states enforced as
a ratchet; and a golden-path acceptance run driven live against the running ecosystem,
no mocks, which is `tools/acceptance_run.py` and is repeatable.
