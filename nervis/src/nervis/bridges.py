"""Reading a registered Clarvis Bridge's `/v1/status` (M8b, `CLARVIS.md` §6.3).

The other half of Stage 8, and it was blocked until now for a reason worth
keeping: a reader written against a Bridge that did not exist would have been a
guess about a contract, and the registry row said `unknown` rather than
pretending. The Bridge exists now, so this reads it.

**One instance, one answer.** §6.3 says `/v1/status` describes the extension
host serving it and never aggregates two windows, so this reads exactly one and
the caller asks again for the next. Merging them here would recreate the thing
the specification spends a paragraph forbidding.

**NERVIS presents the token it issued.** Since the Stage 8 amendment to §6.1 the
Bridge requires that token on every read, and NERVIS is the only holder — which
is also what stops a local process that guessed the port from reading an
editor's activity.

**Nothing the Bridge returns is trusted into the response.** Every field is
copied by name with a known type. The Bridge is our own software, but its port
is dynamic and anything on this machine can bind one; §6.1's port-impersonation
argument is exactly this case, and an allowlist is what keeps a fabricated body
from reaching a dashboard as though NERVIS had vouched for it.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

import httpx

from .instances import Instance

logger = logging.getLogger("nervis.bridges")

# Short, and a deadline rather than a retry budget. A Bridge lives inside an
# editor's extension host: it can be busy running an agent, and NERVIS waiting
# on it is a dashboard that stops repainting because somebody started a build.
READ_TIMEOUT_SECONDS = 2.0

# §6.3's interpreted states, and the whole set. A body claiming anything else is
# reported as `unknown` rather than passed through — the dashboard branches on
# these, and an unrecognised string would render as a state nobody can explain.
STATES = frozenset({
    "idle", "chatting", "agent_running", "waiting_for_approval", "stopping", "failed",
})

# The gate categories §6.7 allows NERVIS to display. The question itself is never
# published by the Bridge, so there is nothing here to strip — but the same
# reasoning applies: an unrecognised category is dropped rather than shown.
AWAITING = frozenset({"command", "sensitive_read", "step", "other"})

# What a configuration summary may contain (`CLARVIS.md` §6.2's
# `clarvis.config.summary@1`). The Bridge decides what to publish; this decides
# what NERVIS will repeat, which is the same allowlist argument the status read
# makes: the port is dynamic, anything on this machine can bind one, and a field
# NERVIS has never heard of should not reach a screen wearing NERVIS's
# authority.
CONFIG_FIELDS = frozenset({
    "chat.provider", "chat.model", "chat.mode", "chat.endpoint",
    "agent.provider", "agent.model",
    "voice.enabled", "voice.selected",
    "bridge.enabled", "bridge.nervis", "bridge.enrolment_configured",
    "theme",
})

# One field's worth of characters. A model id is short; the cap is for the case
# where the answer is not from a Bridge at all.
MAX_CONFIG_CHARS = 120


def _int_or_none(value: Any) -> int | None:
    """A whole number, or nothing at all.

    §6.3: *"Unknown values stay unknown. Never invent a duration, step total,
    branch, task or model route."* So a missing, negative or unparseable value
    becomes absence rather than zero — a step count of `0` is a run that has
    taken no steps, which is a different claim from not knowing.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value >= 0 else None


def interpret(body: Mapping[str, Any]) -> dict[str, Any]:
    """One Bridge's status, as the dashboard may render it.

    Built field by field from an allowlist. Absent fields stay absent, so a
    caller can tell "no run is happening" from "this build does not report it".
    """
    state = str(body.get("state") or "")
    reported: dict[str, Any] = {"state": state if state in STATES else "unknown"}

    awaiting = str(body.get("awaiting") or "")
    if awaiting in AWAITING:
        reported["awaiting"] = awaiting

    # Opaque by contract, and truncated because this one is the only free-form
    # string in the payload. A Bridge that is really a Bridge sends sixteen hex
    # characters; anything longer is somebody else's idea of an identifier.
    activity_id = body.get("activity_id")
    if isinstance(activity_id, str) and activity_id:
        reported["activity_id"] = activity_id[:64]

    for name in ("steps_taken", "elapsed_ms"):
        value = _int_or_none(body.get(name))
        if value is not None:
            reported[name] = value

    return reported


def interpret_config(body: Mapping[str, Any]) -> dict[str, Any]:
    """One Bridge's published settings, as NERVIS may repeat them.

    Field by field from `CONFIG_FIELDS`, primitives only, strings clipped. The
    Bridge already refuses to publish a path, a URL or anything from
    SecretStorage — this is the second half of that promise, kept on the side
    that would be doing the repeating.
    """
    found = body.get("config")
    if not isinstance(found, Mapping):
        return {}
    settings: dict[str, Any] = {}
    for name, value in found.items():
        if str(name) not in CONFIG_FIELDS:
            continue
        if isinstance(value, bool):
            settings[str(name)] = value
        elif isinstance(value, str) and value.strip():
            settings[str(name)] = value.strip()[:MAX_CONFIG_CHARS]
    return settings


def interpret_setting_ids(body: Mapping[str, Any]) -> dict[str, str]:
    """Where each published field lives, so an answer can say how to change it.

    Kept because §6.7 forbids NERVIS changing a setting: the most useful thing a
    control plane can do about a setting it may not touch is name it exactly.
    Filtered the same way as the values — a field NERVIS does not publish gets
    no id, and an id belonging to another extension is dropped rather than
    repeated, since "search for this in settings" is an instruction and a wrong
    one sends somebody somewhere else.
    """
    found = body.get("settings")
    if not isinstance(found, Mapping):
        return {}
    ids: dict[str, str] = {}
    for field, setting in found.items():
        if str(field) not in CONFIG_FIELDS or not isinstance(setting, str):
            continue
        clean = setting.strip()[:MAX_CONFIG_CHARS]
        if clean.startswith("clarvis.") or clean == "workbench.colorTheme":
            ids[str(field)] = clean
    return ids


async def read_config(
    client: httpx.AsyncClient, instance: Instance, now: float
) -> dict[str, Any]:
    """Read one live Bridge's configuration summary, or say why not.

    Same shape and same rules as `read_status`, and separate from it because the
    two answer different questions at different rates: what Clarvis is *doing*
    changes by the second, and what it is *configured to do* changes when
    somebody edits a setting. A dashboard poll should not carry both.
    """
    if not instance.is_live(now):
        return {"reachable": False, "detail": "the lease has lapsed; this window is not answering"}
    try:
        response = await client.get(
            instance.base_url + "/v1/config",
            headers={"Authorization": f"Bearer {instance.token}"},
            timeout=READ_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as failure:
        return {"reachable": False, "detail": f"no response: {type(failure).__name__}"}
    if response.status_code == 404:
        # A Bridge that predates the capability. Named rather than reported as a
        # failure: §5.2's "unknown capabilities are unavailable" is about exactly
        # this, and an older window is not a broken one.
        return {
            "reachable": True,
            "detail": "this Clarvis does not publish a configuration summary",
        }
    if response.status_code >= 400:
        return {"reachable": True, "detail": f"the Bridge answered HTTP {response.status_code}"}
    try:
        body = response.json()
    except ValueError:
        return {"reachable": True, "detail": "the Bridge answered with something that is not JSON"}
    if not isinstance(body, Mapping):
        return {
            "reachable": True,
            "detail": "the Bridge answered with something that is not an object",
        }
    return {
        "reachable": True,
        "detail": "",
        "settings": interpret_config(body),
        "setting_ids": interpret_setting_ids(body),
    }


async def read_status(
    client: httpx.AsyncClient, instance: Instance, now: float
) -> dict[str, Any]:
    """Read one live Bridge, or say why not.

    Never raises. Every outcome — not live, refused, unreachable, answered with
    nonsense — comes back as a `reachable` flag and a `detail`, because this is
    called once per row on a dashboard poll and one dead window must not take
    the screen with it.
    """
    if not instance.is_live(now):
        # Not asked at all. A window whose lease lapsed is usually one that was
        # closed, and probing it every poll would be a connection refusal per
        # dead window for as long as the registry remembers it.
        return {
            "reachable": False,
            "state": "unknown",
            "detail": "the lease has lapsed; this window is not answering",
        }

    try:
        response = await client.get(
            instance.base_url + "/v1/status",
            headers={"Authorization": f"Bearer {instance.token}"},
            timeout=READ_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as failure:
        return {
            "reachable": False,
            "state": "unknown",
            "detail": f"no response: {type(failure).__name__}",
        }

    if response.status_code in (401, 403):
        # The Bridge is up and does not accept the token NERVIS holds. Almost
        # always a window that restarted and re-registered while this row was
        # being drawn; occasionally something that is not the Bridge at all.
        return {
            "reachable": True,
            "state": "unknown",
            "detail": "the Bridge refused NERVIS's token",
        }
    if response.status_code >= 400:
        return {
            "reachable": True,
            "state": "unknown",
            "detail": f"the Bridge answered HTTP {response.status_code}",
        }

    try:
        body = response.json()
    except ValueError:
        return {
            "reachable": True,
            "state": "unknown",
            "detail": "the Bridge answered with something that is not JSON",
        }
    if not isinstance(body, Mapping):
        return {
            "reachable": True,
            "state": "unknown",
            "detail": "the Bridge answered with something that is not an object",
        }

    return {"reachable": True, "detail": "", **interpret(body)}
