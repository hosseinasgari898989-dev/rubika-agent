"""Run one item discovered on the phone through brain -> MEGA/D1 -> Rubika.

This is a manually triggered integration smoke test. It reads the existing
metadata report; discovery dates are not used as a gate. It publishes at most
one item, and only after the configured video classifier and file validation pass.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from agent.brain.authorization import SourceRegistry
from agent.brain.cloud_state import CloudState
from agent.brain.delivery import deliver_with_retry
from agent.brain.deduplication import blocking_ledger_entries
from agent.brain.downloaders import CobaltDownloader, YouTubeRelayDownloader
from agent.brain.ledger import MegaLedger
from agent.brain.inspector import inspect_media
from agent.brain.models import Candidate
from agent.brain.orchestrator import StagingOrchestrator, candidates_from_report
from agent.brain.relay_client import AuthorizedYouTubeRelay
from agent.brain.rubika_publisher import RubikaPublisher, assert_test_channel_destination
from agent.brain.vision_moderator import CloudflareVideoModerator
from agent.storage.mega import MegaStorage
from agent.brain.edit_caption import EDIT_CAPTION
from agent.source_policy import delivery_enabled


DEFAULT_CLOUD_API = "https://rubika-agent.h98609294.workers.dev"
# The agent brain enforces per-source metadata/edit/technical checks; AI only
# classifies the extracted soundtrack. Every failed or uncertain gate stops delivery.


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _response_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            payload = json.loads(value)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _safe_error(exc: Exception) -> str:
    value = getattr(exc, "code", type(exc).__name__)
    return re.sub(r"[^A-Za-z0-9_-]", "", str(value))[:80] or "unknown_error"


def _stage_failure_summary(stage_result: Mapping[str, Any]) -> dict[str, Any]:
    """Retain actionable staging error codes without leaking provider bodies or credentials."""
    raw_skipped = stage_result.get("skipped")
    skipped: list[dict[str, str]] = []
    if isinstance(raw_skipped, (list, tuple)):
        for row in raw_skipped[:8]:
            if not isinstance(row, Mapping):
                continue
            item = re.sub(r"[^A-Za-z0-9._:-]", "", str(row.get("item") or "unknown"))[:140]
            reason = re.sub(r"[^A-Za-z0-9_.,:-]", "", str(row.get("reason") or "unknown_error"))[:180]
            if reason:
                skipped.append({"item": item, "reason": reason})

    raw_sync = stage_result.get("cloud_sync_errors")
    sync_errors: list[str] = []
    if isinstance(raw_sync, (list, tuple)):
        for value in raw_sync[:5]:
            parts = str(value).split(":")
            if len(parts) >= 3:
                platform = parts[0] if re.fullmatch(r"[A-Za-z0-9_-]{1,24}", parts[0]) else "unknown"
                external_id = parts[1] if re.fullmatch(r"[A-Za-z0-9._-]{1,120}", parts[1]) else "unknown"
                # The final component should be an exception/code name. Never copy arbitrary
                # remainder text from a provider response or a malformed diagnostic.
                code = parts[2]
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", code):
                    code = "unknown_error"
                sync_errors.append(f"{platform}:{external_id}:{code}")
            else:
                sync_errors.append("cloud_sync_error")

    if sync_errors:
        primary_reason = "cloud_sync_failed"
    elif skipped:
        primary_reason = skipped[0]["reason"].split(",", 1)[0][:100]
    else:
        primary_reason = "no_media_staged"

    return {
        "reason": primary_reason,
        "failure_details": skipped,
        "cloud_sync_errors": sync_errors,
    }


def _matches_poppy_edit(candidate: Candidate) -> bool:
    # This is an agent-brain metadata prefilter. Creator identity is never enough
    # to classify an individual upload; final policy checks run after download.
    text = " ".join((candidate.title, candidate.caption, " ".join(candidate.hashtags))).casefold()
    relevance = (
        "poppy playtime", "poppyplaytime", "huggy wuggy", "huggywuggy",
        "catnap", "cat nap", "kissy missy", "mommy long legs", "dogday",
        "smiling critters", "smilingcritters", "nightmare critters",
        "bobby bearhug", "craftycorn", "kickin chicken", "hoppy hopscotch",
        "picky piggy", "bubba bubbaphant", "#prototype", "prototype",
    )
    return any(term in text for term in relevance)


def _blocking_ledger_ids(items: Any) -> set[str]:
    """Use ledger identity only when its delivery status blocks another send."""
    protected = blocking_ledger_entries(items)
    ids = set(protected)
    for row in protected.values():
        platform = row.get("platform")
        external_id = row.get("external_id")
        if isinstance(platform, str) and isinstance(external_id, str) and external_id:
            ids.add(platform + ":" + external_id)
            ids.add(external_id)
    return ids


def _is_reusable_staged_item(candidate: Candidate, item: Any) -> bool:
    """Check ledger metadata before downloading a previous validated MEGA copy."""
    if not isinstance(item, Mapping):
        return False
    payload = item.get("cloud_payload")
    validation = payload.get("validation") if isinstance(payload, Mapping) else None
    digest = item.get("sha256")
    payload_digest = payload.get("media_sha256") if isinstance(payload, Mapping) else None
    storage_key = item.get("storage_key")
    return (
        item.get("status") == "ready"
        and item.get("platform") == candidate.platform
        and item.get("external_id") == candidate.external_id
        and item.get("permission_confirmed") is True
        and item.get("cloud_sync") == "ok"
        and item.get("media_type") == "video"
        and isinstance(storage_key, str)
        and storage_key.startswith("/Rubika-Agent/")
        and ".." not in storage_key
        and isinstance(digest, str)
        and re.fullmatch(r"[a-fA-F0-9]{64}", digest) is not None
        and isinstance(payload, Mapping)
        and payload.get("media_type") == "video"
        and isinstance(validation, Mapping)
        and validation.get("ok") is True
        and isinstance(payload_digest, str)
        and payload_digest.casefold() == digest.casefold()
    )

def _select_candidates(
    report_path: Path,
    platform: str,
    seen_ids: set[str],
    ledger_ids: set[str],
    limit: int = 1,
    offset: int = 0,
) -> list[Candidate]:
    candidates = candidates_from_report(report_path)
    selected: list[Candidate] = []
    eligible_index = 0
    bounded_limit = max(1, min(int(limit), 5))
    bounded_offset = max(0, min(int(offset), 100))
    for candidate in candidates:
        if candidate.platform != platform or not _matches_poppy_edit(candidate):
            continue
        identity = f"{candidate.platform}:{candidate.external_id}"
        if identity in seen_ids or candidate.external_id in seen_ids:
            continue
        if identity in ledger_ids or candidate.external_id in ledger_ids:
            continue
        if eligible_index < bounded_offset:
            eligible_index += 1
            continue
        selected.append(candidate)
        eligible_index += 1
        if len(selected) >= bounded_limit:
            break
    return selected


def _find_staged_media(output_dir: Path, external_id: str) -> Path:
    matching = [
        path for path in output_dir.rglob("*.mp4")
        if path.is_file() and external_id in path.name
    ]
    normalized = [
        path for path in matching
        if "square1080x1080" in path.name
    ]
    choices = normalized or matching
    if not choices:
        raise RuntimeError("staged_media_file_missing")
    return sorted(choices)[0]


def run_one(
    report_path: Path,
    platform: str,
    output_report: Path,
    *,
    candidate_limit: int = 1,
    candidate_offset: int = 0,
) -> dict[str, Any]:
    channel_guid = assert_test_channel_destination(
        os.getenv("RUBIKA_CHANNEL_GUID", "")
    )
    if platform == "youtube" and not delivery_enabled("youtube"):
        raise RuntimeError("youtube_delivery_paused_by_owner")
    api_url = os.getenv("AGENT_API_BASE_URL", DEFAULT_CLOUD_API).strip()
    token = os.getenv("AGENT_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("missing_AGENT_API_TOKEN")
    if platform not in {"tiktok", "instagram", "youtube"}:
        raise RuntimeError("unsupported_platform")
    if not 1 <= int(candidate_limit) <= 5:
        raise RuntimeError("candidate_limit_out_of_range")
    if not 0 <= int(candidate_offset) <= 100:
        raise RuntimeError("candidate_offset_out_of_range")

    cloud = CloudState(api_url, token)
    storage = MegaStorage(root="/Rubika-Agent", timeout_seconds=180)
    ledger = MegaLedger(storage)
    initial_state = ledger.load()
    initial_items = initial_state.get("items", {})
    ledger_ids = _blocking_ledger_ids(initial_items)
    seen = cloud.get_seen_index()
    seen_ids = set(seen.get("ids", set()))
    candidates = _select_candidates(
        report_path,
        platform,
        seen_ids,
        ledger_ids,
        limit=candidate_limit,
        offset=candidate_offset,
    )
    if not candidates:
        return {
            "ok": False,
            "status": "no_candidate",
            "platform": platform,
            "publishing": False,
            "reason": "no_new_relevant_candidate_in_report",
        }

    cobalt_url = os.getenv("COBALT_API_URL", "http://127.0.0.1:9000/").strip()
    downloaders: dict[str, Any] = {}
    if platform in {"tiktok", "instagram"}:
        downloaders[platform] = CobaltDownloader(cobalt_url, max_bytes=100 * 1024 * 1024)
    elif platform == "youtube":
        relay_url = os.getenv("RELAY_BASE_URL", "").strip()
        relay_token = os.getenv("RELAY_API_TOKEN", "").strip()
        if not relay_url or not relay_token:
            raise RuntimeError("youtube_relay_not_configured")
        downloaders[platform] = YouTubeRelayDownloader(
            AuthorizedYouTubeRelay(relay_url, relay_token)
        )

    registry_path = Path("config/trusted_sources.json")
    registry = SourceRegistry.load(registry_path)
    moderator = CloudflareVideoModerator(cloud)
    with tempfile.TemporaryDirectory(prefix="rubika-agent-e2e-") as temp:
        output_dir = Path(temp)
        reused_candidate: Candidate | None = None
        reuse_error: str | None = None

        # Reuse a validated staged object after an interrupted delivery rather than
        # downloading and uploading another copy of the same queued item.
        for candidate in candidates:
            identity = candidate.platform + ":" + candidate.external_id
            entry = initial_items.get(identity) if isinstance(initial_items, dict) else None
            if not _is_reusable_staged_item(candidate, entry):
                continue
            local_copy: Path | None = None
            try:
                local_copy = Path(storage.download(str(entry["storage_key"]), output_dir))
                if local_copy.suffix.lower() != ".mp4":
                    raise RuntimeError("reusable_media_not_mp4")
                inspection = inspect_media(local_copy, "video")
                expected_sha = str(entry.get("sha256") or "").lower()
                expected_size = entry.get("size_bytes")
                if (
                    not inspection.ok
                    or inspection.media_type != "video"
                    or not inspection.sha256
                    or inspection.sha256.lower() != expected_sha
                    or (type(expected_size) is int and inspection.size_bytes != expected_size)
                ):
                    raise RuntimeError("reusable_media_integrity_check_failed")

                safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", candidate.external_id)[:80]
                target_path = output_dir / (candidate.platform + "_" + safe_id + "-preserved.mp4")
                if local_copy.resolve() != target_path.resolve():
                    target_path.unlink(missing_ok=True)
                    local_copy.replace(target_path)
                reused_candidate = candidate
                candidates = [candidate]
                break
            except Exception as exc:
                reuse_error = _safe_error(exc)
                if local_copy is not None:
                    local_copy.unlink(missing_ok=True)

        registry_path = Path("config/trusted_sources.json")
        registry = SourceRegistry.load(registry_path)
        moderator = CloudflareVideoModerator(cloud)
        orchestrator = StagingOrchestrator(
            cloud=cloud,
            storage=storage,
            registry=registry,
            downloaders=downloaders,
            moderator=moderator,
            output_dir=output_dir,
            ledger=ledger,
            required_counts={platform: 1},
        )

        if reused_candidate is not None:
            report_detail: dict[str, Any] = {
                "platform": platform,
                "candidates_considered": len(candidates),
                "stage": {
                    "status": "ok",
                    "staged_count": 1,
                    "reused_validated_media": True,
                    "publishing": False,
                },
                "reused_validated_media": True,
                "publishing": False,
            }
        else:
            stage = orchestrator.run(candidates, trigger_type="discovery_e2e_smoke")
            report_detail = {
                "platform": platform,
                "candidates_considered": len(candidates),
                "stage": stage.as_dict(),
                "reused_validated_media": False,
                "publishing": False,
            }
            if reuse_error:
                report_detail["previous_staged_copy_rejected"] = reuse_error
            if stage.status != "ok" or stage.staged_count != 1:
                stage_result = stage.as_dict()
                report_detail["stage_detail"] = stage_result
                report_detail.update(_stage_failure_summary(stage_result))
                stage_file = Path(stage.report_path)
                if stage_file.is_file():
                    try:
                        detailed_report = json.loads(stage_file.read_text(encoding="utf-8"))
                        if isinstance(detailed_report, dict):
                            report_detail["stage_detail"] = detailed_report
                            report_detail.update(_stage_failure_summary(detailed_report))
                    except (OSError, json.JSONDecodeError):
                        pass
                return {"ok": False, "status": "staging_failed", **report_detail}
        current_state = ledger.load()
        item_map = current_state.get("items", {})
        if not isinstance(item_map, dict):
            raise RuntimeError("mega_ledger_items_invalid")
        staged_ids = [
            item_id for item_id, item in item_map.items()
            if isinstance(item, dict)
            and item.get("status") == "ready"
            and item.get("platform") == platform
            and item.get("cloud_sync") == "ok"
            and isinstance(item.get("cloud_payload"), dict)
            and item.get("external_id")
            and any(c.external_id == item.get("external_id") for c in candidates)
        ]
        if len(staged_ids) != 1:
            raise RuntimeError("could_not_identify_single_staged_candidate")
        item_id = staged_ids[0]
        item = item_map[item_id]
        payload = dict(item["cloud_payload"])
        moderation = payload.get("moderation")
        staged_candidate = next(
            (candidate for candidate in candidates
             if candidate.external_id == str(item.get("external_id"))),
            candidates[0],
        )
        policy = staged_candidate.source_policy or {}
        required_checks = {"has_music": True}
        if policy.get("require_poppy_relevance_check") is True:
            required_checks["is_poppy_playtime"] = True
        if policy.get("require_edit_signal_check") is True:
            required_checks["is_edit"] = True
        if policy.get("require_brain_quality_check") is True:
            required_checks["high_quality_edit"] = True
        if not isinstance(moderation, dict) or any(
            moderation.get(name) is not expected
            for name, expected in required_checks.items()
        ):
            return {
                "ok": False, "status": "brain_checks_not_publishable",
                "platform": platform, "item_id": item_id, "publishing": False,
                "moderation": moderation,
                "reason": "required_visual_or_music_check_not_passed",
            }

        candidate_id = str(item.get("external_id"))
        media_path = _find_staged_media(output_dir, candidate_id)
        payload["status"] = "ready"
        payload["review_state"] = "approved"
        cloud.upsert_item(payload)
        current_state["items"][item_id]["cloud_payload"] = payload
        current_state["items"][item_id]["review_state"] = "approved"
        current_state["items"][item_id]["status"] = "ready"
        ledger.save(current_state)

        remote_storage_key = item.get("storage_key")
        if not isinstance(remote_storage_key, str) or not remote_storage_key.startswith("/Rubika-Agent/"):
            return {
                "ok": False,
                "status": "staging_failed",
                "platform": platform,
                "item_id": item_id,
                "publishing": False,
                "reason": "staged_storage_key_missing",
            }

        publish_run_id = uuid.uuid4().hex
        cloud.create_run(trigger_type="e2e_publish_smoke", run_id=publish_run_id)
        publisher = RubikaPublisher(
            os.getenv("RUBIKA_TOKEN", ""),
            channel_guid,
        )
        last_response: dict[str, Any] = {}

        def send() -> Any:
            nonlocal last_response
            raw = publisher.send_media(
                media_path, "video", caption=EDIT_CAPTION
            )
            last_response = _response_dict(raw)
            return raw

        def record_attempt(number: int, status: str, error_code: str | None) -> None:
            raw_message_id = None
            if status == "sent":
                data = last_response.get("data")
                raw_message_id = (
                    data.get("message_id") if isinstance(data, dict)
                    else last_response.get("message_id")
                )
            message_id = str(raw_message_id) if raw_message_id is not None else None
            # If the initial D1 attempt record fails, do not send anything. After
            # a send, preserve the local ledger even if D1 briefly becomes unavailable.
            try:
                cloud.record_attempt(
                    item_id, number, status, error_code=error_code, message_id=message_id
                )
            except Exception as exc:
                if status == "started":
                    raise RuntimeError("d1_attempt_preflight_failed") from None
                current_state["items"][item_id]["d1_attempt_sync_error"] = _safe_error(exc)
            MegaLedger.record_attempt(
                current_state, item_id, attempt_number=number, status=status,
                error_code=error_code, message_id=message_id,
            )
            ledger.save(current_state)

        delivery = deliver_with_retry(
            item_id=item_id,
            send=send,
            record_attempt=record_attempt,
            delete_local=lambda: media_path.unlink(missing_ok=True),
            max_attempts=3,
            sleep_seconds=1,
        )
        sent = delivery.get("status") == "sent"
        try:
            cloud.finish_run(
                publish_run_id,
                status="ok" if sent else "partial" if delivery.get("status") == "uncertain" else "failed",
                found_count=1,
                eligible_count=1,
                sent_count=1 if sent else 0,
                skipped_count=0 if sent else 1,
                failed_count=0 if sent or delivery.get("status") == "uncertain" else 1,
                summary="single discovery-to-brain-to-MEGA/D1-to-Rubika smoke test",
            )
        except Exception as exc:
            report_detail["publish_run_finish_error"] = _safe_error(exc)

        report_detail.update({
            "ok": sent,
            "status": delivery.get("status", "unknown"),
            "platform": platform,
            "item_id": item_id,
            "title": item.get("title", ""),
            "media_path": media_path.name,
            "delivery": delivery,
            "moderation": moderation,
            "storage_key": item.get("storage_key"),
            "publishing": sent,
            "updated_at": _utc_now(),
        })
        return report_detail


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("artifacts/termux_discovery.json"))
    parser.add_argument("--platform", choices=("tiktok", "instagram", "youtube"), default="tiktok")
    parser.add_argument("--candidate-limit", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--candidate-offset", type=int, choices=range(0, 101), default=0)
    parser.add_argument("--output-report", type=Path, default=Path("artifacts/brain-e2e-report.json"))
    args = parser.parse_args()

    try:
        report = run_one(
            args.report,
            args.platform,
            args.output_report,
            candidate_limit=args.candidate_limit,
            candidate_offset=args.candidate_offset,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "status": "error",
            "error": _safe_error(exc),
            "publishing": False,
            "updated_at": _utc_now(),
        }
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
