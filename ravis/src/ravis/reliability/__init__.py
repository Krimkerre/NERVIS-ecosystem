"""Health, retries and fallback — RAVIS.md §10, milestone M12.

Three modules, split by what they know:

    failures.py   what went wrong, and what that permits
    health.py     what has happened to each target, and its circuit breaker
    attempts.py   which target to try next, and when to stop

Nothing here performs I/O. The chain is a policy object the request path drives,
which is what keeps client cancellation safe: code that never wraps a request
cannot accidentally treat a disconnect as a failure to retry (§10).
"""

from ravis.reliability.attempts import Attempt, AttemptChain, RetryBudget
from ravis.reliability.failures import (
    FailureClass,
    HealthScope,
    classify_exception,
    classify_response,
    error_body,
)
from ravis.reliability.health import BreakerState, HealthRegistry, TargetHealth

__all__ = [
    "Attempt",
    "AttemptChain",
    "BreakerState",
    "FailureClass",
    "HealthRegistry",
    "HealthScope",
    "RetryBudget",
    "TargetHealth",
    "classify_exception",
    "classify_response",
    "error_body",
]
