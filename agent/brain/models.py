"""Typed data contracts shared by discovery, inspection, policy and delivery."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PLATFORMS = frozenset({"youtube", "tiktok", "instagram", "mob_official"})
MEDIA_TYPES = frozenset({"video", "image", "audio", "carousel", "unknown"})
SOURCE_TIERS = frozenset({"unknown", "trusted", "official"})


@dataclass(frozen=True)
class Candidate:
    platform: str
    canonical_url: str
    external_id: str
    title: str = ""
    caption: str = ""
    source_key: str = ""
    media_type: str = "unknown"
    source_tier: str = "unknown"
    published_at: str | None = None
    hashtags: tuple[str, ...] = ()
    original_caption: str | None = None
    persian_translation: str | None = None

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise ValueError("unsupported_platform")
        if self.media_type not in MEDIA_TYPES:
            raise ValueError("unsupported_media_type")
        if self.source_tier not in SOURCE_TIERS:
            raise ValueError("unsupported_source_tier")
        if not self.canonical_url.startswith("https://"):
            raise ValueError("https_url_required")
        if not self.external_id.strip():
            raise ValueError("external_id_required")

    @property
    def searchable_text(self) -> str:
        return " ".join((self.title, self.caption, " ".join(self.hashtags), self.source_key)).casefold()


@dataclass(frozen=True)
class InspectionResult:
    ok: bool
    media_type: str
    sha256: str | None
    size_bytes: int
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    has_audio: bool | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def is_square(self) -> bool:
        if not self.width or not self.height:
            return False
        return abs((self.width / self.height) - 1.0) <= 0.02

    @property
    def supports_square_1080(self) -> bool:
        return bool(
            self.ok and self.media_type == "video"
            and self.width and self.height
            and min(self.width, self.height) >= 1080
        )


@dataclass(frozen=True)
class Evaluation:
    decision: str
    score: int
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    requires_review: bool = False
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def eligible(self) -> bool:
        return self.decision == "eligible" and not self.requires_review
