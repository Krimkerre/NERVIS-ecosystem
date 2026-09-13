"""Which task each of Codex's processes belongs to (design §4.5; calibration K6).

The samples in `fixtures/process-samples/` are of two kinds, and each test says which it reads:
- **recorded**: `ps` and `lsof` run on this Mac against a throwaway `sh -c 'sleep 40; true'` the
  recording started in a folder whose name holds a space, its paths then written as the owner's
  (`ps-throwaway-child.txt`, `lsof-throwaway-child.txt`). They pin the two programs' real output
  formats — `lstart`'s padding, the `SN` state word, `lsof -Fn`'s one field a line;
- **shaped after K6's transcript** (run `cal_d2185ed08f50`, Codex 0.154.0), not sampled from Codex:
  in each project a `script -q /dev/null sleep 600` with its `sleep`, and a foreground `sleep 600`,
  every one a child of the app-server in a session of its own, with no sandbox parameter in its
  arguments; a zombie; and the owner's own dev server running in project A, outside Codex.

What RAVIS is held to:
- the process table and `lsof`'s answer are read in their real formats, a path's spaces kept;
- **a command root is its project's by its folder while that project's turn runs**; its children
  are their parent's, whatever folder they moved to;
- a process in a project whose turn wasn't running when it started is nobody's;
- **two claims make a process ambiguous** — and its children too — and it is never a target;
- a process keeps its task by pid and start time after launchd adopts it; a reused pid doesn't;
- a process outside Codex's tree is never a candidate, whatever folder it runs in.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ravis.codex.lock_rule import normalise_start
from ravis.codex.process_table import (
    Attributed,
    ProcessRow,
    TaskClaim,
    attribute_processes,
    parse_cwds,
    parse_rows,
    read_cwds,
    sandbox_owner,
    started_at,
)

SAMPLES = Path(__file__).resolve().parent / "fixtures" / "process-samples"
CODING = Path("/Users/owner/Documents/coding/NERVIS workspace")
A, B = CODING / "project-a", CODING / "project-b"
APP_SERVER = 4411


def sample(name: str) -> str:
    return (SAMPLES / name).read_text()


def turn_at(clock: str) -> datetime:
    moment = started_at(f"Sun Sep 13 {clock} 2026")
    assert moment is not None
    return moment


def k6_look(tasks: dict[str, TaskClaim], **changes: object):  # type: ignore[no-untyped-def]
    rows = parse_rows(sample("ps-k6-shape.txt"))
    return attribute_processes(rows, app_server_pid=APP_SERVER, tasks=tasks,
                               cwds=parse_cwds(sample("lsof-k6-shape.txt")), **changes)  # type: ignore[arg-type]


def test_the_recorded_ps_and_lsof_output_is_read_in_its_real_format() -> None:
    rows = parse_rows(sample("ps-throwaway-child.txt"))
    folders = parse_cwds(sample("lsof-throwaway-child.txt"))

    assert [(r.pid, r.ppid, r.pgid, r.comm) for r in rows] == [
        (93544, 1, 93540, "sh"), (93546, 93544, 93540, "sleep")]
    assert rows[0].start == "Sun Sep 13 20:36:08 2026"
    assert rows[0].args == "/bin/sh -c sleep 40; true"
    assert folders == {93544: str(CODING / "add-utc-demo"), 93546: str(CODING / "add-utc-demo")}


def test_lsof_is_asked_for_every_pid_at_once_and_a_missing_one_changes_nothing() -> None:
    asked: list[list[str]] = []

    def answered(arguments: list[str]) -> tuple[int | None, str]:
        asked.append(arguments)
        # `lsof` exits 1 when one of the pids has gone, and still lists the others.
        return 1, sample("lsof-throwaway-child.txt")

    assert read_cwds([93546, 93544, 999999], run=answered) == {
        93544: str(CODING / "add-utc-demo"), 93546: str(CODING / "add-utc-demo")}
    assert asked == [["lsof", "-a", "-d", "cwd", "-Fn", "-p", "93544,93546,999999"]]
    assert read_cwds([], run=answered) == {} and len(asked) == 1
    assert read_cwds([1], run=lambda _arguments: (None, "")) is None


def test_k6s_commands_are_each_projects_by_their_folder_and_their_parent() -> None:
    running = {"A": TaskClaim(A, turn_at("18:37:00")), "B": TaskClaim(B, turn_at("18:37:00"))}

    result = k6_look(running)

    assert {pid: (a.owner, a.rule) for pid, a in result.attributed.items()} == {
        30870: ("A", "command_cwd"), 30871: ("A", "parent"), 30880: ("A", "command_cwd"),
        30890: ("B", "command_cwd"), 30891: ("B", "parent"), 30900: ("B", "command_cwd"),
    }
    assert not result.ambiguous and not result.unattributed
    # The zombie isn't a process, and the owner's own dev server in project A isn't Codex's.
    everything = {*result.attributed, *(r.pid for r in result.unattributed)}
    assert 30910 not in everything and 52000 not in everything


def test_a_project_whose_turn_wasnt_running_claims_nothing_by_its_folder() -> None:
    idle = {"A": TaskClaim(A, turn_at("18:37:00")), "B": TaskClaim(B, None)}
    later = {"A": TaskClaim(A, turn_at("18:37:00")), "B": TaskClaim(B, turn_at("18:40:00"))}

    for tasks in (idle, later):
        result = k6_look(tasks)
        assert {a.owner for a in result.attributed.values()} == {"A"}
        assert sorted(r.pid for r in result.unattributed) == [30890, 30891, 30900]


def test_a_command_that_moves_into_another_project_stays_its_own() -> None:
    """Review AM5: A's command changing into project B must not make B's Stop kill its child."""
    rows = [
        *parse_rows(sample("ps-k6-shape.txt")),
        ProcessRow(30872, 30870, "Sun Sep 13 18:37:20 2026", "python3 -m http.server", 30870),
    ]
    folders = {**parse_cwds(sample("lsof-k6-shape.txt")), 30872: str(B / "web")}
    running = {"A": TaskClaim(A, turn_at("18:37:00")), "B": TaskClaim(B, turn_at("18:37:00"))}

    result = attribute_processes(rows, app_server_pid=APP_SERVER, tasks=running, cwds=folders)

    assert (result.attributed[30872].owner, result.attributed[30872].rule) == ("A", "parent")


def test_two_claims_make_a_process_and_its_children_ambiguous_and_never_a_target() -> None:
    rows = [
        *parse_rows(sample("ps-k6-shape.txt")),
        ProcessRow(30950, APP_SERVER, "Sun Sep 13 18:37:30 2026",
                   f"/usr/bin/sandbox-exec -p (version 1) -DWRITABLE_ROOT_0={B} -- /bin/zsh -lc x",
                   30950),
        ProcessRow(30951, 30950, "Sun Sep 13 18:37:30 2026", "node server.js", 30950),
    ]
    folders = {**parse_cwds(sample("lsof-k6-shape.txt")), 30950: str(A), 30951: str(A)}
    running = {"A": TaskClaim(A, turn_at("18:37:00")), "B": TaskClaim(B, turn_at("18:37:00"))}

    result = attribute_processes(rows, app_server_pid=APP_SERVER, tasks=running, cwds=folders)

    assert {pid: a.owners for pid, a in result.ambiguous.items()} == {
        30950: ("A", "B"), 30951: ("A", "B")}
    assert 30950 not in result.attributed and 30951 not in result.attributed


def test_a_process_keeps_its_task_by_pid_and_start_time_once_launchd_adopts_it() -> None:
    orphan = ProcessRow(30880, 1, "Sun Sep 13 18:37:16 2026", "sleep 600", 30880)
    # A later process that took an old pid, under Codex: a candidate, but not the recorded process.
    reused = ProcessRow(30900, APP_SERVER, "Sun Sep 13 19:02:00 2026", "sleep 5", 30900)
    known = {
        orphan.identity: Attributed(orphan, "A", "command_cwd"),
        (30900, "Sun Sep 13 18:37:17 2026"): Attributed(reused, "B", "command_cwd"),
    }
    running = {"A": TaskClaim(A, turn_at("18:37:00")), "B": TaskClaim(B, turn_at("18:37:00"))}

    result = attribute_processes([orphan, reused], app_server_pid=APP_SERVER, tasks=running,
                                 known=known)

    assert {pid: (a.owner, a.rule) for pid, a in result.attributed.items()} == {
        30880: ("A", "recorded")}
    assert 30900 not in result.attributed


def test_a_terminal_os_pid_names_its_thread() -> None:
    running = {"A": TaskClaim(A, turn_at("18:37:00")), "B": TaskClaim(B, turn_at("18:37:00"))}

    result = k6_look(running, terminals={30880: "A"})

    assert (result.attributed[30880].owner, result.attributed[30880].rule) == ("A", "terminal")


def test_the_sandbox_parameter_in_its_real_form_names_exactly_one_root() -> None:
    roots = {"A": Path("/w s/a"), "B": Path("/w s/ab")}

    assert sandbox_owner("sandbox-exec -p x -DWRITABLE_ROOT_0=/w s/a -- zsh", roots) == "A"
    assert sandbox_owner("sh -c x WRITABLE_ROOT_1=/w s/ab", roots) == "B"
    # A read-only carve-out names a path inside the root, never the root itself.
    assert sandbox_owner("sandbox-exec -DWRITABLE_ROOT_0_RO_0=/w s/a/.git -- x", roots) is None
    assert sandbox_owner("sleep 600", roots) is None
    assert normalise_start("Sat Sep  5 05:10:02 2026 ") == "Sat Sep 5 05:10:02 2026"
