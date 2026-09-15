"""Reading skills from GitHub without a token (RAVIS 0.28.0).

Every source the Skills page offers but a website ends up here: a GitHub source's folders, a link
list's links, and skills.sh's results all name a skill folder in a GitHub repository, and review,
install and Update read it the same way. All through `skill_web.Web`, on `GITHUB_HOSTS` only.

**What each read costs** — GitHub allows about 60 API calls an hour without a token, and files
from `raw.githubusercontent.com` don't count against that:
- **A branch or tag to a commit**: one API call (`commits/{ref}`, answered as the bare sha). No ref
  means the repository's default branch, asked for as `HEAD`, so no second call finds its name.
- **A repository's files**: one API call (`git/trees/{commit}?recursive=1`). GitHub cuts a very
  large tree short (`truncated`); then RAVIS walks down to the folder it needs, one call per level.
- **A file**: `raw.githubusercontent.com`, at the commit, so what is reviewed is what is installed.

**A link's ref may hold slashes** (`tree/feature/x/skills/pdf`), so where the ref ends is found by
asking: the shortest ref first, at most `MOST_REF_TRIES`.

**The files of a skill folder** follow `skill_package`'s rules, and two more that only a git tree
has: a link (mode `120000`) and a submodule (a link to another repository) are refused.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from ravis.agent.skill_catalog import MOST_DEPTH, SKILL_FILE, front_matter, named, one_line
from ravis.agent.skill_package import (
    LICENSE_FILES,
    MOST_FILE_BYTES,
    MOST_FILES,
    MOST_PATH_DEPTH,
    MOST_TOTAL_BYTES,
    Incoming,
    PackageRefusedError,
    front_matter_problem,
    plain_path,
)
from ravis.agent.skill_web import GITHUB_HOSTS, NotFoundError, Web, WebError

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
#: A tree answer; GitHub's own cap on one is about 7 MB.
MOST_TREE_BYTES = 8 * 1024 * 1024
#: A `SKILL.md` read for a listing: the most the other models are ever served.
MOST_HEAD_BYTES = 64 * 1024
MOST_REF_TRIES = 3
#: Files and `SKILL.md`s fetched at once from `raw.githubusercontent.com`.
AT_ONCE = 8
OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
REPOSITORY = re.compile(r"[A-Za-z0-9._-]{1,100}")
REF = re.compile(r"[A-Za-z0-9._/+-]{1,200}")
SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Repository:
    owner: str
    name: str

    @property
    def full(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def key(self) -> str:
        """GitHub names are the same whatever their case: how two mentions are compared."""
        return self.full.lower()

    @classmethod
    def parse(cls, text: object) -> Repository | None:
        """`owner/name`, also as `https://github.com/owner/name` with or without `.git`."""
        if not isinstance(text, str):
            return None
        value = text.strip().removesuffix("/").removesuffix(".git")
        for prefix in ("https://github.com/", "http://github.com/", "https://www.github.com/",
                       "github.com/"):
            value = value.removeprefix(prefix)
        owner, _, name = value.partition("/")
        if (OWNER.fullmatch(owner) and REPOSITORY.fullmatch(name) and name not in (".", "..")):
            return cls(owner, name)
        return None


def clean_ref(value: object) -> str | None:
    """A branch or tag name RAVIS will ask GitHub for, or None when it can't be one."""
    if not isinstance(value, str) or not REF.fullmatch(value):
        return None
    if (".." in value or value.startswith(("-", "/", ".")) or value.endswith(("/", ".lock"))
            or "//" in value):
        return None
    return value


def clean_folder(value: object) -> str | None:
    """A folder inside a repository: `''` for its top, else a plain relative path; else None."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return None
    folder = value.strip().strip("/")
    if not folder:
        return ""
    try:
        return plain_path(folder)
    except PackageRefusedError:
        return None


@dataclass(frozen=True)
class Link:
    """A GitHub address that may name a skill folder or a repository of skills."""

    repository: Repository
    #: What follows `tree/` or `blob/`, ref and folder together; empty for a repository's own page.
    rest: tuple[str, ...]

    def splits(self) -> list[tuple[str | None, str]]:
        """Where the ref may end: `(ref, folder)` pairs, the shortest ref first."""
        if not self.rest:
            return [(None, "")]
        return [("/".join(self.rest[:count]), "/".join(self.rest[count:]))
                for count in range(1, min(MOST_REF_TRIES, len(self.rest)) + 1)]


def parse_link(url: str) -> Link | None:
    """A github.com link to a repository, a folder (`tree/`) or a `SKILL.md` (`blob/`); else None.

    Issues, pull requests, releases, a person's page and anything else on github.com aren't skills.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("https", "http") or (parts.hostname or "") not in ("github.com",
                                                                                "www.github.com"):
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2:
        return None
    repository = Repository.parse(f"{segments[0]}/{segments[1]}")
    if repository is None:
        return None
    if len(segments) == 2:
        return Link(repository, ())
    return _folder_link(repository, segments[2], tuple(segments[3:]))


def _folder_link(repository: Repository, kind: str, rest: tuple[str, ...]) -> Link | None:
    """A `tree/` link to a folder, or a `blob/` link to a folder's `SKILL.md`; else None."""
    if kind == "blob" and rest and rest[-1] == SKILL_FILE:
        rest = rest[:-1]
    elif kind != "tree":
        return None
    if not rest or clean_ref(rest[0]) is None or clean_folder("/".join(rest[1:])) is None:
        return None
    return Link(repository, rest)


@dataclass(frozen=True)
class Located:
    """A skill folder pinned to a commit: what a review reads and an install records."""

    repository: Repository
    #: The branch or tag asked for; None for the repository's default branch.
    ref: str | None
    folder: str
    commit: str


@dataclass(frozen=True)
class TreeEntry:
    path: str
    mode: str
    type: str
    size: int


@dataclass(frozen=True)
class Head:
    """A skill folder as a listing shows it, from its `SKILL.md`'s front matter."""

    folder: str
    name: str
    description: str
    license: str | None
    #: Why it can't be installed as it stands, in plain words; None when its front matter passes.
    problem: str | None


class GitHub:
    """GitHub's API and raw files, under `Web`'s rules."""

    def __init__(self, web: Web) -> None:
        self.web = web

    async def commit(self, repository: Repository, ref: str | None) -> str:
        """The commit a branch or tag (or the default branch, for None) is at now."""
        answer = await self.web.get(
            f"{API}/repos/{repository.full}/commits/{quote(ref or 'HEAD', safe='/')}",
            hosts=GITHUB_HOSTS, most_bytes=1024, accept="application/vnd.github.sha",
        )
        sha = answer.body.decode("ascii", "replace").strip()
        if not SHA.fullmatch(sha):
            raise NotFoundError(f"GitHub didn't name a commit for {ref or 'the default branch'} "
                                f"of {repository.full}")
        return sha

    async def locate(self, repository: Repository, splits: Sequence[tuple[str | None, str]]
                     ) -> Located:
        """The first `(ref, folder)` whose ref GitHub knows, pinned to its commit now."""
        missing: NotFoundError | None = None
        for ref, folder in splits:
            try:
                return Located(repository, ref, folder, await self.commit(repository, ref))
            except NotFoundError as failure:
                missing = failure
        raise missing or NotFoundError(f"GitHub has no such branch or tag in {repository.full}")

    async def tree(self, repository: Repository, treeish: str) -> tuple[list[TreeEntry], bool]:
        """Every entry below a commit, branch, tag or tree; whether GitHub cut the list short."""
        answer = await self.web.get(
            f"{API}/repos/{repository.full}/git/trees/{quote(treeish, safe='/')}?recursive=1",
            hosts=GITHUB_HOSTS, most_bytes=MOST_TREE_BYTES, accept="application/vnd.github+json",
        )
        data = _json(answer.body, repository)
        entries = data.get("tree")
        if not isinstance(entries, list):
            raise NotFoundError(f"GitHub's list of {repository.full}'s files wasn't readable")
        return [entry for entry in map(_tree_entry, entries) if entry is not None], \
            data.get("truncated") is True

    async def folder_entries(self, located: Located) -> list[TreeEntry]:
        """Every entry inside the located folder, paths relative to it, cut short or not."""
        entries, truncated = await self.tree(located.repository, located.commit)
        if truncated and located.folder:
            entries, truncated = await self._walk_down(located)
            prefix = ""
        else:
            prefix = f"{located.folder}/" if located.folder else ""
        if truncated:
            raise PackageRefusedError("too_many_files", "GitHub cut the list of this folder's "
                                      "files short, so RAVIS can't check them all.")
        return [TreeEntry(entry.path[len(prefix):], entry.mode, entry.type, entry.size)
                for entry in entries if entry.path.startswith(prefix)]

    async def _walk_down(self, located: Located) -> tuple[list[TreeEntry], bool]:
        """The folder's own tree, reached one level at a time from the commit's."""
        treeish = located.commit
        for segment in located.folder.split("/"):
            answer = await self.web.get(
                f"{API}/repos/{located.repository.full}/git/trees/{treeish}",
                hosts=GITHUB_HOSTS, most_bytes=MOST_TREE_BYTES,
                accept="application/vnd.github+json",
            )
            listed = _json(answer.body, located.repository).get("tree")
            inner = next((entry for entry in (listed if isinstance(listed, list) else [])
                          if isinstance(entry, dict) and entry.get("path") == segment
                          and entry.get("type") == "tree"), None)
            if inner is None or not isinstance(inner.get("sha"), str):
                raise NotFoundError(f"{located.repository.full} has no folder {located.folder}")
            treeish = inner["sha"]
        return await self.tree(located.repository, treeish)

    async def download(self, located: Located) -> list[Incoming]:
        """The located folder's files, at its commit, checked as they are listed and fetched."""
        entries = await self.folder_entries(located)
        if not any(entry.path == SKILL_FILE and entry.type == "blob" for entry in entries):
            raise NotFoundError(f"{located.repository.full} has no SKILL.md in "
                                f"{located.folder or 'its top folder'}")
        files = _checked_entries(entries)
        prefix = f"{located.folder}/" if located.folder else ""
        limit = asyncio.Semaphore(AT_ONCE)

        async def fetch(entry: TreeEntry) -> Incoming:
            async with limit:
                data = await self.raw(located.repository, located.commit, prefix + entry.path,
                                      MOST_FILE_BYTES)
            return Incoming(entry.path, data, entry.mode == "100755")

        return list(await asyncio.gather(*(fetch(entry) for entry in files)))

    async def raw(self, repository: Repository, ref: str, path: str, most_bytes: int) -> bytes:
        answer = await self.web.get(
            f"{RAW}/{repository.full}/{quote(ref, safe='/')}/{quote(path)}",
            hosts=GITHUB_HOSTS, most_bytes=most_bytes,
        )
        return answer.body

    async def heads(self, repository: Repository, ref: str,
                    folders: Sequence[tuple[str, str | None]]) -> list[Head]:
        """Each `(folder, license file)`'s front matter, read at `ref`; a folder that can't be read
        is listed with why."""
        limit = asyncio.Semaphore(AT_ONCE)

        async def one(folder: str, license_file: str | None) -> Head:
            path = f"{folder}/{SKILL_FILE}" if folder else SKILL_FILE
            try:
                async with limit:
                    data = await self.raw(repository, ref, path, MOST_HEAD_BYTES)
            except WebError as failure:
                return _unread(folder, repository, f"its SKILL.md couldn't be read: {failure.why}")
            return head_of(folder or repository.name, folder, data, license_file)

        return list(await asyncio.gather(*(one(folder, found) for folder, found in folders)))


def head_of(folder_name: str, folder: str, data: bytes, license_file: str | None) -> Head:
    """A listing's entry from a `SKILL.md`'s bytes: lenient, with its problem where it has one."""
    fallback = folder_name.rsplit("/", 1)[-1]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Head(folder, fallback, "", None, "its SKILL.md isn't UTF-8 text")
    header = front_matter(text)
    if isinstance(header, str):
        return Head(folder, fallback, "", None, header)
    name, description, problem = named(header)
    said = header.get("license")
    licence = (one_line(said, 200) if isinstance(said, str) and said.strip()
               else f"{license_file} in its folder" if license_file else None)
    problem = problem or front_matter_problem(header, fallback)
    return Head(folder, name or fallback, description, licence, problem)


def _unread(folder: str, repository: Repository, problem: str) -> Head:
    return Head(folder, folder.rsplit("/", 1)[-1] or repository.name, "", None, problem)


def skill_folders(entries: Sequence[TreeEntry], roots: Sequence[str], most: int
                  ) -> tuple[list[tuple[str, str | None]], bool]:
    """Folders holding a `SKILL.md` below any of `roots`: each with its license file, and whether
    the list stopped at `most`.

    At most `MOST_DEPTH` folders below a root; a skill's own sub-folders aren't searched for more
    skills; a hidden folder below a root is skipped (a root may itself be hidden, like openai's
    `skills/.curated`).
    """
    blobs = {entry.path for entry in entries if entry.type == "blob"}
    found: list[tuple[str, str | None]] = []
    for root in roots:
        prefix = f"{root}/" if root else ""
        candidates = sorted(path[:-len(SKILL_FILE)].rstrip("/") for path in blobs
                            if path.startswith(prefix) and path.rsplit("/", 1)[-1] == SKILL_FILE)
        for folder in candidates:
            below = folder[len(prefix):].split("/") if folder != root else []
            if (len(below) > MOST_DEPTH or any(part.startswith(".") for part in below)
                    or any(_inside(folder, taken) for taken, _ in found)):
                continue
            if len(found) >= most:
                return found, True
            found.append((folder, _license_file(blobs, folder)))
    return found, False


def _inside(folder: str, other: str) -> bool:
    return other == "" and folder != "" or folder.startswith(f"{other}/")


def _license_file(blobs: set[str], folder: str) -> str | None:
    prefix = f"{folder}/" if folder else ""
    for path in sorted(blobs):
        if path.startswith(prefix) and "/" not in path[len(prefix):] \
                and path[len(prefix):].lower() in LICENSE_FILES:
            return path[len(prefix):]
    return None


def _checked_entries(entries: Sequence[TreeEntry]) -> list[TreeEntry]:
    """The folder's files, after the rules only a git tree needs: no links, no submodules, and the
    count and size caps checked from the tree before anything is fetched."""
    files, total = [], 0
    for entry in entries:
        if entry.type == "commit":
            raise PackageRefusedError("submodule", f"{entry.path} is a link to another "
                                      "repository (a submodule), which RAVIS doesn't follow.")
        if entry.type != "blob":
            continue
        plain_path(entry.path)
        if entry.mode == "120000":
            raise PackageRefusedError("link", f"{entry.path} is a link. RAVIS doesn't install "
                                              "links, since one can point outside the skill.")
        if entry.size > MOST_FILE_BYTES:
            raise PackageRefusedError("file_too_large", f"{entry.path} is larger than "
                                      f"{MOST_FILE_BYTES // (1024 * 1024)} MB.")
        total += entry.size
        files.append(entry)
    if len(files) > MOST_FILES or total > MOST_TOTAL_BYTES:
        raise PackageRefusedError("too_large", f"The folder holds more than {MOST_FILES} files "
                                  f"or {MOST_TOTAL_BYTES // (1024 * 1024)} MB.")
    if any(len(entry.path.split("/")) > MOST_PATH_DEPTH + 1 for entry in files):
        raise PackageRefusedError("unsafe_path", "The folder's files lie too many folders deep.")
    return files


def _tree_entry(raw: object) -> TreeEntry | None:
    if not isinstance(raw, dict):
        return None
    path, mode, kind, size = raw.get("path"), raw.get("mode"), raw.get("type"), raw.get("size", 0)
    if not (isinstance(path, str) and isinstance(mode, str) and isinstance(kind, str)):
        return None
    return TreeEntry(path, mode, kind, size if isinstance(size, int) else 0)


def _json(body: bytes, repository: Repository) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError:
        raise NotFoundError(f"GitHub's answer about {repository.full} wasn't readable") from None
    if not isinstance(data, dict):
        raise NotFoundError(f"GitHub's answer about {repository.full} wasn't readable")
    return data
