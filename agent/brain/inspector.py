"""Local file inspection for video, image and audio; never trusts an extension alone."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import subprocess
from pathlib import Path
from typing import Any

from .models import InspectionResult

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".ts", ".avi"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"}


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _failed(kind: str, size: int, code: str) -> InspectionResult:
    return InspectionResult(False, kind, None, size, errors=(code,))


def inspect_media(
    path: str | Path,
    kind: str | None = None,
    *,
    ffprobe: str = "ffprobe",
    timeout_seconds: int = 25,
    max_bytes: int = 500 * 1024 * 1024,
) -> InspectionResult:
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError:
        return _failed(kind or "unknown", 0, "file_unreadable")
    if not source.is_file() or size <= 0:
        return _failed(kind or "unknown", size, "empty_or_missing_file")
    if size > max_bytes:
        return _failed(kind or "unknown", size, "file_size_limit")

    inferred = kind or (
        "image" if source.suffix.lower() in IMAGE_EXTS else
        "video" if source.suffix.lower() in VIDEO_EXTS else
        "audio" if source.suffix.lower() in AUDIO_EXTS else "unknown"
    )
    if inferred not in {"video", "image", "audio"}:
        return _failed("unknown", size, "unsupported_media_type")

    try:
        digest = sha256_file(source)
    except OSError:
        return _failed(inferred, size, "file_hash_failed")

    if inferred == "image":
        try:
            from PIL import Image
            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                width, height = image.size
                image_format = (image.format or source.suffix.lstrip(".")).lower()
            mime = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png",
                    "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp"}.get(
                        image_format, mimetypes.guess_type(source.name)[0])
            if width <= 0 or height <= 0:
                return _failed(inferred, size, "invalid_image_dimensions")
            return InspectionResult(True, "image", digest, size, mime, width, height)
        except ImportError:
            return _failed(inferred, size, "pillow_not_installed")
        except Exception:
            return _failed(inferred, size, "invalid_image")

    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of", "json", str(source),
            ],
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
        )
    except FileNotFoundError:
        return _failed(inferred, size, "ffprobe_not_installed")
    except subprocess.TimeoutExpired:
        return _failed(inferred, size, "ffprobe_timeout")
    except OSError:
        return _failed(inferred, size, "ffprobe_execution_failed")

    if result.returncode != 0:
        return _failed(inferred, size, "media_probe_failed")
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
        streams = payload.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration_raw = (payload.get("format") or {}).get("duration")
        duration = float(duration_raw) if duration_raw is not None else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return _failed(inferred, size, "invalid_probe_response")

    if inferred == "video" and (video is None or not duration or duration <= 0):
        return _failed(inferred, size, "invalid_video_stream")
    if inferred == "audio" and audio is None:
        return _failed(inferred, size, "invalid_audio_stream")
    if inferred == "audio" and (duration is None or duration <= 0):
        return _failed(inferred, size, "invalid_audio_duration")

    return InspectionResult(
        ok=True,
        media_type=inferred,
        sha256=digest,
        size_bytes=size,
        mime_type=mimetypes.guess_type(source.name)[0],
        width=int(video["width"]) if video and video.get("width") else None,
        height=int(video["height"]) if video and video.get("height") else None,
        duration_seconds=duration,
        has_audio=audio is not None if inferred == "video" else (True if inferred == "audio" else None),
        video_codec=str(video.get("codec_name")) if video else None,
        audio_codec=str(audio.get("codec_name")) if audio else None,
    )
