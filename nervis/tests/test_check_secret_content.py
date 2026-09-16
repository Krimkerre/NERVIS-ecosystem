"""The secret scan (`tools/check_secret_content.py`).

Every key below is assembled while the test runs, from pieces no pattern matches, so this file
never carries a key-shaped string of its own and the scan it tests passes over it.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_secret_content.py"
RANDOMISH = "q7Zp2Lx9Wv4Kd8Rt1Ym6Hc3Nb5Gf0JsE"  # 32 characters, no pattern's prefix


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_secret_content", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scan = load()

KEYS = {
    "an Anthropic key": "sk-" + "ant-api03-" + RANDOMISH + "Aa",
    "an OpenAI-style key (sk-)": "sk-" + "proj-" + RANDOMISH + "Bb",
    "a GitHub token": "gh" + "p_" + RANDOMISH + "Cc1234",
    "an AWS access key": "AK" + "IA" + "Q7ZP2LX9WV4KD8RT",
    "a Google API key": "AI" + "za" + RANDOMISH + "Dd1",
    "a Hugging Face token": "hf" + "_" + RANDOMISH,
    "a Slack token": "xo" + "xb-" + RANDOMISH,
    "a Stripe live key": "sk" + "_live_" + RANDOMISH,
    "an xAI key": "xa" + "i-" + RANDOMISH + RANDOMISH[:10],
    "a Groq key": "gs" + "k_" + RANDOMISH + RANDOMISH[:10],
    "a private key": "-----BEGIN " + "OPENSSH PRIVATE" + " KEY-----",
}


def found(text: str, secrets: list[str] | None = None) -> list[str]:
    return list(scan.findings([("f.py", 7, text)], secrets or []))


@pytest.mark.parametrize(("kind", "key"), sorted(KEYS.items()))
def test_each_known_shape_is_found_and_named_but_not_shown(kind: str, key: str) -> None:
    reports = found(f'API_KEY = "{key}"')
    assert reports == [f"f.py:7: looks like {kind}"]
    assert key not in reports[0]


@pytest.mark.parametrize("text", [
    "set OPENAI_API_KEY=sk-...",
    "a GitHub token looks like ghp_xxx",
    "AKIA followed by sixteen characters",
    "the task-" + RANDOMISH + " ran",
    "-----BEGIN PUBLIC KEY-----",
])
def test_placeholders_and_lookalikes_are_not_findings(text: str) -> None:
    assert found(text) == []


def test_a_line_marked_as_a_deliberate_fake_is_left_alone() -> None:
    assert found(KEYS["a GitHub token"] + "  # secret-scan: allow") == []


def test_this_machines_own_secret_is_found_by_value(tmp_path: Path) -> None:
    run = tmp_path / ".run"
    run.mkdir()
    (run / "dashboard.token").write_text(RANDOMISH + "\n")
    (run / "short.token").write_text("tiny\n")
    (run / "notes.txt").write_text("not-a-secret-file-but-long-enough\n")
    (tmp_path / "nervis").mkdir()
    (tmp_path / "nervis" / "nervis.enrollment").write_text("E" * 40)
    secrets = scan.local_secrets(tmp_path)
    assert secrets == sorted([RANDOMISH, "E" * 40])
    assert found(f"token: {RANDOMISH}", secrets) == [
        "f.py:7: looks like one of this machine's own secrets (.run/ or an enrollment file)"
    ]
    assert found("tiny words", secrets) == []


GIT = shutil.which("git")


@pytest.mark.skipif(GIT is None, reason="needs git")
def test_only_the_lines_a_commit_adds_are_read_and_the_report_never_shows_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert GIT
    repo = tmp_path / "repo"
    subprocess.run([GIT, "init", "-q", str(repo)], check=True)
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "t")):
        subprocess.run([GIT, "-C", str(repo), "config", key, value], check=True)
    old_key = KEYS["an AWS access key"]
    (repo / "a.txt").write_text(f"one\n{old_key}\nthree\n")
    subprocess.run([GIT, "-C", str(repo), "add", "a.txt"], check=True)
    subprocess.run([GIT, "-C", str(repo), "commit", "-q", "-m", "x", "--no-verify"], check=True)
    new_key = KEYS["an Anthropic key"]
    (repo / "a.txt").write_text(f"one\n{old_key}\nthree\nfour\n{new_key}\n")
    subprocess.run([GIT, "-C", str(repo), "add", "a.txt"], check=True)
    monkeypatch.setattr(scan, "ROOT", repo)

    assert list(scan.staged_lines()) == [("a.txt", 4, "four"), ("a.txt", 5, new_key)]
    assert scan.main([]) == 1
    out = capsys.readouterr().out
    assert "a.txt:5: looks like an Anthropic key" in out
    assert new_key not in out and old_key not in out
    assert "a.txt:2" not in out, "a key already committed is the --all scan's business"

    assert scan.main(["--all"]) == 1
    assert "a.txt:2: looks like an AWS access key" in capsys.readouterr().out


def test_git_failing_is_could_not_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(scan, "ROOT", tmp_path / "not-a-repo")
    assert scan.main([]) == 2
