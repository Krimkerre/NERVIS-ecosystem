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

import re
from collections.abc import Mapping
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
    capabilities = {"codeserver.workbench": "available"}
    reasons = {
        "codeserver.workbench": (
            "identified by its own web manifest and health endpoint; "
            + DERIVED if named else
            "health endpoint answered, but the manifest did not name code-server — "
            + DERIVED
        )
    }
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
}
