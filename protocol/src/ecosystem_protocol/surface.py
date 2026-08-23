"""What a service must supply to publish the MEP surface.

The router in `routes.py` is generic; everything that differs between RAVIS,
SIRVIS and NERVIS arrives through this object, attached once at startup as
`app.state.ecosystem`.

Passing a single object rather than four callables is deliberate. The alternative
grew a parameter every time an endpoint needed one more fact, and a router
signature that changes whenever a service adds a health check is a shared package
that is not actually shared.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ecosystem_protocol.capabilities import Capability

# One check: a name, and whether it passed. Raising is also a failure — a check
# that throws is reported as failed rather than taking the health endpoint with
# it, because the endpoint exists precisely for the case where things are broken.
HealthCheck = Callable[[], None]


@dataclass
class EcosystemSurface:
    """Everything the five MEP endpoints need from their host service.

    `instance_id` and `started_at` are generated here rather than configured.
    §4.1 requires the instance to differ between two processes on one machine,
    which is exactly what distinguishes a restart from a second instance — and a
    value an operator could set is a value an operator could set twice.
    """

    service_type: str
    service_id: str
    machine_id: str
    build_version: str
    declared: Mapping[str, Capability]
    revision: int = 1
    # name → callable that raises when the thing is not working.
    checks: Mapping[str, HealthCheck] = field(default_factory=dict)

    instance_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: float = field(default_factory=time.time)

    def run_checks(self) -> list[dict[str, Any]]:
        """Run every readiness check and report each one separately.

        A single boolean would tell an operator that something is wrong and not
        what, which is the point at which they start guessing. Each check is
        caught individually so one failure does not hide the others.
        """
        results = []
        for name, check in sorted(self.checks.items()):
            try:
                check()
            except Exception as failure:  # noqa: BLE001 — any failure means not ready
                results.append({"name": name, "status": "fail", "detail": str(failure)})
            else:
                results.append({"name": name, "status": "pass"})
        return results
