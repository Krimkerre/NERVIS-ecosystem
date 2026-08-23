"""Reading available memory — and specifically, not reading it the wrong way."""

from __future__ import annotations

from ravis.runtime.resources import MemoryReading, read_memory


def test_an_unknown_reading_is_not_treated_as_pressure() -> None:
    """Refusing to load anything because the platform could not be read would
    turn a diagnostic gap into a routing outage."""
    assert MemoryReading().under_pressure is False


def test_an_unknown_reading_reports_no_fraction_rather_than_zero() -> None:
    """Zero would read as "no memory at all" (runbook §14.4)."""
    assert MemoryReading().free_fraction is None


def test_pressure_is_detected_below_the_threshold() -> None:
    assert MemoryReading(available_bytes=1, total_bytes=100).under_pressure is True


def test_ample_memory_is_not_pressure() -> None:
    assert MemoryReading(available_bytes=50, total_bytes=100).under_pressure is False


def test_the_real_machine_reports_a_plausible_reading() -> None:
    """Guards the parser against the platform, which no fixture can.

    Deliberately loose: this asserts the reading is *sane*, not what it says.
    The failure it catches is a parse that silently yields nonsense — which is
    exactly what happened when `top`'s "unused" figure was mistaken for
    available memory and reported 619 MB against 8.6 GB actually free.
    """
    reading = read_memory()

    if not reading.is_known:
        return  # An unsupported platform is allowed; a wrong answer is not.
    assert reading.available_bytes is not None and reading.total_bytes is not None
    assert 0 < reading.available_bytes <= reading.total_bytes
    assert reading.total_bytes > 512 * 2**20
