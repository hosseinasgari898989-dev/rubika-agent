"""Read-only preflight for the separately pinned Rubika MAIN destination."""
from __future__ import annotations

import inspect
import json
import os
import re
from typing import Any, Callable

from agent.brain import test_channel


READ_ONLY_CHAT_METHODS = (
    "get_chat",
    "get_chat_info",
    "get_channel_info",
    "get_chat_admins",
    "get_admin_chats",
    "get_chat_member",
    "get_chats",
)


def _response_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _sanitize_diagnostic(
    value: Any,
    *,
    token: str,
    guid: str,
    allow_spaces: bool,
) -> str | None:
    """Keep short diagnostic text while removing credential/identifier-shaped data."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None

    text = str(value)
    for sensitive_value in (token, guid):
        if sensitive_value:
            text = re.sub(re.escape(sensitive_value), "", text, flags=re.IGNORECASE)

    text = re.sub(
        r"(?i)\b(?:token|guid|authorization)\s*[:=]\s*[^\s,;]+", "", text
    )
    text = re.sub(r"(?i)\b(?:c0|g0|s0|u0)[a-z0-9_-]{6,}\b", "", text)
    text = re.sub(r"\b\d{1,20}:[A-Za-z0-9_-]{8,}\b", "", text)
    text = re.sub(r"\b[A-Za-z0-9_-]{24,}\b", "", text)

    if allow_spaces:
        text = re.sub(r"[^A-Za-z0-9 _.,:;!?()/+-]", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    else:
        text = re.sub(r"[^A-Za-z0-9_.-]", "", text)

    return text[:40] or None


def _exception_type(error: BaseException) -> str:
    """Return only a conservative exception class name, never its message."""
    name = re.sub(r"[^A-Za-z0-9_]", "", type(error).__name__)[:40]
    return name or "Exception"


def _accepts_one_chat_id(method: Any) -> bool:
    """Only invoke methods whose installed signature accepts exactly our one argument."""
    try:
        inspect.signature(method).bind("read-only-chat-id")
    except (TypeError, ValueError):
        return False
    return True


def _method_presence(bot: Any) -> dict[str, bool]:
    """Discover the allowlisted read-only surface on the installed Bot instance."""
    available: dict[str, bool] = {}
    for method_name in READ_ONLY_CHAT_METHODS:
        try:
            available[method_name] = bool(hasattr(bot, method_name))
        except Exception:
            available[method_name] = False
    return available


def verify_main_channel(
    token: str,
    main_channel_guid: str,
    bot_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Verify bot identity and MAIN destination using read-only API methods only."""
    normalized_token = str(token or "").strip()
    normalized_guid = str(main_channel_guid or "").strip()
    guid_hash = test_channel.channel_guid_sha256(normalized_guid) if normalized_guid else ""

    result: dict[str, Any] = {
        "ok": False,
        "token_present": bool(normalized_token),
        "main_guid_present": bool(normalized_guid),
        "bot_identity_ok": False,
        "main_matches_test_pin": bool(normalized_guid and test_channel.is_test_channel_guid(normalized_guid)),
        "main_guid_sha256_prefix": guid_hash[:12] if guid_hash else None,
        "main_destination_accessible": False,
        "accessible_via": None,
        "read_only_method_presence": {name: False for name in READ_ONLY_CHAT_METHODS},
        "read_only_checks": [],
        "chat_exception_type": None,
        "chat_response_status": None,
        "chat_response_description": None,
        "message_sent": False,
        "published": False,
        "failure_stage": "missing_credentials",
    }
    if not normalized_token or not normalized_guid:
        return result

    if bot_factory is None:
        try:
            from rubka import Bot
        except Exception:
            result["failure_stage"] = "rubka_import_failed"
            return result
        bot_factory = Bot

    result["failure_stage"] = "bot_identity_failed"
    try:
        bot = bot_factory(token=normalized_token, timeout=30, show_progress=False)
        identity = _response_payload(bot.get_me())
    except Exception:
        return result

    if identity.get("status") != "OK":
        result["failure_stage"] = "bot_status_not_ok"
        return result
    result["bot_identity_ok"] = True

    # Never inspect/call destination methods with the TEST-pinned identifier.
    if result["main_matches_test_pin"]:
        result["failure_stage"] = "test_pin_match"
        return result

    presence = _method_presence(bot)
    result["read_only_method_presence"] = presence
    accessible_via: str | None = None

    for method_name in READ_ONLY_CHAT_METHODS:
        if not presence[method_name]:
            continue
        try:
            method = getattr(bot, method_name)
        except Exception:
            continue
        if not callable(method) or not _accepts_one_chat_id(method):
            continue

        try:
            raw_response = method(normalized_guid)
        except Exception as error:
            exception_type = _exception_type(error)
            result["read_only_checks"].append(
                {"method": method_name, "ok": False, "exception_type": exception_type}
            )
            if method_name == "get_chat":
                result["chat_exception_type"] = exception_type
            continue

        payload = _response_payload(raw_response)
        status_value = payload.get("status")
        method_ok = isinstance(status_value, str) and status_value.strip().upper() == "OK"
        check: dict[str, Any] = {"method": method_name, "ok": bool(method_ok)}
        safe_status = _sanitize_diagnostic(
            status_value,
            token=normalized_token,
            guid=normalized_guid,
            allow_spaces=False,
        )
        if safe_status is not None:
            check["status"] = safe_status
        result["read_only_checks"].append(check)

        if method_name == "get_chat" and not method_ok and isinstance(raw_response, dict):
            result["chat_response_status"] = safe_status
            result["chat_response_description"] = _sanitize_diagnostic(
                raw_response.get("description"),
                token=normalized_token,
                guid=normalized_guid,
                allow_spaces=True,
            )

        if method_ok and accessible_via is None:
            accessible_via = method_name

    if accessible_via is None:
        result["failure_stage"] = "main_chat_failed"
        return result

    result["main_destination_accessible"] = True
    result["accessible_via"] = accessible_via
    result["ok"] = True
    result["failure_stage"] = "none"
    return result


def main() -> int:
    result = verify_main_channel(
        os.getenv("RUBIKA_TOKEN", ""),
        os.getenv("RUBIKA_MAIN_CHANNEL_GUID", ""),
    )
    # The report contains presence booleans, method names, sanitized diagnostics and a hash prefix only.
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
