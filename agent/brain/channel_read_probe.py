"""Manual read-only probe: get_me and get_chat on the TEST and MAIN channel ids."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from agent.brain import main_channel_preflight as pf
from agent.brain import test_channel


def _word(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"[^A-Za-z0-9_.-]", "", value)[:40]
    return text or None


def _probe(bot: Any, label: str, guid: str, token: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "label": label,
        "guid_present": bool(guid),
        "guid_len": len(guid),
        "guid_first2": guid[:2] if guid else None,
        "guid_sha256_prefix": test_channel.channel_guid_sha256(guid)[:12] if guid else None,
        "pin_match": bool(guid and test_channel.is_test_channel_guid(guid)),
        "ok": False,
        "status": None,
        "description": None,
        "dev_message": None,
        "response_keys": None,
        "exception_type": None,
        "chat_type": None,
        "echoed_id_matches": None,
    }
    if not guid:
        return out
    try:
        raw = bot.get_chat(guid)
    except Exception as error:
        out["exception_type"] = pf._exception_type(error)
        return out
    payload = pf._response_payload(raw)
    status = payload.get("status")
    out["status"] = pf._sanitize_diagnostic(status, token=token, guid=guid, allow_spaces=False)
    out["ok"] = isinstance(status, str) and status.strip().upper() == "OK"
    out["response_keys"] = sorted(w for w in (_word(k) for k in payload if isinstance(k, str)) if w)[:8]
    if out["ok"]:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        chat = data.get("chat") if isinstance(data.get("chat"), dict) else {}
        out["chat_type"] = _word(chat.get("chat_type"))
        echoed = chat.get("chat_id")
        out["echoed_id_matches"] = bool(isinstance(echoed, str) and echoed.strip() == guid)
    else:
        out["description"] = pf._sanitize_diagnostic(
            payload.get("description"), token=token, guid=guid, allow_spaces=True
        )
        out["dev_message"] = pf._sanitize_diagnostic(
            payload.get("dev_message"), token=token, guid=guid, allow_spaces=True
        )
    return out


def main() -> int:
    token = os.getenv("RUBIKA_TOKEN", "").strip()
    targets = [
        ("test", os.getenv("RUBIKA_CHANNEL_GUID", "").strip()),
        ("main", os.getenv("RUBIKA_MAIN_CHANNEL_GUID", "").strip()),
    ]
    report: dict[str, Any] = {
        "read_only": True,
        "message_sent": False,
        "published": False,
        "bot_identity_ok": False,
        "probes": [],
    }
    if token:
        try:
            from rubka import Bot

            bot = Bot(token=token, timeout=30, show_progress=False)
            report["bot_identity_ok"] = pf._response_payload(bot.get_me()).get("status") == "OK"
        except Exception as error:
            report["bot_error_type"] = pf._exception_type(error)
    if report["bot_identity_ok"]:
        for label, guid in targets:
            report["probes"].append(_probe(bot, label, guid, token))
    print(json.dumps(report, sort_keys=True))
    return 0 if report["bot_identity_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
