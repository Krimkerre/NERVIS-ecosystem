"""The command line (§18).

`doctor` and `serve` are M0's exit criterion; `benchmark run` is M6's.

The constraint that shaped `doctor` is that **nothing has to be running** for it
to be useful — not that it contacts nothing, which is what this said until an
audit checked. It does contact the runtime and reports what it finds; an absent
runtime is a finding rather than a failure. That is the property that matters on
a laptop with no runtime installed, and during an incident, when the thing being
diagnosed is that something will not answer.

**`benchmark run` asks before it loads anything, and that is not politeness.**
Four models were loaded onto the developer's machine during this build without
anyone intending it — a test suite reaching a live service, a `generate` call
JIT-loading, a CLI shelling out — and none of them registered as "loading a
model" at the time. A multi-gigabyte load is slow, changes what else fits in
memory, and is exactly the kind of thing a person wants to be told about before
it happens rather than after. `--yes` skips the prompt for scripted use.
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys
import uuid
from typing import TYPE_CHECKING, Awaitable, Sequence

import httpx
import uvicorn
from ecosystem_protocol import EventPublisher, configure_logging

from sirvis.app import create_app
from sirvis.config import ConfigurationReport, Settings, inspect_configuration
from sirvis.core.machine import machine_identity
from sirvis.storage import Database, prepare_database
from sirvis.telemetry import detect_system

if TYPE_CHECKING:  # imported for types only — see `_run_benchmark` on why the
    # real imports are deferred: nothing that starts a benchmark should be paid
    # for by `sirvis doctor`, which is the command run when things are broken.
    from typing import Any

    from sirvis.benchmarks import ExperimentOutcome, ExperimentSpec

EXIT_OK = 0
# A benchmark that ran and failed is a different outcome from a configuration
# that would not let it start, and a script driving this needs to tell them
# apart: one is worth retrying, the other is not.
EXIT_BENCHMARK_FAILED = 1
EXIT_FATAL_CONFIGURATION = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code rather than calling sys.exit.

    Returning the code keeps this testable: a test can assert on the value
    without trapping SystemExit.
    """
    arguments = _build_parser().parse_args(argv)
    settings = Settings()
    configure_logging(settings.log_level)
    # httpx logs a line per request at INFO, which is right for a running
    # service and wrong for a diagnostic: `doctor` prints a report, and three
    # JSON log lines interleaved through it obscure the thing the operator ran
    # it to read. The service keeps them; the CLI does not.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if arguments.command == "doctor":
        return _run_doctor(settings)
    if arguments.command == "token":
        return _run_token(settings, arguments.mint, arguments.scopes)
    if arguments.command == "benchmark":
        return _run_benchmark(settings, arguments)
    if arguments.command == "results":
        return _run_results(settings, arguments.limit)
    return _run_serve(settings)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sirvis", description="Local model evidence plane")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "doctor", help="check configuration and the database, and report what the runtime says"
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

    benchmark = subcommands.add_parser("benchmark", help="run a benchmark experiment")
    benchmark_commands = benchmark.add_subparsers(dest="benchmark_command", required=True)
    run = benchmark_commands.add_parser("run", help="run one experiment specification")
    run.add_argument("specification", help="path to a YAML experiment specification (§11.5)")
    run.add_argument(
        "--model", default=None,
        help="override the model the specification names, so an example file stays runnable",
    )
    run.add_argument("--warmups", type=int, default=None, help="override warmup runs")
    run.add_argument(
        "--repetitions", type=int, default=None, help="override measured repetitions"
    )
    run.add_argument(
        "--tool-trials", action="store_true",
        help=(
            "run the tool-call trials alongside whatever this specification "
            "measures, without declaring a product role. Tool-call reliability "
            "is a property of the build rather than of one consumer's workload, "
            "and this is how it is measured for a build nobody has assigned yet"
        ),
    )
    run.add_argument(
        "--role", default=None,
        help=(
            "the role this run is filed under, overriding the specification's. "
            "Evidence is keyed on a role (§12.2) and consumers ask by one, so "
            "measuring a build *for* a role means saying which — and with "
            "--tool-trials this is how a trial is recorded for any role rather "
            "than only the one a product workload happens to declare"
        ),
    )
    run.add_argument(
        "--clarvis-role", default=None, choices=["clarvis-chat", "clarvis-agent"],
        help="run Clarvis's own role workload instead of the specification's tests (M13); "
             "the agent role also runs the tool-call trials",
    )
    run.add_argument(
        "--runtime-set", default=None,
        help="run the specification's tests against a stored Runtime Set (M10): "
             "every member is measured alone, then co-resident in §11.3's modes",
    )
    run.add_argument(
        "--yes", "-y", action="store_true",
        help="do not ask before loading the model into the runtime",
    )

    results = subcommands.add_parser("results", help="show recent benchmark runs")
    results_commands = results.add_subparsers(dest="results_command", required=True)
    latest = results_commands.add_parser("latest", help="the most recent runs")
    latest.add_argument("--limit", type=int, default=5)
    return parser


def _run_doctor(settings: Settings) -> int:
    """Everything §18 says `doctor` checks, in one pass.

    §18 lists Apple Silicon, macOS, RAM, disk, LM Studio, the LM Studio API,
    installed models, GGUF and MLX capability, the database and the results
    directory. It reported configuration and migrations only — M1 and M2 landed
    and this was never revisited, which is the same drift STATUS.md exists to
    catch and did not, because nothing counts CLI output.

    **It contacts the runtime, and an absent runtime is a finding rather than a
    failure.** That reconciles §18 with §15.4's standalone requirement: a laptop
    with LM Studio closed is the ordinary case, and doctor is most useful
    precisely when something is not answering.
    """
    report = inspect_configuration(settings)
    _print_findings(report)
    _print_machine()
    _print_database(settings)
    _print_results_directory(settings)
    _print_runtime(settings)
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


def _print_results_directory(settings: Settings) -> None:
    """Where raw results will be written (§11.9), and whether it is usable.

    §18 asks doctor to check this. Reported rather than created: a directory
    appearing as a side effect of a diagnostic is a surprise, and the benchmark
    engine that owns it can make it when it needs it.
    """
    location = pathlib.Path(settings.results_path).expanduser()
    if not location.exists():
        print(f"results          {location} (will be created on first run)")
        return
    writable = os.access(location, os.W_OK)
    runs = sum(1 for _ in location.iterdir()) if writable else 0
    print(f"results          {location} · {runs} experiment(s)"
          f"{'' if writable else ' — NOT WRITABLE'}")


def _print_database(settings: Settings) -> None:
    """Migrate and report the version.

    `doctor` migrating is deliberate. The alternative is discovering a failed
    migration when the first request arrives, which is the worst moment and the
    least informative place.
    """
    database = prepare_database(settings.database_path)
    print(f"database         {settings.database_path} at migration {database.version}")


def _print_machine() -> None:
    """The M1 snapshot: what this machine is, and what could not be read.

    §18 asks for Apple Silicon, RAM and disk. Anything the machine declines to
    report prints as `unknown` rather than as a plausible number — M1's rule,
    and the reason a snapshot is worth anything.
    """
    snapshot = detect_system()
    memory = _gigabytes(snapshot.unified_memory_bytes)
    free = _gigabytes(snapshot.disk_free_bytes)
    total = _gigabytes(snapshot.disk_total_bytes)
    print(f"\nmachine          {snapshot.chip or 'unknown chip'} · "
          f"{snapshot.platform_name} {snapshot.os_version or '?'}")
    print(f"  apple silicon  {'yes' if snapshot.is_apple_silicon else 'no'}")
    print(f"  cores          {snapshot.cpu_cores or '?'} cpu "
          f"({snapshot.performance_cores or '?'}P/{snapshot.efficiency_cores or '?'}E), "
          f"{snapshot.gpu_cores or '?'} gpu")
    print(f"  memory         {memory} unified · thermal {snapshot.thermal_state or 'unknown'}")
    print(f"  disk           {free} free of {total}")
    if snapshot.unknown_fields:
        print(f"  unknown        {', '.join(snapshot.unknown_fields)}")


def _print_runtime(settings: Settings) -> None:
    """Whether the runtime answers, and what it holds (§18).

    Contacting it is the point — "configured" tells an operator nothing they did
    not already type. An unreachable runtime prints as stopped with the reason
    and is never fatal, because §15.4 requires SIRVIS to work standalone.
    """
    import asyncio

    from sirvis.core.inventory import build_inventory
    from sirvis.runtimes import LMStudioAdapter, RuntimeUnavailableError

    adapter = LMStudioAdapter(
        base_url=settings.lmstudio_base_url, lms_path=settings.lmstudio_cli_path
    )
    print(f"\nruntime          lmstudio at {settings.lmstudio_base_url}")

    async def probe() -> tuple[str, list[dict[str, object]]]:
        info = await adapter.health()
        if info.state.value != "ready":
            return info.state.value + (f" — {info.detail}" if info.detail else ""), []
        return "ready", await adapter.list_models()

    try:
        state, records = asyncio.run(probe())
    except RuntimeUnavailableError as failure:
        state, records = f"unreachable — {failure}", []

    print(f"  api            {state}")
    if not records:
        print("  installed      none visible (SIRVIS works standalone; §15.4)")
        return

    inventory = build_inventory(records)
    formats: dict[str, int] = {}
    for variant in inventory.variants.values():
        formats[variant.runtime_format or "unknown"] = (
            formats.get(variant.runtime_format or "unknown", 0) + 1
        )
    print(f"  installed      {len(inventory.installed)} build(s), "
          f"{len(inventory.families)} famil(y/ies)")
    # §18 asks for GGUF and MLX capability specifically, because a machine with
    # only one of them cannot produce the format comparison §19 is built around.
    for wanted in ("gguf", "mlx"):
        count = formats.get(wanted, 0)
        print(f"  {wanted:<14} {count} build(s)" + ("" if count else "  — no comparison possible"))
    print(f"  loaded         {len(inventory.instances)} instance(s)")


def _gigabytes(value: int | None) -> str:
    """Bytes as GB, or `unknown` — never a zero standing in for a gap."""
    return f"{value / 2**30:.1f} GB" if value else "unknown"


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


def _with_role(spec: Any, arguments: argparse.Namespace) -> Any:
    """Apply the three flags that decide what a run measures and files it under.

    Lifted out of `_run_benchmark` when adding `--role` took it past ruff's
    complexity 8. They belong together anyway: all three answer one question —
    what is being measured, and about which role.
    """
    import dataclasses

    if arguments.tool_trials:
        # **Independent of the role, deliberately.** A tool-call trial counts
        # whether a build emits a well-formed call for eight phrasings of one
        # request; that is a fact about the build, not about Clarvis's agent
        # workload. Requiring a product role to obtain it is how "which roles
        # can this build do" became unanswerable without picking one first.
        spec = dataclasses.replace(spec, tool_trials=True)

    if arguments.role:
        # Applied before `--clarvis-role`, which replaces the whole
        # specification and names its own role. Passing both would be asking for
        # two different runs, and the one that supplies the workload wins.
        spec = dataclasses.replace(spec, role=arguments.role)

    if arguments.clarvis_role:
        # The role supplies the whole experiment — Clarvis's own scenes, and the
        # tool-call trials for the agent — so the specification file contributes
        # only the model and the repetition counts.
        from sirvis.benchmarks.clarvis_roles import role_spec

        spec = role_spec(
            arguments.clarvis_role, spec.model_key,
            warmups=spec.warmups, repetitions=spec.repetitions,
        )
    return spec


def _run_benchmark(settings: Settings, arguments: argparse.Namespace) -> int:
    """M6's exit criterion: run one specification and persist a valid result.

    Everything the run needs is built here rather than through `create_app`:
    a benchmark does not serve HTTP, and starting a web application to run one
    would put a listening socket on a machine whose memory is about to be spent
    on a model.
    """
    import asyncio
    import dataclasses

    from sirvis.benchmarks import load_experiment, run_experiment
    from sirvis.errors import SirvisError
    from sirvis.resources import ResourceManager
    from sirvis.runtimes import LMStudioAdapter
    from sirvis.storage import RunState, reconcile_interrupted

    try:
        spec = load_experiment(arguments.specification)
    except SirvisError as failure:
        print(f"error: {failure.message}", file=sys.stderr)
        return EXIT_FATAL_CONFIGURATION

    overrides = {
        name: value
        for name, value in (
            ("model_key", arguments.model),
            ("warmups", arguments.warmups),
            ("repetitions", arguments.repetitions),
        )
        if value is not None
    }
    spec = dataclasses.replace(spec, **overrides)

    spec = _with_role(spec, arguments)

    if arguments.runtime_set:
        return _run_multi_benchmark(settings, arguments, spec)

    if not _confirm_load(spec, settings, assume_yes=arguments.yes):
        return EXIT_FATAL_CONFIGURATION

    database = prepare_database(settings.database_path)
    # The same reconciliation the service does, for the same reason: this
    # process is about to own the database, so an unfinished run belongs to a
    # dead one.
    for abandoned in reconcile_interrupted(database):
        print(f"marked interrupted run {abandoned} unrecoverable", file=sys.stderr)
    adapter = LMStudioAdapter(
        base_url=settings.lmstudio_base_url, lms_path=settings.lmstudio_cli_path
    )
    resources = ResourceManager(
        runtime=adapter,
        default_lease_seconds=settings.default_lease_seconds,
        max_loaded=settings.max_loaded_models,
    )
    events = _publisher(settings, database)
    try:
        outcome = asyncio.run(_run_and_publish(
            events,
            run_experiment(
                spec, runtime=adapter, resources=resources, database=database,
                results_root=settings.results_path, events=events,
            ),
        ))
    except SirvisError as failure:
        print(f"error: {failure.message}", file=sys.stderr)
        return EXIT_BENCHMARK_FAILED

    _print_outcome(outcome)
    return EXIT_OK if outcome.state is RunState.SUCCEEDED else EXIT_BENCHMARK_FAILED


def _run_multi_benchmark(
    settings: Settings, arguments: argparse.Namespace, spec: ExperimentSpec
) -> int:
    """M10: the specification's tests, against every member of a stored set.

    The YAML file supplies the *suite* — tests, warmups, repetitions — and the
    set supplies the *targets*. Reusing the single-model file format rather than
    inventing a second one means every suite already written can be pointed at a
    combination unchanged.
    """
    import asyncio

    from sirvis.benchmarks.multi import MultiModelSpec, run_multi_experiment
    from sirvis.errors import SirvisError
    from sirvis.resources import ResourceManager
    from sirvis.runtimes import LMStudioAdapter
    from sirvis.storage import RunState, reconcile_interrupted
    from sirvis.storage.runtime_sets import find_by_name, read_runtime_set

    database = prepare_database(settings.database_path)
    stored = read_runtime_set(database, arguments.runtime_set) or find_by_name(
        database, arguments.runtime_set
    )
    if stored is None:
        print(f"error: no runtime set named {arguments.runtime_set!r}", file=sys.stderr)
        return EXIT_FATAL_CONFIGURATION

    multi = MultiModelSpec.from_set(
        stored, suite_id=spec.suite_id, suite_version=spec.suite_version,
        tests=spec.tests, warmups=spec.warmups, repetitions=spec.repetitions,
    )
    if not _confirm_multi_load(multi, settings, assume_yes=arguments.yes):
        return EXIT_FATAL_CONFIGURATION

    for abandoned in reconcile_interrupted(database):
        print(f"marked interrupted run {abandoned} unrecoverable", file=sys.stderr)
    adapter = LMStudioAdapter(
        base_url=settings.lmstudio_base_url, lms_path=settings.lmstudio_cli_path
    )
    resources = ResourceManager(
        runtime=adapter,
        default_lease_seconds=settings.default_lease_seconds,
        max_loaded=settings.max_loaded_models,
    )
    try:
        outcome = asyncio.run(run_multi_experiment(
            multi, runtime=adapter, resources=resources, database=database,
            results_root=settings.results_path,
        ))
    except SirvisError as failure:
        print(f"error: {failure.message}", file=sys.stderr)
        return EXIT_BENCHMARK_FAILED

    print(f"run {outcome.run_id}: {outcome.state.value} — {outcome.detail}")
    print(f"results: {outcome.results_path}")
    for warning in outcome.warnings:
        print(f"warning: {warning}")
    # A co-residency failure exits non-zero even though results were written:
    # the run is a recorded finding either way, and the exit code answers the
    # different question a script is asking — "did the combination work?"
    return EXIT_OK if outcome.state is RunState.SUCCEEDED else EXIT_BENCHMARK_FAILED


def _confirm_multi_load(multi: Any, settings: Settings, assume_yes: bool) -> bool:
    """Name every model about to be loaded — all of them, before any of them.

    The single-model prompt names one model. This run will load each member
    alone and then all of them together, and the person at the keyboard is
    entitled to the whole bill before the first byte moves.
    """
    members = ", ".join(
        f"{member.role}={member.model_key}" for member in multi.per_role
    )
    print(
        f"about to load {len(multi.per_role)} models into the runtime at "
        f"{settings.lmstudio_base_url}: {members}\n"
        "each is measured alone, then ALL are loaded together for the "
        "co-residency modes"
    )
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("refusing to load models without a terminal to ask; pass --yes", file=sys.stderr)
        return False
    return input("proceed? [y/N] ").strip().lower() in {"y", "yes"}


def _confirm_load(spec: ExperimentSpec, settings: Settings, assume_yes: bool) -> bool:
    """Say what is about to be loaded, and wait for an answer.

    The load is the expensive, memory-spending, minutes-long part, and it is
    invisible from the command that triggers it. Refusing rather than assuming
    when there is nobody to ask is deliberate: an unattended script that meant
    to run this can pass `--yes`, and one that did not should not discover the
    difference by finding a 14 GB model resident an hour later.
    """
    if spec.tool_trials:
        print("this run also makes 9 tool-call generations after the prose tests (M13)")
    print(f"about to load {spec.model_key} into the runtime at "
          f"{settings.lmstudio_base_url}")
    print(f"  configuration  {dict(spec.load) or 'runtime defaults'}")
    print(f"  work           {spec.warmups} warmup(s) + {spec.repetitions} measured "
          f"repetition(s) × {len(spec.tests)} test(s)")
    print("  note           this occupies memory until the run finishes and the "
          "lease is released")
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("\nrefusing to load a model with nobody to ask; pass --yes to proceed",
              file=sys.stderr)
        return False
    answer = input("\nproceed? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _publisher(settings: Settings, database: Database) -> EventPublisher:
    """This benchmark's event publisher, disabled unless a hub is configured.

    The CLI has no application and therefore none of the identity `create_app`
    derives, so it is derived the same way here — the installation from the
    database path, the machine from the stored identity. Deriving it differently
    would put one machine's runs under two identities on the same timeline.
    """
    installation = uuid.uuid5(uuid.NAMESPACE_DNS, settings.database_path).hex[:12]
    return EventPublisher(
        service_type="sirvis",
        service_id=f"sirvis-{installation}",
        machine_id=machine_identity(database),
        base_url=settings.nervis_base_url,
    )


async def _run_and_publish(
    events: EventPublisher, work: Awaitable[ExperimentOutcome]
) -> ExperimentOutcome:
    """Run the benchmark, then send what it queued before the process ends.

    **A benchmark process is short-lived, and that is the whole reason this
    exists.** There is no lifespan to drain on and no loop that outlives the
    run: a fire-and-forget POST issued after the last `await` dies with the
    interpreter, and the event it loses is the *closing* one — precisely the
    event that turns SIRVIS's span from a point into the interval a benchmark
    actually is.

    Bounded, and its failure never reaches the exit code. SIRVIS.md is explicit
    that telemetry export is optional and never blocks a benchmark, so a hub
    that is not answering costs a couple of seconds at the end and nothing else.
    """
    outcome = await work
    if events.enabled:
        async with httpx.AsyncClient() as client:
            await events.drain(client)
    return outcome


def _print_outcome(outcome: ExperimentOutcome) -> None:
    """The run, in the shape an operator asked the question in.

    Validity notes are printed even on a successful run, and prominently. A
    result measured while the machine was swapping, or against a model loaded at
    a context length nobody asked for, is still a result — and reading it as
    though it answered the original question is the mistake §11.8 exists to
    prevent."""
    print(f"\nrun              {outcome.run_id} · {outcome.state.value}")
    print(f"  experiment     {outcome.experiment_id}")
    print(f"  raw results    {outcome.results_path}")
    # The stored rows, not just the directory. `result_ids` was recorded on
    # every outcome and printed nowhere, so a run told you where its files went
    # and never which database rows it produced — leaving the lookup to a
    # timestamp guess, which is the reconstruction a stored id exists to avoid.
    if outcome.result_ids:
        print(f"  result rows    {', '.join(outcome.result_ids)}")
    if outcome.record is None:
        print(f"  detail         {outcome.detail}")
        return
    record = outcome.record
    print(f"  evidence       {record.identity.evidence_id} "
          f"({record.evidence_type.value.lower()})")
    for name, measurement in sorted(record.measurements.items()):
        spread = "" if measurement.spread is None else f" ± {measurement.spread:.3f}"
        print(f"  {name:<30} {measurement.median:.3f}{spread} {measurement.unit} "
              f"(n={measurement.samples})")
    # §13.2's rates print beside the measurements rather than under them: a
    # tool-call reliability is the headline finding of an agent-role run, and a
    # run that measured 8 in 8 and said nothing about it sends the operator to
    # the raw results to find out whether the trials happened at all.
    for name, trial in sorted(record.rates.items()):
        detail = f" · {trial.provenance.notes}" if trial.provenance.notes else ""
        print(f"  {name:<30} {trial.passed}/{trial.total} "
              f"({trial.rate:.0%}){detail}")
    print(f"  validity       {record.validity.value}")
    for note in record.validity_notes:
        print(f"    warning      {note}")


def _run_results(settings: Settings, limit: int) -> int:
    """`sirvis results latest` (§18) — what has been measured on this machine."""
    from sirvis.storage import list_runs

    database = prepare_database(settings.database_path)
    runs, _ = list_runs(database, limit=limit)
    if not runs:
        print("no benchmark runs recorded yet — try: sirvis benchmark run examples/basic.yaml")
        return EXIT_OK
    for run in runs:
        print(f"{run['run_id']}  {run['state']:<10} {run['started_at']}  "
              f"{run['experiment_id']}")
        for result in run["results"]:
            metrics = result.get("metrics", {})
            # `metrics` carries measurements and trial rates together, and they
            # render differently: a measurement has a median and a unit, a rate
            # has passed and total. The `"median" in body` test was already the
            # discriminator and silently dropped every rate — so an agent run
            # listed its throughput and not the 8-in-8 that was the point of it.
            headline = ", ".join(
                f"{name}={body['median']:.3f}{body['unit'][:1]}" if "median" in body
                else f"{name}={body['passed']}/{body['total']}"
                for name, body in sorted(metrics.items())
                if "median" in body or "total" in body
            )
            print(f"    {result['target_key']:<40} {result['validity']:<8} {headline}")
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
