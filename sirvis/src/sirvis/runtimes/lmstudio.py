"""The LM Studio adapter (§7, milestone M2).

Two facts about LM Studio 0.4.x shape everything here, and both were established
by probing the running application rather than by reading its documentation.

**It answers 200 for endpoints it does not have.** `GET /api/v0/load` returns
`200 {"error": "Unexpected endpoint or method. (GET /api/v0/load)"}`, and so
does a path invented at random. An adapter that trusted the status code would
report an unsupported operation as a success, so every response here is checked
for an `error` key before it is believed.

**There is no HTTP load or unload.** The REST surface covers discovery,
completion and embeddings; lifecycle lives in the `lms` CLI. §7 permits exactly
this — "CLI tooling may exist as a debug fallback but must not be the primary
abstraction while an API exists" — and no API exists for these two. Everything
LM Studio *does* expose over HTTP is read over HTTP.

A third fact matters for anyone reading a benchmark taken through this adapter:
**LM Studio loads a model on demand.** A completion naming an unloaded model
will load it, and that load time lands inside the first request. §11 warmups
exist because of this; M2 only has to make it visible.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

from sirvis.runtimes.base import (
    LoadedModel,
    RuntimeInfo,
    RuntimeState,
    RuntimeUnavailableError,
)

RUNTIME_KEY = "lmstudio"

# The lifecycle CLI. LM Studio installs it here; `which` finds it when the user
# has added it to PATH. Absence is reported rather than raised — a SIRVIS that
# can read the runtime but not drive it is degraded, not broken (§15.4).
LMS_DEFAULT_PATH = "~/.lmstudio/bin/lms"

# Loading a multi-gigabyte model off disk is slow, and the timeout has to allow
# for it. Everything else uses the short one, because a discovery call that
# takes ten seconds is a failure wearing a success's clothes.
LOAD_TIMEOUT_SECONDS = 600.0
PROBE_TIMEOUT_SECONDS = 10.0


class LMStudioAdapter:
    """Reads LM Studio over HTTP, drives its lifecycle over the CLI.

    Holds no state about what is loaded. The runtime is the authority on that
    (§7: "never hard-code an assumption where LM Studio can report actual
    capability or state"), and a cache of it would be wrong the first time
    anything else — a user, RAVIS, another SIRVIS — loaded something.
    """

    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        lms_path: str = LMS_DEFAULT_PATH,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._lms_path = lms_path

    # ── Reading, over HTTP ───────────────────────────────────────────────────

    async def health(self) -> RuntimeInfo:
        """Whether the runtime is there, and what it is doing.

        Never raises. This is the call every other part of SIRVIS uses to decide
        whether the runtime is worth talking to, and one that threw would make
        "LM Studio is closed" — the ordinary state of a laptop — into an error
        path in every caller.
        """
        try:
            payload = await self._get("/api/v0/models")
        except RuntimeUnavailableError as failure:
            return RuntimeInfo(
                runtime_key=RUNTIME_KEY,
                state=RuntimeState.STOPPED,
                base_url=self.base_url,
                detail=str(failure),
            )
        models = payload.get("data", [])
        return RuntimeInfo(
            runtime_key=RUNTIME_KEY,
            state=RuntimeState.READY,
            base_url=self.base_url,
            detail="",
            model_count=len(models) if isinstance(models, list) else None,
        )

    async def runtime_info(self) -> RuntimeInfo:
        """§7's `runtime_info`. Today the same reading as `health`.

        Kept as its own method because §7 names both and they diverge as soon as
        there is a version or an engine list to report — at which point the
        callers are already asking the right question.
        """
        return await self.health()

    async def list_models(self) -> list[dict[str, Any]]:
        """Every installed build the runtime knows about, verbatim.

        Returned as the runtime reported it, without reshaping into a SIRVIS
        model domain — that is M3's job, and inventing the mapping here would
        put it in two places. What this does add is `runtime_key`, so a caller
        can tell which runtime a record came from once there is more than one.
        """
        payload = await self._get("/api/v0/models")
        entries = payload.get("data", [])
        if not isinstance(entries, list):
            raise RuntimeUnavailableError("model list was not a list")
        return [
            {**entry, "runtime_key": RUNTIME_KEY}
            for entry in entries
            if isinstance(entry, dict)
        ]

    async def list_loaded_models(self) -> list[LoadedModel]:
        """What is resident right now, with the configuration it actually has.

        The effective context length is the interesting field: it is what the
        model was *loaded* with, which is not necessarily what anyone asked for,
        and §7.1 requires the difference to be visible rather than assumed away.
        """
        loaded = []
        for entry in await self.list_models():
            if entry.get("state") not in ("loaded", "loading"):
                continue
            loaded.append(
                LoadedModel(
                    model_key=str(entry.get("id", "")),
                    state=str(entry.get("state", "unknown")),
                    effective={
                        "context_length": entry.get("loaded_context_length")
                        or entry.get("max_context_length"),
                        "quantization": entry.get("quantization"),
                        "compatibility_type": entry.get("compatibility_type"),
                    },
                )
            )
        return loaded

    async def generate(self, model_key: str, messages: list[dict[str, Any]],
                       **options: Any) -> dict[str, Any]:
        """One completion, for probing rather than for serving traffic.

        SIRVIS generates to *measure*; RAVIS generates to serve. This deliberately
        does not stream: M2 needs a round trip that proves the path works, and
        time-to-first-token — which is the number that needs streaming — is M6's
        job and belongs with the benchmark engine that knows how to time it.
        """
        body = {"model": model_key, "messages": messages, **options}
        return await self._post("/v1/chat/completions", body, timeout=LOAD_TIMEOUT_SECONDS)

    # ── Lifecycle, over the CLI, because no API offers it ───────────────────

    def lifecycle_available(self) -> bool:
        """Whether load and unload can be driven at all on this machine."""
        return self._resolve_lms() is not None

    async def load(self, model_key: str, config: dict[str, Any] | None = None) -> LoadedModel:
        """Load a model, recording what was asked for and what was ignored.

        §7.1: store **both** the requested and the effective configuration, and
        never silently ignore an unsupported value. The CLI accepts a context
        length and a GPU setting; anything else in `config` is reported back in
        `ignored` rather than dropped, because a benchmark run under a
        configuration that was quietly discarded is evidence about nothing.
        """
        requested = dict(config or {})
        arguments = ["load", model_key, "--yes"]
        if "context_length" in requested:
            arguments += ["--context-length", str(requested["context_length"])]
        if "gpu_offload" in requested:
            arguments += ["--gpu", str(requested["gpu_offload"])]
        honoured = {"context_length", "gpu_offload"}

        self._run_lms(arguments, timeout=LOAD_TIMEOUT_SECONDS)

        resident = {model.model_key: model for model in await self.list_loaded_models()}
        actual = resident.get(model_key)
        return LoadedModel(
            model_key=model_key,
            state=actual.state if actual else "unknown",
            requested=requested,
            effective=actual.effective if actual else {},
            ignored=sorted(set(requested) - honoured),
        )

    async def unload(self, model_key: str) -> None:
        """Unload one model.

        SIRVIS never calls this directly once M8 exists: §9 requires every load
        and unload to flow through the Resource Manager, which knows whether
        anybody else is using the thing. Until M8, this is the primitive that
        manager will drive, and it is why the method takes a model rather than
        deciding for itself what ought to go.
        """
        self._run_lms(["unload", model_key], timeout=PROBE_TIMEOUT_SECONDS)

    async def unload_all(self) -> None:
        """Unload everything. Only ever an operator's explicit choice."""
        self._run_lms(["unload", "--all"], timeout=PROBE_TIMEOUT_SECONDS)

    # ── Plumbing ─────────────────────────────────────────────────────────────

    async def _get(self, path: str) -> dict[str, Any]:
        return await self._request("GET", path, None, PROBE_TIMEOUT_SECONDS)

    async def _post(self, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        return await self._request("POST", path, body, timeout)

    async def _request(self, method: str, path: str, body: dict[str, Any] | None,
                       timeout: float) -> dict[str, Any]:
        """One HTTP call, with LM Studio's 200-shaped errors caught.

        The `error` check is the whole reason this is not three lines. LM Studio
        answers 200 with an error body for any endpoint it does not implement,
        so `raise_for_status()` alone would let an unsupported operation through
        as a success — which is worse than a failure, because it is silent.
        """
        client = self._client
        owned = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=timeout)
        try:
            response = await client.request(
                method, f"{self.base_url}{path}", json=body, timeout=timeout
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as failure:
            raise RuntimeUnavailableError(f"{method} {path}: {failure}") from failure
        finally:
            if owned:
                await client.aclose()

        if not isinstance(payload, dict):
            raise RuntimeUnavailableError(f"{method} {path}: response was not an object")
        if "error" in payload:
            raise RuntimeUnavailableError(f"{method} {path}: {payload['error']}")
        return payload

    def _resolve_lms(self) -> str | None:
        """Where the `lms` binary is, or None when it is not installed.

        **An explicitly configured path is authoritative.** `PATH` is searched
        only when the caller left the default alone — otherwise naming a path
        and silently running a different binary found elsewhere would make the
        setting a suggestion, and an operator who pointed SIRVIS at one LM
        Studio install would be measuring through another.
        """
        expanded = Path(self._lms_path).expanduser()
        if expanded.is_file():
            return str(expanded)
        if self._lms_path != LMS_DEFAULT_PATH:
            return None
        return shutil.which("lms")

    def _run_lms(self, arguments: list[str], timeout: float) -> str:
        """Run one `lms` command, or say clearly that the lifecycle is unavailable."""
        binary = self._resolve_lms()
        if binary is None:
            raise RuntimeUnavailableError(
                "the lms CLI is not installed, and LM Studio exposes no HTTP load/unload — "
                "lifecycle operations are unavailable on this machine"
            )
        try:
            finished = subprocess.run(
                [binary, *arguments], capture_output=True, text=True,
                timeout=timeout, check=True,
            )
        except subprocess.CalledProcessError as failure:
            detail = (failure.stderr or failure.stdout or "").strip()
            raise RuntimeUnavailableError(f"lms {' '.join(arguments)}: {detail}") from failure
        except (subprocess.SubprocessError, OSError) as failure:
            raise RuntimeUnavailableError(f"lms {' '.join(arguments)}: {failure}") from failure
        return finished.stdout.strip()


def parse_lms_json(output: str) -> list[dict[str, Any]]:
    """Read `lms ... --json` output, tolerating the noise it prints around it.

    Separate from the adapter so it can be tested against recorded output
    without a subprocess, which is the only way §14.5's no-live-services rule
    and covering this parsing can both hold.
    """
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                continue
            if isinstance(parsed, list):
                return [entry for entry in parsed if isinstance(entry, dict)]
    return []
