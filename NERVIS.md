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
diagnostics and automation:

```text
nervis.registry@1          nervis.sirvis_views@1
nervis.dashboard@1         nervis.clarvis_visibility@1
nervis.event_hub@1         nervis.diagnostics@1
nervis.traces@1            nervis.supervision@1        only for explicitly configured owned services
nervis.ravis_chat@1        nervis.code_server_proxy@1  only after security/compatibility gates pass
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

## 5.3 Service discovery

Initially, configured localhost ports with a health test per service. Automatic local discovery
may follow. **Do not require Bonjour or mDNS for MVP.**

---

# 6. Dashboard and telemetry

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

---

# 7. General chat

NERVIS chat is a **normal client of RAVIS's published OpenAI-compatible API**, using a dedicated
virtual profile that RAVIS accepts. Default `ravis/auto`; modes Auto, Balanced, Fast,
Performance, Cheap, Local, API, Private; optional explicit model selection.

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

**Gate:** start/stop/restart, crash loop, stale PID, PID reuse, partial start, NERVIS
crash/restart and unauthorized-actor tests never affect external instances.

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

## 18.2 Spoken output

NERVIS may speak, through the same Fish Audio provider Clarvis uses, with a distinct voice.
Reuse the provider, not the voice — two identical voices from one machine is a bug.

**Speech is an egress path, and this is the part that is easy to get wrong.** Cloud TTS sends
the text off the machine. So:

- Spoken output obeys the **same privacy policy as routing**. Under `LOCAL_ONLY`, cloud
  synthesis is not permitted — fall back to local system TTS, or stay silent and say so.
- A reply that a privacy constraint kept on-device must not then be read aloud by a cloud
  service. **Failing closed on the route and open on the voice is still a leak.**
- Voice credentials live in NERVIS's own secure storage, are never sent to peers, and never
  appear in events, traces or diagnostic packets.
- Voice is an optional capability (`nervis.voice@1`), advertised only when configured, and
  never a dependency of any other surface. Mute is honoured immediately and persists.

**Not MVP.** Character lands first as text; synthesis follows once the privacy gate above is
implemented and tested.

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

Ecosystem gates from the addenda (E-N0…E-N9) fold in as extra exit criteria; mapping in §21.1.

| # | Milestone | Exit |
|---|---|---|
| **M0** | Foundation — package, FastAPI, config, SQLite, migrations, logging, web shell, CLI | `nervis serve` starts; the browser opens the dashboard; `nervis doctor` works; the database migrates cleanly; **no external service is required** |
| **M1** | System telemetry — CPU, RAM, swap, disk, process list, basic thermal | Dashboard updates live; sampling does not noticeably load the machine; missing telemetry degrades to Unknown; metrics survive a browser reconnect |
| **M2** | Service registry — health model, registry, RAVIS/SIRVIS/LM Studio/Ollama probes, capability negotiation | Each service shows state independently; an offline service never breaks the page; health timeouts are bounded; status changes update live; spoofing, duplicate, stale, auth and version tests pass |
| **M3** | RAVIS integration — health, providers, models, routes, usage, sessions | NERVIS inspects RAVIS **without touching its DB**; provider health and recent routes visible; the RAVIS-unavailable state works |
| **M4** | General chat — RAVIS-backed, streaming, history, mode selector, route inspector | `ravis/auto` chat works; streaming works; route details correspond to the real RAVIS decision; history persists locally; a RAVIS outage produces a clear error; **a NERVIS session never appears as a Clarvis session** |
| **M5** | SIRVIS integration — models, benchmark queue, results, system, recommendations | Existing SIRVIS results appear; a benchmark launches through the SIRVIS API; progress streams live; **no benchmark business logic exists in NERVIS**; provenance renders correctly |
| **M6** | Event hub — envelope, ingestion, SSE broadcast, bounded persistence, filters | Events appear live; RAVIS events ingest; retention is enforced; **an invalid event cannot crash the hub** |
| **M7** | Distributed tracing — trace IDs, correlation, timeline | One RAVIS request forms a trace; multiple events correlate; missing spans render gracefully; filters work |
| **M8** | Clarvis Bridge integration — health, workspace state, mode, busy, agent/task events, gate state | Clarvis still works without NERVIS; connect/disconnect is safe; **no file contents or provider secrets are sent**; existing Clarvis safety behaviour unchanged |
| **M9** | Clarvis diagnostics UI — status, workspace, agent run, tasks, recent events, approval waiting | Live agent activity visible; task state matches Clarvis; a stale connection is clearly shown; **no Clarvis state duplicated independently**; multi-instance isolation holds |
| **M10** | Raw logs — per-source adapters with rotation and retention | Filters work; rotation and retention enforced; secrets redacted; a missing file does not error globally |
| **M11** | API Inspector — RAVIS request stages, normalized request, route, provider metadata, final response | One request inspectable end to end; credentials never displayed; content follows privacy settings; transparent vs translated distinguished honestly |
| **M12** | AI diagnostics — trace packet builder, redaction, RAVIS analysis request, local-only option | A trace can be analyzed; **the user sees exactly what will be sent**; local-only policy enforced; analysis failure does not alter logs |
| **M13** | code-server spike — **do not build the integration yet** | An exit report covering every capability in `CLARVIS.md` §7.1, with `SUPPORTED` / `SUPPORTED_WITH_CHANGES` / `UNSUPPORTED` per capability |
| **M14** | Code tab *(only if M13 succeeds)* — code-server management, reverse proxy, workspace launcher, Clarvis install | Code tab loads; Clarvis activates; the WebSocket survives; the workspace opens; the Clarvis panel renders; the terminal works; the proxy security suite passes |
| **M15** | Browser Clarvis compatibility — **address only issues found in M13**, no speculative porting | The existing VSIX stays one artifact if possible; VS Code stable and VSCodium still pass regression; the code-server path passes the agreed matrix |
| **M16** | Service supervision — ownership modes, start, stop, restart, PID verification | Only managed services are controlled; **external services are never killed**; crash recovery works; a restart does not create a duplicate process |
| **M17** | Unified diagnostics — cross-service trace, health overlay, log correlation, benchmark/runtime context | A Clarvis → RAVIS → provider trace is visible; SIRVIS runtime evidence links where available; a broken link still produces a partial trace |
| **M18** | Polish — responsive UI, navigation, error states, onboarding, settings, backup/export | A fresh install is understandable; no service is required for dashboard startup; every unavailable state has a sensible explanation; settings import/export works **without secrets** |
| **M19** | macOS packaging — `NERVIS.app` | Launches the service, opens the UI, exits cleanly, respects independently running services, leaves no orphan process |

## 21.1 Ecosystem gate mapping

| Addendum gate | Lands in |
|---|---|
| E-N0 contract replacement audit | Before M3 — every conceptual peer call is `REAL CONTRACT`, `LABELLED TEST DOUBLE`, `DEFERRED` or `STOP`; **no invented endpoint remains** |
| E-N1 MEP client/server and registry | M2 |
| E-N2 dashboard and degradation | M1 + M2 |
| E-N3 RAVIS chat and views | M3 + M4 |
| E-N4 SIRVIS views and control | M5 |
| E-N5 Clarvis visibility | M8 + M9 |
| E-N6 event hub, traces, diagnostics | M6 + M7 + M10 + M17 |
| E-N7 supervision | M16 |
| E-N8 code-server proxy | M13 + M14 + M15 |
| E-N9 whole-ecosystem release | after M19 |

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
| Chat profile | Plan defaulted to `ravis/auto`; the addendum forbids inventing a `nervis-chat` profile | `ravis/auto` for MVP; a dedicated profile only once RAVIS publishes and accepts one |
| Milestone numbering | Plan M0–M19; addenda E-N0–E-N9 | M-numbers are the spine; E-N gates fold in (§21.1) |
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
