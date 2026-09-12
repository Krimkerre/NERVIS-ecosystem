"""§8's Discover: what this machine could download, searched on Hugging Face (M11).

**Hugging Face rather than LM Studio, and that is a finding rather than a
preference.** §8 names `LMStudioCatalog` first. LM Studio's REST API downloads by
catalogue name or Hugging Face link but publishes no search: on 12 September 2026
every likely route answered "Unexpected endpoint". Hugging Face's public API
searches, narrows to GGUF or MLX, and lists every file with its size, which the
disk check needs before a byte moves, and LM Studio downloads exactly those links.
So discovery reads Hugging Face, and downloading asks LM Studio (`downloads.py`).

**Anonymous.** A public model needs no token, and Hugging Face's rate-limit page
allows an anonymous address 500 API calls every five minutes; a search is one call.
A gated model is reported as gated rather than offered, because LM Studio would
need a Hugging Face login that SIRVIS does not hold.

**Fenced, like any downloaded metadata (§4.5).** Repository names, tags and file
names are shown, never followed, and a repository id is checked against Hugging
Face's own shape before it is placed into a URL.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from sirvis.errors import CatalogUnavailableError, InvalidConfigurationError, ModelNotFoundError

FORMATS = ("gguf", "mlx")
MAX_RESULTS = 50

# `owner/name`, as Hugging Face names a repository. Anything else is refused before
# it reaches a URL, so a further path segment or a query string cannot ride in on one.
_REPO_ID = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
# A GGUF file's quantization, as its name carries it: `…-Q4_K_M.gguf`,
# `…-IQ3_XXS.gguf`, `…-F16.gguf`, and the parts of a split file,
# `…-Q8_0-00001-of-00002.gguf`.
_GGUF_QUANT = re.compile(
    r"(?i)(?:^|[-_.])(I?Q\d(?:_[A-Z0-9]+)*|F16|F32|BF16)(?:-\d{5}-of-\d{5})?\.gguf$"
)
# An MLX repository's precision, as its name carries it: `…-4bit`, `…-8bit`, `…-bf16`.
_MLX_QUANT = re.compile(r"(?i)(\d+bit|bf16|fp16)$")


def checked_repo_id(repo_id: str) -> str:
    """The repository id, or a refusal that says what one looks like."""
    if not _REPO_ID.match(repo_id or ""):
        raise InvalidConfigurationError(
            "a model is named `owner/name`, the way Hugging Face names a repository",
            repo_id=repo_id,
        )
    return repo_id


async def search(
    client: httpx.AsyncClient,
    query: str,
    format_: str,
    limit: int,
    installed: frozenset[str],
) -> list[dict[str, Any]]:
    """Repositories matching `query`, most downloaded first, in one format or both.

    Hugging Face filters on one tag per request, so "both" is two searches merged.
    """
    formats = FORMATS if format_ == "any" else (format_,)
    found: list[dict[str, Any]] = []
    for each in formats:
        answered = await _get(client, "/api/models", {
            "search": query, "filter": each, "sort": "downloads",
            "direction": "-1", "limit": str(limit),
        })
        for item in answered if isinstance(answered, list) else []:
            if isinstance(item, dict) and _REPO_ID.match(str(item.get("id") or "")):
                found.append(_summary(item, each, installed))
    found.sort(key=lambda entry: -(entry["downloads"] or 0))
    return found[:limit]


def _summary(item: dict[str, Any], format_: str, installed: frozenset[str]) -> dict[str, Any]:
    repo_id = str(item["id"])
    author, name = repo_id.split("/", 1)
    return {
        "repo_id": repo_id,
        "author": author,
        "name": name,
        "format": format_,
        "downloads": _int(item.get("downloads")),
        "likes": _int(item.get("likes")),
        "pipeline_tag": str(item.get("pipeline_tag") or ""),
        "created_at": str(item.get("createdAt") or ""),
        "installed": any(
            path == repo_id or path.startswith(repo_id + "/") for path in installed
        ),
    }


async def model(
    client: httpx.AsyncClient, repo_id: str, installed: frozenset[str]
) -> dict[str, Any]:
    """One repository: what it is, and each downloadable variant with its size.

    A GGUF repository offers one variant per quantization, the parts of a split file
    counted together; an MLX repository is one model as a whole folder.
    """
    data = await _get(client, f"/api/models/{checked_repo_id(repo_id)}", {"blobs": "true"})
    if not isinstance(data, dict):
        raise CatalogUnavailableError("Hugging Face described the model in an unexpected shape")
    tags = {str(tag) for tag in data.get("tags") or []}
    format_ = "gguf" if "gguf" in tags else "mlx" if "mlx" in tags else ""
    siblings = [one for one in data.get("siblings") or [] if isinstance(one, dict)]
    raw_gguf = data.get("gguf")
    gguf: dict[str, Any] = raw_gguf if isinstance(raw_gguf, dict) else {}
    variants = (
        _gguf_variants(repo_id, siblings, installed) if format_ == "gguf"
        else _mlx_variants(repo_id, siblings, installed) if format_ == "mlx"
        else []
    )
    return {
        "repo_id": repo_id,
        "format": format_,
        # `false`, or `"auto"` / `"manual"` for a model behind a licence click.
        "gated": bool(data.get("gated")),
        "architecture": str(gguf.get("architecture") or ""),
        "context_length": _int(gguf.get("context_length")),
        "parameters": _int(gguf.get("total")),
        "downloads": _int(data.get("downloads")),
        "likes": _int(data.get("likes")),
        "updated_at": str(data.get("lastModified") or ""),
        "variants": variants,
    }


def _gguf_variants(
    repo_id: str, siblings: list[dict[str, Any]], installed: frozenset[str]
) -> list[dict[str, Any]]:
    """One variant per quantization, smallest first.

    A vision projector (`mmproj-…`) is left out: it is a companion file, not a model
    anybody would choose to download on its own.
    """
    groups: dict[str, dict[str, Any]] = {}
    for sibling in siblings:
        name = str(sibling.get("rfilename") or "")
        matched = _GGUF_QUANT.search(name)
        if matched is None or name.rsplit("/", 1)[-1].lower().startswith("mmproj"):
            continue
        quantization = matched.group(1).upper()
        group = groups.setdefault(quantization, {
            "quantization": quantization, "files": [], "size_bytes": 0, "installed": False,
        })
        group["files"].append(name)
        size = _int(sibling.get("size"))
        # One file of unknown size makes the variant's size unknown, not smaller.
        group["size_bytes"] = (
            None if size is None or group["size_bytes"] is None else group["size_bytes"] + size
        )
        group["installed"] = group["installed"] or f"{repo_id}/{name}" in installed
    return sorted(groups.values(), key=lambda group: group["size_bytes"] or 0)


def _mlx_variants(
    repo_id: str, siblings: list[dict[str, Any]], installed: frozenset[str]
) -> list[dict[str, Any]]:
    """The whole folder as one variant, its precision read from the repository name."""
    sizes = [_int(sibling.get("size")) for sibling in siblings]
    total = None if not sizes or any(size is None for size in sizes) else sum(
        size for size in sizes if size is not None
    )
    matched = _MLX_QUANT.search(repo_id)
    return [{
        "quantization": matched.group(1).lower() if matched else "",
        "files": [str(sibling.get("rfilename") or "") for sibling in siblings],
        "size_bytes": total,
        "installed": repo_id in installed,
    }]


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> Any:
    """One read of Hugging Face, with its failures named rather than raised raw."""
    try:
        answered = await client.get(path, params=params)
    except httpx.HTTPError as failure:
        raise CatalogUnavailableError(
            f"Hugging Face did not answer: {type(failure).__name__}"
        ) from failure
    # A model Hugging Face does not have, or will not show anonymously, answers 401
    # rather than 404: measured on 12 September 2026, a misspelled repository returned
    # `401 Invalid username or password.` with no marker saying "not found". On a model
    # read that means "no such public model", which a person can act on; reported as
    # the catalogue being unavailable, it sent them looking for an outage.
    missing = answered.status_code == 404 or (
        answered.status_code == 401 and path.startswith("/api/models/")
    )
    if missing:
        raise ModelNotFoundError(
            "Hugging Face has no public model by that name; it may be misspelled or private",
            path=path,
        )
    if answered.status_code >= 400:
        raise CatalogUnavailableError(
            f"Hugging Face answered HTTP {answered.status_code}", status=answered.status_code
        )
    try:
        return answered.json()
    except ValueError as failure:
        raise CatalogUnavailableError("Hugging Face's answer was not JSON") from failure


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
