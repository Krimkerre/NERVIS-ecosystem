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
    # Binding beyond loopback requires BOTH. Either alone fails to start, which
    # is the trap the rule exists to close: a credential-only bind passes every
    # other check and then serves a control plane in cleartext. NERVIS is the
    # service where this matters most — it is the one with buttons.
    client_credential: str = ""
    tls_certificate_path: str = ""
    tls_key_path: str = ""

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
    # Runtimes rather than ecosystem members: neither publishes a MEP surface,
    # so the registry can claim reachability about them and nothing more.
    lmstudio_base_url: str = "http://127.0.0.1:1234"
    ollama_base_url: str = "http://127.0.0.1:11434"

    # Hosts NERVIS may probe beyond loopback. Empty by default, which is §5.1's
    # SSRF rule: a control plane holds a list of URLs and fetches every one on a
    # timer, so an unconstrained list is a request-forgery primitive with a
    # scheduler attached.
    allowed_hosts: list[str] = []

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
    """§15's rule for a NERVIS that is not loopback-only.

    Each missing piece is reported separately, so an operator is told what to
    add rather than that something is wrong.
    """
    report.findings.append(
        ConfigurationFinding(
            "NERVIS_HOST",
            f"{settings.host} is not loopback; §15 requires TLS and a credential",
        )
    )
    if not settings.client_credential:
        report.findings.append(
            ConfigurationFinding(
                "NERVIS_CLIENT_CREDENTIAL",
                "a remote bind requires a client credential",
                fatal=True,
            )
        )
    if not (settings.tls_certificate_path and settings.tls_key_path):
        report.findings.append(
            ConfigurationFinding(
                "NERVIS_TLS_CERTIFICATE_PATH",
                "a remote bind requires TLS; set both the certificate and the key",
                fatal=True,
            )
        )
