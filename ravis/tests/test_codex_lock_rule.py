"""RAVIS's side of the shared lock rule and the checkout lock file (design §6.3).

- **Every table in `lock-rule-cases.json`** — the verdicts, the restart adoption rule and what a
  writer does on finding a lock — against `ravis.codex.lock_rule`, the same file Clarvis's
  `lockRule.ts` is held to;
- **the probes** read `ps` and `sysctl` as the rule needs them, and "couldn't tell" is never "gone";
- **the lock file**: the create is atomic and exclusive, mode 0600, padded to a whole KiB, in the
  fixture's shape; heartbeats go through the descriptor; a holder whose file was replaced has lost
  it, and releasing then deletes nothing; an unreadable file is unknown, not lost;
- **the git folder** is found by its shape, without running git.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ravis.codex.lock_file import (
    PAD_TO_BYTES,
    Holder,
    create_lock_file,
    git_dir_for,
    lock_content,
    lock_file_path,
    read_lock_file,
)
from ravis.codex.lock_rule import (
    FoundLockFile,
    JudgedLock,
    ProcessProbe,
    adoption_decision,
    judge_lock,
    observer_awake_seconds,
    on_finding_a_lock,
    own_start,
    parse_sysctl_seconds,
    probe_process,
)
from ravis.codex.process_table import attribute, parse_rows

CASES = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "lock-rule-cases.json").read_text()
)


@pytest.mark.parametrize("case", CASES["verdict_cases"], ids=lambda case: case["name"])
def test_every_verdict_case(case: dict[str, Any]) -> None:
    lock = JudgedLock(**case["lock"])
    probe = ProcessProbe(**case["probe"])

    assert judge_lock(lock, probe, case["observer_awake_seconds"]) == case["expected"]


def test_start_times_are_compared_with_whitespace_collapsed() -> None:
    """`ps` pads a one-digit day and ends with spaces; a live holder must not read as gone."""
    lock = JudgedLock(pid=7, pid_start="Sat Sep  5 05:10:02 2026", heartbeat_age_seconds=1)

    verdict = judge_lock(lock, ProcessProbe(True, "Sat Sep 5 05:10:02 2026   "), 600)

    assert verdict == "alive"


@pytest.mark.parametrize("case", CASES["adoption_cases"], ids=lambda case: case["name"])
def test_every_adoption_case(case: dict[str, Any]) -> None:
    file = case["file"]
    found = None if file is None else FoundLockFile(
        file["names_previous_ravis_instance"], file["verdict"]
    )

    outcome = adoption_decision(found, case["create_race_lost"])

    expected = case["expected"]
    assert (outcome.action, outcome.file_touched, outcome.ravis_lock_state) == (
        expected["action"], expected["file_touched"], expected["ravis_lock_state"],
    )
    assert outcome.codex_session_state == expected.get("codex_session_state")
    assert list(outcome.refused_with_409_lock_superseded) == expected.get(
        "refused_with_409_lock_superseded", []
    )


FINDING_WORDS = {
    "attach": "attach", "reconcile": "reconcile", "refuse": "refuse",
    "take over": "take_over_with_confirmation",
}


@pytest.mark.parametrize("case", CASES["on_finding_a_lock"], ids=lambda case: case["outcome"])
def test_every_finding_a_lock_case(case: dict[str, Any]) -> None:
    verdicts = ["alive", "unresponsive", "gone"] if case["verdict"] == "any" else [case["verdict"]]
    outcome = case["outcome"]
    expected = next(word for start, word in FINDING_WORDS.items() if outcome.startswith(start))

    for verdict in verdicts:
        waiting = case.get("waiting_on_you", False)
        assert on_finding_a_lock(case["holder"], verdict, waiting) == expected  # type: ignore[arg-type]


# ── Probes ──────────────────────────────────────────────────────────────────


def test_a_running_pid_is_probed_with_ps_in_its_own_words() -> None:
    asked: list[list[str]] = []

    def run(arguments: list[str]) -> tuple[int | None, str]:
        asked.append(arguments)
        return 0, "Sun Sep 13 05:10:02 2026   \n"

    probe = probe_process(4411, run)

    assert asked == [["ps", "-o", "lstart=", "-p", "4411"]]
    assert probe == ProcessProbe(True, "Sun Sep 13 05:10:02 2026")


def test_ps_that_couldnt_run_is_not_knowing_never_gone() -> None:
    assert probe_process(4411, lambda _: (None, "")) is None
    assert probe_process(4411, lambda _: (1, "")) == ProcessProbe(False, None)


def test_this_process_reads_its_own_start_time() -> None:
    """The real `ps`, read only: a lock file RAVIS writes must carry a start time."""
    start = own_start()

    assert start is not None and len(start.split()) == 5


def test_awake_time_is_since_the_last_wake_else_since_boot() -> None:
    def run(arguments: list[str]) -> tuple[int | None, str]:
        woke = "{ sec = 0, usec = 0 } Thu Jan  1 00:00:00 1970"
        booted = "{ sec = 1000, usec = 5 } Thu Jan  1 00:16:40 1970"
        return 0, woke if arguments[-1] == "kern.waketime" else booted

    assert observer_awake_seconds(now=1090.0, run=run, platform="darwin") == 90.0
    assert parse_sysctl_seconds("{ sec = 1789231177, usec = 263628 }") == 1789231177


def test_the_process_table_leaves_out_zombies_and_matches_roots_with_spaces() -> None:
    """A Stop confirmed gone must not read a zombie as a survivor; a root may hold a space."""
    table = (
        "  100     1   100 Ss   Sun Sep 13 05:10:02 2026 codex app-server --listen stdio://\n"
        "  101   100   101 S    Sun Sep 13 05:10:03 2026 "
        "/bin/sh -c sleep 600 WRITABLE_ROOT_0=/w s/a\n"
        "  102   101   101 S    Sun Sep 13 05:10:03 2026 sleep 600\n"
        "  103   100   103 Z    Sun Sep 13 05:10:04 2026 <defunct>\n"
    )

    rows = parse_rows(table)
    attributed, unattributed = attribute(
        rows, app_server_pid=100, roots={"A": Path("/w s/a"), "B": Path("/w s/b")}, terminals={}
    )

    assert [row.pid for row in rows] == [100, 101, 102]
    assert {pid: (entry.owner, entry.rule) for pid, entry in attributed.items()} == {
        101: ("A", "sandbox_root"), 102: ("A", "parent"),
    }
    assert unattributed == []


# ── The lock file ───────────────────────────────────────────────────────────


def _content(session: str = "cal_run_a", now: datetime | None = None) -> dict[str, Any]:
    holder = Holder(kind="codex_session", session_id=session, pid=os.getpid(),
                    pid_start="Sun Sep 13 05:10:02 2026")
    return lock_content(holder, task_id="cal_run", now=now)


def test_the_create_is_exclusive_private_padded_and_in_the_fixtures_shape(tmp_path: Path) -> None:
    path = tmp_path / ".git" / "clarvis-engine.lock"

    first = create_lock_file(path, _content())
    second = create_lock_file(path, _content("someone_else"))

    assert first.held is not None
    assert second.held is None and second.existing is not None
    assert second.existing.kind == "present"
    assert second.existing.content["holder"]["session_id"] == "cal_run_a"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.stat().st_size % PAD_TO_BYTES == 0
    assert set(json.loads(path.read_text())) == set(CASES["lock_file"]["example"])
    first.held.release()


def test_a_heartbeat_moves_only_the_heartbeat_and_keeps_the_size(tmp_path: Path) -> None:
    path = tmp_path / "clarvis-engine.lock"
    earlier = datetime(2026, 9, 13, 1, 0, tzinfo=UTC)
    held = create_lock_file(path, _content(now=earlier)).held
    assert held is not None
    size = path.stat().st_size

    assert held.heartbeat(earlier + timedelta(seconds=15)) == "written"

    written = json.loads(path.read_text())
    assert written["heartbeatAt"] == "2026-09-13T01:00:15Z"
    assert written["holder"]["since"] == "2026-09-13T01:00:00Z"
    assert path.stat().st_size == size
    assert read_lock_file(path, earlier + timedelta(seconds=45)).heartbeat_age_seconds == 30
    held.release()


def test_a_holder_whose_file_was_replaced_has_lost_it_and_deletes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "clarvis-engine.lock"
    held = create_lock_file(path, _content()).held
    assert held is not None
    path.unlink()
    taken = create_lock_file(path, _content("the_new_holder")).held
    assert taken is not None

    assert held.check() == "lost"
    assert held.heartbeat() == "lost"
    assert held.release() == "lost"
    assert json.loads(path.read_text())["holder"]["session_id"] == "the_new_holder"
    assert taken.release() == "released"
    assert not path.exists()


def test_an_unreadable_file_is_unknown_not_lost(tmp_path: Path) -> None:
    path = tmp_path / "clarvis-engine.lock"
    held = create_lock_file(path, _content()).held
    assert held is not None
    with path.open("r+") as handle:
        handle.write("{not json")

    assert held.check() == "unknown"
    assert held.release() == "unknown"
    assert path.exists()
    held.abandon()


def test_trailing_spaces_are_allowed_when_reading(tmp_path: Path) -> None:
    path = tmp_path / "clarvis-engine.lock"
    path.write_text(json.dumps(_content()) + "   \n   ")

    assert read_lock_file(path).kind == "present"


def test_the_git_folder_is_found_by_its_shape_without_git(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    (plain / ".git").mkdir(parents=True)
    main = tmp_path / "main"
    worktree_meta = main / ".git" / "worktrees" / "feature"
    worktree_meta.mkdir(parents=True)
    worktree = tmp_path / "feature"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {worktree_meta}\n")
    (worktree_meta / "gitdir").write_text(f"{worktree / '.git'}\n")
    forged = tmp_path / "forged"
    forged.mkdir()
    (forged / ".git").write_text(f"gitdir: {tmp_path / 'elsewhere'}\n")
    bare = tmp_path / "bare"
    bare.mkdir()

    assert git_dir_for(plain) == Path(os.path.realpath(plain / ".git"))
    assert git_dir_for(worktree) == Path(os.path.realpath(worktree_meta))
    assert git_dir_for(forged) is None
    assert git_dir_for(bare) is None
    assert lock_file_path(bare, None) == bare / ".clarvis" / "engine.lock"
    assert lock_file_path(plain, plain / ".git") == plain / ".git" / "clarvis-engine.lock"
