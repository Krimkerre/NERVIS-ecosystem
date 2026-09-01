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
    """There is no bulk write, and that is deliberate rather than unfinished.

    A `mark_all_read` would take every unseen note to zero along with the ones
    the user actually saw, which is the failure M21 names outright.
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
