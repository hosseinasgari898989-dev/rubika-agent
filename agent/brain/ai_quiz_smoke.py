"""Manual Groq-first/OpenRouter-second Persian Poppy Playtime quiz smoke test.

Uses the repository's existing provider configuration and stdlib-only HTTP calls.
The report contains metadata only; credentials and generated quiz text are never logged.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

# Support the documented plain-script invocation from the repository root.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.brain.ai_provider_clients import (  # noqa: E402
    ProviderClientError,
    ProviderConfig,
    provider_config,
)

PROVIDER_ORDER = ("groq", "openrouter")
OPENROUTER_FREE_MODEL = "inclusionai/ling-3.2-flash:free"
DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-20b",
    "openrouter": OPENROUTER_FREE_MODEL,
}
MAX_RESPONSE_BYTES = 1_000_000
TIMEOUT_SECONDS = 45
MAX_OUTPUT_TOKENS = 500

SYSTEM_PROMPT = (
    "تو یک نویسندهٔ آزمون فارسی هستی. فقط یک شیء JSON معتبر برگردان؛ "
    "هیچ متن، توضیح یا کدبلاک بیرون JSON ننویس. "
    "کلیدها دقیقاً question و options و answer_index باشند. "
    "options دقیقاً سه رشتهٔ غیرخالی و answer_index یک عدد صحیح صفرمبنا از ۰ تا ۲ باشد."
)
QUIZ_PROMPT = (
    "یک سؤال چندگزینه‌ای دشوار اما منصفانه به زبان فارسی دربارهٔ جزئیات داستان، "
    "شخصیت‌ها یا رویدادهای بازی Poppy Playtime طرح کن. سؤال باید مشخص و قابل‌پاسخ "
    "باشد، سه گزینهٔ کوتاه و متفاوت داشته باشد و دقیقاً یک پاسخ درست داشته باشد. "
    "از ساختن جزئیات نامطمئن خودداری کن. فقط JSON با ساختار "
    '{"question":"...","options":["...","...","..."],"answer_index":0} '
    "برگردان و هیچ کلید اضافه‌ای نداشته باش."
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward provider credentials through redirects."""

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


def _safe_model_label(provider: str, environ: Mapping[str, str]) -> str:
    model_name = "GROQ_MODEL" if provider == "groq" else "OPENROUTER_MODEL"
    candidate = str(environ.get(model_name) or DEFAULT_MODELS[provider]).strip()
    if re.fullmatch(r"[@A-Za-z0-9._:/-]{1,160}", candidate):
        return candidate
    return DEFAULT_MODELS[provider]


def _empty_tokens() -> dict[str, int | None]:
    return {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


def _token_usage(payload: Any) -> dict[str, int | None]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    result: dict[str, int | None] = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        result[key] = value if type(value) is int and 0 <= value <= 10_000_000 else None
    return result


def _safe_error_code(value: str) -> str:
    candidate = str(value or "").lower()
    return candidate if re.fullmatch(r"[a-z0-9_.-]{1,80}", candidate) else "provider_error"


def _decode_payload(raw: bytes) -> tuple[dict[str, Any] | None, str | None]:
    if len(raw) > MAX_RESPONSE_BYTES:
        return None, "response_too_large"
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, "bad_json"
    if not isinstance(decoded, dict):
        return None, "bad_response"
    return decoded, None


def _request(
    config: ProviderConfig,
    *,
    timeout: int = TIMEOUT_SECONDS,
) -> tuple[int | None, dict[str, Any] | None, str | None]:
    """Make one OpenAI-compatible request and return only sanitized error codes."""
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": QUIZ_PROMPT},
        ],
        "temperature": 0.2,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        config.url,
        data=json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + config.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Rubika-Agent-AI-Quiz-Smoke/1.0",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            payload, decode_error = _decode_payload(response.read(MAX_RESPONSE_BYTES + 1))
            if status != 200:
                return status, payload, "http_" + str(status)
            return status, payload, decode_error
    except urllib.error.HTTPError as exc:
        try:
            payload, _ = _decode_payload(exc.read(MAX_RESPONSE_BYTES + 1))
        except OSError:
            payload = None
        if isinstance(payload, dict):
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after:
                payload["_retry_after"] = str(retry_after)[:20]
        return int(exc.code), payload, "http_" + str(int(exc.code))
    except TimeoutError:
        return None, None, "timeout"
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            return None, None, "timeout"
        return None, None, "network"
    except (OSError, ValueError):
        return None, None, "network"


def _message_content(payload: Any) -> str:
    try:
        message = payload["choices"][0]["message"]
        content = message.get("content") if isinstance(message, dict) else None
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ).strip()
    return ""


def _quiz_json_ok(payload: Any) -> bool:
    content = _message_content(payload)
    if not content:
        return False
    try:
        quiz = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(quiz, dict) or set(quiz) != {"question", "options", "answer_index"}:
        return False
    question = quiz.get("question")
    options = quiz.get("options")
    answer_index = quiz.get("answer_index")
    return (
        isinstance(question, str)
        and bool(question.strip())
        and isinstance(options, list)
        and len(options) == 3
        and all(isinstance(option, str) and bool(option.strip()) for option in options)
        and type(answer_index) is int
        and 0 <= answer_index < 3
    )


def _error_detail(payload: Any, secret: str) -> dict[str, Any] | None:
    """Sanitized provider error fields only: no raw body, no credentials."""
    if not isinstance(payload, dict):
        return None
    detail: dict[str, Any] = {}
    error = payload.get("error")
    if isinstance(error, dict):
        for key in ("type", "code", "param"):
            value = error.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                detail[key] = re.sub(r"[^A-Za-z0-9_.:-]", "", str(value))[:60]
        message = error.get("message")
        if isinstance(message, str):
            cleaned = message.replace(secret, "<redacted>") if secret else message
            detail["message"] = re.sub(r"\\s+", " ", cleaned).strip()[:200]
        metadata = error.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("provider_name"), str):
            detail["upstream_provider"] = re.sub(r"[^A-Za-z0-9_. -]", "", metadata["provider_name"])[:40]
    elif isinstance(error, str):
        cleaned = error.replace(secret, "<redacted>") if secret else error
        detail["message"] = re.sub(r"\\s+", " ", cleaned).strip()[:200]
    retry_after = payload.get("_retry_after")
    if isinstance(retry_after, str):
        detail["retry_after"] = re.sub(r"[^0-9A-Za-z:, -]", "", retry_after)[:20]
    return detail or None


def _provider_result(
    provider: str,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "provider": provider,
        "model": _safe_model_label(provider, environ),
        "http_status": None,
        "quiz_json_ok": False,
        "tokens": _empty_tokens(),
        "error": None,
    }
    provider_env = dict(environ)
    if provider == "openrouter" and not str(provider_env.get("OPENROUTER_MODEL") or "").strip():
        provider_env["OPENROUTER_MODEL"] = OPENROUTER_FREE_MODEL

    try:
        config = provider_config(provider, environ=provider_env)
        entry["model"] = config.model
        if provider == "openrouter" and not config.model.endswith(":free"):
            entry["error"] = "openrouter_model_not_free"
            return entry
    except ProviderClientError as exc:
        entry["error"] = _safe_error_code(exc.code)
        return entry

    status, payload, request_error = _request(config)
    entry["http_status"] = status
    entry["tokens"] = _token_usage(payload)
    entry["error_detail"] = _error_detail(payload, config.api_key)
    if request_error:
        entry["error"] = _safe_error_code(request_error)
        return entry
    if status != 200:
        entry["error"] = "http_" + str(status) if status is not None else "network"
        return entry

    entry["quiz_json_ok"] = _quiz_json_ok(payload)
    if not entry["quiz_json_ok"]:
        entry["error"] = "bad_quiz_json"
    return entry


def run_smoke(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    providers = [_provider_result(name, env) for name in PROVIDER_ORDER]
    return {
        "ok": all(
            row["http_status"] == 200 and row["quiz_json_ok"] is True and row["error"] is None
            for row in providers
        ),
        "providers": providers,
    }


def main() -> int:
    report = run_smoke()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
