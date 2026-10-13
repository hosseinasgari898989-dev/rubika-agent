"""Python-first policy for Poppy Playtime fan edits and official Mob posts.

Python handles relevance, known metadata-text risks, deduplication, technical media
validation, and delivery state. Workers AI only answers whether music is audible;
it does not judge edit quality or decide whether a video is an edit.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .models import Candidate, Evaluation, InspectionResult

RELEVANCE_TERMS = (
    "poppy playtime", "poppyplaytime", "huggy wuggy", "huggywuggy",
    "catnap", "cat nap", "kissy missy", "mommy long legs",
    "smiling critters", "dogday", "prototype", "chapter 5", "chapter 4",
    "#poppyplaytime", "#huggywuggy", "#catnap",
    "پاپی پلی تایم", "هاگی واگی", "کت نپ", "کیسی میسی",
)

# Deterministic phrase checks over title/caption/hashtags/source key only.
# This is not OCR or speech recognition; those can be added as local Python stages.
NEGATIVE_TEXT_TERMS = (
    "trash game", "worst game ever", "stupid game", "ugly character",
    "this game sucks", "game is trash", "poppy playtime sucks",
    "poppy playtime is trash", "huggy wuggy sucks", "catnap sucks",
    "kissy missy sucks", "the game is garbage", "hate this game",
    "بازی آشغال", "بازی مزخرف", "این بازی آشغاله", "این بازی مزخرفه",
    "پاپی پلی تایم آشغاله", "پاپی پلی تایم مزخرفه",
    "هاگی واگی زشته", "کت نپ زشته", "شخصیت احمق",
    "افتضاح ترین بازی", "افتضاح‌ترین بازی",
)
REQUIRED_MODERATION_FIELDS = ("has_music",)


def _normalize_text(value: str) -> str:
    value = value.casefold().replace("ي", "ی").replace("ك", "ک")
    value = value.replace("\u200c", " ").replace("\u200f", " ").replace("\u200e", " ")
    return re.sub(r"\s+", " ", value).strip()


def _has_any(text: str, terms: Iterable[str]) -> bool:
    normalized = _normalize_text(text)
    return any(_normalize_text(term) in normalized for term in terms)


def precheck_edit_candidate(candidate: Candidate) -> tuple[str, ...]:
    """Cheap Python-only gate run before downloading or calling Workers AI."""
    text = candidate.searchable_text
    reasons: list[str] = []
    if not _has_any(text, RELEVANCE_TERMS):
        reasons.append("poppy_relevance_not_found_by_python")
    if _has_any(text, NEGATIVE_TEXT_TERMS):
        reasons.append("negative_or_insulting_text_signal")
    return tuple(dict.fromkeys(reasons))


def evaluate_edit_candidate(
    candidate: Candidate,
    inspection: InspectionResult,
    *,
    seen_external_ids: set[str] | None = None,
    seen_hashes: set[str] | None = None,
    trusted_source_keys: set[str] | None = None,
    moderation: Mapping[str, Any] | None = None,
) -> Evaluation:
    seen_external_ids = seen_external_ids or set()
    seen_hashes = seen_hashes or set()
    trusted_source_keys = trusted_source_keys or set()
    reasons: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}
    text = candidate.searchable_text
    metadata_relevant = _has_any(text, RELEVANCE_TERMS)
    text_insult_signal = _has_any(text, NEGATIVE_TEXT_TERMS)

    if candidate.platform not in {"youtube", "tiktok", "instagram"}:
        reasons.append("not_an_edit_source_platform")
    if candidate.source_tier == "official":
        reasons.append("official_media_belongs_to_mob_news_track")
    if candidate.media_type != "video" or inspection.media_type != "video":
        reasons.append("edit_track_requires_video")
    if not inspection.ok:
        reasons.append("media_validation_failed")
        reasons.extend(inspection.errors)
    if candidate.external_id in seen_external_ids or f"{candidate.platform}:{candidate.external_id}" in seen_external_ids:
        reasons.append("duplicate_external_id")
    if inspection.sha256 and inspection.sha256.lower() in {value.lower() for value in seen_hashes}:
        reasons.append("duplicate_file_hash")

    trusted = bool(candidate.source_key and candidate.source_key in trusted_source_keys)
    checks["trusted_source"] = trusted
    checks["policy_track"] = "trusted_poppy_fan_edit" if trusted else "poppy_fan_edit_candidate"
    checks["content_category"] = "poppy_playtime_fan_edit" if metadata_relevant else "unclassified"
    checks["python_metadata_relevance"] = metadata_relevant
    checks["python_text_insult_signal"] = text_insult_signal
    checks["text_scan_scope"] = "title_caption_hashtags_source_key"

    if not metadata_relevant:
        reasons.append("poppy_relevance_not_found_by_python")
    if text_insult_signal:
        reasons.append("negative_or_insulting_text_signal")

    if inspection.ok and inspection.media_type == "video":
        if not inspection.supports_vertical_normalization:
            reasons.append("source_below_minimum_normalization_resolution")
        elif inspection.width != 1080 or inspection.height != 1920:
            warnings.append("needs_vertical_1080x1920_normalization")
        if inspection.has_audio is False:
            checks["has_music"] = False
            checks["music_detection_method"] = "python_no_audio_stream"
            reasons.append("music_not_detected")

    moderation = moderation or {}
    for name in REQUIRED_MODERATION_FIELDS:
        value = moderation.get(name)
        if type(value) is bool:
            checks[name] = value
    missing = [name for name in REQUIRED_MODERATION_FIELDS if type(moderation.get(name)) is not bool]
    checks["moderation_fields_missing"] = missing
    if missing:
        warnings.append("music_detection_review_required")
    elif moderation["has_music"] is False:
        reasons.append("music_not_detected")
        checks["music_detection_method"] = moderation.get("music_detection_method", "workers_ai_audio_check")
    else:
        checks["music_detection_method"] = moderation.get("music_detection_method", "workers_ai_audio_check")

    requires_review = bool(missing)
    if reasons:
        decision = "rejected"
    elif requires_review:
        decision = "needs_review"
    else:
        decision = "eligible"

    score = 100 - min(100, len(reasons) * 25 + len(warnings) * 8)
    return Evaluation(
        decision=decision,
        score=max(0, score),
        reasons=tuple(dict.fromkeys(reasons)),
        warnings=tuple(dict.fromkeys(warnings)),
        requires_review=requires_review,
        checks=checks,
    )


def format_official_mob_caption(
    original_caption: str | None,
    persian_translation: str | None = None,
) -> str | None:
    """Format only supplied text; never invent source captions or translations."""
    original = (original_caption or "").strip()
    translated = (persian_translation or "").strip()
    if not original and not translated:
        return None
    parts = ["ماب انترتینمنت پستی منتشر کرد و گفت:"]
    if original:
        parts.append(original)
    if translated:
        parts.extend(["", "ترجمه متن:", translated])
    return "\n".join(parts)
