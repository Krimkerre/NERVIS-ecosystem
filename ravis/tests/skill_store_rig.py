"""A fake GitHub, fake websites and a fake skills.sh, for the skill store's tests (RAVIS 0.28.0).

Runbook §14.5: no test reaches the network. The skill store fetches through `skill_web.Web`, and
every test here hands it `FakeInternet`'s transport; `conftest.py` fails any `Web` built without
one. The fakes answer the way the real services were read to answer:

- **GitHub's API**: `repos/{owner}/{repo}/commits/{ref}` as the bare sha (422 for a ref it doesn't
  have), `repos/{owner}/{repo}/git/trees/{treeish}` with `recursive=1` or one level, a tree cut
  short when a repository says so, and `x-ratelimit-remaining` / `x-ratelimit-reset` on every
  answer; after `limit_api_after` more calls, 403 with the limit at 0.
- **raw.githubusercontent.com**: `{owner}/{repo}/{ref}/{path}`, a ref that may hold slashes found
  the way GitHub finds it; 429 with `retry-after` while `limit_raw`.
- **Websites and skills.sh**: exact addresses mapped to answers (`FakeInternet.page`).

`store_rig` builds a RAVIS whose Codex can't run (the skill store never needs it), with the skill
store on these fakes, a clock the test moves, and a home folder of the test's own, so the Trash is
`<tmp>/home/.Trash`.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from tests.codex_rig import CodexRig, codex_rig
from tests.test_codex_skills import nervis_folder, real

from ravis.agent.skill_installs import SkillInstalls
from ravis.agent.skill_market import SkillMarket
from ravis.agent.skill_web import Web

START = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
ADMIN = "admin.launcher"
FILE, EXECUTABLE, LINK, SUBMODULE = "100644", "100755", "120000", "160000"


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def skill_md(name: str, description: str = "Does one thing well. Use it when that thing is asked.",
             extra: str = "", body: str = "# Instructions\n\nDo the thing.\n") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n{body}".encode()


Files = dict[str, bytes | tuple[bytes, str]]


@dataclass
class FakeRepository:
    full: str
    default: str
    branches: dict[str, str]
    commits: dict[str, dict[str, tuple[bytes, str]]]
    truncated: bool = False

    def resolve(self, ref: str) -> str | None:
        if ref == "HEAD":
            return self.branches[self.default]
        if ref in self.branches:
            return self.branches[ref]
        return ref if ref in self.commits else None


@dataclass
class FakeGitHub:
    clock: Clock
    repositories: dict[str, FakeRepository] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    api_remaining: int = 60
    limit_api_after: int | None = None
    limit_raw: bool = False
    redirects: dict[str, str] = field(default_factory=dict)

    @property
    def reset_at(self) -> datetime:
        return self.clock() + timedelta(minutes=30)

    def repository(self, full: str, files: Files, branch: str = "main") -> FakeRepository:
        commit = _sha(full, files, 0)
        repository = FakeRepository(full, branch, {branch: commit}, {commit: _normal(files)})
        self.repositories[full.lower()] = repository
        return repository

    def push(self, full: str, files: Files, branch: str | None = None) -> str:
        """A new commit on a branch (the default one unless named): the files it holds now."""
        repository = self.repositories[full.lower()]
        commit = _sha(full, files, len(repository.commits))
        repository.commits[commit] = _normal(files)
        repository.branches[branch or repository.default] = commit
        return commit

    def api_calls(self) -> list[str]:
        return [call for call in self.calls if call.startswith("https://api.github.com/")]

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        assert "authorization" not in request.headers, "a credential was sent to GitHub"
        if url in self.redirects:
            return httpx.Response(301, headers={"location": self.redirects[url]})
        if request.url.host == "api.github.com":
            return self._api(request)
        return self._raw(request)

    def _api(self, request: httpx.Request) -> httpx.Response:
        reset = str(int(self.reset_at.timestamp()))
        if self.limit_api_after is not None:
            if self.limit_api_after <= 0:
                return httpx.Response(403, json={"message": "API rate limit exceeded"},
                                      headers={"x-ratelimit-remaining": "0",
                                               "x-ratelimit-reset": reset})
            self.limit_api_after -= 1
        self.api_remaining = max(0, self.api_remaining - 1)
        headers = {"x-ratelimit-remaining": str(self.api_remaining), "x-ratelimit-reset": reset}
        parts = [unquote(part) for part in request.url.path.split("/") if part]
        repository = self.repositories.get("/".join(parts[1:3]).lower()) if len(parts) > 4 else None
        if repository is None or parts[0] != "repos":
            return httpx.Response(404, json={"message": "Not Found"}, headers=headers)
        if parts[3] == "commits":
            sha = repository.resolve("/".join(parts[4:]))
            if sha is None:
                return httpx.Response(422, json={"message": "No commit found"}, headers=headers)
            return httpx.Response(200, text=sha, headers=headers)
        if parts[3:5] == ["git", "trees"]:
            recursive = request.url.params.get("recursive") == "1"
            return self._tree(repository, "/".join(parts[5:]), recursive, headers)
        return httpx.Response(404, json={"message": "Not Found"}, headers=headers)

    def _tree(self, repository: FakeRepository, treeish: str, recursive: bool,
              headers: dict[str, str]) -> httpx.Response:
        if treeish.startswith("tree:"):
            commit, _, folder = treeish[len("tree:"):].partition(":")
        else:
            commit, folder = repository.resolve(treeish) or "", ""
        if commit not in repository.commits:
            return httpx.Response(404, json={"message": "Not Found"}, headers=headers)
        prefix = f"{folder}/" if folder else ""
        entries: dict[str, dict[str, Any]] = {}
        for path, (data, mode) in repository.commits[commit].items():
            if not path.startswith(prefix):
                continue
            parts = path[len(prefix):].split("/")
            depth = len(parts) if recursive else 1
            for count in range(1, min(depth, len(parts) - 1) + 1):
                inner = "/".join(parts[:count])
                entries[inner] = {"path": inner, "mode": "040000", "type": "tree",
                                  "sha": f"tree:{commit}:{prefix}{inner}"}
            if recursive or len(parts) == 1:
                entries["/".join(parts)] = {
                    "path": "/".join(parts), "mode": mode,
                    "type": "commit" if mode == SUBMODULE else "blob",
                    "sha": hashlib.sha1(data).hexdigest(), "size": len(data),
                }
        listed = sorted(entries.values(), key=lambda entry: entry["path"])
        cut = repository.truncated and recursive and not folder
        return httpx.Response(200, headers=headers, json={
            "sha": commit, "tree": listed[:2] if cut else listed, "truncated": cut})

    def _raw(self, request: httpx.Request) -> httpx.Response:
        if self.limit_raw:
            return httpx.Response(429, headers={"retry-after": "120"})
        parts = [unquote(part) for part in request.url.path.split("/") if part]
        repository = self.repositories.get("/".join(parts[:2]).lower())
        if repository is not None:
            rest = parts[2:]
            for count in range(1, len(rest)):
                sha = repository.resolve("/".join(rest[:count]))
                found = repository.commits[sha].get("/".join(rest[count:])) if sha else None
                if found is not None:
                    return httpx.Response(200, content=found[0])
        return httpx.Response(404, text="404: Not Found")


def _normal(files: Files) -> dict[str, tuple[bytes, str]]:
    return {path: value if isinstance(value, tuple) else (value, FILE)
            for path, value in files.items()}


def _sha(full: str, files: Files, count: int) -> str:
    digest = hashlib.sha1(f"{full}:{count}".encode())
    for path, value in sorted(_normal(files).items()):
        digest.update(path.encode() + value[0] + value[1].encode())
    return digest.hexdigest()


@dataclass
class FakeInternet:
    """GitHub, and every other address a test names."""

    github: FakeGitHub
    pages: dict[str, Callable[[], httpx.Response]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def page(self, url: str, body: bytes | str | dict[str, Any] = b"", status: int = 200,
             headers: dict[str, str] | None = None) -> None:
        def answer() -> httpx.Response:
            if isinstance(body, dict):
                return httpx.Response(status, json=body, headers=headers)
            content = body.encode() if isinstance(body, str) else body
            return httpx.Response(status, content=content, headers=headers)
        self.pages[url] = answer

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        if request.url.host in ("api.github.com", "raw.githubusercontent.com"):
            return self.github.handle(request)
        assert "authorization" not in request.headers, "a credential was sent to a website"
        answer = self.pages.get(str(request.url))
        return answer() if answer is not None else httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def internet(clock: Clock | None = None) -> FakeInternet:
    return FakeInternet(FakeGitHub(clock or Clock()))


# ── Packages ─────────────────────────────────────────────────────────────────


def zip_of(files: dict[str, bytes], modes: dict[str, int] | None = None,
           encrypted: tuple[str, ...] = ()) -> bytes:
    """A zip holding `files`, each with its Unix mode (a regular file unless named).

    `zipfile` clears an entry's flags as it writes, so the entries named `encrypted` are marked so
    afterwards, in the central directory, which is where a reader looks.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            info = zipfile.ZipInfo(path, date_time=(2026, 9, 15, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (modes or {}).get(path, 0o100644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    data = bytearray(buffer.getvalue())
    at = data.find(b"PK\x01\x02")
    while at != -1:
        length = int.from_bytes(data[at + 28:at + 30], "little")
        if data[at + 46:at + 46 + length].decode() in encrypted:
            data[at + 8] |= 0x1
        at = data.find(b"PK\x01\x02", at + 46 + length)
    return bytes(data)


def tar_of(members: list[tuple[str, bytes | None, bytes]]) -> bytes:
    """A `.tar.gz`: `(name, data, type)`, the type one of tarfile's (`REGTYPE`, `SYMTYPE`...)."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "../../outside"
            info.size = len(data or b"")
            archive.addfile(info, io.BytesIO(data or b""))
    return buffer.getvalue()


# ── A RAVIS with the skill store on the fakes ────────────────────────────────


@dataclass
class StoreRig:
    rig: CodexRig
    internet: FakeInternet
    clock: Clock
    #: One entry each time the store told Codex to apply the switches again.
    moved: list[int]
    tmp_path: Path

    @property
    def github(self) -> FakeGitHub:
        return self.internet.github

    @property
    def folder(self) -> Path:
        return nervis_folder(self.tmp_path)

    @property
    def trash(self) -> Path:
        return real(self.tmp_path) / "home" / ".Trash"

    @property
    def installs(self) -> SkillInstalls:
        return self.rig.app.app.state.skill_installs  # type: ignore[no-any-return]

    @property
    def staging(self) -> Path:
        return self.installs.staging

    def published(self, event: str) -> list[dict[str, Any]]:
        return self.rig.published(event)


def store_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **settings: Any) -> StoreRig:
    monkeypatch.setenv("HOME", str(real(tmp_path) / "home"))
    rig = codex_rig(tmp_path, app_server=False, **settings)
    clock = Clock()
    fake = internet(clock)
    moved: list[int] = []
    state = rig.app.app.state
    state.skill_installs = SkillInstalls(state.database, state.settings,
                                         Web(transport=fake.transport(), now=clock),
                                         on_change=lambda: moved.append(1), now=clock)
    state.skill_market = SkillMarket(state.database, state.settings, state.skill_installs)
    return StoreRig(rig, fake, clock, moved, tmp_path)


def zip_post(relay: Any, data: bytes, caller: str = ADMIN) -> httpx.Response:
    """The zip preview, its bytes as the whole body."""
    return relay.http.post("/api/v1/skills/previews/zip", content=data,
                           headers={**relay.rig.caller(caller),
                                    "content-type": "application/zip"})


def body_of(response: httpx.Response) -> Any:
    assert response.status_code == 200, (response.status_code, response.text)
    return response.json()


def dumped(value: Any) -> str:
    return json.dumps(value, sort_keys=True)
