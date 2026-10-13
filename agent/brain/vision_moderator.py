"""Audio-only music moderation through the authenticated Cloudflare Worker.

The agent brain, not AI, performs metadata relevance/edit checks and technical
quality validation. This adapter sends a bounded MP3 soundtrack sample only.
"""
from __future__ import annotations

import base64
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

from .inspector import inspect_media
from .models import Candidate


class VisionModeratorError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class CloudflareVideoModerator:
    """Legacy class name retained; the model sees soundtrack audio only."""

    def __init__(
        self,
        cloud: Any,
        *,
        inspector: Any = inspect_media,
        maximum_seconds: int = 60,
        maximum_bytes: int = 8 * 1024 * 1024,
        minimum_confidence: float = 0.70,
        ffmpeg_timeout_seconds: int = 90,
    ) -> None:
        if not 1 <= maximum_seconds <= 60:
            raise ValueError("maximum_seconds_out_of_range")
        if not 64 * 1024 <= maximum_bytes <= 8 * 1024 * 1024:
            raise ValueError("maximum_bytes_out_of_range")
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence_out_of_range")
        self.cloud = cloud
        self.inspector = inspector
        self.maximum_seconds = maximum_seconds
        self.maximum_bytes = maximum_bytes
        # Kept for constructor compatibility/diagnostics; the Worker owns the
        # authoritative music decision threshold.
        self.minimum_confidence = minimum_confidence
        self.ffmpeg_timeout_seconds = max(10, min(180, int(ffmpeg_timeout_seconds)))

    def _prepare_audio(self, source: Path, destination: Path) -> None:
        command = [
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(source),
            "-t", str(self.maximum_seconds),
            "-map", "0:a:0", "-vn",
            "-ac", "1", "-ar", "16000",
            "-c:a", "libmp3lame", "-b:a", "48k",
            "-f", "mp3", "-y", str(destination),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=self.ffmpeg_timeout_seconds, check=False,
            )
        except FileNotFoundError:
            raise VisionModeratorError("ffmpeg_not_installed") from None
        except subprocess.TimeoutExpired:
            raise VisionModeratorError("music_transcode_timeout") from None
        except OSError:
            raise VisionModeratorError("ffmpeg_execution_failed") from None
        if completed.returncode != 0 or not destination.is_file():
            raise VisionModeratorError("music_transcode_failed")
        size = destination.stat().st_size
        if size < 1024:
            destination.unlink(missing_ok=True)
            raise VisionModeratorError("music_sample_too_small")
        if size > self.maximum_bytes:
            destination.unlink(missing_ok=True)
            raise VisionModeratorError("music_sample_size_limit")

    def __call__(self, candidate: Candidate, media_path: str | Path) -> Mapping[str, Any]:
        if candidate.platform not in {"youtube", "tiktok", "instagram"}:
            raise VisionModeratorError("unsupported_moderation_platform")
        source = Path(media_path)
        if not source.is_file() or source.stat().st_size <= 0:
            raise VisionModeratorError("media_file_missing_or_empty")

        source_info = self.inspector(source, "video")
        if not source_info.ok or source_info.media_type != "video":
            raise VisionModeratorError("video_inspection_failed")
        if not isinstance(source_info.duration_seconds, (float, int)) or source_info.duration_seconds <= 0:
            raise VisionModeratorError("video_duration_unavailable")
        if source_info.has_audio is False:
            return {
                "has_music": False,
                "music_probability": 0.0,
                "music_decision_reason": "source_has_no_audio_stream",
                "music_detection_method": "python_no_audio_stream",
                "music_check_uncertain": False,
                "human_review_required": True,
                "auto_approved": False,
                "publishing": False,
            }

        with tempfile.TemporaryDirectory(prefix="rubika-agent-music-") as temp:
            sample_path = Path(temp) / "soundtrack.mp3"
            self._prepare_audio(source, sample_path)
            encoded = base64.b64encode(sample_path.read_bytes()).decode("ascii")
            data_url = "data:audio/mpeg;base64," + encoded
            try:
                result = self.cloud.moderate_music(
                    data_url,
                    platform=candidate.platform,
                )
            except Exception as exc:
                code = getattr(exc, "code", "music_api_" + type(exc).__name__)
                raise VisionModeratorError(str(code)) from None

        if not isinstance(result, Mapping):
            raise VisionModeratorError("music_api_invalid_response")
        probability = result.get("music_probability")
        if probability is not None and (
            type(probability) not in {int, float} or not 0 <= probability <= 1
        ):
            raise VisionModeratorError("music_probability_invalid")

        output: dict[str, Any] = {
            "music_probability": float(probability) if probability is not None else None,
            "music_decision_reason": str(result.get("music_decision_reason", "music_diagnostic_missing"))[:80],
            "music_detection_method": str(result.get("music_detection_method", "cloudflare_clef_omni_audio"))[:80],
            "music_model": str(result.get("music_model", ""))[:100],
            "music_confidence": float(result.get("music_confidence", 0.0)),
            "music_check_uncertain": type(result.get("has_music")) is not bool,
            "human_review_required": True,
            "auto_approved": False,
            "publishing": False,
        }
        if type(result.get("has_music")) is bool:
            output["has_music"] = result["has_music"]
        return output
