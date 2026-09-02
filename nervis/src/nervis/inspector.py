"""M11 — one request through RAVIS, stage by stage (§11.4).

§7.1 calls the route inspector the thing that *"makes ordinary chat a RAVIS
debugging tool"*. M4 built the one-line version onto a chat reply; this is the
whole of §11.4, which asks for different stages depending on how the request was
executed and is emphatic about one thing:

    **Do not imply RAVIS normalized content it actually passed through
    untouched.**

So the stage list is chosen by `execution_path` rather than being one list with
some rows blank. A transparent route has no normalized request — not an empty
one — and drawing an empty box labelled "normalized request" is precisely the
implication that sentence forbids.

**What RAVIS publishes, and what it does not.** `/api/v1/route-decisions` gives
the decision and the execution: what was asked for, what was picked and why,
what was considered and excluded, which provider ran it, every attempt with its
outcome and timings. It publishes **no content** — not the client's messages,
not the native request body, not the events. §1 forbids inventing the endpoint
that would, so every content stage here is present and labelled *not published*,
naming what RAVIS would have to expose. A stage list with honest gaps is a
specification of the missing surface; a stage list with the gaps deleted is a
screen that quietly redefines §11.4 as whatever was easy.

**NERVIS's own turns are the exception**, and only because they are not
RAVIS's to publish: when NERVIS itself was the client, the request it sent and
the reply it received are its own records, already stored and already on the
chat screen. Showing them here is not a new exposure — but it is still content,
so it follows the setting, which is **off by default**.

**Credentials are never displayed.** Nothing in a decision carries one, which is
a property of RAVIS's surface rather than a promise of this module, so this
copies field by field from an allowlist instead of passing the object through.
A field RAVIS adds later cannot arrive on a NERVIS screen without somebody
adding it here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from nervis.storage import Database
from nervis.voice import read_setting, write_setting

# Whether the inspector may show message content NERVIS itself holds. Off by
# default: the inspector's subject is the route, and content is the part a
# person should have to ask for.
SHOW_CONTENT = "inspector.show_content"

# §6's two execution paths, in RAVIS's own vocabulary. Anything else is reported
# as unknown rather than guessed into one of them — mislabelling a transparent
# route as translated is the exact claim §11.4 forbids.
TRANSPARENT = "TRANSPARENT"

# What a stage's content is doing here. Three states, because "we do not show
# this" and "nobody publishes this" and "here it is" are three different facts
# and only one of them is a gap somebody could close.
PUBLISHED = "published"          # RAVIS publishes it and it is below
NOT_PUBLISHED = "not_published"  # RAVIS records it internally; no surface exposes it
HELD_BY_NERVIS = "held"          # NERVIS was the client, so this is its own record
WITHHELD = "withheld"            # NERVIS holds it, and the content setting is off

# The only keys copied out of a decision. An allowlist rather than a filter, so
# a field RAVIS adds later arrives on no screen until somebody puts it here.
DECISION_FIELDS = (
    "decision_id", "request_id", "trace_id", "application_id", "decided_at",
    "requested", "pool", "selected", "reason", "execution_path",
    "fallbacks", "requirements", "excluded", "unverified",
)

# `considered` is a list of every model RAVIS looked at — 550 of them on this
# machine. Kept as a count plus a sample, because a screen that prints all of
# them is one nobody scrolls past.
CONSIDERED_SAMPLE = 12


@dataclass(frozen=True)
class Stage:
    """One step of §11.4's sequence, and what is known about it."""

    name: str
    state: str
    detail: str = ""
    content: str = ""

    def as_dict(self) -> dict[str, Any]:
        found = {"name": self.name, "state": self.state, "detail": self.detail}
        if self.content:
            found["content"] = self.content
        return found


@dataclass(frozen=True)
class Inspection:
    decision: dict[str, Any] = field(default_factory=dict)
    stages: list[Stage] = field(default_factory=list)
    considered_count: int = 0
    considered_sample: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": dict(self.decision),
            "stages": [stage.as_dict() for stage in self.stages],
            "considered_count": self.considered_count,
            "considered_sample": list(self.considered_sample),
            "attempts": [dict(one) for one in self.attempts],
            "path": self.path,
        }


def show_content(database: Database) -> bool:
    """Whether message content may be shown. Off unless switched on."""
    return read_setting(database, SHOW_CONTENT, "0") == "1"


def set_show_content(database: Database, on: bool) -> bool:
    write_setting(database, SHOW_CONTENT, "1" if on else "0")
    return on


def is_transparent(path: str) -> bool:
    """§6's transparent path, matched exactly.

    `execution_path` reads `TRANSPARENT` or `TRANSLATED_NATIVE`. Matching on a
    prefix would make an unknown future value like `TRANSPARENT_CACHED` claim a
    path nobody verified, so this is equality and everything else is treated as
    translated-or-unknown — which shows more stages rather than fewer, and the
    error worth avoiding is the one that shows fewer.
    """
    return path == TRANSPARENT


def _destination(execution: Mapping[str, Any]) -> str:
    """Which provider actually ran it, from the execution record."""
    provider = str(execution.get("provider") or "")
    return provider or "not recorded"


def _stream_metadata(attempts: list[Mapping[str, Any]]) -> str:
    """Timings, which is the stream metadata RAVIS does publish.

    §6.3's rule about unknown values applies: `ttft_ms` is null on a
    non-streamed call and saying "0 ms to first token" would be a measurement
    nobody made.
    """
    if not attempts:
        return "no attempt was recorded"
    last = attempts[-1]
    elapsed = last.get("elapsed_ms")
    ttft = last.get("ttft_ms")
    parts = []
    if isinstance(elapsed, (int, float)):
        parts.append(f"{_ms(float(elapsed))} end to end")
    parts.append(
        f"{_ms(float(ttft))} to first token" if isinstance(ttft, (int, float))
        else "time to first token not measured on this call"
    )
    return " · ".join(parts)


def _ms(value: float) -> str:
    """A duration, without rounding it into a different claim.

    `round()` alone turned RAVIS's `0.42` into **"0 ms end to end"** — a
    streamed call to a hosted provider reported as instantaneous, on the one
    screen whose whole purpose is showing what really happened. The number was
    RAVIS's; making it nonsense was this function's doing.

    A sub-millisecond figure is kept to a decimal so it reads as the small
    number it is. That it is *implausibly* small for a network call is a fact
    about RAVIS's measurement and is the reader's to notice — an inspector that
    silently corrects its subject is not an inspector.
    """
    return f"{value:.2f} ms" if value < 10 else f"{round(value)} ms"


def _own_turn(
    turn: Mapping[str, Any] | None, allowed: bool, name: str, application: str = ""
) -> Stage:
    """A stage NERVIS can fill from its own records, if content is switched on.

    **Absent has two reasons and they are different facts.** A request from
    Clarvis is somebody else's content and NERVIS never had it. A request from
    NERVIS that has no chat turn is a §9.6.1 background call — a generated
    title, a background thought — which NERVIS did make and does not store as a
    conversation. Reporting the second as "NERVIS was not the client" is simply
    false, and it was: the title-generation call sits one row above the chat it
    named, and the inspector disowned it.
    """
    text = str((turn or {}).get(name) or "")
    if not text:
        return Stage(
            name.replace("_", " "), NOT_PUBLISHED,
            "NERVIS made this request but it is not a chat turn — a background call "
            "(§9.6.1) such as a generated title, which is not stored as a conversation."
            if application == "nervis" else
            "NERVIS was not the client for this request, so it holds no copy. RAVIS "
            "publishes no content, so nothing else can supply it.",
        )
    if not allowed:
        return Stage(
            name.replace("_", " "), WITHHELD,
            "NERVIS holds this because it made the request, and the content setting "
            "is off. Turn it on to show it.",
        )
    return Stage(name.replace("_", " "), HELD_BY_NERVIS,
                 "NERVIS's own record of the request it made.", text)


def stages(
    decision: Mapping[str, Any],
    turn: Mapping[str, Any] | None = None,
    *,
    allow_content: bool = False,
) -> list[Stage]:
    """§11.4's sequence for this execution path.

    Two lists, not one list with rows blanked out. A transparent route did not
    have a normalized request, and showing that row empty implies RAVIS
    normalized something it passed through untouched.
    """
    execution = decision.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    attempts = [a for a in (execution.get("attempts") or []) if isinstance(a, Mapping)]
    path = str(decision.get("execution_path") or "")
    application = str(decision.get("application_id") or "")

    if is_transparent(path):
        return [
            _own_turn(turn, allow_content, "request", application),
            Stage("route decision", PUBLISHED,
                  str(decision.get("reason") or "no reason recorded")),
            Stage("upstream destination", PUBLISHED, _destination(execution)),
            Stage("stream metadata", PUBLISHED, _stream_metadata(attempts)),
            _own_turn(turn, allow_content, "response", application),
        ]

    return [
        _own_turn(turn, allow_content, "request", application),
        Stage("normalized request", NOT_PUBLISHED,
              "RAVIS builds this on the translated path and publishes no surface "
              "for it. What it would take: a stage record on the decision."),
        Stage("route decision", PUBLISHED,
              str(decision.get("reason") or "no reason recorded")),
        Stage("native provider request", NOT_PUBLISHED,
              f"assembled for {_destination(execution)} and not published."),
        Stage("native events", NOT_PUBLISHED,
              "the provider's own stream, consumed and not retained."),
        Stage("normalized events", NOT_PUBLISHED,
              "the normalized form RAVIS renders the OpenAI stream from."),
        Stage("stream metadata", PUBLISHED, _stream_metadata(attempts)),
        _own_turn(turn, allow_content, "response", application),
    ]


def inspect(
    decision: Mapping[str, Any],
    turn: Mapping[str, Any] | None = None,
    *,
    allow_content: bool = False,
) -> Inspection:
    """One request, as §11.4 asks for it.

    The decision is copied field by field from `DECISION_FIELDS`; nothing else
    on RAVIS's object reaches the caller.
    """
    execution = decision.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    considered = [str(one) for one in (decision.get("considered") or [])]
    return Inspection(
        decision={name: decision.get(name) for name in DECISION_FIELDS},
        stages=stages(decision, turn, allow_content=allow_content),
        considered_count=len(considered),
        considered_sample=considered[:CONSIDERED_SAMPLE],
        attempts=[dict(a) for a in (execution.get("attempts") or []) if isinstance(a, Mapping)],
        path=str(decision.get("execution_path") or "unknown"),
    )


__all__ = [
    "DECISION_FIELDS", "HELD_BY_NERVIS", "Inspection", "NOT_PUBLISHED", "PUBLISHED",
    "SHOW_CONTENT", "Stage", "TRANSPARENT", "WITHHELD", "inspect", "is_transparent",
    "set_show_content", "show_content", "stages",
]
