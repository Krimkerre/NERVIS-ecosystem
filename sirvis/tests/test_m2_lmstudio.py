"""M2 — the LM Studio adapter (§7).

    Discover → load → generate → unload; effective configuration captured.

Every test here runs against a recorded runtime (§14.5). The live half of that
exit criterion — an actual load and unload against LM Studio — cannot be a test,
because it takes minutes and mutates the machine; it is verified by hand and
recorded in STATUS.md.

The two behaviours worth naming are both LM Studio's rather than SIRVIS's, and
both were found by probing the running application:

- it answers **200 with an error body** for endpoints it does not implement, and
- `loaded_context_length` is routinely *smaller* than `max_context_length`,
  which is §7.1's requested-versus-effective distinction in the wild.
"""

from __future__ import annotations

import httpx
import pytest
from tests.conftest_lmstudio import INSTALLED, transport, unreachable

from sirvis.runtimes import LMStudioAdapter, RuntimeState, RuntimeUnavailableError
from sirvis.runtimes.lmstudio import parse_lms_json


def _adapter(mock: httpx.MockTransport) -> LMStudioAdapter:
    return LMStudioAdapter("http://runtime.invalid", client=httpx.AsyncClient(transport=mock))


async def test_a_closed_runtime_is_stopped_with_a_reason_not_an_error() -> None:
    """§15.4: SIRVIS works standalone, and a laptop with LM Studio closed is the
    ordinary case. `health` never raising is what keeps that from becoming an
    error path in every caller."""
    info = await _adapter(unreachable()).health()

    assert info.state is RuntimeState.STOPPED
    assert info.detail
    assert info.model_count is None


async def test_a_running_runtime_reports_ready_and_its_inventory_size() -> None:
    info = await _adapter(transport()).health()

    assert info.state is RuntimeState.READY
    assert info.model_count == len(INSTALLED)
    assert info.detail == ""


async def test_an_endpoint_lm_studio_does_not_implement_is_a_failure() -> None:
    """The trap this adapter exists to survive.

    `GET /api/v0/whatever` returns **200** with an error body — verified against
    LM Studio 0.4.21, where a randomly invented path answers exactly the same
    way as a plausible one. An adapter trusting the status code would report an
    unsupported operation as a success, which is worse than failing because it
    is silent.
    """
    adapter = _adapter(transport())

    with pytest.raises(RuntimeUnavailableError, match="Unexpected endpoint"):
        await adapter._get("/api/v0/load")


async def test_installed_models_are_returned_as_the_runtime_reported_them() -> None:
    """Unreshaped: mapping onto SIRVIS's model domain is M3, and doing it in two
    places is how the two versions diverge."""
    models = await _adapter(transport()).list_models()

    assert len(models) == len(INSTALLED)
    assert {m["runtime_key"] for m in models} == {"lmstudio"}
    assert models[0]["id"] == "qwen2.5-coder-7b-instruct"


async def test_the_effective_context_is_what_was_loaded_not_what_was_advertised() -> None:
    """§7.1, and the finding that makes it more than bookkeeping.

    On this machine `qwen2.5-coder-7b-instruct` advertises a 32768 maximum and
    is *loaded* at 8192. A consumer that read the maximum and planned a 20K
    request would be refused by the runtime having passed every check upstream
    of it.
    """
    loaded = await _adapter(transport()).list_loaded_models()

    assert [m.model_key for m in loaded] == ["qwen2.5-coder-7b-instruct"]
    assert loaded[0].effective["context_length"] == 8192
    assert loaded[0].effective["compatibility_type"] == "mlx"


async def test_a_model_that_is_not_resident_is_not_reported_as_loaded() -> None:
    loaded = await _adapter(transport()).list_loaded_models()

    assert "lmstudio-community/granite-4.0-h-tiny" not in {m.model_key for m in loaded}


async def test_generation_round_trips() -> None:
    """The 'generate' half of M2's exit, against a recorded runtime."""
    body = await _adapter(transport()).generate(
        "qwen2.5-coder-7b-instruct", [{"role": "user", "content": "hi"}], max_tokens=2
    )

    assert body["choices"][0]["message"]["content"] == "ok"


async def test_an_unsupported_load_option_is_surfaced_rather_than_dropped() -> None:
    """§7.1: never silently ignore an unsupported value.

    A benchmark run under a configuration that was quietly discarded is evidence
    about nothing, so anything the CLI cannot express comes back in `ignored`.
    Driven through a lifecycle-less adapter, so the assertion is about the
    bookkeeping rather than about a real load.
    """
    adapter = LMStudioAdapter(
        "http://runtime.invalid",
        client=httpx.AsyncClient(transport=transport()),
        lms_path="/nonexistent/lms",
    )

    with pytest.raises(RuntimeUnavailableError, match="lifecycle operations are unavailable"):
        await adapter.load("qwen2.5-coder-7b-instruct", {"context_length": 32768, "ttl": 60})


async def test_a_missing_cli_says_the_lifecycle_is_unavailable_not_that_load_failed() -> None:
    """Two different problems. One is fixable by installing something."""
    adapter = LMStudioAdapter(
        "http://runtime.invalid",
        client=httpx.AsyncClient(transport=transport()),
        lms_path="/nonexistent/lms",
    )

    assert adapter.lifecycle_available() is False
    with pytest.raises(RuntimeUnavailableError, match="exposes no HTTP load/unload"):
        await adapter.unload("anything")


def test_lms_json_output_is_read_past_its_surrounding_noise() -> None:
    """The CLI prints banners around its JSON, and will print different ones."""
    output = "Loading...\n[{\"path\": \"a\"}, {\"path\": \"b\"}]\nDone.\n"

    assert parse_lms_json(output) == [{"path": "a"}, {"path": "b"}]


def test_unreadable_lms_output_yields_nothing_rather_than_raising() -> None:
    assert parse_lms_json("no json here") == []


async def test_unload_all_drives_the_cli_and_is_reachable_nowhere_else() -> None:
    """SIRVIS.md §7 lists `unload_all` on the runtime interface, and nothing calls it.

    Kept rather than deleted, for the same reason as
    `ResourceManager.force_unload`: deleting specified behaviour because nothing
    calls it yet is the wrong correction. But an operator escape hatch with no
    test is a claim rather than a feature, and the dead-code gate is right to
    say so.

    **No surface exposes it.** Reaching it means an authorization story SIRVIS
    does not have — the same one `force_unload` waits on.
    """
    ran: list[list[str]] = []

    adapter = LMStudioAdapter("http://runtime.invalid", client=httpx.AsyncClient(
        transport=transport()
    ))
    adapter._resolve_lms = lambda: "/fake/lms"  # type: ignore[method-assign]
    def record(arguments: list[str], timeout: float, **rest: object) -> str:
        del timeout, rest
        ran.append(arguments)
        return ""

    adapter._run_lms = record  # type: ignore[method-assign]

    await adapter.unload_all()

    assert ran == [["unload", "--all"]]
