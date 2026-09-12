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

### What the health screen counts, and what it blames

Counting a failure and blaming one are separate, and `/api/v1/health` shows
both. An attempt that dies at the provider — the runtime is not running, the
connection never opens — is counted against the model it was aimed at *and*
against the provider, so a model that has never worked reads as never having
worked rather than as error-free. Only the provider's circuit breaker moves,
because a model must not be dropped from routing over its runtime being down.

A wrong API key moves neither breaker: `authentication` is counted on both rows
and blamed on neither, since a credential is configuration somebody can fix in a
minute rather than an outage to route around.

Timing is recorded only for the target actually responsible, so a connection
that never opened cannot make a model look slow.

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

**But only when the maker's own copy can actually serve the request.** Until
11 September 2026 the aggregator's copy was pushed to the back even when the
vendor's own copy had just been refused. Anthropic's model catalogue says nothing
about tool support, so Clarvis's agent pool, which requires tools, refused every
Claude model bought directly from Anthropic — and still ranked OpenRouter's
Claude, which can call tools, behind everything nobody resells. Every Clarvis
build from 5 to 11 September ran on Qwen3 Coder 30B, although that pool prefers
Claude Sonnet first. Now a resold copy only drops behind its maker when one of
the maker's own copies passed the same checks. On the same day the operator
declared Claude Sonnet 5 tool-capable, in the operator-capabilities file the
launcher gives RAVIS, so Clarvis's builds go to Sonnet 5 bought directly from
Anthropic. The next morning Claude Haiku 4.5 was declared the same way, so
Clarvis's chat, which also sends tools, goes to Haiku bought directly rather
than to Gemini 2.5 Flash Lite. Both declarations are the operator's word,
recorded at configured provenance, not measurements.

**A slow chatbot is a bad chatbot, so chat now weighs speed — last.** Until
9 September 2026 the conversational pool ignored measured response times
entirely: `ravis/fast` ranked on latency, `ravis/chat` did not, so two models
the pool liked equally were separated by nothing more meaningful than
alphabetical order, and the slower one won about half the time. Chat now adds a
speed term to its ranking, placed *after* everything that says what the pool is
for. Ordering matters more than the term itself: a model the pool was written
around still beats a faster stranger, so chat cannot degenerate into "whichever
tiny model replies quickest". It only decides between candidates the pool
already considers equals.

Speed is read in 750-millisecond buckets rather than as a raw number, because a
difference nobody can feel should not reorder anything — 620ms and 700ms are one
bucket and rank identically, while 600ms and 2,400ms do not. A model nothing has
measured yet is not punished for it; it sorts as though average, so an unmeasured
model still gets its turn and can earn a real timing. This is why the pool
carries `speed_tiebreak_ms` rather than the `prefer_fast` flag `ravis/fast` uses:
the same measurement, read as a tiebreak instead of as the purpose.

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


### What happens to events while the hub is unreachable

Events outlive a hub outage rather than being retried into one. Each service
buffers what it could not publish, up to a fixed number, and drops the *oldest*
when that fills — the newest events are the ones somebody is looking at — and
counts what it dropped, so a gap in the timeline is visible rather than silent.

When the hub answers again the buffer is sent in order, and each event carries
an id derived from what it describes rather than minted per attempt, so a
re-send arrives as the same event rather than as a second one.

## What an operator can ask it for

Live: which providers are reachable, which models are routable, what a specific
routing decision chose and what it excluded and why, per-call usage and
estimated cost, sessions and their model affinity, and the pools themselves.

Every routing decision is recorded rather than recomputed, because re-running a
router later uses a different catalogue and can reach a different answer — an
explanation you recompute is a guess about the past.

## Where provider credentials live

Keys go to the **platform keyring** — macOS Keychain, or the Secret Service on
Linux — written through the tool the platform already ships, with the secret on
standard input so it never appears in the process list. Every write is read back
and compared before it is trusted, because macOS's interactive parser unquotes
what it reads and will drop a backslash; a mismatch falls through to the file
rather than storing a mangled key that would fail later as an authentication
error.

Only the *name* is kept beside the credential file, so RAVIS can still list what
it holds without asking the keyring to be searched — client and admin identities
are matched by walking stored names, and a name that vanished with its value is
an identity nobody can authenticate with.

Where there is no keyring — Windows, a headless server, or
`RAVIS_CREDENTIAL_KEYRING=0` — a `0600` file in RAVIS's config directory holds
them, which is plaintext and is said so rather than promised away. That switch
means *leave this machine's keyring alone entirely*: not read, not written, not
deleted from. Reads are file, then keyring, then environment.

Providers with a credential row: Anthropic, DeepSeek, Google AI Studio, OpenAI,
OpenRouter and xAI. The last two of those were added on 9 September 2026 and
need nothing but an address, because both speak the OpenAI protocol.

## Trying a model on purpose, so it can be measured at all

Two switches under **Model** in chat's settings, both off unless switched on.
The first, *"occasionally try a different model"*, spends about one turn in
twelve on a model that was **not** the best pick. The second, nested under it,
aims those turns at models nothing has timed at all.

**Why they exist.** A model that is never chosen is never measured, and a model
that is never measured is never chosen — a loop that closes on itself. It got
teeth once chat started ranking on speed: an untimed model sorts as merely
average, which is enough to keep it out of first place indefinitely. And it
cannot be solved by benchmarking, because SIRVIS drives local runtimes only —
load time, memory pressure and thermal readings do not exist for an API — so a
hosted model is measured by being used, or not at all.

**What it costs, stated plainly.** A worse answer some of the time. That is the
entire trade, and the reason nothing switches this on by inference. When it does
fire, the route explanation says so in as many words — *"trying X on purpose"* —
rather than describing the choice as though the model had won on merit.

**Where the randomness lives, which is not where it looks.** The routing engine
is a pure function of its arguments and a gate enforces that: identical inputs
must produce an identical decision. So the dice are thrown at the edge, in the
API layer, and only the result is handed to the router. Same throw, same route;
still random across requests.

The model that would ordinarily have won stays first in the fallback list, which
matters more here than usual — an untried model is exactly the one most likely
to fail.

**An exploratory pick that refuses a setting no longer costs the reply.** On 11 September 2026 exploration picked `gpt-5.6-sol`, OpenAI refused the request because that model only accepts `max_completion_tokens`, and chat showed "the model returned an empty message". RAVIS now sends OpenAI the name it wants, treats "this model does not support that parameter" as a reason to try the next model rather than a bad request, and reports a failed stream as the error it was — so the usual pick answers instead, and a real refusal reads as one.

## Where the prices come from, and why some are approximate

RAVIS ships **no built-in price list**. Every hosted rate is written down by the
operator in `~/.config/ravis/prices.json`, keyed by the vendor's own model id,
in dollars per million tokens. This is deliberate: a rate baked into the source
goes stale silently and nobody can say when it was true, whereas a file someone
wrote has both an author and a date. Of the providers configured here only
OpenRouter publishes per-token figures in its catalogue; OpenAI, Anthropic,
Google, DeepSeek and xAI publish catalogues with no pricing in them at all, so
without that file their calls cost `UNKNOWN` — which a budget reads as *nothing
has been spent*.

**Those rates are also what routing ranks on, since 12 September 2026.** A
hosted model whose provider publishes no price used to reach ranking unpriced,
so two direct builds of one family tied and the tie went to alphabetical order —
the oldest build first. It now ranks on the price in that file, and a dated
build name such as claude-haiku-4-5-20251001 finds the price written as
claude-haiku-4-5, which also stopped those calls costing UNKNOWN. A price the
provider does publish is never replaced.

DeepSeek and xAI rates were added on 9 September 2026 from each vendor's own
documentation. Both vendors bill **two tiers**, and the file holds one rate per
model, so two deliberate choices were made:

- **DeepSeek charges double during peak hours** (01:00–04:00 and 06:00–10:00
  UTC, Monday to Friday; everything else, weekends included, is half price). The
  file states the *peak* rate, so an off-peak conversation is reported as
  costing about twice what it did. Overstating is the safe direction — a budget
  that under-reports is worse than one that flatters itself.
- **xAI charges double once a single prompt reaches 200,000 tokens** — the whole
  request, not just the excess. The file states the under-200K rate, because
  ordinary conversation is nowhere near that and doubling every message would
  make the running total meaningless. A genuinely enormous prompt is therefore
  reported at about half what it cost.

Neither distortion touches routing decisions between vendors, since both models'
rates move together. It only affects the figure on the spend screen.

**A call whose token counts went missing was priced as nothing.** RAVIS reads
the token counts off the stream as it passes. It used to look at each HTTP chunk
on its own — but HTTP chunking has nothing to do with the frames a provider
sends, so a chunk boundary landing inside the counts made both halves
unrecognisable. The call was then recorded with no usage at all, which is priced
`UNKNOWN`, which a budget reads as *nothing spent*. Nothing announced it; the
only symptom was RAVIS's spend sitting below the provider's own figures. Since
10 September 2026 the reader keeps the tail of an unfinished frame and joins it
to the next chunk. The bytes sent to the client are untouched either way.

**The spend screen used to forget everything when RAVIS restarted.** Usage
records were kept only in memory, so every restart emptied the list, the
day's total and the monthly budget along with them — a restart was enough to
make a month's spending read as none. Since 12 September 2026 RAVIS writes each
record to its own database and reads the recent ones back when it starts. A
record holds no prompt and no reply, only which model, which app, the token
counts and the estimated cost. Records older than ninety days are dropped; the
budget only looks back thirty.

**Spending can be looked up by day, and the spend tile can be reset.** Since 12
September 2026 the Spending page lists each calendar day RAVIS served a call,
newest first: the estimated total, how many calls were priced, the tokens, and
a breakdown by model and by app. Only days with calls appear, so a missing day
before 12 September is not proof that nothing was spent. The Reset to 0 button
on the dashboard's spend tile makes that tile count from the moment it was
pressed; no record is deleted, and the monthly budget keeps counting as before.

**How often any of this updates, which was worse than it looked.** Until
9 September 2026 the answer was *never*: prices were read from the file once,
at startup, and OpenRouter's published rates — the only machine-readable
pricing any configured provider ships — were parsed on every catalogue refresh
and then thrown away, because the method that accepts a catalogue price had no
callers at all. The source comment claimed otherwise, which is how it went
unnoticed. Every figure on the spend screen was as old as the process.

Now, on the same refresh that already fetches catalogues:

- `prices.json` is **re-read from disk**, so editing a rate takes effect on the
  next refresh instead of at the next restart, and a rate deleted from the file
  stops being charged rather than lingering until a reboot.
- **OpenRouter's own published rates are taken automatically**, so anything
  routed through it stays current with no help. An operator-written rate always
  wins over a catalogue one — a catalogue price is what a vendor charges
  anybody, and what the operator wrote down is what *they* pay.

The hand-written rates still have to be maintained by hand. OpenAI, Anthropic,
Google, DeepSeek and xAI publish no machine-readable prices anywhere, so there
is nothing to fetch; RAVIS will not scrape a marketing page and call the result
a fact. Those numbers are only as current as the last time somebody checked.

## Which provider a model belongs to

One answer, used by policy, execution, provider health and session attribution.
It has to be one answer: they described different requests when it was two.
Until 9 September 2026 a translated provider's model resolved to `anthropic`
when the request named it in full (`ravis/anthropic/<model>`) and to `default`
when it named the model plainly — so an operator's deny-list naming `anthropic`
was compared against `default`, matched nothing, and the request reached
Anthropic anyway. A deny-list a client evades by dropping four characters is not
a deny-list.

Provider allow-lists, deny-lists and trusted-provider rules come from the policy
configured for the calling application, never from the request. A request may
tighten privacy and nothing else, so no client can grant itself a provider by
asking.

## How a model gets measured, and which ones never do

RAVIS times every request it routes and keeps a median per model, but only ranks
on the ones with enough samples to mean it — a model with two samples is treated
as unmeasured rather than as slow. An unmeasured model sorts in the *middle*
rather than last, deliberately: sorting it last would close the loop of never
chosen, never measured, never chosen.

**That guard is weaker for hosted models than for local ones.** A local model
nobody has used can be measured on purpose — SIRVIS loads it and benchmarks it.
A hosted one cannot: SIRVIS drives local runtimes only, and load time, memory
pressure and thermal readings do not exist for an API. So a hosted model is
measured only by being used, and in a pool that ranks on speed — `ravis/fast`,
`ravis/balanced` — anything already measured under a second beats the neutral
placeholder, so an untried hosted model can stay untried.

Known and written down rather than fixed automatically: routing to unproven
models to collect timings spends real money, which is a decision rather than a
default.

**Whether a hosted model can call tools is tried on purpose, since 12 September
2026.** Anthropic, OpenAI and Google publish no tool support in their
catalogues, so their models bought directly sat at unknown and every pool that
requires tools refused them — Clarvis's builds went to a smaller model until
the operator declared Claude Sonnet 5 and Haiku by hand. OpenRouter leaves a
model at unknown too when its list of accepted settings does not mention tools.
RAVIS now sends such a model one small request carrying one tool it must call,
or merely offers the tool when the provider will not let it insist.
A call counts as tool support, a provider refusing tools counts as none, a
passing hiccup — a timeout, a rate limit — counts as nothing and is tried again
hours later, and a refusal for some other reason, such as a model that does not
chat at all, counts as nothing for a month. The makers' own copies are tried
before an aggregator's listings. It is bounded: hosted models only, never a
local one, only models a tool-requiring pool would otherwise admit, three per
pass and forty a day, each result kept a month, and every trial a provider
answered on the spend screen. The result ranks above a
catalogue's word and below SIRVIS's measurements and the operator's own
declarations. The capability_trials setting switches it off.

## Known limits, as of this writing

The management API is **degraded**: reads work, and pool-membership and
provider-configuration writes exist, but authorization on a loopback bind is not
finished. Cost figures are estimates from published prices unless the provider
reported them; a record says which.

**RAVIS adds more delay than its target, measured on 12 September 2026.** A load
test found that with one caller RAVIS adds about 16 ms to each request, against a
target of 5 ms, and that it stops keeping up at roughly 100 to 180 requests a
second. Three causes were measured. One is fixed: a caller that presents a key
used to wait about 46 ms while RAVIS checked each stored client and admin key in
the keychain, holding up everyone else's requests meanwhile; since RAVIS 0.23.1 it
remembers those keys and renews them in the background, so a request with a key is
as fast as one without. Two are not fixed yet: it runs a system command to read
free memory on every request, and it asks a hosted provider for its model list
again on every request whenever that provider's last answer failed (a missing or
refused key, an outage, no network). It stays correct under load: 5,180
requests at up to 200 at once all got their own answers.

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
