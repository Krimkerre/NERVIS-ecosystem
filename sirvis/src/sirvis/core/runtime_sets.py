"""Runtime Sets — versioned multi-model targets (SIRVIS.md §10).

A Runtime Set is a named combination of models intended to coexist: a `chat`
model and an `agent` model, each with its own context length, meant to be
resident at the same time. §10 makes it a *first-class* target rather than a
convenience wrapper, and two properties are what earn that:

**It is versioned, and immutable at use.** A stored revision is never edited.
Changing a definition writes a new revision, so a benchmark result from three
weeks ago still names the combination it actually measured rather than whatever
that name means today. §10's gate says it directly — *old results retain the
original revision* — and the only way to keep that promise is to never mutate a
row a result can point at.

**It has no routing semantics.** RAVIS owns route choice. A Runtime Set says
"these were tested together, under this workload, and here is the interference
that was observed". It never says "send chat traffic here". Anything in this
module that reads like a recommendation is a bug.

The estimate below deserves the same suspicion §10.1 asks for:

    Two models fitting separately does not prove they work well together.

So the fit estimate here can refuse a combination and can never approve one. It
is arithmetic over weights, and weights are the smallest of the costs — KV
cache, runtime overhead, the OS, Clarvis and RAVIS are all outside it. An
estimate that says *this cannot fit* is a fact about numbers that already
exceed the machine. An estimate that says *this fits* is a hypothesis, and M10
is what tests it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sirvis.core.models import Claim, Provenance, _identity

# Roles are arbitrary strings (§10) — `chat`, `agent`, `planner`, `coder`,
# `vision`, `embedder`, `reranker`, `judge` are the examples, not the set. This
# is here so a reader knows the openness is deliberate rather than unfinished:
# validating against a fixed list would refuse the combination somebody is
# building SIRVIS to discover.
EXAMPLE_ROLES = ("chat", "agent", "planner", "coder", "vision", "embedder", "reranker", "judge")

# What the weights-only estimate does not include, carried into the estimate's
# own detail string rather than left in a comment. A number that travels without
# its exclusions is a number somebody will treat as a budget.
ESTIMATE_EXCLUDES = "KV cache, runtime overhead, OS memory, and other resident applications"


class RuntimeSetError(Exception):
    """A definition that cannot be stored as written.

    Distinct from a *load* failure on purpose: this is the definition being
    wrong, which is answerable now, and never a statement about whether the
    combination fits.
    """


@dataclass(frozen=True)
class RuntimeSetMember:
    """One model in a set, and the role it plays there.

    `model_id` is a runtime key — the name the runtime uses (§6), which is the
    only handle that resolves on a **cold** model. Storing a display name here
    would make a set unloadable precisely when it matters: before anything is
    resident.
    """

    role: str
    model_id: str
    context_length: int | None = None
    configuration: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "model_id": self.model_id,
            "context_length": self.context_length,
            "configuration": self.configuration,
        }

    def canonical(self) -> dict[str, Any]:
        """The member as it contributes to the definition hash.

        Identical to `as_dict` today and separate anyway: the moment a purely
        descriptive field is added — a note, a label — it must not make an
        unchanged combination look like a new revision.
        """
        return self.as_dict()


@dataclass(frozen=True)
class RuntimeSet:
    """A named, versioned combination of models (§10).

    `runtime_set_id` is derived from the **name** and therefore stable across
    revisions: `clarvis-balanced` keeps its identity while its membership
    changes, which is what lets a person ask "how has this set performed over
    time" and get an answer. The revision is what distinguishes the definitions.
    """

    runtime_set_id: str
    revision: int
    name: str
    members: tuple[RuntimeSetMember, ...]
    purpose: str = ""
    # §10 stores load order as metadata. It defaults to declaration order rather
    # than to anything cleverer — largest-first would allocate more predictably,
    # but order changes what a multi-model run measures (§11.2 loads role A,
    # then role B), so choosing a different one silently would change the
    # experiment the operator wrote down.
    load_order: tuple[str, ...] = ()
    definition_hash: str = ""

    @staticmethod
    def define(
        name: str,
        members: list[RuntimeSetMember],
        *,
        purpose: str = "",
        load_order: list[str] | None = None,
        revision: int = 1,
    ) -> RuntimeSet:
        """Validate a definition and derive its identity and hash."""
        clean_name = name.strip()
        if not clean_name:
            raise RuntimeSetError("a runtime set needs a name")
        ordered = _validated_members(members)
        order = _validated_order(load_order, ordered)
        return RuntimeSet(
            runtime_set_id=_identity("rset", clean_name),
            revision=revision,
            name=clean_name,
            members=ordered,
            purpose=purpose.strip(),
            load_order=order,
            definition_hash=_definition_hash(clean_name, ordered, purpose.strip(), order),
        )

    def member_for(self, role: str) -> RuntimeSetMember | None:
        """The member playing one role, or None.

        Roles are unique within a set, which is what "independently addressable"
        means in M9's acceptance criterion: a caller names a role and gets one
        model, not a list it has to disambiguate.
        """
        return next((member for member in self.members if member.role == role), None)

    def members_in_load_order(self) -> tuple[RuntimeSetMember, ...]:
        """The members, in the order §11.2's lifecycle should load them."""
        by_role = {member.role: member for member in self.members}
        return tuple(by_role[role] for role in self.load_order)

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtime_set_id": self.runtime_set_id,
            "revision": self.revision,
            "name": self.name,
            "purpose": self.purpose,
            "members": [member.as_dict() for member in self.members],
            "load_order": list(self.load_order),
            "definition_hash": self.definition_hash,
        }


def _validated_members(members: list[RuntimeSetMember]) -> tuple[RuntimeSetMember, ...]:
    """Members with their roles checked for the two things that break addressing."""
    if not members:
        raise RuntimeSetError("a runtime set needs at least one member")
    seen: set[str] = set()
    for member in members:
        if not member.role.strip():
            raise RuntimeSetError("every member needs a role")
        if not member.model_id.strip():
            raise RuntimeSetError(f"member {member.role!r} needs a model_id")
        if member.role in seen:
            # Two members claiming one role makes "the chat model" ambiguous,
            # and M9's exit criterion is that members stay *independently
            # addressable*. Refused at definition time rather than discovered at
            # load time, where half the set is already resident.
            raise RuntimeSetError(f"role {member.role!r} appears twice; roles identify members")
        seen.add(member.role)
    return tuple(members)


def _validated_order(
    load_order: list[str] | None, members: tuple[RuntimeSetMember, ...]
) -> tuple[str, ...]:
    """The declared load order, or declaration order when none was given."""
    if load_order is None:
        return tuple(member.role for member in members)
    roles = {member.role for member in members}
    named = tuple(role for role in load_order)
    if set(named) != roles:
        missing = sorted(roles - set(named))
        unknown = sorted(set(named) - roles)
        raise RuntimeSetError(
            f"load_order must name every member exactly once "
            f"(missing: {missing or 'none'}, unknown: {unknown or 'none'})"
        )
    if len(named) != len(roles):
        raise RuntimeSetError("load_order repeats a role")
    return named


def _definition_hash(
    name: str, members: tuple[RuntimeSetMember, ...], purpose: str, load_order: tuple[str, ...]
) -> str:
    """What decides whether a save is a new revision or the same one again.

    Everything that changes what gets loaded is in it — membership, context
    lengths, configuration, and the order. `purpose` is in it too, because it is
    part of what a person means by the definition and a result citing revision 2
    should be citing the words that were true when it ran.

    Sorted keys and a canonical separator, so re-saving a definition that
    round-tripped through JSON does not manufacture a revision out of key order.
    """
    canonical = json.dumps(
        {
            "name": name,
            "purpose": purpose,
            "members": [member.canonical() for member in members],
            "load_order": list(load_order),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class FitEstimate:
    """Whether a combination's *weights* alone can fit, and nothing more.

    Deliberately not called a prediction. §10.1's whole point is that separate
    fits do not compose, so this answers the one question arithmetic can settle:
    do the weights already exceed the machine before anything else is counted.

    `verdict` is `REFUSED`, `PLAUSIBLE` or `UNKNOWN` rather than a boolean,
    because "we cannot tell" is a real answer here and the common one — a build
    whose installed size nobody recorded makes the total unknown, and a total
    that silently treated it as zero would approve a set that cannot load.
    """

    verdict: str
    weights_bytes: Claim
    machine_bytes: int | None
    detail: str

    REFUSED = "REFUSED"
    PLAUSIBLE = "PLAUSIBLE"
    UNKNOWN = "UNKNOWN"

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "weights_bytes": self.weights_bytes.as_dict(),
            "machine_bytes": self.machine_bytes,
            "detail": self.detail,
            # Named on every response, not just in prose: §10's gate requires
            # resource totals to identify measurement versus estimate, and a
            # consumer reading only the JSON must be able to tell.
            "basis": "estimate",
            "excludes": ESTIMATE_EXCLUDES,
        }


def estimate_fit(sizes: dict[str, int | None], machine_bytes: int | None) -> FitEstimate:
    """Weigh a set's members against the machine, refusing only on certainty.

    `sizes` maps model_id to installed size in bytes, with `None` for a build
    whose size is not recorded. One unknown makes the whole total unknown, which
    is the honest arithmetic: a sum missing a term is not a sum.

    The asymmetry is the design. `REFUSED` means the weights *alone* already
    exceed the machine, which is a fact and worth refusing on. `PLAUSIBLE` means
    only that this particular obstacle is absent — it is never permission, and
    M10's measurement is what turns it into knowledge.
    """
    if not sizes:
        return FitEstimate(
            FitEstimate.UNKNOWN, Claim(None, Provenance.UNKNOWN), machine_bytes,
            "no members to weigh",
        )
    if any(size is None for size in sizes.values()):
        unmeasured = sorted(model for model, size in sizes.items() if size is None)
        return FitEstimate(
            FitEstimate.UNKNOWN,
            Claim(None, Provenance.UNKNOWN, f"no installed size recorded for {unmeasured}"),
            machine_bytes,
            "the combined weight cannot be totalled while a member's size is unknown",
        )
    total = sum(size for size in sizes.values() if size is not None)
    weights = Claim(total, Provenance.DECLARED, "sum of installed sizes, weights only")
    if machine_bytes is None:
        return FitEstimate(
            FitEstimate.UNKNOWN, weights, None,
            "no machine memory snapshot to weigh the total against",
        )
    if total >= machine_bytes:
        return FitEstimate(
            FitEstimate.REFUSED, weights, machine_bytes,
            f"weights alone total {total} bytes against {machine_bytes} of memory, "
            f"before {ESTIMATE_EXCLUDES}",
        )
    return FitEstimate(
        FitEstimate.PLAUSIBLE, weights, machine_bytes,
        f"weights leave {machine_bytes - total} bytes for {ESTIMATE_EXCLUDES}; "
        "co-residency is not established until it is measured",
    )
