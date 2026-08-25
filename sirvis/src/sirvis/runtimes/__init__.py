"""Runtime adapters — the things that actually hold a model in memory (§7)."""

from sirvis.runtimes.base import (
    GenerationChunk,
    LoadedModel,
    RuntimeInfo,
    RuntimeLoadFailedError,
    RuntimeState,
    RuntimeTimeoutError,
    RuntimeUnavailableError,
)
from sirvis.runtimes.lmstudio import LMStudioAdapter

__all__ = [
    "GenerationChunk",
    "LMStudioAdapter",
    "LoadedModel",
    "RuntimeInfo",
    "RuntimeState",
    "RuntimeLoadFailedError",
    "RuntimeTimeoutError",
    "RuntimeUnavailableError",
]
