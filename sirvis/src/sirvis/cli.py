"""`sirvis doctor` and `sirvis serve` — §18, and M0's exit criterion.

`doctor` contacts nothing. That constraint is the feature: it has to be runnable
on a laptop with no runtime installed and useful during an incident, when the
thing being diagnosed is that something will not answer.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

import uvicorn
from ecosystem_protocol import configure_logging

from sirvis.app import create_app
from sirvis.config import ConfigurationReport, Settings, inspect_configuration
from sirvis.storage import prepare_database

EXIT_OK = 0
EXIT_FATAL_CONFIGURATION = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code rather than calling sys.exit.

    Returning the code keeps this testable: a test can assert on the value
    without trapping SystemExit.
    """
    arguments = _build_parser().parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    if arguments.command == "doctor":
        return _run_doctor(settings)
    if arguments.command == "token":
        return _run_token(settings, arguments.mint, arguments.scopes)
    return _run_serve(settings)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sirvis", description="Local model evidence plane")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "doctor", help="check configuration and the database, contacting nothing"
    )
    subcommands.add_parser("serve", help="run the service")
    token = subcommands.add_parser(
        "token", help="show the bootstrap API token, or mint a scoped one"
    )
    token.add_argument("--mint", metavar="LABEL", help="mint a new token under this label")
    token.add_argument(
        "--scopes", default="read",
        help="space-separated scopes for --mint: read, benchmark, runtime, admin",
    )
    return parser


def _run_doctor(settings: Settings) -> int:
    """Print what SIRVIS would do with this configuration, contacting nothing."""
    report = inspect_configuration(settings)
    _print_findings(report)
    _print_database(settings)
    _print_runtime_intent(settings)
    if not report.is_startable():
        # Flush first: the two streams are buffered independently, so without
        # this the refusal reaches the terminal before the findings it tells the
        # reader to look above for.
        sys.stdout.flush()
        print("\nrefusing to serve: fix the fatal findings above", file=sys.stderr)
        return EXIT_FATAL_CONFIGURATION
    print("\nconfiguration is serveable")
    return EXIT_OK


def _print_findings(report: ConfigurationReport) -> None:
    print(f"listen           {'loopback' if not report.fatal_findings else 'see findings'}")
    if not report.findings:
        print("findings         none")
        return
    print("findings")
    for finding in report.findings:
        marker = "FATAL  " if finding.fatal else "note   "
        print(f"  {marker}{finding.setting}: {finding.message}")


def _print_database(settings: Settings) -> None:
    """Migrate and report the version.

    `doctor` migrating is deliberate. The alternative is discovering a failed
    migration when the first request arrives, which is the worst moment and the
    least informative place.
    """
    database = prepare_database(settings.database_path)
    print(f"database         {settings.database_path} at migration {database.version}")


def _print_runtime_intent(settings: Settings) -> None:
    """Say which runtime *would* be measured, without asking whether it is there.

    An address is configuration; reachability is a fact about right now, and
    §15.4 requires SIRVIS to work standalone. Printing "configured" rather than
    "reachable" is the difference between a report that is true offline and one
    that lies whenever LM Studio happens to be closed.
    """
    print(f"runtime          lmstudio configured at {settings.lmstudio_base_url} (not contacted)")


def _run_token(settings: Settings, mint_label: str | None, scope_names: str) -> int:
    """Show or mint an API token (§4.5).

    The only place a token's plaintext is ever revealed, and only at the moment
    it is created. A token already minted cannot be shown again — the database
    holds a hash, not the value — so this prints what exists and how to replace
    it rather than pretending to recover it.
    """
    from sirvis.api.security import Scope, ensure_bootstrap_token, mint_token, token_summary

    database = prepare_database(settings.database_path)
    if mint_label:
        try:
            scopes = {Scope(name) for name in scope_names.split()}
        except ValueError:
            print(f"unknown scope in {scope_names!r}; valid: "
                  f"{', '.join(s.value for s in Scope)}", file=sys.stderr)
            return EXIT_FATAL_CONFIGURATION
        print(mint_token(database, mint_label, scopes))
        print(f"\n  label  {mint_label}\n  scopes {' '.join(sorted(s.value for s in scopes))}",
              file=sys.stderr)
        print("  This is the only time it can be read.", file=sys.stderr)
        return EXIT_OK

    fresh = ensure_bootstrap_token(database)
    if fresh:
        print(fresh)
        print("\n  label  bootstrap\n  scopes admin", file=sys.stderr)
        print("  This is the only time it can be read.", file=sys.stderr)
        return EXIT_OK

    print("a bootstrap token already exists and cannot be shown again — only a hash is stored",
          file=sys.stderr)
    for entry in token_summary(database):
        print(f"  {entry['label']:<16} {' '.join(entry['scopes'])}", file=sys.stderr)
    print("\n  mint another with: sirvis token --mint <label> --scopes \"read runtime\"",
          file=sys.stderr)
    return EXIT_OK


def _run_serve(settings: Settings) -> int:
    """Refuse an unsafe configuration, then serve."""
    report = inspect_configuration(settings)
    if not report.is_startable():
        _print_findings(report)
        print("\nrefusing to serve: fix the fatal findings above", file=sys.stderr)
        return EXIT_FATAL_CONFIGURATION
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)
    return EXIT_OK
