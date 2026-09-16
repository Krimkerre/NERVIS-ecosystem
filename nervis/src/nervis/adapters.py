"""Reading a service that never agreed to be read (§5.2, §2.1).

**The gap.** LM Studio and code-server are somebody else's programs. They do not
publish the MEP surface the ecosystem's own services do, so the registry could
say one thing about them — *"answering; publishes no MEP surface, so no
capabilities are known"* — which is true, useless, and the same sentence whether
the runtime is holding twenty models or none.

**What an adapter is, and what it is not.** It is a *translation of an
observation*: NERVIS asks the service a question in its own dialect, and
reports what came back in the ecosystem's vocabulary. It is not a claim that the
service publishes MEP, and it never invents the parts that were not observed —
there is no synthesised `service_id`, no guessed version, and no capability that
was not demonstrated by a real answer to a real request.

**Everything an adapter produces is marked `adapted`.** §5.2's rule is that
unknown capabilities are unavailable, and the failure mode it guards against is
a control wired to something nobody promised. A capability NERVIS derived is a
weaker fact than one a service published, and the difference has to survive all
the way to the screen — so it travels as a field rather than as a footnote
somebody may drop.

**A 200 is not an answer.** LM Studio returns HTTP 200 with
`{"error": "Unexpected endpoint or method. (GET /healthz)"}` for every path it
does not serve. A reachability check that reads the status code alone would
report every endpoint as present, including the ones that do not exist — so each
adapter below checks for the *shape it asked for* and treats anything else as
absence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# What NERVIS says about a capability it derived rather than read. Written into
# the reason of every adapted entry, because a reason is where §4.1 puts the
# answer to "why should I believe this".
DERIVED = "derived by NERVIS from the service's own API; not published by it"


def _rows(body: Any, key: str = "data") -> list[Mapping[str, Any]]:
    """The list a body was supposed to carry, or nothing.

    Nothing when the body is an error envelope, a string, or the wrong shape —
    which is the whole guard against LM Studio's 200-with-an-error answer.
    """
    if not isinstance(body, Mapping) or "error" in body:
        return []
    found = body.get(key)
    if not isinstance(found, list):
        return []
    return [row for row in found if isinstance(row, Mapping)]


def lmstudio(catalogue: Any, openai_models: Any) -> dict[str, Any]:
    """LM Studio, in ecosystem terms.

    Two questions, because they establish different things: `/api/v0/models`
    proves the native API is there and says what is loaded, and `/v1/models`
    proves the OpenAI-compatible surface is mounted — which is the one RAVIS
    actually routes through, so its presence is worth reporting separately.

    Neither is a completion. The capability says the surface answered, and its
    reason says exactly that: NERVIS has not put a request through it, and
    reporting "chat works" from a model list would be the kind of claim §5.2
    exists to prevent.
    """
    builds = _rows(catalogue)
    openai = _rows(openai_models)
    capabilities: dict[str, str] = {}
    reasons: dict[str, str] = {}
    if builds:
        capabilities["lmstudio.models.list"] = "available"
        reasons["lmstudio.models.list"] = DERIVED
    if openai:
        capabilities["lmstudio.openai.chat_completions"] = "available"
        reasons["lmstudio.openai.chat_completions"] = (
            "the OpenAI-compatible surface answered a model list; "
            "no completion was attempted — " + DERIVED
        )
    if not capabilities:
        # It answered *something* on the probe path, or this would not have been
        # called — but nothing it answered was the shape asked for.
        return {"detail": "answering, but its model API did not answer in the shape expected"}
    loaded = [
        str(row.get("id") or "")
        for row in builds
        if str(row.get("state") or "") == "loaded"
    ]
    names = ", ".join(name for name in loaded if name)
    detail = f"{len(builds)} local build(s)"
    detail += f", loaded: {names}" if names else ", none loaded"
    return {
        "detail": detail,
        "capabilities": capabilities,
        "capability_reasons": reasons,
        "capability_source": "adapted",
    }


# **The versions Stage 9's matrix actually graded.** One entry, because one
# combination was tested — an allowlist that claimed more would be a promise no
# evidence backs. `GRADED_FLOOR` is the same version read as a tuple: at or above
# it the host is untested rather than known-bad, and below it nothing was ever
# run at all.
GRADED_CODE_SERVER = ("4.135.0",)
GRADED_FLOOR = (4, 135, 0)

# **Versions that passed the upgrade check** (`tools/code_server_upgrade_check.py`,
# since 16 September 2026): code-server's login, proxy, origin, WebSocket and
# webview-host files match the graded install byte for byte, and Clarvis's host
# suite passes on the Code version it bundles. The owner ruled out a full re-grade
# for every upgrade; a pass carries the graded cells over. Written by that tool's
# `--record`, read once. A missing or unreadable file is no checks, never an error.
CHECKED_RECORD = Path(__file__).with_name("code_server_checks.json")


def _checked_code_server() -> dict[str, Any]:
    try:
        found = json.loads(CHECKED_RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


CHECKED_CODE_SERVER = _checked_code_server()


def _as_numbers(version: str) -> tuple[int, ...]:
    """The leading numeric run of a version, for comparison and nothing else.

    `4.135.0` and `4.136.1+abc` both reduce to comparable tuples; anything that
    does not start with a number reduces to `()`, which sorts below every real
    version and is treated as unreadable rather than as old.
    """
    parts: list[int] = []
    for piece in version.split("."):
        digits = ""
        for character in piece:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _workbench_state(version: str) -> tuple[str, str]:
    """Whether NERVIS will embed this code-server, and why.

    Stage 9's exit asks that unsupported combinations be blocked in the UI. Three
    answers rather than two, because "not the version we graded" and "older than
    anything we graded" are different claims and only the second is evidence of
    anything.

    Deliberately *not* a hard allowlist. Blocking every version but `4.135.0`
    would take the editor away the first time somebody upgrades code-server,
    which punishes the user for a gap in NERVIS's testing rather than for a fault
    in theirs.
    """
    if version in GRADED_CODE_SERVER:
        return "available", f"code-server {version} is the version Stage 9's matrix graded"
    checked = CHECKED_CODE_SERVER.get(version)
    if isinstance(checked, dict):
        return (
            "available",
            f"code-server {version} passed the upgrade check on {checked.get('checked_on', '?')}: "
            f"its login, proxy, origin, WebSocket and webview-host files match "
            f"{checked.get('carried_from', GRADED_CODE_SERVER[0])}, which Stage 9's matrix graded, "
            f"and Clarvis {checked.get('clarvis', '?')}'s host suite passes on Code "
            f"{checked.get('code', '?')}; other browsers and audio were not re-checked",
        )
    numbers = _as_numbers(version)
    if not numbers:
        return (
            "degraded",
            "code-server did not publish a version its login page could be read for, so "
            "whether this host was ever graded is unknown; embedded anyway, because an "
            "unreadable version is a gap in NERVIS's reading and not a fault in the host",
        )
    if numbers < GRADED_FLOOR:
        return (
            "unavailable",
            f"code-server {version} is older than {GRADED_CODE_SERVER[0]}, the oldest "
            "version Stage 9's matrix graded; nothing was ever run against it, so NERVIS "
            "refuses to embed it rather than presenting an untested editor as a working one",
        )
    return (
        "degraded",
        f"code-server {version} is newer than {GRADED_CODE_SERVER[0]}, the version Stage 9's "
        "matrix graded; embedded because newer is not evidence of breakage, but nothing here "
        "has been tested against it",
    )


def ollama(version: Any) -> dict[str, Any]:
    """Ollama's own version, from its native `/api/version` — no MEP surface.

    **No capability to derive, and that is an honest answer rather than a
    gap.** Unlike LM Studio's model listing or code-server's workbench grade,
    a bare version number implies nothing about what Ollama can serve —
    reporting one is not the same claim as reporting a capability, so this
    adapter contributes `build_version` alone rather than inventing one to
    satisfy a filter built for services that have both.
    """
    if not isinstance(version, Mapping):
        return {"detail": "answering; publishes no MEP surface, so no capabilities are known"}
    found = str(version.get("version") or "")
    if not found:
        return {"detail": "answering; publishes no MEP surface, so no capabilities are known"}
    return {"detail": f"Ollama {found}", "build_version": found}


def _code_server_version(page: Any) -> str:
    """code-server's own version, from the settings blob it puts in its login page.

    **Read from what it publishes, not from the disk.** `/version` would answer
    properly and wants the password; the install directory carries the number in
    its name and would be a lie the moment NERVIS pointed at a code-server on
    another machine. The login page is unauthenticated and carries
    `codeServerVersion` in a `coder-options` meta tag — its own value, published
    deliberately for its own client.

    Absent when the shape changes, which is the point of matching narrowly: a
    looser pattern would eventually pick a version number out of a stylesheet
    path and report it as the service's.
    """
    if not isinstance(page, str):
        return ""
    found = re.search(r"codeServerVersion&quot;:&quot;([0-9][\w.+-]{0,31})&quot;", page)
    if not found:
        found = re.search(r'"codeServerVersion"\s*:\s*"([0-9][\w.+-]{0,31})"', page)
    return found.group(1) if found else ""


def codeserver(health: Any, manifest: Any, page: Any = None) -> dict[str, Any]:
    """code-server, in ecosystem terms.

    `/healthz` is code-server's own, and it answers a question NERVIS was not
    asking: `status` describes whether a *browser session* is still
    heartbeating, not whether the process is well. Both are reported, in those
    words, because "expired" on a healthy server would otherwise read as a
    fault — it means nobody has the tab open.

    The manifest is what distinguishes code-server from anything else that
    happens to answer on that port. `/version` would say more and needs the
    password, and NERVIS holds no code-server credential — an adapter that
    authenticates is a different and much larger thing than one that reads.
    """
    if not isinstance(health, Mapping) or "status" not in health:
        return {"detail": "answering, but not with code-server's health shape"}
    named = isinstance(manifest, Mapping) and "code-server" in str(manifest.get("name") or "")
    session = str(health.get("status") or "unknown")
    detail = "serving the workbench"
    detail += (
        "; a browser session is connected" if session == "alive"
        else f"; no browser session connected (heartbeat {session})"
    )
    version = _code_server_version(page)
    state, supported = _workbench_state(version)
    capabilities = {"codeserver.workbench": state}
    identified = (
        "identified by its own web manifest and health endpoint; "
        + DERIVED if named else
        "health endpoint answered, but the manifest did not name code-server — "
        + DERIVED
    )
    # Both halves, in this order: whether NERVIS will embed it is what a reader
    # is asking, and how NERVIS knows it is code-server at all is the caveat on
    # the answer rather than the answer.
    reasons = {"codeserver.workbench": f"{supported}. {identified}"}
    adapted = {
        "detail": detail,
        "capabilities": capabilities,
        "capability_reasons": reasons,
        "capability_source": "adapted",
    }
    # Only when it said so. An absent version stays absent: the registry prints
    # a dash, which is the truth, and a placeholder would be a number somebody
    # could compare against a release note.
    return {**adapted, "build_version": version} if version else adapted


# Which extra reads each adapter needs, and what to do with them. The paths live
# here rather than in `probes.py` so that adding a service means adding an entry
# rather than editing a probe.
# `json` bodies are parsed; a `text` body arrives as the string, because
# code-server publishes its version in a page rather than in an API.
ADAPTERS: dict[str, dict[str, Any]] = {
    "lmstudio": {
        "paths": (("/api/v0/models", "json"), ("/v1/models", "json")),
        "translate": lmstudio,
    },
    "codeserver": {
        "paths": (("/healthz", "json"), ("/manifest.json", "json"), ("/login", "text")),
        "translate": codeserver,
    },
    "ollama": {
        "paths": (("/api/version", "json"),),
        "translate": ollama,
    },
}
