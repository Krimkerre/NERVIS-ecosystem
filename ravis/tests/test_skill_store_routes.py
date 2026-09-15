"""Installing, updating and removing skills over HTTP (`api/management/skill_store.py`, 0.28.0).

Against the fake GitHub, with a RAVIS whose Codex can't run: the skill store never needs Codex, and
tells it to apply the switches again after each change (`StoreRig.moved`). What RAVIS is held to:

- **a review before anything is installed**: name, description, license, files with scripts
  flagged, `SKILL.md`'s text, warnings; staged in RAVIS's own folder, never the skills folder;
- **an install arrives switched off for both engines**, is recorded and audited without contents,
  and a repeated confirm changes nothing; a skill put in the folder by hand stays on;
- **names that clash** are warned about, and an install over something already there is refused;
- **Update** fetches from where the skill came from, says what changed, and switches the skill off
  when `SKILL.md` or a script changed, keeping its switches otherwise; refused when the folder
  changed after its review; a zip install updates from a new zip only;
- **Remove** is only for what RAVIS installed, and moves the folder to the Trash;
- **reviews expire**, and their staging goes; at most five wait;
- **refusals in plain words**: the package's rules, GitHub rate-limiting (with the time), a link
  that isn't GitHub's, a redirect off GitHub, bodies, and the skills folder;
- **who may call**: the reads NERVIS's relay or an admin; everything else an admin; never Clarvis.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from tests.agent_rig import refused, serving
from tests.skill_store_rig import (
    ADMIN,
    START,
    StoreRig,
    body_of,
    dumped,
    skill_md,
    store_rig,
    zip_of,
    zip_post,
)
from tests.test_codex_skills import real, skill_file

from ravis.agent import store_refusals
from ravis.agent.skill_package import SPECIFICATION
from ravis.agent.skills import CODEX, MODELS, SkillChoices
from ravis.api.management import skill_store
from ravis.api.management.codex import require_admin
from ravis.api.management.skills import require_board_reader

PREVIEWS = "/api/v1/skills/previews"
INSTALLS = "/api/v1/skills/installs"
UPDATE = "/api/v1/skills/installs/update-preview"
REMOVE = "/api/v1/skills/installs/remove"
DISCARD = "/api/v1/skills/previews/discard"
LINK = "https://github.com/anthropics/skills/tree/main/skills/pdf"
PDF = {
    "skills/pdf/SKILL.md": skill_md("pdf", extra="license: Proprietary. LICENSE.txt has complete "
                                                 "terms\n"),
    "skills/pdf/scripts/extract.py": b"print('pdf')\n",
    "skills/pdf/LICENSE.txt": b"Proprietary\n",
}


def call(relay: Any, method: str, path: str, body: Any = None, caller: str = ADMIN) -> Any:
    return relay.call(method, path, caller=caller, body=body)


def review(relay: Any, url: str = LINK) -> dict[str, Any]:
    return body_of(call(relay, "POST", PREVIEWS, {"origin": "github", "url": url}))  # type: ignore[no-any-return]


def confirm(relay: Any, preview: dict[str, Any]) -> dict[str, Any]:
    return body_of(call(relay, "POST", INSTALLS, {"preview_id": preview["preview_id"]}))  # type: ignore[no-any-return]


def choices(store: StoreRig, name: str) -> tuple[bool | None, bool | None]:
    """The owner's switches kept for a skill in NERVIS's folder: (Codex, other models)."""
    path = str(store.folder / name / "SKILL.md")
    database = store.rig.app.app.state.database
    return SkillChoices(database, CODEX).get(path), SkillChoices(database, MODELS).get(path)


def row(relay: Any, name: str) -> dict[str, Any]:
    board = body_of(call(relay, "GET", "/api/v1/skills"))
    return next(entry for entry in board["skills"] if entry["name"] == name)  # type: ignore[no-any-return]


def switch_models_on(relay: Any, store: StoreRig, name: str) -> None:
    body_of(call(relay, "POST", "/api/v1/skills", {"path": str(store.folder / name / "SKILL.md"),
                                                   "engine": MODELS, "enabled": True}))


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StoreRig:
    rig = store_rig(tmp_path, monkeypatch)
    rig.github.repository("anthropics/skills", PDF)
    return rig


# ── A review, then an install switched off ───────────────────────────────────


def test_a_github_folder_is_reviewed_before_anything_is_installed(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        preview = review(relay)

    assert re.fullmatch(r"sp_[\w-]+", preview["preview_id"])
    assert (preview["kind"], preview["name"], preview["arrives_switched_off"]) == (
        "install", "pdf", True)
    assert preview["license"] == {"text": "Proprietary. LICENSE.txt has complete terms",
                                  "from": "front_matter"}
    assert {file["path"]: file["kind"] for file in preview["files"]} == {
        "SKILL.md": "skill_md", "scripts/extract.py": "script", "LICENSE.txt": "text"}
    assert preview["skill_md"] == PDF["skills/pdf/SKILL.md"].decode()
    assert [warning["code"] for warning in preview["warnings"]] == ["has_scripts"]
    assert preview["checked_against"] == SPECIFICATION and preview["update"] is None
    source = preview["source"]
    assert (source["origin"], source["repository"], source["folder"], source["ref"]) == (
        "github", "anthropics/skills", "skills/pdf", "main")
    assert re.fullmatch(r"[0-9a-f]{40}", source["commit"]) and source["commit"] in source["url"]
    assert not (store.folder / "pdf").exists()
    assert (store.staging / preview["preview_id"] / "pdf" / "SKILL.md").is_file()


def test_confirming_installs_it_switched_off_for_both_engines_recorded_and_audited(
    store: StoreRig,
) -> None:
    skill_file(store.folder, "by-hand", "Copied into the folder by hand.")
    with serving(store.rig) as relay:
        preview = review(relay)
        result = confirm(relay, preview)
        installed_row, by_hand_row = row(relay, "pdf"), row(relay, "by-hand")
        listed = body_of(call(relay, "GET", INSTALLS))

    assert result["switches"] == {"codex": False, "models": False}
    assert (result["installed"]["name"], result["installed"]["present"],
            result["installed"]["changed_on_this_mac"]) == ("pdf", True, False)
    assert (store.folder / "pdf" / "scripts" / "extract.py").read_bytes() == b"print('pdf')\n"
    assert not (store.staging / preview["preview_id"]).exists()
    assert choices(store, "pdf") == (False, False)
    assert installed_row["models"] == {"available": True, "enabled": False}
    assert by_hand_row["models"] == {"available": True, "enabled": True}
    assert [entry["name"] for entry in listed["installs"]] == ["pdf"]
    assert listed["installs"][0]["installed_at"] == "2026-09-15T20:00:00Z"
    assert store.moved == [1]
    events = store.published("ravis.skill_installed")
    assert [(event["name"], event["origin"], event["files"], event["scripts"])
            for event in events] == [("pdf", "github", 3, 1)]
    assert "Do the thing" not in dumped(events) and "print(" not in dumped(events)


def test_confirming_the_same_review_again_answers_the_same_and_changes_nothing(
    store: StoreRig,
) -> None:
    with serving(store.rig) as relay:
        preview = review(relay)
        first, second = confirm(relay, preview), confirm(relay, preview)

    assert first == second
    assert len(store.published("ravis.skill_installed")) == 1 and store.moved == [1]


def test_a_name_already_in_the_folder_is_warned_about_and_the_install_refused(
    store: StoreRig,
) -> None:
    skill_file(store.folder, "pdf", "The owner's own pdf skill.")
    with serving(store.rig) as relay:
        preview = review(relay)
        answer = call(relay, "POST", INSTALLS, {"preview_id": preview["preview_id"]})

    warning = next(w for w in preview["warnings"] if w["code"] == "clashes_with_installed")
    assert "put there by hand" in warning["message"]
    refused(answer, 409, "SKILL_ALREADY_INSTALLED", name="pdf", installed_by_ravis=False)
    assert "owner's own" in (store.folder / "pdf" / "SKILL.md").read_text()
    assert choices(store, "pdf") == (None, None) and store.moved == []


def test_a_personal_skill_with_the_same_name_is_warned_about(store: StoreRig) -> None:
    skill_file(real(store.tmp_path) / "home" / ".agents" / "skills", "pdf", "Mine.")
    with serving(store.rig) as relay:
        preview = review(relay)

    assert "clashes_with_personal" in [warning["code"] for warning in preview["warnings"]]


def test_a_skill_named_in_a_repository_is_found_and_installed_through_github(
    store: StoreRig,
) -> None:
    with serving(store.rig) as relay:
        preview = body_of(call(relay, "POST", PREVIEWS, {
            "origin": "github", "repository": "anthropics/skills", "skill": "pdf",
            "via": "skills_sh"}))
        missing = call(relay, "POST", PREVIEWS, {"origin": "github",
                                                 "repository": "anthropics/skills",
                                                 "skill": "docx"})
        confirm(relay, preview)
        listed = body_of(call(relay, "GET", INSTALLS))

    assert (preview["source"]["folder"], preview["source"]["ref"], preview["source"]["via"]) == (
        "skills/pdf", None, "skills_sh")
    refused(missing, 404, "SKILL_SOURCE_NOT_FOUND")
    assert listed["installs"][0]["source"]["via"] == "skills_sh"


# ── Updates ──────────────────────────────────────────────────────────────────


def test_an_update_changing_skill_md_switches_it_off_and_trashes_the_earlier_version(
    store: StoreRig,
) -> None:
    with serving(store.rig) as relay:
        confirm(relay, review(relay))
        switch_models_on(relay, store, "pdf")
        commit = store.github.push("anthropics/skills", {
            **PDF, "skills/pdf/SKILL.md": skill_md("pdf", body="# New steps\n")})
        preview = body_of(call(relay, "POST", UPDATE, {"name": "pdf"}))
        result = confirm(relay, preview)

    update = preview["update"]
    assert (preview["kind"], preview["arrives_switched_off"], update["switches"]) == (
        "update", False, "reset")
    assert "SKILL.md changed" in update["why"]
    assert [file["path"] for file in update["changes"]["changed"]] == ["SKILL.md"]
    assert "+# New steps" in update["changes"]["skill_md_diff"]
    assert result["switches_reset"] is True and choices(store, "pdf") == (False, False)
    assert "# New steps" in (store.folder / "pdf" / "SKILL.md").read_text()
    trashed = Path(result["earlier_version"]["trash"])
    assert trashed.parent == store.trash and trashed.name.startswith("pdf (before update) ")
    assert "Do the thing" in (trashed / "SKILL.md").read_text()
    assert result["updated"]["source"]["commit"] == commit
    assert [event["switches_reset"] for event in store.published("ravis.skill_updated")] == [True]
    assert store.moved == [1, 1]
    assert not [path for path in store.folder.iterdir() if path.name.startswith(".ravis")]


def test_an_update_changing_only_other_files_keeps_the_switches(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        confirm(relay, review(relay))
        switch_models_on(relay, store, "pdf")
        store.github.push("anthropics/skills", {**PDF, "skills/pdf/LICENSE.txt": b"Changed\n"})
        preview = body_of(call(relay, "POST", UPDATE, {"name": "pdf"}))
        result = confirm(relay, preview)

    assert preview["update"]["switches"] == "kept"
    assert result["switches_reset"] is False and choices(store, "pdf") == (False, True)


def test_a_skill_still_at_its_sources_commit_is_up_to_date(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        confirm(relay, review(relay))
        answer = body_of(call(relay, "POST", UPDATE, {"name": "pdf"}))

    assert (answer["up_to_date"], answer["changed_on_this_mac"]) == (True, False)
    assert "preview_id" not in answer and not any(store.staging.iterdir())


def test_an_update_is_refused_when_the_folder_changed_after_its_review(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        confirm(relay, review(relay))
        store.github.push("anthropics/skills", {**PDF, "skills/pdf/SKILL.md": skill_md("pdf",
                                                                                        body="v2")})
        preview = body_of(call(relay, "POST", UPDATE, {"name": "pdf"}))
        (store.folder / "pdf" / "LICENSE.txt").write_text("edited on this Mac\n")
        answer = call(relay, "POST", INSTALLS, {"preview_id": preview["preview_id"]})
        listed = body_of(call(relay, "GET", INSTALLS))

    refused(answer, 409, "SKILL_CHANGED_SINCE_REVIEW", name="pdf")
    assert (store.folder / "pdf" / "LICENSE.txt").read_text() == "edited on this Mac\n"
    assert listed["installs"][0]["changed_on_this_mac"] is True


def test_a_zip_install_updates_from_a_new_zip_file_only(store: StoreRig) -> None:
    first = zip_of({"notes/SKILL.md": skill_md("notes"), "notes/a.md": b"a"})
    second = zip_of({"notes/SKILL.md": skill_md("notes"), "notes/a.md": b"b",
                     "notes/run.sh": b"#!/bin/sh\n"})
    with serving(store.rig) as relay:
        installed = body_of(zip_post(relay, first))
        confirm(relay, installed)
        nowhere = call(relay, "POST", UPDATE, {"name": "notes"})
        update = body_of(zip_post(relay, second))
        result = confirm(relay, update)

    assert installed["source"] == {"origin": "zip"} and installed["kind"] == "install"
    refused(nowhere, 409, "SKILL_NOT_UPDATABLE", name="notes", origin="zip")
    assert (update["kind"], update["update"]["switches"]) == ("update", "reset")
    assert result["switches_reset"] is True


def test_only_a_skill_ravis_installed_can_be_updated(store: StoreRig) -> None:
    skill_file(store.folder, "by-hand", "Mine.")
    with serving(store.rig) as relay:
        answer = call(relay, "POST", UPDATE, {"name": "by-hand"})

    refused(answer, 404, "SKILL_NOT_INSTALLED", name="by-hand")


# ── Removing ─────────────────────────────────────────────────────────────────


def test_remove_moves_the_folder_to_the_trash_and_forgets_its_switches_and_record(
    store: StoreRig,
) -> None:
    with serving(store.rig) as relay:
        confirm(relay, review(relay))
        switch_models_on(relay, store, "pdf")
        result = body_of(call(relay, "POST", REMOVE, {"name": "pdf"}))
        listed = body_of(call(relay, "GET", INSTALLS))

    trashed = Path(result["trash"])
    assert result["removed"] == "pdf"
    assert trashed == store.trash / f"pdf {START.astimezone().strftime('%Y-%m-%d %H.%M.%S')}"
    assert (trashed / "SKILL.md").is_file() and not (store.folder / "pdf").exists()
    assert choices(store, "pdf") == (None, None) and listed["installs"] == []
    assert [(event["name"], event["trashed"])
            for event in store.published("ravis.skill_removed")] == [("pdf", True)]
    assert store.moved == [1, 1]


def test_the_same_name_trashed_twice_in_a_second_is_numbered(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        for _ in range(2):
            confirm(relay, review(relay))
            last = body_of(call(relay, "POST", REMOVE, {"name": "pdf"}))

    assert last["trash"].endswith(" 2") and len(list(store.trash.iterdir())) == 2


def test_remove_is_only_for_skills_ravis_installed(store: StoreRig) -> None:
    skill_file(store.folder, "by-hand", "Mine.")
    with serving(store.rig) as relay:
        by_hand = call(relay, "POST", REMOVE, {"name": "by-hand"})
        unknown = call(relay, "POST", REMOVE, {"name": "nothing"})
        climbing = call(relay, "POST", REMOVE, {"name": "../by-hand"})

    refused(by_hand, 404, "SKILL_NOT_INSTALLED")
    refused(unknown, 404, "SKILL_NOT_INSTALLED")
    refused(climbing, 422, "INVALID_REQUEST_BODY")
    assert (store.folder / "by-hand" / "SKILL.md").is_file()


def test_removing_a_skill_whose_folder_is_already_gone_forgets_it(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        confirm(relay, review(relay))
        shutil.rmtree(store.folder / "pdf")
        result = body_of(call(relay, "POST", REMOVE, {"name": "pdf"}))
        again = call(relay, "POST", REMOVE, {"name": "pdf"})

    assert result == {"removed": "pdf", "trash": None}
    refused(again, 404, "SKILL_NOT_INSTALLED")


# ── Reviews expire ───────────────────────────────────────────────────────────


def test_a_review_expires_its_staging_goes_and_confirming_it_is_refused(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        preview = review(relay)
        store.clock.advance(minutes=16)
        answer = call(relay, "POST", INSTALLS, {"preview_id": preview["preview_id"]})

    refused(answer, 404, "SKILL_PREVIEW_NOT_FOUND")
    assert not (store.staging / preview["preview_id"]).exists()
    assert not (store.folder / "pdf").exists()


def test_a_discarded_review_goes_at_once_and_at_most_five_wait(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        previews = [review(relay) for _ in range(6)]
        discarded = body_of(call(relay, "POST", DISCARD, {"preview_id": previews[1]["preview_id"]}))
        again = body_of(call(relay, "POST", DISCARD, {"preview_id": previews[1]["preview_id"]}))

    assert (discarded, again) == ({"discarded": True}, {"discarded": False})
    assert sorted(path.name for path in store.staging.iterdir()) == sorted(
        preview["preview_id"] for preview in previews[2:])


# ── Refusals ─────────────────────────────────────────────────────────────────


def test_a_package_breaking_a_rule_is_refused_with_its_reason(store: StoreRig) -> None:
    store.github.repository("o/renamed", {"pdf-main/SKILL.md": skill_md("pdf")})
    with serving(store.rig) as relay:
        slipping = zip_post(relay, zip_of({"SKILL.md": skill_md("pdf"), "../escape.txt": b"x"}))
        mismatch = call(relay, "POST", PREVIEWS, {
            "origin": "github", "url": "https://github.com/o/renamed/tree/main/pdf-main"})
        oversized = zip_post(relay, b"PK" + b"\0" * (8 * 1024 * 1024))

    refused(slipping, 422, "SKILL_REFUSED", reason="unsafe_path")
    refused(mismatch, 422, "SKILL_REFUSED", reason="name_mismatch")
    refused(oversized, 422, "SKILL_REFUSED", reason="archive_too_large")
    assert not store.staging.exists() or not any(store.staging.iterdir())


def test_github_rate_limiting_says_when_to_try_again(store: StoreRig) -> None:
    store.github.limit_api_after = 0
    with serving(store.rig) as relay:
        answer = call(relay, "POST", PREVIEWS, {"origin": "github", "url": LINK})

    error = refused(answer, 503, "SKILL_SOURCE_RATE_LIMITED", host="api.github.com")
    lifts = store.github.reset_at
    assert error["retryable"] is True
    assert error["message"] == (f"GitHub is rate-limiting RAVIS; try again at "
                                f"{store_refusals.local_time(lifts)}.")
    assert error["details"]["retry_at"] == lifts.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_links_and_places_that_arent_there_are_refused_in_plain_words(store: StoreRig) -> None:
    store.github.redirects["https://api.github.com/repos/anthropics/skills/commits/main"] = (
        "https://evil.example/commits/main")
    with serving(store.rig) as relay:
        elsewhere = call(relay, "POST", PREVIEWS, {"origin": "github",
                                                   "url": "https://gitlab.com/o/r/-/tree/main"})
        missing = call(relay, "POST", PREVIEWS, {"origin": "github",
                                                 "url": "https://github.com/no/such"})
        no_skill = call(relay, "POST", PREVIEWS, {"origin": "github", "repository": "o/r"})
        redirected = call(relay, "POST", PREVIEWS, {"origin": "github", "url": LINK})

    refused(elsewhere, 422, "SKILL_SOURCE_REFUSED", reason="not_a_github_link")
    refused(missing, 404, "SKILL_SOURCE_NOT_FOUND")
    refused(no_skill, 404, "SKILL_SOURCE_NOT_FOUND")
    refused(redirected, 422, "SKILL_SOURCE_REFUSED", reason="redirect_elsewhere")
    assert not [url for url in store.internet.calls if "evil.example" in url]


@pytest.mark.parametrize("path, body", [
    (PREVIEWS, {}), (PREVIEWS, {"origin": "github"}), (PREVIEWS, {"origin": "gitlab"}),
    (PREVIEWS, {"origin": "github", "repository": "not a repository"}),
    (PREVIEWS, {"origin": "github", "url": 5}),
    (PREVIEWS, {"origin": "github", "repository": "o/r", "ref": "../x"}),
    (PREVIEWS, {"origin": "github", "repository": "o/r", "folder": "../x"}),
    (PREVIEWS, {"origin": "github", "repository": "o/r", "skill": "a/b"}),
    (PREVIEWS, {"origin": "github", "url": LINK, "via": "nowhere"}),
    (INSTALLS, {}), (INSTALLS, {"preview_id": "nope"}), (DISCARD, {"preview_id": 7}),
    (UPDATE, {"name": "Not A Name"}), (REMOVE, {}),
])
def test_bodies_that_arent_what_a_route_takes_are_refused(store: StoreRig, path: str,
                                                          body: dict[str, Any]) -> None:
    with serving(store.rig) as relay:
        answer = call(relay, "POST", path, body)

    refused(answer, 422, "INVALID_REQUEST_BODY")


def test_an_empty_zip_upload_is_refused(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        refused(zip_post(relay, b""), 422, "INVALID_REQUEST_BODY")


def test_a_skills_folder_that_cant_be_used_refuses_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = store_rig(tmp_path, monkeypatch, codex_skills_folder=str(tmp_path / "elsewhere"))
    store.github.repository("anthropics/skills", PDF)
    with serving(store.rig) as relay:
        answer = call(relay, "POST", INSTALLS, {"preview_id": review(relay)["preview_id"]})

    refused(answer, 409, "SKILLS_FOLDER_UNUSABLE")
    assert not (tmp_path / "elsewhere").exists()


# ── Who may call ─────────────────────────────────────────────────────────────


READS = ["/api/v1/skills/installs", "/api/v1/skills/market"]
WRITES = [PREVIEWS, "/api/v1/skills/previews/zip", DISCARD, INSTALLS, UPDATE, REMOVE,
          "/api/v1/skills/market/refresh", "/api/v1/skills/market/resolve",
          "/api/v1/skills/market/search", "/api/v1/skills/market/sources",
          "/api/v1/skills/market/sources/hide", "/api/v1/skills/market/sources/remove"]


def test_the_reads_are_nervis_and_admin_and_everything_else_is_admin_only(store: StoreRig) -> None:
    with serving(store.rig) as relay:
        for path in READS:
            for caller in ("client.clarvis", "client.other", "anonymous"):
                refused(call(relay, "GET", path, caller=caller), 403, "FORBIDDEN")
            for caller in ("client.nervis", ADMIN):
                assert call(relay, "GET", path, caller=caller).status_code == 200
        for path in WRITES:
            for caller in ("client.nervis", "client.clarvis", "anonymous"):
                refused(call(relay, "POST", path, {}, caller=caller), 403, "FORBIDDEN")


def test_every_route_carries_its_guard() -> None:
    for route in skill_store.router.routes:
        guards = {dependency.call for dependency in route.dependant.dependencies}  # type: ignore[attr-defined]
        wanted = require_board_reader if "GET" in route.methods else require_admin  # type: ignore[attr-defined]
        assert guards == {wanted}, route.path  # type: ignore[attr-defined]
