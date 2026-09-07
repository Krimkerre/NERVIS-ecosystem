# The NERVIS Ecosystem

**From ELI5 to technical architecture**

**Status:** Conceptual overview — the front door, not a build specification
**Consolidates:** `The Clarvis Ecosystem — From ELI5 to Technical Architecture.md`
**For contracts and milestones:** see `CLARVIS.md`, `SIRVIS.md`, `RAVIS.md`, `NERVIS.md`
**For build order and the shared protocol:** see `ECOSYSTEM_RUNBOOK.md`

---

# Part 1 — Explain it like I'm five

Four applications. Each has a deliberately over-engineered name, because apparently one
unnecessary backronym was not enough.

## SIRVIS — the tester

**S**ilicon **I**nference **R**untime **V**alidation & **I**ntelligence **S**ystem

- **Silicon** — it cares about the actual hardware in your Mac.
- **Inference** — it measures AI models while they are actually answering.
- **Runtime** — it compares things like MLX and llama.cpp.
- **Validation** — it tests whether models really perform as expected.
- **Intelligence** — it turns measurements into recommendations.
- **System** — it exposes all of that through an app, an API and a service.

SIRVIS tries different AI models and asks: *How fast are you? How smart are you? How much
memory do you need? Can two of you run together without turning the Mac into a swap-file
enthusiast?*

It keeps score. So SIRVIS knows which local models actually work well **on this particular
machine**, rather than trusting theoretical specs or somebody else's benchmark.

## RAVIS — the chooser

**R**untime-**A**daptive **V**endor **I**ntelligence **S**ystem

- **Runtime-Adaptive** — its decisions depend on what is happening *right now*: loaded models,
  available memory, latency, congestion, provider health.
- **Vendor** — it can pick between different AI providers and local runtimes.
- **Intelligence** — it uses policies, benchmarks, costs, capabilities and observations.
- **System** — it is a reusable gateway for many applications, not a feature of one.

You might have lots of models available. Some live on your Mac. Others live at OpenAI,
Anthropic or Google. RAVIS gets a request and decides *which AI should handle this?*

For a simple question it might pick a small fast model already running locally. For something
difficult it might choose a stronger cloud model. Say *"nothing leaves this computer"* and it
only chooses local models. Say *"give me the strongest answer, cost is secondary"* and it routes
differently.

SIRVIS gives RAVIS real benchmark evidence, so RAVIS never has to guess how well a local model
performs.

## CLARVIS — the programmer

**C**lippy-**L**ike, **A** **R**ather **V**ery **I**ntelligent **S**ystem

A deliberate mash-up of **Clippy** and **JARVIS**: Clippy's habit of living beside your work,
with something much closer to JARVIS's competence.

Clarvis lives inside VS Code. Tell him *"fix this bug"* and he reads your project, edits files,
runs tests, inspects failures and works through the problem.

He is deliberately tied to the project and editor window he was started in. He does not roam
around the computer. That workspace-bound, extension-host lifecycle is an explicit design
decision, not an implementation accident.

Clarvis can also ask RAVIS *"I need a model for this coding job — which one?"* So Clarvis
concentrates on **doing the programming** while RAVIS concentrates on selecting the intelligence
behind him.

## NERVIS — the control room

**N**etworked **E**cosystem **R**untime **V**isualization & **I**ntelligence **S**ystem

Also a deliberate metaphor: NERVIS is the **nervous system** connecting everything else.

- **Networked** — it talks to the other local services.
- **Ecosystem** — it spans SIRVIS, RAVIS, Clarvis, local model hosts and system telemetry.
- **Runtime** — it shows what is actually running right now.
- **Visualization** — dashboards, traces, charts, diagnostics.
- **Intelligence** — it helps interpret failures and system behaviour.
- **System** — a complete dashboard and service, not a single monitoring page.

Open NERVIS and you can see how much RAM is in use, which models are loaded, whether RAVIS is
healthy, whether SIRVIS is running a benchmark, what Clarvis is doing, and why that request
failed.

NERVIS also has a normal general-purpose chat box. If you just want to ask *"what's a good
recipe for pancakes?"* you do not need to open VS Code and summon the sarcastic coding butler.
You ask NERVIS, NERVIS asks RAVIS, RAVIS picks a model, the model answers.

**NERVIS has a character too — a different one.** Where Clarvis is a sarcastic butler who
judges your code, NERVIS is a chief of staff who reports the situation: formal, unflappable,
faintly amused, and above all *anticipatory*. The value is in what it volunteers before you
ask — *"RAVIS has failed over to cloud four times in the last eleven minutes, always on the
same runtime timeout. Shall I show you the trace?"* It is never at anyone's expense, and it
never gets witty about a number. Eventually it speaks, in its own voice, distinct from
Clarvis's. See `NERVIS.md` §18 for the split and §18.2 for why speech is a privacy decision
rather than a preference.

## The four at a glance

| App | Backronym | Job |
|---|---|---|
| **SIRVIS** | Silicon Inference Runtime Validation & Intelligence System | Benchmarks and understands local AI performance |
| **RAVIS** | Runtime-Adaptive Vendor Intelligence System | Chooses the best local or cloud model |
| **CLARVIS** | Clippy-Like, A Rather Very Intelligent System | Acts as the coding agent inside VS Code |
| **NERVIS** | Networked Ecosystem Runtime Visualization & Intelligence System | Connects, monitors, visualizes, chats, diagnoses |

> **SIRVIS knows. RAVIS chooses. CLARVIS acts. NERVIS connects.**

```text
                     YOU
                      │
                   NERVIS
               the control room
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
       SIRVIS       RAVIS      CLARVIS
        tests       chooses      acts
```

That is the whole idea in one picture.

---

# Part 2 — A slightly more grown-up explanation

The four applications are intentionally separate. They are not four screens inside one giant
program. Each has a specialist job, and each stays useful on its own.

That separation gives the ecosystem clear ownership:

```text
SIRVIS   owns benchmark truth.
RAVIS    owns routing decisions.
CLARVIS  owns coding-agent actions.
NERVIS   owns presentation, observability and coordination.
```

If two parts of the system appear to disagree, it should always be obvious which one is
authoritative for that kind of information.

---

# SIRVIS in more detail

Its defining question: **what actually works well on this machine?**

Designed for Apple Silicon macOS first, supporting GGUF via llama.cpp and MLX, with LM Studio as
the first orchestration backend. Native llama.cpp, mlx-lm, Ollama and others follow through
adapters.

SIRVIS discovers local models, browses what is available, downloads GGUF or MLX variants, loads
and unloads them, controls load configuration, benchmarks inference performance, evaluates
quality, measures memory and swap, tests different context sizes, compares quantizations and
runtimes, preserves raw outputs, compares runs historically, and recommends models for a
specific machine.

It also treats **multiple simultaneously loaded models** as first-class benchmark targets.

## Why multi-model benchmarking matters

A machine has 64 GB of unified memory. Individually a 24 GB chat model and a 30 GB agent model
both appear to fit. Together they need 54 GB of weights *plus* KV cache, runtime overhead, OS
memory, Clarvis, RAVIS and everything else — producing memory pressure, swap, reduced throughput
and thermal effects.

> **Two models fitting separately does not prove they work well together.**

So SIRVIS measures the combination. A **Runtime Set** pairs, say, a 4-bit MLX chat model with a
GGUF Q5 coder, and SIRVIS measures combined memory, load order, swap, chat throughput, agent
throughput, and sequential, alternating and concurrent performance, plus thermal behaviour.

## Measured, estimated, unknown

SIRVIS always distinguishes them:

```text
Model A individually     MEASURED
Model B individually     MEASURED
A + B together           ESTIMATED
```

RAVIS must not pretend the third result is equally trustworthy. This distinction runs through
the whole ecosystem, and nothing is allowed to quietly promote an estimate into a measurement.

Every result traces back to machine, chip, RAM, macOS version, runtime and runtime version,
model and revision, quantization, context length, load settings, generation settings, benchmark
version and timestamp.

---

# RAVIS in more detail

Its defining question: **which model should handle this request right now?**

Applications connect over an OpenAI-compatible interface at `http://127.0.0.1:<port>/v1`, so no
application needs a separate integration per provider. RAVIS can route to OpenAI, Anthropic,
Google, OpenRouter, LM Studio, Ollama and generic OpenAI-compatible hosts, with more later.

## Virtual models

Instead of naming one physical model, a client asks for a policy:

```text
ravis/auto  ravis/fast  ravis/performance  ravis/balanced  ravis/cheap  ravis/chat
ravis/local  ravis/api  ravis/private  ravis/coding  ravis/reasoning  ravis/long-context
ravis/agent  ravis/free-api  ravis/vision  ravis/draw  ravis/clarvis-chat
ravis/clarvis-agent
```

Each pool declares what it is for. That is a statement about what a model is *for*, never
about how good it is — but it has to be made: a pool that declares nothing has no basis to
order its candidates on and falls back to alphabetical order, which is how ordinary
conversation came to be served by whichever model id happened to sort first.

RAVIS decides which actual model best represents that request at the moment, considering
capability requirements, context length, tool use, vision, structured output, user policy,
privacy, budget, provider allow/deny lists, SIRVIS evidence, local model state and load cost,
provider availability and latency, session history and prompt cache, and which application is
asking.

## Hard constraints versus preferences

This distinction is the heart of the design. `privacy = LOCAL_ONLY` **eliminates** every cloud
provider — they do not merely lose points. `vision required` makes a text-only model
ineligible, and `image output required` excludes a *vision* model rather than merely a
text-only one: reading an image and emitting one are separate capabilities that happen to
share a word, so the pool that sees and the pool that draws have no member in common.

Preferences — prefer local, prefer cheap, prefer already loaded, prefer SIRVIS-tested — affect
scoring but can never override a hard requirement.

## Why RAVIS does not just ask another LLM where to route

A naive AI router would send the prompt to a router LLM and let it pick. That adds latency, cost,
another failure point, unpredictability and debugging difficulty.

Initial RAVIS routing is deliberately deterministic, policy-driven, score-based and explainable.
A classifier may later contribute signals. **It must never become an opaque oracle.**

## Route explainability

Every route answers *why this model?*

```text
Selected: Local Qwen Coder
  + strong SIRVIS coding score   + already loaded   + no API cost
  + low measured latency         + tool support     + sufficient context

Not selected:
  Claude  — better quality, higher monetary cost
  Gemini  — slower current provider latency
```

NERVIS surfaces this prominently.

## Two kinds of evidence

SIRVIS produces controlled benchmark evidence. RAVIS also observes real traffic.

```text
SIRVIS: local model measured at 42 tok/s
RAVIS:  last 100 real requests averaged 39.8 tok/s
```

One answers *what can this model do under controlled conditions?* The other answers *what is it
actually doing during normal use?* Both are useful, and they are never conflated.

## Local model lifecycle

RAVIS views local models as `HOT` (loaded), `WARM` (recently used, worth keeping), `COLD`
(installed, unloaded) or `UNAVAILABLE`. Loading costs time:

```text
Local A   already loaded, 0.7 s response
Local B   better quality, 14 s load time
Cloud C   1.1 s expected response
```

For one simple question, B is the worst decision despite being the stronger model. For a Clarvis
session expected to make 100 requests, loading B makes sense. **That is the runtime-adaptive
part.**

## Session affinity

Once RAVIS picks a model for a conversation it prefers to stay there — for consistency, prompt
caching, context continuity and less model-load churn. It changes when a capability requirement
changes, the context limit is reached, the provider fails, user policy changes, or another model
offers a major advantage.

## Cost awareness

RAVIS tracks input and output tokens, cached input, estimated request price, actual reported
usage, and daily, monthly, per-application and per-provider spend.

```text
Monthly budget €50
  0–70%    normal routing
  70–90%   prefer cheaper/local models
  90–100%  strong cost penalty
  100%     paid APIs blocked if the budget is hard
```

---

# CLARVIS in more detail

Its defining question: **what work should I perform inside this project?**

Clarvis is deliberately a VS Code extension rather than a system daemon. It activates with a
workspace window and dies with the extension host. **This is a feature, not an implementation
detail.**

He can answer questions, plan projects, edit project files, run commands, watch builds, watch
tests, inspect diagnostics, work with Git, remember recurring failures, and execute multi-step
coding tasks — writing code, running it, and continuing until the requested task is complete.

## Workspace boundary

Clarvis's own tools stay confined to the project folder, enforced as an architectural rule rather
than left to an AI prompt. Commands have a separately defined sandbox and security model,
because shell commands have different capabilities from direct file tools.

**NERVIS must never accidentally create a path around those boundaries.**

## Model abstraction — and why RAVIS integration is clean

Clarvis already uses a model-provider abstraction covering completion, streaming, tool support
and model listing, with providers including Anthropic, OpenAI, OpenRouter, LM Studio, Ollama and
generic OpenAI-compatible hosts. It already distinguishes separate chat and coding roles.

That makes RAVIS integration unusually clean:

```text
Chat:   ravis/clarvis-chat
Agent:  ravis/clarvis-agent
```

> **Verified against Clarvis source, not assumed: the initial Clarvis ↔ RAVIS integration
> requires zero Clarvis code changes.** The existing Custom (OpenAI-compatible) provider plus the
> existing separate chat/agent model settings are sufficient. See `CLARVIS.md` §3.

## Why chat and agent models differ

Chat benefits from personality, conversation, planning, analysis and instruction following.
Agent execution benefits from coding accuracy, tool use, repository reasoning, long context and
structured calls.

```text
best chat model  ≠  best coding-agent model
```

SIRVIS can benchmark those roles independently *and together*. RAVIS can then choose the best
pair.

---

# NERVIS in more detail

Its defining question: **what is the ecosystem doing, and how do I interact with it?**

NERVIS does not replace the other services. It consumes them — providing a system dashboard,
service health, resource monitoring, general-purpose chat, RAVIS and SIRVIS administration,
Clarvis visibility, diagnostics, logs, tracing, API inspection, service lifecycle controls, and
an optional browser development environment.

## Structured events, not log scraping

Without a shared envelope, every service produces different text and every consumer guesses.
With one, `route.selected`, `benchmark.completed`, `agent.started`, `agent.completed` and
`model.loaded` all have stable meaning.

## Distributed tracing

Every operation crossing a service boundary carries a `trace_id`:

```text
trace 92A4
CLARVIS   agent request
    ↓
RAVIS     routing
    ↓
SIRVIS    local runtime lookup
    ↓
LM Studio inference
```

Without tracing you get *"Clarvis seems slow."* With it:

```text
Clarvis processing    9 ms
RAVIS routing         5 ms
provider TTFT       355 ms
generation          4.4 s
```

Now the problem is measurable.

## The Clarvis diagnostics bridge

Clarvis is not currently designed as a NERVIS service. The proposed integration adds a small
**Clarvis Bridge** inside the extension host, exposing health, workspace identity, mode, busy
state, agent run state, task events, approval state and a diagnostics summary.

**It must remain optional.** Clarvis functions normally when NERVIS is not running.

Clarvis also has a built, security-gated feature that tails VS Code logs into
`.clarvis/vscode.log`. NERVIS may consume that as a diagnostic fallback — but the preferred
integration is a structured Clarvis event, not a parsed log line.

## AI-assisted diagnostics

Select a trace, press **Analyze**, and NERVIS builds a bounded diagnostic packet — relevant
errors, trace events, system state, provider status, runtime status, selected configuration —
and sends it through RAVIS.

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

By default the packet carries metadata, event information, error messages, configuration and
health state — **never** source files, entire prompts, full conversations, API keys, secrets or
unrelated logs. If the analysis must stay local, RAVIS can be constrained to Local Only.

## The browser-hosted Code tab

NERVIS may contain a **Code** tab:

```text
NERVIS → /code/ → reverse proxy → code-server → Clarvis.vsix
```

The goal is a browser-hosted VS Code-family environment backed by a real Node-capable extension
host. Pure browser-only `vscode.dev` is the wrong target, because Clarvis uses extension-host
behaviour including OS subprocess use.

The browser IDE could open Clarvis, RAVIS, SIRVIS or NERVIS source with Clarvis installed —
making NERVIS capable of hosting the development environment used to work on the ecosystem
itself. A pleasingly unnecessary amount of recursion.

**Embedding the IDE must not imply that it inherits NERVIS authority.** Clarvis keeps its normal
workspace safety model; the browser IDE stays a separate application and process boundary; and
NERVIS must not introduce arbitrary file-write APIs merely because both interfaces appear under
the same hostname.

If framing turns out to be awkward, NERVIS links out to a dedicated browser tab and moves on.
That is an acceptable product, and the Code tab never blocks the core release.

---

# Where the secrets live

| Service | Holds |
|---|---|
| **RAVIS** | Cloud provider credentials — OpenAI, Anthropic, Google, OpenRouter. In the macOS Keychain |
| **CLARVIS** | Extension-specific credentials in VS Code SecretStorage, keychain-backed rather than in config files. Once RAVIS is the primary gateway, Clarvis needs far fewer |
| **SIRVIS** | Usually none — local benchmarking needs no cloud credentials |
| **NERVIS** | Only what NERVIS itself needs. **It must never become a plaintext central secret store** |

---

# Worked examples

**Normal chat.** *"Give me three dinner ideas."* → NERVIS → RAVIS → task is normal chat → a local
model is already loaded → good quality, zero cost → local model answers.

**Difficult reasoning.** Switch NERVIS to Performance and ask something hard. RAVIS weighs local
models against Claude, GPT and Gemini and picks the highest-ranked eligible candidate. NERVIS
shows the reasoning.

**Private conversation.** Select Private / Local Only. RAVIS removes cloud providers completely.
**No scoring calculation can override that.**

**Coding.** *"Fix the failing authentication tests."* Clarvis decides this is an agent task and
sends model work to `ravis/clarvis-agent`. RAVIS sees task = coding, role = agent, tools
required, privacy = local-preferred; consults SIRVIS coding benchmarks, loaded models, memory,
provider health and cost; and picks a model. **Clarvis performs the actual edits and tests.
NERVIS merely observes.**

**Long session.** Local model A is loaded and good (31 tok/s). Local model B is unloaded and
excellent, but costs 14 seconds to load. For one request A wins — but Clarvis signals that an
agent session expects many requests, so RAVIS spreads the 14-second load across perhaps 100
requests and prepares B. Static routers cannot do this.

**Concurrent models.** Clarvis may use a chat model and an agent model simultaneously. SIRVIS has
benchmarked the pair and knows the peak RAM, swap and per-role degradation, so RAVIS knows
whether running both locally is sensible — and may choose chat-cloud/agent-local under pressure.

**Memory pressure.** NERVIS reports 62/64 GB and rising swap. RAVIS is considering loading
another 20 GB model, but SIRVIS evidence says that combination will swap heavily. RAVIS picks a
cloud provider and NERVIS shows *local model rejected: memory pressure.*

**Provider outage.** Anthropic starts erroring. RAVIS's circuit breaker marks it unhealthy and
requests route to a fallback — without every client implementing its own outage handling. NERVIS
shows Anthropic DEGRADED at a 37% error rate with traffic diverted.

**Benchmark regression.** An MLX or LM Studio update lands. SIRVIS reruns a benchmark: generation
−8%, RAM +1.2 GB, quality unchanged. NERVIS surfaces the regression, and RAVIS may adjust its
routing preference automatically, because the underlying evidence changed.

---

# Failure isolation

| If this is offline | Then |
|---|---|
| **SIRVIS** | RAVIS still routes, falling back to its own observations, provider metadata and estimates. NERVIS shows benchmark intelligence unavailable. Clarvis keeps working |
| **RAVIS** | SIRVIS still benchmarks. The NERVIS dashboard still works, but NERVIS chat is unavailable. Clarvis may use direct providers if configured |
| **CLARVIS** | RAVIS routes normally, SIRVIS benchmarks normally, NERVIS chat works. Only Clarvis-specific status and coding-agent functionality disappear |
| **NERVIS** | **Everything else keeps working.** This is an important architectural rule — NERVIS is a control plane, not a life-support system |

---

# Why the separations exist

**SIRVIS and RAVIS** care about different operating conditions. SIRVIS wants controlled
experiments, stable machine state, repeatable workloads, thermal fairness and isolated
comparisons. RAVIS wants live load, current availability, current memory, actual latency and
current provider health. Combining them would weaken both.

**Clarvis and RAVIS** understand different domains. Clarvis understands the workspace, files,
tests, commands, Git, the user's task and the agent lifecycle. RAVIS understands models,
providers, cost, latency, capabilities and local resources. Clarvis should not have to maintain
a constantly changing model-vendor ecosystem; RAVIS should not know how to edit a TypeScript
file.

**NERVIS** has a system-wide view and a potentially long-running lifecycle. Clarvis explicitly
does not — it belongs to one editor window and workspace. NERVIS may eventually observe several
Clarvis instances at once:

```text
                  NERVIS
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
  Clarvis A      Clarvis B      Clarvis C
  project A      project B      project C
```

with RAVIS and SIRVIS shared. Per-workspace agent, shared model router, shared model
intelligence, shared dashboard. **Each Clarvis instance stays isolated to its own workspace.**

---

# Shared protocol and API authority

The ecosystem defines one small shared protocol package: health schema, event envelope, trace
IDs, service identity, capability versions. **It contains no business logic** — each application
keeps its own domain models.

```text
Preferred:  NERVIS → SIRVIS API · NERVIS → RAVIS API · NERVIS → Clarvis Bridge
Avoid:      NERVIS → SIRVIS database · NERVIS → RAVIS database · NERVIS → Clarvis state files
```

---

# Philosophy

**Security.** Prefer real boundaries, explicit policy, least privilege, local-first defaults,
visible routing and auditable decisions — over *"the prompt told the model not to."* Clarvis
already follows this for its workspace safety model. RAVIS applies it to privacy and routing.
SIRVIS applies it to reproducibility. NERVIS applies it to observability and control.

**Privacy.** Services bind localhost. Diagnostics store metadata only. Provider secrets live in
secure stores. Source code is not copied into logs. Cloud analysis is explicit. Local-only
policies are enforceable. The user must always be able to answer: *did this data leave my
computer?*

**Logging.** Structured events first, raw logs second, full request/response capture only when
explicitly enabled — with retention limits, rotation and redaction. No service is allowed to
discover that the disk is full because it has been enthusiastically logging debug JSON for six
months.

**Product.** Local-first, modular, measurable, transparent, explainable, extensible,
user-controlled. Avoiding opaque model switching, hidden cloud transmission, giant shared
databases, silent safety bypasses, infinite logs, untraceable failures and unnecessary coupling.

---

# The feedback loop

```text
             SIRVIS
      controlled benchmarks
               │
               ▼
             RAVIS
        routing decisions
               │
               ▼
            CLARVIS
           real tasks
               │
               ▼
        observed outcomes
               │
               └──────────► RAVIS history

             NERVIS
          observes all of it
```

SIRVIS tells you what *should* perform well. RAVIS sees what *actually* performs well. Clarvis
tells you whether the chosen intelligence completed real work. NERVIS gives visibility into the
whole process.

Over time RAVIS may know:

```text
SIRVIS:   Model A coding score 91
RAVIS:    Model A median TTFT 410 ms
CLARVIS:  Model A completed 93% of coding jobs successfully
```

Considerably better evidence than *"Model A is popular on Reddit."*

---

# The standard local stack

```text
NERVIS        local dashboard
RAVIS         OpenAI-compatible local gateway
SIRVIS        benchmark / local-model intelligence service
LM Studio     local inference host
Ollama        optional second local inference host
code-server   optional browser VS Code environment
Clarvis       VS Code extension
```

---

# Final mental model

Imagine a company.

**SIRVIS is the testing department.** It measures what every worker can actually do.

**RAVIS is dispatch.** It decides which worker gets each job.

**CLARVIS is the engineer.** It actually changes the code and runs the work.

**NERVIS is operations headquarters.** It tells you who is working, what resources are being
used, why a decision was made, and what went wrong when something catches fire.

> **SIRVIS knows. RAVIS chooses. CLARVIS acts. NERVIS connects.**

No single component needs to pretend to be all four. That separation is precisely what makes the
ecosystem coherent.
