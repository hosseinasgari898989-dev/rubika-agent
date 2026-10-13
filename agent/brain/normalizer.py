"""Conservative video normalizer: creates a square 1080x1080 copy, never overwrites source."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .inspector import inspect_media


class NormalizeError(RuntimeError):
    pass


def normalize_video_square_1080(
    source: str | Path,
    destination: str | Path,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    timeout_seconds: int = 240,
) -> dict[str, object]:
    source_path = Path(source)
    destination_path = Path(destination)
    if source_path.resolve() == destination_path.resolve():
        raise NormalizeError("source_and_destination_must_differ")

    inspection = inspect_media(source_path, "video", ffprobe=ffprobe)
    if not inspection.ok:
        raise NormalizeError("source_invalid:" + ",".join(inspection.errors))
    if not inspection.supports_square_1080:
        raise NormalizeError("source_too_small_for_true_1080_square")

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source_path), "-map", "0:v:0", "-map", "0:a?",
        "-vf", "scale=1080:1080:force_original_aspect_ratio=increase,crop=1080:1080",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(destination_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout_seconds, check=False)
    except FileNotFoundError as exc:
        raise NormalizeError("ffmpeg_not_installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise NormalizeError("normalization_timeout") from exc
    if result.returncode != 0:
        destination_path.unlink(missing_ok=True)
        raise NormalizeError("normalization_failed")

    output = inspect_media(destination_path, "video", ffprobe=ffprobe)
    if not output.ok or output.width != 1080 or output.height != 1080:
        destination_path.unlink(missing_ok=True)
        raise NormalizeError("normalized_output_validation_failed")
    return {
        "source_sha256": inspection.sha256,
        "output_sha256": output.sha256,
        "output_bytes": output.size_bytes,
        "width": output.width,
        "height": output.height,
        "duration_seconds": output.duration_seconds,
        "has_audio": output.has_audio,
        "audio_codec": output.audio_codec,
    }
