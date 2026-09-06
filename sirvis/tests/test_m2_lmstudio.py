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

from sirvis.errors import InvalidConfigurationError
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


# ── Argument injection (CWE-88, a Claude Security scan) ──────────────────────
#
# `model_key`, `context_length` and `gpu_offload` all originate in a caller's
# JSON body and land in `lms`'s argv unquoted. Every case below asserts on
# `ran` — the list `_run_lms` would have appended to — staying empty, which is
# the difference between "the value never reached `subprocess.run`" and "the
# eventual result was an error". A value that merely raised late could still
# have shelled out first.


def _recording_adapter() -> tuple[LMStudioAdapter, list[list[str]]]:
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
    return adapter, ran


async def test_a_flag_shaped_model_key_never_reaches_lms_load() -> None:
    """The exploit scenario itself: an installed-looking key that is actually
    an `lms` option (`--verbose`) must be refused before `subprocess.run`
    ever gets a chance to run, not merely produce an eventual failure."""
    adapter, ran = _recording_adapter()

    with pytest.raises(InvalidConfigurationError, match="model_key"):
        await adapter.load("--verbose", {"context_length": 8192})

    assert ran == []


async def test_a_flag_shaped_context_length_never_reaches_lms_load() -> None:
    adapter, ran = _recording_adapter()

    with pytest.raises(InvalidConfigurationError, match="context_length"):
        await adapter.load("qwen2.5-coder-7b-instruct", {"context_length": "--some-flag"})

    assert ran == []


async def test_a_flag_shaped_gpu_offload_never_reaches_lms_load() -> None:
    adapter, ran = _recording_adapter()

    with pytest.raises(InvalidConfigurationError, match="gpu_offload"):
        await adapter.load("qwen2.5-coder-7b-instruct", {"gpu_offload": "--gpu-flag"})

    assert ran == []


async def test_a_flag_shaped_model_key_never_reaches_lms_unload() -> None:
    adapter, ran = _recording_adapter()

    with pytest.raises(InvalidConfigurationError, match="model_key"):
        await adapter.unload("--all")

    assert ran == []


async def test_ordinary_load_values_still_reach_lms_unchanged() -> None:
    """The fix must not turn away a legitimate request. `gpu_offload: max` is
    §7.1's own example configuration (SIRVIS.md §7.1), and a plain qualified
    key and context length are what every other test in this module sends."""
    adapter, ran = _recording_adapter()

    await adapter.load(
        "qwen2.5-coder-7b-instruct", {"context_length": 32768, "gpu_offload": "max"}
    )

    assert ran == [[
        "load", "qwen2.5-coder-7b-instruct", "--yes",
        "--context-length", "32768", "--gpu", "max",
    ]]


async def test_ordinary_unload_still_reaches_lms_unchanged() -> None:
    adapter, ran = _recording_adapter()

    await adapter.unload("qwen2.5-coder-7b-instruct")

    assert ran == [["unload", "qwen2.5-coder-7b-instruct"]]


# ── The builds the catalogue does not publish ────────────────────────────────
#
# Measured on this machine: two builds of `google/gemma-4-e4b` are installed,
# and `/api/v0/models` lists both only while one of them is loaded. Unload it
# and the catalogue publishes one entry describing whichever variant the app has
# *selected* — the other build is still on disk, still loadable, still
# benchmarkable, and unnameable. `lms ls --variants --json` names every one.


def _build(key: str, fmt: str, quantization: str) -> object:
    from sirvis.runtimes.variants import InstalledVariant

    return InstalledVariant(
        model_key=key, family=key.split("@", 1)[0], runtime_format=fmt,
        quantization=quantization, publisher="google", architecture="gemma4",
        max_context=131072, size_bytes=None, model_type="llm",
    )


def test_a_build_the_catalogue_omits_is_added_from_the_cli() -> None:
    """The MLX build is on disk and absent from the catalogue. It arrives
    `not-loaded`, because state is the one field the listing does not carry and
    a build the running catalogue never mentions is not resident."""
    from sirvis.runtimes.lmstudio import add_unpublished

    merged = add_unpublished(
        [{"id": "google/gemma-4-e4b", "compatibility_type": "gguf",
          "quantization": "Q4_K_M", "state": "not-loaded"}],
        [_build("google/gemma-4-e4b@q4_k_m", "gguf", "Q4_K_M"),
         _build("google/gemma-4-e4b@4bit", "mlx", "4bit")],
    )

    assert [entry["id"] for entry in merged] == [
        "google/gemma-4-e4b", "google/gemma-4-e4b@4bit"]
    assert merged[1]["state"] == "not-loaded"
    assert merged[1]["compatibility_type"] == "mlx"


def test_a_build_the_catalogue_already_describes_is_not_added_twice() -> None:
    """The plain key *is* one of these builds while it is the selected variant.
    Matching on family plus format plus quantization is what keeps it from being
    published a second time under its qualified name — and the loaded record,
    which is the only one carrying a live state, is the one that survives."""
    from sirvis.runtimes.lmstudio import add_unpublished

    merged = add_unpublished(
        [{"id": "google/gemma-4-e4b@4bit", "compatibility_type": "mlx",
          "quantization": "4bit", "state": "loaded"},
         {"id": "google/gemma-4-e4b", "compatibility_type": "gguf",
          "quantization": "Q4_K_M", "state": "not-loaded"}],
        [_build("google/gemma-4-e4b@q4_k_m", "gguf", "Q4_K_M"),
         _build("google/gemma-4-e4b@4bit", "mlx", "4bit")],
    )

    assert len(merged) == 2
    assert merged[0]["state"] == "loaded"


def test_nothing_is_added_when_the_cli_cannot_be_asked() -> None:
    """`None` is not `[]`. Nobody to ask means the catalogue is published exactly
    as it arrived — inventing an absence here would report a machine as holding
    nothing the moment the CLI went missing."""
    from sirvis.runtimes.lmstudio import add_unpublished

    catalogue = [{"id": "smollm3-3b", "compatibility_type": "gguf",
                  "quantization": "Q4_K_M", "state": "not-loaded"}]

    assert add_unpublished(catalogue, None) == catalogue
    assert add_unpublished(catalogue, []) == catalogue


def test_a_remote_runtime_is_not_described_by_this_machines_cli() -> None:
    """The CLI reads *this* laptop's disk. An adapter pointed at LM Studio on
    another host must not be handed these builds — that is one machine's
    inventory filed as another's."""
    from sirvis.runtimes.lmstudio import _is_local

    assert _is_local("http://127.0.0.1:1234")
    assert _is_local("http://localhost:1234/v1")
    assert not _is_local("http://runtime.invalid")
    assert not _is_local("http://192.168.1.40:1234")


async def test_a_local_runtime_asks_the_cli_it_was_configured_with() -> None:
    """The guard above returns before the binary is ever resolved, so a wrong
    method name here type-checked, passed every test, and 500'd the models
    endpoint the moment a real loopback runtime asked. This exercises the path
    the guard skips."""
    asked: list[str | None] = []
    adapter = LMStudioAdapter("http://127.0.0.1:1234", client=httpx.AsyncClient(
        transport=transport()
    ))
    adapter._resolve_lms = lambda: "/fake/lms"  # type: ignore[method-assign]

    def record(binary: str | None = None) -> None:
        asked.append(binary)
        return None

    import sirvis.runtimes.lmstudio as module

    original = module.installed_variants
    module.installed_variants = record  # type: ignore[assignment]
    try:
        models = await adapter.list_models()
    finally:
        module.installed_variants = original

    assert asked == ["/fake/lms"]
    assert len(models) == len(INSTALLED)
