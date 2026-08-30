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

**What it does not start.** LM Studio, Ollama and Clarvis are separate
applications with their own lifecycles, and §9 puts model loading behind
SIRVIS's Resource Manager. Their state is *reported* instead, because "nothing
is routing" and "no runtime is running" are the same symptom with very different
fixes.

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
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "ravis" / ".venv"
RUN = ROOT / ".run"
PIDFILE = RUN / "services.json"

SIRVIS_PORT = 8721
RAVIS_PORT = 8731
NERVIS_PORT = 8790

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
    ("Ollama", "http://127.0.0.1:11434/api/tags"),
    ("Clarvis", "http://127.0.0.1:7071/"),
]


def code_server_binary() -> str:
    """Where code-server is, or an empty string.

    Looked up on PATH rather than at a fixed location, because Homebrew, the
    official install script and npm each put it somewhere different and all
    three put it on PATH. An empty answer is a normal outcome, not an error.
    """
    return shutil.which("code-server") or ""


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
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


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
    ] + _code_server()


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
        dict(os.environ),
        f"http://127.0.0.1:{port}/healthz",
    )]


def _default_upstreams() -> dict[str, str]:
    """The local runtime, plus every hosted provider this machine has a key for.

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
    upstreams: list[dict[str, str]] = [
        {"name": "default", "base_url": LM_STUDIO, "kind": "lmstudio"}
    ]
    try:
        stored = json.loads(
            (pathlib.Path.home() / ".config" / "ravis" / "credentials.json").read_text()
        )
    except (OSError, ValueError):
        stored = {}
    for kind in TRANSPARENT_KINDS:
        if kind in stored:
            upstreams.append({"name": kind, "kind": kind})
    if len(upstreams) == 1:
        # One upstream and no hosted keys: keep the simpler pair of variables,
        # which is what every existing message and doc about this refers to.
        return {"RAVIS_UPSTREAM_BASE_URL": LM_STUDIO, "RAVIS_UPSTREAM_KIND": "lmstudio"}
    return {"RAVIS_UPSTREAMS": json.dumps(upstreams)}


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
    cached = RUN / "nervis-ravis.token"
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


def teach_ravis_the_credential() -> str:
    """Store NERVIS's credential in RAVIS, so the two agree on it.

    Idempotent: a PUT replaces, and the value is the same one every time because
    it is cached. Failure is reported and not fatal — an unnamed NERVIS still
    works, it is merely rate-limited like any other anonymous caller, and a
    launcher that refused to finish over a rate limit would be worse than the
    problem it was fixing.

    Returns a short description of what happened, for the start banner.
    """
    secret = nervis_ravis_credential()
    if not secret:
        return "could not be minted"
    body = json.dumps({"secret": secret}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{RAVIS_PORT}/api/v1/providers/credentials/client.nervis",
        data=body, method="PUT", headers={"content-type": "application/json"},
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
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
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
        return str(pid) in found.stdout
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


def start() -> int:
    ensure_venv()
    running = status(quiet=True)
    if all(running.values()):
        print("Already running.")
        print(f"  NERVIS's RAVIS credential: {teach_ravis_the_credential()}")
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
    print(f"\nNERVIS is a named caller to RAVIS: {teach_ravis_the_credential()}")

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
        named = [one["name"] for one in json.loads(declared["RAVIS_UPSTREAMS"])] \
            if "RAVIS_UPSTREAMS" in declared else ["default"]
        print(f"\nRAVIS upstreams defaulted to: {', '.join(named)}.")
        print(f"  Local runtime at {LM_STUDIO}; hosted ones are the providers this")
        print("  machine already holds a key for. Set RAVIS_UPSTREAMS to override.")
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
        pid = int(record.get("pid", 0) or 0)
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
TRANSPARENT_KINDS = ("openrouter", "openai")


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


COMMANDS = {"start": start, "stop": stop, "status": lambda: (status(), 0)[1]}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    if action not in COMMANDS:
        print(f"usage: {Path(__file__).name} [start|stop|status]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(COMMANDS[action]())
