"""Bounded advisory error supervisor.

Workers AI can recommend a next diagnostic step, but cannot execute it. If the
remote API or model is unavailable, use a deterministic fail-closed fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from agent.brain.cloud_state import CloudState

EVENT_CODES = {
    "source_blocked", "discovery_failed", "report_push_failed",
    "queue_intake_failed", "queue_read_failed", "duplicate_candidates_skipped",
    "no_new_candidates", "download_failed", "media_validation_failed",
    "mega_upload_failed", "rubika_delivery_failed", "rubika_delivery_uncertain",
    "cleanup_failed", "official_news_failed", "ai_model_failed",
    "translation_failed", "workflow_tests_failed", "unknown_error",
}
STAGES = {
    "collector", "report", "queue_intake", "queue_read", "download",
    "media_validation", "mega_storage", "moderation", "publish", "cleanup",
    "official_news", "ai", "translation", "tests", "unknown",
}
PLATFORMS = {"none", "tiktok", "youtube", "instagram", "mob_official", "x", "steam"}
COUNT_KEYS = {"queued", "processing", "sent", "failed", "uncertain", "skipped", "sent_today"}
ACTIONS = {
    "retry_next_scheduled_run", "skip_source_and_continue", "inspect_queue",
    "human_review", "no_action",
}
REASONS = {
    "temporary_network", "source_blocked", "queue_duplicate", "uncertain_delivery",
    "stale_metadata", "validation_rejected", "auth_or_configuration",
    "quota_exhausted", "model_output_invalid", "unknown_error",
}
NEXT_STEPS = {
    "wait_for_next_schedule", "continue_with_other_sources", "check_agent_api_health",
    "inspect_d1_queue_counts", "retry_only_after_confirming_not_sent",
    "review_error_code_and_recent_logs", "review_publisher_delivery_receipt",
    "inspect_media_validation_report", "preserve_mega_file_and_ledger",
    "recheck_mega_listing", "none",
}


def _recommendation(action: str, reason: str, next_step: str, human_review: bool) -> dict[str, Any]:
    return {
        "action": action,
        "reason_code": reason,
        "next_step_code": next_step,
        "human_review": human_review,
    }


def local_recommendation(*, event_code: str, stage: str, error_code: str) -> dict[str, Any]:
    """A conservative local fallback for API outages and uncertain incidents."""
    event = event_code if event_code in EVENT_CODES else "unknown_error"
    active_stage = stage if stage in STAGES else "unknown"
    code = (
        error_code.lower()
        if isinstance(error_code, str) and re.fullmatch(r"[a-z0-9_-]{1,80}", error_code.lower())
        else "unknown_error"
    )

    if event in {"duplicate_candidates_skipped", "no_new_candidates"}:
        decision = _recommendation("no_action", "queue_duplicate", "none", False)
    elif event == "source_blocked" or re.search(r"http_(202|403|429)|access_denied|challenge", code):
        decision = _recommendation(
            "skip_source_and_continue", "source_blocked", "continue_with_other_sources", False
        )
    elif event == "rubika_delivery_uncertain" or "uncertain" in code or "delivery_receipt_missing" in code:
        decision = _recommendation(
            "human_review", "uncertain_delivery", "review_publisher_delivery_receipt", True
        )
    elif re.search(r"timeout|temporar|network|connection|reset|rate_limit|http_5[0-9][0-9]", code):
        decision = _recommendation(
            "retry_next_scheduled_run", "temporary_network", "wait_for_next_schedule", False
        )
    else:
        reason = "auth_or_configuration" if any(term in code for term in ("auth", "token", "config")) else "unknown_error"
        if active_stage == "mega_storage":
            next_step = "preserve_mega_file_and_ledger"
        elif active_stage == "media_validation":
            next_step = "inspect_media_validation_report"
        elif active_stage in {"queue_intake", "queue_read"}:
            next_step = "inspect_d1_queue_counts"
        else:
            next_step = "review_error_code_and_recent_logs"
        decision = _recommendation("human_review", reason, next_step, True)

    return {
        "ok": True,
        "source": "local_fallback",
        "recommendation": decision,
        "applied": False,
        "daily_ai_limit": 4,
    }


def _valid_cloud_response(value: Any, *, event_code: str) -> bool:
    if not isinstance(value, dict) or value.get("ok") is not True or value.get("applied") is not False:
        return False
    if value.get("source") not in {"workers_ai", "deterministic_fallback"}:
        return False
    recommendation = value.get("recommendation")
    if not isinstance(recommendation, dict):
        return False
    if (
        recommendation.get("action") not in ACTIONS
        or recommendation.get("reason_code") not in REASONS
        or recommendation.get("next_step_code") not in NEXT_STEPS
        or type(recommendation.get("human_review")) is not bool
        or (recommendation.get("action") == "human_review") != recommendation.get("human_review")
    ):
        return False
    return event_code != "rubika_delivery_uncertain" or recommendation.get("action") == "human_review"


def recommend_error(
    *,
    event_code: str,
    stage: str,
    error_code: str,
    platform: str = "none",
    attempt_count: int = 0,
    counts: Mapping[str, int] | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Request a bounded remote recommendation and never raise to the main workflow."""
    safe_event = event_code if event_code in EVENT_CODES else "unknown_error"
    safe_stage = stage if stage in STAGES else "unknown"
    safe_code = (
        error_code.lower()
        if isinstance(error_code, str) and re.fullmatch(r"[a-z0-9_-]{1,80}", error_code.lower())
        else "unknown_error"
    )
    safe_platform = platform if platform in PLATFORMS else "none"
    safe_attempts = attempt_count if type(attempt_count) is int and 0 <= attempt_count <= 3 else 0
    safe_counts: dict[str, int] = {}
    if isinstance(counts, Mapping):
        for key, value in counts.items():
            if key in COUNT_KEYS and type(value) is int and 0 <= value <= 100000:
                safe_counts[key] = value

    fallback = local_recommendation(event_code=safe_event, stage=safe_stage, error_code=safe_code)
    # An LLM cannot resolve uncertainty about whether a post was sent.
    if safe_event == "rubika_delivery_uncertain":
        return fallback

    factory = client_factory or CloudState
    try:
        client = factory(
            os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev"),
            token=os.getenv("AGENT_API_TOKEN", "").strip(),
            timeout_seconds=12,
        )
        remote = client.supervise_error(
            event_code=safe_event,
            stage=safe_stage,
            error_code=safe_code,
            platform=safe_platform,
            attempt_count=safe_attempts,
            counts=safe_counts,
        )
        if _valid_cloud_response(remote, event_code=safe_event):
            return {
                "ok": True,
                "source": remote["source"],
                "recommendation": dict(remote["recommendation"]),
                "applied": False,
                "daily_ai_limit": 4,
            }
    except Exception:
        # Do not copy exceptions/provider bodies, headers or request inputs into logs.
        pass
    return fallback


def _write_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=path.name + ".", suffix=".tmp", delete=False
    ) as temporary:
        temp_path = Path(temporary.name)
        json.dump(dict(payload), temporary, ensure_ascii=False, sort_keys=True)
        temporary.write("\n")
    temp_path.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-code", default="unknown_error", choices=sorted(EVENT_CODES))
    parser.add_argument("--stage", default="unknown", choices=sorted(STAGES))
    parser.add_argument("--error-code", default="unknown_error")
    parser.add_argument("--platform", default="none", choices=sorted(PLATFORMS))
    parser.add_argument("--attempt-count", type=int, default=0)
    parser.add_argument("--counts-json", default="{}")
    parser.add_argument("--output-report", type=Path, default=None)
    args = parser.parse_args()

    counts: dict[str, int] = {}
    try:
        parsed = json.loads(args.counts_json)
        if isinstance(parsed, dict):
            counts = {
                key: value for key, value in parsed.items()
                if key in COUNT_KEYS and type(value) is int and 0 <= value <= 100000
            }
    except json.JSONDecodeError:
        pass

    result = recommend_error(
        event_code=args.event_code,
        stage=args.stage,
        error_code=args.error_code.lower(),
        platform=args.platform,
        attempt_count=args.attempt_count,
        counts=counts,
    )
    if args.output_report:
        _write_report(args.output_report, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    # Best-effort diagnostics must never replace the main workflow outcome.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
