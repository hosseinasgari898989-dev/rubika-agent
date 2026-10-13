"""Cloudflare Clef-omni moderation for synchronized video and soundtrack.

A short, lower-resolution MP4 copy is sent to the authenticated Worker. This is a
preliminary classifier only: D1 review_state stays pending and publishing remains
disabled. Ambiguous model answers are omitted so the policy fails closed.
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
    """Build a bounded MP4 sample and classify visuals plus its actual soundtrack."""

    def __init__(
        self,
        cloud: Any,
        *,
        inspector: Any = inspect_media,
        maximum_seconds: int = 60,
        maximum_bytes: int = 15 * 1024 * 1024,
        minimum_confidence: float = 0.70,
        ffmpeg_timeout_seconds: int = 90,
    ) -> None:
        if not 1 <= maximum_seconds <= 60:
            raise ValueError("maximum_seconds_out_of_range")
        if not 1 * 1024 * 1024 <= maximum_bytes <= 15 * 1024 * 1024:
            raise ValueError("maximum_bytes_out_of_range")
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence_out_of_range")
        self.cloud = cloud
        self.inspector = inspector
        self.maximum_seconds = maximum_seconds
        self.maximum_bytes = maximum_bytes
        self.minimum_confidence = minimum_confidence
        self.ffmpeg_timeout_seconds = max(10, min(180, int(ffmpeg_timeout_seconds)))

    def _prepare_video(self, source: Path, destination: Path) -> None:
        command = [
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(source),
            "-t", str(self.maximum_seconds),
            "-map", "0:v:0", "-map", "0:a?",
            "-vf", "scale='min(640,iw)':-2",
            "-r", "24",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-maxrate", "1000k", "-bufsize", "2000k",
            "-c:a", "aac", "-b:a", "64k", "-ac", "1",
            "-movflags", "+faststart", "-sn", "-dn", "-y", str(destination),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True,
                timeout=self.ffmpeg_timeout_seconds, check=False,
            )
        except FileNotFoundError:
            raise VisionModeratorError("ffmpeg_not_installed") from None
        except subprocess.TimeoutExpired:
            raise VisionModeratorError("moderation_transcode_timeout") from None
        except OSError:
            raise VisionModeratorError("ffmpeg_execution_failed") from None
        if completed.returncode != 0 or not destination.is_file():
            raise VisionModeratorError("moderation_transcode_failed")
        size = destination.stat().st_size
        if size <= 50 * 1024:
            raise VisionModeratorError("moderation_sample_too_small")
        if size > self.maximum_bytes:
            destination.unlink(missing_ok=True)
            raise VisionModeratorError("moderation_sample_size_limit")

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
        truncated = float(source_info.duration_seconds) > self.maximum_seconds

        with tempfile.TemporaryDirectory(prefix="rubika-agent-omni-") as temp:
            sample_path = Path(temp) / "moderation-sample.mp4"
            self._prepare_video(source, sample_path)
            if sample_path.stat().st_size > self.maximum_bytes:
                raise VisionModeratorError("moderation_sample_size_limit")
            encoded = base64.b64encode(sample_path.read_bytes()).decode("ascii")
            data_url = "data:video/mp4;base64," + encoded
            try:
                result = self.cloud.moderate_video(
                    data_url,
                    platform=candidate.platform,
                    title=candidate.title,
                    caption=candidate.caption,
                )
            except Exception as exc:
                code = getattr(exc, "code", "vision_api_" + type(exc).__name__)
                raise VisionModeratorError(str(code)) from None

        if not isinstance(result, Mapping):
            raise VisionModeratorError("vision_api_invalid_response")
        confidence = result.get("vision_confidence")
        if not isinstance(confidence, (float, int)) or not 0 <= confidence <= 1:
            raise VisionModeratorError("vision_confidence_invalid")

        # The model returns independent, conservatively thresholded signals.
        # Missing core fields stop eligibility; quality is required only for
        # sources whose repository policy requests the extra quality gate.
        core_fields = ("has_music", "is_poppy_playtime", "is_edit")
        optional_fields = ("high_quality_edit",)
        known_answers = {
            key: result[key]
            for key in (*core_fields, *optional_fields)
            if type(result.get(key)) is bool
        }
        missing_core = [
            key for key in core_fields if type(result.get(key)) is not bool
        ]
        missing_all = [
            key for key in (*core_fields, *optional_fields)
            if type(result.get(key)) is not bool
        ]
        return {
            **known_answers,
            "vision_confidence": float(confidence),
            "vision_model": str(result.get("vision_model", ""))[:100],
            "moderation_uncertain": bool(missing_core),
            "moderation_missing_fields": missing_all,
            "quality_check_missing": type(result.get("high_quality_edit")) is not bool,
            "aggregate_confidence_below_threshold": confidence < self.minimum_confidence,
            "sample_truncated": truncated,
            "human_review_required": True,
            "auto_approved": False,
            "publishing": False,
        }
