"""Windows, where the ecosystem is a Linux ecosystem with a Windows front door.

The supported Windows installation is WSL 2: the virtual environment, the services and the
runtimes are all inside the distribution. Two things therefore have to be true, and neither
was until 20 September 2026.

**The double-click has to reach the distribution.** `start-windows.bat` called `py.exe` and
`python.exe`, which would have run `tools/run.py` as a *Windows* program and sent it looking
for `ravis/.venv/bin/sirvis` — a path that does not exist in a Windows install of anything.
It could only ever have produced a console window full of failures, so these tests pin what
the file does now: hand the same script to `wsl.exe`.

**The browser is on the other side.** Inside WSL there is usually no browser at all, so
`webbrowser.open` opens nothing and the dashboard — or a Codex sign-in page, which is worse —
silently never appears. `open_page` crosses back to Windows for it.

Nothing here runs `wsl.exe` or opens a browser: the launchers are read as text, and every
door out of `run.py` is replaced.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_PY = ROOT / "tools" / "run.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("launcher_windows_under_test", RUN_PY)
    assert spec is not None and spec.loader is not None
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    return run


@pytest.mark.parametrize("launcher,verb", [("start-windows.bat", "start"),
                                           ("stop-windows.bat", "stop")])
def test_the_windows_launchers_hand_the_work_to_wsl(launcher: str, verb: str) -> None:
    text = (ROOT / launcher).read_text(encoding="utf-8")

    assert f"wsl.exe --cd \"%~dp0.\" -- python3 tools/run.py {verb}" in text, (
        "the folder in Windows' spelling, translated by wsl itself, so the repository can sit"
        " on C: or inside the distribution; the trailing dot keeps the backslash from"
        " escaping the quote"
    )
    for windows_python in ("py -3", "python tools"):
        assert windows_python not in text, (
            "Windows' own Python cannot run this stack: its programs are in ravis/.venv/bin"
        )
    assert "wsl --install" in text or "nothing of this ecosystem is running" in text, (
        "a machine without WSL is told what to do, not shown a window that closes"
    )


def test_a_plain_linux_is_not_mistaken_for_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _load()
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr(run.Path, "read_text", lambda *_, **__: "6.8.0-45-generic\n")

    assert run.in_wsl() is False


def test_wsl_is_recognised_by_the_kernel_when_the_environment_says_nothing(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session whose environment was scrubbed is still WSL, and the kernel still says so."""
    run = _load()
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr(run.Path, "read_text",
                        lambda *_, **__: "5.15.167.4-microsoft-standard-WSL2\n")

    assert run.in_wsl() is True


def _opened(monkeypatch: pytest.MonkeyPatch, run: ModuleType, *, have: set[str],
            fails: set[str] = frozenset()) -> list[list[str]]:
    """Record what `open_page` tried, with `have` the programs this fake machine has."""
    tried: list[list[str]] = []
    monkeypatch.setattr(run.shutil, "which", lambda name: name if name in have else None)

    def _run(command: list[str], **_: object) -> SimpleNamespace:
        tried.append(command)
        return SimpleNamespace(returncode=1 if command[0] in fails else 0)

    monkeypatch.setattr(run.subprocess, "run", _run)
    monkeypatch.setattr(run.webbrowser, "open",
                        lambda url: tried.append(["webbrowser", url]) or True)
    return tried


def test_under_wsl_the_page_opens_on_the_windows_side(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _load()
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    tried = _opened(monkeypatch, run, have={"wslview", "cmd.exe"})

    assert run.open_page("http://127.0.0.1:8790/#token") is True
    assert tried == [["wslview", "http://127.0.0.1:8790/#token"]], (
        "the distribution's own opener first, and nothing else once it worked"
    )


def test_when_wslview_is_absent_or_fails_windows_own_start_is_used(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """`wslu` is not installed everywhere, and `cmd.exe` always is.

    The fallback is what makes a Codex sign-in possible under WSL at all: that page is the
    whole of the sign-in, and a launcher that could not open it would leave somebody with a
    URL in a terminal they were never shown.
    """
    run = _load()
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    tried = _opened(monkeypatch, run, have={"wslview", "cmd.exe"}, fails={"wslview"})

    assert run.open_page("https://auth.example/sign-in") is True
    assert tried[-1] == ["cmd.exe", "/c", "start", "", "https://auth.example/sign-in"], (
        'the empty argument is start\'s title, without which a quoted URL becomes the title'
    )


def test_off_wsl_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _load()
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.setattr(run, "in_wsl", lambda: False)
    tried = _opened(monkeypatch, run, have={"wslview", "cmd.exe"})

    assert run.open_page("http://127.0.0.1:8790/") is True
    assert tried == [["webbrowser", "http://127.0.0.1:8790/"]], (
        "a Mac and a plain Linux keep the browser they already opened pages with"
    )
