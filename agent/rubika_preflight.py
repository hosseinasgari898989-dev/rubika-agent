"""Verify Rubika bot credentials and channel access without publishing anything."""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def verify_connection(
    token: str,
    channel_guid: str,
    bot_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Return only safe, minimal diagnostics. Never include token or raw API payloads."""
    if not token.strip():
        return {"ok": False, "stage": "configuration", "error": "missing_RUBIKA_TOKEN"}
    if not channel_guid.strip():
        return {"ok": False, "stage": "configuration", "error": "missing_RUBIKA_CHANNEL_GUID"}

    if bot_factory is None:
        try:
            from rubka import Bot
        except ImportError:
            return {"ok": False, "stage": "dependency", "error": "rubka_not_installed"}
        bot_factory = Bot

    try:
        # Bot construction validates the token using Rubka's getMe call.
        bot = bot_factory(token=token, timeout=30, show_progress=False)
    except Exception as exc:
        return {
            "ok": False,
            "stage": "bot_authentication",
            "error": type(exc).__name__,
        }

    try:
        chat_response = bot.get_chat(channel_guid)
        chat = _payload(chat_response)
    except Exception as exc:
        return {
            "ok": False,
            "stage": "channel_access",
            "error": type(exc).__name__,
        }

    if chat.get("status") != "OK":
        return {
            "ok": False,
            "stage": "channel_access",
            "error": "rubika_rejected_channel",
        }

    return {
        "ok": True,
        "stage": "complete",
        "bot_authenticated": True,
        "channel_access": True,
        "publishing": False,
    }


def main() -> int:
    result = verify_connection(
        os.getenv("RUBIKA_TOKEN", ""),
        os.getenv("RUBIKA_CHANNEL_GUID", ""),
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else (2 if result.get("stage") == "configuration" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
