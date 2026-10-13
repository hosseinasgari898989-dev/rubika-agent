"""Batch staging orchestration for the Python brain.

Discovery and model-based moderation are injected adapters. This module never
silently substitutes a guessed search result or auto-approves an unknown item.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .inspector import inspect_media
from .models import Candidate, Evaluation, InspectionResult
from .normalizer import normalize_video_vertical_1080x1920
from .policy import evaluate_edit_candidate


@dataclass(frozen=True)
class StagedMedia:
    candidate: Candidate
    original_path: str
    publish_path: str
    inspection: InspectionResult
    evaluation: Evaluation
    storage_key: str | None = None
    caption: str = ""


@dataclass
class BatchResult:
    status: str
    required_counts: dict[str, int]
    staged: list[StagedMedia] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def ready_for_review(self) -> bool:
        if self.status != "ready":
            return False
        counts = {platform: 0 for platform in self.required_counts}
        for item in self.staged:
            if item.candidate.platform in counts and item.evaluation.eligible:
                counts[item.candidate.platform] += 1
        return counts == self.required_counts

    @property
    def ready_to_publish(self) -> bool:
        # This branch never sends media to Rubika. Human review/approval and a
        # separately enabled production publisher are required before publishing.
        return False


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return cleaned[:80] or "item"


def stage_edit_batch(
    candidates: Iterable[Candidate],
    *,
    required_counts: Mapping[str, int] | None = None,
    download: Callable[[Candidate], str | Path],
    moderate: Callable[[Candidate, Path], Mapping[str, Any] | None],
    output_dir: str | Path,
    seen_external_ids: set[str] | None = None,
    seen_hashes: set[str] | None = None,
    trusted_source_keys: set[str] | None = None,
    storage: Any | None = None,
    persist_staged: Callable[[StagedMedia], None] | None = None,
    inspector: Callable[..., InspectionResult] = inspect_media,
    normalizer: Callable[..., Mapping[str, object]] = normalize_video_vertical_1080x1920,
) -> BatchResult:
    """Stage a complete batch (default: five TikTok plus five YouTube items).

    Instagram can be included by passing a count for that platform. No publishing
    occurs here. If any target count is missing, status is partial.
    """
    requirements = dict(required_counts or {"tiktok": 5, "youtube": 5})
    if not requirements or any(
        platform not in {"tiktok", "youtube", "instagram"} or count < 0
        for platform, count in requirements.items()
    ):
        raise ValueError("invalid_required_counts")
    requirements = {key: value for key, value in requirements.items() if value > 0}
    if not requirements:
        raise ValueError("at_least_one_source_required")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    seen_ids = set(seen_external_ids or set())
    seen_digests = set(seen_hashes or set())
    remaining = dict(requirements)
    result = BatchResult(status="partial", required_counts=requirements)

    for candidate in candidates:
        platform = candidate.platform
        if platform not in remaining or remaining[platform] <= 0:
            continue
        identity = f"{platform}:{candidate.external_id}"
        if identity in seen_ids or candidate.external_id in seen_ids:
            result.skipped.append({"item": identity, "reason": "duplicate_external_id"})
            continue
        original: Path | None = None
        try:
            original = Path(download(candidate))
            first = inspector(original, "video")
            if not first.ok:
                result.skipped.append({
                    "item": identity,
                    "reason": "media_validation_failed:" + ",".join(first.errors),
                })
                original.unlink(missing_ok=True)
                continue
            if first.sha256 and first.sha256 in seen_digests:
                result.skipped.append({"item": identity, "reason": "duplicate_file_hash"})
                original.unlink(missing_ok=True)
                continue

            moderation = moderate(candidate, original)
            evaluation = evaluate_edit_candidate(
                candidate, first,
                seen_external_ids=seen_ids,
                seen_hashes=seen_digests,
                trusted_source_keys=trusted_source_keys,
                moderation=moderation,
            )
            if not evaluation.eligible:
                if evaluation.reasons:
                    reason = evaluation.reasons[0]
                elif evaluation.requires_review:
                    missing = evaluation.checks.get("moderation_fields_missing", [])
                    reason = (
                        "needs_review_missing_moderation_fields_"
                        + "_".join(str(name) for name in missing)
                        if missing else "needs_review"
                    )
                else:
                    reason = "ineligible"
                result.skipped.append({"item": identity, "reason": reason})
                original.unlink(missing_ok=True)
                continue

            publish_path = original
            final_inspection = first
            if first.width != 1080 or first.height != 1920:
                destination = output / (
                    _safe_component(platform) + "-" +
                    _safe_component(candidate.external_id) + "-vertical1080x1920.mp4"
                )
                normalizer(original, destination)
                publish_path = destination
                final_inspection = inspector(publish_path, "video")
                if not final_inspection.ok or final_inspection.width != 1080 or final_inspection.height != 1920:
                    result.skipped.append({"item": identity, "reason": "normalized_output_invalid"})
                    destination.unlink(missing_ok=True)
                    original.unlink(missing_ok=True)
                    continue

            storage_key = storage.upload(publish_path, "staging") if storage is not None else None
            staged = StagedMedia(
                candidate=candidate,
                original_path=str(original),
                publish_path=str(publish_path),
                inspection=final_inspection,
                evaluation=evaluation,
                storage_key=storage_key,
                caption=candidate.caption,
            )
            if persist_staged is not None:
                persist_staged(staged)
            result.staged.append(staged)
            seen_ids.add(identity)
            if first.sha256:
                seen_digests.add(first.sha256)
            if final_inspection.sha256:
                seen_digests.add(final_inspection.sha256)
            remaining[platform] -= 1
        except Exception as exc:
            result.skipped.append({
                "item": identity,
                "reason": "pipeline_error_" + type(exc).__name__,
            })

    result.status = "ready" if all(count == 0 for count in remaining.values()) else "partial"
    return result
