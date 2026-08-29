"""Asking each service what it is, and deciding what that answer means (§5.2).

Two kinds of peer, and the difference is not cosmetic:

- **MEP members** — RAVIS, SIRVIS, NERVIS itself — publish identity, version and
  capabilities, so NERVIS can say what they *can do* and not merely that they
  answered.
- **Runtimes** — LM Studio, Ollama — publish a catalogue endpoint and nothing
  else. Reachability is the whole of what NERVIS can honestly claim about them,
  and the entry says so rather than implying a capability set it never read.

**Every failure maps to a state, and no failure raises.** §5.1's gate requires
an offline service never to break the page; a probe that propagated an exception
would take the refresh loop with it and freeze every *other* entry at whatever
it last held, which is worse than the one service being down.

**Timeouts are bounded and short.** A refused connection fails instantly; a
filtered port hangs forever. Without a deadline the second case turns one
misconfigured entry into a registry that never finishes a pass.
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx
from ecosystem_protocol import PROTOCOL_VERSION, is_supported_protocol, wire_identifier

from nervis.registry import RegistryEntry, RegistryState, ServiceDeclaration

# Long enough for a service that is starting, short enough that six of them in
# sequence still finish inside a dashboard's patience.
PROBE_TIMEOUT_SECONDS = 2.0


async def probe(
    client: httpx.AsyncClient,
    declaration: ServiceDeclaration,
    known: RegistryEntry | None = None,
) -> dict[str, Any]:
    """One observation, as fields the registry can write straight onto an entry.

    `known` is what the registry already holds, and passing it turns a four-call
    probe into a two-call one. Identity and version change when a peer restarts
    or is rebuilt, and `instance_id` on the health-free path would not reveal
    that — so the full pass runs again whenever the light one finds the service
    not answering, which is what a restart looks like from here.

    The saving is not cosmetic. Four calls per service per pass against three MEP
    services is twelve requests a pass; RAVIS's anonymous limit is sixty a
    minute, and NERVIS exceeded it by shortening its own interval.
    """
    try:
        if not declaration.mep:
            return await _probe_runtime(client, declaration)
        return await _probe_mep(client, declaration, known)
    except ProbeFailed as decided:
        # One place turns a failed read into an entry state, rather than five
        # call sites each re-checking whether what they got back was a body or
        # an excuse.
        return decided.observation


async def _probe_runtime(
    client: httpx.AsyncClient, declaration: ServiceDeclaration
) -> dict[str, Any]:
    """A service with no MEP surface: reachable, or not.

    `capabilities` stays empty, which is the honest answer and the one §5.2
    requires — *"unknown capabilities are unavailable"*. Inventing an entry like
    `lmstudio.chat` because the port answered would be a guess NERVIS then
    invites a control to be bound to.
    """
    url = declaration.base_url + (declaration.probe_path or "/")
    try:
        response = await client.get(url, timeout=PROBE_TIMEOUT_SECONDS)
    except httpx.HTTPError as failure:
        return _unreachable(failure)
    if response.status_code in (401, 403):
        return {
            "state": RegistryState.UNAUTHORIZED,
            "detail": f"{declaration.probe_path or '/'} refused: HTTP {response.status_code}",
        }
    if response.status_code == 429:
        return {
            "state": RegistryState.DEGRADED,
            "detail": "rate limited — NERVIS is probing more often than this service allows",
        }
    if response.status_code >= 500:
        return {
            "state": RegistryState.DEGRADED,
            "detail": f"answered HTTP {response.status_code}",
        }
    if response.status_code >= 400:
        return {
            "state": RegistryState.UNREACHABLE,
            "detail": f"no usable endpoint: HTTP {response.status_code}",
        }
    return {
        "state": RegistryState.HEALTHY,
        "detail": "answering; publishes no MEP surface, so no capabilities are known",
    }


async def _probe_mep(
    client: httpx.AsyncClient,
    declaration: ServiceDeclaration,
    known: RegistryEntry | None = None,
) -> dict[str, Any]:
    """Identity, version and capabilities, in the order that lets each fail usefully.

    **`/ecosystem/version` first, and the protocol check before anything else.**
    §4.1 requires version to answer even when `ready` is false, so it is the one
    read that works on a service too broken to do anything else — and §5.2 says
    an unsupported major makes the peer incompatible, which is a different thing
    from unreachable and deserves its own state rather than a failed health
    read.
    """
    settled = known is not None and known.is_usable and known.service_id
    if settled:
        assert known is not None
        version: Any = {"protocol_version": known.protocol_version,
                        "build_version": known.build_version}
    else:
        version = await _read(client, declaration.base_url + "/ecosystem/version")
    declared = str(version.get("protocol_version") or "")
    if declared and not is_supported_protocol(declared):
        return {
            "state": RegistryState.INCOMPATIBLE,
            "detail": f"speaks protocol {declared}; this NERVIS implements {PROTOCOL_VERSION}",
            "protocol_version": declared,
            "build_version": str(version.get("build_version") or ""),
        }

    if settled:
        assert known is not None
        identity: Any = {
            "service_id": known.service_id, "instance_id": known.instance_id,
            "machine_id": known.machine_id, "build_version": known.build_version,
            "api_version": known.api_version,
        }
    else:
        identity = await _read(client, declaration.base_url + "/ecosystem/identity")

    health = await _read(client, declaration.base_url + "/ecosystem/health")

    capabilities = await _read(client, declaration.base_url + "/ecosystem/capabilities")
    advertised = _capabilities(capabilities)

    return {
        # §5.1's sentence, applied: the state is the service's own truthful
        # answer AND the fact that NERVIS reached it. A peer reporting
        # `unhealthy` about itself is `degraded` here rather than `unreachable`
        # — NERVIS reached it perfectly well, and it said no.
        "state": _state_from(str(health.get("status") or "")),
        "detail": _detail_from(health),
        "service_id": str(identity.get("service_id") or ""),
        "instance_id": str(identity.get("instance_id") or ""),
        "machine_id": str(identity.get("machine_id") or ""),
        "build_version": str(identity.get("build_version") or ""),
        "protocol_version": declared,
        "api_version": str(identity.get("api_version") or ""),
        "capabilities": advertised,
        # §4.1 requires a reason on every capability that is not `available`,
        # and NERVIS was reading the state and dropping the sentence next to it.
        # That sentence is the answer to "why can it not do that" — the one
        # question a status screen cannot answer from a state alone, and the one
        # chat is asked in words.
        "capability_reasons": _capability_reasons(capabilities),
        "capability_revision": int(capabilities.get("revision") or 0),
    }


class ProbeFailed(Exception):  # noqa: N818 - not an error; a probe outcome
    """A read that decided the entry's state, carrying that state.

    **Raised rather than returned, and that is a correction.** `_read` used to
    return *either* the decoded body *or* a state dict, and every one of its
    call sites re-discriminated the union with `isinstance(x, dict) and "state"
    in x`. The docstring even acknowledged the ambiguity — "a body with a
    top-level `state` would be ambiguous" — which is the tell: an in-band error
    signal that needs a caveat about collisions is one collision away from being
    wrong, and each new MEP endpoint added another copy of the check.
    """

    def __init__(self, observation: dict[str, Any]) -> None:
        super().__init__(str(observation.get("detail") or observation.get("state")))
        self.observation = observation


async def _read(client: httpx.AsyncClient, url: str) -> Mapping[str, Any]:
    """One MEP read. Raises `ProbeFailed` carrying the state its failure means."""
    try:
        response = await client.get(url, timeout=PROBE_TIMEOUT_SECONDS)
    except httpx.HTTPError as failure:
        raise ProbeFailed(_unreachable(failure)) from failure
    if response.status_code in (401, 403):
        raise ProbeFailed({
            "state": RegistryState.UNAUTHORIZED,
            "detail": f"authentication rejected: HTTP {response.status_code}",
        })
    if response.status_code == 429:
        # Reachable, answering, and telling NERVIS to ask less often. Not a
        # health problem and emphatically not something probing harder fixes —
        # NERVIS caused this once by shortening its interval until it exceeded
        # RAVIS's 60-per-minute anonymous limit, at which point every read
        # failed, the registry read as unhealthy, and that kept the short
        # interval on. A self-sustaining outage of NERVIS's own making.
        raise ProbeFailed({
            "state": RegistryState.DEGRADED,
            "detail": "rate limited — NERVIS is probing more often than this service allows",
        })
    if response.status_code >= 400:
        raise ProbeFailed({
            "state": RegistryState.UNREACHABLE,
            "detail": f"{url.rsplit('/', 1)[-1]} answered HTTP {response.status_code}",
        })
    try:
        body = response.json()
    except ValueError as failure:
        raise ProbeFailed({
            "state": RegistryState.DEGRADED,
            "detail": "answered with something that is not JSON",
        }) from failure
    return body if isinstance(body, Mapping) else {}


def _unreachable(failure: httpx.HTTPError) -> dict[str, Any]:
    """Every transport failure, as one state.

    `stopped` is *not* inferred from a refused connection. §5.1 lists both
    states and they mean different things: `stopped` is a service NERVIS
    supervises and knows it stopped, which needs M16's ownership. Guessing it
    from a connection refusal would report a service somebody else killed as
    though NERVIS had done it deliberately.
    """
    return {
        "state": RegistryState.UNREACHABLE,
        "detail": f"no response: {type(failure).__name__}",
    }


def _state_from(status: str) -> RegistryState:
    """MEP's three self-reported states, as NERVIS's observer-side ones."""
    if status == "healthy":
        return RegistryState.HEALTHY
    if status == "degraded":
        return RegistryState.DEGRADED
    # `unhealthy`, or a status this build does not recognise. Degraded rather
    # than unreachable: NERVIS reached it, and it answered. What it said was bad
    # news about itself, which is information rather than absence.
    return RegistryState.DEGRADED


def _detail_from(health: Mapping[str, Any]) -> str:
    """Which check failed, when one did.

    The failing check's name rather than a count. "1 check failed" sends the
    reader to another screen; "database" ends the question.
    """
    failed = [
        str(check.get("name") or "?")
        for check in health.get("checks") or []
        if isinstance(check, Mapping) and check.get("status") != "pass"
    ]
    if not failed:
        return ""
    return "failing: " + ", ".join(failed)


def _capability_reasons(body: Mapping[str, Any]) -> dict[str, str]:
    """Capability id → why it is not available, for the ones that are not.

    Only the withheld ones: §4.1 makes the reason mandatory exactly there, and
    an `available` capability's reason is the empty string it has to send to
    satisfy the schema. Keeping those would be keeping a column of blanks.
    """
    entries = body.get("capabilities")
    if not isinstance(entries, list):
        return {}
    return {
        wire_identifier(str(entry["id"])): str(entry.get("reason") or "")
        for entry in entries
        if isinstance(entry, Mapping)
        and entry.get("id")
        and str(entry.get("state") or "") != "available"
        and str(entry.get("reason") or "")
    }


def _capabilities(body: Mapping[str, Any]) -> dict[str, str]:
    """Capability id → state, keyed by the wire id.

    §4.1 keeps the `@<major>` shorthand out of the `id` field, so this reads
    what is published rather than reconstructing the prose form — a registry
    that re-derived the shorthand would be inventing part of a name it is
    supposed to be negotiating on.
    """
    entries = body.get("capabilities")
    if not isinstance(entries, list):
        return {}
    return {
        wire_identifier(str(entry["id"])): str(entry.get("state") or "unavailable")
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("id")
    }
