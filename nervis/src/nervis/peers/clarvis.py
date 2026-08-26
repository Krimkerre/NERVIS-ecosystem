"""What NERVIS reads from a Clarvis Bridge (`CLARVIS.md` §6.3), and nothing else.

**Declared here, read at M8b.** M8a is the receiving half — a Bridge can
register, hold a lease and be listed. Reading a live one is M8b, and M8b is
blocked: `CLARVIS.md` §6 specifies the Bridge and the Clarvis repository
contains no implementation, so there is nothing to read from. The surfaces are
declared now because they are *specified* now — §6.3 names both paths — and
declaring what NERVIS would read is not the same as inventing it.

**Every surface is a GET, and that is a property of the Bridge rather than a
choice made here.** §6.3: *"It is read-only — there is no write path on the
Bridge, and §6.7 is why."* §6.7 is the list of things NERVIS may not do:
resolve gates, invoke tools, expand the workspace root, change safety settings,
read SecretStorage, or keep Clarvis running past its extension host. Each of
those would need a write surface, and a test in `test_m8a_registration.py`
asserts this table has none — so adding one fails the suite before it can ship.

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
