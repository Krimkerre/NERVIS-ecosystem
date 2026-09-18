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
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from sirvis.storage.database import Database

# Every filter §15.1 names, as the JSON path it lives at in a stored record.
# A table rather than a chain of `if` statements: adding a filter should be a
# row, and a filter that exists in the API but not here would silently match
# everything — the failure mode that looks like a working query.
# A stored runtime-configuration key is an identifier. Anything else cannot
# match one, and is refused rather than handed to `json_extract`, whose path
# argument raises on malformed input.
_SAFE_CONFIG_KEY = re.compile(r"^[A-Za-z0-9_]+$")

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
    """What a query found.

    A candidate runtime key that no installed build matches is **not** the same
    as a build with no evidence: returning an empty list for both would tell
    RAVIS the model was measured and found wanting when in fact it is not
    installed.

    **That distinction is real and is not kept here.** This carried an
    `unresolved` field described as "the field that keeps this honest", which no
    constructor ever populated and nothing ever read — the API builds
    `unresolved_candidates` from its own local in
    `sirvis/src/sirvis/api/routes.py`, which is where resolution happens and so
    where the answer is known. The field was a second home for a fact that
    already had one.
    """

    items: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None


#: What joins the two halves of a page cursor. A character no timestamp or
#: `res_…` id contains, so splitting one can never be ambiguous.
CURSOR_JOIN = "|"


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
    return EvidenceAnswer(
        items=items,
        next_cursor=_cursor(page[-1]) if len(rows) > query.limit and page else None,
    )


def _cursor(row: Any) -> str:
    """Where the next page resumes: both keys the ordering uses, in that order."""
    return f"{row['created_at']}{CURSOR_JOIN}{row['result_id']}"


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
    # **In SQL, like every other filter.** This one ran in Python *after* the
    # LIMIT, so a page of twenty-five rows could be filtered down to nothing and
    # reported as "no evidence matches" while the record sat one page further
    # in -- on the constraint §15.1's headline question actually names ("under
    # these runtime configuration constraints"). `next_cursor` was derived from
    # the unfiltered page too, so paging could not recover it either.
    #
    # The path is a bound parameter rather than interpolated: the keys come from
    # a query string. `CAST(... AS TEXT)` keeps the tolerance the Python matcher
    # had, where 8192 and "8192" are the same intent in different types.
    for key, value in sorted(query.runtime_config.items()):
        if not _SAFE_CONFIG_KEY.match(key):
            # Not a shape any stored configuration key takes, so nothing can
            # match it. Refused as unmatchable rather than passed to json_extract,
            # which errors on a malformed path.
            clauses.append("0")
            continue
        clauses.append("CAST(json_extract(payload, ?) AS TEXT) = ?")
        arguments.append(f"$.target.runtime_config.{key}")
        arguments.append(str(value))
    if query.since:
        # **Both halves of the sort key, when the caller has both.** Rows are
        # ordered by `created_at DESC, result_id DESC`, and the cursor used to
        # carry the timestamp alone — so "strictly before that second" skipped
        # every row tying with the last one returned, and `datetime('now')` is
        # whole seconds while a run writes all its results in one transaction.
        # A multi-role run therefore lost rows on every page boundary (base
        # review, 17 September 2026, finding 9).
        #
        # A plain timestamp still means what it always did, because a consumer
        # keeping its own high-water mark passes one of those rather than a
        # cursor of ours: page traversal and incremental filtering stay separate.
        stamp, _, result_id = str(query.since).partition(CURSOR_JOIN)
        if result_id:
            clauses.append("(created_at < ? OR (created_at = ? AND result_id < ?))")
            arguments.extend((stamp, stamp, result_id))
        else:
            clauses.append("created_at < ?")
            arguments.append(stamp)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", arguments)


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
