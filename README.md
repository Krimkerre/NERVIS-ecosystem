# The NERVIS Ecosystem

> **SIRVIS knows. RAVIS chooses. CLARVIS acts. NERVIS connects.**

Named for the control plane rather than for Clarvis, because the set is four
applications and no one of them is the centre — NERVIS is simply the one that has to
know about all of them.

Two things live here, and they are two halves of the same work.

**The specifications** — six documents, one per application plus a conceptual overview
and a cross-product runbook. They are the authority for what each service owes the
others.

**[`template/`](./template/)** — an interactive, single-file prototype of the NERVIS
dashboard and the three applications it observes. It is the visual scaffolding those
build plans get poured into: every screen reads from a data layer shaped like the real
API responses, and every endpoint it cites is cited from the spec beside it. Open
`template/index.html` in a browser; no build step, no server.

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

## How these relate to the product repositories

Each application's own repository stays authoritative for its internals. Most importantly:

> **`clarvis/plan.md` remains the only normative source for Clarvis's product behaviour.**
> `CLARVIS.md` here adds ecosystem contracts on top of it and never overrides it.

Where a document here and the runbook disagree about a **cross-product** contract, the runbook
wins. Where they disagree about an application's **internals**, the application document wins.

## The rule that governs every one of them

> **No agent may invent another ecosystem component's API, schema, capability or behaviour
> merely to complete its own milestone. If the required contract does not yet exist, implement
> against the canonical ecosystem contract where specified, use an explicitly labelled test
> double where appropriate, or stop at the integration gate and report the missing dependency.**

## Current state

Only **Clarvis exists as code** (`../clarvis`). SIRVIS, RAVIS and NERVIS are specifications.

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
