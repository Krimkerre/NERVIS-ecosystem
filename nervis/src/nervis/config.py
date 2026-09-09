"""Settings, and the startup checks that refuse to serve on an unsafe one.

Configuration is read once at startup and treated as immutable afterwards.
Nothing here contacts a network: `nervis doctor` has to be runnable with no
other service running, which is also what makes it useful during an incident —
and M0's exit requires NERVIS to start with **no external service required**.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

from pydantic_settings import BaseSettings, SettingsConfigDict

# Loopback for every service in this ecosystem (ECOSYSTEM_RUNBOOK.md §9). 8790
# is the port `tools/run.py` already assigns NERVIS; taking it from there rather
# than picking a new one, because a launcher and a service disagreeing about a
# port produces a dashboard that reports everything as down.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790


class Settings(BaseSettings):
    """Everything NERVIS reads from the environment.

    Field names double as environment variable names with the NERVIS_ prefix,
    so `database_path` is `NERVIS_DATABASE_PATH`. Defaults are the safe choice
    in every case: local-only, no credential required, no peer assumed.
    """

    model_config = SettingsConfigDict(env_prefix="NERVIS_", env_file=".env", extra="ignore")

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    database_path: str = "nervis.db"

    # Structured JSON to stdout, at this level. The process is supervised by
    # something that owns log placement and rotation (`tools/run.py` today), so
    # NERVIS writes no files of its own.
    log_level: str = "INFO"

    # ── Remote exposure (runbook §9, §15) ────────────────────────────────────
    # **The TLS settings were deleted with the rule that referenced them.** They
    # were read by nothing — `cli.py` never passed them to `uvicorn.run`, which
    # is the defect that closed this door — and `check_dead_code.py` puts it
    # exactly right: a field like that "makes something look implemented".
    # Keeping them for the remote mode §16 item 2 will build would be keeping the
    # affordance that caused this. Two lines come back when something reads them.
    # `extra="ignore"` means a stale env var is harmless in the meantime.
    #
    # `client_credential` stays because RAVIS's `identity.py` genuinely reads it.
    # In NERVIS and SIRVIS nothing does, which is its own finding rather than
    # this one — see §16 item 4.
    client_credential: str = ""

    # ── The peers NERVIS reads (§5) ──────────────────────────────────────────
    # Absent is a legitimate state and M0's exit requires it: NERVIS must start,
    # migrate and answer `/ecosystem/*` with nothing else running. A peer that
    # is absent is reported as absent, never as broken. M2 owns the probing;
    # these exist at M0 so `doctor` can print where it *would* look.
    ravis_base_url: str = "http://127.0.0.1:8731"
    # The credential NERVIS presents to RAVIS, so RAVIS resolves it to the
    # `nervis` application rather than to `anonymous` (RAVIS §9.6.0).
    #
    # **This is what makes a background call possible at all.** §9.6.1 honours
    # the background marker only from an authenticated identity, so without a
    # credential NERVIS's conversation titles arrive as ordinary work and can be
    # billed to whatever `ravis/auto` picks — the exact outcome NERVIS.md §7
    # calls out: *an untitled conversation is a smaller failure than a title
    # billed to a frontier model.*
    #
    # Empty by default, and empty means NERVIS stays anonymous and simply does
    # not generate titles. A missing credential must not turn the feature into
    # an expensive one.
    ravis_client_credential: str = ""

    # The credential NERVIS presents to RAVIS when the operator saves or removes
    # a provider key (§15.1). Separate from the one above on purpose and for the
    # same reason SIRVIS's `admin` scope is separate from its `benchmark` one:
    # calling the gateway and re-pointing the keys it calls with are different
    # powers, and NERVIS holding one must not imply the other.
    #
    # Empty by default, and empty is a working state — the Credentials screen
    # says NERVIS holds no admin credential rather than failing at the call. The
    # launcher mints one and passes it, so an ordinary install has it.
    ravis_admin_credential: str = ""

    # The one directory chat may read from and write into.
    #
    # **A wall rather than a rule the model follows.** A document is retrieved
    # content (§11.5), and a file can ask to be given another file as easily as
    # it can say anything else — so the boundary is a path comparison in
    # `workspace.py` and not a sentence in a prompt.
    #
    # Empty means the feature is off, which is the right default for something
    # that reads a person's files: an install that was never asked to do this
    # does not do it, and the capability says so rather than the first request
    # discovering it.
    workspace_path: str = ""
    # **The workspace has four rooms, and which one a file is in says how it
    # got there.** `import` arrived through chat and belongs to a conversation;
    # `export` is what NERVIS produced; `library` is what somebody keeps, which
    # nothing sweeps; `clarvis` is what the editor opens.
    #
    # Everything used to go in one directory: an uploaded PDF, a PDF chat
    # wrote, a generated picture and the editor's own folder, all mixed.
    # Separating them makes the place readable without reading the files.
    #
    # Each is empty by default and means the obvious subdirectory of
    # `workspace_path`, so a deployment that set one path still gets all four
    # and nobody has to configure a layout to get a sensible one.
    workspace_import_path: str = ""
    workspace_export_path: str = ""
    workspace_library_path: str = ""
    # Places the Files tab may reach *besides* the workspace, as
    # `name=/path,name=/path`. A mounted NAS share is the case this exists for:
    # `NERVIS_FILE_PLACES="nas=/Volumes/nervis"`.
    #
    # **Named explicitly rather than discovered.** Listing `/Volumes` and
    # offering whatever is mounted would put a colleague's USB stick and a
    # Time Machine disk in a file manager that is meant to reach two things,
    # and "it was mounted" is not the same as "somebody meant NERVIS to write
    # there". Every place here was typed by the operator.
    file_places: str = ""

    # Where the launcher writes each service's stdout and stderr — §11.3's
    # fourth and last data source. Empty means no adapter exists, which is the
    # right default for an install started some other way: `tools/run.py` is
    # what documents this directory, and a NERVIS launched by hand has no
    # grounds to guess where somebody else's logs went.
    #
    # Set by the launcher, so a stack started the documented way has it and one
    # started by hand honestly reports no source rather than reading a path
    # nobody promised.
    run_directory: str = ""

    # The credential NERVIS presents to SIRVIS when it carries out a confirmed
    # command (§12). Narrow on purpose: `benchmark` scope, minted by the
    # launcher, and nothing else — §4.5 separates SIRVIS's scopes by what they
    # cost, and a control plane holding an admin token is a control plane whose
    # compromise is total.
    #
    # **Empty means the operation does not exist.** §12: "each family of control
    # operations carries its own switch, every switch defaults to off". An
    # install that never configures this can be asked for a benchmark and will
    # say it has no credential, which is the honest answer and not a silent
    # refusal.
    sirvis_client_credential: str = ""
    # **Separate from the one above, and used on exactly one call.** Deleting a
    # measurement needs `admin` on SIRVIS, and `admin` implies every other scope
    # — so folding it into the benchmark credential would silently upgrade the
    # queue path to total access. Two settings means the credential that spends
    # machine time cannot also erase what it produced, and an install that
    # configures neither can do neither.
    sirvis_admin_credential: str = ""
    sirvis_base_url: str = "http://127.0.0.1:8721"
    # The Clarvis Bridge is per extension host, so this is only the first
    # instance. §5.1 lists it among the initial registry entries because an
    # optional peer that is absent should read as absent rather than be missing
    # from the list entirely.
    # **Kept, and no longer probed.** Setting it is how an operator says "there
    # is a Bridge at this exact address" — a fixed deployment rather than the
    # per-window one §6.6 describes. Nothing in the default path reads it, since
    # a Bridge announces its own port at registration.
    clarvis_base_url: str = "http://127.0.0.1:7071"

    # Where code-server serves the editor NERVIS embeds in its Clarvis tab.
    #
    # **Configuration, not an assumption.** The runbook's port table says
    # code-server is "pinned by its own deployment, proxied, never assumed", so
    # this is a setting with a default rather than a constant — and the default
    # is 8080 because that is code-server's own, which is what somebody who
    # installed it and ran it will have.
    code_server_base_url: str = "http://127.0.0.1:8080"
    # Which directories `/code/`'s proxy will open, comma-separated. Empty
    # falls back to `workspace_path`, so a deployment that already said where
    # the workspace is does not have to say it twice — and a deployment that
    # said neither gets a proxy that refuses to open anything, which is the
    # right default for a surface whose whole risk is what it exposes.
    code_workspace_roots: str = ""
    # Which subdirectory of the workspace the editor gets when the roots above
    # say nothing. Named rather than assumed for the same reason the two above
    # are: the editor's folder is a room in the workspace, not the workspace.
    code_workspace_subdirectory: str = "clarvis"
    # §13.5's remaining four. **The tab is on by default** because a NERVIS
    # with code-server running and the tab switched off is a stranger surprise
    # than one that offers a tab and says the editor is absent — and the tab
    # already says exactly that when it is.
    code_tab_enabled: bool = True
    # Whether the Code tab frames the editor through NERVIS's own proxy
    # (`/code/`) or at code-server's own address.
    #
    # **Off by default, and the reason is the editor's data rather than the
    # proxy's quality.** VS Code's web build keeps its state — secrets,
    # history, settings, trust decisions — in the browser's IndexedDB, which is
    # scoped to an *origin*. Framing the same editor through NERVIS changes
    # that origin, so a workspace that had provider keys in it opens with none
    # of them: nothing is deleted, and everything is invisible. That happened
    # to a real operator on the day the proxy shipped, which is why this is a
    # setting rather than a silent improvement. A new deployment can turn it on
    # and lose nothing; an existing one moves its keys deliberately or not at
    # all.
    code_proxy_enabled: bool = False
    # Where code-server is, when it is not on `PATH`. Empty means "look on
    # PATH", which is the same order `tools/run.py` uses.
    code_server_binary: str = ""
    # The Clarvis package NERVIS may install into that editor.
    clarvis_vsix_path: str = ""
    # Whether to install or update it at startup. **Off by default**: putting
    # software into an editor somebody else manages is not a thing to do
    # because a default said so. An operator who wants it says so, and the
    # Code tab offers the same action on demand either way.
    clarvis_auto_install: bool = False
    # Runtimes rather than ecosystem members: neither publishes a MEP surface,
    # so the registry can claim reachability about them and nothing more.
    lmstudio_base_url: str = "http://127.0.0.1:1234"
    ollama_base_url: str = "http://127.0.0.1:11434"

    # Hosts NERVIS may probe beyond loopback. Empty by default, which is §5.1's
    # SSRF rule: a control plane holds a list of URLs and fetches every one on a
    # timer, so an unconstrained list is a request-forgery primitive with a
    # scheduler attached.
    allowed_hosts: list[str] = []

    # Host names NERVIS itself answers to (§16 item 5). Distinct from
    # `allowed_hosts` directly above, which is the *outbound* list of peers
    # NERVIS may probe — the two were nearly given the same name, and a reader
    # who conflated them would think this service already checked its own.
    #
    # NERVIS is the one opened in a browser, which makes it the richest target
    # for DNS rebinding: it proxies to the other two and holds RAVIS's admin
    # credential.
    served_hosts: list[str] = ["127.0.0.1", "localhost", "::1"]

    # How often the registry re-probes, and how long an entry stays believable
    # without one. The second must exceed the first or every entry would spend
    # part of each cycle stale.
    probe_interval_seconds: float = 20.0
    stale_after_seconds: float = 90.0
    # How soon to re-probe during the startup window below. A launcher starts
    # the services in sequence, so NERVIS's first pass routinely catches a peer
    # mid-startup — and at the ordinary interval the dashboard then reports it
    # unreachable for twenty seconds after it is up.
    recovery_interval_seconds: float = 3.0

    # §11.1: retention is bounded and configurable, 7–30 days a sensible
    # default. Two bounds because they fail differently — age alone lets a burst
    # fill a disk inside the window, and a count alone keeps a quiet week
    # forever.
    event_retention_days: float = 14.0
    event_retention_count: int = 50_000
    # And how long that fast window lasts, which is a **hard bound rather than a
    # condition**. The first version sped up whenever anything looked unwell,
    # which is a loop with no exit: probing four MEP endpoints every three
    # seconds against three services is eighty requests a minute, RAVIS's
    # anonymous limit is sixty, and being rate limited reads as unwell — so the
    # fast interval kept itself on and NERVIS produced its own outage. A window
    # that closes on the clock cannot do that.
    startup_window_seconds: float = 30.0


@dataclass(frozen=True)
class ConfigurationFinding:
    """One thing worth saying about a configuration.

    `fatal` is the whole point of the type. A finding that merely wants
    attention and one that must stop the process are different things, and
    collapsing them means either serving on an unsafe bind or refusing to start
    over a note.
    """

    setting: str
    message: str
    fatal: bool = False


@dataclass
class ConfigurationReport:
    findings: list[ConfigurationFinding] = field(default_factory=list)

    @property
    def is_startable(self) -> bool:
        return not any(finding.fatal for finding in self.findings)


def inspect_configuration(settings: Settings) -> ConfigurationReport:
    """Everything worth saying about this configuration, without contacting anything.

    A query: it reports and changes nothing (runbook §14.2). `serve` calls it
    and refuses to start on a fatal finding; `doctor` calls it and prints.
    """
    report = ConfigurationReport()
    if not _is_loopback(settings.host):
        _check_remote_exposure(settings, report)
    return report


def _is_loopback(host: str) -> bool:
    """Whether this address reaches only this machine.

    `0.0.0.0` and `::` are the ones that matter and neither is loopback, so an
    unparseable host is treated as remote: the safe direction for a check whose
    failure mode is publishing a control plane.
    """
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "localhost."}


def _check_remote_exposure(settings: Settings, report: ConfigurationReport) -> None:
    """Refuse a bind that is not loopback, whatever else is configured.

    **This used to require TLS and a credential, and that rule was satisfiable
    without being true.** `cli.py` calls `uvicorn.run` without `ssl_certfile` or
    `ssl_keyfile`, so a NERVIS naming a certificate and a key served its control
    plane in cleartext regardless: the configuration was validated at startup and
    then never applied. `client_credential` had the matching hole — checked once
    here, read on no request path. An external audit found the same shape in all
    three services.

    Refusing is the honest failure. A remote mode that looks encrypted and is not
    is worse than no remote mode, because the operator stops looking. Remote
    operation returns when it is built and proven end to end — TLS actually wired
    to the listener, per-request authentication, Host and Origin validation, SSE
    held to the same rules, and a test against a real TLS listener —
    `ECOSYSTEM_RUNBOOK.md` §16 item 2 lists it.
    """
    report.findings.append(
        ConfigurationFinding(
            "NERVIS_HOST",
            f"{settings.host} is not loopback, and remote operation is not built"
            " yet — see ECOSYSTEM_RUNBOOK.md §16 item 2",
            fatal=True,
        )
    )

