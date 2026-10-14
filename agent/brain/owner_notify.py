"""Best-effort Rubika owner notifications; never log credentials or private IDs."""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any


def send_owner_notification(message: str) -> dict[str, Any]:
    """Send a compact report to the configured owner chat; do nothing if not configured."""
    token = os.getenv("RUBIKA_TOKEN", "").strip()
    owner_id = os.getenv("RUBIKA_OWNER_CHAT_ID", "").strip()
    text = str(message or "").strip()[:3500]
    if not token or not owner_id or not text:
        return {"sent": False, "reason": "owner_notification_not_configured"}
    try:
        from rubka import Bot
        bot = Bot(token=token, timeout=25, show_progress=False)
        raw = bot.send_message(chat_id=owner_id, text=text)
        result: dict[str, Any]
        if isinstance(raw, dict):
            result = raw
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                result = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                result = {}
        else:
            result = {}
        if result.get("status") == "OK":
            return {"sent": True}
        return {"sent": False, "reason": "rubika_notification_rejected"}
    except Exception as exc:
        return {"sent": False, "reason": "rubika_notification_" + type(exc).__name__}


def queue_inventory_lines(counts: dict[str, int] | None) -> str:
    counts = counts or {}
    def get(key: str) -> int:
        value = counts.get(key, 0)
        return value if type(value) is int and value >= 0 else 0
    return (
        "📦 وضعیت صف: "
        f"در انتظار {get('queued')} | در حال پردازش {get('processing')} | "
        f"ارسال‌شده {get('sent')} | ناموفق {get('failed')} | "
        f"نامشخص {get('uncertain')} | ردشده {get('skipped')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=("workflow_failed", "queue_intake", "test"), default="test")
    parser.add_argument("--message", default="")
    parser.add_argument("--workflow", default="Rubika-Agent automation")
    parser.add_argument("--reason", default="unknown_error")
    args = parser.parse_args()

    if args.message.strip():
        message = args.message.strip()
    elif args.event == "workflow_failed":
        reason = re.sub(r"[^A-Za-z0-9_-]", "", args.reason)[:80] or "unknown_error"
        message = (
            "🚨 هشدار عامل روبیکا\n"
            f"گردش‌کار: {args.workflow[:100]}\n"
            f"کد خطا: {reason}\n"
            "هیچ توکن یا اطلاعات محرمانه‌ای در این گزارش درج نشده است."
        )
    elif args.event == "queue_intake":
        message = "📥 دریافت فهرست جدید انجام شد. برای جزئیات، گزارش گردش‌کار را بررسی کن."
    else:
        message = "✅ پیام آزمایشی عامل روبیکا. اعلان مالک فعال است."
    result = send_owner_notification(message)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("sent") or result.get("reason") == "owner_notification_not_configured" else 1


if __name__ == "__main__":
    raise SystemExit(main())
