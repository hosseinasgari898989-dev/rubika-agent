"""Conservative two-track media policy: third-party edits vs official Mob posts.

Text heuristics are only signals; visual/auditory judgements require an explicit
moderation result. The default is fail-closed, never pretending that metadata
can prove an edit is high-effort or non-insulting.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .models import Candidate, Evaluation, InspectionResult

RELEVANCE_TERMS = (
    "poppy playtime", "poppyplaytime", "huggy wuggy", "huggywuggy",
    "catnap", "cat nap", "kissy missy", "mommy long legs",
    "smiling critters", "dogday", "chapter 5", "chapter 4",
    "#poppyplaytime", "#huggywuggy", "#catnap",
)
EDIT_TERMS = (
    "edit", "fan edit", "velocity", "amv", "capcut", "edit audio",
    "transition", "sync edit", "phonk edit", "poppy edit",
)
NEGATIVE_TEXT_TERMS = (
    "trash game", "worst game ever", "stupid game", "ugly character",
    "بازی آشغال", "بازی مزخرف", "کاراکتر زشت", "شخصیت احمق",
    "توهین", "افتضاح ترین بازی",
)
REQUIRED_MODERATION_FIELDS = (
    "poppy_relevant", "is_edit", "insults_poppy", "insults_other_games",
    "high_effort", "has_music",
)


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


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

    if candidate.platform not in {"youtube", "tiktok", "instagram"}:
        reasons.append("not_an_edit_source_platform")
    if candidate.source_tier == "official":
        reasons.append("official_media_belongs_to_mob_news_track")
    if candidate.media_type != "video" or inspection.media_type != "video":
        reasons.append("edit_track_requires_video")
    if not inspection.ok:
        reasons.append("media_validation_failed")
        reasons.extend(inspection.errors)
    if candidate.external_id in seen_external_ids:
        reasons.append("duplicate_external_id")
    if inspection.sha256 and inspection.sha256 in seen_hashes:
        reasons.append("duplicate_file_hash")

    trusted = bool(candidate.source_key and candidate.source_key in trusted_source_keys)
    checks["trusted_source"] = trusted
    checks["policy_track"] = "trusted_edit" if trusted else "unknown_source_edit"

    if _has_any(text, NEGATIVE_TEXT_TERMS):
        reasons.append("negative_or_insulting_text_signal")
    checks["metadata_relevance_signal"] = _has_any(text, RELEVANCE_TERMS)
    checks["metadata_edit_signal"] = _has_any(text, EDIT_TERMS)
    if not checks["metadata_relevance_signal"]:
        warnings.append("poppy_relevance_not_proven_by_metadata")
    if not checks["metadata_edit_signal"]:
        warnings.append("edit_style_not_proven_by_metadata")

    if inspection.ok and inspection.media_type == "video":
        if not inspection.supports_square_1080:
            reasons.append("source_cannot_produce_true_1080_square")
        elif not inspection.is_square:
            warnings.append("needs_square_1080_normalization")
        if inspection.has_audio is False:
            warnings.append("no_audio_stream_detected_music_not_confirmed")

    moderation = moderation or {}
    missing = [name for name in REQUIRED_MODERATION_FIELDS if not isinstance(moderation.get(name), bool)]
    checks["moderation_fields_missing"] = missing
    if missing:
        warnings.append("content_review_required")
    else:
        for name in REQUIRED_MODERATION_FIELDS:
            checks[name] = moderation[name]
        if not moderation["poppy_relevant"]:
            reasons.append("content_not_poppy_playtime")
        if not moderation["is_edit"]:
            reasons.append("not_an_edit")
        if moderation["insults_poppy"]:
            reasons.append("insults_poppy_character")
        if moderation["insults_other_games"]:
            reasons.append("insults_other_games_or_fandom")
        if not moderation["high_effort"]:
            reasons.append("low_effort_edit")
        if not moderation["has_music"]:
            warnings.append("music_not_detected")

    # Trusted creators get a reduced metadata gate, not immunity from safety,
    # duplication, file validation, aspect/resolution, or content-review gates.
    if trusted:
        warnings = [w for w in warnings if w not in {
            "poppy_relevance_not_proven_by_metadata", "edit_style_not_proven_by_metadata"
        }]
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
