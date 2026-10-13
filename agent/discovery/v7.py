"""Evidence-based candidate discovery ported from the user's saved v7 engine.

This module only searches and reports canonical candidates. It does not download,
moderate, or publish. A 200/202 response does not count as a result unless a valid
canonical media URL or 11-character YouTube ID is extracted.
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rubika-Agent discovery engine v7 — discovery only; does not download or publish.

Built from the user's saved discovery-engine architecture. It restores the earlier
YouTube embedded-data search and TikTok URL normalization, fixes the proven regression of discarding usable HTTP 202 response bodies, broadens search
queries, decodes common search-engine redirect wrappers, and records detailed source diagnostics.
"""
from __future__ import annotations
import html as html_lib
import base64
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, parse_qs, parse_qsl

try:
    import requests
except ImportError:
    print("خطا: requests نصب نیست. اجرا کن: python -m pip install requests", flush=True)
    raise SystemExit(2)

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "artifacts" / "discovery_engine_v7_report.json"
UA = ("Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
TIMEOUT = 8

# Keep these broad: the literal @*/video wildcard was overly restrictive for several engines.
TT_QUERIES = [
    'site:tiktok.com Poppy Playtime edit',
    'site:tiktok.com Huggy Wuggy edit',
    'site:tiktok.com CatNap Poppy Playtime',
    'site:tiktok.com Smiling Critters edit',
    'site:tiktok.com "Poppy Playtime" "video"',
    'Poppy Playtime edit TikTok',
    'Huggy Wuggy edit TikTok',
]
TT_KEYWORDS = ["Poppy Playtime edit", "Huggy Wuggy edit", "CatNap edit", "Smiling Critters edit"]
YT_QUERIES = [
    "Poppy Playtime edit shorts",
    "Huggy Wuggy edit shorts",
    "CatNap Poppy Playtime edit",
    "Smiling Critters edit",
    "Poppy Playtime fan edit",
]
ENGINES = [
    ("DuckDuckGo HTML", "https://html.duckduckgo.com/html/"),
    ("Bing HTML", "https://www.bing.com/search"),
    ("Google Web", "https://www.google.com/search"),
    ("Startpage", "https://www.startpage.com/sp/search"),
]

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.8", "Accept": "text/html,application/json,*/*"})
ATTEMPTS: list[dict] = []


def log(msg: str) -> None:
    print(f"[{datetime.now().astimezone().strftime('%H:%M:%S')}] {msg}", flush=True)


def record(platform: str, method: str, query: str, status: str, count: int = 0, detail: str = "") -> None:
    ATTEMPTS.append({
        "platform": platform, "method": method, "query": query,
        "status": status, "count": int(count), "detail": str(detail)[:600]
    })


def tiktok_url(username: str, video_id: str) -> str:
    return f"https://www.tiktok.com/@{username}/video/{video_id}"


def extract_tiktok_urls(text: str) -> list[dict]:
    """Extract canonical TikTok video URLs from plain, HTML-escaped, percent-encoded,
    and common search-engine redirect/base64 wrappers. Works on a 200 OR 202 body.
    """
    working = html_lib.unescape(text or "")
    working = (working.replace("\\u002F", "/").replace("\\u002f", "/")
               .replace("\\/", "/").replace("\\x2F", "/").replace("\\x2f", "/"))
    # Search providers often wrap the final URL in query parameters or encode it repeatedly.
    expanded = [working]
    seen_text = {working}
    for _ in range(4):
        decoded = unquote(working)
        if decoded == working:
            break
        working = decoded
        if working not in seen_text:
            expanded.append(working)
            seen_text.add(working)

    # Bing may encode its destination in a URL-safe base64 `u=a1...` parameter.
    b64_candidates = []
    for body in list(expanded):
        for match in re.finditer(r"(?i)(?:[?&]|&amp;)u=(a1[A-Za-z0-9_-]{12,})", body):
            encoded = match.group(1)[2:]
            try:
                encoded += "=" * ((4 - len(encoded) % 4) % 4)
                decoded_bytes = base64.urlsafe_b64decode(encoded.encode("ascii"))
                decoded_dest = decoded_bytes.decode("utf-8", errors="ignore")
                if "tiktok.com" in decoded_dest.lower():
                    b64_candidates.append(decoded_dest)
            except Exception:
                pass
    for candidate in b64_candidates:
        expanded.append(candidate)
        decoded = candidate
        for _ in range(3):
            next_decoded = unquote(decoded)
            if next_decoded == decoded:
                break
            decoded = next_decoded
            expanded.append(decoded)

    pattern = re.compile(
        r"(?:https?://)?(?:www\.|m\.)?tiktok\.com/@([A-Za-z0-9_.-]+)/video/(\d{10,25})",
        re.I,
    )
    results: dict[str, dict] = {}
    for body in expanded:
        for match in pattern.finditer(body):
            username, vid = match.group(1), match.group(2)
            results[vid] = {
                "platform": "tiktok", "id": vid,
                "url": tiktok_url(username, vid), "author": username,
                "title": "", "method": "HTML/redirect URL extraction",
            }
    return list(results.values())


def extract_youtube_ids(text: str) -> list[str]:
    """Restore the original discovery engine's embedded videoId extraction."""
    if not text:
        return []
    working = html_lib.unescape(text).replace("\\u0026", "&").replace("\\/", "/")
    patterns = [
        r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"',
        r'youtube\.com/watch\?v=([A-Za-z0-9_-]{11})',
        r'youtube\.com/shorts/([A-Za-z0-9_-]{11})',
        r'youtu\.be/([A-Za-z0-9_-]{11})',
    ]
    out, seen = [], set()
    for pattern in patterns:
        for m in re.finditer(pattern, working, re.I):
            vid = m.group(1)
            if vid not in seen:
                seen.add(vid)
                out.append(vid)
    return out


def known_ids() -> dict[str, set[str]]:
    """Exclude video IDs already present as local files and IDs listed in upload-state files."""
    ids = {"tiktok": set(), "youtube": set()}
    if not BASE.exists():
        return ids
    for p in BASE.rglob("*.mp4"):
        stem = p.stem
        m = re.search(r"(?:tiktok[_-])?(\d{10,25})", stem, re.I)
        if m and ("tiktok" in stem.lower() or "diagnostic_downloads/tiktok" in str(p).lower()):
            ids["tiktok"].add(m.group(1))
        m = re.search(r"youtube[_-]([A-Za-z0-9_-]{11})(?:$|[_ .-])", stem, re.I)
        if m:
            ids["youtube"].add(m.group(1))
        # Also detect a raw YouTube ID as the entire basename.
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", stem):
            ids["youtube"].add(stem)
    # Published-state files only; don't treat every discovery report as already published.
    for p in BASE.rglob("*.json"):
        n = p.name.lower()
        if not ("state" in n and ("upload" in n or "publish" in n or "extra5" in n)):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        def walk(obj, context=""):
            if isinstance(obj, dict):
                platform = str(obj.get("platform") or obj.get("source") or context).lower()
                for k, v in obj.items():
                    if isinstance(v, (dict, list)):
                        walk(v, platform or context)
                    elif isinstance(v, str):
                        for item in extract_tiktok_urls(v):
                            ids["tiktok"].add(item["id"])
                        for m in re.finditer(r"(?:youtube\.com/watch\?v=|youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})", v):
                            ids["youtube"].add(m.group(1))
                        if k.lower() in {"video_id", "source_id", "id"} and re.fullmatch(r"\d{10,25}", v) and "tik" in platform:
                            ids["tiktok"].add(v)
                        if k.lower() in {"video_id", "source_id", "id"} and re.fullmatch(r"[A-Za-z0-9_-]{11}", v) and "you" in platform:
                            ids["youtube"].add(v)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item, context)
        walk(data)
    return ids


def add_candidates(store: dict[str, dict[str, dict]], platform: str, items: list[dict], method: str, query: str, seen: set[str]) -> int:
    added = 0
    for item in items:
        vid = str(item.get("id") or "").strip()
        if not vid or vid in seen:
            continue
        url = str(item.get("url") or "").strip()
        if platform == "tiktok":
            if not re.fullmatch(r"\d{10,25}", vid) or not re.search(r"tiktok\.com/@[^/]+/video/\d+", url):
                continue
        else:
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                continue
            url = f"https://www.youtube.com/watch?v={vid}"
        store[platform][vid] = {
            "platform": platform, "id": vid, "url": url,
            "author": item.get("author") or "", "title": item.get("title") or "",
            "duration": item.get("duration"), "method": method, "query": query,
        }
        seen.add(vid)
        added += 1
    return added


def tiktok_from_tikwm(existing: dict[str, dict[str, dict]], seen: set[str]) -> None:
    endpoints = ["https://www.tikwm.com/api/feed/search", "https://tikwm.com/api/feed/search"]
    total_added = 0
    for keyword in TT_KEYWORDS:
        if len(existing["tiktok"]) >= 25:
            break
        result_items: list[dict] = []
        last_status = "NO_RESPONSE"
        detail = ""
        for endpoint in endpoints:
            try:
                r = session.get(endpoint, params={"keywords": keyword, "count": 20, "cursor": 0, "hd": 1}, timeout=TIMEOUT)
                last_status = f"HTTP_{r.status_code}"
                if r.status_code != 200:
                    detail = r.text[:180]
                    continue
                try:
                    payload = r.json()
                except ValueError:
                    last_status, detail = "INVALID_JSON", r.text[:180]
                    continue
                data = payload.get("data") or {}
                videos = data.get("videos") or []
                if not isinstance(videos, list):
                    videos = []
                for v in videos:
                    if not isinstance(v, dict):
                        continue
                    vid = str(v.get("video_id") or v.get("aweme_id") or v.get("id") or "")
                    author_obj = v.get("author") or {}
                    author = str(author_obj.get("unique_id") or author_obj.get("uniqueId") or v.get("author_name") or "")
                    title = str(v.get("title") or v.get("desc") or v.get("description") or "")
                    if vid and author:
                        result_items.append({"id": vid, "url": tiktok_url(author, vid), "author": author, "title": title})
                last_status = f"HTTP_200_API_{payload.get('code', 'NO_CODE')}"
                detail = str(payload.get("msg") or payload.get("message") or "")
                if videos:
                    break
            except Exception as e:
                last_status, detail = type(e).__name__, str(e)
        before = len(existing["tiktok"])
        count = add_candidates(existing, "tiktok", result_items, "TikWM JSON search", keyword, seen)
        added_total = len(existing["tiktok"]) - before
        total_added += added_total
        record("TikTok", "TikWM JSON search", keyword, last_status, len(result_items), f"added={added_total}; {detail}")
        log(f"TikWM [{keyword}]: candidates={len(result_items)}, new_unique={added_total}, status={last_status}")
    if not total_added:
        log("TikWM در این اجرا لینک تازه‌ای برنگرداند؛ موتورهای جست‌وجوی عمومی هم آزمایش می‌شوند.")


def tiktok_from_search_engines(existing: dict[str, dict[str, dict]], seen: set[str]) -> None:
    """Search using broad queries and parse usable 200/202 bodies.

    Historical checkpoint evidence says the previous collector found 13 genuine TikTok
    URLs and that a later regression discarded HTTP 202 bodies before parsing them.
    We therefore judge the body contents separately from the status code.
    """
    for engine, endpoint in ENGINES:
        for query in TT_QUERIES:
            if len(existing["tiktok"]) >= 25:
                return
            try:
                params = {"q": query, "num": 20}
                if "duckduckgo" in endpoint:
                    params.update({"kl": "us-en", "t": "h_"})
                r = session.get(endpoint, params=params, timeout=TIMEOUT, allow_redirects=True)
                status = r.status_code
                body = r.text or ""
                byte_count = len(r.content)
                tiktok_mentions = len(re.findall(r"tiktok\.com", body, re.I))

                # Do not try to bypass explicit access denials; record and move on.
                if status in (403, 429):
                    detail = f"bytes={byte_count}; tiktok_mentions={tiktok_mentions}; access_denied"
                    record("TikTok", engine, query, f"HTTP_{status}", 0, detail)
                    log(f"{engine} [{query}]: HTTP {status}, bytes={byte_count}; منبع دسترسی را رد کرد.")
                    continue
                if status not in (200, 202):
                    record("TikTok", engine, query, f"HTTP_{status}", 0,
                           f"bytes={byte_count}; tiktok_mentions={tiktok_mentions}")
                    log(f"{engine} [{query}]: HTTP {status}، رد شد؛ bytes={byte_count}")
                    continue

                # Critical fix: parse response body even when the provider reports HTTP 202.
                links = extract_tiktok_urls(body)
                before = len(existing["tiktok"])
                added = add_candidates(existing, "tiktok", links, engine, query, seen)
                status_label = f"HTTP_{status}_BODY_PARSED" if links else f"HTTP_{status}_NO_CANONICAL_LINKS"
                detail = f"bytes={byte_count}; tiktok_mentions={tiktok_mentions}; added={added}"
                record("TikTok", engine, query, status_label, len(links), detail)
                log(f"{engine} [{query}]: HTTP {status}, bytes={byte_count}, "
                    f"mentions={tiktok_mentions}, extracted={len(links)}, fresh={added}")
                if status == 202 and not links:
                    log(f"{engine}: پاسخ 202 بررسی شد اما لینک canonical نداشت؛ status به‌تنهایی موفقیت محسوب نشد.")
                if len(existing["tiktok"]) >= 10:
                    return
                time.sleep(0.25)
            except Exception as e:
                record("TikTok", engine, query, type(e).__name__, 0, "request_failed")
                log(f"{engine} [{query}]: {type(e).__name__}; روش بعدی.")


def discover_youtube_ytdlp(existing: dict[str, dict[str, dict]], seen: set[str]) -> None:
    exe = shutil.which("yt-dlp")
    prefix = [exe] if exe else ([sys.executable, "-m", "yt_dlp"] if importlib_util_find("yt_dlp") else [])
    if not prefix:
        record("YouTube", "yt-dlp search", "", "NOT_INSTALLED", 0, "yt-dlp CLI/module not found")
        return
    for query in YT_QUERIES:
        if len(existing["youtube"]) >= 50:
            break
        target = f"ytsearch20:{query}"
        cmd = prefix + ["--flat-playlist", "--dump-single-json", "--no-warnings", "--ignore-errors", "--playlist-end", "20", target]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=28, check=False)
            if proc.returncode != 0 and not proc.stdout.strip():
                detail = (proc.stderr or "yt-dlp failed")[-450:]
                record("YouTube", "yt-dlp search", query, f"EXIT_{proc.returncode}", 0, detail)
                log(f"yt-dlp search [{query}]: نتیجه نداد ({detail[-120:]})")
                continue
            payload = json.loads(proc.stdout)
            entries = payload.get("entries") or []
            items = []
            for v in entries:
                if not isinstance(v, dict):
                    continue
                vid = str(v.get("id") or "")
                title = str(v.get("title") or "")
                text = title.lower()
                if not any(x in text for x in ("poppy", "playtime", "huggy", "catnap", "dogday", "smiling critters", "prototype")):
                    continue
                duration = v.get("duration")
                try:
                    duration = float(duration) if duration is not None else None
                except (TypeError, ValueError):
                    duration = None
                # Shorts and edits only; allow videos <= 180 sec when duration is available.
                if duration is not None and duration > 180:
                    continue
                items.append({"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": title, "duration": duration})
            before = len(existing["youtube"])
            added = add_candidates(existing, "youtube", items, "yt-dlp ytsearch", query, seen)
            record("YouTube", "yt-dlp search", query, f"EXIT_{proc.returncode}_JSON_OK", len(items), f"added={added}; {proc.stderr[-180:] if proc.stderr else ''}")
            log(f"YouTube yt-dlp [{query}]: مرتبط و فیلترشده={len(items)}، تازه={added}")
        except subprocess.TimeoutExpired:
            record("YouTube", "yt-dlp search", query, "TIMEOUT", 0, "28 second timeout")
            log(f"YouTube yt-dlp [{query}]: timeout")
        except Exception as e:
            record("YouTube", "yt-dlp search", query, type(e).__name__, 0, "search_failed")
            log(f"YouTube yt-dlp [{query}]: {type(e).__name__}")


def importlib_util_find(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def discover_youtube_html(existing: dict[str, dict[str, dict]], seen: set[str]) -> None:
    for query in YT_QUERIES:
        if len(existing["youtube"]) >= 50:
            break
        try:
            r = session.get("https://www.youtube.com/results", params={"search_query": query}, timeout=TIMEOUT)
            if r.status_code != 200:
                record("YouTube", "YouTube results HTML", query, f"HTTP_{r.status_code}", 0, f"bytes={len(r.content)}")
                continue
            ids = extract_youtube_ids(r.text)
            items = [{"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "title": ""} for vid in ids]
            added = add_candidates(existing, "youtube", items, "YouTube embedded-data HTML", query, seen)
            record("YouTube", "YouTube results HTML", query, "HTTP_200", len(items), f"added={added}; bytes={len(r.content)}")
            log(f"YouTube HTML [{query}]: IDs={len(ids)}, تازه={added}")
        except Exception as e:
            record("YouTube", "YouTube results HTML", query, type(e).__name__, 0, "request_failed")
            log(f"YouTube HTML [{query}]: {type(e).__name__}")


def self_test() -> int:
    sample = r'<a href="https://www.tiktok.com/@test.handle/video/7616247520657771796?is_from_webapp=1">x</a>'
    assert extract_tiktok_urls(sample)[0]["id"] == "7616247520657771796"
    encoded = r'https%3A%2F%2Fwww.tiktok.com%2F%40test.handle%2Fvideo%2F7616247520657771796'
    assert extract_tiktok_urls(encoded)[0]["author"] == "test.handle"
    # Regression test: the caller must parse this body even when its HTTP status is 202.
    body_202 = r'<a href="https%3A%2F%2Fwww.tiktok.com%2F%40test.handle%2Fvideo%2F7616247520657771796">video</a>'
    assert extract_tiktok_urls(body_202)[0]["id"] == "7616247520657771796"
    # Bing-style u=a1 + URL-safe-base64 wrapper.
    destination = "https://www.tiktok.com/@test.handle/video/7616247520657771796"
    encoded = base64.urlsafe_b64encode(destination.encode()).decode().rstrip("=")
    assert extract_tiktok_urls(f"https://www.bing.com/ck/a?u=a1{encoded}")[0]["author"] == "test.handle"
    ytsample = r'{"videoId":"vkVFgmgLo1s"} {"videoId":"n38wy3o0DhY"}'
    assert extract_youtube_ids(ytsample) == ["vkVFgmgLo1s", "n38wy3o0DhY"]
    print("SELF-TEST OK: plain/encoded URLs, HTTP-202 body parsing, Bing base64 redirect, YouTube embedded IDs", flush=True)
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    log("Rubika-Agent discovery engine v7: فقط جست‌وجو؛ هیچ فایل دانلود یا منتشر نمی‌شود.")
    log("اصلاح مبتنی بر گزارش قبلی: بدنهٔ HTTP 202 هم parse می‌شود؛ queryها broadترند و redirectها decode می‌شوند. TikWM و yt-dlp حفظ شده‌اند.")
    ignored = known_ids()
    store: dict[str, dict[str, dict]] = {"tiktok": {}, "youtube": {}}
    seen = {"tiktok": set(ignored["tiktok"]), "youtube": set(ignored["youtube"])}
    log(f"شناسه‌های قبلاً موجود/منتشرشده برای حذف تکرار: TikTok={len(seen['tiktok'])}, YouTube={len(seen['youtube'])}")

    log("مرحلهٔ ۱/۲: کشف TikTok با API ساختاریافته و موتور قبلیِ اصلاح‌شده.")
    tiktok_from_tikwm(store, seen["tiktok"])
    if len(store["tiktok"]) < 10:
        tiktok_from_search_engines(store, seen["tiktok"])
    log(f"TikTok: {len(store['tiktok'])} کاندیدای تازه و غیرتکراری.")

    log("مرحلهٔ ۲/۲: کشف YouTube با yt-dlp search و parser اصلی YouTube.")
    discover_youtube_ytdlp(store, seen["youtube"])
    if len(store["youtube"]) < 15:
        discover_youtube_html(store, seen["youtube"])
    log(f"YouTube: {len(store['youtube'])} کاندیدای تازه و غیرتکراری.")

    candidates = {k: sorted(v.values(), key=lambda x: (x.get("title") or "").lower()) for k, v in store.items()}
    report = {
        "version": 7,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "discovery_only_no_download_no_publish",
        "summary": {
            "tiktok_candidates": len(candidates["tiktok"]),
            "youtube_candidates": len(candidates["youtube"]),
            "tiktok_target": 5,
            "youtube_target": 5,
            "ready_for_next_stage": len(candidates["tiktok"]) >= 5 and len(candidates["youtube"]) >= 5,
        },
        "excluded_existing_ids": {k: len(v) for k, v in ignored.items()},
        "candidates": candidates,
        "attempts": ATTEMPTS,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"گزارش کامل: {OUT}")
    log(f"نتیجه: TikTok={len(candidates['tiktok'])}/5، YouTube={len(candidates['youtube'])}/5")
    if not candidates["tiktok"]:
        log("تیک‌تاک نتیجه نداده؛ گزارش دقیق نشان می‌دهد API/موتور هرکدام چه status/error برگردانده‌اند.")
    log("هیچ ویدئویی دانلود یا منتشر نشد؛ این مرحله فقط برای تثبیت موتور سرچ است.")
    return 0 if candidates["tiktok"] and candidates["youtube"] else 1


if __name__ == "__main__":
    sys.exit(main())