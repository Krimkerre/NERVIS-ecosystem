"""What SIRVIS measured about models loaded together (§12.3, M20's third part).

§12.3 names SIRVIS's contention evidence among what RAVIS tracks, and until 16 September
2026 RAVIS listed it as not read. SIRVIS had been publishing it all along: a Runtime Set's
multi-model run (SIRVIS §10–§11.3) files its interaction matrix on each member's evidence
record, and `/api/v1/evidence` — the read RAVIS already makes for its candidates — returns
it. RAVIS dropped the field. No new surface and no new request: this reads what arrives.

**A pair, not a model.** SIRVIS §10.1 is that co-residency does not transfer between
combinations, so a matrix is kept whole — which set, which revision, which build in which
role — and never folded into a per-model number.

**Tracking, not steering**, like the rest of `reliability/load.py`: nothing here reaches the
router. Slowdowns are copied as SIRVIS computed them (positive is worse for both metrics,
SIRVIS normalises the sign) and a missing figure stays missing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# The two metrics a matrix's degradation carries, and nothing else is copied.
_METRICS = ("time_to_first_token", "tokens_per_second")


def read_pair(item: Any) -> dict[str, Any] | None:
    """One evidence item's interaction matrix, or None when it carries none RAVIS can read."""
    matrix = item.get("interaction_matrix") if isinstance(item, Mapping) else None
    if not isinstance(matrix, Mapping):
        return None
    name = _text(matrix.get("runtime_set"))
    members = matrix.get("members")
    if not name or not isinstance(members, Mapping) or not members:
        return None
    rows = matrix.get("rows")
    return {
        "runtime_set": name,
        "revision": _number(matrix.get("revision")),
        "members": {str(role): str(model) for role, model in members.items()},
        "conditions": [c for c in _list(matrix.get("conditions")) if isinstance(c, str)],
        # A co-loading that failed is a result (SIRVIS §10's gate), and says so.
        "complete": matrix.get("complete") is True,
        "failure": _text(matrix.get("co_residency_failure")),
        "slowdown_percent": {
            str(role): _slowdowns(row)
            for role, row in (rows.items() if isinstance(rows, Mapping) else ())
            if isinstance(row, Mapping)
        },
        "lowest_free_bytes": _lowest_free(matrix.get("memory")),
        "thermal": _strings(matrix.get("thermal")),
        "validity": _text(item.get("validity")),
        "notes": [n for n in _list(item.get("validity_notes")) if isinstance(n, str)],
        "measured_at": _text(item.get("measured_at")),
        "run_id": _text(item.get("run_id")),
    }


Measured = tuple[dict[str, Any], float | None]


def newest_pairs(items: Iterable[Measured]) -> list[Measured]:
    """One pair per set and revision: the newest measurement of it.

    Each member's record carries the same matrix, so a run arrives once per role;
    two runs of one set are two measurements, and the newer is the reading.
    """
    kept: dict[tuple[str, Any], Measured] = {}
    for pair, age in items:
        key = (pair["runtime_set"], pair["revision"])
        held = kept.get(key)
        if held is None or _younger(age, held[1]):
            kept[key] = (pair, age)
    return [kept[key] for key in sorted(kept, key=lambda k: (k[0], str(k[1])))]


def _younger(age: float | None, held: float | None) -> bool:
    return age is not None and (held is None or age < held)


def _slowdowns(row: Mapping[str, Any]) -> dict[str, dict[str, float | None]]:
    by_condition = row.get("degradation_percent")
    if not isinstance(by_condition, Mapping):
        return {}
    return {
        str(condition): {metric: _number(figures.get(metric)) for metric in _METRICS}
        for condition, figures in by_condition.items()
        if isinstance(figures, Mapping)
    }


def _lowest_free(memory: Any) -> dict[str, int | None]:
    if not isinstance(memory, Mapping):
        return {}
    found: dict[str, int | None] = {}
    for condition, reading in memory.items():
        value = reading.get("lowest_available_bytes") if isinstance(reading, Mapping) else None
        found[str(condition)] = None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found[str(condition)] = int(value)
    return found


def _strings(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, str)}


def _number(value: Any) -> Any:
    return value if _is_number(value) else None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
