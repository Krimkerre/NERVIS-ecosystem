"""Reading skills from GitHub without a token (`agent/skill_github.py`, RAVIS 0.28.0).

What RAVIS is held to, against the fake GitHub:

- **links**: a repository, a folder (`tree/`) or a `SKILL.md` (`blob/`) on github.com are skill
  links; nothing else on github.com is, and nothing anywhere else;
- **a ref may hold slashes**: the shortest ref GitHub knows wins, at most three tries;
- **a folder's files come from one commit**, with their executable bits, and **links, submodules
  and oversized files are refused from the tree** before any file is fetched; no `SKILL.md` is not
  found; a tree GitHub cut short is walked down to the folder;
- **a listing's skill folders**: under its roots, at most four folders deep, never hidden below a
  root or inside another skill, with each one's license file, and capped;
- **a listing's entries** read front matter leniently and say what an install would refuse.
"""

from __future__ import annotations

import pytest
from tests.skill_store_rig import EXECUTABLE, LINK, SUBMODULE, FakeInternet, internet, skill_md

from ravis.agent.skill_github import (
    GitHub,
    Repository,
    TreeEntry,
    clean_folder,
    clean_ref,
    head_of,
    parse_link,
    skill_folders,
)
from ravis.agent.skill_package import MOST_FILE_BYTES, PackageRefusedError
from ravis.agent.skill_web import NotFoundError, Web

REPOSITORY = Repository("o", "r")


def github_on(fake: FakeInternet) -> GitHub:
    return GitHub(Web(transport=fake.transport(), now=fake.github.clock))


def raw_calls(fake: FakeInternet) -> list[str]:
    return [call for call in fake.github.calls if call.startswith("https://raw.githubusercontent")]


@pytest.mark.parametrize("url, repository, rest", [
    ("https://github.com/anthropics/skills", "anthropics/skills", ()),
    ("https://github.com/anthropics/skills/", "anthropics/skills", ()),
    ("https://github.com/anthropics/skills/tree/main/skills/pdf", "anthropics/skills",
     ("main", "skills", "pdf")),
    ("https://github.com/anthropics/skills/blob/main/skills/pdf/SKILL.md", "anthropics/skills",
     ("main", "skills", "pdf")),
    ("http://www.github.com/o/r/tree/feature/x/pdf?tab=readme#top", "o/r", ("feature", "x", "pdf")),
])
def test_links_to_a_repository_a_folder_or_a_skill_md(url: str, repository: str,
                                                      rest: tuple[str, ...]) -> None:
    link = parse_link(url)

    assert link is not None and (link.repository.full, link.rest) == (repository, rest)


@pytest.mark.parametrize("url", [
    "https://github.com/anthropics", "https://github.com/o/r/issues/1",
    "https://github.com/o/r/pulls", "https://github.com/o/r/blob/main/README.md",
    "https://gitlab.com/o/r/tree/main/pdf", "https://github.com/o/r/tree/main/../../x",
    "https://github.com/o/r/tree", "not a link at all",
])
def test_anything_else_isnt_a_skill_link(url: str) -> None:
    assert parse_link(url) is None


def test_where_a_ref_may_end_is_tried_shortest_first_three_times_at_most() -> None:
    link = parse_link("https://github.com/o/r/tree/feature/x/skills/pdf")
    repository = parse_link("https://github.com/o/r")

    assert link is not None and repository is not None
    assert link.splits() == [("feature", "x/skills/pdf"), ("feature/x", "skills/pdf"),
                             ("feature/x/skills", "pdf")]
    assert repository.splits() == [(None, "")]


def test_repository_ref_and_folder_names() -> None:
    assert Repository.parse("anthropics/skills") == Repository("anthropics", "skills")
    assert Repository.parse("https://github.com/o/r.git") == REPOSITORY
    assert all(Repository.parse(bad) is None
               for bad in ("o", "o/r/x", "-o/r", "o/..", "o r/x", None, 7))
    assert clean_ref("feature/x") == "feature/x"
    assert all(clean_ref(bad) is None for bad in ("..", "-x", "a..b", "x/", "x.lock", "a b", "",
                                                  "/x"))
    assert (clean_folder(None), clean_folder("/skills/pdf/"), clean_folder("../x")) == (
        "", "skills/pdf", None)


async def test_a_ref_holding_a_slash_is_found_by_asking_shortest_first() -> None:
    fake = internet()
    fake.github.repository("o/r", {"skills/pdf/SKILL.md": skill_md("pdf")}, branch="feature/x")
    link = parse_link("https://github.com/o/r/tree/feature/x/skills/pdf")
    assert link is not None

    located = await github_on(fake).locate(link.repository, link.splits())

    assert (located.ref, located.folder) == ("feature/x", "skills/pdf")
    assert len(fake.github.api_calls()) == 2


async def test_a_folder_comes_from_the_commit_reviewed_with_its_executable_bits() -> None:
    fake = internet()
    fake.github.repository("o/r", {"skills/pdf/SKILL.md": skill_md("pdf"),
                                   "skills/pdf/run.sh": (b"echo", EXECUTABLE),
                                   "skills/other/SKILL.md": skill_md("other")})
    github = github_on(fake)
    located = await github.locate(REPOSITORY, [("main", "skills/pdf")])
    fake.github.push("o/r", {"skills/pdf/SKILL.md": skill_md("pdf", body="Changed since.\n")})

    files = await github.download(located)

    assert {file.path: (file.data, file.executable) for file in files} == {
        "SKILL.md": (skill_md("pdf"), False), "run.sh": (b"echo", True)}
    assert raw_calls(fake) and all(f"/{located.commit}/" in call for call in raw_calls(fake))


@pytest.mark.parametrize("extra, reason", [
    ({"skills/pdf/escape": (b"/etc/passwd", LINK)}, "link"),
    ({"skills/pdf/vendor": (b"", SUBMODULE)}, "submodule"),
    ({"skills/pdf/big.bin": b"\0" * (MOST_FILE_BYTES + 1)}, "file_too_large"),
])
async def test_links_submodules_and_oversized_files_are_refused_before_any_file_is_fetched(
    extra: dict[str, bytes | tuple[bytes, str]], reason: str,
) -> None:
    fake = internet()
    fake.github.repository("o/r", {"skills/pdf/SKILL.md": skill_md("pdf"), **extra})
    github = github_on(fake)
    located = await github.locate(REPOSITORY, [("main", "skills/pdf")])

    with pytest.raises(PackageRefusedError) as caught:
        await github.download(located)

    assert caught.value.reason == reason and raw_calls(fake) == []


async def test_a_folder_without_a_skill_md_is_not_found() -> None:
    fake = internet()
    fake.github.repository("o/r", {"skills/pdf/README.md": b"not a skill"})
    github = github_on(fake)

    with pytest.raises(NotFoundError, match="no SKILL.md"):
        await github.download(await github.locate(REPOSITORY, [("main", "skills/pdf")]))


async def test_a_tree_github_cut_short_is_walked_down_to_the_folder() -> None:
    fake = internet()
    fake.github.repository("o/r", {"a/1.txt": b"1", "b/2.txt": b"2",
                                   "skills/pdf/SKILL.md": skill_md("pdf"),
                                   "skills/pdf/notes/more.md": b"more"}).truncated = True
    github = github_on(fake)

    files = await github.download(await github.locate(REPOSITORY, [("main", "skills/pdf")]))

    assert {file.path for file in files} == {"SKILL.md", "notes/more.md"}
    assert sum("/git/trees/" in call for call in fake.github.api_calls()) == 4


def blobs(*paths: str) -> list[TreeEntry]:
    return [TreeEntry(path, "100644", "blob", 1) for path in paths]


def test_the_skill_folders_a_listing_shows() -> None:
    listed, stopped = skill_folders(blobs(
        "skills/.curated/pdf/SKILL.md", "skills/.curated/pdf/LICENSE.txt",
        "skills/.curated/pdf/inner/SKILL.md", "skills/.curated/.hidden/SKILL.md",
        "skills/.curated/a/b/c/d/SKILL.md", "skills/.curated/a/b/c/d/e/SKILL.md",
        "skills/other/SKILL.md", "README.md",
    ), ["skills/.curated"], 10)

    assert listed == [("skills/.curated/pdf", "LICENSE.txt"), ("skills/.curated/a/b/c/d", None)]
    assert stopped == 0


def test_a_listing_at_a_repositorys_top_and_its_cap() -> None:
    capped, left_out = skill_folders(blobs("one/SKILL.md", "two/SKILL.md", "three/SKILL.md"),
                                     [""], 2)
    top, _ = skill_folders(blobs("SKILL.md", "inner/SKILL.md"), [""], 5)

    assert [folder for folder, _ in capped] == ["one", "three"] and left_out == 1
    assert top == [("", None)]


def test_a_capped_listing_takes_the_folders_nearest_the_top_first() -> None:
    """ComposioHQ/awesome-claude-skills's shape, found by the owner on 15 September 2026: its own
    skills at the top, beside a folder of generated ones that sort before them."""
    generated = [f"composio-skills/{app}-automation/SKILL.md" for app in ("asana", "box", "canva")]
    capped, left_out = skill_folders(blobs(
        *generated, "zapier/SKILL.md", "changelog-generator/SKILL.md", "a/b/SKILL.md",
        "changelog-generator/examples/SKILL.md",
    ), [""], 3)
    roots, _ = skill_folders(blobs("skills/.experimental/x/SKILL.md",
                                   "skills/.curated/z/y/SKILL.md", "skills/.curated/w/SKILL.md"),
                             ["skills/.curated", "skills/.experimental"], 10)

    assert [folder for folder, _ in capped] == ["changelog-generator", "zapier", "a/b"]
    assert left_out == 3
    assert [folder for folder, _ in roots] == [
        "skills/.curated/w", "skills/.experimental/x", "skills/.curated/z/y"]


def test_a_listing_entry_reads_front_matter_leniently_and_says_what_is_wrong() -> None:
    good = head_of("skills/pdf", "skills/pdf", skill_md("pdf", extra="license: MIT\n"), None)
    by_file = head_of("skills/pdf", "skills/pdf", skill_md("pdf"), "LICENSE.txt")
    mismatch = head_of("skills/pdf", "skills/pdf", skill_md("pdf-tools"), None)
    broken = head_of("skills/pdf", "skills/pdf", b"no front matter", None)
    binary = head_of("skills/pdf", "skills/pdf", b"\xff\xfe", None)

    assert (good.name, good.license, good.problem) == ("pdf", "MIT", None)
    assert by_file.license == "LICENSE.txt in its folder"
    assert mismatch.problem is not None and "isn't the name of its folder" in mismatch.problem
    assert broken.name == "pdf" and broken.problem
    assert binary.problem == "its SKILL.md isn't UTF-8 text"


async def test_heads_are_read_raw_and_an_unreadable_one_is_listed_with_why() -> None:
    fake = internet()
    fake.github.repository("o/r", {"a/SKILL.md": skill_md("a"), "b/README.md": b"x"})

    heads = await github_on(fake).heads(REPOSITORY, "HEAD", [("a", None), ("b", None)])

    assert (heads[0].name, heads[0].problem) == ("a", None)
    assert heads[1].name == "b" and "couldn't be read" in (heads[1].problem or "")
    assert fake.github.api_calls() == []
