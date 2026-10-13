"""Budget-guarded, metadata-only Instagram Reel discovery through Apify.

This adapter never downloads media or publishes. It defaults to at most two Reels
for one hashtag, runs at most once per UTC day per local state file, and refuses
to start if the monthly usage endpoint cannot verify the remaining budget.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

APIFY_API = "https://api.apify.com/v2"
ACTOR_PATH = "acts/apify~instagram-hashtag-scraper/run-sync-get-dataset-items"
USAGE_PATH = "users/me/usage/monthly"
DEFAULT_BUDGET_LIMIT_USD = 3.50
DEFAULT_DAILY_RESULTS = 2
DEFAULT_HASHTAG = "poppyplaytime"
DEFAULT_STATE_PATH = Path.home() / ".local" / "state" / "rubika-agent" / "apify-instagram-state.json"
CANONICAL_MEDIA = re.compile(
    r"^/(reel|p|tv)/([A-Za-z0-9_-]{5,20})/?$",
    re.IGNORECASE,
)


def _now_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "rubika-agent-instagram-metadata/1.0",
    }


def _read_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and payload.get("schema_version") == 1 else None


def _write_state(path: Path, state: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temp_path = Path(stream.name)
        temp_path.replace(path)
        return True
    except OSError:
        try:
            temp_path.unlink(missing_ok=True)  # type: ignore[possibly-undefined]
        except (OSError, UnboundLocalError):
            pass
        return False


def _error_result(status: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "platform": "instagram",
        "status": status,
        "message": message,
        "candidates": [],
        "attempts": [{"provider": "apify", "result": status, **details}],
    }


def _canonical_candidate(row: Any, query: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    media_type = str(row.get("type") or row.get("mediaType") or "").strip().lower()
    if media_type and media_type not in {"video", "reel"}:
        return None
    # The actor's reels-only input is a second gate. Reject explicit non-video rows.
    if media_type not in {"video", "reel"} and not (
        row.get("videoDuration") is not None or row.get("videoViewCount") is not None
    ):
        return None

    raw_url = row.get("url") or row.get("permalink") or ""
    if not isinstance(raw_url, str):
        return None
    try:
        parsed = urlparse(raw_url.strip())
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"}:
        return None
    if parsed.username or parsed.password:
        return None
    match = CANONICAL_MEDIA.fullmatch(parsed.path)
    if not match:
        return None
    post_type, shortcode = match.group(1).lower(), match.group(2)
    canonical_url = f"https://www.instagram.com/{post_type}/{shortcode}/"

    raw_tags = row.get("hashtags")
    hashtags = [
        str(tag).strip().removeprefix("#")[:64]
        for tag in raw_tags[:40]
        if isinstance(tag, (str, int))
    ] if isinstance(raw_tags, list) else []
    caption = row.get("caption")
    caption = caption[:4000] if isinstance(caption, str) else ""
    timestamp = row.get("timestamp")
    author = row.get("ownerUsername") or row.get("username") or ""
    return {
        "platform": "instagram",
        "id": shortcode,
        "external_media_id": str(row.get("id") or shortcode)[:120],
        "url": canonical_url,
        "title": caption[:1200],
        "caption": caption,
        "author": str(author)[:100] if author is not None else "",
        "hashtags": hashtags,
        "published_at": str(timestamp)[:64] if timestamp else None,
        "likes_count": _safe_int(row.get("likesCount")),
        "comments_count": _safe_int(row.get("commentsCount")),
        "view_count": _safe_int(row.get("videoViewCount") or row.get("playsCount")),
        "duration_seconds": _safe_float(row.get("videoDuration")),
        "media_type": "video",
        "method": "apify_instagram_hashtag",
        "query": query,
    }


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return max(0, parsed)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
        return parsed if parsed >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def discover_instagram_reels(
    *,
    token: str | None = None,
    session: Any | None = None,
    state_path: str | Path | None = None,
    now: datetime | None = None,
    budget_limit_usd: float = DEFAULT_BUDGET_LIMIT_USD,
    max_results: int = DEFAULT_DAILY_RESULTS,
    hashtag: str = DEFAULT_HASHTAG,
) -> dict[str, Any]:
    """Fetch no more than two Reel metadata records, with spending and daily guards."""
    secret = (token if token is not None else os.getenv("APIFY_API_TOKEN", "")).strip()
    if not secret:
        return _error_result("not_configured", "APIFY_API_TOKEN is not configured.")
    if len(secret) > 4096 or any(char.isspace() for char in secret):
        return _error_result("invalid_configuration", "Apify token format is invalid.")
    clean_tag = str(hashtag or "").strip().removeprefix("#").lower()
    if not re.fullmatch(r"[a-z0-9_]{2,64}", clean_tag):
        return _error_result("invalid_hashtag", "Hashtag format is invalid.")
    try:
        budget = float(budget_limit_usd)
        if not 0 < budget <= 5.0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return _error_result("invalid_budget", "Budget threshold must be between $0 and $5.")
    result_limit = min(DEFAULT_DAILY_RESULTS, max(1, int(max_results)))
    current = _now_utc(now)
    utc_day = current.date().isoformat()
    state_file = Path(state_path).expanduser() if state_path else DEFAULT_STATE_PATH
    state = _read_state(state_file)
    if state is None:
        return _error_result(
            "state_unreadable",
            "Daily-limit state is unreadable; blocked to avoid an accidental extra run.",
        )
    if state.get("last_attempt_date_utc") == utc_day:
        return _error_result(
            "daily_limit_reached",
            "An Apify Instagram attempt was already made today (UTC).",
            date_utc=utc_day,
        )

    client = session or requests.Session()
    headers = _headers(secret)
    try:
        usage_response = client.get(
            f"{APIFY_API}/{USAGE_PATH}",
            headers=headers,
            timeout=(10, 25),
        )
    except requests.RequestException:
        return _error_result(
            "usage_check_failed",
            "Monthly usage could not be checked; no Actor run was started.",
        )
    if int(getattr(usage_response, "status_code", 0)) != 200:
        return _error_result(
            "usage_check_failed",
            "Monthly usage endpoint did not return HTTP 200; no Actor run was started.",
            http_status=int(getattr(usage_response, "status_code", 0)),
        )
    try:
        usage_payload = usage_response.json()
        data = usage_payload.get("data") if isinstance(usage_payload, dict) else None
        spent = float(data["totalUsageCreditsUsdAfterVolumeDiscount"])
        usage_cycle = data.get("usageCycle") if isinstance(data, dict) else {}
        cycle_start = str((usage_cycle or {}).get("startAt") or "")
        cycle_end = str((usage_cycle or {}).get("endAt") or "")
        if spent < 0:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        return _error_result(
            "usage_check_invalid",
            "Monthly usage response was incomplete; no Actor run was started.",
        )

    if spent >= budget:
        return _error_result(
            "budget_limit_reached",
            "Monthly Apify usage reached the configured safety ceiling.",
            usage_usd=round(spent, 4),
            budget_limit_usd=budget,
            usage_cycle_start=cycle_start,
            usage_cycle_end=cycle_end,
        )

    # Reserve this UTC day before starting the paid Actor. If the call times out,
    # a second invocation still will not launch another potentially charged run.
    state = {
        "schema_version": 1,
        "last_attempt_date_utc": utc_day,
        "last_attempt_status": "started",
        "usage_cycle_start": cycle_start,
        "usage_before_usd": round(spent, 4),
    }
    if not _write_state(state_file, state):
        return _error_result(
            "state_write_failed",
            "Could not persist the daily limit; the Actor was not started.",
        )

    actor_input = {
        "hashtags": [clean_tag],
        "keywordSearch": False,
        "resultsType": "reels",
        "resultsLimit": result_limit,
    }
    try:
        actor_response = client.post(
            f"{APIFY_API}/{ACTOR_PATH}?timeout=180&memory=1024",
            headers=headers,
            json=actor_input,
            timeout=(10, 200),
        )
    except requests.RequestException:
        state["last_attempt_status"] = "request_failed"
        _write_state(state_file, state)
        return _error_result(
            "actor_request_failed",
            "Apify Actor request failed; daily limit remains reserved for safety.",
            usage_usd=round(spent, 4),
            budget_limit_usd=budget,
            requested_results=result_limit,
        )
    actor_status = int(getattr(actor_response, "status_code", 0))
    if actor_status != 200:
        state["last_attempt_status"] = "actor_http_error"
        state["http_status"] = actor_status
        _write_state(state_file, state)
        return _error_result(
            f"actor_http_{actor_status}",
            "Apify Actor returned a non-success status; daily limit remains reserved.",
            usage_usd=round(spent, 4),
            budget_limit_usd=budget,
            requested_results=result_limit,
        )
    try:
        rows = actor_response.json()
    except (ValueError, TypeError):
        state["last_attempt_status"] = "invalid_actor_json"
        _write_state(state_file, state)
        return _error_result(
            "invalid_actor_json",
            "Actor output was not valid JSON; daily limit remains reserved.",
        )
    if not isinstance(rows, list):
        state["last_attempt_status"] = "invalid_actor_shape"
        _write_state(state_file, state)
        return _error_result(
            "invalid_actor_shape",
            "Actor output was not a JSON list; daily limit remains reserved.",
        )

    candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate = _canonical_candidate(row, "#" + clean_tag)
        if not candidate:
            continue
        candidates.setdefault(candidate["id"], candidate)
        if len(candidates) >= result_limit:
            break
    state["last_attempt_status"] = "completed"
    state["result_count"] = len(candidates)
    state["usage_cycle_end"] = cycle_end
    if not _write_state(state_file, state):
        # The Actor ran, so return its results but disclose the persistence problem.
        status = "state_update_failed"
    else:
        status = "ok" if candidates else "zero_results"

    return {
        "platform": "instagram",
        "status": status,
        "message": (
            "Metadata only; no media file was downloaded."
            if candidates else "Actor completed but no canonical video/Reel rows were accepted."
        ),
        "usage": {
            "before_usd": round(spent, 4),
            "safety_ceiling_usd": budget,
            "cycle_start": cycle_start,
            "cycle_end": cycle_end,
        },
        "attempts": [{
            "provider": "apify",
            "actor": "apify/instagram-hashtag-scraper",
            "hashtag": clean_tag,
            "requested_results": result_limit,
            "result_count": len(candidates),
            "result": status,
        }],
        "candidates": list(candidates.values()),
    }
