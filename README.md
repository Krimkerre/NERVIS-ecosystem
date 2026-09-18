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

**[`nervis/`](./nervis/)** — the NERVIS service, and the single-file dashboard it
serves for itself and the three applications it observes. `src/nervis/` is a Python
package like its two siblings; `index.html` is the frontend, still one file with no
build step. Every screen reads from a data layer shaped like the real API responses,
and every endpoint it cites is cited from the spec beside it.

It is also the dashboard that **is** served: since NERVIS M0 it comes from `nervis serve`
rather than a static file server, so the thing serving it has a database, a stable
identity and its own `/ecosystem/*` surface. It keeps the guarantee that makes it
openable at all — a service being down degrades a panel rather than the page — and
opening the file directly with nothing running still shows mocks and says so. See
[`nervis/README.md`](./nervis/README.md).

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
| **[OPERATOR_RUNBOOK.md](./OPERATOR_RUNBOOK.md)** | Reading health and the instance registry, backup/restore/rollback commands, and what to do for each of §10's 19 failure conditions. | The ecosystem is already running and something needs watching, restoring, or diagnosing. |
| **[CLARVIS.md](./CLARVIS.md)** | Clarvis's *ecosystem* contracts — verified source invariants, RAVIS provider integration, the optional Bridge, code-server compatibility, theming. | You are integrating Clarvis with anything, or checking whether a change would regress a guarantee. |
| **[SIRVIS.md](./SIRVIS.md)** | Full SIRVIS build plan and contracts — runtime adapters, Runtime Sets, benchmark methodology, evidence identity and provenance, recommendations, 27 milestones. | You are building SIRVIS, or you need to know what its evidence actually means. |
| **[RAVIS.md](./RAVIS.md)** | Full RAVIS build plan and contracts — the two execution paths, the Clarvis Compatibility Contract, routing pipeline, sessions, cost, management API, 30 milestones. | You are building RAVIS, or debugging why a stream broke. |
| **[NERVIS.md](./NERVIS.md)** | Full NERVIS build plan and contracts — registry and capability negotiation, dashboard, general chat, event hub, tracing, diagnostics, supervision, the Code tab, 30 milestones. | You are building NERVIS, or deciding what a control plane is allowed to do. |

## Installing it

From a checkout of this repository, on macOS or Linux:

```bash
./install.sh
```

It works out which system it is on, installs what the ecosystem needs, builds Clarvis and puts it
into code-server, and adds NERVIS to the desktop — then stops; it starts nothing. Everything it
installs, and what it leaves out, is in its closing lines. Safe to run again: each step looks first
and skips what is there. `./install.sh --help` lists the options, and `--dry-run` shows the plan
without doing any of it.

| | Packages from | Also installs | Desktop |
|---|---|---|---|
| macOS | Homebrew | code-server, Ollama | builds `NERVIS.app` (the menu bar app) and copies it to Applications |
| Ubuntu, Debian and relatives | apt | code-server and Ollama through their official installers | an applications-menu entry and the tray icon |
| Fedora and relatives | dnf | the same | the same |
| Arch and relatives | pacman | the same | the same |
| Windows | — | run it inside WSL 2 (`wsl --install`, then Ubuntu), where it is the Linux install. WSL 1 is refused with the commands that convert it: its sandboxes need a real Linux kernel | no tray: WSL can't reach Windows' tray; open the dashboard in a Windows browser |

**Codex**, the optional coding engine RAVIS runs on your ChatGPT plan, comes from a place RAVIS can
check before trusting it: Homebrew on macOS (Apple's signature), Arch's own `openai-codex` package on
Arch and its relatives (pacman's file check), and elsewhere OpenAI's npm build in
`~/.local/share/nervis/codex`, whose npm signature, provenance record and every file RAVIS checks.
`--no-codex` leaves it out; `--update-codex` brings the latest npm build. A build RAVIS hasn't seen
waits for your OK in NERVIS before it runs.

Clarvis is connected as well as installed: its chat and agent go through RAVIS, its Bridge
reports to NERVIS, and code-server wears its theme. Only settings that are missing are added, so
anything you set yourself stays. On a machine installed before 19 September 2026, run
`python3 tools/run.py clarvis-settings` once.

It asks you to choose a password for the editor (code-server asks for it once per browser); press
Enter and it makes one up and shows it at the end. Running it again offers to change it. A
`~/.config/code-server/config.yaml` of your own takes precedence, and then it asks nothing.

It asks for sudo only to install system packages, and never runs as root. Ollama gets NERVIS's
embedding model (`nomic-embed-text`, about 270 MB); the PDF layout model (about 3 GB) is offered,
not assumed. Building Clarvis needs Node 20 or newer: a system Node that is new enough is used,
and otherwise nodejs.org's own build goes into `~/.local/share/nervis/node`, checked against the
checksums nodejs.org publishes — no package repository is added for it. Clarvis is looked for beside
this checkout (`../clarvis`) and cloned there from the same GitHub account when it isn't.

**Verified on 18 September 2026** on Debian 12, Fedora (Python 3.14) and Arch (x86-64) in containers, and on
an Ubuntu 24.04 desktop in a VM, where the tray was opened from the applications menu and started
the whole stack. See STATUS.md for what each run found.

## Running it

One file per platform, next to this README. Double-click on macOS or Windows, or
run it from a shell:

| | Start | Stop |
|---|---|---|
| macOS | `start-macos.command` | `stop-macos.command` |
| Linux | `./start-linux.sh` | `./stop-linux.sh` |
| Windows | `start-windows.bat` | `stop-windows.bat` |

It brings up SIRVIS on 8721, RAVIS on 8731 and NERVIS on 8790, then opens the
dashboard. First run creates the virtual environment and installs the four
packages, which takes a minute; later runs skip straight past that.

**The services are detached.** They keep running when the window closes — which
is the point, and is also why there is a stop launcher rather than a Ctrl-C. All
six launchers are three lines calling `tools/run.py`, which also takes
`status`:

```bash
python3 tools/run.py status
```

**On a Mac there is also a menu bar app, and on Linux a tray icon with the same menu.**
`nervis/packaging/macos/build_app.sh` builds `NERVIS.app` into `nervis/packaging/macos/build/`;
on Linux `install.sh` adds an applications-menu entry that opens `nervis/packaging/linux/nervis-tray`.
Either one starts the stack, shows what is running and how busy the machine is, loads models
through SIRVIS, looks after Codex's tasks, opens the dashboard, and stops everything when it quits.
Neither carries service code — both run `tools/run.py` — so neither needs rebuilding after an
update; `OPERATOR_RUNBOOK.md` describes the menu. The tray needs a desktop that shows tray icons:
KDE, XFCE, Cinnamon, MATE and Budgie do; GNOME does through its AppIndicator extension, which
Ubuntu ships and `install.sh` switches on.

**It does not start LM Studio or Clarvis.** Those are separate applications
with their own lifecycles, and §9 puts model loading behind SIRVIS's Resource
Manager rather than a launcher. Their state is reported instead, because
"nothing is routing" and "no runtime is running" look identical from the
dashboard and have very different fixes.

**Ollama and code-server are the two exceptions**, each started and stopped
alongside the three services when it is installed. RAVIS's `/v1/embeddings`
route needs a local embedding model to answer NERVIS chat's own knowledge
lookups, which makes Ollama load-bearing for chat rather than an optional
runtime choice — `nomic-embed-text` is warmed once at startup if Ollama is
installed, and the launcher says plainly when it is not
(`./install.sh` installs it). code-server hosts
Clarvis for NERVIS's Code tab; the launcher prints its address and the command
that installs Clarvis into it, and says so when code-server is not installed.

Logs are in `.run/`, one file per service, and so are the credentials the
launcher mints so that nothing has to be pasted anywhere:

| File | What it is |
|---|---|
| `.run/dashboard.token` | SIRVIS, `read runtime` — handed to the page in the URL fragment so it can load and release models |
| `.run/nervis-benchmark.token` | SIRVIS, `benchmark` — held by NERVIS, never by the browser, so a confirmed "bench this model" can be carried out |
| `.run/nervis-ravis.token` | NERVIS's identity to RAVIS, stored on both sides, so its reads are named rather than anonymous |

All three are mode `0600`, minted once and reused. They are local credentials
for services the person running the launcher already controls; the reason each
exists is in `NERVIS.md` §12.1, and the reason the second one is not in the
browser is the whole of that section.

## How these relate to the products

Two repositories, not four — see `ECOSYSTEM_RUNBOOK.md` §3 for why. **Clarvis is separate**
(TypeScript, VS Code extension host, shipped as a `.vsix`). SIRVIS, RAVIS and NERVIS live here
alongside the protocol package, as separately buildable packages with their own entry points and
their own databases. The template is no longer one of them: its mock-only snapshot lives outside
this repository, at `~/Documents/coding/nervis-template/` (see above).

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

**Clarvis** (`../clarvis`), **RAVIS** (`ravis/`), **SIRVIS** (`sirvis/`) and **NERVIS**
(`nervis/`) all exist as code, and all three of this repository's services run.
[`STATUS.md`](./STATUS.md) is the file that says how far each has got and how to check it in
four commands — this section deliberately says no more than that, because two places tracking
the same number is how one of them starts lying.

That sentence previously said SIRVIS and NERVIS were still specifications, which stopped being
true many milestones ago and stayed on the front page — the exact failure the paragraph above
warns about, in the file that warns about it.

**NERVIS chat can be talked to about the machine, and asked to do a short list of
things to it.** It reads the registry, the queue and its results, the runtime and
what it holds, the machine's own memory and thermal state, what routing has cost,
which upstreams are answering, RAVIS's routing record and policy, the evidence
index and what has been withdrawn from it — each only when the question is about
it — and offers, never performs, an enumerated set of operations that a person
confirms with a button. The contract for both halves is `NERVIS.md` §7.0; the
credentials that make the second half possible are §12.1.

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
