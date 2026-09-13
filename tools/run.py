#!/usr/bin/env python3
"""Start, stop and check the ecosystem — detached, so closing a window is safe.

One file behind six launchers, so the sequence cannot drift between macOS, Linux
and Windows.

**Detached is the whole point.** The services are started in their own session
(POSIX) or as detached processes (Windows) with their output redirected to
files, so they outlive the terminal, the launcher, and the person who
accidentally hit ⌘W. That has a consequence worth stating plainly: nothing stops
them when you close the window, so there has to be a way to stop them on
purpose, which is what `stop` is for.

**Ports are not a choice made here.** SIRVIS on 8721 and RAVIS on 8731 are the
services' own defaults *and* the addresses hard-coded in `nervis/index.html`. A
launcher picking different ones would produce a dashboard reporting every
service as offline.

**What it does not start.** LM Studio and Clarvis are separate applications
with their own lifecycles, and §9 puts model loading behind SIRVIS's
Resource Manager. Their state is *reported* instead, because "nothing is
routing" and "no runtime is running" are the same symptom with very
different fixes.

**Ollama is the one named exception**, started here rather than merely
reported. RAVIS's `/v1/embeddings` route needs a local embedding model to
answer NERVIS chat's own knowledge lookups, which makes it load-bearing for
chat rather than an optional runtime choice like every other model this
launcher stays out of — the same argument that keeps LM Studio and Clarvis
untouched cuts the other way here. `nomic-embed-text` is warmed once at startup
(already pulled locally; no download) so the first real question does not
pay that latency.

**code-server is started only if it is installed**, and its absence is a line of
output rather than a failure. It is part of this ecosystem's delivery — the
runbook's port table lists it and Stage 9 grades it — so a launcher that owned
the other three and not this one would leave the Code tab as a thing somebody
had to remember. But it is a separate install that most people running this will
not have, and a launcher that reports NOT ready for a program nobody asked for
teaches its own output to be ignored.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import pathlib
import platform
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "ravis" / ".venv"
RUN = ROOT / ".run"
# The admin secret the launcher mints for RAVIS (§15.1). One path, because the
# health check reads it as well as the credential writes.
RAVIS_ADMIN_TOKEN = RUN / "ravis-admin.token"
# **The owner's own RAVIS admin secret, `admin.owner_cli`** (design §3.7; runbook §2.2). Only
# `codex stop` and `codex reprove` present it, for the menu bar app: stopping a Codex task, and
# starting the file-rules re-test, which spends a turn of the owner's ChatGPT allowance. It is a
# second key rather than the one above because NERVIS holds that one for its dashboard controls,
# and RAVIS lets only this one start the re-test. So it never enters NERVIS's environment, or any
# other service's (`ravis_owner_credential`).
RAVIS_OWNER_TOKEN = RUN / "ravis-owner.token"
PIDFILE = RUN / "services.json"

SIRVIS_PORT = 8721
RAVIS_PORT = 8731
NERVIS_PORT = 8790
OLLAMA_PORT = 11434

# **What Ollama loads a model with, and what RAVIS is told about it.**
#
# Ollama's own default is 4,096 and it does not honour `num_ctx` on the
# OpenAI-compatible path, so a document larger than that is either refused or
# — for a request carrying no image — silently truncated: measured at 2,050
# tokens evaluated out of fifteen thousand, answered confidently. 32,768 is
# enough for the documents chat is actually handed (the blueprint that found
# this is ~25,000 tokens) and costs roughly a gigabyte of key-value cache on a
# 3B model, on top of the model itself.
#
# **The same number reaches RAVIS**, because a router that believes a window
# the runtime does not serve is the defect this pair exists to prevent
# (`ravis/src/ravis/providers/ollama.py`). Set `OLLAMA_CONTEXT_LENGTH` in the
# environment to change it and both follow; they cannot drift apart.
OLLAMA_CONTEXT = int(os.environ.get("OLLAMA_CONTEXT_LENGTH") or 32768)

# What RAVIS's /v1/embeddings warms on startup — small, already pulled on the
# machines this has been run on, and matched to a background lookup rather
# than a chat model. Not configurable yet; becomes an operator setting the
# day a second embedding model is worth choosing between.
EMBEDDING_MODEL = "nomic-embed-text"

# **code-server's own default, not a number this file invented.** The runbook's
# port table declines to assign one — "pinned by its own deployment, proxied,
# never assumed" — so an earlier version of this file continued the ecosystem's
# 87x1 sequence and picked 8741. That was a second number for the same thing:
# NERVIS's `code_server_base_url` defaults to 8080 because that is what somebody
# who installed code-server and ran it will have, so a machine with no config of
# its own would have had the launcher serving on 8741 while the dashboard looked
# at 8080, and the Clarvis tab would report no editor to embed.
#
# Only used when the user has no config of their own. When they do, their
# bind-addr wins and this is never consulted.
CODE_SERVER_PORT = 8080
DASHBOARD = f"http://127.0.0.1:{NERVIS_PORT}/index.html"

WINDOWS = platform.system() == "Windows"

# Runtimes this ecosystem talks to but does not own.
LM_STUDIO = "http://127.0.0.1:1234"

EXTERNAL = [
    ("LM Studio", f"{LM_STUDIO}/v1/models"),
]
# Clarvis is not probed here. A Bridge has no fixed port: each editor window picks one and
# registers it with NERVIS, so NERVIS's registry is what says whether Clarvis is running
# (`_clarvis_bridges`). Until 12 September 2026 this list probed 127.0.0.1:7071, where no
# Bridge listens, and so reported Clarvis as not running whatever was open.

# **Where LM Studio keeps the window it opens a model with.** A model LM Studio has not loaded
# yet is opened at its *default* load length, a figure in LM Studio's own settings that its API
# does not report, and RAVIS needs it to know what a cold model will hold
# (`RAVIS_LMSTUDIO_DEFAULT_CONTEXT`). Ollama is simply started with its number, above. LM Studio
# is not this launcher's to start, so its number is read instead — at every start, so that a
# default changed in LM Studio reaches RAVIS rather than silently disagreeing with it.
#
# LM Studio's folder is `~/.lmstudio` unless `~/.lmstudio-home-pointer` names another: a one-line
# file holding an absolute path, which LM Studio follows and so does `_lmstudio_home`. These are
# only paths; nothing is opened until `lmstudio_default_context` needs it.
LM_STUDIO_HOME = Path.home() / ".lmstudio"
LM_STUDIO_HOME_POINTER = Path.home() / ".lmstudio-home-pointer"
# RAVIS's own figure when it is told none: `lmstudio_default_context` in
# `ravis/src/ravis/config.py`. Only ever printed — when it is the answer, nothing is set.
RAVIS_LMSTUDIO_DEFAULT = 8192


def code_server_binary() -> str:
    """Where code-server is, or an empty string.

    Looked up on PATH rather than at a fixed location, because Homebrew, the
    official install script and npm each put it somewhere different and all
    three put it on PATH. An empty answer is a normal outcome, not an error.
    """
    return shutil.which("code-server") or ""


def ollama_binary() -> str:
    """Where `ollama` is, or an empty string — same lookup as code-server's.

    An empty answer means this launcher starts four services instead of five
    and RAVIS's `/v1/embeddings` has nothing to forward to; that is a
    degraded state with a stated reason, not a launcher failure.
    """
    return shutil.which("ollama") or ""


USER_CONFIG = Path.home() / ".config" / "code-server" / "config.yaml"


def _yaml_value(text: str, key: str) -> str:
    """One `key: value` line out of code-server's config.

    A three-line regex rather than a YAML dependency: this file has four keys,
    code-server writes it itself, and adding pyyaml to a launcher that currently
    needs nothing but the standard library would be a poor trade.
    """
    found = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, re.MULTILINE)
    return found.group(1).strip() if found else ""


def code_server_settings() -> tuple[list[str], int, str]:
    """(extra arguments, port, how it was decided).

    **The user's own config wins, completely.** If `~/.config/code-server/config.yaml`
    exists, this launcher passes no `--config` at all and code-server reads it the
    way it always would — same port, same password, same everything. Starting a
    program is not a licence to reconfigure it, and the first version of this
    function got that wrong in both directions at once: it refused to *write*
    the user's file, which was right, and then ignored what was in it, which
    meant a password they had deliberately set was replaced by a generated one
    they had no reason to expect.

    It also forced `XDG_DATA_HOME` to a private directory, on the theory that
    the ecosystem's extensions should not land in a code-server somebody runs
    for their own work. The immediate consequence was that
    `code-server --install-extension` — which does not read that variable from
    anywhere — installed Clarvis into the default directory while the running
    server looked in the private one, and the extension simply was not there.
    One data directory, the ordinary one, is both simpler and what somebody
    reading the install line would expect it to mean.

    A config is written only when there is none to respect.
    """
    if USER_CONFIG.exists():
        text = USER_CONFIG.read_text(encoding="utf-8")
        bind = _yaml_value(text, "bind-addr")
        port = int(bind.rsplit(":", 1)[-1]) if ":" in bind else CODE_SERVER_PORT
        return [], port, f"your own {USER_CONFIG}"
    return ["--config", str(_written_config())], CODE_SERVER_PORT, "written by this launcher"


def _written_config() -> Path:
    """A config for a machine that has none, kept under `.run/` and rewritten each start.

    **Auth is left on, deliberately.** `--auth none` on loopback is defensible and
    it is also a decision this launcher should not make quietly: the Stage 9
    matrix grades authentication as one of its axes, and a deployment that turned
    it off would make that cell untestable while looking like it passed.
    """
    RUN.mkdir(parents=True, exist_ok=True)
    config = RUN / "code-server.yaml"
    handle = os.open(config, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(
            f"bind-addr: 127.0.0.1:{CODE_SERVER_PORT}\n"
            "auth: password\n"
            f"password: {_generated_password()}\n"
            "cert: false\n"
        )
    return config


def _generated_password() -> str:
    """A password for a machine with no code-server config, minted once and kept.

    Same idiom as `dashboard_token`, and for the same reason: the manual step
    nobody exercises is the step whose instructions go stale. Never consulted
    when the user has a config of their own — that file's password is the one
    that is in effect, and printing a different one would be a lie.
    """
    cached = RUN / "code-server.password"
    try:
        existing = cached.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    minted = secrets.token_urlsafe(18)
    RUN.mkdir(parents=True, exist_ok=True)
    handle = os.open(cached, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(minted + "\n")
    return minted


def venv_bin(name: str) -> Path:
    if WINDOWS:
        return VENV / "Scripts" / f"{name}.exe"
    return VENV / "bin" / name


def ensure_venv() -> None:
    """Create and populate the virtualenv if it is not already there.

    First run has to work without instructions — a launcher that opens a window
    saying "no module named ravis" has failed at the only job it has.
    """
    if venv_bin("python").exists():
        return
    print("First run: creating the virtual environment (a minute or so)…")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    python = str(venv_bin("python"))
    subprocess.run([python, "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    # `ecosystem-protocol` is a local path dependency; pip will not find it on
    # PyPI because it does not live there. First, because the others need it.
    #
    # Installed **with the dev extra**, which costs about 140 MB and is worth it:
    # there is one virtualenv at one path, and the verification commands the
    # repository documents — `.venv/bin/pytest`, `ruff`, `mypy` — live in it. A
    # runtime-only install produced a working application in which every command
    # in STATUS.md's own "verify this yourself" block was missing, which is the
    # kind of gap that only shows up to somebody who just cloned the thing.
    for target in ("protocol", "ravis", "sirvis", "nervis"):
        print(f"  installing {target}…")
        subprocess.run(
            [python, "-m", "pip", "install", "-q", "-e", f"{ROOT / target}[dev]"], check=True
        )


def responds(url: str, timeout: float = 1.5) -> bool:
    """Whether something answers, without caring what it says.

    Any HTTP response counts, a 404 included: the question is "is a server
    listening", and only a connection error or timeout is an absence.

    **Named to RAVIS, since 12 September 2026.** Asked anonymously, each check
    spent one of the sixty requests a minute RAVIS allows all unnamed callers on
    the machine between them — measured at three for a start and a status. The
    launcher presents the admin credential it planted before RAVIS started; every
    other service is asked as before.
    """
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=_named_for(url)), timeout=timeout
        ):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001 - every failure here means "not answering"
        # Deliberately blind. This asks one question — did something answer on
        # that port — and a refused connection, a DNS failure, a timeout and a
        # malformed reply are all the same answer. Narrowing it would mean
        # listing the ways a service can be down, and the list would be wrong
        # the first time a new one appeared.
        return False


def _named_for(url: str) -> dict[str, str]:
    """The launcher's identity for RAVIS, or no header for any other service.

    Read from the cache, never minted: `status` and `stop` must not create a
    credential as a side effect of asking whether something is up, and before the
    first start there is nothing RAVIS would recognise anyway.
    """
    if not url.startswith(f"http://127.0.0.1:{RAVIS_PORT}/"):
        return {}
    try:
        token = RAVIS_ADMIN_TOKEN.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    return {"authorization": f"Bearer {token}"} if token else {}


def _serve_marker(executable: str | Path) -> str:
    """The fragment of a command line that only this service's own `serve` carries.

    The program's file name followed by `serve`: `ravis serve`, `ollama serve`, or
    `ravis.exe serve` on Windows. `stop` looks for it on a recorded PID's command line
    before signalling, so it has to rule out every other process that could be handed
    that number next — and the likeliest ones are not strangers.

    **It used to be the bare name, which matched far more than its service.** Every
    Python service runs from `ravis/.venv`, so `ravis` was on SIRVIS's and NERVIS's
    command lines and on every `ravis/.venv/bin/python -m pytest` or `ruff` run from
    there; `nervis` was on any command naming a path under `nervis/`; `ollama` was on
    each model runner Ollama spawns. A recycled PID held by any of them would have been
    confirmed as ours and stopped. `serve` is part of it because the same programs run
    one-off commands too (`nervis doctor`). Found 12 September 2026 by reading `ps` for
    a running stack.
    """
    return f"{Path(executable).name} serve"


def _services() -> list[tuple[str, list[str], str, dict[str, str], str]]:
    """(name, command, marker, env, health url) for each service we own.

    `marker` is a fragment of the command line that only this service's own serve
    command carries (`_serve_marker`). `stop` uses it to confirm a recorded PID is
    still the process we started rather than whatever the OS handed that number to
    next, and `start` to tell a service that is still booting from a stale record.
    """
    dashboard_origin = f"http://127.0.0.1:{NERVIS_PORT}"

    def env_for(prefix: str, port: int) -> dict[str, str]:
        env = dict(os.environ)
        env[f"{prefix}_HOST"] = "127.0.0.1"
        env[f"{prefix}_PORT"] = str(port)
        # Absolute, because both services default to a *relative* database path
        # and this launcher runs them from the repository root. Left alone, a
        # first run creates empty databases beside this file and the services
        # come up healthy, empty, and disconnected from every benchmark ever
        # recorded — which looks exactly like a working install with no data.
        # Found by the UI sweep on 2026-08-24: 81 runs and 2 runtime sets had
        # been orphaned this way.
        package = prefix.lower()
        env[f"{prefix}_DATABASE_PATH"] = str(ROOT / package / f"{package}.db")
        if prefix == "NERVIS":
            # Where this launcher puts each service's stdout and stderr, so
            # §11.3's log adapters have a documented directory rather than a
            # guessed one. Told to NERVIS here because *this file* is what makes
            # it true: a stack started some other way has different logs, or
            # none, and NERVIS should say so rather than read a path nobody
            # promised it.
            env["NERVIS_RUN_DIRECTORY"] = str(RUN)
            # Named rather than anonymous, which is worth 600 reads a minute
            # instead of 60. See nervis_ravis_credential.
            env["NERVIS_RAVIS_CLIENT_CREDENTIAL"] = nervis_ravis_credential()
            # So a confirmed "bench this model" can actually be carried out.
            # Minted here rather than asked of the operator: see benchmark_token.
            token = benchmark_token()
            if token:
                env["NERVIS_SIRVIS_CLIENT_CREDENTIAL"] = token
            # And the narrower one for deleting a result. Absent is a working
            # state: the button says NERVIS holds no admin credential rather
            # than failing at the call.
            administrative = admin_token()
            if administrative:
                env["NERVIS_SIRVIS_ADMIN_CREDENTIAL"] = administrative
            # §15.1's equivalent one service along: writing a provider key in
            # RAVIS needs an admin credential, and calling the gateway does not
            # grant it. NERVIS holds this so the Credentials screen can write
            # through it rather than the browser writing to RAVIS unauthorised.
            env["NERVIS_RAVIS_ADMIN_CREDENTIAL"] = ravis_admin_credential()
        if prefix == "RAVIS":
            # Point RAVIS at SIRVIS. Without this RAVIS starts healthy with an
            # empty evidence store, its Evidence screen reads zero records, and
            # every routing decision falls back to advertised capabilities — all
            # of which looks like "SIRVIS has no evidence" rather than "nobody
            # told RAVIS where SIRVIS is". §13.4 makes the source optional, so
            # nothing errors; it just quietly does less.
            env["RAVIS_SIRVIS_BASE_URL"] = f"http://127.0.0.1:{SIRVIS_PORT}"
            # Runbook Stage 7. Where RAVIS publishes what it routed, so a
            # request that crosses services shows both lanes on the Traces
            # screen. Empty is a supported state — RAVIS on its own publishes
            # nothing and is not degraded for it — but a launcher that starts
            # all three and wires none of them together would leave the one
            # thing Stage 7 exists for switched off by default.
            env["RAVIS_NERVIS_BASE_URL"] = f"http://127.0.0.1:{NERVIS_PORT}"
            # And at a runtime, when the operator has not named one. RAVIS with
            # no upstream refuses every completion with `upstream_not_configured`,
            # which reaches Clarvis as a 503 and reads there as "the model is
            # down" rather than "nobody told RAVIS where any model is".
            #
            # **This block used to sit in the SIRVIS branch**, four lines below,
            # where it set RAVIS's variable in SIRVIS's environment and therefore
            # did nothing at all. The launcher printed "RAVIS upstream defaulted
            # to LM Studio" on every start while RAVIS had no upstream — a false
            # message that made the real cause invisible. Found from the other
            # end: a Clarvis running under code-server logged the 503 verbatim.
            #
            # Only as a default: anything the operator set in the environment
            # wins, because guessing over a stated choice would be worse than
            # not guessing at all.
            if not env.get("RAVIS_UPSTREAM_BASE_URL") and not env.get("RAVIS_UPSTREAMS"):
                env.update(_default_upstreams())
                env["RAVIS_DEFAULTED_UPSTREAM"] = "1"
            # **The window Ollama is being started with, told to the router that
            # decides on it.** RAVIS reads a resident model's true context from
            # `/api/ps` and needs a number for a cold one; left to its own
            # default it would report 4,096 for models this launcher is about to
            # give 32,768, and route long documents away from a runtime that
            # could hold them. Set only as a default, like everything else here.
            env.setdefault("RAVIS_OLLAMA_DEFAULT_CONTEXT", str(OLLAMA_CONTEXT))
            # **And the window LM Studio opens a model with**, which cannot be set the same
            # way because LM Studio is not started here. It is read out of LM Studio's own
            # settings instead, when that LM Studio is this machine's, and an operator's own
            # value still wins. See `lmstudio_default_context`.
            env.update(_lmstudio_context_environment(env))
            # **What the operator has declared about models nothing has measured.**
            # A hosted vendor's catalogue can leave out a capability its API has —
            # Anthropic's publishes no tool support — and a pool that requires tools
            # then refuses every one of that vendor's models. The file says who
            # declared what, and why. Set only as a default, like the rest.
            env.setdefault(
                "RAVIS_MODEL_CAPABILITIES_PATH", str(ROOT / "ravis" / "operator-capabilities.json")
            )
            # **Where a Codex task may work, and what it must never touch** (runbook §2.2; design
            # §3.7). RAVIS acts on these once it runs Codex tasks, and ignores them until then.
            # The Codex executable is not set here: finding it asks Homebrew, which runs a
            # program, and this table is also built for every `status --json` the menu bar reads.
            # It is added only as RAVIS is launched (`_launch`).
            env.update(_agent_environment(env))
        if package == "sirvis":
            # The same wiring for the second producer. A benchmark mints its own
            # trace, so SIRVIS's own runs form a trace containing only SIRVIS;
            # what makes it appear inside somebody else's is the recommendation
            # endpoint, which is reached over HTTP and inherits the caller's.
            env["SIRVIS_NERVIS_BASE_URL"] = f"http://127.0.0.1:{NERVIS_PORT}"
        if prefix == "NERVIS":
            # Where its peers are. Nothing is probed until M2, but `doctor`
            # prints these and getting them wrong here would make the first
            # thing anyone runs point at the wrong ports.
            env["NERVIS_RAVIS_BASE_URL"] = f"http://127.0.0.1:{RAVIS_PORT}"
            env["NERVIS_SIRVIS_BASE_URL"] = f"http://127.0.0.1:{SIRVIS_PORT}"
            # A directory that exists for this and holds nothing else.
            #
            # `workspace_path` defaults to empty because the setting governs
            # reading a person's files, and an install never asked to do that
            # should not. Pointing it at `<repo>/workspace` does not weaken
            # that: the directory starts empty and the only things in it are
            # ones somebody attached on purpose. What it avoids is a feature
            # that is unreachable unless you already know the variable's name —
            # the button would be there, and every file list would say "off".
            #
            # Somewhere under a person's home is the thing not to do. Anyone who
            # wants that sets NERVIS_WORKSPACE_PATH themselves, which is a
            # decision worth making deliberately.
            workspace = ROOT / "workspace"
            workspace.mkdir(exist_ok=True)
            # **Three rooms, made here so they exist before anything looks.**
            # What somebody handed NERVIS, what NERVIS produced, and the folder
            # the editor opens are three different things, and one directory
            # holding all of them was a heap that had to be read to be
            # understood. NERVIS creates each on demand too; making them at
            # start means the editor's folder is there to be opened rather than
            # created by the first person to try.
            for room in ("import", "export", "library", "clarvis"):
                (workspace / room).mkdir(exist_ok=True)
            env["NERVIS_WORKSPACE_PATH"] = str(workspace)
            # No allow-list: NERVIS serves the dashboard and the dashboard's own
            # API from one origin, so nothing it answers is ever cross-origin.
            return env
        # The dashboard is served from another port, so it is cross-origin to
        # both services. Each default is an empty allow-list, which is why the
        # screens would otherwise silently show nothing.
        env[f"{prefix}_ALLOWED_ORIGINS"] = json.dumps([dashboard_origin])
        return env

    return [
        # Each service's own `serve` rather than bare uvicorn, so its startup
        # configuration check still runs — that is what refuses a non-loopback
        # bind without TLS, and skipping it would make this the one path that
        # bypasses the gate.
        ("SIRVIS", [str(venv_bin("sirvis")), "serve"], _serve_marker(venv_bin("sirvis")),
         _with_results(env_for("SIRVIS", SIRVIS_PORT)),
         f"http://127.0.0.1:{SIRVIS_PORT}/v1/status"),
        ("RAVIS", [str(venv_bin("ravis")), "serve"], _serve_marker(venv_bin("ravis")),
         env_for("RAVIS", RAVIS_PORT), f"http://127.0.0.1:{RAVIS_PORT}/v1/models"),
        # NERVIS's own service since M0, replacing the `http.server` that stood
        # in for it. Same port, same URL, same dashboard file — what changes is
        # that the thing serving it now has a database, an identity and an
        # `/ecosystem/*` surface, which is what makes settings and conversations
        # able to outlive a browser profile.
        ("NERVIS", [str(venv_bin("nervis")), "serve"], _serve_marker(venv_bin("nervis")),
         env_for("NERVIS", NERVIS_PORT),
         f"http://127.0.0.1:{NERVIS_PORT}/api/v1/health"),
    ] + _ollama() + _code_server()


def _code_server() -> list[tuple[str, list[str], str, dict[str, str], str]]:
    """code-server, if this machine has one. An empty list if it does not.

    Appended rather than written into the list above so that everything which
    walks the services — `start`, `stop`, `status` — gains it without knowing it
    is conditional. The alternative is a flag threaded through three functions,
    and the third one to forget it is the bug.
    """
    binary = code_server_binary()
    if not binary:
        return []
    extra, port, _ = code_server_settings()
    # **Clarvis's RAVIS credential, in the environment its extension host inherits.**
    # Read by Clarvis and presented only to this machine's RAVIS (see
    # `clarvis_ravis_credential`). Everything code-server runs can read its
    # environment — terminals and other extensions included — which is the same
    # user on the same machine, and the token names a caller to a loopback-only
    # service; it is not an administrative credential.
    environment = dict(os.environ)
    environment["CLARVIS_RAVIS_CREDENTIAL"] = clarvis_ravis_credential()
    return [(
        "code-server",
        [binary, *extra],
        # **The program name, checked against a real 4.135.0 rather than reasoned
        # about.** An earlier version used the config path, on the theory that it
        # was unique to this deployment and had to still be on the command line
        # for the process to know its port. It is not: code-server re-execs into
        # `lib/node out/node/entry` and its arguments do not survive, so `stop`
        # skipped it as "was not running" and left the port held.
        #
        # `code-server` does survive, in the install path — Homebrew, the
        # standalone installer and npm all put it there. And the narrower marker
        # bought nothing anyway: `_alive` is handed a PID this launcher recorded,
        # so the marker only has to rule out PID *reuse*, never somebody else's
        # code-server.
        #
        # The one marker `_serve_marker` does not build: after the re-exec there is
        # no `serve` argument left to find. Measured 12 September 2026 on a standalone
        # 4.135.0: `~/.local/lib/code-server-4.135.0/lib/node ~/.local/lib/code-server-4.135.0`.
        # It is on no other service's command line and on nothing run from
        # `ravis/.venv`; `test_launcher_lifecycle.py` checks that against this table.
        "code-server",
        environment,
        f"http://127.0.0.1:{port}/healthz",
    )]


def _ollama() -> list[tuple[str, list[str], str, dict[str, str], str]]:
    """Ollama, if this machine has it. An empty list if it does not.

    Same shape and the same reason as `_code_server()`: appended so `start`,
    `stop` and `status` all gain it without being told it is conditional.
    Unlike code-server this one is load-bearing for chat's own knowledge
    lookups (see the module docstring), but the machine still might not have
    it installed, and a launcher that crashed over an optional embedding
    model would make the whole ecosystem depend on the one runtime this file
    otherwise stays out of.
    """
    binary = ollama_binary()
    if not binary:
        return []
    return [(
        "Ollama",
        [binary, "serve"],
        _serve_marker(binary),
        {**os.environ, "OLLAMA_CONTEXT_LENGTH": str(OLLAMA_CONTEXT)},
        f"http://127.0.0.1:{OLLAMA_PORT}/",
    )]


def _warm_ollama() -> bool:
    """One embeddings call, so the model is loaded before chat's first real one.

    Best-effort: `nomic-embed-text` missing is a real, expected state on a machine
    that has not pulled it, and a launcher that failed the whole start over an
    optional model would be worse than the cold-start latency this avoids.
    RAVIS's `/v1/embeddings` makes the same call again on demand either way.
    """
    body = json.dumps({"model": EMBEDDING_MODEL, "prompt": "warm"}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{OLLAMA_PORT}/api/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30.0) as response:
            return bool(response.status == 200)
    except Exception:  # noqa: BLE001 - see responds(): every failure means "not warmed"
        return False


# ── LM Studio's default load window, told to RAVIS ────────────────────────────


def lmstudio_default_context(environ: Mapping[str, str]) -> tuple[str | None, str]:
    """What RAVIS is told LM Studio opens an unloaded model with, and where that came from.

    Returns the value for `RAVIS_LMSTUDIO_DEFAULT_CONTEXT` — `None` leaves it unset — and the
    source in words, for `start` to print. The first of these that applies wins:

    1. **A value already in the environment.** Somebody who set the variable said what they
       want, and guessing over a stated choice is worse than not guessing — the rule every
       default in `env_for` follows. RAVIS itself refuses one that is not a number.
    2. **LM Studio's own settings**, when every LM Studio RAVIS talks to is this machine and the
       file holds a figure in the shape LM Studio writes. A remote LM Studio's default is in its
       own machine's settings; reading this one's would give it a number nobody chose for it.
    3. **Nothing**, so RAVIS uses its own default, `RAVIS_LMSTUDIO_DEFAULT`.

    Only LM Studio's *default* is learned this way. A model given load settings of its own
    inside LM Studio may open at another length, and nothing here reads those.
    """
    stated = environ.get("RAVIS_LMSTUDIO_DEFAULT_CONTEXT")
    if stated is not None:
        return stated, "set by you in RAVIS_LMSTUDIO_DEFAULT_CONTEXT"
    if not _lmstudio_is_this_machine(environ):
        return None, "RAVIS's default: the LM Studio RAVIS uses is not on this machine"
    held = _lmstudio_settings_context(_lmstudio_home() / "settings.json")
    if held is None:
        return None, "RAVIS's default: LM Studio's settings hold no default this launcher can read"
    return str(held), "from LM Studio's settings"


def _lmstudio_context_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """`RAVIS_LMSTUDIO_DEFAULT_CONTEXT` for RAVIS's environment, or nothing to add."""
    told, _ = lmstudio_default_context(environ)
    return {} if told is None else {"RAVIS_LMSTUDIO_DEFAULT_CONTEXT": told}


def lmstudio_context_line(environ: Mapping[str, str]) -> str:
    """`start`'s sentence about it: the number RAVIS will use, and where that number came from."""
    told, source = lmstudio_default_context(environ)
    shown = RAVIS_LMSTUDIO_DEFAULT if told is None else told
    return f"RAVIS's LM Studio context for models not yet loaded: {shown} ({source})."


def _lmstudio_is_this_machine(environ: Mapping[str, str]) -> bool:
    """Whether RAVIS is given an LM Studio, and every one it is given is on this machine.

    Every one, because the setting is a single number for all LM Studio upstreams: with one
    here and one elsewhere, this machine's default would be told about the other one too.
    """
    addresses = _lmstudio_addresses(environ)
    return bool(addresses) and all(_on_this_machine(address) for address in addresses)


def _lmstudio_addresses(environ: Mapping[str, str]) -> list[str]:
    """The address of each LM Studio upstream RAVIS will read out of this environment.

    Read the way RAVIS reads it (`upstream_specs` in `ravis/src/ravis/upstreams.py`, and
    `adapter_for` for the kind): `RAVIS_UPSTREAMS` replaces the singular settings whenever it
    holds anything, and a kind matches whatever its case and spacing. With neither variable
    set, `env_for` declares `_default_upstreams`, whose LM Studio is `LM_STUDIO`.
    """
    if not environ.get("RAVIS_UPSTREAMS") and not environ.get("RAVIS_UPSTREAM_BASE_URL"):
        return [LM_STUDIO]
    declared = environ.get("RAVIS_UPSTREAMS", "").strip()
    if declared:
        return _lmstudio_entries(declared)
    single = environ.get("RAVIS_UPSTREAM_BASE_URL", "")
    return [single] if single and _is_lmstudio(environ.get("RAVIS_UPSTREAM_KIND")) else []


def _lmstudio_entries(declared: str) -> list[str]:
    """The `base_url` of each `lmstudio` entry in a `RAVIS_UPSTREAMS` list.

    A list RAVIS cannot parse stops RAVIS from starting at all, so the answer for one only has
    to be safe: none, which leaves LM Studio's settings unread.
    """
    try:
        entries = json.loads(declared)
    except ValueError:
        return []
    if not isinstance(entries, list):
        return []
    return [
        str(entry.get("base_url") or "").strip()
        for entry in entries
        if isinstance(entry, dict) and _is_lmstudio(entry.get("kind"))
    ]


def _is_lmstudio(kind: object) -> bool:
    """Whether a declared upstream kind names LM Studio, read as `adapter_for` reads it."""
    return str(kind or "").strip().lower() == "lmstudio"


def _on_this_machine(address: str) -> bool:
    """Whether an upstream address is loopback, decided as RAVIS's `is_local_address` decides it.

    Failing closed the same way: a LAN address is somebody else's computer, and one that does
    not parse is not assumed to be this one.
    """
    try:
        host = urllib.parse.urlsplit(address).hostname or ""
        return host in {"localhost", "localhost."} or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _lmstudio_home() -> Path:
    """LM Studio's folder: the one its home pointer names, or `~/.lmstudio` without one."""
    try:
        pointed = Path(LM_STUDIO_HOME_POINTER.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return LM_STUDIO_HOME
    return pointed if pointed.is_absolute() and pointed.is_dir() else LM_STUDIO_HOME


def _lmstudio_settings_context(settings: Path) -> int | None:
    """The default load length in LM Studio's settings file, or None when it holds no usable one.

    **Only `defaultContextLength` is decoded.** The file holds everything else LM Studio
    remembers, none of which is this launcher's business — and LM Studio's credentials sit in
    the same folder — so the text is searched for that one key and only the value after it is
    parsed. The shape is the one LM Studio writes for a length chosen in its settings,
    `"defaultContextLength": {"type": "custom", "value": 8192}`, read off this machine on
    13 September 2026. Anything else (no file, no key, the key twice, another `type`, a value
    that is not a positive whole number) is a shape nobody here has seen, and a guess at it
    could tell RAVIS a window LM Studio does not open; `None` keeps RAVIS's own default.
    """
    try:
        text = settings.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    found = list(re.finditer(r'"defaultContextLength"\s*:\s*', text))
    if len(found) != 1:
        return None
    try:
        held, _ = json.JSONDecoder().raw_decode(text, found[0].end())
    except ValueError:
        return None
    return _custom_length(held)


def _custom_length(held: object) -> int | None:
    """N out of `{"type": "custom", "value": N}` when N is a positive whole number, else None."""
    if not isinstance(held, dict) or held.get("type") != "custom":
        return None
    value = held.get("value")
    # JSON's `true` decodes to a subclass of int in Python, and it is not a window.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _default_upstreams() -> dict[str, str]:
    """Both local runtimes, plus every hosted provider this machine has a key for.

    **Declared, not merely warned about.** RAVIS persists two things per provider
    — the credential and whether it is enabled — and does *not* persist which
    upstreams are declared. That lives only in `RAVIS_UPSTREAMS`. So a restart
    from a shell without it came up healthy, authenticated and silently smaller:
    the key still on disk, the provider simply absent from the Providers screen.

    This function used to be a paragraph of output telling the reader to export
    the variable themselves — the launcher detected the exact problem, printed
    the exact fix, and then did not apply it. Reading a stored credential is
    already what the warning did; declaring the upstream it belongs to is the
    same read with the obvious next step attached.

    Only as a default. Anything the operator set in the environment wins, because
    guessing over a stated choice would be worse than not guessing at all — this
    is only reached when neither `RAVIS_UPSTREAM_BASE_URL` nor `RAVIS_UPSTREAMS`
    is present.

    **No credential is read, only the name of each provider that has one.** The
    file is opened to list its keys and never its values.
    """
    # **Named for the runtime, now that there are two.** One local runtime
    # could be called `local` without ambiguity — this function's own history
    # is why not `default` — but once Ollama joined it, `local` stopped saying
    # which one, the same way `ravis/local/<model>` would have to guess.
    #
    # Both declared unconditionally, though for different reasons. LM Studio
    # always was: whether it happens to be running is exactly what "declared,
    # not discovered" leaves to be found out at request time, same as before.
    # Ollama is declared unconditionally too, but that reflects something
    # actually true rather than a guess — this launcher starts it as part of
    # this same stack, so unlike LM Studio, "is it running" is not an open
    # question by the time this function runs.
    upstreams: list[dict[str, str]] = [
        {"name": "lmstudio", "base_url": LM_STUDIO, "kind": "lmstudio"},
        {"name": "ollama", "base_url": f"http://127.0.0.1:{OLLAMA_PORT}", "kind": "ollama"},
    ]
    for kind in TRANSPARENT_KINDS:
        if kind in _stored_credential_names():
            upstreams.append({"name": kind, "kind": kind})
    return {"RAVIS_UPSTREAMS": json.dumps(upstreams)}


def _stored_credential_names() -> set[str]:
    """Every credential name RAVIS holds, wherever it put the value.

    **Both stores, and the second one is why this exists.** This read
    `credentials.json` alone, which was the whole store until credentials moved
    to the platform keyring — and the migration that moved them emptied that
    file, so the next start declared no hosted upstream at all. RAVIS kept
    answering, because Anthropic and Google are translated adapters registered
    unconditionally; OpenAI and OpenRouter simply stopped existing, silently,
    with the keys still configured and reported as configured.

    Names only. The launcher has no business reading a secret, and does not:
    the keyring index holds names by design, and the file's values are ignored.
    """
    found: set[str] = set()
    config = pathlib.Path.home() / ".config" / "ravis"
    try:
        held = json.loads((config / "credentials.json").read_text())
        found.update(held if isinstance(held, dict) else {})
    except (OSError, ValueError):
        pass
    try:
        indexed = json.loads((config / "credentials.keyring.json").read_text())
        found.update(indexed if isinstance(indexed, list) else [])
    except (OSError, ValueError):
        pass
    return found


def _with_results(env: dict[str, str]) -> dict[str, str]:
    """SIRVIS's raw-result directory, absolute for the same reason as its database.

    §11.9's per-run directory is written relative to the working directory, so a
    launcher running from the repository root would scatter results somewhere
    the CLI never looks.
    """
    env["SIRVIS_RESULTS_PATH"] = str(ROOT / "sirvis" / "results")
    return env


def _agent_environment(env: Mapping[str, str]) -> dict[str, str]:
    """The folders RAVIS's Codex tasks may work in and must stay out of, as RAVIS reads them.

    **Each is a JSON list** — the form RAVIS's settings take a list in, and the only one that can
    carry an entry that is more than a path, such as a folder the owner allows with
    `allow_protected` (design §3.5.1).

    - `RAVIS_AGENT_ALLOWED_ROOTS`: the coding folder this repository sits in. RAVIS lets a task
      work in a project inside it, never in the folder itself.
    - `RAVIS_AGENT_DENIED_PATHS`: this launcher's `.run`, which holds every key it mints; Codex's
      permission profile keeps a task's commands out of it (§4.9).
    - `RAVIS_AGENT_PROTECTED_REPOSITORIES`: this repository and its sibling `clarvis` — the owner's
      decision that Codex never works on the ecosystem itself. RAVIS protects the same two when it
      is started some other way (F-A12); naming them here keeps them protected if that default
      ever moves.

    **What the owner declared is kept**, after the launcher's own entries, so listing a folder
    never drops the coding folder, the `.run` rule or the protection (`_with_owners_entries`).
    """
    coding = ROOT.parent
    ours = {
        "RAVIS_AGENT_ALLOWED_ROOTS": [str(coding)],
        "RAVIS_AGENT_DENIED_PATHS": [str(RUN)],
        "RAVIS_AGENT_PROTECTED_REPOSITORIES": [str(ROOT), str(coding / "clarvis")],
    }
    return {
        name: _with_owners_entries(entries, env.get(name, "")) for name, entries in ours.items()
    }


def _with_owners_entries(ours: list[str], declared: str) -> str:
    """The launcher's entries, then any the owner declared in the same variable, as one JSON list.

    A declared value that is not a JSON list is passed on as written, so RAVIS's own startup check
    names the mistake rather than this launcher quietly throwing a stated choice away.
    """
    if not declared:
        return json.dumps(ours)
    try:
        listed = json.loads(declared)
    except ValueError:
        return declared
    if not isinstance(listed, list):
        return declared
    return json.dumps(ours + [entry for entry in listed if entry not in ours])


#: How long `brew --prefix` may take: it answers in about ten milliseconds, so ten seconds is a
#: Homebrew that is stuck. The same bound RAVIS gives it (`ravis/src/ravis/codex/runtime.py`).
BREW_TIMEOUT_SECONDS = 10


def homebrew_codex_link() -> str:
    """Homebrew's link to Codex, `<brew --prefix>/bin/codex`, or "" when Homebrew cannot say.

    **The link, never the file it leads to** (owner decision D4; design review N6). Homebrew keeps
    each Codex version under `Caskroom/codex/<version>/` and re-points this link on every upgrade.
    RAVIS follows the link at each of its checks, so an upgrade reads as a new build to re-test,
    where a stored `Caskroom` path would read as Codex gone once that version is removed.

    Homebrew is asked rather than assumed to live in `/opt/homebrew`, which is only where it lives
    on Apple silicon. Nothing here runs Codex or checks that the link leads anywhere: whether Codex
    is installed, signed and tested is RAVIS's to find out and report.
    """
    brew = shutil.which("brew")
    if not brew:
        return ""
    try:
        answered = subprocess.run(
            [brew, "--prefix"], capture_output=True, text=True,
            timeout=BREW_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    prefix = answered.stdout.strip()
    if answered.returncode != 0 or not prefix:
        return ""
    return str(Path(prefix) / "bin" / "codex")


def _with_codex_executable(env: dict[str, str]) -> dict[str, str]:
    """RAVIS's environment with `RAVIS_CODEX_EXECUTABLE` naming Homebrew's link (design §3.7).

    **The prefix is found once per start; the link is followed at every check.** Homebrew's prefix
    does not move while the stack runs, so the launcher asks for it as RAVIS launches, and RAVIS
    then only follows the link each time it checks Codex — without running `brew` every time, or
    needing it on its own PATH. It is not asked while `_services()` merely builds the table, which
    every `status --json` does. An executable the owner named wins, and Homebrew is then not asked;
    when Homebrew cannot answer, nothing is set, and RAVIS asks it and reports for itself.
    """
    if env.get("RAVIS_CODEX_EXECUTABLE"):
        return env
    link = homebrew_codex_link()
    return {**env, "RAVIS_CODEX_EXECUTABLE": link} if link else env


def dashboard_token() -> str:
    """A runtime-scoped token for the dashboard, minted once and kept.

    **Why this exists.** §4.5 requires a scope on every mutating endpoint, so
    without a token the dashboard can look but not load a model. Getting one was
    a terminal exercise: change directory, run a CLI, copy 43 characters, paste
    them into a field. That is not a one-click application, and the screen's own
    instructions for doing it were wrong for months — which is what a manual
    step nobody exercises tends to become.

    So the launcher does it. The token is minted on first start and cached at
    mode `0600` beside the logs, then handed to the page in the URL *fragment*.

    **Why the fragment.** Everything after `#` is never sent to a server — not
    to the static file server, not in a `Referer`, not into an access log. The
    page reads it, keeps it in memory, and erases it from the address bar before
    anything else runs. A query string would have been in the server log the
    moment the page loaded.

    This is a local, loopback-only, runtime-scoped credential for a service the
    person running this launcher already controls. It is not a password, and the
    threat it answers — another process on the machine calling a mutating
    endpoint — is unchanged by the launcher being the one to mint it.
    """
    return _sirvis_token("dashboard.token", "nervis-dashboard", "read runtime")


def benchmark_token() -> str:
    """A `benchmark`-scoped token for NERVIS itself, minted the same way.

    **Why NERVIS holds one and the page does not.** §4.5 separates SIRVIS's
    scopes by what they cost: loading a model is `runtime`, occupying the machine
    for ten minutes is `benchmark`. Asking somebody to notice that distinction,
    mint a second token and paste it is the manual step this launcher exists to
    remove — it was reported as "forbidden, scope needed", which is the API being
    exactly right and the product being wrong.

    It goes to NERVIS rather than into the page for the ordinary reason: a
    credential in a browser tab is a credential in every script that tab runs,
    and the page has no need of this one. NERVIS carries out an enumerated
    operation with it and publishes every attempt to the hub (§12).
    """
    return _sirvis_token("nervis-benchmark.token", "nervis-benchmark", "benchmark")


def admin_token() -> str:
    """An `admin`-scoped token for the one operation that needs it.

    **Separate from the benchmark one, and that is the whole point.** SIRVIS's
    `admin` implies every other scope, so a single token covering both would
    quietly give the queue path total access. Deleting a benchmark result is the
    only thing NERVIS does with this, and the Results screen is the only thing
    that asks — the credential itself never leaves NERVIS, exactly as the
    benchmark one does not.

    Minted here for the same reason as the others: the alternative is telling
    somebody to open a terminal, work out which of four scopes a delete button
    needs, and paste 43 characters.
    """
    return _sirvis_token("nervis-admin.token", "nervis-admin", "admin")


def nervis_ravis_credential() -> str:
    """The secret NERVIS presents to RAVIS, minted here and known to both.

    **Why NERVIS needs one at all.** RAVIS gives an anonymous caller sixty reads
    a minute and a named one six hundred, and NERVIS is the busiest reader it
    has — the dashboard polls several screens through NERVIS's peer reader, and
    chat reads the catalogue on every turn that mentions models. That tripped
    the limit routinely, and a rate-limited read is indistinguishable from an
    empty service at the screen: "no models" about a machine holding 591.

    Minted locally rather than fetched, because RAVIS has no minting endpoint —
    a client credential is simply a stored secret whose name begins with
    `client.`, and both halves have to know the same string. This writes it
    once at `0600` and hands it to both sides on every start.
    """
    return _cached_client_secret("nervis-ravis.token")


def clarvis_ravis_credential() -> str:
    """The secret Clarvis presents to RAVIS, handed over through code-server's environment.

    **Why Clarvis needs one too.** Every unnamed caller on this machine shares
    RAVIS's sixty requests a minute. Measured on 11 September 2026, the dashboard
    open in a browser reads RAVIS directly about twenty-four times a minute on its
    own, which left a Clarvis build roughly thirty-six before RAVIS turned it away.
    Named, Clarvis gets six hundred of its own.

    Clarvis reads it from `CLARVIS_RAVIS_CREDENTIAL` and presents it only to a RAVIS
    on this machine, for a `ravis/` model, and only when no key is stored in the
    editor — see Clarvis's `src/model/ravisCredential.ts`. RAVIS knows it as the
    client `clarvis`.
    """
    return _cached_client_secret("clarvis-ravis.token")


def _cached_client_secret(filename: str) -> str:
    """A client secret minted once, cached at `0600` under `.run`, reused on every start."""
    return _minted_secret(RUN / filename)


def _minted_secret(cached: Path) -> str:
    """The secret cached at `cached`, minted at `0600` first when there is none yet."""
    held = _held_secret(cached)
    if held:
        return held
    token = secrets.token_urlsafe(32)
    cached.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(cached, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(token + "\n")
    return token


def _held_secret(cached: Path) -> str:
    """The secret cached at `cached`, or "" when there is none. Never mints one.

    The Codex commands read their keys this way. RAVIS learns its admin keys only as the stack
    starts (`teach_ravis_the_admin_credential`), so a key minted by a command would be refused —
    and would then be the key the next start taught RAVIS, in place of nothing anybody asked for.
    """
    try:
        return cached.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ravis_admin_credential() -> str:
    """The `admin.`-prefixed secret §15.1 requires for a credential write.

    Minted and cached exactly like NERVIS's client credential, and stored
    through `ravis credential` rather than the HTTP endpoint — that endpoint now
    requires this very credential, so the first one cannot be written through
    it. The command line is a different authority: whoever runs the launcher
    already owns the config directory the store lives in.
    """
    return _minted_secret(RAVIS_ADMIN_TOKEN)


def ravis_owner_credential() -> str:
    """The owner's command-line secret for RAVIS, `admin.owner_cli` (design §3.7; F-A3, F-A11).

    Minted and cached like the admin secret above, taught to RAVIS the same way, and read back only
    by `codex stop` and `codex reprove`. **No service is handed it.** NERVIS keeps `admin.launcher`
    for its dashboard controls, and RAVIS refuses that one on the file-rules re-test, so the one key
    that can spend the owner's allowance on a re-test stays with the owner's own command line. Two
    keys also keep the menu bar's Stops apart from the dashboard's in RAVIS's rate limit and audit.
    """
    return _minted_secret(RAVIS_OWNER_TOKEN)


def teach_ravis_the_admin_credential() -> str:
    """Put the admin secret in RAVIS's own store, before anything needs it.

    Runs before RAVIS starts rather than after: the CLI writes the file the
    gateway reads at startup, and doing it while the gateway is running would be
    two processes writing one JSON document. Nothing is racing here because
    nothing else is up yet.
    """
    return _plant_admin_credential("admin.launcher", ravis_admin_credential())


def teach_ravis_the_owner_credential() -> str:
    """Put the owner's command-line secret in RAVIS's store as `admin.owner_cli`.

    The same way, at the same moment and for the same reason as the admin secret above.
    """
    return _plant_admin_credential("admin.owner_cli", ravis_owner_credential())


def _plant_admin_credential(name: str, secret: str) -> str:
    """Store one `admin.` credential through `ravis credential`, and say what happened.

    The secret goes in on stdin, never as an argument, where any process listing would show it.
    """
    if not secret:
        return "could not be minted"
    executable = ROOT / "ravis" / ".venv" / "bin" / "ravis"
    if not executable.exists():
        return "ravis is not installed"
    try:
        finished = subprocess.run(
            [str(executable), "credential", name],
            input=secret, text=True, capture_output=True, timeout=20,
            # The return code is read below rather than raised on: a refusal
            # here is a line the launcher prints, not a reason to stop starting
            # the ecosystem.
            check=False,
        )
    except Exception as failure:  # noqa: BLE001 - a launcher never dies of this
        return f"could not be stored: {failure}"
    return "stored" if finished.returncode == 0 else f"refused: {finished.stderr.strip()[:60]}"


def teach_ravis_the_credential() -> str:
    """Store NERVIS's and Clarvis's credentials in RAVIS, so each side agrees on its own.

    Idempotent: a PUT replaces, and each value is the same one every time because
    it is cached. Failure is reported and not fatal — an unnamed caller still
    works, it is merely rate-limited like any other anonymous one, and a launcher
    that refused to finish over a rate limit would be worse than the problem it
    was fixing.

    Returns a short description of what happened, for the start banner.
    """
    clients = (("nervis", nervis_ravis_credential), ("clarvis", clarvis_ravis_credential))
    return "; ".join(f"{name} {_teach_ravis(name, minted())}" for name, minted in clients)


def _teach_ravis(name: str, secret: str) -> str:
    """Store one `client.<name>` credential in RAVIS, and say what happened."""
    if not secret:
        return "could not be minted"
    body = json.dumps({"secret": secret}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{RAVIS_PORT}/api/v1/providers/credentials/client.{name}",
        data=body, method="PUT",
        headers={
            "content-type": "application/json",
            # §15.1: this endpoint no longer takes a loopback bind as
            # authorization, so the launcher presents the admin credential it
            # planted before RAVIS started.
            "authorization": f"Bearer {ravis_admin_credential()}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as answered:
            if answered.status >= 400:
                return f"RAVIS answered HTTP {answered.status}"
    except urllib.error.HTTPError as failure:
        return f"RAVIS refused it: HTTP {failure.code}"
    except Exception as failure:  # noqa: BLE001 - a launcher never dies of this
        return f"RAVIS could not be told: {failure}"
    return "stored"


def _sirvis_token(filename: str, label: str, scopes: str) -> str:
    """Mint one SIRVIS token, cache it at 0600, and reuse it next time.

    Cached per file rather than per label so two tokens with different scopes
    can coexist: minting on every start would leave a pile of live credentials
    nobody can account for, which is worse than the manual step it replaced.
    """
    cached = RUN / filename
    try:
        existing = cached.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    env = dict(os.environ)
    env["SIRVIS_DATABASE_PATH"] = str(ROOT / "sirvis" / "sirvis.db")
    try:
        minted = subprocess.run(
            [str(venv_bin("sirvis")), "token", "--mint", label, "--scopes", scopes],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if minted.returncode != 0:
        return ""
    # The token is the last non-empty line; everything above it is the label,
    # the scopes and the warning that it is printed once.
    lines = [line.strip() for line in minted.stdout.splitlines() if line.strip()]
    token = lines[-1] if lines else ""
    if not token or " " in token:
        return ""
    RUN.mkdir(parents=True, exist_ok=True)
    handle = os.open(cached, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(token + "\n")
    return token


def dashboard_url() -> str:
    """The dashboard address, with the token and a cache key.

    The `v=` is the dashboard file's own modification time. A browser that has
    the page cached will otherwise happily keep serving it after an edit, which
    is not a theoretical concern — it cost a debugging detour while this handoff
    was being written, with the new code served and the old code running.
    """
    token = dashboard_token()
    try:
        version = int((ROOT / "nervis" / "index.html").stat().st_mtime)
    except OSError:
        version = 0
    return f"{DASHBOARD}?v={version}" + (f"#token={token}" if token else "")


def _spawn_detached(command: list[str], env: dict[str, str], log: Path) -> int:
    """Start one service so that it outlives this process and its terminal."""
    RUN.mkdir(parents=True, exist_ok=True)
    handle = log.open("ab")
    if WINDOWS:
        # These flags exist on `subprocess` only at runtime on Windows; typeshed
        # only exposes them under a literal `sys.platform == "win32"` check, which
        # `WINDOWS = platform.system() == "Windows"` does not give mypy to narrow
        # on. Real and guarded, not a workaround for a real bug.
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                 | subprocess.DETACHED_PROCESS)  # type: ignore[attr-defined]
        process = subprocess.Popen(
            command, cwd=str(ROOT), env=env, stdout=handle, stderr=handle,
            stdin=subprocess.DEVNULL, creationflags=flags,
        )
    else:
        # A new session, so the service has no controlling terminal and does not
        # receive the SIGHUP that closing one sends.
        process = subprocess.Popen(
            command, cwd=str(ROOT), env=env, stdout=handle, stderr=handle,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    return process.pid


def _alive(pid: int, marker: str) -> bool:
    """Whether this PID is still the process we started.

    The marker check is what makes `stop` safe. PIDs are reused, and a recorded
    number that now belongs to something else must not be killed because a file
    on disk says it was ours.
    """
    # **No marker confirms nothing.** An empty string is contained in every command
    # line, so a record without one — hand-edited, or written by anything but
    # `start` — would otherwise vouch for whatever process holds that PID now.
    if not marker:
        return False
    if WINDOWS:
        found = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, check=False,
        )
        if str(pid) not in found.stdout:
            return False
        try:
            listed = subprocess.run(
                ["powershell", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                capture_output=True, text=True, check=False,
            )
        except OSError:
            # powershell.exe missing, not on PATH, or blocked (AppLocker and
            # similar locked-down installs do this). We cannot positively
            # confirm the PID is ours, so fail closed the same way a PID that
            # no longer exists does: treat it as not-alive rather than either
            # killing an unrelated process or crashing `stop` mid-loop.
            return False
        # Windows quotes a program path containing a space, which puts a `"` between
        # `ravis.exe` and `serve` and would hide the marker (`_serve_marker`).
        return marker in listed.stdout.replace('"', "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    listed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True, text=True, check=False,
    )
    return marker in listed.stdout


def _recorded() -> dict[str, dict[str, object]]:
    try:
        with PIDFILE.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _pid_and_marker(record: object, current: str | None) -> tuple[int, str]:
    """The PID a PID-file record names, and the marker to confirm that process with.

    **Today's marker, not the recorded one**, whenever the record has a marker at all
    and the service is still in `_services()`. A PID file outlives the launcher that
    wrote it, and one written before 12 September 2026 carries the old bare-name
    markers (`ravis`, `nervis`) that matched far more than their own service
    (`_serve_marker`). Confirming against those would keep that weakness alive for as
    long as an old file survives. Confirming against today's lets the first `stop`
    after upgrading still stop what the old `start` launched — the command is the same,
    so its command line carries the new marker as well — while a recycled PID that
    only the old marker matched is left alone.

    A record with no marker is not upgraded. Every version of `start` wrote one, so a
    record without it came from somewhere nobody can vouch for, and `_alive` confirms
    nothing with an empty marker. A service no longer in the table (its program has
    left PATH) is confirmed against what was recorded, since that is all there is.
    """
    if not isinstance(record, dict):
        return 0, ""
    raw_pid = record.get("pid", 0)
    pid = raw_pid if isinstance(raw_pid, int) else 0
    recorded = record.get("marker")
    if not isinstance(recorded, str) or not recorded:
        return pid, ""
    return pid, current or recorded


#: Network shares to mount before the stack starts, as URLs macOS understands:
#: `NERVIS_FILE_MOUNTS="smb://synology.local/nervis"`, comma-separated for more
#: than one. Empty is every deployment that keeps everything on one machine.
#:
#: **The workspace itself stays local, deliberately.** A share is somewhere to
#: *put* things from the Files tab, not somewhere to run from: a 64KB write
#: measured 38ms over SMB against 0.09ms locally, and an editor or a chat
#: writing at that rate is a stack that feels broken for no benefit.
FILE_MOUNTS = [
    url.strip() for url in os.environ.get("NERVIS_FILE_MOUNTS", "").split(",") if url.strip()
]


def configured_share() -> str:
    """The share NERVIS was pointed at on its Settings screen, or nothing.

    **Read out of NERVIS's own database rather than kept here.** A share typed
    into Settings and a share named in a launcher variable are two places to
    say one thing, and the pair drifts the first time somebody edits the
    convenient one. The screen writes `files.share`; this reads it; an empty or
    absent value means there is nothing to mount, which is every machine that
    was never told about a NAS.

    Read-only and forgiving: the database may not exist yet on a first run, and
    a launcher that refused to start over a missing settings table would be
    trading a feature nobody configured for the whole stack.
    """
    database = ROOT / "nervis" / "nervis.db"
    if not database.is_file():
        return ""
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=2) as held:
            row = held.execute(
                "SELECT value FROM setting WHERE key = 'files.share'"
            ).fetchone()
    except sqlite3.Error:
        return ""
    if not row:
        return ""
    try:
        return str((json.loads(row[0]) or {}).get("url") or "").strip()
    except (TypeError, ValueError, AttributeError):
        return ""


def mount_share(url: str) -> str:
    """Mount one share, or say why not. Empty means it is there.

    **Mounted at start rather than by a login item, because start is when it is
    needed.** A login item mounts at login; the stack is started whenever, and
    a share that dropped out over lunch is one the next `start` should bring
    back rather than one somebody has to notice and fix by hand.

    `osascript` rather than `mount_smbfs`: it uses the Keychain, so the
    password stays where the operator already put it instead of in a launcher,
    a plist, or a file under `/etc`.
    """
    if sys.platform != "darwin":
        return f"{url}: mounting is only wired for macOS"
    where = Path("/Volumes") / url.rstrip("/").rsplit("/", 1)[-1]
    if os.path.ismount(where):
        return ""
    script = f'try\nmount volume "{url}"\nend try'
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], check=False,
                       capture_output=True, timeout=45)
    except (OSError, subprocess.SubprocessError) as failure:
        return f"{url} could not be mounted: {failure}"
    # Asked again rather than trusting the exit status: `osascript` answers 0
    # for a `try` block that swallowed the failure, and what matters is whether
    # something is mounted there now.
    return "" if os.path.ismount(where) else f"{url} did not mount"


def _launch(running: dict[str, bool], recorded: dict[str, dict[str, object]]) -> dict[str, int]:
    """Launch each service not answering, unless its recorded process is still ours.

    Adds each launch to `recorded`, and returns the services left to finish booting on
    their own: name to the PID already recorded for it.

    **Not answering is not the same as not running.** A service can be alive and still
    booting — started a moment earlier by another `start`, from the other launcher or the
    menu bar app, which writes the PID file before it waits. Before 12 September 2026
    this launched a second copy on top of it and overwrote its record: whichever copy
    lost the race for the port exited, and when the loser was the new one, the process
    still serving was named nowhere `stop` could find it. So a recorded PID that is alive
    and still carries its service's marker is left to boot. It gets the same wait as a
    fresh launch, and its record stays.
    """
    waiting: dict[str, int] = {}
    for name, command, marker, env, _ in _services():
        if running.get(name):
            print(f"  {name} already running")
            continue
        pid = _still_ours(recorded.get(name), marker)
        if pid:
            waiting[name] = pid
            print(f"  {name} already started (pid {pid}) and not answering yet; waiting for it")
            continue
        if name == "RAVIS":
            # Where Homebrew keeps Codex, asked only now that RAVIS is really being launched
            # (`_with_codex_executable`).
            env = _with_codex_executable(env)
        pid = _spawn_detached(command, env, RUN / f"{name.lower()}.log")
        recorded[name] = {"pid": pid, "marker": marker}
        print(f"  {name} started (pid {pid})")
    return waiting


def _still_ours(record: object, current_marker: str) -> int:
    """The PID a record names, if that process is alive and still carries its marker; else 0."""
    pid, marker = _pid_and_marker(record, current_marker)
    return pid if pid and _alive(pid, marker) else 0


#: How long `start` waits for each service to answer — and so how old a silent process must
#: be before `status --json` calls it hung rather than still booting (`_problem`).
START_WAIT_SECONDS = 30.0


def _elapsed_seconds(etime: str) -> float | None:
    """`ps`'s elapsed time, `[[dd-]hh:]mm:ss`, in seconds; None when it is not that shape."""
    days, _, clock = etime.strip().rpartition("-")
    fields = clock.split(":")
    if len(fields) not in (2, 3) or not all(part.isdigit() for part in [days or "0", *fields]):
        return None
    seconds = 0
    for field in fields:
        seconds = seconds * 60 + int(field)
    return float(int(days or "0") * 86_400 + seconds)


def _process_age(pid: int) -> float | None:
    """How long this process has been running, in seconds; None where that cannot be read.

    Windows has no `ps`. There the answer is None, and `_problem` says nothing rather than
    guess whether a silent service is hung or still booting.
    """
    if WINDOWS:
        return None
    listed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "etime="], capture_output=True, text=True, check=False,
    )
    return _elapsed_seconds(listed.stdout)


def _problem(
    name: str, marker: str | None, answering: bool, recorded: dict[str, dict[str, object]],
) -> str | None:
    """What is wrong with a service this launcher owns, beyond not answering; else None.

    **Not answering has two causes that want different things done.** A service that is
    not running starts when the stack does. One that is running and silent — hung, or
    stuck — has to be stopped first, and `start` will not launch a second copy over it
    (`_launch`). Until 13 September 2026 the menu bar app showed both as "not running",
    and the sentence naming the process reached only `.run/menubar.log`, where nobody
    looking at the menu would find it.

    Named only once the process is older than `start`'s own wait. Younger, it may still be
    booting, and the menu is read all through a start; an alarm there would be false every
    time. A process whose age cannot be read is not named, for the same reason. The
    process must still be ours by `_still_ours`, so a recycled number never is.
    """
    if answering or marker is None:
        return None
    pid = _still_ours(recorded.get(name), marker)
    age = _process_age(pid) if pid else None
    if age is None or age < START_WAIT_SECONDS:
        return None
    return f"running as process {pid} but not answering"


def _readiness(name: str, answering: bool, booting_pid: int) -> str:
    """`start`'s verdict on one service once its wait is over, in words somebody can act on."""
    if answering:
        return "ready"
    if booting_pid:
        # Not killed: it may be moments from answering, and a service killed mid-boot is
        # the half-written journal `_ask_then_force` exists to avoid. Not doubled either
        # (`_launch`). What is left is to name the process and say what clears it.
        return (f"NOT ready — {name} (process {booting_pid}) is running but not answering."
                " Stop the stack, then start it again.")
    return f"NOT ready — see .run/{name.lower()}.log"


def start() -> int:
    ensure_venv()
    for url in dict.fromkeys([*FILE_MOUNTS, configured_share()]):
        if not url:
            continue
        trouble = mount_share(url)
        print(f"  share: {trouble or f'{url} mounted'}")
    # **Before anything is up.** The admin credential has to exist in RAVIS's
    # store before RAVIS reads that store at startup, and writing it afterwards
    # would be a second process editing a JSON file the gateway already holds.
    planted = teach_ravis_the_admin_credential()
    # The owner's command-line key for Codex, at the same moment for the same reason. No service
    # is handed it: only `codex stop` and `codex reprove` read it back (`ravis_owner_credential`).
    owner_key = teach_ravis_the_owner_credential()
    running = status(quiet=True)
    if all(running.values()):
        print("Already running.")
        print(f"  Named callers to RAVIS: {teach_ravis_the_credential()}")
        print(f"Dashboard: {DASHBOARD}")
        webbrowser.open(dashboard_url())
        return 0

    print("Starting (detached — closing this window will not stop them)…")
    recorded = _recorded()
    booting = _launch(running, recorded)
    RUN.mkdir(parents=True, exist_ok=True)
    with PIDFILE.open("w", encoding="utf-8") as handle:
        json.dump(recorded, handle, indent=2, sort_keys=True)

    print("\nWaiting for them to answer…")
    ready = True
    for name, _, _, _, url in _services():
        deadline = time.monotonic() + START_WAIT_SECONDS
        while time.monotonic() < deadline and not responds(url):
            time.sleep(0.4)
        answering = responds(url)
        ready = ready and answering
        print(f"  {name:<11} {_readiness(name, answering, booting.get(name, 0))}")

    # After RAVIS answers, because storing a credential is a request to it. Both
    # halves already hold the same string — this is the half RAVIS keeps.
    print(f"\nRAVIS admin credential (§15.1): {planted}")
    print(f"Owner's command-line credential for Codex (admin.owner_cli): {owner_key}")
    print(f"Named callers to RAVIS: {teach_ravis_the_credential()}")

    if ollama_binary():
        if responds(f"http://127.0.0.1:{OLLAMA_PORT}/"):
            print(f"\nOllama: warming {EMBEDDING_MODEL} for RAVIS's /v1/embeddings…")
            if _warm_ollama():
                print("  warmed")
            else:
                print(f"  not warmed — {EMBEDDING_MODEL} may not be pulled;"
                      f" run: ollama pull {EMBEDDING_MODEL}")
    else:
        print("\nOllama is not installed, so RAVIS's /v1/embeddings has nothing to use.")
        print("  brew install ollama && ollama pull nomic-embed-text   (then run this again)")

    if code_server_binary():
        extra, port, source = code_server_settings()
        print(f"\ncode-server: http://127.0.0.1:{port}")
        print(f"  config     {source}")
        if extra:
            # Only when this launcher wrote the config. When the user has one of
            # their own, the password in it is theirs and printing anything here
            # would either leak it or contradict it.
            print(f"  password   {_generated_password()}")
            print(f"  also in    {RUN / 'code-server.password'} (mode 0600)")
        print("  install Clarvis into it with:")
        print(f"    code-server --install-extension {ROOT.parent / 'clarvis' / 'clarvis.vsix'}")
    else:
        # Named, not silent. Stage 9 grades code-server, so "it is not here" is
        # a fact somebody needs, and a launcher that simply omitted the line
        # would look identical to one where it had started.
        print("\ncode-server is not installed, so it was not started.")
        print("  brew install code-server        (then run this again)")

    print("\nRuntimes this ecosystem uses but does not start:")
    for name, url in EXTERNAL:
        print(f"  {name:<10} {'answering' if responds(url, 1.0) else 'not running'}")
    # What RAVIS was told LM Studio opens an unloaded model with, and where the number came
    # from. A figure read out of another application's settings is one somebody should see.
    print(f"\n{lmstudio_context_line(os.environ)}")

    if not os.environ.get("RAVIS_UPSTREAM_BASE_URL") and not os.environ.get("RAVIS_UPSTREAMS"):
        declared = _default_upstreams()
        named = [one["name"] for one in json.loads(declared["RAVIS_UPSTREAMS"])]
        print(f"\nRAVIS upstreams defaulted to: {', '.join(named)}.")
        print(f"  LM Studio at {LM_STUDIO}, Ollama at http://127.0.0.1:{OLLAMA_PORT};")
        print("  hosted ones are the providers this machine already holds a key for.")
        print("  Set RAVIS_UPSTREAMS to override.")
    print(f"\nDashboard: {DASHBOARD}")
    print("Stop them with the stop launcher next to this one.")
    if ready:
        # The fragment is never sent to a server. The page consumes it and
        # clears the address bar immediately.
        webbrowser.open(dashboard_url())
    return 0 if ready else 1


#: Seconds `stop` waits between asking a service to stop and forcing it; six unless named.
#:
#: **SIRVIS waits twelve.** Since 12 September 2026 its shutdown releases every session and
#: unloads every model it loaded: up to 2.5 s unloading and up to 3 s draining events, and an
#: `lms unload` that hangs holds it until that CLI's own 10 s timeout. Forced at six, a stop
#: mid-unload could leave a model loaded in LM Studio and held by nobody — the state that
#: shutdown exists to prevent. The others do no such work on the way down, and a longer wait
#: for them would only delay forcing one that is stuck.
GRACE_BEFORE_KILL = {"SIRVIS": 12.0}
DEFAULT_GRACE_BEFORE_KILL = 6.0


def _ask_then_force(name: str, pid: int, marker: str) -> None:
    """Ask one confirmed service to stop, and force it only if it outlives its wait.

    Ask first. A service killed outright can leave a half-written SQLite journal, and
    SIRVIS a model half-unloaded (`GRACE_BEFORE_KILL`).

    Every check during the wait confirms the marker again, so the forced kill can only
    reach the process that was asked, never one that inherited its number meanwhile.
    """
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, check=False)
    else:
        os.kill(pid, 15)
    deadline = time.monotonic() + GRACE_BEFORE_KILL.get(name, DEFAULT_GRACE_BEFORE_KILL)
    while time.monotonic() < deadline and _alive(pid, marker):
        time.sleep(0.2)
    if _alive(pid, marker):
        print(f"  {name} did not stop; forcing")
        if WINDOWS:
            subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"],
                           capture_output=True, check=False)
        else:
            os.kill(pid, 9)


def stop() -> int:
    recorded = _recorded()
    if not recorded:
        print("Nothing recorded as running.")
        return 0
    print("Stopping…")
    # Read once: its markers confirm each record below, and its health URLs are asked at the end.
    services = _services()
    markers = {name: marker for name, _, marker, _, _ in services}
    # The menu bar app's models first, while SIRVIS can still be asked to unload them. A
    # current SIRVIS would unload them on its way down anyway; `_release_menu_sessions`
    # says why this stays.
    if responds(f"http://127.0.0.1:{SIRVIS_PORT}/ecosystem/health", 1.0):
        _release_menu_sessions()
    for name, record in sorted(recorded.items()):
        pid, marker = _pid_and_marker(record, markers.get(name))
        if not marker:
            print(f"  {name} is recorded without a marker, so it cannot be confirmed as ours;"
                  " left alone")
            continue
        if not pid or not _alive(pid, marker):
            print(f"  {name} was not running")
            continue
        _ask_then_force(name, pid, marker)
        print(f"  {name} stopped")
    PIDFILE.unlink(missing_ok=True)

    # **Checked, because the loop above can be wrong and say nothing.** A
    # recorded PID whose command line no longer carries its marker is skipped as
    # "was not running" — which is right when the PID was reused and wrong when
    # the process simply re-exec'd itself out of recognition. Both print the same
    # line, and in the second case this printed "Stopped." over a service still
    # holding its port. Observed, not imagined: it is what a stub code-server did
    # the first time this path ran.
    still = [name for name, _, _, _, url in services if responds(url, 1.0)]
    if still:
        print("\nStill answering after stop: " + ", ".join(still))
        print("  Something is holding those ports that this launcher did not start,")
        print("  or did not recognise. `ps aux | grep -E 'ravis|sirvis|nervis|code-server'`")
        return 1
    print("Stopped.")
    return 0


# Hosted providers reachable as a transparent upstream, and the kind name that
# knows its own address. A credential for one of these is only usable if the
# upstream is *declared* — RAVIS persists the key and not the declaration — which
# is why `_default_upstreams` reads this list rather than trusting the store.
TRANSPARENT_KINDS = ("openrouter", "openai", "deepseek", "xai")


def status(quiet: bool = False) -> dict[str, bool]:
    """Which services are answering. Health, not the PID file.

    A PID file says what was started; a health check says what is serving. When
    they disagree the health check is right, which is why `start` consults this
    rather than the file before deciding what to launch.
    """
    answers = {name: responds(url, 1.0) for name, _, _, _, url in _services()}
    if not quiet:
        for name, ok in answers.items():
            print(f"  {name:<11} {'answering' if ok else 'not running'}")
        print()
        for name, url in EXTERNAL:
            print(f"  {name:<10} {'answering' if responds(url, 1.0) else 'not running'} (external)")
        print(f"  {'Clarvis':<10} {_bridges_text(_clarvis_bridges())} (external)")
    return answers


#: Where clicking a line of the stack in the menu bar app takes the browser. SIRVIS, RAVIS
#: and CLARVIS serve no pages of their own — their screens are NERVIS's dashboard — so each
#: opens its screen there, addressed the way the dashboard's router reads a hash.
SCREENS = {
    "NERVIS": "nervis/Overview", "SIRVIS": "sirvis/Dashboard",
    "RAVIS": "ravis/Dashboard", "CLARVIS": "clarvis/Workspace",
}


def _open_address(name: str, health_url: str) -> str | None:
    """The page a service's line opens: its dashboard screen, code-server's own address, or
    None for a runtime with nothing to show in a browser."""
    if name in SCREENS:
        return f"{DASHBOARD}#/{SCREENS[name]}"
    if name == "code-server":
        parts = urllib.parse.urlsplit(health_url)
        return f"{parts.scheme}://{parts.netloc}/"
    return None


#: The machine figures the menu bar app shows, out of everything NERVIS samples.
SYSTEM_FIGURES = (
    "memory_total_bytes", "memory_available_bytes", "swap_used_bytes", "disk_free_bytes",
    "load_average", "cpu_count", "thermal_state",
)


def status_report() -> dict[str, object]:
    """What `status --json` prints: every service and its group, the dashboard, the machine.

    **The menu bar app reads this and nothing else** (nervis/packaging/macos), so
    what the stack is — which services, on which ports, and how each is asked
    whether it is up — stays known in one place, this file. `group` is what the
    menu sorts by: "stack" for what this launcher starts, "runtime" for LM Studio
    and Ollama, "editor" for CLARVIS, whose open windows NERVIS's
    registry counts.

    Probed in parallel. One at a time, a stack that is down costs a second per
    service, and the menu asks every time it is opened.

    `problem` is what `_problem` found for a service that is running and silent, so the
    menu can say so under its line instead of "not running"; None for everything else.
    """
    owned = _services()
    probes = [
        (name, url, "runtime" if name == "Ollama" else "stack")
        for name, _, _, _, url in owned
    ] + [(name, url, "runtime") for name, url in EXTERNAL]
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        answers = list(pool.map(lambda probe: responds(probe[1], 1.0), probes))
    # Only a service this launcher owns has a marker, and so a process it can vouch for.
    markers = {name: marker for name, _, marker, _, _ in owned}
    recorded = _recorded()
    services = [
        {"name": name, "group": group, "answering": answering, "address": _open_address(name, url),
         "problem": _problem(name, markers.get(name), answering, recorded)}
        for (name, url, group), answering in zip(probes, answers)
    ]
    nervis_up = any(service["name"] == "NERVIS" and service["answering"] for service in services)
    # CLARVIS is listed with the stack, just above code-server, which hosts it in a browser.
    # Grouped "editor" rather than "stack": no editor window being open is not part of the
    # stack being down, and the menu bar icon must not say it is.
    windows = _clarvis_bridges() if nervis_up else None
    at = next((i for i, service in enumerate(services) if service["name"] == "code-server"), len(services))
    services.insert(at, {"name": "CLARVIS", "group": "editor", "answering": bool(windows),
                              "windows": windows, "address": _open_address("CLARVIS", ""),
                              "problem": None})
    unread = _unread_notifications() if nervis_up else None
    return {
        "services": services,
        "dashboard": DASHBOARD,
        "system": _system_reading() if nervis_up else None,
        "notifications": None if unread is None else {"unread": unread, "screen": NOTIFICATIONS},
        # Codex's state, allowance and tasks, as RAVIS reports them, for the menu's Codex line.
        "codex": _codex_entry(services),
    }


#: The dashboard's notification centre, addressed the way its router reads a hash.
NOTIFICATIONS = f"{DASHBOARD}#/nervis/Notifications"


def _from_nervis(path: str) -> object:
    """One JSON read from NERVIS, or None when it does not answer with one."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{NERVIS_PORT}{path}", timeout=2.0) as reply:
            return json.load(reply)
    except Exception:  # noqa: BLE001 - any failure means "nothing to show", never a crash
        return None


def _system_reading() -> dict[str, object] | None:
    """The handful of machine figures the menu shows, from NERVIS's own reading.

    NERVIS already samples the machine for its dashboard, so this asks it rather
    than measuring a second time. None when the read fails — the menu then leaves
    the figures out, which is truer than showing zeros.
    """
    reading = _from_nervis("/api/v1/system")
    if not isinstance(reading, dict):
        return None
    return {figure: reading.get(figure) for figure in SYSTEM_FIGURES}


def _unread_notifications() -> int | None:
    """How many notes NERVIS's notification centre holds unread, or None if unknown.

    The centre is where every app's notices arrive — a service going down or coming
    back, background work finishing — and the menu bar icon blinks while this is
    above zero. NERVIS keeps the count on the listing so that a badge and its list
    cannot disagree, so one unread item is asked for and only the count is kept.
    """
    listing = _from_nervis("/api/v1/notifications?unread=1&limit=1")
    unread = listing.get("unread") if isinstance(listing, dict) else None
    return unread if isinstance(unread, int) else None


def _clarvis_bridges() -> int | None:
    """How many editor windows have a live Clarvis Bridge registered with NERVIS.

    None when NERVIS cannot be asked. A Bridge picks its own port and registers it
    (NERVIS.md §5.1), so the registry — readable without a credential, since it
    holds no token and no path — is the only place that knows whether Clarvis is
    running. A registration whose lease has lapsed is a window that went away.
    """
    listing = _from_nervis("/api/v1/registry/instances")
    items = listing.get("items") if isinstance(listing, dict) else None
    if not isinstance(items, list):
        return None
    return sum(
        1 for item in items
        if isinstance(item, dict) and item.get("service") == "clarvis" and item.get("live")
    )


def _bridges_text(windows: int | None) -> str:
    if windows is None:
        return "unknown — NERVIS is not answering"
    if windows == 0:
        return "not running"
    return f"answering in {windows} editor window{'s' if windows > 1 else ''}"


# ── Models, for the menu bar app's LM Studio menu ─────────────────────────────
#
# The owner asked for LM Studio's entry in the menu bar app to list the installed
# models and load them *through SIRVIS*, which owns every load and unload
# (SIRVIS.md §9), and chose two behaviours on 12 September 2026: a model loaded from
# the menu stays loaded until it is unloaded there or NERVIS quits, and a model that
# probably will not fit in free memory is asked about first (the app does that part).

#: The sessions the menu bar app opened, by model key: renewed while the app runs,
#: released when a model is unloaded from the menu, and released before the stack stops.
MENU_SESSIONS = RUN / "menubar-sessions.json"


def _sirvis_call(method: str, path: str, body: dict[str, object] | None = None,
                 timeout: float = 10.0) -> tuple[int, object]:
    """One JSON call to SIRVIS: (HTTP status, parsed body). Status 0 when unreachable.

    **Presents the dashboard's runtime token** (`dashboard_token`). The menu bar app
    is the same person's other window onto the same stack, and a second credential
    would be one more that nobody can account for. It is read and sent here, never
    printed. A write carries a JSON content type even with no body, because SIRVIS
    requires one on every mutation as a CSRF defence (§4.5).
    """
    headers = {"authorization": f"Bearer {dashboard_token()}", "accept": "application/json"}
    if method != "GET":
        headers["content-type"] = "application/json"
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{SIRVIS_PORT}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, _parsed(reply.read())
    except urllib.error.HTTPError as failure:
        return failure.code, _parsed(failure.read())
    except Exception:  # noqa: BLE001 - an unreachable SIRVIS is an answer, not a crash
        return 0, None


def _parsed(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _refusal(status: int, answer: object) -> str:
    """SIRVIS's own words for a refusal, or what can be said without them."""
    if status == 0:
        return "SIRVIS is not answering"
    error = answer.get("error") if isinstance(answer, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return str(message) if message else f"SIRVIS answered HTTP {status}"


def _menu_sessions() -> dict[str, str]:
    try:
        stored = json.loads(MENU_SESSIONS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(key): str(value) for key, value in stored.items()} if isinstance(stored, dict) else {}


def _save_menu_sessions(sessions: dict[str, str]) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    MENU_SESSIONS.write_text(json.dumps(sessions, indent=1) + "\n", encoding="utf-8")


def _model_row(model: dict[str, object], loaded_keys: set[str], held: dict[str, str]) -> dict[str, object]:
    """One installed model as the menu shows it."""
    key = str(model.get("runtime_key") or "")
    variant = model.get("variant") if isinstance(model.get("variant"), dict) else {}
    return {
        "key": key,
        "name": key.split("/")[-1],
        "format": str(variant.get("runtime_format") or ""),
        "quantization": str(variant.get("quantization") or ""),
        "size_bytes": model.get("installed_size_bytes"),
        "loaded": key in loaded_keys or bool(model.get("is_loaded")),
        "held_by_menu": key in held,
    }


def models_report() -> dict[str, object]:
    """LM Studio's installed models as SIRVIS knows them, for the menu bar app.

    Each carries its size, whether it is loaded, and whether the menu holds it. A
    model the menu loaded can be unloaded from it; one loaded by anything else —
    RAVIS, a benchmark, LM Studio by itself — is shown as loaded and left alone,
    because releasing another client's lease is not the menu's to do (§9). A session
    the menu recorded that SIRVIS no longer has is forgotten here.
    """
    status, models = _sirvis_call("GET", "/api/v1/models", timeout=15.0)
    if status != 200 or not isinstance(models, dict):
        return {"available": False, "models": [], "max_loaded": None}
    _, residency = _sirvis_call("GET", "/api/v1/runtime/residency", timeout=15.0)
    residency = residency if isinstance(residency, dict) else {}
    live = {str(lease.get("session_id")) for lease in residency.get("leases", []) if isinstance(lease, dict)}
    held = {key: session for key, session in _menu_sessions().items() if session in live}
    _save_menu_sessions(held)
    loaded_keys = {str(h.get("model_key")) for h in residency.get("holdings", []) if isinstance(h, dict)}
    loaded_keys |= {str(key) for key in residency.get("foreign", [])}
    rows = [
        _model_row(model, loaded_keys, held)
        for model in models.get("items", [])
        if isinstance(model, dict) and model.get("runtime_key")
    ]
    rows.sort(key=lambda row: str(row["name"]).lower())
    return {"available": True, "models": rows, "max_loaded": residency.get("max_loaded")}


def load_model(key: str) -> dict[str, object]:
    """Load one model through SIRVIS, under a lease the menu bar app keeps renewing.

    `reject` rather than SIRVIS's default policy, which today refuses the same way
    but is named `wait` (§9's 4 Sep amendment): the menu should never appear to be
    queued behind something. Allowed fifteen minutes, because a large model's first
    load from disk takes minutes and the request only answers when it is loaded.
    """
    if not key:
        return {"ok": False, "error": "name a model to load"}
    held = _menu_sessions()
    if key in held:
        return {"ok": True, "session_id": held[key], "already": True}
    status, answer = _sirvis_call(
        "POST", "/api/v1/runtime/sessions",
        {"models": [{"model_id": key}], "policy": "reject"}, timeout=900.0,
    )
    session = answer.get("session_id") if isinstance(answer, dict) else None
    if status != 200 or not session:
        return {"ok": False, "error": _refusal(status, answer)}
    held[key] = str(session)
    _save_menu_sessions(held)
    return {"ok": True, "session_id": session}


def unload_model(key: str) -> dict[str, object]:
    """Release the menu's session on one model; SIRVIS unloads it if nobody else holds it."""
    held = _menu_sessions()
    session = held.get(key)
    if session is None:
        return {"ok": False, "error": "this model was not loaded from the menu, so the menu leaves it alone"}
    status, answer = _sirvis_call("DELETE", f"/api/v1/runtime/sessions/{session}", timeout=120.0)
    if status not in (200, 404):
        return {"ok": False, "error": _refusal(status, answer)}
    del held[key]
    _save_menu_sessions(held)
    return {"ok": True}


def renew_models() -> dict[str, object]:
    """Renew every session the menu holds. A lapsed one (404) is forgotten; one SIRVIS
    could not be asked about is kept for the next try."""
    kept = {
        key: session for key, session in _menu_sessions().items()
        if _sirvis_call("POST", f"/api/v1/runtime/sessions/{session}/renew")[0] in (200, 0)
    }
    _save_menu_sessions(kept)
    return {"ok": True, "renewed": len(kept)}


def _release_menu_sessions() -> None:
    """Unload what the menu bar app loaded, before the stack stops.

    The owner chose that a model loaded from the menu stays loaded "until I unload it or
    quit", so the launcher releases the menu's sessions first — its own, never another
    client's.

    **SIRVIS now does this itself on the way down.** Until 12 September 2026 it released
    nothing when it stopped, and a model loaded from the menu stayed in LM Studio after the
    stack had gone, held by nobody; its shutdown now releases every session and unloads
    every model it loaded. This stays anyway, for three reasons: it is harmless, since a
    session released here is one fewer for SIRVIS to unload inside its wait; it clears the
    menu's own record (`MENU_SESSIONS`), which SIRVIS knows nothing about; and it covers a
    SIRVIS older than that change, which still releases nothing.
    """
    for key in list(_menu_sessions()):
        result = unload_model(key)
        print(f"  released {key} for the menu bar" if result["ok"] else f"  could not release {key}: {result['error']}")


# ── Codex, for the menu bar app's Codex line ──────────────────────────────────
#
# Codex, OpenAI's coding agent, is an optional coding engine for Clarvis that RAVIS runs on the
# owner's ChatGPT plan (runbook §2.2). **RAVIS owns all of it**: the one Codex process, its home and
# sign-in, the version pin and the allowance reading. So the launcher asks RAVIS and nothing else.
# It never reads the ChatGPT app's `~/.codex` or RAVIS's own Codex folder, and never runs a Codex
# of its own: a menu that did either could show another account's allowance, or spend the owner's
# plan just to draw a line (the owner's rule, `design/codex-engine/constraints.md`).
#
# **The commands** (design §3.7, §7.4) — `run.py codex sign-in`, `cancel-sign-in`, `stop` and
# `reprove` — each ask RAVIS, print one JSON line and exit with a code the app reads. The codes are
# the contract's, in the `launcher` sections of `owner-stop.json` and `codex-admin.json` in
# `ravis/tests/fixtures/relay-contract/`. An answer none of them names exits 1, and the printed
# error says what it was. **Nothing printed carries a key or the sign-in page's address**: the app
# logs what it reads.
#
# **Not usable yet.** RAVIS serves Codex's state, sign-in and the re-test from M29's second
# increment (R2), and a task's Stop from its third (R3). Until then RAVIS answers 404, so
# `status --json` carries no Codex entry and each command exits 1, saying RAVIS does not offer it.

#: The Codex card on RAVIS's dashboard screen, which the menu's Codex line opens.
CODEX_CARD = f"{DASHBOARD}#/{SCREENS['RAVIS']}"
#: How long the menu's read of Codex's state may take. RAVIS answers it from a snapshot in about
#: 50 ms, without waiting on Codex (design §3.3), so this only bounds a RAVIS that is stuck.
CODEX_READ_TIMEOUT = 1.5
#: What each task carries into `status --json`: its line in the menu, and the confirmation its Stop
#: sends back (§3.5.5). A folder name only — RAVIS gives out no path, request text or command.
CODEX_RUN_FIELDS = (
    "id", "turn_id", "project", "state", "since", "age_minutes", "waiting_minutes",
    "attached_windows",
)
#: What each allowance window carries: what is left of it, and when it resets.
CODEX_WINDOW_FIELDS = ("label", "remaining_percent", "resets_at")
CODEX_RAVIS_DOWN = "RAVIS is not answering, so Codex's state is not known."


def _codex_entry(services: list[dict[str, object]]) -> dict[str, object] | None:
    """Codex's entry in `status --json`: RAVIS's reading while RAVIS answers, else not known.

    Asked of RAVIS only once its probe has answered, like NERVIS's figures, so a RAVIS that is down
    costs the menu no wait (`_codex_reading` says what the read itself can return).
    """
    ravis_up = any(service["name"] == "RAVIS" and service["answering"] for service in services)
    return _codex_reading() if ravis_up else _codex_unknown(CODEX_RAVIS_DOWN)


def _codex_reading() -> dict[str, object] | None:
    """Codex's state, allowance and tasks as RAVIS reports them, trimmed for the menu bar app.

    **Read from `GET /api/v1/codex`** (design §3.3, §7.1), which any caller may read. The launcher
    presents NERVIS's client credential because only a *named* caller is given each task's id and
    turn, and a Stop cannot be sent without both.

    Three outcomes, kept apart because each means something different in the menu:
    - **a reading**, trimmed by `_codex_block`;
    - **None, when RAVIS answers 404:** this RAVIS serves no Codex state at all — every RAVIS
      before M29's second increment. The menu draws no Codex line rather than one about nothing;
    - **not known** (`_codex_unknown`) for anything else: RAVIS unreachable, stuck past the
      timeout, or answering with something that is not Codex's state.
    """
    status, answer = _ravis_call(
        "GET", "/api/v1/codex", nervis_ravis_credential(), timeout=CODEX_READ_TIMEOUT
    )
    if status == 404:
        return None
    if status == 200 and isinstance(answer, dict) and isinstance(answer.get("state"), str):
        return _codex_block(answer)
    if status == 0:
        return _codex_unknown(CODEX_RAVIS_DOWN)
    return _codex_unknown(
        f"RAVIS's answer (HTTP {status}) was not Codex's state, so Codex's state is not known."
    )


def _codex_block(reading: dict[str, object]) -> dict[str, object]:
    """RAVIS's Codex reading, reduced to what the menu draws (design §7.1).

    **Unknown is never a number.** When RAVIS says it does not know the allowance, no window is
    carried at all, whatever else arrived, so the menu can say "usage unknown" and never "0% left"
    (§3.3). A field RAVIS left out is None here, never a guess. Nothing of the runtime, of the
    account beyond its plan, or of the models is carried: the menu shows none of it, and the
    dashboard's card reads RAVIS for itself.
    """
    usage = _mapping(reading.get("usage"))
    known = usage.get("known") is True
    return {
        "state": reading["state"],
        "reason": _text(reading.get("reason")),
        "plan": _text(_mapping(reading.get("account")).get("plan")),
        "usage_known": known,
        "stale": usage.get("stale") is True,
        "windows": _picked(usage.get("windows"), CODEX_WINDOW_FIELDS) if known else [],
        "runs": _picked(reading.get("runs"), CODEX_RUN_FIELDS),
        "sign_in_waiting": _mapping(reading.get("sign_in")).get("state") == "waiting_for_browser",
        "address": CODEX_CARD,
    }


def _codex_unknown(reason: str) -> dict[str, object]:
    """The Codex entry when RAVIS could not tell: its state not known, and nothing drawn as zero.

    Shaped like a reading, so the menu decodes one thing. `runs` is None rather than an empty list:
    RAVIS being unreachable says nothing about whether Codex is working, and "no tasks" would.
    `ravis_not_answering` is the one state word this launcher names itself (§7.1); every other
    state word is RAVIS's own.
    """
    return {
        "state": "ravis_not_answering", "reason": reason, "plan": None, "usage_known": False,
        "stale": None, "windows": [], "runs": None, "sign_in_waiting": None,
        "address": CODEX_CARD,
    }


def _mapping(value: object) -> dict[str, object]:
    """`value` when it is a JSON object, else an empty one, so a missing section reads as absent."""
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str | None:
    """`value` when it is a string, else None."""
    return value if isinstance(value, str) else None


def _picked(rows: object, fields: tuple[str, ...]) -> list[dict[str, object]] | None:
    """The objects in `rows`, each cut to `fields` (missing ones None); None if not a list."""
    if not isinstance(rows, list):
        return None
    return [{field: row.get(field) for field in fields} for row in rows if isinstance(row, dict)]


def _ravis_call(
    method: str, path: str, credential: str, body: dict[str, object] | None = None, *,
    headers: Mapping[str, str] | None = None, timeout: float = 10.0,
) -> tuple[int, object]:
    """One JSON call to this machine's RAVIS: (HTTP status, parsed body); status 0 if unreachable.

    The credential goes in the `Authorization` header and nowhere else — not into anything a command
    prints, and not into an error, which carries RAVIS's own words at most. A write carries a JSON
    body, as RAVIS's management routes expect.
    """
    sent = {
        **(headers or {}), "authorization": f"Bearer {credential}", "accept": "application/json",
    }
    data = None
    if body is not None:
        sent["content-type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{RAVIS_PORT}{path}", data=data, headers=sent, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, _parsed(reply.read())
    except urllib.error.HTTPError as failure:
        return failure.code, _parsed(failure.read())
    except Exception:  # noqa: BLE001 - an unreachable RAVIS is an answer, not a crash
        return 0, None


#: Exit codes the four commands share; the others are named in each command's table below.
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_RAVIS_DOWN = 6
EXIT_REFUSED = 10
#: RAVIS's error codes (`conventions.json`) and the exit code each command gives for them.
SIGN_IN_EXITS = {
    "CODEX_ALREADY_SIGNED_IN": 3, "CODEX_NOT_AVAILABLE": 4, "CODEX_UNTESTED_VERSION": 4,
    "CODEX_SIGN_IN_PORT_BUSY": 5, "CODEX_RUN_IN_PROGRESS": 7, "FORBIDDEN": EXIT_REFUSED,
}
STOP_EXITS = {
    "CONFIRMATION_MISMATCH": 8, "AGENT_SESSION_NOT_FOUND": 9, "NOTHING_RUNNING": 9,
    "OWNER_STOP_NOT_ALLOWED": EXIT_REFUSED, "RATE_LIMITED": EXIT_REFUSED, "FORBIDDEN": EXIT_REFUSED,
}
REPROVE_EXITS = {
    "CODEX_RUN_IN_PROGRESS": 7, "REPROOF_NOT_ALLOWED": EXIT_REFUSED, "FORBIDDEN": EXIT_REFUSED,
}
#: The re-test's three result words (`codex-admin.json`) and their exit codes.
REPROOF_RESULTS = {"proven": 0, "failed": 11, "inconclusive": 12}
#: RAVIS caps the re-test at five minutes; the launcher asks every five seconds for six.
REPROOF_POLL_SECONDS = 5.0
REPROOF_WAIT_SECONDS = 360.0
#: A task id as RAVIS mints it — the same shape NERVIS's Stop route accepts (design §3.8).
SESSION_ID = re.compile(r"as_[0-9A-Za-z]{10,40}")
NO_ADMIN_KEY = (
    "The launcher's RAVIS admin key is not there yet; start the stack so the launcher makes it "
    "and RAVIS learns it."
)
NO_OWNER_KEY = (
    "The owner's command-line key for Codex is not there yet; stop the stack and start it again "
    "so the launcher makes it and RAVIS learns it."
)


def codex_sign_in() -> tuple[int, dict[str, object]]:
    """Start Codex's browser sign-in inside RAVIS, and open the page it names (design §3.4, §3.7).

    Presents the launcher's admin key, as the dashboard's Sign in does through NERVIS. The page's
    address is opened in the browser and **never printed**: until the sign-in finishes or expires
    it is a live way into it, and whatever runs this command logs what it prints. Only an `https`
    page is opened. Asked again while RAVIS already waits on a browser, RAVIS names the same page,
    and it is opened again.
    """
    key = _held_secret(RAVIS_ADMIN_TOKEN)
    if not key:
        return EXIT_REFUSED, {"error": NO_ADMIN_KEY}
    status, answer = _ravis_call(
        "POST", "/api/v1/codex/sign-in", key, {"method": "browser"}, timeout=20.0
    )
    if status not in (200, 202):
        return _ravis_refusal(status, answer, SIGN_IN_EXITS)
    sign_in = _mapping(_mapping(answer).get("sign_in"))
    page, state = sign_in.get("auth_url"), _text(sign_in.get("state"))
    if not (isinstance(page, str) and page.startswith("https://")):
        return EXIT_FAILED, {"state": state, "error": "RAVIS named no sign-in page to open."}
    if not webbrowser.open(page):
        return EXIT_FAILED, {"state": state, "error": "No browser could be opened for the sign-in."}
    return 0, {"state": state}


def codex_cancel_sign_in() -> tuple[int, dict[str, object]]:
    """Cancel a sign-in RAVIS is waiting on, with the launcher's admin key (design §3.4).

    Exits 0 once RAVIS has answered, printing whether it cancelled one.
    """
    key = _held_secret(RAVIS_ADMIN_TOKEN)
    if not key:
        return EXIT_REFUSED, {"error": NO_ADMIN_KEY}
    status, answer = _ravis_call("DELETE", "/api/v1/codex/sign-in", key)
    if status != 200:
        return _ravis_refusal(status, answer, {"FORBIDDEN": EXIT_REFUSED})
    return 0, {"cancelled": _mapping(answer).get("cancelled") is True}


def codex_stop(session: str, project: str, turn: str) -> tuple[int, dict[str, object]]:
    """Stop one running Codex task for the owner, through RAVIS's owner Stop route (§3.5.5, §7.4).

    **Stop only.** RAVIS interrupts the task, ends its commands and pauses it with its work left in
    the project. It never approves, answers, starts or steers anything, and neither does this.

    **What it sends:**
    - the owner's command-line key, never NERVIS's, so RAVIS counts and audits the menu bar apart
      from the dashboard;
    - `source: "menu_bar"`, so the Clarvis window attached to the task can say where the stop came
      from;
    - the task's folder name and turn as the menu last read them. RAVIS checks both against the
      task, so a menu that went stale while its confirmation was open gets exit 8 and stops
      nothing, rather than whatever that id means by then;
    - a fresh `Idempotency-Key`, so a request that reaches RAVIS twice is carried out once.

    The id is held to RAVIS's own shape before it goes into the address, so nothing else can be
    addressed through it, and a blank confirmation is not sent at all.
    """
    if not (SESSION_ID.fullmatch(session) and project and turn):
        return EXIT_USAGE, {
            "error": "Name the task's id, folder and turn exactly as status --json lists them."
        }
    key = _held_secret(RAVIS_OWNER_TOKEN)
    if not key:
        return EXIT_REFUSED, {"error": NO_OWNER_KEY}
    status, answer = _ravis_call(
        "POST", f"/api/v1/agent-sessions/{session}/owner-stop", key,
        {"source": "menu_bar", "confirm": {"project": project, "turn_id": turn}},
        headers={"Idempotency-Key": _idempotency_key()},
    )
    if status not in (200, 202):
        return _ravis_refusal(status, answer, STOP_EXITS)
    return 0, {"state": "stopping"}


def codex_reprove() -> tuple[int, dict[str, object]]:
    """Start the re-test of Codex's file rules, and wait for its result (design §3.4).

    **The one way the re-test starts.** It spends a short Codex turn of the owner's allowance, so
    RAVIS accepts only the owner's command-line key for it and refuses NERVIS's, and the menu bar
    app asks the owner first. RAVIS answers at once and runs the test, so the launcher asks every
    five seconds for up to six minutes — RAVIS caps the test itself at five — and prints the result
    word: `proven`, `failed` or `inconclusive`.
    """
    key = _held_secret(RAVIS_OWNER_TOKEN)
    if not key:
        return EXIT_REFUSED, {"error": NO_OWNER_KEY}
    status, answer = _ravis_call(
        "POST", "/api/v1/codex/reprove", key, {}, headers={"Idempotency-Key": _idempotency_key()}
    )
    if status not in (200, 202):
        return _ravis_refusal(status, answer, REPROVE_EXITS)
    return _awaited_reproof(key, answer)


def _awaited_reproof(key: str, answer: object) -> tuple[int, dict[str, object]]:
    """Ask RAVIS about the re-test until it has finished, or six minutes have passed.

    After six minutes the launcher stops asking and says it does not know how the test ended,
    rather than guessing a result.
    """
    deadline = time.monotonic() + REPROOF_WAIT_SECONDS
    while True:
        reproof = _mapping(_mapping(answer).get("reproof"))
        if reproof.get("state") == "finished":
            return _reproof_result(reproof.get("result"))
        if time.monotonic() >= deadline:
            return EXIT_FAILED, {
                "result": None,
                "error": "RAVIS had not finished the re-test after six minutes; its Codex card"
                " shows whether the file rules are proven.",
            }
        time.sleep(REPROOF_POLL_SECONDS)
        status, answer = _ravis_call("GET", "/api/v1/codex/reprove", key)
        if status != 200:
            return _ravis_refusal(status, answer, REPROVE_EXITS)


def _reproof_result(word: object) -> tuple[int, dict[str, object]]:
    """The exit code and printed line for the re-test's result word."""
    if isinstance(word, str) and word in REPROOF_RESULTS:
        return REPROOF_RESULTS[word], {"result": word}
    return EXIT_FAILED, {"result": None, "error": "RAVIS finished the re-test without a result."}


def _ravis_refusal(
    status: int, answer: object, exits: Mapping[str, int],
) -> tuple[int, dict[str, object]]:
    """The exit code and printed line for an answer that was not the command's success.

    Matched on RAVIS's error code, as the contract asks (`conventions.json`: clients match on the
    code, never on wording), and printed in RAVIS's own words. A refusal carrying no code — a key
    RAVIS does not know — is still a refusal; a 404 carrying none is a RAVIS without the route.
    """
    if status == 0:
        return EXIT_RAVIS_DOWN, {"error": "RAVIS is not answering."}
    error = _mapping(_mapping(answer).get("error"))
    code, message = error.get("code"), error.get("message")
    if not isinstance(code, str) and status == 404:
        return EXIT_FAILED, {"error": "This RAVIS does not offer that yet."}
    fallback = EXIT_REFUSED if status in (401, 403) else EXIT_FAILED
    exit_code = exits.get(code, fallback) if isinstance(code, str) else fallback
    return exit_code, {"error": str(message) if message else f"RAVIS answered HTTP {status}."}


def _idempotency_key() -> str:
    """A fresh key for one request: 32 URL-safe characters, within RAVIS's and NERVIS's rules."""
    return secrets.token_urlsafe(24)


#: `run.py codex <action>` for each action that takes nothing more; `stop` is read on its own.
CODEX_ACTIONS = {
    "sign-in": codex_sign_in, "cancel-sign-in": codex_cancel_sign_in, "reprove": codex_reprove,
}


def _run_codex() -> int:
    """`run.py codex …`: one JSON line on stdout, and the exit code the menu bar app reads."""
    arguments = _codex_arguments().parse_args(sys.argv[2:])
    if arguments.action == "stop":
        code, printed = codex_stop(arguments.id, arguments.project, arguments.turn)
    else:
        code, printed = CODEX_ACTIONS[arguments.action]()
    print(json.dumps(printed))
    return code


def _codex_arguments() -> argparse.ArgumentParser:
    """What `run.py codex` accepts. A mistake in it exits 2, as the launcher's own usage does."""
    parser = argparse.ArgumentParser(
        prog="run.py codex", description="Codex through RAVIS, for the menu bar app."
    )
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("sign-in", help="start Codex's sign-in in RAVIS and open its page")
    actions.add_parser("cancel-sign-in", help="cancel a sign-in RAVIS is waiting on")
    stop = actions.add_parser("stop", help="stop one running Codex task, confirming which")
    stop.add_argument("id", help="the task's id, as status --json lists it")
    stop.add_argument("--project", required=True, help="the task's folder, from status --json")
    stop.add_argument("--turn", required=True, help="the task's turn id, from status --json")
    actions.add_parser("reprove", help="re-test Codex's file rules, using one short Codex turn")
    return parser


def _run_models() -> int:
    print(json.dumps(models_report()))
    return 0


def _answer(result: dict[str, object]) -> int:
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


def _run_load() -> int:
    return _answer(load_model(sys.argv[2] if len(sys.argv) > 2 else ""))


def _run_unload() -> int:
    return _answer(unload_model(sys.argv[2] if len(sys.argv) > 2 else ""))


def _run_renew() -> int:
    return _answer(renew_models())


def _run_status() -> int:
    if "--json" in sys.argv[2:]:
        print(json.dumps(status_report()))
        return 0
    status()
    return 0


COMMANDS = {
    "start": start, "stop": stop, "status": _run_status,
    "models": _run_models, "load": _run_load, "unload": _run_unload, "renew": _run_renew,
    "codex": _run_codex,
}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action not in COMMANDS:
        print(
            f"usage: {Path(__file__).name} [start|stop|status [--json]|models|load KEY|unload KEY"
            "|renew|codex sign-in|codex cancel-sign-in|codex stop ID --project NAME --turn TURN"
            "|codex reprove]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(COMMANDS[action]())
