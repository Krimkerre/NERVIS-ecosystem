"""The MEP envelopes, as models a schema can be generated from (§4.1–§4.5).

**§15 asks that "MEP schemas, fixtures and versions are released and pinned", and
`/ecosystem/version` has always published `schema_versions: {"mep": …}`.** There
were no schemas. A version number for an artifact that does not exist is the
shape this repository keeps finding — a claim with nothing underneath it — and
this one was published by all three services to every peer that asked.

**Generated, never hand-written.** The obvious way to release a schema is to
write JSON by hand, and it is the way that drifts: two descriptions of one
envelope, one of them enforced by tests and the other by nobody. These models are
the description, `schemas()` renders them, and `tools/schema_check.py` fails when
the rendered form and the committed files disagree. The file on disk stays the
released artifact — a consumer in another language needs a file, not a Python
import — and it cannot quietly stop matching the code.

**Modelled from §4's own text, not from what the services happen to send.** The
distinction matters at exactly one point: `Health.status` is
`healthy|degraded|unhealthy` because §4.1 says so, though nothing in this
repository currently emits `unhealthy`. A schema written from observed traffic
would have two of the three, and the first service to report the third would be
told it was wrong by the artifact meant to define it.

**Pydantic because it is already here.** It arrives with FastAPI, it generates
JSON Schema, and it validates a payload against a model — so the released file
and the validator are the same definition, and no dependency was added to a
package whose whole argument is that it stays small (§3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ecosystem_protocol.version import PROTOCOL_VERSION

#: Where the released files live. Beside the package rather than inside it: they
#: are an artifact for consumers in any language, and a Python import path is not
#: something a TypeScript client can read.
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"

#: The schemas' own version, which is the protocol's. Separate constant because
#: they are separate promises: a schema may gain an optional field without the
#: protocol changing, and when that day comes this is where it stops agreeing.
SCHEMA_VERSION = PROTOCOL_VERSION


class Envelope(BaseModel):
    """Strict by default, because this validates a wire and not a caller.

    Pydantic coerces by default — `"false"` becomes `True`, `"4"` becomes `4` —
    which is right for an API taking input from a person and wrong for a
    conformance check on a payload another service sent. A validator that repairs
    what it is meant to be judging reports every producer as conformant, and the
    fixture that caught this was `ready: "false"`: accepted, coerced to `True`,
    and readiness had stopped being truthful in the field §4.1 wrote the rule for.
    """

    model_config = ConfigDict(strict=True)


class Check(Envelope):
    """One thing a service tested about itself."""

    name: str
    status: Literal["pass", "fail"]
    detail: str = ""


class Health(Envelope):
    """§4.1. `ready` is independently truthful — a TCP connect is not readiness."""

    status: Literal["healthy", "degraded", "unhealthy"]
    live: bool
    ready: bool
    checked_at: str
    checks: list[Check] = Field(default_factory=list)


class Identity(Envelope):
    """§4.1's identity, and §4.1's identity rules about what these may not be.

    `machine_id` is "locally generated, opaque, non-hardware-derived and
    resettable — never a serial number, MAC address, username or reversible
    fingerprint". A schema cannot enforce that; it is recorded here because the
    field's type is the least interesting thing about it.
    """

    service_id: str
    service_type: str
    instance_id: str
    machine_id: str
    api_version: str
    protocol_version: str
    build_version: str
    started_at: str


class Capability(Envelope):
    """§4.1. `id` carries the identifier alone.

    The `<id>@<major>` notation prose and the dashboard use is shorthand for the
    `{id, version}` pair and is **never a wire value** — which is precisely the
    kind of rule that survives in a document and dies on a wire, so the schema
    keeps them as two fields.
    """

    # No `@` — §4.1: the `<id>@<major>` notation "is never a wire value", and a
    # rule stated only in prose is one that arrives on the wire eventually.
    id: str = Field(pattern=r"^[^@]+$")
    version: str
    state: Literal["available", "degraded", "unavailable", "disabled"]
    constraints: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class Capabilities(Envelope):
    revision: int
    capabilities: list[Capability] = Field(default_factory=list)


class CompatibleProtocol(Envelope):
    min: str
    max: str


class Version(Envelope):
    """§4.1. "Must answer even when `ready` is false.\""""

    build_version: str
    api_version: str
    protocol_version: str
    schema_versions: dict[str, str]
    compatible_protocol: CompatibleProtocol


class Source(Envelope):
    """Which service produced an event. `service_type` is load-bearing: NERVIS
    groups events into spans by it, so a producer that omits it lands in a lane
    called "unknown" with everybody else who omitted it."""

    service_type: str
    service_id: str = ""
    instance_id: str = ""
    machine_id: str = ""


class Subject(Envelope):
    type: str
    id: str


class Privacy(Envelope):
    classification: str
    redactions: list[str] = Field(default_factory=list)


class EventEnvelope(Envelope):
    """§4.4. Twelve fields, of which the producer shipped six until 5 September.

    `span_id` is absent rather than optional-and-empty: §4.4 lists it "where
    applicable", and a producer here has no span to name — NERVIS derives spans
    *from* these events, so minting one would invent a structure nothing keeps.
    """

    event_id: str
    event_type: str
    event_version: str
    occurred_at: str
    source: Source
    severity: Literal["debug", "info", "warning", "error", "critical"]
    data: dict[str, Any] = Field(default_factory=dict)
    privacy: Privacy | None = None
    subject: Subject | None = None
    trace_id: str | None = None
    request_id: str | None = None
    session_id: str | None = None


class Error(Envelope):
    """§4.5's body. `retryable` is what separates "wait" from "never"."""

    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str = ""
    trace_id: str = ""


class ErrorEnvelope(Envelope):
    """The envelope §4.5 governs — RAVIS's OpenAI-compatible routes excepted,
    because clients parse those and the exception is deliberate."""

    error: Error


#: Every released schema, by the name its file carries.
MODELS: dict[str, type[BaseModel]] = {
    "health": Health,
    "identity": Identity,
    "capabilities": Capabilities,
    "version": Version,
    "event_envelope": EventEnvelope,
    "error_envelope": ErrorEnvelope,
}


def schemas() -> dict[str, dict[str, Any]]:
    """Every envelope as JSON Schema, stamped with the version it belongs to."""
    rendered: dict[str, dict[str, Any]] = {}
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://nervis.local/mep/{SCHEMA_VERSION}/{name}.json"
        rendered[name] = schema
    return rendered


def write(directory: Path | None = None) -> list[Path]:
    """Render the released files. Called by the gate with `--update`."""
    target = directory or SCHEMA_DIR
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, schema in schemas().items():
        path = target / f"{name}.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


def validate(name: str, payload: Any) -> str:
    """`""` when the payload satisfies the envelope, else what is wrong with it.

    A string rather than an exception, because every caller of this is reporting
    on somebody else's response and wants to keep going through the rest of them.
    """
    model = MODELS.get(name)
    if model is None:
        return f"no schema named {name!r}; have {', '.join(sorted(MODELS))}"
    try:
        model.model_validate(payload)
    except Exception as refusal:  # pydantic's ValidationError, kept broad on purpose
        first = str(refusal).splitlines()
        return " · ".join(line.strip() for line in first[1:4] if line.strip())
    return ""
