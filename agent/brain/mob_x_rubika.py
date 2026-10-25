"""Private Rubika previews and one-shot test-channel delivery for Mob X jobs.

Every send is attempted once only. If a channel send has an ambiguous outcome,
callers must record uncertain and never automatically retry it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def _bot(token: str, bot_factory: Any | None = None):
    if not str(token or "").strip():
        raise RuntimeError("rubika_token_missing")
    if bot_factory is None:
        from rubka import Bot
        bot_factory = Bot
    return bot_factory(token=str(token).strip(), timeout=30, show_progress=True)


def _message_id(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    nested = data.get("message") if isinstance(data.get("message"), dict) else {}
    value = data.get("message_id") or nested.get("message_id") or response.get("message_id")
    return str(value) if value is not None and str(value).strip() else None


def _assert_ok(response: Any, error_code: str) -> dict:
    if not isinstance(response, dict):
        raise RuntimeError(error_code)
    status = str(response.get("status") or "").upper()
    if response.get("ok") is False or (status and status != "OK"):
        raise RuntimeError(error_code)
    if not status and response.get("ok") is not True:
        raise RuntimeError(error_code)
    return response


def _keypad(button_rows: list[list[tuple[str, str]]]) -> dict:
    return {
        "rows": [
            {"buttons": [{"id": button_id, "type": "Simple", "button_text": label} for label, button_id in row]}
            for row in button_rows
        ]
    }


def owner_preview_keypad(job_id: str) -> dict:
    if not re.fullmatch(r"job_[A-Fa-f0-9]{8,80}", str(job_id or "")):
        raise ValueError("mob_x_job_id_invalid")
    return _keypad([[
        ("✅ انتشار در کانال تست", "p_mob_publish_" + job_id),
        ("✏️ ویرایش کپشن", "p_mob_edit_" + job_id),
        ("✖️ رد", "p_mob_reject_" + job_id),
    ]])


def upload_preview_media(
    token: str, local_path: str | Path, media_type: str, *, bot_factory: Any | None = None,
) -> str:
    """Upload once to Rubika and return the reusable file_id; this does not send a message."""
    path = Path(local_path)
    if media_type not in {"image", "video"} or not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("mob_x_preview_media_invalid")
    bot = _bot(token, bot_factory)
    get_upload_url = getattr(bot, "get_upload_url", None)
    upload_media_file = getattr(bot, "upload_media_file", None)
    if not callable(get_upload_url) or not callable(upload_media_file):
        raise RuntimeError("rubika_media_upload_api_unavailable")
    kind = "Image" if media_type == "image" else "Video"
    upload_url = get_upload_url(kind)
    if not isinstance(upload_url, str) or not upload_url.startswith("https://"):
        raise RuntimeError("rubika_media_upload_url_invalid")
    file_id = upload_media_file(upload_url, path.name, path)
    if not isinstance(file_id, str) or not file_id.strip():
        raise RuntimeError("rubika_media_upload_receipt_missing")
    return file_id.strip()


def send_file_once(
    token: str, chat_id: str, file_id: str, caption: str, *,
    inline_keypad: dict | None = None, bot_factory: Any | None = None,
) -> str | None:
    """Use a previously uploaded Rubika file_id. Never retry sendFile."""
    if not str(chat_id or "").strip() or not str(file_id or "").strip():
        raise RuntimeError("rubika_send_target_or_file_missing")
    bot = _bot(token, bot_factory)
    post = getattr(bot, "_post", None)
    if not callable(post):
        raise RuntimeError("rubika_send_file_api_unavailable")
    payload: dict[str, Any] = {
        "chat_id": str(chat_id).strip(),
        "file_id": str(file_id).strip(),
        "text": str(caption or ""),
        "disable_notification": False,
        "chat_keypad_type": "None",
    }
    if inline_keypad is not None:
        payload["inline_keypad"] = inline_keypad
    response = _assert_ok(post("sendFile", payload), "rubika_send_file_response_invalid")
    return _message_id(response)


def send_owner_preview(
    token: str, owner_chat_id: str, file_id: str, caption: str, job_id: str, *,
    bot_factory: Any | None = None,
) -> str | None:
    return send_file_once(
        token, owner_chat_id, file_id, caption,
        inline_keypad=owner_preview_keypad(job_id), bot_factory=bot_factory,
    )


def send_test_channel_once(
    token: str, channel_guid: str, file_id: str, caption: str, *,
    bot_factory: Any | None = None,
) -> str | None:
    return send_file_once(token, channel_guid, file_id, caption, bot_factory=bot_factory)


def send_uncertain_notice(
    token: str, owner_chat_id: str, job_id: str, message: str, *,
    bot_factory: Any | None = None,
) -> str | None:
    if not re.fullmatch(r"job_[A-Fa-f0-9]{8,80}", str(job_id or "")):
        raise ValueError("mob_x_job_id_invalid")
    bot = _bot(token, bot_factory)
    post = getattr(bot, "_post", None)
    if not callable(post):
        raise RuntimeError("rubika_send_message_api_unavailable")
    response = _assert_ok(post("sendMessage", {
        "chat_id": str(owner_chat_id).strip(),
        "text": str(message or ""),
        "disable_notification": False,
        "inline_keypad": _keypad([[
            ("✅ تأیید می‌کنم ارسال انجام شده", "p_mob_manualconfirm_" + job_id),
        ]]),
    }), "rubika_send_message_response_invalid")
    return _message_id(response)
