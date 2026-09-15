"""Shared fixtures.

Runbook §14.5: no test reaches a live model, a network or a real service. Every
database here is in-memory and every client speaks to the app object directly
through ASGI, so the suite runs in seconds and its results mean the same thing
on any machine.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from ravis.config import Settings
from ravis.credentials import KEYRING_SWITCH

#: The checkout's own database, which the RAVIS running on this Mac uses.
CHECKOUT_DATABASE = Path(__file__).resolve().parents[1] / "ravis.db"

# **No test ever opens a relative `ravis.db`** (found 13 September 2026). `Settings()`'s default
# `database_path` is `ravis.db`, relative to the working directory, and several tests build the
# app with defaults. Run from `ravis/`, a full suite opened the live RAVIS's own database — and once
# migration 8 existed, migrated it past what the running RAVIS could read, taking RAVIS down.
# Set here, at import, so it holds before any test module is even collected; a test that names
# its own `database_path` still gets what it names.
os.environ["RAVIS_DATABASE_PATH"] = ":memory:"


@pytest.fixture(autouse=True, scope="session")
def _never_the_checkouts_database() -> Any:
    """Fail the run if it created the checkout's `ravis.db` (`tests/test_no_checkout_database.py`).

    Only creation is checked: where the file already exists, the live RAVIS writes to it on its
    own, so a changed modification time would say nothing about the suite.
    """
    existed = CHECKOUT_DATABASE.exists()
    yield
    assert existed or not CHECKOUT_DATABASE.exists(), (
        f"the test run created {CHECKOUT_DATABASE}; a test opened a relative database path"
    )


@pytest.fixture(autouse=True)
def _never_the_operators_own_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every on-disk store at a throwaway directory, for every test.

    This file already promised it: *"its results mean the same thing on any
    machine"*. Four stores broke that promise by resolving their own paths at
    construction — credentials, provider enable/disable, model filters and pool
    membership all land in `~/.config/ravis`, and `create_app` builds all four.
    So the suite read whatever the developer's dashboard had been clicking.

    It was not theoretical. Narrowing `ravis/clarvis-chat` to four models in the
    picker made `test_a_pool_id_is_resolved_before_forwarding` fail, because the
    fake upstream in that test serves none of the four — a green suite turned
    red from a UI interaction in another process, with nothing in the diff.

    `config_directory` reads `XDG_CONFIG_HOME` precisely so a test never touches
    a real home directory. The mechanism was there; nothing used it globally.
    Autouse and function-scoped, so tests cannot leak state into each other
    either.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    # `TestClient` addresses the app as `http://testserver`, so the Host check
    # added for §16 item 5 would refuse the whole suite. Named here once rather
    # than carved into the check as a test-shaped exception, and rather than
    # rewritten into two hundred call sites.
    monkeypatch.setenv("RAVIS_ALLOWED_HOSTS", '["127.0.0.1", "localhost", "::1", "testserver"]')
    # **Codex is switched off for the suite, and its data folder is a throwaway**
    # (runbook §2.2, M29). Left on, every test that enters the lifespan would ask
    # Homebrew where the real Codex is and run it. The Codex tests switch it back on,
    # with fake programs (`tests/codex_fakes.py`).
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("RAVIS_CODEX_ENABLED", "false")
    # **The NERVIS skills folder is a throwaway too** (`agent/skills.py`). RAVIS makes the folder
    # when Codex starts, and its default is the owner's real `NERVIS workspace`; a test that forgot
    # to name its own must never create or read that one.
    skills = tmp_path / "coding" / "NERVIS workspace" / "clarvis" / "skills"
    monkeypatch.setenv("RAVIS_CODEX_SKILLS_FOLDER", str(skills))
    # **And so are the owner's personal skills** (`agent/skill_catalog.py`): RAVIS reads that
    # folder itself since 0.27.0, and its default is the owner's real `~/.agents/skills`.
    personal = tmp_path / "home" / ".agents" / "skills"
    monkeypatch.setenv("RAVIS_SKILLS_PERSONAL_FOLDER", str(personal))


@pytest.fixture
def settings() -> Settings:
    """Default settings, pinned to an in-memory database.

    Explicit rather than relying on the environment: a test that reads the
    developer's .env passes or fails for reasons the test does not state.
    """
    return Settings(
        database_path=":memory:",
        _env_file=None,  # type: ignore[call-arg]
    )


ADMIN_SECRET = "an-admin-secret-for-tests"


@pytest.fixture(autouse=True, scope="session")
def _no_keyring_writes_from_the_suite() -> Any:
    """**The suite never writes to this machine's keyring.**

    `as_administrator` below stores a credential through the *app's* real
    store, and once that store began writing to the platform keyring, running
    the tests put `ravis/admin.tests` into the operator's login keychain — found
    on the machine this was written on, and removed by hand. A test that reaches
    outside its `tmp_path` can damage the machine it is checking, so the switch
    is thrown for the whole session rather than per fixture.
    """
    os.environ[KEYRING_SWITCH] = "0"
    # **And the file the store would otherwise read**, which is the other half
    # of the same boundary. With the operator's real keys visible, any test that
    # builds the whole application gets pools full of real hosted models — and
    # `test_transparent_proxy` and the Clarvis conformance suite both duly sent
    # fixture-shaped requests to Anthropic's live API, failed on the reply, and
    # spent the operator's money doing it. A ten-minute suite at 21% CPU is what
    # that looks like from outside: waiting on the network, not computing.
    held = tempfile.mkdtemp(prefix="ravis-tests-config-")
    was = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = held
    yield
    os.environ.pop(KEYRING_SWITCH, None)
    if was is None:
        os.environ.pop("XDG_CONFIG_HOME", None)
    else:
        os.environ["XDG_CONFIG_HOME"] = was


def as_administrator(store: Any) -> dict[str, str]:
    """Store an `admin.` credential and return the header that presents it.

    **Configuration writes need one from §16 item 4 onwards.** Before that, a
    loopback bind was itself the permission, so a test could change a provider
    or a pool with a bare `TestClient(app)`. That is the bypass item 4 removed:
    administration used to arrive free with the ability to call the gateway.

    Tests of the *feature* — does the toggle round-trip, does a filter select
    what it says — take this and get on with it. Tests of the *boundary* build
    their identity themselves, because what they are asserting is which callers
    the guard turns away.
    """
    store.store("admin.tests", ADMIN_SECRET)
    return {"Authorization": f"Bearer {ADMIN_SECRET}"}
