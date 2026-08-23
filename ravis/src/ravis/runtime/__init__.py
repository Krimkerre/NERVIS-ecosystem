"""Local runtime awareness: what is loaded, and what the machine can take."""

from ravis.runtime.lmstudio import probe_residency
from ravis.runtime.residency import Residency, ResidencySnapshot, residency_rank
from ravis.runtime.resources import MemoryReading, read_memory

__all__ = [
    "MemoryReading",
    "Residency",
    "ResidencySnapshot",
    "probe_residency",
    "read_memory",
    "residency_rank",
]
