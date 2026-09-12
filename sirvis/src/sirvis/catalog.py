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

# Every order a search can take, and what Hugging Face calls it. `None` is an order
# Hugging Face will not sort by, so SIRVIS orders a wider page itself: measured on
# 12 September 2026, `sort=downloadsAllTime` answers HTTP 400, and size is not a
# field Hugging Face sorts on at all. `downloads` is Hugging Face's own count, which
# covers the last thirty days.
SORTS: dict[str, str | None] = {
    "downloads": "downloads",
    "downloads_all_time": None,
    "likes": "likes",
    "trending": "trendingScore",
    "updated": "lastModified",
    "created": "createdAt",
    "smallest": None,
}
_SORT_FIELDS = {
    "downloads": "downloads", "downloads_all_time": "downloads_all_time", "likes": "likes",
    "trending": "trending", "updated": "updated_at", "created": "created_at",
}
# The wider page an order SIRVIS sorts itself reads first. So "most downloaded, all
# time" is the two hundred most downloaded matches of the last thirty days, ranked by
# their all-time count: a model nobody downloads any more is not among them.
LOCAL_PAGE = 200
# The fields a search row needs. Asking for any replaces Hugging Face's default set,
# so every one is named — the parameter counts included, which is what lets a search
# say what fits without reading every model's file list.
EXPANDED = (
    "downloads", "downloadsAllTime", "likes", "trendingScore", "lastModified",
    "createdAt", "pipeline_tag", "tags", "gated", "gguf", "safetensors",
)
# "Runs on this machine": the model's usual build fits in this share of its memory,
# leaving the rest for the context, the runtime and everything else that is open.
FIT_SHARE = 0.75
# A GGUF model's usual build is Q4_K_M, about 4.85 bits a weight.
GGUF_BYTES_PER_PARAMETER = 0.61
# An MLX build weighs its bits per weight, plus the scales and the parts it leaves
# unquantized.
MLX_OVERHEAD = 1.1

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
# A size a repository states in its name: `27B`, `0.6B`, `135M`. Not preceded by a letter or
# digit, so a mixture of experts' active count (`A3B`) is not read as its size, and not
# followed by one, so `4bit` is not four billion.
_NAMED_SIZE = re.compile(r"(?i)(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)([BM])(?![A-Za-z0-9])")
# How far Hugging Face's parameter count may stray from the size in the name before the
# name is believed instead.
NAME_DISAGREEMENT = 10


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
    *,
    sort: str = "downloads",
    memory_bytes: int | None = None,
    fits_only: bool = False,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Repositories matching `query`, in the chosen order, in one format or both.

    Hugging Face filters on one tag per request, so "both" is two searches merged and
    ordered again here. An order Hugging Face cannot sort by, and the filter for what
    fits this machine, read a wider page first, so a filtered list still has `limit`
    rows when the page holds that many.
    """
    formats = FORMATS if format_ == "any" else (format_,)
    remote = SORTS[sort]
    page = LOCAL_PAGE if remote is None or fits_only or max_bytes is not None else limit
    budget = int(memory_bytes * FIT_SHARE) if memory_bytes else None
    found: list[dict[str, Any]] = []
    for each in formats:
        answered = await _get(client, "/api/models", (
            ("search", query), ("filter", each), ("sort", remote or "downloads"),
            ("direction", "-1"), ("limit", str(page)),
            *(("expand[]", field) for field in EXPANDED),
        ))
        for item in answered if isinstance(answered, list) else []:
            if isinstance(item, dict) and _REPO_ID.match(str(item.get("id") or "")):
                found.append(_summary(item, each, installed, budget))
    # The size a row may be: this machine's budget when asked for, a stated maximum, or the
    # smaller of the two. A row with no estimate cannot be shown to be under either.
    caps = [cap for cap in (budget if fits_only else None, max_bytes) if cap is not None]
    size_limit = min(caps) if caps else None

    def under(entry: dict[str, Any]) -> bool | None:
        size = entry["estimated_bytes"]
        return None if size is None or size_limit is None else size <= size_limit

    too_large = unknown = 0
    if size_limit is not None:
        too_large = sum(1 for entry in found if under(entry) is False)
        unknown = sum(1 for entry in found if under(entry) is None)
        found = [entry for entry in found if under(entry)]
    return {
        "items": _ordered(found, sort)[:limit],
        "fit_budget_bytes": budget,
        "fits_filter": (
            "applied" if fits_only and budget is not None else "unavailable" if fits_only else "off"
        ),
        "max_bytes": max_bytes,
        "size_limit_bytes": size_limit,
        "hidden_too_large": too_large,
        "hidden_unknown_size": unknown,
    }


def _ordered(found: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Most first for every order but size; a row without the figure goes last."""
    if sort == "smallest":
        return sorted(found, key=lambda entry: (
            entry["estimated_bytes"] is None, entry["estimated_bytes"] or 0,
        ))
    key = _SORT_FIELDS[sort]
    present = [entry for entry in found if entry[key] not in (None, "")]
    absent = [entry for entry in found if entry[key] in (None, "")]
    return sorted(present, key=lambda entry: entry[key], reverse=True) + absent


def _summary(
    item: dict[str, Any], format_: str, installed: frozenset[str], budget: int | None
) -> dict[str, Any]:
    repo_id = str(item["id"])
    author, name = repo_id.split("/", 1)
    parameters, source, estimated = _estimate(item, format_, repo_id)
    return {
        "repo_id": repo_id,
        "author": author,
        "name": name,
        "format": format_,
        "downloads": _int(item.get("downloads")),
        "downloads_all_time": _int(item.get("downloadsAllTime")),
        "likes": _int(item.get("likes")),
        "trending": _int(item.get("trendingScore")),
        "pipeline_tag": str(item.get("pipeline_tag") or ""),
        "created_at": str(item.get("createdAt") or ""),
        "updated_at": str(item.get("lastModified") or ""),
        "gated": bool(item.get("gated")),
        "parameters": parameters,
        "parameters_source": source,
        "estimated_bytes": estimated,
        # `None` when either side is unknown: no size to judge, or no memory to judge by.
        "fits": None if estimated is None or budget is None else estimated <= budget,
        "installed": any(
            path == repo_id or path.startswith(repo_id + "/") for path in installed
        ),
    }


def _estimate(
    item: dict[str, Any], format_: str, repo_id: str
) -> tuple[int | None, str | None, int | None]:
    """A model's parameter count, where it came from, and roughly what its usual build weighs.

    From the count rather than the file list: a search returns the count for every
    row in one call, while real sizes cost a call per model. A GGUF repository holds
    many quantizations, so it is weighed at the usual one; an MLX repository is one
    precision, read from its name. Without a count, or with an MLX name that states
    no precision, there is nothing to estimate and the answer is unknown.
    """
    raw = item.get("gguf" if format_ == "gguf" else "safetensors")
    parameters, source = _parameters(_int(raw.get("total")) if isinstance(raw, dict) else None,
                                     repo_id)
    if parameters is None:
        return None, None, None
    if format_ == "gguf":
        return parameters, source, int(parameters * GGUF_BYTES_PER_PARAMETER)
    matched = _MLX_QUANT.search(repo_id)
    precision = matched.group(1).lower() if matched else ""
    bits: int | None = None
    if precision in ("bf16", "fp16"):
        bits = 16
    elif precision.endswith("bit"):
        bits = int(precision[:-3])
    if bits is None:
        return parameters, source, None
    return parameters, source, int(parameters * bits / 8 * MLX_OVERHEAD)


def _parameters(counted: int | None, repo_id: str) -> tuple[int | None, str | None]:
    """Hugging Face's parameter count, checked against the size the repository names.

    The count is read from a repository's metadata, and for a GGUF split into shards it
    describes only the first shard: measured on 12 September 2026, a 27B model's shards
    counted 2.67 million parameters and ranked as a 1.6 MB download that fits anything.
    So when the count is missing, or strays more than `NAME_DISAGREEMENT` times from the
    size in the name, the name is believed. With neither, the size is unknown.
    """
    sizes = [
        float(number) * (1e9 if unit.upper() == "B" else 1e6)
        for number, unit in _NAMED_SIZE.findall(repo_id.split("/", 1)[-1])
    ]
    named = int(max(sizes)) if sizes else None
    if counted is None:
        return (named, "name") if named else (None, None)
    if named and not named / NAME_DISAGREEMENT <= counted <= named * NAME_DISAGREEMENT:
        return named, "name"
    return counted, "count"


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
    raw_safetensors = data.get("safetensors")
    safetensors: dict[str, Any] = raw_safetensors if isinstance(raw_safetensors, dict) else {}
    raw_card = data.get("cardData")
    card: dict[str, Any] = raw_card if isinstance(raw_card, dict) else {}
    raw_config = data.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    architectures = config.get("architectures")
    named_architecture = (
        str(architectures[0]) if isinstance(architectures, list) and architectures else ""
    )
    parameters, source = _parameters(
        _int(gguf.get("total")) if gguf else _int(safetensors.get("total")), repo_id
    )
    variants = (
        _gguf_variants(repo_id, siblings, installed) if format_ == "gguf"
        else _mlx_variants(repo_id, siblings, installed) if format_ == "mlx"
        else []
    )
    return {
        "repo_id": repo_id,
        "author": str(data.get("author") or repo_id.split("/", 1)[0]),
        # Built from the checked repository id, never taken from the metadata (§4.5).
        "url": f"https://huggingface.co/{repo_id}",
        "format": format_,
        # `false`, or `"auto"` / `"manual"` for a model behind a licence click.
        "gated": bool(data.get("gated")),
        "architecture": str(gguf.get("architecture") or named_architecture),
        "model_type": str(config.get("model_type") or ""),
        "context_length": _int(gguf.get("context_length")),
        "parameters": parameters,
        "parameters_source": source,
        "license": _card_or_tag(card.get("license"), tags, "license:"),
        "base_model": _card_or_tag(card.get("base_model"), tags, "base_model:"),
        "pipeline_tag": str(data.get("pipeline_tag") or card.get("pipeline_tag") or ""),
        "library": str(data.get("library_name") or card.get("library_name") or ""),
        "downloads": _int(data.get("downloads")),
        "likes": _int(data.get("likes")),
        "created_at": str(data.get("createdAt") or ""),
        "updated_at": str(data.get("lastModified") or ""),
        "variants": variants,
    }


def _card_or_tag(value: Any, tags: set[str], prefix: str) -> str:
    """A model card field — a string or a list of them — or, failing that, its tag.

    A tag like `base_model:quantized:Qwen/Qwen3-8B` says how the base was changed as well
    as which model it was, so the relation is dropped and the model kept.
    """
    if isinstance(value, list):
        value = next((str(each) for each in value if each), "")
    if isinstance(value, str) and value:
        return value
    for tag in sorted(tags):
        if tag.startswith(prefix):
            named = tag[len(prefix):]
            return named.split(":", 1)[1] if ":" in named else named
    return ""


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


async def _get(
    client: httpx.AsyncClient, path: str,
    params: dict[str, str] | tuple[tuple[str, str], ...],
) -> Any:
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
