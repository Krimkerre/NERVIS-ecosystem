"""More than one transparent upstream, and how a model finds its way to one.

M1 forwarded to a single upstream and M8's first half taught RAVIS to *describe*
it. This is the plural half: a deployment with LM Studio and Ollama running side
by side is two upstreams, each with its own catalogue, its own adapter and its
own idea of what it has installed.

**Declared, never discovered.** §3's rule about provider clients applies with
more force here: probing to find out which upstreams exist would make coming up
depend on all of them being awake, and a runtime that is merely asleep would
look like a runtime that is gone.

**Names are the addressing scheme.** An upstream's name occupies the same slot a
translating provider's does — `ravis/<name>/<model>` — because a client asking
for a specific place should not have to know whether that place needs
translation. `chat.py` already parses that shape for Path B; this makes the same
segment mean something on Path A.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# The name a singular configuration gets. Deployments that set
# `RAVIS_UPSTREAM_BASE_URL` and nothing else have exactly one upstream and no
# reason to have named it, but everything downstream keys on a name — so one is
# supplied rather than making the name optional everywhere it is read.
DEFAULT_NAME = "default"


@dataclass(frozen=True)
class UpstreamSpec:
    """One declared upstream, before anything has been built for it."""

    name: str
    base_url: str
    api_key: str = ""
    kind: str = "generic"

    def redacted(self) -> dict[str, Any]:
        """The spec as a diagnostic may print it.

        `api_key` is reported as present or absent and never as a value. §9.7
        keeps credentials out of everything RAVIS reads back, and a management
        surface listing upstreams is exactly where one would otherwise leak.
        """
        return {
            "name": self.name,
            "base_url": self.base_url,
            "kind": self.kind,
            "api_key_configured": bool(self.api_key),
        }


class UpstreamConfigurationError(ValueError):
    """A declaration RAVIS will not guess at.

    Distinct from an unrecognised `kind`, which degrades to the generic adapter
    on purpose: a typo in a kind costs the vendor metadata it would have read,
    while a malformed list means RAVIS does not know where to send anything.
    """


def upstream_specs(settings: Any) -> list[UpstreamSpec]:
    """Every transparent upstream this configuration declares, in order.

    Order is load-bearing — it is what breaks a tie when two upstreams serve the
    same model id — so it is preserved from the configuration rather than sorted
    into something prettier.

    The singular settings still work and still mean what they meant. A
    deployment that sets `RAVIS_UPSTREAM_BASE_URL` gets one upstream named
    `default`, which is what every existing deployment and every test written
    before this describes.
    """
    declared = (getattr(settings, "upstreams", "") or "").strip()
    if declared:
        return _parse(declared)
    if not settings.upstream_base_url:
        return []
    return [
        UpstreamSpec(
            name=DEFAULT_NAME,
            base_url=settings.upstream_base_url,
            api_key=settings.upstream_api_key,
            kind=settings.upstream_kind,
        )
    ]


def _parse(raw: str) -> list[UpstreamSpec]:
    """Read the JSON list form, refusing anything ambiguous."""
    try:
        payload = json.loads(raw)
    except ValueError as failure:
        raise UpstreamConfigurationError(f"not valid JSON: {failure}") from failure
    if not isinstance(payload, list):
        raise UpstreamConfigurationError("must be a JSON list of objects")

    specs: list[UpstreamSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(payload):
        spec = _spec_from(entry, index)
        if spec.name in seen:
            # Not a warning. Names are how a request addresses an upstream, so
            # two upstreams sharing one makes half the addresses in the
            # deployment silently unreachable — and which half depends on
            # iteration order, which is the worst kind of bug to ship.
            raise UpstreamConfigurationError(f"duplicate upstream name {spec.name!r}")
        seen.add(spec.name)
        specs.append(spec)
    return specs


def _spec_from(entry: Any, index: int) -> UpstreamSpec:
    """One list element, validated."""
    if not isinstance(entry, dict):
        raise UpstreamConfigurationError(f"entry {index} is not an object")
    name = str(entry.get("name") or "").strip()
    base_url = str(entry.get("base_url") or "").strip()
    if not name:
        raise UpstreamConfigurationError(f"entry {index} has no name")
    if not base_url:
        raise UpstreamConfigurationError(f"upstream {name!r} has no base_url")
    if "/" in name:
        # A name with a slash in it would split `ravis/<name>/<model>` into more
        # segments than the address has, so the upstream could be declared and
        # never addressed.
        raise UpstreamConfigurationError(f"upstream name {name!r} may not contain '/'")
    return UpstreamSpec(
        name=name,
        base_url=base_url,
        api_key=str(entry.get("api_key") or ""),
        kind=str(entry.get("kind") or "generic"),
    )
