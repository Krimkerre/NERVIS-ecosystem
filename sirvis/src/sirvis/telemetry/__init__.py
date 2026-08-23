"""Reading the machine SIRVIS is measuring on.

Every benchmark references an immutable snapshot of this (§5.1), because a
number measured on a thermally-throttled laptop with 2 GB free is not the same
number measured on the same machine an hour later, and a result that cannot say
which one it was is not evidence.

Two probes, at deliberately different weights. `detect_system` builds the whole
snapshot and costs hundreds of milliseconds; `MemoryProbe` reads one thing and
costs a few, because §11.8 wants memory sampled eight times around a single
generation and a snapshot-weight probe at that rate would measure itself.
"""

from sirvis.telemetry.memory import (
    AFTER_LOAD,
    BASELINE,
    BEFORE_GENERATION,
    DURING_GENERATION,
    POST_RUN,
    POST_UNLOAD,
    MemoryProbe,
    MemorySample,
    MemoryWatcher,
)
from sirvis.telemetry.system import SystemSnapshot, detect_system

__all__ = [
    "AFTER_LOAD",
    "BASELINE",
    "BEFORE_GENERATION",
    "DURING_GENERATION",
    "POST_RUN",
    "POST_UNLOAD",
    "MemoryProbe",
    "MemorySample",
    "MemoryWatcher",
    "SystemSnapshot",
    "detect_system",
]
