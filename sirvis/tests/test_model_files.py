"""Reveal and Delete's ground truth: where a model's files are, and the Trash (§8, 19 Sep 2026).

The listings here have the shapes `lms ls --json` and `lms ls --variants --json` printed on the
owner's Mac, and the folders the layouts found there: a catalogue model is an 84 KB description
under `hub/models` plus a weights folder under `models`, named with other capitals than the disk
has; a download is a file in its repository folder, or an MLX folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sirvis.model_files import ModelFilesError, locate, to_trash


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path]:
    models, hub = tmp_path / "models", tmp_path / "hub" / "models"
    # A catalogue model: a description, and weights the build names in its own capitals.
    (hub / "google" / "gemma").mkdir(parents=True)
    (hub / "google" / "gemma" / "manifest.json").write_text("{}")
    weights = models / "lmstudio-community" / "gemma-it-gguf"
    weights.mkdir(parents=True)
    (weights / "gemma-Q4_0.gguf").write_bytes(b"w" * 700)
    (weights / "mmproj-gemma.gguf").write_bytes(b"m" * 70)
    # Two downloads sharing one repository folder, and one alone in its own.
    shared = models / "owner" / "pair-GGUF"
    shared.mkdir(parents=True)
    (shared / "pair-Q4.gguf").write_bytes(b"a" * 40)
    (shared / "pair-Q8.gguf").write_bytes(b"b" * 80)
    alone = models / "LGAI" / "solo-GGUF"
    alone.mkdir(parents=True)
    (alone / "solo.gguf").write_bytes(b"s" * 30)
    # An MLX folder.
    (models / "mlx-community" / "tiny-4bit").mkdir(parents=True)
    (models / "mlx-community" / "tiny-4bit" / "model.safetensors").write_bytes(b"x" * 50)
    return models, hub


PLAIN = [
    {"modelKey": "google/gemma", "path": "google/gemma"},
    {"modelKey": "pair-q4", "path": "owner/pair-GGUF/pair-Q4.gguf"},
    {"modelKey": "pair-q8", "path": "owner/pair-GGUF/pair-Q8.gguf"},
    {"modelKey": "solo", "path": "LGAI/solo-GGUF/solo.gguf"},
    {"modelKey": "tiny", "path": "mlx-community/tiny-4bit"},
    {"modelKey": "bundled-embedder", "path": "nomic-ai/embed-GGUF/embed.gguf"},
]
VARIANTS = [{"variants": [
    {"indexedModelIdentifier": "google/gemma@lmstudio-community/Gemma-IT-GGUF/gemma-Q4_0.gguf"},
]}]


def test_a_catalogue_model_is_its_description_and_its_weights(roots: tuple[Path, Path]) -> None:
    models, hub = roots
    files = locate("google/gemma", PLAIN, VARIANTS, models, hub)
    weights = models / "lmstudio-community" / "gemma-it-gguf"
    assert files.targets == (hub / "google" / "gemma", weights)
    assert files.reveal == models / "lmstudio-community" / "gemma-it-gguf", "Reveal shows weights"
    assert files.size_bytes == 2 + 700 + 70  # the description's "{}" and both weight files


def test_a_download_alone_in_its_folder_takes_the_folder(roots: tuple[Path, Path]) -> None:
    models, hub = roots
    assert locate("solo", PLAIN, VARIANTS, models, hub).targets == (models / "LGAI" / "solo-GGUF",)
    assert locate("tiny", PLAIN, VARIANTS, models, hub).targets == (
        models / "mlx-community" / "tiny-4bit",)


def test_a_download_sharing_its_folder_takes_only_its_file(roots: tuple[Path, Path]) -> None:
    models, hub = roots
    files = locate("pair-q4", PLAIN, VARIANTS, models, hub)
    assert files.targets == (models / "owner" / "pair-GGUF" / "pair-Q4.gguf",)
    assert files.size_bytes == 40


def test_a_model_that_came_with_lm_studio_is_refused_with_why(roots: tuple[Path, Path]) -> None:
    models, hub = roots
    with pytest.raises(ModelFilesError) as refused:
        locate("bundled-embedder", PLAIN, VARIANTS, models, hub)
    assert "may come with LM Studio itself" in str(refused.value)
    with pytest.raises(ModelFilesError):
        locate("never-installed", PLAIN, VARIANTS, models, hub)


def test_a_link_is_refused_rather_than_followed(roots: tuple[Path, Path], tmp_path: Path) -> None:
    models, hub = roots
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (models / "mlx-community" / "linked").symlink_to(elsewhere)
    plain = [*PLAIN, {"modelKey": "linked", "path": "mlx-community/linked"}]
    with pytest.raises(ModelFilesError) as refused:
        locate("linked", plain, VARIANTS, models, hub)
    assert "is a link" in str(refused.value)


def test_the_mac_trash_takes_it_and_a_second_one_gets_its_own_name(tmp_path: Path) -> None:
    trash = tmp_path / ".Trash"
    for _ in range(2):
        folder = tmp_path / "models" / "solo-GGUF"
        folder.mkdir(parents=True)
        (folder / "solo.gguf").write_bytes(b"s")
        to_trash(folder, system="Darwin", trash=trash)
        assert not folder.exists()
    names = sorted(child.name for child in trash.iterdir())
    assert names[0] == "solo-GGUF" and names[1].startswith("solo-GGUF ")


def test_the_linux_trash_follows_the_freedesktop_layout(tmp_path: Path) -> None:
    folder = tmp_path / "models" / "tiny-4bit"
    folder.mkdir(parents=True)
    (folder / "model.safetensors").write_bytes(b"x")
    trash = tmp_path / "Trash"

    to_trash(folder, system="Linux", trash=trash, gio="")

    assert (trash / "files" / "tiny-4bit" / "model.safetensors").exists()
    info = (trash / "info" / "tiny-4bit.trashinfo").read_text()
    assert info.startswith("[Trash Info]\nPath=")
    assert str(folder.resolve()) in info.replace("%20", " ")
    assert "DeletionDate=" in info and not folder.exists()
