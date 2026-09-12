"""§8's downloads: persistent jobs that LM Studio carries out and SIRVIS watches (M11).

**LM Studio does the transfer.** Its REST API takes a Hugging Face link and a
quantization, starts a job of its own, and reports that job's bytes, speed and
estimated finish. SIRVIS keeps a row for each download — what was asked, what the
disk check said, how far LM Studio had got when last asked — so a browser reload
loses nothing, and a SIRVIS restart picks the watching back up while LM Studio
carries on with the transfer.

**No cancel and no pause, because LM Studio publishes neither.** §8's vocabulary
keeps `paused` and `cancelled`: `paused` appears when LM Studio reports it, and
nothing sets `cancelled`. A cancel that only stopped SIRVIS watching would leave the
transfer running behind a screen saying it had stopped.

**Never deletes a model (§8).** Nothing here removes a file, a partial download
included; a failed download leaves whatever LM Studio left.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import httpx

from sirvis.storage import Database

LOG = logging.getLogger(__name__)

GIB = 1024**3


class DownloadStatus(str, Enum):
    """§8's statuses, verbatim."""

    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ALREADY_PRESENT = "already_present"


# Asked of LM Studio on every pass: still moving, as far as anybody knows.
WATCHED = (DownloadStatus.DOWNLOADING.value, DownloadStatus.PAUSED.value)
DONE = (DownloadStatus.COMPLETED.value, DownloadStatus.ALREADY_PRESENT.value)

# LM Studio's words for a job, in §8's. `already_downloaded` is §8's `already_present`.
_FROM_LMSTUDIO = {
    "downloading": DownloadStatus.DOWNLOADING,
    "paused": DownloadStatus.PAUSED,
    "completed": DownloadStatus.COMPLETED,
    "failed": DownloadStatus.FAILED,
    "already_downloaded": DownloadStatus.ALREADY_PRESENT,
}

# The events a download publishes, by the status it has just reached. No progress
# event: a transfer reports every two seconds, and the timeline is not a progress bar.
_EVENTS = {
    DownloadStatus.DOWNLOADING.value: "sirvis.download.started",
    DownloadStatus.COMPLETED.value: "sirvis.download.completed",
    DownloadStatus.ALREADY_PRESENT.value: "sirvis.download.completed",
    DownloadStatus.FAILED.value: "sirvis.download.failed",
}

# Columns a pass may change. Named, so an update cannot be pointed at anything else.
_UPDATABLE = frozenset({
    "status", "downloaded_bytes", "total_bytes", "bytes_per_second",
    "estimated_completion", "lmstudio_job_id", "detail", "started_at", "completed_at",
})


# ── The disk check ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DiskCheck:
    """§8's check: does the download fit, and what should somebody know first."""

    free_bytes: int
    needed_bytes: int | None
    fits: bool
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        remaining = None if self.needed_bytes is None else self.free_bytes - self.needed_bytes
        return {
            "free_bytes": self.free_bytes,
            "needed_bytes": self.needed_bytes,
            "remaining_bytes": remaining,
            "fits": self.fits,
            "warnings": list(self.warnings),
        }


def check_disk(
    free_bytes: int, needed_bytes: int | None, *, low_disk_bytes: int, large_share: float
) -> DiskCheck:
    """Fits or not, and the two warnings §8 names. Refusing is the caller's decision."""
    if needed_bytes is None:
        return DiskCheck(free_bytes, None, True, (
            "Hugging Face publishes no size for this download, so it could not be "
            "checked against the free space",
        ))
    remaining = free_bytes - needed_bytes
    if remaining < 0:
        return DiskCheck(free_bytes, needed_bytes, False, ())
    warnings: list[str] = []
    if remaining < low_disk_bytes:
        warnings.append(
            f"it would leave {_gib(remaining)} free, under the {_gib(low_disk_bytes)} "
            "kept in reserve"
        )
    if free_bytes and needed_bytes > free_bytes * large_share:
        warnings.append(f"it would use {round(needed_bytes / free_bytes * 100)}% of the free space")
    return DiskCheck(free_bytes, needed_bytes, True, tuple(warnings))


def free_bytes(path: str) -> int:
    """Free space on the volume a download would land on.

    The nearest folder that exists is measured, so a models folder LM Studio has not
    created yet still reports its volume rather than failing.
    """
    place = Path(path).expanduser()
    while not place.exists() and place != place.parent:
        place = place.parent
    return shutil.disk_usage(place).free


def _gib(amount: int) -> str:
    return f"{amount / GIB:.1f} GB"


# ── The job store ───────────────────────────────────────────────────────────


def record(
    database: Database,
    *,
    repo_id: str,
    file: str,
    quantization: str,
    format_: str,
    source: str,
    expected_bytes: int | None,
    warnings: tuple[str, ...],
    trace_id: str,
    status: DownloadStatus = DownloadStatus.QUEUED,
    detail: str = "",
) -> str:
    """Keep one download and return its id. The next pass asks LM Studio to start it."""
    download_id = f"dl_{uuid.uuid4().hex[:12]}"
    finished = _now() if status.value in DONE else None
    with database.connection as connection:
        connection.execute(
            "INSERT INTO download_job (download_id, repo_id, file, quantization, format,"
            " source, status, expected_bytes, warnings, trace_id, detail, completed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (download_id, repo_id, file, quantization, format_, source, status.value,
             expected_bytes, json.dumps(list(warnings)), trace_id or None, detail, finished),
        )
    return download_id


def read(database: Database, download_id: str) -> dict[str, Any] | None:
    row = database.connection.execute(
        "SELECT * FROM download_job WHERE download_id = ?", (download_id,)
    ).fetchone()
    return dict(row) if row else None


def recent(database: Database, limit: int = 50) -> list[dict[str, Any]]:
    rows = database.connection.execute(
        "SELECT * FROM download_job ORDER BY submitted_at DESC, rowid DESC LIMIT ?",
        (max(1, min(limit, 200)),),
    ).fetchall()
    return [dict(row) for row in rows]


def in_status(database: Database, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
    marks = ", ".join("?" for _ in statuses)
    rows = database.connection.execute(
        f"SELECT * FROM download_job WHERE status IN ({marks})"  # noqa: S608 - placeholders only
        " ORDER BY submitted_at, rowid",
        statuses,
    ).fetchall()
    return [dict(row) for row in rows]


def update(database: Database, download_id: str, fields: Mapping[str, Any]) -> None:
    columns = [column for column in fields if column in _UPDATABLE]
    if not columns:
        return
    assignments = ", ".join(f"{column} = ?" for column in columns)
    with database.connection as connection:
        connection.execute(
            f"UPDATE download_job SET {assignments} WHERE download_id = ?",  # noqa: S608 - named columns
            (*(fields[column] for column in columns), download_id),
        )


def as_public(job: Mapping[str, Any]) -> dict[str, Any]:
    """A download as a client reads it, with its progress worked out once, here."""
    total = job.get("total_bytes") or job.get("expected_bytes")
    done = job.get("downloaded_bytes")
    if job.get("status") in DONE:
        progress: float | None = 1.0
    elif isinstance(done, int) and isinstance(total, int) and total > 0:
        progress = round(min(done / total, 1.0), 4)
    else:
        progress = None
    return {
        "download_id": job["download_id"],
        "repo_id": job["repo_id"],
        "file": job.get("file") or "",
        "quantization": job.get("quantization") or "",
        "format": job.get("format") or "",
        "source": job.get("source") or "",
        "status": job["status"],
        "expected_bytes": job.get("expected_bytes"),
        "downloaded_bytes": done,
        "total_bytes": job.get("total_bytes"),
        "bytes_per_second": job.get("bytes_per_second"),
        "estimated_completion": job.get("estimated_completion"),
        "progress": progress,
        "detail": job.get("detail") or "",
        "warnings": json.loads(job.get("warnings") or "[]"),
        "submitted_at": job.get("submitted_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        # Stated rather than implied by a missing button: LM Studio publishes no cancel.
        "cancellable": False,
    }


# ── Asking LM Studio ────────────────────────────────────────────────────────


async def start_with_lmstudio(client: httpx.AsyncClient, job: Mapping[str, Any]) -> dict[str, Any]:
    """Ask LM Studio to begin one download; the fields the job should now carry."""
    body: dict[str, Any] = {"model": job["source"]}
    # LM Studio takes a quantization only for a Hugging Face link, and an MLX folder
    # has none to choose.
    if job.get("format") == "gguf" and job.get("quantization"):
        body["quantization"] = job["quantization"]
    answer = await _call(client, "POST", "/api/v1/models/download", body)
    if "error" in answer:
        return _failed(f"LM Studio did not start the download: {_message(answer)}")
    status = _FROM_LMSTUDIO.get(str(answer.get("status") or ""))
    if status is None:
        return _failed(
            f"LM Studio answered with a status SIRVIS does not know: {answer.get('status')!r}"
        )
    fields: dict[str, Any] = {
        "status": status.value,
        "lmstudio_job_id": answer.get("job_id"),
        "total_bytes": answer.get("total_size_bytes"),
        "started_at": answer.get("started_at") or _now(),
        "detail": "",
    }
    if status.value in DONE:
        fields["completed_at"] = answer.get("completed_at") or _now()
    return fields


async def watch_with_lmstudio(client: httpx.AsyncClient, job: Mapping[str, Any]) -> dict[str, Any]:
    """How far one download has got; the fields the job should now carry."""
    answer = await _call(
        client, "GET", f"/api/v1/models/download/status/{job['lmstudio_job_id']}", None
    )
    error = answer.get("error")
    if isinstance(error, dict) and error.get("type") == "job_not_found":
        return _failed(
            "LM Studio no longer knows this download, which usually means LM Studio was "
            "restarted; whatever it had written is left on disk"
        )
    if error is not None:
        # Transient by assumption: LM Studio busy or briefly away. The status stays,
        # the reason is shown, and the next pass asks again.
        return {"detail": f"LM Studio could not report progress: {_message(answer)}"}
    status = _FROM_LMSTUDIO.get(str(answer.get("status") or ""))
    if status is None:
        return {
            "detail": f"LM Studio reported a status SIRVIS does not know: {answer.get('status')!r}"
        }
    fields: dict[str, Any] = {
        "status": status.value,
        "downloaded_bytes": answer.get("downloaded_bytes"),
        "total_bytes": answer.get("total_size_bytes"),
        "bytes_per_second": answer.get("bytes_per_second"),
        "estimated_completion": answer.get("estimated_completion"),
        "detail": "",
    }
    if status.value in DONE:
        fields["completed_at"] = answer.get("completed_at") or _now()
    if status is DownloadStatus.FAILED:
        fields["detail"] = "LM Studio reported the download failed and published no reason"
    return fields


async def _call(
    client: httpx.AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> dict[str, Any]:
    """One request to LM Studio, with every failure folded into LM Studio's error shape."""
    try:
        answered = await client.request(method, path, json=body)
    except httpx.HTTPError as failure:
        return _error("unreachable", f"LM Studio did not answer: {type(failure).__name__}")
    try:
        payload = answered.json()
    except ValueError:
        return _error("malformed", f"LM Studio answered HTTP {answered.status_code}, not JSON")
    if not isinstance(payload, dict):
        return _error("malformed", "LM Studio answered with something other than an object")
    if answered.status_code >= 400 and "error" not in payload:
        return _error("http", f"LM Studio answered HTTP {answered.status_code}")
    return payload


def _error(kind: str, message: str) -> dict[str, Any]:
    return {"error": {"type": kind, "message": message}}


def _message(answer: Mapping[str, Any]) -> str:
    error = answer.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or "no reason given")
    return str(error)


def _failed(detail: str) -> dict[str, Any]:
    return {"status": DownloadStatus.FAILED.value, "detail": detail, "completed_at": _now()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── The watcher ─────────────────────────────────────────────────────────────


async def advance(api: Any) -> None:
    """One pass: start what is queued, and ask after what is moving."""
    database: Database = api.state.database
    client: httpx.AsyncClient = api.state.download_client
    for job in in_status(database, (DownloadStatus.QUEUED.value,)):
        _apply(api, job, await start_with_lmstudio(client, job))
    for job in in_status(database, WATCHED):
        if job.get("lmstudio_job_id"):
            _apply(api, job, await watch_with_lmstudio(client, job))


def _apply(api: Any, job: Mapping[str, Any], fields: Mapping[str, Any]) -> None:
    """Write what a pass learned, and announce a status the job has only just reached."""
    update(api.state.database, str(job["download_id"]), fields)
    reached = fields.get("status")
    event = _EVENTS.get(str(reached)) if reached and reached != job.get("status") else None
    if event is None:
        return
    api.state.events.emit(
        event,
        trace_id=str(job.get("trace_id") or ""),
        severity="warning" if reached == DownloadStatus.FAILED.value else "info",
        data={
            "download_id": job["download_id"],
            "repo_id": job["repo_id"],
            "quantization": job.get("quantization") or "",
            "status": reached,
        },
    )


async def serve_downloads(api: Any) -> None:
    """Pass, then wait, for as long as the service runs.

    Waits first: a service that has just started has nothing it must ask LM Studio
    this instant, and a test that builds the app should not find a pass already
    running against a runtime it never meant to reach.
    """
    while True:
        await asyncio.sleep(api.state.settings.download_poll_seconds)
        try:
            await advance(api)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the watcher outlives one bad pass
            LOG.exception("the download watcher hit an error and is continuing")
