"""Handing back a file NERVIS wrote, as a download.

Chat could already write a reply or a conversation into the workspace, and stop
there — which is right about what to do and wrong about where to stop, because
the workspace is on the machine NERVIS runs on and that is not always the
machine somebody is reading the dashboard from.

The whole risk is that a workspace is a directory a person chose, and it holds
whatever they keep there. So this file is mostly about what must *not* come back
out of it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nervis.app import create_app
from nervis.config import Settings


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "chat.pdf").write_bytes(b"%PDF-1.4 a conversation")
    (tmp_path / "notes.md").write_text("# notes", encoding="utf-8")
    (tmp_path / "secrets.env").write_text("KEY=hunter2", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(workspace: Path, tmp_path: Path) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "nervis.db"),
        workspace_path=str(workspace),
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_a_written_pdf_comes_back_as_a_download(client: TestClient) -> None:
    """`attachment` rather than letting the browser decide.

    The point of the endpoint is a file landing in Downloads, and a PDF rendered
    in a tab instead is the browser being helpful in the one way nobody asked
    for.
    """
    answer = client.get("/api/v1/documents/chat.pdf")
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "application/pdf"
    assert answer.headers["content-disposition"].startswith("attachment")
    assert "chat.pdf" in answer.headers["content-disposition"]
    assert answer.content.startswith(b"%PDF")


def test_markdown_comes_back_too(client: TestClient) -> None:
    assert client.get("/api/v1/documents/notes.md").status_code == 200


def test_only_what_nervis_writes_is_served(client: TestClient) -> None:
    """An allowlist, because the interesting files in somebody's workspace are
    the ones nobody thought to forbid."""
    answer = client.get("/api/v1/documents/secrets.env")
    assert answer.status_code == 409
    assert b"hunter2" not in answer.content


def test_a_name_that_escapes_the_workspace_is_refused(client: TestClient) -> None:
    """Refused as a boundary, not reported as a typo.

    Percent-encoded, because the router normalises a literal `../` away before
    the handler ever sees it — a test using the plain form would pass while
    proving nothing about the containment.
    """
    answer = client.get("/api/v1/documents/%2e%2e%2f%2e%2e%2foutside.pdf")
    assert answer.status_code == 409
    assert "outside the workspace" in answer.json()["error"]["message"]


def test_a_symlink_out_of_the_workspace_is_refused(
    client: TestClient, workspace: Path, tmp_path: Path
) -> None:
    """Resolved before compared, which is the whole guarantee.

    A link named innocently and pointing anywhere is the case that defeats a
    check done the other way round.
    """
    outside = tmp_path.parent / "elsewhere.pdf"
    outside.write_bytes(b"%PDF not yours")
    os.symlink(outside, workspace / "innocent.pdf")
    answer = client.get("/api/v1/documents/innocent.pdf")
    assert answer.status_code == 409
    assert b"not yours" not in answer.content


def test_a_missing_file_is_not_a_boundary_violation(client: TestClient) -> None:
    """Different facts, different answers. Collapsing them reports a refusal as
    a typo and a typo as a refusal."""
    assert client.get("/api/v1/documents/nothing.pdf").status_code == 404


def test_without_a_workspace_nothing_is_served(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_path=str(tmp_path / "n.db"), workspace_path="", _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/documents/chat.pdf").status_code == 409
