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
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "ravis" / ".venv"
RUN = ROOT / ".run"
# The admin secret the launcher mints for RAVIS (§15.1). One path, because the
# health check reads it as well as the credential writes.
RAVIS_ADMIN_TOKEN = RUN / "ravis-admin.token"
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
    ("Clarvis", "http://127.0.0.1:7071/"),
]


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


def _services() -> list[tuple[str, list[str], str, dict[str, str], str]]:
    """(name, command, marker, env, health url) for each service we own.

    `marker` is a distinctive fragment of the command line, used by `stop` to
    confirm a recorded PID is still the process we started rather than whatever
    the OS handed that number to next.
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
            # **What the operator has declared about models nothing has measured.**
            # A hosted vendor's catalogue can leave out a capability its API has —
            # Anthropic's publishes no tool support — and a pool that requires tools
            # then refuses every one of that vendor's models. The file says who
            # declared what, and why. Set only as a default, like the rest.
            env.setdefault(
                "RAVIS_MODEL_CAPABILITIES_PATH", str(ROOT / "ravis" / "operator-capabilities.json")
            )
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
        ("SIRVIS", [str(venv_bin("sirvis")), "serve"], "sirvis",
         _with_results(env_for("SIRVIS", SIRVIS_PORT)),
         f"http://127.0.0.1:{SIRVIS_PORT}/v1/status"),
        ("RAVIS", [str(venv_bin("ravis")), "serve"], "ravis",
         env_for("RAVIS", RAVIS_PORT), f"http://127.0.0.1:{RAVIS_PORT}/v1/models"),
        # NERVIS's own service since M0, replacing the `http.server` that stood
        # in for it. Same port, same URL, same dashboard file — what changes is
        # that the thing serving it now has a database, an identity and an
        # `/ecosystem/*` surface, which is what makes settings and conversations
        # able to outlive a browser profile.
        ("NERVIS", [str(venv_bin("nervis")), "serve"], "nervis",
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
        "ollama",
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
    cached = RUN / filename
    try:
        existing = cached.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    RUN.mkdir(parents=True, exist_ok=True)
    handle = os.open(cached, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(token + "\n")
    return token


def ravis_admin_credential() -> str:
    """The `admin.`-prefixed secret §15.1 requires for a credential write.

    Minted and cached exactly like NERVIS's client credential, and stored
    through `ravis credential` rather than the HTTP endpoint — that endpoint now
    requires this very credential, so the first one cannot be written through
    it. The command line is a different authority: whoever runs the launcher
    already owns the config directory the store lives in.
    """
    cached = RAVIS_ADMIN_TOKEN
    try:
        existing = cached.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    RUN.mkdir(parents=True, exist_ok=True)
    handle = os.open(cached, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(token + "\n")
    return token


def teach_ravis_the_admin_credential() -> str:
    """Put the admin secret in RAVIS's own store, before anything needs it.

    Runs before RAVIS starts rather than after: the CLI writes the file the
    gateway reads at startup, and doing it while the gateway is running would be
    two processes writing one JSON document. Nothing is racing here because
    nothing else is up yet.
    """
    secret = ravis_admin_credential()
    if not secret:
        return "could not be minted"
    executable = ROOT / "ravis" / ".venv" / "bin" / "ravis"
    if not executable.exists():
        return "ravis is not installed"
    try:
        finished = subprocess.run(
            [str(executable), "credential", "admin.launcher"],
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
        return marker in listed.stdout
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
    running = status(quiet=True)
    if all(running.values()):
        print("Already running.")
        print(f"  Named callers to RAVIS: {teach_ravis_the_credential()}")
        print(f"Dashboard: {DASHBOARD}")
        webbrowser.open(dashboard_url())
        return 0

    print("Starting (detached — closing this window will not stop them)…")
    recorded = _recorded()
    for name, command, marker, env, _ in _services():
        if running.get(name):
            print(f"  {name} already running")
            continue
        pid = _spawn_detached(command, env, RUN / f"{name.lower()}.log")
        recorded[name] = {"pid": pid, "marker": marker}
        print(f"  {name} started (pid {pid})")
    RUN.mkdir(parents=True, exist_ok=True)
    with PIDFILE.open("w", encoding="utf-8") as handle:
        json.dump(recorded, handle, indent=2, sort_keys=True)

    print("\nWaiting for them to answer…")
    ready = True
    for name, _, _, _, url in _services():
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and not responds(url):
            time.sleep(0.4)
        answering = responds(url)
        ready = ready and answering
        print(f"  {name:<11} {'ready' if answering else 'NOT ready — see .run/' + name.lower() + '.log'}")

    # After RAVIS answers, because storing a credential is a request to it. Both
    # halves already hold the same string — this is the half RAVIS keeps.
    print(f"\nRAVIS admin credential (§15.1): {planted}")
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


def stop() -> int:
    recorded = _recorded()
    if not recorded:
        print("Nothing recorded as running.")
        return 0
    print("Stopping…")
    for name, record in sorted(recorded.items()):
        raw_pid = record.get("pid", 0)
        pid = raw_pid if isinstance(raw_pid, int) else 0
        marker = str(record.get("marker", ""))
        if not pid or not _alive(pid, marker):
            print(f"  {name} was not running")
            continue
        # Ask first. A service killed outright can leave a half-written SQLite
        # journal, and SIRVIS releases its model leases on shutdown (§9).
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(pid), "/T"], capture_output=True, check=False)
        else:
            os.kill(pid, 15)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline and _alive(pid, marker):
            time.sleep(0.2)
        if _alive(pid, marker):
            print(f"  {name} did not stop; forcing")
            if WINDOWS:
                subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"],
                               capture_output=True, check=False)
            else:
                os.kill(pid, 9)
        print(f"  {name} stopped")
    PIDFILE.unlink(missing_ok=True)

    # **Checked, because the loop above can be wrong and say nothing.** A
    # recorded PID whose command line no longer carries its marker is skipped as
    # "was not running" — which is right when the PID was reused and wrong when
    # the process simply re-exec'd itself out of recognition. Both print the same
    # line, and in the second case this printed "Stopped." over a service still
    # holding its port. Observed, not imagined: it is what a stub code-server did
    # the first time this path ran.
    still = [name for name, _, _, _, url in _services() if responds(url, 1.0)]
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
    return answers


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
    and Ollama, "editor" for Clarvis.

    Probed in parallel. One at a time, a stack that is down costs a second per
    service, and the menu asks every time it is opened.
    """
    probes = [
        (name, url, "runtime" if name == "Ollama" else "stack")
        for name, _, _, _, url in _services()
    ] + [
        (name, url, "runtime" if name == "LM Studio" else "editor") for name, url in EXTERNAL
    ]
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        answers = list(pool.map(lambda probe: responds(probe[1], 1.0), probes))
    services = [
        {"name": name, "group": group, "answering": answering}
        for (name, _, group), answering in zip(probes, answers)
    ]
    nervis_up = any(service["name"] == "NERVIS" and service["answering"] for service in services)
    unread = _unread_notifications() if nervis_up else None
    return {
        "services": services,
        "dashboard": DASHBOARD,
        "system": _system_reading() if nervis_up else None,
        "notifications": None if unread is None else {"unread": unread, "screen": NOTIFICATIONS},
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


def _run_status() -> int:
    if "--json" in sys.argv[2:]:
        print(json.dumps(status_report()))
        return 0
    status()
    return 0


COMMANDS = {"start": start, "stop": stop, "status": _run_status}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action not in COMMANDS:
        print(f"usage: {Path(__file__).name} [start|stop|status [--json]]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(COMMANDS[action]())
