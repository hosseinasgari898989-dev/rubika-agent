"""Preserve media that could not be sent to Rubika: untouched original -> Mega failed/, public link, D1 record.

Never raises: every failure is returned in the result dict so the caller's own error handling is unchanged.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from agent.storage.mega_export import export_public_link

SOURCES = {"tiktok", "mob_x"}
MEDIA_TYPES = {"video", "image"}
RECORD_PATH = "/api/console/failed-media/record"
FAILED_DIR = "failed"


def _slug(value: object, limit: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")[:limit]


def _suffix(path: Path, media_type: str) -> str:
    suffix = path.suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{2,5}", suffix):
        return suffix
    return ".mp4" if media_type == "video" else ".jpg"


def preserve_failed_media(
    client: Any, storage: Any, source: str, media_type: str, local_path: str | Path,
    caption: object, reason: object, job_id: object = None, item_id: object = None,
) -> dict:
    result: dict[str, Any] = {
        "ok": False, "stored": False, "recorded": False,
        "storage_key": None, "share_url": None, "error": None,
    }
    try:
        if source not in SOURCES or media_type not in MEDIA_TYPES:
            result["error"] = "failed_media_invalid_arguments"
            return result
        src = Path(local_path)
        if not src.is_file() or src.stat().st_size <= 0:
            result["error"] = "failed_media_local_file_missing"
            return result
        ident = _slug(job_id) or _slug(item_id)
        if not ident:
            result["error"] = "failed_media_id_missing"
            return result
        name = f"{source}_{ident}{_suffix(src, media_type)}"
        safe_reason = re.sub(r"[^a-z0-9_-]+", "_", str(reason or "").lower()).strip("_")[:80] or "unknown"
        size_bytes = int(src.stat().st_size)

        try:
            with tempfile.TemporaryDirectory(prefix="rubika-agent-failed-") as temp:
                staged = Path(temp) / name
                try:
                    os.link(src, staged)
                except OSError:
                    shutil.copy2(src, staged)
                # Unique name + listing check: a retry never uploads the same original twice.
                if name in storage.list_directory(FAILED_DIR):
                    storage_key = storage.root.rstrip("/") + "/" + FAILED_DIR + "/" + name
                else:
                    storage_key = storage.upload(staged, FAILED_DIR)
        except Exception:
            result["error"] = "failed_media_upload_failed"
            return result
        result.update(stored=True, storage_key=storage_key)

        try:
            result["share_url"] = export_public_link(storage, storage_key)
        except Exception:
            result["share_url"] = None  # a missing link must not stop the record

        try:
            client.request("POST", RECORD_PATH, {
                "source": source, "job_id": str(job_id) if job_id else None,
                "item_id": str(item_id) if item_id else None, "media_type": media_type,
                "storage_key": storage_key, "share_url": result["share_url"],
                "caption": str(caption or "")[:5000], "reason": safe_reason, "size_bytes": size_bytes,
            })
            result["recorded"] = True
        except Exception:
            result["error"] = "failed_media_record_failed"
        result["ok"] = bool(result["stored"] and result["recorded"])
        return result
    except Exception:
        result["error"] = result["error"] or "failed_media_internal_error"
        return result
