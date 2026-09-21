"""What the NERVIS tray menu says, worked out from the launcher's answers and nothing else.

The Linux counterpart of the menu half of `../macos/NERVISMenu.swift`, and deliberately a
translation of it rather than a redesign: every sentence, every state word and every rule about
what may be clicked is the Mac app's, so the two menus agree about the same stack. Where a
sentence changed, the reason is beside it.

**Standard library only, and no GTK.** This module turns `tools/run.py status --json` and
`models --json` into a tree of plain `Item`s; `app.py` draws the tree. The split is what lets the
menu be tested on any machine — the Mac this was written on included — and printed with
`--print-menu`, exactly as the Swift app's menu can be. It also has to run under the *system*
Python rather than the stack's virtual environment, because PyGObject comes from the
distribution's packages, so a dependency here would be one the installer has to put in two places.

**What an AppIndicator menu cannot do**, and what replaces it. Menus reach the panel over D-Bus
(`dbusmenu`), which carries plain labels: no colour, no font, no attributed text. So a service's
green or red dot is a coloured circle character, and a figure the Mac draws in red carries a
warning sign instead. Neither loses information; both are how the Mac app's own `--print-menu`
already spells them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

GIBIBYTE = 1_073_741_824.0
#: CPU or GPU use above this is flagged — the owner's threshold, 12 September 2026.
BUSY_PERCENT = 85
#: Room a model needs beyond its file — context and working memory — when the menu judges
#: whether it probably fits in the memory free right now.
LOAD_HEADROOM = 1.2

# The dots. Circles rather than bullets, because a bullet cannot be coloured in a menu that
# carries plain text; each colour means what it means in the Mac menu.
GREEN, RED, GREY, BLUE, ORANGE = "🟢", "🔴", "⚪", "🔵", "🟠"


@dataclass
class Item:
    """One line of the menu.

    `kind` is `action` (clickable when `action` is set and `enabled`), `reading` (full-strength
    text with nothing behind it), `header` (a section title), `note` (greyed text) or
    `separator`. `action` is a tuple the front end dispatches
    on — `("open", url)`, `("load", key)`, `("stop_task", id)` and so on — so this module decides
    *what* a click means and never *how* it is carried out.
    """

    text: str = ""
    kind: str = "action"
    action: tuple[str, ...] | None = None
    enabled: bool = True
    submenu: list[Item] | None = None
    tooltip: str = ""


def header(text: str) -> Item:
    return Item(text, kind="header", enabled=False)


def note(text: str) -> Item:
    return Item(text, kind="note", enabled=False)


def separator() -> Item:
    return Item(kind="separator", enabled=False)


def reading(text: str) -> Item:
    """A line to read, not to click: drawn at full strength, as the Mac draws its figures and a
    service's problem, because a greyed line is the one a person squints at."""
    return Item(text, kind="reading")


# ── The launcher's answers ───────────────────────────────────────────────────


def stack(report: dict[str, Any]) -> list[dict[str, Any]]:
    """What the launcher starts: the services whose group is `stack`."""
    return [one for one in report.get("services", []) if one.get("group") == "stack"]


def stack_section(report: dict[str, Any]) -> list[dict[str, Any]]:
    """The menu's Stack section: the stack with CLARVIS and the link to the other computer, in
    the launcher's order. Neither of those counts toward `stack_is_up`: no editor window open
    and a peer computer that is asleep are both ordinary, not a stack that is down."""
    return [one for one in report.get("services", [])
            if one.get("group") in ("stack", "editor", "link")]


def runtimes(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [one for one in report.get("services", []) if one.get("group") == "runtime"]


def stack_is_up(report: dict[str, Any] | None) -> bool:
    """Whether every service the launcher starts answers. CLARVIS never counts: no editor
    window being open is not a fault."""
    services = stack(report or {})
    return bool(services) and all(one.get("answering") for one in services)


def unread(report: dict[str, Any] | None, phase: str) -> int:
    """Unread notes in NERVIS's notification centre. None count while stopping: nothing is left
    to read them in."""
    if phase in ("stopping", "stopped") or not report:
        return 0
    return int((report.get("notifications") or {}).get("unread") or 0)


def unread_text(count: int) -> str:
    return "1 unread notification" if count == 1 else f"{count} unread notifications"


def headline(report: dict[str, Any] | None, phase: str) -> str:
    """The line at the top, and the tray's title."""
    if phase == "stopping":
        return "Stopping the stack…"
    if phase == "stopped":
        return "The stack is stopped"
    if not report:
        return "Starting the stack…"
    if stack_is_up(report):
        return "The stack is running"
    if phase == "starting":
        return "Starting the stack…"
    silent = [one["name"] for one in stack(report) if not one.get("answering")]
    return "Not answering: " + ", ".join(silent)


# ── Services ─────────────────────────────────────────────────────────────────


def service_row(service: dict[str, Any]) -> Item:
    """A service and whether it answers: green or red — grey for CLARVIS with no editor open,
    since that is normal rather than a fault. Clicking opens its page, when it has one."""
    answering = bool(service.get("answering"))
    idle = GREY if service.get("group") in ("editor", "link") else RED
    dot = GREEN if answering else idle
    # "not answering" when its process is there and silent, so the line agrees with the
    # detail under it; "not running" when there is no such process.
    if answering:
        state = "running"
    else:
        state = "not running" if service.get("problem") is None else "not answering"
    windows = service.get("windows")
    if answering and isinstance(windows, int) and windows > 1:
        state += f" · {windows} windows"
    address = service.get("address")
    return Item(
        f"{dot}  {service['name']}   {state}",
        action=("open", address) if address else None,
        tooltip=f"Open {service['name']} in the browser" if address else "",
    )


def service_lines(services: list[dict[str, Any]], models: dict[str, Any] | None,
                  context: Context, sirvis_up: bool = True) -> list[Item]:
    """Each service's line, and under one that is running but not answering, what the launcher
    found and what clears it. LM Studio's line carries its submenu."""
    items: list[Item] = []
    for service in services:
        row = service_row(service)
        if service.get("name") == "LM Studio":
            row.submenu = lm_studio_menu(models, context, sirvis_up)
        items.append(row)
        problem = service.get("problem")
        if service.get("answering") or not problem:
            continue
        items.append(reading(f"     {service['name']} is {problem}."))
        items.append(reading("     Quit NERVIS and open it again to restart the stack."))
    return items


# ── LM Studio's models ───────────────────────────────────────────────────────


@dataclass
class Context:
    """What the menu knows that the launcher's answers do not say."""

    phase: str = "running"
    #: The model being loaded or unloaded right now, drawn as working and not clickable.
    model_busy: str | None = None
    #: Whether LM Studio's application can be found here, and whether it is running now.
    lm_studio_installed: bool = False
    lm_studio_running: bool = False
    #: Codex tasks this menu asked RAVIS to stop, by id, with the turn the stop confirmed.
    codex_stopping: dict[str, str] = field(default_factory=dict)
    codex_retesting: bool = False
    codex_signing_in: bool = False
    cpu: int | None = None
    gpu: int | None = None


def lm_studio_menu(models: dict[str, Any] | None, context: Context,
                   sirvis_up: bool = True) -> list[Item]:
    """LM Studio's submenu: the app itself, then every installed model, loadable through
    SIRVIS rather than straight into LM Studio (SIRVIS.md §9)."""
    items: list[Item] = []
    if context.lm_studio_installed:
        items.append(Item("Open LM Studio", action=("open_lmstudio",)))
    # Only while it runs: a Quit item for an app that isn't open would do nothing.
    if context.lm_studio_running:
        items.append(Item("Quit LM Studio", action=("quit_lmstudio",)))
    items.append(header("Load through SIRVIS"))
    if not models or not models.get("available"):
        items.append(note("Reading the installed models…" if sirvis_up
                          else "SIRVIS is not running"))
        return items
    listed = models.get("models") or []
    if not listed:
        items.append(note("No models installed"))
    shown_device: object = None
    for model in listed:
        # LM Link: another machine's models, after this one's, under that machine's name.
        device = model.get("linked_device")
        if device and device != shown_device:
            items.append(separator())
            items.append(header(f"On {device_name(model)} (LM Link)"))
            shown_device = device
        items.append(model_item(model, context))
    items.append(separator())
    items.append(note("✓ loaded from this menu, click to unload · – loaded by something else"))
    return items


def model_item(model: dict[str, Any], context: Context) -> Item:
    """One model: click to load, a tick and click to unload when this menu loaded it, a dash
    and no click when something else did, and "loading…" while SIRVIS works on it."""
    size = model.get("size_bytes")
    details = [str(model.get("format", "")).upper(), str(model.get("quantization", "")),
               f"{size / GIBIBYTE:.1f} GB" if isinstance(size, (int, float)) else ""]
    working = context.model_busy == model["key"]
    held = bool(model.get("held_by_menu"))
    trailing = ("unloading…" if held else "loading…") if working else " · ".join(
        part for part in details if part)
    label = model["name"] + (f"   {trailing}" if trailing else "")
    if working:
        return Item(label, enabled=False)
    if held:
        return Item(f"✓ {label}", action=("unload", model["key"]),
                    tooltip="Loaded from this menu. Click to unload it.")
    if model.get("loaded"):
        where = (f"Loaded on {device_name(model)}" if model.get("linked_device")
                 else "Loaded by something else")
        return Item(f"– {label}", enabled=False,
                    tooltip=f"{where}, which this menu leaves alone.")
    runs_there = (f" It runs on {device_name(model)}, in that machine's memory."
                  if model.get("linked_device") else "")
    return Item(label, action=("load", model["key"]), enabled=context.model_busy is None,
                tooltip="Load through SIRVIS." + runs_there)


def device_name(model: dict[str, Any]) -> str:
    """The LM Link device a model is on, by the name its owner gave it when LM Studio says."""
    return str(model.get("linked_device_name") or "another device")


def fit_warning(model: dict[str, Any], report: dict[str, Any] | None) -> tuple[str, str] | None:
    """"Ask me first", as the owner chose: a model whose file, with room to work, is larger than
    the memory free right now loads only after a dialog says so. Unknown size or unknown free
    memory loads without asking — there is nothing to judge with."""
    if model.get("linked_device"):
        return None  # it loads into the other machine's memory, which this one can't see
    size = model.get("size_bytes")
    free = ((report or {}).get("system") or {}).get("memory_available_bytes")
    if not isinstance(size, (int, float)) or not isinstance(free, (int, float)):
        return None
    if size * LOAD_HEADROOM <= free:
        return None
    return (
        f"{model['name']} probably won't fit in free memory",
        f"Its file is {size / GIBIBYTE:.1f} GB, and with room to work it needs about "
        f"{size * LOAD_HEADROOM / GIBIBYTE:.1f} GB. {free / GIBIBYTE:.1f} GB is free right now. "
        "Loading it anyway can make the computer swap and slow everything down until it is "
        "unloaded.",
    )


# ── The machine ──────────────────────────────────────────────────────────────


def figures(cpu: int | None, gpu: int | None, system: dict[str, Any] | None) -> list[str]:
    """The machine figures, each only when it was measured. CPU and GPU come from the tray's own
    meters, so they show even while the stack is down; memory, swap, disk and heat from NERVIS's
    reading, so the menu and the dashboard agree on them."""
    system = system or {}
    lines: list[str] = []
    if cpu is not None:
        lines.append(_percentage("CPU", cpu))
    if gpu is not None:
        lines.append(_percentage("GPU", gpu))
    total, available = system.get("memory_total_bytes"), system.get("memory_available_bytes")
    if isinstance(total, (int, float)) and isinstance(available, (int, float)) and total > 0:
        used = total - available
        lines.append(f"Memory {round(used / total * 100)}% · {used / GIBIBYTE:.1f} of "
                     f"{total / GIBIBYTE:.1f} GB")
    rest: list[str] = []
    if isinstance(system.get("swap_used_bytes"), (int, float)):
        rest.append(f"Swap {system['swap_used_bytes'] / GIBIBYTE:.1f} GB")
    if isinstance(system.get("disk_free_bytes"), (int, float)):
        rest.append(f"{system['disk_free_bytes'] / GIBIBYTE:.0f} GB disk free")
    if system.get("thermal_state"):
        rest.append(f"thermal {system['thermal_state']}")
    if rest:
        lines.append(" · ".join(rest))
    return lines


def _percentage(label: str, value: int) -> str:
    """"CPU 92% ⚠" above the threshold — the Mac draws the figure red, which a D-Bus menu
    cannot."""
    return f"{label} {value}%" + (" ⚠" if value > BUSY_PERCENT else "")


# ── Codex ────────────────────────────────────────────────────────────────────

#: The task states RAVIS lists, as the menu says them.
TASK_WORDS = {
    "running": "running",
    "waiting_on_you": "waiting for your answer",
    "paused_unanswered": "paused — waited 30 min for an answer",
    "paused_for_update": "paused — Codex updated",
    "completed_needs_review": "finished — needs review",
    "uncertain": "uncertain — cut off mid-step",
    "leftover": "stopped, with processes left over",
    "clarvis_engine": "Clarvis's own engine, not Codex",
}
#: What a Stop can reach.
STOPPABLE = {"running", "waiting_on_you"}
#: Tasks held for the owner in Clarvis.
NEEDING_YOU = {"paused_unanswered", "paused_for_update", "completed_needs_review", "uncertain",
               "leftover"}
#: Codex's own states, as the row says them when no task is listed.
STATE_WORDS = {
    "checking": "checking…",
    "not_installed": "not installed",
    "not_available": "not available",
    "untested_version": "paused — needs re-testing",
    "runtime_down": "process restarting",
    "signed_out": "signed out",
    "sign_in_expired": "sign-in expired",
    "account_changed": "different account — confirm it on the dashboard",
    "quota_exhausted": "allowance used up",
    "signed_in": "signed in",
    "ravis_not_answering": "not known — RAVIS isn't answering",
}
#: The states that need the owner, drawn orange.
ORANGE_STATES = {"signed_out", "sign_in_expired", "account_changed", "untested_version",
                 "quota_exhausted"}


def codex_headline(codex: dict[str, Any]) -> tuple[str, str]:
    """The row and its dot: tasks first, since they are what the owner can act on; then, with
    none, the state and what is left of the tightest allowance window. **Never red**: Codex
    signed out or used up is something to do, not a fault in the stack."""
    tasks = [run for run in codex.get("runs") or [] if run.get("state") != "clarvis_engine"]
    waiting = sum(1 for run in tasks if run.get("state") == "waiting_on_you")
    needing = sum(1 for run in tasks if run.get("state") in NEEDING_YOU)
    count = "1 task" if len(tasks) == 1 else f"{len(tasks)} tasks"
    if waiting:
        return f"Codex · {count} · {waiting} waiting for your answer", ORANGE
    if needing:
        return f"Codex · {count} · {needing} {'needs' if needing == 1 else 'need'} you", ORANGE
    if tasks:
        return f"Codex · {count} running", BLUE
    if codex.get("sign_in_waiting"):
        return "Codex · signing in — finish in the browser", ORANGE
    tight = tightest(codex)
    state = codex.get("state", "")
    if state == "signed_in":
        if not tight or tight.get("remaining_percent") is None:
            return "Codex · signed in · allowance not read yet", GREEN
        old = " · an old reading" if codex.get("stale") else ""
        return (f"Codex · {round(tight['remaining_percent'])}% left · resets "
                f"{clock(tight.get('resets_at'))}{old}", GREEN)
    if state == "quota_exhausted":
        resets = f" · resets {clock(tight.get('resets_at'))}" if tight else ""
        return f"Codex · allowance used up{resets}", ORANGE
    return f"Codex · {STATE_WORDS.get(state, state)}", ORANGE if state in ORANGE_STATES else GREY


def tightest(codex: dict[str, Any]) -> dict[str, Any] | None:
    """The allowance window with the least left, among those RAVIS gave a figure for."""
    if not codex.get("usage_known"):
        return None
    known = [one for one in codex.get("windows") or [] if one.get("remaining_percent") is not None]
    return min(known, key=lambda one: one["remaining_percent"]) if known else None


def is_stopping(run: dict[str, Any], stopping: dict[str, str]) -> bool:
    """Whether this menu asked RAVIS to stop the task on the turn RAVIS still lists it working."""
    task, turn = run.get("id"), run.get("turn_id")
    if not task or not turn:
        return False
    return stopping.get(str(task)) == turn and run.get("state") in STOPPABLE


def task_line(run: dict[str, Any], stopping: dict[str, str]) -> str:
    """One task: "add-utc-demo — waiting for your answer · 12 min, no editor open"."""
    project = run.get("project") or "a folder RAVIS didn't name"
    state = run.get("state") or ""
    if is_stopping(run, stopping):
        return f"{project} — stopping…"
    if state == "clarvis_engine":
        return f"{project} — {TASK_WORDS.get(state, state)}"
    words = ("reconnecting Codex (up to two minutes)" if run.get("reopening")
             else TASK_WORDS.get(state, state))
    line = f"{project} — {words}"
    shown = run.get("waiting_minutes") if run.get("waiting_minutes") is not None \
        else run.get("age_minutes")
    if shown is not None:
        line += f" · {minutes(shown)}"
    if run.get("attached_windows") == 0:
        line += ", no editor open"
    return line


def task_notes(run: dict[str, Any]) -> list[str]:
    """The quiet lines in a task's submenu: the model and effort it runs at, and a reconnect."""
    notes: list[str] = []
    if run.get("state") != "clarvis_engine" and (run.get("model") or run.get("effort")):
        effort = f"{run['effort']} effort" if run.get("effort") else "its default effort"
        notes.append(f"Runs {run.get('model') or 'Codex’s default model'} at {effort}")
    if run.get("reopening"):
        notes.append("Reconnecting Codex so a newly allowed site works — up to two minutes")
    return notes


def stop_offer(run: dict[str, Any], stopping: dict[str, str]) -> tuple[str, str]:
    """`("offer", id)` where RAVIS would stop something, else `("unavailable", why)`."""
    if run.get("state") == "clarvis_engine":
        return "unavailable", "Clarvis's own engine — stop it in the editor"
    if run.get("state") not in STOPPABLE:
        return "unavailable", "Nothing to stop — open the project in Clarvis to review it"
    if not (run.get("id") and run.get("project") and run.get("turn_id")):
        return "unavailable", "Stop this task… once Codex begins its first step"
    if is_stopping(run, stopping):
        return "unavailable", "Stopping…"
    return "offer", run["id"]


def can_sign_in(codex: dict[str, Any]) -> bool:
    """Where RAVIS would start a sign-in: nobody signed in, and Codex signed out or paused on a
    build RAVIS runs a process for."""
    if codex.get("signed_in") is not False:
        return False
    if codex.get("state") in ("signed_out", "sign_in_expired"):
        return True
    return codex.get("state") == "untested_version" and \
        (codex.get("runtime") or {}).get("verdict") != "untested"


def codex_items(codex: dict[str, Any] | None, context: Context) -> list[Item]:
    """Codex's row, one line per task with its submenu, then the re-test and sign-in commands
    where they apply. Nothing for a RAVIS that serves no Codex state."""
    if not codex:
        return []
    title, dot = codex_headline(codex)
    address = codex.get("address")
    items = [Item(f"{dot}  {title}", action=("open", address) if address else None,
                  tooltip="Open the Codex card on the dashboard" if address else "")]
    for run in codex.get("runs") or []:
        items.append(_task_item(run, address, context))
    retest = _retest_item(codex, context)
    sign_in = _sign_in_item(codex, context)
    items.extend(item for item in (retest, sign_in) if item is not None)
    return items


def _task_item(run: dict[str, Any], address: str | None, context: Context) -> Item:
    submenu: list[Item] = []
    if address:
        submenu.append(Item("Open the Codex card", action=("open", address)))
    submenu.extend(note(text) for text in task_notes(run))
    submenu.append(separator())
    offer, value = stop_offer(run, context.codex_stopping)
    if offer == "offer":
        submenu.append(Item("Stop this task…", action=("stop_task", value),
                            tooltip="Asks you to confirm first. Stopping answers and approves "
                                    "nothing."))
    else:
        submenu.append(note(value))
    return Item("     " + task_line(run, context.codex_stopping), submenu=submenu)


def _retest_item(codex: dict[str, Any], context: Context) -> Item | None:
    runtime = codex.get("runtime")
    if not runtime or runtime.get("strict_rules") == "proven":
        return None
    if context.codex_retesting:
        return note("     Re-testing the file rules… (up to 6 minutes)")
    if runtime.get("verdict") == "accepted":
        return Item("     Re-test the file rules…", action=("retest",),
                    tooltip="Uses one short Codex turn from your plan's allowance. Asks you "
                            "first.")
    if runtime.get("verdict") == "untested" and codex.get("address"):
        return Item(f"     Accept Codex {runtime.get('version') or ''} on the Codex card first…",
                    action=("open", codex["address"]),
                    tooltip="RAVIS re-tests only a build you have accepted.")
    return None


def _sign_in_item(codex: dict[str, Any], context: Context) -> Item | None:
    if context.codex_signing_in:
        return note("     Asking RAVIS about the sign-in…")
    if codex.get("sign_in_waiting"):
        return Item("     Cancel the Codex sign-in", action=("cancel_sign_in",),
                    tooltip="Ends the sign-in page RAVIS is waiting on.")
    if not can_sign_in(codex):
        return None
    return Item("     Sign in to Codex…", action=("sign_in",),
                tooltip="Opens OpenAI's sign-in page in your browser.")


# ── Codex's dialogs: the same sentences, the same exit codes ─────────────────


def stop_question(run: dict[str, Any]) -> str:
    state = TASK_WORDS.get(run.get("state") or "", "running")
    since = run.get("since")
    started = clock(since) if since and parsed_date(since) else None
    opening = f"It started at {started} and is {state}." if started else f"It is {state}."
    return (opening + " Stopping ends its current step and the commands it started. Codex's "
            "work so far stays in the project; open the project in an editor to review and save "
            "it. Nothing is answered or approved.")


def stop_exit(code: int, error: str | None) -> str:
    return {
        6: "RAVIS isn't answering, so the stop didn't reach it. The task may still be running; "
           "try again once RAVIS is back.",
        8: "That task changed; the menu has been refreshed. Look at the task again, and stop it "
           "again if you still want to.",
        9: "That task isn't running any more, so there was nothing to stop.",
        10: "RAVIS refused to stop it. " + (error or "It gave no reason."),
        2: "The menu couldn't name the task to the launcher: its id, folder or turn was missing. "
           "The menu has been refreshed; try again.",
    }.get(code, f"The launcher couldn't stop it. {error}" if error else
          "The launcher couldn't stop it and gave no reason; .run/menubar.log may say more.")


def retest_question(version: str | None) -> tuple[str, str]:
    build = f"Codex {version}" if version else "Codex"
    return (f"Re-test {build}'s file rules?",
            "Codex is asked to run four fixed test commands in two throwaway folders with no "
            "network — reading a decoy key file, and writing outside its folder — and RAVIS "
            "approves exactly those, so only the file rules can stop them. It uses one short "
            "Codex turn from your plan's allowance, at low effort, and takes at most 5 minutes. "
            "No Codex task can run meanwhile, and none of your projects is touched.")


def retest_result(code: int, error: str | None) -> tuple[str, str]:
    known = {
        0: ("The file rules held", "Every rule that keeps Codex away from your key files held on "
            "this build, so Codex can take tasks again."),
        11: ("A file rule didn't hold", "On this Codex build a rule that keeps Codex away from "
             "your key files didn't hold, so Codex stays paused for tasks, and what happens next "
             "is your decision. Nothing ran in your projects: the test used throwaway folders."),
        12: ("The re-test couldn't tell", "Codex didn't follow the four test commands, or the "
             "test hit its time or step limit. The file rules stay unproven and Codex stays "
             "paused; you can try again."),
        7: ("A Codex task is running", "The re-test runs only while no Codex task is live. Stop "
            "or finish the task, then try again."),
        6: ("RAVIS isn't answering", "The re-test didn't start. Try again once RAVIS is running."),
        10: ("RAVIS refused the re-test", error or "It gave no reason."),
    }
    title, said = known.get(code, ("The re-test didn't finish", error or
                                   "The launcher gave no reason; .run/menubar.log may say more."))
    # RAVIS's own reason, when it gave one, after the general sentence (19 September 2026).
    if code in (11, 12) and error:
        said = f"{said}\n\nRAVIS said: {error}"
    return title, said


def sign_in_exit(code: int, error: str | None) -> str:
    return {
        3: "Codex is already signed in.",
        4: "Codex isn't installed, or it is a build RAVIS hasn't tested, so there is nothing to "
           "sign in to yet.",
        5: "Another program is holding the sign-in ports 1455 and 1457 — usually the ChatGPT "
           "app's own Codex signing in. Finish or close that sign-in, then try again.",
        6: "RAVIS isn't answering, so the sign-in didn't start.",
        7: "Codex is working on a task; sign in after it pauses.",
        10: "RAVIS refused. " + (error or "It gave no reason."),
    }.get(code, error or "The launcher gave no reason; .run/menubar.log may say more.")


# ── Times ────────────────────────────────────────────────────────────────────


def minutes(value: float) -> str:
    whole = round(value)
    return f"{whole} min" if whole < 60 else f"{whole // 60} h {whole % 60} min"


def parsed_date(iso: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def clock(iso: str | None, now: datetime | None = None) -> str:
    """A time from RAVIS in local time, 24-hour: the clock alone today, the day and date
    otherwise."""
    moment = parsed_date(iso) if iso else None
    if moment is None:
        return "at a time not given"
    local = moment.astimezone()
    today = (now or datetime.now().astimezone()).date()
    return local.strftime("%H:%M") if local.date() == today else local.strftime("%a %-d %b %H:%M")


# ── The whole menu ───────────────────────────────────────────────────────────


def build(report: dict[str, Any] | None, models: dict[str, Any] | None,
          context: Context) -> list[Item]:
    """The menu, top to bottom — the Mac app's `rebuildMenu`, line for line."""
    items = [header(headline(report, context.phase))]
    count = unread(report, context.phase)
    if count:
        items.append(Item(unread_text(count), action=("notifications",)))
    items.append(Item("Open NERVIS dashboard", action=("dashboard",),
                      enabled=report is not None and context.phase != "stopping"))
    if report:
        items.append(header("Stack"))
        items.extend(service_lines(stack_section(report), models, context))
        items.append(header("Models"))
        # Whether SIRVIS answers words LM Studio's empty model list: still reading, or not running.
        sirvis_up = any(one.get("name") == "SIRVIS" and one.get("answering")
                        for one in stack(report))
        items.extend(service_lines(runtimes(report), models, context, sirvis_up))
        items.extend(codex_items(report.get("codex"), context))
    lines = figures(context.cpu, context.gpu, (report or {}).get("system"))
    if lines:
        items.append(header("This computer"))
        items.extend(reading(line) for line in lines)
    items.append(separator())
    stopping = context.phase == "stopping"
    items.append(Item("Stopping the stack…" if stopping else "Quit NERVIS and stop the stack",
                      action=("quit",), enabled=not stopping))
    return items


def render_text(items: list[Item], indent: str = "  ") -> list[str]:
    """The menu as text, for `--print-menu` and for tests — the Swift app's `printItems`."""
    lines: list[str] = []
    for item in items:
        if item.kind == "separator":
            lines.append(f"{indent}────")
            continue
        if item.kind == "header":
            lines.append(f"{indent}[{item.text}]")
            continue
        disabled = "" if item.enabled and item.kind not in ("note",) else "  (disabled)"
        opens = f"  → {item.tooltip}" if item.tooltip else ""
        lines.append(f"{indent}{item.text}{disabled}{opens}")
        if item.submenu:
            lines.extend(render_text(item.submenu, indent + "      "))
    return lines
