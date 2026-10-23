"""Select and enqueue a small, diverse batch of new TikTok edit metadata.

The collector may discover many candidates; intake adds at most four *new* queue
items per scheduled run, rotating across creator profiles and skipping known IDs.
Media bytes are never handled here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from agent.brain.cloud_state import CloudState, CloudStateError
from agent.brain.owner_notify import queue_inventory_lines, send_owner_notification
# Official Mob news lifecycle cutoffs are not applied to the TikTok edit queue.
from agent.source_policy import resolve_source_policy


# YouTube is paused for later; Instagram is no longer part of the normal edit queue.
# Official Mob news remains in its own separate lane.
PLATFORMS = ("tiktok",)
QUEUE_STATUSES = ("queued", "processing", "sent", "failed", "uncertain", "skipped")
MAX_ENQUEUE_ITEMS = 500
DEFAULT_NEW_ITEMS_PER_RUN = 4
MAX_NEW_ITEMS_PER_RUN = 4
MAX_QUEUE_PAGES_PER_STATUS = 100
MAX_CANDIDATE_AGE_DAYS = 90
FUTURE_TIMESTAMP_TOLERANCE = timedelta(minutes=5)

POPPY_RELEVANCE_TERMS = (
    "poppy playtime", "poppyplaytime", "poppyplytime",
    "huggy wuggy", "huggywuggy", "catnap", "cat nap",
    "kissy missy", "mommy long legs", "mommylonglegs", "dogday",
    "smiling critters", "smilingcritters", "nightmare critters",
    "bobby bearhug", "craftycorn", "kickin chicken", "hoppy hopscotch",
    "picky piggy", "bubba bubbaphant", "prototype1006",
    "lily lovebraids", "lilylovebraids",
)
EDIT_MARKER_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:edit(?:s|ed|ing)?|aep|amv|fan[\s_-]?edit|"
    r"capcut|velocity|transitions?|slowed|reverbed|vfx)(?:$|[^a-z0-9])"
)
EDIT_HASHTAG_SUFFIXES = ("edit", "edits", "aep", "amv")

TIKTOK_CANONICAL_RE = re.compile(
    r"^https://(?:www\.)?tiktok\.com/@([A-Za-z0-9._-]{1,40})/video/([A-Za-z0-9._-]{1,120})/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_tiktok_identity(raw: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return creator handle and video ID from a canonical TikTok URL only."""
    url = raw.get("url") or raw.get("canonical_url")
    if not isinstance(url, str):
        return None
    match = TIKTOK_CANONICAL_RE.fullmatch(url.strip())
    if match is None:
        return None
    return match.group(1), match.group(2)


def _visible_metadata_text(raw: Mapping[str, Any]) -> str:
    """Return visible descriptive text without hashtags, queries, or creator handles."""
    parts = [raw.get("title"), raw.get("caption")]
    visible = " ".join(value for value in parts if isinstance(value, str))
    visible = re.sub(r"https?://\S+", " ", visible, flags=re.IGNORECASE)
    visible = re.sub(r"(?<!\w)#[^\s#]+", " ", visible)
    return re.sub(r"\s+", " ", visible).strip().casefold()


def _is_poppy_related(raw: Mapping[str, Any]) -> bool:
    """Hashtags alone do not prove the actual clip is about Poppy Playtime."""
    text = _visible_metadata_text(raw)
    return any(term in text for term in POPPY_RELEVANCE_TERMS)


def _owner_selected_poppy_signal(raw: Mapping[str, Any]) -> bool:
    """Allow explicit Poppy tags for owner-selected sources, without accepting edit-only posts."""
    parts = [raw.get("title"), raw.get("caption")]
    hashtags = raw.get("hashtags")
    if isinstance(hashtags, list):
        parts.extend(value for value in hashtags if isinstance(value, str))
    visible = " ".join(value for value in parts if isinstance(value, str))
    visible = re.sub(r"https?://\S+", " ", visible, flags=re.IGNORECASE)
    compact = re.sub(r"[^a-z0-9]+", "", visible.casefold())
    return any(
        re.sub(r"[^a-z0-9]+", "", term) in compact
        for term in POPPY_RELEVANCE_TERMS
    )


def _has_edit_signal(raw: Mapping[str, Any]) -> bool:
    """Known creators still need an edit marker; discovery provenance is not proof."""
    parts = [raw.get("title"), raw.get("caption")]
    hashtags = raw.get("hashtags")
    if isinstance(hashtags, list):
        clean_tags = [value.strip().lstrip("#").casefold() for value in hashtags if isinstance(value, str)]
        parts.extend(clean_tags)
        if any(tag.endswith(EDIT_HASHTAG_SUFFIXES) or tag in {
            "velocity", "transition", "transitions", "slowed", "reverbed", "capcut", "vfx"
        } for tag in clean_tags):
            return True
    text = " ".join(value for value in parts if isinstance(value, str)).casefold()
    return bool(EDIT_MARKER_RE.search(text))


def load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("discovery_report_unreadable") from None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("source") != "termux_phone"
        or payload.get("mode") != "metadata_only_no_download_no_publish"
        or not isinstance(payload.get("candidates"), dict)
    ):
        raise ValueError("invalid_termux_metadata_report")
    return payload


def candidates_for_queue(
    payload: dict[str, Any],
    *,
    max_items: int = MAX_ENQUEUE_ITEMS,
    now: datetime | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate fresh queue metadata and optionally summarize rejection reasons."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now_must_be_timezone_aware")
    current = current.astimezone(timezone.utc)
    oldest_allowed = current - timedelta(days=MAX_CANDIDATE_AGE_DAYS)
    future_allowed = current + FUTURE_TIMESTAMP_TOLERANCE

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def reject(reason: str, source: str) -> None:
        if diagnostics is None:
            return
        by_reason = diagnostics.setdefault("rejected_by_reason", {})
        by_source = diagnostics.setdefault("rejected_by_source", {})
        by_reason[reason] = int(by_reason.get(reason, 0)) + 1
        by_source[source] = int(by_source.get(source, 0)) + 1

    if diagnostics is not None:
        diagnostics.setdefault("input_count", 0)
        diagnostics.setdefault("rejected_by_reason", {})
        diagnostics.setdefault("rejected_by_source", {})
        diagnostics.setdefault("input_by_source", {})

    for platform in PLATFORMS:
        raw_rows = payload.get("candidates", {}).get(platform, [])
        if not isinstance(raw_rows, list):
            continue
        for raw in raw_rows:
            source_value = (
                raw.get("author") or raw.get("source_key") or raw.get("channel") or "unknown"
            ) if isinstance(raw, dict) else "unknown"
            source_name = str(source_value).strip()[:100] or "unknown"
            if diagnostics is not None:
                diagnostics["input_count"] += 1
                inputs = diagnostics["input_by_source"]
                inputs[source_name] = int(inputs.get(source_name, 0)) + 1

            if not isinstance(raw, dict):
                reject("invalid_candidate_shape", source_name)
                continue
            canonical_identity = _canonical_tiktok_identity(raw)
            url = raw.get("url") or raw.get("canonical_url")
            external_id = raw.get("id") or raw.get("external_id")
            if not isinstance(url, str) or canonical_identity is None:
                reject("canonical_url_invalid", source_name)
                continue
            if not isinstance(external_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", external_id):
                reject("external_id_invalid", source_name)
                continue
            if external_id != canonical_identity[1]:
                reject("canonical_url_id_mismatch", source_name)
                continue

            source_policy = resolve_source_policy(platform, canonical_identity[0])
            owner_selected = (
                source_policy.get("known_source") is True
                and source_policy.get("trust_tier") == "owner_selected"
            )
            topic_relevant = (
                _owner_selected_poppy_signal(raw) if owner_selected else _is_poppy_related(raw)
            )
            if not topic_relevant:
                reject("poppy_relevance_not_verified_in_visible_text", source_name)
                continue
            if not _has_edit_signal(raw):
                reject("edit_signal_missing", source_name)
                continue

            published_at = _parse_timestamp(raw.get("published_at"))
            if published_at is None:
                reject("published_timestamp_missing_or_invalid", source_name)
                continue
            if published_at < oldest_allowed:
                reject("candidate_stale", source_name)
                continue
            if published_at > future_allowed:
                reject("published_timestamp_in_future", source_name)
                continue

            identity = (platform, external_id)
            if identity in seen:
                reject("duplicate_within_report", source_name)
                continue
            seen.add(identity)
            source_key = canonical_identity[0][:200]
            rows.append({
                "platform": platform,
                "external_id": external_id,
                "canonical_url": url[:2048],
                "source_key": source_key,
                "title": str(raw.get("title") or "")[:500],
                "caption": str(raw.get("caption") or "")[:4000],
                "published_at": published_at.isoformat(),
                "media_type": "video",
                "author": canonical_identity[0][:100],
                "query": str(raw.get("query") or "")[:200],
                "freshness_source": str(raw.get("freshness_source") or "")[:100],
                "freshness_verified": raw.get("freshness_verified") is True,
                "method": str(raw.get("method") or "termux_phone")[:100],
            })
            if len(rows) >= max_items:
                break
        if len(rows) >= max_items:
            break

    if diagnostics is not None:
        diagnostics["eligible_count"] = len(rows)
        diagnostics["rejected_count"] = sum(diagnostics["rejected_by_reason"].values())
        diagnostics["duplicate_within_report_count"] = int(
            diagnostics["rejected_by_reason"].get("duplicate_within_report", 0)
        )
    return rows

def fair_round_robin(
    rows: list[dict[str, Any]],
    *,
    last_source_key: str | None = None,
) -> list[dict[str, Any]]:
    """Interleave creators, rotating the first creator after the previous batch."""
    groups: dict[str, list[dict[str, Any]]] = {}
    names: dict[str, str] = {}
    for row in rows:
        raw_key = str(row.get("source_key") or row.get("author") or "unknown").strip()
        key = raw_key.casefold() or "unknown"
        groups.setdefault(key, []).append(row)
        names.setdefault(key, raw_key)

    keys = list(groups)
    if len(keys) > 1 and last_source_key:
        previous = last_source_key.strip().casefold()
        if previous in keys:
            start = (keys.index(previous) + 1) % len(keys)
            keys = keys[start:] + keys[:start]

    ordered: list[dict[str, Any]] = []
    while any(groups.get(key) for key in keys):
        for key in keys:
            group = groups.get(key, [])
            if group:
                ordered.append(group.pop(0))
    return ordered


def _existing_queue_inventory(cloud: CloudState) -> tuple[set[str], str | None]:
    """Read all queue states fail-closed; return existing identities and latest creator."""
    identities: set[str] = set()
    latest_created_at = ""
    latest_source_key: str | None = None

    for status in QUEUE_STATUSES:
        offset = 0
        for _page in range(MAX_QUEUE_PAGES_PER_STATUS):
            page = cloud.get_automation_queue(status=status, limit=100, offset=offset)
            items = page.get("items")
            if not isinstance(items, list):
                raise CloudStateError("control_api_invalid_automation_queue")
            for item in items:
                if not isinstance(item, dict):
                    continue
                platform = item.get("platform")
                external_id = item.get("external_id")
                row_id = item.get("id")
                if isinstance(platform, str) and isinstance(external_id, str) and external_id:
                    identities.add(platform + ":" + external_id)
                elif isinstance(row_id, str) and re.fullmatch(r"(?:tiktok|youtube|instagram):[A-Za-z0-9._-]{1,120}", row_id):
                    identities.add(row_id)

                created_at = item.get("created_at")
                source_key = item.get("source_key")
                if isinstance(created_at, str) and created_at >= latest_created_at:
                    latest_created_at = created_at
                    latest_source_key = source_key if isinstance(source_key, str) else None

            returned = len(items)
            offset += returned
            if returned < 100:
                break
        else:
            # Never guess that a large queue was fully read.
            raise CloudStateError("control_api_automation_queue_page_limit")
    return identities, latest_source_key


def run_intake(
    report_path: Path,
    *,
    max_new_items: int = DEFAULT_NEW_ITEMS_PER_RUN,
) -> dict[str, Any]:
    if type(max_new_items) is not int or not 1 <= max_new_items <= MAX_NEW_ITEMS_PER_RUN:
        raise ValueError("max_new_items_must_be_between_1_and_4")

    base_url = os.getenv("AGENT_API_BASE_URL", "https://rubika-agent.h98609294.workers.dev")
    token = os.getenv("AGENT_API_TOKEN", "").strip()
    cloud = CloudState(base_url, token=token, timeout_seconds=25)
    payload = load_report(report_path)
    filter_diagnostics: dict[str, Any] = {}
    candidates = candidates_for_queue(payload, diagnostics=filter_diagnostics)
    existing_ids, last_source_key = _existing_queue_inventory(cloud)

    # The separate media ledger may know about sends from an older pipeline version.
    seen_index = cloud.get_seen_index()
    delivery_blocking_ids = seen_index.get("ids", set())
    if not isinstance(delivery_blocking_ids, set):
        raise CloudStateError("control_api_invalid_seen_index")
    existing_ids.update(delivery_blocking_ids)

    ordered = fair_round_robin(candidates, last_source_key=last_source_key)
    inserted_items: list[dict[str, Any]] = []
    duplicate_count = invalid_count = api_received_count = 0
    skipped_existing_count = 0

    # Send one identity at a time. If the API reports a duplicate/invalid row, keep
    # going to the next fresh candidate so the run can still fill its four-new-item cap.
    for item in ordered:
        identity = item["platform"] + ":" + item["external_id"]
        if identity in existing_ids:
            skipped_existing_count += 1
            continue

        result = cloud.enqueue_automation_items([item])
        api_received_count += int(result.get("received_count") or 0)
        inserted = int(result.get("inserted_count") or 0)
        duplicate_count += int(result.get("duplicate_count") or 0)
        invalid_count += int(result.get("invalid_count") or 0)

        if inserted == 1:
            existing_ids.add(identity)
            inserted_items.append({
                "platform": item["platform"],
                "external_id": item["external_id"],
                "url": item["canonical_url"],
                "source_key": item["source_key"],
                "title": item["title"],
                "published_at": item["published_at"],
                "queue_status": "queued",
            })
        elif inserted > 1:
            raise CloudStateError("control_api_insert_count_exceeded_single_item")
        else:
            # A race or API-level duplicate must not consume one of the four slots.
            existing_ids.add(identity)

        if len(inserted_items) >= max_new_items:
            break

    queue_counts = cloud.get_automation_queue_summary()
    inserted_sources = Counter(item["source_key"] for item in inserted_items)
    result: dict[str, Any] = {
        "ok": True,
        "status": "enqueued" if inserted_items else "no_new_candidates",
        "report_generated_at": str(payload.get("generated_at") or "")[:80],
        "discovered_candidate_count": filter_diagnostics.get("input_count", 0),
        "candidate_pool_count": len(candidates),
        "filter_eligible_count": filter_diagnostics.get("eligible_count", len(candidates)),
        "filter_rejected_count": filter_diagnostics.get("rejected_count", 0),
        "filter_rejected_by_reason": filter_diagnostics.get("rejected_by_reason", {}),
        "filter_rejected_by_source": filter_diagnostics.get("rejected_by_source", {}),
        "filter_input_by_source": filter_diagnostics.get("input_by_source", {}),
        "duplicate_within_report_count": filter_diagnostics.get("duplicate_within_report_count", 0),
        "ordered_candidate_count": len(ordered),
        "received_count": api_received_count,
        "inserted_count": len(inserted_items),
        "duplicate_count": duplicate_count,
        "invalid_count": invalid_count,
        "skipped_existing_count": skipped_existing_count,
        "max_new_items_per_run": max_new_items,
        "selection_policy": "fresh_unique_poppy_relevant_round_robin_across_creators",
        "selected_items": inserted_items,
        "selected_source_distribution": dict(inserted_sources),
        "queue_counts": queue_counts,
        "queue_inventory": queue_inventory_lines(queue_counts),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("artifacts/termux_discovery.json"))
    parser.add_argument("--max-new", type=int, default=DEFAULT_NEW_ITEMS_PER_RUN)
    parser.add_argument("--no-notify", action="store_true")
    args = parser.parse_args()
    try:
        result = run_intake(args.report, max_new_items=args.max_new)
    except Exception as exc:
        result = {
            "ok": False,
            "status": "failed",
            "error": re.sub(r"[^A-Za-z0-9_-]", "", type(exc).__name__) or "unknown_error",
        }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not args.no_notify:
        if result.get("ok"):
            message = (
                "📥 صف ویدئوهای روبیکا به‌روزرسانی شد.\n"
                f"موارد جدید: {result.get('inserted_count', 0)} از سقف {args.max_new}\n"
                f"تکراری‌های کنارگذاشته‌شده: {result.get('duplicate_count', 0) + result.get('skipped_existing_count', 0)}\n"
                f"موارد نامعتبر: {result.get('invalid_count', 0)}\n"
                + str(result.get("queue_inventory") or "")
            )
        else:
            message = "🚨 افزودن اطلاعات به صف ناموفق بود.\nکد خطا: " + str(result.get("error") or "unknown_error")
        sent = send_owner_notification(message)
        result["owner_notification_sent"] = bool(sent.get("sent"))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
