"""M10 — the raw-log adapters (§11.3).

Four exit clauses, and the interesting one is the last: *a missing file does not
error globally*. The shape of that failure is a service nobody started blanking
a screen that describes the other three.

Rotation gets the most attention here because it is the part that can lose a
running service's output if it is done the obvious way.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from nervis import logs


def _write(run: Path, service: str, lines: list[Any]) -> Path:
    path = run / logs.FILES[service]
    path.write_text("".join(
        (json.dumps(one) if isinstance(one, dict) else str(one)) + "\n" for one in lines
    ))
    return path


# ── Reading and filtering ───────────────────────────────────────────────────


def test_a_level_filter_reads_the_field_not_the_word(tmp_path: Path) -> None:
    """A message quoting "ERROR" is not an error.

    The whole reason these adapters parse rather than grep: the files are JSON
    lines, so the level is a field, and a substring hunt would report every log
    line that mentions the word as one.
    """
    _write(tmp_path, "nervis", [
        {"level": "INFO", "logger": "a", "message": "retrying after ERROR from upstream"},
        {"level": "ERROR", "logger": "b", "message": "the real one"},
    ])
    found = logs.read(tmp_path, "nervis", level="ERROR")
    assert [one["message"] for one in found["items"]] == ["the real one"]


def test_a_line_that_is_not_json_is_kept_rather_than_dropped(tmp_path: Path) -> None:
    """A traceback goes to stderr unstructured, and is what somebody came for.

    Dropping unparseable lines would hide exactly the output worth reading, on
    the grounds that it did not arrive in the expected shape.
    """
    _write(tmp_path, "nervis", [
        {"level": "INFO", "logger": "a", "message": "ordinary"},
        "Traceback (most recent call last):",
    ])
    found = logs.read(tmp_path, "nervis")
    assert [one["parsed"] for one in found["items"]] == [True, False]
    assert "Traceback" in found["items"][-1]["message"]


def test_the_tail_does_not_read_the_whole_file(tmp_path: Path) -> None:
    """A 43 MB log read whole to show two hundred lines is how a diagnostics
    screen becomes the reason a machine swaps."""
    path = _write(tmp_path, "nervis", [{"level": "INFO", "logger": "x", "message": f"line {n}"}
                                       for n in range(20_000)])
    assert path.stat().st_size > 500_000
    found = logs.read(tmp_path, "nervis", limit=3)
    assert [one["message"] for one in found["items"]] == [
        "line 19997", "line 19998", "line 19999"]


# ── A missing file is absence, never an error ───────────────────────────────


def test_a_missing_log_is_reported_rather_than_raised(tmp_path: Path) -> None:
    found = logs.read(tmp_path, "sirvis")
    assert found["present"] is False and found["items"] == []
    assert "has not written a log" in found["reason"]


def test_one_absent_service_does_not_hide_the_others(tmp_path: Path) -> None:
    """M10's exit verbatim: a missing file does not error *globally*."""
    _write(tmp_path, "ravis", [{"level": "INFO", "logger": "a", "message": "here"}])
    found = {one.service: one.present for one in logs.sources(tmp_path)}
    assert found["ravis"] is True
    assert found["nervis"] is False and found["sirvis"] is False


def test_only_documented_names_are_read_so_a_token_is_never_one(tmp_path: Path) -> None:
    """`.run` holds 0600 token files beside the logs.

    A glob over that directory is how a credential reaches a diagnostics screen,
    which is why the adapter list is a fixed set of names.
    """
    (tmp_path / "nervis-admin.token").write_text("a-real-looking-secret")
    listed = {Path(one.path).name for one in logs.sources(tmp_path)}
    assert listed == set(logs.FILES.values())
    assert not any("token" in name for name in listed)


# ── Secrets ─────────────────────────────────────────────────────────────────


def test_a_named_secret_is_blanked(tmp_path: Path) -> None:
    _write(tmp_path, "nervis", [
        {"level": "INFO", "logger": "a", "message": "ok", "api_key": "sk-live-abcdefghij"},
    ])
    found = logs.read(tmp_path, "nervis")
    assert found["items"][0]["api_key"] == "[redacted]"


def test_a_secret_spelled_into_a_message_is_blanked_too(tmp_path: Path) -> None:
    """The likelier leak. `REDACTED_KEYS` catches a field called `api_key`; it
    does not catch the same secret quoted inside a sentence, a URL or a header
    dump, which is how one actually reaches a log file."""
    _write(tmp_path, "nervis", [
        {"level": "INFO", "logger": "a",
         "message": "calling upstream with Authorization: Bearer sk-live-9f8e7d6c5b4a3210"},
    ])
    message = logs.read(tmp_path, "nervis")["items"][0]["message"]
    assert "sk-live-9f8e7d6c5b4a3210" not in message
    assert "[redacted]" in message


def test_a_launcher_token_in_a_line_is_blanked(tmp_path: Path) -> None:
    """44 characters of URL-safe base64, which is what `.run/*.token` holds."""
    secret = "K" * 43 + "="
    _write(tmp_path, "nervis", [{"level": "INFO", "logger": "a",
                                 "message": f"handing over {secret} to sirvis"}])
    assert secret not in logs.read(tmp_path, "nervis")["items"][0]["message"]


# ── Rotation, and the writer that is still holding the file ─────────────────


def test_rotation_keeps_the_writers_handle_working(tmp_path: Path) -> None:
    """The reason it is copy-truncate rather than rename.

    A running service holds this file open in append mode. Renaming it leaves
    the service writing into a file with no name anybody will look at, and the
    service never finds out. Truncating in place keeps the inode, so an
    `O_APPEND` handle simply continues from the new end.
    """
    path = _write(tmp_path, "nervis", [{"level": "INFO", "logger": "a",
                                        "message": "x" * 200} for _ in range(200)])
    with path.open("ab") as writer:            # the service, still running
        assert logs.rotate(path, max_bytes=1024) is True
        writer.write(b'{"level":"INFO","logger":"a","message":"after rotation"}\n')
        writer.flush()

    assert path.stat().st_size < 1024, "the live log was not truncated"
    found = logs.read(tmp_path, "nervis")
    assert [one["message"] for one in found["items"]] == ["after rotation"], \
        "the writer's line did not land in the file anybody reads"


def test_what_was_rotated_away_is_kept(tmp_path: Path) -> None:
    path = _write(tmp_path, "nervis", [{"level": "INFO", "logger": "a", "message": "old"}
                                       for _ in range(200)])
    logs.rotate(path, max_bytes=512)
    copies = list(tmp_path.glob("nervis.log.*"))
    assert len(copies) == 1
    assert "old" in copies[0].read_text()


def test_a_log_under_the_limit_is_left_alone(tmp_path: Path) -> None:
    path = _write(tmp_path, "nervis", [{"level": "INFO", "logger": "a", "message": "small"}])
    assert logs.rotate(path, max_bytes=logs.MAX_BYTES) is False
    assert list(tmp_path.glob("nervis.log.*")) == []


def test_rotated_copies_are_capped(tmp_path: Path) -> None:
    """§11.3 bounds the count as well as the size — otherwise rotation is just
    a slower way of filling the disk."""
    path = tmp_path / logs.FILES["nervis"]
    for n in range(5):
        path.write_text("x" * 4096)
        logs.rotate(path, max_bytes=512, max_files=2)
        time.sleep(1.05)          # the stamp is second-resolution
    assert len(list(tmp_path.glob("nervis.log.*"))) == 2


# ── Retention ───────────────────────────────────────────────────────────────


def test_retention_drops_old_copies_and_never_the_live_log(tmp_path: Path) -> None:
    """A machine left alone for a month should come back to its logs, not to an
    empty directory. Only rotated copies age out."""
    live = _write(tmp_path, "nervis", [{"level": "INFO", "logger": "a", "message": "current"}])
    old = tmp_path / "nervis.log.20200101T000000Z"
    old.write_text("ancient")
    long_ago = time.time() - 40 * 86400
    import os
    os.utime(old, (long_ago, long_ago))
    os.utime(live, (long_ago, long_ago))

    assert logs.prune(tmp_path, days=14.0) == 1
    assert not old.exists()
    assert live.exists(), "the live log was deleted for being old"


def test_a_filter_searches_further_back_than_it_shows(tmp_path: Path) -> None:
    """"No match" must not mean "not in the last twelve lines".

    Caught against the live log: filtering for a word that was plainly in the
    file returned nothing, because the search covered only as many lines as it
    was going to display. On a log gaining a line a second that reads as "it
    never happened", which is almost always wrong.
    """
    _write(tmp_path, "nervis", (
        [{"level": "INFO", "logger": "a", "message": "the needle"}]
        + [{"level": "INFO", "logger": "a", "message": f"noise {n}"} for n in range(3000)]
    ))
    found = logs.read(tmp_path, "nervis", limit=5, text="needle")
    assert [one["message"] for one in found["items"]] == ["the needle"]
    assert found["scanned"] > 3000


def test_an_empty_filter_result_says_how_far_it_looked(tmp_path: Path) -> None:
    """An absence nobody can size is an absence nobody can trust."""
    _write(tmp_path, "nervis", [{"level": "INFO", "logger": "a", "message": f"line {n}"}
                                for n in range(50)])
    found = logs.read(tmp_path, "nervis", text="absent-word")
    assert found["items"] == []
    assert found["filtered"] is True and found["scanned"] == 50
