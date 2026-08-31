"""`/api/v1/voice` — entering a key, naming voices, and the gate before speaking (§18.2).

**The gate is the whole reason synthesis is a NERVIS endpoint and not a `fetch`
in the browser.** Cloud TTS is an egress path: the text leaves the machine. §18.2
is explicit that spoken output obeys the same privacy policy as routing, and
that *failing closed on the route and open on the voice is still a leak* — a
reply a local model produced, then read aloud by Fish Audio, has left the
machine by the back door after the front one was locked.

So `POST /voice/speak` refuses unless it can positively confirm the text already
left the machine, by asking RAVIS which models are remote and checking the one
that produced it. Unknown counts as local. The browser is told *why* it was
refused and falls back to the voice built into it, which costs nothing and sends
nothing — §18.2's "fall back to local system TTS" with the fallback where the
speaker already is.

**What the client asserts, and what it cannot.** The browser states which model
produced the text; NERVIS decides what that means. That is a guard against the
design error §18.2 names, not a sandbox — a dashboard on loopback is already
trusted to originate the completion in the first place. What it categorically
cannot do is read the key back: there is no endpoint that returns it.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from nervis import voice
from nervis.ecosystem import advertise_voice
from nervis.errors import InvalidConfigurationError, NotFoundError

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

# How long NERVIS reuses RAVIS's answer about which models are remote. Models
# move between providers rarely, a spoken reply asks this question on every
# turn, and a stale reading is only ever wrong in the direction of refusing —
# a model that became remote is spoken by the browser for up to a minute.
LOCALITY_CACHE_SECONDS = 60.0

# 128 kbps is for music. Speech at 64 halves the transfer with no audible loss
# through a laptop speaker, which is where all of this is heard.
MP3_BITRATE = 64


class CredentialInput(BaseModel):
    """The Fish Audio key arriving from the dashboard.

    **Deliberately unconstrained.** A pydantic validation failure echoes the
    offending input back in its 422 body, so every constraint on this field is a
    path by which a secret reaches a response and a browser's error log. With no
    constraint there is nothing for pydantic to reject, and emptiness is checked
    in the handler, where the message can name the problem without quoting it.
    """

    secret: str


class ProfileInput(BaseModel):
    """One named voice."""

    name: str
    voice_id: str
    engine: str = voice.DEFAULT_ENGINE
    speed: float = 1.0


class SettingsInput(BaseModel):
    """What is in force. Every field optional — the screen sends what changed."""

    enabled: bool | None = None
    muted: bool | None = None
    announce_status: bool | None = None
    selected_profile: str | None = None
    latency: str | None = None
    fallback: str | None = None
    daily_cap: int | None = None
    daily_cap_enabled: bool | None = None
    trim_long_replies: bool | None = None


class SpeakInput(BaseModel):
    """Text to say, and where it came from.

    `source_model` is empty for NERVIS's own words — a voice preview, or a line
    NERVIS wrote itself. Model output must name its model or the gate refuses
    it, because an unattributed reply is exactly the one that might be local.
    """

    text: str
    source_model: str = ""
    profile_id: str = ""


def _credential(request: Request) -> voice.VoiceCredential:
    return request.app.state.voice_credential  # type: ignore[no-any-return]


@router.get("")
async def read_voice(request: Request) -> dict[str, Any]:
    """Everything the Voice screen needs, and nothing that could leak the key."""
    database = request.app.state.database
    credential = _credential(request)
    selected = voice.selected_profile(database)
    return {
        "credential": {
            "configured": credential.configured(),
            "source": credential.source(),
            "file_is_private": credential.file_is_private(),
        },
        "profiles": [profile.as_dict() for profile in voice.profiles(database)],
        "selected_profile": selected.profile_id if selected else "",
        "engines": list(voice.SPEECH_ENGINES),
        "enabled": voice.read_setting(database, voice.ENABLED_SETTING) == "true",
        "muted": voice.read_setting(database, voice.MUTED_SETTING) == "true",
        "announce_status": voice.read_setting(database, voice.ANNOUNCE_SETTING) == "true",
        # What each engine costs you, so the picker can say it rather than
        # listing four version strings nobody can choose between.
        "engine_detail": dict(voice.ENGINE_DETAIL),
        "latency": voice.read_setting(database, voice.LATENCY_SETTING, voice.DEFAULT_LATENCY),
        "latency_modes": list(voice.LATENCY_MODES),
        "latency_detail": dict(voice.LATENCY_DETAIL),
        "daily_cap": voice.daily_cap(database),
        "daily_cap_enabled": voice.cap_enforced(database),
        "fallback": voice.read_setting(database, voice.FALLBACK_SETTING, voice.DEFAULT_FALLBACK),
        "fallback_modes": list(voice.FALLBACK_MODES),
        # Reported next to the cap, because a limit with no reading against it
        # is a number nobody can act on.
        "spent_today": voice.spent_today(database),
        "trim_long_replies": voice.read_setting(database, voice.TRIM_SETTING, "true") == "true",
    }


@router.put("/credential")
async def set_credential(body: CredentialInput, request: Request) -> dict[str, Any]:
    """Store the Fish Audio key. Write-only: nothing reads it back out."""
    if not body.secret.strip():
        raise InvalidConfigurationError("the key must not be empty")
    credential = _credential(request)
    credential.store(body.secret.strip())
    # §18.2's "advertised only when configured", kept true without a restart.
    advertise_voice(request.app.state.ecosystem, True)
    return {"configured": True, "source": credential.source()}


@router.delete("/credential")
async def forget_credential(request: Request) -> dict[str, Any]:
    """Remove NERVIS's own copy.

    A key supplied through the environment survives, and the status still reads
    configured. That is the truth rather than a failed delete.
    """
    credential = _credential(request)
    credential.forget()
    # Still configured when the environment supplies one, so the advertisement
    # follows the *reading* rather than the delete.
    advertise_voice(request.app.state.ecosystem, credential.configured())
    return {"configured": credential.configured(), "source": credential.source()}


@router.get("/catalogue")
async def read_catalogue(request: Request) -> dict[str, Any]:
    """The user's own Fish Audio voices — and the key test, for free.

    This is an authenticated read that spends no synthesis quota, so it answers
    "is this key good?" without costing anything. Rendering a test phrase would
    also answer it and would bill for the privilege.

    **Note the two endpoints do not share a path shape.** Synthesis is at
    `/v1/tts`; the model list is at `/model`, with no `/v1`. Copying the prefix
    across is a 404 that reads like an empty account.
    """
    key = _credential(request).reveal()
    if not key:
        raise InvalidConfigurationError("no Fish Audio key is configured")
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        response = await client.get(
            voice.FISH_MODELS_URL,
            params={"self": "true"},
            headers={"Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
    except httpx.HTTPError as failure:
        raise InvalidConfigurationError(
            f"Fish Audio could not be reached: {type(failure).__name__}"
        ) from failure
    if response.status_code == 401:
        raise InvalidConfigurationError("Fish Audio rejected this key")
    if response.status_code >= 400:
        raise InvalidConfigurationError(
            f"Fish Audio answered {response.status_code}: {_detail_of(response)}"
        )
    return {"items": _voices_of(response.json()), "key_accepted": True}


def _voices_of(payload: Any) -> list[dict[str, str]]:
    """Ids and titles from Fish's listing.

    **The id arrives as `_id`, not `id`.** Reading `id` yields a list of blank
    rows rather than an error, which is the kind of bug that survives review.
    """
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    found = []
    for item in items:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("_id") or item.get("id") or "")
        if identifier:
            found.append({"voice_id": identifier, "title": str(item.get("title") or "untitled")})
    return found


@router.post("/profiles")
async def add_profile(body: ProfileInput, request: Request) -> dict[str, Any]:
    """Add a voice, under an id NERVIS generates.

    **Separate from PUT, and that separation is a bug fix.** Creating used to go
    through `PUT /profiles/new`, and `new` is a perfectly good id — so every
    voice anybody added was stored under it and replaced the one before. The
    symptom was a list that refused to grow past one entry, which reads as a
    save that failed rather than as a save that overwrote.
    """
    return _store_profile(voice.new_profile_id(), body, request)


@router.put("/profiles/{profile_id}")
async def save_profile(profile_id: str, body: ProfileInput, request: Request) -> dict[str, Any]:
    """Replace one voice by id. Idempotent, hence PUT."""
    return _store_profile(profile_id, body, request)


def _store_profile(profile_id: str, body: ProfileInput, request: Request) -> dict[str, Any]:
    if not body.name.strip():
        raise InvalidConfigurationError("a voice needs a name")
    reference = voice.voice_id_from(body.voice_id)
    if not reference:
        # Fish's own wording, because it is the rule and it is precise.
        raise InvalidConfigurationError(
            "that is not a Fish voice id: it must be 1-128 characters of "
            "A-Z, a-z, 0-9, underscore or hyphen. Pasting the fish.audio "
            "address of a voice works too."
        )
    engine = body.engine if body.engine in voice.SPEECH_ENGINES else voice.DEFAULT_ENGINE
    saved = voice.save_profile(
        request.app.state.database,
        voice.VoiceProfile(
            profile_id=profile_id,
            name=body.name.strip(),
            voice_id=reference,
            engine=engine,
            # Fish accepts 0.5-2.0. Clamped rather than refused: a slider that
            # rejects its own extreme is a worse control than one that stops.
            speed=min(2.0, max(0.5, body.speed)),
        ),
    )
    return saved.as_dict()


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: str, request: Request) -> dict[str, Any]:
    if not voice.delete_profile(request.app.state.database, profile_id):
        raise NotFoundError(f"no voice profile {profile_id!r}")
    return {"profile_id": profile_id, "deleted": True}


def _flag(value: Any) -> str:
    return "true" if value else "false"


def _one_of(allowed: tuple[str, ...]) -> Any:
    """An encoder that drops anything outside the set.

    Silently, and deliberately: an unrecognised latency or fallback is a client
    sending a value this build does not have, and the safe answer is to keep the
    one that works rather than store a mode nothing implements.
    """

    def encode(value: Any) -> str | None:
        return str(value) if value in allowed else None

    return encode


def _clamped(value: Any) -> str:
    # Not clamped upward: somebody who types a large number means it. Zero is
    # meaningful — never call Fish, the browser reads everything.
    return str(max(0, int(value)))


# Every writable setting, as a table rather than a branch each.
#
# It was a branch each, which cost a point of complexity per field and crossed
# the gate on the eighth — the gate pointing at a function that had become a
# list written in control flow. `None` means the caller did not send the field,
# which is distinct from sending `false`.
_WRITABLE: tuple[tuple[str, str, Any], ...] = (
    ("enabled", voice.ENABLED_SETTING, _flag),
    ("muted", voice.MUTED_SETTING, _flag),
    ("announce_status", voice.ANNOUNCE_SETTING, _flag),
    ("selected_profile", voice.SELECTED_SETTING, str),
    ("latency", voice.LATENCY_SETTING, _one_of(voice.LATENCY_MODES)),
    ("fallback", voice.FALLBACK_SETTING, _one_of(voice.FALLBACK_MODES)),
    ("daily_cap", voice.DAILY_CAP_SETTING, _clamped),
    ("daily_cap_enabled", voice.DAILY_CAP_ENABLED_SETTING, _flag),
    ("trim_long_replies", voice.TRIM_SETTING, _flag),
)


@router.put("/settings")
async def write_settings(body: SettingsInput, request: Request) -> dict[str, Any]:
    """Enable, mute, choose the voice, or change how it behaves.

    Kept server-side rather than in the browser because §18.2 requires mute to
    *persist* — a mute a second tab does not honour is not a mute — and the rest
    followed it for the same reason.
    """
    database = request.app.state.database
    for field, key, encode in _WRITABLE:
        value = getattr(body, field)
        if value is None:
            continue
        encoded = encode(value)
        if encoded is not None:
            voice.write_setting(database, key, encoded)
    return await read_voice(request)


@router.post("/speak")
async def speak(body: SpeakInput, request: Request) -> Response:
    """Synthesize one line, if §18.2 allows this text to leave the machine."""
    database = request.app.state.database
    spoken = voice.spoken_form(body.text, _trims(database))
    # What the browser is allowed to say instead. Empty means *say nothing* —
    # the same shape a mute already uses, so silence needs no second mechanism.
    instead = spoken if voice.speaks_the_fallback(database) else ""
    blocked = await _blocked(request, database, body, spoken, instead)
    if blocked is not None:
        return blocked
    profile = _profile_for(database, body.profile_id)
    if profile is None:
        return _declined("no_voice", "no voice has been chosen", instead)
    key = _credential(request).reveal()
    if not key:
        return _declined("no_credential", "no Fish Audio key is configured", instead)
    return await _synthesize(request, database, spoken, profile, key, instead)


def _trims(database: Any) -> bool:
    """Whether a long reply is announced rather than read.

    On by default. Nothing is hidden either way — the transcript always holds
    the whole reply — so this only decides whether the *voice* attempts it.
    """
    return voice.read_setting(database, voice.TRIM_SETTING, "true") == "true"


async def _blocked(
    request: Request, database: Any, body: SpeakInput, spoken: str, instead: str
) -> Response | None:
    """Every reason this line must not reach a cloud voice, in order.

    Extracted so `speak` reads as its happy path. The order is not arbitrary:
    mute comes first because it is the one the user just pressed, and the
    privacy gate comes before the spend guard because a refusal to *leak* should
    never be reported as a refusal to *spend*.
    """
    if voice.read_setting(database, voice.MUTED_SETTING) == "true":
        return _declined("muted", "voice is muted")
    if not spoken:
        # Nothing worth hearing survived the strip — a reply that was only a
        # code block, for instance. Not an error, and not silence to explain.
        return _declined("nothing_to_say", "nothing in this reply is speakable")
    gate = await _egress_permitted(request, body.source_model)
    if gate is not None:
        return _declined("local_only", gate, instead)
    if voice.cap_enforced(database):
        cap = voice.daily_cap(database)
        if not voice.within_cap(voice.spent_today(database), cap):
            return _declined(
                "daily_cap",
                f"{cap} Fish Audio requests today is the cap; the browser reads the rest",
                instead,
            )
    return None


def _declined(reason: str, detail: str, spoken: str = "") -> Response:
    """A refusal the browser can act on rather than a failure it must report.

    `409` rather than `403`: the request is well-formed and the caller is
    allowed — the machine's current state is what makes it wrong. The browser
    reads `reason` and falls back to the voice built into it, so the user hears
    the line either way and the screen can say which voice said it.

    **`spoken` carries the stripped text back** when a fallback should say it.
    Not a leak — the browser sent this text in the first place — and it means
    the two voices say the same words: `speakable` runs once, here, rather than
    being re-implemented in JavaScript where it would drift. Refusals where
    nothing should be said at all send no text, which is how the browser tells
    "say this yourself" from "say nothing".
    """
    body = {"spoken": False, "reason": reason, "detail": detail}
    if spoken:
        body["fallback_text"] = spoken
    return JSONResponse(body, status_code=409)


def _profile_for(database: Any, profile_id: str) -> voice.VoiceProfile | None:
    if not profile_id:
        return voice.selected_profile(database)
    for profile in voice.profiles(database):
        if profile.profile_id == profile_id:
            return profile
    return None


async def _egress_permitted(request: Request, model: str) -> str | None:
    """None when this text may go to a cloud voice, otherwise why it may not.

    Empty `model` means NERVIS wrote the line itself — a preview or its own
    words — which was never anyone's private data and is allowed. Anything
    attributed to a model must be *confirmed remote*: the reply already left
    the machine, so reading it aloud sends nothing that had stayed home.
    """
    if not model:
        return None
    placement = await _placement_of(request)
    if placement is None:
        return "RAVIS could not be asked where models run, so this stayed on the machine"
    where = placement.get(model)
    if where is False:
        return None
    # **Three answers, not two.** "RAVIS says this ran here" and "RAVIS does not
    # say" both end in the browser's voice, and saying so the same way would put
    # a claim in the second one's mouth — §9.4's rule about never presenting a
    # guess as a measurement, at the grain of a refusal message. Somebody
    # reading the second sentence should go and look at why RAVIS has no answer,
    # not conclude their cloud model is secretly local.
    if where is True:
        return f"{model} answered on this machine, so its reply is not sent to a cloud voice"
    return f"RAVIS does not report where {model} runs, so it is treated as local"


async def _placement_of(request: Request) -> dict[str, bool | None] | None:
    """Where RAVIS says each model runs. `None` when RAVIS could not be asked.

    Cached briefly: asked once per spoken reply, and the answer changes about as
    often as a provider is added.
    """
    cache = getattr(request.app.state, "voice_locality", None)
    now = time.monotonic()
    if cache and now < cache[0]:
        return cache[1]  # type: ignore[no-any-return]
    found = await _read_placement(request)
    request.app.state.voice_locality = (now + LOCALITY_CACHE_SECONDS, found)
    return found


async def _read_placement(request: Request) -> dict[str, bool | None] | None:
    """Ask RAVIS. Any failure is `None`, which the gate treats as local.

    The per-model value is kept tri-state exactly as RAVIS reports it. Collapsing
    it to a set of remote ids here would throw away the difference between "RAVIS
    says this runs locally" and "RAVIS has no entry for it" — and those deserve
    different sentences, even though they reach the same outcome.
    """
    entry = request.app.state.registry.get("ravis")
    if entry is None:
        return None
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        response = await client.get(
            entry.declaration.base_url + "/api/v1/models", timeout=5.0
        )
        if response.status_code >= 400:
            return None
        items = response.json().get("items") or []
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    placement: dict[str, bool | None] = {}
    for item in items:
        found = item.get("local")
        # An older RAVIS has no such field at all, which is *unknown* and not
        # remote. Reading a missing key as False is how this gate would open on
        # every model against a service that predates it.
        placement[str(item.get("model_id"))] = found if isinstance(found, bool) else None
    return placement


def _detail_of(response: httpx.Response) -> str:
    """What Fish said was wrong with the request.

    **The status alone is not actionable.** A bare "Fish Audio answered 400"
    tells nobody whether the voice id is malformed, the engine header is
    unknown, or a field is out of range — and the body says exactly which. It
    describes the *request*, never the credential: the key travels in a header
    that is not echoed back, so there is nothing here to redact.

    Truncated because an upstream error body is not a log format, and a long one
    would push the useful first sentence off a dashboard row.
    """
    try:
        body = response.text.strip()
    except (UnicodeDecodeError, httpx.HTTPError):
        return "no readable detail"
    return (body[:300] or "no detail given").replace("\n", " ")


async def _synthesize(
    request: Request,
    database: Any,
    text: str,
    profile: voice.VoiceProfile,
    key: str,
    instead: str = "",
) -> Response:
    """The Fish Audio call.

    **The engine is an HTTP header, not a body field.** Fish falls back to the
    account default for an unrecognised header rather than rejecting it, so
    getting this backwards produces the wrong voice and no error at all.
    """
    client: httpx.AsyncClient = request.app.state.probe_client
    try:
        response = await client.post(
            voice.FISH_TTS_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "model": profile.engine,
            },
            json={
                "text": text,
                "reference_id": profile.voice_id,
                "format": "mp3",
                "mp3_bitrate": MP3_BITRATE,
                "latency": voice.read_setting(
                    database, voice.LATENCY_SETTING, voice.DEFAULT_LATENCY
                ),
                "prosody": {"speed": profile.speed},
            },
            timeout=voice.SPEECH_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as failure:
        return _declined(
            "unreachable", f"Fish Audio did not answer: {type(failure).__name__}", instead
        )
    if response.status_code >= 400:
        return _declined(
            "refused",
            f"Fish Audio answered {response.status_code}: {_detail_of(response)}",
            instead,
        )
    # Counted only once Fish actually answered. Charging the cap for a refused
    # or unreachable request would let an outage exhaust the day's budget.
    voice.note_request(database)
    return Response(
        content=response.content,
        media_type="audio/mpeg",
        # The voice that actually spoke, so the screen can say so rather than
        # assume. A browser fallback and a cloud voice must be tellable apart.
        headers={"x-voice-profile": profile.profile_id, "cache-control": "no-store"},
    )
