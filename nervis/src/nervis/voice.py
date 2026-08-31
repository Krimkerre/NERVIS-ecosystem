"""Spoken output: the credential, the named voices, and the gate that guards them (§18.2).

**Why this lives in NERVIS and not in RAVIS.** RAVIS owns provider credentials
for everything it routes to, so a Fish Audio key looks at first like one more
row on that screen. It is not, and §18.2 says so outright: *voice credentials
live in NERVIS's own secure storage*. RAVIS.md §5.0.1 says the same thing from
the other side, reserving `tts`, `audio` and `whisper` as substrings RAVIS pool
ids must avoid — the authors had already decided speech is not RAVIS's to route.
The deciding argument is the gate below: whether a sentence may leave the
machine depends on how *this* reply was produced, which is a fact NERVIS holds
and RAVIS would have to be told.

**The gate is the reason this module is not simply an HTTP call.** Cloud TTS is
an egress path. A reply that a privacy constraint kept on-device, then read
aloud by a third party, has leaked — §18.2 puts it plainly: *failing closed on
the route and open on the voice is still a leak*. So synthesis is permitted only
for text NERVIS can positively confirm already left the machine, and everything
else falls back to the browser's own voice. Unknown is treated as local, because
the failure that matters is the silent one.

**The key is write-only and lives in a file, not the database.** `nervis.db` is
created in the working directory with whatever mode the umask allows, and a
secret sitting in a world-readable file next to the source is the failure this
whole module exists to avoid. A `0600` JSON file in the user's config directory
is what `~/.aws/credentials`, `gh` and npm all do, and it is the same shape
RAVIS's own M10 store settled on. Deliberately *not* imported from RAVIS: a
service that imports another service's internals is no longer independently
deployable, which is the one rule the ecosystem is built on. The duplication is
about sixty lines and is stated here so a reader knows it was a decision.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nervis.storage import Database

# Fish Audio's TTS models, sent as the `model` **header** — not a body field.
# That is genuinely easy to get backwards and fails as a silently wrong voice
# rather than an error, because an unrecognised value falls back to the default
# instead of being rejected.
SPEECH_ENGINES = ("s2.1-pro-free", "s2.1-pro", "s2-pro", "s1")

# Their current best model on a free developer tier. A default that costs
# nothing is the right one for a feature that is off until somebody asks for it.
DEFAULT_ENGINE = "s2.1-pro-free"

# What each engine costs you, rather than what it is called. "s1 versus
# s1-mini" tells a reader nothing they can choose on; "flatter but faster"
# does. Only the four Fish currently documents are offered: an unrecognised
# `model` header is answered with the *account default* instead of an error, so
# a stale id in this list would surface as the wrong voice and no message at
# all — which is why the sibling project's two older ids are not carried over.
ENGINE_DETAIL = {
    "s2.1-pro-free": "their current best, on a free developer tier",
    "s2.1-pro": "the same model without the free tier's queue or rate limit",
    "s2-pro": "the previous generation — steadier over long text, less expressive",
    "s1": "the original: quickest and cheapest, and audibly flatter",
}

# How long Fish may take before the first byte. Their default is `normal`, and
# it was measured in the sibling project at 1.5-3 s of dead air — long enough
# that a one-line remark lands after the moment it was about.
LATENCY_MODES = ("balanced", "normal", "low")
LATENCY_DETAIL = {
    "balanced": "low latency, no audible quality cost on conversational lines",
    "normal": "Fish's quality-first mode — 1.5-3 s before the first word",
    "low": "fastest, and trades voice stability for it",
}
DEFAULT_LATENCY = "balanced"

# Requests per day before NERVIS stops calling Fish and lets the browser speak.
#
# **Off by default**, which is a reversal. The guard was written on the sibling
# project's reasoning — a dashboard that greets you every time you open a tab is
# the shape of thing that runs up a bill while nobody is watching — and that is
# still true. What it missed is the *cost of reaching it*: the fallback is the
# browser's own voice, and dropping mid-conversation from a chosen voice to that
# one is a worse experience than the bill it avoids. A spend guard whose failure
# mode is "everything suddenly sounds wrong" gets switched off in irritation
# rather than tuned, so it is off until asked for.
#
# The counter still runs either way, so the reading is there to look at before
# deciding to enforce it.
DEFAULT_DAILY_CAP = 200

# Where a synthesis request goes, and where the user's own saved voices are read
# from. The second doubles as the credential test: it is authenticated, cheap,
# and returns 401 on a bad key without spending any synthesis quota.
FISH_TTS_URL = "https://api.fish.audio/v1/tts"
FISH_MODELS_URL = "https://api.fish.audio/model"

# Longer than a read and shorter than a completion. Past this a plainer voice
# arriving now beats a better one arriving after the moment it was about.
SPEECH_TIMEOUT_SECONDS = 30.0

# How much of a reply is spoken. Roughly two minutes at a normal speaking rate.
# A spoken reply that runs for six minutes is not a feature, and the panel shows
# the whole thing regardless — nothing is hidden, only unspoken.
MAX_SPOKEN_CHARACTERS = 1200

# Past this, the voice says *that* the answer is long instead of reading it.
#
# About 110 words, or three quarters of a minute aloud — already past the point
# where listening is slower than reading. Announcing beats truncating: a reply
# cut at two minutes reads a fragment and stops, which sounds like a fault and
# leaves the listener not knowing whether they missed the answer.
WALL_OF_TEXT_CHARACTERS = 700

# Varied, because one fixed line becomes wallpaper by the third time. Chosen by
# length rather than at random so the same reply always announces the same way —
# a voice that says something different on a re-read sounds like a different
# answer.
WALL_OF_TEXT_LINES = (
    "Wall of text incoming. It is on screen.",
    "That one is far too long to read out. Have a look at the screen.",
    "Several paragraphs. I will let you read that yourself.",
)

# Settings kept in the database rather than the browser, because §18.2 requires
# mute to *persist* — a mute that a second tab does not honour is not a mute.
ENABLED_SETTING = "voice.enabled"
MUTED_SETTING = "voice.muted"
SELECTED_SETTING = "voice.selected_profile"
LATENCY_SETTING = "voice.latency"
DAILY_CAP_SETTING = "voice.daily_cap"
DAILY_CAP_ENABLED_SETTING = "voice.daily_cap_enabled"

# Whether NERVIS says a service's state out loud when it changes.
#
# **Its own flag rather than a use of `enabled`.** Wanting replies read aloud and
# wanting to be told a service fell over are different wants: somebody reading
# quietly still wants to hear that RAVIS stopped answering, and somebody who
# likes the voice in chat may not want the machine talking at them while they
# work in another tab. Off by default, because a dashboard that starts speaking
# unprompted the first time it is opened is a surprise, and the status bar
# already carries the same fact silently.
ANNOUNCE_SETTING = "voice.announce_status"

# What happens when the chosen voice cannot be used.
#
# `browser` — the default — hands the line to the browser's own `speechSynthesis`,
# which is §18.2's "fall back to local system TTS": free, offline, and it sends
# nothing, which is why it is the right refusal for a local model's reply.
#
# `silence` is the other half of the same sentence — *"or stay silent and say
# so"* — and exists because that fallback voice is not everyone's idea of an
# improvement on nothing. Dropping from a chosen voice to a flat one can be
# worse than not hearing the line at all, and the text is on screen either way.
FALLBACK_SETTING = "voice.fallback"
FALLBACK_MODES = ("browser", "silence")
DEFAULT_FALLBACK = "browser"
TRIM_SETTING = "voice.trim_long_replies"

# Read when no key has been entered through the dashboard, so an existing
# deployment can supply one the way it supplies every other secret.
KEY_ENVIRONMENT_VARIABLE = "NERVIS_FISH_AUDIO_KEY"


def config_directory(environment: dict[str, str] | None = None) -> Path:
    """Where NERVIS keeps the one file it writes outside its database.

    Resolved from the environment rather than from `Path.home()` so a test never
    touches a real home directory — the same reason RAVIS's store takes the same
    argument.
    """
    env = environment if environment is not None else dict(os.environ)
    if sys.platform == "win32":
        base = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "nervis"


@dataclass(frozen=True)
class VoiceProfile:
    """One named voice: what to call it, and which Fish voice it selects.

    `voice_id` is Fish's `reference_id`. The name exists because a reference id
    is thirty-two hex characters and tells nobody anything about how it sounds,
    which makes a list of them unusable for the one thing a list of voices is
    for.
    """

    profile_id: str
    name: str
    voice_id: str
    engine: str = DEFAULT_ENGINE
    # Fish's `prosody.speed`, 0.5–2.0. Held per voice rather than globally
    # because the right speed is a property of the voice, not of the listener.
    speed: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "voice_id": self.voice_id,
            "engine": self.engine,
            "speed": self.speed,
        }


class VoiceCredential:
    """The Fish Audio key: enterable, never readable back over HTTP.

    Same guarantee RAVIS's credential store makes and for the same reason — the
    only endpoint that reports on this returns whether a key exists and where it
    came from, never the value. There is no read endpoint to leak it from.
    """

    def __init__(self, path: Path, environment: dict[str, str] | None = None) -> None:
        self.path = path
        self._environment = environment if environment is not None else dict(os.environ)

    def _read_file(self) -> str:
        """The stored key, or empty when there is not one.

        A malformed or unreadable file yields nothing rather than raising. The
        environment is tried next and an unusable file is reported as *absent*,
        which fails closed, instead of taking the service down over a file the
        operator can repair.
        """
        try:
            with self.path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return ""
        if not isinstance(payload, dict):
            return ""
        value = payload.get("fish_audio")
        return value if isinstance(value, str) else ""

    def reveal(self) -> str:
        """The actual key. Named to be conspicuous, so `grep reveal(` is the audit."""
        return self._read_file() or self._environment.get(KEY_ENVIRONMENT_VARIABLE, "")

    def source(self) -> str:
        """Where the key came from. Safe to print — it names no value."""
        if self._read_file():
            return "file"
        if self._environment.get(KEY_ENVIRONMENT_VARIABLE):
            return "environment"
        return "absent"

    def configured(self) -> bool:
        return bool(self.reveal())

    def store(self, secret: str) -> None:
        """Replace the file, atomically, never leaving it world-readable.

        `os.open` with an explicit mode creates the temporary file already
        private: writing and then chmod-ing leaves a window in which the key
        exists at the umask's discretion.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"fish_audio": secret}, handle, indent=2)
                handle.write("\n")
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        os.replace(temporary, self.path)

    def forget(self) -> None:
        """Remove NERVIS's own copy.

        A key supplied through the environment survives this, and the status
        will still say `configured`. That is the truth rather than a failed
        delete: NERVIS does not remove what it did not put there.
        """
        self.path.unlink(missing_ok=True)

    def file_is_private(self) -> bool:
        """False when anyone but the owner can read it.

        Reported rather than enforced. Refusing to read would be the stronger
        stance and is what ssh does, but it locks an operator out of their own
        service over a permission bit they can fix in one command.
        """
        try:
            mode = self.path.stat().st_mode
        except OSError:
            return True
        return not bool(mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH))


# ── Profiles, in the database because they are configuration, not secrets ──


def profiles(database: Database) -> list[VoiceProfile]:
    rows = database.connection.execute(
        "SELECT profile_id, name, voice_id, engine, speed FROM voice_profile"
        " ORDER BY name COLLATE NOCASE"
    )
    return [VoiceProfile(**dict(row)) for row in rows]


def save_profile(database: Database, profile: VoiceProfile) -> VoiceProfile:
    """Add a voice or replace one, by id. Idempotent, which is why the API uses PUT."""
    with database.connection as connection:
        connection.execute(
            """
            INSERT INTO voice_profile (profile_id, name, voice_id, engine, speed)
                 VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE
                    SET name = excluded.name, voice_id = excluded.voice_id,
                        engine = excluded.engine, speed = excluded.speed
            """,
            (profile.profile_id, profile.name, profile.voice_id, profile.engine, profile.speed),
        )
    return profile


def delete_profile(database: Database, profile_id: str) -> bool:
    with database.connection as connection:
        deleted = connection.execute(
            "DELETE FROM voice_profile WHERE profile_id = ?", (profile_id,)
        ).rowcount
    # Selecting a voice that no longer exists would leave the screen naming a
    # profile it cannot show, so the selection is cleared with it.
    if deleted and read_setting(database, SELECTED_SETTING) == profile_id:
        write_setting(database, SELECTED_SETTING, "")
    return bool(deleted)


# Fish's own rule, quoted from the 400 it returns: `reference_id must be
# 1..=128 chars of [A-Za-z0-9_-]`. Checked here so a bad id fails when it is
# entered, next to the field that is wrong, rather than days later as a refusal
# in the middle of a conversation.
_REFERENCE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def voice_id_from(raw: str) -> str:
    """The reference id inside whatever was pasted, or empty if there is none.

    **A pasted URL is the expected input, not a mistake.** The help text says a
    voice id is the last segment of a fish.audio URL, so the address bar is
    exactly where somebody gets one — and it comes with a trailing slash, which
    Fish rejects with a 400 that names the character class and not the offending
    character. Taking the last path segment costs one line and removes the whole
    class of report.
    """
    candidate = raw.strip().split("?")[0].split("#")[0].rstrip("/")
    candidate = candidate.rsplit("/", 1)[-1]
    return candidate if _REFERENCE_ID.fullmatch(candidate) else ""


def new_profile_id() -> str:
    return "vp_" + uuid.uuid4().hex[:10]


# ── Settings ──


def read_setting(database: Database, key: str, default: str = "") -> str:
    row = database.connection.execute(
        "SELECT value FROM setting WHERE key = ?", (key,)
    ).fetchone()
    return str(row["value"]) if row else default


def write_setting(database: Database, key: str, value: str) -> None:
    with database.connection as connection:
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def selected_profile(database: Database) -> VoiceProfile | None:
    """The voice in force, or none.

    Falls back to the only profile when exactly one exists and nothing is
    selected: a user who has entered one voice has expressed a preference, and
    making them then pick it from a list of one is a step that exists only
    because the data model has a nullable column.
    """
    chosen = read_setting(database, SELECTED_SETTING)
    available = profiles(database)
    for profile in available:
        if profile.profile_id == chosen:
            return profile
    return available[0] if len(available) == 1 else None


# ── What actually gets spoken ──

# Whole `*...*` spans, not just the punctuation. A model told not to write stage
# directions writes them anyway — and every TTS engine reads a bare `*` aloud as
# the word "asterisk", so the failure is audible and absurd rather than subtle.
_STAGE_DIRECTION = re.compile(r"\*[^*]*\*")
# **Reasoning models emit their thinking inline.** DeepSeek-R1 through LM Studio
# puts a `<think>` block in `content` rather than in `reasoning_content`, so the
# separation the streaming path relies on does not happen and the whole chain of
# thought arrives as ordinary text. Read aloud, that is several minutes of a
# model talking itself through the problem before it answers — the single worst
# thing this feature could do. Seen live on the first greeting ever generated.
#
# The unclosed form is deliberate: a reply cut off mid-thought has an opening
# tag and no closing one, and matching only balanced pairs would read the entire
# truncated monologue.
_REASONING = re.compile(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|$)", re.DOTALL | re.IGNORECASE)
_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")
# Markdown links: the label is worth speaking, the URL never is.
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADING_OR_QUOTE = re.compile(r"^\s{0,3}[#>]+\s*", re.MULTILINE)
_BULLET = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
# Emoji and the other pictographs. Both personas already say not to write
# anything you would not say out loud, and a model put a 😄 on the end of a
# sentence anyway — an instruction is a request and this is the enforcement.
#
# What a voice does with one is undefined and provider-specific: silence, a
# pause, or the character's name read out. None of those is what the sentence
# meant, and the transcript keeps the emoji either way.
_PICTOGRAPHS = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\uFE0F\u2B00-\u2BFF]"
)
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r" *\n[\n ]*")


def speakable(text: str, limit: int = MAX_SPOKEN_CHARACTERS) -> str:
    """A reply, reduced to what is worth hearing.

    Everything removed here is something that reads badly aloud rather than
    something the user should not see — the transcript on screen is untouched.
    Code is dropped outright: a spoken code fence is a minute of punctuation.
    """
    spoken = _REASONING.sub(" ", text)
    spoken = _FENCED_CODE.sub(" ", spoken)
    spoken = _INLINE_CODE.sub(" ", spoken)
    spoken = _LINK.sub(r"\1", spoken)
    spoken = _STAGE_DIRECTION.sub(" ", spoken)
    # Any unpaired asterisk the span rule left behind, plus markdown emphasis.
    spoken = spoken.replace("*", "").replace("_", " ")
    spoken = _PICTOGRAPHS.sub("", spoken)
    spoken = _HEADING_OR_QUOTE.sub("", spoken)
    spoken = _BULLET.sub("", spoken)
    spoken = _BLANK_LINES.sub("\n", _WHITESPACE.sub(" ", spoken)).strip()
    return _truncate(spoken, limit)


def _truncate(text: str, limit: int) -> str:
    """Cut at a sentence boundary if there is one within reach, never mid-word.

    A reply cut mid-sentence sounds like a fault; one cut at a full stop sounds
    like it finished. The fallback is a word boundary, which is still better
    than the middle of a word.
    """
    if len(text) <= limit:
        return text
    window = text[:limit]
    for boundary in (". ", "! ", "? ", ".\n"):
        cut = window.rfind(boundary)
        if cut > limit // 2:
            return window[: cut + 1]
    cut = window.rfind(" ")
    return (window[:cut] if cut > 0 else window).rstrip() + "…"


# ── The spend guard ─────────────────────────────────────────────────────────


def cap_key(today: str) -> str:
    """One counter per day, keyed by the date itself.

    Day-keyed rather than a stored reset timestamp, so rollover needs no timer
    and no cleanup: yesterday's key simply stops being read.

    **Local date, not UTC.** The sibling project used UTC and documented the
    result as a wart — in CEST the counter rolls at 02:00 and a request at 01:00
    counts against the previous day. Nobody thinks in UTC about how much they
    have spoken today, and this is a personal dashboard on one machine.
    """
    return f"voice.requests.{today}"


def today() -> str:
    """This machine's date. Injected nowhere, because there is nothing to fake:
    a test that needs a boundary can write the counter for a chosen key."""
    from datetime import date

    return date.today().isoformat()


def within_cap(used: int, cap: int) -> bool:
    """Whether one more cloud request is allowed.

    **Fails toward refusing.** A cap that is zero, negative or unreadable
    returns False rather than being treated as "no limit configured", because
    that reading is exactly how a guard becomes a bill. Zero therefore means
    *never* — the browser's voice does everything — which is a useful setting
    rather than a broken one.

    Strictly less-than, so a cap of 200 permits the 200th request and refuses
    the 201st: the number somebody typed is the number they get.
    """
    if cap <= 0:
        return False
    return used < cap


def spent_today(database: Database) -> int:
    raw = read_setting(database, cap_key(today()), "0")
    try:
        return int(raw)
    except ValueError:
        # An unreadable counter is treated as spent rather than as zero, for
        # the same reason a nonsense cap refuses: the safe direction is fewer
        # requests, not more.
        return DEFAULT_DAILY_CAP


def note_request(database: Database) -> None:
    write_setting(database, cap_key(today()), str(spent_today(database) + 1))


def daily_cap(database: Database) -> int:
    raw = read_setting(database, DAILY_CAP_SETTING, str(DEFAULT_DAILY_CAP))
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_DAILY_CAP


def spoken_form(text: str, trim: bool) -> str:
    """What the voice actually says, which is not always the reply.

    A long answer is *announced* rather than read. The alternative — reading the
    first two minutes and stopping — sounds like a fault and leaves the listener
    unsure whether they missed the answer, when the whole thing is on screen in
    front of them. With trimming off, everything is read, however long.
    """
    stripped = speakable(text, limit=10_000_000)
    if not trim or len(stripped) <= WALL_OF_TEXT_CHARACTERS:
        return speakable(text, MAX_SPOKEN_CHARACTERS if trim else 10_000_000)
    return WALL_OF_TEXT_LINES[len(stripped) % len(WALL_OF_TEXT_LINES)]


def cap_enforced(database: Database) -> bool:
    """Whether the daily cap is switched on. Off unless somebody asked for it.

    Separate from the number, so `0` keeps meaning what it has always meant —
    *never call Fish, the browser reads everything* — instead of being
    overloaded into a second way of saying "no limit". Two settings, two
    questions, neither of them ambiguous.
    """
    return read_setting(database, DAILY_CAP_ENABLED_SETTING, "false") == "true"


def speaks_the_fallback(database: Database) -> bool:
    """Whether a refused line is read by the browser or simply not read.

    Defaults to reading it. Silence is a deliberate choice, and a voice feature
    whose out-of-the-box behaviour was "sometimes nothing happens" would be
    indistinguishable from a broken one.
    """
    return read_setting(database, FALLBACK_SETTING, DEFAULT_FALLBACK) != "silence"
