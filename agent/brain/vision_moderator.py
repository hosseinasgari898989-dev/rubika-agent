"""Conservative contact-sheet moderation through the authenticated Cloudflare AI endpoint.

This model produces preliminary visual signals only. It cannot hear music, and its
answers are not publication approval. Without an independent audio/music checker,
the required has_music decision is deliberately omitted so the policy remains closed.
"""
from __future__ import annotations

import base64
import io
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image, ImageDraw, ImageOps

from .inspector import inspect_media
from .models import Candidate


class VisionModeratorError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


FrameInspector = Callable[..., Any]
AudioModerator = Callable[[Path], bool | None]


class CloudflareVisionModerator:
    """Extract a four-frame contact sheet and request preliminary visual moderation."""

    def __init__(
        self,
        cloud: Any,
        *,
        inspector: FrameInspector = inspect_media,
        audio_moderator: AudioModerator | None = None,
        minimum_confidence: float = 0.70,
        frame_timeout_seconds: int = 20,
    ) -> None:
        if not 0 <= minimum_confidence <= 1:
            raise ValueError("minimum_confidence_out_of_range")
        self.cloud = cloud
        self.inspector = inspector
        self.audio_moderator = audio_moderator
        self.minimum_confidence = minimum_confidence
        self.frame_timeout_seconds = max(5, min(45, int(frame_timeout_seconds)))

    @staticmethod
    def _make_contact_sheet(frame_paths: list[Path]) -> bytes:
        if len(frame_paths) < 2:
            raise VisionModeratorError("too_few_video_frames")
        tile_size = 480
        sheet = Image.new("RGB", (tile_size * 2, tile_size * 2), "black")
        draw = ImageDraw.Draw(sheet)
        positions = ((0, 0), (tile_size, 0), (0, tile_size), (tile_size, tile_size))
        loaded = 0
        for index, frame_path in enumerate(frame_paths[:4]):
            try:
                with Image.open(frame_path) as opened:
                    tile = ImageOps.fit(opened.convert("RGB"), (tile_size, tile_size))
            except (OSError, ValueError):
                continue
            x, y = positions[index]
            sheet.paste(tile, (x, y))
            draw.rectangle((x, y, x + 138, y + 24), fill="black")
            draw.text((x + 6, y + 5), f"Sample frame {index + 1}", fill="white")
            loaded += 1
        if loaded < 2:
            raise VisionModeratorError("contact_sheet_frame_decode_failed")
        output = io.BytesIO()
        sheet.save(output, format="JPEG", quality=72, optimize=True)
        result = output.getvalue()
        if not 500 <= len(result) <= 1_200_000:
            raise VisionModeratorError("contact_sheet_size_invalid")
        return result

    def _extract_frames(self, path: Path, duration: float, folder: Path) -> list[Path]:
        frames: list[Path] = []
        for index, fraction in enumerate((0.12, 0.36, 0.62, 0.88), start=1):
            timestamp = max(0.0, min(duration - 0.05, duration * fraction))
            destination = folder / f"frame-{index}.jpg"
            command = [
                "ffmpeg", "-nostdin", "-v", "error",
                "-ss", f"{timestamp:.3f}", "-i", str(path),
                "-frames:v", "1",
                "-vf", "scale=480:480:force_original_aspect_ratio=decrease,"
                       "pad=480:480:(ow-iw)/2:(oh-ih)/2",
                "-q:v", "4", "-y", str(destination),
            ]
            try:
                completed = subprocess.run(
                    command, capture_output=True, text=True,
                    timeout=self.frame_timeout_seconds, check=False,
                )
            except FileNotFoundError:
                raise VisionModeratorError("ffmpeg_not_installed") from None
            except subprocess.TimeoutExpired:
                continue
            except OSError:
                raise VisionModeratorError("ffmpeg_execution_failed") from None
            if completed.returncode == 0 and destination.is_file() and destination.stat().st_size:
                frames.append(destination)
        if len(frames) < 2:
            raise VisionModeratorError("video_frame_extraction_failed")
        return frames

    def __call__(self, candidate: Candidate, media_path: str | Path) -> Mapping[str, Any]:
        if candidate.platform not in {"youtube", "tiktok", "instagram"}:
            raise VisionModeratorError("unsupported_vision_platform")
        path = Path(media_path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise VisionModeratorError("media_file_missing_or_empty")

        inspection = self.inspector(path, "video")
        if not inspection.ok or inspection.media_type != "video":
            raise VisionModeratorError("video_inspection_failed")
        duration = inspection.duration_seconds
        if not isinstance(duration, (float, int)) or duration <= 0:
            raise VisionModeratorError("video_duration_unavailable")

        with tempfile.TemporaryDirectory(prefix="rubika-agent-vision-") as temp:
            frame_paths = self._extract_frames(path, float(duration), Path(temp))
            sheet = self._make_contact_sheet(frame_paths)
            image_data_url = "data:image/jpeg;base64," + base64.b64encode(sheet).decode("ascii")
            try:
                result = self.cloud.moderate_frame(
                    image_data_url,
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

        moderation: dict[str, Any] = {
            "vision_confidence": float(confidence),
            "vision_model": str(result.get("vision_model", ""))[:100],
            "human_review_required": True,
            "audio_review_required": True,
        }
        required_visual = (
            "poppy_relevant", "is_edit", "insults_poppy",
            "insults_other_games", "high_effort",
        )
        # Low-confidence guesses are withheld, which keeps the downstream policy fail-closed.
        if confidence >= self.minimum_confidence:
            for key in required_visual:
                if type(result.get(key)) is bool:
                    moderation[key] = result[key]

        if self.audio_moderator is not None:
            try:
                has_music = self.audio_moderator(path)
            except Exception:
                has_music = None
            if type(has_music) is bool:
                moderation["has_music"] = has_music
                moderation["audio_review_required"] = False

        # No is_edit/high_effort/music claims are fabricated if the model or audio
        # checker does not provide the requested signal.
        return moderation
