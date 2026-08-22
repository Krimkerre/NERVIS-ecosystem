"""What RAVIS advertises it can do, and — more importantly — what it cannot yet.

RAVIS.md §4.1 is unusually strict about this, and the strictness is the point:

    Do not advertise streaming, tools, JSON or structured output, vision,
    embeddings or audio unless that exact operation passes provider *and*
    gateway conformance.

A capability list is a promise other software plans around. NERVIS disables
controls from it, Clarvis decides whether a route is usable from it. So at M0 —
where nothing routes yet — almost everything here is `unavailable` with a reason
naming the milestone that will change it. An honest "not yet, because X" is
useful to a peer; an optimistic "available" is a bug in somebody else's product.
"""

from __future__ import annotations

from typing import Any

# The MEP major this build implements. A peer asking for a different major is
# refused cleanly rather than served a guess (runbook §4.2).
PROTOCOL_VERSION = "1.0.0"
COMPATIBLE_PROTOCOL_MIN = "1.0.0"
COMPATIBLE_PROTOCOL_MAX = "1.999.999"

# The build's own version, distinct from the protocol it speaks. Consumers must
# never infer behaviour from this (runbook §4.2) — that is what capabilities are
# for — but it belongs in a bug report.
BUILD_VERSION = "0.0.1"

# id → (semantic version, state, reason). Written as data rather than as branches
# so that adding a capability is one line and cannot forget a field.
_DECLARED: dict[str, tuple[str, str, str]] = {
    "ravis.openai_compatible.chat_completions@1": (
        "1.0.0",
        "unavailable",
        "transparent gateway lands at M1; conformance at M2 (runbook Stage 2)",
    ),
    "ravis.providers.native@1": (
        "1.0.0",
        "unavailable",
        "translated execution path is M3b (runbook Stage 5)",
    ),
    "ravis.routing.explanations@1": (
        "1.0.0",
        "unavailable",
        "routing begins at M5 (runbook Stage 3)",
    ),
    "ravis.virtual_profiles@1": (
        "1.0.0",
        "unavailable",
        "pools and profiles land at M5 (runbook Stage 3)",
    ),
    "ravis.sessions@1": ("1.0.0", "unavailable", "sessions land at M11"),
    "ravis.usage_cost@1": ("1.0.0", "unavailable", "cost engine lands at M15"),
    "ravis.management@1": (
        "1.0.0",
        "unavailable",
        "management API lands at M18 (runbook Stage 6)",
    ),
    "ravis.events@1": (
        "1.0.0",
        "unavailable",
        "event publication lands at M18 (runbook Stage 7)",
    ),
}


def capability_snapshot(revision: int) -> dict[str, Any]:
    """The GET /ecosystem/capabilities body.

    `revision` increments when the set changes, so a consumer can tell a genuine
    change from a re-read. It is supplied by the caller rather than computed
    here, because the thing that knows a capability changed is whatever changed
    it — not this renderer.
    """
    return {
        "revision": revision,
        "capabilities": [
            {
                "id": capability_id,
                "version": version,
                "state": state,
                "constraints": {},
                "reason": reason,
            }
            for capability_id, (version, state, reason) in sorted(_DECLARED.items())
        ],
    }


def is_supported_protocol(requested_major: str) -> bool:
    """Whether this build can speak the protocol major a peer asked for.

    Compares majors only. Minor and patch differences are additive by definition
    (runbook §4.2), and a consumer is required to ignore unknown optional fields,
    so refusing on them would break compatibility rather than protect it.
    """
    return requested_major.split(".")[0] == PROTOCOL_VERSION.split(".")[0]
