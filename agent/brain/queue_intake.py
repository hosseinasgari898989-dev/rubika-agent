"""Validate and enqueue the metadata-only Termux report into Cloudflare D1."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from agent.brain.cloud_state import CloudState
from agent.brain.owner_notify import queue_inventory_lines, send_owner_notification


PLATFORMS = ("tiktok", "youtube", "instagram")
MAX_ENQUEUE_ITEMS = 500


def load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("discovery_report_unreadable") from None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("source") != "termux_phone"
        or payload.get("mode") != "metadata_only_no_download_no_publish"
        or not isinstance(payload.get("candidates"), dict)
    ):
        raise ValueError("invalid_termux_metadata_report")
    return payload


def candidates_for_queue(payload: dict[str, Any], *, max_items: int = MAX_ENQUEUE_ITEMS) -> list[dict[str, Any]]:
    """Map phone discovery rows to minimal queue metadata; never includes media bytes."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for platform in PLATFORMS:
        raw_rows = payload["candidates"].get(platform, [])
        if not isinstance(raw_rows, list):
            continue
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            external_id = raw.get("id") or raw.get("external_id")
            url = raw.get("url") or raw.get("canonical_url")
            if not isinstance(external_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", external_id):
                continue
            if not isinstance(url, str) or not url.startswith("https://"):
                continue
            if platform == "instagram" and str(raw.get("media_type") or "").lower() != "video":
                continue
            identity = (platform, external_id)
            if identity in seen:
                continue
            seen.add(identity)
            source_key = str(raw.get("author") or raw.get("source_key") or raw.get("channel") or "").strip()[:200]
            if not source_key:
                source_key = platform + "_public_discovery"
            rows.append({
                "platform": platform,
                "external_id": external_id,
                "canonical_url": url[:2048],
                "source_key": source_key,
                "title": str(raw.get("title") or "")[:500],
                "caption": str(raw.get("caption") or "")[:4000],
                "published_at": str(raw.get("published_at") or "")[:80],
                "media_type": "video",
                "author": str(raw.get("author") or "")[:100],
                "query": str(raw.get("query") or "")[:200],
                "freshness_source": str(raw.get("freshness_source") or "")[:100],
                "freshness_verified": raw.get("freshness_verified") is True,
                "method": str(raw.get("method") or "termux_phone")[:100],
            })
            if len(rows) >= max_items:
                return rows
    return rows


def run_intake(report_path: Path) -> dict[str, Any]:
    base_url = os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev")
    token = os.getenv("AGENT_API_TOKEN", "").strip()
    cloud = CloudState(base_url, token=token, timeout_seconds=25)
    payload = load_report(report_path)
    rows = candidates_for_queue(payload)
    if not rows:
        summary = {
            "ok": True,
            "status": "empty_report",
            "received_count": 0,
            "inserted_count": 0,
            "duplicate_count": 0,
            "invalid_count": 0,
        }
    else:
        summary = {"ok": True, "status": "enqueued", **cloud.enqueue_automation_items(rows)}
    summary["report_generated_at"] = str(payload.get("generated_at") or "")[:80]
    summary["queue_counts"] = cloud.get_automation_queue_summary()
    summary["queue_inventory"] = queue_inventory_lines(summary["queue_counts"])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("artifacts/termux_discovery.json"))
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()
    try:
        result = run_intake(args.report)
    except Exception as exc:
        result = {
            "ok": False,
            "status": "failed",
            "error": re.sub(r"[^A-Za-z0-9_-]", "", type(exc).__name__) or "unknown_error",
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not args.no_notify:
        if result.get("ok"):
            message = (
                "📥 صف ویدئوهای روبیکا به‌روزرسانی شد.\n"
                f"موارد جدید: {result.get('inserted_count', 0)}\n"
                f"تکراری‌ها: {result.get('duplicate_count', 0)}\n"
                f"موارد نامعتبر: {result.get('invalid_count', 0)}\n"
                + str(result.get("queue_inventory") or "")
            )
        else:
            message = "🚨 افزودن اطلاعات به صف ناموفق بود.\nکد خطا: " + str(result.get("error") or "unknown_error")
        sent = send_owner_notification(message)
        result["owner_notification_sent"] = bool(sent.get("sent"))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
