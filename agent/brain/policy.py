"""TikTok edit policy: selected sources, configured caption keywords, and media basics."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from agent.source_policy import has_required_any_keyword, required_any_keywords, resolve_source_policy

from .models import Candidate, Evaluation, InspectionResult


def required_moderation_fields(candidate: Candidate) -> tuple[str, ...]:
    """Workers AI remains responsible only for the existing soundtrack music check."""
    return ("has_music",)


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
    value = value.replace("\\u200c", " ").replace("\\u200f", " ").replace("\\u200e", " ")
    return re.sub(r"\\s+", " ", value).strip()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    normalized = _normalize_text(text)
    return any(_normalize_text(term) in normalized for term in terms)


def _candidate_content_text(candidate: Candidate) -> str:
    return _normalize_text(" ".join((candidate.title, candidate.caption, " ".join(candidate.hashtags))))


def _policy_for(candidate: Candidate) -> dict[str, Any]:
    if candidate.platform == "tiktok":
        return resolve_source_policy("tiktok", candidate.source_key)
    supplied = candidate.source_policy or {}
    return dict(supplied) if supplied else resolve_source_policy(candidate.platform, candidate.source_key)


def _has_poppy_relevance(candidate: Candidate) -> bool:
    # Retained for non-TikTok legacy metadata discovery; TikTok uses the exact
    # configurable caption+hashtags substring rule below.
    text = _candidate_content_text(candidate)
    terms = (
        "poppy playtime", "poppyplaytime", "playtime", "huggy wuggy", "huggywuggy",
        "catnap", "cat nap", "kissy missy", "mommy long legs", "dogday",
        "smiling critters", "smilingcritters", "nightmare critters", "prototype",
    )
    return any(term in text for term in terms)


def _tiktok_keyword_match(candidate: Candidate) -> bool:
    return has_required_any_keyword(candidate.caption, candidate.hashtags)


def precheck_edit_candidate(candidate: Candidate) -> tuple[str, ...]:
    """Fail closed on unselected TikTok handles and apply only the selected-channel keyword rule."""
    reasons: list[str] = []
    if candidate.platform == "tiktok":
        source_policy = _policy_for(candidate)
        if source_policy.get("known_source") is not True or source_policy.get("enabled") is not True:
            return ("source_not_selected",)
        if not _tiktok_keyword_match(candidate):
            reasons.append("required_keyword_missing")
    elif not _has_poppy_relevance(candidate):
        reasons.append("poppy_relevance_not_found_by_python")
    if _has_any(_candidate_content_text(candidate), NEGATIVE_TEXT_TERMS):
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
    required_fields = required_moderation_fields(candidate)
    policy = _policy_for(candidate)
    reasons: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

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

    if candidate.platform == "tiktok":
        selected = policy.get("known_source") is True and policy.get("enabled") is True
        keyword_match = _tiktok_keyword_match(candidate)
        checks["selected_tiktok_source"] = selected
        checks["required_keyword_match"] = keyword_match
        checks["required_any_keywords"] = list(required_any_keywords())
        checks["policy_track"] = "selected_tiktok_channel" if selected else "unselected_tiktok_channel"
        if not selected:
            reasons.append("source_not_selected")
        elif not keyword_match:
            reasons.append("required_keyword_missing")
    else:
        relevant = _has_poppy_relevance(candidate)
        checks["python_metadata_relevance"] = relevant
        if not relevant:
            reasons.append("poppy_relevance_not_found_by_python")
        checks["policy_track"] = "non_tiktok_metadata"

    text_insult_signal = _has_any(_candidate_content_text(candidate), NEGATIVE_TEXT_TERMS)
    checks["python_text_insult_signal"] = text_insult_signal
    if text_insult_signal:
        reasons.append("negative_or_insulting_text_signal")

    minimum_short_side = policy.get("minimum_source_short_side", 480)
    if type(minimum_short_side) is not int or minimum_short_side < 1:
        minimum_short_side = 480
    checks["minimum_source_short_side"] = minimum_short_side
    checks["output_format"] = policy.get("output_format", "square_1080x1080")

    if inspection.ok and inspection.media_type == "video":
        width, height = inspection.width, inspection.height
        if not width or not height or min(width, height) < minimum_short_side:
            reasons.append("source_below_policy_minimum_resolution")
        if not isinstance(inspection.duration_seconds, (int, float)) or not 3 <= inspection.duration_seconds <= 180:
            reasons.append("media_duration_out_of_range")
        if inspection.size_bytes < 50_000:
            reasons.append("media_size_below_minimum")
        output_format = policy.get("output_format", "square_1080x1080")
        if output_format == "square_1080x1080":
            if not inspection.supports_square_normalization:
                reasons.append("source_below_minimum_square_normalization_resolution")
            elif width != 1080 or height != 1080:
                warnings.append("square_normalization_skipped_no_upscale" if min(width, height) < 1080 else "needs_square_1080x1080_normalization")
        elif output_format != "preserve_source":
            reasons.append("unsupported_output_format_policy")

        if inspection.has_audio is False:
            checks["has_audio"] = False
            checks["has_music"] = False
            checks["music_detection_method"] = "ffprobe_audio_stream_check"
            reasons.append("no_audio")
        elif inspection.has_audio is True:
            checks["has_audio"] = True

    for name in required_fields:
        value = moderation.get(name)
        if name == "has_music" and inspection.has_audio is False:
            continue
        if type(value) is bool:
            checks[name] = value
    missing = [
        name for name in required_fields
        if type(moderation.get(name)) is not bool
        and not (name == "has_music" and (checks.get("has_music") is False or inspection.has_audio is False))
    ]
    checks["moderation_fields_missing"] = missing
    if "has_music" in missing:
        warnings.append("music_detection_review_required")
    if missing:
        warnings.append("required_video_checks_missing")
    if type(moderation.get("has_music")) is bool:
        if moderation["has_music"] is False and inspection.has_audio is not False:
            reasons.append("music_not_detected")
        if inspection.has_audio is not False:
            checks["music_detection_method"] = moderation.get("music_detection_method", "workers_ai_audio_check")

    for key in ("music_probability", "music_decision_reason", "music_detection_method", "music_model", "music_confidence", "music_check_uncertain", "recovery_diagnostics"):
        value = moderation.get(key)
        if key in {"music_probability", "music_confidence"} and type(value) in {int, float} and 0 <= value <= 1:
            checks[key] = float(value)
        elif key in {"music_decision_reason", "music_detection_method", "music_model"} and isinstance(value, str):
            checks[key] = re.sub(r"[^A-Za-z0-9_.@/-]", "", value)[:120]
        elif key == "music_check_uncertain" and type(value) is bool:
            checks[key] = value
        elif key == "recovery_diagnostics" and isinstance(value, dict):
            checks[key] = {
                "attempts": max(0, min(2, int(value.get("attempts", 0) or 0))),
                "recovered": value.get("recovered") is True,
                "action": re.sub(r"[^A-Za-z0-9_-]", "", str(value.get("action", "")))[:80],
                "error_codes": [re.sub(r"[^A-Za-z0-9_-]", "", str(code))[:80] for code in value.get("error_codes", [])[:3]]
                if isinstance(value.get("error_codes"), list) else [],
            }

    requires_review = bool(missing)
    decision = "rejected" if reasons else "needs_review" if requires_review else "eligible"
    score = 100 - min(100, len(reasons) * 25 + len(warnings) * 8)
    return Evaluation(
        decision=decision, score=max(0, score), reasons=tuple(dict.fromkeys(reasons)),
        warnings=tuple(dict.fromkeys(warnings)), requires_review=requires_review, checks=checks,
    )


def format_official_mob_caption(original_caption: str | None, persian_translation: str | None = None) -> str | None:
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
