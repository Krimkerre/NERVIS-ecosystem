"""Pictures, both directions: one somebody attaches, and one a model draws.

The two are deliberately in one file because they are one feature to the person
using it — *show chat a picture, ask chat for a picture* — and because they
share the one thing that makes either work: an image is not text, so every
place that assumed "a file is characters" has to say what it does instead.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from nervis import documents
from test_m4_chat import _attach, _with_models, an_api, turn

#: A real 1×1 PNG. Small enough to inline, and genuinely decodable — a
#: `b"\x89PNG"` stub would pass every assertion here and fail the moment
#: anything actually opened it.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)


def _user_content(sent: list[dict[str, Any]]) -> Any:
    return [m for body in sent for m in body.get("messages", []) if m["role"] == "user"][-1]


def _prompt(sent: list[dict[str, Any]]) -> str:
    return " ".join(
        str(message.get("content", ""))
        for body in sent for message in body.get("messages", [])
    )


# ── A picture somebody attached ──────────────────────────────────────────────


def test_an_attached_picture_travels_as_the_picture_it_is(tmp_path: Path) -> None:
    """There is no text version of a photograph, so the image part is not an
    enrichment of the reading — it is the whole reading."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen2.5vl-3b"], vision=True)
    _attach(client, "cv_pic", "shot.png", PNG)

    turn(client, "what is in this picture?", system="Be someone.", attachment_id="cv_pic")

    content = _user_content(sent)["content"]
    assert isinstance(content, list), "a text part and an image part, not a bare string"
    images = [part for part in content if part["type"] == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "shot.png, a picture" in _prompt(sent)


def test_the_page_switch_does_not_empty_an_attached_picture(tmp_path: Path) -> None:
    """The switch exists so a *document* can be read without paying for vision,
    and the document's text stays behind when its pages are dropped. A picture
    has nothing behind it: applying the same switch would answer "what is in
    this photo" from a prompt containing no photo."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen2.5vl-3b"], vision=True)
    _attach(client, "cv_switch", "shot.png", PNG)

    turn(client, "what is in this picture?", system="Be someone.",
         attachment_id="cv_switch", page_images=False)

    content = _user_content(sent)["content"]
    assert isinstance(content, list)
    assert any(part["type"] == "image_url" for part in content)


def test_a_picture_nothing_can_see_is_said_rather_than_dropped(tmp_path: Path) -> None:
    """RAVIS reads an image as a hard requirement, so it cannot be sent where
    nothing has vision — and a model told nothing about the omission describes
    the picture anyway, which is the worst available answer."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"], vision=False)
    _attach(client, "cv_blind", "shot.png", PNG)

    turn(client, "what is in this picture?", system="Be someone.",
         attachment_id="cv_blind")

    assert isinstance(_user_content(sent)["content"], str), "no image part"
    assert "No model available to this request can see images" in _prompt(sent)


def test_a_picture_too_large_for_a_prompt_says_to_resize_it(tmp_path: Path) -> None:
    """Half a picture is not a smaller picture, so this refuses where a text
    file truncates — and the refusal has to name the way out."""
    (tmp_path / "huge.png").write_bytes(b"\x89PNG" + b"\0" * documents.MAX_IMAGE_BYTES)

    with pytest.raises(ValueError, match="resize it"):
        documents.read_document(tmp_path, "huge.png")


def test_the_upload_says_whether_chat_can_read_what_it_stored(tmp_path: Path) -> None:
    """**Readability is answered by the side that owns the reader.** The
    dashboard kept its own copy of the suffix list, "in step with
    `documents.py` by hand", and the hand slipped the day pictures became
    readable: an uploaded `.png` was described correctly by chat and carried
    the label *not readable as text* on its own card, in the transcript, beside
    the answer that disproved it."""
    client = an_api(workspace_path=str(tmp_path))

    picture = _attach(client, "cv_says", "shot.png", PNG).json()
    archive = _attach(client, "cv_says", "bundle.zip", b"PK\x03\x04").json()

    assert picture["file"]["readable"] is True
    assert archive["file"]["readable"] is False


# ── A picture the model drew ─────────────────────────────────────────────────


def _drawing(client: Any, sent: list[dict[str, Any]], url: str) -> None:
    """A RAVIS that answers with one image, in the shape RAVIS actually sends.

    Measured through the running gateway on 7 September 2026 against
    `google/gemini-2.5-flash-image`, `google/gemini-3.1-flash-image` and
    `openai/gpt-5-image-mini`: one delta carrying an `images` array of
    `image_url` parts, then the ordinary finish.
    """
    lines = [
        b'data: ' + json.dumps({"model": "draw-1", "choices": [{"delta": {
            "role": "assistant", "content": "",
            "images": [{"type": "image_url", "image_url": {"url": url}}],
        }}]}).encode() + b"\n\n",
        b'data: ' + json.dumps({"model": "draw-1", "choices": [
            {"delta": {"content": ""}, "finish_reason": "stop"}]}).encode() + b"\n\n",
        b"data: [DONE]\n\n",
    ]

    def capture(request: httpx.Request) -> httpx.Response:
        if "/api/v1/models" in str(request.url):
            return httpx.Response(200, json={"items": []})
        if "/v1/embeddings" in str(request.url):
            return httpx.Response(200, json={"object": "list", "data": [], "model": "none"})
        sent.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"".join(lines)))

    client.app.state.probe_client = httpx.AsyncClient(
        transport=httpx.MockTransport(capture)
    )


def test_a_drawn_picture_becomes_a_file_and_a_link(tmp_path: Path) -> None:
    """A megabyte of base64 in a stream frame is not something a conversation
    can keep: the reply NERVIS stores is text, and a browser reloads its
    history from that text. Saving it is what makes the picture survive the
    stream, and the link in the reply is what makes it downloadable."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _drawing(client, sent, "data:image/png;base64," + base64.b64encode(PNG).decode())

    answered = turn(client, "draw me a red circle", system="Be someone.")

    written = [item.name for item in (tmp_path / "export").iterdir() if item.suffix == ".png"]
    assert len(written) == 1, written
    # The link carries the room, because that is where the file is: a bare
    # name points at the workspace root and resolves to nothing.
    assert f"](/api/v1/documents/export/{written[0]})" in answered.text

    stored = client.get(
        f"/api/v1/chat/conversations/{answered.headers['x-conversation-id']}"
    ).json()
    assert f"](/api/v1/documents/export/{written[0]})" in stored["items"][-1]["content"]


def test_the_link_reaches_the_browser_before_the_stream_ends(tmp_path: Path) -> None:
    """A client stops reading at `[DONE]`, and the first version put the link
    behind it — so the file was written, the reply stored it, and no browser
    watching the answer arrive ever saw the picture."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _drawing(client, sent, "data:image/png;base64," + base64.b64encode(PNG).decode())

    body = turn(client, "draw me a red circle", system="Be someone.").text

    assert "/api/v1/documents/" in body
    assert body.index("/api/v1/documents/") < body.index("[DONE]")


def test_a_drawn_picture_is_served_back_to_be_looked_at(tmp_path: Path) -> None:
    """`Content-Disposition: attachment` on the image a conversation is showing
    replaces the picture with a download prompt, which is the browser being
    helpful in the one way nobody asked for."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _drawing(client, sent, "data:image/png;base64," + base64.b64encode(PNG).decode())
    turn(client, "draw me a red circle", system="Be someone.")
    name = next(item.name for item in (tmp_path / "export").iterdir() if item.suffix == ".png")

    served = client.get(f"/api/v1/documents/export/{name}")

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.headers["content-disposition"].startswith("inline")
    assert served.content == PNG


def test_a_drawn_picture_keeps_the_format_it_was_drawn_in(tmp_path: Path) -> None:
    """The falsifier for naming everything `.png`. A file named for a format it
    does not hold is one the browser refuses to show and the person cannot
    open, and the media type in the URL is the only thing that knows."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _drawing(client, sent, "data:image/webp;base64," + base64.b64encode(PNG).decode())

    turn(client, "draw me a red circle", system="Be someone.")

    assert [item.suffix for item in (tmp_path / "export").iterdir() if item.is_file()] == [".webp"]


def test_asking_a_text_profile_to_draw_is_told_where_drawing_lives(
    tmp_path: Path,
) -> None:
    """Measured the day this went in: asked to draw a cat on the default
    profile, chat said "I can't draw images myself" and then offered to route
    the request to a hosted model, which it cannot do. Both halves are wrong —
    NERVIS draws, and it draws by the person changing one control."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"])

    turn(client, "draw me a picture of a cat", system="Be someone.")

    assert "Image generation profile (ravis/draw)" in _prompt(sent)


def test_the_drawing_profile_is_not_told_to_switch_to_itself(tmp_path: Path) -> None:
    """The falsifier for the note: on the profile that draws, the sentence is
    an instruction to change nothing, which is noise in the one place a model
    is already short of room."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"])

    turn(client, "draw me a picture of a cat", system="Be someone.",
         profile="ravis/draw")

    assert "Image generation profile" not in _prompt(sent)


def test_drawing_a_conclusion_is_not_asking_for_a_picture(tmp_path: Path) -> None:
    """The other falsifier. Telling a model that somebody asked for a picture
    when they asked for an inference is a confident wrong steer."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"])

    turn(client, "what conclusion would you draw from that?", system="Be someone.")

    assert "Image generation profile" not in _prompt(sent)


def test_a_reply_with_no_picture_gains_no_link(tmp_path: Path) -> None:
    """The falsifier for the saving path: an ordinary answer must reach the
    workspace no differently than it did before any of this existed."""
    sent: list[dict[str, Any]] = []
    client = an_api(workspace_path=str(tmp_path))
    _with_models(client, sent, ["qwen/qwen3-4b-2507"])

    answered = turn(client, "say hello", system="Be someone.")

    assert "/api/v1/documents/" not in answered.text
    assert [item.name for item in (tmp_path / "export").iterdir() if item.is_file()] == []
