"""Which process the soak test watches (`service_process` in `tools/load_test.py`).

On 17 September 2026 the soak test watched its own private RAVIS instead of the live one: both
are `ravis serve`, and the private one had the lower process number after the numbers wrapped.
The live service is the one listening on its port.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


@pytest.fixture()
def lt(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(TOOLS))
    sys.modules.pop("load_test", None)
    import load_test

    return load_test


class Fake:
    def __init__(self, pid: int, cmdline: list[str], listening: int | None) -> None:
        self.pid, self.info, self.listening = pid, {"cmdline": cmdline}, listening

    def net_connections(self, kind: str) -> list[Any]:
        del kind
        if self.listening is None:
            return []
        return [SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=self.listening))]


def test_the_live_service_is_the_one_on_its_port(lt: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    private = Fake(764, ["/venv/bin/python", "/venv/bin/ravis", "serve"], 51234)
    live = Fake(37459, ["/venv/bin/python", "/venv/bin/ravis", "serve"], 8731)
    other = Fake(12, ["/venv/bin/python", "/venv/bin/sirvis", "serve"], 8721)
    monkeypatch.setattr(lt.psutil, "process_iter", lambda _attrs: [other, private, live])
    monkeypatch.setattr(lt.psutil, "CONN_LISTEN", "LISTEN")

    assert lt.service_process("ravis", 8731) is live
    assert lt.service_process("ravis") is private, "without a port, the first match, as before"
    assert lt.service_process("ravis", 8790) is None
    assert lt.service_process("sirvis", 8721) is other
