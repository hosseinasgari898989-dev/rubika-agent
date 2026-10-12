"""One-time public-media download smoke test.

Downloads at most one short public video per supported platform to a temporary
GitHub Actions runner directory, validates it with ffprobe, reports only safe
metadata, and deletes all media afterward. It never logs in or publishes.
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
from urllib.parse import urlparse
from typing import Any

ALLOWED_HOSTS = {
    "tiktok": ("tiktok.com",),
    "instagram": ("instagram.com",),
    "youtube": ("youtube.com", "youtu.be"),
}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts", ".flv"}
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_DURATION_SECONDS = 180
PROCESS_TIMEOUT_SECONDS = 180


def validate_public_video_url(platform: str, raw_url: str) -> tuple[bool, str]:
    """Only allow public HTTPS links for the platform requested."""
    if platform not in ALLOWED_HOSTS:
        return False, "unsupported_platform"
    try:
        parsed = urlparse(raw_url.strip())
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        return False, "invalid_url"
    if parsed.scheme != "https" or not host or not parsed.path:
        return False, "invalid_url"
    if parsed.username or parsed.password:
        return False, "invalid_url"
    if not any(host == domain or host.endswith("." + domain) for domain in ALLOWED_HOSTS[platform]):
        return False, "platform_host_mismatch"
    if not host.replace(".", "").replace("-", "").isalnum():
        return False, "invalid_url"
    return True, host


def _safe_error(stderr: str, timed_out: bool = False) -> str:
    if timed_out:
        return "download_timeout"
    text = stderr.lower()
    if any(token in text for token in ("login required", "log in", "sign in", "cookies", "authentication required")):
        return "login_or_access_restricted"
    if any(token in text for token in ("private video", "private account", "not authorized", "not authorised")):
        return "private_or_unavailable"
    if any(token in text for token in ("429", "too many requests", "rate limit", "rate-limited")):
        return "rate_limited"
    if any(token in text for token in ("http error 403", "forbidden", "captcha", "verify you are human", "blocked by cloudflare", "anti-bot")):
        return "platform_access_blocked"
    if "unsupported url" in text:
        return "unsupported_url"
    if any(token in text for token in ("no video formats found", "requested format is not available", "no formats found")):
        return "no_downloadable_format"
    if any(token in text for token in ("unable to extract", "unable to parse", "extractor error")):
        return "extractor_parse_error"
    if any(token in text for token in ("video unavailable", "not available", "not found", "does not exist", "requested content is not available")):
        return "video_unavailable_or_removed"
    if "maximum file size" in text or "file is larger than" in text:
        return "size_limit"
    if "duration" in text and ("too long" in text or "match filter" in text):
        return "duration_limit"
    return "extractor_or_download_error"


def _run_json(command: list[str], timeout: int) -> tuple[int, str, str, bool]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr, False
    except subprocess.TimeoutExpired:
        return 124, "", "", True


def test_one_platform(platform: str, raw_url: str, root: Path) -> dict[str, Any]:
    started = time.monotonic()
    valid, detail = validate_public_video_url(platform, raw_url)
    if not valid:
        return {
            "platform": platform,
            "ok": False,
            "error": detail,
            "seconds": 0,
        }

    # A unique empty directory per platform ensures no old file can count as a success.
    output_dir = root / platform
    output_dir.mkdir(parents=True, exist_ok=True)
    template = str(output_dir / "%(id)s.%(ext)s")
    command = [
        sys.executable, "-m", "yt_dlp",
        "--no-warnings",
        "--no-progress",
        "--no-cache-dir",
        "--socket-timeout", "15",
        "--retries", "1",
        "--fragment-retries", "1",
        "--extractor-retries", "1",
        "--playlist-end", "1",
        "--max-downloads", "1",
        "--max-filesize", "100M",
        "--match-filter", f"duration <= {MAX_DURATION_SECONDS}",
        "--format", "best[height<=720][ext=mp4]/best[height<=720]/best",
        "--output", template,
        "--no-overwrites",
        "--no-continue",
        raw_url,
    ]

    return_code, stdout, stderr, timed_out = _run_json(command, PROCESS_TIMEOUT_SECONDS)
    if return_code != 0:
        return {
            "platform": platform,
            "ok": False,
            "host": detail,
            "error": _safe_error(stderr, timed_out),
            "seconds": round(time.monotonic() - started, 2),
        }

    candidates = sorted(
        p for p in output_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
        and not p.name.endswith((".part", ".ytdl"))
    )
    if not candidates:
        return {
            "platform": platform,
            "ok": False,
            "host": detail,
            "error": "no_video_file_created",
            "seconds": round(time.monotonic() - started, 2),
        }

    media = candidates[0]
    size_bytes = media.stat().st_size
    if size_bytes <= 0:
        return {
            "platform": platform,
            "ok": False,
            "host": detail,
            "error": "empty_download",
            "seconds": round(time.monotonic() - started, 2),
        }
    if size_bytes > MAX_FILE_BYTES:
        return {
            "platform": platform,
            "ok": False,
            "host": detail,
            "error": "size_limit",
            "bytes": size_bytes,
            "seconds": round(time.monotonic() - started, 2),
        }

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {
            "platform": platform,
            "ok": False,
            "host": detail,
            "error": "ffprobe_not_installed",
            "bytes": size_bytes,
            "seconds": round(time.monotonic() - started, 2),
        }

    probe_code, probe_stdout, probe_stderr, probe_timeout = _run_json(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration:stream=codec_type,codec_name,width,height",
            "-of", "json", str(media),
        ],
        30,
    )
    if probe_code != 0 or probe_timeout:
        return {
            "platform": platform,
            "ok": False,
            "host": detail,
            "error": "media_validation_failed",
            "bytes": size_bytes,
            "seconds": round(time.monotonic() - started, 2),
        }

    try:
        probe = json.loads(probe_stdout)
        streams = probe.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        duration = float((probe.get("format") or {}).get("duration") or 0)
    except (ValueError, TypeError, json.JSONDecodeError):
        video, audio, duration = None, None, 0.0

    if not video or duration <= 0 or duration > MAX_DURATION_SECONDS + 1:
        return {
            "platform": platform,
            "ok": False,
            "host": detail,
            "error": "invalid_video_stream_or_duration",
            "bytes": size_bytes,
            "seconds": round(time.monotonic() - started, 2),
        }

    return {
        "platform": platform,
        "ok": True,
        "host": detail,
        "bytes": size_bytes,
        "duration_seconds": round(duration, 2),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "width": video.get("width"),
        "height": video.get("height"),
        "has_audio": audio is not None,
        "seconds": round(time.monotonic() - started, 2),
    }


def _write_summary(results: list[dict[str, Any]]) -> None:
    lines = [
        "## Rubika-Agent media download smoke test",
        "",
        "No login/cookies were used. No Rubika message was sent. Temporary media is deleted after validation.",
        "",
        "| Platform | Result | Downloaded size | Duration | Details |",
        "|---|---|---:|---:|---|",
    ]
    for item in results:
        if item.get("ok"):
            details = f'{item.get("video_codec", "unknown")} {item.get("width", "?")}×{item.get("height", "?")}'
            lines.append(
                f'| {item["platform"]} | ✅ downloaded + validated | {item.get("bytes", 0)} bytes | '
                f'{item.get("duration_seconds", 0)} s | {details} |'
            )
        elif item.get("skipped"):
            lines.append(f'| {item["platform"]} | ⚪ skipped | — | — | URL not provided |')
        else:
            lines.append(
                f'| {item["platform"]} | ❌ failed | {item.get("bytes", "—")} | — | '
                f'{item.get("error", "unknown")} |'
            )
    lines.extend([
        "",
        "**Note:** a failure may mean the post is private, login-restricted, removed, rate-limited, or unsupported. The test does not try to bypass access controls.",
    ])
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write("\n".join(lines) + "\n")


def main() -> int:
    urls = {
        "tiktok": os.getenv("TIKTOK_URL", "").strip(),
        "instagram": os.getenv("INSTAGRAM_URL", "").strip(),
        "youtube": os.getenv("YOUTUBE_URL", "").strip(),
    }
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rubika-media-smoke-") as temp:
        root = Path(temp)
        for platform, url in urls.items():
            if not url:
                results.append({"platform": platform, "ok": False, "skipped": True, "error": "url_not_provided"})
                continue
            results.append(test_one_platform(platform, url, root))
        report = {
            "ok": all(item.get("ok") for item in results if not item.get("skipped")) and
                  any(not item.get("skipped") for item in results),
            "downloaded_count": sum(1 for item in results if item.get("ok")),
            "total_configured": sum(1 for item in results if not item.get("skipped")),
            "results": results,
        }
        report_path = Path(os.getenv("RUNNER_TEMP", temp)) / "rubika-agent-media-smoke-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_summary(results)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        # Fail the workflow when any configured platform fails, but keep all source results visible.
        return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
