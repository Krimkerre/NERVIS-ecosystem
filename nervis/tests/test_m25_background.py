"""Work NERVIS starts with nobody watching (NERVIS.md M25).

The last milestone of Stage 11 and the first that changes what NERVIS may *do*.
Four clauses carry it, and three are about restraint:

  1. **Nothing it produces is an act.** Every output is a note; §12's gate does
     not move.
  2. **Every run is attributable** — what prompted it, which model, what it
     cost — including the runs that decided to say nothing.
  3. **A trigger is disableable individually and all of them together**, and
     disabling stops future runs rather than hiding past notes.
  4. It does not contend with chat. That one this build cannot promise, because
     `ravis/background` does not exist yet; what it can do is keep a session of
     its own and let the pool be chosen, which is asserted here so the gap stays
     visible rather than becoming an assumption.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import background, notifications
from nervis.app import create_app
from nervis.config import Settings
from nervis.storage.database import prepare_database

FACTS = "- RAVIS: unreachable for 4 consecutive checks"
REPLY = "RAVIS is still refusing connections\n\nFour checks in a row were refused."


@pytest.fixture()
def database() -> Any:
    return prepare_database(":memory:")


@pytest.fixture()
def on(database: Any) -> Any:
    background.configure(database, enabled=True)
    return background.settings(database)


def _answers(text: str = REPLY, model: str = "claude-haiku-4-5",
             cost: str = "1,240 tokens") -> Any:
    # The signature is the contract `think` calls this with; the names are the
    # documentation, so they stay rather than becoming underscores.
    async def ask(pool: str, session: str, brief: str, facts: str) -> tuple[str, str, str]:  # noqa: ARG001, E501

        return text, model, cost
    return ask


# ── Off by default, and off means off ───────────────────────────────────────


def test_unattended_work_is_off_until_somebody_turns_it_on(database: Any) -> None:
    """The one kind of feature that may not default to on.

    It spends money without being asked to. Whatever the default would cost,
    the person paying has to have chosen it.
    """
    config = background.settings(database)
    assert config.enabled is False
    assert background.may_run(database, config) == "unattended work is switched off"


def test_nothing_is_worth_thinking_about_while_it_is_off(database: Any) -> None:
    """The switch is checked before the triggers, not after.

    A build that decided what to think about and *then* noticed it was disabled
    would be one refactor away from doing the thinking too.
    """
    unwell = [{"label": "RAVIS", "state": "unreachable", "observations": 9}]
    assert background.worth_thinking_about(
        background.settings(database), unwell, 99, [{"event_type": "x"}]
    ) is None


# ── Restraint ───────────────────────────────────────────────────────────────


def test_a_state_has_to_settle_before_it_is_worth_a_run(on: Any) -> None:
    """Two sweeps is noise; a state that outlived several is a condition."""
    for seen, expected in ((1, None), (2, None), (background.SETTLED_AFTER, "service_health")):
        found = background.worth_thinking_about(
            on, [{"label": "RAVIS", "state": "unreachable", "observations": seen}], 0, []
        )
        assert (found.trigger if found else None) == expected


def test_one_thing_at_a_time(on: Any) -> None:  # noqa: ARG001
    """A sweep producing two notes would be two model calls the ceiling counted
    as one, and a notification centre that arrives in bursts."""
    found = background.worth_thinking_about(
        on, [{"label": "RAVIS", "state": "unreachable", "observations": 5}],
        99, [{"event_type": "x"}],
    )
    assert found is not None and found.trigger == "service_health"


def test_the_ceiling_is_enforced_and_says_so(database: Any) -> None:
    background.configure(database, enabled=True, daily_runs=3)
    for _ in range(3):
        background.record(database, trigger="daily_digest", prompted_by="x",
                          outcome="nothing to say")
    refusal = background.may_run(database, background.settings(database))
    assert "ceiling" in refusal and "3" in refusal


# ── Attribution ─────────────────────────────────────────────────────────────


def test_every_outcome_is_a_row(on: Any, database: Any) -> None:
    """Including the ones that produced nothing.

    A ledger of the runs that filed a note answers the cheerful half of "what
    has this been doing" and leaves the expensive half unanswerable.
    """
    prompted = background.Prompted("service_health", "RAVIS is unwell", FACTS)

    async def boom(pool: str, session: str, brief: str, facts: str) -> Any:  # noqa: ARG001, E501

        raise RuntimeError("RAVIS said 503")

    asyncio.run(background.think(database, prompted, on, ask=_answers()))
    asyncio.run(background.think(database, prompted, on, ask=_answers(text="")))
    asyncio.run(background.think(database, prompted, on, ask=boom))

    outcomes = [row["outcome"] for row in background.runs(database)]
    assert sorted(outcomes) == ["failed", "noted", "unusable"]
    filed = next(r for r in background.runs(database) if r["outcome"] == "noted")
    assert filed["model"] == "claude-haiku-4-5"
    assert filed["cost"] == "1,240 tokens"
    assert filed["prompted_by"] == "RAVIS is unwell"


def test_a_note_it_files_names_its_author_and_price(on: Any, database: Any) -> None:
    """M21 refuses a model-produced note that names one without the other, so
    this is that constraint doing its job rather than a rule restated."""
    asyncio.run(background.think(
        database, background.Prompted("service_health", "why", FACTS), on,
        ask=_answers(),
    ))
    note = notifications.recent(database)[0]
    assert note.as_dict()["produced_by"] == {
        "model": "claude-haiku-4-5", "cost": "1,240 tokens",
    }
    assert note.source == "background"
    assert note.reason == "why"


def test_a_reply_in_the_wrong_shape_is_not_filed(on: Any, database: Any) -> None:
    """A heading with no note would go into the centre looking like something
    NERVIS meant to say."""
    asyncio.run(background.think(
        database, background.Prompted("service_health", "why", FACTS), on,
        ask=_answers(text="Just a heading"),
    ))
    assert notifications.recent(database) == []
    assert background.runs(database)[0]["outcome"] == "unusable"


# ── Nothing it produces is an act ───────────────────────────────────────────


def test_the_module_can_only_write_notes(on: Any, database: Any) -> None:  # noqa: ARG001
    """§12's gate does not move, and the way that survives a refactor is that
    there is no other writer in reach.

    Asserted against the source: `background.py` may reach `notifications` and
    its own ledger, and nothing that performs an operation.
    """
    from pathlib import Path

    source = (Path(background.__file__)).read_text(encoding="utf-8")
    for forbidden in ("commands.run", "_submit_benchmark", "runCommand",
                      "commands.propose", "subprocess", "os.system"):
        assert forbidden not in source, (
            f"background.py reaches {forbidden!r}; unattended work may propose "
            "and notify, never act"
        )


def test_the_brief_asks_for_prose_and_forbids_proposing() -> None:
    """A prompt that invites a plan gets a plan, and a plan in a notification
    reads like something NERVIS intends to do."""
    assert "Do not propose that anything be run" in background.BRIEF
    assert "do not invent figures" in background.BRIEF


# ── Its own session, and an honest word about contention ────────────────────


def test_the_session_is_its_own_and_stable(database: Any) -> None:
    """RAVIS keeps session affinity, so sharing chat's would let unattended work
    decide which model the person's next message goes to."""
    first = background.session_id(database)
    assert first.startswith("bgs_")
    assert background.session_id(database) == first


def test_the_contention_claim_matches_the_pool_in_force(database: Any) -> None:
    """**This test used to pin the gap. The gap is closed, so it pins the fix.**

    M25 shipped naming RAVIS M26's `ravis/background`, whose invariant was "must
    not contend" — a judgement nothing could check. `ravis/free-api` (RAVIS M28) is
    a checkable rule with the same outcome: free and remote cannot take the
    memory chat needs.

    What is asserted is that the sentence tracks the configuration rather than
    being a fixed boast. Point it at another pool and it stops claiming.
    """
    said = background.settings(database).as_dict()["contention"]
    assert "ravis/free-api costs nothing and runs elsewhere" in said

    background.configure(database, pool="ravis/auto")
    said = background.settings(database).as_dict()["contention"]
    assert "ravis/auto is not ravis/free-api" in said
    assert "depends on what that pool resolves to" in said


def test_the_default_pool_cannot_resolve_to_a_local_model(database: Any) -> None:
    """The constraint underneath the default, which outlives the default.

    `ravis/cheap` is the trap: "least monetary cost" resolves to a local model,
    and loading one is exactly how work nobody is watching starts competing with
    the conversation somebody is having. `ravis/free-api` requires *remote* as well
    as free, which is why it is the default rather than cheap.
    """
    assert background.DEFAULTS[background.POOL] == "ravis/free-api"
    assert background.settings(database).pool == "ravis/free-api"
    assert background.DEFAULTS[background.POOL] != "ravis/cheap"


# ── Triggers ────────────────────────────────────────────────────────────────


def test_a_trigger_can_be_turned_off_on_its_own(on: Any, database: Any) -> None:  # noqa: ARG001
    # `on` is taken for the fixture: it switches the feature on, which is the
    # precondition for a per-trigger switch meaning anything.
    background.configure(database, **{"trigger.service_health": False})
    config = background.settings(database)
    unwell = [{"label": "RAVIS", "state": "unreachable", "observations": 9}]
    assert background.worth_thinking_about(config, unwell, 0, []) is None
    # …and the other one still fires.
    assert background.worth_thinking_about(
        config, [], 99, [{"event_type": "x"}]
    ) is not None


def test_disabling_stops_future_runs_and_keeps_past_notes(on: Any, database: Any) -> None:
    """Hiding the record of what something did is not the same as stopping it."""
    asyncio.run(background.think(
        database, background.Prompted("service_health", "why", FACTS), on,
        ask=_answers(),
    ))
    background.configure(database, enabled=False)
    assert len(notifications.recent(database)) == 1
    assert len(background.runs(database)) == 1
    assert background.may_run(database, background.settings(database))


def test_an_unknown_setting_is_refused(database: Any) -> None:
    """A caller told nothing has changed a setting it believes it changed, and
    finds out weeks later from a bill."""
    for bad in ({"intervall_minutes": 5}, {"trigger.nonsense": True}, {"pooll": "x"}):
        with pytest.raises(ValueError):
            background.configure(database, **bad)


# ── The API and the switch ──────────────────────────────────────────────────


def test_the_api_offers_the_switch_and_the_ledger(tmp_path: Any) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "n.db"), workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/v1/background").json()
        assert body["enabled"] is False
        assert body["why_not"] == "unattended work is switched off"
        assert {t["name"] for t in body["triggers"]} == set(background.TRIGGERS)

        assert client.post("/api/v1/background", json={"enabled": True}).json()["enabled"]
        assert client.get("/api/v1/background").json()["why_not"] == ""
        assert client.post("/api/v1/background", json={"nope": 1}).status_code == 409


def test_there_is_no_endpoint_that_starts_a_run(tmp_path: Any) -> None:
    """Unattended work is unattended. A "run now" button would exercise the
    timer, which is not the thing anybody wants to test."""
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "n.db"), workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        for path in ("/api/v1/background/run", "/api/v1/background/now"):
            assert client.post(path, json={}).status_code == 404


# ── Where every background call goes ────────────────────────────────────────


def test_titles_are_on_by_default_and_not_behind_the_thinking_switch(database: Any) -> None:
    """A title is part of a conversation somebody is having, not thinking nobody
    asked for — so it defaults on while unattended work defaults off."""
    config = background.settings(database)
    assert config.titles is True and config.enabled is False
    assert background.configure(database, titles=False).titles is False
    assert background.settings(database).as_dict()["titles"] is False


def test_the_route_is_the_chosen_pool_then_this_machine(database: Any) -> None:
    """Free and remote first by default; a private choice is never overruled by
    a fallback that could leave the machine."""
    loaded = "ravis/ollama/llama3.2:3b"
    assert background.route(background.settings(database)) == ("ravis/free-api", "ravis/local")
    assert background.route(background.settings(database), loaded) == (
        "ravis/free-api", loaded, "ravis/local")
    background.configure(database, pool="ravis/private")
    assert background.route(background.settings(database)) == ("ravis/private", "ravis/local")
    background.configure(database, pool="ravis/local")
    assert background.route(background.settings(database)) == ("ravis/local",)


def test_a_private_pool_borrows_no_remote_model_and_says_so_to_ravis(database: Any) -> None:
    """**The middle step was the hole.** Between the chosen pool and this
    machine sat "whatever the caller already has in memory" — for a title, the
    model that answered the conversation, which is remote whenever the chat is.
    So an owner who set unattended work to `ravis/private` could have a
    conversation's opening posted to a free remote model, because the only
    constraint that travelled was the background marker, and that one means
    "must be free" rather than "must stay here" (base review, 17 September
    2026, finding 6)."""
    remote = "anthropic/claude-haiku-4-5"
    for private in ("ravis/private", "ravis/local"):
        background.configure(database, pool=private)
        config = background.settings(database)
        assert remote not in background.route(config, remote), private
        assert background.route(config, remote)[0] == private, private
        # And the constraint travels with every attempt, rather than being a
        # property of the first one.
        assert background.marker(config) == {"background": True, "privacy": "LOCAL_ONLY"}

    background.configure(database, pool="ravis/free-api")
    config = background.settings(database)
    assert background.route(config, remote) == ("ravis/free-api", remote, "ravis/local")
    assert background.marker(config) == {"background": True}


def test_unattended_thinking_falls_back_to_this_machine() -> None:
    """A free tier that is rate-limited should cost a slower note, not no note."""
    from nervis.app import _ask_ravis
    from test_m4_chat import an_api

    asked: list[str] = []

    class _Reply:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

        @staticmethod
        def json() -> dict[str, Any]:
            return {"model": "ravis/ollama/llama3.2:3b",
                    "choices": [{"message": {"content": REPLY}}],
                    "usage": {"total_tokens": 42}}

    class _Client:
        @staticmethod
        async def post(url: str, **kwargs: Any) -> Any:
            del url
            asked.append(kwargs["json"]["model"])
            return _Reply(429 if len(asked) == 1 else 200)

    app = an_api().app
    app.state.settings.ravis_client_credential = "secret"
    app.state.probe_client = _Client()
    text, _, cost = asyncio.run(_ask_ravis(app)("ravis/free-api", "s", "brief", FACTS))

    assert asked == ["ravis/free-api", "ravis/local"]
    assert text == REPLY and cost == "42 tokens"


def test_unattended_thinking_leaves_a_reasoning_model_room_to_answer() -> None:
    """Found 13 September 2026: at 300 tokens, the free pool's reasoning model spent the
    whole budget thinking and returned no text, so every morning's digest fell back to
    `ravis/local` and loaded a local model nobody asked for. The free pool is asked with
    room to finish, so the fallback is for a free tier that is down, not one cut off.
    """
    from nervis.app import _ask_ravis
    from test_m4_chat import an_api

    budgets: list[int] = []

    class _Reply:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"model": "cohere/north-mini-code:free",
                    "choices": [{"message": {"content": REPLY}}],
                    "usage": {"total_tokens": 900}}

    class _Client:
        @staticmethod
        async def post(url: str, **kwargs: Any) -> Any:
            del url
            budgets.append(kwargs["json"]["max_tokens"])
            return _Reply()

    app = an_api().app
    app.state.settings.ravis_client_credential = "secret"
    app.state.probe_client = _Client()
    asyncio.run(_ask_ravis(app)("ravis/free-api", "s", "brief", FACTS))

    assert budgets and budgets[0] >= 1000, budgets
