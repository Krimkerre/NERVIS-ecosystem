"""Checking a skill before it is installed (`agent/skill_package.py`, RAVIS 0.28.0).

What RAVIS is held to, whichever way a skill arrives:

- **the Agent Skills specification**: the name's characters, hyphens and length, and that it is the
  folder's name; a description; the optional fields' types and lengths — and what it only advises
  (other fields, a long `SKILL.md`, metadata that isn't text) warned about, never refused;
- **paths stay inside the skill** (zip-slip), and **no links, special files or encrypted entries**,
  refused rather than skipped, in a zip and in a `.tar.gz` alike;
- **the caps**: the archive, each file, all files, how many, and `SKILL.md` as small UTF-8 text;
- **one skill per archive**, at its top or in one folder, with Finder's additions left out;
- **what the review shows**: each file's kind with scripts flagged, and the license;
- **the update rule**: `SKILL.md` or a script added or changed switches the skill off; anything else
  keeps its switches.
"""

from __future__ import annotations

import os
import stat
import tarfile
from pathlib import Path

import pytest
from tests.skill_store_rig import skill_md, tar_of, zip_of

from ravis.agent import skill_package as package
from ravis.agent.skill_package import (
    Incoming,
    PackageRefusedError,
    changes,
    check,
    folder_hash,
    from_archive,
    installed_files,
    one_skill,
    stage,
)


def refused(reason: str, call: object, *args: object) -> str:
    with pytest.raises(PackageRefusedError) as caught:
        call(*args)  # type: ignore[operator]
    assert caught.value.reason == reason, caught.value.why
    return caught.value.why


def unpacked(data: bytes) -> tuple[str | None, list[Incoming], int]:
    files, left = from_archive(data)
    top, inner = one_skill(files)
    return top, inner, left


def only(text: bytes, folder: str = "pdf") -> package.Package:
    return check(folder, [Incoming("SKILL.md", text)])


# ── The specification ───────────────────────────────────────────────────────


def test_a_skill_at_a_zips_top_passes_and_its_review_sorts_every_file() -> None:
    data = zip_of({
        "SKILL.md": skill_md("pdf-tools", extra="license: Apache-2.0\n"),
        "scripts/extract.py": b"print(1)\n",
        "tools/run": b"#!/bin/sh\necho hi\n",
        "bin/tool": b"\x00\x01",
        "references/REFERENCE.md": b"# Reference\n",
        "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00",
    }, modes={"bin/tool": 0o100755})

    top, files, left = unpacked(data)
    result = check(top or "pdf-tools", files, left)

    assert (top, left, result.name) == (None, 0, "pdf-tools")
    assert {file.path: file.kind for file in result.files} == {
        "SKILL.md": "skill_md", "scripts/extract.py": "script", "tools/run": "script",
        "bin/tool": "script", "references/REFERENCE.md": "text", "assets/logo.png": "binary",
    }
    assert (result.license, result.license_from) == ("Apache-2.0", "front_matter")
    assert [code for code, _ in result.warnings] == ["has_scripts"]
    assert "scripts/extract.py" in result.warnings[0][1]


@pytest.mark.parametrize("name", ["PDF-Tools", "-pdf", "pdf-", "pdf--tools", "a" * 65, "pdf_tools",
                                  "pdf tools", "pdf.tools"])
def test_a_name_outside_the_specification_is_refused(name: str) -> None:
    refused("name_invalid", only, skill_md(name), name)


def test_the_name_must_be_the_name_of_its_folder() -> None:
    why = refused("name_mismatch", only, skill_md("pdf"), "pdf-main")

    assert "pdf-main" in why and "'pdf'" in why


def test_a_zip_whose_one_folder_holds_the_skill_takes_that_folders_name() -> None:
    top, files, _ = unpacked(zip_of({"pdf/SKILL.md": skill_md("pdf"), "pdf/notes.md": b"x"}))

    assert top == "pdf" and {file.path for file in files} == {"SKILL.md", "notes.md"}
    assert check("pdf", files).name == "pdf"


@pytest.mark.parametrize("text, reason", [
    (b"no front matter here\n", "front_matter"),
    (b"---\nname: [unclosed\n---\n", "front_matter"),
    (b"---\nname: pdf\ndescription: y\n", "front_matter"),
    (b"---\ndescription: y\n---\n", "name_invalid"),
    (b"---\nname: pdf\n---\n", "description_invalid"),
    (b"---\nname: pdf\ndescription: '   '\n---\n", "description_invalid"),
    (skill_md("pdf", description="d" * 1025), "description_invalid"),
    (skill_md("pdf", extra="compatibility: ''\n"), "field_invalid"),
    (skill_md("pdf", extra=f"compatibility: {'c' * 501}\n"), "field_invalid"),
    (skill_md("pdf", extra="metadata: just words\n"), "field_invalid"),
    (skill_md("pdf", extra="license: [MIT]\n"), "field_invalid"),
    (skill_md("pdf", extra="allowed-tools: [Read]\n"), "field_invalid"),
])
def test_front_matter_that_breaks_the_specification_is_refused(text: bytes, reason: str) -> None:
    refused(reason, only, text)


def test_the_longest_name_description_and_compatibility_allowed_pass() -> None:
    name = "a" * 64
    result = only(skill_md(name, description="d" * 1024, extra=f"compatibility: {'c' * 500}\n"),
                  name)

    assert result.name == name and result.compatibility == "c" * 500


def test_what_the_specification_only_advises_is_warned_about_never_refused() -> None:
    text = skill_md("pdf", extra="metadata:\n  version: 1.0\nargument-hint: '[file]'\n",
                    body="a line\n" * 501)

    assert [code for code, _ in only(text).warnings] == [
        "metadata_not_text", "unknown_fields", "long_skill_md"]


def test_skill_md_must_be_there_small_and_utf8_text() -> None:
    refused("skill_md_too_large", only, skill_md("pdf", body="x" * 70_000))
    refused("skill_md_not_text", only, b"---\nname: pdf\n\xff\xfe\n---\n")
    refused("no_skill_md", check, "pdf", [Incoming("README.md", b"# hi")])


def test_a_license_file_speaks_when_the_front_matter_doesnt() -> None:
    result = check("pdf", [Incoming("SKILL.md", skill_md("pdf")),
                           Incoming("LICENSE.txt", b"\n  Apache License\nVersion 2.0\n")])

    assert (result.license, result.license_from) == ("LICENSE.txt: Apache License", "file")
    assert (only(skill_md("pdf")).license, only(skill_md("pdf")).license_from) == (None, None)


# ── Paths, links and special files ───────────────────────────────────────────


@pytest.mark.parametrize("path", ["../evil/SKILL.md", "/etc/passwd", "a/../../b", "a\\b.txt",
                                  "C:/x.txt", "a//b", "./a", "a/\nb", "/".join("d" * 12)])
def test_a_path_that_could_leave_the_skill_is_refused(path: str) -> None:
    refused("unsafe_path", from_archive, zip_of({"SKILL.md": skill_md("pdf"), path: b"x"}))


def test_a_link_in_a_zip_is_refused_not_skipped() -> None:
    data = zip_of({"SKILL.md": skill_md("pdf"), "escape": b"/etc/passwd"},
                  modes={"escape": stat.S_IFLNK | 0o777})

    refused("link", from_archive, data)


def test_a_special_file_in_a_zip_is_refused() -> None:
    data = zip_of({"SKILL.md": skill_md("pdf"), "pipe": b""}, modes={"pipe": stat.S_IFIFO | 0o644})

    refused("special_file", from_archive, data)


def test_an_encrypted_entry_is_refused() -> None:
    data = zip_of({"SKILL.md": skill_md("pdf"), "secret.txt": b"x"}, encrypted=("secret.txt",))

    refused("encrypted", from_archive, data)


def test_finders_additions_are_left_out_and_counted() -> None:
    data = zip_of({"pdf/SKILL.md": skill_md("pdf"), "__MACOSX/pdf/._SKILL.md": b"x",
                   "pdf/.DS_Store": b"x"})

    top, files, left = unpacked(data)

    assert (top, left, [file.path for file in files]) == ("pdf", 2, ["SKILL.md"])
    assert "left_out" in [code for code, _ in check("pdf", files, left).warnings]


def test_a_tar_gz_keeps_the_same_rules() -> None:
    good = tar_of([("pdf/SKILL.md", skill_md("pdf"), tarfile.REGTYPE),
                   ("pdf/run.sh", b"echo", tarfile.REGTYPE)])
    top, files, _ = unpacked(good)
    assert check(top or "", files).name == "pdf"

    skill = ("pdf/SKILL.md", skill_md("pdf"), tarfile.REGTYPE)
    refused("link", from_archive, tar_of([skill, ("pdf/escape", None, tarfile.SYMTYPE)]))
    refused("link", from_archive, tar_of([skill, ("pdf/hard", None, tarfile.LNKTYPE)]))
    refused("special_file", from_archive, tar_of([skill, ("pdf/pipe", None, tarfile.FIFOTYPE)]))
    climbing = ("../SKILL.md", skill_md("pdf"), tarfile.REGTYPE)
    refused("unsafe_path", from_archive, tar_of([climbing]))


@pytest.mark.parametrize("data", [b"\x1f\x8b not really gzip", b"PK\x03\x04 a broken zip",
                                  b"plain text"])
def test_anything_that_isnt_a_readable_archive_is_refused(data: bytes) -> None:
    refused("not_archive", from_archive, data)


def test_an_archive_holds_exactly_one_skill() -> None:
    refused("not_one_skill", one_skill, [Incoming("a/SKILL.md", skill_md("a")),
                                         Incoming("b/SKILL.md", skill_md("b"))])
    refused("not_one_skill", one_skill, [Incoming("a/b/SKILL.md", skill_md("b"))])
    refused("no_skill_md", one_skill, [Incoming("a/README.md", b"x")])


# ── The caps ─────────────────────────────────────────────────────────────────


def test_an_archive_over_its_cap_is_refused_before_it_is_opened() -> None:
    refused("archive_too_large", from_archive, b"PK" + b"\0" * package.MOST_ARCHIVE_BYTES)


def test_a_file_over_its_cap_is_refused() -> None:
    data = zip_of({"SKILL.md": skill_md("pdf"), "big.bin": b"\0" * (package.MOST_FILE_BYTES + 1)})

    refused("file_too_large", from_archive, data)
    refused("file_too_large", check, "pdf", [
        Incoming("SKILL.md", skill_md("pdf")),
        Incoming("big.bin", b"\0" * (package.MOST_FILE_BYTES + 1))])


def test_files_adding_up_past_the_total_are_refused() -> None:
    four = b"\0" * (4 * 1024 * 1024)
    data = zip_of({"SKILL.md": skill_md("pdf"), **{f"f{count}.bin": four for count in range(6)}})

    refused("too_large", from_archive, data)


def test_too_many_files_or_entries_are_refused() -> None:
    refused("too_many_files", check, "pdf", [
        Incoming("SKILL.md", skill_md("pdf")),
        *(Incoming(f"notes/{count}.md", b"x") for count in range(package.MOST_FILES))])
    refused("too_many_files", from_archive,
            zip_of({f"d/{count}": b"" for count in range(package.MOST_ARCHIVE_ENTRIES + 1)}))


def test_a_path_in_the_skill_twice_is_refused() -> None:
    refused("duplicate_path", check, "pdf", [Incoming("SKILL.md", skill_md("pdf")),
                                              Incoming("SKILL.md", skill_md("pdf"))])


# ── Staging, and what an update changes ──────────────────────────────────────


def test_staging_writes_every_file_with_its_executable_bit_and_the_same_hash(tmp_path: Path
                                                                            ) -> None:
    result = check("pdf", [Incoming("SKILL.md", skill_md("pdf")),
                           Incoming("scripts/run.sh", b"#!/bin/sh\n", True)])

    target = stage(result, tmp_path / "sp_one")

    assert target == tmp_path / "sp_one" / "pdf"
    assert (target / "scripts" / "run.sh").stat().st_mode & 0o111
    assert not (target / "SKILL.md").stat().st_mode & 0o111
    assert folder_hash(installed_files(target)) == result.content_hash


def test_a_link_found_in_an_installed_folder_changes_its_hash(tmp_path: Path) -> None:
    result = check("pdf", [Incoming("SKILL.md", skill_md("pdf"))])
    target = stage(result, tmp_path / "sp_one")

    os.symlink("/etc/hosts", target / "hosts")

    assert "hosts" in installed_files(target)
    assert folder_hash(installed_files(target)) != result.content_hash


BASE = {"SKILL.md": skill_md("pdf"), "scripts/run.py": b"print(1)", "references/a.md": b"a"}


def updated(files: dict[str, bytes]) -> package.Package:
    return check("pdf", [Incoming(path, data) for path, data in files.items()])


def test_skill_md_changing_switches_the_skill_off() -> None:
    found = changes(BASE, updated({**BASE, "SKILL.md": skill_md("pdf", body="New steps.\n")}))

    assert found.skill_md_changed and found.resets_switches
    assert "+New steps." in found.skill_md_diff and "-Do the thing." in found.skill_md_diff


def test_a_script_changed_or_added_switches_the_skill_off() -> None:
    changed = changes(BASE, updated({**BASE, "scripts/run.py": b"print(2)"}))
    added = changes(BASE, updated({**BASE, "scripts/new.sh": b"echo"}))

    assert changed.scripts_changed == ("scripts/run.py",) and changed.resets_switches
    assert [file.path for file in added.added] == ["scripts/new.sh"] and added.resets_switches


def test_other_changes_keep_the_switches() -> None:
    reference = changes(BASE, updated({**BASE, "references/a.md": b"b"}))
    removed = changes(BASE, updated({"SKILL.md": BASE["SKILL.md"], "references/a.md": b"a"}))

    assert [file.path for file in reference.changed] == ["references/a.md"]
    assert removed.removed == ("scripts/run.py",)
    assert not reference.resets_switches and not removed.resets_switches
    assert not changes(BASE, updated(BASE)).any


def test_a_long_skill_md_diff_is_cut_and_says_how_much_more() -> None:
    before = {"SKILL.md": skill_md("pdf", body="".join(f"old {n}\n" for n in range(600)))}
    after = updated({"SKILL.md": skill_md("pdf", body="".join(f"new {n}\n" for n in range(600)))})

    lines = changes(before, after).skill_md_diff.splitlines()

    assert len(lines) == package.MOST_DIFF_LINES + 1 and lines[-1].startswith("… ")
