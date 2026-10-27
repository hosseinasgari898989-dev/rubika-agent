"""Bounded yt-dlp download + ffmpeg 720p normalization; never uploads or publishes.

The caller must explicitly select a public YouTube URL and output directory.
This module verifies disk headroom before downloading, preserves resume data
inside a temporary work directory, checks the source/final media with ffprobe,
and returns the Rubika size guard result without performing any Rubika request.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from agent.brain.youtube_pipeline import (
    YouTubePolicyError,
    assess_download_disk_budget,
    build_ffmpeg_normalize_command,
    build_ytdlp_command,
    check_rubika_upload_size,
    load_youtube_policy,
    normalize_channel_key,
    validate_youtube_video_url,
)

VIDEO_SUFFIXES = frozenset({".mp4", ".mkv", ".webm", ".mov", ".m4v", ".ts", ".flv"})
DEFAULT_MAX_RUNTIME_SECONDS = 6 * 60 * 60
MAX_METADATA_SECONDS = 90
MAX_PROBE_SECONDS = 45
SAFE_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


class YouTubeDownloadError(RuntimeError):
    """Stable, sanitized code for a failed YouTube download/transcode."""


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _positive_number(value: Any) -> float | None:
    if type(value) in (int, float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) and number > 0 else None


def _format_rate_bytes(duration_seconds: float, rate_kbps: Any) -> int | None:
    rate = _positive_number(rate_kbps)
    if rate is None:
        return None
    # yt-dlp reports tbr/abr in kbit/s. Add a 35% margin for imperfect estimates.
    return math.ceil(duration_seconds * rate * 1000 / 8 * 1.35)


def _format_fits_720_class(row: Mapping[str, Any], max_width: int, max_height: int) -> bool:
    width = _positive_int(row.get("width"))
    height = _positive_int(row.get("height"))
    return width is not None and height is not None and width <= max_width and height <= max_height


def estimate_download_size(
    metadata: Mapping[str, Any],
    *,
    max_width: int,
    max_height: int,
) -> tuple[int, str]:
    """Estimate the actual 720-class selection, not the source's arbitrary best 4K format."""
    duration = _positive_number(metadata.get("duration"))
    if duration is None:
        raise YouTubeDownloadError("youtube_duration_missing_for_disk_estimate")

    width = _positive_int(metadata.get("width"))
    height = _positive_int(metadata.get("height"))
    top_level_size = _positive_int(metadata.get("filesize")) or _positive_int(metadata.get("filesize_approx"))
    if (
        top_level_size is not None
        and width is not None
        and height is not None
        and width <= max_width
        and height <= max_height
    ):
        return top_level_size, "metadata_filesize"

    raw_formats = metadata.get("formats")
    formats = [row for row in raw_formats if isinstance(row, dict)] if isinstance(raw_formats, list) else []
    eligible = [row for row in formats if _format_fits_720_class(row, max_width, max_height)]
    video_rows = [
        row for row in eligible
        if isinstance(row.get("vcodec"), str) and row.get("vcodec") != "none"
    ]
    separate_video = [
        row for row in video_rows
        if row.get("acodec") == "none"
    ]
    separate_audio = [
        row for row in formats
        if row.get("vcodec") == "none"
        and isinstance(row.get("acodec"), str)
        and row.get("acodec") != "none"
    ]
    video_rows.sort(
        key=lambda row: (
            _positive_int(row.get("height")) or 0,
            _positive_int(row.get("width")) or 0,
            _positive_number(row.get("tbr")) or 0,
        ),
        reverse=True,
    )
    separate_video.sort(
        key=lambda row: (
            _positive_int(row.get("height")) or 0,
            _positive_int(row.get("width")) or 0,
            _positive_number(row.get("tbr")) or 0,
        ),
        reverse=True,
    )
    separate_audio.sort(key=lambda row: _positive_number(row.get("abr")) or _positive_number(row.get("tbr")) or 0, reverse=True)

    chosen_video = (separate_video or video_rows)
    if chosen_video:
        video = chosen_video[0]
        video_size = _positive_int(video.get("filesize")) or _positive_int(video.get("filesize_approx"))
        if video_size is None:
            video_size = _format_rate_bytes(duration, video.get("tbr"))
        if separate_video and separate_audio:
            audio = separate_audio[0]
            audio_size = _positive_int(audio.get("filesize")) or _positive_int(audio.get("filesize_approx"))
            if audio_size is None:
                audio_size = _format_rate_bytes(duration, audio.get("abr") or audio.get("tbr"))
            if video_size is not None and audio_size is not None:
                return video_size + audio_size, "selected_video_audio_formats"
        elif video_size is not None:
            return video_size, "selected_combined_format"

    # Conservative fallback when format sizes/bitrates are omitted by the source.
    # 6 Mbit/s is an estimate, not a download cap; disk budgeting adds its own margin.
    return math.ceil(duration * 750_000), "duration_6mbit_estimate"


def _orientation_limits(width: int, height: int, policy: Mapping[str, Any]) -> tuple[int, int]:
    short_edge = int(policy["output_short_edge_max"])
    long_edge = int(policy["output_long_edge_max"])
    if width == height:
        return short_edge, short_edge
    return (long_edge, short_edge) if width > height else (short_edge, long_edge)


def _run_json_process(command: list[str], *, timeout_seconds: int, code_prefix: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise YouTubeDownloadError(code_prefix + "_timeout") from None
    except OSError:
        raise YouTubeDownloadError(code_prefix + "_start_failed") from None
    if result.returncode != 0:
        raise YouTubeDownloadError(code_prefix + "_failed")
    if len(result.stdout) > 4_000_000:
        raise YouTubeDownloadError(code_prefix + "_response_too_large")
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        raise YouTubeDownloadError(code_prefix + "_invalid_json") from None
    if not isinstance(payload, dict):
        raise YouTubeDownloadError(code_prefix + "_invalid_json")
    return payload


def _probe_video(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise YouTubeDownloadError("ffprobe_missing")
    payload = _run_json_process(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height",
            "-of", "json", str(path),
        ],
        timeout_seconds=MAX_PROBE_SECONDS,
        code_prefix="media_probe",
    )
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise YouTubeDownloadError("media_probe_streams_missing")
    video = next(
        (row for row in streams if isinstance(row, dict) and row.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (row for row in streams if isinstance(row, dict) and row.get("codec_type") == "audio"),
        None,
    )
    if not isinstance(video, dict):
        raise YouTubeDownloadError("media_video_stream_missing")
    width = _positive_int(video.get("width"))
    height = _positive_int(video.get("height"))
    duration = _positive_number((payload.get("format") or {}).get("duration"))
    if width is None or height is None or duration is None:
        raise YouTubeDownloadError("media_dimensions_or_duration_invalid")
    try:
        size_bytes = path.stat().st_size
    except OSError:
        raise YouTubeDownloadError("media_file_unreadable") from None
    if size_bytes <= 0:
        raise YouTubeDownloadError("media_file_empty")
    return {
        "width": width,
        "height": height,
        "duration_seconds": round(duration, 2),
        "size_bytes": size_bytes,
        "video_codec": str(video.get("codec_name") or "unknown"),
        "audio_codec": str(audio.get("codec_name") or "none") if isinstance(audio, dict) else None,
        "has_audio": isinstance(audio, dict),
    }


def download_youtube_video(
    source_url: str,
    output_dir: str | Path,
    *,
    max_runtime_seconds: int = DEFAULT_MAX_RUNTIME_SECONDS,
) -> dict[str, Any]:
    """Download one public video and normalize a final MP4 to a 720p-class profile.

    No cookies, credentials, AI provider, D1, MEGA, Rubika API, or publisher are
    accessed here. The final file remains in output_dir for the caller to stage
    after its own policy/owner checks.
    """
    try:
        url = validate_youtube_video_url(source_url)
    except YouTubePolicyError as exc:
        raise YouTubeDownloadError(exc.args[0]) from None
    if type(max_runtime_seconds) is not int or not 60 <= max_runtime_seconds <= DEFAULT_MAX_RUNTIME_SECONDS:
        raise YouTubeDownloadError("youtube_runtime_limit_invalid")

    yt_dlp = shutil.which("yt-dlp")
    ffmpeg = shutil.which("ffmpeg")
    if not yt_dlp:
        raise YouTubeDownloadError("yt_dlp_missing")
    if not ffmpeg:
        raise YouTubeDownloadError("ffmpeg_missing")

    root = Path(output_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve()
    except (OSError, RuntimeError, TypeError):
        raise YouTubeDownloadError("youtube_output_directory_invalid") from None
    if not root.is_dir():
        raise YouTubeDownloadError("youtube_output_directory_invalid")

    metadata = _run_json_process(
        [
            yt_dlp, "--ignore-config", "--no-playlist", "--no-progress", "--no-warnings",
            "--dump-single-json", "--socket-timeout", "20",
            "--retries", "2", "--fragment-retries", "2",
            "--", url,
        ],
        timeout_seconds=MAX_METADATA_SECONDS,
        code_prefix="youtube_metadata",
    )
    video_id = metadata.get("id")
    if not isinstance(video_id, str) or not SAFE_VIDEO_ID.fullmatch(video_id):
        raise YouTubeDownloadError("youtube_video_id_invalid")
    source_width = _positive_int(metadata.get("width"))
    source_height = _positive_int(metadata.get("height"))
    if source_width is None or source_height is None:
        formats = metadata.get("formats")
        format_rows = [f for f in formats if isinstance(f, dict)] if isinstance(formats, list) else []
        video_formats = [
            f for f in format_rows
            if _positive_int(f.get("width")) and _positive_int(f.get("height"))
            and isinstance(f.get("vcodec"), str) and f.get("vcodec") != "none"
        ]
        if not video_formats:
            raise YouTubeDownloadError("youtube_dimensions_missing")
        largest = max(video_formats, key=lambda f: (_positive_int(f.get("height")) or 0, _positive_int(f.get("width")) or 0))
        source_width, source_height = int(largest["width"]), int(largest["height"])

    policy = load_youtube_policy()
    max_width, max_height = _orientation_limits(source_width, source_height, policy)
    try:
        estimated_bytes, estimate_basis = estimate_download_size(
            metadata, max_width=max_width, max_height=max_height
        )
    except YouTubeDownloadError:
        raise
    except Exception:
        raise YouTubeDownloadError("youtube_size_estimate_failed") from None

    try:
        free_disk_bytes = shutil.disk_usage(root).free
    except OSError:
        raise YouTubeDownloadError("youtube_disk_space_unavailable") from None
    budget = assess_download_disk_budget(
        source_size_bytes=estimated_bytes,
        available_disk_bytes=free_disk_bytes,
        policy=policy,
    )
    if budget.get("ok") is not True:
        raise YouTubeDownloadError("youtube_insufficient_disk_space")

    final_path = root / f"{video_id}.720p.mp4"
    if final_path.exists():
        raise YouTubeDownloadError("youtube_output_already_exists")

    download_started = time.monotonic()
    transcode_seconds = 0.0
    with tempfile.TemporaryDirectory(prefix=f".{video_id}-source-", dir=str(root)) as folder:
        work = Path(folder)
        template = str(work / f"{video_id}.source.%(ext)s")
        command = build_ytdlp_command(
            source_url=url,
            output_template=template,
            estimated_size_bytes=estimated_bytes,
            source_width=source_width,
            source_height=source_height,
            policy=policy,
        )
        command[1:1] = ["--socket-timeout", "30"]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max_runtime_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise YouTubeDownloadError("youtube_download_timeout") from None
        except OSError:
            raise YouTubeDownloadError("youtube_download_start_failed") from None
        if completed.returncode != 0:
            # Never return or print raw stderr/stdout; they can include source metadata.
            raise YouTubeDownloadError("youtube_download_failed")

        source_candidates = sorted(
            item for item in work.glob(f"{video_id}.source.*")
            if item.is_file() and item.suffix.lower() in VIDEO_SUFFIXES
        )
        if len(source_candidates) != 1:
            raise YouTubeDownloadError("youtube_download_output_missing_or_ambiguous")
        source_path = source_candidates[0]
        source_info = _probe_video(source_path)
        expected_max_width, expected_max_height = _orientation_limits(
            source_info["width"], source_info["height"], policy
        )
        if (
            source_info["width"] > expected_max_width
            or source_info["height"] > expected_max_height
        ):
            raise YouTubeDownloadError("youtube_download_exceeded_720p_profile")

        ffmpeg_command = build_ffmpeg_normalize_command(
            input_path=source_path,
            output_path=final_path,
            source_width=source_info["width"],
            source_height=source_info["height"],
            policy=policy,
        )
        transcode_started = time.monotonic()
        try:
            normalized = subprocess.run(
                ffmpeg_command,
                capture_output=True,
                text=True,
                timeout=max_runtime_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            final_path.unlink(missing_ok=True)
            raise YouTubeDownloadError("youtube_transcode_timeout") from None
        except OSError:
            final_path.unlink(missing_ok=True)
            raise YouTubeDownloadError("youtube_transcode_start_failed") from None
        transcode_seconds = round(time.monotonic() - transcode_started, 2)
        if normalized.returncode != 0:
            final_path.unlink(missing_ok=True)
            raise YouTubeDownloadError("youtube_transcode_failed")

        try:
            final_info = _probe_video(final_path)
        except YouTubeDownloadError:
            final_path.unlink(missing_ok=True)
            raise
        final_max_width, final_max_height = _orientation_limits(
            source_info["width"], source_info["height"], policy
        )
        if (
            final_info["width"] > final_max_width
            or final_info["height"] > final_max_height
            or final_info["width"] > source_info["width"]
            or final_info["height"] > source_info["height"]
        ):
            final_path.unlink(missing_ok=True)
            raise YouTubeDownloadError("youtube_output_exceeded_720p_or_upscaled")

        upload_guard = check_rubika_upload_size(final_info["size_bytes"], policy=policy)
        return {
            "ok": True,
            "status": "downloaded_and_normalized",
            "video_id": video_id,
            "output_path": str(final_path),
            "source_estimated_size_bytes": estimated_bytes,
            "source_estimate_basis": estimate_basis,
            "disk_budget": budget,
            "download_and_transcode_seconds": round(time.monotonic() - download_started, 2),
            "transcode_seconds": transcode_seconds,
            "source": source_info,
            "output": final_info,
            "rubika_upload_guard": upload_guard,
            "published": False,
            "uploaded": False,
        }
