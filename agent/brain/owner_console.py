"""Owner-only Rubika operations console and link-intake menu.

The console can display read-only reports and collect one X status link for a later
processing slice. It does not download media, translate, publish, execute shell
commands, change code, merge, or deploy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

RUBIKA_API_BASE = "https://botapi.rubika.ir/v3"
DEFAULT_CONTROL_URL = "https://rubika-agent.h98609294.workers.dev"
EXPECTED_CONTROL_HOST = "rubika-agent.h98609294.workers.dev"
GITHUB_RUNS_URL = "https://api.github.com/repos/hosseinasgari898989-dev/rubika-agent/actions/runs?per_page=10"
ALLOWED_RUBIKA_METHODS = {"getMe", "getUpdates", "sendMessage", "editMessageText", "editMessageKeypad"}
PROVIDERS = ("groq", "openrouter")
MAX_REPLY_CHARS = 3400
MAX_UPDATES_PER_POLL = 20
MAX_UPDATE_ID_CHARS = 200
CONSOLE_STATE_VERSION = 1
CONSOLE_IDLE = "idle"
CONSOLE_WAITING_FOR_LINK = "waiting_for_link"
CONSOLE_MODES = {CONSOLE_IDLE, CONSOLE_WAITING_FOR_LINK}
BUTTON_MOB_POST_FROM_X = "mob_post_from_x"
BUTTON_CANCEL = "owner_console_cancel"
SAFE_CODE = re.compile(r"[a-z0-9_-]{1,80}")
HASH_RE = re.compile(r"[a-f0-9]{64}")
SAFE_PROVIDER = set(PROVIDERS)
X_STATUS_URL_RE = re.compile(
    r"https?://(?:(?:www|mobile)\.)?(?:x\.com|twitter\.com)/"
    r"([A-Za-z0-9_]{1,15})/status/([0-9]{15,22})(?:/)?(?:[?#][^\s<>]*)?",
    re.IGNORECASE,
)


class OwnerConsoleError(RuntimeError):
    """A machine-readable error code that never contains credential-bearing text."""

    def __init__(self, code: str):
        clean = str(code or "").lower()
        self.code = clean if SAFE_CODE.fullmatch(clean) else "owner_console_error"
        super().__init__(self.code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _opener():
    return urllib.request.build_opener(_NoRedirect())


def _decode_json(raw: bytes, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise OwnerConsoleError(code) from None
    if not isinstance(payload, dict):
        raise OwnerConsoleError(code)
    return payload


def _http_error_code(exc: urllib.error.HTTPError, prefix: str) -> str:
    status = int(getattr(exc, "code", 0) or 0)
    return f"{prefix}_http_{status}" if 100 <= status <= 599 else prefix + "_http_error"


class RubikaBotAPI:
    """Fixed-host REST adapter. Token-bearing URLs are never printed or re-raised."""

    def __init__(self, token: str, *, opener: Any | None = None, timeout_seconds: int = 15):
        clean = (token or "").strip()
        if len(clean) < 16:
            raise OwnerConsoleError("rubika_token_missing_or_short")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise OwnerConsoleError("rubika_timeout_invalid")
        self._token = clean
        self._opener = opener or _opener()
        self.timeout_seconds = timeout_seconds

    def call(self, method: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if method not in ALLOWED_RUBIKA_METHODS:
            raise OwnerConsoleError("rubika_method_not_allowed")
        body = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{RUBIKA_API_BASE}/{self._token}/{method}",
            data=body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Rubika-Agent-ReadOnly-Operations-Console/1.0",
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status != 200:
                    raise OwnerConsoleError(f"rubika_http_{status}")
                result = _decode_json(response.read(256 * 1024), "rubika_response_invalid_json")
        except urllib.error.HTTPError as exc:
            raise OwnerConsoleError(_http_error_code(exc, "rubika")) from None
        except OwnerConsoleError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise OwnerConsoleError("rubika_network_result_unknown") from None

        if result.get("status") != "OK":
            raw_status = str(result.get("status") or "unknown").lower()
            safe_status = re.sub(r"[^a-z0-9_-]", "_", raw_status)[:60] or "unknown"
            raise OwnerConsoleError("rubika_api_" + safe_status)
        return result

    def get_updates(self, offset_id: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"limit": MAX_UPDATES_PER_POLL}
        if offset_id:
            payload["offset_id"] = offset_id
        return self.call("getUpdates", payload)

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        inline_keypad: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean = str(text or "").strip()[:MAX_REPLY_CHARS]
        if not clean:
            raise OwnerConsoleError("rubika_reply_empty")
        payload: dict[str, Any] = {"chat_id": chat_id, "text": clean}
        if inline_keypad is not None:
            payload["inline_keypad"] = dict(inline_keypad)
        return self.call("sendMessage", payload)

    def edit_message_text(self, chat_id: str, message_id: str, text: str) -> dict[str, Any]:
        clean = str(text or "").strip()[:MAX_REPLY_CHARS]
        if not chat_id or not message_id or not clean:
            raise OwnerConsoleError("rubika_edit_message_invalid")
        return self.call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": clean,
        })

    def edit_inline_keypad(
        self,
        chat_id: str,
        message_id: str,
        inline_keypad: Mapping[str, Any],
        *,
        text: str | None = None,
    ) -> dict[str, Any]:
        # Rubka 8.1.7 maps edit_inline_keypad() to Bot API v3 editMessageKeypad.
        if text is not None:
            self.edit_message_text(chat_id, message_id, text)
        if not chat_id or not message_id or not isinstance(inline_keypad, Mapping):
            raise OwnerConsoleError("rubika_edit_keypad_invalid")
        return self.call("editMessageKeypad", {
            "chat_id": chat_id,
            "message_id": message_id,
            "inline_keypad": dict(inline_keypad),
        })


class ControlWorker:
    """HTTPS-only client for a narrow allowlist of authenticated Worker endpoints."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        opener: Any | None = None,
        timeout_seconds: int = 10,
    ):
        parsed = urllib.parse.urlsplit((base_url or "").strip())
        if (
            parsed.scheme != "https"
            or parsed.hostname != EXPECTED_CONTROL_HOST
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise OwnerConsoleError("control_api_url_invalid")
        clean_token = (token or "").strip()
        if len(clean_token) < 32:
            raise OwnerConsoleError("control_api_token_missing_or_short")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
            raise OwnerConsoleError("control_api_timeout_invalid")
        self.base_url = "https://" + EXPECTED_CONTROL_HOST
        self.token = clean_token
        self.timeout_seconds = timeout_seconds
        self._opener = opener or _opener()

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        parsed = urllib.parse.urlsplit(path)
        allowed_methods = {
            "/api/status": {"GET"},
            "/api/brain/owner-console/state": {"GET"},
            "/api/brain/owner-console/offset": {"POST"},
            "/api/brain/owner-console/claim": {"POST"},
            "/api/brain/owner-console/finish": {"POST"},
            "/api/brain/owner-console/queue-summary": {"GET"},
            "/api/brain/ai-provider/status": {"GET"},
        }
        if parsed.path not in allowed_methods or parsed.fragment:
            raise OwnerConsoleError("control_api_path_not_allowed")
        if method not in allowed_methods[parsed.path]:
            raise OwnerConsoleError("control_api_method_not_allowed")
        if parsed.query:
            if parsed.path != "/api/brain/ai-provider/status":
                raise OwnerConsoleError("control_api_query_not_allowed")
            try:
                query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
            except ValueError:
                raise OwnerConsoleError("control_api_query_invalid") from None
            if set(query) != {"provider"} or len(query["provider"]) != 1 or query["provider"][0] not in SAFE_PROVIDER:
                raise OwnerConsoleError("control_api_provider_invalid")
        if method not in {"GET", "POST"} or (method == "GET" and payload is not None):
            raise OwnerConsoleError("control_api_method_not_allowed")
        raw_body = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "Rubika-Agent-ReadOnly-Operations-Console/1.0",
        }
        if method == "POST":
            raw_body = json.dumps(dict(payload or {}), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(raw_body) > 4096:
                raise OwnerConsoleError("control_api_request_too_large")
            headers["Content-Type"] = "application/json"
            headers["Authorization"] = "Bearer " + self.token
        elif parsed.path.startswith("/api/brain/"):
            headers["Authorization"] = "Bearer " + self.token

        request = urllib.request.Request(
            self.base_url + parsed.path + (("?" + parsed.query) if parsed.query else ""),
            data=raw_body,
            method=method,
            headers=headers,
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status not in {200, 201}:
                    raise OwnerConsoleError(f"control_api_http_{status}")
                result = _decode_json(response.read(64 * 1024), "control_api_invalid_json")
        except urllib.error.HTTPError as exc:
            raise OwnerConsoleError(_http_error_code(exc, "control_api")) from None
        except OwnerConsoleError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise OwnerConsoleError("control_api_unreachable") from None

        if result.get("ok") is not True:
            raw = result.get("error")
            code = str(raw).lower() if isinstance(raw, str) else "rejected"
            code = re.sub(r"[^a-z0-9_-]", "", code)[:60] or "rejected"
            raise OwnerConsoleError("control_api_" + code)
        return result

    def get_state(self) -> dict[str, Any]:
        return self._request("GET", "/api/brain/owner-console/state")

    def set_offset(self, offset_id: str) -> dict[str, Any]:
        if not isinstance(offset_id, str) or not 1 <= len(offset_id) <= MAX_UPDATE_ID_CHARS:
            raise OwnerConsoleError("owner_console_offset_invalid")
        return self._request("POST", "/api/brain/owner-console/offset", {"offset_id": offset_id})

    def claim(self, update_id: str, chat_hash: str, command: str) -> dict[str, Any]:
        if not HASH_RE.fullmatch(update_id) or not HASH_RE.fullmatch(chat_hash):
            raise OwnerConsoleError("owner_console_identity_invalid")
        return self._request("POST", "/api/brain/owner-console/claim", {
            "update_id": update_id,
            "chat_hash": chat_hash,
            "command": command,
        })

    def finish(self, update_id: str, status: str, result_code: str) -> dict[str, Any]:
        if not HASH_RE.fullmatch(update_id) or status not in {"answered", "failed", "uncertain", "ignored"}:
            raise OwnerConsoleError("owner_console_finish_invalid")
        code = str(result_code or "").lower()
        if not SAFE_CODE.fullmatch(code):
            code = "unspecified"
        return self._request("POST", "/api/brain/owner-console/finish", {
            "update_id": update_id,
            "status": status,
            "result_code": code,
        })

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/api/status")

    def queue_summary(self) -> dict[str, Any]:
        return self._request("GET", "/api/brain/owner-console/queue-summary")

    def ai_status(self, provider: str) -> dict[str, Any]:
        if provider not in SAFE_PROVIDER:
            raise OwnerConsoleError("code_ai_provider_not_enabled")
        return self._request("GET", "/api/brain/ai-provider/status?provider=" + provider)


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    provider: str | None
    audit_name: str


def parse_command(message: str) -> ParsedCommand:
    """Parse an allowlisted command only; arbitrary text is never treated as code."""
    text = str(message or "").strip()
    if not text or len(text) > MAX_REPLY_CHARS:
        return ParsedCommand("unknown", None, "unknown")
    parts = text.split()
    if not parts or not parts[0].startswith("/"):
        return ParsedCommand("unknown", None, "unknown")
    name = parts[0].split("@", 1)[0].lower()
    if name in {"/start", "/menu"} and len(parts) == 1:
        return ParsedCommand("menu", None, "help")
    if name == "/help" and len(parts) == 1:
        return ParsedCommand("help", None, "help")
    if name == "/status" and len(parts) == 1:
        return ParsedCommand("status", None, "status")
    if name == "/queue" and len(parts) == 1:
        return ParsedCommand("queue", None, "queue")
    if name == "/report" and len(parts) == 1:
        return ParsedCommand("report", None, "report")
    if name == "/ai-status" and len(parts) == 1:
        return ParsedCommand("ai-status", None, "ai-status")
    if name == "/ai-status" and len(parts) == 2 and parts[1].lower() in SAFE_PROVIDER:
        provider = parts[1].lower()
        return ParsedCommand("ai-status", provider, "ai-status:" + provider)
    return ParsedCommand("unknown", None, "unknown")


def _message_identity(update: Mapping[str, Any]) -> str | None:
    update_type = update.get("type")
    if update_type == "NewMessage":
        message = update.get("new_message")
        if not isinstance(message, dict):
            return None
        message_id = message.get("message_id")
        chat_id = update.get("chat_id") or message.get("chat_id")
        if not isinstance(message_id, str) or not message_id or not isinstance(chat_id, str) or not chat_id:
            return None
        return hashlib.sha256((chat_id + "|" + message_id).encode("utf-8")).hexdigest()

    if update_type == "ReceiveQuery":
        message = update.get("inline_message")
        if not isinstance(message, dict):
            return None
        chat_id = update.get("chat_id") or message.get("chat_id")
        message_id = message.get("message_id")
        aux_data = message.get("aux_data")
        if not isinstance(aux_data, dict):
            return None
        button_id = _safe_button_id(aux_data.get("button_id"))
        start_id = aux_data.get("start_id")
        if not isinstance(chat_id, str) or not chat_id or not isinstance(message_id, str) or not message_id or not button_id:
            return None
        safe_start_id = re.sub(r"[^A-Za-z0-9_.:-]", "", str(start_id or ""))[:100]
        identity = "|".join(("ReceiveQuery", chat_id, message_id, button_id, safe_start_id))
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return None


def _is_group_or_channel(update: Mapping[str, Any], message: Mapping[str, Any]) -> bool:
    values = [
        update.get("chat_type"), message.get("chat_type"),
        update.get("type"), message.get("type"),
    ]
    if update.get("is_group") is True or message.get("is_group") is True:
        return True
    return any(
        isinstance(value, str) and value.casefold() in {"group", "supergroup", "channel", "channelmessage"}
        for value in values
    )


def _owner_private_text(
    update: Mapping[str, Any], owner_chat_id: str, owner_user_id: str
) -> str | None:
    if update.get("type") != "NewMessage":
        return None
    message = update.get("new_message")
    if not isinstance(message, dict) or _is_group_or_channel(update, message):
        return None
    chat_id = update.get("chat_id") or message.get("chat_id")
    sender_id = message.get("sender_id")
    if str(chat_id or "") != owner_chat_id or str(sender_id or "") != owner_user_id:
        return None
    text = message.get("text")
    if not isinstance(text, str):
        return None
    clean = text.strip()
    if not clean or len(clean) > MAX_REPLY_CHARS:
        return None
    return clean



def _safe_button_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean = re.sub(r"[^A-Za-z0-9_-]", "", value)[:60]
    return clean or None


def _log_update_diagnostic(update: Any) -> None:
    """Log only update type and sanitized button ID; never text, chat, or message IDs."""
    if not isinstance(update, Mapping):
        update_type = "unknown"
        button_id = None
    else:
        raw_type = update.get("type")
        update_type = re.sub(r"[^A-Za-z0-9_-]", "", str(raw_type or "unknown"))[:40] or "unknown"
        message = update.get("inline_message")
        aux = message.get("aux_data") if isinstance(message, Mapping) else None
        button_id = _safe_button_id(aux.get("button_id")) if isinstance(aux, Mapping) else None
    print(json.dumps({"update_type": update_type, "button_id": button_id}, separators=(",", ":")))


def menu_inline_keypad() -> dict[str, Any]:
    return {"rows": [{"buttons": [{
        "id": BUTTON_MOB_POST_FROM_X,
        "type": "Simple",
        "button_text": "📰 خبر Mob از لینک X",
    }]}]}


def waiting_inline_keypad() -> dict[str, Any]:
    return {"rows": [{"buttons": [{
        "id": BUTTON_CANCEL,
        "type": "Simple",
        "button_text": "لغو",
    }]}]}


def encode_console_state(offset_id: str, mode: str) -> str:
    """Persist mode beside the Rubika cursor using the existing opaque offset field."""
    if not isinstance(offset_id, str) or not 1 <= len(offset_id) <= MAX_UPDATE_ID_CHARS:
        raise OwnerConsoleError("owner_console_stored_offset_invalid")
    if mode not in CONSOLE_MODES:
        raise OwnerConsoleError("owner_console_mode_invalid")
    value = json.dumps(
        {"v": CONSOLE_STATE_VERSION, "offset_id": offset_id, "mode": mode},
        separators=(",", ":"),
    )
    if len(value) > MAX_UPDATE_ID_CHARS:
        raise OwnerConsoleError("owner_console_state_too_large")
    return value


def decode_console_state(value: Any) -> tuple[str | None, str]:
    """Read the new envelope; accept an old raw offset for backward compatibility."""
    if value is None:
        return None, CONSOLE_IDLE
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_UPDATE_ID_CHARS:
        raise OwnerConsoleError("owner_console_stored_offset_invalid")
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        decoded = None
    if isinstance(decoded, dict) and decoded.get("v") == CONSOLE_STATE_VERSION:
        offset_id = decoded.get("offset_id")
        mode = decoded.get("mode")
        if (
            isinstance(offset_id, str)
            and 1 <= len(offset_id) <= MAX_UPDATE_ID_CHARS
            and mode in CONSOLE_MODES
        ):
            return offset_id, mode
        raise OwnerConsoleError("owner_console_stored_state_invalid")
    return value, CONSOLE_IDLE


def parse_x_status_url(text: str) -> str | None:
    """Return a canonical URL for the first valid X/Twitter status link in text."""
    for match in X_STATUS_URL_RE.finditer(str(text or "")[:MAX_REPLY_CHARS]):
        raw = match.group(0).rstrip(".,!?;:)]}")
        try:
            parsed = urllib.parse.urlsplit(raw)
        except ValueError:
            continue
        host = (parsed.hostname or "").lower().rstrip(".")
        if host not in {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}:
            continue
        try:
            port = parsed.port
        except ValueError:
            continue
        if parsed.scheme != "https" or parsed.username or parsed.password or port not in {None, 443}:
            continue
        path = re.fullmatch(r"/([A-Za-z0-9_]{1,15})/status/([0-9]{15,22})/?", parsed.path, re.IGNORECASE)
        if not path:
            continue
        handle, post_id = path.groups()
        return f"https://x.com/{handle}/status/{post_id}"
    return None


def _owner_private_event(
    update: Mapping[str, Any],
    owner_chat_id: str,
    owner_user_id: str,
) -> tuple[str, str | None, str | None, Mapping[str, Any]] | None:
    """Return (kind, text, button_id, nested payload) only for the exact configured owner."""
    update_type = update.get("type")
    if update_type == "NewMessage":
        message = update.get("new_message")
        kind = "message"
        text = message.get("text") if isinstance(message, Mapping) else None
    elif update_type == "ReceiveQuery":
        message = update.get("inline_message")
        kind = "callback"
        text = None
    else:
        return None
    if not isinstance(message, Mapping) or _is_group_or_channel(update, message):
        return None
    chat_id = update.get("chat_id") or message.get("chat_id")
    sender_id = message.get("sender_id")
    if str(chat_id or "") != owner_chat_id or str(sender_id or "") != owner_user_id:
        return None
    if kind == "message":
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_REPLY_CHARS:
            return None
        return kind, text.strip(), None, message
    aux_data = message.get("aux_data")
    button_id = _safe_button_id(aux_data.get("button_id")) if isinstance(aux_data, Mapping) else None
    if not button_id:
        return None
    return kind, None, button_id, message

def _nonnegative_count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def format_status(status: Mapping[str, Any]) -> str:
    if status.get("ok") is not True:
        return "❌ وضعیت Worker فعلاً در دسترس نیست؛ بعداً دوباره بررسی کن."
    database = status.get("database")
    db_text = "متصل" if database == "connected" else "نامشخص"
    agent = status.get("agent")
    if not isinstance(agent, dict):
        agent = {}
    enabled = "روشن" if agent.get("enabled") == 1 else "خاموش/آماده‌به‌کار نیست"
    capabilities = status.get("capabilities")
    publishing = False
    if isinstance(capabilities, dict):
        publishing = capabilities.get("publishing") is True
    publishing = publishing or status.get("publishing") is True
    rows = status.get("item_counts")
    if not isinstance(rows, list):
        rows = []
    counts = []
    for row in rows[:8]:
        if not isinstance(row, dict):
            continue
        label = re.sub(r"[^a-z_-]", "", str(row.get("status") or "unknown").lower())[:20]
        count = _nonnegative_count(row.get("total"))
        if label:
            counts.append(f"{label}: {count}")
    runs = status.get("recent_runs")
    latest = "ندارد"
    if isinstance(runs, list) and runs and isinstance(runs[0], dict):
        run = runs[0]
        run_status = re.sub(r"[^a-z_-]", "", str(run.get("status") or "unknown").lower())[:20]
        run_id = re.sub(r"[^A-Za-z0-9_-]", "", str(run.get("id") or ""))[:32]
        latest = f"{run_status or 'unknown'}" + (f" (شناسه {run_id})" if run_id else "")
    return (
        "📊 وضعیت Rubika-Agent\n"
        f"• پایگاه داده: {db_text}\n"
        f"• عامل پیکربندی‌شده: {enabled}\n"
        f"• انتشار از این API: {'فعال' if publishing else 'غیرفعال'}\n"
        f"• وضعیت آیتم‌ها: {' | '.join(counts) if counts else 'داده‌ای ثبت نشده'}\n"
        f"• آخرین اجرای ثبت‌شده: {latest}"
    )[:MAX_REPLY_CHARS]


def format_queue(queue: Mapping[str, Any]) -> str:
    if queue.get("ok") is not True:
        return "❌ خلاصهٔ صف فعلاً در دسترس نیست."
    queue_counts = queue.get("queue_counts")
    if not isinstance(queue_counts, dict):
        queue_counts = {}
    item_counts = queue.get("item_counts")
    if not isinstance(item_counts, dict):
        item_counts = {}
    names = (
        ("queued", "در انتظار"), ("processing", "در حال پردازش"),
        ("sent", "ارسال‌شده"), ("failed", "ناموفق"),
        ("uncertain", "نامشخص"), ("skipped", "ردشده"),
    )
    line = "📦 صف انتشار (فقط مشاهده)\n" + " | ".join(
        f"{label}: {_nonnegative_count(queue_counts.get(key))}" for key, label in names
    )
    line += f"\n• ارسال‌شده امروز: {_nonnegative_count(queue.get('sent_today'))}"
    if item_counts:
        small = []
        for key in ("ready", "sent", "failed", "uncertain"):
            if key in item_counts:
                small.append(f"{key}: {_nonnegative_count(item_counts.get(key))}")
        if small:
            line += "\n• آیتم‌های مخزن: " + " | ".join(small)
    line += "\n• هیچ موردی از صف با این فرمان تغییر نمی‌کند."
    return line[:MAX_REPLY_CHARS]


def format_ai_status(provider: str, data: Mapping[str, Any]) -> str:
    label = provider.upper()
    if data.get("ok") is not True:
        error = re.sub(r"[^a-z0-9_-]", "", str(data.get("error") or "status_unavailable").lower())[:60]
        return f"🤖 وضعیت {label}: در دسترس نیست ({error or 'unknown'})."
    status = re.sub(r"[^a-z0-9_-]", "", str(data.get("status") or "unknown").lower())[:60]
    used = _nonnegative_count(data.get("calls_used"))
    limit = _nonnegative_count(data.get("daily_limit"))
    remaining = _nonnegative_count(data.get("remaining"))
    cooldown = data.get("cooldown_active") is True
    code = re.sub(r"[^a-z0-9_-]", "", str(data.get("last_error_code") or "").lower())[:60]
    state = "در دورهٔ توقف" if cooldown else ("آماده طبق ledger" if data.get("ready") is True else "غیرآماده")
    return (
        f"🤖 وضعیت {label}\n"
        f"• وضعیت: {state} ({status})\n"
        f"• سهمیهٔ روزانه: {used}/{limit}؛ باقی‌مانده: {remaining}\n"
        f"• خطای اخیر: {code or 'ندارد'}"
    )


def format_ai_statuses(results: Mapping[str, Mapping[str, Any]]) -> str:
    pieces = ["🤖 وضعیت دفتر سهمیهٔ هوش مصنوعی"]
    for provider in PROVIDERS:
        data = results.get(provider)
        if not isinstance(data, Mapping):
            data = {"ok": False, "error": "status_unavailable"}
        pieces.append(format_ai_status(provider, data))
    return "\n\n".join(pieces)[:MAX_REPLY_CHARS]


def fetch_recent_github_runs(*, opener: Any | None = None, token: str | None = None) -> list[dict[str, Any]]:
    """Read the public repository's latest Actions status; never return raw logs."""
    clean_token = (os.getenv("GITHUB_TOKEN", "") if token is None else token).strip()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Rubika-Agent-ReadOnly-Operations-Console/1.0",
    }
    if clean_token:
        headers["Authorization"] = "Bearer " + clean_token
    request = urllib.request.Request(GITHUB_RUNS_URL, headers=headers, method="GET")
    try:
        with (opener or _opener()).open(request, timeout=8) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                return []
            data = _decode_json(response.read(256 * 1024), "github_status_invalid_json")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, OwnerConsoleError):
        return []
    runs = data.get("workflow_runs")
    if not isinstance(runs, list):
        return []
    safe = []
    for run in runs[:10]:
        if not isinstance(run, dict):
            continue
        name = str(run.get("name") or "Workflow")
        name = re.sub(r"[\r\n\t]+", " ", name)
        name = re.sub(r"[^A-Za-z0-9 _.,:()/-]", "", name)[:80].strip() or "Workflow"
        branch = re.sub(r"[^A-Za-z0-9_.:/-]", "", str(run.get("head_branch") or ""))[:80]
        status = re.sub(r"[^a-z_-]", "", str(run.get("status") or "unknown").lower())[:20]
        conclusion = re.sub(r"[^a-z_-]", "", str(run.get("conclusion") or status).lower())[:20]
        run_id = run.get("id")
        if type(run_id) is not int or run_id < 1:
            continue
        safe.append({
            "id": run_id,
            "name": name,
            "branch": branch,
            "status": status or "unknown",
            "conclusion": conclusion or "unknown",
        })
    return safe[:5]


class OperationsConsole:
    def __init__(
        self,
        *,
        bot: RubikaBotAPI,
        control: ControlWorker,
        owner_chat_id: str,
        owner_user_id: str | None,
        github_runs: Callable[[], list[dict[str, Any]]] | None = None,
    ):
        self.bot = bot
        self.control = control
        self.owner_chat_id = owner_chat_id
        self.owner_user_id = owner_user_id
        self.github_runs = github_runs or fetch_recent_github_runs
        self.conversation_state = CONSOLE_IDLE
        self._rubika_offset: str | None = None

    def _reply(self, command: ParsedCommand) -> str:
        if command.name == "help":
            return (
                "🤖 دستیار مدیریتی روبیکا — مرحلهٔ فقط‌خواندنی\n\n"
                "/status — وضعیت Worker، پایگاه داده و آخرین اجرا\n"
                "/queue — خلاصهٔ صف بدون تغییر وضعیت آن\n"
                "/ai-status — وضعیت ledger و سهمیهٔ دو provider\n"
                "/ai-status groq — وضعیت یک provider مشخص\n"
                "/report — گزارش ترکیبی پروژه، صف، سهمیهٔ AI و CI اخیر\n"
                "/help — همین راهنما\n\n"
                "این نسخه فقط گزارش می‌خواند؛ کد را تغییر نمی‌دهد، فرمان اجرا نمی‌کند، "
                "در صف دست نمی‌برد و هیچ‌چیز را merge/deploy/publish نمی‌کند."
            )
        if command.name == "status":
            try:
                return format_status(self.control.status())
            except OwnerConsoleError as exc:
                return "❌ گزارش وضعیت در دسترس نیست؛ کد: " + exc.code
        if command.name == "queue":
            try:
                return format_queue(self.control.queue_summary())
            except OwnerConsoleError as exc:
                return "❌ خلاصهٔ صف در دسترس نیست؛ کد: " + exc.code
        if command.name == "ai-status":
            providers = (command.provider,) if command.provider else PROVIDERS
            result = {}
            for provider in providers:
                try:
                    result[provider] = self.control.ai_status(provider)
                except OwnerConsoleError as exc:
                    result[provider] = {"ok": False, "error": exc.code}
            if command.provider:
                return format_ai_status(command.provider, result[command.provider])
            return format_ai_statuses(result)
        if command.name == "report":
            pieces = ["🧾 گزارش Rubika-Agent"]
            try:
                pieces.append(format_status(self.control.status()))
            except OwnerConsoleError as exc:
                pieces.append("وضعیت Worker: در دسترس نیست (" + exc.code + ")")
            try:
                pieces.append(format_queue(self.control.queue_summary()))
            except OwnerConsoleError as exc:
                pieces.append("خلاصهٔ صف: در دسترس نیست (" + exc.code + ")")
            ai_rows = {}
            for provider in PROVIDERS:
                try:
                    ai_rows[provider] = self.control.ai_status(provider)
                except OwnerConsoleError as exc:
                    ai_rows[provider] = {"ok": False, "error": exc.code}
            pieces.append(format_ai_statuses(ai_rows))
            runs = self.github_runs()
            pieces.append(format_github_runs(runs))
            return "\n\n".join(pieces)[:MAX_REPLY_CHARS]
        return "این فرمان پشتیبانی نمی‌شود. برای فهرست فرمان‌های مجاز /help را بفرست."

    def _persist_conversation_state(self) -> None:
        # There is no cursor before the first poll bootstrap. Once bootstrapped, reuse
        # the existing opaque offset field; no new D1 schema or endpoint is introduced.
        if self._rubika_offset is None:
            return
        self.control.set_offset(encode_console_state(self._rubika_offset, self.conversation_state))

    def _send_once(self, text: str, keypad: Mapping[str, Any] | None = None) -> None:
        if keypad is None:
            response = self.bot.send_message(self.owner_chat_id, text)
        else:
            response = self.bot.send_message(self.owner_chat_id, text, inline_keypad=keypad)
        if isinstance(response, Mapping) and response.get("status") not in {None, "OK"}:
            raise OwnerConsoleError("rubika_send_rejected")

    def _disable_source_keypad(self, message: Mapping[str, Any]) -> None:
        message_id = message.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            return
        edit = getattr(self.bot, "edit_inline_keypad", None)
        if not callable(edit):
            return
        try:
            edit(self.owner_chat_id, message_id, {"rows": []})
        except OwnerConsoleError:
            return

    def _finish_update(self, update_id: str, status: str, code: str) -> None:
        try:
            self.control.finish(update_id, status, code)
        except OwnerConsoleError:
            return

    def process_update(
        self,
        update: Mapping[str, Any],
        *,
        resolved_owner_user_id: str | None = None,
    ) -> str:
        _log_update_diagnostic(update)
        if not self.owner_chat_id or not self.owner_user_id:
            return "ignored"
        event = _owner_private_event(update, self.owner_chat_id, self.owner_user_id)
        update_id = _message_identity(update)
        if event is None or update_id is None:
            return "ignored"
        kind, message_text, button_id, nested_message = event
        command = parse_command(message_text or "") if kind == "message" else ParsedCommand("unknown", None, "unknown")
        chat_hash = hashlib.sha256(self.owner_chat_id.encode("utf-8")).hexdigest()
        try:
            claim = self.control.claim(update_id, chat_hash, command.audit_name)
        except OwnerConsoleError:
            raise
        if claim.get("claimed") is not True:
            return "duplicate" if claim.get("duplicate") is True else "not_claimed"

        response_status = "answered"
        result_code = "reply_sent"
        try:
            if kind == "callback":
                self._disable_source_keypad(nested_message)
                if button_id == BUTTON_MOB_POST_FROM_X:
                    self.conversation_state = CONSOLE_WAITING_FOR_LINK
                    self._persist_conversation_state()
                    self._send_once(
                        "لینک پست X را بفرست. فعلاً فقط لینک دریافت می‌شود؛ پردازش در به‌روزرسانی بعدی اضافه خواهد شد.",
                        waiting_inline_keypad(),
                    )
                elif button_id == BUTTON_CANCEL:
                    self.conversation_state = CONSOLE_IDLE
                    self._persist_conversation_state()
                    self._send_once("به منوی اصلی برگشتی.", menu_inline_keypad())
                else:
                    response_status = "ignored"
                    result_code = "unsupported_button"
                    self._finish_update(update_id, response_status, result_code)
                    return "ignored"
            elif command.name == "menu":
                self.conversation_state = CONSOLE_IDLE
                self._persist_conversation_state()
                self._send_once("🤖 منوی مدیریت Rubika-Agent", menu_inline_keypad())
            elif command.name == "help":
                self._send_once(self._reply(command), menu_inline_keypad())
            elif self.conversation_state == CONSOLE_WAITING_FOR_LINK and command.name == "unknown":
                canonical = parse_x_status_url(message_text or "")
                if canonical:
                    self.conversation_state = CONSOLE_IDLE
                    self._persist_conversation_state()
                    self._send_once(
                        "✅ لینک دریافت شد: " + canonical
                        + "\nپردازش لینک در به‌روزرسانی بعدی پیاده‌سازی می‌شود. فعلاً دانلود، ترجمه یا انتشار انجام نشده.",
                        menu_inline_keypad(),
                    )
                else:
                    self._send_once(
                        "یک لینک معتبر از فرم x.com/نام‌کاربری/status/شناسه بفرست، یا «لغو» را بزن.",
                        waiting_inline_keypad(),
                    )
            else:
                self._send_once(self._reply(command))
        except OwnerConsoleError as exc:
            self._finish_update(update_id, "uncertain", exc.code)
            return "uncertain"

        self._finish_update(update_id, response_status, result_code)
        return "answered" if response_status == "answered" else response_status

    def poll_once(self) -> dict[str, Any]:
        state = self.control.get_state()
        offset_id, mode = decode_console_state(state.get("offset_id"))
        self._rubika_offset = offset_id
        self.conversation_state = mode
        response = self.bot.get_updates(offset_id)
        data = response.get("data")
        if not isinstance(data, dict):
            raise OwnerConsoleError("rubika_updates_invalid_shape")
        updates = data.get("updates", [])
        if not isinstance(updates, list):
            raise OwnerConsoleError("rubika_updates_invalid_shape")
        stats = {
            "updates_received": len(updates),
            "answered": 0,
            "ignored": 0,
            "duplicates": 0,
            "uncertain": 0,
            "bootstrapped": False,
            "offset_advanced": False,
        }
        next_offset = data.get("next_offset_id")
        valid_next_offset = (
            isinstance(next_offset, str)
            and 1 <= len(next_offset) <= MAX_UPDATE_ID_CHARS
        )

        # Baseline an existing bot cursor before handling messages from an old backlog.
        if offset_id is None:
            if valid_next_offset:
                self._rubika_offset = next_offset
                self.control.set_offset(encode_console_state(next_offset, self.conversation_state))
                stats["offset_advanced"] = True
            stats["bootstrapped"] = True
            stats["ignored"] = len(updates[:MAX_UPDATES_PER_POLL])
            return stats

        # The workflow requires the configured owner user ID; no sender inference.
        if not self.owner_user_id:
            stats["ignored"] = len(updates[:MAX_UPDATES_PER_POLL])
            if valid_next_offset:
                self._rubika_offset = next_offset
                self.control.set_offset(encode_console_state(next_offset, self.conversation_state))
                stats["offset_advanced"] = True
            return stats

        for update in updates[:MAX_UPDATES_PER_POLL]:
            if not isinstance(update, Mapping):
                _log_update_diagnostic(update)
                stats["ignored"] += 1
                continue
            result = self.process_update(update)
            if result in {"answered", "answered_unconfirmed"}:
                stats["answered"] += 1
            elif result == "uncertain":
                stats["uncertain"] += 1
            elif result == "duplicate":
                stats["duplicates"] += 1
            else:
                stats["ignored"] += 1

        if valid_next_offset:
            self._rubika_offset = next_offset
            self.control.set_offset(encode_console_state(next_offset, self.conversation_state))
            stats["offset_advanced"] = True
        return stats


def format_github_runs(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "🧪 CI اخیر: اطلاعات فعلاً در دسترس نیست."
    # Surface recent failures first, then fill remaining slots with the latest runs.
    ordered: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for run in runs:
        conclusion = str(run.get("conclusion") or run.get("status") or "").lower()
        run_id = run.get("id")
        if conclusion in {"failure", "timed_out", "action_required", "cancelled"} and type(run_id) is int and run_id not in seen_ids:
            ordered.append(run)
            seen_ids.add(run_id)
    for run in runs:
        run_id = run.get("id")
        if type(run_id) is int and run_id not in seen_ids:
            ordered.append(run)
            seen_ids.add(run_id)
    has_failure = any(
        str(run.get("conclusion") or "").lower() in {"failure", "timed_out", "action_required", "cancelled"}
        for run in ordered[:3]
    )
    pieces = ["🧪 خطاهای اخیر و اجراهای GitHub Actions" if has_failure
              else "🧪 اجراهای اخیر GitHub Actions"]
    for run in ordered[:3]:
        name = str(run.get("name") or "Workflow")[:70]
        status = re.sub(r"[^a-z_-]", "", str(run.get("conclusion") or run.get("status") or "unknown").lower())[:20]
        branch = re.sub(r"[^A-Za-z0-9_.:/-]", "", str(run.get("branch") or ""))[:50]
        run_id = run.get("id")
        pieces.append(f"• {name}: {status or 'unknown'}" + (f" ({branch})" if branch else "") +
                      (f" — run {run_id}" if type(run_id) is int else ""))
    return "\n".join(pieces)


def _required_env() -> tuple[str, str, str, str, str]:
    token = os.getenv("RUBIKA_TOKEN", "").strip()
    owner_chat_id = os.getenv("RUBIKA_OWNER_CHAT_ID", "").strip()
    owner_user_id = os.getenv("RUBIKA_OWNER_USER_ID", "").strip()
    api_token = os.getenv("AGENT_API_TOKEN", "").strip()
    control_url = os.getenv("AGENT_API_BASE_URL", DEFAULT_CONTROL_URL).strip()
    if len(token) < 16:
        raise OwnerConsoleError("rubika_token_missing_or_short")
    if len(owner_chat_id) < 3:
        raise OwnerConsoleError("rubika_owner_chat_id_missing")
    if len(owner_user_id) < 3:
        raise OwnerConsoleError("rubika_owner_user_id_missing")
    if len(api_token) < 32:
        raise OwnerConsoleError("control_api_token_missing_or_short")
    return token, owner_chat_id, owner_user_id, api_token, control_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run local checks without network access or credentials.")
    parser.add_argument("--once", action="store_true", help="Poll one Rubika update batch and exit.")
    args = parser.parse_args(argv)
    if args.self_test:
        assert parse_command("/status").name == "status"
        assert parse_command("/queue").name == "queue"
        assert parse_command("/ai-status groq").audit_name == "ai-status:groq"
        assert parse_command("not a command").name == "unknown"
        assert "انتشار" in format_status({
            "ok": True, "database": "connected", "agent": {"enabled": 0},
            "publishing": False, "capabilities": {"publishing": False},
            "item_counts": [], "recent_runs": [],
        })
        assert "هیچ موردی" in format_queue({"ok": True, "queue_counts": {}, "item_counts": {}})
        print(json.dumps({"ok": True, "mode": "self-test", "publishing": False, "queue_mutation": False}))
        return 0

    if not args.once:
        parser.error("use --once; the scheduled workflow owns polling cadence")
    try:
        token, owner_chat_id, owner_user_id, api_token, control_url = _required_env()
        bot = RubikaBotAPI(token)
        control = ControlWorker(control_url, api_token)
        console = OperationsConsole(
            bot=bot,
            control=control,
            owner_chat_id=owner_chat_id,
            owner_user_id=owner_user_id,
        )
        stats = console.poll_once()
        print(json.dumps({"ok": True, "mode": "poll-once", **stats}, sort_keys=True))
        return 0
    except OwnerConsoleError as exc:
        print(json.dumps({"ok": False, "mode": "poll-once", "error_code": exc.code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())