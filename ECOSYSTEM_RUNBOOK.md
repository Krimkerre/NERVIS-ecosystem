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
| **RAVIS** | Provider adaptation, eligibility and routing, virtual profiles, local-runtime coordination, route explanations, sessions, usage and cost | Benchmarking hardware, editing workspaces, displaying the ecosystem |
| **CLARVIS** | Coding-agent behaviour inside one workspace: tools, approval gates, agent loop, conversation and workspace state | Model selection across vendors, benchmark truth, ecosystem visualisation |
| **NERVIS** | Registry, dashboard, diagnostics, event hub, traces, general RAVIS-backed chat, control surfaces it is explicitly granted | Any peer's business logic; any peer's safety decisions |

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

---

## 3. Repository and change strategy

**Two repositories.** Clarvis is one: different language, different runtime, shipped as a
`.vsix` on its own cadence, and `clarvis/plan.md` is normative for it in a way no document here
is. SIRVIS, RAVIS and NERVIS share the second, alongside the protocol package, these documents
and the template.

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
  choice carrying TLS *and* authentication once §16 item 2 has built and proven it — the
  requirement is unchanged, its enforcement is now honest about not existing yet.
- Authenticate service-control operations separately from read-only diagnostics.
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

**Recovery gate:** a rehearsed rollback returns the ecosystem to the last compatible set,
with no lost accepted work, no duplicated execution, no secret exposure and no weakened
Clarvis boundary.

---

## 14. Engineering standards

One standard for all four products, stated once here because a coding rule copied into four
documents drifts exactly like a contract copied into four documents. Product documents add
deltas; they do not restate this.

### 14.1 What is enforced, and by what

**A rule nobody checks is a preference.** Every mechanical rule below is a CI gate, and the
gate is the authority — not this prose.

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
  criterion and passes in CI. Not yet demonstrated against real running services.
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

A product document may not soften this to a single completion mark for convenience (§14.7); it
may only add product-specific detail beside the required state.

---

## 15. Whole-ecosystem acceptance checklist

- [ ] All four repositories build and run independently.
- [ ] The non-invention rule appears in every app document and in agent working instructions.
- [ ] MEP schemas, fixtures and versions are released and pinned.
- [ ] Health, identity, capabilities, version, events, traces, request/session IDs and error
      envelopes conform.
- [ ] SIRVIS provenance distinguishes `MEASURED`, `ESTIMATED` and `UNKNOWN` end to end.
- [ ] Runtime Sets and multi-model evidence are represented without invented semantics.
- [ ] RAVIS enforces hard constraints before preferences, and explains routes and rejections.
- [ ] OpenAI-compatible streaming, cancellation, tools, errors and usage pass contract tests.
- [ ] `clarvis-chat` and `clarvis-agent` are independently configurable and tested.
- [ ] Clarvis lifecycle, workspace containment, approval gates, provider abstraction and
      SecretStorage are unchanged.
- [ ] Multiple Clarvis instances remain isolated.
- [ ] NERVIS negotiates actual capabilities; no UI assumes a missing API.
- [ ] NERVIS cannot bypass a Clarvis or RAVIS safety gate.
- [ ] code-server compatibility is evidenced for every supported matrix cell.
- [ ] Pairwise and full E2E suites pass against real services.
- [ ] The failure/degradation matrix passes with no unsafe failover.
- [ ] Logs, events and traces are correlated, bounded, redacted and optional to core operation.
- [ ] Security, threat-model and privacy gates pass.
- [ ] Upgrade, downgrade, backup, rollback and recovery rehearsals pass.
- [ ] Compatibility matrix, operator runbook, release notes and known limitations are published.
- [ ] §14's gates run in CI for every product and pass: complexity, lint, types and tests.

The ecosystem is accepted only when every checked item links to reproducible evidence.
"Implemented" without a passing exit criterion is not completion.

---

## 16. Stabilization track (temporary — closes and is deleted when complete)

Opened following an independent security and correctness audit of the shipped ecosystem,
2026-09-03. Every item below is a defect against contracts already written elsewhere in this
document — most of §9 does not change; the code does. This section exists only to sequence and
track closing that gap, and is removed once every box is checked, its evidence appended to
`STATUS.md`, and §15's "Security, threat-model and privacy gates pass" line points to that
evidence instead of to this section.

**Working rule, every item below:** one small, independently reviewable patch. Write the
regression test that demonstrates the defect, confirm it fails for the intended reason, apply
the narrowest fix, run the component's full gate suite, perform the live check where the
acceptance condition requires one, and only then correct the specification or status text that
was wrong. Never combine a security fix with a refactor, a dashboard restructure or feature
work in the same patch (§3's change-order discipline applies here without exception).

- [x] **1. Clarvis Bridge trust boundary.** *(LIVE VERIFIED 2026-09-04 — see `STATUS.md`.)* `clarvis.bridge.enabled`, `.nervisUrl`,
      `.enrollmentSecretPath` become machine-scoped (VS Code `application` scope, not merely
      Restricted Mode's list) so no workspace can set them at all. Bridge startup refuses an
      untrusted workspace regardless. `nervisUrl` accepts loopback only. The enrollment secret
      must be a regular file, mode `0600`. Its path may be logged; its contents never are,
      before or after a validation failure.
- [x] **2. Loopback-only containment, all three Python services.** *(LIVE VERIFIED 2026-09-04 — see `STATUS.md`.)* Refuse a non-loopback bind
      outright rather than pass unproven TLS arguments — a remote mode that looks encrypted and
      isn't is worse than no remote mode, which is the defect being closed. Real remote
      operation (TLS actually wired to the listener, mandatory per-request authentication,
      Host/Origin validation, SSE held to the same rules as ordinary HTTP, positive tests
      against a real TLS listener) is a separate, later patch, not a rider on this one.
- [ ] **3. NERVIS zero-injection.** Replace unescaped interpolation at every HTML-construction
      site with DOM nodes/`textContent` or one reviewed escaping layer. Set
      `injection_check.js`'s `CEILING` to `0`. Tests: attribute breakout, element insertion,
      `<img onerror>` and equivalent payloads, malformed markup, on every affected screen.
- [ ] **4. RAVIS administration/inference separation.** An explicit service-control permission
      gates every mutation (providers, model filters, pools, policies, budgets, credentials).
      Loopback origin alone must stop granting it. Anonymous and ordinary inference credentials
      get 403 on every management mutation; authorized administration stays audited and tested.
- [ ] **5. Host/Origin/CSRF.** SIRVIS's gap is confirmed (`security.py`'s own comment admits the
      missing CSRF token). Verify whether RAVIS shares it before fixing it there — unconfirmed,
      not assumed. Where it applies: validate `Host`, validate `Origin`, require an explicit
      CSRF token on browser mutations, require an approved non-simple content type, apply the
      same rules to event streams and control endpoints alike.
- [ ] **6. RAVIS/SIRVIS input validation.** Validate complete payloads — shapes, message/tool
      entries, numeric ranges, enum values, session membership, context/lease bounds, body and
      list size limits — before routing, benchmarking or config conversion. Malformed input
      always returns the canonical structured 400, never a 500 or 502.
- [ ] **7. SIRVIS effective-condition truthfulness.** Before reusing a resident model, compare
      requested against effective configuration; reject incompatible reuse or report the
      effective values explicitly; store both alongside the result; mark a run invalid when its
      required conditions weren't met. Implement real `wait`/`preempt` behaviour or remove those
      labels from the shipped contract — a label with no behaviour behind it is the same defect
      as an optimistic milestone tick (§14.8).
- [ ] **8. Retrieved-content fencing, generalized.** One shared fencing helper, not per-surface
      special-casing, covering every model-facing evidence block: files, diffs, search results,
      diagnostics, terminal/test output, learned notes, web results — not only the two gaps this
      audit happened to name. The wrapper states source and provenance and that the content
      cannot approve an action, select a tool, supply a command, change provider or model,
      widen access, or override instructions — backed by a code-level guarantee that no path
      treats fenced content as a command, not by the fence text alone.
- [ ] **9. Canonical `/ecosystem/events`.** Replace the heartbeat-only route with a real stream:
      genuine `id`/`event`/`data` frames, `Last-Event-ID`, replay, expired-cursor `409`, bounded
      subscriber buffers, gap events after overflow, authentication and Origin policy. NERVIS's
      existing private event stream becomes the canonical implementation or a thin alias to it.
      **Largest single item here — size and schedule it separately, not inside a stabilization
      sprint cadence.**
- [ ] **10. Shared event/error/capability conformance.** The protocol package validates every
      mandatory envelope field, semantic versions, the four capability states and their reasons,
      the canonical error envelope including `retryable`, correct 400/401/403/409/415/429/500/
      503 mapping — proven by one production-route conformance suite all three services pass,
      not a standalone helper test.
- [ ] **11. Gates back to green, milestones reconciled.** Fix rather than waive: NERVIS
      telemetry's failure-to-`Unknown` behaviour (confirmed — `sample_system` calls
      `psutil.virtual_memory`/`swap_memory` with no surrounding `try`/`except`, contradicting
      `SystemSample`'s own documented intent), and every other gate this audit named, each
      triaged on its own merits rather than assumed cheap. Update `check_plans.py` to enforce
      §14.8. Apply those states only to rows independently reverified as wrong, not the audit's
      list at face value — this document already has one incident of a number copied without
      being run (§14.6).
- [ ] **12. Golden-path live acceptance run.** SIRVIS measures a model with truthful
      requested/effective conditions; RAVIS selects it and explains every candidate, exclusion
      and winning factor; Clarvis performs a contained, undoable task through that route; NERVIS
      shows live progress and the joined trace; restarting a component produces accurate
      degraded state and recovery; the evidence is inspectable without exposing credentials,
      prompts or private paths; direct-provider and Bridge-disabled paths still work. One
      repeatable procedure, no mocks. This closes the section.

Closing this section requires every box checked, its evidence in `STATUS.md`, and §15
re-pointed at that evidence. Nothing here is satisfied by editing a status line without the run
behind it — the exact failure this track exists to end.
