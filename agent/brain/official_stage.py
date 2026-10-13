"""Separate staging track for official Mob Entertainment news media.

No edit-style/insult filters are applied to official news. Sources still require
explicit reuse permission, files are validated and deduplicated, and review remains
pending. Carousel/album publishing is deliberately unsupported until an album adapter
is added.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .authorization import SourceRegistry
from .cloud_state import CloudState
from .inspector import inspect_media
from .ledger import MegaLedger
from .models import Candidate, Evaluation, InspectionResult
from .official import evaluate_official_media
from .pipeline import StagedMedia
from .policy import format_official_mob_caption
from agent.storage.mega import MegaStorage


OfficialDownloader = Callable[[Candidate], str | Path]
OfficialInspector = Callable[..., InspectionResult]


def stage_official_media(
    candidates: Iterable[Candidate],
    *,
    registry: SourceRegistry,
    download: OfficialDownloader,
    storage: MegaStorage,
    cloud: CloudState,
    ledger_state: dict[str, Any],
    output_dir: str | Path,
    seen_external_ids: set[str] | None = None,
    seen_hashes: set[str] | None = None,
    inspector: OfficialInspector = inspect_media,
    persist_ledger: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Stage authorized official videos/images/audio and retain D1 sync failures."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    seen_ids = set(seen_external_ids or set())
    seen_digests = set(seen_hashes or set())
    staged: list[StagedMedia] = []
    skipped: list[dict[str, str]] = []
    cloud_sync_errors: list[str] = []

    def save_ledger() -> None:
        if persist_ledger is not None:
            persist_ledger(ledger_state)

    for candidate in candidates:
        if candidate.platform != "mob_official":
            continue
        identity = "mob_official:" + candidate.external_id
        permission = registry.check(candidate)
        if not permission.allowed:
            skipped.append({"item": identity, "reason": permission.reason})
            continue
        if candidate.media_type == "carousel":
            skipped.append({"item": identity, "reason": "carousel_requires_album_adapter"})
            continue
        if candidate.media_type not in {"video", "image", "audio"}:
            skipped.append({"item": identity, "reason": "unsupported_official_media_type"})
            continue
        if identity in seen_ids or candidate.external_id in seen_ids:
            skipped.append({"item": identity, "reason": "duplicate_external_id"})
            continue

        prepared = replace(candidate, source_tier="official")
        local_path: Path | None = None
        storage_key: str | None = None
        try:
            local_path = Path(download(prepared))
            validation = inspector(local_path, prepared.media_type)
            evaluation = evaluate_official_media(
                prepared,
                validation,
                seen_external_ids=seen_ids,
                seen_hashes=seen_digests,
            )
            if not validation.ok or evaluation.decision != "eligible":
                reason = evaluation.reasons[0] if evaluation.reasons else "media_validation_failed"
                skipped.append({"item": identity, "reason": reason})
                local_path.unlink(missing_ok=True)
                continue

            storage_key = storage.upload(local_path, "official")
            caption = format_official_mob_caption(
                prepared.original_caption or prepared.caption,
                prepared.persian_translation,
            )
            staged_item = StagedMedia(
                candidate=prepared,
                original_path=str(local_path),
                publish_path=str(local_path),
                inspection=validation,
                evaluation=evaluation,
                storage_key=storage_key,
                caption=caption or "",
            )
            cloud_payload = cloud.prepare_staged_payload(staged_item)
            ledger_values = {
                "platform": prepared.platform,
                "external_id": prepared.external_id,
                "canonical_url": prepared.canonical_url,
                "source_key": prepared.source_key,
                "title": prepared.title,
                "caption": caption or "",
                "original_caption": prepared.original_caption or prepared.caption,
                "persian_translation": prepared.persian_translation,
                "permission_confirmed": True,
                "permission_basis": permission.basis,
                "source_tier": "official",
                "media_type": validation.media_type,
                "status": "ready",
                "review_state": "pending",
                "sha256": validation.sha256,
                "size_bytes": validation.size_bytes,
                "storage_key": storage_key,
                "validation": {
                    "ok": validation.ok,
                    "mime_type": validation.mime_type,
                    "width": validation.width,
                    "height": validation.height,
                    "duration_seconds": validation.duration_seconds,
                    "video_codec": validation.video_codec,
                    "audio_codec": validation.audio_codec,
                    "has_audio": validation.has_audio,
                    "errors": list(validation.errors),
                    "warnings": list(validation.warnings),
                },
                "cloud_payload": cloud_payload,
                "cloud_sync": "pending",
            }
            MegaLedger.upsert_item(ledger_state, identity, ledger_values)
            save_ledger()
            try:
                cloud.upsert_item(cloud_payload)
                ledger_state["items"][identity]["cloud_sync"] = "ok"
                ledger_state["items"][identity]["cloud_synced_at"] = __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(timespec="seconds").replace("+00:00", "Z")
            except Exception as exc:
                ledger_state["items"][identity]["cloud_sync_error"] = type(exc).__name__
                cloud_sync_errors.append(identity + ":" + type(exc).__name__)
                MegaLedger.add_event(
                    ledger_state,
                    "official_d1_sync_pending",
                    level="warning",
                    item_id=identity,
                    details={"media_type": validation.media_type, "reason": type(exc).__name__},
                )
            save_ledger()
            staged.append(staged_item)
            seen_ids.add(identity)
            if validation.sha256:
                seen_digests.add(validation.sha256)
        except Exception as exc:
            skipped.append({"item": identity, "reason": "official_stage_error_" + type(exc).__name__})
            # If MEGA accepts the file but D1 has a temporary failure, keep it for recovery.
            if storage_key is None and local_path is not None:
                local_path.unlink(missing_ok=True)

    return {
        "status": "partial" if skipped or cloud_sync_errors else "ready",
        "staged": staged,
        "skipped": skipped,
        "cloud_sync_errors": cloud_sync_errors,
        "staged_count": len(staged),
        "publishing": False,
    }
