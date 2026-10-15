"""Bounded, auditable recovery helpers for the agent brain.

Only transient infrastructure errors may be retried automatically. Content decisions,
missing music confidence, permission failures and validation failures are never bypassed.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_TRANSIENT_EXACT = frozenset({
    "control_api_unreachable",
    "control_api_http_408",
    "control_api_http_425",
    "control_api_http_429",
    "control_api_http_500",
    "control_api_http_502",
    "control_api_http_503",
    "control_api_http_504",
    "music_transcode_timeout",
    "music_api_timeout",
})


_ERROR_GUIDANCE = {
    "control_api_unreachable": "Check Cloudflare Worker availability and network, then retry.",
    "control_api_http_429": "The AI endpoint rate-limited this request; wait briefly and retry.",
    "control_api_http_500": "Cloudflare returned a temporary server error; retry once and inspect Worker logs if it persists.",
    "control_api_http_502": "Music inference failed upstream; retry once and inspect the Worker diagnostic event if it persists.",
    "control_api_http_503": "The AI service or usage counter is unavailable; retry after checking Worker bindings and D1.",
    "control_api_http_504": "The service timed out; retry once and inspect current platform status.",
    "ffmpeg_not_installed": "Install FFmpeg in the execution environment; do not skip soundtrack validation.",
    "music_transcode_timeout": "The audio extraction exceeded its time budget; retry once, then inspect the source file.",
    "music_sample_too_small": "The extracted soundtrack is too small to classify; verify the video's audio stream.",
    "music_probability_ambiguous": "The music result was uncertain; inspect the stored probability or use human review.",
    "music_answer_missing_or_invalid": "The model returned no usable music score; inspect the Worker diagnostic event.",
    "source_below_policy_minimum_resolution": "The source does not meet its creator policy's minimum resolution.",
    "brain_technical_quality_check_failed": "The source failed the agent brain's technical quality gate.",
    "poppy_relevance_not_found_by_brain": "No Poppy Playtime term was found in the title, caption or hashtags.",
    "edit_signal_not_found_by_brain": "No explicit edit marker was found in the title, caption or hashtags.",
    "cobalt_unreachable": "Check the local Cobalt service before retrying the download.",
    "cobalt_rate_limited": "The media provider rate-limited the download; wait before retrying.",
    "source_restricted_or_unavailable": "The source is private, removed or unavailable; skip it instead of bypassing restrictions.",
}


def safe_error_code(exc: BaseException) -> str:
    value = getattr(exc, "code", None)
    if not isinstance(value, str) or not value:
        value = type(exc).__name__
    return re.sub(r"[^A-Za-z0-9_-]", "", value)[:80] or "unknown_error"


def is_retryable_error(exc: BaseException) -> bool:
    code = safe_error_code(exc)
    return code in _TRANSIENT_EXACT or (
        code.startswith("control_api_http_")
        and code.rsplit("_", 1)[-1] in {"500", "502", "503", "504"}
    )


def recovery_guidance(code: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]", "", str(code))[:80]
    if normalized.startswith("control_api_http_"):
        normalized = "control_api_http_" + normalized.rsplit("_", 1)[-1]
    return _ERROR_GUIDANCE.get(normalized, "Review the safe error code in the stage report; the content/permission gate was not bypassed.")


def call_with_bounded_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = 2,
    sleep_seconds: float = 0.75,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[T, dict[str, Any]]:
    """Retry only allow-listed transient errors and return a structured recovery receipt."""
    attempts = max(1, min(2, int(max_attempts)))
    error_codes: list[str] = []
    for index in range(1, attempts + 1):
        try:
            value = operation()
            return value, {
                "attempts": index,
                "recovered": bool(error_codes),
                "error_codes": error_codes,
                "action": "retry_succeeded" if error_codes else "no_retry_needed",
            }
        except Exception as exc:
            code = safe_error_code(exc)
            error_codes.append(code)
            retry = index < attempts and is_retryable_error(exc)
            receipt = {
                "attempts": index,
                "recovered": False,
                "error_codes": error_codes,
                "action": "retrying_transient_error" if retry else "retry_not_safe_or_exhausted",
                "guidance": recovery_guidance(code),
            }
            try:
                setattr(exc, "recovery_diagnostics", receipt)
            except Exception:
                pass
            if not retry:
                raise
            sleeper(max(0.0, min(2.0, float(sleep_seconds))))
    raise RuntimeError("bounded_recovery_unreachable")
