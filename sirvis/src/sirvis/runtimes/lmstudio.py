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

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx

from sirvis.errors import InvalidConfigurationError
from sirvis.runtimes.base import (
    GenerationChunk,
    LoadedModel,
    RuntimeInfo,
    RuntimeLoadFailedError,
    RuntimeState,
    RuntimeTimeoutError,
    RuntimeUnavailableError,
)
from sirvis.runtimes.variants import (
    InstalledVariant,
    LoadedVariant,
    confirm,
    installed_sizes,
    installed_variants,
    loaded_variants,
)
from sirvis.runtimes.variants import installed_paths as cli_installed_paths

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



def add_unpublished(published: list[dict[str, Any]],
                    builds: list[InstalledVariant] | None) -> list[dict[str, Any]]:
    """The catalogue, plus the installed builds it left out.

    **Additive on purpose.** The CLI names every build of a group and the HTTP
    catalogue does not, but the CLI also indexes *fewer models overall* on this
    machine — 7 builds against 21 entries — so replacing one reading with the
    other would trade a missing variant for fourteen missing models. Nothing
    published is rewritten or dropped; a build the catalogue already describes
    keeps the catalogue's record, including its state.

    A build is treated as already published when its qualified key is listed, or
    when an entry of the same family already carries its format and
    quantization — which is how the plain key appears while that variant is the
    selected one. What is left is installed, loadable, and was invisible.

    Added records are `not-loaded` by construction: `state` is the one field the
    CLI listing does not carry, and a build the running catalogue does not
    mention is a build the runtime does not have resident.
    """
    if builds is None:
        return published
    listed = {str(entry.get("id") or "") for entry in published}
    described = {
        (
            _family_of(str(entry.get("id") or "")),
            str(entry.get("compatibility_type") or ""),
            str(entry.get("quantization") or ""),
        )
        for entry in published
    }
    added = [
        _as_entry(build) for build in builds
        if build.model_key not in listed
        and (build.family, build.runtime_format, build.quantization) not in described
    ]
    return published + added


def with_sizes(published: list[dict[str, Any]],
               builds: list[InstalledVariant] | None,
               sizes: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """The catalogue, with each entry's size on disk when the CLI names its build.

    **The HTTP catalogue carries no size and the CLI does**, per build — so until
    12 September 2026 every installed model reached `/api/v1/models` with
    `installed_size_bytes: null`, and nothing could say whether one would fit in
    memory before loading it. An entry is matched to its build exactly as
    `add_unpublished` decides a build is already described: by its qualified key,
    or by family, format and quantization together. Failing that, an entry takes the
    size the CLI's plain listing gives its exact key (`installed_sizes`), which names
    the models the build listing leaves out. An entry neither matches is left without
    a size rather than borrowing a sibling's, because a GGUF and an MLX of the same
    weights are different sizes.
    """
    if not builds and not sizes:
        return published
    by_key = {build.model_key: build for build in builds or []}
    by_shape = {
        (build.family, build.runtime_format, build.quantization): build for build in builds or []
    }
    sized = []
    for entry in published:
        key = str(entry.get("id") or "")
        shape = (_family_of(key), str(entry.get("compatibility_type") or ""),
                 str(entry.get("quantization") or ""))
        build = by_key.get(key) or by_shape.get(shape)
        size = build.size_bytes if build else None
        if size is None and sizes:
            size = sizes.get(key)
        sized.append({**entry, "size_bytes": size} if size is not None else entry)
    return sized


def _as_lms_argument(value: Any, field: str) -> str:
    """One caller-supplied value, as the argv token it will become — or a
    refusal when `lms` would read it as one of its own options.

    `model_key`, `context_length` and `gpu_offload` all originate in a
    caller's JSON body (`POST /api/v1/runtime/sessions`,
    `POST /api/v1/runtime-sets`) and are placed into `lms`'s argv unquoted.
    Every CLI parser, `lms` included, decides "option or value" by looking at
    the leading character rather than at the position SIRVIS meant the token
    to fill, so a value shaped like `--verbose` or `-y` would be read as a
    flag rather than as data (CWE-88, a Claude Security scan). Refusing it
    here, before it is ever placed in `arguments`, is what keeps a caller from
    reaching `lms` options that were never meant to be reachable from the API.
    """
    text = str(value)
    if text.startswith("-"):
        raise InvalidConfigurationError(
            f"{field} may not begin with '-': {text!r} would be read by the "
            "lms CLI as an option rather than as the value it names"
        )
    return text


# Loopback only. A hostname that merely resolves to this machine is not the
# same claim — the operator wrote an address to somewhere else, and the CLI has
# no way to know whether the LM Studio answering there is this one.
_LOCAL_HOSTS = {"127.0.0.1", "::1", "[::1]", "localhost", "0.0.0.0"}


def _is_local(base_url: str) -> bool:
    """Whether this adapter is talking to LM Studio on the machine it runs on."""
    return urlsplit(base_url).hostname in _LOCAL_HOSTS or (
        urlsplit(base_url).netloc.split(":")[0] in _LOCAL_HOSTS
    )


def _family_of(runtime_key: str) -> str:
    return runtime_key.split("@", 1)[0]


def _as_entry(build: InstalledVariant) -> dict[str, Any]:
    """One CLI build in the shape the catalogue publishes.

    The same field names, because the domain reads records without caring which
    reader produced them — and a second vocabulary here would be a second
    mapping to keep in step with §6.
    """
    return {
        "id": build.model_key,
        "object": "model",
        "type": build.model_type,
        "publisher": build.publisher,
        "arch": build.architecture,
        "compatibility_type": build.runtime_format,
        "quantization": build.quantization,
        "state": "not-loaded",
        "max_context_length": build.max_context,
        "size_bytes": build.size_bytes,
    }


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
        published = [entry for entry in entries if isinstance(entry, dict)]
        # Both CLI listings on worker threads, and at the same time. Each is a
        # blocking `lms ls` with a five-second timeout, and this listing sits
        # under residency, every acquire and every load's confirmation — so on
        # the event loop the pair stalled all of SIRVIS for as long as the CLI
        # took to start and answer, twice.
        builds, sizes = await asyncio.gather(
            asyncio.to_thread(self._local_builds), asyncio.to_thread(self._local_sizes)
        )
        return [
            {**entry, "runtime_key": RUNTIME_KEY}
            for entry in add_unpublished(with_sizes(published, builds, sizes), builds)
        ]

    def confirm_variant(self, model_key: str) -> LoadedVariant | None:
        """Which build is actually loaded, or nothing when it cannot be told.

        **This runtime describes the wrong build.** It groups several builds of
        one model under a single `/api/v0/models` entry and reports whichever
        variant the app has selected, not the one that is resident — so a
        machine holding an MLX and a GGUF of the same weights is described as
        `mlx / 4bit` while `gguf / Q4_K_M` is the build answering. It compounds
        it by accepting `…@q4_k_m` on the completions route and returning
        "not found" for that same key on the metadata route.

        Its own CLI does know, so that is what is asked. Nothing is guessed:
        two loaded builds of one family behind an unqualified key returns
        `None`, and the caller refuses to record evidence rather than choose.
        """
        binary = self._resolve_lms()
        return confirm(model_key, loaded_variants(binary)) if binary else None

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

    async def stream_generate(
        self, model_key: str, messages: list[dict[str, Any]], **options: Any
    ) -> AsyncIterator[GenerationChunk]:
        """One completion, streamed, so the first token's arrival can be timed.

        This is the M6 half of generation. `generate` above is a round trip and
        stays one: it is the probe that proves the path works. Time-to-first-
        token is a headline metric (§11.4) and is simply not recoverable from a
        response that arrives whole, so the benchmark engine reads this instead.

        **The 200-with-an-error-body trap applies here too, and looks different.**
        An unsupported request does not arrive as SSE at all — it arrives as one
        JSON object with an `error` key and a 200 beside it. So the first line
        is inspected before the stream is believed, and anything that is not a
        `data:` frame is collected and re-raised rather than silently yielding
        an empty completion, which would read as a model that said nothing.

        `stream_options.include_usage` asks the runtime for token counts on the
        final frame. A runtime that ignores it produces chunks with no `usage`,
        and the caller is expected to notice rather than to assume.
        """
        body: dict[str, Any] = {
            "model": model_key,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            **options,
        }
        client = self._client
        owned = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=LOAD_TIMEOUT_SECONDS)
        try:
            async with client.stream(
                "POST", f"{self.base_url}/v1/chat/completions",
                json=body, timeout=LOAD_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                async for chunk in _read_sse(response):
                    yield chunk
        except httpx.TimeoutException as failure:
            # The streaming twin of the branch above. A generation that stalls
            # mid-stream is the same fact as one that stalls before the first
            # byte: the runtime is working on something, or stuck on it.
            raise RuntimeTimeoutError(
                f"POST /v1/chat/completions (stream): {failure}"
            ) from failure
        except (httpx.HTTPError, ValueError) as failure:
            raise RuntimeUnavailableError(
                f"POST /v1/chat/completions (stream): {failure}"
            ) from failure
        finally:
            if owned:
                await client.aclose()

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
        arguments = ["load", _as_lms_argument(model_key, "model_key"), "--yes"]
        if "context_length" in requested:
            arguments += [
                "--context-length",
                _as_lms_argument(requested["context_length"], "context_length"),
            ]
        if "gpu_offload" in requested:
            arguments += ["--gpu", _as_lms_argument(requested["gpu_offload"], "gpu_offload")]
        honoured = {"context_length", "gpu_offload"}

        # A non-zero exit here is the runtime answering and failing to load
        # this build — which is not the same fact as the runtime being absent,
        # and not the same thing to do about it.
        #
        # **On a worker thread**, for `unload`'s reason and with more at stake:
        # a load can take minutes, and on the event loop SIRVIS answered nothing
        # for all of them — health checks timed out, and the dashboard and menu
        # bar showed the service down until the model arrived. The arguments
        # were checked above, on the loop, so a refused value never reaches it.
        await asyncio.to_thread(
            self._run_lms, arguments, timeout=LOAD_TIMEOUT_SECONDS, refused=RuntimeLoadFailedError
        )

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

        **On a worker thread**, since 12 September 2026. `lms unload` is a
        blocking subprocess, and run on the event loop it froze the whole
        service for as long as the CLI took — up to the full timeout against a
        runtime that had stopped answering. That made any bound on an unload a
        comment rather than a fact: SIRVIS's stop gives its unloads a budget
        (`STOP_BUDGET_SECONDS` in `app.py`), and a timer cannot fire on a loop
        that is not running. The thread changes who waits, not what runs — the
        same `_run_lms`, the same timeout — so a caller that stops waiting does
        not cut an unload off halfway. The key is still checked here, on the
        loop, so a refused one never reaches the thread at all.
        """
        argument = _as_lms_argument(model_key, "model_key")
        await asyncio.to_thread(self._run_lms, ["unload", argument], timeout=PROBE_TIMEOUT_SECONDS)

    async def unload_all(self) -> None:
        """Unload everything. Only ever an operator's explicit choice.

        On a worker thread, like every `lms` call here, so it cannot stall the
        service while the CLI runs.
        """
        await asyncio.to_thread(self._run_lms, ["unload", "--all"], timeout=PROBE_TIMEOUT_SECONDS)

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
        except httpx.TimeoutException as failure:
            # **A runtime that accepted the connection and then went quiet is
            # busy, not absent**, and the CLI path has drawn that distinction
            # since it was written: "the obvious response to unreachable is to
            # retry immediately, which is the worst possible response to a load
            # already underway." The HTTP path collapsed both into
            # `RuntimeUnavailableError` until 8 September 2026, so a stalled
            # runtime was reported as one nobody had started — and §4.3 exists
            # so a caller can branch on the difference.
            raise RuntimeTimeoutError(f"{method} {path}: {failure}") from failure
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

    def installed_paths(self) -> frozenset[str] | None:
        """Where every model on this machine came from, or `None` when nobody can be asked.

        Public for M11's discovery, which marks a search result as already installed.
        Local-only, for the reason `_local_builds` gives: the CLI describes this disk.
        """
        if not _is_local(self.base_url):
            return None
        binary = self._resolve_lms()
        return cli_installed_paths(binary) if binary else None

    def _local_builds(self) -> list[InstalledVariant] | None:
        """Installed builds from the CLI — but only for a runtime on this machine.

        The CLI describes *this* laptop's disk. An adapter pointed at LM Studio
        on another host would otherwise be handed this machine's builds and file
        them as the remote one's inventory: builds attributed to a machine that
        does not hold them, which is §12.2's error in a different coordinate.

        `None` for a remote runtime is the same `None` the CLI's absence gives,
        and means the same thing — nobody could be asked — so the caller adds
        nothing and publishes the catalogue exactly as it arrived.
        """
        if not _is_local(self.base_url):
            return None
        binary = self._resolve_lms()
        return installed_variants(binary) if binary else None

    def _local_sizes(self) -> dict[str, int] | None:
        """Installed sizes from the CLI's plain listing, for a runtime on this machine only,
        for the reason `_local_builds` gives."""
        if not _is_local(self.base_url):
            return None
        binary = self._resolve_lms()
        return installed_sizes(binary) if binary else None

    def _run_lms(
        self,
        arguments: list[str],
        timeout: float,
        refused: type[RuntimeUnavailableError] = RuntimeUnavailableError,
    ) -> str:
        """Run one `lms` command, distinguishing the three ways it can fail.

        **All three used to raise `RuntimeUnavailableError`**, so the error
        model published three codes and used one. §4.3 exists so a caller can
        branch on the code, and a caller told the runtime was unreachable when
        it was answering perfectly well and simply refused the operation looks
        at the wrong process. Found by sweeping for definitions nothing
        references: `LoadFailedError` and `DeadlineExceededError` were both
        written, both documented, and never raised.

        - **No binary** — genuinely unavailable. LM Studio exposes no HTTP
          load or unload, so without the CLI there is no lifecycle at all.
        - **Timed out** — a *busy* runtime, not an absent one. The obvious
          response to "unreachable" is to retry immediately, which is the worst
          possible response to a load already underway.
        - **Non-zero exit** — the CLI ran and the command failed. `refused`
          lets the caller say what that means, because a failed `load` is a
          load failure while a failed `ps` really is the lifecycle being
          unusable.
        """
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
        except subprocess.TimeoutExpired as failure:
            raise RuntimeTimeoutError(
                f"lms {' '.join(arguments)} was still running after {timeout:.0f}s"
            ) from failure
        except subprocess.CalledProcessError as failure:
            detail = (failure.stderr or failure.stdout or "").strip()
            raise refused(f"lms {' '.join(arguments)}: {detail}") from failure
        except (subprocess.SubprocessError, OSError) as failure:
            raise RuntimeUnavailableError(f"lms {' '.join(arguments)}: {failure}") from failure
        return finished.stdout.strip()


async def _read_sse(response: httpx.Response) -> AsyncIterator[GenerationChunk]:
    """Turn an SSE body into chunks, refusing anything that is not one.

    Separate from the adapter so the parsing can be exercised against recorded
    frames without a client, and because the error path is the interesting half:
    LM Studio answers 200 with a plain JSON error object for a request it will
    not serve, and a parser that skipped every line failing to start with
    `data:` would turn that into a successful empty completion.
    """
    saw_frame = False
    stray: list[str] = []
    async for line in response.aiter_lines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("data:"):
            stray.append(stripped)
            continue
        saw_frame = True
        payload = stripped[len("data:"):].strip()
        if payload == "[DONE]":
            break
        yield _chunk_of(_decode_frame(payload))
    if not saw_frame:
        # Nothing that could be believed arrived. Whatever the body was, it is
        # reported verbatim rather than summarised: a 200 carrying an error
        # object is the case this exists for, and paraphrasing it loses the
        # only sentence that says what went wrong.
        raise RuntimeUnavailableError(
            f"stream carried no data frames: {' '.join(stray)[:200] or 'empty body'}"
        )


def _decode_frame(payload: str) -> dict[str, Any]:
    """One frame's JSON, with LM Studio's 200-shaped error caught."""
    try:
        frame = json.loads(payload)
    except ValueError as failure:
        raise RuntimeUnavailableError(f"malformed stream frame: {failure}") from failure
    if not isinstance(frame, dict):
        raise RuntimeUnavailableError("stream frame was not an object")
    if "error" in frame:
        raise RuntimeUnavailableError(f"stream: {frame['error']}")
    return frame


def _chunk_of(frame: dict[str, Any]) -> GenerationChunk:
    """One SSE frame in the shape the engine measures against."""
    choices = frame.get("choices") or []
    delta: dict[str, Any] = {}
    finish: str | None = None
    if choices and isinstance(choices[0], dict):
        delta = choices[0].get("delta") or {}
        finish = choices[0].get("finish_reason")
    usage = frame.get("usage")
    pieces = delta.get("tool_calls") or []
    return GenerationChunk(
        content=str(delta.get("content") or ""),
        finish_reason=finish,
        usage=usage if isinstance(usage, dict) else None,
        tool_calls=[piece for piece in pieces if isinstance(piece, dict)],
    )


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
