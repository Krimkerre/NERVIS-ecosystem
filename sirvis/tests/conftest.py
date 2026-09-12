"""Shared fixtures.

Runbook §14.5: no test reaches a live model, a network or a real service. Every
database here is in memory and every client speaks to the app object through
ASGI, so the suite runs in seconds and means the same thing on any machine.
"""

from __future__ import annotations

import pytest

from sirvis.config import Settings

# A port nothing listens on. Every test points here, and the reason is a defect
# rather than caution: `Settings()` defaults `lmstudio_base_url` to
# `127.0.0.1:1234`, which is the *real* LM Studio, so any test building an app
# from defaults had a live adapter wired into it. Runbook §14.5 forbids a test
# reaching a live service, and this one did — it JIT-loaded models onto the
# developer's machine, twice, before anybody noticed. The user noticed.
UNREACHABLE_RUNTIME = "http://127.0.0.1:9"


# A path nothing lives at. LM Studio exposes no HTTP load or unload, so the
# adapter shells out to the `lms` CLI — which is a second channel to the runtime
# that a dead HTTP port does not close. Missing it is what let a "isolated" test
# suite load models for real.
UNREACHABLE_CLI = "/nonexistent/lms"


@pytest.fixture(autouse=True)
def _no_live_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Close **both** channels to a runtime, for every test, without being asked.

    Autouse and environment-level on purpose: a fixture a test has to request is
    one a test can forget, and forgetting is invisible — the suite passes and a
    model quietly loads on whoever ran it.

    Two channels, because closing one is what went wrong. `SIRVIS_LMSTUDIO_BASE_URL`
    handles reads over HTTP. Lifecycle does not go over HTTP at all, so the CLI
    path is pinned too, and `LMStudioAdapter` treats an explicitly configured
    path as authoritative rather than falling back to `PATH`.
    """
    monkeypatch.setenv("SIRVIS_LMSTUDIO_BASE_URL", UNREACHABLE_RUNTIME)
    monkeypatch.setenv("SIRVIS_LMSTUDIO_CLI_PATH", UNREACHABLE_CLI)
    # M11's third channel out: discovery searches Hugging Face. A test that wants a
    # catalogue hands the app a recorded client; none reaches the real one.
    monkeypatch.setenv("SIRVIS_HUGGINGFACE_BASE_URL", UNREACHABLE_RUNTIME)
    # `TestClient` addresses the app as `http://testserver`, so the §16 item 5
    # Host check would refuse the whole suite. Named here once rather than
    # carved into the check as a test-shaped exception.
    monkeypatch.setenv(
        "SIRVIS_ALLOWED_HOSTS", '["127.0.0.1", "localhost", "::1", "testserver"]'
    )


@pytest.fixture(autouse=True)
def _no_lifecycle_to_a_real_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make driving the `lms` CLI fail the test rather than load a model.

    The belt to the previous fixture's braces, and the one that would actually
    have caught this. Pinning a path relies on every construction site reading
    the setting; this relies on nothing, because it replaces the operation.

    Runbook §14.5 says no test reaches a live model, a network or a real
    service. That was true of the HTTP client and quietly false of a subprocess
    — the kind of gap a rule cannot close on its own, so it is closed by making
    the operation raise something a test cannot ignore.

    **Scoped to the adapter's own method, not to `subprocess`.** The first
    attempt patched `lmstudio.subprocess.run`, which is the *module* attribute
    and therefore global: it broke M1's machine detection, which shells out to
    `sysctl` and has nothing to do with runtimes. A guard that disables
    unrelated code is a guard that gets removed.
    """
    from sirvis.runtimes.lmstudio import LMStudioAdapter

    real = LMStudioAdapter._run_lms

    def guarded(
        self: LMStudioAdapter, arguments: list[str], timeout: float, **rest: object
    ) -> str:
        """Refuse only when a real binary would actually have been invoked.

        Not a blanket block, because two tests exist to assert the adapter's own
        behaviour when no CLI is installed — and a guard that pre-empts the code
        under test replaces a real assertion with its own. So when the resolved
        path is absent this defers to the adapter, which raises the
        `RuntimeUnavailableError` those tests are about; only a *resolvable*
        binary is intercepted, which is the case that would spend the
        developer's memory.
        """
        if self._resolve_lms() is None:
            # `**rest` forwarded rather than dropped: the adapter grew a
            # `refused` argument naming which error a non-zero exit means, and a
            # guard that silently discarded it would make the two tests below
            # assert against a signature the real code no longer has.
            return real(self, arguments, timeout, **rest)  # type: ignore[arg-type]
        raise AssertionError(
            f"a test tried to run `lms {' '.join(arguments)}`, which would load or "
            "unload a real model on this machine (runbook §14.5). Use a recorded "
            "runtime, or point the adapter at a path that does not exist."
        )

    monkeypatch.setattr(LMStudioAdapter, "_run_lms", guarded)


@pytest.fixture
def settings() -> Settings:
    """Default settings, pinned to an in-memory database and a dead runtime.

    Explicit rather than reading the environment: a test that picks up a
    developer's .env passes or fails for reasons the test does not state.

    Both runtime channels are named here as well as in the autouse fixture,
    because `_env_file=None` bypasses the environment entirely — so a fixture
    that works by setting environment variables cannot reach this object.
    """
    return Settings(
        database_path=":memory:",
        lmstudio_base_url=UNREACHABLE_RUNTIME,
        lmstudio_cli_path=UNREACHABLE_CLI,
        huggingface_base_url=UNREACHABLE_RUNTIME,
        _env_file=None,  # type: ignore[call-arg]
    )
