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

## The gates

These are `ECOSYSTEM_RUNBOOK.md` §14, and they are what actually fails a build:

```bash
.venv/bin/ruff check src tests    # lint, imports, naming, complexity <= 8
.venv/bin/mypy                    # strict types
.venv/bin/pytest                  # no test touches a network or a live service
```

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
