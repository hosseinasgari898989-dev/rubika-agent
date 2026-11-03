"""Read-only health snapshot for the existing owner digest.

Only public GitHub workflow metadata is fetched at runtime. Quota counters and
MEGA usage are deliberately reported as unavailable: the digest job has no
authorized Cloudflare/MEGA telemetry access and this module never reads secrets.
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable


GITHUB_REPOSITORY = "hosseinasgari898989-dev/rubika-agent"
GITHUB_API_BASE = "https://api.github.com/repos/" + GITHUB_REPOSITORY
GITHUB_TIMEOUT_SECONDS = 6
GITHUB_RESPONSE_MAX_BYTES = 512 * 1024

D1_LIMIT_BYTES = 500 * 1024 * 1024
D1_WARN_BYTES = 50 * 1024 * 1024
D1_PV_ALERT_BYTES = 250 * 1024 * 1024
D1_LAST_SAMPLE_BYTES = 991_232
D1_LAST_SAMPLE_DATE = "2026-11-03"

MEGA_LIMIT_BYTES = 20 * 1024 * 1024 * 1024
MEGA_WARN_BYTES = int(MEGA_LIMIT_BYTES * 0.70)

DAILY_LIMITS = {
    "d1_rows_written": 100_000,
    "d1_rows_read": 5_000_000,
    "worker_requests": 100_000,
    "workers_ai_neurons": 10_000,
}

WORKFLOW_MONITORS = {
    "termux_discovery": {
        "file": "termux-docker-discovery.yml",
        "event": "schedule",
        "label": "Termux discovery",
    },
    "daily_queue": {
        "file": "daily-queue-processor.yml",
        "event": "schedule",
        "label": "Daily queue",
    },
    "mob_x": {
        "file": "mob-x-link-processor.yml",
        "event": "schedule",
        "label": "Mob X processor",
    },
    "deploy_worker": {
        "file": "deploy-worker.yml",
        "event": "push",
        "label": "Deploy Worker (push)",
    },
}

_FAILURE_CONCLUSIONS = {"failure", "timed_out", "action_required"}
_NEUTRAL_CONCLUSIONS = {"cancelled", "skipped", "neutral", "stale"}


def unavailable_daily_usage() -> dict[str, int | None]:
    """No D1/Workers/Workers AI daily telemetry is exposed to this digest job."""
    return {key: None for key in DAILY_LIMITS}


def get_daily_usage_snapshot() -> dict[str, int | None]:
    """Return only counters genuinely accessible to the digest runtime."""
    return unavailable_daily_usage()


def unavailable_health_snapshot() -> dict[str, Any]:
    return {
        "sampled_at": None,
        "d1": {
            "sample_bytes": D1_LAST_SAMPLE_BYTES,
            "sample_date": D1_LAST_SAMPLE_DATE,
            "live_size_available": False,
        },
        "daily_usage": unavailable_daily_usage(),
        "workflows": {
            key: {
                "available": False,
                "consecutive_failures": None,
                "last_run_number": None,
                "last_conclusion": None,
            }
            for key in WORKFLOW_MONITORS
        },
        "mega": {"available": False, "used_bytes": None, "free_bytes": None},
    }


def should_reduce_nonessential_writes(snapshot: Mapping[str, Any] | None) -> bool:
    """Return true only when a real daily counter reaches 70% of its free limit.

    Queue, dedupe, delivery, error notifications and digest state writes are not
    affected. Only low-priority informational/success event-ledger inserts may
    be omitted by the caller.
    """
    if not isinstance(snapshot, Mapping):
        return False
    usage = snapshot.get("daily_usage")
    if not isinstance(usage, Mapping):
        return False
    for key, limit in DAILY_LIMITS.items():
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(float(value)) or value < 0:
            continue
        if value >= limit * 0.70:
            return True
    return False


def _read_json(url: str, opener: Any | None = None) -> dict[str, Any] | None:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Rubika-Agent-Owner-Digest-Health/1.0",
        },
        method="GET",
    )
    try:
        with (opener or urllib.request).urlopen(request, timeout=GITHUB_TIMEOUT_SECONDS) as response:
            raw = response.read(GITHUB_RESPONSE_MAX_BYTES + 1)
        if len(raw) > GITHUB_RESPONSE_MAX_BYTES:
            return None
        decoded = json.loads(raw.decode("utf-8")) if raw else {}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def fetch_workflow_failure_streak(
    key: str,
    *,
    opener: Any | None = None,
) -> dict[str, Any]:
    monitor = WORKFLOW_MONITORS.get(key)
    if monitor is None:
        return {
            "available": False,
            "consecutive_failures": None,
            "last_run_number": None,
            "last_conclusion": None,
        }

    workflow_file = monitor["file"]
    event_filter = monitor["event"]
    url = (
        f"{GITHUB_API_BASE}/actions/workflows/{workflow_file}/runs"
        "?per_page=20"
    )
    payload = _read_json(url, opener=opener)
    if payload is None:
        return {
            "available": False,
            "consecutive_failures": None,
            "last_run_number": None,
            "last_conclusion": None,
        }

    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        return {
            "available": False,
            "consecutive_failures": None,
            "last_run_number": None,
            "last_conclusion": None,
        }

    consecutive = 0
    last_number: int | None = None
    last_conclusion: str | None = None
    saw_relevant = False

    # API returns newest first. Ignore non-main runs, other event types, running
    # jobs and neutral/cancelled/skipped outcomes; stop at the first success.
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        if run.get("head_branch") != "main" or run.get("event") != event_filter:
            continue
        if run.get("status") != "completed":
            continue
        conclusion = str(run.get("conclusion") or "").lower()
        if conclusion in _NEUTRAL_CONCLUSIONS or not conclusion:
            continue
        if conclusion not in _FAILURE_CONCLUSIONS and conclusion != "success":
            continue

        run_number = run.get("run_number")
        if last_number is None and isinstance(run_number, int) and not isinstance(run_number, bool):
            last_number = run_number
        if last_conclusion is None:
            last_conclusion = conclusion
        saw_relevant = True

        if conclusion == "success":
            break
        consecutive += 1

    return {
        "available": True,
        "consecutive_failures": consecutive if saw_relevant else 0,
        "last_run_number": last_number,
        "last_conclusion": last_conclusion,
    }


def build_health_snapshot(
    *,
    opener: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    snapshot = unavailable_health_snapshot()
    snapshot["sampled_at"] = current.isoformat()
    snapshot["workflows"] = {
        key: fetch_workflow_failure_streak(key, opener=opener)
        for key in WORKFLOW_MONITORS
    }
    # The sample is a dated one-off read from Cloudflare's D1 metadata API.
    # Runtime live D1 size, daily usage analytics and MEGA usage are unavailable
    # to the digest job without adding credentials, which is intentionally barred.
    return snapshot


def _format_bytes(value: int) -> str:
    return f"{value / (1024 * 1024):.2f} MiB"


def render_health_section(snapshot: Mapping[str, Any] | None) -> list[str]:
    data = snapshot if isinstance(snapshot, Mapping) else unavailable_health_snapshot()
    lines = ["📊 پایش سلامت (فقط‌خواندنی)"]

    d1 = data.get("d1") if isinstance(data.get("d1"), Mapping) else {}
    sample_bytes = d1.get("sample_bytes")
    sample_date = str(d1.get("sample_date") or "نامشخص")
    if isinstance(sample_bytes, int) and not isinstance(sample_bytes, bool) and sample_bytes >= 0:
        pct = sample_bytes / D1_LIMIT_BYTES * 100
        current_date = str(data.get("sampled_at") or "")[:10]
        freshness = "نمونهٔ امروز" if current_date == sample_date else "نمونهٔ تاریخی"
        lines.append(
            f"• D1: آخرین نمونه {freshness} {sample_date}، {_format_bytes(sample_bytes)} "
            f"از 500 MiB ({pct:.2f}٪)؛ اندازهٔ زنده در digest قابل خواندن نیست."
        )
        if sample_bytes >= D1_PV_ALERT_BYTES:
            lines.append("🚨 هشدار PV: اندازهٔ D1 از 250 MiB عبور کرده است.")
        elif sample_bytes >= D1_WARN_BYTES:
            lines.append("⚠️ هشدار ظرفیت D1: اندازه از 50 MiB عبور کرده است.")
    else:
        lines.append("• D1: اندازهٔ فعلی از محیط digest قابل خواندن نیست؛ حدود هشدار 50 MiB و PV برابر 250 MiB از سقف 500 MiB است.")

    usage = data.get("daily_usage") if isinstance(data.get("daily_usage"), Mapping) else {}
    usage_labels = {
        "d1_rows_written": ("D1 write", DAILY_LIMITS["d1_rows_written"]),
        "d1_rows_read": ("D1 read", DAILY_LIMITS["d1_rows_read"]),
        "worker_requests": ("Worker request", DAILY_LIMITS["worker_requests"]),
        "workers_ai_neurons": ("Workers AI neuron", DAILY_LIMITS["workers_ai_neurons"]),
    }
    usage_parts = []
    for key, (label, limit) in usage_labels.items():
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and value >= 0:
            usage_parts.append(f"{label} {value:,.0f}/{limit:,}")
            if value >= limit * 0.70:
                lines.append(f"⚠️ هشدار سهمیه: {label} به آستانهٔ 70٪ رسیده است.")
        else:
            usage_parts.append(f"{label}: ناموجود")
    lines.append("• مصرف روزانه: " + " | ".join(usage_parts) + "؛ هشدار از 70٪.")

    workflows = data.get("workflows") if isinstance(data.get("workflows"), Mapping) else {}
    for key, monitor in WORKFLOW_MONITORS.items():
        result = workflows.get(key)
        if not isinstance(result, Mapping) or result.get("available") is not True:
            lines.append(f"• {monitor['label']}: وضعیت اجرا در دسترس نیست.")
            continue
        streak = result.get("consecutive_failures")
        if not isinstance(streak, int) or isinstance(streak, bool) or streak < 0:
            lines.append(f"• {monitor['label']}: شمار شکست متوالی ناموجود.")
            continue
        run_number = result.get("last_run_number")
        conclusion = str(result.get("last_conclusion") or "نامشخص")
        suffix = f"؛ آخرین run #{run_number} ({conclusion})" if isinstance(run_number, int) else ""
        lines.append(f"• {monitor['label']}: {streak} شکست متوالی{suffix}.")

    mega = data.get("mega") if isinstance(data.get("mega"), Mapping) else {}
    if mega.get("available") is True and isinstance(mega.get("used_bytes"), int):
        used = mega["used_bytes"]
        pct = used / MEGA_LIMIT_BYTES * 100
        lines.append(f"• MEGA: {_format_bytes(used)} از 20 GiB ({pct:.1f}٪)؛ هشدار در 14 GiB.")
        if used >= MEGA_WARN_BYTES:
            lines.append("⚠️ هشدار فضای MEGA: مصرف به 70٪ رسیده است.")
    else:
        lines.append("• MEGA: مصرف و فضای خالی برای digest قابل خواندن نیست؛ هشدار در 14 GiB از 20 GiB (70٪).")

    if should_reduce_nonessential_writes(data):
        lines.append("• کاهش بار فعال: فقط ثبت رویدادهای info/success کم‌اهمیت کم می‌شود؛ صف، dedupe، خطاها و ارسال تغییر نمی‌کنند.")
    else:
        lines.append("• کاهش بار خودکار فعال نیست؛ شمارنده‌های روزانهٔ مصرف در محیط digest قابل اندازه‌گیری نیستند. صف، dedupe و ارسال دست‌نخورده‌اند.")

    return lines
