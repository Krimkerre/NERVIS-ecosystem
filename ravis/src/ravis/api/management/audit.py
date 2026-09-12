"""M18b's audit record for management mutations (RAVIS.md §15.1).

§15.1: mutations *"are separately authorized and audited"*. This is the audited
half. It became possible rather than merely specified when M18b's other half
gave RAVIS a publisher — an audit event is an ordinary §4.4 envelope on the
ecosystem's own hub, so it lands beside the route decisions it explains and on
the same timeline.

**Built from an allowlist, never from the request.** §15.1 ends with *"Never
expose credential values"*, and the way to keep that true is not to remember to
redact: it is for the secret never to be in scope at the call site. Every caller
here passes named, non-secret facts — which provider, which pool, how many
models — and there is no path that takes a body and serialises it. `redact_deep`
runs underneath as well, and is the second line rather than the first.

**An audit event always carries a trace, even when the caller sent none.**
`EventPublisher.emit` deliberately drops an event with an empty trace_id,
because such an event is stored by the hub and then dropped by `summarise` —
present and invisible, the worst of the three outcomes. That rule is right for
telemetry and wrong for an audit record: a browser sends no `traceparent`, and
the dashboard is precisely where these mutations come from, so honouring the
rule unchanged would mean the audit fired for everything except the ordinary
case. So a trace is minted when the caller has none. The event is still
correlatable by what it names.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request

# What every audit event is about. Named rather than derived from the route, so
# a renamed endpoint does not silently rename the audit trail.
ACTION_CREDENTIAL_SET = "ravis.credential.set"
ACTION_CREDENTIAL_FORGOTTEN = "ravis.credential.forgotten"
ACTION_PROVIDER_ENABLED = "ravis.provider.enabled_changed"
ACTION_PROVIDER_MODELS = "ravis.provider.model_filter_changed"
ACTION_POOL_MEMBERS = "ravis.pool.members_changed"
# An operator ending a tool-refusal suppression before its window ran out.
ACTION_SUPPRESSION_LIFTED = "ravis.capability.suppression_lifted"


def record(request: Request, action: str, **facts: Any) -> None:
    """Publish one audit event. Never raises, never blocks.

    Same contract as every other `emit` in this codebase: the call site is one
    unconditional line, because a management handler must not fail because a
    dashboard is not running. §15.1 asks that a mutation be audited; it does not
    ask that a mutation be refused when the audit cannot be delivered, and a
    write that succeeded and then reported failure would be worse than either.

    What that costs is worth saying plainly: this is a published event, not a
    durable local ledger. If no hub is configured, or the publisher's buffer
    overruns during an outage, the record is lost — and the publisher logs when
    it drops. A tamper-evident local audit log is a stronger thing than §15.1
    asks for and is not this.
    """
    identity = getattr(request.state, "identity", None)
    request.app.state.events.emit(
        action,
        # Minted when absent — see the module docstring. A dashboard sends no
        # `traceparent`, and that is the case this exists for.
        trace_id=str(getattr(request.state, "trace_id", "") or "") or uuid.uuid4().hex,
        data={
            "request_id": getattr(request.state, "request_id", ""),
            # Who asked, as far as RAVIS can tell. `anonymous` is a real answer
            # on a loopback bind and is recorded as one rather than omitted —
            # an audit trail that hides the ordinary case describes nothing.
            "application_id": (
                getattr(identity, "application_id", "") if identity else "anonymous"
            ),
            **facts,
        },
    )
