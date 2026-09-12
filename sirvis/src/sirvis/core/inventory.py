"""Turning what a runtime reports into the domain §6 defines.

One mapping, in one place. The adapter deliberately returns a runtime's records
unreshaped (M2) so that this translation exists exactly once — the alternative
is two versions of it that agree until the day they do not.

The output is an `Inventory`: families, variants, installed builds and whatever
is loaded, with every link recorded rather than assumed. Nothing here probes,
loads or measures. It reads what the runtime already said and organises it, and
anything the runtime did not say stays Unknown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sirvis.core.models import (
    LocalModel,
    ModelFamily,
    ModelVariant,
    RuntimeModelInstance,
)

# A parameter count embedded in a model name: `7b`, `2.6b`, `30b-a3b`. The last
# match wins, because in a mixture-of-experts name the trailing figure is the
# *active* parameter count and that is the one that governs how fast it answers.
_PARAMETERS = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)

# LM Studio marks the second and later loaded copies of one build with `:2`,
# `:3` … Those are instances of the same installed model, not separate models.
_INSTANCE_SUFFIX = re.compile(r":(\d+)$")

# And it names a *variant* with `@q4_k_m`, `@4bit` … Once a variant is loaded by
# its qualified key the catalogue lists it as an entry of its own, so the same
# weights appear twice under two ids — `google/gemma-4-e4b` and
# `google/gemma-4-e4b@4bit`. They are two builds of one family, which is exactly
# what §12.2's identity is for: the family is the same, and `runtime_format` and
# `quantization` are what tell them apart. Left in the family name they would be
# two families, and no comparison between them would group.
_VARIANT_SUFFIX = re.compile(r"@[\w.-]+$")


@dataclass
class Inventory:
    """Everything this machine has, organised but not interpreted."""

    families: dict[str, ModelFamily] = field(default_factory=dict)
    variants: dict[str, ModelVariant] = field(default_factory=dict)
    installed: dict[str, LocalModel] = field(default_factory=dict)
    instances: list[RuntimeModelInstance] = field(default_factory=list)
    # variant_id → the architecture its family recorded, where the two differ.
    architecture_disagreements: dict[str, str] = field(default_factory=dict)

    def by_runtime_key(self, runtime_key: str) -> LocalModel | None:
        """The installed build a runtime calls `runtime_key`, or None.

        The lookup §6 requires RAVIS to use instead of matching names, and it
        works on a **cold** model on purpose: the load-or-don't decision needs
        evidence about a build precisely when it is not loaded.

        None rather than a nearest match. §6 says 404 is a correct answer and
        must stay distinguishable from a guess — a fuzzy hit here would be a
        guess wearing an identity's clothes.
        """
        return self.installed.get(runtime_key)

    def resolve(self, runtime_key: str) -> LocalModel | None:
        """The build behind a key, allowing an unqualified one where it is exact.

        `google/gemma-4-e2b` names one build on this machine and
        `google/gemma-4-e2b@4bit` names the same build; resolving between them
        is not a guess, it is the runtime's own two names for one artefact. So
        an exact hit answers first, and a family key answers only when exactly
        one build sits under it.

        Two builds under one family returns None — the same refusal
        `runtimes.variants` makes for a loaded model, and for the same reason:
        `google/gemma-4-e4b` with an MLX and a GGUF installed is a question,
        not an address, and choosing one would file evidence about one build
        under the identity of another.
        """
        exact = self.installed.get(runtime_key)
        if exact is not None:
            return exact
        under = self.builds_of(runtime_key)
        return under[0] if len(under) == 1 else None

    def builds_of(self, base_key: str) -> list[LocalModel]:
        """Every installed build whose family key is `base_key`."""
        return [
            build for key, build in self.installed.items()
            if _VARIANT_SUFFIX.sub("", key) == base_key
        ]

    def instances_of(self, local_model_id: str) -> list[RuntimeModelInstance]:
        """Every loaded copy of one installed build.

        A list, not an optional, because one build genuinely can be resident
        more than once — LM Studio loads a second instance rather than
        reconfiguring the first when a request wants more context than the
        running copy has. Modelling it as at-most-one would make that invisible,
        and it is exactly the thing worth seeing.
        """
        return [i for i in self.instances if i.local_model_id == local_model_id]

    def as_dict(self) -> dict[str, Any]:
        return {
            "families": [f.as_dict() for f in self.families.values()],
            "variants": [
                {
                    **v.as_dict(),
                    "family_architecture_disagrees": v.variant_id
                    in self.architecture_disagreements,
                }
                for v in self.variants.values()
            ],
            "installed": [m.as_dict() for m in self.installed.values()],
            "instances": [i.as_dict() for i in self.instances],
        }


def build_inventory(records: list[dict[str, Any]], runtime: str = "lmstudio") -> Inventory:
    """Organise a runtime's model records into the §6 domain.

    Records arrive exactly as the runtime gave them. Embeddings models are kept
    rather than filtered: SIRVIS's job is to say what is installed, and deciding
    an embeddings model is uninteresting is a consumer's judgement to make with
    the `model_type` field, not one to make on its behalf by omission.
    """
    inventory = Inventory()
    for record in records:
        runtime_key = str(record.get("id") or "").strip()
        if not runtime_key:
            continue
        _absorb(inventory, record, runtime_key, runtime)
    return inventory


def _absorb(inventory: Inventory, record: dict[str, Any], runtime_key: str,
            runtime: str) -> None:
    """Fold one runtime record into the four concepts."""
    # Two keys, because a build and its family are two different things and this
    # function needs both. `build_key` keeps `@4bit` — it is what addresses one
    # build and what makes two builds of one family separately loadable —
    # while `base_key` drops it to group them.
    #
    # **They were the same key, and it cost a build.** LM Studio publishes a
    # loaded variant under its qualified id and the rest of the group under the
    # plain one, so `google/gemma-4-e4b@4bit` (MLX, resident) and
    # `google/gemma-4-e4b` (GGUF) arrive as two records; keying the installed
    # map on the family collapsed them, first writer won, and the machine
    # reported one build where it holds two. The screen showed MLX and no GGUF,
    # and a benchmark of the missing one had nowhere to file.
    build_key = _INSTANCE_SUFFIX.sub("", runtime_key)
    base_key = _VARIANT_SUFFIX.sub("", build_key)
    family = _family_for(inventory, base_key, record)
    variant = ModelVariant.derive(
        family=family,
        publisher=_text(record.get("publisher")),
        runtime_format=_text(record.get("compatibility_type")),
        quantization=_text(record.get("quantization")),
        architecture=_text(record.get("arch")),
    )
    inventory.variants.setdefault(variant.variant_id, variant)
    _note_architecture_disagreement(inventory, family, variant)

    installed = LocalModel.derive(
        variant=variant,
        runtime=runtime,
        # The qualified key, because it is the one that addresses this build
        # rather than whichever of its siblings the app has selected. The
        # display name stays unqualified: `runtime_format` and `quantization`
        # travel with every record and are what §12.2 says tell builds apart.
        runtime_key=build_key,
        display_name=base_key,
        declared_context=_number(record.get("max_context_length")),
        # The size on disk, when the runtime's CLI reported it (`with_sizes`).
        installed_size_bytes=_number(record.get("size_bytes")),
    )
    inventory.installed.setdefault(build_key, installed)

    state = str(record.get("state") or "unknown")
    if state != "not-loaded":
        inventory.instances.append(
            RuntimeModelInstance(
                # The runtime's own key for the instance, which for a second copy
                # carries the `:2` suffix — that is what makes two instances of
                # one build separately addressable.
                instance_id=runtime_key,
                local_model_id=installed.local_model_id,
                runtime=runtime,
                state=state,
                effective_context=_number(record.get("loaded_context_length")),
                effective_configuration={
                    "context_length": _number(record.get("loaded_context_length")),
                    "quantization": _text(record.get("quantization")),
                    "runtime_format": _text(record.get("compatibility_type")),
                },
            )
        )


def _family_for(inventory: Inventory, base_key: str, record: dict[str, Any]) -> ModelFamily:
    """The family this build belongs to, created on first sight.

    First writer wins for the architecture, and the second is not allowed to
    overwrite it — that is what turns a disagreement into a recorded fact rather
    than into whichever packaging happened to be inventoried last.
    """
    candidate = ModelFamily.derive(
        display_name=base_key,
        architecture=_text(record.get("arch")),
        parameter_billions=_parameter_billions(base_key),
    )
    return inventory.families.setdefault(candidate.family_id, candidate)


def _note_architecture_disagreement(inventory: Inventory, family: ModelFamily,
                                    variant: ModelVariant) -> None:
    """Record where a build's architecture differs from its family's.

    §6: link equivalent variants under one family, but never assert equivalence
    solely because names look similar. Keeping the link *and* the disagreement
    is how both halves of that hold — this machine's `granite-4.0-h-tiny` is
    `granitehybrid` in its GGUF packaging and `granitemoehybrid` in its MLX one,
    and a domain that silently picked one would be inventing agreement.
    """
    declared = family.architecture.value
    if declared and variant.architecture and variant.architecture != declared:
        inventory.architecture_disagreements[variant.variant_id] = str(declared)


def _parameter_billions(name: str) -> float | None:
    """A parameter count read from a name, or None.

    An inference and labelled as one wherever it surfaces — `Claim` carries
    `INFERRED` for exactly this. Useful to show a person, never sufficient to
    route on, and never allowed to become `MEASURED` later (§12.1).
    """
    matches = _PARAMETERS.findall(name)
    return float(matches[-1]) if matches else None


def _text(value: Any) -> str | None:
    """A non-empty string, or None. Empty and absent mean the same thing here."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> int | None:
    """An integer, or None when the runtime did not report one.

    Never zero as a stand-in. A context length of 0 would be a claim that the
    model holds nothing, and "the runtime did not say" is a different statement
    (runbook §14.4).
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
