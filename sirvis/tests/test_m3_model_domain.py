"""M3 — the four concepts §6 says must never be collapsed.

    GGUF and MLX variants represented separately; family relation without
    pretending exact equivalence; stable IDs survive restart; duplicate display
    names do not collide.

Every one of those has a real case on this machine, which is why the fixtures
are the shapes LM Studio actually returns rather than invented ones.
`granite-4.0-h-tiny` exists twice — GGUF and MLX — and the two packagings
disagree about their own architecture *and* about whether tool calls work.
"""

from __future__ import annotations

import httpx
from fastapi.testclient import TestClient
from tests.conftest_lmstudio import transport

from sirvis.app import create_app
from sirvis.config import Settings
from sirvis.core.inventory import build_inventory
from sirvis.core.models import Provenance, normalise_family_name
from sirvis.runtimes import LMStudioAdapter

# The two granite packagings, verbatim: same display name, different publisher,
# different format, and architectures that do not match each other.
GRANITE = [
    {
        "id": "lmstudio-community/granite-4.0-h-tiny",
        "publisher": "lmstudio-community",
        "arch": "granitehybrid",
        "compatibility_type": "gguf",
        "quantization": "Q4_K_M",
        "state": "not-loaded",
        "max_context_length": 1048576,
    },
    {
        "id": "mlx-community/granite-4.0-h-tiny",
        "publisher": "mlx-community",
        "arch": "granitemoehybrid",
        "compatibility_type": "mlx",
        "quantization": "4bit",
        "state": "not-loaded",
        "max_context_length": 131072,
    },
]


def test_the_two_packagings_are_one_family_and_two_variants() -> None:
    """§6: link equivalent variants under one family, keep the builds distinct.

    §12.2 makes format and quantization part of evidence identity, and this is
    the pair that proves why — the same weights reach 8/8 and 1/8 on the same
    tool-call trial depending only on which of these two is loaded.
    """
    inventory = build_inventory(GRANITE)

    assert len(inventory.families) == 1
    assert len(inventory.variants) == 2
    assert len({v.variant_id for v in inventory.variants.values()}) == 2


def test_an_architecture_disagreement_is_recorded_rather_than_resolved() -> None:
    """§6: never assert equivalence solely because names look similar.

    These two report `granitehybrid` and `granitemoehybrid`. Silently choosing
    one would be inventing agreement; refusing to link them would lose a
    relation that is real. Both the link and the disagreement are kept.
    """
    inventory = build_inventory(GRANITE)

    disagreeing = inventory.architecture_disagreements

    assert len(disagreeing) == 1
    mlx = next(v for v in inventory.variants.values() if v.runtime_format == "mlx")
    assert mlx.variant_id in disagreeing


def test_duplicate_display_names_do_not_collide() -> None:
    """M3's exit, stated literally. Both are called `granite-4.0-h-tiny`."""
    inventory = build_inventory(GRANITE)

    names = {m.display_name.split("/")[-1] for m in inventory.installed.values()}
    ids = {m.local_model_id for m in inventory.installed.values()}

    assert names == {"granite-4.0-h-tiny"}
    assert len(ids) == 2


def test_identifiers_are_stable_across_a_rebuild() -> None:
    """M3's exit: stable IDs survive restart.

    Derived from the attributes that identify a build rather than generated, so
    a fresh inventory on an empty database reproduces them. A random UUID would
    need a table to survive, and a wiped database would orphan every result
    that referenced one.
    """
    first = build_inventory(GRANITE)
    second = build_inventory(list(reversed(GRANITE)))

    assert {m.local_model_id for m in first.installed.values()} == {
        m.local_model_id for m in second.installed.values()
    }
    assert set(first.families) == set(second.families)


def test_a_parameter_count_read_from_a_name_is_labelled_inferred() -> None:
    """§12.1: provenance is never upgraded, so a guess must arrive as one.

    Useful to show a person, never sufficient to route on — and it must not be
    mistakable for something a benchmark measured.
    """
    inventory = build_inventory([
        {"id": "qwen2.5-coder-7b-instruct", "arch": "qwen2", "state": "not-loaded"}
    ])
    family = next(iter(inventory.families.values()))

    assert family.parameter_billions.value == 7.0
    assert family.parameter_billions.provenance is Provenance.INFERRED


def test_an_unstated_context_is_unknown_rather_than_zero() -> None:
    """Zero would be a claim that the model holds nothing (runbook §14.4)."""
    inventory = build_inventory([{"id": "mystery-model", "state": "not-loaded"}])
    model = inventory.by_runtime_key("mystery-model")

    assert model is not None
    assert model.declared_context.value is None
    assert model.declared_context.provenance is Provenance.UNKNOWN


def test_a_second_loaded_copy_is_an_instance_not_another_model() -> None:
    """LM Studio suffixes `:2` when it loads a second copy of one build.

    Three copies of one 7B were resident on this machine that way, because the
    runtime loads a new instance rather than reconfiguring the running one. They
    are instances of a single installed build, and modelling them as separate
    models would hide both the duplication and its memory cost.
    """
    records = [
        {"id": "coder-7b", "state": "loaded", "loaded_context_length": 8192,
         "max_context_length": 32768},
        {"id": "coder-7b:2", "state": "loaded", "loaded_context_length": 32768,
         "max_context_length": 32768},
    ]

    inventory = build_inventory(records)
    installed = inventory.by_runtime_key("coder-7b")

    assert len(inventory.installed) == 1
    assert installed is not None
    instances = inventory.instances_of(installed.local_model_id)
    assert [i.instance_id for i in instances] == ["coder-7b", "coder-7b:2"]
    assert [i.effective_context for i in instances] == [8192, 32768]


def test_the_advertised_context_and_the_loaded_one_stay_separate() -> None:
    """The distinction that has already caused a wrong route.

    A build advertising 32768 was *loaded* at 8192, and a consumer reading the
    advertised number planned a request the running instance could not take.
    §6 requires artifact, configuration and instance to be distinguishable, and
    this is the pair that matters.
    """
    inventory = build_inventory([
        {"id": "coder-7b", "state": "loaded", "loaded_context_length": 8192,
         "max_context_length": 32768}
    ])
    installed = inventory.by_runtime_key("coder-7b")

    assert installed is not None
    assert installed.declared_context.value == 32768
    assert inventory.instances_of(installed.local_model_id)[0].effective_context == 8192


def test_family_normalisation_is_conservative() -> None:
    """Over-normalising merges genuinely different models under one family.

    Only the publisher prefix, the instance suffix and a short list of packaging
    suffixes are stripped. Anything else is left alone even when it looks like
    noise, because §6 forbids asserting equivalence from names.
    """
    assert normalise_family_name("mlx-community/granite-4.0-h-tiny") == "granite-4.0-h-tiny"
    assert normalise_family_name("qwen2.5-coder-7b-instruct") == "qwen2.5-coder-7b"
    assert normalise_family_name("coder-7b:2") == "coder-7b"
    # Not stripped: these distinguish real models rather than packagings.
    assert normalise_family_name("qwen3-4b-2507") == "qwen3-4b-2507"


def _client() -> TestClient:
    """The real app with a recorded runtime under it (§14.5)."""
    settings = Settings(database_path=":memory:", lmstudio_base_url="http://127.0.0.1:9",
                        _env_file=None)  # type: ignore[call-arg]
    app = create_app(
        settings,
        runtime=LMStudioAdapter(
            "http://runtime.invalid", client=httpx.AsyncClient(transport=transport())
        ),
    )
    return TestClient(app)


def test_the_runtime_key_lookup_resolves_a_cold_model() -> None:
    """§6: the lookup RAVIS uses, and it must work when nothing is loaded."""
    response = _client().get(
        "/api/v1/models", params={"runtime_key": "lmstudio-community/granite-4.0-h-tiny"}
    )
    body = response.json()

    assert body["local_model_id"].startswith("lm_")
    assert body["is_loaded"] is False
    assert body["variant"]["runtime_format"] == "gguf"


def test_an_unknown_runtime_key_is_a_404_not_a_nearest_match() -> None:
    """§6: 404 is a correct answer and must stay distinguishable from a guess.

    A fuzzy hit here would be a guess wearing an identity's clothes, and RAVIS
    would route on it.
    """
    response = _client().get("/api/v1/models", params={"runtime_key": "granite-4.0-h-tiny"})

    assert response.status_code == 404


def test_the_model_listing_keeps_artifact_variant_and_instance_apart() -> None:
    body = _client().get("/api/v1/models").json()
    coder = next(m for m in body["items"] if m["runtime_key"] == "qwen2.5-coder-7b-instruct")

    assert coder["declared_context"]["value"] == 32768
    assert coder["instances"][0]["effective_context"] == 8192
    assert coder["family"]["family_id"].startswith("fam_")
