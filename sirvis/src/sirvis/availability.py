"""What LM Studio or Hugging Face being down takes away from SIRVIS (§15.4, 19 September 2026).

Until now every capability was a fixed `available`, so a peer asking what SIRVIS could do
with LM Studio closed was told "everything", and found out at click time. Here SIRVIS keeps
what it last saw of the two things it depends on and restates the affected capabilities:

- **LM Studio** is asked every `runtime_watch_seconds` through the adapter's `health`, which
  never raises and loads nothing — the same read `/api/v1/runtimes` makes.
- **Hugging Face** is never asked for this. Its answer to the last real search or model read
  is what counts (`note_hub`), so SIRVIS sends nothing to the internet on its own, and a
  failure clears the moment a search succeeds. That is why its loss is `degraded` rather
  than `unavailable`: Discover must stay usable for the search that finds it back.

Each reason starts "Waiting on <who>, which <why>: <what is lost>", so a screen can group the
losses by cause without a new field on the wire. The revision moves only when a state or a
reason changes, never on a probe that saw the same thing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from ecosystem_protocol import DEGRADED, UNAVAILABLE, Capability, EcosystemSurface

from sirvis.ecosystem import DECLARED
from sirvis.runtimes.base import RuntimeState

LOG = logging.getLogger(__name__)

LMSTUDIO = "LM Studio"
HUB = "Hugging Face"

# What each dependency's loss costs, per capability: the state it drops to and what stops.
# Capabilities left out keep working without it — results, Runtime Sets and events read
# SIRVIS's own database.
LOSSES: dict[str, dict[str, tuple[str, str]]] = {
    LMSTUDIO: {
        "sirvis.inventory.read@1": (
            DEGRADED, "the machine can be read, but installed models can't be listed"),
        "sirvis.runtime.state.read@1": (
            DEGRADED, "SIRVIS's own leases can be read, but not what LM Studio has loaded"),
        "sirvis.runtime.control@1": (UNAVAILABLE, "no model can be loaded or unloaded"),
        "sirvis.benchmarks.jobs@1": (
            DEGRADED, "jobs can be read and cancelled, but a benchmark run now would fail"),
        "sirvis.recommendations@1": (
            DEGRADED, "a recommendation can't see which models are installed, so it scores none"),
        "sirvis.downloads@1": (DEGRADED, "past downloads can be read, but a new one can't start"),
        "sirvis.model_files@1": (
            UNAVAILABLE, "an installed model's files can't be shown or moved to the Trash"),
    },
    HUB: {
        "sirvis.catalog.read@1": (DEGRADED, "models can't be searched or described right now"),
        "sirvis.downloads@1": (DEGRADED, "a new download can't be checked before it starts"),
    },
}

_WORSE = {DEGRADED: 1, UNAVAILABLE: 2}


@dataclass
class Availability:
    """Why each dependency is down, or nothing for one that is up."""

    down: dict[str, str] = field(default_factory=dict)

    def note(self, surface: EcosystemSurface, who: str, why: str | None) -> bool:
        """Record `who` as down for `why`, or up for None; restate on a change. True if changed."""
        if self.down.get(who) == why:
            return False
        if why is None:
            self.down.pop(who, None)
            LOG.info("%s is answering again; its capabilities are restored", who)
        else:
            self.down[who] = why
            LOG.warning("%s %s; the capabilities that need it say so", who, why)
        restate(surface, self.down)
        return True


def restate(surface: EcosystemSurface, down: dict[str, str]) -> None:
    """Rebuild every declaration from `DECLARED`, lowered for what `down` takes away."""
    declared = surface.declared
    if not isinstance(declared, dict):  # pragma: no cover - sirvis_surface builds a dict
        return
    for capability_id, base in DECLARED.items():
        lost = [(state, f"Waiting on {who}, which {why}: {what}")
                for who, why in sorted(down.items())
                for cid, (state, what) in LOSSES[who].items() if cid == capability_id]
        if lost:
            worst = max((state for state, _ in lost), key=_WORSE.__getitem__)
            declared[capability_id] = Capability(
                version=base.version, state=worst, reason="; ".join(r for _, r in lost))
        else:
            declared[capability_id] = base
    surface.revision += 1


def note_hub(api: Any, failure: Exception | None) -> None:
    """A real Hugging Face read finished: `failure` is what it raised, or None when it answered."""
    # No colon inside `why`: the reason's first colon is where a screen splits cause from loss.
    detail = str(failure).replace(":", " —") if failure is not None else ""
    why = None if failure is None else f"failed SIRVIS's last read ({detail})"
    api.state.availability.note(api.state.ecosystem, HUB, why)


async def watch_lmstudio(api: Any) -> None:
    """Ask LM Studio whether it is there, for as long as the service runs.

    Waits first, like the download watcher: a test that builds the app should not find a
    probe already running against a runtime it never meant to reach.
    """
    while True:
        await asyncio.sleep(api.state.settings.runtime_watch_seconds)
        try:
            info = await api.state.lmstudio.health()
            answering = info.state in (RuntimeState.READY, RuntimeState.BUSY)
            why = None if answering else "isn't answering"
            api.state.availability.note(api.state.ecosystem, LMSTUDIO, why)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the watcher outlives one bad probe
            LOG.exception("the LM Studio watcher hit an error and is continuing")
