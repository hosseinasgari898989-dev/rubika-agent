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
from agent.brain.downloaders import CobaltDownloader, YouTubeRelayDownloader
from agent.brain.ledger import MegaLedger
from agent.brain.models import Candidate
from agent.brain.orchestrator import StagingOrchestrator, candidates_from_report
from agent.brain.relay_client import AuthorizedYouTubeRelay
from agent.brain.rubika_publisher import RubikaPublisher
from agent.brain.vision_moderator import CloudflareVideoModerator
from agent.storage.mega import MegaStorage


DEFAULT_CLOUD_API = "https://rubika-agent.h98609294.workers.dev"
DEFAULT_CHANNEL_GUID = "c0EFohK0f3174377fb6fa422d3ccb230"
# Python performs metadata, duplicate and technical policy checks. The hosted
# model is only responsible for music presence.
# The Python policy handles metadata relevance, known text red flags, duplicates,
# and media validation. Workers AI is used only for the actual soundtrack check.
REQUIRED_MUSIC_CHECKS = {"has_music": True}


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


def _matches_poppy_edit(candidate: Candidate) -> bool:
    text = candidate.searchable_text
    relevance = ("poppy playtime", "poppyplaytime", "huggy wuggy", "huggywuggy",
                 "catnap", "kissy missy", "mommy long legs", "dogday")
    return any(term in text for term in relevance)


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
    ledger_ids = set(initial_items) if isinstance(initial_items, dict) else set()
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
        stage = orchestrator.run(candidates, trigger_type="discovery_e2e_smoke")
        report_detail: dict[str, Any] = {
            "platform": platform,
            "candidates_considered": len(candidates),
            "stage": stage.as_dict(),
            "publishing": False,
        }
        if stage.status != "ok" or stage.staged_count != 1:
            stage_file = Path(stage.report_path)
            if stage_file.is_file():
                try:
                    report_detail["stage_detail"] = json.loads(
                        stage_file.read_text(encoding="utf-8")
                    )
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
        if not isinstance(moderation, dict) or any(
            moderation.get(name) is not expected
            for name, expected in REQUIRED_MUSIC_CHECKS.items()
        ):
            return {
                "ok": False, "status": "brain_checks_not_publishable",
                "platform": platform, "item_id": item_id, "publishing": False,
                "moderation": moderation,
                "reason": "music_detection_did_not_pass_required_check",
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

        publish_run_id = uuid.uuid4().hex
        cloud.create_run(trigger_type="e2e_publish_smoke", run_id=publish_run_id)
        publisher = RubikaPublisher(
            os.getenv("RUBIKA_TOKEN", ""),
            os.getenv("RUBIKA_CHANNEL_GUID", DEFAULT_CHANNEL_GUID),
        )
        last_response: dict[str, Any] = {}

        def send() -> Any:
            nonlocal last_response
            raw = publisher.send_media(
                media_path, "video", caption=f"منبع: {platform.title()} | Poppy Playtime Edit"
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
