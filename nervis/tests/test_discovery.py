"""Finding the owner's other computers, and letting them find this one.

Every service here listens on its own machine only, so another computer's SIRVIS cannot be
scanned for — it has to *announce* itself, over multicast DNS, with the operating system's
own tool: `dns-sd` on macOS, Avahi on Linux. These tests pin the three things that decide
whether the button in Settings is honest:

- **what the tools print is read correctly** — the macOS samples below are real output,
  captured on the owner's Mac on 21 September 2026; the Linux ones follow Avahi's parsable
  format (`avahi-browse -p`) and are proven live separately, on a Linux machine;
- **announcing is off until the owner turns it on**, and turning it off stops it at once;
- **nothing is announced by a NERVIS nobody asked**, including every one built for a test.

Nothing here runs `dns-sd` or Avahi: the process is replaced wherever one would start.
"""

# The tools' output below is kept exactly as they print it, long lines and all: a sample
# reflowed to fit a line limit is no longer the thing the parser has to read.
# ruff: noqa: E501

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis import discovery
from nervis.app import create_app
from nervis.config import Settings
from nervis.discovery import Announcer, Found
from nervis.settings_transfer import EXPORTABLE

# Captured from the owner's Mac, 21 September 2026: a test service announced for a few
# seconds, then browsed and resolved. Note it is listed twice — once per interface.
DNSSD_BROWSE = """Browsing for _nervis._tcp.local.
DATE: ---Mon 21 Sep 2026---
23:00:51.161  ...STARTING...
Timestamp     A/R    Flags  if Domain               Service Type         Instance Name
23:00:51.162  Add        3   1 local.               _nervis._tcp.        nervis-format-check
23:00:51.162  Add        2  12 local.               _nervis._tcp.        nervis-format-check
"""

DNSSD_RESOLVE = """Lookup nervis-format-check._nervis._tcp.local.
DATE: ---Mon 21 Sep 2026---
23:00:53.168  ...STARTING...
23:00:53.169  nervis-format-check._nervis._tcp.local. can be reached at Govert.local.:22 (interface 12) Flags: 1
 user=test ssh=yes
23:00:53.169  nervis-format-check._nervis._tcp.local. can be reached at Govert.local.:22 (interface 12)
 user=test ssh=yes
"""

# Avahi's parsable format: `+` lines as found, `=` lines once resolved, `;` between fields,
# a space in a name escaped as `\032`. One computer, answering over IPv6 and IPv4.
AVAHI_BROWSE = """+;wlan0;IPv6;Mathias\\032ThinkPad;_nervis._tcp;local
+;wlan0;IPv4;Mathias\\032ThinkPad;_nervis._tcp;local
=;wlan0;IPv6;Mathias\\032ThinkPad;_nervis._tcp;local;ThinkPadX13G2.local;fe80::1c2b:3a4d:5e6f:7a8b;22;"ssh=no" "user=mathias"
=;wlan0;IPv4;Mathias\\032ThinkPad;_nervis._tcp;local;ThinkPadX13G2.local;192.168.1.23;22;"ssh=no" "user=mathias"
=;wlan0;IPv4;Govert;_nervis._tcp;local;Govert.local;192.168.1.10;22;"ssh=yes" "user=mathias"
"""


def test_the_macs_browse_lists_each_computer_once() -> None:
    assert discovery.parse_dnssd_browse(DNSSD_BROWSE) == ["nervis-format-check"]


def test_a_name_with_spaces_survives_and_one_that_left_is_dropped() -> None:
    text = DNSSD_BROWSE + (
        "23:00:52.000  Add        2  12 local.               _nervis._tcp.        Mathias's Mac mini\n"
        "23:00:52.500  Rmv        0  12 local.               _nervis._tcp.        nervis-format-check\n"
    )

    assert discovery.parse_dnssd_browse(text) == ["Mathias's Mac mini"]


def test_the_macs_resolve_gives_host_port_and_what_it_announced() -> None:
    assert discovery.parse_dnssd_resolve(DNSSD_RESOLVE) == (
        "Govert.local", 22, {"user": "test", "ssh": "yes"})
    assert discovery.parse_dnssd_resolve("Lookup x\n...STARTING...\n") is None


def test_linux_lists_each_computer_once_by_its_host_name() -> None:
    """The first resolved line per name wins, and the host name is kept rather than the
    address: `fe80::…%wlan0` is not something anybody can type into `ssh`."""
    found = discovery.parse_avahi(AVAHI_BROWSE)

    assert found == [
        Found(name="Mathias ThinkPad", host="ThinkPadX13G2.local", port=22, user="mathias",
              accepts=False),
        Found(name="Govert", host="Govert.local", port=22, user="mathias", accepts=True),
    ]
    assert found[1].address == "mathias@Govert.local"


@pytest.mark.parametrize("using,expected", [
    ("dns-sd", ["dns-sd", "-R", "Govert", "_nervis._tcp", "local", "22",
                "user=mathias", "ssh=yes"]),
    ("avahi", ["avahi-publish", "-s", "Govert", "_nervis._tcp", "22",
               "user=mathias", "ssh=yes"]),
    (None, None),
])
def test_the_announcement_says_only_three_things(using: str | None, expected: Any) -> None:
    """Its name, who to link as, and whether it accepts SSH — nothing about versions, models
    or services, because a network does not need to know them to link."""
    assert discovery.announce_command(using, "Govert", "mathias", True) == expected


def _finding(monkeypatch: Any, found: list[Found], *, using: str = "avahi",
             trouble: str = "") -> dict[str, Any]:
    monkeypatch.setattr(discovery, "tool", lambda: using)
    monkeypatch.setattr(discovery, "own_name", lambda: "Govert")
    monkeypatch.setattr(discovery, "in_wsl", lambda: False)
    return discovery.find_computers({using: lambda: (found, trouble)})


def test_this_computer_is_left_out_of_its_own_list(monkeypatch: Any) -> None:
    me = Found("Govert", "Govert.local", 22, "mathias", True)
    other = Found("ThinkPadX13G2", "ThinkPadX13G2.local", 22, "mathias", False)

    answer = _finding(monkeypatch, [me, other])

    assert [one["name"] for one in answer["computers"]] == ["ThinkPadX13G2"]
    assert answer["computers"][0]["accepts"] is False


def test_finding_nobody_says_what_to_do_on_the_other_computer(monkeypatch: Any) -> None:
    answer = _finding(monkeypatch, [])

    assert answer["computers"] == []
    assert "Let other computers find this one" in answer["detail"]


def test_under_wsl_finding_nobody_says_why_that_may_be(monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "tool", lambda: "avahi")
    monkeypatch.setattr(discovery, "own_name", lambda: "Govert")
    monkeypatch.setattr(discovery, "in_wsl", lambda: True)

    answer = discovery.find_computers({"avahi": lambda: ([], "")})

    assert "WSL" in answer["detail"] and "typing the address still works" in answer["detail"]


def test_a_linux_without_avahi_is_told_which_package_for_its_distribution(
    monkeypatch: Any
) -> None:
    monkeypatch.setattr(discovery, "tool", lambda: None)
    monkeypatch.setattr(discovery.platform, "system", lambda: "Linux")

    answer = discovery.find_computers()

    assert answer["tool"] is None
    for package in ("avahi-utils", "pacman -S avahi", "avahi-tools", "avahi-daemon"):
        assert package in answer["detail"]


def test_avahis_own_refusal_is_passed_on(monkeypatch: Any) -> None:
    """"Daemon not running" is the usual one, and the fix is one command — so say it."""
    class _Done:
        returncode = 1
        stdout = ""
        stderr = "Failed to create client object: Daemon not running\n"

    monkeypatch.setattr(discovery.subprocess, "run", lambda *_, **__: _Done())

    found, trouble = discovery._find_with_avahi()

    assert found == []
    assert "Daemon not running" in trouble and "enable --now avahi-daemon" in trouble


class FakeProcess:
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.code: int | None = None

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.code = -15

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002 — Popen's own signature
        return self.code or 0

    def kill(self) -> None:
        self.code = -9


@pytest.fixture()
def client(tmp_path: Any, monkeypatch: Any) -> Any:
    started: list[FakeProcess] = []

    def spawn(command: list[str], **_: object) -> FakeProcess:
        started.append(FakeProcess(command))
        return started[-1]

    monkeypatch.setattr(discovery, "tool", lambda: "avahi")
    monkeypatch.setattr(discovery, "own_name", lambda: "ThinkPadX13G2")
    monkeypatch.setattr(discovery.getpass, "getuser", lambda: "mathias")
    monkeypatch.setattr(discovery, "accepts_ssh", lambda: False)
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        made.app.state.announcer = Announcer(spawn=spawn)
        made.started = started  # type: ignore[attr-defined]
        yield made


def test_a_nervis_nobody_asked_announces_nothing(tmp_path: Any, monkeypatch: Any) -> None:
    """Including every NERVIS built for a test: the setting is off, so nothing starts."""
    def _never(*_: object, **__: object) -> None:
        raise AssertionError("announced without being asked")

    monkeypatch.setattr(discovery.subprocess, "Popen", _never)
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"), workspace_path=str(tmp_path),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as made:
        state = made.get("/api/v1/link/findable").json()

    assert state["findable"] is False and state["announcing"] is False


def test_turning_it_on_announces_now_and_off_stops_it_now(client: Any) -> None:
    on = client.put("/api/v1/link/findable", json={"value": True}).json()

    assert on["findable"] is True and on["announcing"] is True
    (process,) = client.started
    assert process.command == ["avahi-publish", "-s", "ThinkPadX13G2", "_nervis._tcp", "22",
                               "user=mathias", "ssh=no"]

    off = client.put("/api/v1/link/findable", json={"value": False}).json()

    assert off["findable"] is False and off["announcing"] is False
    assert process.code is not None, "turned off means the announcement stopped, not paused"


def test_turning_it_on_twice_announces_once(client: Any) -> None:
    client.put("/api/v1/link/findable", json={"value": True})
    client.put("/api/v1/link/findable", json={"value": True})

    assert len(client.started) == 1


def test_the_answer_is_remembered_and_kept_off_the_export(client: Any) -> None:
    """Whether a machine announces itself belongs to that machine and its network, so it is
    stored here and never carried to another computer by a settings pull or a backup."""
    client.put("/api/v1/link/findable", json={"value": True})

    assert client.get("/api/v1/link/findable").json()["findable"] is True
    assert "link.findable" not in EXPORTABLE
    assert "link.findable" not in client.get("/api/v1/settings/export").json()["settings"]


def test_anything_but_true_or_false_is_refused(client: Any) -> None:
    response = client.put("/api/v1/link/findable", json={"value": "yes"})

    assert response.status_code >= 400
    assert client.started == []


def test_the_discover_route_answers_with_the_finders_list(client: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr("nervis.api.link.find_computers", lambda: {
        "tool": "avahi", "detail": "",
        "computers": [Found("Govert", "Govert.local", 22, "mathias", True).as_dict()]})

    answer = client.get("/api/v1/link/discover").json()

    assert answer["computers"][0]["address"] == "mathias@Govert.local"
