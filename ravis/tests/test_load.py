"""§12.3's live figures (M20): what RAVIS has running, and what providers say about limits.

Tracking only — nothing here may change a route — so these tests read the figures and never
a routing decision. The wire tests drive real requests through the real application with the
upstream replaced, and read `/api/v1/health`'s `load`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from tests.test_fallback import ScriptedUpstream, _app_with

from ravis.reliability.load import LoadTracker, read_limits

MODELS: dict[str, dict[str, str]] = {"talker": {"context_window": "32000"}}
ASK: dict[str, Any] = {"model": "talker", "messages": [{"role": "user", "content": "hi"}]}


# ── Reading what a provider says ─────────────────────────────────────────────


def test_openai_and_anthropic_limits_are_read_as_sent() -> None:
    found = read_limits({
        "x-ratelimit-limit-requests": "500",
        "x-ratelimit-remaining-requests": "499",
        "x-ratelimit-reset-tokens": "6m0s",
        "anthropic-ratelimit-input-tokens-remaining": "39000",
        "anthropic-ratelimit-requests-reset": "2026-09-16T15:00:00Z",
        "content-type": "application/json",
    })
    assert found == {
        "requests": {"limit": "500", "remaining": "499",
                     "reset": "2026-09-16T15:00:00Z"},
        "tokens": {"reset": "6m0s"},
        "input-tokens": {"remaining": "39000"},
    }


def test_a_bare_limit_is_filed_as_unnamed_and_other_headers_are_ignored() -> None:
    assert read_limits({"X-RateLimit-Limit": "20", "x-ratelimit-remaining": "3"}) == {
        "unnamed": {"limit": "20", "remaining": "3"},
    }
    assert read_limits({"x-request-id": "abc", "x-ratelimit-used": "4"}) == {}


# ── Counting what runs ───────────────────────────────────────────────────────


def _tracker(hosts: dict[str, str] | None = None) -> tuple[LoadTracker, list[float]]:
    now = [100.0]
    tracker = LoadTracker(lambda host: (hosts or {}).get(host, ""), clock=lambda: now[0],
                          wall=lambda: 1_789_000_000.0)
    return tracker, now


def test_running_attempts_are_counted_until_they_end_however_they_end() -> None:
    tracker, _ = _tracker()

    async def scenario() -> None:
        async with (tracker.running("gemma", "lmstudio", True),
                    tracker.running("gpt-4.1", "openai", False)):
            snap = tracker.snapshot()
            assert (snap["active"], snap["local_generations"]) == (2, 1)
            assert snap["by_provider"] == [
                {"provider": "lmstudio", "local": True, "active": 1, "models": {"gemma": 1}},
                {"provider": "openai", "local": False, "active": 1, "models": {"gpt-4.1": 1}},
            ]
        with pytest.raises(RuntimeError):
            async with tracker.running("gemma", "lmstudio", True):
                raise RuntimeError("the provider went away")

    asyncio.run(scenario())
    snap = tracker.snapshot()
    assert (snap["active"], snap["local_generations"], snap["by_provider"]) == (0, 0, [])
    assert (snap["peak"], snap["attempts_started"]) == (2, 3)
    assert snap["queue"]["held"] == 0 and "no queue" in snap["queue"]["reason"]
    assert snap["local_queues"]["state"] == "unknown"


def test_a_providers_statement_is_kept_by_kind_and_aged() -> None:
    tracker, now = _tracker({"api.openai.com": "openai", "127.0.0.1:1234": "lmstudio"})

    def response(url: str, headers: dict[str, str]) -> httpx.Response:
        return httpx.Response(200, headers=headers, request=httpx.Request("POST", url))

    async def scenario() -> None:
        await tracker.observe(response("https://api.openai.com/v1/chat/completions",
                                       {"x-ratelimit-remaining-requests": "9",
                                        "x-ratelimit-remaining-tokens": "900"}))
        now[0] += 30
        await tracker.observe(response("https://api.openai.com/v1/models",
                                       {"x-ratelimit-remaining-requests": "8"}))
        await tracker.observe(response("https://api.openai.com/v1/chat/completions",
                                       {"retry-after": "2"}))
        await tracker.observe(response("http://127.0.0.1:1234/v1/chat/completions", {}))
        await tracker.observe(response("https://elsewhere.example/v1",
                                       {"x-ratelimit-remaining-requests": "1"}))

    asyncio.run(scenario())
    now[0] += 5
    assert tracker.snapshot()["limits"] == [{
        "provider": "openai",
        "requests": {"remaining": "8", "seconds_ago": 5.0},
        "tokens": {"remaining": "900", "seconds_ago": 35.0},
        "retry_after": {"value": "2", "seconds_ago": 5.0},
    }], "the newest reading per kind; a host no provider is at, and a silent one, add nothing"


# ── Through the application ──────────────────────────────────────────────────


class Limited(ScriptedUpstream):
    """An upstream that states its limits on every answer, like OpenAI."""

    def _handle(self, request: httpx.Request) -> httpx.Response:
        answer = super()._handle(request)
        if "messages" in json.loads(request.content or b"{}"):
            answer.headers["x-ratelimit-remaining-requests"] = "41"
        return answer


def _load(client: Any) -> dict[str, Any]:
    body = client.get("/api/v1/health").json()
    return dict(body["load"])


def _watch(tracker: LoadTracker) -> list[tuple[int, bool]]:
    """Record, at each attempt's start, how many were running and whether it was local."""
    seen: list[tuple[int, bool]] = []
    running = tracker.running

    def watching(model: str, provider: str, local: bool) -> Any:
        manager = running(model, provider, local)

        class Watch:
            async def __aenter__(self) -> None:
                await manager.__aenter__()
                seen.append((tracker.active, local))

            async def __aexit__(self, *exc: Any) -> Any:
                return await manager.__aexit__(*exc)

        return Watch()

    tracker.running = watching  # type: ignore[method-assign]
    return seen


@pytest.mark.parametrize("stream", [False, True])
def test_a_request_is_counted_while_it_runs_and_not_after(stream: bool) -> None:
    upstream = Limited(MODELS)
    with _app_with(upstream) as client:
        tracker = client.app.app.state.load  # type: ignore[attr-defined]
        seen = _watch(tracker)
        # `_app_with` swaps in its fake client after the app is built, so the hook the app
        # put on its own client (`test_the_apps_client_carries_the_hook`) goes on this one.
        hooks = client.app.app.state.upstream_client.event_hooks  # type: ignore[attr-defined]
        hooks["response"].append(tracker.observe)
        reply = client.post("/v1/chat/completions", json={**ASK, "stream": stream})
        assert reply.status_code == 200, reply.text
        load = _load(client)

    assert seen == [(1, False)], "the attempt was counted while it ran, as a hosted one"
    assert (load["active"], load["attempts_started"], load["by_provider"]) == (0, 1, [])
    assert load["limits"] and load["limits"][0]["requests"]["remaining"] == "41", load["limits"]


@pytest.mark.parametrize("stream", [False, True])
def test_a_translated_request_is_counted_too(stream: bool) -> None:
    """Anthropic's and Google's path runs through an adapter, and is counted all the same."""
    from tests.test_translated_path import FakeAnthropic
    from tests.test_translated_path import _app as translated_app

    with translated_app(FakeAnthropic()) as client:
        tracker = client.app.app.state.load  # type: ignore[attr-defined]
        seen = _watch(tracker)
        reply = client.post("/v1/chat/completions",
                            json={"model": "ravis/fake/claude-x", "stream": stream})
        assert reply.status_code == 200, reply.text
        load = _load(client)

    assert seen == [(1, False)]
    assert (load["active"], load["attempts_started"]) == (0, 1)


def test_the_apps_client_carries_the_hook() -> None:
    """Every provider call goes through this one client, translated ones included."""
    from ravis.app import create_app
    from ravis.config import Settings

    app = create_app(Settings(database_path=":memory:", _env_file=None))  # type: ignore[call-arg]
    state = app.app.state  # type: ignore[attr-defined]
    assert state.load.observe in state.upstream_client.event_hooks["response"]


def test_a_rate_limited_provider_is_reported_as_congested() -> None:
    upstream = ScriptedUpstream(MODELS, refuse={"talker": (429, {"error": "slow down"})})
    with _app_with(upstream) as client:
        client.post("/v1/chat/completions", json=ASK)
        load = _load(client)

    assert load["active"] == 0, "a refused attempt is not left counted"
    [congested] = load["congestion"]
    assert (congested["last"], congested["rate_limit"]) == ("rate_limit", 1)


def test_health_says_what_is_known_about_memory_and_what_is_not_read() -> None:
    with _app_with(ScriptedUpstream(MODELS)) as client:
        load = _load(client)

    assert set(load["memory"]) == {"available_bytes", "total_bytes", "free_fraction",
                                   "under_pressure", "detail"}
    assert load["congestion"] == []
    assert any("credits" in line for line in load["not_read"])
    # SIRVIS's co-residency evidence is read since 0.29.1 (`test_co_residency.py`).
    assert "co_residency" in load
