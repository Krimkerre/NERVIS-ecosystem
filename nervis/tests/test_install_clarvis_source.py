"""Where the installer clones Clarvis from (`install.sh`, `clarvis_source`; 19 September 2026).

Found on the owner's Linux laptop: a copy of NERVIS-ecosystem whose origin git could not read
sent the clone to a repository named just "clarvis.git" ("repository 'clarvis.git' doesn't
exist"). The function is lifted out of the installer and run by bash against a real repository
with each way an origin can be written, so the rule is pinned on macOS and Linux alike.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[2] / "install.sh"
PUBLIC = "https://github.com/Krimkerre/clarvis.git"


def _function() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    public = re.search(r'^CLARVIS_PUBLIC_REPO=.*$', text, re.MULTILINE)
    body = re.search(r"^clarvis_source\(\) \{.*?^\}", text, re.MULTILINE | re.DOTALL)
    assert public and body, "install.sh no longer defines clarvis_source"
    return public.group(0) + "\n" + body.group(0)


def source_for(repo: Path, origin: str | None, env: dict[str, str] | None = None) -> str:
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if origin is not None:
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", origin], check=True)
    script = f'{_function()}\nREPO="{repo}"\nclarvis_source'
    ran = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True,
                         env={"PATH": "/usr/bin:/bin:/usr/local/bin", **(env or {})})
    return ran.stdout.strip()


@pytest.mark.parametrize(("origin", "expected"), [
    ("https://github.com/Krimkerre/NERVIS-ecosystem.git",
     "https://github.com/Krimkerre/clarvis.git"),
    ("https://github.com/Krimkerre/NERVIS-ecosystem", "https://github.com/Krimkerre/clarvis.git"),
    ("https://github.com/krimkerre/nervis-ecosystem/", "https://github.com/krimkerre/clarvis.git"),
    ("git@github.com:Krimkerre/NERVIS-ecosystem.git", "git@github.com:Krimkerre/clarvis.git"),
    ("/srv/mirror/NERVIS-ecosystem", "/srv/mirror/clarvis.git"),
])
def test_clarvis_is_cloned_from_beside_the_origin(tmp_path: Path, origin: str,
                                                  expected: str) -> None:
    assert source_for(tmp_path / "eco", origin) == expected


@pytest.mark.parametrize("origin", [None, "https://example.com/someone/fork-of-it.git"])
def test_no_usable_origin_falls_back_to_the_public_repository(tmp_path: Path,
                                                              origin: str | None) -> None:
    assert source_for(tmp_path / "eco", origin) == PUBLIC


def test_not_a_git_copy_at_all_falls_back_too(tmp_path: Path) -> None:
    """A downloaded ZIP: no .git anywhere."""
    folder = tmp_path / "zip"
    folder.mkdir()
    script = f'{_function()}\nREPO="{folder}"\nclarvis_source'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True,
                         cwd=tmp_path, env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                                            "GIT_CEILING_DIRECTORIES": str(tmp_path)})
    assert out.stdout.strip() == PUBLIC


def test_clarvis_repo_wins_when_set(tmp_path: Path) -> None:
    assert source_for(tmp_path / "eco", "https://github.com/Krimkerre/NERVIS-ecosystem.git",
                      {"CLARVIS_REPO": "/home/me/clarvis"}) == "/home/me/clarvis"
