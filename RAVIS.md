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
coordination, route explanations, sessions, and usage and cost accounting, **and brokers Codex
agent sessions: it hosts the optional Codex runtime and relays each task between Codex and
Clarvis under the project write lock (runbook §2.2)**. It does **not** benchmark hardware
(SIRVIS), decide approvals, run Clarvis's tools or manage git (Clarvis), or supervise and display
the ecosystem (NERVIS). **Codex edits workspaces as the agent RAVIS hosts; RAVIS's own logic never
does.** The Codex half was decided on 13 September 2026; of it, the runtime check, the conditional
listing and the `/v1` refusal (M29's first increment, R1) and RAVIS's one Codex process with its
sign-in, allowance, version acceptance and file-rules re-test (the second, R2), the
agent-session relay (the third, R3) and the project lock with per-task process clean-up and
restart reconciliation (the fourth, R4, 0.24.0) are built (§15.1.2).

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

Codex tasks are the one place where RAVIS is not "just a very good OpenAI-compatible server". They
are brokered agent sessions on a separate relay (§15.1.2, runbook §2.2), and `/v1` refuses
`ravis/clarvis-codex`. *Decided 13 September 2026. The `/v1` refusal is built (M29's first increment, R1),
and the relay since the third (R3).*

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

`/ecosystem` carries the five MEP metadata endpoints — `health`, `identity`,
`capabilities`, `version` and the `events` SSE stream — **exactly as specified in
`ECOSYSTEM_RUNBOOK.md` §4.1, which is authoritative for their shapes; nothing about them is
restated here.** They are the runbook's Stage 1 exit condition and therefore precede the
transparent gateway: RAVIS answers identity, health and capability negotiation before it
proxies its first completion. `/ecosystem/*` returns the MEP error envelope, not the
OpenAI-compatible shapes `/v1` returns (§21).

**Malformed input on `/v1` answers 400, never a 5xx (§16 item 6, 4 Sep).** RAVIS
validates the two fields it dereferences itself — `model`, to route on, and
`messages`, to walk — and forwards everything else untouched, because a transparent
proxy does not own the upstream's schema (§1's non-invention rule, applied to a
request body). So `temperature: 99999` still travels and the upstream decides; a
`messages` that is not a list is refused here, because RAVIS is the thing that would
crash on it.

**An upstream's own 4xx is the client's fault, and is reported as one.** It used to
surface as `502 upstream_error` with outcome `unknown` — the status was discarded
when the error was raised, so nothing downstream could classify it. Blaming a
provider that answered correctly is both untrue and an invitation to retry
something that cannot succeed.

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
| `ravis.embeddings@1` | `POST /v1/embeddings` passes provider *and* gateway conformance, as the rule below requires. Until then it is advertised `degraded` — since 6 September 2026 — because it forwards to one configured local runtime with no routing between candidates, no fallback chain and no conformance suite (`ravis/src/ravis/ecosystem/capabilities.py`) |
| `ravis.codex_runtime@1` | `/api/v1/codex` and its routes pass their tests. Never a readiness check. **Advertised available since M29's second increment (R2, 0.23.9)**, with its `relay` constraint since the relay exists (R3, 0.23.15) |
| `ravis.agent_sessions@1` | The relay and project-lock routes pass their contract tests against the shared fixtures, including NERVIS and admin refusals. **Advertised available since M29's third increment (R3, 0.23.15)**, for the relay's routes, and for the project lock's since R4 (0.24.0); never a readiness check |

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

Shortly after MVP: `POST /v1/responses`. **`POST /v1/embeddings` was pulled forward and
built on 6 September 2026**, ahead of "later" — NERVIS chat's own knowledge lookup needed a
real embedding path rather than a workaround that reached into a local runtime directly,
which would have crossed the one product boundary this ecosystem enforces everywhere else.
Scoped to what that needed and no further: one configured local runtime (`RAVIS_EMBEDDING_BASE_URL`,
default Ollama's `nomic-embed-text`), no routing between candidates, no fallback chain, no
conformance suite yet — declared `ravis.embeddings@1`, `DEGRADED`, honestly. **Do not delay
core routing for secondary APIs** remains the rule for everything this did not need to touch.

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

`ravis/clarvis-codex` is listed as `{id, object, owned_by}` only, after the pools, in both builders, while
Codex is enabled and the request carries `X-Clarvis-Engines: codex`. Listing it reads cached values
and one header and starts nothing. *Decided 13 September 2026; built in M29's first increment, R1
(0.23.8).*

## 4.4 Inbound limits and admission control

RAVIS is a listening service, so the first thing it owes is the ability to refuse. These
limits apply to `/v1`, `/api/v1` and `/ecosystem` alike.

**Maximum request body.** A configured byte ceiling, enforced **before authentication and
before parsing**. The ordering is the substance of the rule: a body large enough to hurt
must be refused before RAVIS spends anything deciding who sent it. Exceeding it returns
413.

**Maximum images per request.** A configured count ceiling, independent of any per-image
byte limit. A per-image limit alone does not bound a request carrying a thousand small
images.

**Inbound rate limiting, per client application.** §10's rate-limit handling covers
*provider* limits applied *to* RAVIS. This is the opposite direction: a looping or
misbehaving local client must not be able to saturate the gateway or the local runtime.
Limits are keyed to application identity (§9.6); the runbook's 429 status mapping applies.

**Client-supplied URLs are not dereferenced.** `/v1` accepts inline `data:` payloads for
images and other binary inputs and **refuses `http(s)` URLs by default** — RAVIS does not
fetch on a client's behalf. Refusal is the whole feature: a gateway that dereferences a
caller's URL is an SSRF proxy into the local network, which the runbook §9 already forbids.
Should a deployment ever enable fetching, it requires an egress allowlist excluding
loopback, link-local and RFC1918, refusal to follow redirects into those ranges, and byte
and time caps. On the transparent path the field is forwarded untouched — the upstream
owns its own dereferencing policy.

**Client address determination.** Where RAVIS sits behind a proxy, the address used for
rate limiting comes from forwarded headers **only** for configured trusted proxies, and
from the socket otherwise. An untrusted forwarded header is a rate-limit bypass.

**Startup refusal.** The runbook requires binding locally by default, with remote exposure
an explicit choice carrying **TLS and authentication** — both, not either. RAVIS enforced
that by refusing a non-loopback bind unless TLS *and* a client credential were configured,
naming whichever was missing.

**Amended 4 Sep: the bind is refused outright.** The old rule was satisfiable and still not
safe. `cli.py` calls `uvicorn.run` without `ssl_certfile` or `ssl_keyfile`, so configuring
both pieces produced a bind that started and published the model registry in cleartext —
the exact trap the rule existed to close, reached by satisfying it. Naming the missing
piece made it worse, since supplying it is what an operator would then do. The finding now
names the host and says binding beyond loopback is not supported yet (`_check_remote_exposure`
in `ravis/src/ravis/config.py`).

**As built, 12 September 2026: remote access is not built, and nothing owns it.** That
finding and the function's docstring pointed at `ECOSYSTEM_RUNBOOK.md` §16 item 2 as the
list of what remote operation must prove until 12 September, and now point at runbook §9. But that item belonged to the stabilization track,
and it closed as loopback-only containment (runbook §15.1), not as a remote mode. No milestone
in §20 schedules TLS and authentication on a non-loopback bind, so the bind stays refused
however it is configured. What remote operation would have to prove is still written in that
docstring: TLS wired to the listener, per-request authentication, `Host` and `Origin`
validation, SSE held to the same rules, and a test against a real TLS listener.

**Origin and Host validation.** Loopback is not a boundary against a browser: any page the
user visits can issue a cross-origin request to `127.0.0.1`. RAVIS rejects requests whose
`Origin` or `Host` is not allow-listed, and refuses a state-changing request sent as one of
the three CORS-safelisted content types. This is independent of §9.6's identity model — an
identity check does not stop a browser the user is already authenticated in, and an origin
check does not identify the caller. Both are required.

**Amended 4 Sep (§16 item 5): two of those were claims rather than code.** `Host` was not
read anywhere, and no content type was required — the handler took raw bytes and parsed
them whatever the header said, so a form posting `text/plain` never preflighted, was never
measured against the origin allowlist, and ran inference on the operator's account. Both
are enforced now, and the second makes `admission.py`'s own argument ("a JSON body always
preflights") true of the server rather than only of the browser.

**A CSRF token is not among them, and the omission is deliberate.** This clause used to
require one. What a CSRF token defends is an *ambient* credential — a cookie the browser
attaches by itself — and RAVIS has none: every privileged call carries a bearer credential
in an `Authorization` header, which a cross-origin page cannot set without a preflight that
consults an allowlist holding `http://127.0.0.1:8790` and `http://localhost:8790` by default — the two addresses NERVIS serves its dashboard from, and nothing else. Adding a token would defend a vector this design
does not have, while implying the header requirement was insufficient. `Host`, `Origin`,
the content-type rule and the credential are the controls; the token was a fifth name for
work three of them already do.

**Gate:** every limit has a negative test proving refusal; the body-size test proves refusal
occurs without authentication having run; a wrong-`Origin` mutation is rejected; and a
non-loopback bind fails to start *however it is configured* — including with both a
credential and TLS paths, which is the case that used to pass.

> Six of these — body size, image count, inbound rate limiting, URL refusal, client address
> and startup refusal — were identified against Alexander Keisse's `ai-router`
> (<https://github.com/alexander-keisse>, MIT), a working local router where each is
> implemented and documented as an enforced boundary. None appeared in RAVIS's plans. Origin
> and Host validation came from a later audit of these documents, not from that source.

---

# 5. Virtual models and pools

Required defaults:

```text
ravis/auto      ravis/balanced   ravis/local    ravis/coding        ravis/long-context
ravis/fast      ravis/cheap      ravis/api      ravis/reasoning     ravis/chat
ravis/performance                ravis/private  ravis/agent         ravis/vision
ravis/free-api                                                      ravis/draw
```

`ravis/chat` and `ravis/agent` were implemented and missing from this list.
`ravis/chat` is what a conversation with no stated profile resolves to;
`ravis/agent` is the general "a model that can call a tool" pool, distinct from
`ravis/clarvis-agent` (§5.1). `ravis/vision` is the same kind of addition,
built 7 September 2026 ahead of the feature that needed it, and in use by one
the same day: NERVIS's save-time visual check (`NERVIS.md`) routes a rendered
PDF's first page here to ask whether the layout itself came out broken. No
curated family list, deliberately: a guessed name pattern for "built to see"
is the same heuristic `ravis/coding`'s own exclusion list already applies in
the other direction, and the capability requirement is real evidence where a
name pattern would be a guess.

`ravis/draw` is the other half of that pair and shares no member with it:
reading an image and emitting one are separate capabilities that happen to
share a word, so it requires `image_out` rather than `vision`. Two exclusions
are deliberate and both were measured. **OpenRouter's auto-routers are excluded
by name** — they advertise `image` among their output modalities because
something behind them can draw and then choose the model themselves; asked for
a red circle, `openrouter/auto` chose `z-ai/glm-5.2` and answered in words,
which is a claim about somebody else's routing decision that this pool can
neither verify nor fall back from. **The native Google models fail closed**,
because that catalogue publishes no output modalities at all and an UNKNOWN
capability is not a yes; naming one of those models directly still works, as it
does everywhere else. Its label is `Image generation`, which §9.3 lists as a
display name that does not hyphenate mechanically — deriving `ravis/image-…`
from it would produce exactly the substring constraint 2 below forbids.

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
   silently disappear from the list. The current pool set is clean — and this rule earned its
   keep on 7 September 2026: the image-generation pool was first written as `ravis/image` and
   a routing test whose docstring names this exact hazard failed on the first run. It is
   called `ravis/draw` for that reason and no other.
3. **Set `created` on every model entry, or on none.** Clarvis sorts by timestamp only when
   *all* entries carry `created`, otherwise it falls back to alphabetical. A mixed list scrambles
   any intended ordering.
4. **`owned_by` is optional.** A placeholder such as `organization_owner`, or omitting the field,
   both yield the vendor-prefix label.
5. **`ravis/clarvis-codex` is listed as `{id, object, owned_by}` and nothing else** (decided 13 September
   2026; built in M29's first increment, R1), and only for a request carrying `X-Clarvis-Engines: codex` (§4.3). No
   entry in RAVIS's catalogue sets `created` today, so rule 3 holds; the day other entries gain
   `created`, rule 3 and the Codex entry's no-extra-keys rule will disagree, and rule 3 must win.

## 5.1 Pool semantics

**`ravis/chat`** — ordinary conversation, and the pool a request with no profile gets.
Draws from general conversational assistants, ordered cheap-capable first with the
expensive tier last (§5.1.1). Prefers a hosted model with this machine's own underneath
it, because "prefer local, prefer cheap, prefer already loaded" selects whatever small
thing is resident — right for a classification call and poor for talking to.

**`ravis/agent`** — a caller that needs a model which can call a tool, and nothing more
specific. Tools REQUIRED, 32K minimum context. Deliberately weaker than
`ravis/clarvis-agent`: a caller with tools in the request should not have to borrow a role
pool and inherit a preference for code models it never asked for.

**`ravis/clarvis-chat`** — optimized for conversation, planning, analysis, instruction
following, personality quality and reasonable latency. Tool support is optional unless the
request itself supplies tools.

**`ravis/clarvis-agent`** — optimized for coding, tool use, repository reasoning, structured
calls, long context and reliability. **All six are enforced**, not just the first two: tools
and `structured_output` REQUIRED, 128K minimum context, and membership drawn from the coding
families. It enforced tools and 32K for a long time, which made it member-for-member
identical to `ravis/agent` — two pools that select the same models are one pool with two
names, and Clarvis names this one because its needs are narrower.

> **Hard invariant: every model eligible for `ravis/clarvis-agent` must satisfy the pool's
> required tool capability.** Do not put a non-tool-capable model in the agent pool because it
> scores well on coding.

### 5.1.1 Curated membership

A pool may declare the **families it draws from**, as ordered fragments of model ids, and the
fragments that disqualify a model even when a family matched it. Both are per pool: a code
specialist is noise in `ravis/chat` and the entire point of `ravis/coding`.

This is a statement about **what a model is for**, never about how good it is — §9.2 forbids
RAVIS inventing quality it has not measured, and nothing here claims any. Ranking *within* the
class stays where §13 leaves it.

**Order is the interface.** Preference rank reads position, so the same tuple that defines
membership defines what the pool reaches for first. Two consequences are deliberate:

- **The cheap-capable tier leads and the expensive tier is last.** An expensive model is a
  manual pick — named directly, or ticked into the pool — and never what an ordinary turn
  reaches for. It stays *in* the list so it can still answer when nothing above it is
  available, which is better than refusing.
- **A pool that declares nothing sorts alphabetically**, which the routing engine states in
  its own comment and which is why this exists: with no requirements and no preference,
  `ravis/chat` admitted every model RAVIS knew and conversation was served by whichever id
  sorted first.

**A routable baseline applies to every pool, declared or not.** An embedding model cannot
answer a chat completion, and neither can a reranker, a moderation classifier, a speech or
image model, or a batch endpoint — the same weights on an asynchronous queue. No pool wants
these and every pool had them.

### 5.1.2 Membership derived from role evidence

A pool may also name the **SIRVIS role whose evidence is about its own work**. Where a
measurement exists, it decides — in both directions:

- a build measured under that role and **passing** is a member whether or not a declared
  family names it, because the families are a stand-in for evidence and a stand-in has to
  lose to the thing it stands in for;
- a build measured and **failing** is not a member whether or not a family names it, because
  a hand-written list cannot outvote a trial that ran.

`UNKNOWN` and absence both fall through to the families, and they are not the same fact: one
means the role was measured on some other axis, the other that nobody has measured it at all.
Neither is a failed measurement, and §9.1 fails closed on what is *not established* rather
than treating silence as refusal.

**Evidence admits within the invariants, never past them.** §5.1's hard invariant still
applies first: on this machine the passing `granite-4.0-h-tiny` is `SUPPORTED` for
`clarvis-agent` and is still not a member of that pool, because its `structured_output` state
is `UNKNOWN` and the pool requires it. A measurement can say a build is good at the work; it
cannot say the build satisfies a requirement nobody has established.

**And a trial belongs to the build, not to the role that filed it.** §13.1 defines it as a
pass rate over phrasings and repetitions of one request; nothing in that is specific to any
product's workload. RAVIS previously read evidence for **one configured role**, which meant
every trial on this machine — all seven — was invisible to every pool but one. The read is
role-agnostic now, the role travels on each record, and the tool-call verdict is taken from
the freshest record that actually ran a trial rather than from the freshest record, which a
later throughput run under another role would otherwise hide.

That change also removed a request: reasoning shares needed a second, role-agnostic read
purely to work around the role filter, and one response now carries every role's records.

**Membership is derived, never stored.** An operator may pin a per-pool selection, and
`POST /api/v1/pools/curate` removes every such pin so each pool follows its own default again.
That endpoint deliberately writes nothing: a stored list is a snapshot of a catalogue that
moves, so a model a provider ships next week would match a pool's declared families and never
be routed to. Computing membership per request is what §5.2 means by it being derived.

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

A Codex agent session is neither Path A nor Path B. It isn't a completion; it is relayed on
`/api/v1/agent-sessions` (§15.1.2). *Decided 13 September 2026; built in M29's third increment (R3).*

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
reason, usage, provider, model, latency, provider request ID, reasoning metadata, cache usage,
and **images the model emitted**. That last one is not decoration: a translated path that
carries every field but the picture answers a drawing request with a 200 and almost no text,
which is what Gemini's native path did until 7 September 2026.

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

**Implemented (12 Sep) as a ranking term for probes only.** A request is a probe by its shape —
exactly one tool and `max_tokens` ≤ 1 — never by its content. For a probe, every candidate that
can answer without a load (resident, or hosted) ranks ahead of every one that needs a load, ahead
of session affinity and pool preference, and exploration is off; every other request ranks as
before. When only cold local candidates are eligible the probe is still routed, not refused:
residency ranks and never excludes (§9.2), and a refusal would itself make the pool look
incapable. `LOCAL_PREFERRED` still leads, so a probe is never sent off the machine to dodge a load
(§14). The route explanation names the probe.

## 8.8 Conformance harness

```text
tests/compatibility/clarvis/
```

Use the real Clarvis OpenAI-compatible expectations wherever possible. If Clarvis provides an
existing provider request probe, adapt or mirror its behaviour rather than inventing a
different interpretation of the protocol.

The suite is built in two passes, because the runbook forbids routing intelligence at Stage 2
and two of these scenarios cannot be satisfied without it.

**Stage 2 — wire-level, no routing.** Chat (streamed text, `[DONE]`); agent tool call (tools
supplied, fragmented arguments, valid assembly); multiple tools (parallel and interleaved
deltas); reasoning (`reasoning_content` stays separate); cancellation (Clarvis abort reaches
upstream); cached `/v1/models`. All of these hold against a single configured upstream.

**Stage 3 — added once routing exists.** Separate pools (chat and agent may route to different
models — needs M5); capability probe (the agent pool correctly reports tool capability — needs
M6); fallback (primary failure → valid compatible fallback — needs M12, and cannot be static
configuration by definition).

**Do not delete the Stage 3 scenarios to make Stage 2 pass, and do not pull routing into Stage
2 to satisfy them.** The transparent path is only useful as a control while it stays
unintelligent.

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


**Endpoint note, 2026-08-27.** `GET /api/v1/providers` now carries `breaker`,
`error_rate`, `requests` and `consecutive_failures` per provider, read from the
§10 registry **without creating a record**. A provider nobody has called reports
`null` for the first two rather than `CLOSED` and `0%`: acquiring a clean
history by being listed would turn "unknown" into "healthy", which is the one
reading a reliability surface must never produce.

**Implementation note, 2026-08-27.** Of the soft column, `prefer already loaded`
has worked since M5 (residency). `prefer local`, `prefer cheap` and `prefer fast`
were prose until now and are implemented as *opt-in per pool* — an unconditional
term becomes the entire ordering for every pool that declares nothing.

`prefer cheap` reads OpenRouter's published per-token pricing and the `0.0` a
local runtime bills; a model whose price nobody published sorts **last** rather
than free. `prefer fast` reads RAVIS's own `OBSERVED_BY_RAVIS` timings and only
above a sample floor — an unmeasured model sorts *neutral*, because RAVIS only
measures a model by routing to it and sorting unmeasured last is a trap that
closes.

**What that leaves open, for hosted models.** A local model nobody has routed to
can still be measured deliberately: SIRVIS loads it and benchmarks it. A hosted
one cannot — SIRVIS drives local runtimes only, and most of what it measures
(load time, memory pressure, thermal) does not exist for an API. So production
traffic is the *only* way a hosted model acquires timings, and in a pool where
speed leads the ranking, anything already measured under a second beats the
neutral placeholder — which can keep an untried hosted model untried
indefinitely. The neutral sort stops the loop in pools that rank on something
else, and does not close it here. Recorded rather than fixed: closing it means
either spending money on unproven models automatically, or asking a person to
choose, and both are decisions rather than defaults.

**And `prefer fast` is consulted before a pool's declared preference**, which is
a trap of its own for a pool that has one. Any model RAVIS happens to have timed
jumps ahead of the families the pool declared, so a curated pool (§5.1.1) either
declares no speed preference or has its order silently inverted by whichever of
its members was called once. `ravis/chat` declares none for that reason.

**Residency is not left to accumulate.** A local runtime holds a model until
something evicts it — right for a runtime, which cannot know whether another
request is coming, and wrong for a router that has just finished one. Left alone
it inverts the ordering above: residency is a soft preference until memory is
tight, at which point already-loaded models are preferred over the pool's usual
order and whatever happens to be warm decides the route. So RAVIS sends the
runtime's own idle TTL on a local request — ten minutes by default,
`RAVIS_LOCAL_MODEL_IDLE_TTL_SECONDS`, and `0` to leave it to an operator who
configured their own. A client that sets its own TTL keeps it.

`prefer SIRVIS-tested`, `prefer provider X`, `energy` and `evidence confidence`
remain unimplemented.

**The hard column owes the same disclosure, for one entry.** `data residency` is
unimplemented: `RoutingPolicy` (`ravis/src/ravis/policy.py`) carries no region or
jurisdiction field, no provider anywhere declares where it is hosted, and no
refusal in `policy_refusals` or `RoutingEngine` mentions it. Unlike the soft
column above, the hard table never said so — it read as an enforced exclusion
next to ten that are, which is the exact optimism this document exists to
prevent. (`residency` elsewhere in this document is the unrelated, implemented
concept above — a *model* kept warm in memory, not data staying in a
jurisdiction; the shared word is a coincidence worth not tripping over.)

If cloud fallback would violate a local-only or privacy policy, return a structured **no-route**
error rather than routing around it.

**Gate:** table-driven tests prove each hard constraint excludes an otherwise top-ranked
candidate, and the route explanation names the exclusion without leaking secrets.

## 9.3 Routing profiles

`Auto`, `Balanced`, `Speed`, `Performance`, `Cheap`, `Free API`, `Local Only`, `API Only`,
`Private`, `Coding`, `Reasoning`, `Long Context`, `Vision`, `Image generation`, `Clarvis Chat`,
`Clarvis Agent`.

**These are display names for the §5 pools, one-to-one — not a second set of objects.**
`Speed` is `ravis/fast`, `Local Only` is `ravis/local`, `API Only` is `ravis/api`,
`Image generation` is `ravis/draw`; the rest lowercase and hyphenate directly. **That last
exception is load-bearing rather than cosmetic**: hyphenating its label mechanically yields
`ravis/image-generation`, and §5.0.1's second constraint forbids the substring `image` in a
pool ID because Clarvis's unanchored filter would make it disappear. **The pool ID is the only form that appears on the wire, in
storage, in a route explanation or in another product's UI.** A consumer that renders its own
spelling — a mode selector, a settings picker, a test fixture — renders the pool ID or a label
resolved from this mapping, never a third spelling of its own.

## 9.4 No opaque magic

MVP routing is deterministic, explainable, rule-based and score-based. **No mandatory LLM
router** — it would add latency, cost, another failure point, unpredictability and debugging
difficulty. A classifier may later contribute signals; it must never become an opaque oracle.

## 9.5 Capability and request analysis

Capabilities tracked: text, vision, image out, audio in, audio out, tools, parallel tools,
structured output, reasoning, streaming, embeddings, context size, max output, prompt caching.
**Vision and image out are separate and share no evidence**: one is reading an image and the
other is emitting one, and a model that does either does not thereby do the other. States:
`SUPPORTED`, `UNSUPPORTED`, `PARTIAL`, `UNKNOWN`.

Before scoring, analyse the request for images *it carries*, tools, response schema, context requirement,
reasoning and streaming, producing `RequestRequirements`.

## 9.6 Application identities and policy

Track clients — Clarvis, NERVIS, OpenWebUI, CLI, others — each with its own routing defaults,
budgets, privacy level, provider restrictions and logging. The policy engine supports
structured conditions and actions:

```text
IF application == Clarvis AND model == ravis/clarvis-agent THEN require tools
```

### 9.6.0 Resolving an application identity

Everything above — routing defaults, budgets, privacy level, provider restrictions, logging,
the §4.4 rate limit and the §9.6.1 background marker — is keyed to an application identity,
so how that identity is established is a security contract, not a configuration detail.

A caller presents its RAVIS client credential; RAVIS resolves it to exactly one
`ClientApplication`. **A claimed identity is never accepted on its own** — not from a request
field, not from a user agent, and not from `X-Ecosystem-Actor`, which the runbook §4.3
already forbids trusting from an unauthenticated caller. RAVIS reports the resolved identity
back through that header on management surfaces; it does not read it as an assertion.

An unauthenticated caller is **not refused** — §5.0.1 requires `GET /v1/models` to answer 200
without credentials — it resolves to the built-in `anonymous` identity, which is
least-privileged by construction:

```text
anonymous  →  strictest inbound limit · no background marker honoured
              no policy inheritance · no privacy level above NORMAL
              no provider the default profile would not already allow
```

The failure this prevents is concrete: without it, any local process claims
`application=Clarvis`, adds the background marker, and inherits Clarvis's privacy level,
provider allow-list and budget relief — at which point §14's "privacy constraints can never
be overridden by score" is advice rather than a boundary.

**Gate:** an unauthenticated request resolves to `anonymous` and provably fails to obtain any
privilege of a named application; a forged `X-Ecosystem-Actor` changes nothing.

### 9.6.1 Background and utility calls

Chat clients issue requests the user never sees: conversation titles, tag suggestions,
summaries, autocomplete. They are short, frequent, latency-insensitive and disposable —
and they arrive on the same endpoint as real work, indistinguishable from it unless the
client says otherwise.

Left unmarked they route as real requests. A title generated through `ravis/auto` can
select a paid frontier model, so a string nobody reads becomes a billed call, repeatedly.

**Clients declare them.** A background call carries an explicit marker — request
`metadata`, or a distinct virtual model. **RAVIS never infers the class from prompt
shape**, because a wrong inference in the other direction silently downgrades real work.

**A declared background call is:** eligible for a cheap or local pool by default, exempt
from session affinity, excluded from the default route-explanation view, and accounted
separately under §14 so utility spend stays visible instead of blending into conversation
cost.

**The marker is a trust boundary.** It lowers cost and relaxes ordinary limits, so it is
honoured only from an authenticated application identity, and the per-application inbound
limit (§4.4) still applies — a lower one, never none. **It buys cost and limit relief and
nothing else.** An identity permitted to mark background calls gains no policy exemption, no
provider access and no management authority from that permission.

**Gate:** a declared background call never selects a paid provider under the default
profile, and no undeclared request is ever reclassified as background by heuristic.

> The failure mode, and the labelled-identity approach to it, come from Alexander Keisse's
> `ai-router` (<https://github.com/alexander-keisse>, MIT), which detects its chat client's
> background calls and routes them to a cheap local model. Its own review flags the trust
> boundary recorded above.

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

A candidate resting after a tool refusal (§10) is listed among the excluded with the upstream's
reason and the minutes until tool requests resume — a temporary exclusion, like an open circuit,
and only on requests that carry tools.

Agent sessions store the workspace's real path, because the hosted Codex process must run there.
It is never placed in route explanations or events. `GET /api/v1/codex` lists running tasks by
folder name only, never a path, for the menu bar and dashboard on this machine. *Decided
13 September 2026; built in M29's third increment (R3).*

**A no-route decision is first-class and explainable.**

**Determinism gate:** fixed candidates, evidence, policy and time inputs produce the same
decision and the same explanation.

## 9.8 Routing overhead

Measure `normalization_ms`, `capability_ms`, `policy_ms`, `scoring_ms`, `total_routing_ms`,
`upstream_connect_ms`, `upstream_ttft_ms`. Target for cached rule-based routing:
**P50 < 5 ms, P95 < 20 ms.**

**As built, 12 September 2026: none of those seven timings is measured.** Nothing in
`ravis/src` records `normalization_ms` or any of the others; the management routes' own module
docstring (`ravis/src/ravis/api/management/routes.py`) lists §9.8's routing timings as a
subsystem that does not exist yet. The figures below come from outside the router instead:
`tools/load_test.py` subtracting a stand-in's latency, and one-off in-process timing.

Background refresh keeps provider models, pricing, health, SIRVIS evidence and local host state
current. **Routing reads cached snapshots**, never live lookups.

**Measured 12 September 2026: not met, then met narrowly.** `tools/load_test.py` puts a private RAVIS in front
of a stand-in model and subtracts the stand-in's own latency: RAVIS adds 16.1 ms at the median
and 17.3 ms at P95 with one caller, and 6.1 / 63.8 ms with ten. It stops keeping up at roughly
100–180 requests a second while using under a core, which is the signature of work waited on
inside the event loop rather than of computation. Timing each step in-process found two live
lookups on the routing path the sentence above rules out. `_direct_providers` asks each hosted
vendor for its model list on every request, which is a dictionary lookup only while the last
listing *succeeded*: a failed listing is never cached, so a vendor with a missing or refused key,
an outage or no network is asked again on every routed request — 165 ms of a 173 ms route with
the internet reachable, 2.85 ms against a closed port. And `read_memory` runs `vm_stat` as a
subprocess on every routed request, 6–8 ms, blocking the loop. Outside routing, the same suite
found the live RAVIS spending 46 ms more on a request that presents a key (59.7 against 13.4 ms):
every client and admin credential was in the keychain, and identifying a caller ran `security`
for each of them on every request, inside the loop — anonymous requests mixed with named ones
went from 64 to 157 ms at five callers. **Fixed in 0.23.1:** the credential store keeps a lookup
for sixty seconds and a worker thread renews it every thirty, so a keyed read now takes 15.1 ms
against 15.2 ms without a key, mixing the two changes nothing at five callers, and NERVIS's
relayed reads went from 45 ms to 2.9 ms one at a time. **Both routing
lookups fixed in 0.23.2**: free memory is sampled every five seconds in a
worker thread and routing reads the sample, and a failed vendor listing is remembered for thirty
seconds. Measured the same way afterwards, RAVIS adds 4.3 / 4.1 ms with one caller and
1.7 / 9.3 ms with ten; `_route` takes 0.58 ms in-process against 9.9 before; a vendor that
refuses slowly was asked twice across twenty-five routed requests rather than on each; and it
keeps up with 336 requests a second at fifty callers and 457 at a hundred, against 139 and 159.
The margin is under a millisecond: over five rounds of fifty requests one caller at a time RAVIS
added 4.0–4.7 ms at the median, and a complete load-test run the same evening measured 5.0 ms and
failed the check. P95 keeps room (4.1–6.1 ms with one caller, 3.0–7.6 ms with ten), and ten
callers add nothing measurable at the median. What the remaining 4–5 ms is made of was not
profiled.

**A context window is what the runtime serves, not what the architecture allows.** Ollama
publishes the architecture's maximum at `/api/show` and loads the model at its own default;
believing the first number routed a 25,000-token request to a model holding 4,096, which
evaluated 2,050 of them and answered confidently. `/api/ps` reports the window a resident
model actually has, and that is what the adapter reports — capped by the architecture, and
falling back to Ollama's default for a model not yet loaded rather than to its maximum.
LM Studio is read the same way: `loaded_context_length` for a model it reports as loaded, and
otherwise its default load context (8,192, the `defaultContextLength` its own settings hold and
every on-demand load in its logs used), capped by `max_context_length`. RAVIS never asks for a
larger load — a request for a model that is not loaded is served by LM Studio's on-demand load
at that default — so the build's maximum describes nothing RAVIS will actually be served.
That default is `RAVIS_LMSTUDIO_DEFAULT_CONTEXT` (8,192); the launcher sets it from LM Studio's own
`defaultContextLength` when the LM Studio upstream is on the same machine (13 Sep), so changing the
default in LM Studio and restarting the stack keeps the two in agreement, as `OLLAMA_CONTEXT_LENGTH`
and `RAVIS_OLLAMA_DEFAULT_CONTEXT` are for Ollama.

---

# 10. Reliability

**Provider health** tracks availability, HTTP errors, timeouts, rate limits, TTFT, request
latency and stream interruptions. **Circuit breakers** use `CLOSED`, `OPEN`, `HALF_OPEN` —
do not keep routing to a failing provider.

**Fallback chains** produce Primary → Fallback 1 → Fallback 2. Every fallback candidate must
still satisfy the original hard constraints **and the pool capability invariants**. An agent
backend (`ravis/clarvis-codex`) is never a candidate, a pool member or a fallback target (decided
13 September 2026; not built, M29).

**Failure classification:** timeout, connection failure, rate limit, provider overload, model
unavailable, local OOM, invalid request, authentication, tool incompatibility, context
overflow, content refusal — and, added on 11 September 2026, unsupported parameter: a request
one model cannot take as written, which may fall back to the next candidate.

**A tool refusal falls back, and rests the model from tool requests only** (12 Sep). Tool support
is a property of the model, and every candidate in the chain is tool-capable by construction, so
the next one very likely works. A refusal on a request that carried tools skips the refusing model
for requests carrying tools for a configurable window (`RAVIS_TOOL_REFUSAL_SUPPRESSION_SECONDS`,
default 30 minutes); it stays eligible for requests without tools, so a chat pool keeps it
(`ECOSYSTEM_RUNBOOK.md` §2.1). No circuit opens and no health scope widens. A pool whose
tool-capable candidates are all resting answers 503, never 422, because a client caches a 4xx on its
capability probe for the session (§8.7). A directly named model is neither filtered nor overruled.
Kept in memory, like the breakers.

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
reasoning metadata, **an emitted image**, disconnect, upstream error mid-stream, and `[DONE]`.
The image is on this list because its absence from it is exactly how the Gemini regression
survived: a re-test list that never named images is a list nobody used to re-test images.

**Structured output** normalizes JSON mode, JSON Schema and provider-native structured
generation, with capability filtering applied. **Reasoning** is represented separately; do not
assume all provider reasoning controls are equivalent.

**Agent-session streams** (M29 — decided 13 September 2026, built in R3) are per-session SSE, with
integer event ids, `Last-Event-ID` resume and bounded in-memory replay buffers (the last 2,000
deltas or 8 MB, and up to 20,000 other events or 32 MB for the session's life). Past the kept
range → `409 EVENT_CURSOR_EXPIRED`, and the client reads the snapshot. Closing a stream never stops
the task. The `/ecosystem/events` stream is unchanged.

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

Agent sessions are separate records (migration 8), never `routing_session`. Restart, expiry and
concurrency tests preserve session isolation, token binding, the one-writer lock and process
clean-up. *Decided 13 September 2026; built in M29's third increment (R3), and the project
lock's routes, restart reconciliation and process recording in the fourth (R4, 0.24.0).*

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

**OBSERVED_BY_RAVIS is RAVIS's own trial of a hosted model** (added 12 September 2026). Hosted
providers that publish no tool support — Anthropic, OpenAI and Google — leave their models UNKNOWN, as
does OpenRouter when a model's parameter list is silent about tools, and SIRVIS cannot measure an API.
RAVIS sends such a model one request carrying one tool it is required to call, or only offered where the
provider refuses to force the choice: a call records SUPPORTED,
a provider refusing tools records UNSUPPORTED, a transient failure records nothing and is retried hours
later, and a refusal for any other reason records nothing for thirty days. The makers' own copies are
tried before an aggregator's listings. Hosted models only, only models a tool-requiring pool would
otherwise admit, a few per pass under a daily ceiling, results kept thirty days, and every trial a
provider answered recorded as usage. In code the provenance is `OBSERVED`, ranked above `ADVERTISED` and below `MEASURED` and
`CONFIGURED`.

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
estimated cost as an invoice.** They are kept in RAVIS's database for ninety days and read back at
start (since 12 September 2026): held only in memory, every restart emptied the spend screen and
the monthly budget with it. A record carries no prompt and no completion.

**Budgets:** daily, weekly, monthly, per application, per provider. Budget constraints use
declared accounting semantics and fail predictably when a price is unavailable.

```text
Monthly budget €50 →  0–70% normal routing · 70–90% prefer cheaper/local
                     90–100% strong cost penalty · 100% paid APIs blocked if hard
```

**As built, 12 September 2026: one budget, not five.** RAVIS has a single global budget, set by
`RAVIS_BUDGET_LIMIT`, `RAVIS_BUDGET_CURRENCY`, `RAVIS_BUDGET_PERIOD` and `RAVIS_BUDGET_HARD`. The
period is one of `daily`, `weekly` or `monthly`, each a rolling window rather than a calendar
period, and spend within it falls into the bands above at 70, 90 and 100 % (`Budget.band` in
`ravis/src/ravis/cost.py`). A limit of zero means no budget. Budgets per application or per
provider, and more than one period at a time, are not built.

**Gate:** fixture arithmetic, currency, price-version, partial stream, retry, fallback and
missing-usage tests prevent double counting.

**Privacy levels:** `NORMAL`, `LOCAL_PREFERRED`, `TRUSTED_PROVIDERS`, `LOCAL_ONLY`. Privacy
constraints can never be overridden by score. `LOCAL_PREFERRED`, the one level that ranks rather
than excludes, is consulted before session affinity, load cost, the tool-probe term, speed, price
and pool preference, and exploration never leaves the machine for it (12 Sep: five of those terms
sat ahead of it and each could move a request off-device).

**Request logging** defaults to metadata only. Do not persist prompt or response content by
default.

**The exception for Codex tasks** (owner decision, runbook §2.2 invariant 8; M29, decided
13 September 2026; the relay's part built in R3). RAVIS's database, logs, audit and events stay metadata-only.
Content passes through RAVIS's memory while it is relayed to the session-token holder (bounded,
never persisted). Codex writes its own conversation history into RAVIS's Codex folder
(`~/.local/share/ravis-codex`), kept up to 90 days without use and denied to Codex's commands.
Codex fetches OpenAI's plugin catalogue at start unless `features.plugins=false` stops it;
calibration records which, in `STATUS.md`.

**Retrieved content and the fencing rule.** The runbook §9 requires every path that places
retrieved content into a prompt to fence it first, and names the producer as the owner.
**RAVIS owns no such path and must never acquire one:** it forwards what a client sends and
returns what an upstream answers, and it does not search, retrieve, summarize or inject. Its
obligation under that rule is therefore negative, and worth stating because a gateway is
exactly where a well-meaning "enrich the request" feature would land: content passing through
RAVIS is never treated as instruction to RAVIS, and no route decision is ever influenced by the
*content* of a message — only by its declared capabilities, its pool and its policy.

**Credentials go to the platform keyring** — macOS Keychain through `security -i`, Linux's
Secret Service through `secret-tool`, both taking the secret on standard input so it never
reaches `argv` and never appears in `ps`. **Every keyring write is read back and compared before
it is believed**, because macOS's interactive parser unquotes what it reads and drops a
backslash from a credential containing one; a write that does not survive its own read-back is
treated as no write at all. The *name* is recorded in an index beside the credential file — never
the value — because client and admin identities are matched by walking stored names, and a name
that vanished with its value is an identity nobody can authenticate with.

**Where there is no keyring — Windows, a headless server, a container — the file is the answer**,
created `0600` inside a `0700` directory, holding the secret as text. `RAVIS_CREDENTIAL_KEYRING=0`
chooses it deliberately, which is also how this repository's own test suite avoids writing into
the operator's login keychain. Reads are unaffected by that switch and stay file, then keyring,
then environment.

**Never store provider API keys in SQLite**, and never let them traverse NERVIS, SIRVIS, Clarvis
telemetry, route explanations or events.

---

# 15. Management API, events and UI

## 15.1 Management API

Separate from `/v1`. Canonical v1 reads:

```text
GET /api/v1/health          /api/v1/route-decisions
    /api/v1/providers       /api/v1/route-decisions/{decision_id}
    /api/v1/models          /api/v1/sessions
    /api/v1/profiles        /api/v1/sessions/{session_id}
    /api/v1/profiles/{id}   /api/v1/usage[?since=]
                            /api/v1/usage/daily
    /api/v1/pools           /api/v1/diagnostics
    /api/v1/policies        /api/v1/settings
    /api/v1/runtime-state
    /api/v1/sirvis
```

**As built, 12 September 2026.** Five of these reads do not exist: `/api/v1/profiles/{id}`,
`/api/v1/diagnostics`, `/api/v1/settings`, `/api/v1/runtime-state` and `/api/v1/sirvis` each
answer 404 on the running RAVIS. Seven reads exist that the list does not name:
`/api/v1/observations`, `/api/v1/evidence`, `/api/v1/usage/records`,
`/api/v1/pools/{pool_key}/members`, `/api/v1/providers/credentials`,
`/api/v1/providers/{name}/models` and `/api/v1/providers/{name}/catalogue`, defined in
`ravis/src/ravis/api/management/routes.py` and `credentials.py`.

Since 12 September 2026 `/api/v1/health` also carries `capability_suppressions` — each model
resting from tool requests after a refusal (§10), with its provider, capability, the upstream's
reason, the window and `lifts_in_seconds` — and one write ends a rest early:
`POST /api/v1/health/suppressions/{model}/lift`, admin-only and audited as
`ravis.capability.suppression_lifted`. A new rest is published as `ravis.capability.suppressed`.

List responses use `{items, next_cursor, snapshot_revision}`. Provider and model results are
redacted and capability-evidenced.

`/usage?since=` counts spend from a moment rather than over the last 24 hours, for a dashboard's
reset; it changes the figure and nothing else. `/usage/daily` totals each of the machine's calendar
days that had calls, newest first, broken down by model and by application, with the same
estimated-never-billed and one-currency-or-no-total rules as `/usage`. Both since 12 September 2026.

**Who may write, and why being local is not an answer.** The mutating half of this
surface — enabling a provider, narrowing a catalogue, re-pointing a pool, and the
credential writes §15.1 already separated — requires an `admin.`-prefixed credential.
It does **not** accept a loopback bind as authorization, and that is a correction
rather than a tightening for its own sake.

Until 4 Sep it did. `_may_write` returned early whenever the bind was loopback, which
after §16 item 2 made loopback the only bind that starts meant every configuration
write was effectively unauthenticated. The consequence is the one §15.1 already
states for keys, one surface along: **administration arrived free with the ability to
call the gateway.** Clarvis holds an ordinary client credential, so "any local
process" was never hypothetical — a bug in an agent loop could have disabled a
provider for everything else on the machine.

An ordinary client credential is therefore refused here, exactly as it is for keys.
Two predicates rather than one — `may_write_configuration` and
`may_write_credentials` — because they are different powers that share a grantor
today, and an operator role that may toggle a provider but never touch a key changes
one and not the other.

**The dashboard did not lose the screen.** The objection to this bar, recorded when
the split was first made, was that it "would take the Providers screen away from a
loopback install" — true while the browser called RAVIS directly with nothing to
present. Those five writes now go through NERVIS, which holds the `admin.` credential
the launcher mints and already proxied the credential writes the same way. The
credential never reaches the browser, which is the point of the hop.

`/api/v1/pools` reads the `VirtualModelPool` set with each pool's declared requirements and its
**currently eligible members, derived rather than stored** (§5.2) — so an installed model that
gains or loses a capability moves the membership without anyone editing a list.
`/api/v1/policies` reads `RoutingRule` in the `IF … THEN …` form of §9.6, marked hard or soft.
`/api/v1/policies` accepts no mutation: operator policies are loaded from `policies.json` in
RAVIS's configuration directory. **Pools do accept one** — corrected 12 September 2026, since
this paragraph said neither did. `PUT /api/v1/pools/{pool_key}/members` narrows one pool to
chosen models or clears the narrowing, and `POST /api/v1/pools/curate` removes every narrowing
so membership is derived again; both are audited. Nor does a profile change go through
`POST /api/v1/profiles/{id}/activate`: that endpoint is not built, and a profile is currently
the display name of a pool (`GET /api/v1/profiles` returns one per pool, each at revision 1),
so there is no separate profile to activate yet.

Canonical v1 mutations:

```text
POST /api/v1/profiles/{profile_id}/activate
POST /api/v1/evidence/refresh
POST /api/v1/route-tests
PUT  /api/v1/providers/{name}/enabled     {"enabled": true｜false}
```

**Runtime control has no RAVIS endpoint until an ownership contract adds one.** Runbook §2.2 adds
them, **for Codex only**: the Codex runtime routes (`/api/v1/codex…`, admin for changes, for UX and
audit); the agent-session relay (`/api/v1/agent-sessions…`, Clarvis client credential plus session
token, NERVIS and admin refused, except the stop-only `owner-stop`, which takes only the owner's
command-line credential or NERVIS's admin credential, and a confirmation); and the project lock
(`/api/v1/project-locks…`, Clarvis client plus lease). §15.1.2 lists them — decided 13 September
2026, not built — and the contract fixtures, shared with Clarvis, carry their shapes. Mutations accept
`Idempotency-Key` and `If-Match` where state changes, are separately authorized and audited, and
return the actual post-state plus revision. **Never expose credential values.**

**As built, 12 September 2026.** Of the four mutations listed, only
`PUT /api/v1/providers/{name}/enabled` exists. Activating a profile, refreshing evidence and
route tests have no route defined, and NERVIS's Diagnostics screen says so of route tests. Five
writes exist that the list does not name: `PUT /api/v1/pools/{pool_key}/members`,
`POST /api/v1/pools/curate`, `PUT` and `DELETE /api/v1/providers/credentials/{name}`, and
`PUT /api/v1/providers/{name}/models`. Each publishes an audit event
(`ravis/src/ravis/api/management/audit.py`). `If-Match` is honoured on the pool-members write
(`_if_match_refusal` in `api/management/routes.py`). **`Idempotency-Key` is not implemented, on
purpose:** the writes replace whole state, so a replay leaves the same result, and a replay
cache would imply a guarantee it does not add. The one effect that is not idempotent, the live
catalogue refresh a credential write triggers, wants a refresh cooldown, which is not built
either. `STATUS.md`'s entry on M18b's management half has the reasoning.

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

### 15.1.2 Codex agent sessions — decided 13 September 2026; the relay built in R3 (M29)

Runbook §2.2 is the contract; this is the list of what RAVIS will serve for it. The engine's catalogue
id is `ravis/clarvis-codex`: it was `ravis/codex` until 0.23.11, when the owner renamed it to sit with
`ravis/clarvis-agent` and `ravis/clarvis-chat` (13 September 2026). The old id has no alias. **Since M29's
second increment (R2, 0.23.9) the routes in the first table exist, and since calibration's
harness (Cal, 0.23.10) the dev-only calibration route does too, and since the relay increment (R3,
0.23.15) every agent-session route does, and since R4 (0.24.0) the project lock's routes do too.** Their request and response shapes, every
error code and the event stream are
the contract fixtures in `ravis/tests/fixtures/relay-contract/`, with the shared lock rule's cases in
`ravis/tests/fixtures/lock-rule-cases.json`; `tests/test_codex_contract_fixtures.py` holds them to
their manifest, and Clarvis keeps a copy. Errors use the MEP envelope (runbook §4.5), bodies are
JSON, and nothing here — nor RAVIS's logs, audit or events — carries prompt text, approval text,
commands, file contents or diffs, except the relay stream and snapshot a session-token holder reads.

**Codex's state, allowance and running tasks.**

| Route | Who | What |
|---|---|---|
| `GET /api/v1/codex` | any caller | Always 200, from an in-memory snapshot: one state (`checking`, `not_installed`, `not_available`, `untested_version`, `runtime_down`, `signed_out`, `sign_in_expired`, `account_changed`, `quota_exhausted`, `signed_in`), the runtime pin, the account, the plan's allowance per window (never money; unknown is never zero), models, and running tasks by folder name — each task's id and turn only for named callers |
| `POST`, `GET` and `DELETE /api/v1/codex/sign-in`; `POST /api/v1/codex/sign-out`; `POST /api/v1/codex/account/confirm` | admin | A browser sign-in inside RAVIS's one Codex process, its cancellation, sign-out, and confirming the account after its fingerprint changed |
| `GET /api/v1/codex/version-check`; `POST /api/v1/codex/accept-version`; `DELETE /api/v1/codex/accept-version/{sha256}` | admin | The report on an untested binary, and accepting or revoking it; an accepted version stays paused until the file-rules re-test proves it |
| `POST` and `GET /api/v1/codex/reprove` | `admin.owner_cli` to start; any named caller to read | The file-rules re-test, started from the menu bar; 403 `REPROOF_NOT_ALLOWED` for every other credential, NERVIS's included |
| `POST` and `GET /api/v1/codex/calibration/runs`, `GET …/runs/{run_id}` | `admin.owner_cli` | Exist only while `RAVIS_CODEX_CALIBRATION=1`: start a calibration run on two throwaway git projects (with an `Idempotency-Key` and the owner's allowance go-ahead), and read its progress and result. `ravis codex calibrate`, run as `tools/run.py codex calibrate`, calls them |

Admin here is UX and audit, not a boundary against local programs (runbook §2.2).

**The agent-session relay.** Every route needs a Clarvis client credential (application `clarvis`),
checked before anything else, GETs included: anonymous callers, every admin credential and the
`nervis` and `launcher` applications get 403 `AGENT_CLIENT_NOT_ALLOWED`, even holding a valid session
token. Routes with `{sid}` also need that session's token in `X-Agent-Session-Token`; a missing or
wrong one is 404.

| Route | What |
|---|---|
| `POST /api/v1/agent-sessions` | Create a task in an allowed workspace root, taking the project lock; returns the session, its token (once) and the events URL |
| `GET /api/v1/agent-sessions?workspace_root=` | The workspace's sessions, without tokens or payloads |
| `GET …/{sid}`, `GET …/{sid}/events`, `GET …/{sid}/transcript` | The session with its pending requests; the resumable SSE stream (§11); Codex's own turns, read through |
| `POST …/{sid}/turns`, `…/steer`, `…/interrupt`, `…/requests/{rid}/answer` | Continue, steer, stop, and answer a request with a decision RAVIS allows; a turn needs the project lock |
| `POST …/{sid}/presence`, `…/mode`, `…/leftover` | The panel's heartbeat, the mode from the next turn, stopping leftover processes |
| `POST …/{sid}/settle-claim`, `…/settle`, `…/cancel`; `DELETE …/{sid}` | One window claims and records the settle after Clarvis commits; cancel; end, keeping the thread |
| `POST …/{sid}/reissue-token` | A new token for the session's own root, once no window has been attached for 60 s |
| `POST …/{sid}/owner-stop` | **The one exception:** its own router; only `admin.owner_cli` (the menu bar) or `admin.launcher` (NERVIS's control route); no session token (400 if one is sent); a confirmation of the folder name and current turn; stops and pauses the task and does nothing else |

`Idempotency-Key` is required on create, `turns`, `steer`, `answer`, `settle`, `reissue-token`,
`owner-stop`, the project lock's create, takeover and transfer, and `reprove`. That is the exception
to the note above that RAVIS's writes don't implement it: a replayed settle or transfer must return
its original result rather than repeat it. Workspace roots resolve to a realpath inside
`agent_allowed_roots`; `$HOME`, configuration and credential folders, `.run` folders, an allowed-roots
entry itself, and — by the owner's decision — RAVIS's own checkout and its sibling `clarvis` are
refused with 422 `WORKSPACE_ROOT_NOT_ALLOWED`.

**As built in M29's third increment (R3, 0.23.15, 13 September 2026)**, in `src/ravis/agent/`: every
route above but the project lock's, against the fixtures. The approval settings are the ones
calibration measured (run `cal_d2185ed08f50`: Codex's granular policy for every mode), and no
approval grants network, because on Codex 0.154.0 none ever opens it; both live in
`agent/calibration_dependent.py`, the one place a later calibration changes. Internet access comes
from an approved-sites allowlist instead: a site Codex's network proxy blocked is asked of the owner
as a `site` request (`agent/sites.py`), and allowing it adds that exact host to Codex's list while
Codex runs. Codex starts with its network proxy on and no site list; as soon as its process is
ready, and again after every restart, RAVIS writes a default list of registries and GitHub into
Codex's own configuration in RAVIS's Codex home, through the same write an allowed site uses
(Cal-3, 0.24.2). Until Codex has taken that list no task starts, and `GET /api/v1/codex` says why.
Codex's two permission-request features, switched on in 0.23.14, were
withdrawn. Because an interrupted Codex turn leaves its open request unresolved
(K7), RAVIS answers and publishes every open request itself whenever a turn ends, however it ends;
a file change is offered only once its item says what it would write (K12). A replayed create or
token reissue returns the same token within a RAVIS run, and no token is stored. A task settled
idle releases its project lock; one settled for a transfer keeps it for the destination's token.
Malformed bodies are 422 `INVALID_REQUEST_BODY`. Every task and turn stays refused with 409
`CODEX_NOT_READY` while the running build's strict file rules aren't proven. The project lock's
routes, restart reconciliation and per-task process recording followed in the fourth increment
(R4), below.

**Calibration, as rewritten in Cal-2 (0.23.16, 13 September 2026).** Of the dev-only calibration
route's sixteen questions, three now check what the first real run found and the owner decided. K3
proves the approved-sites allowlist in one running task: a default site answers; `example.com` is
refused with the proxy's fixed line, which `agent/sites.py` detects; calibration adds it through
`SiteAllowlist.add` and the same task reaches it with no restart; loopback and `example.org`, never
added, stay refused; and the Codex site list is written back afterwards. K7 passes when an
interrupted turn ends within the cap and nothing it had open is left unanswered — calibration
answers it with the stop response when the turn ends, as a task does. K8 records a Codex that never
sends a permissions request, which doesn't keep a full run from proving the rules. The candidate
profile is the syntax Codex 0.154.0 accepted, with R3's default sites as its network section, and a
full pass pins it. K6 is unchanged there; R4 changed it (below).

**Calibration fixed again in Cal-3 (0.24.2, 13 September 2026).** The fifth real run
(`cal_330b7525d115`, RAVIS 0.24.1) passed every file-rule question, K7 and K13, and failed two, each
for a confirmed cause. **K3's live add came back `okOverridden`**, because the site list was in the
launch flags: a `-c` flag is Codex's command-line layer, which outranks every `config/batchWrite` to
`permissions.<profile>.network.domains`. Checked without a model on Codex 0.154.0: launched with
the network section and no `domains`, the same upsert answers `ok`, the site answers at once, and
an unwritten host stays blocked. So no launch flag and no pinned or candidate profile carries a
site list (a stored one has it taken out), and the default sites are written when Codex becomes
ready, as described above. K3 waits for that start-up write's answer and fails naming Codex's word
if it wasn't taken. **K6 could never see all its commands**: the sandbox forbids the listener
`python3 -m http.server`, `script -q /dev/null sleep 600` held the turn in the foreground, and
`sleep 600 &` ended at once. Each project now runs one command, `sleep 600 & script -q /dev/null
sleep 600 & python3 -c 'import time; time.sleep(600)' & wait`, and K6 waits for the five processes it
makes: the waiting shell, `sleep`, `script` and its child, and `python3`.

**Calibration fixed again in Cal-4 (0.24.3, 14 September 2026).** The sixth real run
(`cal_ed672bf12c6f`, K3 and K6 only) failed both. **K3's add was taken** — `ok`, not overridden —
but the turn already running stayed blocked: Codex 0.154.0's proxy keeps the site list a turn started
with. The owner decided that a site counting from the task's next step is enough, since the thread
and its progress carry on. So when the retry in the running turn is refused, K3 asks for the site
once more in the thread's next turn, passes if it answers there, and records `reached_in`. Whatever
resumes a task after the owner allows a site must therefore start a new turn in the same thread.
**K6's failure was a miscount**: all five of B's long-runners survived A's Stop; what was gone were
three `(bash)` helpers caught while B was still starting. K6 now waits for and judges only its
long-runners (`OUR_COMMANDS`); A's Stop still signals every process attributed to A.

**The project lock, for both engines.**

| Route | Who | What |
|---|---|---|
| `POST /api/v1/project-locks` | client | Take a project's write lock for a Clarvis-engine run, or adopt its checkout lock file; returns a lease |
| `POST /api/v1/project-locks/{lid}/heartbeat` and `…/release` | lease | Every 15 s, carrying any running command; release only once the run's processes are confirmed gone |
| `POST /api/v1/project-locks/{lid}/takeover` | client, no lease | Only from a Clarvis-engine holder that is gone, unresponsive or waiting, with a confirmation; a Codex holder is attached to, never taken over |
| `POST /api/v1/project-locks/{lid}/transfer` | lease, or the holding session's token | Hand the lock between engines for a switch; an expired transfer token never releases it |
| `GET /api/v1/project-locks?workspace_root=` | client | The lock on a root, or null |

**As built in M29's fourth increment (R4, 0.24.0, 13 September 2026)**, in `src/ravis/agent/`
(`lock_routes.py`, `lock_api.py`, `group_kill.py`, `attribution.py`, `reconcile.py`), against
`project-locks.json`, whose `decided_in_r4` records what the design left open. A window takes a free
project with a lease (`lk_…`, kept only as its sha256), heartbeats it with its running command, and
releases it only with its processes confirmed gone. A held project is 409 `PROJECT_LOCKED` naming the
holder, with `takeover_allowed` (a Clarvis window that is gone, unresponsive or waiting) or the Codex
task to attach to. A takeover is for exactly the root the lock is for, needs the confirmation the
holder's verdict calls for, revokes the old lease first, and stops the old window's running command
with its whole process group and descendants before it hands over; if that can't be confirmed, the
lock stays `leftover` and nobody holds it. A transfer reserves the project for the other engine for
15 minutes (`tt_…`); from a Codex task, RAVIS then leaves its lock file to the destination window, and
an expired token releases nothing. A window's file lock is adopted over a lock a restart superseded,
after which a window may settle the Codex task (F-A9).

**Which process is whose** (§4.5, as calibration found it). Codex runs every command through
`sandbox-exec`, which replaces itself, so no sandbox argument shows in the process table. Every two
seconds while a turn runs, RAVIS reads the table under its own Codex process and gives each *command
root* — a process whose parent has no task — to the project whose root holds its working folder
(`lsof`), when that project's turn started before it; children follow their parent, and a process
keeps its task by pid and start time after launchd adopts it. A process two tasks claim is reported
and never signalled, unless the owner presses **Stop them** for it. Attributed processes are recorded
in `agent_process` and written into the checkout lock file's `leftover` as `{pid, start, comm}`; a
Stop signals only those, one by one, never by group. Calibration's K6 uses the same rule. Residual
gap: a command that changes into another running task's root before RAVIS first reads its folder.

**Restarts, shutdown and new builds.** Until a restart's reconciliation has run, every agent-session
and project-lock route answers 503. Reconciliation ends only recorded processes, then applies the
restart adoption rule (`lock-rule-cases.json`): a lock file naming a previous RAVIS instance
(`ravis_instance`) is rewritten and kept; one naming anyone else is left untouched, and the lock is
`superseded` (409 `LOCK_SUPERSEDED`) until the window registers its lock or lets go of its file.
Shutdown interrupts running turns at once and then ends recorded processes, briefly enough to finish
inside the launcher's six seconds. While a new Codex build waits to run, new tasks and turns get 409
`CODEX_NOT_READY` (`codex_update_pending`), and a turn only waiting for an answer becomes
`paused_for_update` after ten minutes. Codex's own threads are deleted after
`agent_history_retention_days` (90) unused, unless a Clarvis checkpoint still names them.
`GET /api/v1/codex` gives named callers `account.fingerprint_sha256`.

**NERVIS reaches none of these** except `GET /api/v1/codex` through its GET relay, and seven control
routes: the sign-in, sign-out, account and version routes above, and a task's Stop, which forwards to
`owner-stop` (`NERVIS.md` §8).

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

**Codex agent sessions** (M29 — decided 13 September 2026; `ravis.codex.state_changed` and the
sign-in, sign-out, account, version and re-test audit entries are built in R2, the session and lock
events and the token-reissue and owner-stop audit entries in R3, and `ravis.project_lock.taken_over`
in R4) add
`ravis.codex.state_changed {from, to, reason_code}`, `ravis.codex.sign_in_started`,
`ravis.agent_session.started {session_id, mode}`, `ravis.agent_session.state_changed {session_id,
from, to}` — with `reason_code: owner_stop` when the owner Stop route stopped the task —
`ravis.agent_session.request_opened {session_id, request_id, kind}`,
`ravis.agent_session.request_resolved {session_id, request_id, by, decision_kind}` and
`ravis.project_lock.changed {lock_id, state, holder_kind}`. **Metadata only:** state names, ids and
counts, never request text, commands, paths or feedback. Each session stores a trace id minted at
creation, so events emitted from the Codex process's reader carry one. Audit adds the sign-in,
sign-out, account, version and re-test entries, `ravis.agent_session.token_reissued`,
`ravis.agent_session.owner_stopped` and `ravis.project_lock.taken_over`.

**As built, 12 September 2026.** Three events publish from the request path
(`ravis/src/ravis/api/openai/chat.py`): `ravis.route.selected`; `ravis.route.refused`, which is
this list's `no_route`, named so that a type never asserts a selection that did not happen; and
`ravis.request.completed`, whose severity is `error` when no attempt succeeded. Management
writes publish audit events: `ravis.credential.set`, `ravis.credential.forgotten`,
`ravis.provider.enabled_changed`, `ravis.provider.model_filter_changed` and
`ravis.pool.members_changed`. **Not built:** request accepted, failed and cancelled as events of
their own; route fallback; usage recorded; provider, capability and runtime state changed; and
profile changed. **Nor are the spans.** RAVIS emits no per-stage span, so a NERVIS trace draws
RAVIS as one bar covering the interval between its events.

## 15.3 Dashboard

**Most of these screens exist, rendered by `nervis/index.html` — not all of them; the dated
note below says which.** It is not a mockup of
this section — it is a working implementation whose data layer is shaped like §15.1's responses
and whose every method cites the endpoint it will call. From Stage 3 it reads RAVIS's real
read-only management API (M18a), so the screens below are how RAVIS is observed *during* the
build rather than after it. Do not build a second implementation inside RAVIS: M17 is deferred
for exactly this reason, and NERVIS serves these properly from Stage 6 (`NERVIS.md` §25).

Current profile, requests today, local %, cloud %, spend, estimated savings, provider status,
loaded models, SIRVIS status, recent routes, errors.

**Models page:** model, provider, capabilities, context, cost, latency, quality, SIRVIS
evidence, protocol mode, status.

**Clarvis compatibility page** (developer-facing, not essential user UI) showing the conformance
results live.

**As built, 12 September 2026.** NERVIS's RAVIS tab has twelve screens: Dashboard, Routes,
Sessions, Spending, Pools, Providers, Credentials, Policies, Evidence, Logs, Diagnostics and
Settings (`APP_CONFIG` in `nervis/index.html`). Four things this section names are not among
them. There is **no Profiles screen**, and **no Models page** of its own; the Pools screen reads
the model list beside the pools. The dashboard shows **no estimated savings**; the word appears
nowhere in the page. And there is **no live Clarvis compatibility page**: the Diagnostics screen
lists the suite's checks under *not run from here*, because `ravis conformance clarvis` is a CLI
command whose result reaches no endpoint a browser can read. **Since 13 September 2026 the Dashboard
shows no current profile:** at the owner's request its headline row is decisions, local share, spend
and Codex's plan allowance, and the active profile is shown on the Settings screen.

## 15.4 CLI

```bash
ravis doctor
ravis serve
ravis providers · ravis models · ravis profiles · ravis routes · ravis usage
ravis sirvis status
ravis test --model ravis/auto "Explain this code"
ravis conformance clarvis
```

**As built, 12 September 2026.** `ravis --help` lists six commands: `serve`, `doctor`,
`conformance`, `preflight` (whether a consumer pointed here right now would work), `credential`
(stores one read from standard input, for bootstrapping an admin token) and `restore-database`
(puts back the backup taken before a migration). **Not built:** `providers`, `models`,
`profiles`, `routes`, `usage`, `sirvis status` and `test`. What the first five would print is
readable from the management API (§15.1) and NERVIS's RAVIS screens.

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
- **Without a platform keyring** — credentials are written to a `0600` file inside a `0700`
  directory, which is plaintext and says so (§14, rule 16); a provider whose credential is in
  none of the file, the keyring or the environment stays unavailable. *Corrected 12 September
  2026: this line said "never plaintext", which §14 and rule 16 stopped promising on
  9 September.*
- **Without Codex** (M29 — decided 13 September 2026; the listing rule and `RAVIS_CODEX_ENABLED` are
  built, the sessions are not) — `ravis/clarvis-codex` is not listed,
  session creation is refused with the state's reason, the lock API still serves Clarvis's own
  engine, and nothing else changes. Disabling it with `RAVIS_CODEX_ENABLED=false` and a restart
  refuses new sessions while existing ones can still be settled and ended.

---

# 17. Storage

```text
Provider · ProviderCredentialRef · ProviderModel · ModelCapability · SirvisModelRef
VirtualModelPool · RoutingProfile · RoutingRule · ModelAlias
ClientApplication · ClientCredential
RoutingSession
RouteDecision · RouteCandidate · RequestMetric
ProviderHealth · ModelObservation
UsageRecord · CostRecord · Budget
SirvisEvidenceSnapshot
Setting
```

**As built, 12 September 2026.** Most names in this list are not tables. RAVIS's SQLite database
has six: `applied_migration`, `client_application`, `setting`, `routing_session`,
`capability_trial` and `usage_record` (schema in `ravis/src/ravis/storage/database.py`).
Operator configuration is JSON in RAVIS's configuration directory (`providers.json`,
`models.json`, `pools.json`, `policies.json`, `prices.json`, `observations.json`), with
credentials in the keyring or the credential file (§14). **`RouteDecision` is not stored:** the
most recent 200 decisions are held in memory (`DecisionLog` in
`ravis/src/ravis/api/management/decisions.py`), so a restart empties `/api/v1/route-decisions`.
**`SirvisModelRef` is not built**; nothing in `ravis/src` defines it, so the join the next
paragraph describes does not exist yet.

`SirvisModelRef` is the join RAVIS would otherwise have to guess. A provider reports a runtime
name — `qwen3-30b-a3b-mlx` — while SIRVIS keys evidence by family, variant, source revision,
format, quantization, runtime, runtime version, runtime configuration and role. §13.3 forbids
inferring one from the other by name, and SIRVIS forbids RAVIS reading its database, so the
reference is stored: one `ProviderModel` resolves to at most one SIRVIS build key, recorded
when SIRVIS confirms it and **left null when it does not**. A null reference routes on `UNKNOWN`
provenance, which is a correct outcome; a guessed one is not.

**Built in M29's third increment (R3, 0.23.15):** migration 8 adds `agent_session`, `agent_request`, `agent_turn`,
`agent_process`, `project_lock` and `agent_idempotency` — separate records, never `routing_session`.
Sessions, turns and requests are kept 30 days after a session ends, process rows 24 hours after they
are confirmed gone and idempotency records 24 hours; a lock row exists only while it is held. None
stores prompt, approval or command text — only ids, states, counts, timestamps and the workspace's
real path and folder name (§9.7). Rolling back past migration 8 means restoring the backup taken
before it, which loses the agent-session records. `agent_process` is created by migration 8 and
filled since the fourth increment.

**Migration 9 (R4, 0.24.0)** adds `ravis_instance` (each RAVIS start's pid and process start time,
the last 20, for the restart adoption rule), `agent_thread` (a Codex thread's id, its project's path
and when it was last used, kept past the task's 30-day records for the 90-day sweep, then 30 days
after deletion) and a unique index making `agent_process` one row per task, pid and start time. It
is backed up first (`ravis.db.v8.bak`); rolling back loses only those records.

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

**M29, in part:** `src/ravis/codex/` holds the runtime check and pin (`runtime.py`, `pin.py`,
`tested_runtimes.json` and the pinned build's definition hashes in `schemas/`) and, since R2,
RAVIS's one supervised Codex process (`supervisor.py`, `rpc.py`, `routing.py`), its state
(`state.py`), sign-in and account (`sign_in.py`, `account.py`), allowance (`usage.py`), version
acceptance (`acceptance.py`, `schema_report.py`), the file-rules re-test (`reprove.py`), RAVIS's own
record (`store.py`) and the service composing them (`service.py`); the routes are
`api/management/codex.py`. **Since Cal (0.23.10):** `codex/calibration/` holds calibration — its
sixteen questions, the harness answering from each question's fixed list, the redacted transcripts,
the results summary and the pin entry written only by a full pass, and `ravis codex calibrate` —
behind `api/management/codex_calibration.py`, mounted only while `RAVIS_CODEX_CALIBRATION=1`; and
`codex/lock_rule.py`, `codex/lock_file.py` and `codex/process_table.py` hold the shared lock rule
(settling its module's name), the checkout lock file, and process attribution with the end
sequence, which calibration uses first. **Since R3 (0.23.15):** `src/ravis/agent/` holds the
relay — its routes (`routes.py`), callers and tokens (`identity.py`, `tokens.py`), workspace roots,
idempotency, the session store (`store.py`), each task's state machine (`session.py`,
`sessions.py`), request views, translation, redaction and the event log, the values calibration
decides (`calibration_dependent.py`), the project lock (`locks.py`) and per-task process clean-up
(`cleanup.py`). **Since R4 (0.24.0):** the project-lock routes (`lock_routes.py`, `lock_api.py`),
the takeover's group kill (`group_kill.py`), process attribution and recording (`attribution.py`,
on `codex/process_table.py`) and restart reconciliation (`reconcile.py`). The contract fixtures are in
`tests/fixtures/relay-contract/` and `tests/fixtures/lock-rule-cases.json`.

---

# 19. Development principles for AI coding agents

**Engineering standards live in `ECOSYSTEM_RUNBOOK.md` §14** — complexity ceiling, naming,
comments, error handling, tests, and the CI gates that enforce them — and are not restated here.
The numbered principles below are RAVIS's wire-compatibility rules, which no general coding standard implies, and they add to that standard rather than replacing
it. Where one of them tightens a §14 rule, the tighter rule wins.

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
16. API credentials are never written into SQLite and never travel to a peer, a trace, a route
    explanation or an event. They are written to the platform keyring where the machine has one,
    verified by reading back what was stored; where it has none, to a `0600` file, which is
    plaintext and is said so rather than promised away. *This rule read "never stored in
    plaintext" until 9 September 2026 while `credentials.py` wrote the file on every platform —
    an external audit found it, and the keyring write was built rather than the promise
    weakened.*
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

Milestone numbers identify work; they do not schedule it. The build order is the runbook's,
and §20.1 maps these milestones onto its stages.

| # | Milestone | Acceptance |
|---|---|---|
| **M0** AUTOMATED VERIFIED | Foundation — Python package, FastAPI, configuration, SQLite, migrations, structured logging, CLI, §4.4 admission control, §9.6.0 identity resolution, the `/ecosystem/*` MEP surface | `ravis doctor` and `ravis serve` work; each §4.4 limit refuses in a negative test; a non-loopback bind without a credential fails to start; `ravis doctor` prints the resolved model-to-provider truth table, naming the setting behind each resolution, without contacting any upstream; MEP conformance fixtures pass at one pinned protocol version and a mismatched major fails cleanly; an unauthenticated caller resolves to `anonymous` and gains no named application's privileges. **Moved from IMPLEMENTED on reverification against §14.8:** the gap named at the time — "`ravis serve` binding a port alone is tested nowhere" — is closed. `ravis/tests/test_cli.py` now binds a real socket with `uvicorn.Server` and drives it with a real HTTP client, rather than the ASGI transport every other test uses, which was never going to fail to bind anything |
| **M1** LIVE VERIFIED | OpenAI-compatible transparent pass-through — `/v1/models`, `/v1/chat/completions`, streaming, one compatible upstream, no routing intelligence | OpenAI SDK can use RAVIS; `/v1/models` is cache-backed; the stream reaches the client without buffering; `[DONE]` correct; cancellation reaches upstream |
| **M2** AUTOMATED VERIFIED | Clarvis wire-contract harness — conformance fixtures, tool-fragment tests, reasoning-field tests, cancellation tests | `ravis conformance clarvis` passes against the transparent route |
| **M3** AUTOMATED VERIFIED | Two separable halves, and the stage mapping schedules them apart. **M3a — adapter interface:** `NormalizedRequest`, `NormalizedResponse`, `NormalizedStreamEvent`, the `ProviderAdapter` protocol and capability discovery. **M3b — translated execution path.** M3a is a prerequisite of M6's capability filtering and therefore of Stage 3; M3b is Stage 5 | Transparent route still passes Clarvis conformance; translated adapter works independently |
| **M4** AUTOMATED VERIFIED | Anthropic native adapter — text, streaming, tools, errors, usage | OpenAI SDK works through Anthropic; Clarvis tool semantics still pass where applicable |
| **M5** LIVE VERIFIED | Basic routing — `auto`, `fast`, `performance`, `cheap`, `clarvis-chat`, `clarvis-agent` | Profiles select predictably; the agent pool enforces tools |
| **M6** LIVE VERIFIED | Capability filtering — tools, vision, context, structured output, streaming | Incompatible candidates eliminated before scoring; the Clarvis agent pool cannot select a non-tool model |
| **M7** AUTOMATED VERIFIED | Provider expansion — Google, OpenRouter | Transparent/translated path chosen correctly; conformance stays green |
| **M8** LIVE VERIFIED | Local providers — LM Studio, Ollama, generic OpenAI-compatible | Local/cloud routing works; transparent local streams preserve tool semantics |
| **M9** LIVE VERIFIED | **Clarvis live integration** — point unmodified Clarvis at RAVIS | Custom provider connects; chat works; agent works; fragmented tools work; Stop works; chat and agent pools route separately. **Must not require any Clarvis source modification** |
| **M10** LIVE VERIFIED | Credentials and provider UI — macOS Keychain, provider UI, health tests, enable/disable | Secrets absent from all outputs and logs |
| **M11** AUTOMATED VERIFIED | Sessions — `RoutingSession`, affinity, sticky routes | Isolation, correlation and retention tests pass |
| **M12** AUTOMATED VERIFIED | Health, retries and fallbacks — circuit breaker, retry budget, failure classification | Fallback never violates Clarvis pool invariants; cancellation never triggers fallback |
| **M13** AUTOMATED VERIFIED | SIRVIS evidence integration — evidence keyed by family, variant/build, runtime config, machine, role, suite | No one-number-per-model shortcut; repeated-measurement statistics preserved; provenance never upgraded |
| **M14** AUTOMATED VERIFIED | Local lifecycle intelligence — HOT/WARM/COLD, load penalty, resource awareness. **Split:** the observation half (residency preference, memory pressure) is Stage 3; the load-versus-don't tradeoff is **Stage 6, after M11**, since it needs M11's session length (M13's evidence landed at Stage 5) | Memory pressure produces a safe route change |
| **M15** AUTOMATED VERIFIED | Cost engine — pricing, estimates, actual usage, budgets | No double counting; estimates never presented as invoices |
| **M16** AUTOMATED VERIFIED | Policy engine — application policies, privacy, provider allow/deny, model exclusions, §9.6.1 background-call class. **`ClientApplication` regains `may_declare_background_calls` and `max_privacy_level` here**: both were set on every identity and enforced nowhere, and a field describing an unenforced trust boundary reads as protection, so they were removed rather than left looking live. **Also: a tiebreak that knows about reasoning overhead.** `ravis/auto` breaks a tie on smallest-build-is-cheapest, which on this machine selects a reasoning distill that spends most of a small `max_tokens` budget on reasoning tokens before emitting any content. RAVIS cannot know this from advertised metadata — LM Studio publishes no reasoning flag — so it needs SIRVIS M22b's measurement, not a name-pattern guess. **Also: pool versions and revisions**, which §4.1 makes the advertise-when condition for `ravis.virtual_profiles@1` — the capability is `degraded` until they exist | Each hard constraint provably excludes a top-ranked candidate; a declared background call never selects a paid provider under the default profile; a pool carries a revision a consumer can pin |
| **M17** | Dashboard — Dashboard, Providers, Models, Profiles, Rules, Sessions, Routes, Usage, SIRVIS. **Mostly covered by NERVIS as of 12 September 2026, and not closed here:** NERVIS's RAVIS tab serves Dashboard, Providers, Sessions, Routes, Spending for usage, Policies for rules and Evidence for what SIRVIS measured, plus Pools, Credentials, Logs, Diagnostics and Settings. It has no Profiles or Models screen (§15.3). Whether that closes M17 is the owner's call | — |
| **M18** AUTOMATED VERIFIED | Two halves, scheduled apart. **M18a — read-only management API:** the `/api/v1` reads (`pools`, `policies`, `providers`, `models`, `profiles`, `route-decisions`, `usage`), which is what makes a route decision visible while it is being debugged. **M18b — events and tracing surfaces** for NERVIS | Does not affect Clarvis wire compatibility; M18a exposes no mutation and no credential |
| **M19** AUTOMATED VERIFIED | Production observations — rolling latency, TTFT and error rate, sampled from real traffic. **Throughput is not part of this**: `observations.py`'s rolling windows and `HealthRegistry.error_rate()` are real and tested, and nothing in `src/ravis` tracks a production throughput figure — the row originally claimed one, corrected 3 Sep after an audit found no supporting code | — |
| **M20** | Concurrency awareness — active requests, local congestion, SIRVIS contention evidence | — |
| **M21** | Replay and evaluation — request replay, routing comparison | — |
| **M22** | Advanced routing — escalation, shadow routing, outcome scoring | — |
| **M23** | Responses API — `/v1/responses` | No regression in Chat Completions compatibility |
| **M24** | Packaging — `RAVIS.app`. **Superseded 12 September 2026, by the owner's decision:** one menu bar app, NERVIS M19 (`nervis/packaging/macos`), starts the whole stack from the repository, so no separate `RAVIS.app` will be built. | — |
| **M25a** | **Serverless GPU as a transparent upstream — RunPod.** A `kind: "runpod"` adapter over `https://api.runpod.ai/v2/{endpoint_id}/openai/v1`, which vLLM workers already expose OpenAI-compatibly. **Direct addressing only** — `ravis/runpod/<model>` — and deliberately *not* a pool candidate, the same position Anthropic holds. No new capability: it is Path A, so `ravis.openai_compatible.chat_completions@1` already covers it | A completion runs through a declared RunPod endpoint; no pool can select it; the key never leaves the credential store |
| **M26** | **`ravis/background` — a pool for work with nobody watching.** Unattended callers (NERVIS's own thinking, M25 there) need a pool that runs *beside* interactive work rather than competing with it, and none of the thirteen pools that existed when this row was written expressed that. Eighteen are served on 12 September 2026, `ravis/free-api` (M28) among them, and whether that one now serves this purpose is §20.1's open decision. **The invariant is "must not contend", and it is deliberately not "must be hosted" or "must be free".** Both of those are answers to the question rather than the question, and each is wrong on some machine: on a laptop with a closed runtime, free resolves to a cold local load that fights chat for RAM — the exaone incident — while on a workstation with an idle 16 GB card, local is fast, costs nothing and contends with nobody, and a hosted-only rule would spend money to avoid a machine that was sitting there. So the pool's price ceiling is **operator configuration** and the invariant is expressed through what RAVIS already measures: residency, memory pressure and §12.2's load-versus-don't tradeoff. Prefer a model already resident, refuse to pay a load under pressure, and let the ceiling say what this machine is willing to spend | A background caller and an interactive one run concurrently without either paying a model load for the other; **the same RAVIS reaches opposite answers on two machines from configuration alone** — a resident local model where one is loaded and idle, a small hosted one where the local runtime is cold or the memory is tight — with the route explanation naming which and why; spend is attributable by pool in `/api/v1/usage`, so "what did unattended work cost this week" is a query; **§9.6.1's marker is not how this is used** — "must be free" is the same baked-in answer in a different place, and it resolves to local on exactly the machine where local is the wrong choice |
| **M25b** | **Serverless GPU as a routing candidate.** Everything that must be true before a pool may pick one — see the four dependencies below | A cold endpoint warms without opening its circuit; a scaled-to-zero endpoint is distinguishable from a COLD local model in a route decision; spend on it is visible; a background call under the default profile never selects it |
| **M28** AUTOMATED VERIFIED | **`ravis/free-api` — costs nothing and runs somebody else's hardware.** Both halves are load-bearing and neither is `ravis/cheap`: cheap prefers local, and on any machine with a runtime "least monetary cost" resolves to a local model — right for cheap, wrong for the caller this exists for. Unattended work must not load a local model, because loading one is exactly how work nobody is watching starts competing for memory with the conversation somebody is having. It is also why §9.6.1's background marker is not the answer: that marker means *must be free* and a local model satisfies it. **Below `private` on the privacy ladder**, and structurally rather than by rule — a free tier is free because the prompt is worth something, so this is an egress path with logging, and `free` requiring remote while `local` and `private` require local leaves the two with no candidate in common. Rate limits are the normal case rather than a fault, and needed no new handling: `RATE_LIMIT` is already `retry_same_target=False, may_fall_back=True` | A machine with a free hosted model, a paid one and a local one routes `ravis/free-api` to the free hosted one, `ravis/cheap` to the local one, and the two disagreeing **is** the reason both exist; **a pool with nothing free refuses rather than billing** — the ceiling is a contract, so membership resolves to nothing and the engine says the pool is unavailable, where `cheap` correctly falls back to the cheapest paid; a request carrying `LOCAL_ONLY` can reach no member of this pool by any route; the description says on its face that it is logged and never private. **Moved from IMPLEMENTED, 12 September 2026:** `ravis/tests/test_pool_free_api.py` exercises the acceptance and passes, eleven tests. Offered a local, a free hosted and a paid model, the pool takes the free one while `ravis/cheap` takes the local one; given only the local or only the paid one it selects nothing, so it refuses rather than billing; the ceiling is zero rather than merely low; and the description says logged. The `LOCAL_ONLY` clause is checked as pool locality — `ravis/free-api` requires remote where `ravis/local` and `ravis/private` require local — not by sending a `LOCAL_ONLY` request. A live request through the pool, served by a free hosted model, is recorded in `STATUS.md` on 1 September 2026; that is one request rather than a recorded run of the acceptance, so the row stops short of live verification |
| **M27** | **More than one SIRVIS.** `sirvis_base_url` is one string and the evidence store is keyed `runtime_key → role → record`, so a second measuring service is not "configure another URL" — it is a store that can hold two machines' answers about the same build without one erasing the other. See below | The same build measured on two machines yields two records, both readable; a route decision names *which machine* its evidence came from; an upstream on machine A is never ranked on a measurement taken on machine B; one SIRVIS going away degrades only the machine it measured |
| **M29** | **Codex agent sessions, brokered.** Specified 13 September 2026 (runbook §2.2, §15.1.2). **The id became `ravis/clarvis-codex` in 0.23.11** (owner decision, the same day; it was `ravis/codex`, and no alias is kept): the constant, the listing, the refusal and its prefix, conformance, the capability's `backend_id` and the contract fixtures. **Built so far — R1 (13 September 2026, 0.23.8):** the runtime check and version pin — Homebrew's link through `brew --prefix`, OpenAI's signature, and the build's sha256 and both schema trees against `src/ravis/codex/tested_runtimes.json` — `ravis/clarvis-codex` listed only with `X-Clarvis-Engines: codex`, and the 400 refusal on `/v1/chat/completions` and `/v1/embeddings`. **R2 (13 September 2026, 0.23.9):** one supervised `codex app-server` over stdio in RAVIS's own Codex home, only for a tested or accepted build, with bounded I/O, a local health probe, restart with backoff that gives up after five failures in 30 minutes, a new binary swapped in only when idle, and the executable looked at again every 60 seconds; the ChatGPT browser sign-in with its port check, ten-minute expiry, cancel and sign-out, and the account fingerprint with its confirmation; the plan's allowance read every 15 minutes while idle and merged from Codex's notifications during turns; `GET /api/v1/codex` and its sign-in, sign-out, account, version-check, accept-version and re-test routes, with acceptance checks 1-7a against the pinned build's definition hashes; the file-rules re-test's harness, started only by `admin.owner_cli`; conformance's 24th check; and `ravis.codex_runtime@1`. **R3 (13 September 2026, 0.23.15):** migration 8 and the agent-session relay — every `/api/v1/agent-sessions` route and the stop-only owner route on its own router, SSE with replay buffers and cursor expiry, Clarvis-only callers and session tokens, workspace roots and the git-folder rule, durable idempotency, the decisions RAVIS allows, the unanswered-request policy, the step cap, redaction, presence, the per-task action lock, turns needing the project lock, `runs` in `GET /api/v1/codex` and `ravis.agent_sessions@1` — built to the approval settings and findings of calibration run `cal_d2185ed08f50`. **R4 (13 September 2026, 0.24.0):** the project lock's routes for Clarvis's own runs — leases, takeover with its process-group kill, transfer both ways, adoption over a superseded lock — per-task process attribution by Codex's process tree and each command's folder, recorded and ended one by one by pid and start time, restart reconciliation with the adoption rule, shutdown interrupts, `paused_for_update`, the 90-day thread sweep and migration 9. All four increments are exercised by RAVIS's own suite against fake Codex programs, not yet by a real Codex task, so they are short of AUTOMATED VERIFIED (runbook §14.8); the rest of this row — calibration proving the file rules, and the live task — is not done. One supervised Codex process with a pinned, calibrated version and a proven permission profile; sign-in and allowance; agent-session records; the SSE relay with tokens, idempotency and NERVIS or admin refusals; per-task command clean-up; the unanswered-request policy; the project lock for both engines; `/v1` refusal and conditional listing. Paired with Clarvis E-C9 and NERVIS M28, and built in four increments around a calibration step with the owner, in the order `STATUS.md` gives | Contract tests against the shared fixtures; a crash or restart marks running turns uncertain and kills only recorded processes; answers after Stop are refused; NERVIS and admin credentials get 403 on every session route except `owner-stop`, even with a token; `owner-stop` stops only, needs a matching confirmation, and refuses client credentials and tokens; a stale lock after sleep isn't treated as dead; untested versions and unproven rules refuse new tasks; conformance counts 24 checks; calibration and one live task recorded in STATUS |

## 20.1 Ecosystem gate mapping

| Runbook stage | Lands in |
|---|---|
| Stage 0 — baseline and invariant lock | M0 — no unresolved cross-owner assumption; a missing contract is a STOP item |
| Stage 1 — shared protocol | M0 — the `/ecosystem/*` surface and MEP conformance at a pinned version |
| Stage 2 — transparent gateway and Clarvis conformance | M1 + M2, **wire-level scenarios only** — stream termination and `[DONE]`, fragmented tool-call arguments, tool-call indexes and IDs, tool result IDs, `reasoning_content`, cancellation, fast cached `/v1/models`. Pool separation and fallback are Stage 3 additions to the same suite (§8.8). Plus M10, since an upstream needing a credential cannot be reached without it |
| Stage 3 — live Clarvis ↔ RAVIS | M9, and with it M3a (the adapter interface M6 filters through), M5 (chat and agent pools resolve independently), M6 (the agent pool refuses a non-tool model), M12 (fallback does not corrupt the stream), **M14** and **M18a** (see the notes below) — each of the first four is named in the stage's own exit criteria. Direct-provider fallback verified |
| Stage 5 — RAVIS intelligence | M3b + M4 + M7 + M8 (translated path, native and local adapters), M13 (SIRVIS evidence), M16 (policy) |
| Stage 6 — NERVIS core | M11 + M15, and **M14's remaining half**, which landed after M11 because that is what supplies expected session length |
| Stage 7 — events and tracing | M18b |
| **Stage 11 — pools for unattended work** | M28 (`ravis/free-api`), built alongside NERVIS M25 so background thinking had somewhere to run that costs nothing. **M26 (`ravis/background`) is unbuilt and its purpose is now served by M28** — whether it closes as superseded or keeps a distinct meaning for a machine with a resident local model is an open decision, recorded here rather than resolved silently |
| Stage 10 — whole-ecosystem hardening | M19 + M20 |
| **Unscheduled — blocked on M14's residency vocabulary** | M25b (serverless GPU as a routing candidate). **M15 and M16 are no longer blockers** — both are AUTOMATED VERIFIED, and the paragraphs below that said otherwise were stale. M25a may land at any time, because a directly-addressed upstream is not a routing decision |
| **Unscheduled — wanted only with a second machine** | M27 (multi-source evidence). Blocked on nothing; the work is not worth doing until a second host actually serves models — see §20.3 |
| **Unscheduled — Codex tasks through RAVIS (runbook §2.2)** | M29, paired with Clarvis E-C9 and NERVIS M28. It may land at any time once the contract is accepted; the owner accepted it on 13 September 2026, and its fixtures landed that day. Its increments and their order are in `STATUS.md` |
| **Unscheduled — deferred by decision** | M17 (RAVIS's own dashboard). Not "never": §15 keeps a *built-in* UI optional because the prototype at `nervis/` renders RAVIS's screens from Stage 3 onward and NERVIS serves them properly from Stage 6, so a third implementation inside RAVIS would be the redundant one. M21, M22, M23, M24 likewise deferred. Listed so that no milestone is silently unassigned |

### M25 — why serverless splits in two

The wire is nearly free and the routing is not, so they are separate milestones.
RunPod's vLLM workers speak OpenAI-compatible HTTP with a bearer key, which
`Upstream` already sends and `upstream_timeout_seconds` (300 s) already
tolerates; `KINDS` falls back to the generic adapter for an unrecognised kind,
so **M25a is one class and a configuration entry**.

**M25b needs four things that do not exist yet**, and shipping it before them
would produce a router that spends money badly:

- **A cold start is not a hung upstream, and §10 cannot currently tell.**
  Scale-to-zero means a first request of thirty seconds to several minutes.
  `breaker_failure_threshold` is 3 and `breaker_cooldown_seconds` is 30, so a
  cold endpoint trips its own breaker before it ever warms and the cooldown
  keeps it cold. Needs per-upstream breaker and timeout settings, and a
  *warming* state read from the provider rather than inferred from silence.
- **Residency has no third value (M14).** HOT/WARM/COLD describe a model on this
  machine, where COLD means seconds from disk. Scaled-to-zero means minutes and
  costs money to wake. A router that treats the two alike picks badly for
  reasons it cannot explain.
- **Cost (M15) — the unit, not the mechanism.** M15 shipped: `ravis.usage_cost@1`
  is `available`, prices come from a published book, and REPORTED, ESTIMATED and
  UNKNOWN are kept apart rather than collapsed to a number. What serverless adds
  is a unit nothing reports — a GPU-second price and a count of how many were
  spent — so this is an accounting gap on top of a working engine rather than an
  absent engine. *(This paragraph said the opposite until 9 September 2026, long
  after M15 was AUTOMATED VERIFIED; an external audit found it.)*
- **Policy (M16) — built, and pointed at the wrong thing.** §9.6.1's
  background-call class and the privacy ladder shipped and are AUTOMATED
  VERIFIED: `_privacy_refusals` and the background refusal run before ranking,
  so a declared background call already cannot reach a paid remote provider.
  What serverless needs is for those rules to *see* a scaled-to-zero endpoint as
  the paid remote provider it is, which depends on the residency value above —
  not on the policy engine being written.

**Evidence is the open design question, not a dependency.** SIRVIS keys evidence
by variant and pins it to a machine snapshot — chip, memory, thermal — and a
serverless GPU is a different machine every invocation with no honest
`machine_id`: §5.1 forbids a hardware-derived one, and there is no stable local
installation to generate one for. Either serverless becomes its own machine
class with its own provenance, or it is never benchmarked and routes on
advertised capability alone. **Decide this before M25b, not during it.**

**Nothing here is RunPod-specific except the base URL.** Any serverless
OpenAI-compatible endpoint — Modal, Together, Fireworks, a self-hosted vLLM
behind a scaler — has the same four problems, so M25b is the serverless
*shape*, not one vendor's integration.

### M27 — why a second SIRVIS is a store problem, not a config problem

**The field is singular and that is the easy half.** `sirvis_base_url` is one
string; making it a list costs an afternoon. The store underneath it is what
makes this a milestone.

Records are held `runtime_key → role → record` and **the freshest wins**.
`machine_id` rides along on each record and takes no part in that key, because
until now it could not: one SIRVIS measures one machine, so every record in the
store described the same hardware and the field was provenance rather than
identity. Point RAVIS at two, and `qwen3:14b` measured on a laptop's CPU and the
same build measured on a 16 GB card land on the same shelf — the later
measurement displacing the earlier one, then routing traffic to *either* machine
on the strength of a number taken on the other. Wrong in the confident
direction, and wrong the same way §7 of `SIRVIS.md` describes: a claim about one
machine, applied to another.

**What the work actually is.** Key on `(machine, runtime_key, role)`, and join
through the upstream's own address, since a base URL already identifies a host —
`localhost:11434` and `192.168.1.50:11434` are different machines by inspection
and need no new configuration to say so. Each source names itself through
`/ecosystem/identity`, so a multi-source store has an honest label per source
and `SourceState` becomes per-source: one measuring service going quiet should
degrade evidence for its machine and leave the other's alone, rather than
flipping the whole store to `DEGRADED`.

**It shares a question with M25b.** That milestone asks what provenance means
when a serverless GPU is a different machine every invocation; this one asks what
identity means when there are two honest machines at once. Both are the same
question — *what is a measurement a claim about?* — and answering it once, in
the store, likely answers both.

**Do it when there are two machines, not before.** A single SIRVIS on whichever
host carries the models, with the other host's models unmeasured and labelled as
such under §13.4, is the cheaper arrangement and correct on its own terms. The
second source earns its keep only once the second machine is really serving
models a pool would rank.

> **M18a moved to Stage 3, 2026-08-23.** The read-only half of the management API is what makes
> the work visible while it is being done: the prototype's Routes and Pools screens read exactly
> these endpoints, and a real route decision on screen — pool, selected model, requirements,
> every excluded candidate and its reason — is how M9's integration gets debugged. Deferring it
> to Stage 6 meant nothing was observable until after the hardest integration was finished. It
> exposes reads only; every mutation, and the events and tracing surfaces, stay at M18b.
>
> **M14 moved to Stage 3, 2026-08-23.** Its *observation* half — residency preference and
> memory-pressure awareness — turned out to be a prerequisite for M5 being usable rather than a
> refinement of it. Routing with no view of what is loaded picks alphabetically, and during M5
> testing that evicted a deliberately-loaded model to load an alphabetically-earlier one, on a
> machine where every pool request risks a multi-gigabyte load. Preferring what is already
> loaded needs no ownership of the runtime's lifecycle (§12.2 restricts RAVIS to operations the
> adapter or SIRVIS owns), which is exactly why this half can land early. **M14's remaining
> half moves to Stage 6**: the load-versus-don't tradeoff needs expected session length (M11)
> and evidence that a cold model is actually better (M13), and RAVIS still neither loads nor
> unloads anything.
>
> **Corrected 2026-08-28.** This said "stays at Stage 5", which cannot be true and was not
> caught because both halves were read as one milestone: M11 is a *Stage 6* milestone, so a
> Stage 5 item was declared to depend on one that lands after it. The stage table above never
> listed M14 under Stage 5 either, so the two disagreed. Stage 5's own exit criteria — all
> five of them, in the runbook — are met without this half, which is the other way of saying
> it does not belong there. M13, its evidence dependency, landed at Stage 5; M11 is what it
> is actually waiting for.
>
> This also pulled a **slice of M8** forward: the LM Studio residency probe. M14 is inert
> without a runtime that reports residency, and a generic OpenAI-compatible endpoint does not.
> It is a residency probe only, not the LM Studio adapter, and it is kept in `runtime/` rather
> than `providers/` so it is not mistaken for one.
>
> **Ordering note.** M4 is numbered before M9 but executes after it, and M3 straddles them:
> M3a lands at Stage 3 because M6 cannot filter on capabilities without the adapter that
> discovers them, while M3b — the translated path — waits for Stage 5. §20.2 already says
> translation comes only after the transparent Clarvis slice works. Where the numbering and the
> stages disagree, the stages win.

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

**This is not the first release.** The runbook ships RAVIS twice: *RAVIS alpha* at Stages 2–3
— transparent gateway plus Clarvis conformance — which with Clarvis is the first genuinely
useful release in the ecosystem, and then the Stage 5 build described here. MVP below is the
Stage 5 outcome. Do not treat it as the target for the first shippable thing.

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

Shadow routing, automatic self-learning, audio, complex orchestration, a distributed gateway,
cloud sync, accounts, AI-generated policies, automatic summarization, the Clarvis Bridge, and
NERVIS code-server integration.

**Image generation left this list on 7 September 2026, and only half of it did.** What shipped
is *routing to* a model that answers a chat completion with a picture beside its text — a
capability, a pool and a normalized field, all of which fit the gateway RAVIS already is.
What remains a non-goal is RAVIS operating an image endpoint of its own: a `dall-e`-shaped
`/v1/images/generations` is a different protocol with a different request, and `dall-e` stays
unconditionally in the not-chat list that §5's membership filter consults.

---

# 21. Conflicts resolved in this consolidation

| Conflict | Sources | Resolution |
|---|---|---|
| Error envelope | Addendum mandates the MEP envelope; the plan requires OpenAI-compatible errors | Split by surface: `/v1/*` returns OpenAI shapes, `/api/v1/*` and `/ecosystem/*` return the MEP envelope. Correlation IDs in headers on both |
| Management API paths | Addendum used `/v1/providers` etc.; the plan used `/api/v1/…` | `/api/v1/…` for management — `/v1` is reserved for the OpenAI-compatible surface and must not be shared |
| Profile IDs | Both propose `ravis/clarvis-chat` / `ravis/clarvis-agent`, but the addendum says confirm before coding | Retained as the proposal, with §5 requiring verification against the real Clarvis picker. RAVIS adapts, not Clarvis |
| Evidence provenance enums | SIRVIS publishes 4 levels; RAVIS defines 5 of its own | Both kept; §13.3 defines the mapping and forbids promotion |
| Milestone numbering | Plan M0–M24; addendum E-R0–E-R7 | M-numbers identify the work. The addendum's E-R gates are retired — that document is not in this set — and §20.1 now maps the milestones onto the runbook's stages, which schedule them |
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
