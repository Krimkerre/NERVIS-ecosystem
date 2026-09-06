"""The Clarvis surface `CLARVIS.md` §6.3 specifies — declared, but not the read.

**M8b is built, and this is not how.** This module was written before the
Bridge existed, when the only honest thing to do was declare the surface
§6.3 specifies without inventing a reader for it. The Bridge exists now
(`CLARVIS.md` §6, `clarvis/plan.md` M14, signed off 29 Aug), and M8b's actual
read — polling a live Bridge's `/v1/status` and `config`, then folding its
event stream into agent runs, tasks and gate history — is `nervis/bridges.py`
and `nervis/clarvis.py` (M9), wired into `api/instances.py` and
`api/chat_reads.py` directly. Neither goes through `SERVICE`/`SURFACES` here,
and `clarvis_peer` is deliberately absent from `api/routes.py`'s `PEERS` —
Clarvis never joined the generic per-surface negotiation RAVIS and SIRVIS use,
because a Bridge's identity, auth and per-instance token do not fit that
shape. This file's one remaining job is `test_m8a_registration.py`'s §6.7
write-surface guard, below.

**Every surface is a GET, and that is a property of the Bridge rather than a
choice made here.** §6.3: *"It is read-only — there is no write path on the
Bridge, and §6.7 is why."* §6.7 is the list of things NERVIS may not do:
resolve gates, invoke tools, expand the workspace root, change safety settings,
read SecretStorage, or keep Clarvis running past its extension host. Each of
those would need a write surface, and `test_m8a_registration.py` asserts this
table has none — so adding one fails the suite before it can ship.

`/v1/status` is per extension host and never aggregates two windows (§6.6),
which is why the instance registry keys by `(service, instance_id)` and never
merges. Two Bridges are two rows here for the same reason they are two rows
there.
"""

from __future__ import annotations

from nervis.peers.reader import Surface

SERVICE = "clarvis"

# §6.3's two paths. The MEP surface comes from the shared protocol package and
# is read by `probes.py` like any other peer's, so only the Clarvis-specific
# one needs declaring.
SURFACES: tuple[Surface, ...] = (
    Surface("status", "/v1/status", "clarvis.status.read", "Bridge status"),
)
