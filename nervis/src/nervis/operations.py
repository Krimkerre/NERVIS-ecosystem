"""Every control the dashboard can offer, and what each one needs (§5.2).

§5.2 requires a control to declare its owning service, capability version,
read-versus-mutate authorization, availability reason, request timeout and
idempotency. Keeping the declarations here rather than beside the buttons means
**a control cannot exist without having answered those questions** — which is
the difference between a rule and a convention.

The dashboard reads this list from `/api/v1/services`, so adding a control is
adding an entry here. It is deliberately not derived from the capability lists
the peers publish: a capability is something a service can do, an operation is
something this UI offers, and several operations can sit behind one capability.

**Nothing here mutates.** §15.1 makes RAVIS's management surface read-only until
M18b and SIRVIS's control surface is authorized separately, so `mutates` is
false throughout — present as structure for the milestone that adds the first
one rather than as a field nobody filled in.
"""

from __future__ import annotations

from nervis.negotiation import Operation

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        key="ravis.chat",
        service="ravis",
        capability="ravis.openai_compatible.chat_completions",
        label="Chat through RAVIS",
        # A completion is not idempotent and never will be: repeating it spends
        # tokens and produces a different answer. The flag is what stops a
        # transport retry being added later without anyone noticing.
        idempotent=False,
        timeout_seconds=120.0,
    ),
    Operation(
        key="ravis.routes",
        service="ravis",
        capability="ravis.routing.explanations",
        label="Inspect a route decision",
    ),
    Operation(
        key="ravis.usage",
        service="ravis",
        capability="ravis.usage_cost",
        label="Read usage and cost",
    ),
    Operation(
        key="ravis.pools",
        service="ravis",
        capability="ravis.virtual_profiles",
        label="List routing pools",
    ),
    Operation(
        key="ravis.management",
        service="ravis",
        capability="ravis.management",
        label="Read the management surface",
    ),
    Operation(
        key="sirvis.inventory",
        service="sirvis",
        capability="sirvis.inventory.read",
        label="Browse machines, runtimes and models",
    ),
    Operation(
        key="sirvis.results",
        service="sirvis",
        capability="sirvis.benchmarks.results",
        label="Read benchmark results and evidence",
    ),
    Operation(
        key="sirvis.recommendations",
        service="sirvis",
        capability="sirvis.recommendations",
        label="Ask for a recommendation",
    ),
    Operation(
        key="sirvis.runtime_state",
        service="sirvis",
        capability="sirvis.runtime.state.read",
        label="Read what is loaded",
    ),
    Operation(
        key="sirvis.runtime_control",
        service="sirvis",
        capability="sirvis.runtime.control",
        label="Load or release a model",
        # The one operation here that changes something on the machine. Left
        # read-classified until M16 gives NERVIS an ownership mode to check:
        # SIRVIS.md marks load/unload "only if implemented and authorized", and
        # NERVIS has no authorization story of its own yet.
        timeout_seconds=180.0,
        idempotent=False,
    ),
    Operation(
        key="sirvis.jobs",
        service="sirvis",
        capability="sirvis.benchmarks.jobs",
        label="Submit or cancel a benchmark",
        idempotent=False,
    ),
    Operation(
        key="ravis.sessions",
        service="ravis",
        capability="ravis.sessions",
        label="Inspect routing sessions",
    ),
    Operation(
        key="nervis.events",
        service="nervis",
        capability="nervis.event_hub",
        label="Subscribe to the event feed",
    ),
    Operation(
        key="nervis.traces",
        service="nervis",
        capability="nervis.traces",
        label="Follow a trace",
    ),
    Operation(
        key="clarvis.visibility",
        service="clarvis",
        capability="clarvis.status.read",
        label="See editor instances",
    ),
)
