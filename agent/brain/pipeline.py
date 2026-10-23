"""Batch staging orchestration for the Python brain.

Discovery and model-based moderation are injected adapters. This module never
silently substitutes a guessed search result or auto-approves an unknown item.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .inspector import inspect_media
from .models import Candidate, Evaluation, InspectionResult
from .normalizer import normalize_video_square_1080
from agent.source_policy import allow_upscale
from .policy import evaluate_edit_candidate, precheck_edit_candidate
from .recovery import call_with_bounded_retry, recovery_guidance, safe_error_code


@dataclass(frozen=True)
class StagedMedia:
    candidate: Candidate
    original_path: str
    publish_path: str
    inspection: InspectionResult
    evaluation: Evaluation
    storage_key: str | None = None
    caption: str = ""
    downloaded_inspection: InspectionResult | None = None


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
    normalizer: Callable[..., Mapping[str, object]] = normalize_video_square_1080,
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
        local_reasons = precheck_edit_candidate(candidate)
        if local_reasons:
            result.skipped.append({"item": identity, "reason": local_reasons[0]})
            continue
        original: Path | None = None
        moderation: Mapping[str, Any] = {}
        recovery_receipt: dict[str, Any] = {
            "attempts": 0, "recovered": False, "error_codes": [], "action": "no_moderation_attempt"
        }
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

            # A missing audio stream is a deterministic Python result; do not
            # spend a Workers AI request on a video that cannot contain music.
            if first.has_audio is False:
                moderation = {
                    "has_music": False,
                    "music_detection_method": "python_no_audio_stream",
                }
            else:
                moderation_value, recovery_receipt = call_with_bounded_retry(
                    lambda: moderate(candidate, original),
                    max_attempts=2,
                    sleep_seconds=0.75,
                )
                moderation = dict(moderation_value or {})
                moderation["recovery_diagnostics"] = recovery_receipt
            evaluation = evaluate_edit_candidate(
                candidate, first,
                seen_external_ids=seen_ids,
                seen_hashes=seen_digests,
                trusted_source_keys=trusted_source_keys,
                moderation=moderation,
            )
            if recovery_receipt.get("recovered"):
                updated_checks = dict(evaluation.checks)
                updated_checks["moderation_recovery"] = recovery_receipt
                evaluation = replace(
                    evaluation,
                    warnings=tuple(dict.fromkeys((*evaluation.warnings, "music_moderation_recovered_after_retry"))),
                    checks=updated_checks,
                )
            if not evaluation.eligible:
                if evaluation.reasons:
                    reason = evaluation.reasons[0]
                elif evaluation.requires_review:
                    missing = evaluation.checks.get("moderation_fields_missing", [])
                    if "has_music" in missing and moderation.get("music_decision_reason"):
                        reason = str(moderation["music_decision_reason"])
                        reason = re.sub(r"[^A-Za-z0-9_-]", "", reason)[:80] or "music_answer_missing"
                    else:
                        reason = (
                            "needs_review_missing_moderation_fields_"
                            + "_".join(str(name) for name in missing)
                            if missing else "needs_review"
                        )
                else:
                    reason = "ineligible"
                skipped_row: dict[str, Any] = {"item": identity, "reason": reason}
                for key in (
                    "music_probability", "music_decision_reason", "music_detection_method",
                    "music_model", "music_confidence", "music_check_uncertain",
                ):
                    value = moderation.get(key)
                    if value is not None:
                        skipped_row[key] = str(value)[:120]
                if recovery_receipt.get("attempts"):
                    skipped_row["recovery_action"] = str(recovery_receipt.get("action", ""))[:80]
                    skipped_row["recovery_attempts"] = str(recovery_receipt.get("attempts", 0))
                    skipped_row["recovery_error_codes"] = ",".join(
                        str(code)[:80] for code in recovery_receipt.get("error_codes", [])[:3]
                    )
                result.skipped.append(skipped_row)  # type: ignore[arg-type]
                original.unlink(missing_ok=True)
                continue

            policy = candidate.source_policy or {}
            output_format = policy.get("output_format", "square_1080x1080")
            publish_path = original
            final_inspection = first
            if output_format == "square_1080x1080" and (first.width != 1080 or first.height != 1080):
                if (
                    not allow_upscale()
                    and first.width and first.height
                    and min(first.width, first.height) < 1080
                ):
                    # Owner policy: preserve the validated source rather than inventing detail by upscaling.
                    publish_path = original
                    final_inspection = first
                else:
                    destination = output / (
                        _safe_component(platform) + "-" +
                        _safe_component(candidate.external_id) + "-square1080x1080.mp4"
                    )
                    normalizer(original, destination)
                    publish_path = destination
                    final_inspection = inspector(publish_path, "video")
                    if not final_inspection.ok or final_inspection.width != 1080 or final_inspection.height != 1080:
                        result.skipped.append({"item": identity, "reason": "normalized_output_invalid"})
                        destination.unlink(missing_ok=True)
                        original.unlink(missing_ok=True)
                        continue
            elif output_format == "preserve_source":
                # Preserve the aspect ratio for sources whose owner policy says
                # that 1:1 normalization is not required, while giving the file
                # a stable ID-based name for report recovery and cleanup.
                destination = output / (
                    _safe_component(platform) + "-" +
                    _safe_component(candidate.external_id) + "-preserved.mp4"
                )
                if original.resolve() != destination.resolve():
                    shutil.copy2(original, destination)
                publish_path = destination
                final_inspection = inspector(publish_path, "video")
                if not final_inspection.ok or final_inspection.media_type != "video":
                    result.skipped.append({"item": identity, "reason": "preserved_output_invalid"})
                    destination.unlink(missing_ok=True)
                    original.unlink(missing_ok=True)
                    continue
            elif output_format != "square_1080x1080":
                result.skipped.append({"item": identity, "reason": "unsupported_output_format_policy"})
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
                downloaded_inspection=first,
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
            # Report only the downloader/adapter's safe machine error code when one
            # is provided; never expose response bodies, URLs, or credentials.
            safe_code = safe_error_code(exc)
            row: dict[str, Any] = {
                "item": identity,
                "reason": "pipeline_error_" + safe_code,
                "recovery_guidance": recovery_guidance(safe_code),
            }
            receipt = getattr(exc, "recovery_diagnostics", None)
            if isinstance(receipt, dict):
                row["recovery_action"] = str(receipt.get("action", ""))[:80]
                row["recovery_attempts"] = str(receipt.get("attempts", 0))
                row["recovery_error_codes"] = ",".join(
                    str(code)[:80] for code in receipt.get("error_codes", [])[:3]
                )
            result.skipped.append(row)  # type: ignore[arg-type]

    result.status = "ready" if all(count == 0 for count in remaining.values()) else "partial"
    return result
