"""Policy and dry-run planning helpers for the future YouTube processing lane.

No function here downloads media, calls AI providers, changes D1, uploads files,
or publishes to Rubika. Large downloads are allowed only when the runner's
available disk budget is sufficient. Output is capped at 720-class dimensions:
1280x720 landscape, 720x1280 portrait, or 720x720 square, without upscaling.
Rubika's total upload-size limit remains explicitly unverified until confirmed.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "youtube_pipeline.json"
ALLOWED_VIDEO_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
})
HANDLE_RE = re.compile(r"@[A-Za-z0-9._-]{3,30}")
CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_-]{20,30}")
SAFE_MODEL_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
SUPPORTED_COMPUTE_TYPES = frozenset({"int8", "float32", "float16", "int8_float16", "int8_float32"})


class YouTubePolicyError(ValueError):
    """Safe, stable error code for invalid YouTube policy or input."""


def _int_in_range(value: Any, *, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise YouTubePolicyError(name + "_invalid")
    return value


def _profile_for_dimensions(
    width: int,
    height: int,
    *,
    short_edge: int,
    long_edge: int,
) -> tuple[str, int, int]:
    if width <= 0 or height <= 0:
        raise YouTubePolicyError("youtube_video_dimensions_invalid")
    if width == height:
        return "square", short_edge, short_edge
    if width > height:
        return "landscape", long_edge, short_edge
    return "portrait", short_edge, long_edge


def normalize_channel_key(raw: Any) -> str:
    """Normalize a YouTube handle, channel ID, or canonical channel URL."""
    if not isinstance(raw, str):
        raise YouTubePolicyError("youtube_channel_key_invalid")
    value = raw.strip()
    if not value or len(value) > 300:
        raise YouTubePolicyError("youtube_channel_key_invalid")

    if "://" in value:
        try:
            parsed = urlsplit(value)
        except ValueError:
            raise YouTubePolicyError("youtube_channel_url_invalid") from None
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or host not in ALLOWED_VIDEO_HOSTS - {"youtu.be"}:
            raise YouTubePolicyError("youtube_channel_url_invalid")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise YouTubePolicyError("youtube_channel_url_invalid")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 1 and parts[0].startswith("@"):
            value = parts[0]
        elif len(parts) == 2 and parts[0] == "channel":
            value = parts[1]
        else:
            raise YouTubePolicyError("youtube_channel_url_invalid")

    if value.startswith("@"):
        if not HANDLE_RE.fullmatch(value):
            raise YouTubePolicyError("youtube_channel_handle_invalid")
        return value.lower()
    if CHANNEL_ID_RE.fullmatch(value):
        return value
    if HANDLE_RE.fullmatch("@" + value):
        return "@" + value.lower()
    raise YouTubePolicyError("youtube_channel_key_invalid")


def validate_youtube_video_url(raw_url: Any) -> str:
    """Return a trimmed, safe HTTPS YouTube video URL or raise a stable error."""
    if not isinstance(raw_url, str) or not raw_url.strip() or len(raw_url) > 2048:
        raise YouTubePolicyError("youtube_video_url_invalid")
    value = raw_url.strip()
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        raise YouTubePolicyError("youtube_video_url_invalid") from None
    if (
        parsed.scheme != "https"
        or host not in ALLOWED_VIDEO_HOSTS
        or parsed.username
        or parsed.password
        or not parsed.path
    ):
        raise YouTubePolicyError("youtube_video_url_invalid")

    video_id = None
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif parsed.path == "/watch":
        values = parse_qs(parsed.query, keep_blank_values=False).get("v", [])
        video_id = values[0] if values else None
    else:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
            video_id = parts[1]
    if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise YouTubePolicyError("youtube_video_url_invalid")
    return value


def load_youtube_policy(path: Path | str | None = None) -> dict[str, Any]:
    """Load and strictly validate the checked-in policy without external libraries."""
    source = Path(path) if path is not None else CONFIG_PATH
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise YouTubePolicyError("youtube_policy_unreadable") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise YouTubePolicyError("youtube_policy_schema_invalid")

    chunk_bytes = _int_in_range(
        payload.get("http_chunk_size_bytes"),
        name="http_chunk_size_bytes", minimum=1_000_000, maximum=100_000_000,
    )
    retries = _int_in_range(payload.get("download_retries"), name="download_retries", minimum=0, maximum=10)
    fragment_retries = _int_in_range(
        payload.get("fragment_retries"), name="fragment_retries", minimum=0, maximum=10,
    )
    concurrent = _int_in_range(payload.get("concurrent_fragments"), name="concurrent_fragments", minimum=1, maximum=8)
    reserve = _int_in_range(
        payload.get("minimum_free_disk_reserve_bytes"),
        name="minimum_free_disk_reserve_bytes", minimum=500_000_000, maximum=100_000_000_000,
    )
    multiplier = _int_in_range(
        payload.get("temporary_disk_multiplier_milli"),
        name="temporary_disk_multiplier_milli", minimum=1000, maximum=5000,
    )
    short_edge = _int_in_range(
        payload.get("output_short_edge_max"), name="output_short_edge_max", minimum=144, maximum=1080,
    )
    long_edge = _int_in_range(
        payload.get("output_long_edge_max"), name="output_long_edge_max", minimum=short_edge, maximum=1920,
    )
    chunk_seconds = _int_in_range(
        payload.get("transcription_chunk_seconds"),
        name="transcription_chunk_seconds", minimum=60, maximum=3600,
    )
    max_chunks = _int_in_range(
        payload.get("max_transcription_chunks"),
        name="max_transcription_chunks", minimum=1, maximum=500,
    )

    raw_upload_limit = payload.get("rubika_upload_limit_bytes")
    if raw_upload_limit is not None:
        raw_upload_limit = _int_in_range(
            raw_upload_limit, name="rubika_upload_limit_bytes",
            minimum=1_000_000, maximum=100_000_000_000,
        )

    model = payload.get("whisper_model")
    if not isinstance(model, str) or not SAFE_MODEL_RE.fullmatch(model):
        raise YouTubePolicyError("whisper_model_invalid")
    compute_type = payload.get("whisper_compute_type")
    if compute_type not in SUPPORTED_COMPUTE_TYPES:
        raise YouTubePolicyError("whisper_compute_type_invalid")

    raw_channels = payload.get("persian_subtitle_channels")
    if not isinstance(raw_channels, list):
        raise YouTubePolicyError("persian_subtitle_channels_invalid")
    channels: list[str] = []
    for raw in raw_channels:
        normalized = normalize_channel_key(raw)
        if normalized in channels:
            raise YouTubePolicyError("persian_subtitle_channel_duplicate")
        channels.append(normalized)

    return {
        "schema_version": 1,
        "http_chunk_size_bytes": chunk_bytes,
        "download_retries": retries,
        "fragment_retries": fragment_retries,
        "concurrent_fragments": concurrent,
        "minimum_free_disk_reserve_bytes": reserve,
        "temporary_disk_multiplier_milli": multiplier,
        "output_short_edge_max": short_edge,
        "output_long_edge_max": long_edge,
        "transcription_chunk_seconds": chunk_seconds,
        "max_transcription_chunks": max_chunks,
        "whisper_model": model,
        "whisper_compute_type": compute_type,
        "persian_subtitle_channels": channels,
        "rubika_upload_limit_bytes": raw_upload_limit,
    }


def select_youtube_channels(
    sources: Any,
    *,
    policy: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Select enabled YouTube source rows, annotating per-channel subtitle policy."""
    settings = dict(policy) if policy is not None else load_youtube_policy()
    subtitle_keys = set(settings.get("persian_subtitle_channels", []))
    if not isinstance(sources, list):
        raise YouTubePolicyError("youtube_sources_invalid")

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in sources:
        if not isinstance(row, dict) or row.get("platform") != "youtube":
            continue
        enabled = row.get("enabled") in (1, True) and row.get("enabled") is not False
        if not enabled:
            continue
        source_key = normalize_channel_key(row.get("source_key"))
        if source_key in seen:
            continue
        seen.add(source_key)
        selected.append({
            "source_key": source_key,
            "display_name": str(row.get("display_name") or source_key)[:160],
            "requires_persian_subtitles": source_key in subtitle_keys,
        })
    return selected


def assess_download_disk_budget(
    *,
    source_size_bytes: Any,
    available_disk_bytes: Any,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate safe working space for download + transcode and retain a disk reserve."""
    settings = dict(policy) if policy is not None else load_youtube_policy()
    if type(source_size_bytes) is not int or source_size_bytes <= 0:
        return {"ok": False, "status": "source_size_unknown", "required_bytes": None}
    if type(available_disk_bytes) is not int or available_disk_bytes < 0:
        return {"ok": False, "status": "available_disk_space_unknown", "required_bytes": None}

    estimated_working = math.ceil(
        source_size_bytes * settings["temporary_disk_multiplier_milli"] / 1000
    )
    required = estimated_working + settings["minimum_free_disk_reserve_bytes"]
    return {
        "ok": available_disk_bytes >= required,
        "status": "ok" if available_disk_bytes >= required else "insufficient_disk_space",
        "source_size_bytes": source_size_bytes,
        "available_disk_bytes": available_disk_bytes,
        "estimated_working_bytes": estimated_working,
        "reserve_bytes": settings["minimum_free_disk_reserve_bytes"],
        "required_bytes": required,
    }


def check_rubika_upload_size(
    output_size_bytes: Any,
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail closed while Rubika's total upload cap is unverified; apply a safety margin."""
    settings = dict(policy) if policy is not None else load_youtube_policy()
    if type(output_size_bytes) is not int or output_size_bytes <= 0:
        raise YouTubePolicyError("youtube_output_size_invalid")
    maximum = settings.get("rubika_upload_limit_bytes")
    if type(maximum) is not int or maximum <= 0:
        return {
            "allowed": False,
            "status": "rubika_upload_limit_unverified",
            "output_size_bytes": output_size_bytes,
            "configured_limit_bytes": None,
            "effective_limit_bytes": None,
        }
    effective = maximum * 90 // 100
    return {
        "allowed": output_size_bytes <= effective,
        "status": "ok" if output_size_bytes <= effective else "output_exceeds_rubika_upload_limit",
        "output_size_bytes": output_size_bytes,
        "configured_limit_bytes": maximum,
        "effective_limit_bytes": effective,
    }


def plan_video_processing(
    *,
    source_key: Any,
    metadata: Mapping[str, Any],
    policy: Mapping[str, Any] | None = None,
    channel_enabled: bool = True,
    available_disk_bytes: int | None = None,
) -> dict[str, Any]:
    """Create a deterministic plan without downloading or executing commands."""
    settings = dict(policy) if policy is not None else load_youtube_policy()
    normalized_key = normalize_channel_key(source_key)
    if channel_enabled is not True:
        raise YouTubePolicyError("youtube_source_disabled")
    if not isinstance(metadata, Mapping):
        raise YouTubePolicyError("youtube_metadata_invalid")

    size = metadata.get("filesize")
    if type(size) is not int or size <= 0:
        size = metadata.get("filesize_approx")
    size_bytes = size if type(size) is int and size > 0 else None
    # Estimated size is telemetry for capacity planning, not an arbitrary cutoff.

    width, height = metadata.get("width"), metadata.get("height")
    dimensions_known = type(width) is int and type(height) is int and width > 0 and height > 0
    if dimensions_known:
        orientation, max_width, max_height = _profile_for_dimensions(
            width, height,
            short_edge=settings["output_short_edge_max"],
            long_edge=settings["output_long_edge_max"],
        )
    else:
        # Safe fallback until extractor metadata is available: conventional landscape cap.
        orientation, max_width, max_height = (
            "unknown_landscape_fallback",
            settings["output_long_edge_max"],
            settings["output_short_edge_max"],
        )

    requires_subtitles = normalized_key in set(settings.get("persian_subtitle_channels", []))
    duration = metadata.get("duration")
    duration_seconds = (
        duration if type(duration) in (int, float) and math.isfinite(duration) and duration > 0
        else None
    )
    ranges: list[dict[str, int]] = []
    if requires_subtitles:
        if duration_seconds is None:
            raise YouTubePolicyError("youtube_duration_required_for_subtitles")
        chunk_seconds = settings["transcription_chunk_seconds"]
        count = math.ceil(duration_seconds / chunk_seconds)
        if count > settings["max_transcription_chunks"]:
            raise YouTubePolicyError("youtube_transcription_chunk_limit_exceeded")
        for index in range(count):
            start = index * chunk_seconds
            end = min(math.ceil(duration_seconds), start + chunk_seconds)
            ranges.append({"index": index + 1, "start_seconds": start, "end_seconds": end})

    disk_budget = (
        assess_download_disk_budget(
            source_size_bytes=size_bytes,
            available_disk_bytes=available_disk_bytes,
            policy=settings,
        )
        if available_disk_bytes is not None
        else {
            "ok": False,
            "status": "not_checked",
            "source_size_bytes": size_bytes,
            "required_bytes": None,
        }
    )
    return {
        "source_key": normalized_key,
        "requires_persian_subtitles": requires_subtitles,
        "download": {
            "estimated_size_bytes": size_bytes,
            "has_fixed_total_size_cap": False,
            "http_chunking_enabled": True,
            "http_chunk_size_bytes": settings["http_chunk_size_bytes"],
            "retries": settings["download_retries"],
            "fragment_retries": settings["fragment_retries"],
            "concurrent_fragments": settings["concurrent_fragments"],
            "resume_partial_downloads": True,
            "disk_budget": disk_budget,
        },
        "output": {
            "orientation": orientation,
            "max_width": max_width,
            "max_height": max_height,
            "no_upscaling": True,
            "requires_ffmpeg_normalization": True,
            "rubika_upload_guard": "checked_after_encoding",
        },
        "transcription": {
            "required": requires_subtitles,
            "model": settings["whisper_model"] if requires_subtitles else None,
            "compute_type": settings["whisper_compute_type"] if requires_subtitles else None,
            "chunk_seconds": settings["transcription_chunk_seconds"] if requires_subtitles else None,
            "chunks": ranges,
        },
    }


def _format_selector(width: int | None, height: int | None, settings: Mapping[str, Any]) -> str:
    if type(width) is int and type(height) is int and width > 0 and height > 0:
        _, max_width, max_height = _profile_for_dimensions(
            width, height,
            short_edge=int(settings["output_short_edge_max"]),
            long_edge=int(settings["output_long_edge_max"]),
        )
    else:
        max_width = int(settings["output_long_edge_max"])
        max_height = int(settings["output_short_edge_max"])
    return (
        f"bestvideo[width<={max_width}][height<={max_height}]+bestaudio/"
        f"best[width<={max_width}][height<={max_height}]"
    )


def build_ytdlp_command(
    *,
    source_url: Any,
    output_template: str,
    estimated_size_bytes: int | None = None,
    source_width: int | None = None,
    source_height: int | None = None,
    policy: Mapping[str, Any] | None = None,
) -> list[str]:
    """Build a guarded, resumable 720-class yt-dlp argv vector; never execute it here."""
    url = validate_youtube_video_url(source_url)
    if (
        not isinstance(output_template, str)
        or not output_template.strip()
        or "\n" in output_template
        or "\r" in output_template
        or len(output_template) > 1024
    ):
        raise YouTubePolicyError("youtube_output_template_invalid")
    settings = dict(policy) if policy is not None else load_youtube_policy()
    # estimated_size_bytes is intentionally not a hard-coded download cutoff.
    format_selector = _format_selector(source_width, source_height, settings)
    command = [
        "yt-dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--no-warnings",
        "--continue",
        "--part",
        "--retries", str(settings["download_retries"]),
        "--fragment-retries", str(settings["fragment_retries"]),
        "--concurrent-fragments", str(settings["concurrent_fragments"]),
        "--format", format_selector,
        "--merge-output-format", "mp4",
        "--output", output_template.strip(),
    ]
    # Chunking affects HTTP transfer behavior only; it sets no total-size limit.
    command.extend(["--http-chunk-size", str(settings["http_chunk_size_bytes"])])
    command.extend(["--", url])
    return command


def build_ffmpeg_normalize_command(
    *,
    input_path: str | Path,
    output_path: str | Path,
    source_width: int,
    source_height: int,
    policy: Mapping[str, Any] | None = None,
) -> list[str]:
    """Build a no-upscale MP4 transcode bounded to 720p-class dimensions."""
    settings = dict(policy) if policy is not None else load_youtube_policy()
    if type(source_width) is not int or type(source_height) is not int or source_width <= 0 or source_height <= 0:
        raise YouTubePolicyError("youtube_video_dimensions_invalid")
    try:
        source = Path(input_path).resolve()
        target = Path(output_path).resolve()
    except (OSError, RuntimeError, TypeError):
        raise YouTubePolicyError("youtube_media_path_invalid") from None
    if source == target or target.suffix.lower() != ".mp4":
        raise YouTubePolicyError("youtube_output_path_invalid")

    orientation, max_width, max_height = _profile_for_dimensions(
        source_width, source_height,
        short_edge=settings["output_short_edge_max"],
        long_edge=settings["output_long_edge_max"],
    )
    del orientation
    scale = (
        f"scale='min({max_width},iw)':'min({max_height},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-y",
        "-i", str(source),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-sn", "-dn",
        "-vf", scale,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-f", "mp4",
        str(target),
    ]
