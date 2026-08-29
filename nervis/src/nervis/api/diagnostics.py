"""M12 — Analyze: build a packet, show it, and send it through RAVIS (§11.5).

Two endpoints and the split between them is the milestone's exit condition
rather than an API-design preference. §11.5: *"The user sees exactly what will
be sent before it is sent."* A preview that assembled the packet its own way
would be an illustration; both routes call `build_packet` with the same
arguments, so the preview is the thing.

NERVIS is an ordinary RAVIS client here, as §7 requires — this is a chat
completion against a pool, not a private channel. `ravis/local` is what the
**Local analysis only** option means, and it is a pool RAVIS already enforces as
a refusal rather than a preference, so the constraint is RAVIS's to keep.
"""

from __future__ import annotations

from typing import Any

import httpx
from ecosystem_protocol import new_request_id
from fastapi import APIRouter, Request

from nervis.api.chat import _forwarded
from nervis.diagnostics import build_packet, fenced_prompt
from nervis.registry import RegistryEntry
from nervis.traces import assemble

router = APIRouter(prefix="/api/v1/diagnostics", tags=["diagnostics"])

# Which pool an analysis is routed to. The local one is a refusal in RAVIS, not
# a preference: `ravis/local` promises the request never leaves the machine, and
# a promise a picker can tick away is not one.
ANALYSIS_POOL = "ravis/auto"
LOCAL_ANALYSIS_POOL = "ravis/local"

# An analysis is a paragraph, not an essay, and a bounded answer is also a
# bounded amount of anything a crafted log line could have persuaded a model to
# emit.
ANALYSIS_MAX_TOKENS = 700
ANALYSIS_TIMEOUT_SECONDS = 120.0


async def _packet_for(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """The packet for this request, from NERVIS's own hub and registry.

    One function, called by both routes. The preview's promise depends on there
    being no second assembly path, so there is not one.
    """
    hub = request.app.state.hub
    trace_id = str(body.get("trace_id") or "")
    events = list(hub.query(limit=200))
    trace = None
    if trace_id:
        matching = [
            event for event in events
            if str(event.get("trace_id") or "") == trace_id
        ]
        trace = assemble(trace_id, matching).as_dict()
        events = matching
    return build_packet(
        trace=trace,
        # Errors first: a packet bounded at forty events should spend them on
        # the failure rather than on whatever happened to be most recent.
        events=sorted(
            events,
            key=lambda event: 0 if str(event.get("severity")) in ("error", "critical") else 1,
        ),
        services=[entry.as_dict() for entry in request.app.state.registry.all()],
        note=str(body.get("note") or ""),
    )


@router.post("/packet")
async def preview(request: Request) -> dict[str, Any]:
    """Exactly what an analysis would send, without sending it.

    §11.5's exit condition, as a route. Returns the packet *and* the assembled
    prompt, because "what will be sent" is the prompt — a reader shown only the
    JSON would not see the instructions wrapped around it, and the instructions
    are the part that decides how the data is read.
    """
    body = await request.json()
    packet = await _packet_for(request, body if isinstance(body, dict) else {})
    return {"packet": packet, "prompt": fenced_prompt(packet), "sent": False}


@router.post("/analyze")
async def analyze(request: Request) -> dict[str, Any]:
    """Send the packet through RAVIS and return what came back, as text.

    **The result is text and nothing reads it.** §11.5 is explicit that nothing
    the model returns from analysing a packet may become an action — not a
    control call, not a supervision decision, not a gate resolution. There is no
    parser here and no schema; the field is a string and the only thing NERVIS
    does with it is hand it to the caller.

    **A failed analysis alters nothing.** M12's exit says so in those words, and
    it is why this reads the hub and writes nowhere: no event is emitted, no log
    line is written, no trace is annotated. An analysis is a question about the
    record, and a question that edited the record would make the second analysis
    of the same failure a different one.
    """
    body = await request.json()
    if not isinstance(body, dict):
        body = {}
    packet = await _packet_for(request, body)
    prompt = fenced_prompt(packet)

    entry: RegistryEntry | None = request.app.state.registry.get("ravis")
    if entry is None or not entry.is_usable:
        return {
            "available": False,
            "reason": "RAVIS is not reachable, so nothing can analyse this packet",
            "packet": packet,
            "analysis": "",
        }

    settings = request.app.state.settings
    client: httpx.AsyncClient = request.app.state.probe_client
    payload = {
        "model": LOCAL_ANALYSIS_POOL if body.get("local_only") else ANALYSIS_POOL,
        "max_tokens": ANALYSIS_MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        response = await client.post(
            entry.declaration.base_url + "/v1/chat/completions",
            json=payload,
            headers=_forwarded(
                new_request_id(),
                str(getattr(request.state, "trace_id", "")),
                settings.ravis_client_credential,
            ),
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            return _refused(packet, f"RAVIS answered HTTP {response.status_code}")
        answer = response.json()
    except (httpx.HTTPError, ValueError) as failure:
        return _refused(packet, f"the analysis request did not complete: {failure}")

    return {
        "available": True,
        "reason": "",
        "packet": packet,
        # Read defensively rather than trusted: this is a body from another
        # service, and a missing choices list must not become a 500 on a
        # diagnostic route — the one route somebody reaches *because* something
        # is already broken.
        "analysis": _text_of(answer),
        "pool": payload["model"],
    }


def _refused(packet: dict[str, Any], reason: str) -> dict[str, Any]:
    """An analysis that did not happen, said plainly and with the packet intact.

    The packet is returned either way, because it is the half of the answer that
    did not depend on RAVIS — an operator can read it themselves, which is more
    than they had before, and it makes the failure legible rather than empty.
    """
    return {"available": False, "reason": reason, "packet": packet, "analysis": ""}


def _text_of(answer: Any) -> str:
    if not isinstance(answer, dict):
        return ""
    choices = answer.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    return str(message.get("content") or "") if isinstance(message, dict) else ""
