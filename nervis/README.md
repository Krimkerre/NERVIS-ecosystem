# NERVIS

The ecosystem's control plane: a Python service (FastAPI, SQLite) that serves the dashboard,
watches RAVIS, SIRVIS, Clarvis and the local runtimes, runs the event hub and traces, and offers a
general chat through RAVIS. It holds no models and runs no benchmarks. It reads what the other
services publish, over their HTTP APIs, and never opens another service's database.

Runs on macOS and Linux, and on Windows inside WSL 2 (where it is the Linux install).

**Specification:** [`../NERVIS.md`](../NERVIS.md) · **Status:** [`../STATUS.md`](../STATUS.md) ·
**Release notes:** [`../RELEASES.md`](../RELEASES.md) · **Operating it:**
[`../OPERATOR_RUNBOOK.md`](../OPERATOR_RUNBOOK.md) · **Plain-language notes on each product:**
[`knowledge/`](knowledge/) (these are also what NERVIS chat answers from)

## What it does

- **The dashboard.** [`index.html`](index.html), one file with no build step, served by
  `nervis serve`. Four application tabs — NERVIS, RAVIS, SIRVIS and CLARVIS — each with its own
  side menu, which shows a shorter everyday list until *Advanced controls* is switched on. The
  full screen list is `APP_CONFIG` in `index.html`. Every screen reads its live service first; a
  service that is down degrades its cards and never the page. Opened straight from disk with
  nothing running, the page still renders, on labelled mock data.
- **The registry.** Every 20 seconds NERVIS probes RAVIS, SIRVIS and itself over the ecosystem
  protocol (`/ecosystem/{health,identity,capabilities,version}`), and code-server, LM Studio and
  Ollama for reachability. Each dashboard control is negotiated against the capability that backs
  it. "Healthy" means the service answered and reported itself healthy, never a successful
  connection alone. An unconfigured local runtime is shown as absent, not broken.
- **Chat.** A normal client of RAVIS's OpenAI-compatible API: streamed replies, history kept in
  NERVIS's database, the route RAVIS chose for each reply, titles generated as RAVIS background
  calls, attachments and PDFs, slash commands, skills, and memory of earlier conversations (off
  until switched on; what was recalled is shown beside the reply). Chat can *offer* actions — an
  operation from a fixed list, or a short plan of them — and nothing happens until a person
  presses the button. Nothing a model returns becomes an action on its own.
- **RAVIS and SIRVIS, in the same page.** RAVIS's routes, pools, providers, keys, budgets,
  spending, policies, evidence and logs, and its Codex card (sign-in, allowance, tasks, allowed
  sites, new versions). SIRVIS's installed models (load, show files, delete to the Trash),
  Hugging Face search and downloads, runtime, benchmark queue, results and recommendations.
  Writes to RAVIS go through NERVIS, which holds RAVIS's admin credential so the browser never
  does.
- **Events, traces and diagnostics.** An event hub that RAVIS, SIRVIS, Clarvis editor windows and
  NERVIS itself publish to, with retention, filters and an SSE stream with `Last-Event-ID` replay.
  Per-request traces across Clarvis, RAVIS and the provider, with a missing service drawn as a
  named gap rather than an invented span. The launcher's per-service logs with secrets blanked, an
  API inspector for one RAVIS request, and *Analyze*: a bounded, redacted packet shown in full
  before it is sent to a model, with a local-only option.
- **Clarvis.** Each Clarvis editor window whose Bridge is switched on registers itself and is
  shown separately; NERVIS reads its status and events and never resolves one of its approval
  gates. A coding task described in
  chat can be handed over as a file in the workspace, which a person then starts in the editor.
  The CLARVIS tab frames code-server, directly or through NERVIS's `/code/` proxy when that is
  switched on.
- **The rest.** A notification centre; background work (off by default, runs on RAVIS's free
  `ravis/free-api` pool unless another is chosen, and may only notify or propose); settings export
  and import, which never carries a secret; a Files tab over the workspace; spoken replies through
  Fish Audio once a key is entered, used only for text that has already left the machine (the
  browser's own voice otherwise); and start, stop and restart of services — off by default, and
  only for services NERVIS started itself.
- **This machine's load.** `/api/v1/system` samples load average, memory, swap, disk, thermal
  state and the largest processes when asked, with no background sampler. SIRVIS's machine
  reading (hardware, GPU) is shown beside it.

Capabilities are published at `/ecosystem/capabilities`, each `available`, `degraded` or
`unavailable` with a reason, and nothing is advertised that NERVIS cannot do. As of 19 September
2026 the degraded ones are the dashboard (some cards still draw a labelled transcription when
their service has nothing to say), SIRVIS views (a benchmark is polled, not streamed), the API
inspector (RAVIS publishes no request content), the code-server proxy (one graded browser and
host combination) and chat until a RAVIS client credential is configured. Supervision and voice
are unavailable until a service is configured for NERVIS to own, or a voice key is entered.

On a Mac the stack is also run from a menu bar app, and on Linux from a tray icon with the same
menu; both are in [`packaging/`](packaging/) and both call `../tools/run.py`. The root
[`README.md`](../README.md) covers installing and starting them.

## Running it

Normally the whole stack is installed by `./install.sh` at the repository root and started from
the menu bar app, the tray, or `python3 tools/run.py start`. NERVIS then serves the dashboard at
<http://127.0.0.1:8790/>.

Every Python package in the repository shares one virtual environment, `ravis/.venv`, which
`python3 tools/run.py setup` creates with all four packages and their development tools. From
this directory, which is where the launcher keeps `nervis.db`:

```bash
../ravis/.venv/bin/nervis doctor     # configuration, database, capabilities; contacts no peer
../ravis/.venv/bin/nervis serve      # http://127.0.0.1:8790/
../ravis/.venv/bin/nervis restore-database --version N   # put back a pre-migration backup
```

`doctor` needs nothing else running, so it works when everything else is broken. It migrates the
database for real, prints every capability NERVIS declares with its reason, and lists where it
would look for its peers without contacting them.

Configuration is environment variables with a `NERVIS_` prefix, or a `.env` file in the working
directory. The launcher sets the ones the stack needs. The ones most often changed:

| Variable | Default | What it is |
|---|---|---|
| `NERVIS_PORT` | `8790` | Where the service and the dashboard are served |
| `NERVIS_RAVIS_BASE_URL`, `NERVIS_SIRVIS_BASE_URL` | `http://127.0.0.1:8731`, `:8721` | The two peers |
| `NERVIS_RAVIS_CLIENT_CREDENTIAL` | empty | NERVIS's identity to RAVIS; without it, chat titles fall back to the first message |
| `NERVIS_RAVIS_ADMIN_CREDENTIAL` | empty | What NERVIS presents for writes to RAVIS |
| `NERVIS_WORKSPACE_PATH` | empty (off) | The one folder chat and the Files tab may read and write; the launcher points it at `NERVIS workspace` beside the repositories |
| `NERVIS_ALLOWED_HOSTS` | empty | Hosts beyond loopback that NERVIS may probe |
| `NERVIS_CODE_PROXY_ENABLED` | `false` | Serve code-server through `/code/` instead of framing it at its own address |

[`src/nervis/config.py`](src/nervis/config.py) is the full list, with the reason for each default.

## Security

- **Loopback only.** A non-loopback bind is refused at startup, and NERVIS answers only to the host
  names in `NERVIS_SERVED_HOSTS` (loopback by default), which defends against DNS rebinding.
- **Every change needs the page's token.** Any request under `/api/v1/` that is not a read must
  carry a token minted per process and embedded in the page NERVIS serves. A page on another
  origin cannot read it, and a state-changing request from another origin is refused outright.
  The exceptions are writes the page does not make: events, which must carry a sender's
  credential (a secret the launcher gives RAVIS and SIRVIS, or a registered editor window's own
  token), and editor-window registration.
- **Registration is authenticated by a file.** NERVIS writes a secret beside its database with
  mode `0600`, and a Clarvis window proves it runs as the same user by reading it. A window sends
  a port, never a URL; only Clarvis may register this way; and a window's token is returned once
  and never listed.
- **Probing cannot be pointed elsewhere.** Every peer address is checked against an allowlist
  that is loopback-only by default. A host name other than `localhost` is refused rather than
  resolved, because resolving would make the check depend on DNS at the moment it runs.
- **No CORS headers.** The dashboard and NERVIS's own API share one origin. The page's
  cross-origin reads go to RAVIS and SIRVIS, and both allow `http://127.0.0.1:8790` and
  `http://localhost:8790` by default. A page opened from disk sends `Origin: null`, which neither
  allows, so it stays on its mocks.

## Checking it

The Python package, from this directory:

```bash
../ravis/.venv/bin/ruff check src tests    # lint, imports, naming, complexity <= 8
../ravis/.venv/bin/mypy                    # strict types
../ravis/.venv/bin/python -m pytest -q     # no network, no live service
```

The dashboard has its own checks, because almost nothing in a single-file page fails loudly.
`tools/check.py` catches a script that no longer parses, a lost CSS rule and a duplicated API
method. The Node gates listed in [`tools/dashboard_gates.txt`](tools/dashboard_gates.txt) each
load `index.html` in Node and assert one thing: every screen renders with every `fetch` failing
(`render_check.js`), the live readers shape recorded payloads the same way (`shaping_check.js`),
the complexity ratchet (13, target 10) holds, and so on. They need `npm ci` in this directory
once. `honesty_check.js` and `recovery_check.js` need a running NERVIS and are not in the list.

**There is no CI**: GitHub Actions is switched off on this repository. Two things run the gates
instead:

- **The pre-commit hook** in `../tools/githooks`, which the launcher switches on. It runs a secret
  scan on every commit. When a commit touches the dashboard, its checks, chat's knowledge notes or
  the hook, it also runs `check.py`, every gate in `dashboard_gates.txt`, `sandbox_check.js` and
  the knowledge check, on the staged snapshot, with the gates inside a no-network, no-write
  sandbox (`../tools/node_guard.sh`).
- **`../tools/check_clean_clone.sh`**, the full gate. It clones both repositories from GitHub,
  builds an environment from the committed package metadata alone, and runs every package's lint,
  types and tests, the repository gates, the dashboard gates and Clarvis's checks. It tests what
  was pushed, not the working tree.

Before a script-driven edit to `index.html`, read [`docs/PITFALLS.md`](docs/PITFALLS.md): every
defect this page has produced, and the rule that prevents each. [`AGENTS.md`](AGENTS.md) has the
working rules for this directory, and [`docs/WIRING.md`](docs/WIRING.md) the method for moving a
screen from mock data to a real endpoint.

## History

- **The dashboard came first**, as a single-file prototype on mock data, and was wired to the live
  services screen by screen.
- **M0 (25 August 2026)** replaced the static file server that served it with this service, so
  the page gained a database, a stable identity and its own `/ecosystem/*` surface.
- **M1–M12** added telemetry, the registry, the RAVIS and SIRVIS reads, chat, the event hub,
  traces, Clarvis registration and diagnostics, raw logs, the API inspector and Analyze.
- **M13–M19** added the code-server spike and the Code tab, supervision, unified diagnostics, the
  settings backup and the macOS menu bar app. The Linux tray joined it on 18 September 2026.
- **M20–M28** added conversation memory, notifications, proposal outcomes, learned notes,
  planning, background work, the hand-over to Clarvis and the Codex screens.
- **18 September 2026:** the owner struck the rest of the planned command line, a `doctor` that
  contacts its peers, and streamed benchmark progress.
- **Release candidates** `ecosystem-rc1` (18 September 2026) and `ecosystem-rc2` (19 September
  2026) each froze one combination of all five components; `../RELEASES.md` lists them and what
  changed since.

The milestone table with each row's state is `../NERVIS.md` §21. `docs/CURRENT_STATE.md` is a
snapshot of the dashboard from 28 August 2026, kept as history and no longer current.
