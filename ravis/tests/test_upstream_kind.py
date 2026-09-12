"""Which adapter discovers the transparent upstream (M8).

The selection is configuration, not detection. These pin that an unrecognised
value degrades to the generic adapter rather than refusing to start, because the
failure mode of a typo should be capabilities RAVIS does not learn about — which
fails closed at pool admission — and not an outage.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from ravis.config import Settings
from ravis.providers import GenericOpenAiAdapter, LmStudioAdapter, OllamaAdapter
from ravis.transparent import adapter_for
from ravis.upstream import Upstream
from ravis.upstreams import UpstreamSpec


def _adapter_for(kind: str) -> GenericOpenAiAdapter:
    settings = Settings(  # type: ignore[call-arg]
        database_path=":memory:", upstream_kind=kind, _env_file=None
    )
    upstream = Upstream(base_url="http://upstream.invalid", declared_key="")
    return adapter_for(
        UpstreamSpec(name="probe", base_url=upstream.base_url, kind=kind),
        upstream,
        httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))),
        settings,
    )


def test_lmstudio_selects_the_lmstudio_adapter() -> None:
    assert isinstance(_adapter_for("lmstudio"), LmStudioAdapter)


def test_ollama_selects_the_ollama_adapter() -> None:
    assert isinstance(_adapter_for("ollama"), OllamaAdapter)


def test_the_default_is_the_generic_adapter() -> None:
    """It claims nothing it cannot see, which is the right default for unknown kit."""
    adapter = _adapter_for("generic")

    assert type(adapter) is GenericOpenAiAdapter


def test_the_kind_is_read_case_and_space_insensitively() -> None:
    assert isinstance(_adapter_for("  LMStudio "), LmStudioAdapter)


def test_an_unrecognised_kind_degrades_to_generic_rather_than_refusing() -> None:
    assert type(_adapter_for("lmstduio")) is GenericOpenAiAdapter


def test_the_adapter_is_named_after_the_upstream_not_its_kind() -> None:
    """§6 diagnostics name where a request went. With several upstreams of the
    same kind, the kind stops identifying anything and the name is what does."""
    assert _adapter_for("lmstudio").name == "probe"
    assert _adapter_for("generic").name == "probe"


# ── LM Studio's default load window, from setting to adapter ─────────────────
#
# A model LM Studio has not loaded yet is opened at LM Studio's default load
# length, which its API does not publish. `RAVIS_LMSTUDIO_DEFAULT_CONTEXT` is how
# RAVIS is told it, and these follow the number from the environment to the
# window a cold model is reported with — the path a launcher-read value takes.


def _settings(**overrides: Any) -> Settings:
    """Settings from this process's environment, without any `.env` file."""
    return Settings(database_path=":memory:", _env_file=None, **overrides)  # type: ignore[call-arg]


def test_lmstudio_default_context_is_the_8192_this_machines_lmstudio_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAVIS_LMSTUDIO_DEFAULT_CONTEXT", raising=False)

    assert _settings().lmstudio_default_context == 8192


def test_lmstudio_default_context_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAVIS_LMSTUDIO_DEFAULT_CONTEXT", "32768")

    assert _settings().lmstudio_default_context == 32768


def test_a_lmstudio_default_context_that_is_not_a_number_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refused like any other malformed number, rather than quietly becoming 8,192.

    A default the operator meant to raise, silently ignored, is the disagreement
    between RAVIS and LM Studio this setting exists to remove.
    """
    monkeypatch.setenv("RAVIS_LMSTUDIO_DEFAULT_CONTEXT", "lots")

    with pytest.raises(ValidationError, match="lmstudio_default_context"):
        _settings()


async def test_the_setting_reaches_the_lmstudio_adapter_and_is_capped_by_each_ceiling() -> None:
    """A cold model is reported at the configured default, never above its build's maximum."""
    catalogue = [
        {"id": "wide", "type": "llm", "state": "not-loaded", "max_context_length": 1048576},
        {"id": "narrow", "type": "llm", "state": "not-loaded", "max_context_length": 4096},
    ]

    def lmstudio(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": catalogue})

    upstream = Upstream(base_url="http://lmstudio.invalid", declared_key="")
    adapter = adapter_for(
        UpstreamSpec(name="lmstudio", base_url=upstream.base_url, kind="lmstudio"),
        upstream,
        httpx.AsyncClient(transport=httpx.MockTransport(lmstudio)),
        _settings(lmstudio_default_context=16384),
    )

    assert (await adapter.capabilities("wide")).context_window == 16384
    assert (await adapter.capabilities("narrow")).context_window == 4096
