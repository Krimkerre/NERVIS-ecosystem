# RAVIS

A local-first AI routing gateway with an OpenAI-compatible API.

**Build plan:** [`../RAVIS.md`](../RAVIS.md) · **Cross-product contracts:**
[`../ECOSYSTEM_RUNBOOK.md`](../ECOSYSTEM_RUNBOOK.md), which wins wherever the two disagree
about anything crossing a product boundary.

## What exists

**STATUS.md is the file that tracks this**, and it is checked by CI against the repository.
This section says only what a newcomer needs before reading it: RAVIS routes, and both of
§6's execution paths work.

- **`/v1/models` and `/v1/chat/completions`** — the OpenAI-compatible surface, streaming and
  cancellation included. `ravis conformance clarvis` runs sixteen wire-level checks and is
  the §8.9 release gate.
- **Routing (§5, §9)** — 13 virtual pools, hard-constraint filtering before scoring, and a
  route explanation on every decision naming what was considered and why each candidate was
  excluded.
- **Providers** — a translated adapter for Anthropic (§6 Path B), transparent adapters for
  LM Studio, Ollama and any generic OpenAI-compatible endpoint, and more than one upstream
  at a time addressed as `ravis/<name>/<model>`.
- **Evidence (§13)** — SIRVIS measurements consumed and applied at `MEASURED` provenance,
  which outranks a catalogue's advertisement and has already excluded a build the catalogue
  claimed was tool-capable.
- **Credentials (§4.5, M10)** — a `Secret` type that will not render itself, an OS-agnostic
  `0600` credential file, and provider enable/disable that actually stops routing.
- **Admission control (§4.4)**, **identity resolution (§9.6.0)** and the **`/ecosystem/*`**
  MEP surface, all from M0 and unchanged.

Capabilities are advertised per §4.1's table, which sets a condition **per capability**
rather than one bar for all of them. What is still `unavailable` says which milestone
changes it.

## Running it

```bash
python3 -m venv .venv && .venv/bin/pip install -e ../protocol -e ".[dev]"
.venv/bin/ravis doctor            # configuration findings, contacting nothing
.venv/bin/ravis serve             # http://127.0.0.1:8731
.venv/bin/ravis conformance clarvis
```

`ecosystem-protocol` is a **local path dependency and must be installed first** — it is not
on PyPI, and `ravis/pyproject.toml` does not declare it, so `pip install -e ".[dev]"` alone
produces a working install that fails at import. The whole ecosystem starts together with
the launcher in the repository root, which does this for you.

Configuration is environment variables with a `RAVIS_` prefix — `RAVIS_PORT`,
`RAVIS_UPSTREAM_BASE_URL`, `RAVIS_UPSTREAMS`, `RAVIS_SIRVIS_BASE_URL`,
`RAVIS_CLIENT_CREDENTIAL` and so on; the fields in
[`src/ravis/config.py`](src/ravis/config.py) are the list, each with the reason for its
default.

## Telling RAVIS what the models can do

Nothing probes capabilities yet (§8.7), but two other sources now answer. A **vendor
adapter** reads what the runtime publishes — LM Studio's catalogue turns most of a local
install from `UNKNOWN` into `ADVERTISED` tool support — and **SIRVIS evidence** (M13)
arrives at `MEASURED`, which outranks it.

That ordering is not decorative. LM Studio advertises `tool_use` for both packagings of
`granite-4.0-h-tiny`; SIRVIS measured the GGUF build passing 24 of 24 tool trials and the
MLX build passing 3. The catalogue is wrong about one of them, and `MEASURED` beating
`ADVERTISED` is what keeps it out of `ravis/clarvis-agent`.

A generic endpoint that publishes nothing still reads `UNKNOWN`, and a pool requiring that
capability still fails closed — §5.2 working as written.

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
