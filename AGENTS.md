# Agent instructions for this repository

*Tool-agnostic: this applies to Claude Code, OpenCode, Codex or anything else.*

## Read [`STATUS.md`](./STATUS.md) first

It says what is built, what is next, and how to verify both yourself in four
commands. **Milestone numbers are identifiers, not a schedule** — M4 is numbered
before M9 and executes after it — so the ordering in STATUS.md is the one to
follow, not the numbering in the milestone tables.

Updating it is part of finishing a milestone. A status file that drifts is worse
than none, because it is believed.

## What is here

**The `.md` files in this directory are the specifications**, and they are the
authority for what each service owes the others. `ECOSYSTEM_RUNBOOK.md` wins on any
cross-product contract; an application's own document wins on its internals.

**`nervis/` is the NERVIS service and the dashboard it serves** — the Python service in
`nervis/src/nervis/`, and `nervis/index.html`, the one HTML file the service serves as its
UI (it still opens from disk on its mocks with nothing running). It is the one the build
changes. It has its own
working rules in [`nervis/AGENTS.md`](./nervis/AGENTS.md), and you should read those
before editing anything in there. It also has
[`nervis/docs/PITFALLS.md`](./nervis/docs/PITFALLS.md), which is every defect that page
has produced and the rule that prevents each one. Almost nothing in it fails loudly, so
read that before your first edit.

It used to be `template/`. **A frozen reference copy now lives outside this repository**,
at `~/Documents/coding/nervis-template/`, so that the one-copy rule below stays true
inside it: there is exactly one prototype here. The copy out there stays on its mocks and
always opens from disk with nothing running; this one tracks real endpoints and is
expected to be ahead of it. Do not bring it back in, and do not reconcile the two by
copying one over the other.

Superseded copies are **not kept in the working tree** — git history is the archive. A second
copy of a document is the one failure this layout exists to prevent, and a folder blessing the
practice invited it.

## The rule that governs everything

> **No agent may invent another ecosystem component's API, schema, capability or
> behaviour merely to complete its own milestone. If the required contract does not
> exist, implement against the canonical contract, use a labelled test double, or
> stop and report the missing dependency.**

In the dashboard that means: do not add a UI control without an endpoint that backs it.
A screen implying an API nobody has agreed to is worse than a missing screen, because
it looks finished.

## Writing code here

**`ECOSYSTEM_RUNBOOK.md` §14 is the engineering standard** — complexity ceiling of 8, naming,
comments, error handling, tests, and the lint/type/test gates that enforce them. It is stated
once there and is not repeated in this file or in any product document, for the same reason the
specs are not repeated in the dashboard.

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

The specs used to exist twice — here, and snapshotted inside the prototype — and the
snapshot drifted. There is now one copy. The prototype reads these documents from one
directory up. **Do not reintroduce a copy of a spec inside `nervis/`**, however
convenient it looks; that is the mistake this layout exists to prevent.

The same applies to the avatars. If you find yourself saving a second version, **don't** —
commit the change and let git hold the previous one. A superseded copy that sits next to the
live one gets read, edited and cited by mistake.

There is one prototype in this repository — `nervis/` — and it owns its documentation and
its tooling outright. The mock-only reference snapshot was moved to
`~/Documents/coding/nervis-template/` rather than kept beside it, because a second copy in
the tree is exactly what this rule exists to prevent, however defensible the reason.

## Commits run the dashboard's checks

A commit that touches `nervis/index.html`, `nervis/tools/`, `nervis/knowledge/` or the hook
itself runs `nervis/tools/check.py`, every node gate named in `nervis/tools/dashboard_gates.txt`,
the sandbox gate and the knowledge check — against exactly what is being committed — and stops
the commit if one fails (`tools/githooks/pre-commit`, about thirty seconds). Switch it on once per
clone with `git config core.hooksPath tools/githooks` — `tools/run.py start` does this for you
since NERVIS 0.34.14, unless the clone already names another hooks path; add a new gate by adding its name to that
list, which the clean-clone gate reads too. These checks used to run only in the clean-clone gate,
and five of them failed unnoticed for days. Do not skip the hook with `--no-verify` unless the
owner says so. **Every commit, whatever it touches, first gets the secret scan**
(`tools/check_secret_content.py`): a key-shaped string or one of this machine's launcher secrets
in an added line stops it. A deliberate fake in a test says `secret-scan: allow` on its line.
