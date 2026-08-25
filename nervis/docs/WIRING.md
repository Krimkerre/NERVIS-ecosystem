# Wiring this to real services

This page is built so this is mechanical. Nothing about the render path changes.

**Most screens have been through it**, which turned the claims below from theory
into experience — see *What the first wirings actually cost* at the end. The
current live/invented split lives in `STATUS.md` and is checked against the
repository; it is not duplicated here, because the copy that used to say "three
screens" stayed at three while the real number passed twenty.

## The swap

Every screen reads from `API`, which has one method per real endpoint:

```js
async models(){return{snapshot_revision:412,items:[ /* … */ ]}}
```

Replace the body, keep the shape:

```js
async models(){return (await fetch(BASE.sirvis + '/api/v1/models')).json()}
```

That is the whole change. The views are already `async` and already `await`, so a
method that starts doing I/O behaves exactly as the mock did.

`BASE` holds the four service origins in one place. Their default ports are assigned in
`../../ECOSYSTEM_RUNBOOK.md` §5 — this page hardcodes them because it runs from disk
with no configuration, but a real client reads them from configuration or the registry.

## LM Studio is already wired, in the only sense that matters

`API.sirvis.models()` and `API.sirvis.catalog()` do not return handwritten records.
They return `lmstudioBuilds()`, which maps the verbatim body of

```text
GET http://127.0.0.1:1234/api/v0/models
```

onto SIRVIS's build identity — family, variant, runtime, runtime config, plus
`tool_use` and vision from the capability array. The payload is embedded as
`LMSTUDIO_SNAPSHOT` because this file opens from disk with no server, and a
runtime fetch would make it fail to render whenever LM Studio is down.

Going live is therefore one line:

```js
const payload = await (await fetch('http://127.0.0.1:1234/api/v0/models')).json()
return {snapshot_revision: 412, items: lmstudioBuilds(payload)}
```

The mapper stays. It has already been exercised against a genuine response, which
is the difference between a mock that is shaped like the API and a mock that is
the API's actual output.

Two things the models endpoint does not carry, so they come from elsewhere:
`LMSTUDIO_SIZES` and `LMSTUDIO_PARAMS` hold what `lms ls` reports. `SCENARIO_LOADED`
stages two resident builds so routing has something to route to; delete it and
`state` comes straight from LM Studio's own field.

## The evidence has a source file

`API.sirvis.evidence()`, `runs()`, `runtimeSet()` and `recommendations()` are built
from `BENCHMARK` and `BENCHMARK_PAIRS`, which are a transcription of
`../clarvis/docs/benchmarks.md` — a real suite run on this machine. When a live
SIRVIS exists it overwrites all four from `/api/v1/benchmark-results`,
`/benchmark-runs`, `/runtime-sets` and `/recommendations`; nothing about the views
changes, because they already read measures rather than a score.

**There is no `score` field, and that is deliberate.** Two of the four accuracy
measures returned identical values for every model in the run. A composite that
averages a measure with no variance in it invents a difference the measurements did
not find, so SIRVIS publishes what varied — rate, first token, tool reliability,
follow-up classification — and RAVIS computes its ranking from those. Keep that
split when you wire it: it is what lets a bad route be diagnosed as either a bad
measurement or a bad weighting instead of one unarguable number.

**Measurement outranks metadata, in both directions.** `toolsWork(build)` prefers
measured tool-call reliability over LM Studio's `tool_use` flag, because on this
machine they disagree about four builds each way — three advertise nothing and work,
one advertises support and loses seven calls in eight to the MLX runtime's parser.
`API.ravis.pools()` filters on `toolsWork`, not on the flag. If you wire real
capability negotiation, keep the two fields separate rather than reconciling them at
the source: the disagreement is data.

## Providers carry their own wiring

`API.ravis.providers()` returns, per provider, what an implementation needs to
place a call:

| Field | Example |
|---|---|
| `base_url` | `https://api.anthropic.com/v1` |
| `chat_path` | `/messages` |
| `models_path` | `/models` |
| `protocol` | `TRANSLATED` or `OPENAI_TRANSPARENT` |
| `auth.scheme` / `auth.header` | `api-key` / `x-api-key` |
| `auth.secret_ref` | `keychain:ravis/anthropic` |

Six are defined: LM Studio and Ollama locally, then Anthropic, OpenAI, Google
Gemini and OpenRouter. The origins, paths, protocol modes and auth schemes are
real; the health numbers and credential presence for the cloud four are staged.

`secret_ref` is a reference and never a value. RAVIS resolves it in-process at call
time — the key does not enter the browser, an event, a log or a diagnostic packet,
which is exactly what makes it safe to render the reference on a dashboard.

`protocol` is not cosmetic: it selects the execution path. Transparent providers
are proxied essentially unchanged, which is why Clarvis's OpenAI client works
against them at all. Translated providers have their request and their streamed
response rewritten, and every translation is somewhere tool-call framing can be
lost — which is what the conformance suite exists to catch.

## Derived, not restated — worked example

`API.ravis.pools()` does not state member counts. It filters the real build list by
each pool's own invariant:

```js
const fits = r => local.filter(b =>
  (r.tools !== 'REQUIRED' || toolsWork(b)) &&
  (!r.minimum_context || b.runtime_config.max_context_length >= r.minimum_context) &&
  (!r.vision || b.vision)).length
```

So `ravis/clarvis-agent` has ten members on this machine — not because anyone typed a
ten, but because ten of eleven installed builds satisfy the invariant at 32K or better.
Install another tool-capable build and the number moves on its own. This is the pattern
to copy when a figure could be computed from data already on the page.

**Filter on `toolsWork(b)`, never on a raw field.** An earlier version of this passage
filtered on `b.tools`, which stopped existing when the measured record arrived — the
build list emits `tools_advertised`. Copied as it stood, that predicate matched nothing
and emptied the pool silently, which is exactly the failure this file records at the
`toolsWork` definition. `toolsWork` is the point: it prefers measured tool-call
reliability and falls back to the advertised flag only where no measurement exists,
which is why the count is ten rather than the eight that advertise.

## Four rules that keep it mechanical

**Contract field names in `API`, display names in views.** The data layer uses
`evidence_type`, `runtime_config.context_length`, `execution_path`,
`snapshot_revision`. If you rename to something friendlier here, every future swap
becomes a rewrite.

**Derive, never restate.** Concurrent degradation is computed from `alone_tok_s` and
`concurrent_tok_s`; the constraint count is `eligible.length + excluded.length →
eligible.length`. A hardcoded caption goes stale against live data without anyone
noticing.

**Cite the endpoint.** Every method carries the path it will call. A method with no
citation is a claim about an API that may not exist — which the ecosystem's governing
rule forbids:

> No agent may invent another component's API, schema, capability or behaviour merely
> to complete its own milestone.

**Absence is a state, not an error.** `SERVICES[key].state` drives every tile through
`usable()` and `cell()`. When you wire the registry up, set those states from real
health and capability negotiation — and keep them observer-side. A successful TCP
connect is not readiness.

## What the first wirings actually cost

Three things the mechanical description above does not prepare you for.

**"Replace the body, keep the shape" holds only while the shape was a good
guess.** The mock's `evidence()` predated the engine that now fills it, so the
live envelope disagreed: `target.model_family` against `target.family`, a
`metrics` object per measure against a bare number, `validity_notes` against
`warnings`. Renaming to the contract names would have touched six functions and
a hundred call sites across screens that have nothing to do with this one. What
worked was a **mapper** — `sirvisEvidence()`, the same pattern `lmstudioBuilds()`
already uses for LM Studio's payload. Keep the mapper; do not rewrite the views
around a second vocabulary.

**Live data has a different cardinality than a mock.** The mock had one record
per build; the endpoint returns one per *run*, and today's database holds every
sweep — so the table showed the same build eight times with different numbers.
The fix was already in the contract: §12.2 keys evidence on machine, build,
runtime configuration, role and suite, so two runs of one suite against one
build share an evidence ID. One row per identity, newest first. Where a build
legitimately appears twice, print the field that differs — otherwise two
identical-looking rows disagreeing reads as a bug.

**Fields the live data does not have must render as absent, not as zero.**
`tool_calls`, `code`, `fib` and `follow_up` come from capability suites SIRVIS
cannot run yet, so a live record carries no such fields. `0 / 0` would read as
"measured and found nothing"; the cells show a dash and name the milestone.
Filling them from the transcription instead would put measured and transcribed
numbers in one row with nothing to tell them apart.

And one prerequisite that is not optional: **a read endpoint without CORS is
unreachable from this page.** The SIRVIS surface was built and served and the
browser discarded every response until `Access-Control-Allow-Origin` existed.
`curl` will not tell you — see `PITFALLS.md` §6.

## Order to do it in

The dependency order from the runbook, which is also the useful order here:

1. **`/ecosystem/*` on each service** — health, identity, capabilities, version.
   Drives `SERVICES`, and therefore every absence path already on screen.
2. **RAVIS** `/api/v1/route-decisions`, `/profiles`, `/usage`, `/providers`.
   The Dashboard, Chat and Routes screens light up together.
3. **SIRVIS** `/api/v1/models`, `/benchmark-results`, `/runtime-sets`.
   Evidence provenance is already rendered; it just needs real records.
   `/benchmark-runs` and `/benchmark-results` are **done**. `/models` exists and
   is not yet wired. `/runtime-sets` does not exist and **must not be faked**:
   Runtime sets reads measured *pairs*, and synthesising them from single-model
   runs asserts exactly what `../../SIRVIS.md` §10.1 exists to deny. It waits
   for M9 + M10.
4. **`/ecosystem/events` (SSE)** — replace `API.nervis.events()` with an
   `EventSource`. The stream-health tiles (buffered, dropped, quarantined, gaps) are
   already wired to report it.
5. **Clarvis Bridge** last, per the runbook. `API.clarvis.status()` is read-only
   status; there is deliberately no transcript endpoint, because the Bridge must
   never carry prompts or model output.

## What is deliberately not an endpoint

`EDITOR_PREVIEW` in the Clarvis view holds the code, the transcript and the choice
buttons. That content is rendered by **code-server and the Clarvis extension inside
the frame** — NERVIS never receives it. When the Code tab becomes a real iframe,
delete that constant outright; do not port it, and do not add an endpoint for it.

## Actions

`DEMO_API` covers the POSTs (run a benchmark, load a model, simulate a route,
analyze a trace). Same rule: one method per real endpoint, replace the body, keep the
signature. Mutations in the real build need `Idempotency-Key`, an actor identity and
an audit event — see `../RAVIS.md` §15.1 and `../SIRVIS.md` §15.2.
