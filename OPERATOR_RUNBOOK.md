# Operator Runbook

**Status:** Living document for whoever is running this ecosystem, not building it
**Audience:** Operators — the person who started the stack and is watching it work
**Scope:** Reading health, credentials and logs, backup/rollback/restore, and what to do
when each of §10's failure conditions actually happens
**Companion to:** `README.md` (install and start/stop), `ECOSYSTEM_RUNBOOK.md` (the build and
integration spec this document does not repeat)

> This document names things and points at evidence rather than re-explaining it. If a
> section here and the file it cites ever disagree, the cited file is right — the same rule
> `ECOSYSTEM_RUNBOOK.md` §3 states about two copies of one fact.

---

## Running it

Covered in full in `README.md`'s "Running it" section: the six per-platform launchers,
`python3 tools/run.py status`, where logs live (`.run/*.log`, one file per service), and what
each of the three minted credential files (`.run/dashboard.token`, `.run/nervis-benchmark.token`,
`.run/nervis-ravis.token`) is for. Not repeated here — read it there.

One thing worth stating plainly for an operator rather than a builder: **the launcher starts
SIRVIS, RAVIS and NERVIS only.** LM Studio, Ollama and Clarvis are separate applications with
their own lifecycles; the dashboard reports their state rather than controlling it. "Nothing is
routing" and "no runtime is running" look identical on the dashboard and have different fixes —
check which one it actually is before touching anything.

---

## Reading health

The dashboard (`http://127.0.0.1:8790` by default, printed by the launcher) is the first place
to look. Underneath it, two APIs carry the vocabulary this section explains:

- `GET /api/v1/services` on NERVIS — the three core services (SIRVIS, RAVIS, NERVIS itself),
  each with a `state` field.
- `GET /api/v1/registry/instances` on NERVIS — every live Clarvis Bridge, one row per editor
  window (desktop VS Code, VSCodium, or a code-server tab), each with its own `instance_id`,
  `endpoint` (host and port), and a `live` boolean. This is a *different* endpoint from
  `/api/v1/services` — the first lists Clarvis windows, the second lists the three core
  services — and confusing the two is an easy mistake to make once, not twice.

**`unreachable` and `stopped` are not the same finding, and the difference matters.**
`unreachable` means a peer stopped answering on its own — a crash, a network problem, someone
killing it outside the launcher. `stopped` is reserved for a peer NERVIS itself shut down as
part of an operation it initiated. Reading the two as interchangeable turns "I stopped this on
purpose" and "this fell over" into the same alarm, which is exactly backwards for deciding
whether to act. (Confirmed live, not just designed: killing SIRVIS by pid and watching NERVIS's
own `/api/v1/services` read produces `unreachable`, never `stopped`, until SIRVIS is restarted
and reports itself healthy again — see `tools/acceptance_run.py`'s `restart_clause`.)

A Clarvis instance's row in `/api/v1/registry/instances` does not disappear the moment its
window closes — it stays present with `live: false` until its lease naturally expires. Seeing a
`live: false` row is not itself a problem; seeing a row you expect to be `live: true` reporting
`false` is the thing to act on.

---

## Backup, rollback and recovery

### The database backup/restore commands

NERVIS, RAVIS and SIRVIS each write a `.v{N}.bak` file beside their own database before every
schema migration, automatically — using SQLite's own online backup API rather than a file copy,
because a plain `cp` in WAL mode can hand you back the state you were trying to roll away from
without telling you. Each service also ships the command to put one back:

```bash
ravis/.venv/bin/nervis restore-database [--version N]
ravis/.venv/bin/ravis  restore-database [--version N]
ravis/.venv/bin/sirvis restore-database [--version N]
```

(All three console scripts live in RAVIS's own virtual environment — `tools/run.py` installs
all four packages there, so there is one venv to reach for regardless of which service you are
restoring.)

Omitting `--version` restores the *latest* backup; if more than one backup exists beside the
database, the command refuses to guess and lists what is available so you can name one. **Stop
the affected service first.** The command does not check for a running one — the tool's own
design note is that a lock probe is a second thing to get wrong, so it prints the instruction
("stop the service before restoring; a running one holds its own connection") and trusts you to
follow it, the same way you would follow any other precondition it can't verify for you. After
restoring: start the service back up and confirm it reports `healthy` before resuming normal
work.

### Clarvis rollback

Reinstall the prior `.vsix` and reopen the workspace:

```bash
code    --install-extension /path/to/clarvis-<old-version>.vsix --force   # VS Code
codium  --install-extension /path/to/clarvis-<old-version>.vsix --force   # VSCodium
code-server --install-extension /path/to/clarvis-<old-version>.vsix --force
```

This is not theoretical — it was run for real on 6 September 2026 against a live daily-driver
workspace with real conversation history and real provider keys already in it, downgrading
`krimkerre.clarvis@0.12.6` to `0.12.3`. Both `context.secrets` (the stored provider keys) and
`context.workspaceState` (the conversation history) were hashed before and after rather than
eyeballed, and came back intact: every provider-key ciphertext byte-for-byte identical, every
pre-existing chat session preserved verbatim. Full detail in
`clarvis/docs/code-server-matrix.md`'s rollback section, graded `PASS`.

---

## §10 failure playbook

`tools/check_degradation.py` is this ecosystem's own scored matrix for the conditions below —
run it (`python3 tools/check_degradation.py`) for the exact evidence and citation behind each
one. Every condition there is graded `PARTIAL`, not `COVERED`: the ecosystem is generally
*designed* to handle each one, and most of that design is proven by a real test somewhere, but
not always by the kind of end-to-end, live-route test that would let anyone say the full
outcome is guaranteed. The entries below say, for each condition, what you should see, what the
system is built to do about it, and what to actually do — and are honest about which half of
that is proven versus designed-but-not-yet-exercised live. Where a condition's automated
evidence is thin, treat the "what the system does" column as intent, confirmed at the unit or
static-analysis level, not as a live-fire guarantee.

| Condition | What you'll see | What the system does | What to do |
|---|---|---|---|
| **Clock skew** | A trace shows a warning about a producer's clock | NERVIS flags an event stamped more than 2s ahead of receipt (one-directional: a clock running *behind* is not yet distinguished from normal latency) | Check the flagged host's clock; a skew warning on one producer only usually means that host, not the ecosystem |
| **Unsupported major protocol version** | A peer's registration or probe is rejected | NERVIS's own probing of RAVIS/SIRVIS and RAVIS's reading of SIRVIS evidence both reject an unsupported major (tested against fakes); the Clarvis Bridge registration path is the one not yet guarded the same way | Check the rejected peer's own protocol version against what this NERVIS declares (`tools/check_compatibility.py`) before assuming a bug |
| **Crash and restart mid-operation** | A service disappears and reappears with a new `instance_id` | Startup reconciliation (`reconcile_interrupted`/`sweep`) is unit-tested; nothing yet kills a service *while a request is genuinely in flight* and checks the outcome live | After a crash, confirm the restarted service's `instance_id` actually changed and that in-flight work wasn't silently duplicated |
| **Corrupt response** | RAVIS forwards something that doesn't parse | On the transparent proxy path, an unparseable 200 is deliberately left alone rather than repaired or hidden | Treat a malformed response as a signal to check the upstream provider directly, not a RAVIS bug by default |
| **Duplicate / out-of-order events** | A trace looks incomplete or doubled | Duplicates are handled at the storage layer and at producer retry; out-of-order arrival is proven inside the publisher queue, not yet through NERVIS's own ingestion route | A trace that looks wrong is worth re-fetching once before treating it as data loss — ordering at the route level isn't fully proven yet |
| **A service absent at startup** | The launcher comes up with one peer missing | NERVIS's registry and readiness logic are route-tested for a peer that never registers; no live run yet starts the *whole stack* with one peer deliberately withheld from boot | Start the missing service and confirm it registers; nothing here should require restarting peers that are already up |
| **Timeout** | A request takes the full timeout window and then fails over | RAVIS's rule (don't retry the same target, fall back, open the provider's circuit) is asserted on the code path; not yet provoked as a real timeout against a running stack | A single timeout should fail over automatically — if it doesn't, that's the gap to report, not expected behavior |
| **Slow response** | Something is answering, just slowly | Deadlines and probe timeouts exist per-component (Clarvis, RAVIS, SIRVIS); nothing yet checks that a service's *own routes* stay truthful while a dependency is slow | Distinguish "slow but eventually correct" from "slow and now lying about its state" — only the first is currently proven |
| **Full disk** | A write fails, a benchmark or log stops progressing | Nothing in this ecosystem currently simulates or specifically handles a full disk | Treat this as an unhandled condition today — free space before it happens rather than trusting graceful degradation |
| **Unavailable keychain** | Credential reads hang or fail | RAVIS's keychain fallthrough has a 5-second timeout, code-only — no test exercises a keychain that's missing, erroring, or stuck prompting | If credential reads seem to hang, check the OS keychain state directly rather than waiting past 5s expecting an automatic recovery |
| **Trace collector loss** (NERVIS's event hub down) | Producers keep answering; NERVIS's dashboard/trace view goes quiet | **This is the one outcome that actually regressed once.** Verified live (not just designed): killing NERVIS mid-run left a dependent service answering at baseline latency, still reporting healthy, with every event queued during the outage delivered as one ordered batch on reconnect — but that evidence is from before 29 August and `events.py` has moved since, and it was never turned into a repeatable test | A dead collector should never make a healthy product report itself unready — if it does, that's a real regression against previously-observed behavior, worth escalating specifically |
| **Read-only data directory** | Writes fail | One untested diagnostic string exists; nothing simulates or specifically handles this today | Treat as unhandled; fix directory permissions rather than expecting a graceful message |
| **Cloud provider 401/403/429/5xx** | RAVIS fails over from a cloud provider | 429 and 503 have real route-level tests and proven fallback; 401/403 and a bare 500 are asserted only on the internal classifier object, not through a live route | 429/503 failover is trustworthy; if a 401/403 doesn't fail over as expected, that's the specific untested edge |
| **Network loss** | A candidate provider is unreachable before any response | The decision logic (fall through to the next candidate) lives in code reviewed for this; not yet driven through a real pre-first-byte connection failure | Expect failover to the next candidate; if RAVIS instead surfaces the raw connection error, that's the gap |
| **Hung local runtime** | A local model runtime accepts a connection and then never answers | SIRVIS's HTTP timeouts collapse a hang into the same `RUNTIME_UNAVAILABLE` as an absent runtime | You cannot currently tell "hung" from "not running" by SIRVIS's own report alone — check the runtime process directly if this matters |
| **Expired credential** | RAVIS keeps a provider marked reachable after its key stopped working | RAVIS's authenticated health probe currently turns a 401 into `reachable=False` without a distinct "credential expired" signal (worse for some providers, which discard the status code entirely) | Don't trust `reachable=False` alone to mean "the service is down" — check whether the actual credential is still valid first |
| **code-server loss after it had answered** | The Code tab's embedded editor stops responding | Loss semantics are proven only through generic registry tests, not a scenario naming code-server specifically | Treat exactly like any other lost peer until this gets dedicated coverage — the tab's own absent-editor state should still degrade sensibly |
| **Bridge collision** (two Clarvis Bridges colliding on an endpoint, `instance_id`, or registry row) | A live-Bridge collision is refused; **a *dead* Bridge's stale row is not yet checked before being read** | `read_status`/`read_diagnostics` currently look an instance up without checking whether it's actually live | A registry row that looks stale (old `renewed_at`, no recent activity) may not be reliably distinguishable from a live one on every read path yet — don't assume every row you can query is actually answering |
| **Stale registry lease** | A Bridge's lease should have lapsed but its row is still queried | Lease expiry is proven with injected clocks in unit tests and on the Clarvis registrant side; nothing yet lets a real lease lapse and asks a live NERVIS route what it says | Same caution as Bridge collision above — cross-check `renewed_at`/`expires_in` yourself rather than trusting every returned row is current |

---

## Known limitations, compatibility and releases

Not restated here — each is published as a gate rather than a list, so it's counted rather than
remembered:

- **Known limitations**: the §10 table above (source: `tools/check_degradation.py`), the
  pairwise state grades in `clarvis/docs/code-server-matrix.md` (§13.4), and the
  `IMPLEMENTED` / `AUTOMATED VERIFIED` / `LIVE VERIFIED` / `BLOCKED` state on every milestone row
  in `NERVIS.md`, `RAVIS.md`, `SIRVIS.md` and `CLARVIS.md`.
- **Load**: `ravis/.venv/bin/python tools/load_test.py` — correctness with many callers at once,
  §9.8's overhead budget, the anonymous rate limit, and the dashboard's reads against the running
  NERVIS. It calls no real model and spends nothing, and it exits non-zero while the overhead
  budget is unmet (see §9.8 in `RAVIS.md`).
- **Compatibility matrix**: `python3 tools/check_compatibility.py` prints what this NERVIS
  supports its peers at and fails if any peer ships outside that window — printed rather than
  filed, because a table in a document is a copy that goes stale.
- **Release notes**: `RELEASES.md`, enforced by `tools/check_releases.py` — a version bump with
  no note fails the gate.
