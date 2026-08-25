"""What a service advertises it can do, and — more importantly — what it cannot.

ECOSYSTEM_RUNBOOK.md §4.1 is unusually strict here, and the strictness is the
point: **do not advertise an operation unless that exact operation passes
conformance.** A capability list is a promise other software plans around.
NERVIS disables controls from it; Clarvis decides whether a route is usable from
it. An honest "not yet, because X" is useful to a peer; an optimistic
"available" is a bug in somebody else's product.

The *declarations* stay with each service, because only RAVIS knows what RAVIS
can do. What is shared is the shape they are published in, so two services
cannot describe the same idea two ways.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

# The three states §4.1 allows. A capability is never simply absent from the
# list when a service knows about it: "unavailable, because M13" tells a peer
# when to look again, and silence tells it nothing.
AVAILABLE = "available"
UNAVAILABLE = "unavailable"
DEGRADED = "degraded"


@dataclass(frozen=True)
class Capability:
    """One advertised capability.

    `reason` is required rather than optional, and that is deliberate: every
    capability that is not `available` has to say why, and the field being
    mandatory is what stops "unavailable" appearing with no explanation. For an
    available capability it is ordinarily empty.
    """

    version: str
    state: str
    reason: str = ""
    constraints: Mapping[str, Any] | None = None

    def as_dict(self, capability_id: str) -> dict[str, Any]:
        """One capability, as §4.1 says it goes on the wire.

        `capability_id` arrives in the `<id>@<major>` shorthand every
        declaration, docstring and UI label uses, and the `@<major>` is stripped
        here. §4.1 is explicit that the shorthand "is never a wire value": the
        `id` field carries the identifier alone and `version` carries the full
        semantic version, because `@<major>` names a compatibility boundary a
        consumer negotiates against rather than part of the name.

        Stripped at the boundary rather than in the declarations so that
        everything inside a service still keys on the form its prose uses, and
        so no service can forget.
        """
        return {
            "id": wire_identifier(capability_id),
            "version": self.version,
            "state": self.state,
            "constraints": dict(self.constraints or {}),
            "reason": self.reason,
        }


def wire_identifier(capability_id: str) -> str:
    """`ravis.events@1` → `ravis.events`; anything without the suffix is unchanged.

    Tolerant of an id that is already bare, because a declaration written
    without the shorthand is not wrong — it is just missing the annotation, and
    rejecting it would turn a documentation convention into a startup failure.
    """
    return capability_id.split("@", 1)[0]


def declared_major(capability_id: str) -> str:
    """The major the shorthand claims, or an empty string when it claims none."""
    _, _, major = capability_id.partition("@")
    return major


def capability_snapshot(revision: int, declared: Mapping[str, Capability]) -> dict[str, Any]:
    """The `GET /ecosystem/capabilities` body.

    `revision` increments when the set changes, so a consumer can tell a genuine
    change from a re-read. It is supplied by the caller rather than computed
    here, because the thing that knows a capability changed is whatever changed
    it — not this renderer.

    Sorted by id so two reads of an unchanged set are byte-identical, which is
    what makes a revision worth comparing. Sorting on the declared key rather
    than the wire id keeps `foo@1` ahead of `foo@2`, which share a wire id and
    would otherwise order arbitrarily.

    A shorthand whose `@<major>` disagrees with the major in `version` is a
    programming error and raises. The two say the same thing, so a disagreement
    means one of them is stale — and shipping the pair to a peer that negotiates
    on it would make the service lie about which contract it implements.
    """
    for capability_id, capability in declared.items():
        major = declared_major(capability_id)
        if major and major != capability.version.split(".", 1)[0]:
            raise ValueError(
                f"{capability_id} declares major {major} but version {capability.version}"
            )
    return {
        "revision": revision,
        "capabilities": [
            declared[capability_id].as_dict(capability_id)
            for capability_id in sorted(declared)
        ],
    }
