"""Policy for Poppy Playtime fan edits, source trust and delivery format.

Python owns duplicate/technical checks. Workers AI checks actual visuals and audio;
the publishing gate fails closed when a required signal is missing or uncertain.
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


def _normalize_text(value: str) -> str:
    value = value.casefold().replace("ي", "ی").replace("ك", "ک")
    value = value.replace("\u200c", " ").replace("\u200f", " ").replace("\u200e", " ")
    return re.sub(r"\s+", " ", value).strip()


def _has_any(text: str, terms: Iterable[str]) -> bool:
    normalized = _normalize_text(text)
    return any(_normalize_text(term) in normalized for term in terms)


def required_moderation_fields(candidate: Candidate) -> tuple[str, ...]:
    """Signals required for this candidate according to trusted-source policy."""
    policy = candidate.source_policy or {}
    fields = ["has_music"]
    if policy.get("require_visual_poppy_check") is True:
        fields.append("is_poppy_playtime")
    if policy.get("require_visual_edit_check") is True:
        fields.append("is_edit")
    if policy.get("require_ai_quality_check") is True:
        fields.append("high_quality_edit")
    return tuple(fields)


def precheck_edit_candidate(candidate: Candidate) -> tuple[str, ...]:
    """Cheap metadata check before download; actual video signals are checked later."""
    text = candidate.searchable_text
    reasons: list[str] = []
    visual_topic_check = bool((candidate.source_policy or {}).get("require_visual_poppy_check"))
    if not _has_any(text, RELEVANCE_TERMS) and not visual_topic_check:
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
    moderation = moderation or {}
    policy = candidate.source_policy or {}
    required_fields = required_moderation_fields(candidate)
    reasons: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    text = candidate.searchable_text
    metadata_relevant = _has_any(text, RELEVANCE_TERMS)
    text_insult_signal = _has_any(text, NEGATIVE_TEXT_TERMS)
    visual_topic_required = policy.get("require_visual_poppy_check") is True
    visual_edit_required = policy.get("require_visual_edit_check") is True
    quality_required = policy.get("require_ai_quality_check") is True
    visual_poppy = moderation.get("is_poppy_playtime")
    visual_edit = moderation.get("is_edit")
    quality_ok = moderation.get("high_quality_edit")

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

    trusted = bool(
        policy.get("trust_tier") == "owner_selected"
        or (candidate.source_key and candidate.source_key in trusted_source_keys)
    )
    checks["trusted_source"] = trusted
    checks["policy_track"] = "owner_selected_creator" if trusted else "unknown_creator_full_review"
    checks["python_metadata_relevance"] = metadata_relevant
    checks["python_text_insult_signal"] = text_insult_signal
    checks["visual_topic_check_required"] = visual_topic_required
    checks["visual_edit_check_required"] = visual_edit_required
    checks["quality_check_required"] = quality_required
    checks["output_format"] = policy.get("output_format", "square_1080x1080")
    checks["text_scan_scope"] = "title_caption_hashtags_source_key"

    if not metadata_relevant and not visual_topic_required:
        reasons.append("poppy_relevance_not_found_by_python")
    if text_insult_signal:
        reasons.append("negative_or_insulting_text_signal")
    if visual_topic_required and type(visual_poppy) is bool:
        checks["is_poppy_playtime"] = visual_poppy
        if not visual_poppy:
            reasons.append("visual_content_not_poppy_playtime")
    if visual_edit_required and type(visual_edit) is bool:
        checks["is_edit"] = visual_edit
        if not visual_edit:
            reasons.append("visual_content_not_an_edit")
    if quality_required and type(quality_ok) is bool:
        checks["high_quality_edit"] = quality_ok
        if not quality_ok:
            reasons.append("visual_edit_quality_below_policy")

    if inspection.ok and inspection.media_type == "video":
        width, height = inspection.width, inspection.height
        minimum_short_side = policy.get("minimum_source_short_side", 480)
        if type(minimum_short_side) is not int or minimum_short_side < 1:
            minimum_short_side = 720
        if not width or not height or min(width, height) < minimum_short_side:
            reasons.append("source_below_policy_minimum_resolution")
        output_format = policy.get("output_format", "square_1080x1080")
        if output_format == "square_1080x1080":
            if not inspection.supports_square_normalization:
                reasons.append("source_below_minimum_square_normalization_resolution")
            elif width != 1080 or height != 1080:
                warnings.append("needs_square_1080x1080_normalization")
        elif output_format != "preserve_source":
            reasons.append("unsupported_output_format_policy")
        if inspection.has_audio is False:
            checks["has_music"] = False
            checks["music_detection_method"] = "python_no_audio_stream"
            reasons.append("music_not_detected")

    for name in required_fields:
        value = moderation.get(name)
        if type(value) is bool:
            checks[name] = value
    missing = [
        name for name in required_fields
        if type(moderation.get(name)) is not bool
        and not (name == "has_music" and checks.get("has_music") is False)
    ]
    checks["moderation_fields_missing"] = missing
    if missing:
        warnings.append("required_video_checks_missing")
    if type(moderation.get("has_music")) is bool:
        if moderation["has_music"] is False:
            reasons.append("music_not_detected")
        checks["music_detection_method"] = moderation.get("music_detection_method", "workers_ai_audio_check")
    if visual_topic_required and type(visual_poppy) is not bool:
        warnings.append("visual_poppy_classification_review_required")
    if visual_edit_required and type(visual_edit) is not bool:
        warnings.append("visual_edit_classification_review_required")
    if quality_required and type(quality_ok) is not bool:
        warnings.append("visual_quality_classification_review_required")

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
