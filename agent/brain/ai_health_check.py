"""Manual, read-only provider health checks.

Requests bypass the D1 ledger and report only safe diagnostic fields. Provider
response text and credentials are never written to logs or the job summary.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ai_provider_clients import ProviderClientError, ProviderConfig, ProviderResult, invoke_provider, provider_config


PROVIDERS = ("groq", "openrouter")
HEALTH_PROMPT = "Reply with exactly OK."
MAX_OUTPUT_TOKENS = 10
FOLLOWUP_OUTPUT_TOKENS = 64
TIMEOUT_SECONDS = 20
DEFAULT_MODELS = {"groq": "openai/gpt-oss-20b", "openrouter": "openrouter/free"}
SAFE_FIELD = re.compile(r"[A-Za-z0-9_.-]{1,60}")


@dataclass(frozen=True)
class ProviderHealthResult:
    provider: str
    model: str
    max_output_tokens: int
    status: str
    latency_ms: int
    http_status: int | None = None
    exception_class: str | None = None
    provider_error_type: str | None = None
    provider_error_code: str | None = None
    response_content_non_empty: bool | None = None
    finish_reason: str | None = None


def _safe_field(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", value)[:60]
    return cleaned if cleaned and SAFE_FIELD.fullmatch(cleaned) else None


def _model_label(provider: str, config: ProviderConfig | None = None) -> str:
    if config is not None:
        candidate = config.model
    else:
        env_name = "GROQ_MODEL" if provider == "groq" else "OPENROUTER_MODEL"
        candidate = os.environ.get(env_name, DEFAULT_MODELS.get(provider, "unknown"))
    candidate = str(candidate or DEFAULT_MODELS.get(provider, "unknown")).strip()
    # Model/route names are public identifiers, but still constrain log output.
    if not re.fullmatch(r"[@A-Za-z0-9._:/-]{1,160}", candidate):
        return DEFAULT_MODELS.get(provider, "unknown")
    return candidate


def _status_for_error(code: str, http_status: int | None) -> str:
    normalized = str(code or "").strip().lower()
    if (
        normalized.endswith("_api_key_missing")
        or http_status in {401, 403}
        or normalized in {"provider_http_401", "provider_http_403"}
        or "unauthorized" in normalized
        or "authentication" in normalized
    ):
        return "auth error"
    if (
        http_status == 429
        or normalized in {"provider_http_429", "rate_limited", "too_many_requests"}
        or "rate_limit" in normalized
    ):
        return "rate limit"
    if normalized == "provider_text_missing" and http_status == 200:
        return "empty response"
    return "other error"


def _http_status(exc: BaseException) -> int | None:
    diagnostics = getattr(exc, "diagnostics", None)
    if isinstance(diagnostics, dict):
        value = diagnostics.get("http_status")
        if type(value) is int and 100 <= value <= 599:
            return value
    code = _safe_field(getattr(exc, "code", ""))
    match = re.fullmatch(r"provider_http_(\d{3})", code or "")
    if match:
        value = int(match.group(1))
        if 100 <= value <= 599:
            return value
    return None


def _diagnostic(exc: BaseException, name: str) -> str | None:
    diagnostics = getattr(exc, "diagnostics", None)
    if isinstance(diagnostics, dict):
        return _safe_field(diagnostics.get(name))
    return None


def _exception_result(
    provider: str,
    model: str,
    max_tokens: int,
    started: float,
    exc: BaseException,
) -> ProviderHealthResult:
    code = _safe_field(getattr(exc, "code", None))
    status_code = _http_status(exc)
    exception_class = _safe_field(type(exc).__name__)
    status = _status_for_error(code or "", status_code)
    content_non_empty: bool | None = False if code == "provider_text_missing" and status_code == 200 else None
    return ProviderHealthResult(
        provider=provider,
        model=model,
        max_output_tokens=max_tokens,
        status=status,
        latency_ms=max(0, round((time.monotonic() - started) * 1000)),
        http_status=status_code,
        exception_class=exception_class,
        provider_error_type=_diagnostic(exc, "provider_error_type"),
        provider_error_code=_diagnostic(exc, "provider_error_code") or code,
        response_content_non_empty=content_non_empty,
        finish_reason=_diagnostic(exc, "finish_reason"),
    )


def _probe_once(provider: str, config: ProviderConfig, max_tokens: int) -> ProviderHealthResult:
    started = time.monotonic()
    try:
        # Deliberately bypass call_provider_with_ledger: no D1 ledger access.
        result = invoke_provider(
            config,
            HEALTH_PROMPT,
            max_output_tokens=max_tokens,
            timeout_seconds=TIMEOUT_SECONDS,
            role="diagnose",
        )
    except Exception as exc:
        return _exception_result(provider, config.model, max_tokens, started, exc)

    if not isinstance(result, ProviderResult):
        return ProviderHealthResult(
            provider=provider,
            model=config.model,
            max_output_tokens=max_tokens,
            status="other error",
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
            exception_class="UnexpectedResult",
            http_status=None,
            response_content_non_empty=None,
        )
    content_non_empty = bool(isinstance(result.text, str) and result.text.strip())
    finish_reason = _safe_field(result.finish_reason)
    if not content_non_empty:
        return ProviderHealthResult(
            provider=provider,
            model=config.model,
            max_output_tokens=max_tokens,
            status="empty response",
            latency_ms=max(0, round((time.monotonic() - started) * 1000)),
            http_status=result.http_status if type(result.http_status) is int else 200,
            response_content_non_empty=False,
            finish_reason=finish_reason,
        )
    return ProviderHealthResult(
        provider=provider,
        model=config.model,
        max_output_tokens=max_tokens,
        status="ok",
        latency_ms=max(0, round((time.monotonic() - started) * 1000)),
        http_status=result.http_status if type(result.http_status) is int else 200,
        response_content_non_empty=True,
        finish_reason=finish_reason,
    )


def check_provider(provider: str) -> tuple[ProviderHealthResult, ...]:
    """Check one provider; only an HTTP-200 empty response earns a 64-token follow-up."""
    started = time.monotonic()
    try:
        config = provider_config(provider)
    except Exception as exc:
        result = _exception_result(provider, _model_label(provider), MAX_OUTPUT_TOKENS, started, exc)
        return (result,)

    first = _probe_once(provider, config, MAX_OUTPUT_TOKENS)
    if first.status != "empty response":
        return (first,)
    # One bounded follow-up helps distinguish an empty 10-token reasoning response
    # from a provider that continues returning no content.
    second = _probe_once(provider, config, FOLLOWUP_OUTPUT_TOKENS)
    return (first, second)


def format_summary(results: tuple[ProviderHealthResult, ...]) -> str:
    lines = [
        "## Manual AI provider health check",
        "",
        "| Provider | Model/route | Max tokens | Result | Latency | HTTP | Exception | Provider error type/code | Content non-empty | finish_reason |",
        "|---|---|---:|---|---:|---:|---|---|---|---|",
    ]
    labels = {"groq": "Groq", "openrouter": "OpenRouter"}
    for row in results:
        http_value = str(row.http_status) if row.http_status is not None else "unknown"
        content_value = (
            "yes" if row.response_content_non_empty is True
            else "no" if row.response_content_non_empty is False
            else "unknown"
        )
        error_fields = []
        if row.provider_error_type:
            error_fields.append("type=" + row.provider_error_type)
        if row.provider_error_code:
            error_fields.append("code=" + row.provider_error_code)
        lines.append(
            "| "
            + " | ".join((
                labels.get(row.provider, "Provider"),
                row.model,
                str(row.max_output_tokens),
                row.status,
                f"{row.latency_ms} ms",
                http_value,
                row.exception_class or "-",
                ";".join(error_fields) or "-",
                content_value,
                row.finish_reason or "-",
            ))
            + " |"
        )
    lines.extend([
        "",
        "Only categorized results and sanitized diagnostics are shown; provider response text and credentials are omitted.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    results = tuple(row for provider in PROVIDERS for row in check_provider(provider))
    summary = format_summary(results)
    # Print exactly the same compact table to the job log and Job Summary.
    print(summary, end="")
    summary_path = (os.environ.get("GITHUB_STEP_SUMMARY") or "").strip()
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
