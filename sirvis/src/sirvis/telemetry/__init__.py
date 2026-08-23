"""Reading the machine SIRVIS is measuring on.

Every benchmark references an immutable snapshot of this (§5.1), because a
number measured on a thermally-throttled laptop with 2 GB free is not the same
number measured on the same machine an hour later, and a result that cannot say
which one it was is not evidence.
"""

from sirvis.telemetry.system import SystemSnapshot, detect_system

__all__ = ["SystemSnapshot", "detect_system"]
