"""NERVIS in the system tray on Linux — the counterpart of the macOS menu bar app.

Starts the stack in the repository this file lives in, shows whether the stack and the model
runtimes are answering and how busy the machine is, blinks while NERVIS has unread notifications,
opens the dashboard in the default browser, and stops the stack when it quits. `menu.py` decides
every line; this file draws them and carries out the clicks.

**An app that holds no code**, like the Mac one. It runs `tools/run.py`, the launcher the start
and stop scripts already run, so a code change needs a restart and never a rebuild; and it knows
nothing about the stack, because `run.py status --json` answers every question the menu shows.
It finds the repository from where it sits in it, so the one thing that breaks it is moving the
checkout — and then the installer, run again, writes the new path into the application entry.

**AppIndicator, not a GTK status icon.** `GtkStatusIcon` never appears on GNOME under Wayland,
which is Ubuntu's default session. An AppIndicator publishes the icon and menu over D-Bus as a
StatusNotifierItem, which KDE, XFCE, Cinnamon, MATE and Budgie show natively and GNOME shows
through the AppIndicator extension Ubuntu ships switched on. Where nothing on the desktop is
listening for one, the icon would simply never appear — so the tray checks, and says how to fix
it rather than running invisibly.

Runs under the **system** Python, because PyGObject and the AppIndicator bindings come from the
distribution's packages (`python3-gi`, `gir1.2-ayatanaappindicator3-0.1` on Debian and Ubuntu).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from nervis_tray import menu as words
from nervis_tray.mark import write_icons
from nervis_tray.meters import CPUMeter, gpu_percent

REPOSITORY = Path(os.environ.get("NERVIS_REPOSITORY") or Path(__file__).resolve().parents[4])
RUN = REPOSITORY / ".run"
LOG = RUN / "tray.log"
STARTED_LINE = "NERVIS tray started"
EXITED_LINE = "NERVIS tray exiting"


# ── Running the launcher ─────────────────────────────────────────────────────


class Launcher:
    """Runs `tools/run.py` in the repository, with the PATH a terminal would have.

    An application started from the desktop's launcher inherits the session's environment,
    which on most distributions leaves out `~/.local/bin` — where code-server's standalone
    install and pip's user scripts live — so the PATH is asked of a login shell once, as the
    Mac app asks one for the same reason.
    """

    def __init__(self, repository: Path) -> None:
        self.repository = repository
        self.environment = dict(os.environ)
        self.environment["PATH"] = login_path() or self.environment.get("PATH", "")

    def command(self, arguments: list[str]) -> list[str]:
        python = shutil.which("python3", path=self.environment["PATH"]) or sys.executable
        return [python, str(self.repository / "tools" / "run.py"), *arguments]

    def run_logged(self, arguments: list[str]) -> int:
        """`start` or `stop`, output to the log. Not piped: `start` leaves detached services
        behind, and a pipe one of them inherited would never reach its end."""
        RUN.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as log:
            return subprocess.call(self.command(arguments), cwd=self.repository,
                                   env=self.environment, stdout=log, stderr=log,
                                   stdin=subprocess.DEVNULL)

    def capture(self, arguments: list[str], timeout: float = 420) -> tuple[int, bytes]:
        try:
            done = subprocess.run(self.command(arguments), cwd=self.repository,
                                  env=self.environment, capture_output=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as failure:
            record(f"`run.py {' '.join(arguments)}` failed: {failure}")
            return 1, b""
        return done.returncode, done.stdout

    def report(self) -> dict[str, Any] | None:
        code, data = self.capture(["status", "--json"], timeout=60)
        return _json(data) if data else None


def login_path() -> str:
    """The PATH a login shell sets up, or "" when none answers."""
    shell = os.environ.get("SHELL") or "/bin/bash"
    try:
        done = subprocess.run([shell, "-l", "-c", "printf '\\n__NERVIS_PATH__%s' \"$PATH\""],
                              capture_output=True, text=True, timeout=10,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    marker = done.stdout.rsplit("__NERVIS_PATH__", 1)
    return marker[1].strip() if len(marker) == 2 else ""


def _json(data: bytes) -> dict[str, Any] | None:
    try:
        answer = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    return answer if isinstance(answer, dict) else None


def error_of(data: bytes) -> str | None:
    """The `error` sentence of the launcher's one JSON line, when it printed one."""
    answer = _json(data) or {}
    error = answer.get("error")
    return error if isinstance(error, str) else None


# ── The log ──────────────────────────────────────────────────────────────────


def record(line: str) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with LOG.open("a") as log:
        log.write(f"{stamp} {line}\n")


def start_log() -> None:
    """Appended, never started afresh, so how the last run ended survives the next launch;
    moved aside past half a megabyte so it cannot grow for ever."""
    before = LOG.read_text(errors="replace") if LOG.exists() else ""
    last_run = before.rsplit(STARTED_LINE, 1)[-1]
    if len(before.encode()) > 512_000:
        LOG.replace(RUN / "tray.previous.log")
    record(f"{STARTED_LINE} for {REPOSITORY}")
    if before and EXITED_LINE not in last_run:
        record("the previous run ended without quitting — killed or crashed; its last lines "
               "are just above, or in tray.previous.log")


# ── LM Studio, found the Linux way ───────────────────────────────────────────


def lm_studio_entry() -> Path | None:
    """LM Studio's application entry, if it has one. LM Studio for Linux is an AppImage with no
    fixed install path, so it is found the way the desktop finds it: by its `.desktop` file."""
    data_dirs = [Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")]
    data_dirs += [Path(one) for one in
                  (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")]
    for directory in data_dirs:
        for entry in sorted((directory / "applications").glob("*.desktop")):
            try:
                text = entry.read_text(errors="replace")
            except OSError:
                continue
            if re.search(r"^Name=LM[ -]?Studio", text, re.MULTILINE | re.IGNORECASE):
                return entry
    return None


def lm_studio_pids() -> list[int]:
    """LM Studio's own processes: the AppImage and the Electron processes it starts."""
    try:
        done = subprocess.run(["pgrep", "-f", "-i", r"lm[ -]?studio"], capture_output=True,
                              text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    mine = os.getpid()
    return [int(one) for one in done.stdout.split() if one.isdigit() and int(one) != mine]


# ── The tray ─────────────────────────────────────────────────────────────────


def main_gui() -> int:  # noqa: C901 — one closure-heavy function is easier to read than a class tree
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gio, GLib, Gtk

    indicator_module = _indicator_module(gi)
    if indicator_module is None:
        _explain_missing("the AppIndicator library isn't installed",
                         "Install gir1.2-ayatanaappindicator3-0.1 (Debian, Ubuntu), "
                         "libayatana-appindicator-gtk3 (Fedora, Arch) or run the NERVIS "
                         "installer again.")
        return 1

    # One copy in the tray. Opening NERVIS again while it runs does nothing more.
    RUN.mkdir(parents=True, exist_ok=True)
    lock = (RUN / "tray.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0

    start_log()
    launcher = Launcher(REPOSITORY)
    icons = write_icons(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
                        / "nervis-tray" / "icons")
    indicator = indicator_module.Indicator.new(
        "nervis", "nervis-tray-partial", indicator_module.IndicatorCategory.APPLICATION_STATUS)
    indicator.set_icon_theme_path(str(icons))
    indicator.set_status(indicator_module.IndicatorStatus.ACTIVE)
    indicator.set_title("NERVIS")

    state: dict[str, Any] = {
        "report": None, "models": None, "context": words.Context(phase="starting"),
        "drawn": None, "blink": None, "refreshing": False, "models_refreshing": False,
    }
    cpu = CPUMeter()
    gtk_menu = Gtk.Menu()
    indicator.set_menu(gtk_menu)

    def on_main(work: Callable[[], None]) -> None:
        GLib.idle_add(lambda: (work(), False)[1])

    def in_background(work: Callable[[], Any], then: Callable[[Any], None]) -> None:
        def body() -> None:
            result = work()
            on_main(lambda: then(result))
        threading.Thread(target=body, daemon=True).start()

    # ── Drawing ──

    def draw() -> None:
        context: words.Context = state["context"]
        report = state["report"]
        items = words.build(report, state["models"], context)
        whole = words.stack_is_up(report)
        indicator.set_title("NERVIS — " + words.headline(report, context.phase))
        set_blinking(words.unread(report, context.phase) > 0, whole)
        text = "\n".join(words.render_text(items))
        if text == state["drawn"]:
            # Unchanged: rebuilding a menu somebody has open would snap its submenu shut.
            return
        state["drawn"] = text
        for child in gtk_menu.get_children():
            gtk_menu.remove(child)
        fill(gtk_menu, items)
        gtk_menu.show_all()

    def fill(target: Any, items: list[words.Item]) -> None:
        for item in items:
            if item.kind == "separator":
                target.append(Gtk.SeparatorMenuItem())
                continue
            widget = Gtk.MenuItem(label=item.text)
            # Sensitive unless the line is a note, a header or switched off — a service row with
            # nothing to open stays at full strength, as on the Mac, since a greyed row loses the
            # one thing it is for. Clicking one does nothing.
            widget.set_sensitive(item.enabled and item.kind in ("action", "reading"))
            if item.submenu is not None:
                inner = Gtk.Menu()
                fill(inner, item.submenu)
                widget.set_submenu(inner)
                widget.set_sensitive(True)
            elif item.action is not None:
                widget.connect("activate", lambda _w, action=item.action: act(action))
            target.append(widget)

    def set_blinking(on: bool, whole: bool) -> None:
        base = "nervis-tray-whole" if whole else "nervis-tray-partial"
        if not on:
            if state["blink"] is not None:
                GLib.source_remove(state["blink"])
                state["blink"] = None
            indicator.set_icon_full(base, "NERVIS")
            return
        if state["blink"] is None:
            # The pupil goes out for a quarter of a second in every second and a half — an eye
            # blinking, as on the Mac. Two icon changes per blink rather than six ticks: each
            # change is a D-Bus message to the panel.
            def blink() -> bool:
                indicator.set_icon_full("nervis-tray-blink", "NERVIS")
                GLib.timeout_add(250, lambda: (indicator.set_icon_full(
                    "nervis-tray-whole" if words.stack_is_up(state["report"])
                    else "nervis-tray-partial", "NERVIS"), False)[1])
                return True
            state["blink"] = GLib.timeout_add(1500, blink)
        indicator.set_icon_full(base, "NERVIS")

    # ── Readings ──

    def refresh() -> bool:
        context: words.Context = state["context"]
        if state["refreshing"] or context.phase in ("stopping", "stopped"):
            return True
        state["refreshing"] = True

        def read() -> tuple[dict[str, Any] | None, int | None, bool, bool]:
            # Everything that runs a program, off the main thread: the launcher, the GPU query
            # and the look for LM Studio's processes.
            return (launcher.report(), gpu_percent(), lm_studio_entry() is not None,
                    bool(lm_studio_pids()))

        def done(answer: tuple[dict[str, Any] | None, int | None, bool, bool]) -> None:
            report, gpu, installed, running = answer
            state["refreshing"] = False
            if report is not None:
                state["report"] = report
                forget_finished_stops()
            # CPU as the share of the time since the last tick, which is what the Mac shows; a
            # reading with nothing to compare with keeps the last figure rather than blanking it.
            context.cpu = cpu.percent() or context.cpu
            context.gpu = gpu
            context.lm_studio_installed = installed
            context.lm_studio_running = running
            draw()
        in_background(read, done)
        return True

    def refresh_models() -> bool:
        context: words.Context = state["context"]
        if state["models_refreshing"] or context.phase in ("stopping", "stopped"):
            return True
        state["models_refreshing"] = True

        def done(answer: tuple[int, bytes]) -> None:
            state["models_refreshing"] = False
            fresh = _json(answer[1])
            if fresh is not None:
                state["models"] = fresh
            draw()
        in_background(lambda: launcher.capture(["models", "--json"], timeout=60), done)
        return True

    def forget_finished_stops() -> None:
        runs = ((state["report"] or {}).get("codex") or {}).get("runs") or []
        context: words.Context = state["context"]
        context.codex_stopping = {
            task: turn for task, turn in context.codex_stopping.items()
            if any(run.get("id") == task and run.get("turn_id") == turn
                   and run.get("state") in words.STOPPABLE for run in runs)
        }

    # ── Dialogs ──

    # Every dialog on screen, so a quit can close them. `Gtk.main_quit` ends only the innermost
    # event loop, and a dialog waiting for an answer is one: quitting while the start-up notice sat
    # behind a locked screen stopped the stack, logged "exiting", and left the tray running — found
    # on the Ubuntu desktop test, 18 September 2026.
    open_dialogs: list[Any] = []

    def confirm(title: str, text: str, button: str) -> bool:
        """A question with one action; True when it was chosen. Waits for the answer."""
        dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.WARNING, text=title,
                                   secondary_text=text)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, button, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        dialog.set_keep_above(True)
        open_dialogs.append(dialog)
        answer = dialog.run()
        open_dialogs.remove(dialog)
        dialog.destroy()
        return answer == Gtk.ResponseType.OK and state["context"].phase == "running"

    def tell(title: str, text: str) -> None:
        """A sentence to read. Doesn't wait: nothing after it depends on its being read."""
        dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.INFO,
                                   buttons=Gtk.ButtonsType.OK, text=title, secondary_text=text)
        dialog.set_keep_above(True)

        def closed(shown: Any, _answer: Any) -> None:
            if shown in open_dialogs:
                open_dialogs.remove(shown)
            shown.destroy()
        dialog.connect("response", closed)
        open_dialogs.append(dialog)
        dialog.show()

    def close_dialogs() -> None:
        for dialog in list(open_dialogs):
            dialog.response(Gtk.ResponseType.CANCEL)

    def open_uri(uri: str | None) -> None:
        if uri:
            try:
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except GLib.Error as failure:
                record(f"could not open {uri}: {failure.message}")

    # ── Clicks ──

    def open_lm_studio() -> None:
        entry = lm_studio_entry()
        app = Gio.DesktopAppInfo.new_from_filename(str(entry)) if entry is not None else None
        if app is not None:
            app.launch([], None)

    def act(action: tuple[str, ...]) -> None:
        """What a click means, from the action `menu.py` put on the line."""
        report = state["report"] or {}
        handlers: dict[str, Callable[[], None]] = {
            "open": lambda: open_uri(action[1]),
            "dashboard": lambda: open_uri(report.get("dashboard")),
            "notifications": lambda: open_uri((report.get("notifications") or {}).get("screen")),
            "load": lambda: change_model("load", action[1]),
            "unload": lambda: change_model("unload", action[1]),
            "open_lmstudio": open_lm_studio,
            "quit_lmstudio": quit_lm_studio,
            "stop_task": lambda: stop_codex_task(action[1]),
            "retest": retest,
            "sign_in": lambda: codex_sign(["codex", "sign-in"], "Codex's sign-in didn't start"),
            "cancel_sign_in": lambda: codex_sign(["codex", "cancel-sign-in"],
                                                 "The sign-in wasn't cancelled"),
            "quit": lambda: begin_quit() and None,
        }
        handler = handlers.get(action[0])
        if handler is not None:
            handler()

    def change_model(kind: str, key: str) -> None:
        context: words.Context = state["context"]
        model = next((one for one in (state["models"] or {}).get("models", [])
                      if one.get("key") == key), None)
        if model is None:
            return
        if kind == "load":
            warning = words.fit_warning(model, state["report"])
            if warning and not confirm(warning[0], warning[1], "Load Anyway"):
                return
        context.model_busy = key
        draw()

        def done(answer: tuple[int, bytes]) -> None:
            context.model_busy = None
            if answer[0] != 0:
                verb = "load" if kind == "load" else "unload"
                tell(f"SIRVIS did not {verb} {model['name']}", error_of(answer[1])
                     or "The launcher gave no reason; .run/tray.log may say more.")
            refresh_models()
        in_background(lambda: launcher.capture([kind, key]), done)

    def quit_lm_studio() -> None:
        pids = lm_studio_pids()
        if not pids:
            return
        loaded = [one["name"] for one in (state["models"] or {}).get("models", [])
                  if one.get("loaded")]
        if loaded:
            count = "a model" if len(loaded) == 1 else f"{len(loaded)} models"
            if not confirm(f"Quit LM Studio with {count} loaded?",
                           f"Quitting unloads {', '.join(loaded)}, and anything using "
                           f"{'it' if len(loaded) == 1 else 'them'} stops.", "Quit LM Studio"):
                return
        # The same signal LM Studio's own window close and a logout send it.
        for pid in pids:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
        refresh_models()

    def stop_codex_task(task: str) -> None:
        context: words.Context = state["context"]
        runs = ((state["report"] or {}).get("codex") or {}).get("runs") or []
        run = next((one for one in runs if one.get("id") == task), None)
        if run is None or words.stop_offer(run, context.codex_stopping)[0] != "offer":
            return
        # Taken before the dialog opens, so a refresh meanwhile changes nothing it names.
        project, turn = run["project"], run["turn_id"]
        if not confirm(f"Stop Codex's task in {project}?", words.stop_question(run), "Stop Task"):
            return
        context.codex_stopping[task] = turn
        draw()

        def done(answer: tuple[int, bytes]) -> None:
            if answer[0] != 0:
                context.codex_stopping.pop(task, None)
            refresh()
            if answer[0] != 0:
                tell(f"Codex's task in {project} wasn't stopped",
                     words.stop_exit(answer[0], error_of(answer[1])))
        in_background(lambda: launcher.capture(["codex", "stop", task, "--project", project,
                                                "--turn", turn]), done)

    def retest() -> None:
        context: words.Context = state["context"]
        version = (((state["report"] or {}).get("codex") or {}).get("runtime") or {}).get("version")
        question = words.retest_question(version)
        if context.codex_retesting or not confirm(question[0], question[1], "Re-test"):
            return
        context.codex_retesting = True
        draw()

        def done(answer: tuple[int, bytes]) -> None:
            context.codex_retesting = False
            refresh()
            tell(*words.retest_result(answer[0], error_of(answer[1])))
        in_background(lambda: launcher.capture(["codex", "reprove"], timeout=420), done)

    def codex_sign(arguments: list[str], failed: str) -> None:
        context: words.Context = state["context"]
        if context.codex_signing_in:
            return
        context.codex_signing_in = True
        draw()

        def done(answer: tuple[int, bytes]) -> None:
            context.codex_signing_in = False
            refresh()
            if answer[0] != 0:
                tell(failed, words.sign_in_exit(answer[0], error_of(answer[1])))
        in_background(lambda: launcher.capture(arguments), done)

    # ── Starting and quitting ──

    def start() -> None:
        def done(_code: int) -> None:
            context: words.Context = state["context"]
            if context.phase == "stopping":
                # Quit arrived while the stack was still starting; stop reads the record start
                # writes, so it had to wait for the start to finish.
                stop_then_exit()
                return
            context.phase = "running"
            refresh()
            refresh_models()
        in_background(lambda: launcher.run_logged(["start"]), done)
        GLib.timeout_add_seconds(10, tick)
        # A model loaded from the menu stays loaded until it is unloaded there or NERVIS quits;
        # SIRVIS's lease lasts an hour, so the tray renews it every ten minutes.
        GLib.timeout_add_seconds(600, lambda: (in_background(
            lambda: launcher.capture(["renew"]), lambda _a: None), True)[1])

    ticks = {"count": 0}

    def tick() -> bool:
        ticks["count"] += 1
        refresh()
        if ticks["count"] % 3 == 0:
            refresh_models()
        return state["context"].phase not in ("stopping", "stopped")

    def begin_quit() -> bool:
        context: words.Context = state["context"]
        if context.phase in ("stopping", "stopped"):
            return True
        starting = context.phase == "starting"
        context.phase = "stopping"
        record("quit requested — releasing the menu's models and stopping the stack first")
        state["drawn"] = None
        draw()
        if not starting:
            stop_then_exit()
        return True

    def stop_then_exit() -> None:
        def done(code: int) -> None:
            record(f"{EXITED_LINE}; the stack's stop finished with status {code}")
            state["context"].phase = "stopped"
            # Answer every open dialog first, so the loops they run end; then end the tray's own,
            # from the idle queue, once those have unwound back to it.
            close_dialogs()
            GLib.idle_add(lambda: (Gtk.main_quit(), False)[1])
        in_background(lambda: launcher.run_logged(["stop"]), done)

    # `kill` and a logout quit the way the menu does, stack and all.
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, begin_quit)

    if not _tray_host_present(Gio, GLib):
        record("no StatusNotifier host on this desktop: the icon will not be shown")
        on_main(lambda: tell(
            "NERVIS is running, but its tray icon can't be shown",
            "Nothing on this desktop displays tray icons. On GNOME, enable the “AppIndicator "
            "and KStatusNotifierItem Support” extension (Ubuntu has it on by default), then "
            "open NERVIS again. The stack is starting anyway; the dashboard is at "
            "http://127.0.0.1:8790, and ./stop-linux.sh stops it."))

    draw()
    start()
    Gtk.main()
    return 0


def _indicator_module(gi: Any) -> Any:
    """Ayatana's AppIndicator, or the older Canonical one on distributions that still ship it."""
    for name in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(name, "0.1")
            module = __import__("gi.repository", fromlist=[name])
            return getattr(module, name)
        except (ValueError, ImportError, AttributeError):
            continue
    return None


def _tray_host_present(gio: Any, glib: Any) -> bool:
    """Whether something on the session bus shows StatusNotifier icons."""
    try:
        bus = gio.bus_get_sync(gio.BusType.SESSION, None)
        answer = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                               "org.freedesktop.DBus", "NameHasOwner",
                               glib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                               None, gio.DBusCallFlags.NONE, 2000, None)
        return bool(answer.unpack()[0])
    except Exception:  # noqa: BLE001 — no bus, no answer: assume a host rather than nag
        return True


def _explain_missing(what: str, fix: str) -> None:
    """Say so on the terminal, and on the desktop when `notify-send` is there."""
    print(f"NERVIS tray: {what}. {fix}", file=sys.stderr)
    if shutil.which("notify-send"):
        subprocess.call(["notify-send", "NERVIS", f"The tray icon can't start: {what}. {fix}"])


def print_menu(report_file: str | None) -> int:
    """The menu as it would be drawn from one real status answer, as text — or from the answer
    in `report_file`, so a hard-to-cause state can be looked at without causing it."""
    launcher = Launcher(REPOSITORY)
    if report_file:
        report = json.loads(Path(report_file).read_text())
        models = None
    else:
        report = launcher.report()
        models = _json(launcher.capture(["models", "--json"], timeout=60)[1])
    cpu = CPUMeter()
    cpu.percent()
    import time
    time.sleep(1)
    context = words.Context(phase="running", cpu=cpu.percent(), gpu=gpu_percent(),
                            lm_studio_installed=lm_studio_entry() is not None,
                            lm_studio_running=bool(lm_studio_pids()))
    print("\n".join(words.render_text(words.build(report, models, context))))
    return 0


def main(argv: list[str]) -> int:
    if "--print-menu" in argv:
        at = argv.index("--print-menu")
        return print_menu(argv[at + 1] if len(argv) > at + 1 else None)
    if "--render-icons" in argv:
        at = argv.index("--render-icons")
        print(write_icons(Path(argv[at + 1])))
        return 0
    if not (REPOSITORY / "tools" / "run.py").exists():
        _explain_missing(f"its repository isn't at {REPOSITORY} any more",
                         "Run install.sh again from the repository's new place.")
        return 1
    return main_gui()
