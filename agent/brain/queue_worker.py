"""Continuously process eligible TEST-only TikTok queue items within a safe runtime budget."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agent.brain.cloud_state import CloudState, CloudStateError
from agent.brain.e2e_from_report import run_one
from agent.brain.owner_notify import queue_inventory_lines, send_owner_notification
# The global lifecycle birth gate is reserved for official Mob news, not TikTok edits.


QUEUE_READ_LIMIT = 100  # API page size; not a per-run send ceiling.
MAX_QUEUE_OFFSET = 1_000_000  # CloudState API pagination boundary.
DEFAULT_TIME_BUDGET_SECONDS = 9_000  # 150 min in a 180 min workflow job.
DEFAULT_SEND_DELAY_SECONDS = 3.0
MAX_SEND_DELAY_SECONDS = 60.0
PLATFORMS = ("tiktok",)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_code(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(value or "unknown_error"))[:80] or "unknown_error"


def _safe_cloud_state_error_detail(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    raw_status = value.get("status_code")
    status_code = raw_status if type(raw_status) is int and 100 <= raw_status <= 599 else None
    raw_endpoint = value.get("endpoint_path")
    endpoint_path = (
        raw_endpoint
        if isinstance(raw_endpoint, str)
        and re.fullmatch(r"/api/brain/[A-Za-z0-9_{}-]+(?:/[A-Za-z0-9_{}-]+)*", raw_endpoint)
        else None
    )
    raw_code = value.get("code")
    code = _safe_code(raw_code) if isinstance(raw_code, str) else None
    if status_code is None and endpoint_path is None and code is None:
        return None
    return {"status_code": status_code, "endpoint_path": endpoint_path, "code": code}


def _cloud_state_error_detail(exc: Exception) -> dict[str, Any] | None:
    if not isinstance(exc, CloudStateError):
        return None
    return _safe_cloud_state_error_detail({
        "status_code": exc.status_code,
        "endpoint_path": exc.endpoint_path,
        "code": exc.code,
    })


def _safe_music_probability(value: Any) -> float | None:
    if type(value) not in {int, float}:
        return None
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) and 0.0 <= numeric <= 1.0 else None


def _safe_failure_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    """Preserve actionable, sanitized staging detail without copying provider payloads."""
    diagnostics: dict[str, Any] = {}
    moderation = result.get("moderation") if isinstance(result.get("moderation"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    raw_probability = (
        result.get("music_probability")
        if "music_probability" in result
        else moderation.get("music_probability")
    )
    raw_audio = (
        result.get("has_audio_stream")
        if "has_audio_stream" in result
        else validation.get("has_audio")
    )
    diagnostics["music_probability"] = _safe_music_probability(raw_probability)
    diagnostics["has_audio_stream"] = raw_audio if type(raw_audio) is bool else None

    raw_details = result.get("failure_details")
    details: list[dict[str, str]] = []
    if isinstance(raw_details, (list, tuple)):
        for row in raw_details[:8]:
            if not isinstance(row, dict):
                continue
            raw_item = str(row.get("item") or "unknown")
            raw_reason = str(row.get("reason") or "unknown_error")
            item = raw_item if re.fullmatch(r"[A-Za-z0-9._:-]{1,140}", raw_item) else "unknown"
            reason = raw_reason if re.fullmatch(r"[A-Za-z0-9_.,:-]{1,180}", raw_reason) else "unknown_error"
            details.append({"item": item, "reason": reason})
    if details:
        diagnostics["failure_details"] = details

    raw_sync_errors = result.get("cloud_sync_errors")
    sync_errors: list[str] = []
    if isinstance(raw_sync_errors, (list, tuple)):
        for raw in raw_sync_errors[:5]:
            value = str(raw or "")
            match = re.fullmatch(
                r"([A-Za-z0-9_-]{1,24}):([A-Za-z0-9._-]{1,120}):([A-Za-z][A-Za-z0-9_-]{0,79})",
                value,
            )
            sync_errors.append(":".join(match.groups()) if match else "cloud_sync_error")
    if sync_errors:
        diagnostics["cloud_sync_errors"] = sync_errors

    cloud_error = _safe_cloud_state_error_detail(result.get("cloud_state_error"))
    if cloud_error is not None:
        diagnostics["cloud_state_error"] = cloud_error

    reuse_error = result.get("previous_staged_copy_rejected")
    if isinstance(reuse_error, str) and reuse_error:
        diagnostics["previous_staged_copy_rejected"] = _safe_code(reuse_error)

    stage_detail = result.get("stage")
    if isinstance(stage_detail, dict):
        for key in ("found_count", "authorized_count", "staged_count"):
            value = stage_detail.get(key)
            if type(value) is int and value >= 0:
                diagnostics[key] = value

    return diagnostics


def _single_candidate_report(item: dict[str, Any], destination: Path) -> Path:
    platform = str(item["platform"])
    metadata: dict[str, Any] = {}
    raw_metadata = item.get("metadata_json")
    if isinstance(raw_metadata, str):
        try:
            parsed = json.loads(raw_metadata)
            if isinstance(parsed, dict):
                metadata = parsed
        except json.JSONDecodeError:
            metadata = {}
    row = {
        "id": str(item["external_id"]),
        "url": str(item["canonical_url"]),
        "title": str(item.get("title") or ""),
        "caption": str(item.get("caption") or ""),
        "author": str(item.get("source_key") or ""),
        "source_key": str(item.get("source_key") or ""),
        "media_type": str(item.get("media_type") or "video"),
        "published_at": item.get("published_at"),
        "freshness_verified": item.get("freshness_verified") == 1 or item.get("freshness_verified") is True,
        "freshness_source": metadata.get("freshness_source"),
        "method": item.get("method") or metadata.get("source_method") or "persistent_queue",
    }
    payload = {
        "schema_version": 1,
        "source": "persistent_d1_queue",
        "generated_at": _utc_now(),
        "mode": "metadata_only_no_download_no_publish",
        "summary": {"tiktok_candidates": 0, "youtube_candidates": 0, "instagram_candidates": 0},
        "candidates": {"tiktok": [], "youtube": [], "instagram": []},
        "attempts": [],
    }
    payload["candidates"][platform] = [row]
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def _mega_inventory() -> dict[str, Any]:
    """Read only aggregate counts; never include file names or credentials in notifications."""
    try:
        from agent.brain.ledger import MegaLedger
        from agent.storage.mega import MegaStorage
        ledger = MegaLedger(MegaStorage(root="/Rubika-Agent", timeout_seconds=90))
        state = ledger.load()
        rows = state.get("items", {})
        if not isinstance(rows, dict):
            return {"available": False}
        counts = Counter()
        for row in rows.values():
            if isinstance(row, dict):
                counts[str(row.get("status") or "unknown")[:30]] += 1
        return {"available": True, "total": len(rows), "by_status": dict(counts)}
    except Exception:
        return {"available": False}


def _inventory_text(queue_counts: dict[str, int], ledger: dict[str, Any]) -> str:
    lines = [queue_inventory_lines(queue_counts)]
    if ledger.get("available") is True:
        by_status = ledger.get("by_status") if isinstance(ledger.get("by_status"), dict) else {}
        ready = int(by_status.get("ready", 0) or 0)
        sent = int(by_status.get("sent", 0) or 0)
        lines.append(f"🗄️ انبار رسانه: {ledger.get('total', 0)} رکورد | آماده {ready} | ارسال‌شده {sent}")
    else:
        lines.append("🗄️ موجودی انبار رسانه این بار قابل خواندن نبود.")
    return "\n".join(lines)


def run_daily(
    *,
    time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS,
    send_delay_seconds: float = DEFAULT_SEND_DELAY_SECONDS,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    if type(time_budget_seconds) is not int or not 1 <= time_budget_seconds <= 10_800:
        raise ValueError("time_budget_seconds_out_of_range")
    if (
        type(send_delay_seconds) not in {int, float}
        or not math.isfinite(float(send_delay_seconds))
        or not 0 <= float(send_delay_seconds) <= MAX_SEND_DELAY_SECONDS
    ):
        raise ValueError("send_delay_seconds_out_of_range")

    monotonic = clock or time.monotonic
    sleep = sleeper or time.sleep
    started_monotonic = monotonic()
    cloud = CloudState(
        os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev"),
        token=os.getenv("AGENT_API_TOKEN", ""),
        timeout_seconds=25,
    )
    report: dict[str, Any] = {
        "ok": True,
        "status": "running",
        "started_at": _utc_now(),
        "lifecycle_mode": "not_applied_to_tiktok_edits",
        "agent_born_at": None,
        "birth_cutoff_enabled": False,
        "daily_success_limit": None,
        "max_candidate_attempts": None,
        "time_budget_seconds": time_budget_seconds,
        "send_delay_seconds": float(send_delay_seconds),
        "attempted_count": 0,
        "sent_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "rate_limited_count": 0,
        "queue_pages_read": 0,
        "results": [],
        "errors": [],
    }

    initial_counts = cloud.get_automation_queue_summary()
    sent_today_before = int(initial_counts.get("sent_today", 0) or 0)
    report["daily_sent_before"] = sent_today_before

    candidates: list[dict[str, Any]] = []
    offset = 0
    while monotonic() - started_monotonic < time_budget_seconds:
        queue_response = cloud.get_automation_queue(
            status="queued", limit=QUEUE_READ_LIMIT, offset=offset
        )
        page = queue_response.get("items", [])
        if not isinstance(page, list):
            raise RuntimeError("automation_queue_invalid")
        report["queue_pages_read"] += 1
        if not page:
            break
        candidates.extend(item for item in page if isinstance(item, dict))
        if len(page) < QUEUE_READ_LIMIT:
            break
        offset += len(page)
        if offset > MAX_QUEUE_OFFSET:
            raise CloudStateError("control_api_automation_queue_offset_limit")
    else:
        report["time_budget_exhausted"] = True
        report["stop_reason"] = "time_budget_exhausted"

    # SQL pages are oldest-first. Stable grouping prioritizes TikTok while preserving
    # oldest-first order within TikTok; paused platforms stay queued.
    candidates.sort(
        key=lambda item: 0 if item.get("platform") in PLATFORMS else 1
    )
    report["queued_at_start"] = len(candidates)

    if not candidates and not report.get("time_budget_exhausted"):
        report["status"] = "queue_empty"
        report["finished_at"] = _utc_now()
        report["queue_counts"] = cloud.get_automation_queue_summary()
        report["daily_sent_today"] = int(report["queue_counts"].get("sent_today", 0) or 0)
        report["inventory"] = _mega_inventory()
        report["inventory_text"] = _inventory_text(report["queue_counts"], report["inventory"])
        return report

    with tempfile.TemporaryDirectory(prefix="rubika-agent-queue-") as folder:
        root = Path(folder)
        for item in candidates:
            elapsed = monotonic() - started_monotonic
            if elapsed >= time_budget_seconds:
                report["time_budget_exhausted"] = True
                report["stop_reason"] = "time_budget_exhausted"
                break

            # Pace successful sends. Do not begin a delay that would consume the
            # remaining safety margin, and never sleep past the job's safe budget.
            if report["sent_count"] and send_delay_seconds:
                if elapsed + float(send_delay_seconds) >= time_budget_seconds:
                    report["time_budget_exhausted"] = True
                    report["stop_reason"] = "time_budget_exhausted"
                    break
                sleep(float(send_delay_seconds))
                if monotonic() - started_monotonic >= time_budget_seconds:
                    report["time_budget_exhausted"] = True
                    report["stop_reason"] = "time_budget_exhausted"
                    break

            platform = str(item.get("platform") or "")
            external_id = str(item.get("external_id") or "")
            queue_id = str(item.get("id") or "")
            if not external_id or not queue_id:
                report["skipped_count"] += 1
                continue

            if platform not in PLATFORMS:
                report["skipped_count"] += 1
                report["results"].append({
                    "platform": platform,
                    "external_id": external_id,
                    "status": "deferred",
                    "reason": "platform_paused",
                    "queue_state": "left_queued",
                })
                continue

            try:
                cloud.update_automation_queue_item(queue_id, "processing")
            except Exception as exc:
                report["ok"] = False
                report["errors"].append({
                    "platform": platform, "external_id": external_id,
                    "error": "queue_claim_" + _safe_code(type(exc).__name__),
                })
                continue

            one_report = root / f"{platform}-{external_id}.json"
            result_path = root / f"result-{platform}-{external_id}.json"
            try:
                _single_candidate_report(item, one_report)
                result = run_one(
                    one_report,
                    platform,
                    result_path,
                    candidate_limit=1,
                    candidate_offset=0,
                )
                if not isinstance(result, dict):
                    result = {"ok": False, "status": "error", "reason": "invalid_worker_result"}
            except Exception as exc:
                result = {
                    "ok": False,
                    "status": "error",
                    "reason": _safe_code(getattr(exc, "code", None) or type(exc).__name__),
                }
                cloud_error = _cloud_state_error_detail(exc)
                if cloud_error is not None:
                    result["cloud_state_error"] = cloud_error

            status = str(result.get("status") or "error")
            report["attempted_count"] += 1
            result_entry = {
                "platform": platform,
                "external_id": external_id,
                "title": str(item.get("title") or "")[:250],
                "status": status,
                "reason": _safe_code(result.get("reason") or result.get("error") or status),
            }
            result_entry.update(_safe_failure_diagnostics(result))

            if status == "sent" and result.get("ok") is True:
                try:
                    cloud.update_automation_queue_item(queue_id, "sent")
                except Exception as exc:
                    report["ok"] = False
                    report["errors"].append({
                        "platform": platform,
                        "external_id": external_id,
                        "error": "sent_but_queue_update_" + _safe_code(type(exc).__name__),
                    })
                report["sent_count"] += 1
                result_entry["message"] = "published"
                try:
                    counts = cloud.get_automation_queue_summary()
                    ledger = _mega_inventory()
                    inventory = _inventory_text(counts, ledger)
                except Exception:
                    inventory = "📦 موجودی صف پس از انتشار این بار قابل دریافت نبود."
                message = (
                    "✅ انتشار موفق ویدئوی ادیت\n"
                    f"پلتفرم: {platform}\n"
                    f"عنوان: {str(item.get('title') or 'بدون عنوان')[:250]}\n"
                    "سقف روزانه: ندارد (مقصد فقط کانال تست)\n"
                    f"موفق‌های امروز: {sent_today_before + report['sent_count']}\n"
                    + inventory
                )
                notify_result = send_owner_notification(message)
                result_entry["owner_notification_sent"] = bool(notify_result.get("sent"))

            elif status == "rate_limited" or "rubika_rate_limited" in result_entry["reason"]:
                # A clear HTTP/API flood rejection did not publish. Return this queue
                # row to queued and stop; never hammer Rubika or advance to another item.
                try:
                    cloud.update_automation_queue_item(
                        queue_id, "queued", error_code="rubika_rate_limited"
                    )
                    result_entry["queue_state"] = "left_queued"
                except Exception as exc:
                    report["ok"] = False
                    report["errors"].append({
                        "platform": platform, "external_id": external_id,
                        "error": "rate_limit_requeue_" + _safe_code(type(exc).__name__),
                    })
                report["rate_limited_count"] += 1
                report["rate_limited"] = True
                report["stop_reason"] = "rubika_rate_limited"
                result_entry["halt_reason"] = "rubika_rate_limited_stop_run"
                report["results"].append(result_entry)
                break

            elif status == "uncertain" or str(
                result.get("delivery", {}).get("status")
                if isinstance(result.get("delivery"), dict) else ""
            ) == "uncertain":
                cloud.update_automation_queue_item(
                    queue_id, "uncertain", error_code="delivery_uncertain"
                )
                report["failed_count"] += 1
                report["errors"].append({
                    "platform": platform, "external_id": external_id,
                    "error": "delivery_uncertain",
                })
                result_entry["halt_reason"] = "delivery_uncertain_requires_receipt_reconciliation"
                report["results"].append(result_entry)
                break

            elif status == "no_candidate":
                cloud.update_automation_queue_item(
                    queue_id, "skipped", error_code="already_seen_or_unavailable"
                )
                report["skipped_count"] += 1

            else:
                reason = _safe_code(result.get("reason") or result.get("error") or status)
                cloud.update_automation_queue_item(queue_id, "failed", error_code=reason)
                report["failed_count"] += 1
                report["errors"].append({
                    "platform": platform, "external_id": external_id, "error": reason,
                })
            report["results"].append(result_entry)

    report["finished_at"] = _utc_now()
    report["queue_counts"] = cloud.get_automation_queue_summary()
    report["daily_sent_today"] = int(report["queue_counts"].get("sent_today", 0) or 0)
    has_queue_claim_error = any(
        error.get("error", "").startswith("queue_claim_") for error in report["errors"]
    )

    if report.get("rate_limited"):
        report["status"] = "rate_limited"
    elif report.get("time_budget_exhausted"):
        report["status"] = "time_budget_exhausted"
    elif report["failed_count"]:
        report["status"] = "partial" if report["sent_count"] else "failed"
    elif report["sent_count"]:
        report["status"] = "sent"
    elif report["queue_counts"].get("queued", 0):
        report["status"] = "partial"
    else:
        report["status"] = "queue_empty"

    # Time-budget and explicit flood stops are graceful. A definite media failure
    # or an uncertain delivery still fails the run; uncertain always stops the loop.
    report["ok"] = (
        report["ok"]
        and not has_queue_claim_error
        and (report["failed_count"] == 0 or bool(report.get("rate_limited")))
    )
    ledger = _mega_inventory()
    report["inventory"] = ledger
    report["inventory_text"] = _inventory_text(report["queue_counts"], ledger)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--time-budget-seconds", type=int, default=DEFAULT_TIME_BUDGET_SECONDS,
        help="Graceful per-run processing budget; must remain below the Actions job timeout.",
    )
    parser.add_argument(
        "--send-delay-seconds", type=float, default=DEFAULT_SEND_DELAY_SECONDS,
        help="Delay between confirmed TikTok sends (0-60 seconds).",
    )
    parser.add_argument("--report", type=Path, default=Path("artifacts/automation-daily-report.json"))
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()
    try:
        result = run_daily(
            time_budget_seconds=args.time_budget_seconds,
            send_delay_seconds=args.send_delay_seconds,
        )
    except Exception as exc:
        result = {
            "ok": False,
            "status": "failed",
            "error": _safe_code(type(exc).__name__),
            "sent_count": 0,
            "failed_count": 1,
            "results": [],
            "errors": [{"error": _safe_code(type(exc).__name__)}],
            "finished_at": _utc_now(),
        }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": result.get("ok"),
        "status": result.get("status"),
        "sent_count": result.get("sent_count", 0),
        "failed_count": result.get("failed_count", 0),
        "skipped_count": result.get("skipped_count", 0),
        "rate_limited_count": result.get("rate_limited_count", 0),
        "time_budget_seconds": result.get("time_budget_seconds"),
        "queued_remaining": (result.get("queue_counts") or {}).get("queued"),
        "report": str(args.report),
    }, ensure_ascii=False, sort_keys=True))
    if not args.no_notify:
        error_rows = result.get("errors") if isinstance(result.get("errors"), list) else []
        error_lines = []
        for error_row in error_rows[:4]:
            if not isinstance(error_row, dict):
                continue
            platform = re.sub(r"[^A-Za-z0-9_-]", "", str(error_row.get("platform") or ""))[:20]
            external_id = re.sub(r"[^A-Za-z0-9._-]", "", str(error_row.get("external_id") or ""))[:40]
            code = _safe_code(error_row.get("error") or "unknown_error")
            identity = f" ({platform}:{external_id})" if platform and external_id else ""
            error_lines.append(f"• {code}{identity}")
        error_summary = ("\nآخرین کدهای خطا:\n" + "\n".join(error_lines)) if error_lines else ""
        message = (
            "📊 گزارش پایان نوبت خودکار روبیکا\n"
            f"ویدئوهای موفق در این اجرا: {result.get('sent_count', 0)} | مجموع امروز: {result.get('daily_sent_today', result.get('daily_sent_before', 0))} | سقف روزانه: ندارد\n"
            f"تلاش‌ها: {result.get('attempted_count', 0)} | خطاها: {result.get('failed_count', 0)} | ردشده‌ها: {result.get('skipped_count', 0)}\n"
            f"وقفه بین ارسال: {args.send_delay_seconds:g} ثانیه | بودجهٔ اجرا: {args.time_budget_seconds} ثانیه\n"
            f"وضعیت: {result.get('status', 'unknown')}\n"
            + str(result.get("inventory_text") or queue_inventory_lines(result.get("queue_counts") or {}))
            + error_summary
        )
        notify = send_owner_notification(message)
        result["final_notification_sent"] = bool(notify.get("sent"))
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
