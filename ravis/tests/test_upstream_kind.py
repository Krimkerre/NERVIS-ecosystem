"""Which adapter discovers the transparent upstream (M8).

The selection is configuration, not detection. These pin that an unrecognised
value degrades to the generic adapter rather than refusing to start, because the
failure mode of a typo should be capabilities RAVIS does not learn about — which
fails closed at pool admission — and not an outage.
"""

from __future__ import annotations

import httpx

from ravis.config import Settings
from ravis.providers import GenericOpenAiAdapter, LmStudioAdapter, OllamaAdapter
from ravis.transparent import adapter_for
from ravis.upstream import Upstream
from ravis.upstreams import UpstreamSpec


def _adapter_for(kind: str) -> GenericOpenAiAdapter:
    settings = Settings(  # type: ignore[call-arg]
        database_path=":memory:", upstream_kind=kind, _env_file=None
    )
    upstream = Upstream(base_url="http://upstream.invalid", api_key="")
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
