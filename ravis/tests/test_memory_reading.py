"""Reading available memory — and specifically, not reading it the wrong way."""

from __future__ import annotations

import pytest

from ravis.runtime.resources import MemoryReading, read_memory


def test_an_unknown_reading_is_not_treated_as_pressure() -> None:
    """Refusing to load anything because the platform could not be read would
    turn a diagnostic gap into a routing outage."""
    assert MemoryReading().under_pressure is False


def test_an_unknown_reading_reports_no_fraction_rather_than_zero() -> None:
    """Zero would read as "no memory at all" (runbook §14.4)."""
    assert MemoryReading().free_fraction is None


def test_pressure_is_detected_below_the_threshold() -> None:
    assert MemoryReading(available_bytes=1, total_bytes=100).under_pressure is True


def test_ample_memory_is_not_pressure() -> None:
    assert MemoryReading(available_bytes=50, total_bytes=100).under_pressure is False


def test_the_real_machine_reports_a_plausible_reading() -> None:
    """Guards the parser against the platform, which no fixture can.

    Deliberately loose: this asserts the reading is *sane*, not what it says.
    The failure it catches is a parse that silently yields nonsense — which is
    exactly what happened when `top`'s "unused" figure was mistaken for
    available memory and reported 619 MB against 8.6 GB actually free.
    """
    reading = read_memory()

    if not reading.is_known:
        return  # An unsupported platform is allowed; a wrong answer is not.
    assert reading.available_bytes is not None and reading.total_bytes is not None
    assert 0 < reading.available_bytes <= reading.total_bytes
    assert reading.total_bytes > 512 * 2**20


# ── Sampled on a timer, read by routing ──────────────────────────────────────
#
# Every routed request used to call `read_memory()`, which on macOS is a
# `vm_stat` process: 6–8 ms inside the event loop per request, measured by
# `tools/load_test.py` (RAVIS.md §9.8). Routing now reads the app's latest sample.


def test_a_routed_request_reads_the_sampled_memory_and_measures_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx
    from fastapi.testclient import TestClient
    from tests.conftest_upstream import RecordingUpstream

    from ravis.app import create_app
    from ravis.config import Settings
    from ravis.runtime import resources

    def measured(*_: object, **__: object) -> MemoryReading:
        raise AssertionError("a request measured memory instead of reading the sample")

    monkeypatch.setattr(resources, "_read_vm_stat", measured)
    monkeypatch.setattr(resources, "_read_proc_meminfo", measured)
    upstream = RecordingUpstream()
    app = create_app(Settings(database_path=":memory:", upstream_base_url="http://upstream.invalid",
                              _env_file=None))  # type: ignore[call-arg]
    fake = httpx.AsyncClient(transport=upstream.transport())
    app.app.state.upstream_client = fake  # type: ignore[attr-defined]
    app.app.state.model_registry.use_client(fake)  # type: ignore[attr-defined]
    app.app.state.memory = MemoryReading(available_bytes=1, total_bytes=100)  # type: ignore[attr-defined]

    response = TestClient(app).post(
        "/v1/chat/completions",
        json={"model": "qwen2.5-coder-7b", "messages": [{"role": "user", "content": "Hi"}]},
    )

    assert response.status_code == 200, response.text


async def test_the_app_samples_memory_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import threading
    from types import SimpleNamespace

    from ravis import app as app_module

    loop_thread = threading.current_thread()
    sampled_on_loop_thread: list[bool] = []
    reading = MemoryReading(available_bytes=40, total_bytes=100)

    def read() -> MemoryReading:
        sampled_on_loop_thread.append(threading.current_thread() is loop_thread)
        return reading

    monkeypatch.setattr(app_module, "read_memory", read)
    monkeypatch.setattr(app_module, "MEMORY_SAMPLE_SECONDS", 0.01)
    api = SimpleNamespace(state=SimpleNamespace(memory=MemoryReading()))
    task = asyncio.create_task(app_module._sample_memory_periodically(api))  # type: ignore[arg-type]
    await asyncio.sleep(0.1)
    task.cancel()

    assert len(sampled_on_loop_thread) >= 2, "sampled at startup and again on the timer"
    assert not any(sampled_on_loop_thread), "vm_stat must never run on the event loop"
    assert api.state.memory is reading
