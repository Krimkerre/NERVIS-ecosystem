"""The Linux tray's menu, meters and icon — everything that can be checked without a desktop.

The tray is a translation of the macOS menu bar app, so most of these tests are about the two
agreeing: the same lines for the same launcher answer, the same rules about what may be clicked,
the same sentences in the dialogs. The GTK half is exercised on a real desktop (see STATUS); the
half here decides every word it draws.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging" / "linux"))

from nervis_tray import menu  # noqa: E402
from nervis_tray.mark import svg, write_icons  # noqa: E402
from nervis_tray.meters import CPUMeter, gpu_percent  # noqa: E402


def _service(name: str, group: str = "stack", answering: bool = True,
             problem: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"name": name, "group": group, "answering": answering, "problem": problem, **extra}


def _report(**extra: Any) -> dict[str, Any]:
    return {
        "dashboard": "http://127.0.0.1:8790/index.html",
        "services": [
            _service("SIRVIS", address="http://127.0.0.1:8790/#/sirvis"),
            _service("RAVIS"),
            _service("NERVIS"),
            _service("CLARVIS", group="editor", answering=False),
            _service("Ollama", group="runtime"),
            _service("LM Studio", group="runtime", answering=False),
        ],
        "notifications": {"unread": 0, "screen": "http://127.0.0.1:8790/#/notifications"},
        **extra,
    }


def _lines(report: dict[str, Any] | None, models: dict[str, Any] | None = None,
           **context: Any) -> list[str]:
    return menu.render_text(menu.build(report, models, menu.Context(**context)))


# ── The same lines as the Mac menu ───────────────────────────────────────────


def test_a_whole_stack_draws_as_the_mac_menu_does() -> None:
    """The Mac app's `--print-menu` for the same answer, with its dots spelt as circles."""
    lines = _lines(_report())

    assert lines[0] == "  [The stack is running]"
    assert "  Open NERVIS dashboard" in lines
    assert "  🟢  SIRVIS   running  → Open SIRVIS in the browser" in lines
    # CLARVIS with no editor open is grey, never red: that is normal, not a fault.
    assert "  ⚪  CLARVIS   not running" in lines
    assert "  🔴  LM Studio   not running" in lines
    assert lines[-1] == "  Quit NERVIS and stop the stack"


def test_a_silent_service_is_named_in_the_headline_with_what_clears_it() -> None:
    report = _report()
    report["services"][1] = _service("RAVIS", answering=False,
                                     problem="running as process 700 but not answering")
    lines = _lines(report)

    assert lines[0] == "  [Not answering: RAVIS]"
    assert "  🔴  RAVIS   not answering" in lines
    # Full strength, not greyed: the Mac draws a service's problem in the menu's own text colour.
    assert "       RAVIS is running as process 700 but not answering." in lines
    assert "       Quit NERVIS and open it again to restart the stack." in lines


def test_the_headline_follows_the_phase() -> None:
    assert menu.headline(None, "starting") == "Starting the stack…"
    assert menu.headline(_report(), "stopping") == "Stopping the stack…"
    assert menu.headline(_report(), "stopped") == "The stack is stopped"


def test_unread_notifications_get_a_line_and_none_are_counted_while_stopping() -> None:
    report = _report(notifications={"unread": 3, "screen": "x"})

    assert "  3 unread notifications" in _lines(report)
    assert menu.unread(report, "stopping") == 0
    assert menu.unread_text(1) == "1 unread notification"


def test_quitting_greys_out_the_dashboard_and_says_it_is_stopping() -> None:
    lines = _lines(_report(), phase="stopping")

    assert "  Open NERVIS dashboard  (disabled)" in lines
    assert lines[-1] == "  Stopping the stack…  (disabled)"


# ── Machine figures ──────────────────────────────────────────────────────────


def test_a_busy_figure_is_flagged_where_the_mac_draws_it_red() -> None:
    """The owner's threshold: 85 stays plain, 86 is flagged."""
    assert menu.figures(85, 86, None) == ["CPU 85%", "GPU 86% ⚠"]


def test_a_figure_nobody_measured_is_left_out_rather_than_shown_as_zero() -> None:
    lines = menu.figures(None, None, {"memory_total_bytes": 8 * menu.GIBIBYTE,
                                      "memory_available_bytes": 2 * menu.GIBIBYTE})

    assert lines == ["Memory 75% · 6.0 of 8.0 GB"]


# ── LM Studio's models ───────────────────────────────────────────────────────

MODELS = {"available": True, "max_loaded": 2, "models": [
    {"key": "a", "name": "qwen-coder", "format": "mlx", "quantization": "4bit",
     "size_bytes": 4 * menu.GIBIBYTE, "loaded": True, "held_by_menu": True},
    {"key": "b", "name": "gemma", "format": "gguf", "quantization": "Q4",
     "size_bytes": 2 * menu.GIBIBYTE, "loaded": True, "held_by_menu": False},
    {"key": "c", "name": "llama", "format": "gguf", "quantization": "Q8",
     "size_bytes": None, "loaded": False, "held_by_menu": False},
]}


def test_models_say_who_loaded_them_and_what_a_click_does() -> None:
    items = menu.lm_studio_menu(MODELS, menu.Context(lm_studio_installed=True,
                                                     lm_studio_running=True))
    by_text = {item.text: item for item in items}

    assert items[0].action == ("open_lmstudio",)
    assert items[1].action == ("quit_lmstudio",)
    assert by_text["✓ qwen-coder   MLX · 4bit · 4.0 GB"].action == ("unload", "a")
    # Loaded by something else: shown, left alone.
    assert not by_text["– gemma   GGUF · Q4 · 2.0 GB"].enabled
    assert by_text["llama   GGUF · Q8"].action == ("load", "c")


def test_no_quit_item_for_an_lm_studio_that_is_not_running() -> None:
    items = menu.lm_studio_menu(MODELS, menu.Context(lm_studio_installed=True))

    assert all(item.action != ("quit_lmstudio",) for item in items)


def test_a_model_being_loaded_is_drawn_working_and_the_others_wait() -> None:
    items = menu.lm_studio_menu(MODELS, menu.Context(model_busy="c"))
    texts = [item.text for item in items]

    assert "llama   loading…" in texts
    assert not next(item for item in items if item.text == "llama   loading…").enabled


def test_an_empty_model_list_says_why() -> None:
    assert menu.lm_studio_menu(None, menu.Context(), sirvis_up=False)[-1].text == \
        "SIRVIS is not running"
    assert menu.lm_studio_menu(None, menu.Context(), sirvis_up=True)[-1].text == \
        "Reading the installed models…"


def test_a_model_too_big_for_free_memory_asks_first() -> None:
    report = _report(system={"memory_available_bytes": 3 * menu.GIBIBYTE})
    warning = menu.fit_warning(MODELS["models"][0], report)

    assert warning is not None
    assert warning[0] == "qwen-coder probably won't fit in free memory"
    # Nothing to judge with: no size, or no reading of free memory.
    assert menu.fit_warning(MODELS["models"][2], report) is None
    assert menu.fit_warning(MODELS["models"][0], _report()) is None


# ── Codex ────────────────────────────────────────────────────────────────────


def _codex(**extra: Any) -> dict[str, Any]:
    return {"state": "signed_in", "signed_in": True, "usage_known": True,
            "windows": [{"label": "week", "remaining_percent": 41.6,
                         "resets_at": "2026-09-20T00:43:00Z"}],
            "runs": [], "address": "http://127.0.0.1:8790/#/ravis", **extra}


def test_codex_is_never_red() -> None:
    for state in list(menu.STATE_WORDS):
        _title, dot = menu.codex_headline(_codex(state=state, usage_known=False))
        assert dot != menu.RED, state


def test_a_waiting_task_leads_the_codex_row_and_offers_a_stop() -> None:
    run = {"id": "t1", "turn_id": "u1", "project": "demo", "state": "waiting_on_you",
           "waiting_minutes": 12, "attached_windows": 0}
    items = menu.codex_items(_codex(runs=[run]), menu.Context())

    assert items[0].text == f"{menu.ORANGE}  Codex · 1 task · 1 waiting for your answer"
    assert items[1].text == "     demo — waiting for your answer · 12 min, no editor open"
    assert any(item.action == ("stop_task", "t1") for item in items[1].submenu or [])


def test_a_task_being_stopped_offers_no_second_stop() -> None:
    run = {"id": "t1", "turn_id": "u1", "project": "demo", "state": "running"}
    assert menu.stop_offer(run, {"t1": "u1"}) == ("unavailable", "Stopping…")
    assert menu.task_line(run, {"t1": "u1"}) == "demo — stopping…"


def test_sign_in_is_offered_only_where_ravis_would_start_one() -> None:
    assert menu.can_sign_in(_codex(state="signed_out", signed_in=False))
    assert not menu.can_sign_in(_codex())
    assert not menu.can_sign_in(_codex(state="untested_version", signed_in=False,
                                       runtime={"verdict": "untested"}))


def test_the_dialogs_say_the_mac_apps_sentences_for_each_exit() -> None:
    assert menu.stop_exit(9, None) == \
        "That task isn't running any more, so there was nothing to stop."
    assert menu.retest_result(11, None)[0] == "A file rule didn't hold"
    assert menu.sign_in_exit(5, None).startswith("Another program is holding the sign-in ports")


def test_minutes_and_times_read_as_the_mac_writes_them() -> None:
    assert menu.minutes(12.4) == "12 min"
    assert menu.minutes(135) == "2 h 15 min"
    assert menu.clock(None) == "at a time not given"


# ── Meters and the mark ──────────────────────────────────────────────────────


def test_cpu_is_the_share_of_busy_ticks_since_the_last_reading(tmp_path: Path) -> None:
    stat = tmp_path / "stat"
    meter = CPUMeter(stat)
    stat.write_text("cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3\n")
    assert meter.percent() is None, "the first reading has nothing to compare with"
    stat.write_text("cpu  150 0 150 900 0 0 0 0 0 0\n")
    # 100 busy of 200 ticks since the last reading.
    assert meter.percent() == 50


def test_iowait_counts_as_waiting_not_working(tmp_path: Path) -> None:
    stat = tmp_path / "stat"
    meter = CPUMeter(stat)
    stat.write_text("cpu  0 0 0 0 0 0 0 0\n")
    meter.percent()
    stat.write_text("cpu  10 0 0 40 50 0 0 0\n")
    assert meter.percent() == 10


def test_a_gpu_whose_driver_states_nothing_shows_no_line(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert gpu_percent(tmp_path) is None
    card = tmp_path / "card0" / "device"
    card.mkdir(parents=True)
    (card / "gpu_busy_percent").write_text("37\n")
    assert gpu_percent(tmp_path) == 37


def test_the_mark_is_valid_svg_with_a_faint_pupil_when_the_stack_is_not_whole(
        tmp_path: Path) -> None:
    whole, partial, blink = svg(True), svg(False), svg(True, pupil=False)
    for drawing in (whole, partial, blink):
        ElementTree.fromstring(drawing)
    assert 'fill-opacity="1"' in whole
    assert 'fill-opacity="0.35"' in partial
    assert blink.count("<path") == 1, "the blink draws the ring alone"
    written = write_icons(tmp_path / "icons")
    assert (written / "nervis-tray-whole-symbolic.svg").exists()
