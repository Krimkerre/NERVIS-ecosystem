"""Capabilities that say so when LM Studio or Hugging Face is down (§15.4, 19 September 2026).

Every capability used to be a fixed `available`, so a peer was told SIRVIS could load a model
with LM Studio closed and learned otherwise at click time. LM Studio's answer and Hugging
Face's are stated here: nothing reaches either, or the running stack.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from ecosystem_protocol import AVAILABLE, DEGRADED, UNAVAILABLE
from fastapi.testclient import TestClient

from sirvis.app import create_app
from sirvis.availability import HUB, LMSTUDIO, Availability, watch_lmstudio
from sirvis.config import Settings
from sirvis.ecosystem import DECLARED
from sirvis.runtimes import LMStudioAdapter
from sirvis.runtimes.base import RuntimeInfo, RuntimeState


def an_app() -> Any:
    settings = Settings(database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
                        lmstudio_cli_path="/nonexistent/lms", runtime_watch_seconds=0.0,
                        _env_file=None)  # type: ignore[call-arg]
    return create_app(settings)


def states(app: Any) -> dict[str, tuple[str, str]]:
    body = TestClient(app).get("/ecosystem/capabilities").json()
    return {c["id"]: (c["state"], c["reason"]) for c in body["capabilities"]}


def test_lm_studio_down_lowers_what_needs_it_and_back_up_restores_all() -> None:
    app = an_app()
    surface = app.state.ecosystem
    before = surface.revision

    assert app.state.availability.note(surface, LMSTUDIO, "isn't answering")
    down = states(app)

    assert down["sirvis.runtime.control"] == (
        UNAVAILABLE, "Waiting on LM Studio, which isn't answering: no model can be loaded "
        "or unloaded")
    assert down["sirvis.model_files"][0] == UNAVAILABLE
    assert down["sirvis.inventory.read"][0] == down["sirvis.downloads"][0] == DEGRADED
    assert down["sirvis.catalog.read"][0] == down["sirvis.benchmarks.results"][0] == AVAILABLE
    assert not app.state.availability.note(surface, LMSTUDIO, "isn't answering"), "no change"
    assert surface.revision == before + 1

    assert app.state.availability.note(surface, LMSTUDIO, None)
    assert surface.declared == DECLARED and surface.revision == before + 2
    assert all(c.state == AVAILABLE for c in DECLARED.values()), "the module's are never touched"


def test_both_down_names_both_on_what_needs_both() -> None:
    availability, app = Availability(), an_app()
    availability.note(app.state.ecosystem, LMSTUDIO, "isn't answering")
    availability.note(app.state.ecosystem, HUB, "failed SIRVIS's last read (answered HTTP 503)")

    state, reason = states(app)["sirvis.downloads"]

    assert state == DEGRADED
    assert reason.startswith("Waiting on Hugging Face, which failed SIRVIS's last read")
    assert "; Waiting on LM Studio, which isn't answering: " in reason


def _hub(status: int) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _request: httpx.Response(status, json=[])), base_url="https://hf.test")


def test_a_failed_search_marks_hugging_face_down_and_the_next_answer_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = an_app()
    monkeypatch.setattr(LMStudioAdapter, "installed_paths", lambda _self: frozenset())
    app.state.machine_memory_bytes = 16 * 2**30
    client = TestClient(app)

    app.state.hub_client = _hub(503)
    assert client.get("/api/v1/catalog?q=qwen").status_code >= 500
    state, reason = states(app)["sirvis.catalog.read"]
    assert state == DEGRADED and "(Hugging Face answered HTTP 503)" in reason

    app.state.hub_client = _hub(200)
    assert client.get("/api/v1/catalog?q=qwen").status_code == 200
    assert states(app)["sirvis.catalog.read"][0] == AVAILABLE


def test_the_watcher_reads_lm_studio_s_health(monkeypatch: pytest.MonkeyPatch) -> None:
    app = an_app()
    answers = iter([RuntimeState.STOPPED, RuntimeState.READY])
    seen: list[str] = []

    async def health(_self: Any) -> RuntimeInfo:
        return RuntimeInfo(runtime_key="lmstudio", state=next(answers), base_url="", detail="")

    monkeypatch.setattr(LMStudioAdapter, "health", health)

    async def two_probes() -> None:
        task = asyncio.create_task(watch_lmstudio(app))
        for probe in (1, 2):
            while app.state.ecosystem.revision < 1 + probe:  # each probe here is a change
                await asyncio.sleep(0)
            seen.append(app.state.ecosystem.declared["sirvis.runtime.control@1"].state)
        task.cancel()

    asyncio.run(two_probes())
    assert seen == [UNAVAILABLE, AVAILABLE]
