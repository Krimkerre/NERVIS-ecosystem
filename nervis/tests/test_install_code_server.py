"""Which code-server the installer puts on each system (`install.sh`; 20 September 2026).

Homebrew's code-server stopped at 4.112.0 — it uses a non-FOSS dependency from 4.113.0, and the
formula is disabled on 11 April 2027 — so a fresh Mac was getting an old editor, and a different
one from the release the owner runs (4.137.0 here) and Clarvis is graded against. macOS now takes
the standalone release, as Arch already did.

The step itself is lifted out of the installer and run by bash with stand-ins for its helpers, so
what is pinned is what the script does on each system rather than what its text says.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[2] / "install.sh"
STANDALONE = "--method standalone"

#: Every helper the step calls, answering in a way the assertions can read back.
STUBS = """
step() { :; }; say() { :; }; warn() { :; }; good() { printf 'good: %s\\n' "$1"; }
skipped() { printf 'skipped: %s\\n' "$1"; }; later() { printf 'later: %s\\n' "$1"; }
fail() { printf 'fail: %s\\n' "$1"; exit 1; }
run() { printf 'would run: %s\\n' "$*"; }
install_packages() { shift; printf 'package install: %s\\n' "$*"; }
have() { case "$1" in code-server) [ "${CODE_SERVER_THERE:-0}" = 1 ];;
  brew) [ "${OS}" = macos ];; *) return 0;; esac; }
brew() { [ "${BREW_HAS_CODE_SERVER:-0}" = 1 ]; }
"""


def _step() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    block = re.search(r'^CODE_SERVER_CONFIG=.*?^fi$', text, re.MULTILINE | re.DOTALL)
    assert block, "install.sh no longer has the code-server step"
    return block.group(0)


def installing(system: str, *, package_manager: str = "brew", installed: bool = False,
               brew_has_it: bool = False) -> str:
    """What the step does on one system, under --dry-run so nothing is fetched."""
    script = "\n".join([
        STUBS, f'OS={system}', f'PM={package_manager}', 'DRY_RUN=1', 'WANT_CODE_SERVER=1',
        f'CODE_SERVER_THERE={int(installed)}', f'BREW_HAS_CODE_SERVER={int(brew_has_it)}',
        'code-server() { echo 4.137.0; }', _step(),
    ])
    ran = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False,
                         env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})
    return ran.stdout + ran.stderr


@pytest.mark.parametrize("package_manager", ["brew", "apt"])
def test_a_mac_gets_the_standalone_release_and_never_homebrews_deprecated_one(
    package_manager: str
) -> None:
    plan = installing("macos", package_manager=package_manager)

    assert STANDALONE in plan, plan
    assert "package install" not in plan, (
        "Homebrew's code-server is deprecated and stops at 4.112.0")
    # systemd is a Linux package install's advice; a Mac has neither.
    assert "systemctl" not in plan


@pytest.mark.parametrize(("package_manager", "wants_standalone"), [
    ("pacman", True),   # Arch: the script's own choice builds from the AUR
    ("apt", False),     # Debian and Ubuntu: its .deb is the better install there
    ("dnf", False),
])
def test_linux_is_unchanged(package_manager: str, wants_standalone: bool) -> None:
    plan = installing("linux", package_manager=package_manager)

    assert (STANDALONE in plan) is wants_standalone, plan
    assert ("systemctl" in plan) is not wants_standalone, "only a package install starts a service"


def test_a_code_server_already_there_is_left_alone_and_homebrews_is_named() -> None:
    from_homebrew = installing("macos", installed=True, brew_has_it=True)
    standalone = installing("macos", installed=True)

    for plan in (from_homebrew, standalone):
        assert "already installed (4.137.0)" in plan
        assert "would run" not in plan and "package install" not in plan, "nothing is replaced"
    assert "11 April 2027" in from_homebrew, "a Homebrew code-server is named, with what to do"
    assert "later:" not in standalone, "nothing to say about a standalone one"


def test_a_dry_run_plans_without_looking_for_what_it_did_not_install() -> None:
    """The plan used to end in "code-server isn't where it said" on any machine without it."""
    assert "fail:" not in installing("linux", package_manager="apt")
