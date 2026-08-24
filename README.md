# The NERVIS Ecosystem

> **SIRVIS knows. RAVIS chooses. CLARVIS acts. NERVIS connects.**

Named for the control plane rather than for Clarvis, because the set is four
applications and no one of them is the centre — NERVIS is simply the one that has to
know about all of them.

**Building?** Start with [`STATUS.md`](./STATUS.md) — what is finished, what is
next, and how to check both in four commands.

Two things live here, and they are two halves of the same work.

**The specifications** — six documents, one per application plus a conceptual overview
and a cross-product runbook. They are the authority for what each service owes the
others.

**[`nervis/`](./nervis/)** — an interactive, single-file prototype of the NERVIS
dashboard and the three applications it observes. It is the visual scaffolding those
build plans get poured into: every screen reads from a data layer shaped like the real
API responses, and every endpoint it cites is cited from the spec beside it. Open
`nervis/index.html` in a browser; no build step, no server.

It is also the dashboard that will actually be served, so it tracks real endpoints as
they land — while keeping the guarantee that makes it openable at all: a service being
down degrades a panel rather than the page. Three SIRVIS screens read a running service
today and the rest are still mocks; opening the file with nothing running shows the
mocks and says so. See [`nervis/README.md`](./nervis/README.md).

> It used to be `template/`. A **mock-only reference snapshot** now lives outside this
> repository at `~/Documents/coding/nervis-template/`, so that the one-copy rule below
> stays true in here. Don't bring it back in.

They were separate repositories until the obvious problem arrived: the template kept a
*copy* of the specs, and a copy drifts. There is now exactly one of each document, and
the template reads them from one directory up.

| Document | What's in it | Read it when |
|---|---|---|
| **[ECOSYSTEM_OVERVIEW.md](./ECOSYSTEM_OVERVIEW.md)** | What the four applications are and why they are separate — ELI5 through to architecture, with worked examples and the failure-isolation rules. No milestones, no contracts. | You are meeting the ecosystem for the first time, or explaining it to someone else. **Start here.** |
| **[ECOSYSTEM_RUNBOOK.md](./ECOSYSTEM_RUNBOOK.md)** | The only cross-product authority: the non-invention rule, the Minimal Ecosystem Protocol, the build order and its stage gates, test-double rules, end-to-end scenarios, security and degradation matrices, the release sequence, and rollback. | You are about to build anything that crosses a product boundary, or you need to know what happens next. |
| **[CLARVIS.md](./CLARVIS.md)** | Clarvis's *ecosystem* contracts — verified source invariants, RAVIS provider integration, the optional Bridge, code-server compatibility, theming. | You are integrating Clarvis with anything, or checking whether a change would regress a guarantee. |
| **[SIRVIS.md](./SIRVIS.md)** | Full SIRVIS build plan and contracts — runtime adapters, Runtime Sets, benchmark methodology, evidence identity and provenance, recommendations, 23 milestones. | You are building SIRVIS, or you need to know what its evidence actually means. |
| **[RAVIS.md](./RAVIS.md)** | Full RAVIS build plan and contracts — the two execution paths, the Clarvis Compatibility Contract, routing pipeline, sessions, cost, management API, 25 milestones. | You are building RAVIS, or debugging why a stream broke. |
| **[NERVIS.md](./NERVIS.md)** | Full NERVIS build plan and contracts — registry and capability negotiation, dashboard, general chat, event hub, tracing, diagnostics, supervision, the Code tab, 20 milestones. | You are building NERVIS, or deciding what a control plane is allowed to do. |

## Running it

One file per platform, next to this README. Double-click on macOS or Windows, or
run it from a shell:

| | Start | Stop |
|---|---|---|
| macOS | `start-macos.command` | `stop-macos.command` |
| Linux | `./start-linux.sh` | `./stop-linux.sh` |
| Windows | `start-windows.bat` | `stop-windows.bat` |

It brings up SIRVIS on 8721, RAVIS on 8731 and the NERVIS dashboard on 8790,
then opens the dashboard. First run creates the virtual environment and installs
the three packages, which takes a minute; later runs skip straight past that.

**The services are detached.** They keep running when the window closes — which
is the point, and is also why there is a stop launcher rather than a Ctrl-C. All
six launchers are three lines calling `tools/run.py`, which also takes
`status`:

```bash
python3 tools/run.py status
```

**It does not start LM Studio, Ollama or Clarvis.** Those are separate
applications with their own lifecycles, and §9 puts model loading behind
SIRVIS's Resource Manager rather than a launcher. Their state is reported
instead, because "nothing is routing" and "no runtime is running" look identical
from the dashboard and have very different fixes.

Logs are in `.run/`, one file per service.

## How these relate to the products

Two repositories, not four — see `ECOSYSTEM_RUNBOOK.md` §3 for why. **Clarvis is separate**
(TypeScript, VS Code extension host, shipped as a `.vsix`). SIRVIS, RAVIS and NERVIS live here
alongside the protocol package and the template, as separately buildable packages with their own
entry points and their own databases.

Each application stays authoritative for its own internals. Most importantly:

> **`clarvis/plan.md` remains the only normative source for Clarvis's product behaviour.**
> `CLARVIS.md` here adds ecosystem contracts on top of it and never overrides it.

Where a document here and the runbook disagree about a **cross-product** contract, the runbook
wins. Where they disagree about an application's **internals**, the application document wins.

## Acknowledgements

Several operational contracts in these documents were identified against
[Alexander Keisse](https://github.com/alexander-keisse)'s `ai-router`, an MIT-licensed local
LLM router, used here with his permission:

| Contract | Taken from |
|---|---|
| RAVIS §4.4 — inbound limits and admission control | its enforced request boundaries |
| RAVIS §9.6.1 — background and utility calls | its handling of a chat client's hidden calls |
| NERVIS §12 — closed control surface | its action registry and per-family switches |
| RUNBOOK §9 — evidence is not intent; the owner enforces permission | its action-policy layer |

Where its code is lifted rather than its lessons, the MIT copyright notice travels with the
file.

## The rule that governs every one of them

> **No agent may invent another ecosystem component's API, schema, capability or behaviour
> merely to complete its own milestone. If the required contract does not yet exist, implement
> against the canonical ecosystem contract where specified, use an explicitly labelled test
> double where appropriate, or stop at the integration gate and report the missing dependency.**

## Current state

**Clarvis** (`../clarvis`) and **RAVIS** (`ravis/`) exist as code; SIRVIS and NERVIS are still
specifications. [`STATUS.md`](./STATUS.md) is the file that says how far RAVIS has got and how
to check it in four commands — this section deliberately says no more than that, because two
places tracking the same number is how one of them starts lying.

The single most useful verified fact in this set, because it determines the build order:

> The initial Clarvis ↔ RAVIS integration requires **zero Clarvis code changes**. Its existing
> Custom (OpenAI-compatible) provider, its separate chat/agent model settings, and its real
> tool-support probe are already sufficient. Verified against source — see `CLARVIS.md` §3.

## What changed in this consolidation

These six documents replace thirteen source drafts (four build plans, four addenda, four
"Revised" addenda, one overview). Beyond merging:

- **Build order reconciled.** The two source runbooks disagreed, and the revised one contradicted
  itself — its §7 built the RAVIS gateway first while its §19 released SIRVIS first. Resolved in
  favour of retiring the riskiest unknown first: proxy wire-compatibility. Rationale in
  `ECOSYSTEM_RUNBOOK.md` §6.1.
- **Contracts recovered.** The "Revised" addenda were thinner than the originals and silently
  dropped canonical endpoint paths, the MEP mechanics and the non-invention rule. The originals
  are the spine; the revisions' sharper framing is folded in.
- **Claims verified.** Every assertion about Clarvis was re-checked against source. One was
  wrong: M13 log tailing is **built**, not planned.
- **Dead citations removed.** All `fileciteturn…` markers are gone — they pointed at nothing and
  would have read as resolvable evidence to a coding agent.
- **Conflicts named, not smoothed over.** Each application document ends with a *Conflicts
  resolved* table recording what disagreed and which way it was decided.
