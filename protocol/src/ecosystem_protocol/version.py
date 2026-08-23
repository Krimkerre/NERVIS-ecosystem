"""The protocol major this ecosystem speaks, and what counts as compatible.

ECOSYSTEM_RUNBOOK.md §4.2 settles the rule these constants encode: **majors must
match, minors and patches must not be refused.** Minor and patch changes are
additive by definition and every consumer is required to ignore unknown optional
fields, so refusing on them would break compatibility rather than protect it.

This lives in the shared package rather than in each service because a protocol
version that two services disagree about is not a protocol.
"""

from __future__ import annotations

PROTOCOL_VERSION = "1.0.0"
COMPATIBLE_PROTOCOL_MIN = "1.0.0"
COMPATIBLE_PROTOCOL_MAX = "1.999.999"


def is_supported_protocol(requested: str) -> bool:
    """Whether this build can speak the protocol major a peer asked for.

    Compares majors only, per §4.2. An empty or malformed request is refused
    rather than guessed at: a peer that cannot state its protocol version is
    exactly the peer not to improvise with.
    """
    major = requested.split(".")[0].strip()
    return bool(major) and major == PROTOCOL_VERSION.split(".")[0]
