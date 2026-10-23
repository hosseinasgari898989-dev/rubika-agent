#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Metadata-only YouTube discovery; TikTok is sourced exclusively from selected Termux profiles."""
from __future__ import annotations
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
try:
    import requests
except ImportError:
    requests = None

BASE = Path(__file__).resolve().parents[2]
OUT = BASE / "artifacts" / "discovery_engine_v7_report.json"
TIMEOUT = 8
YT_QUERIES = [
    "Poppy Playtime edit shorts", "Huggy Wuggy edit shorts",
    "CatNap Poppy Playtime edit", "Smiling Critters edit", "Poppy Playtime fan edit",
]
UA = "Mozilla/5.0 (compatible; Rubika-Agent/1.0 metadata discovery)"
ATTEMPTS: list[dict] = []


def record(platform: str, method: str, query: str, status: str, count: int = 0, detail: str = "") -> None:
    ATTEMPTS.append({"platform": platform, "method": method, "query": query,
                     "status": status, "count": int(count), "detail": str(detail)[:400]})


def extract_youtube_ids(text: str) -> list[str]:
    """Extract only canonical 11-character YouTube video identifiers from returned text."""
    if not text:
        return []
    working = text.replace("\\u0026", "&").replace("\\/", "/")
    patterns = [
        r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"',
        r'youtube\.com/watch\?v=([A-Za-z0-9_-]{11})',
        r'youtube\.com/shorts/([A-Za-z0-9_-]{11})',
        r'youtu\.be/([A-Za-z0-9_-]{11})',
    ]
    out: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, working, re.I):
            video_id = match.group(1)
            if video_id not in seen:
                seen.add(video_id)
                out.append(video_id)
    return out


def known_ids() -> dict[str, set[str]]:
    """Exclude YouTube IDs already present as local files or published-state records."""
    ids = {"youtube": set()}
    if not BASE.exists():
        return ids
    for path in BASE.rglob("*.mp4"):
        stem = path.stem
        match = re.search(r"youtube[_-]([A-Za-z0-9_-]{11})(?:$|[_ .-])", stem, re.I)
        if match:
            ids["youtube"].add(match.group(1))
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", stem):
            ids["youtube"].add(stem)
    for path in BASE.rglob("*.json"):
        name = path.name.lower()
        if not ("state" in name and ("upload" in name or "publish" in name or "extra5" in name)):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(child, (dict, list)):
                        walk(child)
                    elif isinstance(child, str) and key.lower() in {"video_id", "source_id", "id"}:
                        if re.fullmatch(r"[A-Za-z0-9_-]{11}", child):
                            ids["youtube"].add(child)
                    elif isinstance(child, str):
                        for video_id in extract_youtube_ids(child):
                            ids["youtube"].add(video_id)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(data)
    return ids


def add_candidates(existing: dict[str, dict[str, dict]], platform: str, items: list[dict],
                   method: str, query: str, seen: set[str]) -> int:
    """Only YouTube metadata is accepted here; TikTok candidates come from allowlisted Termux feeds."""
    if platform != "youtube":
        return 0
    added = 0
    for item in items:
        video_id = str(item.get("id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or video_id in seen:
            continue
        existing.setdefault("youtube", {})[video_id] = {
            "platform": "youtube", "id": video_id,
            "url": f"https://www.youtube.com/shorts/{video_id}",
            "author": str(item.get("author") or "")[:100],
            "title": str(item.get("title") or "")[:1200],
            "duration": item.get("duration"), "method": method, "query": query,
        }
        seen.add(video_id)
        added += 1
    return added


def discover_youtube_ytdlp(existing: dict[str, dict[str, dict]], seen: set[str]) -> None:
    executable = shutil.which("yt-dlp")
    prefix = [executable] if executable else ([sys.executable, "-m", "yt_dlp"] if importlib_util_find("yt_dlp") else [])
    if not prefix:
        record("YouTube", "yt-dlp search", "", "NOT_INSTALLED", 0, "yt-dlp executable/module not found")
        return
    for query in YT_QUERIES:
        if len(existing.setdefault("youtube", {})) >= 50:
            break
        command = prefix + ["--flat-playlist", "--dump-single-json", "--no-warnings", "--ignore-errors",
                            "--playlist-end", "20", f"ytsearch20:{query}"]
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=28, check=False)
            if proc.returncode != 0 and not proc.stdout.strip():
                record("YouTube", "yt-dlp search", query, f"EXIT_{proc.returncode}", 0, "extractor_failed")
                continue
            payload = json.loads(proc.stdout)
            entries = payload.get("entries") or []
            items = []
            for row in entries:
                if not isinstance(row, dict):
                    continue
                video_id = str(row.get("id") or "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                    continue
                title = str(row.get("title") or "")
                if not any(term in title.casefold() for term in (
                    "poppy", "playtime", "huggy", "catnap", "dogday", "smiling critters", "prototype"
                )):
                    continue
                try:
                    duration = float(row["duration"]) if row.get("duration") is not None else None
                except (TypeError, ValueError, OverflowError):
                    duration = None
                if duration is not None and (duration <= 0 or duration > 180):
                    continue
                author = str(row.get("channel_id") or row.get("uploader_id") or row.get("channel") or row.get("uploader") or "")
                items.append({"id": video_id, "title": title, "duration": duration, "author": author})
            added = add_candidates(existing, "youtube", items, "yt-dlp ytsearch", query, seen)
            record("YouTube", "yt-dlp search", query, f"EXIT_{proc.returncode}_JSON_OK", len(items), f"added={added}")
        except subprocess.TimeoutExpired:
            record("YouTube", "yt-dlp search", query, "TIMEOUT", 0, "28 second timeout")
        except Exception as exc:
            record("YouTube", "yt-dlp search", query, type(exc).__name__, 0, "search_failed")


def importlib_util_find(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def discover_youtube_html(existing: dict[str, dict[str, dict]], seen: set[str]) -> None:
    if requests is None:
        record("YouTube", "YouTube results HTML", "", "REQUESTS_NOT_INSTALLED", 0)
        return
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8"})
    for query in YT_QUERIES:
        if len(existing.setdefault("youtube", {})) >= 50:
            break
        try:
            response = session.get("https://www.youtube.com/results", params={"search_query": query}, timeout=TIMEOUT)
            if response.status_code != 200:
                record("YouTube", "YouTube results HTML", query, f"HTTP_{response.status_code}", 0, f"bytes={len(response.content)}")
                continue
            items = [{"id": video_id, "title": ""} for video_id in extract_youtube_ids(response.text)]
            added = add_candidates(existing, "youtube", items, "YouTube embedded-data HTML", query, seen)
            record("YouTube", "YouTube results HTML", query, "HTTP_200", len(items), f"added={added}; bytes={len(response.content)}")
            if len(existing["youtube"]) >= 15:
                break
        except Exception:
            record("YouTube", "YouTube results HTML", query, "REQUEST_FAILED", 0, "request_failed")


def self_test() -> int:
    sample = '{"videoId":"vkVFgmgLo1s"} {"videoId":"n38wy3o0DhY"}'
    assert extract_youtube_ids(sample) == ["vkVFgmgLo1s", "n38wy3o0DhY"]
    store = {"youtube": {}}
    seen: set[str] = set()
    assert add_candidates(store, "youtube", [{"id": "vkVFgmgLo1s"}, {"id": "vkVFgmgLo1s"}],
                          "self-test", "Poppy Playtime", seen) == 1
    assert add_candidates({"tiktok": {}}, "tiktok", [{"id": "7616247520657771796"}],
                          "self-test", "Poppy", set()) == 0
    print("SELF-TEST OK: YouTube IDs and deduplication; no broad TikTok discovery", flush=True)
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    print("Rubika-Agent discovery engine v7: YouTube metadata only; TikTok is selected-profile-only.", flush=True)
    ATTEMPTS.clear()
    ignored = known_ids()
    store: dict[str, dict[str, dict]] = {"youtube": {}}
    seen = {"youtube": set(ignored["youtube"])}
    discover_youtube_ytdlp(store, seen["youtube"])
    if len(store["youtube"]) < 15:
        discover_youtube_html(store, seen["youtube"])
    candidates = {"youtube": sorted(store["youtube"].values(), key=lambda item: str(item.get("title") or "").casefold())}
    report = {"version": 7, "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "discovery_only_no_download_no_publish",
        "summary": {"youtube_candidates": len(candidates["youtube"]), "youtube_target": 5,
                    "ready_for_next_stage": len(candidates["youtube"]) >= 5},
        "excluded_existing_ids": {"youtube": len(ignored["youtube"])},
        "candidates": candidates, "attempts": ATTEMPTS}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"YouTube={len(candidates['youtube'])}/5; no media downloaded or published.", flush=True)
    return 0 if candidates["youtube"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
