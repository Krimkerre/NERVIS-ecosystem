"""Machine identity, and the snapshots benchmarks are pinned to (§5.1).

Two rules govern this file and both are about what must *not* happen.

**The identity is a locally generated UUID.** Never a hostname, MAC address,
serial number, username or any reversible hardware fingerprint. §5.1 is
explicit, and the reason is that this ID travels: it appears in evidence RAVIS
reads and, later, in anything NERVIS shows. An opaque value that means nothing
outside this installation is the only kind safe to send anywhere.

**It is resettable**, which is what makes "opaque" true rather than merely
claimed. Resetting produces a genuinely new installation, and old snapshots keep
pointing at the old identity rather than being rewritten — the past happened on
a machine that, as far as this service is now concerned, was a different one.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sirvis.storage import Database
from sirvis.telemetry import SystemSnapshot

MACHINE_ID_SETTING = "machine_id"


def machine_identity(database: Database) -> str:
    """This installation's machine ID, created on first use.

    Get-or-create rather than created at install time, so a database restored
    onto another machine keeps its history coherent — the snapshots recorded
    against that ID describe the hardware they were captured on, whatever is
    running now.
    """
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (MACHINE_ID_SETTING,)
    ).fetchone()
    if row is not None:
        return str(row["value"])

    identity = uuid.uuid4().hex
    with database.connection as connection:
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?)",
            (MACHINE_ID_SETTING, identity),
        )
        connection.execute(
            "INSERT OR IGNORE INTO machine (machine_id) VALUES (?)", (identity,)
        )
    return identity


def reset_machine_identity(database: Database) -> str:
    """Forget this identity and take a new one.

    Old snapshots are deliberately left pointing at the old machine row. Nothing
    is rewritten and nothing is deleted: the results that cited it really were measured
    on that machine, and rewriting history to tidy up a reset would falsify them.
    """
    with database.connection as connection:
        connection.execute("DELETE FROM setting WHERE key = ?", (MACHINE_ID_SETTING,))
    return machine_identity(database)


def record_snapshot(database: Database, snapshot: SystemSnapshot) -> dict[str, Any]:
    """Persist one immutable snapshot and return it with its identity.

    Append-only. There is no update path here and that is the point (§5.1): a
    snapshot a benchmark has already cited must keep describing the conditions
    that benchmark ran under.
    """
    machine_id = machine_identity(database)
    snapshot_id = uuid.uuid4().hex
    payload = snapshot.as_dict()
    with database.connection as connection:
        connection.execute(
            "INSERT INTO machine_snapshot (snapshot_id, machine_id, payload) VALUES (?, ?, ?)",
            (snapshot_id, machine_id, json.dumps(payload)),
        )
    return {"snapshot_id": snapshot_id, "machine_id": machine_id, **payload}


def latest_snapshot(database: Database, machine_id: str) -> dict[str, Any] | None:
    """The most recent snapshot for a machine, or None when there is none.

    None rather than an empty snapshot: a machine nobody has captured yet and a
    machine with no detectable hardware are different situations, and only one
    of them is a problem (runbook §14.4).
    """
    row = database.connection.execute(
        "SELECT snapshot_id, machine_id, captured_at, payload FROM machine_snapshot"
        " WHERE machine_id = ? ORDER BY captured_at DESC, rowid DESC LIMIT 1",
        (machine_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "snapshot_id": row["snapshot_id"],
        "machine_id": row["machine_id"],
        "captured_at": row["captured_at"],
        **json.loads(row["payload"]),
    }
