"""Which of a provider's models RAVIS will actually offer.

OpenRouter publishes **417 models**. Without a filter every one of them lands in
`/v1/models`, which Clarvis renders as a picker, and every one becomes a routing
candidate evaluated on each request. A provider that floods the catalogue is a
provider that makes the whole gateway worse to use.

**Glob patterns, and only glob patterns.** Filtering on capability or on price
is tempting and is deliberately not here: capability predicates belong with
M16's policy engine, and price predicates need a cost model, which is M15 —
inventing one here would be §14's "presenting an estimate as an invoice" in a
different costume. Patterns solve the actual problem, which is that 417 entries
reduce to a handful once someone writes `anthropic/*`.

**The same rule in both places.** The catalogue a client reads and the candidate
set the router picks from apply this identically, because they must never
disagree about whether a model exists — the same reasoning that made declaration
order the one collision rule across three code paths.

**No filter means everything passes.** Not a cap, not a sample, not a truncation
with a warning: a provider with no filter behaves exactly as it did before this
existed. Silent truncation would read as "covered everything" while quietly
hiding models, which is the failure this module is supposed to prevent rather
than commit.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

from ravis.credentials import config_directory


@dataclass(frozen=True)
class ModelFilter:
    """Include and exclude patterns for one provider.

    Case-sensitive matching via `fnmatchcase`: model identifiers are opaque
    strings from a vendor's catalogue, and `GPT-4` and `gpt-4` are not
    interchangeable in a URL path even where a human would read them as the
    same thing.
    """

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.include and not self.exclude

    def matches(self, model_id: str) -> bool:
        """Whether this model is offered.

        **Exclude wins.** An operator writing `openai/*` in include and
        `*-preview` in exclude means "all of OpenAI except the previews", and
        the other precedence would make the exclusion unreachable.

        An empty include list means *everything* rather than *nothing*, so a
        filter that only excludes is useful on its own — `exclude: ["*-vision"]`
        against a provider whose catalogue is otherwise fine.
        """
        if any(fnmatchcase(model_id, pattern) for pattern in self.exclude):
            return False
        if not self.include:
            return True
        return any(fnmatchcase(model_id, pattern) for pattern in self.include)

    def apply(self, model_ids: list[str]) -> list[str]:
        """The subset offered, in the order given."""
        return [model for model in model_ids if self.matches(model)]

    def as_dict(self) -> dict[str, list[str]]:
        return {"include": list(self.include), "exclude": list(self.exclude)}


@dataclass
class ModelFilters:
    """Every provider's filter, persisted as ordinary configuration.

    Beside `providers.json` and for the same reason: nothing here is secret, so
    it is a file a person can read, diff and keep in a dotfiles repository.
    """

    path: Path
    _cache: dict[str, ModelFilter] = field(default_factory=dict)

    @staticmethod
    def default(environment: dict[str, str] | None = None) -> ModelFilters:
        return ModelFilters(config_directory(environment) / "models.json")

    def for_provider(self, name: str) -> ModelFilter:
        return self.all().get(name, ModelFilter())

    def all(self) -> dict[str, ModelFilter]:
        """Every stored filter.

        A malformed file yields none at all — every provider unfiltered, which
        is the same permissive direction `providers.json` takes. A filter is a
        convenience, and a corrupt one should not be able to make a gateway
        serve nothing.
        """
        try:
            with self.path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(name): ModelFilter(
                include=tuple(_strings(record.get("include"))),
                exclude=tuple(_strings(record.get("exclude"))),
            )
            for name, record in payload.items()
            if isinstance(record, dict)
        }

    def set_for(self, name: str, model_filter: ModelFilter) -> ModelFilter:
        """Store one provider's filter, replacing any previous one."""
        payload = {
            provider: stored.as_dict() for provider, stored in self.all().items()
        }
        payload[name] = model_filter.as_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, self.path)
        return model_filter


def _strings(value: object) -> list[str]:
    """Patterns out of whatever the file held.

    Non-strings are dropped rather than coerced. `str(3)` is a pattern that
    matches nothing and looks like it should match something, which is worse
    than the entry not being there.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]
