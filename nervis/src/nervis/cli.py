"""The command line.

`nervis doctor` and `nervis serve` are M0's exit criteria, and the constraint
that shaped `doctor` is that **nothing has to be running** for it to be useful.
It reports what it finds — a peer that is absent is a finding rather than a
failure — which is the property that matters during an incident, when the thing
being diagnosed is that something will not answer.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

import uvicorn
from ecosystem_protocol import PROTOCOL_VERSION, configure_logging

from nervis.app import create_app
from nervis.config import ConfigurationReport, Settings, inspect_configuration
from nervis.ecosystem import DECLARED
from nervis.registry import admissible, declared_services
from nervis.storage import prepare_database
from nervis.web import DASHBOARD

EXIT_OK = 0
EXIT_FATAL_CONFIGURATION = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nervis", description="Ecosystem control plane")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the API and serve the dashboard")
    commands.add_parser("doctor", help="report on this configuration without needing a peer")

    arguments = parser.parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    if arguments.command == "doctor":
        return _doctor(settings)
    return _serve(settings)


def _serve(settings: Settings) -> int:
    """Start the service, refusing an unsafe configuration first.

    The check runs before the port is bound rather than after: a service that
    binds and then discovers it should not have is a service that was briefly
    reachable, and briefly is enough.
    """
    report = inspect_configuration(settings)
    if not report.is_startable:
        _print_findings(report)
        return EXIT_FATAL_CONFIGURATION
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)
    return EXIT_OK


def _doctor(settings: Settings) -> int:
    """Everything that can be said about this installation without a peer.

    Deliberately in this order: what is wrong, then what is stored, then what is
    advertised, then where the peers would be. An operator running this has a
    question, and the answer is usually in the first two sections.
    """
    report = inspect_configuration(settings)
    _print_findings(report)
    _print_storage(settings)
    _print_capabilities()
    _print_peers(settings)
    if not report.is_startable:
        return EXIT_FATAL_CONFIGURATION
    print("\nconfiguration is serveable")
    return EXIT_OK


def _print_findings(report: ConfigurationReport) -> None:
    if not report.findings:
        print("configuration: nothing to report")
        return
    print("findings")
    for finding in report.findings:
        marker = "FATAL  " if finding.fatal else "note   "
        print(f"  {marker}{finding.setting}: {finding.message}")


def _print_storage(settings: Settings) -> None:
    """Open and migrate the database, which is the only way to know it migrates.

    `doctor` does the real thing rather than checking that the file exists,
    because "the database migrates cleanly" is M0's exit criterion and a report
    that inspects a path proves nothing about the schema inside it.
    """
    database = prepare_database(settings.database_path)
    print(f"\ndatabase  {settings.database_path}")
    print(f"  migrated to version {database.version}")
    print(f"  dashboard {'found' if DASHBOARD.is_file() else 'MISSING'} at {DASHBOARD}")


def _print_capabilities() -> None:
    """What NERVIS advertises, and the reason attached to each.

    Printed in full rather than summarised. §4.1's whole argument is that an
    honest "not yet, because M6" is more useful than a status light, and that
    argument applies to a person reading a terminal as much as to a peer.
    """
    print(f"\ncapabilities (protocol {PROTOCOL_VERSION})")
    for capability_id, capability in sorted(DECLARED.items()):
        print(f"  {capability.state:<12} {capability_id:<30} {capability.reason}")


def _print_peers(settings: Settings) -> None:
    """Where NERVIS would look, without looking.

    Nothing here is contacted, and that stayed true when M2 added probing:
    `doctor` has to work with the whole ecosystem down, and a command that
    probed would take one timeout per stopped service before printing anything.
    The running service probes on a timer; this prints what it would probe.

    Endpoints the SSRF guard refuses are printed with the rule they broke, which
    is the only place they are visible before the service starts.
    """
    declarations = declared_services(settings)
    admitted, refused = admissible(declarations, settings.allowed_hosts)
    print("\nregistry (not contacted — the running service probes on a timer)")
    for declaration in admitted:
        kind = "MEP" if declaration.mep else "reachability only"
        print(f"  {declaration.label:<16} {declaration.base_url:<28} {kind}")
    if not refused:
        return
    print("\n  REFUSED — these will not be probed at all")
    for key, reason in refused:
        print(f"    {key}: {reason}")


if __name__ == "__main__":
    sys.exit(main())
