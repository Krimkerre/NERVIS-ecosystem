"""The model domain: four concepts §6 says must never be collapsed.

    ModelFamily          a conceptual base model
    ModelVariant         a specific build or conversion of it
    LocalModel           that build, installed here
    RuntimeModelInstance that build, loaded right now

Collapsing any two of them loses something the rest of SIRVIS needs. A family
and a variant differ because GGUF Q4_K_M and MLX 4-bit are *not* the same thing
to measure — §12.2 makes format and quantization part of evidence identity, and
this machine has one model where the two packagings disagree about whether tool
calls work at all. A variant and an installed model differ because a build can
be catalogued without being here. An installed model and an instance differ
because the same build can be resident twice with different context lengths,
which is not hypothetical: LM Studio does it by itself.

**Identifiers are derived, not generated.** §6's exit requires stable IDs to
survive a restart, and §5.2 requires display names never to be identifiers. A
random UUID satisfies neither on its own — it needs a table to survive, and a
wiped database orphans every result that referenced it. So an ID is a hash of
the attributes that make the thing what it is: the same build inventoried on a
fresh database gets the same ID, and two builds that differ in any identifying
attribute cannot collide however similar their names look.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Provenance(str, Enum):
    """Where a value came from (§12.1).

    The invariant this exists to serve is that provenance is never *upgraded*.
    Something `INFERRED` does not become `DECLARED` because it was stored, and
    neither becomes `MEASURED` because a benchmark ran on something adjacent.

    `INFERRED` is the one to be suspicious of. It means SIRVIS worked a value
    out from a name, which is a guess with a good hit rate and no guarantee —
    useful to show a person, never sufficient to route on.
    """

    DECLARED = "DECLARED"
    INFERRED = "INFERRED"
    MEASURED = "MEASURED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Claim:
    """One value and where it came from.

    Small and everywhere on purpose. A bare number in this domain would be
    indistinguishable from a measured one by the time it reached RAVIS, and
    §12.1 exists because that is precisely the mistake that cannot be undone
    downstream.
    """

    value: Any
    provenance: Provenance = Provenance.UNKNOWN
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "provenance": self.provenance.value, "detail": self.detail}


def _identity(prefix: str, *parts: str | None) -> str:
    """A stable, opaque ID derived from the attributes that identify a thing.

    Truncated to 16 hex characters — 64 bits, against a population of tens of
    models on one machine, where a collision would need something closer to
    four billion. The prefix is for the human reading a log line; nothing parses
    it, and nothing should.

    `None` and empty string are folded together deliberately: a missing
    attribute and an absent one identify the same thing, and treating them
    differently would give one build two IDs depending on how a runtime happened
    to report it.
    """
    canonical = "\\x1f".join((part or "").strip().lower() for part in parts)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class ModelFamily:
    """A conceptual base model, and how confident we are that it is one.

    **Family linking preserves uncertainty** (§6). Two builds are grouped only
    when there is a basis for it, and the basis is recorded rather than assumed:
    `link_basis` says whether the grouping came from something declared or from
    two names looking alike. Asserting equivalence from a name is the specific
    thing §6 forbids, and this machine shows why — `granite-4.0-h-tiny` is
    reported as `granitehybrid` by one packaging and `granitemoehybrid` by the
    other, so even the architecture disagrees with itself.
    """

    family_id: str
    display_name: str
    architecture: Claim
    parameter_billions: Claim
    link_basis: str = "name"

    @staticmethod
    def derive(display_name: str, architecture: str | None,
               parameter_billions: float | None) -> ModelFamily:
        """Build a family from what a runtime reported.

        Keyed on the *normalised name alone*, not on architecture. That is the
        decision that groups the two granite packagings together despite their
        architectures disagreeing — which is the useful answer, provided the
        disagreement is then visible rather than resolved. `link_basis` is what
        makes it visible.
        """
        normalised = normalise_family_name(display_name)
        return ModelFamily(
            family_id=_identity("fam", normalised),
            display_name=normalised,
            architecture=(
                Claim(architecture, Provenance.DECLARED, "reported by the runtime")
                if architecture
                else Claim(None, Provenance.UNKNOWN)
            ),
            parameter_billions=(
                Claim(parameter_billions, Provenance.INFERRED, "read from the model name")
                if parameter_billions is not None
                else Claim(None, Provenance.UNKNOWN)
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "display_name": self.display_name,
            "architecture": self.architecture.as_dict(),
            "parameter_billions": self.parameter_billions.as_dict(),
            "link_basis": self.link_basis,
        }


@dataclass(frozen=True)
class ModelVariant:
    """One usable build of a family — a format and a quantization of it.

    Separate from the family because §12.2 makes format and quantization part of
    evidence identity. Evidence about an MLX 4-bit build is not evidence about
    the GGUF Q4_K_M build of the same weights, and on this machine those two
    reach 1/8 and 8/8 on the same tool-call trial.
    """

    variant_id: str
    family_id: str
    publisher: str | None
    runtime_format: str | None
    quantization: str | None
    architecture: str | None
    source_repository: str | None = None
    source_revision: str | None = None
    size_bytes: int | None = None

    @staticmethod
    def derive(family: ModelFamily, publisher: str | None, runtime_format: str | None,
               quantization: str | None, architecture: str | None) -> ModelVariant:
        """Identity is family plus everything that makes this build distinct."""
        return ModelVariant(
            variant_id=_identity("var", family.family_id, publisher, runtime_format, quantization),
            family_id=family.family_id,
            publisher=publisher,
            runtime_format=runtime_format,
            quantization=quantization,
            architecture=architecture,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "family_id": self.family_id,
            "publisher": self.publisher,
            "runtime_format": self.runtime_format,
            "quantization": self.quantization,
            "architecture": self.architecture,
            "source_repository": self.source_repository,
            "source_revision": self.source_revision,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class LocalModel:
    """A variant installed on this machine.

    `runtime_key` is the field RAVIS needs and the reason this concept is not
    merged into `ModelVariant`. It is the name *the runtime* uses — the only
    handle RAVIS ever sees — and it belongs here rather than on an instance
    because §6 requires the lookup to work on a **cold** model. A resolution
    that needed the model loaded would be useless for the load-or-don't decision
    it exists to inform.
    """

    local_model_id: str
    variant_id: str
    runtime_key: str
    runtime: str
    display_name: str
    declared_context: Claim
    storage_path: str | None = None
    installed_size_bytes: int | None = None

    @staticmethod
    def derive(variant: ModelVariant, runtime: str, runtime_key: str, display_name: str,
               declared_context: int | None,
               installed_size_bytes: int | None = None) -> LocalModel:
        """Identity is the variant plus the runtime that holds it and its key.

        Including `runtime_key` is what stops two builds with identical display
        names colliding — §6's exit criterion — because a runtime cannot use one
        key for two things and still address them.
        """
        return LocalModel(
            local_model_id=_identity("lm", variant.variant_id, runtime, runtime_key),
            variant_id=variant.variant_id,
            runtime_key=runtime_key,
            runtime=runtime,
            display_name=display_name,
            declared_context=(
                Claim(declared_context, Provenance.DECLARED, "the build's advertised maximum")
                if declared_context is not None
                else Claim(None, Provenance.UNKNOWN)
            ),
            installed_size_bytes=installed_size_bytes,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "local_model_id": self.local_model_id,
            "variant_id": self.variant_id,
            "runtime_key": self.runtime_key,
            "runtime": self.runtime,
            "display_name": self.display_name,
            "declared_context": self.declared_context.as_dict(),
            "storage_path": self.storage_path,
            "installed_size_bytes": self.installed_size_bytes,
        }


@dataclass(frozen=True)
class RuntimeModelInstance:
    """A build that is loaded right now, with the configuration it actually got.

    The distinction from `LocalModel` is the one that has already caused a real
    problem. A build advertising a 32768 maximum can be *loaded* at 8192, and a
    consumer reading the advertised number plans a request the running instance
    will refuse. `effective_context` is what is true now; `declared_context` on
    the installed model is what the build could do.
    """

    instance_id: str
    local_model_id: str
    runtime: str
    state: str
    effective_context: int | None
    requested_configuration: dict[str, Any] = field(default_factory=dict)
    effective_configuration: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "local_model_id": self.local_model_id,
            "runtime": self.runtime,
            "state": self.state,
            "effective_context": self.effective_context,
            "requested_configuration": self.requested_configuration,
            "effective_configuration": self.effective_configuration,
        }


def normalise_family_name(display_name: str) -> str:
    """Reduce a runtime's name for a build to the family it belongs to.

    Strips the publisher prefix, the packaging suffixes runtimes append, and the
    instance suffix LM Studio adds when it loads a second copy. What survives is
    a name shared by builds of the same base model.

    Deliberately conservative. Over-normalising merges two genuinely different
    models under one family, and §6 forbids asserting equivalence from names —
    so anything not on this short list is left alone even when it looks like
    noise.
    """
    name = display_name.split("/")[-1].strip().lower()
    # LM Studio appends `:2`, `:3` … to the second and later loaded instances of
    # one build. That is an instance marker, never part of the model's name.
    name = name.split(":")[0]
    for suffix in ("-mlx", "-gguf", "-instruct", "-it", "-chat"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip("-") or display_name.strip().lower()
