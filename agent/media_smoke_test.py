"""Temporary media-download smoke test; never publishes anything.

Cobalt is used for TikTok and Instagram, while YouTube is tested with a pinned
yt-dlp CLI on the temporary GitHub Actions runner. No cookies, credentials, or
browser profiles are supplied. Media is validated with ffprobe and deleted
when the run ends.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ALLOWED_HOSTS = {
    "tiktok": ("tiktok.com",),
    "instagram": ("instagram.com",),
    "youtube": ("youtube.com", "youtu.be"),
}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts", ".flv"}
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_API_SECONDS = 45
MAX_DOWNLOAD_SECONDS = 180


class SmokeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_public_video_url(platform: str, raw_url: str) -> tuple[bool, str]:
    """Only accept public HTTPS URLs belonging to the requested platform."""
    if platform not in ALLOWED_HOSTS:
        return False, "unsupported_platform"
    try:
        parsed = urlparse(raw_url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False, "invalid_url"
    if parsed.scheme != "https" or not host or not parsed.path:
        return False, "invalid_url"
    if parsed.username or parsed.password or not host.replace(".", "").replace("-", "").isalnum():
        return False, "invalid_url"
    if not any(host == domain or host.endswith("." + domain) for domain in ALLOWED_HOSTS[platform]):
        return False, "platform_host_mismatch"
    return True, host


def _classify_cobalt_error(payload: dict[str, Any]) -> str:
    code = str((payload.get("error") or {}).get("code") or "").lower()
    if "private" in code or "unavailable" in code or "not_found" in code:
        return "private_or_unavailable"
    if "rate" in code or "limit" in code:
        return "rate_limited"
    if "auth" in code or "login" in code or "session" in code:
        return "login_or_access_restricted"
    if "service.unsupported" in code or "link.unsupported" in code:
        return "unsupported_url"
    return "cobalt_error"


def _classify_ytdlp_error(message: str) -> str:
    """Map downloader errors to safe labels without logging raw output."""
    text = message.lower()
    if any(term in text for term in (
        "sign in", "login required", "confirm your age",
        "confirm you're not a bot", "confirm you are not a bot",
        "age-restricted", "authentication required",
    )):
        return "login_or_access_restricted"
    if any(term in text for term in (
        "private video", "video unavailable", "has been removed",
        "this video is private",
    )):
        return "private_or_unavailable"
    if any(term in text for term in ("429", "too many requests", "rate limit")):
        return "rate_limited"
    if any(term in text for term in ("max-filesize", "file is larger", "filesize exceeded")):
        return "size_limit"
    return "youtube_download_failed"


def _allowed_download_url(download_url: str, api_url: str) -> bool:
    """Allow only the local Cobalt tunnel or an HTTPS source-media URL."""
    try:
        parsed = urlparse(download_url)
        api = urlparse(api_url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.scheme == api.scheme and parsed.netloc == api.netloc:
        return True
    return parsed.scheme == "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}


def _safe_diagnostic(code: str) -> str:
    """Return only a controlled, non-sensitive error label."""
    return "".join(ch for ch in code.lower() if ch.isalnum() or ch in "_-")[:80] or "unknown_error"


def _request_cobalt(api_url: str, source_url: str) -> dict[str, Any]:
    body = json.dumps({
        "url": source_url,
        "videoQuality": "480",
        "filenameStyle": "basic",
        "youtubeVideoCodec": "h264",
    }).encode("utf-8")
    req = Request(
        api_url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "rubika-agent-public-media-smoke/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=MAX_API_SECONDS) as response:
            raw = response.read(1024 * 1024)
            payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        category = "cobalt_error"
        try:
            error_payload = json.loads(exc.read(4096).decode("utf-8"))
            error_code = str((error_payload.get("error") or {}).get("code") or "")
            if error_code:
                category = _safe_diagnostic(error_code)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            pass
        raise SmokeError("cobalt_http_" + str(exc.code) + "_" + category) from None
    except (URLError, TimeoutError, OSError):
        raise SmokeError("cobalt_connection_error") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise SmokeError("cobalt_invalid_response") from None

    if not isinstance(payload, dict):
        raise SmokeError("cobalt_invalid_response")
    if payload.get("status") == "error":
        raise SmokeError(_classify_cobalt_error(payload))
    if payload.get("status") not in {"tunnel", "redirect"}:
        raise SmokeError("cobalt_no_single_video")
    download_url = payload.get("url")
    if not isinstance(download_url, str) or not _allowed_download_url(download_url, api_url):
        raise SmokeError("cobalt_unsafe_download_url")
    return payload


def _download_stream(download_url: str, destination: Path) -> int:
    req = Request(download_url, headers={"User-Agent": "rubika-agent-public-media-smoke/1.0"})
    started = time.monotonic()
    total = 0
    try:
        with urlopen(req, timeout=30) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "application/json" in content_type or "text/html" in content_type:
                raise SmokeError("cobalt_returned_non_media")
            with destination.open("wb") as output:
                while True:
                    if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                        raise SmokeError("download_timeout")
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise SmokeError("size_limit")
                    output.write(chunk)
    except SmokeError:
        raise
    except HTTPError as exc:
        raise SmokeError("media_http_" + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise SmokeError("media_download_error") from None

    if total <= 0:
        raise SmokeError("empty_download")
    return total


def _download_youtube(source_url: str, destination: Path) -> tuple[Path, int]:
    """Download one public YouTube video without credentials or browser cookies."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    output_template = str(destination.with_suffix(".%(ext)s"))
    executable = shutil.which("yt-dlp")
    command = [executable] if executable else [sys.executable, "-m", "yt_dlp"]
    command.extend([
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--no-colors",
        "--socket-timeout", "15",
        "--retries", "1",
        "--fragment-retries", "1",
        "--max-filesize", "100M",
        "--format", "best[ext=mp4][height<=720]/best[height<=720]/best",
        "--merge-output-format", "mp4",
        "--output", output_template,
        "--",
        source_url,
    ])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=MAX_DOWNLOAD_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SmokeError("youtube_download_timeout") from None
    except OSError:
        raise SmokeError("yt_dlp_not_available") from None

    if result.returncode != 0:
        raise SmokeError(_classify_ytdlp_error((result.stderr or "") + "\n" + (result.stdout or "")))

    candidates = sorted(
        path for path in destination.parent.glob(destination.stem + ".*")
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
    )
    if len(candidates) != 1:
        raise SmokeError("youtube_output_missing_or_ambiguous")
    media_path = candidates[0]
    try:
        size_bytes = media_path.stat().st_size
    except OSError:
        raise SmokeError("youtube_output_unreadable") from None
    if size_bytes <= 0:
        raise SmokeError("empty_download")
    if size_bytes > MAX_FILE_BYTES:
        raise SmokeError("size_limit")
    return media_path, size_bytes


def _probe_media(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SmokeError("ffprobe_not_installed")
    try:
        completed = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
                "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SmokeError("media_validation_timeout") from None
    if completed.returncode != 0:
        raise SmokeError("media_validation_failed")
    try:
        probe = json.loads(completed.stdout)
        streams = probe.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float((probe.get("format") or {}).get("duration") or 0)
    except (ValueError, TypeError, json.JSONDecodeError):
        raise SmokeError("media_validation_failed") from None
    if not video or duration <= 0:
        raise SmokeError("invalid_video_stream")
    return {
        "duration_seconds": round(duration, 2),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "has_audio": audio is not None,
    }


def test_one_platform(platform: str, source_url: str, api_url: str, root: Path) -> dict[str, Any]:
    started = time.monotonic()
    valid, detail = validate_public_video_url(platform, source_url)
    if not valid:
        return {"platform": platform, "ok": False, "error": detail, "seconds": 0}

    destination = root / platform / "smoke-test.mp4"
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        if platform == "youtube":
            media_path, size_bytes = _download_youtube(source_url, destination)
            download_method = "yt-dlp"
        else:
            response = _request_cobalt(api_url, source_url)
            size_bytes = _download_stream(response["url"], destination)
            media_path = destination
            download_method = "cobalt-" + str(response.get("status") or "unknown")
        media = _probe_media(media_path)
        return {
            "platform": platform,
            "ok": True,
            "host": detail,
            "bytes": size_bytes,
            "download_method": download_method,
            **media,
            "seconds": round(time.monotonic() - started, 2),
        }
    except SmokeError as exc:
        return {
            "platform": platform,
            "ok": False,
            "host": detail,
            "error": _safe_diagnostic(exc.code),
            "seconds": round(time.monotonic() - started, 2),
        }


def _write_summary(results: list[dict[str, Any]]) -> None:
    lines = [
        "## Rubika-Agent media download smoke test",
        "",
        "The test uses temporary Cobalt for TikTok/Instagram and pinned yt-dlp for YouTube.",
        "No credentials/cookies are used, no Rubika message is sent, and temporary media is deleted.",
        "",
        "| Platform | Result | Size | Duration | Details |",
        "|---|---|---:|---:|---|",
    ]
    for item in results:
        if item.get("ok"):
            details = f'{item.get("video_codec", "unknown")} {item.get("width", "?")}×{item.get("height", "?")}'
            if item.get("has_audio"):
                details += f' + {item.get("audio_codec") or "audio"}'
            lines.append(
                f'| {item["platform"]} | ✅ downloaded + validated | {item["bytes"]} bytes | '
                f'{item["duration_seconds"]} s | {details} |'
            )
        else:
            lines.append(
                f'| {item["platform"]} | ❌ failed | — | — | {item.get("error", "unknown")} |'
            )
    lines.extend([
        "",
        "Failures are reported without trying to bypass sign-in, CAPTCHA, or private-post restrictions.",
    ])
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write("\n".join(lines) + "\n")


def main() -> int:
    api_url = os.getenv("COBALT_API_URL", "http://127.0.0.1:9000/").strip()
    parsed_api = urlparse(api_url)
    if parsed_api.scheme != "http" or parsed_api.hostname not in {"127.0.0.1", "localhost"}:
        print(json.dumps({"ok": False, "error": "COBALT_API_URL_must_be_local_http"}))
        return 2
    if not api_url.endswith("/"):
        api_url += "/"

    urls = {
        "tiktok": os.getenv("TIKTOK_URL", "").strip(),
        "instagram": os.getenv("INSTAGRAM_URL", "").strip(),
        "youtube": os.getenv("YOUTUBE_URL", "").strip(),
    }

    configured = [(platform, url) for platform, url in urls.items() if url]
    if not configured:
        print(json.dumps({"ok": False, "error": "no_platform_urls_configured"}))
        return 2

    temp_root = os.getenv("RUNNER_TEMP") or tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="rubika-cobalt-smoke-", dir=temp_root) as temp:
        root = Path(temp)
        results = []
        for platform, url in urls.items():
            if url:
                results.append(test_one_platform(platform, url, api_url, root))
            else:
                results.append({"platform": platform, "ok": False, "skipped": True, "error": "url_not_provided"})

        attempted = [item for item in results if not item.get("skipped")]
        report = {
            "ok": bool(attempted) and all(item.get("ok") for item in attempted),
            "downloaded_count": sum(1 for item in attempted if item.get("ok")),
            "total_configured": len(attempted),
            "results": results,
        }
        _write_summary(results)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
