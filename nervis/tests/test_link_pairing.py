"""Two computers linking from their screens, with nobody typing a password anywhere.

The owner, 22 September 2026: *"i didn't like that i needed to copy a command in the
terminal… can we link them through the interface?"* They pair the way two devices pair: one
asks, the other allows. The asking computer says on the network what its key is and who it
is asking; the other one's owner presses Allow, where NERVIS is already running as them, and
that writes the key. No password is asked for, sent, or stored at any point.

These tests pin the parts that a later change could quietly break:

- **Allow only ever authorises a computer that is asking now**, and the key it writes is the
  one taken from a fresh search — not from the browser, which could say anything.
- **Asking is visible only while it is being asked**, and stops the moment the link works.
- **The SSH itself is the launcher's**, so these tests replace that one call and assert what
  it was asked to do; a NERVIS with no launcher says so rather than guessing.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from nervis import discovery
from nervis.api import link as link_api
from nervis.app import create_app
from nervis.config import Settings
from nervis.discovery import Announcer, Found

THEIR_KEY = ("ssh-ed25519 "
             "AAAAC3NzaC1lZDI1NTE5AAAAIHZBEWrJ0hYdfrLfiu0Uq1VkKLsH5VoGxD0QhXQWvJ9y")
OUR_KEY = ("ssh-ed25519 "
           "AAAAC3NzaC1lZDI1NTE5AAAAIHZBEWrJ0hYdfrLfiu0Uq1VkKLsH5VoGxD0QhXQWvJ8x")


class FakeLauncher:
    """The launcher, answering as `tools/run.py link … --json` would, and remembering asks."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, ...]] = []
        self.connected = False

    def __call__(self, *arguments: str, timeout: float = 0) -> dict[str, Any]:  # noqa: ARG002 — the real signature
        self.asked.append(arguments)
        named = dict(zip(arguments[1::2], arguments[2::2]))
        answers: dict[str, Any] = {
            "key": lambda: {"ok": True, "public_key": OUR_KEY, "fingerprint": "SHA256:ours"},
            "fingerprint": lambda: {"ok": True,
                                    "fingerprint": f"SHA256:{named['--key'].split()[-1][:6]}"},
            "authorize": lambda: {"ok": True, "already": False, "fingerprint": "SHA256:theirs"},
            "revoke": lambda: {"ok": True, "removed": 1},
            "test": lambda: {"ok": self.connected, "connected": self.connected,
                             "sirvis": self.connected, "nervis": self.connected},
            "save": lambda: {"ok": True},
            "open": lambda: {"ok": True, "answering": True},
            "close": lambda: {"ok": True, "closed": 4242},
        }
        make = answers.get(arguments[0])
        return make() if make else {"ok": False, "detail": f"no such action {arguments[0]}"}


@pytest.fixture()
def launcher(monkeypatch: Any) -> FakeLauncher:
    fake = FakeLauncher()
    monkeypatch.setattr(link_api, "ask_launcher", fake)
    return fake


@pytest.fixture()
def client(tmp_path: Any, monkeypatch: Any) -> Any:
    monkeypatch.setattr(discovery, "tool", lambda: "avahi")
    monkeypatch.setattr(discovery, "own_name", lambda: "Govert")
    monkeypatch.setattr(discovery.getpass, "getuser", lambda: "mathias")
    monkeypatch.setattr(discovery, "accepts_ssh", lambda: True)
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path), _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        made.app.state.announcer = Announcer(spawn=lambda command, **_: _Announcing(command))
        yield made


class _Announcing:
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.code: int | None = None

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.code = -15

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002 — Popen's own signature
        return 0

    def kill(self) -> None:
        self.code = -9


def announcing(client: Any) -> list[str]:
    process = client.app.state.announcer._process
    return list(process.command) if process is not None else []


def thinkpad(**changes: Any) -> Found:
    fields: dict[str, Any] = {"name": "ThinkPadX13G2", "host": "ThinkPadX13G2.local",
                              "port": 22, "user": "mathias", "accepts": False,
                              "key": THEIR_KEY, "want": "Govert"}
    return Found(**{**fields, **changes})


def found(monkeypatch: Any, *computers: Found) -> None:
    monkeypatch.setattr(link_api, "find_computers", lambda: {
        "tool": "avahi", "detail": "", "computers": [one.as_dict() for one in computers]})


@pytest.mark.usefixtures("launcher")
def test_asking_announces_this_computers_key_and_who_it_is_asking(client: Any) -> None:
    answer = client.post("/api/v1/link/request", json={
        "address": "mathias@Govert.local", "name": "Govert", "inbound": True}).json()

    assert answer["ok"] is True
    assert answer["waiting_for"]["name"] == "Govert"
    assert answer["waiting_for"]["fingerprint"] == "SHA256:ours", (
        "shown on this screen, to be compared with the one on the other"
    )
    assert answer["findable"] is True, "a question nobody can hear is not a question"
    said = announcing(client)
    # Encoded, because a TXT value with a space in it is cut in two by `dns-sd` — which is
    # what stopped the first real pairing (22 September 2026).
    assert f"key={quote(OUR_KEY, safe='')}" in said and "want=Govert" in said


@pytest.mark.usefixtures("launcher")
def test_stopping_asking_takes_the_question_off_the_network(client: Any) -> None:
    client.post("/api/v1/link/request", json={"address": "a@b", "name": "Govert"})

    answer = client.post("/api/v1/link/request/cancel").json()

    assert answer["waiting_for"] == {}
    assert not [word for word in announcing(client) if word.startswith("want=")]


@pytest.mark.usefixtures("launcher")
def test_a_computer_asking_is_shown_with_its_fingerprint(client: Any, monkeypatch: Any) -> None:
    found(monkeypatch, thinkpad(), thinkpad(name="SomeoneElse", want="OtherComputer"))

    answer = client.get("/api/v1/link/requests").json()

    assert [one["name"] for one in answer["asking"]] == ["ThinkPadX13G2"], (
        "a computer asking somebody else is not this computer's question to answer"
    )
    assert answer["asking"][0]["fingerprint"].startswith("SHA256:")


def test_allowing_writes_the_key_that_is_being_announced_now(
    client: Any, launcher: FakeLauncher, monkeypatch: Any
) -> None:
    """Not the key the browser sent: what is authorised is what that computer is saying at
    the moment somebody presses Allow."""
    found(monkeypatch, thinkpad())

    answer = client.post("/api/v1/link/approve", json={"name": "ThinkPadX13G2"}).json()

    assert answer["ok"] is True
    authorised = next(one for one in launcher.asked if one[0] == "authorize")
    assert dict(zip(authorised[1::2], authorised[2::2])) == {
        "--key": THEIR_KEY, "--label": "ThinkPadX13G2"}
    allowed = client.get("/api/v1/link/findable").json()["allowed"]
    assert [one["name"] for one in allowed] == ["ThinkPadX13G2"]


def test_allowing_a_computer_that_is_not_asking_is_refused(
    client: Any, launcher: FakeLauncher, monkeypatch: Any
) -> None:
    found(monkeypatch)

    answer = client.post("/api/v1/link/approve", json={"name": "ThinkPadX13G2"}).json()

    assert answer["ok"] is False and "not asking" in answer["detail"]
    assert not [one for one in launcher.asked if one[0] == "authorize"]


def test_waiting_is_answered_with_not_yet_until_the_link_works(
    client: Any, launcher: FakeLauncher
) -> None:
    client.post("/api/v1/link/request", json={"address": "mathias@Govert.local",
                                              "name": "Govert", "inbound": True})

    answer = client.post("/api/v1/link/check").json()

    assert answer == {"ok": True, "waiting": True, "linked": False,
                      "detail": "Govert has not allowed it yet"}
    assert not [one for one in launcher.asked if one[0] in ("save", "open")]


def test_the_moment_it_works_it_is_saved_and_opened(client: Any, launcher: FakeLauncher) -> None:
    """**No "now restart the stack".** Pairing from the screen ends with the link open."""
    client.post("/api/v1/link/request", json={"address": "mathias@Govert.local",
                                              "name": "Govert", "inbound": True})
    launcher.connected = True

    answer = client.post("/api/v1/link/check").json()

    assert answer["linked"] is True and answer["answering"] is True
    saved = next(one for one in launcher.asked if one[0] == "save")
    assert "--address" in saved and "mathias@Govert.local" in saved and "--inbound" in saved
    assert [one[0] for one in launcher.asked].count("open") == 1
    assert client.get("/api/v1/link/findable").json()["waiting_for"] == {}, (
        "the question is answered, so it stops being asked"
    )


def test_stopping_allowing_takes_the_key_back(
    client: Any, launcher: FakeLauncher, monkeypatch: Any
) -> None:
    found(monkeypatch, thinkpad())
    client.post("/api/v1/link/approve", json={"name": "ThinkPadX13G2"})

    answer = client.post("/api/v1/link/revoke", json={"name": "ThinkPadX13G2"}).json()

    assert answer["ok"] is True
    revoked = next(one for one in launcher.asked if one[0] == "revoke")
    assert THEIR_KEY in revoked
    assert client.get("/api/v1/link/findable").json()["allowed"] == []


def test_without_a_launcher_it_says_so_rather_than_guessing(client: Any, monkeypatch: Any) -> None:
    """A NERVIS somebody started by hand has no launcher to do the SSH with."""
    monkeypatch.delenv("NERVIS_LAUNCHER", raising=False)

    answer = client.post("/api/v1/link/request", json={"address": "a@b", "name": "Govert"}).json()

    assert answer["ok"] is False
    assert "not started by the launcher" in answer["detail"]
    assert json.loads(json.dumps(answer))  # it is still an ordinary answer, not an error page


def test_unlinking_closes_the_tunnel_now(client: Any, launcher: FakeLauncher) -> None:
    """A link nobody wants any more stops being open, rather than lasting until the next stop."""
    answer = client.post("/api/v1/link/close").json()

    assert answer["ok"] is True
    assert [one[0] for one in launcher.asked] == ["close"]


def test_the_computer_that_was_dialled_into_says_it_is_linked(
    client: Any, monkeypatch: Any
) -> None:
    """Both ends of a link report one, even though only one end holds an address.

    Found on the Mac with the ThinkPad linked into it: the card read "not linked to anything"
    while its settings were being read through that very tunnel.
    """
    monkeypatch.setattr(link_api, "linked_in", lambda: True)

    answer = client.get("/api/v1/link/findable").json()

    assert answer["linked_in"] is True


def test_with_nothing_dialled_in_it_says_so(client: Any) -> None:
    assert client.get("/api/v1/link/findable").json()["linked_in"] in (True, False)


def test_turning_on_the_way_back_saves_and_reopens_the_link(
    client: Any, launcher: FakeLauncher
) -> None:
    """**A link is one way until somebody asks for both**, and from the other end that looks
    exactly like no link at all — the evening of 22 September 2026, where the ThinkPad's link
    was up and healthy and the Mac could see nothing of it.

    What the tunnel forwards is fixed when it starts, so this closes and opens it.
    """
    # What a real `link save` leaves behind: the launcher writes this row itself, and the
    # fake one cannot, so the test puts the link where the launcher would have.
    client.put("/api/v1/settings/link.peer", json={"value": {
        "enabled": True, "address": "mathias@Govert.local", "inbound": False}})
    launcher.asked.clear()

    answer = client.post("/api/v1/link/both-ways", json={"value": True}).json()

    assert answer["ok"] is True and answer["inbound"] is True
    saved = next(one for one in launcher.asked if one[0] == "save")
    assert "--inbound" in saved and "mathias@Govert.local" in saved
    assert [one[0] for one in launcher.asked if one[0] in ("close", "open")] == ["close", "open"]


def test_the_way_back_cannot_be_set_before_a_link_is_saved(
    client: Any, launcher: FakeLauncher
) -> None:
    answer = client.post("/api/v1/link/both-ways", json={"value": True}).json()

    assert answer["ok"] is False and "no other computer is saved" in answer["detail"]
    assert launcher.asked == []
