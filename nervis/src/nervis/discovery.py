"""Finding the owner's other computers on the local network, and letting them find this one.

**Why a computer has to announce itself.** Every service in this ecosystem listens on this
machine only (§15.1), so there is nothing on the network to scan for — a SIRVIS on another
computer cannot be seen from here, by design. What *can* be seen is an announcement: a
computer that says, over multicast DNS, "NERVIS is here, and you can link to me over SSH".
That is the whole mechanism, and it is the same one printers and AirPlay speakers use.

**Both halves use the operating system's own tool, not a Python package.** macOS ships
`dns-sd`; Linux has Avahi (`avahi-browse`, `avahi-publish`), which every mainstream desktop
distribution runs already and which `install.sh` adds where it is missing. The alternative
was a new dependency that reimplements mDNS beside the daemon that already owns it.

**Announcing is off until the owner turns it on** (`link.findable`), in the spirit of their
rule for the link itself: "users should have a say". An announcement opens no port — it
names port 22, which is SSH's, and SSH is only there if the owner switched it on — but it
does tell the network this machine exists, and that is theirs to decide. *Looking* is only
ever done when somebody presses the button, and changes nothing.

What is announced is deliberately little: the computer's name, the user name to link as,
and whether it accepts SSH at all (`ssh=yes|no`), so a computer that can only dial out is
shown as such rather than offered as something to dial.
"""

from __future__ import annotations

import getpass
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

#: The service type every NERVIS announces and looks for. Its own, rather than `_ssh._tcp`,
#: so the list holds computers running NERVIS and not every machine with Remote Login on.
SERVICE_TYPE = "_nervis._tcp"

#: SSH's port, which is what an announcement points at: SSH is how two computers link.
SSH_PORT = 22

#: How long to listen for announcements, and to wait for each one's details. A multicast
#: answer from the same network arrives in milliseconds; these are for the slow Wi-Fi case,
#: and short enough that the button still feels like a button.
BROWSE_SECONDS = 2.5
RESOLVE_SECONDS = 1.5
AVAHI_TIMEOUT_SECONDS = 8.0

#: What to say on a Linux machine without Avahi's tools, per distribution family.
AVAHI_MISSING = (
    "finding computers on Linux needs Avahi — Debian/Ubuntu: sudo apt install avahi-utils; "
    "Arch/CachyOS: sudo pacman -S avahi; Fedora: sudo dnf install avahi-tools; then "
    "sudo systemctl enable --now avahi-daemon (or run ./install.sh again, which does both)"
)

_REACHED = re.compile(r"can be reached at (?P<host>\S+?)\.?:(?P<port>\d+)")


@dataclass(frozen=True)
class Found:
    """One computer that announced itself, in the words a screen needs.

    `key` is its link key, which is public and is what the *other* computer authorises when
    somebody presses Allow; `want` is the name of the computer it is asking to link to, set
    only while somebody there is waiting for an answer. Both are absent from an ordinary
    announcement — a computer that is merely findable asks for nothing.
    """

    name: str
    host: str
    port: int
    user: str
    accepts: bool
    key: str = ""
    want: str = ""

    @property
    def address(self) -> str:
        """What `tools/run.py link add` takes: `user@host`, or the host alone."""
        return f"{self.user}@{self.host}" if self.user else self.host

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "host": self.host, "port": self.port, "user": self.user,
                "accepts": self.accepts, "address": self.address, "key": self.key,
                "want": self.want}


def tool() -> str | None:
    """Which mDNS tool this machine has: `dns-sd`, `avahi`, or None."""
    if shutil.which("dns-sd"):
        return "dns-sd"
    if shutil.which("avahi-browse") and shutil.which("avahi-publish"):
        return "avahi"
    return None


def own_name() -> str:
    """This computer's name as it announces itself: the host name without its domain."""
    return platform.node().split(".")[0] or "this computer"


def accepts_ssh() -> bool:
    """Whether something answers on this machine's SSH port — Remote Login, or sshd.

    Asked once, when the announcement starts. A computer whose SSH is switched on later
    says `ssh=no` until the announcement is restarted, which the switch on the card does.
    """
    try:
        with socket.create_connection(("127.0.0.1", SSH_PORT), timeout=0.3):
            return True
    except OSError:
        return False


def in_wsl() -> bool:
    """Windows' Subsystem for Linux, where multicast usually does not reach the real network."""
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").lower()
    except OSError:
        return False


# ── Reading what the tools print ─────────────────────────────────────────────


def _pairs(items: list[str]) -> dict[str, str]:
    """TXT record entries (`user=mathias`) as a mapping; anything without `=` is ignored."""
    return dict(item.split("=", 1) for item in items if "=" in item)


def parse_dnssd_browse(text: str) -> list[str]:
    """Instance names from `dns-sd -B`, once each, in the order they arrived.

    One computer is listed once per network interface it answered on — measured on this Mac,
    21 September 2026, where a test announcement appeared on interfaces 1 and 12 — so names
    are de-duplicated. A `Rmv` line means it left while we were listening. The name is the
    last column and may contain spaces, which is why the split stops at six.
    """
    names: list[str] = []
    for line in text.splitlines():
        parts = line.split(None, 6)
        if len(parts) != 7 or not parts[5].startswith(SERVICE_TYPE):
            continue
        name = parts[6].strip()
        if parts[1] == "Add" and name not in names:
            names.append(name)
        elif parts[1] == "Rmv" and name in names:
            names.remove(name)
    return names


def parse_dnssd_resolve(text: str) -> tuple[str, int, dict[str, str]] | None:
    """(host, port, TXT) from `dns-sd -L`, or None if it never said where the service is.

    The address comes on one line (`… can be reached at Govert.local.:22 (interface 12)`) and
    the TXT record on the next, indented by one space.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        reached = _REACHED.search(line)
        if not reached:
            continue
        following = lines[index + 1] if index + 1 < len(lines) else ""
        txt = following.split() if following.startswith(" ") else []
        return reached["host"], int(reached["port"]), _pairs(txt)
    return None


def _unescape_avahi(value: str) -> str:
    """Undo `avahi-browse -p`'s escaping: `\\032` for a space, `\\.` for a literal dot."""
    value = re.sub(r"\\(\d{3})", lambda match: chr(int(match[1])), value)
    return re.sub(r"\\(.)", r"\1", value)


def parse_avahi(text: str) -> list[Found]:
    """Computers from `avahi-browse -rtp`, once each.

    Resolved entries are the lines starting `=`, with `;` between fields: event, interface,
    protocol, name, type, domain, host, address, port, TXT — the TXT as quoted strings. One
    computer appears once per interface and protocol, so the first line per name wins; the
    host name is used rather than the address, because an IPv6 link-local address with its
    interface suffix is no use to type into `ssh`.
    """
    found: dict[str, Found] = {}
    for line in text.splitlines():
        fields = line.split(";")
        if len(fields) < 10 or fields[0] != "=" or fields[4] != SERVICE_TYPE:
            continue
        name = _unescape_avahi(fields[3])
        if name in found:
            continue
        txt = _pairs(re.findall(r'"([^"]*)"', ";".join(fields[9:])))
        found[name] = Found(name=name, host=_unescape_avahi(fields[6]), port=int(fields[8]),
                            user=txt.get("user", ""), accepts=txt.get("ssh") == "yes",
                            key=txt.get("key", ""), want=txt.get("want", ""))
    return list(found.values())


# ── Looking ──────────────────────────────────────────────────────────────────


def _listen(argv: list[str], seconds: float) -> str:
    """Run a tool that never exits on its own for `seconds`, and return what it printed."""
    try:
        running = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
    except OSError:
        return ""
    time.sleep(seconds)
    running.terminate()
    try:
        printed, _ = running.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        running.kill()
        printed, _ = running.communicate()
    return printed or ""


def _find_with_dnssd() -> list[Found]:
    names = parse_dnssd_browse(_listen(["dns-sd", "-B", SERVICE_TYPE, "local."], BROWSE_SECONDS))

    def resolve(name: str) -> Found | None:
        where = parse_dnssd_resolve(
            _listen(["dns-sd", "-L", name, SERVICE_TYPE, "local."], RESOLVE_SECONDS))
        if where is None:
            return None
        host, port, txt = where
        return Found(name=name, host=host, port=port, user=txt.get("user", ""),
                     accepts=txt.get("ssh") == "yes", key=txt.get("key", ""),
                     want=txt.get("want", ""))

    # In parallel: each resolve waits its full window, and five computers one after another
    # would turn a button into a seven-second pause.
    with ThreadPoolExecutor(max_workers=max(1, min(len(names), 8))) as pool:
        return [one for one in pool.map(resolve, names) if one is not None]


def _find_with_avahi() -> tuple[list[Found], str]:
    try:
        done = subprocess.run(["avahi-browse", "-rtp", SERVICE_TYPE], capture_output=True,
                              text=True, timeout=AVAHI_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as trouble:
        return [], f"avahi-browse did not finish: {trouble}"
    if done.returncode != 0:
        # The usual one is "Failed to create client object: Daemon not running".
        said = (done.stderr or done.stdout).strip().splitlines()
        return [], (f"avahi could not look: {said[-1] if said else 'no reason given'} — "
                    "sudo systemctl enable --now avahi-daemon")
    return parse_avahi(done.stdout), ""


def find_computers(
    finders: dict[str, Callable[[], tuple[list[Found], str]]] | None = None,
) -> dict[str, Any]:
    """Every other computer on this network announcing NERVIS, and a sentence if none.

    This machine's own announcement is left out: it answers its own question, and a list
    whose first entry is "you" is a list somebody has to read past.
    """
    finders = finders or {"dns-sd": lambda: (_find_with_dnssd(), ""), "avahi": _find_with_avahi}
    using = tool()
    if using is None:
        detail = ("this computer has no mDNS tool to look with" if platform.system() == "Darwin"
                  else AVAHI_MISSING)
        return {"tool": None, "computers": [], "detail": detail}
    found, trouble = finders[using]()
    others = [one for one in found if one.name != own_name()]
    detail = trouble
    if not others and not trouble:
        detail = ("no other computer on this network is announcing NERVIS — on the other "
                  "computer, turn on Settings → Another computer → "
                  "Let other computers find this one")
        if in_wsl():
            detail += (". Under WSL, the network Linux sees is usually a private one inside "
                       "Windows, so announcements from real computers may not reach it; linking "
                       "by typing the address still works")
    return {"tool": using, "computers": [one.as_dict() for one in others], "detail": detail}


# ── Announcing ───────────────────────────────────────────────────────────────


def announce_command(using: str | None, name: str, user: str, accepts: bool,
                     extra: Mapping[str, str] | None = None) -> list[str] | None:
    """The command that keeps this computer's announcement up for as long as it runs.

    `extra` is what pairing adds: `key`, this computer's public link key, and `want`, the
    name of the computer it is asking to link to. Both are dropped when empty, so an
    announcement says the least it can — a computer that is only findable carries neither.
    """
    txt = [f"user={user}", f"ssh={'yes' if accepts else 'no'}"]
    txt += [f"{name_}={value}" for name_, value in sorted((extra or {}).items()) if value]
    if using == "dns-sd":
        return ["dns-sd", "-R", name, SERVICE_TYPE, "local", str(SSH_PORT), *txt]
    if using == "avahi":
        return ["avahi-publish", "-s", name, SERVICE_TYPE, str(SSH_PORT), *txt]
    return None


class Announcer:
    """The one process that announces this computer, started and stopped by the setting.

    **Owned by NERVIS, not the launcher**, so the switch on the card takes effect when it is
    pressed rather than at the next start: announcing is a thing somebody turns on to be
    found *now*, from the other computer they are sitting at. NERVIS stops it on its way down
    (`app._lifespan`), and the launcher's `stop` asks NERVIS to go down gracefully.
    """

    def __init__(self, spawn: Callable[..., Any] = subprocess.Popen) -> None:
        self._spawn = spawn
        self._process: Any = None
        self._saying: dict[str, str] = {}

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def sync(self, wanted: bool, extra: Mapping[str, str] | None = None) -> str:
        """Make the announcement match the setting. Returns a sentence when it cannot.

        **An announcement that has to say something new is restarted**, because what it says
        is fixed when it starts: pressing *Link to this computer* has to reach the other
        computer's screen within seconds, not at the next restart.
        """
        saying = {key: value for key, value in (extra or {}).items() if value}
        if not wanted:
            self.stop()
            self._saying = {}
            return ""
        if self.running and saying == self._saying:
            return ""
        self.stop()
        self._saying = saying
        command = announce_command(tool(), own_name(), getpass.getuser(), accepts_ssh(), saying)
        if command is None:
            return ("this computer has no mDNS tool to announce with" if platform.system() ==
                    "Darwin" else AVAHI_MISSING)
        try:
            self._process = self._spawn(command, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        except OSError as trouble:
            return f"could not start the announcement: {trouble}"
        logger.info("announcing this computer on the local network as %s", command[2])
        return ""

    def stop(self) -> None:
        if self._process is None:
            return
        process, self._process = self._process, None
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
