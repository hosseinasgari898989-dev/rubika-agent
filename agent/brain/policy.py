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
    """Only soundtrack music is AI-moderated; all other checks are agent-brain rules."""
    return ("has_music",)


EDIT_SIGNAL_RE = re.compile(
    r"(?<![a-z0-9])(?:edit|edits|edited|editing|aep|amv|montage|velocity|fanedit|sync)(?![a-z0-9])"
)
# Hashtags and mentions are deliberately treated as flexible metadata tokens.
# Examples include #poppyplaytimeedit, #poppy_playtime_edits, @playtime, and @edit.
TAG_TOKEN_RE = re.compile(r"(?<![a-z0-9])[@#]([a-z0-9_][a-z0-9_.-]{0,120})")
EDIT_TAG_MARKERS = ("edit", "aep", "amv", "montage", "velocity", "fanedit", "sync")
COMPACT_RELEVANCE_MARKERS = (
    "poppyplaytime", "huggywuggy", "catnap", "kissymissy",
    "mommylonglegs", "smilingcritters", "dogday", "prototype",
    "chapter4", "chapter5",
)
SUPPORTED_VIDEO_CODECS = frozenset({
    "h264", "avc1", "hevc", "h265", "vp9", "av1", "mpeg4",
})


def _candidate_content_text(candidate: Candidate) -> str:
    # Do not let a creator's username alone prove that a specific upload is relevant or an edit.
    return _normalize_text(" ".join((
        candidate.title, candidate.caption, " ".join(candidate.hashtags)
    )))


def _compact_tag(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _normalize_text(str(value or "")))


def _candidate_tags(candidate: Candidate) -> tuple[str, ...]:
    content = _candidate_content_text(candidate)
    extracted = [match.group(1) for match in TAG_TOKEN_RE.finditer(content)]
    supplied = [
        str(tag) for tag in candidate.hashtags
        if isinstance(tag, str) and tag.strip()
    ]
    return tuple(dict.fromkeys([*supplied, *extracted]))


def _has_poppy_relevance(candidate: Candidate) -> bool:
    """Recognize full names, character names, compound tags, and @playtime-style mentions."""
    content = _candidate_content_text(candidate)
    if _has_any(content, RELEVANCE_TERMS):
        return True

    compact_content = _compact_tag(content)
    if any(marker in compact_content for marker in COMPACT_RELEVANCE_MARKERS):
        return True

    # The short @playtime/#playtime form is accepted as a tag/mention, not as
    # an arbitrary occurrence of the common word "playtime" in ordinary prose.
    return any("playtime" in _compact_tag(tag) for tag in _candidate_tags(candidate))


def _has_edit_signal(candidate: Candidate) -> bool:
    """Accept explicit edit words and editing markers embedded in hashtags or mentions."""
    content = _candidate_content_text(candidate)
    if EDIT_SIGNAL_RE.search(content) is not None:
        return True
    return any(
        any(marker in _compact_tag(tag) for marker in EDIT_TAG_MARKERS)
        for tag in _candidate_tags(candidate)
    )


def _candidate_classification(candidate: Candidate) -> tuple[bool, bool]:
    """Return relevance/edit signals with a lighter, owner-selected creator rule."""
    relevant = _has_poppy_relevance(candidate)
    edit_signal = _has_edit_signal(candidate)
    policy = candidate.source_policy or {}
    known_owner_selected = (
        policy.get("known_source") is True
        and policy.get("trust_tier") == "owner_selected"
    )

    # These six profiles were deliberately selected by the owner for edit quality.
    # For them, either a Poppy/Playtime marker OR an edit marker is enough: do not
    # require both signals to be spelled in a particular way in every caption.
    if known_owner_selected and (relevant or edit_signal):
        return True, True
    return relevant, edit_signal


def _technical_quality_gate(inspection: InspectionResult, minimum_short_side: int) -> bool:
    """Conservative non-AI quality proxy: readable resolution, valid codec, duration and size."""
    if not inspection.ok or inspection.media_type != "video":
        return False
    if not inspection.width or not inspection.height:
        return False
    if min(inspection.width, inspection.height) < minimum_short_side:
        return False
    if not isinstance(inspection.duration_seconds, (int, float)):
        return False
    if not 3 <= inspection.duration_seconds <= 180:
        return False
    if inspection.size_bytes < 50_000:
        return False
    codec = str(inspection.video_codec or "").casefold()
    if codec not in SUPPORTED_VIDEO_CODECS:
        return False
    return inspection.size_bytes / max(float(inspection.duration_seconds), 1.0) >= 10_000


def precheck_edit_candidate(candidate: Candidate) -> tuple[str, ...]:
    """Screen source metadata before download; never infer relevance from creator identity."""
    content = _candidate_content_text(candidate)
    policy = candidate.source_policy or {}
    topic_required = policy.get("require_poppy_relevance_check") is True
    reasons: list[str] = []
    relevant, edit_signal = _candidate_classification(candidate)
    if not relevant:
        reasons.append(
            "poppy_relevance_not_found_by_brain"
            if topic_required else "poppy_relevance_not_found_by_python"
        )
    if _has_any(content, NEGATIVE_TEXT_TERMS):
        reasons.append("negative_or_insulting_text_signal")
    if policy.get("require_edit_signal_check") is True and not edit_signal:
        reasons.append("edit_signal_not_found_by_brain")
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

    content_text = _candidate_content_text(candidate)
    metadata_relevant, edit_signal = _candidate_classification(candidate)
    text_insult_signal = _has_any(content_text, NEGATIVE_TEXT_TERMS)
    policy = candidate.source_policy or {}
    topic_required = policy.get("require_poppy_relevance_check") is True
    edit_required = policy.get("require_edit_signal_check") is True
    quality_required = policy.get("require_brain_quality_check") is True
    minimum_short_side = policy.get("minimum_source_short_side", 480)
    if type(minimum_short_side) is not int or minimum_short_side < 1:
        minimum_short_side = 720

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
    checks["topic_check_method"] = "agent_brain_public_metadata"
    checks["edit_check_method"] = "agent_brain_edit_signal_rules"
    checks["quality_check_method"] = "agent_brain_technical_quality" if quality_required else "not_required_by_source_policy"
    checks["topic_check_required"] = topic_required
    checks["edit_check_required"] = edit_required
    checks["quality_check_required"] = quality_required
    checks["output_format"] = policy.get("output_format", "square_1080x1080")
    checks["text_scan_scope"] = "title_caption_hashtags_only"

    if not metadata_relevant:
        reasons.append(
            "poppy_relevance_not_found_by_brain"
            if topic_required else "poppy_relevance_not_found_by_python"
        )
    if text_insult_signal:
        reasons.append("negative_or_insulting_text_signal")
    if topic_required:
        checks["is_poppy_playtime"] = metadata_relevant
    if edit_required:
        checks["is_edit"] = edit_signal
        if not edit_signal:
            reasons.append("edit_signal_not_found_by_brain")
    if quality_required:
        quality_ok = _technical_quality_gate(inspection, minimum_short_side)
        checks["high_quality_edit"] = quality_ok
        if not quality_ok:
            reasons.append("brain_technical_quality_check_failed")

    if inspection.ok and inspection.media_type == "video":
        width, height = inspection.width, inspection.height
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
    if "has_music" in missing:
        warnings.append("music_detection_review_required")
    if missing:
        warnings.append("required_video_checks_missing")
    if type(moderation.get("has_music")) is bool:
        if moderation["has_music"] is False:
            reasons.append("music_not_detected")
        checks["music_detection_method"] = moderation.get("music_detection_method", "workers_ai_audio_check")
    # Preserve safe diagnostics so uncertainty is actionable rather than reported as a
    # generic missing-field error. No media payloads or provider response bodies are stored.
    for key in (
        "music_probability", "music_decision_reason", "music_detection_method",
        "music_model", "music_confidence", "music_check_uncertain", "recovery_diagnostics",
    ):
        value = moderation.get(key)
        if key == "music_probability" and type(value) in {int, float} and 0 <= value <= 1:
            checks[key] = float(value)
        elif key in {"music_decision_reason", "music_detection_method", "music_model"} and isinstance(value, str):
            checks[key] = re.sub(r"[^A-Za-z0-9_.@/-]", "", value)[:120]
        elif key == "music_confidence" and type(value) in {int, float} and 0 <= value <= 1:
            checks[key] = float(value)
        elif key == "music_check_uncertain" and type(value) is bool:
            checks[key] = value
        elif key == "recovery_diagnostics" and isinstance(value, dict):
            checks[key] = {
                "attempts": max(0, min(2, int(value.get("attempts", 0) or 0))),
                "recovered": value.get("recovered") is True,
                "action": re.sub(r"[^A-Za-z0-9_-]", "", str(value.get("action", "")))[:80],
                "error_codes": [
                    re.sub(r"[^A-Za-z0-9_-]", "", str(code))[:80]
                    for code in value.get("error_codes", [])[:3]
                ] if isinstance(value.get("error_codes"), list) else [],
            }

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
