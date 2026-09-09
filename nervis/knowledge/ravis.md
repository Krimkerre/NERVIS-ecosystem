# RAVIS — what it does and how it decides

RAVIS is the model gateway. Everything in the ecosystem that wants a model asks
RAVIS, and RAVIS decides which one actually serves the request. It speaks the
OpenAI chat-completions API, so anything already pointed at OpenAI or OpenRouter
can point at RAVIS instead without changing its code.

## How a request is routed

A caller names either a **pool** (`ravis/chat`) or a **model** (`gpt-4o-mini`).
Naming a model is honoured as-is — RAVIS does not second-guess an explicit
choice. Naming a pool is asking RAVIS to choose, and that runs in four stages.

**1. Hard constraints, before anything is scored.** Three independent sets: the
pool's own invariants, the request's requirements (context length, tools,
images), and the calling application's policy. A candidate failing any of them
is gone before ranking. This ordering is why a privacy constraint cannot be
outvoted by a good score — the candidate is never in the running.

**2. Health.** A model or provider whose circuit breaker is open is excluded
with its reason. Model-scoped and provider-scoped are separate, so one dead
model does not take its provider's other models with it.

Counting a failure and blaming one are also separate, and the health screen
shows both. An attempt that dies at the provider — the runtime is not running,
the connection never opens — is counted against the model it was aimed at *and*
against the provider, so a model that has never worked reads as never having
worked; but only the provider's breaker moves, because a model must not be
dropped from routing over its runtime being down. A wrong API key moves neither:
`authentication` is counted on both rows and blamed on neither, since a
credential is configuration somebody can fix in a minute rather than an outage
to route around. Timing is only ever recorded for the target actually
responsible, so a connection that never opened cannot make a model look slow.

**3. Ranking, in this order.** Session affinity first — a conversation stays on
one model for consistency and prompt caching. Then whether the request would pay
a model load, when RAVIS has measured that this caller's sessions are short.
Then reseller preference. Then cost, placement, the pool's declared preference,
and reach. Size is consulted last and only for pools that declared a preference,
because "smaller is cheaper" is a tiebreak, not a ranking.

**4. The attempt chain.** The ranked list is walked in order. If the first
choice fails, the next is tried, and every attempt is recorded with its outcome
and elapsed time.

## What it optimises for

Cost first for a pool that asked to be cheap; latency first for a pool that
asked to be fast. Between two free models the one that costs no network wins.
Hosted models are ranked on published price per million tokens; local models on
size, because size is the only cost a local model has.

**A context window is what the runtime serves, not what the model could hold.**
Ollama publishes the *architecture's* maximum — 128,000 for `qwen2.5vl` — while
loading the model at its own default, and RAVIS believed the larger number until
7 September 2026. A 25,000-token request was handed to a model holding 4,096,
and the runtime evaluated the first 2,050 of them and answered as though it had
read everything. RAVIS now reads `/api/ps`, which reports the window a resident
model actually has, and reports a cold model at the default it will be loaded
with rather than at its maximum. This machine starts Ollama with 32,768, and
RAVIS is told the same number by the launcher so the two cannot disagree. A
document larger than that is now refused with *"context window 32768 < estimated
N tokens needed"* rather than quietly truncated.

**Never a reseller when the maker is reachable.** An aggregator like OpenRouter
sells other companies' models with a margin on top. When the vendor's own
provider is configured and usable, its models rank ahead of the aggregator's
copies of the same models. Measured on this machine: Claude Haiku direct took
0.7-1.0s, the same model through OpenRouter 8.5-11.1s. It is a *preference* and
not a ban — the aggregator's copy stays in the chain as a fallback, because a
vendor catalogue can advertise a model whose endpoint answers 404, and a ban
there would leave no route at all.

## Pools

Every pool is a standing description of what matters, so a caller states intent
rather than picking a model.

- `ravis/auto` — no constraint beyond what the request needs
- `ravis/chat` — conversation; a hosted model first, this machine's underneath
- `ravis/balanced`, `ravis/fast`, `ravis/performance` — the speed/cost/quality axis
- `ravis/cheap` — least money, preferring local
- `ravis/local` — never leaves this machine
- `ravis/api` — cloud only
- `ravis/private` — strictest privacy; cloud excluded
- `ravis/coding`, `ravis/reasoning`, `ravis/long-context` — capability-shaped
- `ravis/agent` — tools required
- `ravis/vision` — image input required; chat's save-time visual check uses it
- `ravis/draw` — models that *emit* an image, which is a different capability
  from reading one and shares no members with the pool above
- `ravis/clarvis-chat`, `ravis/clarvis-agent` — Clarvis's two roles

A pool's constraint holds when things fail, not only when they are chosen. If
the local model behind `ravis/local` is unreachable — or accepts the connection
and never answers — RAVIS refuses the request rather than reaching for a cloud
model that is configured, eligible and answering. Sending the prompt off the
machine is the thing that pool exists to prevent, so it is not available as a
degraded answer.

## Asking for a picture rather than for words

A model that answers with an image is a model answering an ordinary chat
completion — the picture rides beside the text, and OpenAI's wire format has no
field for it. OpenRouter invented one: an `images` array of `image_url` parts on
the message or the delta. RAVIS emits that same shape on both of its paths,
because a picture that arrived one way through the transparent path and another
way through a translated provider would be the gateway creating the difference
it exists to remove.

That mattered immediately. Gemini's native API returns the picture as an
`inlineData` part beside the text, and RAVIS's translation kept only the parts
it recognised: `models/gemini-2.5-flash-image` answered *"Here you go: "* with
200 OK and no image, while the same model through OpenRouter returned a real
PNG. Which route was chosen decided whether the caller got a picture. Fixed
7 September 2026; the same day, `ravis/draw` was measured end to end and
returned a 1024×1024 PNG from `google/gemini-2.5-flash-image`.

Two things about that pool are deliberate. **A router that advertises drawing is
not a drawing model** — `openrouter/auto` publishes `image` among its output
modalities because something behind it can draw, then picks the model itself;
asked for a red circle it chose `z-ai/glm-5.2` and answered in words, so it is
excluded by name. And **the native Google models stay out**, because Google's
catalogue publishes no output modalities at all: `image_out` is UNKNOWN there
and an unknown fails closed. Naming one of those models directly still works,
which is the rule everywhere else too.

Nothing local draws. This machine's runtimes serve no image-generating model,
so `ravis/draw` is hosted-only in practice — and asking a vision model to draw
does not work, because reading an image and emitting one are separate
capabilities that happen to share a word.

The reverse does hold, and it is why one profile serves both directions: every
model in this pool reads images as well as emitting them, so a picture sent
*to* `ravis/draw` is understood and a picture asked *of* it comes back. Measured
in one conversation on that profile — an attached triangle described, a circle
drawn, and then the attached triangle redrawn in a different colour from the
picture itself.

**Session affinity is a preference, not a pin.** A conversation stays on one
model for consistency and prompt caching, and a turn that names a different
profile is routed afresh: the same conversation went to `amazon/nova-2-lite-v1`
for a reading and to `google/gemini-2.5-flash-image` for a drawing.

## What the local models on this machine are actually good for

Measured on 7 September 2026 rather than assumed, because "use local" and "use
the cloud" are usually argued rather than tested. This machine runs
`qwen2.5vl:3b` (vision), `llama3.2:3b` and two embedding models — all small.

**Bounded jobs: local, and it works.** The save-time layout glance on a PDF runs
on `qwen2.5vl:3b` and correctly named a real defect in a deliberately broken
page. Embeddings for chat's own background retrieval run on `nomic-embed-text`.
Both are single-purpose, small-input tasks, and they are free, private and fast
enough.

**Long documents needing synthesis: not local, on this hardware.** Asked for its
thoughts on a 42-page blueprint — about 25,000 tokens plus six rendered pages —
`qwen2.5vl:3b` took four and a half minutes and returned twenty-six characters
of nothing. The same question routed to a hosted model returned four thousand
characters citing the document's actual concurrency model and offline profile. A
3B model given that much dense material spreads its attention too thin; the
context window was not the limit, capability was.

So the honest split is by *size of job*, not by principle: bounded work stays on
this machine, long synthesis goes wherever the request's requirements lead. That
is also the argument for naming a **pool** rather than pinning a model — the
right choice depends on what the request carries, which changes every turn.

## What it will not do

**It does not route around a policy constraint.** A request that policy refuses
gets a structured no-route naming the reason, never a quiet substitution.

**It does not invent capability claims.** A model whose tool support is unknown
is UNKNOWN and fails closed, until an operator configures it or SIRVIS measures
it.

**It does not bill a background call to a frontier model by default.** A caller
can declare a request as background — a conversation title, say — and RAVIS
excludes paid providers unless told otherwise.

**A dead NERVIS never makes RAVIS report itself unready.** This regressed
once for real: a readiness check that read the event publisher's own
dropped-event count made a dead collector look like a RAVIS problem, which
broke the rule that a collector outage leaves every product healthy. The fix
was to stop checking it — a dropped event is logged, not turned into an
opinion about RAVIS's own health — and it is now a route-level regression
test rather than only a code comment: overflow the publisher's buffer against
a collector that refuses every request, and readiness still reads true.

**Events outlive a hub outage rather than being retried into one.** Each
service buffers what it could not publish, up to a fixed number, and drops the
*oldest* when full — the newest events are the ones somebody is looking at — and
counts what it dropped so a gap is visible rather than silent. When the hub
answers again the buffer is sent in order, and each event carries an id derived
from what it describes rather than minted per attempt, so a re-send arrives at
the hub as the same event rather than as a second one.

## What an operator can ask it for

Live: which providers are reachable, which models are routable, what a specific
routing decision chose and what it excluded and why, per-call usage and
estimated cost, sessions and their model affinity, and the pools themselves.

Every routing decision is recorded rather than recomputed, because re-running a
router later uses a different catalogue and can reach a different answer — an
explanation you recompute is a guess about the past.

## Known limits, as of this writing

The management API is **degraded**: reads work, and pool-membership and
provider-configuration writes exist, but authorization on a loopback bind is not
finished. Cost figures are estimates from published prices unless the provider
reported them; a record says which.

**Embeddings are also degraded, and narrowly so on purpose.** `POST
/v1/embeddings` forwards to one configured local runtime — Ollama's
`nomic-embed-text` by default — with no routing between candidates and no
fallback chain, unlike chat. Built for NERVIS chat's own knowledge lookup
rather than a general-purpose embeddings API; a machine with no local
embedding model configured gets a stated refusal, not a guess.

## The free pool

`ravis/free-api` holds models that cost nothing **and** run on somebody else's
hardware. Both halves matter. `ravis/cheap` prefers local, and on a machine with
a runtime "cheapest" means a local model — which is right for cheap and wrong
for background work, because loading a local model is how work nobody is
watching starts competing for memory with the conversation you are having.

It is deliberately not private. A free tier is free because your prompt is worth
something to the provider, so anything sent here is logged and likely trained
on. A request that asked to stay on this machine can never reach it: free
requires remote and local requires local, so the two have no model in common.

Rate limits are normal here rather than a fault — a refusal from one free model
means try the next, which is what the fallback chain already did.

If nothing free is available the pool refuses rather than quietly using a paid
model. That is the difference between a ceiling that is a promise and one that
is a preference: cheap falls back to the cheapest paid model, free does not fall
back at all.
