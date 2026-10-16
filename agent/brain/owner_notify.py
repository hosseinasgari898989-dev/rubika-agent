"""Private Rubika owner digest: one editable message per rolling 24-hour window.

All events are stored by the authenticated Worker/D1 API. Provider and transport
failures are sanitized; notifications never block the underlying automation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


DIGEST_MAX_CHARS = 3400
DIGEST_CLAIM_ATTEMPTS = 8
DIGEST_CLAIM_DELAY_SECONDS = 0.35


def _safe_diagnostic_value(value: Any, secrets: list[str]) -> str | None:
    if value is None or not isinstance(value, (str, int, float, bool)):
        return None
    rendered = str(value)
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "[REDACTED]")
    rendered = re.sub(r"[A-Za-z0-9_-]{32,}", "[REDACTED]", rendered)
    rendered = re.sub(r"[\r\n\t]+", " ", rendered)
    rendered = re.sub(r"[^A-Za-z0-9_. \-\[\]]", "_", rendered)
    return rendered[:100]


def _response_diagnostic(raw: Any, secrets: list[str]) -> dict[str, Any]:
    diagnostic: dict[str, Any] = {
        "stage": "api_response",
        "response_type": type(raw).__name__,
    }
    payload: dict[str, Any] | None = None
    if isinstance(raw, dict):
        payload = raw
        diagnostic["json_decoded"] = True
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
                diagnostic["json_decoded"] = True
            else:
                diagnostic["json_decoded"] = False
        except json.JSONDecodeError:
            diagnostic["json_decoded"] = False
    else:
        diagnostic["json_decoded"] = False

    if payload is not None:
        diagnostic["response_keys"] = sorted(str(key)[:60] for key in payload.keys())[:12]
        for key in ("status", "status_det", "error_code", "code"):
            value = _safe_diagnostic_value(payload.get(key), secrets)
            if value is not None:
                diagnostic[key] = value
    return diagnostic


def _sanitize_text(value: Any, max_chars: int = 600) -> str:
    rendered = str(value or "")
    rendered = re.sub(r"Bearer\s+\S+", "[REDACTED]", rendered, flags=re.IGNORECASE)
    rendered = re.sub(
        r"(?:token|password|secret|authorization|cookie)\s*[:=]\s*[^\s,;]+",
        "[REDACTED]",
        rendered,
        flags=re.IGNORECASE,
    )
    rendered = re.sub(r"https?://\S+", "[URL omitted]", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"\b[A-Za-z0-9_-]{36,}\b", "[REDACTED]", rendered)
    rendered = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in rendered)
    return rendered.strip()[:max_chars]


def _worker_url() -> str:
    base = os.getenv(
        "AGENT_API_BASE_URL",
        "https://rubika-agent.h98609294.workers.dev",
    ).strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("owner_digest_api_url_invalid")
    return base


def _worker_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("AGENT_API_TOKEN", "").strip()
    if len(token) < 32:
        raise RuntimeError("owner_digest_api_token_missing")

    url = _worker_url() + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Rubika-Agent-Owner-Digest",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            raw = response.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise RuntimeError("owner_digest_api_response_too_large")
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raise RuntimeError("owner_digest_api_http_" + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Do not expose the exception text; it may contain a URL or request data.
        raise RuntimeError("owner_digest_api_" + type(exc).__name__) from None

    if not isinstance(decoded, dict) or decoded.get("ok") is not True:
        code = decoded.get("error", "owner_digest_api_response_invalid") if isinstance(decoded, dict) else "owner_digest_api_response_invalid"
        if not re.fullmatch(r"[a-z0-9_-]{1,80}", str(code)):
            code = "owner_digest_api_response_invalid"
        raise RuntimeError(str(code))
    return decoded


def _infer_category(message: str) -> str:
    text = message.strip().casefold()
    if text.startswith(("🚨", "⚠️", "❌")) or any(
        token in text for token in ("workflow failed", "خطا رخ داد", "ناموفق شد", "شکست خورد", "error:")
    ):
        return "error"
    if text.startswith(("✅", "🎉")) or any(
        token in text for token in ("با موفقیت انجام شد", "با موفقیت ثبت شد", "successfully completed")
    ):
        return "success"
    return "info"


def _parse_rubika_response(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _rubika_ok(raw: Any) -> bool:
    result = _parse_rubika_response(raw)
    if result.get("status") == "OK" or result.get("ok") is True:
        return True
    data = result.get("data")
    return isinstance(data, dict) and bool(data.get("message_id")) and result.get("status") not in {"ERROR", "FAILED"}


def _rubika_message_id(raw: Any) -> str | None:
    result = _parse_rubika_response(raw)
    candidates: list[Any] = [
        result.get("message_id"),
        result.get("result", {}).get("message_id") if isinstance(result.get("result"), dict) else None,
        result.get("data", {}).get("message_id") if isinstance(result.get("data"), dict) else None,
    ]
    data = result.get("data")
    if isinstance(data, dict) and isinstance(data.get("message"), dict):
        candidates.append(data["message"].get("message_id"))
    for item in candidates:
        if isinstance(item, (str, int)) and str(item).strip():
            value = str(item).strip()
            if len(value) <= 120:
                return value
    return None


def _make_rubika_bot(token: str) -> Any:
    # The supplied rubka-main.zip exports Bot as an alias of synchronous Robot.
    from rubka import Bot
    return Bot(token=token, timeout=25, show_progress=False)


def render_daily_digest(state: dict[str, Any]) -> str:
    counts = state.get("counts") if isinstance(state.get("counts"), dict) else {}
    successful = int(counts.get("success", 0) or 0) + int(counts.get("ai_success", 0) or 0)
    errors = int(counts.get("error", 0) or 0) + int(counts.get("ai_error", 0) or 0)
    info = int(counts.get("info", 0) or 0)
    start = str(state.get("window_started_at") or "نامشخص").replace("T", " ").replace("Z", " UTC")
    end = str(state.get("window_ends_at") or "نامشخص").replace("T", " ").replace("Z", " UTC")
    lines = [
        "🧠 اعلامیهٔ روزانهٔ مغز Rubika-Agent",
        f"🕒 بازهٔ ۲۴ ساعته: {start} تا {end}",
        f"✅ موفقیت‌ها: {successful} | ❌ خطاها: {errors} | ℹ️ اطلاعیه‌ها: {info}",
        "━━━━━━━━━━━━━━━━━━",
    ]

    events = state.get("events") if isinstance(state.get("events"), list) else []
    visible = events[-24:]
    total_events = max(0, int(state.get("total_event_count", len(events)) or 0))
    omitted = max(0, total_events - len(visible))
    for event in visible:
        if not isinstance(event, dict):
            continue
        category = str(event.get("category") or "info")
        icon = {
            "success": "✅",
            "ai_success": "🤖✅",
            "error": "❌",
            "ai_error": "🤖❌",
            "info": "ℹ️",
        }.get(category, "ℹ️")
        at = str(event.get("occurred_at") or "")[:16].replace("T", " ")
        source = _sanitize_text(event.get("source"), 50)
        title = _sanitize_text(event.get("title"), 140)
        detail = _sanitize_text(event.get("detail"), 220)
        row = f"{icon} {at} · {source}: {title}"
        if detail and detail != title:
            row += f"\n   {detail}"
        lines.append(row)

    if omitted:
        lines.append(f"… {omitted} رویداد قدیمی‌تر هم در سوابق این بازه ذخیره شده است.")
    elif not events:
        lines.append("هنوز رویدادی ثبت نشده؛ این پیام با رخدادهای جدید ویرایش می‌شود.")
    lines.append("این پیام در طول بازه ویرایش می‌شود؛ پیام تازه پس از پایان ۲۴ ساعت ساخته خواهد شد.")

    # Stay below Rubika message-size limits; newest visible entries are at the end.
    rendered = "\n".join(lines)
    if len(rendered) <= DIGEST_MAX_CHARS:
        return rendered
    suffix = "\n… برای جلوگیری از ردشدن پیام، جزئیات قدیمی‌تر خلاصه شد."
    while len(rendered) + len(suffix) > DIGEST_MAX_CHARS and len(lines) > 6:
        lines.pop(4)
        rendered = "\n".join(lines)
    return (rendered[:DIGEST_MAX_CHARS - len(suffix)] + suffix).strip()


def _commit_digest(
    *,
    window_id: str,
    lock_token: str,
    message_id: str | None,
    message_body: str,
    message_creation_uncertain: bool,
) -> bool:
    result = _worker_post(
        "/api/brain/owner-digest/commit",
        {
            "window_id": window_id,
            "lock_token": lock_token,
            "message_id": message_id,
            "message_body": message_body[:3800],
            "message_creation_uncertain": message_creation_uncertain,
        },
    )
    return result.get("ok") is True


def refresh_daily_digest() -> dict[str, Any]:
    """Refresh the current Rubika message, without creating a new event."""
    token = os.getenv("RUBIKA_TOKEN", "").strip()
    owner_id = os.getenv("RUBIKA_OWNER_CHAT_ID", "").strip()
    if not token or not owner_id:
        return {"sent": False, "reason": "owner_notification_not_configured"}

    try:
        bot = _make_rubika_bot(token)
    except Exception as exc:
        return {
            "sent": False,
            "reason": "rubika_client_" + type(exc).__name__,
        }

    for attempt in range(DIGEST_CLAIM_ATTEMPTS):
        try:
            claim = _worker_post("/api/brain/owner-digest/claim", {})
        except Exception as exc:
            return {"sent": False, "reason": _sanitize_text(exc, 90) or "digest_claim_failed"}

        if claim.get("claimed") is not True:
            if attempt + 1 < DIGEST_CLAIM_ATTEMPTS:
                time.sleep(DIGEST_CLAIM_DELAY_SECONDS)
                continue
            return {"sent": False, "reason": "digest_update_busy"}

        lock_token = str(claim.get("lock_token") or "")
        state = claim.get("state") if isinstance(claim.get("state"), dict) else {}
        window_id = str(state.get("window_id") or "")
        old_message_id = state.get("message_id")
        old_message_id = str(old_message_id) if old_message_id else None
        old_body = str(state.get("message_body") or "")
        uncertain = state.get("message_creation_uncertain") is True
        new_body = render_daily_digest(state)

        if not window_id or not lock_token:
            return {"sent": False, "reason": "digest_claim_response_invalid"}

        if not old_message_id and uncertain:
            try:
                _commit_digest(
                    window_id=window_id,
                    lock_token=lock_token,
                    message_id=None,
                    message_body=old_body,
                    message_creation_uncertain=True,
                )
            except Exception:
                pass
            return {"sent": False, "reason": "digest_message_creation_uncertain"}

        try:
            if old_message_id and new_body == old_body:
                _commit_digest(
                    window_id=window_id,
                    lock_token=lock_token,
                    message_id=old_message_id,
                    message_body=old_body,
                    message_creation_uncertain=False,
                )
                return {"sent": True, "updated": False, "message_id": old_message_id}

            if old_message_id:
                edit = getattr(bot, "edit_message_text", None)
                if not callable(edit):
                    _commit_digest(
                        window_id=window_id,
                        lock_token=lock_token,
                        message_id=old_message_id,
                        message_body=old_body,
                        message_creation_uncertain=False,
                    )
                    return {"sent": False, "reason": "rubika_edit_message_unavailable"}

                raw = edit(chat_id=owner_id, message_id=old_message_id, text=new_body)
                if not _rubika_ok(raw):
                    _commit_digest(
                        window_id=window_id,
                        lock_token=lock_token,
                        message_id=old_message_id,
                        message_body=old_body,
                        message_creation_uncertain=False,
                    )
                    return {
                        "sent": False,
                        "reason": "rubika_digest_edit_rejected",
                        "diagnostic": _response_diagnostic(raw, [token, owner_id]),
                    }

                _commit_digest(
                    window_id=window_id,
                    lock_token=lock_token,
                    message_id=old_message_id,
                    message_body=new_body,
                    message_creation_uncertain=False,
                )
                return {"sent": True, "updated": True, "message_id": old_message_id}

            raw = bot.send_message(chat_id=owner_id, text=new_body)
            if not _rubika_ok(raw):
                _commit_digest(
                    window_id=window_id,
                    lock_token=lock_token,
                    message_id=None,
                    message_body="",
                    message_creation_uncertain=False,
                )
                return {
                    "sent": False,
                    "reason": "rubika_digest_send_rejected",
                    "diagnostic": _response_diagnostic(raw, [token, owner_id]),
                }

            message_id = _rubika_message_id(raw)
            if not message_id:
                # A success response without an ID is ambiguous. Never create duplicates
                # automatically; keep the window blocked until it expires/manual recovery.
                _commit_digest(
                    window_id=window_id,
                    lock_token=lock_token,
                    message_id=None,
                    message_body=new_body,
                    message_creation_uncertain=True,
                )
                return {"sent": False, "reason": "rubika_digest_message_id_missing"}

            _commit_digest(
                window_id=window_id,
                lock_token=lock_token,
                message_id=message_id,
                message_body=new_body,
                message_creation_uncertain=False,
            )
            return {"sent": True, "created": True, "message_id": message_id}

        except Exception as exc:
            # For sends, a transport error can be ambiguous; do not resend and duplicate.
            if not old_message_id:
                try:
                    _commit_digest(
                        window_id=window_id,
                        lock_token=lock_token,
                        message_id=None,
                        message_body=old_body,
                        message_creation_uncertain=True,
                    )
                except Exception:
                    pass
            else:
                try:
                    _commit_digest(
                        window_id=window_id,
                        lock_token=lock_token,
                        message_id=old_message_id,
                        message_body=old_body,
                        message_creation_uncertain=False,
                    )
                except Exception:
                    pass
            return {"sent": False, "reason": "rubika_digest_" + type(exc).__name__}

    return {"sent": False, "reason": "digest_update_busy"}


def send_owner_notification(
    message: str,
    *,
    category: str | None = None,
    source: str = "automation",
) -> dict[str, Any]:
    """Append an event, then create/edit the single owner digest message."""
    token = os.getenv("RUBIKA_TOKEN", "").strip()
    owner_id = os.getenv("RUBIKA_OWNER_CHAT_ID", "").strip()
    content = _sanitize_text(message, 1200)
    if not token or not owner_id or not content:
        return {"sent": False, "reason": "owner_notification_not_configured"}

    picked_category = category or _infer_category(content)
    if picked_category not in {"info", "success", "error", "ai_success", "ai_error"}:
        picked_category = "info"
    title, _, detail = content.partition("\n")
    title = _sanitize_text(title, 180) or "Automation event"
    detail = _sanitize_text(detail or content, 600)

    try:
        _worker_post(
            "/api/brain/owner-digest/events",
            {
                "category": picked_category,
                "source": re.sub(r"[^A-Za-z0-9_.:-]", "_", source)[:64] or "automation",
                "title": title,
                "detail": detail,
            },
        )
    except Exception as exc:
        return {
            "sent": False,
            "reason": _sanitize_text(exc, 90) or "owner_digest_event_write_failed",
        }

    return refresh_daily_digest()


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
    parser.add_argument(
        "--event",
        choices=("workflow_failed", "queue_intake", "daily_digest_start", "digest_refresh", "success", "test"),
        default="test",
    )
    parser.add_argument("--message", default="")
    parser.add_argument("--workflow", default="Rubika-Agent automation")
    parser.add_argument("--reason", default="unknown_error")
    args = parser.parse_args()

    if args.event == "digest_refresh":
        result = refresh_daily_digest()
    else:
        if args.message.strip():
            message = args.message.strip()
        elif args.event == "workflow_failed":
            reason = re.sub(r"[^A-Za-z0-9_-]", "", args.reason)[:80] or "unknown_error"
            message = (
                "🚨 هشدار عامل روبیکا\n"
                f"گردش‌کار: {args.workflow[:100]}\n"
                f"کد خطا: {reason}\n"
                "جزئیات محرمانه در این اعلان درج نشده است."
            )
        elif args.event == "queue_intake":
            message = "📥 دریافت فهرست جدید انجام شد."
        elif args.event == "daily_digest_start":
            message = "📌 بازهٔ تازهٔ گزارش روزانه آغاز شد؛ رخدادهای ۲۴ ساعت آینده در همین پیام جمع می‌شوند."
        elif args.event == "success":
            message = "✅ " + args.workflow[:150] + " با موفقیت انجام شد."
        else:
            message = "ℹ️ آزمایش مسیر اعلان روزانهٔ مالک."
        category = {
            "workflow_failed": "error",
            "queue_intake": "info",
            "daily_digest_start": "info",
            "success": "success",
            "test": "info",
        }[args.event]
        source = re.sub(r"[^A-Za-z0-9_.:-]", "_", args.workflow)[:64] or "automation"
        result = send_owner_notification(message, category=category, source=source)

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("sent") or result.get("reason") == "owner_notification_not_configured" else 1


if __name__ == "__main__":
    raise SystemExit(main())
