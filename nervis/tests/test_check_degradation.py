"""The degradation matrix's rules for an outcome that cannot arise (`tools/check_degradation.py`).

Since 17 September 2026 a cell may say an outcome cannot arise under its condition, so a count like
"no unsafe failover under 7 of 19" can tell impossible from unproved. The rules: the outcome must be
one §10 lists, the cell must say why, and it cannot also claim to establish it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "check_degradation.py"


@pytest.fixture(scope="module")
def matrix() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_degradation", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass looks its module up
    spec.loader.exec_module(module)
    return module


REASON = "Nothing on a request's route reads this, so nothing can fail over."


def cell(matrix: ModuleType, **over: object) -> object:
    fields: dict[str, object] = {
        "condition": "clock skew", "verdict": "COVERED",
        "outcomes": ("truthful",), "residual": "handled, and the rest said here",
        "cannot_arise": {"no_unsafe_failover": REASON},
    }
    return matrix.Cell(**(fields | over))


def test_a_reasoned_mark_passes(matrix: ModuleType) -> None:
    assert matrix._scope_failures(cell(matrix)) == []


@pytest.mark.parametrize(("over", "said"), [
    ({"cannot_arise": {"no_unsafe_failover": "impossible"}}, "without saying why"),
    ({"outcomes": ("truthful", "no_unsafe_failover")}, "both establishes and rules out"),
    ({"cannot_arise": {"teleportation": REASON}}, "which §10 does not list"),
])
def test_a_bad_mark_is_refused(matrix: ModuleType, over: dict[str, object], said: str) -> None:
    problems = matrix._scope_failures(cell(matrix, **over))
    assert any(said in problem for problem in problems), problems


def test_a_ruled_out_outcome_counts_toward_what_the_cell_accounts_for(matrix: ModuleType) -> None:
    """A cell naming every outcome as established or impossible needs no residual."""
    names = [name for name, _ in matrix.OUTCOMES]
    whole = cell(matrix, outcomes=tuple(names[:-1]), residual="",
                 cannot_arise={names[-1]: REASON})
    assert matrix._scope_failures(whole) == []
    short = cell(matrix, outcomes=tuple(names[:-2]), residual="",
                 cannot_arise={names[-1]: REASON})
    assert any("says nothing about the rest" in one for one in matrix._scope_failures(short))


def test_the_real_matrix_accounts_for_failover_under_every_condition(matrix: ModuleType) -> None:
    """Each of the nineteen conditions either establishes "no unsafe failover" or says why it
    cannot arise — the owner's request of 17 September 2026, held from now on."""
    unaccounted = [c.condition for c in matrix.CELLS
                   if "no_unsafe_failover" not in c.outcomes
                   and "no_unsafe_failover" not in c.cannot_arise]
    assert unaccounted == []
