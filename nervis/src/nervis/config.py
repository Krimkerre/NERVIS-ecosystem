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
    sirvis_base_url: str = "http://127.0.0.1:8721"


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
