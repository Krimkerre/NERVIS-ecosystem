"""Storing Runtime Sets so old results keep meaning what they meant (§10).

One rule governs this module and everything else follows from it: **a revision
row is written once and never updated.** §10's gate is that old results retain
the revision they measured, and a benchmark result from three weeks ago is only
honest if the definition it cites still says what it said then. An `UPDATE`
here would rewrite history that has already been published to RAVIS.

So `save` does not mean "write these fields". It means: find the revision whose
definition matches, or add the next one. Saving an unchanged definition is
therefore idempotent and returns the same revision — which is what makes a
revision number worth reading, because it only moves when something moved.
"""

from __future__ import annotations

import json
from typing import Any

from sirvis.core.runtime_sets import RuntimeSet, RuntimeSetMember
from sirvis.storage.database import Database


def save_runtime_set(database: Database, definition: RuntimeSet) -> RuntimeSet:
    """Store a definition, returning it with the revision it actually has.

    Three outcomes, and the caller cannot tell them apart from the signature —
    deliberately, because it should not branch on them:

    - a set nobody has defined before becomes revision 1;
    - a definition identical to an existing revision returns *that* revision,
      writing nothing;
    - a changed definition becomes the next revision, leaving every earlier one
      exactly as it was.

    The second case is the one that matters. A UI that saves on every keystroke,
    or a script re-applying a YAML file, must not walk the revision number
    upward while nothing changes — a version that increments for no reason is a
    version nobody trusts.
    """
    connection = database.connection
    with connection:
        connection.execute(
            "INSERT OR IGNORE INTO runtime_set (runtime_set_id, name) VALUES (?, ?)",
            (definition.runtime_set_id, definition.name),
        )
        existing = connection.execute(
            "SELECT revision FROM runtime_set_revision "
            "WHERE runtime_set_id = ? AND definition_hash = ? "
            "ORDER BY revision LIMIT 1",
            (definition.runtime_set_id, definition.definition_hash),
        ).fetchone()
        if existing is not None:
            return _with_revision(definition, int(existing["revision"]))

        row = connection.execute(
            "SELECT MAX(revision) AS latest FROM runtime_set_revision WHERE runtime_set_id = ?",
            (definition.runtime_set_id,),
        ).fetchone()
        revision = (int(row["latest"]) if row and row["latest"] is not None else 0) + 1
        stored = _with_revision(definition, revision)
        connection.execute(
            "INSERT INTO runtime_set_revision "
            "(runtime_set_id, revision, definition_hash, purpose, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                stored.runtime_set_id,
                revision,
                stored.definition_hash,
                stored.purpose,
                json.dumps(stored.as_dict(), sort_keys=True),
            ),
        )
        return stored


def read_runtime_set(
    database: Database, runtime_set_id: str, revision: int | None = None
) -> RuntimeSet | None:
    """One revision of a set — the latest by default, or the one named.

    `revision` is not optional in spirit. A benchmark loading a set for a run
    resolves the latest *once* and records the number, and every later reference
    passes it back: §10 calls a set immutable **at use**, which means the run
    must be pinned to the definition it started with even if somebody edits the
    set while it is running.
    """
    query = (
        "SELECT payload FROM runtime_set_revision WHERE runtime_set_id = ? "
        + ("AND revision = ?" if revision is not None else "ORDER BY revision DESC LIMIT 1")
    )
    arguments: tuple[Any, ...] = (
        (runtime_set_id, revision) if revision is not None else (runtime_set_id,)
    )
    row = database.connection.execute(query, arguments).fetchone()
    return _from_payload(row["payload"]) if row else None


def find_by_name(database: Database, name: str) -> RuntimeSet | None:
    """The latest revision of the set with this name, or None.

    A name is what a person types and an ID is what a machine stores, so both
    resolve. The name is unique by schema, which is what makes this a lookup
    rather than a search.
    """
    row = database.connection.execute(
        "SELECT runtime_set_id FROM runtime_set WHERE name = ?", (name,)
    ).fetchone()
    return read_runtime_set(database, str(row["runtime_set_id"])) if row else None


def list_runtime_sets(database: Database, limit: int = 50) -> list[RuntimeSet]:
    """The latest revision of every set, newest first."""
    rows = database.connection.execute(
        """
        SELECT r.payload FROM runtime_set_revision AS r
        JOIN (
            SELECT runtime_set_id, MAX(revision) AS revision
            FROM runtime_set_revision GROUP BY runtime_set_id
        ) AS latest
          ON latest.runtime_set_id = r.runtime_set_id AND latest.revision = r.revision
        JOIN runtime_set AS s ON s.runtime_set_id = r.runtime_set_id
        ORDER BY s.created_at DESC, s.name
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_from_payload(row["payload"]) for row in rows]


def list_revisions(database: Database, runtime_set_id: str) -> list[RuntimeSet]:
    """Every revision of one set, oldest first.

    The endpoint that makes §10's gate observable rather than merely true: two
    revisions being *distinguishable* is a claim somebody has to be able to
    check, and this is where they check it.
    """
    rows = database.connection.execute(
        "SELECT payload FROM runtime_set_revision WHERE runtime_set_id = ? ORDER BY revision",
        (runtime_set_id,),
    ).fetchall()
    return [_from_payload(row["payload"]) for row in rows]


def _with_revision(definition: RuntimeSet, revision: int) -> RuntimeSet:
    """The same definition, carrying the revision the database assigned."""
    return RuntimeSet(
        runtime_set_id=definition.runtime_set_id,
        revision=revision,
        name=definition.name,
        members=definition.members,
        purpose=definition.purpose,
        load_order=definition.load_order,
        definition_hash=definition.definition_hash,
    )


def _from_payload(payload: str) -> RuntimeSet:
    """Rebuild a set from the JSON stored with it.

    Read back from the payload rather than recomputed from the members, so that
    a revision returns the definition *as stored*. If the derivation rules ever
    change, an old revision must keep describing what actually ran — recomputing
    would quietly restate history in today's terms.
    """
    stored = json.loads(payload)
    return RuntimeSet(
        runtime_set_id=stored["runtime_set_id"],
        revision=int(stored["revision"]),
        name=stored["name"],
        members=tuple(
            RuntimeSetMember(
                role=member["role"],
                model_id=member["model_id"],
                context_length=member.get("context_length"),
                configuration=member.get("configuration") or {},
            )
            for member in stored["members"]
        ),
        purpose=stored.get("purpose", ""),
        load_order=tuple(stored.get("load_order") or ()),
        definition_hash=stored["definition_hash"],
    )
