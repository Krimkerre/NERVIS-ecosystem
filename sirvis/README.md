# SIRVIS

*Silicon Inference Runtime Validation & Intelligence System — the evidence plane.*

SIRVIS manages and measures the models on this machine and publishes what it found. It looks after
the models installed in LM Studio (load, unload, show their files, delete, download from Hugging
Face), benchmarks them, and serves the results as evidence with the conditions they were measured
under. It does not route requests: RAVIS routes on what SIRVIS publishes.

That is why one rule shapes every module here: **a value's provenance is never upgraded.**
Something `ESTIMATED` does not become `MEASURED` because it was copied, averaged or stored, and
something `UNKNOWN` does not become a zero because a column needed filling.

Runs on macOS and Linux. On Windows the ecosystem installs inside WSL 2, where SIRVIS runs as it
does on Linux. LM Studio is the one runtime it manages and measures.

**Specification:** [`../SIRVIS.md`](../SIRVIS.md) · **Status:** [`../STATUS.md`](../STATUS.md) ·
**Release notes:** [`../RELEASES.md`](../RELEASES.md)

## What it does

- **Inventory.** This machine (hardware, memory, GPU, thermal state), its runtimes, and every
  installed model, identified by family, variant, runtime and configuration rather than by a name.
  On Linux the machine reading comes from `/proc`, the kernel's graphics devices or `nvidia-smi`,
  and the thermal zones; a figure the machine does not report is `UNKNOWN`, never guessed.
- **Loading, through one owner.** Every load and unload goes through SIRVIS's Resource Manager,
  with leases and reference counts, so two clients holding one model is the ordinary case. The
  loaded-model limit (`SIRVIS_MAX_LOADED_MODELS`, 2 by default) counts every model in this
  machine's memory, including ones loaded by hand in LM Studio or by another machine through
  LM Link. A load whose
  requester gave up while it was loading is unloaded as soon as it lands, and stopping SIRVIS
  unloads what SIRVIS loaded and nothing else.
- **Installed models.** *Show files* opens a model's folder in the file manager. *Delete* moves
  its files to the Trash — `~/.Trash` on a Mac, the freedesktop Trash on Linux — never erasing
  them. It is refused while the model is loaded, and anything another installed model still needs
  is kept.
- **Discover and downloads.** Searches Hugging Face for GGUF and MLX models, anonymously, and lists
  each file's size. A download is checked against free disk first: one that does not fit is
  refused, and one that would leave little room asks for confirmation. LM Studio does the transfer
  and SIRVIS keeps the record, so a reload or a restart loses nothing. A gated model is shown but
  not offered. There is no cancel or pause, because LM Studio publishes neither.
- **Benchmarks.** A queue that runs one benchmark at a time, because two running at once measure
  each other. Each run has warmups, repetitions, streamed timing and memory sampling, and records
  its conditions; a run taken under memory pressure, swap or heat is marked `SUSPECT` with its
  reasons. Tool-call reliability is counted over repeated trials rather than averaged. Runtime Sets
  measure several models alone and then co-resident. Clarvis's own role workloads
  (`clarvis-chat`, `clarvis-agent`) are wrapped from Clarvis's benchmark tools rather than
  rewritten.
- **Evidence and recommendations.** The query API RAVIS reads, with nothing ever keyed as
  model → score. A deleted result leaves a tombstone, so a retracted measurement is
  distinguishable from one never taken. Recommendations are weighted and carry the coverage they
  rest on, so a confident score and a thinly evidenced one look different.
- **LM Link.** Models LM Studio reaches on another of the owner's machines through LM Link are
  marked with that machine's name and listed apart. They do not count against this machine's
  loaded-model limit, have no Show files or Delete, and are not benchmarked here, because the
  numbers would describe the other machine.
- **Saying what is missing.** SIRVIS checks every 10 seconds whether LM Studio answers, with a read
  that loads nothing. While it does not, the affected capabilities are published as degraded or
  unavailable, each with a reason naming what stops. Hugging Face is never probed: it is judged
  by the last real search, and its loss is shown as degraded so that Discover stays usable for the
  search that shows it is back.
- **Events.** Benchmark runs, recommendations and downloads are published to NERVIS's event hub.

The API is under `/api/v1/` (machine, models, runtimes, residency and sessions, Runtime Sets,
benchmark jobs, runs and results, evidence, recommendations, catalogue, downloads), beside the
ecosystem protocol's `/ecosystem/*`. `/ecosystem/capabilities` lists all ten capabilities; each is
`available` while LM Studio and Hugging Face answer.

## Running it

Normally SIRVIS starts with the rest of the stack, from the menu bar app, the tray or
`python3 tools/run.py start` at the repository root, on <http://127.0.0.1:8721>. It starts, and
answers, with no runtime present: a laptop with LM Studio closed is the ordinary case, not a fault.

To run it on its own, from this directory:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ../protocol -e ".[dev]"
.venv/bin/sirvis doctor                   # configuration, machine, database, and what LM Studio says
.venv/bin/sirvis serve                    # http://127.0.0.1:8721
.venv/bin/sirvis token                    # the bootstrap API token
.venv/bin/sirvis token --mint LABEL --scopes "read benchmark"
.venv/bin/sirvis benchmark run examples/basic.yaml --model KEY
.venv/bin/sirvis results latest
.venv/bin/sirvis restore-database --version N
```

`ecosystem-protocol` lives in `../protocol` and not on PyPI, so it has to be installed from there,
as above. `python3 tools/run.py setup` at the repository root builds the shared environment in
`ravis/.venv` with all four packages. `doctor` contacts LM Studio and reports an absent one as a
finding, not a failure.

Configuration is environment variables with a `SIRVIS_` prefix: among them `SIRVIS_PORT`,
`SIRVIS_LMSTUDIO_BASE_URL`, `SIRVIS_LMSTUDIO_CLI_PATH` (LM Studio's `lms`, which loading goes
through), `SIRVIS_LMSTUDIO_MODELS_PATH`, `SIRVIS_MAX_LOADED_MODELS`, `SIRVIS_RESULTS_PATH` and
`SIRVIS_ALLOWED_ORIGINS`. [`src/sirvis/config.py`](src/sirvis/config.py) is the full list, with the
reason for each default.

## Security

- **Loopback only.** A non-loopback bind is refused at startup, and requests addressed to any host
  name but loopback are refused.
- **A token for anything that changes something**, on loopback included, because loopback is not
  a boundary against another local process. Tokens carry scopes: `read`, `runtime` (leases),
  `benchmark` (the queue) and `admin` (downloads, Show files, Delete, Runtime Sets). Reads a peer
  needs to negotiate stay open.
- **An origin check besides the token**, because a browser attaches credentials on a page's behalf.
  Only `http://127.0.0.1:8790` and `http://localhost:8790`, where NERVIS serves the dashboard, are
  allowed by default.

## Checking it

```bash
../ravis/.venv/bin/ruff check src tests    # lint, imports, naming, complexity <= 8
../ravis/.venv/bin/mypy                    # strict types
../ravis/.venv/bin/python -m pytest -q     # no network, no live runtime
```

(With a virtual environment of its own, as above, use `.venv/bin/` instead.) There is no CI: GitHub
Actions is switched off on this repository. `../tools/check_clean_clone.sh` is the full gate. It
clones from GitHub, builds an environment from the packages' own metadata, and runs these checks
for every package, plus the repository's own.

## Two things it deliberately does not share

The ecosystem protocol surface comes from `../protocol`, because a health endpoint that means
something slightly different per service is worse than none.

Storage does not. The runbook (`ECOSYSTEM_RUNBOOK.md` §3) permits sharing the protocol package's
transport types and nothing else, so `storage/database.py` is a sibling of RAVIS's rather than an
import of it. Its two hard-won details — thread-local connections, and `":memory:"` giving every
connection a private database — travel as comments because the code cannot.

## History

SIRVIS began as the measurement engine: machine detection, the LM Studio adapter, single-model and
multi-model benchmarks, the evidence schema and the Resource Manager. Then came the query API that
RAVIS reads, recommendations, Clarvis's role workloads, the benchmark queue (which made
`sirvis.benchmarks.jobs@1` available) and events (`sirvis.events@1`). From 12 September 2026 it
gained Discover and downloads; on 19 September 2026 Show files and Delete, the notices for LM Studio
and Hugging Face being down, Linux machine readings, LM Link awareness, a loaded-model limit that
counts models loaded outside SIRVIS, and abandoned loads given back. On 18 September 2026 the owner
struck the unbuilt comparison analytics, the Playground, a fuller command line and several
installed-model tools. As of that week a benchmark had not yet been run by hand on Linux
(`../STATUS.md`). The milestone table is in `../SIRVIS.md`; the release candidates
`ecosystem-rc1` (18 September 2026) and `ecosystem-rc2` (19 September 2026) are in
`../RELEASES.md`.
