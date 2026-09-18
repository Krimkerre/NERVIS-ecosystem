"""How hard a provider is to reach right now, as a number the router can rank on.

§12.3 had RAVIS watch active requests, congestion and providers' stated quotas,
and M20 named no exit that changed a route — so until now nothing here reached
the router. The base review put it plainly: *an existing load display is not a
scheduler*. The owner chose the small half on 18 September 2026 — load as a
penalty inside the ranking that already exists, rather than a queue in front of
the providers — and this is that penalty.

**Three levels, two of which fire, and each one is something a provider said**, rather than a
threshold invented here:

- `REFUSING` — the provider answered with a rate limit or an overload moments
  ago, or asked for a wait that has not run out. It is not failing, so §10 does
  not exclude it; it is simply the worst place to send the next request.
- `BUSY` — the provider's own rate-limit headers say it has almost no headroom
  left.
- `CLEAR` — nothing known against it. The default, and what every provider
  scores when nobody has said anything, because silence is not evidence of
  strain.

A raw count of what RAVIS has in flight to a *hosted* provider is deliberately
not a level. RAVIS is not the only caller of that key and does not know the
limit unless the provider states it, so "eight in flight" would be a number
compared against a ceiling nobody measured.

**A local runtime already generating is deliberately not a level either**, and
this one is a judgement rather than a measurement problem. LM Studio and Ollama
do queue a second generation, so the wait is real and RAVIS's own count is an
honest measure of it — but this term is ranked *above* the pool's preference, so
scoring a busy local runtime would move the next request to whichever candidate
is next in line, and for most pools that candidate is hosted and paid for.
Spending money because the machine is busy is a decision for the owner, not a
side effect of a latency preference. Every level here therefore comes from a
*remote* provider's own statement, which means this can move a request away from
a strained provider and, at worst, onto this machine.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from ravis.reliability.health import HealthRegistry
from ravis.reliability.load import LoadTracker

REFUSING = 2.0
BUSY = 1.0
CLEAR = 0.0

# How recently a refusal has to have happened to still say anything. Long enough
# to cover the burst that caused it — providers state windows in minutes, and a
# 429 is rarely alone — and short enough that a provider is given another chance
# quickly, since the penalty is the only thing keeping traffic off it.
CONGESTION_WINDOW_SECONDS = 60.0

# What counts as almost no headroom, against the provider's own stated limit.
HEADROOM_SHARE = 0.1

# How long a stated limit is worth reading. A remaining count describes a window
# that resets; past this it describes a window that has already reset.
LIMIT_WINDOW_SECONDS = 120.0


def strain_of(
    models: Iterable[str],
    provider_of: Callable[[str], str],
    load: LoadTracker | None,
    health: HealthRegistry | None,
) -> Mapping[str, float]:
    """Each model's provider's strain, for the models that have any.

    Only the models with something against them appear, in the same shape and
    for the same reason as `unavailable`: an entry per candidate would put a
    zero beside every model on the machine and read as a measurement of each.

    Tolerant of both halves being absent, because a RAVIS assembled without them
    — which is most tests — must route exactly as it did before this existed.
    """
    refusing, busy = _strained_providers(load, health)
    if not refusing and not busy:
        return {}
    levels = {provider: REFUSING for provider in refusing}
    levels.update({provider: BUSY for provider in busy if provider not in refusing})
    by_model = ((model, levels.get(provider_of(model), CLEAR)) for model in models)
    return {model: level for model, level in by_model if level > CLEAR}


def _strained_providers(
    load: LoadTracker | None, health: HealthRegistry | None
) -> tuple[frozenset[str], frozenset[str]]:
    """The providers that refused just now, and the ones with little left."""
    refusing: frozenset[str] = frozenset()
    busy: frozenset[str] = frozenset()
    if health is not None:
        refusing |= health.congested_within(CONGESTION_WINDOW_SECONDS)
    if load is not None:
        refusing |= load.retrying_after(CONGESTION_WINDOW_SECONDS)
        busy |= load.nearly_spent(HEADROOM_SHARE, LIMIT_WINDOW_SECONDS)
    return refusing, busy
