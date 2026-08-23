"""Querying stored evidence the way RAVIS needs to ask (§15.1).

§15.1 states the question this module exists to answer, in RAVIS's words:

> Give me the best measured evidence for role `clarvis-agent` on this machine
> for these candidate builds, under these runtime configuration constraints.

Three things in that sentence shape everything below.

**"Best" cannot be a ranking, and this module does not compute one.** §12.2
forbids evidence keyed as model → score, and a `best=true` flag or an
`ORDER BY score` would be that rule broken by a different spelling. So the query
filters and orders by *recency*, which is a fact about the data rather than a
judgement about the model, and RAVIS decides what "best" means for the route it
is choosing. If a future caller wants ranking, it belongs in the caller.

**"These candidate builds" arrive as runtime keys**, which is the only handle
RAVIS has (§6). Resolving a runtime key to the variant its evidence is filed
under is SIRVIS's job precisely because §15.1 forbids the alternative: *do not
force RAVIS to infer equivalence across builds.* A RAVIS that had to know that
one GGUF and one MLX packaging of `granite-4.0-h-tiny` are the same family — and
that their evidence disagrees — would be reimplementing §6 from names.

**Filtering happens in SQL over the stored payload**, via SQLite's JSON1, rather
than by loading every row and filtering in Python. Not for speed at this corpus
size — 69 results is nothing — but because the filter then has one
implementation, and a `LIMIT` means what it says instead of truncating a list
that was already wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from sirvis.storage.database import Database

# Every filter §15.1 names, as the JSON path it lives at in a stored record.
# A table rather than a chain of `if` statements: adding a filter should be a
# row, and a filter that exists in the API but not here would silently match
# everything — the failure mode that looks like a working query.
FILTERS: tuple[tuple[str, str], ...] = (
    ("machine_id", "$.machine_id"),
    ("role", "$.role"),
    ("model_family", "$.target.model_family"),
    ("variant", "$.target.variant"),
    ("runtime", "$.target.runtime"),
    ("format", "$.target.format"),
    ("quantization", "$.target.quantization"),
    ("suite", "$.suite.id"),
    ("suite_version", "$.suite.version"),
    ("evidence_type", "$.evidence_type"),
    ("validity", "$.validity"),
)

# The retention window §15.1 asks evidence references to resolve within. Nothing
# deletes evidence today, so this is what a consumer is *promised* rather than
# what is enforced — and it is stated rather than implied so that whoever adds
# deletion knows what they are breaking.
RETENTION_DAYS = 365


@dataclass
class EvidenceQuery:
    """One RAVIS question, as filters rather than as prose."""

    filters: dict[str, str] = field(default_factory=dict)
    # Runtime keys RAVIS is considering. Resolved to variants before the query
    # runs, because evidence is filed by variant and RAVIS does not know one.
    candidates: tuple[str, ...] = ()
    runtime_config: Mapping[str, Any] = field(default_factory=dict)
    since: str | None = None
    limit: int = 50


@dataclass
class EvidenceAnswer:
    """What a query found, and what it could not resolve.

    `unresolved` is the field that keeps this honest. A candidate runtime key
    that no installed build matches is **not** the same as a build with no
    evidence, and returning an empty list for both would tell RAVIS the model
    was measured and found wanting when in fact it is not installed.
    """

    items: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    next_cursor: str | None = None


def query_evidence(
    database: Database, query: EvidenceQuery, *, now: datetime | None = None
) -> EvidenceAnswer:
    """Every stored record matching a query, newest first.

    Newest first and nothing cleverer. §12.2's prohibition on model → score
    makes ordering by any quality metric a violation dressed as a convenience,
    so the only ordering offered is the one that is a fact about the record
    rather than a claim about the model.
    """
    where, arguments = _conditions(query)
    rows = database.connection.execute(
        "SELECT result_id, run_id, target_key, evidence_id, validity, payload, created_at "
        f"FROM benchmark_result {where} ORDER BY created_at DESC, result_id DESC LIMIT ?",
        (*arguments, query.limit + 1),
    ).fetchall()

    page = rows[: query.limit]
    moment = now or datetime.now(timezone.utc)
    items = [_view(row, moment) for row in page]
    if query.runtime_config:
        items = [item for item in items if _config_matches(item, query.runtime_config)]
    return EvidenceAnswer(
        items=items,
        next_cursor=page[-1]["created_at"] if len(rows) > query.limit and page else None,
    )


def read_evidence(
    database: Database, evidence_id: str, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Every record filed under one evidence identity, newest first.

    A list rather than a single record, and that is §12.2 rather than an
    oversight: an evidence ID identifies *measurement conditions*, so running
    one suite against one build twice produces two results under one identity.
    Returning "the" record would mean silently picking one, which is the
    judgement this module refuses to make anywhere else either.
    """
    rows = database.connection.execute(
        "SELECT result_id, run_id, target_key, evidence_id, validity, payload, created_at "
        "FROM benchmark_result WHERE evidence_id = ? ORDER BY created_at DESC, result_id DESC",
        (evidence_id,),
    ).fetchall()
    moment = now or datetime.now(timezone.utc)
    return [_view(row, moment) for row in rows]


def _conditions(query: EvidenceQuery) -> tuple[str, list[Any]]:
    """The WHERE clause and its arguments, built from the filter table."""
    clauses: list[str] = []
    arguments: list[Any] = []
    for name, path in FILTERS:
        value = query.filters.get(name)
        if value:
            clauses.append(f"json_extract(payload, '{path}') = ?")
            arguments.append(value)
    if query.candidates:
        placeholders = ",".join("?" for _ in query.candidates)
        clauses.append(f"json_extract(payload, '$.target.variant') IN ({placeholders})")
        arguments.extend(query.candidates)
    if query.since:
        # Strictly less-than, because the cursor is the last row already
        # returned: `<=` would hand the caller that row again on every page and
        # a caller paging to exhaustion would never exhaust.
        clauses.append("created_at < ?")
        arguments.append(query.since)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", arguments)


def _config_matches(item: Mapping[str, Any], wanted: Mapping[str, Any]) -> bool:
    """Whether a record was measured under the runtime configuration asked for.

    A **subset** match: the record must carry every constraint asked for, and
    may carry more. §15.1 lets RAVIS constrain on runtime configuration, and a
    request for `context_length=8192` should find a run that also recorded which
    models it sat beside — the extra facts narrow what the evidence is about,
    they do not disqualify it.

    Compared as strings because a configuration crosses JSON and a query string,
    where 8192 and "8192" are the same intent and different types.
    """
    measured = (item.get("target") or {}).get("runtime_config") or {}
    return all(str(measured.get(key)) == str(value) for key, value in wanted.items())


def _view(row: Any, now: datetime) -> dict[str, Any]:
    """One stored result as the evidence a consumer reads.

    Staleness is computed here rather than stored: §15.1 asks for staleness
    timestamps, and an age written into the row at insert time would be a
    number that was true once. `measured_at` is the fact; `age_seconds` is the
    derivation, and both travel so a consumer can apply its own threshold.
    """
    payload = json.loads(row["payload"])
    measured_at = row["created_at"]
    return {
        **payload,
        "result_id": row["result_id"],
        "run_id": row["run_id"],
        "target_key": row["target_key"],
        "measured_at": measured_at,
        "age_seconds": _age(measured_at, now),
        # The stable reference §15.1 requires. Carried on every item so a
        # consumer never has to build one by concatenating fields — the moment
        # it does, the format is frozen by a caller rather than by SIRVIS.
        "evidence_ref": f"sirvis://evidence/{row['evidence_id']}/{row['result_id']}",
        "retention_days": RETENTION_DAYS,
    }


def _age(measured_at: str, now: datetime) -> float | None:
    """Seconds since the measurement, or None when the stamp cannot be read.

    None rather than zero for an unreadable timestamp: zero means "measured just
    now", which is the most trustworthy thing evidence can be and the last thing
    an unparseable row deserves to claim.
    """
    try:
        stamp = datetime.fromisoformat(measured_at).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return max(0.0, (now - stamp).total_seconds())


def known_roles(database: Database) -> list[str]:
    """Every role the corpus actually holds evidence for.

    Exists because of a seam found while building M16: RAVIS names its pools
    `ravis/clarvis-agent` and this machine's evidence is filed under `agent`,
    so a faithful query for one returns nothing about the other. SIRVIS must not
    guess the mapping — §15.1 forbids inferring equivalence, and inventing a
    `clarvis-` prefix rule would be exactly that — but it can stop the mismatch
    presenting as "this build was measured and has no evidence for that role".
    """
    rows = database.connection.execute(
        "SELECT DISTINCT json_extract(payload, '$.role') AS role FROM benchmark_result "
        "WHERE role IS NOT NULL ORDER BY role"
    ).fetchall()
    return [row["role"] for row in rows]
