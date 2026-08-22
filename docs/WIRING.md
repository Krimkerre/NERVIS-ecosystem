# Wiring this to real services

The template is built so this is mechanical. Nothing about the render path changes.

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

`BASE` holds the four service origins in one place.

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

## Order to do it in

The dependency order from the runbook, which is also the useful order here:

1. **`/ecosystem/*` on each service** — health, identity, capabilities, version.
   Drives `SERVICES`, and therefore every absence path already on screen.
2. **RAVIS** `/api/v1/route-decisions`, `/profiles`, `/usage`, `/providers`.
   The Dashboard, Chat and Routes screens light up together.
3. **SIRVIS** `/api/v1/models`, `/benchmark-results`, `/runtime-sets`.
   Evidence provenance is already rendered; it just needs real records.
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
an audit event — see `docs/spec/RAVIS.md` §15.1 and `docs/spec/SIRVIS.md` §15.2.
