"""Persistence. SIRVIS owns its own, and that is a rule rather than an accident.

ECOSYSTEM_RUNBOOK.md §3 permits services to share generated transport types from
the protocol package and nothing else — "shared database tables, provider
clients, routing engines and benchmark logic are not". So this is deliberately a
sibling of RAVIS's storage rather than an import of it.
"""

from sirvis.storage.database import Database, current_version, prepare_database

__all__ = ["Database", "current_version", "prepare_database"]
