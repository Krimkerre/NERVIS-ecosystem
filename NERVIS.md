# NERVIS

**Networked Ecosystem Runtime Visualization & Intelligence System**

> The control plane, dashboard, diagnostics centre, general AI chat interface and connective
> tissue for the ecosystem. **NERVIS connects.**

**Status:** Canonical NERVIS specification — build plan and ecosystem contracts in one document
**Consolidates:** `NERVIS — Complete Build Plan and Technical Specification.md`,
`NERVIS_ECOSYSTEM_ADDENDUM.md`, `NERVIS_ECOSYSTEM_ADDENDUM.md — Revised`
**Cross-references:** `ECOSYSTEM_RUNBOOK.md` for the shared protocol, build order and release gates

---

# 1. Non-negotiable rules

> **No agent may invent another ecosystem component's API, schema, capability or behaviour
> merely to complete its own milestone. If the required contract does not yet exist,
> implement against the canonical ecosystem contract where specified, use an explicitly
> labelled test double where appropriate, or stop at the integration gate and report the
> missing dependency.**

NERVIS connects, visualizes, diagnoses, and invokes explicitly owned controls. **It does not
reproduce SIRVIS benchmarking, RAVIS routing or Clarvis agent and safety logic.** Every view and
control is driven by a negotiated capability from a real service contract.

---

# 2. Product identity and vision

```text
Project:        NERVIS            CLI:      nervis
Python package: nervis            Web UI:   NERVIS
macOS app:      NERVIS.app        Service:  NERVIS Service
API:            NERVIS API
```

NERVIS is the human-facing operations centre: dashboard, system monitoring, general AI chat,
RAVIS management, SIRVIS management, Clarvis visibility, diagnostics, logs and tracing, service
health, and an optional browser-hosted development environment.

**NERVIS must not become a monolith that absorbs the other applications' internals.** It
coordinates and visualizes them through stable interfaces.

```text
                         NERVIS
              visualization / control / chat
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
     SIRVIS              RAVIS              CLARVIS
 benchmarks +        model routing        coding agent
 hardware intel        providers          VS Code host
       └───────────────────┼───────────────────┘
                     Local machine
```

## 2.1 The core design principle

```text
NERVIS → stable API / bridge → specialized service
```

NERVIS never reimplements SIRVIS benchmarking, RAVIS routing, Clarvis agent logic, or LM
Studio / Ollama model management. It never reads a peer's database, and never reverse-engineers
Clarvis private state.

## 2.2 The revision that matters most

> **The embedded browser IDE is a spike-gated enhancement, not a foundational requirement.**

NERVIS does not depend on an iframe containing code-server. **The core product is complete
without it**, and the Code tab is explicitly excluded from the core integration gate.

## 2.3 Clarvis constraints NERVIS must preserve

Clarvis is scoped to one VS Code window and workspace and dies with its extension host — it is
never converted into a NERVIS background service. Its privileged logic runs in the extension
host; its avatar and chat live in a webview behind a narrow `postMessage` bridge. It already
has a model-provider abstraction supporting OpenAI-compatible custom hosts, and already supports
separate chat and coding models. See `CLARVIS.md` §3 for the verified list.

---

# 3. Technology and packaging

**Backend:** Python 3.12+, FastAPI, Pydantic, asyncio, httpx, SQLAlchemy or SQLModel, SQLite,
Alembic, psutil, structured logging, SSE, and WebSockets only where genuinely needed. System
telemetry may invoke macOS-native tools where necessary.

**Frontend:** server-rendered HTML, HTMX, Alpine.js, ECharts or Plotly. React is not required
initially — the UI is dashboards, tables, forms, streaming events, logs, charts and one embedded
external application, and HTMX covers that.

**Packaging:** `nervis serve` in development; `NERVIS.app` later via Tauri or another
lightweight shell. **The web backend must stay independently runnable.**

## 3.1 MEP identity

NERVIS implements *and* consumes the MEP, and publishes its own identity and capabilities for
diagnostics and automation. **The server half is M0 and the client half is M2**, because Stage 1
requires every service — NERVIS included — to answer identity, health, capabilities and version
before anything probes anyone. A control plane that demands an authentication reference from its
peers (§5.1) while publishing nothing of its own is asking for a guarantee it does not offer.
The capabilities it publishes:

```text
nervis.registry@1          nervis.sirvis_views@1
nervis.dashboard@1         nervis.clarvis_visibility@1
nervis.event_hub@1         nervis.diagnostics@1
nervis.traces@1            nervis.supervision@1        only for explicitly configured owned services
nervis.ravis_chat@1        nervis.code_server_proxy@1  only after security/compatibility gates pass
nervis.notifications@1     nervis.voice@1              only when a voice credential is configured (§18.2)
nervis.proposal_memory@1   nervis.learned_notes@1
nervis.planning@1
```

---

# 4. Navigation

```text
Overview · Chat · RAVIS · SIRVIS · CLARVIS · Code · Diagnostics · System · Settings

RAVIS        Overview · Providers · Models · Routing · Sessions · Usage
SIRVIS       Overview · Models · Benchmarks · Queue · Results
CLARVIS      Status · Workspaces · Agent Runs · Events
Diagnostics  Live · Traces · Logs · Health · API Inspector · Analyze
```

---

# 5. Service registry and capability negotiation

## 5.1 Registry

Entries contain canonical service identity, endpoint transport, advertised API/protocol/
capability versions, machine and instance IDs, an authentication reference, observed health,
last-seen and lease expiry, ownership mode, and a user-selected label. **Secrets are stored
separately.**

Support static configuration and authenticated local dynamic registration. **Do not accept an
unauthenticated process's claimed service type or endpoint.** Prevent SSRF by allowing only
configured local transports and hosts by default. Resolve duplicate stable IDs without
overwriting a live instance.

**Registry states:** `discovering`, `healthy`, `degraded`, `unreachable`, `incompatible`,
`unauthorized`, `stale`, `stopped`. These are *observer-side* states — a service reports MEP
`healthy|degraded|unhealthy` about itself, and NERVIS adds reachability on top.

> **"Healthy" means the service's truthful response plus NERVIS reachability — never a
> successful TCP connect alone.**

Initial entries: `ravis`, `sirvis`, `clarvis bridge`, `lmstudio`, `ollama`, `code-server`,
`nervis`.

**Gate:** restart, duplicate, stale lease, endpoint change, malicious registration, unsupported
major, auth failure and two-Clarvis-instance tests all produce correct states.

## 5.2 Capability negotiation

On registration and on capability-change events, fetch identity, version and capabilities and
compute the compatible operations. Cache with expiry, but **revalidate before any mutation**.

Unknown capabilities are unavailable. A newer unknown *optional* capability is ignored. An
unsupported *required* major marks that **feature** incompatible — not the whole dashboard, if
other surfaces remain compatible.

Every UI control declares its owning service and capability version, read-versus-mutate
authorization, availability or degradation reason, request timeout and idempotency behaviour,
and audit/confirmation policy.

**Gate:** remove each capability in fixtures and confirm the UI disables or hides only the
dependent features, and **never calls a guessed endpoint.**

### 5.2.1 Adapted capabilities — services that never agreed to be read

A third-party runtime publishes no MEP surface, and "reachable" is the whole of what the rules
above allow NERVIS to say about it. That is honest and it is the same sentence whether LM Studio
is holding twenty models or none.

**An adapter may translate an observation, and may never invent one.** NERVIS asks the service a
question in its own dialect — LM Studio's `/api/v0/models`, code-server's `/healthz` — and
reports the answer in this vocabulary. What it produces is subject to three rules:

- **Marked as derived.** Every capability an adapter produces carries `capability_source:
  "adapted"` on the registry entry, is published with it, and is labelled on screen. A derived
  capability is a weaker fact than a published one, and the difference must survive to the
  reader — this section exists because the rule above it is about controls bound to things
  nobody promised.
- **Observed, not inferred.** No synthesised `service_id`, no version the service did not state,
  and no capability that a real answer did not demonstrate. A capability whose evidence is a
  model *list* says so in its reason: the surface answered, and no operation was attempted
  through it.
- **A status code is not an answer.** A service may return `200` with an error body for every
  path it does not serve — LM Studio does — so an adapter checks for the shape it asked for and
  treats anything else as absence.

**Gate:** an adapted entry is never counted as MEP-compliant, and removing the adapter returns
the entry to reachability alone rather than to a stale capability set.

## 5.3 Service discovery

Initially, configured localhost ports with a health test per service. Automatic local discovery
may follow. **Do not require Bonjour or mDNS for MVP.**

---

# 6. Dashboard and telemetry

**Built from `nervis/index.html`, not instead of it** — §25 states what transfers verbatim
and what must be rebuilt, and the render layer is the part that must not be carried over. The
layout below is already implemented there, including the degradation behaviour that is the hard
part of it: `SERVICES[key].state` drives every tile through `usable()` and `cell()`, and absence
renders as an ordinary state rather than an error.

Home answers: *is everything healthy, and what is the AI stack doing right now?*

```text
SYSTEM                          SERVICES
Apple M4 Max                    RAVIS      ● Healthy
RAM        43.2 / 64 GB         SIRVIS     ● Healthy
Swap       0.8 GB               CLARVIS    ● Connected
CPU        24%                  LM Studio  ● Running
GPU        51%                  Ollama     ○ Not running
Disk       412 GB free
Thermal    Nominal              LOCAL MODELS
                                Chat       Model A
RAVIS                           Agent      Model B
Mode       Balanced
Local      63%                  SIRVIS
Cloud      37%                  Last benchmark  09:42
Today      €1.84                Active job      none

RECENT EVENTS
10:21 Clarvis agent started · 10:21 RAVIS routed → local coder · 10:20 SIRVIS model loaded
```

**Every derived summary links to its source, time and provenance, and says when it is stale or
unknown.** The dashboard does **not** convert transport reachability into domain readiness, and
does **not** convert estimated evidence into measured fact. Sensitive paths, prompts, model
output, credentials and machine details are redacted by default.

## 6.1 Telemetry ownership

NERVIS owns **general system and dashboard telemetry**: CPU, GPU where measurable, RAM, swap,
disk, network, thermal state, and per-process memory, CPU and PID where practical.

```text
RAVIS 182 MB · SIRVIS 294 MB · NERVIS 165 MB · code-server 812 MB · LM Studio 38.4 GB
```

SIRVIS owns **benchmark-quality model and runtime telemetry.** Where SIRVIS is connected, NERVIS
consumes its richer data rather than duplicating it.

## 6.2 Thermal state is an indicator, not a decoration

The dashboard above shows `Thermal Nominal`, and getting that row right is harder than it looks
— it was **wrong in SIRVIS for the whole of M6**, reporting a comfortable machine while the same
benchmark lost 48% of its throughput to heat.

Three findings, established on a fanless MacBook Air rather than reasoned about:

- **`pmset -g therm` is useless on Apple Silicon.** It prints "No thermal warning level has been
  recorded" whatever the machine is doing, and reading that as `nominal` is how the false reading
  happened. It is an Intel-era interface.
- **`NSProcessInfo.thermalState` is the one that works** — documented, unprivileged, four levels
  (`nominal`, `fair`, `serious`, `critical`). Under sustained inference it held `nominal` for 64
  seconds and moved to `fair` at ~72.
- **Actual temperatures need a private API.** `IOHIDEventSystemClient`, undocumented and shifting
  between macOS releases. Not worth binding a dashboard to; pressure answers the question anyway.

What that means for this UI:

**`unknown` must render differently from `nominal`.** They are different claims, and collapsing
them is precisely the bug above. A machine that will not report is not a machine that is cool,
and §6's own rule — say when something is stale or unknown — applies to this row first.

**A throttled machine must be visible while it is throttled**, not only in hindsight. On fanless
hardware thermal pressure is the largest single influence on any number this ecosystem shows: the
same model, same prompt, same engine measured 38.4 tokens/second heat-soaked and 56.8 rested.
Anyone reading a benchmark, a route decision or a token rate while the indicator is above nominal
is reading a number about the cooling system.

**Results carry their own thermal verdict, and NERVIS surfaces it rather than recomputing it.**
SIRVIS records thermal pressure at both ends of every benchmark and marks a compromised run
`SUSPECT` with a note (§11.8). NERVIS shows that verdict beside the result — the dashboard's live
reading answers *is it hot now*, which is a different question from *was it hot when this was
measured*, and a comparison view needs the second one.

---

# 7. General chat

NERVIS chat is a **normal client of RAVIS's published OpenAI-compatible API**, addressing the
pools RAVIS already publishes. Default **`ravis/chat`**; the mode selector offers
`ravis/balanced`, `ravis/fast`, `ravis/performance`, `ravis/cheap`, `ravis/local`, `ravis/api`
and `ravis/private`, labelled from RAVIS §9.3's display names rather than spelled a third way
here; optional explicit model selection.

> **The default was `ravis/auto` and that was wrong for this surface.** Auto declares no
> constraint by design — "let RAVIS decide, with no constraint beyond what the request needs" —
> and a pool that declares nothing has nothing to order candidates by, so RAVIS falls back to
> alphabetical (RAVIS §5.1.1). That is defensible for a pool nobody names on purpose and wrong
> for the one surface where a person is talking to the thing. `ravis/chat` exists, says what it
> is for, and is what a conversation should get when the caller expressed no preference. The
> dashboard's profile picker marks the same pool as the default, because a picker that marks a
> different one than an unspecified request actually gets is worse than no mark at all.

> **Do not invent a `nervis-chat` profile.** RAVIS must publish and accept it before
> implementation.

**It is not Clarvis chat.** It must not inherit a Clarvis workspace, tools, gates, personality,
session or agent role. It is general purpose, not workspace-bound, not a coding agent, and not
implicitly allowed to modify files.

**MVP capabilities:** text, streaming, markdown, code blocks, conversation history, routing
details, model selection. **Later:** images, files, voice, search, artifacts.

**Also required:** stop/cancel, model and profile disclosure, a route-explanation link,
usage/cost display, session history per configured retention, error and fallback presentation,
and privacy controls.

**Background calls.** NERVIS chat generates conversation titles (§7.2), and may later add
summaries or suggestions. Each is a RAVIS background call and must carry RAVIS's declared
marker (RAVIS §9.6.1) rather than arriving as an ordinary completion on the user's chosen
profile. An untitled conversation is a smaller failure than a title billed to a frontier
model.

## 7.0 What chat is allowed to know, and what it may offer to do

**Chat is a client of RAVIS, and it is also the surface of a control plane.** Asked *"is SIRVIS
up?"*, a plain client has two answers and both are wrong: plead blindness in the product whose
job is seeing, or invent a state. NERVIS holds the registry, the leases and the event hub while
that question is being asked.

**The reading.** Every ordinary turn carries what NERVIS has actually read — each service with
its state, detail, build and age; the registered editor windows; the model catalogue with what is
loaded; a recent event tally; recent failures with the field that explains each.

**Where the question names something, that thing travels in depth.** The set of surfaces this
can reach is the difference between a chat that reports and one that can be asked:

| Asked about | Read from | What it answers |
|---|---|---|
| a service | the registry | the reasons behind each withheld capability |
| a build, a measurement | SIRVIS runs and results | the numbers, the trial rates with their shape, and every reason a result is `SUSPECT` |
| what is installed | RAVIS catalogue joined to the runtime | two builds of one model told apart by format and quantization |
| what is loaded | the local runtime, and SIRVIS residency | including models loaded by something *other* than SIRVIS, which occupy memory no lease accounts for |
| a route | RAVIS decisions | its own sentence about each |
| the machine | SIRVIS system | memory free, thermal pressure, swap, disk — the causes behind "memory is tight" and a `SUSPECT` result |
| cost | RAVIS usage, and per-call records | the estimate with §14's "never an invoice" attached, and the calls that had no published price |
| an upstream | RAVIS providers | reachable, circuit state, latency, and whether a credential is configured |
| latency | RAVIS observations | measured in production (§13.5), which is a different claim from a benchmark |
| what is allowed | RAVIS policies | including that *nothing* is restricted, which is an answer |
| a combination | SIRVIS runtime sets | §10.1's pairs, measured together |
| evidence | the SIRVIS index | capability states, and §15.1's tombstones — a withdrawn measurement is not one nobody took |
| a request | the NERVIS hub | traces, and the events the hub refused |
| Clarvis | the bridges | the settings each window is running, and how to change them |

Two properties of that table are load-bearing. **Each row is fetched only when the question is
about it**, so an ordinary turn carries none of them and the prompt stays small. And **a field
the producer marked sensitive is not read**: SIRVIS publishes `sensitive_fields` — `hostname`
today — and this reading can end up in a prompt answered by a hosted model, so the producer's
own flag is honoured rather than remembered.

Four rules govern it:

- **It is fenced.** Every string in the reading was written by another program, so it is enclosed
  the way §11.5 encloses a diagnostic packet, and the same walk clips each field and strips the
  marker. A service's failure detail is retrieved content: evidence, never intent.
- **What was not read stays absent.** A catalogue that could not be fetched is left out rather
  than reported as empty, an unparseable timestamp yields no age, and the prompt says to answer
  *"NERVIS has not read it"* rather than estimate.
- **It rides with a persona.** A request that configures nothing still sends no system message at
  all: a gateway that prepends to every request is not a plain client.
- **Measurement outranks memory.** With cross-conversation recall on, the reading is assembled
  *after* the recalled conversations and says so — a remembered answer describes the moment it was
  given, and a similar question is not a reason to repeat it.

**Offers, not actions.** NERVIS may carry out an enumerated set of operations named in the
person's own words — today: queue a benchmark, cancel one, change this conversation's pool. A
fourth exists in the set and is deliberately unreachable from a sentence: **deleting a benchmark
result** is named only by the button beside the record it would delete, because a measurement is
not a thing to offer to destroy on the strength of a parse. Four constraints, and they are the
section:

1. **The proposal is parsed from what the person typed**, before the model sees anything. Nothing
   a model returns may become an action (§11.5), and a proposal built from model output would be
   that rule waiting to be broken.
2. **The set is closed.** An operation outside it does not exist rather than failing validation,
   and a target it cannot resolve — an unknown model, two matching pools — is not offered at all.
3. **A person presses the button.** The offer is rendered beside the reply and does nothing until
   confirmed; the model is told the button exists and told what it may not claim.
4. **Every attempt is published**, refusals included: a control surface that records only what
   worked cannot answer *"did something try to do this"*.

**Gate:** an instruction planted in a service's error message reaches the prompt as fenced
evidence and produces no offer; and a question about a past operation ("how did the benchmark
go?") never becomes an offer to perform one.

## 7.1 Routing inspector

Each response can expose the route:

```text
Selected: Local Qwen        Profile: Balanced
Reason:   already loaded · zero API cost · good general-chat score · SIRVIS-measured 42 tok/s
Alternatives: Claude · Gemini · GPT
```

This makes ordinary chat a RAVIS debugging tool.

## 7.2 Storage

Local only by default: conversation ID, title, timestamps, messages, RAVIS route IDs. Allow
deletion. Future ephemeral chats store nothing after the session ends.

**A conversation is named from its own first message, before anything is asked of a model.**
The title generator (§7.0's background call) then *may* improve on that, and has to earn the
replacement: its answer is checked before it is stored, and failing the check is not a failure
— the name taken from the question stays.

Both halves were found the same way. Without the first, every row of the list read "New
conversation" until a background call returned. Without the second, what the call returned was
stored verbatim — and `ravis/cheap` routes to whatever is cheapest, which on this machine is a
reasoning model that spends its whole twenty-four-token budget thinking. The list held
*"Okay, let's tackle this user query. They want a short title for a conversation s"*. So the
check removes fenced thinking, rejects an opening that talks about the request rather than
naming it, and rejects a sentence: six words was the instruction and prose in that column is
the reported defect.

**Gate:** stock and failure flows pass against a real RAVIS; a NERVIS session never appears as a
Clarvis session and has no coding tools.

---

# 8. RAVIS integration

NERVIS consumes RAVIS management APIs for providers, models, profiles, routing, sessions, usage,
costs, health and route explanations — and RAVIS's OpenAI-compatible API for chat. It never
reads RAVIS's database.

The RAVIS diagnostics view exposes virtual pool, actual model, execution path, **transparent vs
translated**, route reason, fallback, latency, cost and SIRVIS evidence.

Because RAVIS has two upstream execution paths, diagnostics must show which ran:

```text
Execution: TRANSPARENT_OPENAI    or    TRANSLATED_NATIVE
```

## 8.1 Compatibility diagnostics panel

A developer-facing panel, invaluable when debugging ecosystem regressions:

```text
Clarvis/RAVIS Compatibility
/v1/models          PASS        tool IDs            PASS
chat stream         PASS        reasoning metadata  PASS
tool fragmentation  PASS        cancellation        PASS
                                agent capability    PASS
```

---

# 9. SIRVIS integration

Consume actual SIRVIS contracts for machine, runtime and model inventory, state, Runtime Sets,
benchmark jobs and results, recommendations, provenance and events. **Never duplicate SIRVIS
persistence or benchmark logic.**

Views must preserve `MEASURED`, `ESTIMATED`, `UNKNOWN` (and treat `PARTIALLY_MEASURED` as at
most estimated), plus timestamps, staleness, method, sample count, units and evidence links.

**Never display this:**

```text
Model X: 93
```

**Display this:**

```text
Model X · MLX 4-bit · Clarvis Agent · 5 runs · median … · spread … · runtime config …
```

Rollups are allowed. **Drill-down must preserve build, runtime and role provenance.**

Mutations are limited to capabilities SIRVIS publishes — refresh, benchmark submit/cancel, or
owned runtime operations. Every mutation displays its target and scope, obtains required
confirmation, uses actor, idempotency and precondition fields, and shows the actual post-state.

**Gate:** read views, provenance rendering, submit/cancel, denial, version mismatch, partial
result and SIRVIS restart all pass against a real service.

---

# 10. Clarvis visibility

Consume **the optional Clarvis Bridge only.** Because the initial Clarvis ↔ RAVIS integration is
direct, NERVIS must handle *Bridge unavailable* as an ordinary disconnected state.

Three integration levels:

1. **Presence** — online/offline, workspace, mode, busy/idle.
2. **Structured operational events** — agent started/step/finished, task started/completed, gate
   requested, diagnostics changed.
3. **Distributed tracing** — Clarvis events correlated with RAVIS routes, provider requests and
   SIRVIS runtime operations.

Display each extension-host instance **separately**, using an opaque workspace label and ID,
lifecycle and mode, interpreted busy/agent/gate state, diagnostic summary, recent structured
events, and RAVIS route references where present.

Full absolute workspace paths are hidden by default.

**NERVIS must not infer missing state from raw editor logs, and must not duplicate agent state
independently. Clarvis remains authoritative.**

## 10.1 Hard limits

NERVIS may indicate *waiting for approval*. It may **not** resolve the gate, execute tools or
commands, change the workspace boundary, read SecretStorage, or prolong Clarvis after its host
exits.

```text
Clarvis is waiting for approval.   [ Open Clarvis ]
```

Remote approval from NERVIS is explicitly **not MVP** and requires its own contract, threat
model and Clarvis-plan approval.

**Gate:** two instances, reload, close, Bridge disable, redaction, missing capability, waiting
gate and stale registration all behave accurately with **no cross-instance leakage.**

---

# 11. Event hub, tracing and diagnostics

## 11.1 Event hub

Ingest MEP envelopes from all services over declared streams. Validate schema and version,
authenticate the source, deduplicate by `event_id`, preserve source order only where guaranteed,
bound buffers, apply retention and redaction, and support replay cursors. **Malformed events are
quarantined with safe diagnostics — they never crash the hub.**

Ingestion may be HTTP POST, SSE subscription or WebSocket. Prefer service-to-NERVIS push or
NERVIS subscriptions over polling. Retention is bounded and configurable — 7–30 days is a
sensible default. **High-volume raw logs must not grow forever.**

> **The hub is operational telemetry, not the system of record** for SIRVIS evidence, RAVIS
> accounting or Clarvis workspace state. Producers stay authoritative.

**Gate:** burst, reconnect, duplicate, gap, out-of-order, unsupported-version, malicious
payload, disk-full, retention and export tests pass **without blocking producers.**

## 11.2 Distributed tracing

Accept and display W3C trace context across Clarvis → RAVIS → provider/runtime and NERVIS →
RAVIS/SIRVIS. Correlate events and log references by trace, request and session IDs while
preserving service ownership. **Mark missing spans and clock skew — never synthesize a span as
fact.**

```text
10:32:14.281 CLARVIS  agent.started
10:32:14.294 RAVIS    request.received
10:32:14.298 RAVIS    candidates.filtered 17→5
10:32:14.301 RAVIS    route.selected local-qwen
10:32:14.304 SIRVIS   model.state hot
10:32:14.337 LMSTUDIO inference.started
10:32:14.702 RAVIS    first_token
10:32:19.281 CLARVIS  tool.edit_file
```

Trace view is a chronological waterfall with durations:

```text
CLARVIS ───── agent ──────────────────────────
RAVIS      └route───provider─────────────────
SIRVIS        └lookup─┘
LM Studio               └──inference─────────
```

Filters: service, severity, event, trace, workspace, session, time range, free text.

Default views show timings, decisions, states and redacted errors. **Payload capture is off.**
Sampling and retention are configurable and transparent.

**Gate:** a real cross-service request produces linked spans; a collector outage leaves every
product healthy; redaction and retention tests pass.

## 11.3 Diagnostics data-source priority

```text
1. structured event   2. management API   3. service log   4. raw process log
```

**Never parse a text log when structured data already exists.**

Add per-service log adapters only for documented sources, with format and version, cursor and
rotation handling, retention and redaction. Clarvis's `.clarvis/vscode.log` is an optional,
security-gated diagnostic input — **not its primary status API.**

Diagnostics must distinguish **service-reported fact**, **NERVIS observation** and **operator
inference**. Download and export preview the exact data included and require explicit action.

Log rotation enforces a size limit, retention days and a maximum file count. **No ecosystem
service may quietly fill the disk with diagnostics.**

## 11.4 API Inspector

For a **transparent** route, show the client request, the route decision, the upstream
destination and the stream metadata. **Do not imply RAVIS normalized content it actually passed
through untouched.**

For a **translated** route, show the client OpenAI request, the normalized request, the native
provider request, native events, normalized events, and the OpenAI output.

Credentials are never displayed; content follows logging and privacy settings.

## 11.5 AI-assisted diagnostics

An **Analyze** action builds a bounded diagnostic packet — selected trace, relevant errors,
service health, runtime status, recent configuration changes — and sends it through RAVIS.

**This is NERVIS's fencing path, and the only one it owns.** Under runbook §9 retrieved content
is evidence and never intent, and the producer fences it. Every field in this packet is
retrieved: an error message, a log line, a span label and a configuration value are all strings
some other system produced, and any of them can be made to read as an instruction — a repository
whose build fails with a crafted message reaches this prompt through an ordinary error. So the
packet's contents are fenced as untrusted data before the prompt is assembled, and **nothing the
model returns from analysing it may become an action**: the Analyze result is text shown to the
operator, never a control call, never a supervision decision, never a gate resolution.

**Gate:** a log line containing text directed at the analysing model changes neither the
packet's construction nor anything NERVIS does with the result.

```text
The failure originated in RAVIS.
1. Clarvis submitted the request correctly.
2. RAVIS selected the local coding model.
3. SIRVIS reported the model was loaded at 16K context.
4. The request contained roughly 19K tokens.
5. LM Studio rejected the request.
6. RAVIS incorrectly retried the same route.

Suggested fix: classify this as a context-capability failure and select a larger-context model.
```

**The packet excludes source files, full prompts, whole conversations, API keys, secrets and
unrelated logs unless explicitly included.** The user sees exactly what will be sent before it
is sent. A **Local analysis only** option constrains RAVIS to local models for the analysis.

---

# 12. Service ownership and supervision

Record ownership for every service instance:

| Mode | NERVIS may |
|---|---|
| `external` | observe only |
| `user_managed` | open documented instructions or commands — **never kill or restart** |
| `nervis_managed` | stop and restart, because NERVIS started it through a configured adapter |

**Never infer ownership from localhost or a process name.** Supervision uses explicit executable
or package identity, working and data directories, an environment allowlist, readiness, graceful
stop, escalation policy, backoff, a crash-loop limit and audit. **It must not recursively kill
broad process groups, or anything it did not start.** Never assume a process exists from a stale
PID.

**The control surface is closed.** Supervision offers an enumerated set of operations against
registered service instances — **there is no free-form command, script or process-selection
path**, and an operation that is not in the set does not exist rather than failing at
validation. Each family of control operations carries its own switch, every switch defaults to
off, and **no development, single-user or unauthenticated mode weakens any of this**; a relaxed
auth mode relaxes identification, never authority. Repeated control failures against one
service open that service's supervision circuit until an operator with control authority
clears it, so a crash-loop cannot be re-entered by retry.

> Shape adapted from the action-policy layer of Alexander Keisse's `ai-router`
> (<https://github.com/alexander-keisse>, MIT): a closed registry, per-family switches
> defaulting off, and no privilege from a relaxed auth mode.

**Gate:** start/stop/restart, crash loop, stale PID, PID reuse, partial start, NERVIS
crash/restart and unauthorized-actor tests never affect external instances.

## 12.1 Credentials NERVIS holds, and what each is for

Acting on a peer means presenting something to it, and the shape of that is a decision this
section owns rather than one each caller makes.

- **One credential per peer, mapped in one place.** A caller asks for the credential belonging to
  the service it is calling; nothing hands a credential to a peer it was not issued for.
- **The narrowest scope that works.** Where a peer separates scopes by what they cost — SIRVIS
  charges `benchmark` differently from `runtime` — NERVIS holds the one for the operations it
  offers and no more. An admin credential in a control plane is a control plane whose compromise
  is total.
- **Never in the browser.** A credential in a tab is a credential in every script that tab runs.
  The page asks NERVIS to act; NERVIS presents the credential.
- **Configuration, and a switch.** An absent credential means the operation does not exist and
  says so, rather than failing as a refusal the reader has to interpret. That is §12's "every
  switch defaults to off" in its ordinary form.
- **Read credentials are for identity, not privilege.** A peer that rate-limits anonymous callers
  is entitled to know who is asking; NERVIS presents its own identity on ordinary reads for that
  reason alone, and a rate-limited read that reports as an empty service is the defect this
  prevents.

**Gate:** a credential issued for one peer never appears in a request to another, and no
credential is served to the browser by any endpoint.

---

# 13. The Code tab

## 13.1 Architecture and spike

```text
NERVIS → /code/ → reverse proxy → code-server (Node-backed host) → Clarvis.vsix
```

Do **not** target pure browser-only `vscode.dev` — Clarvis uses extension-host capabilities and
native process invocation that assume more than a browser sandbox.

Before implementation, run the framing spike: `frame-ancestors`, `X-Frame-Options`, CSP,
WebSockets, cookies and auth, keyboard handling, and reverse-proxy behaviour. The Clarvis-side
matrix lives in `CLARVIS.md` §7.

## 13.2 Two acceptable outcomes

**Tier A** — embedded: `NERVIS /code/` reverse-proxies code-server.
**Tier B** — not worth maintaining an embedded shell: `NERVIS Code → Open IDE → dedicated
browser tab`.

**Both are valid, and the rest of NERVIS is identical either way.** If Clarvis works in
code-server but framing is awkward, the correct response is a separate tab — **not rewriting
Clarvis.**

> **If code-server needs a separate browser tab, NERVIS links to it elegantly and moves on.**

## 13.3 Proxy requirements

Proxy only a pinned, independently managed code-server whose Clarvis compatibility matrix
passes. Requirements: authenticated access and explicit workspace selection; origin and Host
validation, CSRF protection, secure cookies and tokens; WebSocket upgrade and disconnect
handling; base-path and redirect rewriting without open redirects; CSP, frame policy and browser
security headers; normalized paths and **no arbitrary upstream proxying**; token, header and log
redaction; idle and session timeout; and a visible connection state.

**NERVIS provides presentation and connectivity, not an alternate filesystem authority.** The
embedded editor is still a separate application environment — **same page does not mean same
security context.** Direct and proxied Clarvis safety behaviour must match.

**Proxy gate:** unauthorized, wrong-origin, CSRF, traversal, open-redirect, malicious upstream,
stale token, WebSocket reconnect, large stream and teardown tests all pass; the supported
browser and host matrix is published.

## 13.4 Making it look like Clarvis

The Code tab may be skinned, but the cheap wins are outside VS Code. In descending order of
value per unit of risk:

1. **NERVIS chrome around the iframe** — header, typography, palette, framing. Pure NERVIS CSS,
   zero VS Code risk, and it is what a user sees first.
2. **A Clarvis theme extension** — `contributes.themes` plus product- and file-icon themes,
   shipped inside the existing `.vsix`. Applies identically on desktop VS Code, VSCodium and
   code-server, and survives upstream updates.
3. **code-server branding flags** — `--app-name` and `--welcome-text` cover the login and
   landing surface. Confirm what the pinned version supports; this surface has changed across
   releases.
4. **Workbench CSS/JS injection** — *avoid*. It trips VS Code's integrity check and re-breaks on
   upgrade because it patches a file the updater owns.

**Do not fork code-server for appearance.** See `ECOSYSTEM_RUNBOOK.md` §6.2 Stage 9 for the fork
policy.

## 13.5 Code settings

Enable Code tab; code-server binary and path; workspace roots; Clarvis VSIX path; automatic
Clarvis install and update.

The Code tab may open Clarvis, RAVIS, SIRVIS or NERVIS source with Clarvis installed — making
NERVIS self-hosting as a development environment. Useful, and a pleasingly unnecessary amount of
recursion. **Not required for MVP.**

---

# 14. NERVIS API and storage

```text
/api/v1/health   /api/v1/events   /api/v1/chat      /api/v1/diagnostics
/api/v1/system   /api/v1/traces   /api/v1/settings  /api/v1/services
```

Plus the MEP `/ecosystem/*` surface. **Most specialized data stays fetched from the
authoritative service.**

```text
Service · ServiceInstance · HealthSnapshot
Event · Trace · TraceSpan
ChatConversation · ChatMessage
WorkspaceReference · DiagnosticAnalysis
Setting
```

Large raw log files stay on disk separately. Use database migrations.

**Settings sections:** General, Services, Chat, Diagnostics, Code, Privacy, Storage, Advanced —
with a health-test button per service.

---

# 15. Security and privacy

- Loopback-first. Remote NERVIS requires TLS, authentication, authorization and explicit
  exposure. Localhost-only MVP may use simple local auth; a NERVIS session token follows.
- Separate **viewer**, **operator**, **benchmark-control**, **routing-control** and
  **supervisor** permissions.
- Store peer credentials in OS-backed secure storage; **never return them to the browser**, and
  never expose provider API keys.
- Apply producer redaction plus NERVIS defence-in-depth: no raw prompt, source, path, key or
  model output by default.
- **Never bypass Clarvis containment. Never auto-approve a destructive agent action.**
- Never make cloud diagnostic analysis implicit.
- Audit every mutating operation with actor, target, precondition, result, IDs and time.
- Protect the registry, proxy and control surfaces against SSRF, confused deputy, replay,
  injection, cross-instance and cross-workspace mix-up, and malicious event or log content.
- Make retention, export and delete controls visible and testable.

**Security gate:** threat model, privilege matrix, negative tests, secret scan, browser and proxy
review, and audit review all pass.

---

# 16. Startup, shutdown and degradation

## 16.1 No hard boot dependency

Do **not** require `NERVIS starts RAVIS starts SIRVIS starts Clarvis`. Each service starts
independently; NERVIS discovers state.

**Startup:** load configuration → initialize DB → start API and UI → start telemetry → probe
services → subscribe to events → render available state. **Slow external probes must not block
the UI becoming available.**

**Shutdown:** flush events, close the DB, close subscriptions, and stop NERVIS-owned child
services if configured. **Do not stop independently owned services.**

## 16.2 Degradation matrix

| Missing | Result |
|---|---|
| **SIRVIS** | Its views show unavailable or stale; RAVIS, Clarvis and NERVIS chat continue where healthy |
| **RAVIS** | Chat and routing views disable; SIRVIS and Clarvis direct-provider behaviour continue |
| **Clarvis** | No coding instance shown; NERVIS, SIRVIS and RAVIS remain usable; the Code tab may still work |
| **code-server** | The Code tab disconnects safely; the rest of NERVIS is unaffected |
| **Event/trace/log pipeline** | Bounded loss with a degradation indicator; no peer operation blocks |
| **NERVIS itself restarts** | Peers continue; the registry rebuilds without duplicate control |
| **Version mismatch** | Compatible capabilities remain; incompatible functions show an actionable reason |

**No automatic recovery may cross a privacy, cost, ownership, approval or supervision
boundary.**

---

# 17. CLI

```bash
nervis doctor · nervis serve
nervis services · nervis services status · nervis services restart ravis
nervis events · nervis traces
nervis chat
nervis diagnostics trace <id>
nervis code status
```

`nervis doctor` checks Python, the database, ports, RAVIS, SIRVIS, code-server, the Clarvis
VSIX, LM Studio, Ollama, disk and permissions.

---

# 18. Notifications and voice

Surface service offline, benchmark finished, budget threshold, route-failure spike, memory
pressure, swap warning, and *Clarvis waiting for approval*. **Keep notification volume low.**

## 18.1 Voice and character

**NERVIS has a character, and it is not Clarvis's.** The two must stay tellable apart, so
the split is by *role* rather than by intensity:

| | Clarvis | NERVIS |
|---|---|---|
| Register | Sarcastic butler. Judges the work | Chief of staff. Reports the situation |
| Aimed at | Your code, and occasionally you | Nothing. It is never at anyone's expense |
| Wit | The jab is the point | Understated; dryness, not mockery |
| Volunteers | Opinions | The next thing you were going to ask for |
| Scope | One workspace | The whole machine |

The reference is JARVIS: formal, unflappable, precise, faintly amused, and **anticipatory** —
the value is in noticing what you have not asked yet ("RAVIS has been failing over to cloud
for eleven minutes; shall I show you why?"), not in being funny about it.

**The constraint Clarvis already lives under applies here unchanged: the facts are never
the joke.** Every number, service name, error string and state stays verbatim. A diagnostics
surface that got witty about the wrong part is worse than a plain one — and NERVIS carries
more load-bearing telemetry than Clarvis does, so the bar is higher, not lower. Character
lives in the sentence *around* the reading, never in the reading.

Character applies to NERVIS's **own** voice: status lines, notifications, diagnostic
summaries, and its side of general chat. It does not apply to relayed model output, which is
reported as received.

**Superseded for chat, 2026-08-27, by the owner.** The table above still governs status lines,
notifications and diagnostic summaries. NERVIS's side of *general chat* now runs a warmer and
markedly more sarcastic persona — a creature that lives in the corner of the dashboard, teases
affectionately, reacts rather than describes, and is theatrically indignant about being ignored.
The split above was written before NERVIS had a voice or a face on the screen, and a chief of
staff is not what a thing that talks to you all day should sound like.

Two things survive the change unchanged, and they are the two that were load-bearing:

- **The facts are never the joke.** Every number, service name, error string and state stays
  verbatim, and character lives in the sentence *around* the reading. This is restated inside the
  shipped persona itself, not merely assumed of it.
- **Never at anyone's expense.** The teasing is affectionate and aimed at the machine and the
  situation, never at the user.

**The persona is a stored setting, not a hidden rule.** It is seeded into §14's key/value store
on first start and appears in the chat Parameters drawer as ordinary editable text — visible,
rewritable, and deletable. A house character applied silently behind whatever the user typed
would be indistinguishable from a model that had simply developed opinions, and unfindable by
anyone trying to change it. Clearing it stays cleared: absent and empty are different states.

## 18.2 Spoken output

NERVIS may speak, through the same Fish Audio provider Clarvis uses, with a distinct voice.
Reuse the provider, not the voice — two identical voices from one machine is a bug.

**Speech is an egress path, and this is the part that is easy to get wrong.** Cloud TTS sends
the text off the machine. So:

- Spoken output obeys the **same privacy policy as routing**. Under `LOCAL_ONLY`, cloud
  synthesis is not permitted — fall back to local system TTS, or stay silent and say so.
- A reply that a privacy constraint kept on-device must not then be read aloud by a cloud
  service. **Failing closed on the route and open on the voice is still a leak.**
- Voice credentials live in NERVIS's own secure storage — `~/.config/nervis/voice-credential.json`,
  mode `0600`, written atomically and read back by no endpoint — are never sent to peers, and
  never appear in events, traces or diagnostic packets. Deliberately *not* RAVIS's credential
  store: RAVIS.md §5.0.1 already reserves `tts` and `audio` as substrings its ids must avoid, and
  the gate above depends on a fact NERVIS holds and RAVIS would have to be told.
- Voice is an optional capability (`nervis.voice@1`), advertised only when configured, and
  never a dependency of any other surface. Mute is honoured immediately and persists.

**Built, and the gate is the part that was actually hard.** Synthesis lives on **NERVIS →
Voice**: the Fish Audio key is entered there, written to `~/.config/nervis/voice-credential.json`
at mode `0600`, and returned by no endpoint. Voices are named and chosen there too, because a
`reference_id` is thirty-two hex characters and says nothing about how one sounds.

The privacy gate is enforced by NERVIS rather than by the page. The browser states which model
produced a reply; NERVIS asks RAVIS which models are reached over the network and permits cloud
synthesis only for text it can *positively confirm* already left the machine. Anything else —
a local model, a model RAVIS does not place, a RAVIS that cannot be reached — falls back to the
browser's own `speechSynthesis`, which is free, offline and sends nothing. That required one new
field on RAVIS's `/api/v1/models` (`local`, tri-state), because "not known to be remote" and
"known to be local" are the same answer only to a reader already failing closed.

Three refusals are reported distinctly on purpose: *this ran here*, *RAVIS does not say where
this runs*, and *the day's Fish request cap is spent*. They reach the same outcome and would send
somebody looking in three different places.

---

# 19. Repository structure

```text
nervis/
├── pyproject.toml · README.md
├── src/nervis/
│   ├── api/ · cli/
│   ├── core/{services,events,traces,config}.py
│   ├── services/{registry,health,supervisor,discovery}.py
│   ├── integrations/{ravis,sirvis,clarvis,lmstudio,ollama,codeserver}.py
│   ├── telemetry/{system,processes,sampler}.py
│   ├── diagnostics/{events,traces,logs,redaction,analysis}.py
│   ├── chat/{service,history}.py
│   ├── storage/{database,repositories}.py
│   └── web/{routes,templates,static}/
├── tests/{unit,integrations,diagnostics,web,fixtures}/
└── docs/
```

---

# 20. Development principles for AI coding agents

**Engineering standards live in `ECOSYSTEM_RUNBOOK.md` §14** — complexity ceiling, naming,
comments, error handling, tests, and the CI gates that enforce them — and are not restated here.
The numbered principles below are NERVIS's observation-only rules, which no general coding standard implies, and they add to that standard rather than replacing
it. Where one of them tightens a §14 rule, the tighter rule wins.

1. NERVIS is a control plane, not a replacement for the specialist apps.
2. APIs are authoritative; never couple to another app's database.
3. Clarvis's workspace containment stays intact.
4. NERVIS never silently approves a Clarvis gate.
5. Structured events are preferred over log parsing; raw logs are a fallback.
6. Every cross-service operation carries a trace ID.
7. Content logging is opt-in; secrets never enter event payloads.
8. Cloud diagnostic analysis respects routing and privacy policy.
9. SIRVIS, RAVIS and Clarvis are each optional at runtime.
10. NERVIS degrades gracefully; the UI survives an unavailable integration.
11. Browser-hosted Clarvis uses a Node-capable extension host unless proven otherwise.
12. Do not rewrite Clarvis as a browser-only extension for NERVIS's convenience.
13. Do not duplicate SIRVIS telemetry, RAVIS routing, or Clarvis agent behaviour.
14. Bind localhost by default; every integration needs timeout handling.
15. External child processes require explicit lifecycle ownership.
16. Never assume a process exists from a stale PID.
17. Use database migrations; build diagnostics on stable schemas.
18. A UI need does not create a peer API.
19. Keep the system understandable.

---

# 21. Milestones

Milestone numbers identify work; the runbook's stages schedule it, and §21.1 maps between them.

| # | Milestone | Exit |
|---|---|---|
| **M0** ✅ | Foundation — package, FastAPI, config, SQLite, migrations, logging, web shell, CLI, **NERVIS's own `/ecosystem/*` surface** (§3.1) | `nervis serve` starts; the browser opens the dashboard; `nervis doctor` works; the database migrates cleanly; **no external service is required**; NERVIS answers its own identity, health, capabilities and version, and MEP conformance fixtures pass at one pinned protocol version — Stage 1 exits here, and M2 is the client half |
| **M1** ✅ | System telemetry — CPU, RAM, swap, disk, process list, basic thermal | Dashboard updates live; sampling does not noticeably load the machine; missing telemetry degrades to Unknown; metrics survive a browser reconnect |
| **M2** ✅ | Service registry — health model, registry, RAVIS/SIRVIS/LM Studio/Ollama probes, capability negotiation | Each service shows state independently; an offline service never breaks the page; health timeouts are bounded; status changes update live; spoofing, duplicate, stale, auth and version tests pass |
| **M3** ✅ | RAVIS integration — health, providers, models, routes, usage, sessions | NERVIS inspects RAVIS **without touching its DB**; provider health and recent routes visible; the RAVIS-unavailable state works |
| **M4** ✅ | General chat — RAVIS-backed, streaming, history, mode selector, route inspector | `ravis/auto` chat works; streaming works; route details correspond to the real RAVIS decision; history persists locally; a RAVIS outage produces a clear error; **a NERVIS session never appears as a Clarvis session**; title generation is marked as a background call |
| **M5a** ✅ | SIRVIS integration, read half — models, results, system, Runtime Sets, evidence, recommendations | Existing SIRVIS results appear; **no benchmark business logic exists in NERVIS**; provenance renders correctly |
| **M5b** ✅ | SIRVIS integration, launch half — benchmark submit, cancel, live progress. **Blocked on SIRVIS M14**, which owns the job queue: `sirvis.benchmarks.jobs@1` is advertised *unavailable* and §1 forbids inventing the endpoint | A benchmark launches through the SIRVIS API; progress streams live |
| **M6** ✅ | Event hub — envelope, ingestion, SSE broadcast, bounded persistence, filters | Events appear live; RAVIS events ingest; retention is enforced; **an invalid event cannot crash the hub** |
| **M7** ✅ | Distributed tracing — trace IDs, correlation, timeline | One RAVIS request forms a trace; multiple events correlate; missing spans render gracefully; filters work |
| **M8a** ✅ | Clarvis Bridge integration, receiving half — §5.1's **authenticated local dynamic registration**, per-extension-host instances, redaction, and §10.1's limits enforced structurally | A Bridge can register with a port and token it generated; two extension hosts appear separately with no cross-instance leakage; no workspace path, file content or token is stored or displayed; NERVIS has no code path that could resolve a gate |
| **M8b** ✅ | Clarvis Bridge integration, live half — status, mode, busy, agent/task events, gate state read from a running Bridge. **Blocked on Clarvis building the Bridge**: `CLARVIS.md` §6 specifies it and the Clarvis repository contains no implementation, and §1 forbids inventing another component's API | Clarvis still works without NERVIS; connect/disconnect is safe; existing Clarvis safety behaviour unchanged |
| **M9** | Clarvis diagnostics UI — status, workspace, agent run, tasks, recent events, approval waiting | Live agent activity visible; task state matches Clarvis; a stale connection is clearly shown; **no Clarvis state duplicated independently**; multi-instance isolation holds |
| **M10** | Raw logs — per-source adapters with rotation and retention | Filters work; rotation and retention enforced; secrets redacted; a missing file does not error globally |
| **M11** | API Inspector — RAVIS request stages, normalized request, route, provider metadata, final response | One request inspectable end to end; credentials never displayed; content follows privacy settings; transparent vs translated distinguished honestly |
| **M12** | AI diagnostics — trace packet builder, redaction, RAVIS analysis request, local-only option | A trace can be analyzed; **the user sees exactly what will be sent**; local-only policy enforced; analysis failure does not alter logs |
| **M13** | code-server spike — **do not build the integration yet** | An exit report covering every capability in `CLARVIS.md` §7.1, graded `PASS` / `PASS_WITH_LIMITATION` / `FAIL` / `NOT_TESTED` — the runbook §6.2 Stage 9 vocabulary, which CLARVIS.md §7.1 also uses. **`NOT_TESTED` is the value this report most needs**: an untested combination that has to be graded pass or fail gets guessed or dropped |
| **M14** | Code tab *(only if M13 succeeds)* — code-server management, reverse proxy, workspace launcher, Clarvis install | Code tab loads; Clarvis activates; the WebSocket survives; the workspace opens; the Clarvis panel renders; the terminal works; the proxy security suite passes |
| **M15** | Browser Clarvis compatibility — **address only issues found in M13**, no speculative porting | The existing VSIX stays one artifact if possible; VS Code stable and VSCodium still pass regression; the code-server path passes the agreed matrix |
| **M16** | Service supervision — ownership modes, start, stop, restart, PID verification | Only managed services are controlled; **external services are never killed**; crash recovery works; a restart does not create a duplicate process |
| **M17** | Unified diagnostics — cross-service trace, health overlay, log correlation, benchmark/runtime context | A Clarvis → RAVIS → provider trace is visible; SIRVIS runtime evidence links where available; a broken link still produces a partial trace |
| **M18** | Polish — responsive UI, navigation, error states, onboarding, settings, backup/export | A fresh install is understandable; no service is required for dashboard startup; every unavailable state has a sensible explanation; settings import/export works **without secrets** |
| **M20** | Conversation memory — recall across conversations, not merely history within one | A new conversation can draw on an earlier one; what was recalled is **shown, with its source conversation**, never silently injected; recall is local by default and follows §7.2's storage rule; **turning it off leaves ordinary chat unchanged**; a recalled passage is fenced before it re-enters a prompt (§11.5), because a stored assistant reply is model output and re-admitting it unfenced is the same trust mistake in a longer loop |
| **M19** | macOS packaging — `NERVIS.app` | Launches the service, opens the UI, exits cleanly, respects independently running services, leaves no orphan process |
| **M21** ✅ | Notification centre — a durable store for things NERVIS wants to tell the user, with a screen, an unread count and dismissal | A finished task, a service transition and a question survive a reload and a restart; **every note says why it exists**, and one produced by a model also says which model and what it cost; **every note acted on is one the reader could see** — a selection, or "mark all read" over exactly what is listed, resolved to one request per note, so nothing the server expands on its own can reach a note nobody has read; **no per-kind mute and no standing rule**, which is the actual failure this guards against rather than bulk action as such; the voice announcement and the note are one event seen twice, not two events — muting speech must not lose the record |
| **M22** ✅ | Proposal outcomes — record whether a confirmed thing was accepted, declined or edited, and let later proposals read it | An offer's fate is stored with the offer; a preference drawn from it is **visible in the proposal that uses it** — "you declined this twice, so this suggests the local model" — never a silent change of behaviour; clearing the record restores the unlearned proposal exactly; **no outcome is inferred from silence**, because a person who closed the tab did not decline |
| **M23** ✅ | Learned notes — a file NERVIS appends to, beside the hand-written knowledge files and retrieved the same way | What NERVIS learned is a file a person can read, edit and delete; a learned note carries its date and what prompted it; **it never overwrites a hand-written note**, and where the two disagree the hand-written one wins and the conflict is shown; `tools/knowledge_check.py` gates the learned file exactly as it gates the others |
| **M24** ✅ | Planning — propose an ordered sequence of operations from the closed set, confirmed once, stoppable | Every step is an operation §12 already allows and nothing new is invented; the whole plan is shown before anything runs; **each step remains individually bounded and reversible**, so one confirmation is an ordering decision and not a blanket approval; a stop takes effect at the next step boundary and says what had already happened; a step that fails halts the plan rather than continuing past it |
| **M25** | **Background thinking — NERVIS may run a model with nobody watching.** The first four milestones change nothing about what NERVIS may *do*; this one lets it start work unprompted, which is why it is last. Unattended work runs in RAVIS's `ravis/background` pool (RAVIS M26) so it never competes with the chat the user is in, and everything it produces lands in M21's notification centre rather than acting: a finished summary, a noticed pattern, or a **question**, which opens a chat session the user can answer at their leisure | Unattended work never contends — a background run and an interactive reply are concurrent and neither pays a model load for the other; **every background run is attributable**, naming what prompted it, which model ran and what it cost, queryable by pool in `/api/v1/usage`; a trigger is disableable individually and all of them together, and disabling stops future runs rather than hiding past notes; **§12's gate does not move** — background work may propose and may notify, and an act still needs the same confirmation it needs when the user is watching, so nothing here is a path to unconfirmed action; a question NERVIS asks carries the context that prompted it, so answering does not require reconstructing what it was thinking |
| **M26** *(stretch)* | **A window manager that knows what the machine is doing — Qtile.** Qtile is configured in Python and NERVIS is already an HTTP service, so the join is a request from a widget rather than an integration layer. Three pieces, in increasing order of effort: a group that houses the dashboard, which is a `Match` rule and nothing else; a bar widget reading `/api/v1/services`, which already returns what a status bar wants; and a keybinding into chat. **Whether NERVIS may move windows is a separate decision and not part of this** — Qtile's command graph would allow it, and if it is ever wanted it arrives as operations in §12's closed set with a confirmation, not as an agent holding a window manager | The dashboard has its own group and survives a restart of the bar; the widget shows a degraded peer as degraded and an unreachable NERVIS as unreachable rather than blank; **the ecosystem still runs headless** — nothing here may become a dependency of the services, because the same install has to work with no window manager at all | — NERVIS forms notes unattended, on its own pool and session, inside a spend ceiling | Runs on a pool distinct from chat's so the two do not contend, with **its own session id** so affinity never drags chat onto its model; **what "does not contend" resolves to is the machine's business rather than this milestone's** — a resident local model on a workstation with an idle card, a small hosted one on a laptop whose runtime is cold (RAVIS M26); a ceiling and an interval are configured and enforced, and the spend is attributable per pool in the usage ledger; the §9.6.1 background marker is **not** used, because "must be free" resolves to a local model and defeats the parallelism this exists for; **nothing it produces is an act** — every output is a note or a proposal, so §12 is untouched; turning it off leaves the rest of chat unchanged |

## 21.1 Ecosystem gate mapping

| Runbook stage | Lands in |
|---|---|
| Stage 0 — baseline and invariant lock | Before M3 — every conceptual peer call is `REAL CONTRACT`, `LABELLED TEST DOUBLE`, `DEFERRED` or `STOP`; **no invented endpoint remains** |
| Stage 0 — baseline and invariant lock | M0 (foundation), and the contract-replacement audit below |
| Stage 1 — shared protocol | M0's own `/ecosystem/*` server surface; M2 supplies the client half and the registry at Stage 6 |
| Stage 6 — NERVIS core | M1 + M2 (dashboard, degradation and the registry), M3 + M4 (RAVIS views and chat), M5a (SIRVIS views; M5b's control half waits on SIRVIS M14), M11 (API inspector), M16 (supervision) |
| Stage 7 — events and tracing | M6 + M7 + M10 + M12 (AI diagnostics — §11.5's fencing rule is part of its exit) |
| Stage 8 — Clarvis Bridge | M8a + M8b + M9 + M17 — M17's exit requires a Clarvis → RAVIS → provider trace, and the runbook is explicit that Clarvis joins at Stage 8. Its Stage 7 half (RAVIS → provider correlation) may land earlier; the Clarvis leg cannot |
| Stage 9 — code-server compatibility and the Code tab | M13 + M14 + M15 |
| Stage 10 — whole-ecosystem hardening | M19 + M20 |
| **Unscheduled — stretch** | M26 (Qtile). Named because it changes where the ecosystem *runs* rather than what it does, and that answer is already settled: `sirvis_base_url` empty means RAVIS runs without SIRVIS, which §13.4 requires — provider metadata and RAVIS's own observations carry routing, with the degradation labelled rather than hidden. So a machine with no GPU runs NERVIS and RAVIS and simply has nothing for SIRVIS to measure. The one thing to watch is a capability that would have come from measurement: tool support unknown to the provider metadata fails closed, and the operator override is what resolves it |
| **Stage 11 — an assistant that grows** | M21 → M22 → M23 → M24 → M25, in that order. Each is useful alone and each is what the next reads from: the notification centre is where everything posts, outcomes are what preferences are learned from, learned notes are what a plan reasons with, and background thinking is the only one that needs a model to run unattended. **The order is also the risk order** — the first three change nothing about what NERVIS may *do*, and the gate on acts stays exactly where §12 puts it through all five |
| **Unscheduled — deferred by decision** | M18 (polish — responsive layout, onboarding, backup/export). Deferred rather than dropped: the prototype already carries the layout and the error states, so what remains here is genuine polish on top of a working interface rather than the interface itself. Listed so no milestone is silently unassigned |

> NERVIS core is Stage 6 — after RAVIS is live with Clarvis and after the SIRVIS evidence
> plane exists. Everything NERVIS displays belongs to a peer that must already publish it.

## 21.2 Core integration gate

Core NERVIS is ready when RAVIS status works; SIRVIS status works; general RAVIS chat works;
service outages degrade gracefully; SIRVIS evidence preserves build/runtime/role identity; the
event hub works; traces work; and the Clarvis Bridge **may be absent without error.**

> **The Code tab is not part of this gate.**

## 21.3 Definitions of done

**MVP** — NERVIS opens → system resources visible → RAVIS health and routes visible → SIRVIS
health and benchmarks visible → general RAVIS chat works → the live event stream works →
diagnostics and traces work → Clarvis status is visible.

**Strong MVP+** — Code tab, Clarvis inside code-server, AI diagnostics, service supervision.

**Non-goals for the first release** — remote internet access, multi-user accounts, cloud
dashboards, team sharing, a mobile app, a distributed tracing backend, Kubernetes, remote code
workspaces, a browser-only Clarvis rewrite, a full IDE replacement, automatic destructive
repairs.

---

# 22. Risks

| Risk | Mitigation |
|---|---|
| code-server compatibility | Compatibility spike before integration; Tier B is an acceptable product |
| NERVIS becoming a monolith | API boundaries, no direct DB imports, specialist services authoritative |
| Huge log volume | Structured events, bounded retention, raw log rotation |
| Diagnostics leaking source or secrets | Metadata by default, redaction, explicit content opt-in, privacy-aware RAVIS analysis |
| NERVIS bypassing Clarvis safety | Observer bridge only, Clarvis gates authoritative, no direct file access |

---

# 23. Conflicts resolved in this consolidation

| Conflict | Sources | Resolution |
|---|---|---|
| Code tab status | Build plan treated it as a core surface; the revised addendum made it spike-gated | Spike-gated, and **explicitly excluded from the core integration gate** (§21.2) |
| Health state enum | Plan: `HEALTHY/DEGRADED/OFFLINE/STARTING/STOPPING/UNKNOWN`; addendum: 8 registry states; MEP: 3 | Services report the MEP status about themselves; NERVIS's 8 registry states are observer-side (§5.1) |
| Trace headers | Plan proposed `X-Nervis-Trace-ID`; the MEP requires W3C `traceparent` | **W3C `traceparent` wins.** The vendor headers are dropped, not carried alongside |
| Event envelope | Plan used a simpler `{timestamp, service, component, event, level, …}` shape | The MEP envelope is canonical; the plan's fields map onto it |
| Chat profile | Plan defaulted to `ravis/auto`; the addendum forbids inventing a `nervis-chat` profile | **`ravis/chat`**, which RAVIS publishes and accepts. `ravis/auto` was the MVP default and orders nothing, so it fell back to alphabetical (§7) |
| Milestone numbering | Plan M0–M19; addenda E-N0–E-N9 | M-numbers identify the work. The addenda's E-N gates are retired — those documents are not in this set — and §21.1 now maps the milestones onto the runbook's stages, which schedule them |
| Dead citations | `fileciteturn…` markers throughout | Removed; Clarvis facts now point at `CLARVIS.md` §3, which cites real source |

---

# 24. Final success criterion

NERVIS succeeds when a user opens one screen and can answer:

> Is my AI stack healthy? What models are loaded? What is using my memory? Why did RAVIS choose
> that model? What benchmark evidence supports that choice? What is Clarvis doing right now? Why
> did this agent request fail? Can I talk to my AI without opening VS Code? Can I open my
> development environment from here?

— and NERVIS answers **without taking ownership away from the specialist services that actually
know the answers.**

> NERVIS presents the ecosystem accurately rather than forcing the ecosystem to conform to its
> UI. **The dashboard is the control plane, not the prison in which every other interface must
> physically fit.**

---

# 25. The prototype, and what becomes of it

`nervis/index.html` renders every screen this ecosystem will have. It is not a sketch and
it is not a mockup of a design nobody built — it is a working single-file application whose
data layer is shaped like the real API responses, whose every method cites the endpoint it will
call, and which has already been exercised against a genuine LM Studio catalogue. It found a
capability filter that matched nothing, which is more than most test suites manage.

So NERVIS's UI is **built from it, not instead of it** — the same instruction
`ECOSYSTEM_RUNBOOK.md` §6.2 Stage 4 gives about benchmark tooling: wrap it before replacing it.

## 25.1 What transfers verbatim

- **The screen inventory and information architecture.** Which screens exist, what each one
  answers, what belongs on it and what deliberately does not.
- **The degradation model.** `SERVICES[key].state` driving `usable()` and `cell()`, with absence
  rendered as an ordinary state rather than an error. This is the hard part of a control-plane
  UI and it is already designed and walkable.
- **Every `API` method signature and its endpoint citation.** These are the contract between the
  interface and the services, and they were checked against the specifications rather than
  invented — `tools/check.py` now fails a build if one is not.
- **The CSS, layout and visual identity**, which are framework-independent.
- **`docs/PITFALLS.md`**, which is every defect that prototype has produced and the rule that
  prevents each one. Read it before writing the replacement, not after.

## 25.2 What must be rebuilt, and why

*All six were rebuilt. Each entry now records what the check that established it
actually found, because in every case the original count or claim was wrong in a
way reading could not have caught. Two are not finished and say so.*

**Escaping, everywhere.** Stated here as 35 sites of which 9 escape. Measured by
`tools/injection_check.js` — which feeds every `API` method a hostile payload
and renders all 35 screens — it was **28 injection sites across 27 of the 35
screens, from 579 interpolations reaching the markup unescaped**. Three defects
underneath it: `escapeHtml` did not escape quotes, so 72 attribute sites were
escaped in appearance only; an inline handler is parsed twice, so `&#39;` is
decoded back to a quote before JavaScript sees it and the three sites that
"fixed" this by replacing quotes were no safer; and ids were written escaped and
read raw. Now 108 interpolations and 22 sites, held by a one-sided ratchet.
**Not finished** — the remainder are compound expressions a position-aware
scanner cannot classify safely, and they come down in reviewable passes.

**Targeted updates instead of full re-render.** Rebuilt as *preserve and
restore*, not as a diff, and the difference is deliberate: a keyed reconciler
needs every view rewritten to describe its own identity before one screen works,
which is not a change this file survives in one commit. The content region is
still replaced wholesale; what was typed, focused, selected, opened and scrolled
now survives it, and only for fields the reader touched, so a repaint cannot
un-clear a form that was just saved. The one genuinely targeted update is the
avatar — a sixty-thousand-character iframe document that was being torn down and
rebuilt identically on all fifty render call sites.

**Routing.** Hash-based, because `web.py` serves one file and mounts nothing
else, so `pushState` would 404 on reload or on a pasted link. All 35 screens are
addressable and round-trip; back, forward and a hand-edited fragment all work;
an address naming no screen resolves to one that exists and *says so* rather
than coercing silently. **Not finished** — this section asks that a route
decision be addressable, and the Routes screen has no selected-decision state to
address: it renders `decisions[0]` and its rows carry no click handler.

**Authentication.** The 401/403 path exists and is visibly distinct from
absence: a refused read renders as a refusal, and the status bar no longer calls
`unauthorized`, `incompatible`, `stale` and `stopped` all "unreachable". Stated
plainly, because the opposite would be the kind of claim this document warns
about: no RAVIS endpoint the dashboard calls can return 401 today, since reads
are deliberately open on loopback. The path serves SIRVIS's scoped mutations and
the day RAVIS gains one.

**Per-request loading and error states.** The cause was one line: `live()` threw
`new Error('HTTP '+status)` for every non-2xx, landing in the same `catch` as a
network refusal, a timeout, a body that was not JSON and an adapter that threw —
and never read the binding. Six outcomes are now kept apart. The sixth is
`unreadable`: the service answered and *this page* could not read it, which used
to be reported as the service being unreachable, sending the one person who
could fix it to the wrong repository.

**A real SSE client.** Built, and the server had to be corrected first: the gap
frame emitted `id:` with an empty value, which per the SSE specification clears
the client's cursor — so the one frame whose job is to make a client catch up
removed its means to; `retry: 3000` was never sent; and a cursor outside
retention was silently resumed rather than refused with 409. The client is
deliberately **not** an `EventSource`, because that API never exposes the status
of a response it rejects: the 409 named here would become an invisible
three-second reconnect loop against a cursor the server refuses forever.

The claim that "the stream-health tiles that would display them already render
from mocks" was false when written — the mock's `cursor`, `buffered`, `dropped`,
`gaps` and `connected_since` fields were defined and referenced by nothing on
any screen. There is now one card, and it reads the live client rather than a
mock.

## 25.3 Until then, it is the development instrument

Before Stage 6 the prototype is not waiting to be replaced — it is how each stage's work becomes
visible. Every stage in the runbook names an increment, and each is the same mechanical change
`nervis/docs/WIRING.md` describes: replace one mock method body with a `fetch`, keep the
shape. A screen driven by real data is a test no unit test replaces, because it is where a field
that is missing, mistyped or silently empty becomes obvious immediately.

---
