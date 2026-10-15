"""Authenticated, fail-closed YouTube download relay for authorized public URLs.

No cookies, login, third-party proxy, challenge solver, or access-control bypass is used.
The endpoint returns one temporary media file; it never publishes to Rubika.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictBool
from starlette.background import BackgroundTask


SERVICE = "rubika-agent-authorized-media-relay"
MAX_DURATION_SECONDS = 180
MAX_MEDIA_BYTES = 45 * 1024 * 1024
MAX_SOURCE_URL_BYTES = 2048
DOWNLOAD_TIMEOUT_SECONDS = 210
FFPROBE_TIMEOUT_SECONDS = 20
ALLOWED_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_DOWNLOAD_SLOT = threading.BoundedSemaphore(1)

app = FastAPI(
    title="Rubika-Agent Authorized Media Relay",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=12, max_length=MAX_SOURCE_URL_BYTES)
    platform: str = Field(default="youtube", pattern=r"^youtube$")
    permission_confirmed: StrictBool


class RelayError(RuntimeError):
    def __init__(self, code: str, status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def canonical_youtube_url(raw_url: str) -> tuple[str, str]:
    """Reduce a public YouTube watch/Shorts URL to a stable URL and video ID."""
    if not isinstance(raw_url, str) or len(raw_url.encode("utf-8")) > MAX_SOURCE_URL_BYTES:
        raise RelayError("invalid_source_url", 400)
    try:
        parsed = urlparse(raw_url.strip())
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in ALLOWED_YOUTUBE_HOSTS:
            raise RelayError("youtube_https_url_required", 400)
        if parsed.username or parsed.password or parsed.fragment:
            raise RelayError("noncanonical_source_url", 400)
        video_id = ""
        if host in {"youtu.be", "www.youtu.be"}:
            if parsed.query:
                raise RelayError("short_link_query_not_allowed", 400)
            video_id = parsed.path.strip("/")
        elif parsed.path == "/watch":
            query = parse_qs(parsed.query, keep_blank_values=True)
            values = query.get("v", [])
            if len(values) != 1:
                raise RelayError("youtube_video_id_required", 400)
            video_id = values[0]
        else:
            match = re.fullmatch(r"/shorts/([A-Za-z0-9_-]{11})/?", parsed.path)
            if not match or parsed.query:
                raise RelayError("unsupported_youtube_url_type", 400)
            video_id = match.group(1)
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise RelayError("invalid_youtube_video_id", 400)
        canonical = "https://www.youtube.com/watch?v=" + video_id
        return canonical, video_id
    except ValueError:
        raise RelayError("invalid_source_url", 400) from None


def _authorize(request: Request, authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("RELAY_API_TOKEN", "")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail={"error": "relay_not_configured"})
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})
    supplied = authorization[7:]
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail={"error": "unauthorized"})


def _download_and_probe(url: str, video_id: str, work_dir: Path) -> tuple[Path, dict[str, object]]:
    output_template = str(work_dir / "%(id)s.%(ext)s")
    command = [
        sys.executable, "-m", "yt_dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--no-colors",
        "--no-cache-dir",
        "--socket-timeout", "15",
        "--retries", "0",
        "--fragment-retries", "0",
        "--max-filesize", str(MAX_MEDIA_BYTES),
        "--match-filter", f"duration <= {MAX_DURATION_SECONDS} & !is_live",
        "--format", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--output", output_template,
        "--print", "after_move:filepath",
        url,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            check=False,
            cwd=work_dir,
            env={
                **os.environ,
                "PYTHONUNBUFFERED": "1",
                "HOME": str(work_dir),
                "XDG_CONFIG_HOME": str(work_dir / ".config"),
            },
        )
    except subprocess.TimeoutExpired:
        raise RelayError("download_timeout", 504) from None
    except FileNotFoundError:
        raise RelayError("downloader_unavailable", 503) from None
    except OSError:
        raise RelayError("downloader_execution_failed", 503) from None

    # Intentionally discard subprocess output; it may include private URLs or provider details.
    if result.returncode != 0:
        raise RelayError("source_restricted_or_unavailable", 422)

    candidates = [
        path for path in work_dir.glob("*")
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl", ".temp"}
    ]
    media_path = next((
        path for path in candidates
        if path.suffix.lower() in {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
        and video_id in path.name
    ), None)
    if media_path is None:
        raise RelayError("download_output_missing", 422)
    if media_path.stat().st_size <= 50 * 1024:
        raise RelayError("downloaded_file_too_small", 422)
    if media_path.stat().st_size > MAX_MEDIA_BYTES:
        raise RelayError("downloaded_file_too_large", 413)

    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,codec_name,width,height",
                "-of", "json", str(media_path),
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RelayError("media_validation_timeout", 422) from None
    except FileNotFoundError:
        raise RelayError("media_probe_unavailable", 503) from None
    except OSError:
        raise RelayError("media_probe_failed", 422) from None
    if probe.returncode != 0:
        raise RelayError("media_validation_failed", 422)
    try:
        data = json.loads(probe.stdout)
        streams = data.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        duration = float((data.get("format") or {}).get("duration") or 0)
        width = int(video.get("width") or 0) if video else 0
        height = int(video.get("height") or 0) if video else 0
        video_codec = str(video.get("codec_name") or "") if video else ""
        audio_codec = str(audio.get("codec_name") or "") if audio else ""
    except (ValueError, TypeError, json.JSONDecodeError):
        raise RelayError("invalid_media_probe_response", 422) from None
    if not video or width <= 0 or height <= 0 or duration <= 0:
        raise RelayError("invalid_video_stream", 422)
    if duration > MAX_DURATION_SECONDS:
        raise RelayError("video_duration_limit", 413)

    has_audio = audio is not None
    output_path = media_path
    if media_path.suffix.lower() != ".mp4" or video_codec != "h264" or (has_audio and audio_codec != "aac"):
        output_path = work_dir / f"youtube_{video_id}_h264.mp4"
        encode_command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(media_path), "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", str(output_path),
        ]
        try:
            encoded = subprocess.run(
                encode_command,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                cwd=work_dir,
            )
        except subprocess.TimeoutExpired:
            raise RelayError("video_transcode_timeout", 504) from None
        except (FileNotFoundError, OSError):
            raise RelayError("video_transcoder_unavailable", 503) from None
        if encoded.returncode != 0 or not output_path.is_file():
            raise RelayError("video_transcode_failed", 422)
        if output_path.stat().st_size <= 50 * 1024:
            raise RelayError("transcoded_file_too_small", 422)
        if output_path.stat().st_size > MAX_MEDIA_BYTES:
            raise RelayError("transcoded_file_too_large", 413)
        try:
            output_probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration:stream=codec_type,codec_name,width,height",
                    "-of", "json", str(output_path),
                ],
                capture_output=True, text=True, timeout=FFPROBE_TIMEOUT_SECONDS, check=False,
            )
            output_info = json.loads(output_probe.stdout) if output_probe.returncode == 0 else {}
            output_streams = output_info.get("streams") or []
            output_video = next((s for s in output_streams if s.get("codec_type") == "video"), None)
            output_audio = next((s for s in output_streams if s.get("codec_type") == "audio"), None)
            if not output_video or output_video.get("codec_name") != "h264":
                raise RelayError("transcoded_video_validation_failed", 422)
            if output_audio and output_audio.get("codec_name") != "aac":
                raise RelayError("transcoded_audio_validation_failed", 422)
            media_path = output_path
            video_codec = str(output_video.get("codec_name") or "h264")
            audio_codec = str(output_audio.get("codec_name") or "aac") if output_audio else ""
            width = int(output_video.get("width") or width)
            height = int(output_video.get("height") or height)
            audio = output_audio
            duration = float((output_info.get("format") or {}).get("duration") or duration)
        except RelayError:
            raise
        except (ValueError, TypeError, json.JSONDecodeError, subprocess.TimeoutExpired):
            raise RelayError("transcoded_media_validation_failed", 422) from None

    return media_path, {
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "video_codec": video_codec,
        "audio_codec": audio_codec or None,
        "has_audio": audio is not None,
        "size_bytes": media_path.stat().st_size,
        "mime_type": "video/mp4",
    }


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": SERVICE,
        "downloader": "yt-dlp",
        "max_duration_seconds": MAX_DURATION_SECONDS,
        "max_media_bytes": MAX_MEDIA_BYTES,
        "publishing": False,
    }


@app.post("/v1/download", dependencies=[Depends(_authorize)])
def download_video(payload: DownloadRequest, request: Request) -> FileResponse:
    if payload.permission_confirmed is not True:
        raise HTTPException(status_code=403, detail={"error": "permission_confirmation_required"})
    canonical_url, video_id = canonical_youtube_url(payload.url)
    if not _DOWNLOAD_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail={"error": "relay_busy"})
    work_dir = Path(tempfile.mkdtemp(prefix="rubika-relay-"))
    try:
        media_path, metadata = _download_and_probe(canonical_url, video_id, work_dir)
        response_headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Relay-Publishing": "disabled",
            "X-Media-Width": str(metadata["width"]),
            "X-Media-Height": str(metadata["height"]),
            "X-Media-Duration": str(metadata["duration_seconds"]),
            "X-Media-Has-Audio": "true" if metadata["has_audio"] else "false",
            "X-Media-SHA256": _sha256(media_path),
        }
        # The temporary directory is removed after the response finishes.
        return FileResponse(
            path=media_path,
            media_type="video/mp4",
            filename=f"youtube_{video_id}.mp4",
            headers=response_headers,
            background=BackgroundTask(_cleanup_after_response, work_dir),
        )
    except HTTPException:
        shutil.rmtree(work_dir, ignore_errors=True)
        _DOWNLOAD_SLOT.release()
        raise
    except RelayError as error:
        shutil.rmtree(work_dir, ignore_errors=True)
        _DOWNLOAD_SLOT.release()
        raise HTTPException(
            status_code=error.status,
            detail={"error": error.code, "publishing": False},
        ) from None
    except Exception:
        shutil.rmtree(work_dir, ignore_errors=True)
        _DOWNLOAD_SLOT.release()
        raise HTTPException(status_code=500, detail={"error": "relay_internal_error"}) from None


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_after_response(work_dir: Path) -> None:
    shutil.rmtree(work_dir, ignore_errors=True)
    try:
        _DOWNLOAD_SLOT.release()
    except ValueError:
        pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "relay.app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000")),
        log_level="warning",
        access_log=False,
    )
