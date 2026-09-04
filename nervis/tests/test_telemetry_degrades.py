"""A probe that cannot answer reports absence, not an exception (§16 item 11).

`SystemSample`'s own docstring states the rule: *"Every field is `| None`-able
where the platform may not answer. Absent is a real state and reported as such:
a zero for 'we could not read swap' is a number somebody will believe."*

Every helper in the module honours that — `_load_average`, `_disk` and
`_thermal_state` each catch and return `None`. The two memory readings were
called raw at the top of `sample_system`, so a platform refusing either took the
whole sample down rather than one field of it. Reported by the external audit
from a sandbox where `psutil.swap_memory()` raised; the same call can be denied
under a hardened profile on a real machine.
"""

from __future__ import annotations

from typing import Any

import pytest

from nervis import telemetry


def test_a_refused_swap_reading_leaves_the_rest_of_the_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The audit's own case: everything else on the machine is still knowable."""
    def refuse() -> Any:
        raise OSError("swap is not readable under this profile")

    monkeypatch.setattr(telemetry.psutil, "swap_memory", refuse)

    sample = telemetry.sample_system(processes=0)

    assert sample.swap_total_bytes is None
    assert sample.swap_used_bytes is None
    assert sample.memory_total_bytes is not None, "memory is unaffected by swap"
    assert sample.os_description, "and the rest of the reading survives"


def test_a_refused_memory_reading_does_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> Any:
        raise OSError("denied")

    monkeypatch.setattr(telemetry.psutil, "virtual_memory", refuse)

    sample = telemetry.sample_system(processes=0)

    assert sample.memory_total_bytes is None
    assert sample.memory_available_bytes is None
    assert sample.swap_total_bytes is not None, "swap is unaffected by memory"


def test_an_ordinary_reading_still_carries_numbers() -> None:
    """The falsifier. Degrading everything to None would satisfy both tests
    above and make the System screen a column of dashes."""
    sample = telemetry.sample_system(processes=0)

    assert sample.memory_total_bytes and sample.memory_total_bytes > 0
