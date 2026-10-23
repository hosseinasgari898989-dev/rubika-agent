"""Manual, read-only provider health checks using the existing HTTP clients.

This intentionally calls invoke_provider directly instead of
call_provider_with_ledger, so it makes no Worker/D1 ledger writes.
Only the result category and elapsed time are reported; response text and
credentials are never printed or written to the job summary.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from .ai_provider_clients import ProviderClientError, invoke_provider, provider_config


PROVIDERS = ("groq", "openrouter")
HEALTH_PROMPT = "Reply with exactly OK."
MAX_OUTPUT_TOKENS = 10
TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class ProviderHealthResult:
    provider: str
    status: str
    latency_ms: int


def _status_for_error(code: str) -> str:
    """Collapse safe client error codes into the four report categories."""
    normalized = str(code or "").strip().lower()
    if (
        normalized.endswith("_api_key_missing")
        or normalized in {"provider_http_401", "provider_http_403"}
        or "unauthorized" in normalized
        or "authentication" in normalized
    ):
        return "auth error"
    if normalized in {"provider_http_429", "rate_limited", "too_many_requests"} or "rate_limit" in normalized:
        return "rate limit"
    return "other error"


def check_provider(provider: str) -> ProviderHealthResult:
    """Perform one small provider request. Failures are data, not job failures."""
    started = time.monotonic()
    try:
        config = provider_config(provider)
        # Deliberately bypass call_provider_with_ledger: this health check must
        # not reserve/finish a D1 usage-ledger record.
        invoke_provider(
            config,
            HEALTH_PROMPT,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            timeout_seconds=TIMEOUT_SECONDS,
            role="diagnose",
        )
        status = "ok"
    except ProviderClientError as exc:
        status = _status_for_error(exc.code)
    except Exception:
        # Never echo exception text: unknown exceptions can contain request details.
        status = "other error"
    latency_ms = max(0, round((time.monotonic() - started) * 1000))
    return ProviderHealthResult(provider=provider, status=status, latency_ms=latency_ms)


def format_summary(results: tuple[ProviderHealthResult, ...]) -> str:
    lines = [
        "## Manual AI provider health check",
        "",
        "| Provider | Result | Latency |",
        "|---|---|---:|",
    ]
    labels = {"groq": "Groq", "openrouter": "OpenRouter"}
    for result in results:
        lines.append(
            f"| {labels.get(result.provider, 'Provider')} | {result.status} | {result.latency_ms} ms |"
        )
    lines.extend(["", "Only the result category and latency are shown. No credentials or provider response text are recorded."])
    return "\n".join(lines) + "\n"


def main() -> int:
    # Check providers independently: one failure never suppresses the other call.
    results = tuple(check_provider(provider) for provider in PROVIDERS)
    summary = format_summary(results)
    summary_path = (os.environ.get("GITHUB_STEP_SUMMARY") or "").strip()
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(summary)
    else:
        # Local invocation remains safe: output contains no provider body or secret.
        print(summary, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
