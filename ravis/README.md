# RAVIS

A local-first AI routing gateway with an OpenAI-compatible API.

**Build plan:** [`../RAVIS.md`](../RAVIS.md) · **Cross-product contracts:**
[`../ECOSYSTEM_RUNBOOK.md`](../ECOSYSTEM_RUNBOOK.md), which wins wherever the two disagree
about anything crossing a product boundary.

## What exists

**M0 only.** RAVIS does not route anything yet — there is no `/v1` surface, no provider
adapter and no routing engine. What is here is the foundation those need, built first
because the runbook puts the MEP surface at Stage 1, before the gateway:

- **`/ecosystem/*`** — health, identity, capabilities, version and the SSE event stream, so
  peers can negotiate with RAVIS rather than assume things about it. Every capability is
  currently advertised as `unavailable` with the milestone that will change it, because
  RAVIS.md §4.1 forbids advertising an operation that has not passed conformance.
- **Admission control (§4.4)** — body-size cap enforced before authentication, image count,
  per-identity rate limiting, refusal to dereference client-supplied URLs, trusted-proxy
  client address, Origin and Host validation, and a startup refusal for an unsafe bind.
- **Identity resolution (§9.6.0)** — a credential resolves to one application; anything else
  resolves to the least-privileged `anonymous`, which is a real identity rather than a null.
- **`ravis doctor`** — configuration findings and the resolved model-to-provider table,
  contacting nothing, so it works during the incident you are diagnosing.
- **Capability discovery (§7, §9.5)** — the adapter surface M6 will filter against. Its
  defining behaviour is refusing to guess: a generic OpenAI-compatible endpoint publishes
  model IDs and nothing about what they can do, so tool support reads `UNKNOWN` and any
  pool requiring it is unavailable until configuration, probing or SIRVIS says otherwise.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/ravis doctor      # check configuration, print the resolved table
.venv/bin/ravis serve       # http://127.0.0.1:8731
```

Configuration is environment variables with a `RAVIS_` prefix — `RAVIS_PORT`,
`RAVIS_MAX_REQUEST_BYTES`, `RAVIS_CLIENT_CREDENTIAL` and so on; the fields in
[`src/ravis/config.py`](src/ravis/config.py) are the list, each with the reason for its
default.

## Telling RAVIS what the models can do

Nothing probes capabilities yet (§8.7) and SIRVIS evidence is M13, so a generic
OpenAI-compatible endpoint publishes model IDs and **nothing about what they can do**.
Every capability stays `UNKNOWN`, and a pool that requires one fails closed — which is
§5.2 working as written, and which makes `ravis/clarvis-agent` unroutable until somebody
says otherwise.

[`measured-capabilities.json`](measured-capabilities.json) is that somebody, for this
machine. It is derived from `clarvis/docs/benchmarks.md` — executed tool-call trials from
`clarvis-firstrun/tools/suite2.py`, not a vendor flag.

```bash
RAVIS_UPSTREAM_BASE_URL=http://127.0.0.1:1234 RAVIS_MODEL_CAPABILITIES_PATH=measured-capabilities.json .venv/bin/ravis preflight clarvis
```

**Read the flag and you get the wrong answer in both directions.** On this machine three
models advertise no `tool_use` and make well-formed calls in every attempt, while
`granite-4.0-h-tiny` advertises it in both packagings and scores **8/8 as GGUF against
1/8 as MLX** — the runtime's parser discards seven calls' arguments. Build identity is
load-bearing: compare by full `publisher/model`, never the bare name.

**The claims go in at `CONFIGURED` provenance, not `MEASURED`.** That is deliberate and it
is a downgrade: RAVIS did not do the measuring and must not say it did. §13.3 forbids
upgrading provenance, never downgrading it. When SIRVIS evidence lands at M13 it should
*replace* this file rather than sit beside it.

`RAVIS_MODEL_CAPABILITIES` still works and overrides the file per capability, so one model
can be corrected without editing anything. A path that is named and cannot be read is
fatal at startup — the alternative is an agent pool that fails closed for a reason nobody
can see.

## The gates

These are `ECOSYSTEM_RUNBOOK.md` §14, and they are what actually fails a build:

```bash
.venv/bin/ruff check src tests        # lint, imports, naming, complexity <= 8
.venv/bin/mypy                        # strict types
.venv/bin/pytest                      # no test touches a network or a live service
.venv/bin/ravis conformance clarvis   # the §8.9 release gate
```

The conformance suite reads each recorded stream twice — once straight, once
after it has been through the real application — using a faithful port of
Clarvis's own reader, and requires the two readings to be identical. The fixture
is the oracle, so the suite cannot certify RAVIS against RAVIS's own idea of the
protocol. It runs in-process against recorded fixtures, in well under a second.

## Two things worth knowing before editing

**The body-size limiter is a pure ASGI wrapper, not a framework middleware, and that is
deliberate.** Registration order is a convention; nesting is a fact. In the router this
limit was borrowed from, the cap was registered before authentication and its docstring said
so — but the framework builds middleware in reverse, so it ran second, and the test could not
see it. `tests/test_body_limit_ordering.py` asserts the ordering directly.

**Absence is a domain value here.** `anonymous` is an identity, `UNKNOWN` is a provenance,
an empty provider table means "none configured". None of these are defects to be optimised
into something else — but none of them may stand in for *empty* or for *failure* either.
See runbook §14.4.
