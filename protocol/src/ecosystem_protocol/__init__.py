"""The Minimal Ecosystem Protocol, shared by SIRVIS, RAVIS and NERVIS.

`ECOSYSTEM_RUNBOOK.md` §4 is the specification; this package is the one
implementation of it. It exists because the second service needed the same five
endpoints as the first, and two copies of a protocol is not a protocol.

What is shared is the *shape*: the endpoints, the capability envelope, the
version rule. What each service supplies is its own identity, its own health
checks and its own capability declarations — which is everything that ought to
differ, and nothing that ought not.
"""

from ecosystem_protocol.capabilities import (
    AVAILABLE,
    DEGRADED,
    UNAVAILABLE,
    Capability,
    capability_snapshot,
    wire_identifier,
)
from ecosystem_protocol.observability import (
    JsonLineFormatter,
    configure_logging,
    new_request_id,
    redact,
)
from ecosystem_protocol.routes import router
from ecosystem_protocol.surface import EcosystemSurface
from ecosystem_protocol.version import (
    COMPATIBLE_PROTOCOL_MAX,
    COMPATIBLE_PROTOCOL_MIN,
    PROTOCOL_VERSION,
    is_supported_protocol,
)

__all__ = [
    "AVAILABLE",
    "COMPATIBLE_PROTOCOL_MAX",
    "COMPATIBLE_PROTOCOL_MIN",
    "DEGRADED",
    "PROTOCOL_VERSION",
    "UNAVAILABLE",
    "Capability",
    "wire_identifier",
    "EcosystemSurface",
    "JsonLineFormatter",
    "capability_snapshot",
    "configure_logging",
    "new_request_id",
    "is_supported_protocol",
    "redact",
    "router",
]
