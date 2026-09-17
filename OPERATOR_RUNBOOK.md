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
SIRVIS, RAVIS and NERVIS, and also Ollama and code-server when each is installed** — and stops
them again with the stop launcher. Ollama is there because RAVIS's `/v1/embeddings` route needs
it for NERVIS chat's knowledge lookups; code-server is there because it hosts Clarvis for the
Code tab. When either is missing, the launcher says so in its output rather than failing. LM
Studio and Clarvis itself are separate applications with their own lifecycles (under
code-server the launcher prints the command that installs Clarvis rather than installing it);
the dashboard reports their state rather than controlling it. "Nothing is routing" and "no
runtime is running" look identical on the dashboard and have different fixes — check which one
it actually is before touching anything.

**Start SIRVIS, RAVIS and NERVIS with the launcher, not by hand** (since NERVIS 0.34.18). The
launcher gives RAVIS and SIRVIS each a secret (`.run/ravis-events.token`,
`.run/sirvis-events.token`) and gives NERVIS both, and NERVIS refuses events that don't carry one.
A service started some other way still works, but its events are refused; the Events screen and
the Overview then say that NERVIS is refusing events from it, and its own log says the same.

**On a Mac, the menu bar app does the same with a click.** Build it with
`nervis/packaging/macos/build_app.sh` and copy `nervis/packaging/macos/build/NERVIS.app` into
Applications once; every later build refreshes that copy. It does not open at login unless it is
added under System Settings → General → Login Items. It has
no window and no Dock icon. The NERVIS mark appears in the menu bar and the stack starts; its
menu shows the number of unread notifications when there are any, **Open NERVIS dashboard**,
each service — CLARVIS among them, running while an editor window has its Bridge on — and
LM Studio and Ollama as running or not (LM Studio's entry opens LM Studio and lists its installed
models), CPU, GPU and memory use, and **Quit
NERVIS and stop the stack**. Clicking a line of the stack opens it in the browser: SIRVIS, RAVIS,
NERVIS and CLARVIS on their screens in the dashboard, code-server at its own address. The pupil in
the mark is solid while the whole stack answers, faint
when part of it does not, and blinks while NERVIS has unread notifications. The CPU or GPU percentage turns red above 85%. GPU use is the figure
the graphics driver publishes; Activity Monitor's GPU History is the place to compare it.

It runs `tools/run.py` and holds no code of its own, so an update needs a restart and never a
rebuild — only moving the repository does, because the app records where the repository is when
it is built. Its log is `.run/menubar.log`, kept across launches: it records each start and each quit, and a launch
that finds the previous run never quit — killed, force-quit or crashed — says so.

**Loading a model from it goes through SIRVIS**, which owns every load and unload. Clicking a model in
LM Studio's list loads it under a SIRVIS lease that the app renews every ten minutes; it is ticked, and
clicking it again unloads it. Quitting NERVIS — or `tools/run.py stop` — frees them: the launcher
releases the menu's leases first, and since 12 September SIRVIS itself, when it stops, releases every
session and unloads every model it loaded — the menu's, RAVIS's, a benchmark's — finishing inside the
launcher's 12-second wait, and names in its log anything that may still be loaded. A model you loaded yourself in LM Studio
stays loaded. A model whose file, with a fifth
more room to work, is larger than the memory free at that moment is loaded only after a dialog says so.
A model loaded any other way — by RAVIS, a benchmark or LM Studio itself — is shown with a dash and is
not the menu's to unload. SIRVIS loads at most two models at once and says so when asked for a third. Opening a second copy does nothing, and quitting it
stops the stack first however the quit arrives, an ordinary `kill` included.

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

### Rolling the stack back to an earlier release

SIRVIS, RAVIS, NERVIS and the shared protocol package roll back **together**, to a commit where
all four were released as a set. Rolling back one alone can break what they agree on: since
NERVIS 0.34.18 NERVIS refuses events without a sender's secret, and RAVIS before 0.29.2 and SIRVIS
before 0.19.4 send none. Rehearsed on 17 September 2026 (below); each direction took under a
minute.

1. **Check nothing is in flight**: no running benchmark job (`GET /api/v1/benchmark-jobs` on
   SIRVIS), no open runtime session, no Codex task (RAVIS → Dashboard's Codex card), nothing
   loaded in LM Studio (`lms ps`). A job stopped halfway is marked unrecoverable, not rerun.
2. **Pick the target commit** and check whether any database format changed since it:
   `git diff --stat <commit> HEAD -- '*migrat*'`. If nothing changed, no restore is needed. If
   something did, stop the stack and restore each affected database (the commands above) before
   starting the older release — never run an older service on a newer database; the services
   refuse to.
3. **Unpack the target beside the checkout**, leaving the checkout itself alone:
   `mkdir <folder> && git archive <commit> | tar -x -C <folder>`.
4. **Stop the stack**: `python3 tools/run.py stop`.
5. **Point the services at the older code**:
   `ravis/.venv/bin/python -m pip install --no-deps -e <folder>/protocol -e <folder>/ravis -e <folder>/sirvis -e <folder>/nervis`.
6. **Start the stack** with the checkout's launcher, `python3 tools/run.py start`, and confirm
   each service reports the older version at `/ecosystem/version` and `healthy`. The launcher
   stays the current one; an older service ignores settings it doesn't know.
7. **To roll forward**, repeat 4–6 with the checkout's own folders:
   `pip install --no-deps -e protocol -e ravis -e sirvis -e nervis`.

**The rehearsal, 17 September 2026:** from NERVIS 0.34.18 / RAVIS 0.29.2 / SIRVIS 0.19.4 /
protocol 0.2.2 back to commit `b423b5e` (0.34.17 / 0.29.1 / 0.19.3 / 0.2.1) and forward again.
No database format had changed, so no restore was needed. Before, between and after, every
database passed SQLite's integrity check and no table lost a row; all three services' events
arrived on the older set and, through the sender check, on the current one; SIRVIS's 20 jobs
stayed 20; Clarvis stayed 0.17.16 in both editors; and nothing the launcher printed carried a
secret (checked against this machine's launcher secrets and every key shape). **The restore
commands** were rehearsed on copies: each database copied with SQLite's backup API, a
`.v<N>.bak` made of the copy, the copy's largest table emptied (820 chat messages, 438 usage
records, 3,406 machine readings), and `restore-database` run against it — all three came back
whole and passed a full integrity check. The live databases were only read.

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

### Upgrading code-server

Homebrew's `code-server` formula is deprecated and stops at 4.112.0, so code-server is the
standalone install: each version in `~/.local/lib/code-server-<version>`, with
`~/.local/bin/code-server` pointing at the one in use. To move to a new release:

1. Download `code-server-<version>-macos-arm64.tar.gz` from the release on GitHub and compare its
   SHA-256 with the digest GitHub shows for that file.
2. Unpack it to `~/.local/lib/code-server-<version>` (rename the unpacked folder) and repoint the
   link: `ln -sfn ~/.local/lib/code-server-<version>/bin/code-server ~/.local/bin/code-server`.
   **Keep `code-server-4.135.0`**: it is the install Stage 9's matrix graded, and the check below
   compares against it. Keep the previous version too, as the rollback — repoint the link back.
3. Stop the running code-server (it keeps running detached otherwise) and restart the stack.
   Clarvis stays installed; the editor asks for its password again.
4. Run the upgrade check — seconds, not the matrix's hours:

   ```bash
   python3 tools/code_server_upgrade_check.py --record
   ```

   It compares code-server's login, proxy, origin, WebSocket and webview-host files with the
   graded 4.135.0, runs Clarvis's host suite on the exact Code version code-server bundles, and
   checks the live editor, Clarvis's installed copy and any open window. A pass is recorded in
   `nervis/src/nervis/code_server_checks.json` (commit it, then restart NERVIS) and NERVIS shows
   the version as checked rather than untested. A failure names the matrix cells a changed file
   touches; re-run only those. Other browsers and audio are never re-checked by it.

### Checking dependencies for known holes

The code the ecosystem is built from — Python packages, npm packages — can have published
security holes. One command asks the public advisory databases about all of it (seconds; needs
the network; reads only):

```bash
python3 tools/check_dependencies.py
```

It checks the Python environment the launcher starts all three services from (`ravis/.venv`),
NERVIS's page-check tooling, what the Clarvis extension ships, and Clarvis's build and test tools.
A hole in any of the first three fails it (exit 1); holes only in Clarvis's build and test tools
are listed with their fix but do not fail, because those tools run only on Clarvis's own source.
Exit 2 means something could not be checked — never read that as clean.

`pip-audit` lives in its own environment, `tools/.venv`, so its dependencies never change the
services'. On a fresh machine: `python3 -m venv tools/.venv && tools/.venv/bin/python -m pip
install pip-audit`. Run it after upgrading anything, and now and then — new holes are published
against versions that were clean yesterday.

---

## §10 failure playbook

`tools/check_degradation.py` is this ecosystem's own scored matrix for the conditions below —
run it (`python3 tools/check_degradation.py`) for the exact evidence and citation behind each
one. **As of 12 September 2026 it grades all 19 conditions `COVERED`, and that word is narrower
than it sounds.** `COVERED` means the outcomes a cell names are proved and everything else is
written down as unproved — never "nothing is left". The lines that matter are the per-outcome
counts it prints under the verdicts: readiness and capability become truthful under 16 of the
19 conditions; retries stay bounded, recovery stays idempotent and each product stays usable on
its own under 8 each; no failover crosses a constraint under 7; and queues stay bounded under 6.
So no condition below is a guarantee of all six outcomes. The entries say, for each condition,
what you should see, what is actually proved, and what to do — and where the proof stops, they
say that too. Most of the proof is route-level tests, driving one service's real routes in a
test process; the few conditions exercised against the running stack are named as live.

| Condition | What you'll see | What is proved | What to do |
|---|---|---|---|
| **Clock skew** | A trace shows a warning about a producer's clock | NERVIS flags an event stamped more than 2 s ahead of receipt, and one more than 120 s behind, both tested through its trace API. A producer running a few seconds to two minutes slow is reported as nothing, and nothing tests NERVIS's own clock stepping or RAVIS's spend window, which reads the wall clock | Check the flagged host's clock; a skew warning on one producer only usually means that host, not the ecosystem |
| **Unsupported major protocol version** | A peer's registration or probe is rejected | Rejected where a version is presented — NERVIS probing a peer, and a Clarvis Bridge registering — with route tests for both. Not covered: a peer whose major changes mid-session, and RAVIS reading SIRVIS evidence across an unsupported major | Check the rejected peer's own protocol version against what this NERVIS declares (`tools/check_compatibility.py`) before assuming a bug |
| **Crash and restart mid-operation** | A service disappears and reappears with a new `instance_id` | **Live, for SIRVIS:** `tools/acceptance_run.py` kills it with `SIGKILL` during a real benchmark, and the next process reconciles the run and ends the job rather than leaving it hanging — and does not quietly run the interrupted job again. RAVIS and NERVIS have never been killed mid-operation by anything | After a SIRVIS crash, confirm the interrupted run reads as ended rather than `running`. After a RAVIS or NERVIS crash, confirm the `instance_id` changed and check in-flight work by hand — that case is unproved |
| **Corrupt response** | RAVIS forwards something that doesn't parse | A corrupt body is forwarded rather than rewritten, the next request is still served, and a refusal RAVIS cannot recognise stops the fallback chain at one attempt rather than shopping the request around. Not shown: readiness — a provider answering garbage still reads as reachable | Check the upstream provider directly, and don't read its "reachable" state as proof it is answering sensibly |
| **Duplicate / out-of-order events** | A trace looks incomplete or doubled | Tested through NERVIS's trace API: a trace assembles the same whatever order its events arrived in, the same batch sent twice does not double it, and a replaying producer cannot grow the hub without bound. Not covered: a producer that rebuilds the same event under a fresh id, which is not deduplicated | Re-fetch a trace that looks wrong once before treating it as data loss; a doubled span carrying two different event ids is the unproved case |
| **A service absent at startup** | The launcher comes up with one peer missing | **Live, for one starting order:** `tools/acceptance_run.py` starts NERVIS with SIRVIS already gone, and NERVIS comes up and reports SIRVIS as unreachable or discovering rather than healthy. A peer that is simply absent costs one connection attempt per probe pass, and event publishing buffers to a bound and counts what it drops. Other starting orders are untested | Start the missing service and confirm it registers; nothing here should require restarting peers that are already up |
| **Timeout** | A request waits out its timeout, then moves on or fails | Tested through RAVIS's routes: a timed-out model is asked once and the chain moves to the next; a model the caller named directly is not replaced; a local model that hangs is not answered from the cloud; the decision log stays bounded. Not shown: a service's own routes staying usable while a timeout is in flight | A pool request should move on after one timeout. A directly named model, or a request that must stay local, correctly returns the failure instead — that is the rule working, not a bug |
| **Slow response** | Something is answering, just slowly | NERVIS's probe deadline is tested, a slow peer is reported unreachable rather than healthy, the services listing keeps answering while peers are slow, and a reader that falls behind on the event stream is dropped rather than waited for. Not shown: one peer slow while the rest are healthy, or that a slow peer cannot delay another peer's row | A slow peer showing as unreachable is the designed reading. Distinguish "slow but eventually correct" from "slow and now reported wrongly" — mixed slow-and-healthy is the unproved case |
| **Full disk** | A write fails, a benchmark stops progressing | Tested in SIRVIS: a run that cannot write ends as failed rather than staying `running`, the model it held is released, and the service keeps answering while every results write fails with `ENOSPC`. Two gaps: nothing checks free space before a run starts, so a run that cannot finish still starts; and the test proving the model is released is weaker evidence than it reads | Free space before starting a benchmark — SIRVIS won't stop you starting one that cannot finish. A run killed by a full disk should read as failed; one still showing `running` is a real bug. If memory stays held afterwards, check the runtime |
| **Unavailable keychain** | Credential reads hang or fail | RAVIS gives a keychain lookup 5 seconds, and its tests cover a keychain that holds nothing, never answers, errors at the operating system, or has no `security` binary — each falls through to environment variables — and the providers listing keeps answering. Not shown: what a provider does when its credential is unreachable rather than absent | If credential reads seem to hang, check the OS keychain directly. After the 5 s wait RAVIS uses the environment instead, so a key set there is the one in use |
| **Trace collector loss** (NERVIS's event hub down) | Producers keep answering; NERVIS's dashboard/trace view goes quiet | **Live once, and now repeatable.** Against a collector killed mid-run (recorded in `STATUS.md`'s Stage 7 entry), a dependent service kept answering at baseline latency and reporting healthy, and the events queued during the outage arrived as one ordered batch on reconnect. RAVIS's route tests now read the same thing off `/ecosystem/health` after a real overflow: the publisher's queue is bounded, what it drops is counted, and an event retried after the hub returns arrives once, not twice | A dead collector should never make a healthy product report itself unready — if it does, that is a regression against tested behaviour, worth escalating specifically |
| **Read-only data directory** | Writes fail | `sirvis doctor` reports a results directory that is not writable, and SIRVIS keeps answering with its results directory read-only. What a *write* does under this exact condition is proved only for a full disk, which is a different error reaching the same code | Fix the directory's permissions, then run `sirvis doctor` to confirm it reads writable again |
| **Cloud provider 401/403/429/5xx** | RAVIS refuses, or moves on from, a cloud provider | Tested through RAVIS's routes: a 401 or 403 on a directly named model reaches you and is final whatever the error body says, and in a pool it counts against that provider only; a bare 500 is not chased across the pool; a run of 503s stops at the configured attempt count; and the health surface shows the refused model as never having worked without opening its circuit breaker, because a wrong key is configuration, not an outage. Not shown: the jittered retry §10 asks for — the 429 evidence proves the status is passed through, not a bounded, jittered retry | A 401/403 is a key problem: fix the credential rather than waiting for failover. Don't count on RAVIS retrying a 429 for you |
| **Network loss** | A candidate provider is unreachable before any response | Tested through RAVIS's routes: a connection that never opens falls through to the next candidate, streaming or not; a failure after the request was sent is not re-sent to the same target; a local model that cannot be reached is refused rather than answered from the cloud; and a world where nothing connects stops at the retry budget. Not shown: loss mid-stream on the transparent path, or a dropped connection between two of this ecosystem's own services | Expect a pool request to move to the next candidate, and a request that must stay local to fail rather than go to the cloud. A stream that breaks partway is the unproved case |
| **Hung local runtime** | A local model runtime accepts a connection and then never answers | SIRVIS reports a stalled runtime with its `TIMEOUT` code rather than `RUNTIME_UNAVAILABLE`, on request and streaming paths, and keeps answering. RAVIS asks a runtime that never answers only up to its budget, and a hung local model is not replaced by a cloud one. Not shown: a *benchmark* against a hung runtime ending | A timeout means the runtime is there and stuck — don't start or retry it, check what it is doing. `RUNTIME_UNAVAILABLE` means it is not running. A benchmark stuck against a hung runtime is the unproved case; check it by hand |
| **Expired credential** | A provider shows a refused key | RAVIS gives a refused key its own state, distinct from an unreachable provider, and publishes it on the providers listing; a directly named model is not replaced when its key is refused, while a pool deliberately moves on to another provider. Not shown: a replaced key being noticed without a restart | Read the providers listing: a refused key shows as such, not as "down". After replacing a key, restart RAVIS if the refusal persists — noticing a rotation live is unproved |
| **code-server loss after it had answered** | The Code tab's embedded editor stops responding | NERVIS marks code-server unreachable (never "stopped", which it says only of something it stopped itself), the services listing publishes that, and the Code tab refuses to frame an editor that is not answering. The capability derived from it deliberately survives the loss, so anything reading the capability rather than the state still looks fine | Read code-server's `state` in `/api/v1/services`, not its capability, and start it again with the start launcher |
| **A service running but not answering** | The menu bar app's headline says "Not answering: RAVIS" (or another service); RAVIS's line reads "not answering", with "RAVIS is running as process N but not answering." and what clears it underneath | Since 12 September the launcher does not launch a second copy over it: it waits the usual start-up time, then prints "RAVIS (process N) is running but not answering. Stop the stack, then start it again.", keeps the process in its PID file and exits 1. Since 13 September `status --json` reports the same thing for any service the launcher owns whose process has been silent longer than that 30-second wait — at start or any time after — and the menu shows it under the service's line. A service with no process at all still reads "not running" | Quit NERVIS (or `tools/run.py stop`), which reaches the hung process through the PID file, then open it again |
| **Bridge collision** (two Clarvis Bridges colliding on an endpoint, `instance_id`, or registry row) | A second window's claim on a live window's id is refused | Tested through NERVIS's routes: a second claim on an `instance_id` that is still answering is refused with a 409 and the live window keeps its lease; an event claiming a registered window must present that window's token; and a window whose lease lapsed is gone rather than probed. Two live windows contesting one *port* are covered by Clarvis's own suite, not by this matrix | A 409 at registration means another live window holds that id — close the duplicate rather than forcing it. A dead window's row goes once its lease lapses (next row) |
| **Stale registry lease** | A closed Clarvis window's row is still listed | Tested through NERVIS's routes: a lapsed lease answers 404 and the window is not asked for its status; a heartbeat brings it back; and a Bridge that restarts and registers the same id again gets one row, a new token and its old token retired. NERVIS's own *service* registry keeps a separate staleness window, which no cell covers | A `live: false` row inside its lease is normal (see "Reading health"). A window whose lease has lapsed should answer 404 — one still read as live after that is a real bug |

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
  NERVIS. It calls no real model and spends nothing, and it exits non-zero if any check fails.
  Since RAVIS 0.23.2 the overhead check sits right at its 5 ms line and can land either side of it
  (see §9.8 in `RAVIS.md`).
- **Long-running**: `caffeinate -i ravis/.venv/bin/python tools/soak_test.py --hours 4` — the
  running stack under one dashboard's reads, plus chat against a private RAVIS, for hours awake:
  whether any service stops answering or restarts, and whether memory, open files, threads or
  read times creep. It samples once a minute into `.run/soak/`, so a run stopped early keeps its
  record; `--report FILE` reads one back.
- **Compatibility matrix**: `python3 tools/check_compatibility.py` prints what this NERVIS
  supports its peers at and fails if any peer ships outside that window — printed rather than
  filed, because a table in a document is a copy that goes stale.
- **Release notes**: `RELEASES.md`, enforced by `tools/check_releases.py` — a version bump with
  no note fails the gate.
