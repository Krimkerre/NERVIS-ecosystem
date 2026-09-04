"""The notification centre (NERVIS.md M21).

M21 makes three promises, and each of them is easy to lose in a refactor
because breaking any one still produces a screen that looks fine:

  1. Every note says *why* it exists.
  2. A note a model produced names the model and what it cost.
  3. Dismissing is per-note, and there is no way to silence a class.

Underneath them is a structural claim: **the announcement and the note are one
event seen twice.** The dashboard speaks and the probe loop writes, so a muted
browser, a closed tab, or a person on another screen all lose the speech and
none of them lose the record. That is what most of this file is about, because
it is the promise a plausible implementation breaks silently.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import app as app_module
from nervis import notifications
from nervis.app import create_app
from nervis.config import Settings
from nervis.registry import RegistryState
from nervis.storage.database import prepare_database


@pytest.fixture()
def database() -> Any:
    return prepare_database(":memory:")


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        yield client


# ── The three promises ──────────────────────────────────────────────────────


def test_a_note_must_say_why_it_exists(database: Any) -> None:
    """§4.1's discipline, applied to notes.

    Refused rather than defaulted, because a placeholder reason is worse than
    none: it satisfies the reader's glance and tells them nothing.
    """
    with pytest.raises(ValueError, match="why it exists"):
        notifications.post(database, kind="idea", title="Something", reason="   ")


def test_a_model_produced_note_names_the_model_and_the_cost(database: Any) -> None:
    """Both, or neither. A cost with no author is the same defect reversed."""
    for kwargs in ({"model": "claude-haiku-4-5"}, {"cost": "$0.01"}):
        with pytest.raises(ValueError, match="model and its cost"):
            notifications.post(
                database, kind="idea", title="t", reason="r", **kwargs
            )
    note = notifications.post(
        database, kind="idea", title="t", reason="r",
        model="claude-haiku-4-5", cost="$0.0012",
    )
    assert note.as_dict()["produced_by"] == {
        "model": "claude-haiku-4-5", "cost": "$0.0012",
    }


def test_nothing_silences_a_class() -> None:
    """The server has no bulk write, and that survives a UI that offers one.

    The screen does have a "mark all read" — it resolves to one request per
    note, over exactly the notes drawn on it. That is a different thing from a
    server operation meaning *everything*, which would also sweep up whatever
    was filed between the page loading and the button being pressed, and would
    give any future caller a way to clear notes nobody has read.

    So the line is not "no bulk action". It is: **every note acted on is named
    by somebody who could see it.** Keeping the server free of a bulk verb is
    what makes that structural rather than a habit of the current UI.
    """
    for name in ("mark_all_read", "dismiss_all", "mute_kind", "silence"):
        assert not hasattr(notifications, name), (
            f"notifications.{name} exists; M21 forbids silencing a class"
        )


def test_a_note_produced_without_a_model_claims_no_author(database: Any) -> None:
    note = notifications.post(
        database, kind="service_state", title="t", reason="r", source="registry"
    )
    assert note.as_dict()["produced_by"] is None


# ── The store ───────────────────────────────────────────────────────────────


def test_unread_counts_neither_read_nor_dismissed(database: Any) -> None:
    a = notifications.post(database, kind="k", title="a", reason="r")
    b = notifications.post(database, kind="k", title="b", reason="r")
    notifications.post(database, kind="k", title="c", reason="r")
    assert notifications.unread_count(database) == 3
    notifications.mark_read(database, a.note_id)
    notifications.dismiss(database, b.note_id)
    assert notifications.unread_count(database) == 1


def test_dismissing_hides_without_deleting(database: Any) -> None:
    """"I dealt with this" and "this never happened" are different claims."""
    note = notifications.post(database, kind="k", title="a", reason="r")
    notifications.dismiss(database, note.note_id)
    assert [n.note_id for n in notifications.recent(database)] == []
    assert [n.note_id for n in notifications.recent(database, include_dismissed=True)] == [
        note.note_id
    ]


def test_only_unread_lists_exactly_what_the_badge_counts(database: Any) -> None:
    """The unread tab and the header badge must not be able to disagree.

    This is why the filter is SQL rather than a `.filter()` in the browser: the
    badge is an unbounded COUNT and the list stops at `limit`, so a screen that
    fetched a page and kept the unread ones would promise nine and show four
    once enough read notes piled up on top.
    """
    waiting = notifications.post(database, kind="k", title="waiting", reason="r")
    seen = notifications.post(database, kind="k", title="seen", reason="r")
    gone = notifications.post(database, kind="k", title="gone", reason="r")
    notifications.mark_read(database, seen.note_id)
    notifications.dismiss(database, gone.note_id)

    listed = [n.note_id for n in notifications.recent(database, only_unread=True)]
    assert listed == [waiting.note_id]
    assert len(listed) == notifications.unread_count(database)


def test_only_unread_excludes_dismissed_even_when_asked_for_them(database: Any) -> None:
    """Unread means unread. A dismissed note is dealt with however it got there.

    `include_dismissed` and `only_unread` can both arrive on one query string,
    and the pair must not add up to "notes nobody read but somebody put away" —
    a slice `unread_count` does not count and no tab asks for.
    """
    gone = notifications.post(database, kind="k", title="gone", reason="r")
    notifications.dismiss(database, gone.note_id)
    assert notifications.recent(database, include_dismissed=True, only_unread=True) == []


def test_only_dismissed_is_not_the_same_as_include_dismissed(database: Any) -> None:
    """"Include" widens the list; "only" narrows it, and the tab wants narrow.

    Caught in the browser: the tab labelled "dismissed" sent
    `?include_dismissed=1` and drew all twenty-four notes, every one of them
    still outstanding.
    """
    kept = notifications.post(database, kind="k", title="kept", reason="r")
    gone = notifications.post(database, kind="k", title="gone", reason="r")
    notifications.dismiss(database, gone.note_id)

    assert [n.note_id for n in notifications.recent(database, only_dismissed=True)] == [
        gone.note_id
    ]
    assert {n.note_id for n in notifications.recent(database, include_dismissed=True)} == {
        kept.note_id,
        gone.note_id,
    }


def test_asking_for_two_slices_at_once_gets_the_narrower(database: Any) -> None:
    """Unread wins, because unread already excludes dismissed."""
    gone = notifications.post(database, kind="k", title="gone", reason="r")
    notifications.dismiss(database, gone.note_id)
    assert notifications.recent(database, only_unread=True, only_dismissed=True) == []


def test_dismissing_twice_is_not_an_error(database: Any) -> None:
    """What a second click on a slow connection looks like."""
    note = notifications.post(database, kind="k", title="a", reason="r")
    assert notifications.dismiss(database, note.note_id)
    assert notifications.dismiss(database, note.note_id)
    assert not notifications.dismiss(database, "nt_nothing")


def test_only_dismissed_notes_are_swept(database: Any) -> None:
    """A fortnight away must not empty the centre.

    An undismissed note is still waiting for somebody however old it is; a
    dismissed one has done its job.
    """
    waiting = notifications.post(database, kind="k", title="waiting", reason="r")
    dealt = notifications.post(database, kind="k", title="dealt with", reason="r")
    notifications.dismiss(database, dealt.note_id)
    with database.connection as connection:
        connection.execute(
            "UPDATE notification SET created_at = datetime('now', '-90 days')"
        )
    assert notifications.prune_dismissed(database) == 1
    kept = [n.note_id for n in notifications.recent(database, include_dismissed=True)]
    assert kept == [waiting.note_id]


# ── The API ─────────────────────────────────────────────────────────────────


def test_the_listing_carries_its_own_count(client: TestClient) -> None:
    """One request, so the badge and the list cannot disagree."""
    database = client.app.state.database  # type: ignore[attr-defined]
    notifications.post(database, kind="k", title="a", reason="r")
    body = client.get("/api/v1/notifications").json()
    assert body["unread"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["reason"] == "r"


def test_acting_on_a_note_that_does_not_exist_is_a_404(client: TestClient) -> None:
    assert client.post("/api/v1/notifications/nt_nothing/read").status_code == 404
    assert client.post("/api/v1/notifications/nt_nothing/dismiss").status_code == 404


def test_reading_is_not_dismissing(client: TestClient) -> None:
    """Reading clears the badge; only dismissing takes it off the list."""
    database = client.app.state.database  # type: ignore[attr-defined]
    note = notifications.post(database, kind="k", title="a", reason="r")
    client.post(f"/api/v1/notifications/{note.note_id}/read")
    body = client.get("/api/v1/notifications").json()
    assert body["unread"] == 0
    assert len(body["items"]) == 1


def test_the_unread_tab_asks_the_endpoint_rather_than_filtering(client: TestClient) -> None:
    """`?unread=1` is what the tab fetches, and it still carries the full count.

    The count stays unbounded on every tab: standing in Dismissed and seeing the
    badge go to nought would be a lie about the other list.
    """
    database = client.app.state.database  # type: ignore[attr-defined]
    seen = notifications.post(database, kind="k", title="seen", reason="r")
    notifications.post(database, kind="k", title="waiting", reason="r")
    notifications.mark_read(database, seen.note_id)

    body = client.get("/api/v1/notifications?unread=1").json()
    assert [n["title"] for n in body["items"]] == ["waiting"]
    assert body["unread"] == 1

    everything = client.get("/api/v1/notifications").json()
    assert len(everything["items"]) == 2
    assert everything["unread"] == 1


def test_the_capability_is_advertised(client: TestClient) -> None:
    """A screen gates on this rather than on NERVIS being reachable."""
    declared = client.get("/ecosystem/capabilities").json()["capabilities"]
    entry = next(c for c in declared if c["id"] == "nervis.notifications")
    assert entry["state"] == "available"
    assert entry["reason"]


# ── One event, seen twice ───────────────────────────────────────────────────


def test_a_state_change_is_filed_by_nervis_not_by_the_browser(client: TestClient) -> None:
    """The record survives a muted voice because the voice never wrote it.

    Driven through `_announce_transitions`, which is the probe loop's own
    function — no browser involved anywhere in this test, which is the point.
    """
    api = client.app  # type: ignore[attr-defined]
    # Past the startup window, or nothing is filed at all — a freshly built test
    # client is by definition a stack that has just come up, which is exactly
    # the period the centre now stays quiet through.
    api.state.probe_started_at = time.monotonic() - api.state.settings.startup_window_seconds - 1
    entry = api.state.registry.all()[0]
    before = {e.key: e.state for e in api.state.registry.all()}
    before[entry.key] = RegistryState.HEALTHY
    app_module._announce_transitions(api, before)

    body = client.get("/api/v1/notifications").json()
    assert body["unread"] == 1
    note = body["items"][0]
    assert entry.declaration.label in note["title"]
    # The reason is the transition, not the destination. "It is degraded" is a
    # status; "it was healthy a moment ago" is why anybody wants to know now.
    assert "from healthy to" in note["reason"]
    assert note["produced_by"] is None


def test_a_first_sighting_is_not_news(client: TestClient) -> None:
    """Startup moves every entry out of `discovering`, and that is a roll call.

    Without this guard NERVIS greets the user with one note per service every
    time it restarts, which is how a notification centre becomes something
    people close without reading.
    """
    assert client.get("/api/v1/notifications").json()["unread"] == 0
    api = client.app  # type: ignore[attr-defined]
    before = {e.key: RegistryState.DISCOVERING for e in api.state.registry.all()}
    app_module._announce_transitions(api, before)
    assert client.get("/api/v1/notifications").json()["unread"] == 0


def test_a_cold_start_does_not_fill_the_centre(client: TestClient) -> None:
    """A launcher starts services in sequence, and the machine is busy doing it.

    Reported after a cold start: a centre full of "has stopped answering", each
    note true for about twenty seconds and worthless by the time anybody read
    it. The existing guards cover a peer NERVIS has never reached; what they
    missed is the *second* transition — seen healthy once, then missed while the
    rest of the stack is still loading, which reads as a real outage.
    """
    api = client.app  # type: ignore[attr-defined]
    api.state.probe_started_at = time.monotonic()          # still coming up
    before = {e.key: RegistryState.HEALTHY for e in api.state.registry.all()}
    app_module._announce_transitions(api, before)
    assert client.get("/api/v1/notifications").json()["unread"] == 0


def test_the_same_outage_is_filed_once_the_stack_has_settled(client: TestClient) -> None:
    """The window closes on the clock, so a real outage is never swallowed.

    The pair matters more than either half: a grace period that never ended
    would be a notification centre that had quietly stopped working.
    """
    api = client.app  # type: ignore[attr-defined]
    window = api.state.settings.startup_window_seconds
    api.state.probe_started_at = time.monotonic() - window - 1
    before = {e.key: RegistryState.HEALTHY for e in api.state.registry.all()}
    app_module._announce_transitions(api, before)
    assert client.get("/api/v1/notifications").json()["unread"] > 0


def test_the_hub_still_records_what_the_centre_stays_quiet_about(client: TestClient) -> None:
    """Nothing is lost, and that is what makes the silence affordable.

    The hub is the record of what NERVIS observed; the centre is the shorter
    list of what is worth telling somebody. Suppressing a note is a decision
    about the second, never about the first.
    """
    api = client.app  # type: ignore[attr-defined]
    api.state.probe_started_at = time.monotonic()
    before = {e.key: RegistryState.HEALTHY for e in api.state.registry.all()}
    app_module._announce_transitions(api, before)
    recorded = api.state.hub.query(event_type="nervis.service.state_changed", latest=True)
    assert recorded, "the hub must hold the transitions the centre withheld"
    assert client.get("/api/v1/notifications").json()["unread"] == 0


def test_an_uninstalled_optional_peer_files_nothing(client: TestClient) -> None:
    """Absent is not broken — the same rule the status bar and the voice use."""
    api = client.app  # type: ignore[attr-defined]
    optional = [e for e in api.state.registry.all() if e.awaiting_first_contact]
    if not optional:
        pytest.skip("this installation declares no optional peer")
    before = {e.key: e.state for e in api.state.registry.all()}
    for entry in optional:
        before[entry.key] = RegistryState.HEALTHY
    app_module._announce_transitions(api, before)
    filed = client.get("/api/v1/notifications").json()["items"]
    for entry in optional:
        assert not any(entry.declaration.label in n["title"] for n in filed)


def test_a_wake_from_sleep_does_not_file_a_note_for_every_service(client: TestClient) -> None:
    """The machine slept; the services never moved.

    Reported as a centre holding four "is back to healthy" notes per wake, on a
    laptop sleeping every fifteen minutes: probing stops with the machine, every
    entry ages into `stale` while nothing is asking it anything, and the first
    sweep after the wake finds all four alive at once.

    The startup window does not cover this. It is keyed to when *this process*
    began probing, and NERVIS never restarted — `probe_started_at` was hours
    old, so the window had long since closed. What the guards missed is that a
    resumed loop and a fresh one are the same situation: a period nobody was
    watching, followed by observations that only look like transitions.
    """
    api = client.app  # type: ignore[attr-defined]
    api.state.probe_started_at = time.monotonic() - 3600  # long settled
    api.state.last_sweep_at = time.time() - 900  # ...and then fifteen minutes asleep

    app_module._reopen_window_after_a_gap(api)

    before = {e.key: RegistryState.STALE for e in api.state.registry.all()}
    app_module._announce_transitions(api, before)
    assert client.get("/api/v1/notifications").json()["unread"] == 0


def test_an_ordinary_tick_leaves_the_window_closed(client: TestClient) -> None:
    """The pair that keeps the fix from being a mute button.

    A guard that reopened the window on every sweep would be a centre that had
    quietly stopped reporting recoveries at all — so the gap has to be measured,
    not assumed, and an ordinary interval must still file the note.
    """
    api = client.app  # type: ignore[attr-defined]
    api.state.probe_started_at = time.monotonic() - 3600
    api.state.last_sweep_at = time.time() - 1  # the loop ran when it said it would

    app_module._reopen_window_after_a_gap(api)

    before = {e.key: RegistryState.STALE for e in api.state.registry.all()}
    app_module._announce_transitions(api, before)
    assert client.get("/api/v1/notifications").json()["unread"] > 0
