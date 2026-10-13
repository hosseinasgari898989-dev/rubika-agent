"""Unified metadata discovery; never downloads or publishes media."""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import v7
from .instagram import InstagramDiscovery, discover_official_hashtag_urls, discover_public_indexed_urls
from .proxitok import ProxiTokDiscovery, discover_public_rss
from .tavily import discover_social_urls


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "artifacts" / "discovery_report.json"
TERMUX_REPORT_PATH = ROOT / "artifacts" / "termux_discovery.json"
REQUIRED_CORE_COUNTS = {"tiktok": 5, "youtube": 5}
TIKTOK_URL_RE = re.compile(r"^https://www\.tiktok\.com/@([A-Za-z0-9._-]{1,40})/video/(\d{10,25})/?$")
YOUTUBE_SHORT_URL_RE = re.compile(r"^https://(?:www\.)?youtube\.com/(?:shorts/|watch\?v=)([A-Za-z0-9_-]{11})/?$")
INSTAGRAM_URL_RE = re.compile(r"^https://(?:www\.)?instagram\.com/(reel|p|tv)/([A-Za-z0-9_-]{5,20})/?$")


def _unique_sorted(items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items.values(), key=lambda item: (
        str(item.get("title") or "").casefold(),
        str(item.get("id") or ""),
    ))


def _load_phone_report(path: str | Path) -> tuple[dict[str, list[dict[str, Any]]], str | None, str]:
    """Load and strictly validate the phone's metadata report, if present."""
    report_path = Path(path)
    if not report_path.exists():
        return {"tiktok": [], "youtube": [], "instagram": []}, None, "not_found"
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tiktok": [], "youtube": [], "instagram": []}, None, "unreadable"
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("source") != "termux_phone"
        or payload.get("mode") != "metadata_only_no_download_no_publish"
        or not isinstance(payload.get("candidates"), dict)
    ):
        return {"tiktok": [], "youtube": [], "instagram": []}, None, "invalid_report"

    accepted: dict[str, list[dict[str, Any]]] = {"tiktok": [], "youtube": [], "instagram": []}
    for platform in accepted:
        raw_rows = payload["candidates"].get(platform, [])
        if not isinstance(raw_rows, list):
            continue
        seen_ids: set[str] = set()
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            item_id = row.get("id")
            raw_url = row.get("url")
            if not isinstance(item_id, str) or not isinstance(raw_url, str):
                continue
            url = raw_url.strip()
            canonical_id = ""
            if platform == "tiktok":
                match = TIKTOK_URL_RE.fullmatch(url)
                if match:
                    canonical_id = match.group(2)
            elif platform == "youtube":
                match = YOUTUBE_SHORT_URL_RE.fullmatch(url)
                if match:
                    canonical_id = match.group(1)
            else:
                match = INSTAGRAM_URL_RE.fullmatch(url)
                if match and str(row.get("media_type") or "").lower() == "video":
                    canonical_id = match.group(2)
            if not canonical_id or canonical_id != item_id or item_id in seen_ids:
                continue
            safe = dict(row)
            safe["platform"] = platform
            safe["id"] = canonical_id
            if platform == "youtube":
                safe["url"] = f"https://www.youtube.com/shorts/{canonical_id}"
            elif platform == "tiktok":
                safe["url"] = f"https://www.tiktok.com/@{match.group(1)}/video/{canonical_id}"
            else:
                safe["url"] = f"https://www.instagram.com/{match.group(1).lower()}/{canonical_id}/"
            safe["title"] = str(safe.get("title") or "")[:1200]
            safe["caption"] = str(safe.get("caption") or "")[:4000]
            safe["author"] = str(safe.get("author") or "")[:100]
            safe["method"] = str(safe.get("method") or "termux_phone")[:80]
            seen_ids.add(item_id)
            accepted[platform].append(safe)
    return accepted, str(payload.get("generated_at") or "") or None, "loaded"


def run_discovery(
    *,
    report_path: str | Path = REPORT_PATH,
    phone_report_path: str | Path = TERMUX_REPORT_PATH,
) -> dict[str, Any]:
    """Merge verified phone metadata with controlled fallbacks and write diagnostic evidence."""
    v7.ATTEMPTS.clear()
    ignored = v7.known_ids()
    store: dict[str, dict[str, dict[str, Any]]] = {
        "tiktok": {},
        "youtube": {},
        "instagram": {},
    }
    seen = {
        "tiktok": set(ignored.get("tiktok", set())),
        "youtube": set(ignored.get("youtube", set())),
        "instagram": set(ignored.get("instagram", set())),
    }

    phone_candidates, phone_generated_at, phone_status = _load_phone_report(phone_report_path)
    phone_report_loaded = phone_status == "loaded"
    termux_only = os.getenv("TERMUX_ONLY", "").strip().lower() in {"1", "true", "yes"}
    phone_attempts: list[dict[str, Any]] = [{
        "platform": "multi",
        "method": "termux_phone_report",
        "result": phone_status,
        "generated_at": phone_generated_at,
    }]
    for platform, rows in phone_candidates.items():
        for row in rows:
            item_id = row["id"]
            if item_id in seen[platform]:
                continue
            store[platform][item_id] = row
            seen[platform].add(item_id)

    if phone_report_loaded:
        print("Discovery: validated phone report loaded; using phone metadata only.", flush=True)
    elif termux_only:
        print(f"Discovery: TERMUX_ONLY is enabled but the phone report is {phone_status}; no hosted search will run.", flush=True)
    else:
        print("Discovery: no valid phone report; using the legacy metadata-only fallbacks.", flush=True)

    if not phone_report_loaded and not termux_only and len(store["tiktok"]) < REQUIRED_CORE_COUNTS["tiktok"]:
        print("TikTok fallback: legacy endpoints are attempted only when no phone report is available.", flush=True)
        v7.tiktok_from_tikwm(store, seen["tiktok"])
        if len(store["tiktok"]) < REQUIRED_CORE_COUNTS["tiktok"]:
            v7.tiktok_from_search_engines(store, seen["tiktok"])

    if not phone_report_loaded and len(store["youtube"]) < REQUIRED_CORE_COUNTS["youtube"]:
        print("YouTube fallback: yt-dlp metadata search only; download=False.", flush=True)
        v7.discover_youtube_ytdlp(store, seen["youtube"])
        if len(store["youtube"]) < REQUIRED_CORE_COUNTS["youtube"]:
            v7.discover_youtube_html(store, seen["youtube"])

    # Do not repeatedly retry the known-unproductive public Instagram search-engine path.
    # Only use the optional official API when it is configured, unless explicitly opting in
    # to one legacy index diagnostic run for debugging.
    instagram_graph = InstagramDiscovery(candidates=(), attempts=())
    graph_configured = bool(
        os.getenv("INSTAGRAM_GRAPH_ACCESS_TOKEN", "").strip()
        and os.getenv("INSTAGRAM_GRAPH_USER_ID", "").strip()
    )
    if not phone_report_loaded and len(store["instagram"]) < 1 and graph_configured:
        print("Instagram fallback: configured official Graph API metadata search.", flush=True)
        instagram_graph = discover_official_hashtag_urls(max_results=2)
        for item in instagram_graph.candidates:
            if item.external_id in seen["instagram"]:
                continue
            store["instagram"][item.external_id] = {
                "platform": "instagram",
                "id": item.external_id,
                "url": item.canonical_url,
                "title": "",
                "caption": "",
                "post_type": item.post_type,
                "method": item.method,
                "query": item.query,
                "media_type": item.media_type,
            }
            seen["instagram"].add(item.external_id)
    elif not phone_report_loaded and len(store["instagram"]) < 1 and os.getenv("ALLOW_LEGACY_INSTAGRAM_INDEX", "").lower() == "true":
        print("Instagram index diagnostic explicitly enabled by ALLOW_LEGACY_INSTAGRAM_INDEX.", flush=True)
        instagram = discover_public_indexed_urls(max_results=5)
        for item in instagram.candidates:
            if item.external_id in seen["instagram"]:
                continue
            store["instagram"][item.external_id] = {
                "platform": "instagram",
                "id": item.external_id,
                "url": item.canonical_url,
                "title": "",
                "caption": "",
                "post_type": item.post_type,
                "method": item.method,
                "query": item.query,
                "media_type": item.media_type,
            }
            seen["instagram"].add(item.external_id)
    else:
        instagram = InstagramDiscovery(candidates=(), attempts=())

    proxitok = ProxiTokDiscovery(candidates=(), attempts=())
    if not phone_report_loaded and len(store["tiktok"]) < REQUIRED_CORE_COUNTS["tiktok"]:
        print("TikTok fallback: public ProxiTok RSS feeds; no login or challenge bypass.", flush=True)
        proxitok = discover_public_rss()
        for item in proxitok.candidates:
            if item.external_id in seen["tiktok"]:
                continue
            store["tiktok"][item.external_id] = {
                "platform": "tiktok",
                "id": item.external_id,
                "url": item.canonical_url,
                "title": item.title,
                "author": item.source_key,
                "method": item.discovery_method,
                "query": item.discovery_tag,
            }
            seen["tiktok"].add(item.external_id)

    # Search-engine API use remains opt-in. No external API receives requests without a key.
    tavily_reports: dict[str, Any] = {}
    if not phone_report_loaded and os.getenv("TAVILY_API_KEY", "").strip():
        for platform in ("tiktok", "instagram"):
            target = REQUIRED_CORE_COUNTS["tiktok"] if platform == "tiktok" else 2
            if len(store[platform]) >= target:
                continue
            result = discover_social_urls(platform, max_results=target, max_queries=2)
            tavily_reports[platform] = result.report()
            for item in result.candidates:
                if item.external_id in seen[platform]:
                    continue
                store[platform][item.external_id] = {
                    "platform": item.platform,
                    "id": item.external_id,
                    "url": item.canonical_url,
                    "title": item.title,
                    "caption": "",
                    "author": item.source_key,
                    "method": item.discovery_method,
                    "query": item.discovery_query,
                    "media_type": item.media_type,
                }
                seen[platform].add(item.external_id)

    candidates = {key: _unique_sorted(values) for key, values in store.items()}
    summary = {
        "tiktok_candidates": len(candidates["tiktok"]),
        "youtube_candidates": len(candidates["youtube"]),
        "instagram_candidates": len(candidates["instagram"]),
        "required_core_counts": REQUIRED_CORE_COUNTS,
        "core_search_target_met": all(
            len(candidates[platform]) >= count
            for platform, count in REQUIRED_CORE_COUNTS.items()
        ),
        "termux_report_loaded": phone_report_loaded,
        "termux_report_status": phone_status,
        "termux_report_generated_at": phone_generated_at,
        "termux_only": termux_only,
        "reason_phone_report_missing": "phone_report_missing_or_invalid" if termux_only and not phone_report_loaded else None,
        "ready_to_publish": False,
        "tavily_configured": bool(os.getenv("TAVILY_API_KEY", "").strip()),
        "instagram_graph_api_configured": graph_configured,
        "reason_not_publish_ready": (
            "metadata_only; downloading, duplicate-content inspection, reuse permission, "
            "media moderation, complete-batch checks and explicit publish authorization are separate gates"
        ),
    }
    instagram_attempts = (
        instagram.report()["attempts"] if "instagram" in locals()
        else [{"engine": "Instagram public index", "result": "skipped_known_unproductive_path"}]
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "metadata_only_no_download_no_publish",
        "summary": summary,
        "excluded_existing_ids": {key: len(value) for key, value in ignored.items()},
        "candidates": candidates,
        "attempts": (
            phone_attempts
            + v7.ATTEMPTS
            + instagram_attempts
            + instagram_graph.report()["attempts"]
            + proxitok.report()["attempts"]
            + [attempt for result in tavily_reports.values() for attempt in result["attempts"]]
        ),
    }
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    print(f"Discovery report written: {path}", flush=True)
    return report


def self_test() -> int:
    v7.self_test()
    from .instagram import extract_instagram_urls

    reel = extract_instagram_urls(
        '<a href="https://www.instagram.com/reel/AbCdEf_1234/?igsh=example">reel</a>'
    )
    assert reel and reel[0]["id"] == "AbCdEf_1234"
    post = extract_instagram_urls(
        "https%3A%2F%2Fwww.instagram.com%2Fp%2FAbCdEf_1234%2F"
    )
    assert post and post[0]["post_type"] == "p"
    assert extract_instagram_urls("instagram.com/explore/tags/poppyplaytime/") == []
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    report = run_discovery()
    # A green workflow must mean the required metadata targets really were found.
    return 0 if report.get("summary", {}).get("core_search_target_met") else 2


if __name__ == "__main__":
    raise SystemExit(main())
