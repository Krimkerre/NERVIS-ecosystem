# RAVIS

**Runtime-Adaptive Vendor Intelligence System**

> A local-first, OpenAI-compatible intelligent model gateway that routes each request to the
> best available local or cloud model according to capability, quality, latency, cost,
> privacy, resource state and user policy. **RAVIS chooses.**

**Status:** Canonical RAVIS specification — build plan and ecosystem contracts in one document
**Consolidates:** `RAVIS — Revised Complete Build Plan and Technical Specification.md`,
`RAVIS_ECOSYSTEM_ADDENDUM.md`
**Cross-references:** `ECOSYSTEM_RUNBOOK.md` for the shared protocol, build order and release gates

---

# 1. Non-negotiable rules

> **No agent may invent another ecosystem component's API, schema, capability or behaviour
> merely to complete its own milestone. If the required contract does not yet exist,
> implement against the canonical ecosystem contract where specified, use an explicitly
> labelled test double where appropriate, or stop at the integration gate and report the
> missing dependency.**

RAVIS owns provider adaptation, eligibility, routing, virtual profiles, local-runtime
coordination, route explanations, sessions, and usage and cost accounting. It does **not**
benchmark hardware (SIRVIS), edit workspaces (Clarvis), or supervise and display the
ecosystem (NERVIS).

---

# 2. The integration insight that shapes this document

Clarvis already supports arbitrary OpenAI-compatible servers through its existing **Custom
(OpenAI-compatible)** provider, and already supports separate chat and coding models. This
was verified against Clarvis source, not assumed — see `CLARVIS.md` §3.

> **Initial Clarvis ↔ RAVIS integration requires zero Clarvis code changes.**

Clarvis points its existing custom provider at RAVIS. The integration problem is therefore
almost entirely a **wire-compatibility problem inside RAVIS**, not a Clarvis feature-development
problem. Everything downstream follows from that:

1. A strict Clarvis Compatibility Contract (§8) with its own conformance suite.
2. Upstream handling split into **transparent pass-through** and **native translation** (§6).
3. `/v1/models` is explicitly cache-backed and cheap.
4. Clarvis chat and agent pools are explicit virtual models.
5. Cancellation propagation is a release criterion, not a nicety.
6. Streamed tool-call transformation is treated as the highest-risk code in the system.
7. `tools` present is a routing *signal*, not the sole role identifier.
8. SIRVIS evidence identity is keyed by build, runtime and role — never model name.
9. Clarvis Bridge and distributed tracing are **out** of the initial integration path.

---

# 3. Product vision

A local-first AI routing gateway that exposes an OpenAI-compatible API; connects to cloud
providers and local inference hosts; discovers models; normalizes capabilities; routes each
request appropriately; applies user policy; tracks latency, reliability, usage and cost;
understands which local models are currently loaded; incorporates SIRVIS benchmark evidence;
handles provider failure and fallback; supports application-specific routing profiles;
preserves session affinity; explains its decisions; and can be used by any application that
supports a custom OpenAI-compatible endpoint.

Primary platform **macOS / Apple Silicon**, architecture portable enough for later Linux and
Windows support.

The core product question:

> Given this request, user policy, available models, provider state, local machine state, cost
> constraints and benchmark evidence — which model should handle it?

RAVIS is not a reverse proxy. It is an AI gateway + capability broker + policy engine + model
router + provider abstraction + local runtime coordinator + cost controller + observability
layer.

---

# 4. External interface

```text
http://127.0.0.1:<port>/v1        OpenAI-compatible client API
http://127.0.0.1:<port>/api/v1    management API (NERVIS, UI, CLI)
http://127.0.0.1:<port>/ecosystem MEP metadata and events
```

Clients configure the base URL and optionally a RAVIS client credential.

## 4.1 MEP capabilities

| Capability | Advertise only when |
|---|---|
| `ravis.openai_compatible.chat_completions@1` | conformance passes |
| `ravis.openai_compatible.responses@1` | `/v1/responses` conformance actually implemented |
| `ravis.providers.native@1` | a translated adapter ships |
| `ravis.routing.explanations@1` | every route is explainable |
| `ravis.virtual_profiles@1` | profiles are versioned and revisioned |
| `ravis.sessions@1` | session isolation tests pass |
| `ravis.usage_cost@1` | accounting distinguishes reported/estimated/billed/unknown |
| `ravis.management@1` | the management API is authorized and audited |
| `ravis.events@1` | MEP events publish |

**Do not advertise streaming, tools, JSON or structured output, vision, embeddings or audio
unless that exact operation passes provider *and* gateway conformance.**

## 4.2 OpenAI-compatible API

Required initially:

```text
GET  /v1/models
POST /v1/chat/completions
```

Must support non-streaming and streaming responses, system/user/assistant messages,
temperature, max-token normalization, stop sequences where supported, tools and functions,
tool choice, structured output where feasible, usage metadata, provider-independent errors,
and client disconnect/cancellation propagation.

Shortly after MVP: `POST /v1/responses`. Later: `POST /v1/embeddings`. **Do not delay core
routing for secondary APIs.**

OpenAI-compatible endpoints return **OpenAI-compatible error shapes**, not the MEP error
envelope — clients parse them. The MEP envelope applies to the management API. Correlation IDs
appear in headers and logs either way.

## 4.3 `/v1/models` is a cached registry endpoint

It must be fast, cheap, non-blocking and cache-backed, reading from the in-memory registry.
Provider and catalog refresh happens separately, in the background.

```text
GET /v1/models must not:
  load models · contact every upstream synchronously · trigger benchmarks · wait on SIRVIS
```

This matters specifically because Clarvis's OpenAI-compatible provider uses the model-list
endpoint as part of availability and model-selection behaviour.

---

# 5. Virtual models and pools

Required defaults:

```text
ravis/auto      ravis/balanced   ravis/local    ravis/coding        ravis/long-context
ravis/fast      ravis/cheap      ravis/api      ravis/reasoning
ravis/performance                ravis/private
```

Clarvis-specific pools — these IDs must be **stable**:

```text
ravis/clarvis-chat
ravis/clarvis-agent
```

Direct addressing also exists: `ravis/openai/<model>`, `ravis/anthropic/<model>`,
`ravis/google/<model>`, `ravis/openrouter/<model>`, `ravis/lmstudio/<model>`,
`ravis/ollama/<model>`. Direct routes bypass dynamic selection but still receive error
normalization, cost tracking, health tracking, optional tracing and applicable policy
enforcement.

> **Verified against Clarvis source (2026-08-22): namespaced IDs work.** The
> `clarvis.{chat,agent}.model` settings are free strings with no enum or pattern; Clarvis's
> catalog filter rejects only non-chat families (`embed`, `whisper`, `dall-e`, `moderation`,
> …), which no pool ID matches; and slash-namespaced IDs are actively expected, since
> `describeOwner` derives the owner label from the `vendor/` prefix. All pool IDs above render
> as "by ravis". The picker also always offers a free-text "Type a model name…" entry, so no
> ID is unreachable.

### 5.0.1 Constraints this places on RAVIS

1. **`GET /v1/models` must answer 200 to an *unauthenticated* request within 2 seconds.**
   Clarvis's `custom` provider declares `needsKey: false`, so its availability probe issues a
   bare `fetch(baseUrl + '/v1/models')` with **no headers** and a 2-second timeout. A 401 there
   makes Clarvis report the provider **offline**, not unauthorized — an actively misleading
   failure. This is a second, independent reason `/v1/models` must be cache-backed: two seconds
   leaves no room for synchronous provider discovery.
2. **Avoid these substrings in pool IDs**, now and later: `embed`, `tts` (leading), `whisper`,
   `transcribe`, `dall-e` (leading), `image`, `moderation`, `audio`, `realtime`, `rerank`,
   `guard`. Clarvis's filter patterns are unanchored substring matches, so `ravis/image` would
   silently disappear from the list. The current pool set is clean.
3. **Set `created` on every model entry, or on none.** Clarvis sorts by timestamp only when
   *all* entries carry `created`, otherwise it falls back to alphabetical. A mixed list scrambles
   any intended ordering.
4. **`owned_by` is optional.** A placeholder such as `organization_owner`, or omitting the field,
   both yield the vendor-prefix label.

## 5.1 Pool semantics

**`ravis/clarvis-chat`** — optimized for conversation, planning, analysis, instruction
following, personality quality and reasonable latency. Tool support is optional unless the
request itself supplies tools.

**`ravis/clarvis-agent`** — optimized for coding, tool use, repository reasoning, structured
calls, long context and reliability.

> **Hard invariant: every model eligible for `ravis/clarvis-agent` must satisfy the pool's
> required tool capability.** Do not put a non-tool-capable model in the agent pool because it
> scores well on coding.

## 5.2 Pool capability invariants

A pool declares requirements, e.g. `tools = REQUIRED`, `vision = OPTIONAL`,
`minimum_context = 32768`. Every eligible member satisfies every required capability. If no
candidate satisfies the invariant, the pool is **unavailable** — never route to an
incompatible model.

## 5.3 Role identity versus request signals

`tools present` is a useful routing signal but never the sole role identifier. Signal
hierarchy, most authoritative first:

```text
explicit virtual model/pool → RAVIS metadata if supplied
  → request capability signals → heuristic task classification
```

So `ravis/clarvis-agent` outranks the mere observation that tools happen to be present.
**Do not replace Clarvis's explicit chat/agent configuration with heuristics.**

## 5.4 Profiles as versioned objects

A virtual profile specifies workload reference, hard constraints, preferences, fallback
policy, allowed providers and models, evidence-age policy, budget and version. It stores no
provider secrets. Revisions are auditable, and every session records the revision it used.

`clarvis-chat` and `clarvis-agent` revisions are **independent** — changing one must not
change the other.

---

# 6. Two upstream execution paths

This is the central architectural refinement. **Do not normalize all traffic.**

## Path A — transparent OpenAI-compatible forwarding

Used when the upstream already speaks the OpenAI-compatible protocol the client expects:
OpenAI, OpenRouter, LM Studio, generic OpenAI-compatible hosts, some Ollama interfaces.

```text
Client → RAVIS routing decision → minimal safe forwarding → upstream
       → minimal safe streaming proxy → Client
```

Goal: **preserve the wire representation as far as practical.** Avoid needless
parse → normalize → serialize cycles.

## Path B — native-provider translation

Used when the upstream does not speak the external protocol: Anthropic native, Gemini native,
other native APIs.

```text
Client OpenAI format → NormalizedRequest → native request
  → provider-native stream → NormalizedStreamEvents → OpenAI-compatible stream
```

## Why both exist

Every stream transformation can introduce bugs, and the high-risk surfaces are exactly the
ones Clarvis depends on: tool-call indexes, tool-call IDs, fragmented JSON arguments,
reasoning fields, finish reasons, usage chunks and `[DONE]`.

> **Do not normalize an already-compatible stream for architectural purity. Normalize only
> when necessary.**

Diagnostics must expose which path ran — `TRANSPARENT_OPENAI` or `TRANSLATED_NATIVE`.

---

# 7. Internal models and adapters

```python
NormalizedRequest(
    messages, system, tools, tool_choice, response_schema, modalities,
    temperature, max_output_tokens, reasoning_effort, stream,
    metadata, routing_context,
)
```

An OpenAI-compatible route retains access to the **original request envelope** for transparent
forwarding after route selection.

Normalized response (for translated providers and observability): text, tool calls, finish
reason, usage, provider, model, latency, provider request ID, reasoning metadata, cache usage.

```python
class ProviderAdapter:
    async def health(self): ...
    async def models(self): ...
    async def capabilities(self, model): ...
    async def complete(self, request): ...
    async def stream(self, request): ...
    async def estimate_cost(self, request): ...
```

Each adapter declares `protocol_mode`: `OPENAI_TRANSPARENT` or `TRANSLATED`, and implements
capability discovery, model listing, request and stream, cancellation, usage, error
classification and health.

**Required early:** OpenAI, Anthropic, Google Gemini, OpenRouter, LM Studio, Ollama, generic
OpenAI-compatible endpoint. **Likely later:** Mistral, Groq, Cerebras, Together, xAI, DeepSeek,
Azure OpenAI, AWS Bedrock, Vertex AI.

A provider family may seed defaults but **cannot assert safety- or capability-critical
behaviour without authoritative metadata or probing.** Unsupported features must never silently
disappear.

**Adapter gate:** a recorded provider fixture plus at least one authorized live smoke test
validates success, streaming, cancellation, tool use where claimed, rate limit, auth failure,
and redaction.

---

# 8. The Clarvis Compatibility Contract

Mandatory before RAVIS may be declared Clarvis-compatible. RAVIS conforms to Clarvis's
existing expectations; **Clarvis is never changed to compensate for a broken proxy.**

## 8.1 Model discovery

Expose stable IDs through `GET /v1/models`, minimally `ravis/clarvis-chat` and
`ravis/clarvis-agent`, served from cached registry state. No synchronous provider discovery.

## 8.2 Streaming

Clarvis consumes streamed OpenAI-style chat completions. Preserve or generate `data: {...}`
frames terminated by `data: [DONE]`. The stream must terminate correctly. **No buffering until
completion.**

## 8.3 Tool-call deltas — release-critical

Streamed tool calls arrive as fragments; semantics must survive the proxy intact:

```text
delta 1  tool_calls[0]  index=0  id=call_123  function.name=edit_file  function.arguments="{\"pa"
delta 2  tool_calls[0]  index=0                                        function.arguments="th\":\"foo"
delta 3  tool_calls[0]  index=0                                        function.arguments=".ts\"}"
```

RAVIS must **not**: renumber indexes; invent new IDs per fragment; split one call into
multiple; lose argument fragments; convert arguments into objects mid-stream; or coalesce in
any way that changes semantics.

Required tests: single fragmented tool call; multiple concurrent tool calls; interleaved
indexes; ID only on the first fragment; name only on the first fragment; arguments split at
arbitrary byte and string boundaries; empty initial arguments; multiple final chunks. **The
final assembled Clarvis-side tool call must match the upstream call exactly.**

## 8.4 Tool results

Preserve `role = "tool"`, `tool_call_id` = the original call ID, and `content` = the result.
**Do not remap IDs.**

## 8.5 `reasoning_content`

If an OpenAI-compatible upstream emits `reasoning_content`, transparent forwarding preserves
it **separately**. Never merge it into `content`. Clarvis treats reasoning leaking into
visible or spoken output as a defect, and strips thinking at the provider layer.

For translated providers, RAVIS may map provider-native reasoning metadata into an appropriate
separate field where its compatibility contract allows. **Never silently inject it into
visible content.**

## 8.6 Cancellation

```text
Clarvis cancels → client socket aborts → RAVIS cancels the upstream HTTP request
  → provider/local runtime generation stops where supported
```

RAVIS must not keep consuming an abandoned generation. **This is a release requirement.**

Tests must cover disconnect before the first token, mid-text, mid-tool-call, while a local
model generates, and during a translated provider stream. No orphan provider task may remain
unless the upstream API fundamentally cannot cancel — in which case the limitation is surfaced,
not hidden.

**Cancellation is not a failure.** It stops execution and must never trigger fallback or bill a
phantom completion.

## 8.7 Tool capability probing

Clarvis treats an actual capability probe as authoritative rather than trusting model-family
heuristics — it sends a one-tool, one-token request. RAVIS must therefore make sure that probe
receives a reliable answer.

For `ravis/clarvis-agent` the pool contract is `tools = required/supported`. **RAVIS must not
route a tiny tool probe to a cold or unsupported candidate and thereby make the pool appear
incapable.** This is the single sharpest wire-level constraint in the integration.

## 8.8 Conformance harness

```text
tests/compatibility/clarvis/
```

Use the real Clarvis OpenAI-compatible expectations wherever possible. If Clarvis provides an
existing provider request probe, adapt or mirror its behaviour rather than inventing a
different interpretation of the protocol.

Required scenarios: chat (`ravis/clarvis-chat`, streamed text, `[DONE]`); agent tool call
(`ravis/clarvis-agent`, tools supplied, fragmented arguments, valid assembly); multiple tools
(parallel and interleaved deltas); reasoning (`reasoning_content` stays separate); capability
probe (agent pool correctly reports tool capability); cancellation (Clarvis abort reaches
upstream); separate pools (chat and agent may route to different models); fallback (primary
failure → valid compatible fallback).

```bash
ravis conformance clarvis
```

```text
Clarvis OpenAI Compatibility
✓ /v1/models cached response      ✓ tool_call_id preserved
✓ chat stream                     ✓ reasoning_content preserved
✓ [DONE]                          ✓ cancellation propagated
✓ fragmented tool arguments       ✓ clarvis-agent tool invariant
✓ multiple tool indexes           ✓ clarvis-chat route
PASS
```

## 8.9 Release gate

A release claiming Clarvis compatibility must pass: `GET /v1/models`, chat streaming,
`[DONE]`, tool-call indexes, fragmented tool args, parallel tools, `tool_call_id`,
`reasoning_content` preservation, `supportsTools` behaviour, client cancellation, separate
chat/agent pools, and fallback compatibility.

**No exceptions hidden behind "works with most models."**

## 8.10 What is *not* in the initial integration

Clarvis Bridge, distributed tracing inside Clarvis, and NERVIS presence are later ecosystem
features. The initial path is plain OpenAI-compatible HTTP.

---

# 9. Routing

## 9.1 Pipeline

```text
Incoming request → normalize routing metadata → determine hard requirements
  → apply hard user policies → apply virtual-pool invariants
  → remove incapable candidates → remove unhealthy/unavailable candidates
  → apply budget constraints → calculate candidate scores
  → consider session affinity → consider local load cost
  → select route → build fallback chain → choose transparent vs translated → execute
```

Two phases, strictly ordered: **eligibility** (hard constraints) then **ranking**
(preferences). No preference outweighs a failed hard constraint. Unknown capability **fails
closed** when required.

## 9.2 Hard versus soft

| Hard constraints | Soft preferences |
|---|---|
| local only · API only · provider prohibited · privacy level · tools required · vision required · minimum context · maximum request cost · data residency · credential availability · pool capability invariant | prefer local · prefer fast · prefer cheap · prefer already loaded · prefer SIRVIS-tested · prefer provider X · energy · evidence confidence · warm state |

If cloud fallback would violate a local-only or privacy policy, return a structured **no-route**
error rather than routing around it.

**Gate:** table-driven tests prove each hard constraint excludes an otherwise top-ranked
candidate, and the route explanation names the exclusion without leaking secrets.

## 9.3 Routing profiles

`Auto`, `Balanced`, `Speed`, `Performance`, `Cheap`, `Local Only`, `API Only`, `Private`,
`Coding`, `Reasoning`, `Long Context`, `Clarvis Chat`, `Clarvis Agent`.

## 9.4 No opaque magic

MVP routing is deterministic, explainable, rule-based and score-based. **No mandatory LLM
router** — it would add latency, cost, another failure point, unpredictability and debugging
difficulty. A classifier may later contribute signals; it must never become an opaque oracle.

## 9.5 Capability and request analysis

Capabilities tracked: text, vision, audio in, audio out, tools, parallel tools, structured
output, reasoning, streaming, embeddings, context size, max output, prompt caching. States:
`SUPPORTED`, `UNSUPPORTED`, `PARTIAL`, `UNKNOWN`.

Before scoring, analyse the request for images, tools, response schema, context requirement,
reasoning and streaming, producing `RequestRequirements`.

## 9.6 Application identities and policy

Track clients — Clarvis, NERVIS, OpenWebUI, CLI, others — each with its own routing defaults,
budgets, privacy level, provider restrictions and logging. The policy engine supports
structured conditions and actions:

```text
IF application == Clarvis AND model == ravis/clarvis-agent THEN require tools
```

## 9.7 Explainability

Every route answers **why this model?** — pool or profile, hard requirements, positive scoring
factors, rejected alternatives, SIRVIS evidence, runtime state, cost/latency tradeoff.

```text
Selected: Local Qwen Coder
  + strong SIRVIS coding score   + already loaded   + no API cost
  + low measured latency         + tool support     + sufficient context
Not selected:
  Claude — better quality, higher monetary cost
  Gemini — slower current provider latency
```

Every decision records the candidate snapshot, excluded candidates with reasons, the selected
target, the profile/policy revision, SIRVIS evidence references, the fallback chain, timestamps
and correlation IDs. Explanations distinguish facts, estimates, unknowns, constraints and
preferences, and redact credentials, internal URLs, prompt content and sensitive
machine/workspace identifiers.

**A no-route decision is first-class and explainable.**

**Determinism gate:** fixed candidates, evidence, policy and time inputs produce the same
decision and the same explanation.

## 9.8 Routing overhead

Measure `normalization_ms`, `capability_ms`, `policy_ms`, `scoring_ms`, `total_routing_ms`,
`upstream_connect_ms`, `upstream_ttft_ms`. Target for cached rule-based routing:
**P50 < 5 ms, P95 < 20 ms.**

Background refresh keeps provider models, pricing, health, SIRVIS evidence and local host state
current. **Routing reads cached snapshots**, never live lookups.

---

# 10. Reliability

**Provider health** tracks availability, HTTP errors, timeouts, rate limits, TTFT, request
latency and stream interruptions. **Circuit breakers** use `CLOSED`, `OPEN`, `HALF_OPEN` —
do not keep routing to a failing provider.

**Fallback chains** produce Primary → Fallback 1 → Fallback 2. Every fallback candidate must
still satisfy the original hard constraints **and the pool capability invariants**.

**Failure classification:** timeout, connection failure, rate limit, provider overload, model
unavailable, local OOM, invalid request, authentication, tool incompatibility, context
overflow, content refusal.

**Retry budget:** max attempts, max total latency, max total monetary cost.

**Client cancellation is not a retry** and must not trigger fallback.

**Context management:** route to a larger-context model, or reject. **Do not silently truncate
by default.**

**Do not route around a safety refusal** merely to find a more permissive provider.

Every provider call requires bounded timeout behaviour.

---

# 11. Streaming architecture

```text
Transparent:  upstream SSE → minimal proxy → client
Translated:   provider-native events → normalized events → OpenAI SSE
```

Every provider change must re-test: text fragments, tool fragments, finish reason, usage,
reasoning metadata, disconnect, upstream error mid-stream, and `[DONE]`.

**Structured output** normalizes JSON mode, JSON Schema and provider-native structured
generation, with capability filtering applied. **Reasoning** is represented separately; do not
assume all provider reasoning controls are equivalent.

---

# 12. Sessions and local runtime

## 12.1 Sessions

A `RoutingSession` stores session ID, application, virtual pool, actual model, provider,
created time, last activity, cache state and routing profile. It correlates related requests,
chosen profiles, route decisions, provider conversations where supported, usage and traces —
**it does not imply that RAVIS stores full prompts or responses.** Retention and content
capture are configured separately and default to minimal metadata.

**Sticky routing** prefers the current model unless capability changes, the context limit is
reached, the provider is unhealthy, policy changes, or another candidate offers a major
advantage. Reasons: consistency, prompt caching, context continuity, reduced model-load churn.

Clarvis supplies distinct chat and agent requests under appropriate session and trace
relationships. NERVIS general chat uses its own session scope. **Cross-workspace Clarvis
sessions must never merge because display names match.**

**Session gate:** restart, expiry and concurrency tests preserve isolation, correlation,
cancellation ownership and documented retention.

## 12.2 Local model lifecycle

States: `HOT` (loaded), `WARM` (recently used, worth retaining), `COLD` (installed, unloaded),
`UNAVAILABLE`.

Loading costs time, so the decision is genuinely adaptive:

```text
Local A  already loaded, 0.7 s response
Local B  better quality, 14 s load time
Cloud C  1.1 s expected response
```

For one simple question B is the worst choice despite being the stronger model. For a Clarvis
session expected to make 100 requests, loading B is worth it. That tradeoff — model load time,
available memory, currently loaded models, expected session length — is the *runtime-adaptive*
part of RAVIS.

RAVIS coordinates only operations the runtime adapter or a SIRVIS management capability
actually owns: discover, load/start, readiness wait, dispatch, cancel, unload/stop. **Ownership
is explicit so RAVIS and NERVIS never fight over lifecycle.** Use per-runtime locking, bounded
queues, capacity reservations, startup timeout, lease and heartbeat, idempotent operations, and
cleanup after crash.

Runtime Sets may inform which combinations are compatible; **RAVIS chooses placement and route
policy.**

**Gate:** concurrent requests, warm reuse, cold start, load failure, hung inference, crash,
stale lease, cancellation and resource exhaustion all leave a consistent state.

## 12.3 Concurrency awareness

Track active requests, local generations, queue depth, memory pressure and provider congestion.
Track provider quotas where possible: RPM, TPM, credits, rate-limit state.

---

# 13. SIRVIS evidence

RAVIS queries SIRVIS for machine information, local benchmark evidence, role-specific scores,
TTFT, generation throughput, prompt throughput, memory, model fit, Runtime Sets, contention,
runtime availability and loaded models.

## 13.1 Evidence identity

**Never** reduce SIRVIS results to `model → score`. Evidence is keyed by at least model family,
build/variant, runtime, runtime configuration, machine, role, and benchmark suite/version.

```text
Qwen family → MLX 4-bit build → MLX runtime / context 32K → Clarvis Agent role → measured evidence
```

## 13.2 Repeated measurements

Consume statistical evidence, not one scalar:

```json
{"generation_tok_s": {"median": 38.4, "min": 35.9, "max": 40.8, "samples": 5}}
```

Clarvis's own development work documented substantial run-to-run variation — roughly 32% on
unchanged runs — and concluded that single-take comparisons can measure noise rather than a
real difference. Scoring may use median, spread, sample count and confidence.

## 13.3 Provenance

RAVIS's own provenance enum:

```text
MEASURED_BY_SIRVIS · OBSERVED_BY_RAVIS · PROVIDER_METADATA · ESTIMATED · UNKNOWN
```

**Do not flatten these into equal-confidence values, and never upgrade provenance.** SIRVIS's
`PARTIALLY_MEASURED` maps to `ESTIMATED` on the RAVIS side unless RAVIS models it explicitly.

Preserve source IDs, snapshot revisions, observed time and expiry. Cache evidence with a
staleness policy. SIRVIS recommendations are **advisory inputs** — RAVIS remains responsible for
eligibility and ranking. On absence, version mismatch or corruption, mark the source degraded
and fall back to configured evidence or unknown values. **Never fabricate a benchmark score.**

## 13.4 Optionality

Without SIRVIS, RAVIS still operates using provider metadata, its own observations,
user-defined ratings and explicit estimates — with the degradation labelled.

**Pairwise gate:** real SIRVIS measured, estimated, unknown, stale, tombstoned, runtime-down
and unsupported-major fixtures produce deterministic route effects.

## 13.5 Production observations

Record latency, TTFT, throughput, error rate, success and cost from real traffic. These
complement SIRVIS evidence rather than replacing it — one answers *what can this model do under
controlled conditions*, the other *what is it doing during normal use*.

Later, Clarvis may report task succeeded, tests passed, tool use succeeded and retry count.
**Not required for the initial integration.**

---

# 14. Cost, budgets and privacy

**Cost registry** tracks versioned pricing: input, output, cached input, cache writes and other
provider-specific billable usage.

**Usage records** capture input/output/cached/reasoning tokens where providers report them,
latency, provider and model, currency, price-source version and time, estimated-versus-billed
status, and route/session/request IDs. **Unknown usage or cost stays unknown. Never present an
estimated cost as an invoice.**

**Budgets:** daily, weekly, monthly, per application, per provider. Budget constraints use
declared accounting semantics and fail predictably when a price is unavailable.

```text
Monthly budget €50 →  0–70% normal routing · 70–90% prefer cheaper/local
                     90–100% strong cost penalty · 100% paid APIs blocked if hard
```

**Gate:** fixture arithmetic, currency, price-version, partial stream, retry, fallback and
missing-usage tests prevent double counting.

**Privacy levels:** `NORMAL`, `LOCAL_PREFERRED`, `TRUSTED_PROVIDERS`, `LOCAL_ONLY`. Privacy
constraints can never be overridden by score.

**Request logging** defaults to metadata only. Do not persist prompt or response content by
default.

**Credentials** live in macOS Keychain. **Never store provider API keys in plaintext in SQLite**,
and never let them traverse NERVIS, SIRVIS, Clarvis telemetry, route explanations or events.
Without secure storage, providers needing credentials are simply unavailable — **never fall back
to plaintext.**

---

# 15. Management API, events and UI

## 15.1 Management API

Separate from `/v1`. Canonical v1 reads:

```text
GET /api/v1/health          /api/v1/route-decisions
    /api/v1/providers       /api/v1/route-decisions/{decision_id}
    /api/v1/models          /api/v1/sessions
    /api/v1/profiles        /api/v1/sessions/{session_id}
    /api/v1/profiles/{id}   /api/v1/usage
    /api/v1/runtime-state   /api/v1/diagnostics
    /api/v1/sirvis          /api/v1/settings
```

List responses use `{items, next_cursor, snapshot_revision}`. Provider and model results are
redacted and capability-evidenced.

Canonical v1 mutations:

```text
POST /api/v1/profiles/{profile_id}/activate
POST /api/v1/evidence/refresh
POST /api/v1/route-tests
POST /api/v1/providers/{provider_id}/enable
POST /api/v1/providers/{provider_id}/disable
```

**Runtime control has no RAVIS endpoint until an ownership contract adds one.** Mutations accept
`Idempotency-Key` and `If-Match` where state changes, are separately authorized and audited, and
return the actual post-state plus revision. **Never expose credential values.**

NERVIS controls must be capability-driven. **A UI need does not create a RAVIS API.**

### 15.1.1 Proposed, not agreed — runtime listen address

**Status: PROPOSED. Do not implement, and do not build UI against it.** Recorded here
because the need is real and recurring, and because the reason it is not already in the
list above is easy to mistake for an oversight.

**The need.** RAVIS's bind address (§3) is configuration: one host and port carry `/v1`,
`/api/v1` and `/ecosystem` together. Today it is set by `ravis serve` and changed by
restarting. A dashboard that shows the address is naturally expected to change it.

**Why it is not simply another mutation.** Rebinding the socket a request arrived on
drops that request. Every other entry in §15.1 changes state that the answering process
keeps serving from; this one changes the answering process's own front door.

**Proposed shape**

```text
POST /api/v1/listen        { host, port }  →  the actual post-state plus revision
GET  /api/v1/listen                        →  current binding, and any pending one
```

**Unresolved. Each of these blocks agreement:**

1. **Response ordering.** Does the call answer on the old socket before rebinding, or
   after? Answering first is a promise the caller cannot verify; answering after means
   answering on a socket that no longer exists.
2. **In-flight work.** Streaming completions may be open on the old listener. Drain them,
   cut them, or keep both listeners alive during a handover window — and if a handover
   window exists, what closes it when nothing drains?
3. **Failure and rollback.** If the new bind fails (port in use, permission denied), the
   old one has already been released in the naive implementation and RAVIS is now
   reachable at neither address. Rollback must be part of the contract, not the
   implementation's business.
4. **Propagation.** Clarvis holds the address as a custom provider base URL and NERVIS
   holds it as a registry entry. Does RAVIS announce the change on `/ecosystem/events`
   before it moves, does it expect rediscovery, or is propagation entirely the operator's
   problem? Note that Clarvis probes `/v1/models` unauthenticated with a two-second
   timeout and reports **any** failure as *provider offline* (§5.0.1), so an unpropagated
   move is indistinguishable to a user from RAVIS being down.
5. **Ownership.** NERVIS may only restart a service it started (`nervis_managed`). For an
   `external` RAVIS, a bind change NERVIS cannot complete is a control it must not offer.
6. **Authorization.** This is not `routing-control` — it changes the process's exposure,
   not its routing. It is closer to `supervisor`, and it deserves its own permission.

> **A host change that leaves loopback is a security decision, not a preference.**
> `GET /v1/models` answers unauthenticated within two seconds by design, because
> Clarvis's availability probe sends no headers. Binding to `0.0.0.0` therefore publishes
> an unauthenticated model gateway to the network. If this endpoint is ever accepted, the
> safest version of it **refuses a non-loopback host outright** and leaves remote exposure
> to the deployment path, which the runbook already requires to carry TLS, authentication
> and authorization.

**Until this is agreed:** show the binding, show what depends on it, and show the command
that changes it. Do not render a field that writes it.

## 15.2 Events and traces

```text
ravis.request.accepted/completed/failed/cancelled   ravis.profile.changed
ravis.route.selected/no_route/fallback              ravis.usage.recorded
ravis.provider.state_changed                        ravis.capability.changed
ravis.runtime.state_changed
```

Spans cover gateway validation, policy and eligibility, evidence lookup, ranking, runtime
coordination, provider call, streaming and accounting. **Payload capture is off by default.
Export failure never blocks routing.**

## 15.3 Dashboard

Current profile, requests today, local %, cloud %, spend, estimated savings, provider status,
loaded models, SIRVIS status, recent routes, errors.

**Models page:** model, provider, capabilities, context, cost, latency, quality, SIRVIS
evidence, protocol mode, status.

**Clarvis compatibility page** (developer-facing, not essential user UI) showing the conformance
results live.

## 15.4 CLI

```bash
ravis doctor
ravis serve
ravis providers · ravis models · ravis profiles · ravis routes · ravis usage
ravis sirvis status
ravis test --model ravis/auto "Explain this code"
ravis conformance clarvis
```

## 15.5 Replay, shadow routing, escalation

**Replay** a route against a different model, provider, profile or router version.
**Shadow routing** is future and opt-in. **Escalation** (cheap/local → stronger model on failure
or low confidence) is future. **Multi-model orchestration** is future — do not assume forever
that one incoming request equals one upstream call, but do not implement orchestration in MVP.

---

# 16. Graceful operation

- **Without SIRVIS** — route using configured static, probed or unknown evidence; label the
  degradation; keep obeying hard constraints.
- **Without NERVIS** — full API and routing remain available.
- **Without a provider or runtime** — remove it from eligibility; continue only if policy allows.
- **Without a telemetry collector** — bounded local observability; no request fails.
- **Without secure storage** — providers needing credentials are unavailable; never plaintext.

---

# 17. Storage

```text
Provider · ProviderCredentialRef · ProviderModel · ModelCapability
VirtualModelPool · RoutingProfile · RoutingRule · ModelAlias
ClientApplication · ClientCredential
RoutingSession
RouteDecision · RouteCandidate · RequestMetric
ProviderHealth · ModelObservation
UsageRecord · CostRecord · Budget
SirvisEvidenceSnapshot
Setting
```

---

# 18. Repository structure

```text
ravis/
├── pyproject.toml · README.md
├── src/ravis/
│   ├── api/
│   │   ├── openai/{chat,models,transparent_proxy,translated_stream,responses}.py
│   │   └── management/
│   ├── core/{requests,responses,capabilities,models,pools,errors}.py
│   ├── routing/{engine,candidates,scoring,profiles,policies,requirements,explain}.py
│   ├── providers/{base,openai,anthropic,google,openrouter,lmstudio,ollama,generic_openai}.py
│   ├── execution/{executor,transparent,translated,cancellation,retries,fallback,circuit_breaker}.py
│   ├── compatibility/clarvis/{contract,fixtures,conformance}.py
│   ├── sessions/ · sirvis/ · health/ · usage/ · credentials/ · storage/ · web/ · cli/
├── tests/{unit,providers,routing,integration,fixtures}/
│   └── compatibility/clarvis/
└── docs/
```

---

# 19. Development principles for AI coding agents

1. RAVIS exposes OpenAI compatibility externally but uses normalized objects internally only
   where useful.
2. Do not normalize or re-serialize an already-compatible stream unnecessarily.
3. Transparent forwarding and native translation are distinct execution paths.
4. Tool-call stream transformations are compatibility-critical.
5. Preserve tool indexes, IDs and argument-fragment semantics.
6. Preserve separate reasoning metadata where the source protocol provides it.
7. `[DONE]` must be emitted correctly.
8. Cancellation must propagate upstream.
9. `/v1/models` must be cache-backed and cheap.
10. Initial Clarvis integration requires no Clarvis source modification.
11. `ravis/clarvis-agent` routes only to tool-capable candidates.
12. `tools present` is a routing signal, not the sole role identifier.
13. Hard constraints are evaluated before scoring.
14. Privacy constraints cannot be overridden by score.
15. Unsupported capabilities never silently disappear.
16. API credentials are never stored in plaintext.
17. SIRVIS is optional. 18. Clarvis is optional. 19. NERVIS is optional.
20. Every dynamic route must be explainable.
21. Every provider call requires bounded timeout behaviour.
22. Client cancellation is not a provider failure and must not trigger fallback.
23. Do not use an LLM router in MVP.
24. Keep routing-critical state cached.
25. Store measured and estimated evidence separately.
26. SIRVIS evidence identity includes build, runtime and role — not merely model name.
27. Prefer repeated benchmark evidence with spread over single-run figures.
28. Do not silently truncate context.
29. Do not route around safety refusals to find a permissive provider.
30. Provider adapters require contract tests.
31. Clarvis compatibility requires its own conformance suite.
32. Keep the UI optional.
33. Prefer boring, testable protocol handling to clever rewriting.

---

# 20. Milestones

Ecosystem gates from the addendum (E-R0…E-R7) fold in as extra exit criteria; mapping in §20.1.

| # | Milestone | Acceptance |
|---|---|---|
| **M0** | Foundation — Python package, FastAPI, configuration, SQLite, migrations, structured logging, CLI | `ravis doctor` and `ravis serve` work |
| **M1** | OpenAI-compatible transparent pass-through — `/v1/models`, `/v1/chat/completions`, streaming, one compatible upstream, no routing intelligence | OpenAI SDK can use RAVIS; `/v1/models` is cache-backed; the stream reaches the client without buffering; `[DONE]` correct; cancellation reaches upstream |
| **M2** | Clarvis wire-contract harness — conformance fixtures, tool-fragment tests, reasoning-field tests, cancellation tests | `ravis conformance clarvis` passes against the transparent route |
| **M3** | Normalization/translation layer — `NormalizedRequest`, `NormalizedResponse`, `NormalizedStreamEvent`, `ProviderAdapter`, translated execution path | Transparent route still passes Clarvis conformance; translated adapter works independently |
| **M4** | Anthropic native adapter — text, streaming, tools, errors, usage | OpenAI SDK works through Anthropic; Clarvis tool semantics still pass where applicable |
| **M5** | Basic routing — `auto`, `fast`, `performance`, `cheap`, `clarvis-chat`, `clarvis-agent` | Profiles select predictably; the agent pool enforces tools |
| **M6** | Capability filtering — tools, vision, context, structured output, streaming | Incompatible candidates eliminated before scoring; the Clarvis agent pool cannot select a non-tool model |
| **M7** | Provider expansion — Google, OpenRouter | Transparent/translated path chosen correctly; conformance stays green |
| **M8** | Local providers — LM Studio, Ollama, generic OpenAI-compatible | Local/cloud routing works; transparent local streams preserve tool semantics |
| **M9** | **Clarvis live integration** — point unmodified Clarvis at RAVIS | Custom provider connects; chat works; agent works; fragmented tools work; Stop works; chat and agent pools route separately. **Must not require any Clarvis source modification** |
| **M10** | Credentials and provider UI — macOS Keychain, provider UI, health tests, enable/disable | Secrets absent from all outputs and logs |
| **M11** | Sessions — `RoutingSession`, affinity, sticky routes | Isolation, correlation and retention tests pass |
| **M12** | Health, retries and fallbacks — circuit breaker, retry budget, failure classification | Fallback never violates Clarvis pool invariants; cancellation never triggers fallback |
| **M13** | SIRVIS evidence integration — evidence keyed by family, variant/build, runtime config, machine, role, suite | No one-number-per-model shortcut; repeated-measurement statistics preserved; provenance never upgraded |
| **M14** | Local lifecycle intelligence — HOT/WARM/COLD, load penalty, resource awareness | Memory pressure produces a safe route change |
| **M15** | Cost engine — pricing, estimates, actual usage, budgets | No double counting; estimates never presented as invoices |
| **M16** | Policy engine — application policies, privacy, provider allow/deny, model exclusions | Each hard constraint provably excludes a top-ranked candidate |
| **M17** | Dashboard — Dashboard, Providers, Models, Profiles, Rules, Sessions, Routes, Usage, SIRVIS | — |
| **M18** | NERVIS management integration — management, events and tracing surfaces | Does not affect Clarvis wire compatibility |
| **M19** | Production observations — rolling latency, TTFT, error rate, throughput | — |
| **M20** | Concurrency awareness — active requests, local congestion, SIRVIS contention evidence | — |
| **M21** | Replay and evaluation — request replay, routing comparison | — |
| **M22** | Advanced routing — escalation, shadow routing, outcome scoring | — |
| **M23** | Responses API — `/v1/responses` | No regression in Chat Completions compatibility |
| **M24** | Packaging — `RAVIS.app` | — |

## 20.1 Ecosystem gate mapping

| Addendum gate | Lands in |
|---|---|
| E-R0 existing-plan mapping | M0 — no unresolved cross-owner assumption; a missing contract is a STOP item |
| E-R1 MEP and OpenAI-compatible surface | M1 + M2 |
| E-R2 provider adapters | M4 + M7 + M8 |
| E-R3 profiles, policy, explanations | M5 + M6 + M16 |
| E-R4 SIRVIS ingestion and runtime coordination | M13 + M14 |
| E-R5 sessions, usage, NERVIS APIs | M11 + M15 + M18 |
| E-R6 Clarvis pairwise integration | M9 (+ direct-provider fallback verified) |
| E-R7 hardening and release | after M24 |

## 20.2 First vertical slice

```text
OpenAI-compatible client → RAVIS → OpenAI-compatible upstream → streaming response
```

then immediately:

```text
Clarvis → Custom OpenAI provider → RAVIS → transparent upstream
```

including tool calls. **Only after that** add native-provider translation.

## 20.3 MVP definition

OpenAI-compatible clients connect; `/v1/models` is fast and cached; chat completions work;
streaming works; cancellation works; tools work; OpenAI, Anthropic, Google, OpenRouter,
LM Studio and Ollama work; virtual models work; the Clarvis chat and agent pools work; Speed,
Performance, Balanced, Cheap, Local and API profiles work; fallback works; cost is tracked;
routing decisions are explainable.

And specifically:

```text
unmodified Clarvis + Custom OpenAI-compatible provider + RAVIS
  = working chat and coding-agent path
```

## 20.4 Non-goals for MVP

Shadow routing, automatic self-learning, audio, image generation, complex orchestration, a
distributed gateway, cloud sync, accounts, AI-generated policies, automatic summarization, the
Clarvis Bridge, and NERVIS code-server integration.

---

# 21. Conflicts resolved in this consolidation

| Conflict | Sources | Resolution |
|---|---|---|
| Error envelope | Addendum mandates the MEP envelope; the plan requires OpenAI-compatible errors | Split by surface: `/v1/*` returns OpenAI shapes, `/api/v1/*` and `/ecosystem/*` return the MEP envelope. Correlation IDs in headers on both |
| Management API paths | Addendum used `/v1/providers` etc.; the plan used `/api/v1/…` | `/api/v1/…` for management — `/v1` is reserved for the OpenAI-compatible surface and must not be shared |
| Profile IDs | Both propose `ravis/clarvis-chat` / `ravis/clarvis-agent`, but the addendum says confirm before coding | Retained as the proposal, with §5 requiring verification against the real Clarvis picker. RAVIS adapts, not Clarvis |
| Evidence provenance enums | SIRVIS publishes 4 levels; RAVIS defines 5 of its own | Both kept; §13.3 defines the mapping and forbids promotion |
| Milestone numbering | Plan M0–M24; addendum E-R0–E-R7 | M-numbers are the spine; E-R gates fold in (§20.1) |
| Dead citations | `fileciteturn…` markers throughout | Removed; the Clarvis-derived facts are restated as verified claims pointing at `CLARVIS.md` §3 |

---

# 22. Final architecture principle

The router must stay understandable **and** wire-compatible. For every decision RAVIS answers:

> Why this model?

For every compatible stream it must also satisfy:

> Did the intermediary preserve the protocol semantics exactly enough that the client never had
> to care a router was present?

Both matter. **A smart router that breaks tool-call fragments is not smart. A flawless proxy
that makes poor model choices is not intelligent.**

From Clarvis's perspective the best possible outcome is that it never notices anything special:

> **RAVIS is simply an extremely capable OpenAI-compatible server.**
