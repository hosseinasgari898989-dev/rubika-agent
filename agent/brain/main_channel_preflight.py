"""Read-only preflight for the separately pinned Rubika MAIN destination."""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from agent.brain import test_channel


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


def verify_main_channel(
    token: str,
    main_channel_guid: str,
    bot_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Verify getMe/getChat and the TEST-pin separation without exposing credentials."""
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
        "message_sent": False,
        "published": False,
    }
    if not normalized_token or not normalized_guid:
        return result

    if bot_factory is None:
        try:
            from rubka import Bot
        except ImportError:
            return result
        bot_factory = Bot

    try:
        bot = bot_factory(token=normalized_token, timeout=30, show_progress=False)
        identity = _response_payload(bot.get_me())
    except Exception:
        return result

    if identity.get("status") != "OK":
        return result
    result["bot_identity_ok"] = True

    # Never call getChat on the pinned TEST destination from this MAIN-only preflight.
    if result["main_matches_test_pin"]:
        return result

    try:
        destination = _response_payload(bot.get_chat(normalized_guid))
    except Exception:
        return result

    if destination.get("status") != "OK":
        return result

    result["main_destination_accessible"] = True
    result["ok"] = True
    return result


def main() -> int:
    result = verify_main_channel(
        os.getenv("RUBIKA_TOKEN", ""),
        os.getenv("RUBIKA_MAIN_CHANNEL_GUID", ""),
    )
    # The report intentionally contains only booleans and a short hash prefix.
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
