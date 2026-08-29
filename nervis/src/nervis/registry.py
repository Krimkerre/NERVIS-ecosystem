"""Who is out there, what they said, and how sure NERVIS is (§5.1).

**The states here are NERVIS's, not the services'.** A service reports MEP
`healthy | degraded | unhealthy` *about itself*; NERVIS adds reachability on
top, and the result is a different vocabulary with eight members. Conflating
them would lose the distinction the whole registry exists for — "it says it is
fine" and "I can reach it and it says it is fine" are not the same claim, and
only the second is worth putting a control behind.

§5.1 states the rule in one sentence, and it is the load-bearing one:

> "Healthy" means the service's truthful response plus NERVIS reachability —
> never a successful TCP connect alone.

**Declared, never discovered.** §5.3: configured localhost ports for the MVP,
and explicitly no Bonjour or mDNS. Entries come from configuration; a peer does
not get to tell NERVIS that it exists, because §5.1 forbids accepting an
unauthenticated process's claimed service type or endpoint.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping
from urllib.parse import urlsplit

logger = logging.getLogger("nervis.registry")


# §5.1's observer-side states. `discovering` is the initial one rather than a
# guess at `unreachable`: an entry NERVIS has not yet asked about has not failed
# — a dashboard that opened on a wall of red would be lying about a service it
# simply had not gotten to.
class RegistryState(str, Enum):
    DISCOVERING = "discovering"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"
    INCOMPATIBLE = "incompatible"
    UNAUTHORIZED = "unauthorized"
    STALE = "stale"
    STOPPED = "stopped"


# Which states mean a control bound to this service may run. Deliberately two:
# `degraded` is usable — it means some capability is missing, which the
# capability check catches at a finer grain than the service state can.
USABLE_STATES = frozenset({RegistryState.HEALTHY, RegistryState.DEGRADED})

# What a probe may write onto an entry. `declaration` is deliberately absent:
# what a service *is* comes from configuration, and what it *did* comes from
# observation — letting an observation rewrite the declaration would let a peer
# change its own endpoint by answering a probe.
OBSERVABLE = frozenset({
    "state", "detail", "service_id", "instance_id", "machine_id",
    "build_version", "protocol_version", "api_version",
    "capabilities", "capability_revision",
})


class OwnershipMode(str, Enum):
    """Whether NERVIS may control this service (§5.1, and M16's precondition).

    `EXTERNAL` is the default and the only one M2 ships. §3.1 attaches the
    supervision capability to *"explicitly configured owned services"*, so an
    entry that has not been declared owned is one NERVIS observes and does not
    touch.
    """

    EXTERNAL = "external"
    OWNED = "owned"


@dataclass(frozen=True)
class ServiceDeclaration:
    """One configured service, before anything has been asked of it.

    `probe_path` is what NERVIS reads when the service publishes no MEP surface.
    LM Studio and Ollama are runtimes rather than ecosystem members: they answer
    a catalogue endpoint and nothing else, so reachability is all NERVIS can
    honestly claim about them.
    """

    key: str
    label: str
    base_url: str
    mep: bool = True
    probe_path: str = ""
    ownership: OwnershipMode = OwnershipMode.EXTERNAL
    # Whether this peer's absence is an outage or a fact.
    #
    # An optional peer is one NERVIS works without and did not choose the
    # address of: a local runtime nobody configured, or the Clarvis Bridge,
    # which starts and stops with an editor window. It is still probed — a
    # refused connection on loopback costs about a millisecond, and probing is
    # what lets one appear the moment somebody starts it — but until it has
    # answered once, "not there" is reported as **absent rather than broken**.
    #
    # The distinction that makes this honest is *has it ever answered*. Once a
    # peer has, its going away is a real outage and says so.
    optional: bool = False


@dataclass
class RegistryEntry:
    """A declaration plus everything observing it has established (§5.1).

    Mutable, unlike almost everything else in this codebase, because that is
    what an entry *is*: one row whose observed half is rewritten on every probe
    while its declared half never changes.
    """

    declaration: ServiceDeclaration
    state: RegistryState = RegistryState.DISCOVERING
    detail: str = ""
    service_id: str = ""
    instance_id: str = ""
    machine_id: str = ""
    build_version: str = ""
    protocol_version: str = ""
    api_version: str = ""
    capabilities: dict[str, str] = field(default_factory=dict)
    capability_revision: int = 0
    last_seen: float = 0.0
    checked_at: float = 0.0

    @property
    def key(self) -> str:
        return self.declaration.key

    @property
    def is_usable(self) -> bool:
        return self.state in USABLE_STATES

    @property
    def awaiting_first_contact(self) -> bool:
        """An optional peer that has never answered — absent, not broken.

        The condition a status line should stay quiet about. An installation
        with LM Studio and no Ollama would otherwise read "Ollama unreachable"
        forever, which is an alarm about software that was never installed.

        Deliberately keyed on `last_seen` rather than on configuration alone: a
        peer that answered once and then stopped is a genuine outage, whatever
        it was configured from.
        """
        return self.declaration.optional and not self.last_seen and not self.is_usable

    def as_dict(self) -> dict[str, Any]:
        """The entry as `/api/v1/services` publishes it.

        No authentication reference and no credential. §5.1 says secrets are
        stored separately, and a registry listing is exactly the surface where
        one would otherwise be published by accident.
        """
        return {
            "key": self.key,
            "label": self.declaration.label,
            "endpoint": self.declaration.base_url,
            "ownership": self.declaration.ownership.value,
            "optional": self.declaration.optional,
            "awaiting_first_contact": self.awaiting_first_contact,
            "publishes_mep": self.declaration.mep,
            "state": self.state.value,
            "detail": self.detail,
            "service_id": self.service_id,
            "instance_id": self.instance_id,
            "machine_id": self.machine_id,
            "build_version": self.build_version,
            "protocol_version": self.protocol_version,
            "api_version": self.api_version,
            "capabilities": dict(self.capabilities),
            "capability_revision": self.capability_revision,
            "last_seen": self.last_seen or None,
            "checked_at": self.checked_at or None,
        }


class EndpointRefusedError(ValueError):
    """An endpoint NERVIS will not talk to, whatever declared it."""


# Loopback only, by default. §5.1 requires SSRF prevention by "allowing only
# configured local transports and hosts by default", and a control plane is the
# single worst place to get this wrong: it holds a list of URLs and fetches
# every one of them on a timer, which is a server-side request forgery primitive
# with a scheduler attached.
ALLOWED_SCHEMES = frozenset({"http", "https"})


def allowed_endpoint(url: str, *, extra_hosts: frozenset[str] = frozenset()) -> str:
    """The endpoint, normalised — or a refusal saying which rule it broke.

    Applied to *every* entry regardless of where it came from. M2 registers
    only from configuration, so there is no untrusted registration path yet;
    writing the guard at the entry rather than at the registration endpoint
    means the endpoint cannot be added later without it.

    A hostname that is not a literal address is refused rather than resolved.
    Resolving would make the check depend on DNS at the moment of the check, and
    a name that resolves to loopback now can resolve elsewhere on the next
    probe — which is the DNS-rebinding half of SSRF, and the half a naive
    allowlist misses.
    """
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise EndpointRefusedError(f"scheme {parts.scheme or '(none)'!r} is not http or https")
    host = parts.hostname or ""
    if not host:
        raise EndpointRefusedError("no host")
    # **Userinfo first, because it is the trick.** `http://127.0.0.1@evil/` has
    # hostname `evil` and reads to a person as loopback.
    if parts.username or parts.password:
        raise EndpointRefusedError("a base URL may not carry credentials")

    _refuse_remote_host(host, extra_hosts)

    # A base URL is an **origin**. A path, query or fragment on it would be
    # silently prepended to every surface path, so an entry that passed the
    # loopback check could still point at a proxying path on a genuinely local
    # service. Checked after the host, so an obviously remote address reports
    # the reason that matters rather than the one it happened to trip first.
    if parts.path.strip("/") or parts.query or parts.fragment:
        raise EndpointRefusedError(
            "a base URL is an origin — it may not carry a path, query or fragment"
        )
    return f"{parts.scheme}://{parts.netloc}".rstrip("/")


class Registry:
    """Every declared service and the last thing observed about each (§5.1).

    In-process and rebuilt from configuration at startup. What persists is the
    *observation* — `service_seen` in the database — because "stale" and
    "stopped" are claims about time, and a registry that forgot every restart
    could never make one.
    """

    def __init__(
        self,
        declarations: list[ServiceDeclaration],
        *,
        stale_after_seconds: float = 90.0,
        now: Any = time.time,
    ) -> None:
        self._now = now
        self._stale_after = stale_after_seconds
        self._entries: dict[str, RegistryEntry] = {}
        for declaration in declarations:
            self._entries[declaration.key] = RegistryEntry(declaration=declaration)

    def all(self) -> list[RegistryEntry]:
        """Every entry, in declaration order, with staleness applied.

        Staleness is computed on read rather than written by a timer. A value
        that only becomes stale when something runs is a value that stays fresh
        forever if the timer dies — and the timer dying is precisely the failure
        this state exists to make visible.
        """
        return [self._aged(entry) for entry in self._entries.values()]

    def get(self, key: str) -> RegistryEntry | None:
        entry = self._entries.get(key)
        return self._aged(entry) if entry else None

    def _aged(self, entry: RegistryEntry) -> RegistryEntry:
        if entry.state not in USABLE_STATES:
            return entry
        if self._now() - entry.checked_at <= self._stale_after:
            return entry
        entry.state = RegistryState.STALE
        entry.detail = f"last answered {int(self._now() - entry.checked_at)}s ago"
        return entry

    def record(self, key: str, observation: Mapping[str, Any]) -> RegistryEntry:
        """Write what a probe established, refusing to overwrite a live instance.

        §5.1: *"Resolve duplicate stable IDs without overwriting a live
        instance."* Two processes claiming one `service_id` is either a
        misconfiguration or an impersonation attempt, and the resolution is to
        keep the one already answering rather than to let the newest writer win
        — a race that an attacker controls the timing of is not a tie-break.
        """
        entry = self._entries[key]
        claimed = str(observation.get("service_id") or "")
        if claimed and self._claimed_elsewhere(key, claimed):
            entry.state = RegistryState.UNAUTHORIZED
            entry.detail = f"service_id {claimed} is already held by a live instance"
            entry.checked_at = self._now()
            return entry
        for name, value in observation.items():
            if name not in OBSERVABLE:
                # A closed allowlist rather than a blind `setattr`. This wrote
                # whatever a caller handed it, which is fine while the only
                # caller is `probes.py` returning a fixed shape — and is a hole
                # the moment anything else can reach it, because `declaration`
                # and `state` are both attributes and both spellable.
                logger.warning("ignoring unexpected observation field %r for %s", name, key)
                continue
            setattr(entry, name, value)
        entry.checked_at = self._now()
        if entry.state in USABLE_STATES:
            entry.last_seen = entry.checked_at
        return entry

    def _claimed_elsewhere(self, key: str, service_id: str) -> bool:
        """Whether another entry that is currently answering holds this id."""
        return any(
            other.key != key and other.service_id == service_id and other.is_usable
            for other in self._entries.values()
        )


def declared_services(settings: Any) -> list[ServiceDeclaration]:
    """§5.1's initial entries, from configuration.

    `code-server` is on §5.1's list and is deliberately absent here: §3.1 gates
    its capability on M13's spike, whose exit may be that it is never built.
    Declaring an entry for something with no decided endpoint would put a row on
    the dashboard that can only ever read `unreachable`.

    NERVIS itself is included, because §3.1 says it *"implements and consumes"*
    the MEP and a registry that skipped its own host would be the one entry
    nobody could check.
    """
    # Whether the operator chose this address or inherited a default. pydantic
    # records which fields were actually supplied, which is the only reliable
    # answer — comparing a value against the default would call an operator who
    # deliberately typed the default address "unconfigured".
    chosen = set(getattr(settings, "model_fields_set", set()))

    def runtime(key: str, label: str, url: str, path: str) -> ServiceDeclaration:
        """A local runtime, optional unless somebody named it.

        RAVIS and SIRVIS are not optional: NERVIS exists to watch them, and one
        being down is the thing it is for. A runtime is different — it is
        somebody else's program, and a machine with LM Studio and no Ollama is
        an ordinary machine rather than one with a fault.
        """
        # `codeserver` has no underscore in its key but its setting does, so the
        # name is derived rather than interpolated — getting this wrong would
        # make an address the operator deliberately set look like a default, and
        # the entry would go quiet instead of reporting an outage.
        setting = "code_server_base_url" if key == "codeserver" else f"{key}_base_url"
        return ServiceDeclaration(
            key, label, url, mep=False, probe_path=path,
            optional=setting not in chosen,
        )

    return [
        ServiceDeclaration("nervis", "NERVIS", f"http://{settings.host}:{settings.port}"),
        ServiceDeclaration("ravis", "RAVIS", settings.ravis_base_url),
        ServiceDeclaration("sirvis", "SIRVIS", settings.sirvis_base_url),
        # **No static entry for the Clarvis Bridge, deliberately.**
        #
        # There was one, probing `clarvis_base_url` — a fixed 127.0.0.1:7071.
        # The runbook's own port table says the Bridge's port is "dynamic and
        # discovered through registration, never assumed", and §6.6 has each
        # editor window take an OS-assigned one, so nothing ever binds that
        # address. The row could not go green under any circumstances, and it
        # produced two display defects before anybody noticed why: a red node on
        # the ecosystem map with two live Bridges on the machine, and a
        # "5 / 7 reachable" tile counting a peer that does not exist.
        #
        # Where a Bridge actually is comes from `instances.py` — the registration
        # each window makes, with the port it chose. That is the only place that
        # can know, and now the only place that says.
        runtime("codeserver", "code-server", settings.code_server_base_url, "/healthz"),
        runtime("lmstudio", "LM Studio", settings.lmstudio_base_url, "/v1/models"),
        runtime("ollama", "Ollama", settings.ollama_base_url, "/api/tags"),
    ]


def admissible(
    declarations: list[ServiceDeclaration], allowed_hosts: list[str]
) -> tuple[list[ServiceDeclaration], list[tuple[str, str]]]:
    """Split declarations into the ones NERVIS may probe and the ones it refuses.

    Refusals are *returned* rather than raised. A single bad endpoint must not
    stop NERVIS starting — §5.1's gate says an offline service never breaks the
    page, and a malformed one deserves the same treatment. `doctor` prints them
    and the registry omits them, which is visible without being fatal.
    """
    extra = frozenset(allowed_hosts)
    admitted: list[ServiceDeclaration] = []
    refused: list[tuple[str, str]] = []
    for declaration in declarations:
        try:
            canonical = allowed_endpoint(declaration.base_url, extra_hosts=extra)
        except EndpointRefusedError as refusal:
            refused.append((declaration.key, str(refusal)))
            continue
        # The *canonical* form, not the one that was declared. Returning a
        # normalised origin and then discarding it left the guard checking one
        # string while every probe used another.
        admitted.append(replace(declaration, base_url=canonical))
    return admitted, refused


def _refuse_remote_host(host: str, extra_hosts: frozenset[str]) -> None:
    """Everything the host alone decides, extracted so `allowed_endpoint` stays
    under the complexity gate as it grew.

    A hostname is refused rather than resolved: resolving would make the check
    depend on DNS at the moment of the check, and a name that resolves to
    loopback now can resolve elsewhere on the next probe — which is the
    rebinding half of SSRF, and the half a resolve-then-allowlist always misses.
    """
    if host in extra_hosts or host in {"localhost", "localhost."}:
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as failure:
        raise EndpointRefusedError(
            f"host {host!r} is a name, not a literal address; "
            "resolving one would make this check depend on DNS at probe time"
        ) from failure
    if not address.is_loopback:
        raise EndpointRefusedError(f"host {host} is not loopback; add it to NERVIS_ALLOWED_HOSTS")
