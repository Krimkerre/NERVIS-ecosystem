"""Persistence. SIRVIS owns its own, and that is a rule rather than an accident.

ECOSYSTEM_RUNBOOK.md §3 permits services to share generated transport types from
the protocol package and nothing else — "shared database tables, provider
clients, routing engines and benchmark logic are not". So this is deliberately a
sibling of RAVIS's storage rather than an import of it.
"""

from sirvis.storage.database import Database, current_version, prepare_database
from sirvis.storage.repositories import (
    RunState,
    StoredResult,
    create_experiment,
    finish_run,
    list_runs,
    read_result,
    read_run,
    reconcile_interrupted,
    start_run,
)
from sirvis.storage.results import ResultDirectory

__all__ = [
    "Database",
    "ResultDirectory",
    "RunState",
    "StoredResult",
    "create_experiment",
    "current_version",
    "finish_run",
    "list_runs",
    "prepare_database",
    "read_result",
    "reconcile_interrupted",
    "read_run",
    "start_run",
]
