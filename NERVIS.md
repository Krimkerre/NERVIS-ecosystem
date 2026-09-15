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
nervis.planning@1          nervis.background_work@1
nervis.conversation_memory@1                           off unless switched on (§7.2)
nervis.clarvis_handoff@1   nervis.api_inspector@1      degraded while RAVIS publishes no request content
nervis.settings_backup@1
nervis.raw_logs@1          only where a run directory is configured (§11.3)
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

**As built, 12 September 2026.** The block above is the plan, and the page is organised
differently. The real screen list is `APP_CONFIG` in `nervis/index.html`: four application
tabs across the top, each with its own side rail.

```text
NERVIS   Overview · Chat · Files · Notifications · Ecosystem map · Events · Traces ·
         Diagnostics · System · Settings
SIRVIS   Dashboard · Models · Runtime · Discover · Benchmarks · Runtime sets · Results ·
         Recommendations · Downloads
RAVIS    Dashboard · Routes · Sessions · Spending · Pools · Providers · Credentials ·
         Policies · Evidence · Logs · Diagnostics · Settings
CLARVIS  the editor's own rail — Workspace · Explorer · Search · Source control ·
         Run & debug · Extensions · Terminal
```

Where the planned screens went:

- **There is no separate Code tab.** The CLARVIS tab *is* the editor: it frames code-server —
  through NERVIS's `/code/` proxy when that is switched on, at the editor's own address
  otherwise — and its rail is the editor's side-bar list (§13).
- **Clarvis's status, agent run, tasks and events** live on NERVIS → Diagnostics → *clarvis in
  the editor* rather than on the CLARVIS tab, which hides its rail so the editor can fill the
  page (§10).
- **Diagnostics** has four sections picked from a row of tabs — *services* (the health read),
  *clarvis in the editor*, *api inspector* and *raw logs* — with **Analyze trace…** in its
  header (§11.5). The planned *Live* and *Traces* are NERVIS's own **Events** and **Traces**
  screens.
- **SIRVIS's queue** is the Benchmarks screen, which reads SIRVIS's job queue. **RAVIS's usage**
  is the Spending screen.

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

**Codex** (M28 — specified 13 September 2026; built on 13 and 14 September, see below): under the headline tiles, one line from
RAVIS's `/api/v1/codex` — for example *"Codex · 2 tasks · 1 waiting for your answer · 62% left in
the 5-hour window"* — owned by RAVIS, so it goes absent with RAVIS like every other RAVIS figure
(§8, §16.2). It shows tasks and allowance and controls nothing. As built (0.28.13, with the tasks
since 0.29.0) it shows Codex's state, its tasks — Codex's own, not a project Clarvis's own engine
holds — and what is left in the tightest window with its reset, for example *"signed in · 2 tasks ·
1 waiting for your answer · 62% left in the 5-hour window · resets 04:30 (in 2 h 10 min)"*, and it
links to the Codex card on RAVIS → Dashboard (§8).

## 6.1 Telemetry ownership

NERVIS owns **general system and dashboard telemetry**: CPU, GPU where measurable, RAM, swap,
disk, network, thermal state, and per-process memory, CPU and PID where practical.

**Taken in a worker thread since 0.25.1.** `read_system` used to call `sample_system()` — a scan
of every process plus an `osascript` thermal query — straight from an `async` route, so NERVIS
answered nothing else while it ran: on 12 September 2026, with ten readers at once,
`/api/v1/health` took 3.9 ms at the median on its own and 203.6 ms interleaved with
`/api/v1/system`. Live after the fix: 11.0 ms interleaved, and 4.9 ms at the median (20.3 ms at
P95) beside one System screen redrawing without pause. What remains is the process scan holding
Python's interpreter lock inside its thread, which adds up only when samples overlap: fifty
callers mixing every dashboard read put `health` at 159 ms, against 19.7 ms for health alone.
Overlapping samples are not merged — one System screen is the case that occurs, and it is fine.

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
`ravis/balanced`, `ravis/fast`, `ravis/performance`, `ravis/cheap`, `ravis/local`, `ravis/api`,
`ravis/private`, `ravis/vision` and `ravis/draw`, labelled from RAVIS §9.3's display names
rather than spelled a third way here; optional explicit model selection.

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
details, model selection, voice, files, and images **in both directions** — a picture attached
to a conversation and a picture the model drew. **Later:** search, artifacts.

Three things about images that a profile list does not imply:

- **A picture attached is the whole reading, not an addition to one.** There is no text version
  of a photograph, so the *Show the model a PDF's pages* switch — which exists so a document can
  be read without paying for vision — does not apply to it: a document keeps its text when its
  pictures are dropped and a photograph keeps nothing. Where nothing available can see, the
  picture is withheld and the model is told so rather than left to describe it. Five megabytes
  is the ceiling, and above it the refusal says *resize it* rather than truncating.
- **A picture the model drew becomes a file.** It arrives as a megabyte of base64 in one stream
  frame and the reply a conversation stores is text, so it is written into the workspace and
  linked from the reply — which is what makes it survive a reload and what makes §14's document
  endpoint the download.
- **Asked for a picture on a profile that answers in words, chat says which profile draws.**
  It does not claim it cannot, and it does not offer to reroute the request itself, which it has
  no power to do.

**Also required:** stop/cancel, model and profile disclosure, a route-explanation link,
usage/cost display, session history per configured retention, error and fallback presentation,
and privacy controls.

**Background calls.** NERVIS chat generates conversation titles (§7.2), and may later add
summaries or suggestions. Each is a RAVIS background call and must carry RAVIS's declared
marker (RAVIS §9.6.1) rather than arriving as an ordinary completion on the user's chosen
profile. **Every background call walks one route**, set under Settings → Unattended work: the
pool chosen there (`ravis/free-api` by default — free, remote, and loading nothing on this
machine), then for a title the model that just answered (already in memory), then
`ravis/local`. A title is switched on or off in the same place, independently of unattended
thinking, and the same route serves a handed-over task's folder name, the layout glance on a
saved PDF and unattended thinking itself. Privacy is the pool's to express: `ravis/free-api`
is logged and trained on, and an operator who does not want that picks `ravis/private` or
`ravis/local` — the fallback never leaves the machine, so it cannot overrule that choice. The
marker still makes RAVIS refuse any step that would cost money, a hosted model that answered
the conversation included. *Changed on 11 September 2026: titles went to the model that
answered and then `ravis/cheap`, which on this machine loaded a local reasoning build to write
six words and produced none; and a title was only ever attempted for an opening of six words or
fewer, because the stand-in the titler recognised was not the one conversations were created
with — 121 of 223 conversations had never been titled.* *The marker was sent only on the unnamed path until
9 September 2026, so a conversation answered by a frontier model had its title billed to that
model — the failure this paragraph names. An external audit found it.* An untitled conversation is a smaller failure than a title billed to a frontier
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

**Skills, read before the model sees anything** (0.32.0). The skills the owner switched on for
the other models reach chat the way the notes do, because chat has no tools: NERVIS reads RAVIS's
`GET /api/v1/skills/models` with its client credential, matches the question against each skill's
name and description — named outright, or enough shared words, never words every skill here could
carry — and reads the one fitting skill's `SKILL.md` through `GET /api/v1/skills/models/read`. The
model gets a sentence saying NERVIS's own rules come first, that a skill never changes what it may
say or do, approves or presses anything, or overrides an instruction outside the fence; then, fenced,
the list (at most 20, a line each) and that skill's instructions without their front matter (at most
6,000 characters). A read RAVIS refuses — switched off since the list, a file it won't serve — is
said plainly, so the model neither follows the skill nor claims to. Only for a real question with a
persona, like the readings; nothing is added when no skill is on. `nervis/skills.py` records why it
isn't a tool call: one model call per answer, and no model output choosing what NERVIS reads.

**Slash commands, and a skill asked for by name** (0.34.0, the owner's decisions of 15 September
2026). A message that opens with `/` is looked at on the page before anything is sent, and only its
first word counts: "what does /help do?" is a question, and so is a message opening with a path.

- **Three built-ins, answered on the page and never by a model.** `/help` lists them and every
  switched-on skill as it is typed, with its description. `/clear` starts a new conversation through
  New chat's own path; nothing is deleted, and the old one stays under History. `/model` says which
  model answers and opens the picker; `/model <pool or model id>` sets it through the picker's own
  path for this conversation only, and only to a choice the picker offers, never a guess. A built-in
  counts only as the whole message; `/model <id>` is the one that takes an argument.
- **A skill by its own name.** `/skill-name request` asks for a skill switched on for Other models;
  `/skill <name or id> request` always reaches one. The built-in wins a name it shares with a skill,
  which `/skill <name>` still reaches, and two skills sharing a name are reached by full id, the short
  form answering one line naming both. A switched-off skill stays off, even typed.
- **Checked at send time.** The page reads the list from `GET /api/v1/chat/skills` (id, name,
  description; `read` false when RAVIS gave none) when the chat panel opens and at most once a minute
  while `/` is typed, and sends the skill's id as the optional `skill` on `POST /api/v1/chat`. Its copy
  can be stale, so NERVIS checks the id against RAVIS's list and reads the skill's `SKILL.md` then.
  A skill it can't use (switched off since, unknown, RAVIS not answering, a refused read) is refused
  with one plain line (409) before the turn is stored or a model is asked.
- **Used in place of the fitting skill**, through the same read, `block`, framing and 6,000-character
  cap. The model is asked the request without the `/name`, in its typed case; the conversation keeps
  the message as typed. **It rides even without a persona**: `_turn_context`'s `wanted` still keeps
  the clock and the readings from a plain client, and the skill asked for is added alone, because the
  request itself asked for it. Greetings and nudges ignore the field.
- **Local lines.** A first word nothing is called, a skill with nothing to do, two skills sharing a
  name, or an unread list get one line on the page. Local lines are drawn in the conversation only:
  never sent, read aloud, counted as turns, or kept in the browser's saved copy.
- **While a reply streams or an offer waits.** Typed text has never answered an offer here, since only
  its buttons do. A skill asked for while the latest reply's offer or plan waits is refused on the
  page ("Answer the question first; the skill can wait."), so it can't carry the conversation past
  the offer; one typed while a reply streams stays in the box, like any message then. `/help` and
  `/model` on its own still answer; `/clear` and `/model <id>` ask to finish or stop, or to answer the
  offer, first. Enter doesn't send while an input method is composing.
- **The pop-up** opens when `/` starts the chat box and the cursor is in the first word: matching
  built-ins and skills, a clashing skill shown as `/skill <name>` and duplicates by full id, filtered
  by plain string comparison. Arrows move, Enter or Tab completes with a trailing space, Escape
  closes, a click works; it is a listbox the chat box points into with `aria-activedescendant`, drawn
  with `textContent` only. Gate: `nervis/tools/slash_check.js`.

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

**The dashboard reads RAVIS through NERVIS** (since 12 September 2026), at
`/api/v1/relay/ravis/{path}`: GET only, RAVIS's `/api/v1/` and `/ecosystem/` paths only, never
the `/v1` gateway. NERVIS presents its own client credential and returns RAVIS's status and body
unchanged; a RAVIS that did not answer comes back as 502 or 504 marked `x-nervis-relay`, which the
page shows as unreachable or slow. Read directly, every open tab shared RAVIS's one anonymous
allowance of sixty requests a minute with any other local caller, and the overview alone used
about twenty-seven. The browser holds no RAVIS credential either way. Every read NERVIS itself
makes of RAVIS names NERVIS too: the shared peer reader takes no default credential, so a reader
cannot go out anonymous by omission — found on 12 September 2026 in the API Inspector's two reads
and chat's per-reply route lookup, the last to do so.

**Spending** (since 12 September 2026): the RAVIS Spending page reads `/api/v1/usage/daily`, and
the dashboard's spend tile has a reset. The reset is a NERVIS setting, `ravis.spend_counted_since`,
sent to RAVIS as `/api/v1/usage?since=`. It changes what the tile counts from and nothing else: no
record is removed, and RAVIS's budget keeps its own window.

**Codex** (M28 — specified 13 September 2026; built on 13 and 14 September, see the end of this paragraph): the RAVIS Dashboard carries a Codex card
and the Overview a one-line summary, from RAVIS's `/api/v1/codex` through the relay. They show the
ChatGPT plan's allowance per window and its reset (never as money), and which Codex tasks are
running or waiting for an answer (project name, state and age). **Stop is their only task control:**
after a confirmation, a control route forwards to RAVIS's stop-only owner route with NERVIS's RAVIS
admin credential. Answering and steering happen in Clarvis, and RAVIS refuses NERVIS's credentials on
those routes. Sign in, cancel, sign out, confirming the account and accepting a version are control
routes presenting NERVIS's RAVIS admin credential. The file-rules re-test is not: it starts only from
the menu bar. The menu bar app's Codex line reads the same state through `tools/run.py status --json`,
and its **Stop this task…** runs the launcher's `codex stop` with the owner's command-line credential,
which NERVIS never holds (runbook §2.2). **Built on 13 September 2026 (0.28.12): the sign-in, on
RAVIS → Credentials** rather than on the card, because that is where the owner looked for it. Its
control routes are the four sign-in routes above (start, cancel, sign out, confirm the account) and
a read of the waiting sign-in, `GET /api/v1/ravis/codex/sign-in`, gated like the writes because it
returns the sign-in page's address; every answer is marked `no-store`. **The allowance followed the
same day (0.28.13), as a tile rather than a card:** at the owner's request it sits in the RAVIS
Dashboard's headline row beside Spend, in place of the active profile's tile, with the state and
reason, the plan, each window's remaining allowance and reset, *unknown* rather than 0%, and *stale*
with its age. Since 0.28.14 the tile itself shows only the figure, the tightest window's reset and a
chip when Codex isn't ready or the reading is stale; the rest opens in a tooltip on hover or keyboard
focus. **The Codex card followed on 14 September 2026 (0.29.0, N2b)**, under RAVIS → Dashboard's
headline tiles, in three parts. *Tasks:* each Codex task RAVIS lists, by folder name and never a
path, with its state in words, how long it has waited, the model and effort it runs at, and
*reconnecting* while RAVIS reopens its Codex conversation so a newly allowed site reaches it (the
last three since RAVIS 0.25.1). **Stop…** beside a running or waiting task is the only task control.
It takes two clicks — the page's rule, since a browser can mute `confirm()` for good — and the second
sends `POST /api/v1/ravis/codex/runs/{sid}/stop` with the folder and turn the row showed and the
page's own `Idempotency-Key`, kept per task and turn, so a click retried after a lost answer is
replayed by RAVIS rather than carried out twice. NERVIS holds the id to `as_` and 10 to 40 letters
and digits, and the key to 16 to 128 letters, digits, `-` or `_`, answering 400 before RAVIS hears
anything otherwise, and forwards to RAVIS's `owner-stop` with its RAVIS admin credential,
`source: dashboard` of its own and the page's key; `ravis_peer.configure` gained a keyword-only
`headers` for it, which refuses a header named `authorization` in any case. *Allowed sites:* the
sites Codex's commands may reach, read from `/api/v1/codex/sites` through the relay but outside
`live()`, so its 503 while Codex isn't running doesn't mark the rest of RAVIS failed: RAVIS's
defaults folded away with no Remove, and **Remove**, in two clicks, beside each site the owner added,
through `DELETE /api/v1/ravis/codex/sites/{host}`, which forwards only a host name's shape.
*Version:* the build RAVIS runs and, for one RAVIS hasn't tested, **Check this version** — RAVIS's
seven checks and what changed, through `GET /api/v1/ravis/codex/version-check`, which waits up to
210 s because RAVIS may run the checks then — and **Use this version…**, in two clicks, through
`POST /api/v1/ravis/codex/accept-version`, after words saying that accepting neither starts the
file-rules re-test nor spends allowance. Every refusal is said in words. The same release gave the
menu bar app its Codex line (N1b), which opens the card: the state or the task count, each task with
its wait, **Stop this task…** in each task's submenu behind an `NSAlert`
naming the folder, **Re-test the file rules…** for an accepted build whose rules are unproven — the
one place the re-test starts — and **Sign in to Codex…** while signed out, each through
`tools/run.py codex …`, whose `status --json` now carries each task's model, effort and reconnecting,
whether an account is signed in, and the build's version and verdict. The line is never red.
**The card's Skills followed on 14 September 2026 (0.30.0)**, at the owner's decision after the
first live Codex test ran one of the owner's personal skills unasked, and **moved to a page of their
own on 15 September 2026 (0.32.0)**, below. Since then the card keeps one line — *Which skills Codex
may use, and which the other models may, is on NERVIS → Skills* — through `CODEX_CARD.openSkills()`,
and neither reads nor switches skills itself. The launcher gives RAVIS the skills folder from the
same workspace path it gives NERVIS.

**NERVIS → Skills (0.32.0), the owner's decisions of 15 September 2026.** Skills are no longer
Codex's alone: Clarvis's own engine and NERVIS chat use them too, and each skill has one switch for
Codex and one for **the other models**, those two together. The page, in NERVIS's menu between System
and Settings, reads RAVIS 0.27.0's `GET /api/v1/skills` through the relay — outside `live()`, with a
12-second deadline, since RAVIS asks Codex for its list on that read — and lists every skill under
where it comes from, *In NERVIS's skills folder*, *Your personal skills* and *Built into Codex*,
each with its name, description, where it lives, and two switches, **Codex** and **Other models
(Clarvis and NERVIS chat)**. A built-in skill shows Codex's switch alone and "not available to other
models"; a skill RAVIS couldn't read whole says why and has no switch for the other models; while
Codex isn't running, its switches say they wait, built-in skills aren't listed, and the other models'
switches still work. The top line names NERVIS's skills folder and the personal one, what each starts
as, and when a change counts: for Codex from a task's next start or reopen, for the other models
from their next request, such as NERVIS chat's next message. What starts on and off is RAVIS's. A
switch takes one click, never `confirm()`, shows *switching on…* or *switching off…* until RAVIS
answers, and goes through `POST /api/v1/ravis/skills`, which checks the control token and forwards
only the page's `path`, `engine` and `enabled` to RAVIS's `POST /api/v1/skills` with NERVIS's RAVIS
admin credential; RAVIS's answer redraws the page, and a refusal (`SKILL_NOT_CHANGED`,
`SKILL_NOT_FOUND`, Codex not running, a RAVIS or NERVIS too old) is said in words before the list is
read again. That route is registered before the peers' negotiated reads, whose `/ravis/{surface}`
would otherwise answer it. It replaced the card's `POST /api/v1/ravis/codex/skills`.
`skills_check.js` holds the page and `codex_check.js` the card's line.

**Installing, updating, removing and browsing skills (0.33.0, with RAVIS 0.28.0), the owner's
decisions of 15 September 2026.** The Skills page gains an **Install and browse** card. **Install
skill…** takes a GitHub link to a skill's folder or a zip file (at most 8 MB, checked in the page
before anything is sent) and shows RAVIS's review before anything is installed: the name,
description, license, where it came from (the repository, folder, ref and commit reviewed, a
website, or a zip file), RAVIS's warnings, each file with its kind — a script flagged *script —
Codex could run it* — and `SKILL.md` as escaped text, with a plain line that the skill will arrive
switched off for Codex and for the other models and that it was checked against the Agent Skills
specification at agentskills.io. **Install** sends only the review's id; **Cancel** tells RAVIS to
drop it. Each skill RAVIS installed in NERVIS's folder says, on its row, where it came from, the
commit and the date (and when it changed on this Mac), with **Update…** — RAVIS fetches from where
it came from; nothing newer is said so, and otherwise the review shows the files added, changed and
removed, the `SKILL.md` diff and RAVIS's sentence on the switches, both off again when `SKILL.md` or
a script changed; a zip install updates from a zip of the same skill — and **Remove…**, two clicks
and never `confirm()`, which moves the folder to the Trash. **Browse** reads RAVIS's cached
marketplace through the relay, reads stale sources once per opening, and lists each entry with its
name, description, source (and the repository a link-list entry is kept in), license, a label such
as *curated* or *experimental*, an *installed* badge, and **Review and install**, which sends the
entry's own `install` body. The source filter marks openai/skills *deprecated by its owner* and link
lists and skills.sh *uncurated*; a words filter narrows the list; the link-list entries shown are
looked up as they are shown, ten at a time and each once. **Since 0.33.1**, from what the owner found
using it on 15 September 2026: the words filter follows the typing (250 ms after the last key) across
every loaded entry of the chosen sources rather than the page shown, and a redraw keeps the box's
focus and caret. An entry that can't be installed here, such as one kept outside GitHub, is hidden
whatever its source, under one line saying how many with a *Show them* switch this browser remembers
in `localStorage` (owner decision, the same day). skills.sh is searched with *All sources* chosen as
well as on its own: 600 ms after typing stops, or at once on Enter, never under 2 letters, which it
refuses (the page says *skills.sh needs a search of at least 2 letters.*), its results most installed
first in a group of their own under *From skills.sh, uncurated, ranked by installs*. A question
overtaken by newer words is cancelled, an answer arriving after a newer question is dropped, RAVIS's
refusal or skills.sh's own is said in plain words, and after an install the same words are asked
again, which RAVIS answers from the five minutes it keeps them, for the installed badges. The **Sources** card
lists every source with Hide or Show, a two-click Remove… for the owner's own alone, and a form adding
a GitHub repository (optional folder, branch or tag), a link list, or a *Website with an Agent Skills
index (agentskills.io standard)*. Every call is a POST to NERVIS's control routes under
`/api/v1/ravis/skills/` (`ravis/tests/fixtures/skill-store/contract.json` → `nervis_control_routes`):
each checks the control token and forwards to its one fixed RAVIS path, with NERVIS's RAVIS admin
credential, only the fields that route carries and only those the page sent, waiting up to 180
seconds since a review or a refresh fetches from GitHub; the zip route forwards the file's bytes and
refuses past 8 MB with 413 before RAVIS is asked. RAVIS's refusals are shown in its own words, which
it writes for the owner, and every handler carries only an id or a name the page drew itself.
`skill_store_check.js` holds the page and `test_skill_store_routes.py` the routes.

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

**Discover and Downloads read SIRVIS M11 live** (since 12 September 2026): Discover searches
`/api/v1/catalog`, which SIRVIS answers from Hugging Face, and opens a model's variants with their
sizes and SIRVIS's disk check; Downloads lists `/api/v1/downloads`. Starting a download is the
button-only operation `sirvis.download.start` on `/api/v1/commands/run`: NERVIS makes the call
with its `admin`-scoped SIRVIS credential, which this page never holds, and a disk-warning refusal
comes back with SIRVIS's warnings so the page can ask again with `confirm`. No chat phrase proposes
a download, and there is no cancel, because LM Studio, which carries the transfer, publishes none.
Since 0.26.0 Discover sends `sort` and `fits` too: a sort menu with SIRVIS's seven orders, a *Runs
on this Mac* toggle, and *Top 10*, which asks for ten rows with the box empty. The column beside
each model shows the figure the list is ordered by, and a note under the list says when an order
or the filter worked from a sample of Hugging Face rather than all of it. Since 0.27.0 a *Max size*
slider sends `max_bytes` (1 to 128 GB, or any), and clicking a model opens its details — author,
parameters, architecture, context length, licence, base model, task, downloads, likes and dates, a
link to its Hugging Face page — above its versions, each marked when it runs on this Mac.

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

**No one source may fill the hub — the flood guard, NERVIS 0.31.0.** On 14 September 2026 a
RAVIS loop published about a hundred events a second for eight minutes, the count bound filled,
and every older event — 4 to 14 September — was pruned. The hub now meters each source
(`source.service_type` plus `source.service_id`) before it stores (`nervis/src/nervis/flood.py`):

- An event identical to the newest stored event of its type from that source, within
  `event_collapse_seconds` (60) of its last sighting, is counted onto that row — `repeats` and
  `last_received_at`, read back as `_repeats` and `_last_received_at` beside the envelope. Trace
  ids, and a traced event's time, are part of "identical" unless the source is over its rate.
- A source may send `event_source_burst` (120) events at once and `event_source_per_minute` (12)
  after that. Past it, only the newest event of each type waits, and it is stored once that type has
  been quiet for `event_guard_quiet_seconds` (5). **The latest event of each type is never dropped**,
  and whatever is still held is stored on shutdown.
- A source adds at most `event_source_daily_share` (5%) of the retention count a day — at the
  default, fourteen days at that limit fill 70% of the store. It is counted from the store, so a
  restart does not reset it; final states may run one burst past it.

Ingestion still answers 202 and counts every event accepted. The guard stores
`nervis.events.flood_guarded` when it engages (`warning`) and releases (`info`, with `held_back`,
`collapsed`, `thinned` and `types`); those are written around the guard, never through it, so they
cannot spend an allowance or engage a guard, while an event *posted* under that name is metered like
any other. SSE subscribers receive exactly what is stored. `GET /api/v1/events` carries `guard` —
`on`, `limits`, `active`, and the last day's guard events as `recent` — and the Events screen and the
Overview's Recent events card say it in words. Pruning stays oldest-first across sources, so §4.1's
cursor floor holds. The envelope producers send is unchanged. Each number's measurement is in
`nervis/src/nervis/config.py`.

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

## 12.0 How a service becomes supervisable

§12 says supervision uses *"explicit executable or package identity, working and
data directories, an environment allowlist"* and says nothing about where an
operator puts them. This is that, added when M16 was built and the gap became
obvious: the rules were written down and the way to satisfy them was not.

**An adapter, per service.** `POST /api/v1/supervision/adapter/{service}` records
an absolute executable path, an argument list and a working directory. The path
is resolved and checked for executability **at the moment it is configured**,
because a configuration that names something unrunnable fails at the worst
possible moment — when somebody is restarting a service *because* it is already
down.

**Two switches, and both must be on.** The family switch
(`POST /api/v1/supervision/enable`, off by default per §12) and an adapter for
the service in question. Neither alone is enough.

**`nervis_managed` is earned, not declared.** A service configured as owned but
with no adapter reports as `user_managed` — NERVIS has no way to start it, so
claiming it may is promising a control that does not exist. And a service with
an adapter that NERVIS has not *started* still cannot be stopped by it: §12's
"anything it did not start" is about the running process, not the configuration.
On a machine where a launcher brought everything up, every control refuses.

**The environment a child inherits is an allowlist**, not the parent's
environment less some deletions. §12.1's *"an admin credential in a control
plane is a control plane whose compromise is total"* is otherwise reached by
accident, through a child that inherited every credential NERVIS holds.

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
- **Holding a credential is not deciding to use it (added 5 Sep).** The four rules above kept the
  credential away from the browser and left the *decision* ungated: six routes proxy RAVIS
  configuration mutations, and until this anything able to reach NERVIS's port could invoke them
  while holding nothing. That is the gate `ECOSYSTEM_RUNBOOK.md` §15.1's item 4 closed at RAVIS,
  re-opened one hop up, in routes whose own docstrings cite it. NERVIS mints a control token per
  process, serves it inside `index.html`, and requires it back on those six —
  `nervis/src/nervis/api/control.py`. It is a CSRF token in the precise sense `RAVIS.md` §4.4 says
  RAVIS does not need one: RAVIS's callers present a bearer header a cross-origin page cannot set,
  while the dashboard presents nothing and the browser's willingness to send the request is itself
  the authority. A page on another origin may issue the request and may not read the document, so
  it never learns the value. A local process that can read the page can already do whatever the
  page can do; that boundary is the operating system's, and this does not pretend to hold it.

**Gate:** a credential issued for one peer never appears in a request to another, no credential is
served to the browser by any endpoint, and every route that spends a peer credential on a mutation
requires the control token — proved by `nervis/tests/test_control_token.py`, which lists them
rather than deriving them (seventeen since NERVIS 0.30.0, nine of them Codex's since 0.32.0: the sign-in's five
and the Codex card's four, the card's skill switch having become NERVIS → Skills'
`POST /api/v1/ravis/skills`), so one
added without the gate fails the list.

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

### Built, and what is graded — 9 September 2026

`nervis/src/nervis/api/code.py` serves `/code/` and
`nervis/src/nervis/code_proxy.py` owns the sessions. Every clause above is
implemented: a session is opened deliberately per workspace and carries an
HttpOnly, `SameSite=Strict` cookie; the upstream comes from configuration and
nothing in a request can move it; paths that leave the editor are refused rather
than normalised; `Location` is rewritten onto `/code/` when it points inside the
editor and dropped when it does not; the four browser security headers ride
every proxied response; NERVIS's own credentials are stripped before the request
leaves and the editor's own cookies are not; sessions end after 30 minutes idle
or 8 hours absolute, enforced when presented rather than swept.

The gate's ten tests are `nervis/tests/test_m14_code_proxy.py`, each driving the
real application. Each was checked by disabling the mechanism it names and
confirming it fails.

**The proxy is opt-in, and the reason is the editor's data rather than the
proxy's quality.** VS Code's web build keeps its state — secrets, chat history,
settings, workspace-trust decisions — in the browser's IndexedDB
(`vscode-web-db`, `vscode-web-state-db-*`), which is scoped to an **origin**.
Framing the same editor through NERVIS changes that origin, so an editor that
had provider keys in it opens with none of them: nothing is deleted, and
everything is invisible. That happened to a real operator on the day the proxy
shipped — they opened the tab and reported their API keys gone — and it is the
reason `code_proxy_enabled` defaults to **false**. A deployment that starts on
the proxy loses nothing; an existing one moves its keys deliberately, or not at
all. The old address still holds them, and opening it directly is the recovery.

**The header the editor cannot work without.** code-server refuses any request
whose `Origin` does not match its own host — its CSRF defence — and behind a
proxy those never match: the origin is NERVIS's, the host is the upstream's. It
resolves this the way a reverse proxy is expected to, by preferring
`X-Forwarded-Host`, so NERVIS sends the authority it was itself asked on. The
failure without it is worth recording because it points nowhere near its cause:
the HTML and every asset load, the WebSocket upgrade is answered `403`, the
workbench renders an *empty frame*, and the only complaint is `ETIMEDOUT` from
the browser's own side. Any `X-Forwarded-*` arriving from a caller is dropped
before NERVIS sets its own — a header that decides an origin check is not one a
request supplies.

**Supported browser and host matrix (the proxied path).** Graded live against a
running stack, not inferred from the direct-embed matrix, which measures a
different arrangement.

| Host | Browser | Result | Evidence |
| --- | --- | --- | --- |
| macOS 26 (Darwin 27), loopback `http://127.0.0.1:8790`, code-server 4.135.0 | Chromium 152 | `PASS` | **The full workbench, signed in and rendering through the proxy** — file explorer, editor, and the Clarvis panel active inside it — reached by logging in to a throwaway password-protected code-server rather than the operator's own. Before that: the login page framed same-origin; `GET /code/` answered `302` with `location: /code/login`, its own redirect rewritten onto the proxy rather than sent absolute, carrying `frame-ancestors 'self'`, `SAMEORIGIN`, `nosniff` and `no-referrer`; `..%2f..%2fetc/passwd` and an absolute URL as a path both answered `409` |
| Same host, Firefox | — | `NOT_TESTED` | No second browser was driven |
| Same host, Safari | — | `NOT_TESTED` | No second browser was driven |
| Non-loopback host, `https` | — | `NOT_TESTED` | The deployment this ships as binds `127.0.0.1`; the `Secure` cookie attribute follows the scheme and has not been exercised over TLS |

**One browser and one host is why `nervis.code_server_proxy@1` is `degraded`
rather than `available`.** The capability is not gated on the code being
written — it is written and gated — but on this table having more than one row
that says `PASS`.

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

**Built, 9 September 2026.** `code_tab_enabled` (default on) closes `/code/` itself rather
than only hiding the tab — a switch that hid a screen while the route kept serving would be
off in the one place that cannot reach the editor anyway. `code_server_binary` falls back to
`PATH`, the same order `tools/run.py` uses. `code_workspace_roots` is the menu the session
offers, and falls back to `workspace_path`. `clarvis_vsix_path` names the package;
`clarvis_auto_install` (default **off**) installs or updates it at startup, because putting
software into an editor somebody else manages is not a thing to do because a default said so.
`nervis/src/nervis/code_extension.py` reads the offered version out of the `.vsix` itself
rather than off its filename, refuses any package that is not `krimkerre.clarvis`, and
installs with `--force` so update does not mean uninstall-first. `GET /api/v1/code/extension`
reports configured, offered, installed and what is due; `POST` installs and takes no
arguments, so a request cannot choose the file.

The Code tab may open Clarvis, RAVIS, SIRVIS or NERVIS source with Clarvis installed — making
NERVIS self-hosting as a development environment. Useful, and a pleasingly unnecessary amount of
recursion. **Not required for MVP.**

---

# 14. NERVIS API and storage

```text
/api/v1/health   /api/v1/events   /api/v1/chat      /api/v1/diagnostics
/api/v1/system   /api/v1/traces   /api/v1/settings  /api/v1/services
/api/v1/documents
```

`/api/v1/documents` hands back a file NERVIS wrote, from the workspace and nowhere else, on an
extension allowlist rather than a denylist — a workspace is a directory a person chose, and the
interesting files in one are the ones nobody thought to forbid. PDFs and markdown are served as
downloads; images are served `inline`, because a download prompt in place of the picture a
conversation is showing is the browser being helpful in the one way nobody asked for.

Plus the MEP `/ecosystem/*` surface. **Most specialized data stays fetched from the
authoritative service.**

```text
Service · ServiceInstance · HealthSnapshot
Event · Trace · TraceSpan
ChatConversation · ChatMessage · ChatAttachment
WorkspaceReference · DiagnosticAnalysis
Setting
```

Large raw log files stay on disk separately. Use database migrations.

**Settings sections:** General, Services, Chat, Diagnostics, Code, Privacy, Storage, Advanced —
with a health-test button per service.

**As built, 12 September 2026.** NERVIS → Settings has nine collapsible sections, and they are
not the eight above: Ecosystem, Screen, Files, Voice, Privacy and retention, Backup, Unattended
work, What NERVIS remembers, and Supervision. The per-service health test is built — a **Test**
button on each row of the Services table inside Ecosystem, which reads that service's health
and capabilities. There is no General, Chat, Diagnostics, Code, Storage or Advanced section;
§13.5's Code settings are NERVIS configuration (`nervis/src/nervis/config.py`) rather than
controls on this screen.

---

# 15. Security and privacy

- Loopback-first, and **loopback-only for now**: a non-loopback bind fails to start
  whatever it is configured with — `nervis/src/nervis/config.py` refuses it as a fatal
  finding before the port is bound — because the TLS this clause requires was validated at
  startup and never reached the listener. That was item 2 of the stabilization track, and
  it was closed by making all three services loopback-only, not by building remote access
  (`ECOSYSTEM_RUNBOOK.md` §15.1). Remote NERVIS still requires TLS, authentication,
  authorization and explicit exposure. **As of 12 September 2026 none of that is built and
  nothing owns it**: the stabilization list that was to build it is finished and gone, no
  milestone in §21 carries it, and remote internet access is a first-release non-goal
  (§21.3). Localhost-only MVP may use simple local auth; a NERVIS session token follows.
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
| **RAVIS** | Chat and routing views disable; SIRVIS and Clarvis direct-provider behaviour continue; the Codex card and Overview line go absent, and no Codex task can run, since RAVIS hosts them (M28 and RAVIS M29); the menu bar's Codex line says RAVIS isn't answering and offers no task control |
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

**As built, 12 September 2026.** Three commands exist, in `nervis/src/nervis/cli.py`:
`nervis serve`, `nervis doctor`, and `nervis restore-database`, which puts back the backup
taken before a migration (runbook §13). None of the others above is built. `nervis doctor` is
also narrower than the sentence above: it reports configuration findings (a non-loopback bind
among them), opens and migrates the database, says whether the dashboard file is present,
prints every capability NERVIS advertises with its reason, and lists the address each peer
would be probed at along with any the SSRF guard refuses — **without contacting any of them**,
so it still works with the whole ecosystem down. It does not check Python, ports,
code-server, the Clarvis VSIX, LM Studio, Ollama, disk or permissions.

---

# 18. Notifications and voice

Surface service offline, benchmark finished, budget threshold, route-failure spike, memory
pressure, swap warning, and *Clarvis waiting for approval*. **Keep notification volume low.**

**As built, 12 September 2026.** Two things file notes. The registry files one when a service
changes state (`nervis/src/nervis/app.py`), which covers *service offline*. Unattended work
(§21 M25), when switched on, files notes for its two triggers in
`nervis/src/nervis/background.py`: a service that has stayed unreachable or degraded for a
while, and a once-a-day digest of what the event hub recorded. **Nothing files a note for a
finished benchmark, a budget threshold, a spike in route failures, memory pressure or a swap
warning.** *Clarvis waiting for approval* is shown but never filed: the status line at the top
of every screen changes to "Waiting for you", naming the editor window and the kind of
approval, and no note is written.

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
| **M0** AUTOMATED VERIFIED | Foundation — package, FastAPI, config, SQLite, migrations, logging, web shell, CLI, **NERVIS's own `/ecosystem/*` surface** (§3.1) | `nervis serve` starts; the browser opens the dashboard; `nervis doctor` works; the database migrates cleanly; **no external service is required**; NERVIS answers its own identity, health, capabilities and version, and MEP conformance fixtures pass at one pinned protocol version — Stage 1 exits here, and M2 is the client half |
| **M1** IMPLEMENTED | System telemetry — CPU, RAM, swap, disk, process list, basic thermal | Dashboard updates live; sampling does not noticeably load the machine; missing telemetry degrades to Unknown; metrics survive a browser reconnect |
| **M2** AUTOMATED VERIFIED | Service registry — health model, registry, RAVIS/SIRVIS/LM Studio/Ollama probes, capability negotiation | Each service shows state independently; an offline service never breaks the page; health timeouts are bounded; status changes update live; spoofing, duplicate, stale, auth and version tests pass. **Automated verified, 12 September 2026:** `nervis/tests/test_m2_registry.py` covers that test list and its 76 tests pass — an observation cannot rewrite what a service is and a disguised remote host is refused (spoofing), a duplicate id does not overwrite a live instance, an entry goes stale on read rather than by a timer, a rejected probe reads unauthorized rather than unreachable, and an unsupported major version reads incompatible — along with a silent peer bounded by the probe deadline and the services route still answering while a peer is slow |
| **M3** LIVE VERIFIED | RAVIS integration — health, providers, models, routes, usage, sessions | NERVIS inspects RAVIS **without touching its DB**; provider health and recent routes visible; the RAVIS-unavailable state works |
| **M4** AUTOMATED VERIFIED | General chat — RAVIS-backed, streaming, history, mode selector, route inspector | `ravis/auto` chat works; streaming works; route details correspond to the real RAVIS decision; history persists locally; a RAVIS outage produces a clear error; **a NERVIS session never appears as a Clarvis session**; title generation is marked as a background call |
| **M5a** LIVE VERIFIED | SIRVIS integration, read half — models, results, system, Runtime Sets, evidence, recommendations | Existing SIRVIS results appear; **no benchmark business logic exists in NERVIS**; provenance renders correctly |
| **M5b** IMPLEMENTED | SIRVIS integration, launch half — benchmark submit, cancel, live progress. **No longer blocked**: SIRVIS M14 shipped the queue and advertises `sirvis.benchmarks.jobs@1` as *available* — "submit, poll and cancel over the queue (M14); one benchmark at a time, because two measure each other" — and NERVIS submits and cancels through it as §12 operations. What is left is the third word of this row: a run is polled, not streamed, which is why `nervis.sirvis_views@1` publishes itself DEGRADED with that as its reason | A benchmark launches through the SIRVIS API; progress streams live |
| **M6** AUTOMATED VERIFIED | Event hub — envelope, ingestion, SSE broadcast, bounded persistence, filters | Events appear live; RAVIS events ingest; retention is enforced; **an invalid event cannot crash the hub** |
| **M7** LIVE VERIFIED | Distributed tracing — trace IDs, correlation, timeline | One RAVIS request forms a trace; multiple events correlate; missing spans render gracefully; filters work |
| **M8a** LIVE VERIFIED | Clarvis Bridge integration, receiving half — §5.1's **authenticated local dynamic registration**, per-extension-host instances, redaction, and §10.1's limits enforced structurally | A Bridge can register with a port and token it generated; two extension hosts appear separately with no cross-instance leakage; no workspace path, file content or token is stored or displayed; NERVIS has no code path that could resolve a gate |
| **M8b** LIVE VERIFIED | Clarvis Bridge integration, live half — status, mode, busy and gate state polled from a running Bridge's `/v1/status` and `config` (`nervis/src/nervis/bridges.py`, wired into `api/instances.py` and `api/chat_reads.py`); agent runs, tasks and gate history folded from the same event stream every peer ingests (`nervis/src/nervis/clarvis.py`, M9). Was blocked on Clarvis building the Bridge; it now exists (`CLARVIS.md` §6, `clarvis/plan.md` M14, signed off 29 Aug) and this reads it. **As built, 12 September 2026: the task list stays empty.** NERVIS folds `clarvis.task.started` and `clarvis.task.completed`, and the Bridge emits no task events at all — `clarvis/src/bridge/events.ts` names lifecycle, gate, chat, agent and capability events only — so no task Clarvis started or finished ever appears | Clarvis still works without NERVIS; connect/disconnect is safe; existing Clarvis safety behaviour unchanged |
| **M9** AUTOMATED VERIFIED | Clarvis diagnostics UI — status, workspace, agent run, tasks, recent events, approval waiting | Live agent activity visible; task state matches Clarvis; a stale connection is clearly shown; **no Clarvis state duplicated independently**; multi-instance isolation holds |
| **M10** AUTOMATED VERIFIED | Raw logs — per-source adapters with rotation and retention | Filters work; rotation and retention enforced; secrets redacted; a missing file does not error globally |
| **M11** AUTOMATED VERIFIED | API Inspector — RAVIS request stages, normalized request, route, provider metadata, final response | One request inspectable end to end; credentials never displayed; content follows privacy settings; transparent vs translated distinguished honestly |
| **M12** AUTOMATED VERIFIED | AI diagnostics — trace packet builder, redaction, RAVIS analysis request, local-only option | A trace can be analyzed; **the user sees exactly what will be sent**; local-only policy enforced; analysis failure does not alter logs |
| **M13** LIVE VERIFIED | code-server spike — **do not build the integration yet** | An exit report covering every capability in `CLARVIS.md` §7.1, graded `PASS` / `PASS_WITH_LIMITATION` / `FAIL` / `NOT_TESTED` — the runbook §6.2 Stage 9 vocabulary, which CLARVIS.md §7.1 also uses. **`NOT_TESTED` is the value this report most needs**: an untested combination that has to be graded pass or fail gets guessed or dropped |
| **M14** IMPLEMENTED | Code tab *(only if M13 succeeds)* — code-server management, reverse proxy, workspace launcher, Clarvis install | Code tab loads; Clarvis activates; the WebSocket survives; the workspace opens; the Clarvis panel renders; the terminal works; the proxy security suite passes. **§13.3's proxy and §13.5's settings both shipped 9 September 2026** — `/code/` with its session store and ten-test gate, and the tab served through it rather than from code-server's own port; `code_tab_enabled` (which closes the route, not only the screen), `code_server_binary`, `code_workspace_roots`, `clarvis_vsix_path` and `clarvis_auto_install`, with NERVIS installing and updating the extension itself rather than printing a command to copy. What remains is coverage rather than code: the proxied path's browser and host matrix (§13.3) has one graded row |
| **M15** IMPLEMENTED | Browser Clarvis compatibility — **address only issues found in M13**, no speculative porting | The existing VSIX stays one artifact if possible; VS Code stable and VSCodium still pass regression; the code-server path passes the agreed matrix |
| **M16** AUTOMATED VERIFIED | Service supervision — ownership modes, start, stop, restart, PID verification | Only managed services are controlled; **external services are never killed**; crash recovery works; a restart does not create a duplicate process |
| **M17** IMPLEMENTED | Unified diagnostics — cross-service trace, health overlay, log correlation, benchmark/runtime context | A Clarvis → RAVIS → provider trace is visible; SIRVIS runtime evidence links where available; a broken link still produces a partial trace. **Still unwitnessed, as of 12 September 2026:** the health overlay and log correlation are built and the SIRVIS evidence link was seen live (`STATUS.md`, M17, 2 Sep), but no Clarvis → RAVIS → provider trace has ever been seen whole — a chat turn draws only NERVIS and RAVIS, and the Clarvis leg needs an agent run in an editor window. `nervis.diagnostics@1` publishes itself degraded for that reason |
| **M18** LIVE VERIFIED | Polish — responsive UI, navigation, error states, onboarding, settings, backup/export | A fresh install is understandable; no service is required for dashboard startup; every unavailable state has a sensible explanation; settings import/export works **without secrets** |
| **M20** LIVE VERIFIED | Conversation memory — recall across conversations, not merely history within one | A new conversation can draw on an earlier one; what was recalled is **shown, with its source conversation**, never silently injected; recall is local by default and follows §7.2's storage rule; **turning it off leaves ordinary chat unchanged**; a recalled passage is fenced before it re-enters a prompt (§11.5), because a stored assistant reply is model output and re-admitting it unfenced is the same trust mistake in a longer loop |
| **M19** LIVE VERIFIED | macOS launcher — `NERVIS.app`, a menu bar app. **Rescoped 12 September 2026 by the owner:** one app that starts the whole stack from this repository, instead of a bundle per service, so a code change never needs repackaging. Built by `nervis/packaging/macos/build_app.sh` from `NERVISMenu.swift`; it holds the repository's path and runs `tools/run.py`, reading `run.py status --json` for everything it shows. No Dock icon: the NERVIS mark in the menu bar — its pupil solid while the stack answers, faint when part of it does not, blinking while NERVIS has unread notifications — and a menu with the unread count, the dashboard, each service — a stack line opens that part of the stack in the browser — LM Studio (with its installed models, loadable through SIRVIS) and Ollama, CPU, GPU and memory (CPU and GPU red above 85%), and a Quit that stops the stack. A service whose process is running but has not answered for longer than a start waits is named under its line, with what clears it, rather than shown as not running (13 September). Its log keeps every run and notes one that ended without quitting. It opens the UI from that menu rather than on launch, by the owner's choice. **Seen live**, every clause of the exit: it started a stopped stack in 10 s; opened with the stack already up, it started nothing; on 12 September the owner opened the dashboard from its menu and quit from it, and the quit stopped all five services, left no service process and no listening port behind, and the app exited. A hang on quit found earlier that day was fixed first, and verified against a stand-in launcher, where the build before the fix reproduced it. Installed as `/Applications/NERVIS.app`, which `build_app.sh` keeps in step; it does not open at login, by the owner's choice | Launches the service, opens the UI, exits cleanly, respects independently running services, leaves no orphan process |
| **M21** IMPLEMENTED | Notification centre — a durable store for things NERVIS wants to tell the user, with a screen, an unread count and dismissal | A finished task, a service transition and a question survive a reload and a restart; **every note says why it exists**, and one produced by a model also says which model and what it cost; **every note acted on is one the reader could see** — a selection, or "mark all read" over exactly what is listed, resolved to one request per note, so nothing the server expands on its own can reach a note nobody has read; **no per-kind mute and no standing rule**, which is the actual failure this guards against rather than bulk action as such; the voice announcement and the note are one event seen twice, not two events — muting speech must not lose the record. **As built, 12 September 2026: nothing files a note for a finished task.** The only producers are the registry's service transitions and unattended work's two triggers (§18), so this exit's service transition has a producer and its finished task does not |
| **M22** AUTOMATED VERIFIED | Proposal outcomes — record whether a confirmed thing was accepted, declined or edited, and let later proposals read it | An offer's fate is stored with the offer; a preference drawn from it is **visible in the proposal that uses it** — "you declined this twice, so this suggests the local model" — never a silent change of behaviour; clearing the record restores the unlearned proposal exactly; **no outcome is inferred from silence**, because a person who closed the tab did not decline |
| **M23** LIVE VERIFIED | Learned notes — a file NERVIS appends to, beside the hand-written knowledge files and retrieved the same way | What NERVIS learned is a file a person can read, edit and delete; a learned note carries its date and what prompted it; **it never overwrites a hand-written note**, and where the two disagree the hand-written one wins and the conflict is shown; `tools/knowledge_check.py` gates the learned file exactly as it gates the others |
| **M24** AUTOMATED VERIFIED | Planning — propose an ordered sequence of operations from the closed set, confirmed once, stoppable | Every step is an operation §12 already allows and nothing new is invented; the whole plan is shown before anything runs; **each step remains individually bounded and reversible**, so one confirmation is an ordering decision and not a blanket approval; a stop takes effect at the next step boundary and says what had already happened; a step that fails halts the plan rather than continuing past it |
| **M25** IMPLEMENTED | **Background thinking — NERVIS may run a model with nobody watching.** The first four milestones change nothing about what NERVIS may *do*; this one lets it start work unprompted, which is why it is last. Unattended work runs in the pool `background.pool` names, defaulting to RAVIS's `ravis/free-api` (RAVIS M28) — whose candidates are remote and free, so nothing here loads a local model. **RAVIS M26's `ravis/background` is unbuilt and M28 serves its purpose**; the guarantee below holds for the default and is only as good as a pool somebody else chooses so it never competes with the chat the user is in, and everything it produces lands in M21's notification centre rather than acting: a finished summary, a noticed pattern, or a **question** — which is filed as a note like everything else here, with the question in its text. *Opening an answerable chat session from one is not built*: the note is the whole of it today | Unattended work never contends — a background run and an interactive reply are concurrent and neither pays a model load for the other; **every background run is attributable**, naming what prompted it, which model ran and what it cost, queryable by pool in `/api/v1/usage`; a trigger is disableable individually and all of them together, and disabling stops future runs rather than hiding past notes; **§12's gate does not move** — background work may propose and may notify, and an act still needs the same confirmation it needs when the user is watching, so nothing here is a path to unconfirmed action; a question NERVIS asks carries the context that prompted it, so answering does not require reconstructing what it was thinking; it runs under **its own session id** so affinity never drags chat onto its model; **what "does not contend" resolves to is the machine's business rather than this milestone's** — a resident local model on a workstation with an idle card, a small hosted one on a laptop whose runtime is cold; the §9.6.1 background marker is **not** used, because "must be free" resolves to a local model and defeats the parallelism this exists for |
| **M27** IMPLEMENTED | **Hand a coding task to Clarvis, through the workspace.** Chat describes a task and NERVIS writes it into the shared workspace as a file — `clarvis-task.md` in a new folder of its own under `nervis-tasks/`, opened in Clarvis as that task's workspace so one task's `plan.md` never becomes another's; Clarvis picks it up through the flow it already uses for its own plans, shows the handoff prompt editable before anything runs, and gates every tool call as it always did. **The whole point is that this needs no new authority.** `nervis.document.write` is already an enumerated §12 operation bounded to `NERVIS_WORKSPACE_PATH`, and this is that writer with a different template — NERVIS never invokes a tool, never resolves a gate, and never starts the run. Progress comes *back* through `clarvis.events@1`, which the Bridge already publishes, so neither side gains a write path into the other | A task described in chat becomes a file the person confirmed, and Clarvis offers it as a build with the prompt visible and editable; **the run is started by a human in the editor**, and NERVIS's record of it is events it was sent rather than a status it assumed; **the destination is checked as far as it can be, and the limit is stated** — NERVIS refuses with no workspace configured and refuses with no Clarvis registered, and where Clarvis publishes a workspace *label* it refuses on a mismatch. Where no label is published, which is `CLARVIS.md` §6.1's default, **the offer says the destination cannot be verified** rather than implying it was: the raw path and name are private by design and `workspace_id` is salted, so NERVIS cannot compare directories and must not pretend to. This clause originally read *a mismatch is refused at proposal time*, which is unachievable without weakening §6.1 — corrected on building it rather than by relaxing the other document; the handoff file is marked as NERVIS-authored so a person approving it can see where it came from; **§6.7 is untouched** — with the Bridge stopped, every part of this still works, because the interface is a file. **Corrected on 10 September 2026:** each task now gets a new folder of its own, opened as that task's workspace *after* the handoff, so no window registered beforehand can be looking at it — the registration and label refusals above were removed with that change, and pressing Hand over now opens the new folder in the Code tab at the editor's own address (with the proxy on, the tab opens its configured workspace and the folder is opened by hand). **Later the same night:** each folder is named for its task (`pomodoro-timer`) by a background RAVIS call, and when no name can be chosen — the task too vague, the model unreachable — NERVIS asks for one on the card and writes nothing until it has it; and Clarvis now plans a handed-over task through its interview, with the task pre-typed as the first answer, instead of offering to run it (`CLARVIS.md` E-C8). **On 14 September 2026 (NERVIS 0.29.3, owner decision):** the new folder starts as a git repository — initialised on `main`, with the brief as its first commit, signed with git's own configured identity or, when git has none, `NERVIS <nervis@localhost>` passed for that one command so no git configuration changes — because Codex tasks save their work as commits and refuse a folder that is not a repository. Git runs as an argument list with a timeout and no prompts, only in the folder `write` has just made (directly under `nervis-tasks/` after symlinks are followed, holding only the brief, and the repository's own root rather than any enclosing one), from its own module (`nervis.handoff_git`) so `handoff.py` still imports nothing that can reach anything. When git is missing or fails, the folder and brief are written exactly as before, the failure is logged, anything git had started is removed, and the answer says the folder is not a repository yet; Clarvis offers to set one up. Folders made earlier are not touched, and §6.7 is unaffected: git is a local program, not a path to the editor. **State, on reverification against §14.8:** the Clarvis half was rebuilt on 5 September 2026 — the task is armed as a real offer rather than announced, and the file now survives until the answer and is re-read on yes, so *editable before it runs* is true rather than promised (`CLARVIS.md` E-C8 carries the detail). This row stays IMPLEMENTED because its own clause is about what happens **in the editor**, and NERVIS's suite cannot reach one: the pair moves on a single live run — a real write from here, a real editor window, the offer seen and answered there |
| **M28** | **Codex's allowance and tasks, observed.** Specified 13 September 2026 (runbook §2.2, §8's *Codex*). **Built so far, the launcher's part (N1a, 13 September 2026, 0.28.11):** Codex's entry in `tools/run.py status --json`, read from RAVIS's `/api/v1/codex`; `run.py codex sign-in`, `cancel-sign-in`, `stop` and `reprove`; the owner's command-line credential, which no service's environment holds; and RAVIS's Codex executable and task-folder settings. They answer once RAVIS serves those routes (M29). **The dashboard's sign-in (N2's first part, 13 September 2026, 0.28.12):** on RAVIS → Credentials, where the owner looked for it — Sign in with ChatGPT, the waiting page's link, time left and Cancel, Sign out, This is my account and Try again — through five control routes, four of the seven below (sign in, cancel it, sign out, confirm the account) and a read of the waiting sign-in so the page can open it again; `nervis/tools/codex_check.js` is its gate. **The allowance (0.28.13):** a Codex tile beside Spend on the RAVIS Dashboard, in place of the active profile's tile at the owner's request, and the Overview's line, without task counts. **The Pools row (0.28.15):** a read-only **Clarvis Codex · `ravis/clarvis-codex`** row on RAVIS → Pools, straight after the two Clarvis pools, because the owner looked for Codex there. It is composed on the dashboard from `/api/v1/codex`, not from RAVIS's pools API, since Codex is not a pool: it says Codex runs coding tasks through the ChatGPT plan, not chats, and has no fallback, and shows the state as a chip, the tightest window's allowance with its reset (details in the tile's tooltip) and, signed out, the link to Credentials; with RAVIS not answering, "—" and why. `codex_check.js` asserts it. The same release renamed the engine's id from `ravis/codex` (owner decision). **The Codex card and the menu bar's line (N2b and N1b, 14 September 2026, 0.29.0):** under RAVIS → Dashboard's headline tiles, each Codex task RAVIS lists with its state, wait, model, effort and *reconnecting*, and **Stop…** beside a running or waiting task, in two clicks, forwarding the folder, the turn and the page's `Idempotency-Key` to RAVIS's owner Stop; the sites Codex may reach, with **Remove** beside the owner's own and RAVIS's defaults folded away; a new build's report (**Check this version**) and **Use this version…**; the Overview's line counting the tasks and opening the card; four control routes behind them; and the menu bar app's Codex line with **Stop this task…**, **Re-test the file rules…** and **Sign in to Codex…**. `codex_check.js` asserts that Stop is the only task control. Built and tested against RAVIS's contract fixtures; the live test (L) is still to come. **The card's Skills (NERVIS 0.30.0, 14 September 2026):** every skill Codex can use, as RAVIS 0.26.0 lists it, grouped by where it comes from (NERVIS's skills folder, the owner's personal skills, built into Codex) with its description and a one-click **Switch on** or **Switch off** that says it is working until RAVIS answers, through a ninth control route, `POST /api/v1/ravis/codex/skills`, forwarding only the skill's path and `enabled`; the folder and that a change counts from a task's next start or reopen named on top; and the launcher giving RAVIS the skills folder from NERVIS's workspace. `codex_check.js` asserts each state, the switch and every refusal. **Since NERVIS 0.32.0 the card's Skills are NERVIS → Skills** (§8's Codex card paragraph), with the other models' switch beside Codex's, through `POST /api/v1/ravis/skills` instead of the ninth route; the card keeps a line pointing there. As specified: the RAVIS Dashboard's Codex card and the Overview's line (§6), read from RAVIS's `/api/v1/codex` through the relay; eight control routes, each requiring the control token (§12.1's list grows by nine, with the gated read of a waiting sign-in) and presenting NERVIS's RAVIS admin credential — sign in, cancel it, sign out, confirm the account, check and accept a version, remove a site the owner added, and a task's Stop, which forwards the page's `Idempotency-Key` to RAVIS's owner Stop route. The menu bar app's Codex line in its Models section, with each task's **Stop this task…** and, while Codex is paused for re-testing, **Re-test the file rules…** — the one place the re-test starts. Paired with RAVIS M29 and Clarvis E-C9 | Card and Overview line draw every state and task state; Stop, with confirmation, is the only task control (a gate asserts it); popup-safe sign-in; the version report; the menu bar's Models line shows tasks waiting for an answer |

## 21.1 Ecosystem gate mapping

| Runbook stage | Lands in |
|---|---|
| Stage 0 — baseline and invariant lock | Before M3 — every conceptual peer call is `REAL CONTRACT`, `LABELLED TEST DOUBLE`, `DEFERRED` or `STOP`; **no invented endpoint remains** |
| Stage 0 — baseline and invariant lock | M0 (foundation), and the contract-replacement audit below |
| Stage 1 — shared protocol | M0's own `/ecosystem/*` server surface; M2 supplies the client half and the registry at Stage 6 |
| Stage 6 — NERVIS core | M1 + M2 (dashboard, degradation and the registry), M3 + M4 (RAVIS views and chat), M5a (SIRVIS views) and M5b's control half, which SIRVIS M14's queue unblocked, M11 (API inspector), M16 (supervision) |
| Stage 7 — events and tracing | M6 + M7 + M10 + M12 (AI diagnostics — §11.5's fencing rule is part of its exit) |
| Stage 8 — Clarvis Bridge | M8a + M8b + M9 + M17 — M17's exit requires a Clarvis → RAVIS → provider trace, and the runbook is explicit that Clarvis joins at Stage 8. Its Stage 7 half (RAVIS → provider correlation) may land earlier; the Clarvis leg cannot |
| Stage 9 — code-server compatibility and the Code tab | M13 + M14 + M15 |
| Stage 10 — whole-ecosystem hardening | M19 + M20 |
| **After Stage 11 — the coding handoff** | M27. Scheduled after Stage 11 and blocked on nothing technical: a handoff is a proposal, so it inherits whatever M21–M25 settle about how NERVIS proposes anything. It needs no new authority — the write is `nervis.document.write`, already in §12's closed set |
| **Stage 11 — an assistant that grows** | M21 → M22 → M23 → M24 → M25, in that order. Each is useful alone and each is what the next reads from: the notification centre is where everything posts, outcomes are what preferences are learned from, learned notes are what a plan reasons with, and background thinking is the only one that needs a model to run unattended. **The order is also the risk order** — the first three change nothing about what NERVIS may *do*, and the gate on acts stays exactly where §12 puts it through all five |
| **After M17, ahead of M19 by decision** | M18 (polish), built before packaging so a UI change does not need re-wrapping into `NERVIS.app` to test. Scheduling note only — the exit criteria are what M18 actually settles |
| **Unscheduled — Codex tasks through RAVIS (runbook §2.2)** | M28, paired with RAVIS M29 and Clarvis E-C9. Its contract and fixtures landed on 13 September 2026; it is built after RAVIS's relay and lock increments, since everything it shows and its one control are RAVIS's, and the order is in `STATUS.md` |

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
read raw. It came down in reviewable passes — 22 sites, then nine.

**Finished 4 Sep: the count is zero and the ceiling with it** (§16 item 3). The
last nine were not nine separate edits. Six ran through two shared helpers,
`kpis` and `prov`, and those now escape by default with an explicit `safe()` for
the two callers that hand them real markup — so the fix is structural rather
than a sweep, and a screen added tomorrow inherits it instead of having to
remember. The remaining three were a settings table, a machine name and
`orUnknown`, whose absent branch returns markup and whose value branch therefore
looked like markup too.

The gate gained the question it used to decline to ask. It probed element
injection and over-escaping; quoted attributes were left to be settled by
reading `escapeHtml`, which is a defensible line at nine sites and the wrong one
at zero, where a bespoke escaper at some call site covering `<` and forgetting
`"` is the only way back in. `attributeBreakout` asks it directly across all 36
screens, and was checked by reopening a real site and watching it report.

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
