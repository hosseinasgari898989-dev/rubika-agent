"""Separate, lighter validation track for verified official Mob Entertainment news."""
from __future__ import annotations

from .models import Candidate, Evaluation, InspectionResult


def evaluate_official_media(
    candidate: Candidate,
    inspection: InspectionResult,
    *,
    seen_external_ids: set[str] | None = None,
    seen_hashes: set[str] | None = None,
) -> Evaluation:
    """Validate official source track, actual media type, file integrity and duplicates."""
    seen_external_ids = seen_external_ids or set()
    seen_hashes = seen_hashes or set()
    reasons: list[str] = []
    if candidate.platform != "mob_official" or candidate.source_tier != "official":
        reasons.append("source_not_marked_official")
    identity = "mob_official:" + candidate.external_id
    if identity in seen_external_ids or candidate.external_id in seen_external_ids:
        reasons.append("duplicate_external_id")
    if not inspection.ok:
        reasons.append("media_validation_failed")
        reasons.extend(inspection.errors)
    if inspection.sha256 and inspection.sha256 in seen_hashes:
        reasons.append("duplicate_file_hash")
    if candidate.media_type == "carousel":
        reasons.append("carousel_requires_album_adapter")
    elif candidate.media_type not in {"video", "image", "audio"}:
        reasons.append("unsupported_official_media_type")
    elif inspection.media_type != candidate.media_type:
        reasons.append("media_type_mismatch")

    decision = "rejected" if reasons else "eligible"
    return Evaluation(
        decision=decision,
        score=max(0, 100 - len(reasons) * 30),
        reasons=tuple(dict.fromkeys(reasons)),
        warnings=(),
        requires_review=False,
        checks={
            "track": "official_mob_news",
            "media_type": candidate.media_type,
            "content_filters_applied": False,
        },
    )
