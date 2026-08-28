"""The `doctor` truth table: what routes where, and which setting decided it.

M0's acceptance criterion is that this can be answered "without contacting any
upstream", so several of these tests point the configuration at a port with
nothing behind it. If someone later makes the table probe for real models,
those tests stop passing rather than start hanging.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ravis.app import create_app
from ravis.config import Settings
from ravis.credentials import CredentialFile, CredentialStore
from ravis.model_filter import ModelFilter, ModelFilters
from ravis.provider_state import ProviderState
from ravis.providers_map import (
    NO_ROUTE,
    resolve_provider_map,
    shared_provider_names,
)

# Nothing listens here. Any accidental probe fails or hangs; a table computed
# from configuration alone does not care.
DEAD = "http://127.0.0.1:9/v1"


@pytest.fixture()
def configuration(tmp_path: Path) -> tuple[ModelFilters, ProviderState]:
    """Filters and provider state in a temporary directory.

    Both default to the user's real config directory, so a test that let them
    would read whatever the developer running it happens to have configured.
    """
    return ModelFilters(tmp_path / "models.json"), ProviderState(tmp_path / "providers.json")


def _map(settings: Settings, configuration: tuple[ModelFilters, ProviderState]) -> list[tuple]:
    filters, state = configuration
    return [
        (row.model, row.provider, row.decided_by)
        for row in resolve_provider_map(settings, filters=filters, state=state)
    ]


def test_nothing_configured_is_an_empty_table_not_a_failure(configuration) -> None:  # noqa: ANN001
    assert _map(Settings(database_path=":memory:"), configuration) == []


def test_a_singular_upstream_is_named_default_and_takes_everything(configuration) -> None:  # noqa: ANN001
    rows = _map(Settings(database_path=":memory:", upstream_base_url=DEAD), configuration)

    assert [row[:2] for row in rows] == [
        ("ravis/default/*", "default"),
        ("*", "default"),
    ]
    assert DEAD in rows[0][2]


def test_upstreams_appear_in_declaration_order(configuration) -> None:  # noqa: ANN001
    """Order is the tie-break, so the table must not sort it into something prettier.

    `transparent.py` breaks a model-id collision by declaration order, and a
    diagnostic that listed the upstreams alphabetically would name the wrong
    winner in exactly the situation someone runs it to understand.
    """
    settings = Settings(
        database_path=":memory:",
        upstreams=f'[{{"name":"zeta","base_url":"{DEAD}"}},{{"name":"alpha","base_url":"{DEAD}"}}]',
    )

    providers = [row[1] for row in _map(settings, configuration)]

    assert providers.index("zeta") < providers.index("alpha")


def test_an_exclusion_is_listed_above_the_inclusions_it_overrides(configuration) -> None:  # noqa: ANN001
    """Exclude wins in `ModelFilter.matches`, so it must read first here too."""
    filters, state = configuration
    filters.set_for("openrouter", ModelFilter(include=("anthropic/*",), exclude=("*-preview",)))
    settings = Settings(
        database_path=":memory:",
        upstreams=f'[{{"name":"openrouter","base_url":"{DEAD}"}}]',
    )

    rows = _map(settings, (filters, state))
    patterns = [row[0] for row in rows]

    assert patterns.index("*-preview") < patterns.index("anthropic/*")
    assert rows[patterns.index("*-preview")][:2] == ("*-preview", NO_ROUTE)
    # A filter with includes claims only what it lists — no catch-all row.
    assert "*" not in patterns


def test_a_disabled_upstream_routes_nowhere(configuration) -> None:  # noqa: ANN001
    filters, state = configuration
    state.set_enabled("default", False)
    settings = Settings(database_path=":memory:", upstream_base_url=DEAD)

    rows = _map(settings, (filters, state))

    assert rows == [("ravis/default/*", NO_ROUTE, "disabled in providers.json")]


def test_a_translating_provider_is_shown_only_as_a_direct_address(configuration) -> None:  # noqa: ANN001
    """Path B is unreachable from a pool, so a bare pattern would be a lie (§6)."""
    settings = Settings(database_path=":memory:", anthropic_api_key="sk-not-a-real-key")

    rows = _map(settings, configuration)

    assert rows == [
        ("ravis/anthropic/*", "anthropic (translated)", "RAVIS_ANTHROPIC_API_KEY")
    ]


def test_every_translated_provider_gets_a_row_not_just_the_first_one(configuration) -> None:  # noqa: ANN001
    """M7 added Gemini to Path B and this table did not hear about it.

    The rows named Anthropic literally, so `ravis/google/*` — an address that
    routes perfectly well — was absent from the one diagnostic whose job is to
    say what routes where.
    """
    settings = Settings(
        database_path=":memory:",
        anthropic_api_key="sk-not-a-real-key",
        google_api_key="not-a-real-key",
    )

    rows = _map(settings, configuration)

    assert rows == [
        ("ravis/anthropic/*", "anthropic (translated)", "RAVIS_ANTHROPIC_API_KEY"),
        ("ravis/google/*", "google (translated)", "RAVIS_GOOGLE_API_KEY"),
    ]


def test_a_key_from_the_credential_store_still_produces_a_row(
    configuration,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """The screen stores it in a file; the table only looked at the settings.

    So a provider configured the way M10 intends people configure it had no row
    at all, and `doctor` reported nothing where the router had a working route.
    The source is named rather than assumed, because "credential store" and an
    environment variable send an operator to two different places.
    """
    store = CredentialStore(
        allow_environment=False,
        keychain=False,
        file=CredentialFile(tmp_path / "credentials.json"),
    )
    store.store("google", "not-a-real-key")
    filters, state = configuration

    rows = [
        (row.model, row.provider, row.decided_by)
        for row in resolve_provider_map(
            Settings(database_path=":memory:"),
            filters=filters,
            state=state,
            credentials=store,
        )
    ]

    assert rows == [("ravis/google/*", "google (translated)", "credential store (file)")]


def test_a_disabled_translated_provider_says_so_whatever_supplied_its_key(
    configuration,  # noqa: ANN001
    tmp_path: Path,
) -> None:
    """Disabling has to win over a credential from either source."""
    store = CredentialStore(
        allow_environment=False,
        keychain=False,
        file=CredentialFile(tmp_path / "credentials.json"),
    )
    store.store("google", "not-a-real-key")
    filters, state = configuration
    state.set_enabled("google", False)

    rows = [
        (row.model, row.provider, row.decided_by)
        for row in resolve_provider_map(
            Settings(database_path=":memory:"),
            filters=filters,
            state=state,
            credentials=store,
        )
    ]

    assert rows == [("ravis/google/*", NO_ROUTE, "disabled in providers.json")]


def test_a_malformed_declaration_is_reported_rather_than_raised(configuration) -> None:  # noqa: ANN001
    """`doctor` is what you run *because* the configuration is broken.

    Raising here would make the one command that can explain a bad
    `RAVIS_UPSTREAMS` the one command that dies on it.
    """
    settings = Settings(database_path=":memory:", upstreams="{not a list}")

    rows = _map(settings, configuration)

    assert len(rows) == 1
    assert rows[0][1] == NO_ROUTE
    assert "malformed RAVIS_UPSTREAMS" in rows[0][2]


def test_a_name_claimed_by_both_tables_is_reported() -> None:
    """`google` is declarable as an upstream *and* is a translated provider."""
    assert shared_provider_names(["lmstudio", "google", "openrouter"]) == ["google"]


def test_a_name_claimed_by_one_table_is_not_a_collision() -> None:
    """The common case must stay silent, or the warning becomes noise."""
    assert shared_provider_names(["lmstudio", "openrouter", "openai"]) == []


def test_the_collision_warning_names_the_provider_that_wins(caplog) -> None:  # noqa: ANN001
    """Startup says it out loud, because that is when someone can still act.

    RAVIS resolves this fine — a direct address reaches the translated provider
    while it has a credential. It resolved it *silently*, which is how two
    defects lived in it: `doctor` showing no row for a translated provider, and
    a screen crediting one provider with another's catalogue.
    """
    settings = Settings(
        database_path=":memory:",
        upstreams='[{"name": "google", "kind": "google"}]',
        _env_file=None,  # type: ignore[call-arg]
    )

    with caplog.at_level(logging.WARNING, logger="ravis"):
        create_app(settings)

    assert any(
        "'google'" in record.getMessage() and "translated" in record.getMessage()
        for record in caplog.records
    ), caplog.text
