"""Bringing settings over from the owner's other computer, through the link.

The link (`tools/run.py`, 20 September 2026) forwards the other machine's NERVIS to a
loopback port here. This is what NERVIS does with it: read that machine's exported settings,
show what would change, and apply them through the same allowlist a saved file goes through.

**A pull, not a sync**, and these tests pin the parts of that decision that a later
"improvement" would quietly undo: nothing is applied without being asked for, nothing is
removed, a machine that is asleep is an ordinary answer rather than an error, and what
crosses is still only what `EXPORTABLE` allows — the link must not become a wider door than
the export file ever was.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings
from nervis.peers import computer
from nervis.settings_transfer import FORMAT, FORMAT_VERSION
from nervis.storage import prepare_database

RUN_PY = Path(__file__).resolve().parents[2] / "tools" / "run.py"


@pytest.fixture()
def client(tmp_path: Any) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        yield made


def put(client: Any, key: str, value: Any) -> None:
    database = client.app.state.database
    with database.connection as connection:
        connection.execute(
            "INSERT INTO setting (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def link_to(client: Any, address: str = "me@thinkpad", enabled: bool = True) -> None:
    put(client, "link.peer", {"enabled": enabled, "address": address, "inbound": False})


def peer_holding(settings: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """What the other computer's `/export` answers with, in its own words.

    The format name comes from the code rather than being spelled here: a fixture that hard-
    codes it keeps passing after a rename, while the real pull stops working.
    """
    return ({"format": FORMAT, "version": FORMAT_VERSION,
             "exported_at": "2026-09-20T11:00:00Z", "settings": settings}, "")


def test_the_port_is_the_one_the_launcher_forwards() -> None:
    """The two numbers that have to agree, checked rather than trusted.

    NERVIS reads the other computer at a port `tools/run.py` opens. They are declared in two
    files because a service reading the launcher would be worse — so this fails the moment
    they stop matching, which is the only way that mistake is ever noticed.
    """
    launcher = RUN_PY.read_text(encoding="utf-8")

    for name, here in (("LINK_LOCAL_NERVIS_PORT", computer.LINK_NERVIS_PORT),
                       ("LINK_BACK_NERVIS_PORT", computer.LINK_BACK_NERVIS_PORT)):
        declared = re.search(rf"^{name} = (\d+)$", launcher, re.MULTILINE)
        assert declared is not None, f"the launcher no longer declares {name}"
        assert int(declared.group(1)) == here


def test_with_no_computer_linked_it_says_so_in_words(client: Any, monkeypatch: Any) -> None:
    """The ordinary state of most machines: nothing answers at either end of the tunnel."""
    monkeypatch.setattr(computer, "peer_settings", lambda *_, **__: (None, "nothing answered"))
    monkeypatch.setattr("nervis.api.settings_transfer.peer_settings",
                        lambda *_, **__: (None, "nothing answered"))

    answer = client.get("/api/v1/settings/peer").json()

    assert answer["linked"] is False and answer["reachable"] is False
    assert answer["changes"] == []
    assert "Another computer" in answer["detail"] or "link add" in answer["detail"]


def test_the_machine_that_was_dialled_can_pull_too(client: Any, monkeypatch: Any) -> None:
    """A link has two ends, and only the dialling one has an address in its settings.

    The Mac accepts rather than dials, so a pull offered only where `link.peer` is set would be
    missing from exactly the computer it is used from most. What makes a pull possible is the
    other machine answering, not this machine holding its address.
    """
    monkeypatch.setattr("nervis.api.settings_transfer.peer_settings",
                        lambda *_, **__: peer_holding({"chat.mode": "build"}))

    answer = client.get("/api/v1/settings/peer").json()

    assert answer["linked"] is True and answer["reachable"] is True
    assert answer["address"] == "the computer that linked to this one"
    assert [change["key"] for change in answer["changes"]] == ["chat.mode"]


def test_both_ends_of_the_tunnel_are_tried(monkeypatch: Any) -> None:
    """Whichever port carries the link, the answer is the same.

    The dialling machine has the other's NERVIS on its own forward; the machine that accepted
    has it on the one opened backwards. Neither knows which it is, so both are asked.
    """
    asked: list[str] = []

    class _Answer:
        status_code = 200

        @staticmethod
        def json() -> Any:
            return {"format": FORMAT, "version": FORMAT_VERSION, "settings": {"chat.mode": "ask"}}

    def _get(url: str, **_: object) -> Any:
        asked.append(url)
        if str(computer.LINK_BACK_NERVIS_PORT) not in url:
            raise httpx.ConnectError("nothing is listening on this one")
        return _Answer()

    monkeypatch.setattr(computer.httpx, "get", _get)

    body, trouble = computer.peer_settings()

    assert trouble == "" and body is not None
    assert len(asked) == 2 and str(computer.LINK_NERVIS_PORT) in asked[0], (
        "this machine's own forward first — it is the one it opened itself"
    )


def test_a_sleeping_computer_is_an_answer_not_an_error(client: Any, monkeypatch: Any) -> None:
    link_to(client)
    monkeypatch.setattr(computer.httpx, "get",
                        lambda *_, **__: (_ for _ in ()).throw(httpx.ConnectError("refused")))

    response = client.get("/api/v1/settings/peer")

    assert response.status_code == 200, "asleep is not a failure of this machine"
    answer = response.json()
    assert answer["linked"] is True and answer["reachable"] is False
    assert "not open" in answer["detail"] or "not started" in answer["detail"]


def test_the_preview_shows_what_would_change_and_counts_the_rest(
    client: Any, monkeypatch: Any
) -> None:
    link_to(client)
    put(client, "chat.mode", "ask")            # different there
    put(client, "recall.enabled", True)        # the same there
    monkeypatch.setattr("nervis.api.settings_transfer.peer_settings",
                        lambda *_, **__: peer_holding({"chat.mode": "build",
                                                      "recall.enabled": True,
                                                      "user.display_name": "Mathias"}))

    answer = client.get("/api/v1/settings/peer").json()

    assert answer["reachable"] is True
    assert answer["unchanged"] == 1, "a preview that lists what already agrees hides the rest"
    by_key = {change["key"]: change for change in answer["changes"]}
    assert by_key["chat.mode"] == {"key": "chat.mode", "ours": "ask", "theirs": "build",
                                   "state": "different"}
    assert by_key["user.display_name"]["state"] == "new"
    assert by_key["user.display_name"]["ours"] is None


def test_a_setting_only_this_machine_has_is_not_a_change(client: Any, monkeypatch: Any) -> None:
    """A pull never removes anything, so what is only here is not something it would resolve."""
    link_to(client)
    put(client, "chat.mode", "ask")
    monkeypatch.setattr("nervis.api.settings_transfer.peer_settings",
                        lambda *_, **__: peer_holding({}))

    answer = client.get("/api/v1/settings/peer").json()

    assert answer["changes"] == []


def test_applying_takes_what_the_other_computer_holds_now(client: Any, monkeypatch: Any) -> None:
    """Applied from a fresh read, not from a body the page has been holding."""
    link_to(client)
    put(client, "chat.mode", "ask")
    monkeypatch.setattr("nervis.api.settings_transfer.peer_settings",
                        lambda *_, **__: peer_holding({"chat.mode": "build"}))

    outcome = client.post("/api/v1/settings/peer", json={"settings": {"chat.mode": "nonsense"}})

    assert outcome.status_code == 200, outcome.text
    assert outcome.json()["applied"] == ["chat.mode"]
    # Read back the way anything else reads a setting, rather than decoding the stored text
    # here: strings are stored as themselves and everything else as JSON (`_encoded`), and a
    # test that knew that would be pinning the storage detail instead of the behaviour.
    held = client.get("/api/v1/settings").json()["items"]
    assert held["chat.mode"] == "build", (
        "what a caller posted must not decide what is applied — the other computer does"
    )


def test_the_link_is_not_a_wider_door_than_the_export_file(client: Any, monkeypatch: Any) -> None:
    """A peer naming a key outside the allowlist has it skipped and named, never applied.

    The far machine is the owner's, and this still holds: one allowlist, enforced in one
    place, whether the settings arrive in a file somebody picked or over a tunnel.
    """
    link_to(client)
    monkeypatch.setattr("nervis.api.settings_transfer.peer_settings",
                        lambda *_, **__: peer_holding({"chat.mode": "build",
                                                      "files.share": {"url": "smb://theirs"},
                                                      "link.peer": {"address": "somewhere"}}))

    outcome = client.post("/api/v1/settings/peer").json()

    assert outcome["applied"] == ["chat.mode"]
    assert {skipped["key"] for skipped in outcome["skipped"]} == {"files.share", "link.peer"}
    assert client.app.state.database.connection.execute(
        "SELECT value FROM setting WHERE key = 'files.share'").fetchone() is None


def test_applying_with_no_computer_linked_writes_nothing(client: Any) -> None:
    outcome = client.post("/api/v1/settings/peer").json()

    assert outcome["applied"] == [] and "no other computer" in outcome["detail"]


@pytest.mark.parametrize("status,payload,expected", [
    (200, ["not", "settings"], "not settings"),
    (200, {"format": "nervis-settings"}, "not settings"),
    (403, {"settings": {}}, "refused"),
])
def test_a_peer_that_answers_with_something_else_is_refused_in_words(
    monkeypatch: Any, status: int, payload: Any, expected: str
) -> None:
    """Whatever comes back, the caller gets a sentence — never an exception, never a guess.

    The far end is another whole stack, possibly a different version of it, and the honest
    outcomes are "it is not answering", "it refused" and "that was not settings".
    """
    class _Answer:
        status_code = status

        @staticmethod
        def json() -> Any:
            return payload

    monkeypatch.setattr(computer.httpx, "get", lambda *_, **__: _Answer())

    body, trouble = computer.peer_settings()

    assert body is None
    assert expected in trouble


def test_the_stored_link_row_reads_as_off_when_it_is_nonsense(tmp_path: Any) -> None:
    """A hand-edited or half-written row must read as "no link", never as a link to nowhere."""
    database = prepare_database(str(tmp_path / "nervis.db"))
    with database.connection as connection:
        connection.execute("INSERT INTO setting (key, value) VALUES ('link.peer', 'nonsense')")

    assert computer.linked_peer(database) == {"enabled": False, "address": "", "inbound": False}
