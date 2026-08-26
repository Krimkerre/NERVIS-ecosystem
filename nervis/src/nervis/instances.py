"""Dynamically registered instances, one per extension host (§5.1, M8a).

The configured registry answers "is RAVIS up" — one row per service, address
known in advance. That model breaks on the Clarvis Bridge, which is **one
process per open editor window**. Two VS Code windows are two Bridges on two
ports, both genuinely Clarvis, and neither is the configured `clarvis` entry.
Folding them into one row would report the union of two workspaces as one
thing, which is wrong in the direction that matters: it would show one window's
activity under the other's name.

So instances are keyed by `(service, instance_id)` and never merged. §5.1's
duplicate rule applies within that key: a second process claiming an
`instance_id` that a live one holds is refused, rather than being allowed to
take the row over.

**Redaction is structural, not a filter.** The claim a registrant may make is a
closed allowlist of scalar fields, and there is no free-form string in it. That
is deliberate and it is the second design pass — the first had a `label`, on
the reasonable-sounding grounds that a user with three windows open wants to
tell them apart. But a Bridge's natural label is its workspace folder name, and
`CLARVIS.md` §6.7 forbids NERVIS holding the workspace root. A field that
*invites* the value you have promised not to store is worse than no field: the
promise then depends on every future caller's restraint. NERVIS derives a label
from the instance id instead, so there is nowhere for a path to go.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from hmac import compare_digest
from typing import Any, Callable, Mapping

from .registry import EndpointRefusedError, allowed_endpoint

logger = logging.getLogger("nervis.instances")

# What a registrant may claim about itself. Everything else in the body is
# dropped with a log line naming the *key* — never the value, because the whole
# reason a key is not on this list is that NERVIS does not want its contents.
#
# Note what is absent: any path, any label, any free text, any token belonging
# to the registrant. §5.1 says secrets are stored separately; the strongest
# form of "separately" is "not at all", and NERVIS never needs to call *back*
# into a Bridge with the Bridge's own credential.
CLAIMABLE = frozenset({
    "service", "instance_id", "machine_id", "port",
    "api_version", "protocol_version", "capabilities",
})

# Which services may register dynamically at all. Not an open door: §5.1 says
# do not accept a claimed *service type*, and enrolment proves the caller is
# the user, not that it is the program it says it is. Restricting the claim to
# services whose model is genuinely per-instance means a compromised local
# process cannot register itself as RAVIS and start collecting chat traffic.
DYNAMIC_SERVICES = frozenset({"clarvis"})

# How long a registration is believed without a heartbeat, and how long a dead
# instance stays visible before it is dropped. Two numbers because they answer
# different questions: the first is "should I still route to it", the second is
# "should the user still see that it was here". An editor window that closed
# should stop being counted immediately and stop being listed shortly after.
LEASE_SECONDS = 45.0
EVICT_AFTER_SECONDS = 300.0


class RegistrationRefusedError(ValueError):
    """A registration NERVIS will not accept, with the rule it broke."""


@dataclass
class Instance:
    """One registered process, and what NERVIS is willing to know about it."""

    service: str
    instance_id: str
    base_url: str
    machine_id: str = ""
    api_version: str = ""
    protocol_version: str = ""
    capabilities: dict[str, str] = field(default_factory=dict)
    registered_at: float = 0.0
    renewed_at: float = 0.0
    # The token NERVIS issued *to* this instance, for it to present on
    # heartbeat and deregistration. Held so it can be compared, never published
    # — `as_dict` below is what the API returns and this is not in it.
    token: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.service, self.instance_id)

    @property
    def label(self) -> str:
        """Derived, so that no caller can put a workspace path in it.

        Short enough to read, long enough to distinguish the two or three
        windows a person actually has open.
        """
        return f"Clarvis Bridge · {self.instance_id[:8]}"

    def is_live(self, now: float, lease: float = LEASE_SECONDS) -> bool:
        return now - self.renewed_at <= lease

    def as_dict(self, now: float) -> dict[str, Any]:
        """What `/api/v1/registry/instances` publishes.

        No token, no port-and-token pair that would let a reader impersonate
        the instance, and no field that has ever held a path.
        """
        return {
            "service": self.service,
            "instance_id": self.instance_id,
            "label": self.label,
            "endpoint": self.base_url,
            "machine_id": self.machine_id,
            "api_version": self.api_version,
            "protocol_version": self.protocol_version,
            "capabilities": dict(self.capabilities),
            "registered_at": self.registered_at,
            "renewed_at": self.renewed_at,
            "live": self.is_live(now),
            "expires_in": max(0.0, round(LEASE_SECONDS - (now - self.renewed_at), 1)),
        }


class Instances:
    """The dynamic half of §5.1's registry.

    Separate from `Registry` rather than a mode of it, because the two have
    different trust: a configured entry was written by the operator and an
    instance was claimed by a process. Sharing one container would mean every
    piece of code that reads the registry has to remember which kind it is
    holding, and the first one to forget is the bug.
    """

    def __init__(
        self,
        *,
        allowed_hosts: frozenset[str] = frozenset(),
        now: Callable[[], float] = time.time,
    ) -> None:
        self._instances: dict[tuple[str, str], Instance] = {}
        self._allowed_hosts = allowed_hosts
        self._now = now

    def register(self, claim: Mapping[str, Any]) -> tuple[Instance, str]:
        """Accept a claim, or refuse it saying which rule it broke.

        Returns the instance and the token it must present afterwards. The
        token is returned *once*, here — it is not readable back out of the
        API, so a process that loses it re-registers rather than asking.
        """
        accepted = self._accepted_fields(claim)
        service = str(accepted.get("service") or "")
        if service not in DYNAMIC_SERVICES:
            raise RegistrationRefusedError(
                f"{service or 'a service with no name'} may not register dynamically; "
                f"dynamic registration is limited to {sorted(DYNAMIC_SERVICES)}"
            )
        instance_id = str(accepted.get("instance_id") or "")
        if not instance_id:
            raise RegistrationRefusedError("instance_id is required and identifies the process")

        base_url = self._endpoint_for(accepted.get("port"))
        existing = self._instances.get((service, instance_id))
        if existing and existing.is_live(self._now()):
            # §5.1: resolve duplicates *without overwriting a live instance*.
            raise RegistrationRefusedError(
                f"instance_id {instance_id} is held by an instance that is still answering"
            )

        token = secrets.token_urlsafe(32)
        instance = Instance(
            service=service,
            instance_id=instance_id,
            base_url=base_url,
            machine_id=str(accepted.get("machine_id") or ""),
            api_version=str(accepted.get("api_version") or ""),
            protocol_version=str(accepted.get("protocol_version") or ""),
            capabilities=_capabilities(accepted.get("capabilities")),
            registered_at=self._now(),
            renewed_at=self._now(),
            token=token,
        )
        self._instances[instance.key] = instance
        logger.info("registered %s instance %s at %s", service, instance_id, base_url)
        return instance, token

    def _accepted_fields(self, claim: Mapping[str, Any]) -> dict[str, Any]:
        """The allowlist, applied before anything is read out of the claim.

        Logging the dropped *key* and not its value is the point: a registrant
        that sent `workspace_root` should produce a diagnostic that helps
        somebody fix their client, without that diagnostic being the leak.
        """
        dropped = [name for name in claim if name not in CLAIMABLE]
        if dropped:
            logger.info("ignoring unclaimable registration field(s): %s", sorted(dropped))
        return {name: value for name, value in claim.items() if name in CLAIMABLE}

    def _endpoint_for(self, port: Any) -> str:
        """A port, not a URL. The host is NERVIS's to decide, and it is loopback.

        Letting a registrant supply a full endpoint would hand it the SSRF
        primitive `allowed_endpoint` exists to deny — and the guard is still
        applied underneath, so the two disagree only in NERVIS's favour.
        """
        try:
            number = int(port)
        except (TypeError, ValueError):
            raise RegistrationRefusedError("port must be a number") from None
        if not 1 <= number <= 65535:
            raise RegistrationRefusedError(f"port {number} is not a port")
        try:
            return allowed_endpoint(
                f"http://127.0.0.1:{number}", extra_hosts=self._allowed_hosts
            )
        except EndpointRefusedError as refusal:
            raise RegistrationRefusedError(str(refusal)) from refusal

    def renew(self, service: str, instance_id: str, token: str) -> Instance:
        """Extend a lease, on proof of the token issued at registration."""
        instance = self._authenticated(service, instance_id, token)
        instance.renewed_at = self._now()
        return instance

    def deregister(self, service: str, instance_id: str, token: str) -> None:
        """An editor window closing, said out loud rather than left to the lease.

        Worth having even though the lease would expire anyway: `CLARVIS.md`
        §6.7 forbids keeping Clarvis running past its extension host, and a
        registry that showed a closed window as live for another forty seconds
        would be the visible half of exactly that.
        """
        instance = self._authenticated(service, instance_id, token)
        del self._instances[instance.key]
        logger.info("deregistered %s instance %s", service, instance_id)

    def _authenticated(self, service: str, instance_id: str, token: str) -> Instance:
        """The instance, if the caller holds its token.

        One refusal message for "no such instance" and "wrong token" on
        purpose: distinguishing them tells an attacker which instance ids
        exist, and the caller who legitimately holds a token cannot tell the
        difference anyway because for them neither case happens.
        """
        instance = self._instances.get((service, instance_id))
        if not instance or not token or not compare_digest(token, instance.token):
            raise RegistrationRefusedError("no such instance, or the token does not match it")
        return instance

    def all(self) -> list[Instance]:
        """Live and recently-dead instances, evicting the long dead first."""
        self._evict()
        return sorted(self._instances.values(), key=lambda one: one.registered_at)

    def live(self, service: str = "") -> list[Instance]:
        now = self._now()
        return [
            one for one in self.all()
            if one.is_live(now) and (not service or one.service == service)
        ]

    def _evict(self) -> None:
        """Drop what has been silent long past its lease.

        §5.1 lists `stale` and `stopped` as states rather than as deletions,
        which is why eviction waits: an instance that vanished five seconds ago
        is information, and one that vanished an hour ago is clutter.
        """
        now = self._now()
        expired = [
            key for key, one in self._instances.items()
            if now - one.renewed_at > EVICT_AFTER_SECONDS
        ]
        for key in expired:
            del self._instances[key]
            logger.info("evicted %s instance %s after its lease lapsed", *key)


def _capabilities(claimed: Any) -> dict[str, str]:
    """Capabilities as declared, flattened to `id -> version` strings.

    Coerced rather than trusted: a registrant that sends a nested object under
    a capability id would otherwise put arbitrary structure into something the
    dashboard renders.
    """
    if not isinstance(claimed, Mapping):
        return {}
    return {str(name): str(version) for name, version in claimed.items()}
