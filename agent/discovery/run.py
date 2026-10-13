"""Unified public-source candidate discovery; never downloads or publishes media."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import v7
from .instagram import discover_public_indexed_urls


ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "artifacts" / "discovery_report.json"
REQUIRED_CORE_COUNTS = {"tiktok": 5, "youtube": 5}


def _unique_sorted(items: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items.values(), key=lambda item: (
        str(item.get("title") or "").casefold(),
        str(item.get("id") or ""),
    ))


def run_discovery(*, report_path: str | Path = REPORT_PATH) -> dict[str, Any]:
    """Search TikTok, YouTube and indexed public Instagram links and write evidence."""
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

    print("Discovery 1/3: TikTok search endpoints and canonical URL validation.", flush=True)
    v7.tiktok_from_tikwm(store, seen["tiktok"])
    if len(store["tiktok"]) < 10:
        v7.tiktok_from_search_engines(store, seen["tiktok"])

    print("Discovery 2/3: YouTube yt-dlp search; metadata only, no media download.", flush=True)
    v7.discover_youtube_ytdlp(store, seen["youtube"])
    if len(store["youtube"]) < 15:
        v7.discover_youtube_html(store, seen["youtube"])

    print("Discovery 3/3: public indexed Instagram post/Reel URLs.", flush=True)
    instagram = discover_public_indexed_urls()
    for item in instagram.candidates:
        if item.external_id in seen["instagram"]:
            continue
        store["instagram"][item.external_id] = {
            "platform": "instagram",
            "id": item.external_id,
            "url": item.canonical_url,
            "title": "",
            "post_type": item.post_type,
            "method": item.method,
            "query": item.query,
            "media_type": item.media_type,
        }
        seen["instagram"].add(item.external_id)

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
        "ready_to_publish": False,
        "reason_not_publish_ready": "discovery_only; media validation and explicit content moderation are separate gates",
    }
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "public_discovery_only_no_download_no_publish",
        "summary": summary,
        "excluded_existing_ids": {key: len(value) for key, value in ignored.items()},
        "candidates": candidates,
        "attempts": v7.ATTEMPTS + [item for item in instagram.report()["attempts"]],
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
    print("INSTAGRAM SELF-TEST OK: canonical posts/Reels only; navigation excluded.", flush=True)
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
    # External sources may be empty/blocked. Preserve diagnostics and do not fake success.
    return 0 if report.get("summary") else 1


if __name__ == "__main__":
    raise SystemExit(main())
