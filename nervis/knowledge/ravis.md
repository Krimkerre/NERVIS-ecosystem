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
- `ravis/vision` — image input required, ahead of the feature that needs it
- `ravis/clarvis-chat`, `ravis/clarvis-agent` — Clarvis's two roles

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
