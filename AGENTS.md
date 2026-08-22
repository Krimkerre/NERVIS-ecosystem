# Agent instructions for this repository

*Tool-agnostic: this applies to Claude Code, OpenCode, Codex or anything else.*

## What is here

**The `.md` files in this directory are the specifications**, and they are the
authority for what each service owes the others. `ECOSYSTEM_RUNBOOK.md` wins on any
cross-product contract; an application's own document wins on its internals.

**`template/` is the prototype** — the NERVIS dashboard and the three applications it
observes, as one openable HTML file. It has its own working rules in
[`template/AGENTS.md`](./template/AGENTS.md), and you should read those before editing
anything in there. It also has [`template/docs/PITFALLS.md`](./template/docs/PITFALLS.md),
which is every defect that repository has produced and the rule that prevents each one.
Almost nothing in the template fails loudly, so read it before your first edit.

Superseded copies are **not kept in the working tree** — git history is the archive. A second
copy of a document is the one failure this layout exists to prevent, and a folder blessing the
practice invited it.

## The rule that governs everything

> **No agent may invent another ecosystem component's API, schema, capability or
> behaviour merely to complete its own milestone. If the required contract does not
> exist, implement against the canonical contract, use a labelled test double, or
> stop and report the missing dependency.**

In the template that means: do not add a UI control without an endpoint that backs it.
A screen implying an API nobody has agreed to is worse than a missing screen, because
it looks finished.

## Writing code here

**`ECOSYSTEM_RUNBOOK.md` §14 is the engineering standard** — complexity ceiling of 8, naming,
comments, error handling, tests, and the lint/type/test gates that enforce them. It is stated
once there and is not repeated in this file or in any product document, for the same reason the
specs are not repeated in the template.

Two parts of it are easy to get wrong from habit, so they are worth naming here:

- **Comments are generous by design.** This project is being built as a way of learning the
  domain, so the reader to write for is the author six months from now, still learning. Explain
  what a thing does and why it exists — including the domain reasoning, not only the code
  reasoning — and cite the specification section that governs the rule. See §14.3.
- **Absence is a domain value.** Null, `UNKNOWN` and 404 carry meaning in this system and are
  not defects to be optimised away — but they must never stand in for *empty* or for *failure*.
  See §14.4.
- **Verify rather than assume**, and say what you checked. Confirm a symbol exists before
  editing it, compute a number before stating it, read what you are about to delete, and run
  the check after the change rather than before. §14.6 lists the cases this repository has
  actually produced — every one of them passed lint and type-checking first.

## One copy of everything

The specs used to exist twice — here, and snapshotted inside the template — and the
snapshot drifted. There is now one copy. The template reads these documents from one
directory up. **Do not reintroduce a copy of a spec inside `template/`**, however
convenient it looks; that is the mistake this layout exists to prevent.

The same applies to the avatars and the prototype itself. If you find yourself saving a second
version of either, **don't** — commit the change and let git hold the previous one. A superseded
copy that sits next to the live one gets read, edited and cited by mistake.
