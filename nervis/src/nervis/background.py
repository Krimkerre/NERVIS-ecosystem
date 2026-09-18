"""Work NERVIS starts with nobody watching (M25).

**The last milestone of Stage 11, and the first that changes what NERVIS may
do rather than what it knows.** M21 gave it somewhere to put things, M22 a
record of what you did with its offers, M23 a place to keep what it was told,
M24 an order to do things in. All four leave the gate exactly where §12 puts it.
This one lets NERVIS *begin* something, which is why it is last and why it is
off until somebody turns it on.

**Nothing it produces is an act.** Every output is a note in the notification
centre. It may observe, draft and ask; it may not run an operation, and there is
no code path here that could — `notifications.post` is the only writer this
module reaches. An act still needs the same confirmation it needs when you are
watching.

**Every run is a row, including the ones that decided to say nothing.** A ledger
of the runs that produced a note answers the cheerful half of "what has this
been doing" and leaves the expensive half unanswerable. What prompted it, which
model ran, what it cost, and whether anything came of it.

**Where the contention promise stands.** M25's criterion asks for a pool
distinct from chat's *so the two do not contend*, which RAVIS M26's
`ravis/background` is meant to guarantee. That pool does not exist yet. So the
pool is configuration with a default, the session id is distinct — affinity
cannot drag chat onto whatever this loaded — and the guarantee is honestly
absent rather than quietly assumed. §9.6.1's background marker is deliberately
*not* sent: "must be free" resolves to a local model, which is the one thing
that would make this contend with the chat it is supposed to run beside.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from nervis import notifications
from nervis.storage import Database
from nervis.voice import read_setting, write_setting

#: Off until somebody says otherwise. A feature that spends money unattended is
#: the one kind that may not default to on, whatever the default would cost.
ENABLED = "background.enabled"
POOL = "background.pool"
INTERVAL = "background.interval_minutes"
CEILING = "background.daily_runs"
#: Whether conversations get a model-written title. Not behind `ENABLED`: that
#: switch is about thinking nobody asked for, and a title is part of a
#: conversation somebody is having — so it defaults on, and uses the same pool.
TITLES = "background.titles"

DEFAULTS = {
    ENABLED: "0",
    # **`ravis/free-api` (RAVIS M28), which is what this milestone actually wanted.**
    # It shipped defaulting to `ravis/auto` with the contention clause openly
    # unmet, because the pool that would satisfy it did not exist. It does now:
    # free *and* remote, so unattended work costs nothing and cannot take the
    # memory the conversation somebody is having needs.
    #
    # Not `ravis/cheap`, whose "cheapest" resolves to a local model — the one
    # outcome this cannot afford. Not §9.6.1's marker either, for the same
    # reason: "must be free" admits a local model.
    POOL: "ravis/free-api",
    INTERVAL: "30",
    # A ceiling in runs rather than currency. NERVIS counts requests and does
    # not know prices — §14's rule about estimates and invoices — so a spend cap
    # here would be a number NERVIS was asserting rather than measuring.
    CEILING: "12",
    TITLES: "1",
}

#: What may prompt a run, and what each one is for. Every trigger is individually
#: disableable, which is M25's word: turning one off stops future runs of it and
#: leaves the notes it already filed alone, because hiding the record of what
#: something did is not the same as stopping it.
TRIGGERS = {
    "service_health": "a service has been unreachable or degraded for a while",
    "daily_digest": "once a day, what the event hub recorded since the last one",
}


@dataclass(frozen=True)
class Settings:
    """How unattended work is configured on this installation."""

    enabled: bool
    pool: str
    interval_minutes: int
    daily_runs: int
    triggers: dict[str, bool]
    titles: bool = True

    def wants(self, trigger: str) -> bool:
        return self.enabled and self.triggers.get(trigger, False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "pool": self.pool,
            "interval_minutes": self.interval_minutes,
            "daily_runs": self.daily_runs,
            "titles": self.titles,
            "triggers": [
                {"name": name, "about": about, "on": self.triggers.get(name, False)}
                for name, about in TRIGGERS.items()
            ],
            # Said plainly rather than left to be discovered: the pool is the
            # operator's choice until RAVIS M26 exists, so "does not contend" is
            # not a promise this build can make.
            # **The clause is met now, and by a different pool than planned.**
            # M25 named RAVIS M26's `ravis/background`, whose invariant was
            # "must not contend" — a judgement. `ravis/free-api` (RAVIS M28) is a
            # checkable rule that produces the same outcome: free and remote
            # cannot take local memory, and cannot cost anything either.
            "contention": (
                "ravis/free-api costs nothing and runs elsewhere, so unattended work "
                "cannot take memory chat needs — as long as the pool above is a "
                "remote one"
                if self.pool == "ravis/free-api" else
                f"{self.pool} is not ravis/free-api, so whether this competes with "
                "chat depends on what that pool resolves to"
            ),
        }


def settings(database: Database) -> Settings:
    """Read the configuration, falling back to the defaults above."""
    return Settings(
        enabled=read_setting(database, ENABLED, DEFAULTS[ENABLED]) == "1",
        pool=read_setting(database, POOL, DEFAULTS[POOL]),
        interval_minutes=_number(read_setting(database, INTERVAL, DEFAULTS[INTERVAL]), 30),
        daily_runs=_number(read_setting(database, CEILING, DEFAULTS[CEILING]), 12),
        titles=read_setting(database, TITLES, DEFAULTS[TITLES]) == "1",
        triggers={
            name: read_setting(database, f"background.trigger.{name}", "1") == "1"
            for name in TRIGGERS
        },
    )


#: Every background call's fallback when the chosen pool answers nothing: this
#: machine's own models. Never a pool that could leave the machine, so an
#: operator who chose `ravis/private` or `ravis/local` for privacy is not
#: overruled by the fallback.
LOCAL_POOL = "ravis/local"

#: The pools whose whole point is that the work stays on this machine.
#:
#: `ravis/private` is RAVIS's strictest level and `ravis/local` says it in its
#: name; both are published with `locality="local"`, so a route that leaves one
#: of them has ignored the choice rather than fallen back within it.
ON_THIS_MACHINE = (LOCAL_POOL, "ravis/private")


def stays_here(config: Settings) -> bool:
    """Whether the chosen pool is one that must not leave this machine."""
    return config.pool.strip() in ON_THIS_MACHINE


def marker(config: Settings) -> dict[str, object]:
    """What every background request tells RAVIS about itself.

    **`background` is a price, not a boundary.** RAVIS reads it as "must be
    free", which a free *remote* model satisfies — so on its own it never kept a
    private choice. When the chosen pool is one that stays here, the request also
    declares `LOCAL_ONLY`, which RAVIS's policy may only tighten with
    (`ravis/src/ravis/policy.py`, `_declared_privacy`), so the constraint holds
    for every attempt in the chain rather than for the first one alone (base
    review, 17 September 2026, finding 6).
    """
    said: dict[str, object] = {"background": True}
    if stays_here(config):
        said["privacy"] = "LOCAL_ONLY"
    return said


def route(config: Settings, *between: str) -> tuple[str, ...]:
    """The models a background call tries, in order, each once.

    The pool chosen under Settings → Unattended work first — `ravis/free-api`
    unless somebody changed it, which costs nothing and loads nothing here —
    then whatever the caller already has in memory (the model that just
    answered a conversation), then this machine's own. Every background call
    goes through here: unattended thinking, conversation titles, a handed-over
    task's folder name, and the layout glance on a saved PDF.
    """
    # **Nothing is borrowed when the pool must stay here.** `between` is
    # whatever the caller already has in memory — for a title, the model that
    # answered the conversation, which is remote whenever the chat is — and
    # NERVIS cannot tell from the id whether a model is on this machine. So a
    # private or local choice drops the borrowed step rather than guessing, and
    # `marker` sends the constraint with the request besides.
    borrowed = () if stays_here(config) else between
    return tuple(dict.fromkeys(m for m in (config.pool, *borrowed, LOCAL_POOL) if m))


#: The settings that are one stored value each, and how each is stored.
_WRITTEN_AS: dict[str, tuple[str, Callable[[Any], str]]] = {
    "enabled": (ENABLED, lambda value: "1" if value else "0"),
    "titles": (TITLES, lambda value: "1" if value else "0"),
    "pool": (POOL, lambda value: str(value).strip()),
    "interval_minutes": (INTERVAL, lambda value: str(_number(value, 30))),
    "daily_runs": (CEILING, lambda value: str(_number(value, 12))),
}


def configure(database: Database, **values: Any) -> Settings:
    """Change one or more settings, refusing anything not in the set.

    Refused rather than ignored: a caller that misspells `intervall_minutes` and
    is told nothing has changed a setting it believes it changed, and will find
    out weeks later from a bill.
    """
    for key, value in values.items():
        if key in _WRITTEN_AS:
            name, written = _WRITTEN_AS[key]
            write_setting(database, name, written(value))
        elif key.startswith("trigger."):
            name = key.split(".", 1)[1]
            if name not in TRIGGERS:
                raise ValueError(f"no such trigger {name!r}")
            write_setting(database, f"background.trigger.{name}", "1" if value else "0")
        else:
            raise ValueError(f"{key!r} is not a background setting")
    return settings(database)


def record(
    database: Database, *, trigger: str, prompted_by: str, outcome: str,
    model: str = "", cost: str = "", detail: str = "", note_id: str = "",
) -> str:
    """File one run in the ledger, whatever came of it."""
    run_id = "bg_" + uuid.uuid4().hex[:12]
    with database.connection as connection:
        connection.execute(
            "INSERT INTO background_run (run_id, trigger, prompted_by, model, "
            "cost, outcome, detail, note_id, ran_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, trigger, prompted_by, model, cost, outcome, detail, note_id, _now()),
        )
    return run_id


def runs(database: Database, limit: int = 50) -> list[dict[str, Any]]:
    """The ledger, newest first."""
    rows = database.connection.execute(
        "SELECT * FROM background_run ORDER BY ran_at DESC LIMIT ?",
        (max(1, min(int(limit), 500)),),
    ).fetchall()
    return [dict(row) for row in rows]


def ran_today(database: Database) -> int:
    """How many runs have happened since midnight, for the ceiling."""
    row = database.connection.execute(
        "SELECT COUNT(*) FROM background_run WHERE ran_at >= ?",
        (datetime.now(timezone.utc).strftime("%Y-%m-%d"),),
    ).fetchone()
    return int(row[0]) if row else 0


def may_run(database: Database, config: Settings) -> str:
    """Empty when a run is allowed, or the reason it is not.

    A sentence rather than a boolean, because every one of these is worth being
    able to show somebody who is wondering why nothing has happened — "it is
    off" and "it has hit today's ceiling" are different problems.
    """
    if not config.enabled:
        return "unattended work is switched off"
    used = ran_today(database)
    if used >= config.daily_runs:
        return f"today's ceiling of {config.daily_runs} runs is used up ({used})"
    return ""


def session_id(database: Database) -> str:
    """A session id of this installation's own, distinct from any chat.

    **Stable across runs and never a chat's.** RAVIS keeps session affinity, so
    sharing one would let unattended work decide which model the person's next
    message goes to — the exact coupling M25 asks to avoid. Stored rather than
    generated per run so the affinity is worth having at all.
    """
    held = read_setting(database, "background.session", "")
    if held:
        return held
    minted = "bgs_" + uuid.uuid4().hex[:12]
    write_setting(database, "background.session", minted)
    return minted


#: How long a service has to have been unwell before it is worth thinking about.
#: Two consecutive sweeps is noise; a state that has outlived several is a
#: condition. Expressed in observations rather than minutes because the probe
#: interval is configurable and the question is "has this settled", not "how long".
SETTLED_AFTER = 3


@dataclass(frozen=True)
class Prompted:
    """Something worth a run, and the facts it should be given.

    `facts` is what NERVIS observed, in NERVIS's own words. It is handed to the
    model as material and never as instruction — §11.5's rule applies here as
    much as to a retrieved document, and more so, because nobody is watching.
    """

    trigger: str
    prompted_by: str
    facts: str


def worth_thinking_about(
    config: Settings, unwell: list[dict[str, Any]], since_digest_hours: float,
    recent_events: list[dict[str, Any]],
) -> Prompted | None:
    """What, if anything, this installation should think about now.

    **Pure, and that is deliberate.** Deciding whether there is anything to say
    is the half worth testing exhaustively, and it must be answerable without a
    model, a network or a clock. The run that follows is the easy part.

    Returns one thing at most. A sweep that produced two notes at once would be
    two model calls the ceiling counted as one, and a notification centre that
    arrives in bursts.
    """
    if config.wants("service_health"):
        settled = [s for s in unwell if int(s.get("observations", 0)) >= SETTLED_AFTER]
        if settled:
            named = ", ".join(f"{s['label']} is {s['state']}" for s in settled[:4])
            return Prompted(
                "service_health",
                f"{len(settled)} service(s) have stayed unwell: {named}",
                "\n".join(
                    f"- {s['label']}: {s['state']} for {s['observations']} consecutive "
                    f"checks. Last detail: {s.get('detail') or 'none recorded'}"
                    for s in settled
                ),
            )
    if config.wants("daily_digest") and since_digest_hours >= 24 and recent_events:
        kinds: dict[str, int] = {}
        for event in recent_events:
            kinds[str(event.get("event_type", "?"))] = kinds.get(
                str(event.get("event_type", "?")), 0) + 1
        return Prompted(
            "daily_digest",
            f"{len(recent_events)} events recorded since the last digest",
            "\n".join(f"- {kind}: {count}" for kind, count in sorted(kinds.items())),
        )
    return None


#: What the model is asked to do with those facts. **It is asked for prose about
#: observations, and given no verbs.** There is nothing here it could answer with
#: that would become an action, because the only thing done with its reply is
#: `notifications.post` — but the instruction says so anyway, because a prompt
#: that invites a plan gets a plan, and a plan in a notification reads like
#: something NERVIS intends to do.
BRIEF = (
    "You are writing one short note for the person who runs this machine. "
    "Below are things NERVIS observed. Say what they mean and what is worth "
    "checking, in two or three sentences. Do not propose that anything be run, "
    "and do not invent figures that are not below. Begin with a heading line of "
    "at most eight words, then a blank line, then the note."
)


async def think(
    database: Database, prompted: Prompted, config: Settings, *,
    ask: Any,
) -> str:
    """Run one unattended thought and file whatever came of it.

    `ask` is a callable taking `(pool, session, brief, facts)` and returning
    `(text, model, cost)` — the RAVIS call, injected rather than imported.
    That keeps the decision, the ledger and the note testable without a network,
    and it is the same seam the title generator would have wanted.

    **Every path writes a row.** A refusal, an empty answer and a note all leave
    the ledger able to say what happened; the failure this avoids is a run that
    cost something and left no trace because it produced nothing worth showing.

    **Failures are silent to the user and loud in the ledger.** Nobody asked for
    this to happen, so nobody should be interrupted by it going wrong — but
    somebody paying for it should be able to see that it did.
    """
    try:
        text, model, cost = await ask(
            config.pool, session_id(database), BRIEF, prompted.facts
        )
    except Exception as failure:  # noqa: BLE001 - unattended work must not raise
        record(database, trigger=prompted.trigger, prompted_by=prompted.prompted_by,
               outcome="failed", detail=str(failure)[:200])
        return ""
    heading, _, body = text.strip().partition("\n")
    if not heading.strip() or not body.strip():
        # A reply that did not come back in the shape asked for is not filed as
        # a note. A heading with no note, or a note with no heading, would go
        # into the centre looking like something NERVIS meant to say.
        record(database, trigger=prompted.trigger, prompted_by=prompted.prompted_by,
               model=model, cost=cost, outcome="unusable",
               detail=text.strip()[:200] or "the model returned nothing")
        return ""
    note = notifications.post(
        database,
        kind=prompted.trigger,
        title=heading.strip().lstrip("#").strip(),
        body=body.strip(),
        reason=prompted.prompted_by,
        severity="warning" if prompted.trigger == "service_health" else "info",
        source="background",
        # M21 refuses a model-produced note that does not name both. That is the
        # constraint doing its job: everything filed here has an author and a
        # price, and there is no path that files one without.
        model=model,
        cost=cost,
    )
    record(database, trigger=prompted.trigger, prompted_by=prompted.prompted_by,
           model=model, cost=cost, outcome="noted", note_id=note.note_id)
    return note.note_id


def _number(value: Any, fallback: int) -> int:
    try:
        found = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return found if found > 0 else fallback


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


__all__ = [
    "BRIEF", "DEFAULTS", "Prompted", "Settings", "TRIGGERS", "configure",
    "may_run", "record", "ran_today", "runs", "session_id", "settings", "think",
    "worth_thinking_about",
]
