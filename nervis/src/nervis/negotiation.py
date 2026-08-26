"""Which operations are actually available, per control (§5.2).

The registry says whether a *service* is reachable. This says whether one
**operation** may run, which is a finer question and the one a button needs
answered.

Three rules from §5.2, and each exists because the obvious shortcut is wrong:

- **Unknown capabilities are unavailable.** Not "probably fine" — a capability
  NERVIS has not read is one it cannot promise, and a control that ran anyway
  would be calling a guessed endpoint, which the section's gate forbids by name.
- **A newer unknown *optional* capability is ignored.** A peer that grows a
  capability this build has never heard of has not broken anything; treating an
  unrecognised name as a fault would make every upgrade of a peer look like a
  regression in NERVIS.
- **An unsupported required major marks the *feature* incompatible — not the
  whole dashboard**, when other surfaces remain compatible. This is the one that
  takes deliberate structure: the natural implementation checks the protocol
  once at the service level and disables everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from nervis.registry import RegistryEntry, RegistryState


class Availability(str, Enum):
    """Why a control is or is not usable, at the grain a control cares about."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    SERVICE_DOWN = "service_down"
    UNKNOWN = "unknown"


# The two that let a control run. `DEGRADED` is included because a degraded
# capability is one the service says it partly offers — RAVIS's management
# surface has its reads and none of its mutations — and hiding the reads because
# the writes are missing would remove a working screen.
USABLE = frozenset({Availability.AVAILABLE, Availability.DEGRADED})


# §4.1's states, as this layer's vocabulary. A state NERVIS has never heard of
# is `UNAVAILABLE` rather than an error: a peer inventing one is not a reason to
# stop rendering, and treating the unknown as unusable is the same fail-closed
# direction §5.2 takes everywhere else.
_ADVERTISED = {
    "available": Availability.AVAILABLE,
    "degraded": Availability.DEGRADED,
    "unavailable": Availability.UNAVAILABLE,
}


@dataclass(frozen=True)
class Operation:
    """One thing the UI can do, and what it needs to be true (§5.2).

    §5.2 requires every control to declare its owning service, its capability,
    read-versus-mutate authorization, a request timeout and its idempotency
    behaviour. All of that lives here rather than in the screen that draws the
    button, so a control cannot exist without having answered the questions.
    """

    key: str
    service: str
    capability: str
    label: str
    mutates: bool = False
    timeout_seconds: float = 5.0
    # Whether repeating the call is safe. §5.2 asks for it explicitly, and it is
    # what decides whether a failed request may be retried automatically or has
    # to be handed back to a person.
    idempotent: bool = True
    # Whether a person must confirm before it runs. Every mutation defaults to
    # yes; §15.1's read-only rule means M2 ships no mutations at all, so this is
    # the shape M18b arrives into rather than a live switch.
    confirm: bool = True


@dataclass(frozen=True)
class Verdict:
    """Whether one operation may run right now, and the sentence explaining it.

    `reason` is never empty for anything not `AVAILABLE`. A disabled control
    that does not say why is the thing §4.1 spends a whole section preventing at
    the service level, and it is no better one layer up.
    """

    operation: Operation
    availability: Availability
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.availability in USABLE

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.operation.key,
            "service": self.operation.service,
            "capability": self.operation.capability,
            "label": self.operation.label,
            "mutates": self.operation.mutates,
            "idempotent": self.operation.idempotent,
            "confirm": self.operation.confirm,
            "timeout_seconds": self.operation.timeout_seconds,
            "availability": self.availability.value,
            "reason": self.reason,
            "usable": self.usable,
        }


def negotiate(operation: Operation, entry: RegistryEntry | None) -> Verdict:
    """What this one control may do, given what was last observed.

    Ordered so the most specific true statement wins. A control on an
    incompatible peer reads *incompatible* rather than *service down*, because
    the two call for different actions — one is "upgrade something", the other
    is "start something".
    """
    if entry is None:
        return Verdict(operation, Availability.UNKNOWN, "no registry entry for this service")
    if entry.state is RegistryState.INCOMPATIBLE:
        # §5.2's rule, at the grain it asks for: this marks the operations that
        # depend on the peer, and leaves every other service's controls alone.
        return Verdict(operation, Availability.INCOMPATIBLE, entry.detail or "unsupported major")
    if not entry.is_usable:
        return Verdict(
            operation,
            Availability.SERVICE_DOWN,
            entry.detail or f"{entry.declaration.label} is {entry.state.value}",
        )
    state = entry.capabilities.get(operation.capability)
    if state is None:
        # Unknown, not absent-therefore-fine. This also covers a peer that
        # publishes no MEP surface at all, where the capability map is empty by
        # construction and every operation on it is honestly unknown.
        return Verdict(
            operation,
            Availability.UNKNOWN,
            f"{entry.declaration.label} does not advertise {operation.capability}",
        )
    # A table rather than a ladder: one new protocol capability state would
    # otherwise be one more branch on a function already at seven of eight, and
    # three branches that differ only in which enum they name is data.
    known_state = _ADVERTISED.get(state)
    if known_state is Availability.AVAILABLE:
        return Verdict(operation, Availability.AVAILABLE)
    return Verdict(
        operation,
        known_state or Availability.UNAVAILABLE,
        f"{entry.declaration.label} reports {operation.capability} as {state}",
    )


def may_attempt(verdict: Verdict, entry: RegistryEntry | None) -> bool:
    """Whether to make the call, which is narrower than "is this control usable".

    **Liveness is not a veto.** The registry's reading is up to one probe
    interval old, so gating a call on it means refusing a peer that came back
    twenty seconds ago — reporting `ConnectError` for a service that is
    answering. That is guessing in the other direction from the one §5.2
    forbids.

    What the gate is for is *"never calls a guessed endpoint"*: a capability
    never advertised, or one the service says it does not offer. Those stay
    refused without a request. A capability last seen usable on a service now
    thought unreachable is **attempted** — the connection refuses in about a
    millisecond on loopback, and the transport's answer is fresher and more
    specific than the registry's.

    `negotiate()` still returns `SERVICE_DOWN`, and a *control* should grey out
    on it. A call should try. Shared by the M3 readers and M4's chat because
    both need the same answer and two copies would drift.
    """
    if verdict.usable:
        return True
    if entry is None or verdict.availability is not Availability.SERVICE_DOWN:
        return False
    return entry.capabilities.get(verdict.operation.capability) in {"available", "degraded"}
