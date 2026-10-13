"""Fail-closed integration of discovery, reuse authorization, relay, MEGA and D1.

This staging job never publishes to Rubika. A real moderation adapter is mandatory;
metadata alone cannot prove that a clip is high-effort, non-insulting, or has music.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .authorization import SourceRegistry
from .cloud_state import CloudState, CloudStateError
from .ledger import MegaLedger, new_state
from .models import Candidate
from .pipeline import BatchResult, StagedMedia, stage_edit_batch
from ..storage.mega import MegaStorage, MegaStorageError


Moderator = Callable[[Candidate, Path], Mapping[str, Any] | None]
Downloader = Callable[[Candidate, Path, bool], str | Path]


@dataclass(frozen=True)
class StageJobResult:
    run_id: str
    status: str
    found_count: int
    authorized_count: int
    staged_count: int
    skipped: tuple[dict[str, str], ...]
    cloud_sync_errors: tuple[str, ...]
    report_path: str
    publishing: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def candidates_from_report(path: str | Path) -> list[Candidate]:
    """Read only canonical candidates from a discovery report; no arbitrary URL invention."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("discovery_report_unreadable") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), dict):
        raise ValueError("invalid_discovery_report")
    candidates: list[Candidate] = []
    platform_map = payload["candidates"]
    for platform in ("tiktok", "youtube", "instagram"):
        rows = platform_map.get(platform, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            external_id = row.get("id") or row.get("external_id")
            canonical_url = row.get("url") or row.get("canonical_url")
            if not isinstance(external_id, str) or not isinstance(canonical_url, str):
                continue
            duration = row.get("duration")
            title = row.get("title") if isinstance(row.get("title"), str) else ""
            caption = row.get("caption") if isinstance(row.get("caption"), str) else ""
            source_key = row.get("author") or row.get("source_key") or ""
            if not isinstance(source_key, str):
                source_key = ""
            media_type = row.get("media_type") if row.get("media_type") in {
                "video", "image", "audio", "carousel", "unknown"
            } else ("video" if platform in {"tiktok", "youtube"} else "unknown")
            try:
                candidates.append(Candidate(
                    platform=platform,
                    canonical_url=canonical_url,
                    external_id=external_id,
                    title=title,
                    caption=caption,
                    source_key=source_key,
                    media_type=media_type,
                    source_tier="unknown",
                ))
            except ValueError:
                continue
    return candidates


class StagingOrchestrator:
    """Stages explicitly authorized candidates and records recoverable MEGA/D1 state."""

    def __init__(
        self,
        *,
        cloud: CloudState,
        storage: MegaStorage,
        registry: SourceRegistry,
        downloaders: Mapping[str, Downloader],
        moderator: Moderator | None,
        output_dir: str | Path,
        ledger: MegaLedger | None = None,
        required_counts: Mapping[str, int] | None = None,
    ) -> None:
        self.cloud = cloud
        self.storage = storage
        self.registry = registry
        self.downloaders = dict(downloaders)
        self.moderator = moderator
        self.output_dir = Path(output_dir)
        self.required_counts = dict(required_counts or {"tiktok": 5, "youtube": 5})
        self.ledger = ledger or MegaLedger(storage)

    def run(self, candidates: Iterable[Candidate], *, trigger_type: str = "manual") -> StageJobResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        candidates_list = list(candidates)
        run_id = uuid.uuid4().hex
        report_path = self.output_dir / ("stage-report-" + run_id + ".json")
        skipped: list[dict[str, str]] = []
        cloud_sync_errors: list[str] = []
        ledger_state: dict[str, Any] | None = None
        remote_run_created = False
        staged_count = 0
        authorized_count = 0
        run_status = "failed"

        try:
            ledger_state = self.ledger.load()
            MegaLedger.start_run(ledger_state, run_id, trigger_type)
            self.ledger.save(ledger_state)
        except Exception as exc:
            return self._finish_report(
                report_path, run_id, "failed", len(candidates_list), 0, 0,
                [{"item": "run", "reason": "ledger_unavailable_" + type(exc).__name__}],
                ["ledger_unavailable"], summary="Durable MEGA ledger could not be loaded or saved.",
            )

        try:
            self.cloud.create_run(trigger_type=trigger_type, run_id=run_id)
            remote_run_created = True
            seen = self.cloud.get_seen_index()
        except Exception as exc:
            skipped.append({"item": "run", "reason": "cloud_state_unavailable_" + type(exc).__name__})
            cloud_sync_errors.append("cloud_state_unavailable")
            return self._finish_report(
                report_path, run_id, "failed", len(candidates_list), 0, 0,
                skipped, cloud_sync_errors, ledger_state=ledger_state,
                remote_run_created=remote_run_created,
                summary="Cloud state/deduplication index was unavailable; no media was downloaded.",
            )

        ledger_items = ledger_state.get("items", {})
        ledger_ids = set(ledger_items) if isinstance(ledger_items, dict) else set()
        ledger_hashes = {
            item.get("sha256")
            for item in ledger_items.values()
            if isinstance(item, dict) and isinstance(item.get("sha256"), str)
        } if isinstance(ledger_items, dict) else set()
        seen_ids = set(seen.get("ids", set())) | ledger_ids
        seen_hashes = set(seen.get("hashes", set())) | ledger_hashes

        # Refuse to download anything if real content moderation is not configured.
        if not callable(self.moderator):
            skipped.append({"item": "run", "reason": "moderation_not_configured"})
            cloud_sync_errors.append("moderation_not_configured")
            return self._finish_report(
                report_path, run_id, "failed", len(candidates_list), 0, 0,
                skipped, cloud_sync_errors, ledger_state=ledger_state,
                remote_run_created=remote_run_created,
                summary="A real image/audio moderation adapter is required before staging.",
            )

        eligible_candidates: list[Candidate] = []
        permission_by_id: dict[str, Any] = {}
        target_platforms = set(self.required_counts)
        for candidate in candidates_list:
            if candidate.platform not in target_platforms:
                continue
            identity = candidate.platform + ":" + candidate.external_id
            decision = self.registry.check(candidate)
            if not decision.allowed:
                skipped.append({"item": identity, "reason": decision.reason})
                continue
            if candidate.platform not in self.downloaders:
                skipped.append({"item": identity, "reason": "downloader_not_configured"})
                continue
            trusted_keys = self.registry.authorized_source_keys(candidate.platform)
            tier = "trusted" if (
                decision.reason == "explicit_source_permission" and
                candidate.source_key in trusted_keys
            ) else "unknown"
            prepared = replace(candidate, source_tier=tier)
            eligible_candidates.append(prepared)
            permission_by_id[identity] = decision
        authorized_count = len(eligible_candidates)

        if not eligible_candidates:
            skipped.append({"item": "run", "reason": "no_authorized_candidates"})
            return self._finish_report(
                report_path, run_id, "partial", len(candidates_list), authorized_count, 0,
                skipped, cloud_sync_errors, ledger_state=ledger_state,
                remote_run_created=remote_run_created,
                summary="No candidate passed the explicit reuse-permission gate.",
            )

        raw_dir = self.output_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)

        def download(candidate: Candidate) -> str | Path:
            identity = candidate.platform + ":" + candidate.external_id
            decision = permission_by_id.get(identity)
            if decision is None or not decision.allowed:
                raise RuntimeError("permission_gate_failed")
            downloader = self.downloaders[candidate.platform]
            return downloader(candidate, raw_dir, decision.allowed)

        def persist(staged: StagedMedia) -> None:
            identity = staged.candidate.platform + ":" + staged.candidate.external_id
            cloud_payload = self.cloud.prepare_staged_payload(staged)
            validation = cloud_payload.get("validation") or {}
            moderation = cloud_payload.get("moderation") or {}
            permission = permission_by_id.get(identity)
            ledger_values = {
                "platform": staged.candidate.platform,
                "external_id": staged.candidate.external_id,
                "canonical_url": staged.candidate.canonical_url,
                "source_key": staged.candidate.source_key,
                "title": staged.candidate.title,
                "caption": staged.caption,
                "permission_confirmed": bool(permission and permission.allowed),
                "permission_basis": permission.basis if permission else None,
                "status": "ready",
                "review_state": "pending",
                "source_tier": staged.candidate.source_tier,
                "media_type": staged.inspection.media_type,
                "sha256": staged.inspection.sha256,
                "size_bytes": staged.inspection.size_bytes,
                "storage_key": staged.storage_key,
                "validation": validation,
                "moderation": moderation,
                "cloud_sync": "pending",
                "cloud_payload": cloud_payload,
            }
            MegaLedger.upsert_item(ledger_state, identity, ledger_values)
            self.ledger.save(ledger_state)
            try:
                self.cloud.upsert_item(cloud_payload)
                ledger_state["items"][identity]["cloud_sync"] = "ok"
                ledger_state["items"][identity]["cloud_synced_at"] = _utc_now()
            except Exception as exc:
                # Keep the verified MEGA copy and the exact safe payload for later sync.
                ledger_state["items"][identity]["cloud_sync"] = "pending"
                ledger_state["items"][identity]["cloud_sync_error"] = type(exc).__name__
                cloud_sync_errors.append(identity + ":" + type(exc).__name__)
                MegaLedger.add_event(
                    ledger_state,
                    "d1_item_sync_pending",
                    level="warning",
                    item_id=identity,
                    details={"platform": staged.candidate.platform, "reason": type(exc).__name__},
                )
            self.ledger.save(ledger_state)

        batch = stage_edit_batch(
            eligible_candidates,
            required_counts=self.required_counts,
            download=download,
            moderate=self.moderator,
            output_dir=self.output_dir / "normalized",
            seen_external_ids=seen_ids,
            seen_hashes=seen_hashes,
            trusted_source_keys=set().union(*(
                self.registry.authorized_source_keys(platform)
                for platform in self.required_counts
            )),
            storage=self.storage,
            persist_staged=persist,
        )
        staged_count = len(batch.staged)
        if cloud_sync_errors or batch.status != "ready":
            run_status = "partial"
        else:
            run_status = "ok"
        skipped.extend(batch.skipped)
        summary = (
            f"found={len(candidates_list)}; authorized={authorized_count}; staged={staged_count}; "
            f"skipped={len(skipped)}; cloud_sync_errors={len(cloud_sync_errors)}; publishing=false"
        )
        return self._finish_report(
            report_path, run_id, run_status, len(candidates_list), authorized_count, staged_count,
            skipped, cloud_sync_errors, ledger_state=ledger_state,
            remote_run_created=remote_run_created, summary=summary,
        )

    def retry_pending_cloud_sync(self) -> dict[str, Any]:
        """Retry previously stored D1 payloads without redownloading or re-uploading media."""
        ledger_state = self.ledger.load()
        pending = [
            (item_id, item)
            for item_id, item in ledger_state.get("items", {}).items()
            if isinstance(item, dict)
            and item.get("cloud_sync") == "pending"
            and isinstance(item.get("cloud_payload"), dict)
        ]
        completed: list[str] = []
        failed: list[dict[str, str]] = []
        for item_id, item in pending:
            try:
                self.cloud.upsert_item(item["cloud_payload"])
                item["cloud_sync"] = "ok"
                item["cloud_synced_at"] = _utc_now()
                item.pop("cloud_sync_error", None)
                completed.append(item_id)
            except Exception as exc:
                item["cloud_sync_error"] = type(exc).__name__
                failed.append({"item": item_id, "reason": type(exc).__name__})
        self.ledger.save(ledger_state)
        return {
            "ok": not failed,
            "completed": completed,
            "failed": failed,
            "publishing": False,
        }

    def _finish_report(
        self,
        report_path: Path,
        run_id: str,
        status: str,
        found_count: int,
        authorized_count: int,
        staged_count: int,
        skipped: list[dict[str, str]],
        cloud_sync_errors: list[str],
        *,
        ledger_state: dict[str, Any] | None = None,
        remote_run_created: bool = False,
        summary: str = "",
    ) -> StageJobResult:
        skipped_count = len(skipped)
        if ledger_state is not None:
            try:
                runs = ledger_state.get("runs", [])
                if any(isinstance(run, dict) and run.get("id") == run_id for run in runs):
                    MegaLedger.finish_run(
                        ledger_state,
                        run_id,
                        status=status,
                        found_count=found_count,
                        eligible_count=authorized_count,
                        sent_count=0,
                        skipped_count=skipped_count,
                        failed_count=1 if status == "failed" else 0,
                        summary=summary,
                    )
                MegaLedger.add_event(
                    ledger_state,
                    "staging_run_" + status,
                    level="error" if status == "failed" else "warning" if status == "partial" else "info",
                    details={
                        "found_count": found_count,
                        "authorized_count": authorized_count,
                        "staged_count": staged_count,
                        "skipped_count": skipped_count,
                        "cloud_sync_errors": len(cloud_sync_errors),
                        "publishing": False,
                    },
                )
                self.ledger.save(ledger_state)
            except Exception:
                # Keep the report result even when the final ledger snapshot cannot be saved.
                if status == "ok":
                    status = "partial"
        if remote_run_created:
            try:
                self.cloud.finish_run(
                    run_id,
                    status=status,
                    found_count=found_count,
                    eligible_count=authorized_count,
                    sent_count=0,
                    skipped_count=skipped_count,
                    failed_count=1 if status == "failed" else 0,
                    summary=summary,
                )
                self.cloud.add_event(
                    "staging_run_" + status,
                    level="error" if status == "failed" else "warning" if status == "partial" else "info",
                    run_id=run_id,
                    details={
                        "found_count": found_count,
                        "authorized_count": authorized_count,
                        "staged_count": staged_count,
                        "skipped_count": skipped_count,
                        "publishing": False,
                    },
                )
            except Exception as exc:
                cloud_sync_errors.append("run_finish_" + type(exc).__name__)
                if status == "ok":
                    status = "partial"

        report = {
            "schema_version": 1,
            "run_id": run_id,
            "updated_at": _utc_now(),
            "status": status,
            "found_count": found_count,
            "authorized_count": authorized_count,
            "staged_count": staged_count,
            "skipped": skipped,
            "cloud_sync_errors": cloud_sync_errors,
            "publishing": False,
            "summary": summary,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return StageJobResult(
            run_id=run_id,
            status=status,
            found_count=found_count,
            authorized_count=authorized_count,
            staged_count=staged_count,
            skipped=tuple(skipped),
            cloud_sync_errors=tuple(cloud_sync_errors),
            report_path=str(report_path),
            publishing=False,
        )
