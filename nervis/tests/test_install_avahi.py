"""The installer's Avahi step: what finding other computers needs on Linux, and nothing on a Mac.

Settings → Another computer finds other computers with multicast DNS. macOS has it built in;
Linux has Avahi, whose command-line tools are a separate package on Debian, Ubuntu and Fedora,
and whose daemon is what actually talks to the network. This pins that each package manager is
asked for the right names, that the daemon is started only where systemd can start it, and that
a dry run changes nothing.

The step is lifted out of `install.sh` and run by bash with stand-ins for the installer's own
helpers, the way `test_install_code_server.py` does it — nothing is installed and no service is
touched. The one path the step reads from the machine, `/run/systemd/system`, is pointed at a
folder this test makes or leaves out.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[2] / "install.sh"

STUBS = """
step() { echo "step: $1"; }
say() { echo "say: $1"; }
good() { echo "good: $1"; }
skipped() { echo "skipped: $1"; }
later() { echo "later: $1"; }
need_root() { :; }
SUDO=(sudo)
run() { echo "run: $*"; }
install_packages() { echo "install_packages: $*"; }
have() { [ "$1" = systemctl ] && [ "${HAS_SYSTEMCTL:-1}" = 1 ]; }
systemctl() { [ "$1" = is-active ] && [ "${AVAHI_ACTIVE:-0}" = 1 ]; }
"""


def _step(systemd: Path) -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    block = re.search(r'^if \[ "\$OS" != macos \]; then\n  step "Avahi.*?^fi$', text,
                      re.MULTILINE | re.DOTALL)
    assert block, "install.sh no longer has the Avahi step"
    return block.group(0).replace("/run/systemd/system", str(systemd))


def installing(tmp_path: Path, *, system: str = "linux", package_manager: str = "apt",
               systemd: bool = True, active: bool = False, dry_run: bool = False) -> str:
    """What the step printed, plus anything it sent to the install log."""
    (tmp_path / ".run").mkdir(exist_ok=True)
    systemd_dir = tmp_path / "systemd"
    if systemd:
        systemd_dir.mkdir(exist_ok=True)
    script = "\n".join([
        STUBS, f"OS={system}", f"PM={package_manager}", f"DRY_RUN={int(dry_run)}",
        f"REPO={tmp_path}", f"AVAHI_ACTIVE={int(active)}", _step(systemd_dir),
    ])
    ran = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    log = tmp_path / ".run" / "install.log"
    return ran.stdout + ran.stderr + (log.read_text() if log.exists() else "")


def test_a_mac_skips_it_entirely(tmp_path: Path) -> None:
    assert installing(tmp_path, system="macos", package_manager="brew") == ""


@pytest.mark.parametrize(("package_manager", "packages"), [
    ("apt", "avahi-daemon avahi-utils"),   # Debian and Ubuntu split the tools out
    ("dnf", "avahi avahi-tools"),          # so does Fedora, under another name
    ("pacman", "avahi"),                   # Arch and CachyOS ship them together
])
def test_each_package_manager_is_asked_for_its_own_names(
    tmp_path: Path, package_manager: str, packages: str
) -> None:
    plan = installing(tmp_path, package_manager=package_manager, active=True)

    assert f"install_packages: Avahi and its tools {packages}" in plan, plan


def test_a_stopped_daemon_is_enabled_and_started(tmp_path: Path) -> None:
    plan = installing(tmp_path)

    assert "run: sudo systemctl enable --now avahi-daemon" in plan, plan


def test_a_running_daemon_is_left_alone(tmp_path: Path) -> None:
    plan = installing(tmp_path, active=True)

    assert "good: Avahi: the daemon is running" in plan
    assert "enable --now" not in plan


def test_without_systemd_it_says_so_instead_of_trying(tmp_path: Path) -> None:
    """WSL without systemd: there is nothing to start the daemon with, and pretending would
    leave a "done" line over a search that cannot work."""
    plan = installing(tmp_path, systemd=False)

    assert "skipped: Avahi's daemon: no systemd here" in plan
    assert "systemctl" not in plan.replace("no systemd", "")


def test_a_dry_run_changes_nothing(tmp_path: Path) -> None:
    plan = installing(tmp_path, dry_run=True)

    assert "would enable and start avahi-daemon" in plan
    assert "run:" not in plan
