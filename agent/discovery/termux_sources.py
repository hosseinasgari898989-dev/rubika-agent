"""Metadata-only public discovery adapters intended to run on the owner's phone.

No login cookies, browser challenges, proxies, or media downloads are used here.
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

import requests


TIKTOK_TAGS = ("poppyplaytime", "huggywuggy", "catnap", "smilingcritters")
TIKTOK_PROFILES = (
    "dragonboyedits",
    "s2gryder",
    "prime_noxem.edits",
    "poppy_playtime308",
    "fame.yt.editx",
    "imakeeditsthatarebad",
    "prepecl",
    "monster_9861",
    "executor_edit",
    "fanyedits8",
)
YOUTUBE_QUERIES = (
    "Poppy Playtime edit shorts",
    "Huggy Wuggy edit shorts",
    "CatNap Poppy Playtime edit short",
    "Smiling Critters edit shorts",
)
TIKTOK_URL_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9._-]{1,40})/video/(\d{10,25})",
    re.IGNORECASE,
)
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_]{2,64})")


def _expanded_html(body: str) -> list[str]:
    current = html.unescape(body or "")
    current = (
        current.replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\/", "/")
        .replace("\\x2F", "/")
        .replace("\\x2f", "/")
    )
    variants = [current]
    seen = {current}
    for _ in range(4):
        decoded = unquote(current)
        if decoded == current or decoded in seen:
            break
        variants.append(decoded)
        seen.add(decoded)
        current = decoded
    return variants


def _json_scripts(body: str) -> list[Any]:
    payloads: list[Any] = []
    for match in re.finditer(
        r"<script\b([^>]*)>(.*?)</script\s*>",
        html.unescape(body or ""),
        flags=re.IGNORECASE | re.DOTALL,
    ):
        attributes, script_body = match.group(1), match.group(2).strip()
        if not re.search(
            r"\bid\s*=\s*['\"](?:__UNIVERSAL_DATA_FOR_REHYDRATION__|SIGI_STATE)['\"]",
            attributes,
            flags=re.IGNORECASE,
        ):
            continue
        try:
            payloads.append(json.loads(script_body))
        except (json.JSONDecodeError, TypeError):
            continue
    return payloads


def _walk_objects(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)


def _to_iso_timestamp(value: Any) -> str | None:
    try:
        numeric = int(value)
        if numeric <= 0:
            return None
        return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _tiktok_candidate(
    video_id: str,
    username: str,
    *,
    title: str = "",
    query: str = "",
    timestamp: Any = None,
    stats: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    clean_id = str(video_id)
    clean_user = str(username).lstrip("@")
    text_tags = HASHTAG_RE.findall(title)
    all_tags = list(dict.fromkeys([*(tags or []), *text_tags]))[:40]
    stats = stats if isinstance(stats, dict) else {}
    return {
        "platform": "tiktok",
        "id": clean_id,
        "url": f"https://www.tiktok.com/@{clean_user}/video/{clean_id}",
        "title": str(title or "")[:1200],
        "caption": str(title or "")[:4000],
        "author": clean_user[:100],
        "hashtags": all_tags,
        "published_at": _to_iso_timestamp(timestamp),
        "view_count": _safe_int(stats.get("playCount") or stats.get("viewCount")),
        "like_count": _safe_int(stats.get("diggCount") or stats.get("likeCount")),
        "comment_count": _safe_int(stats.get("commentCount")),
        "share_count": _safe_int(stats.get("shareCount")),
        "media_type": "video",
        "method": "termux_public_hashtag_page",
        "query": query,
    }


def _safe_int(value: Any) -> int | None:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return None


class _QuietYDLLogger:
    """Suppress noisy extractor logs; outcomes are summarized in the JSON report."""
    def debug(self, message: str) -> None:
        return None

    def info(self, message: str) -> None:
        return None

    def warning(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        return None


def extract_tiktok_candidates(body: str, query: str = "") -> list[dict[str, Any]]:
    """Parse already-returned public HTML; it does not request individual video pages."""
    candidates: dict[str, dict[str, Any]] = {}
    for payload in _json_scripts(body):
        for item in _walk_objects(payload):
            video_id = item.get("id") or item.get("videoId") or item.get("video_id")
            if not isinstance(video_id, (str, int)):
                continue
            video_id = str(video_id)
            if not re.fullmatch(r"\d{10,25}", video_id):
                continue
            author = item.get("author") or item.get("authorInfo") or {}
            if not isinstance(author, dict):
                continue
            username = (
                author.get("uniqueId") or author.get("unique_id")
                or author.get("uniqueIdName") or author.get("name")
            )
            # Avoid treating arbitrary numeric IDs as videos.
            stats = item.get("stats") or item.get("statsV2") or {}
            desc = item.get("desc") or item.get("description") or ""
            if not isinstance(username, str) or not username:
                continue
            if not isinstance(stats, dict):
                stats = {}
            video = item.get("video")
            if not (isinstance(video, dict) or isinstance(desc, str) or stats):
                continue
            challenges = item.get("challenges") or item.get("textExtra") or []
            tags: list[str] = []
            if isinstance(challenges, list):
                for challenge in challenges:
                    if isinstance(challenge, dict):
                        tag = challenge.get("title") or challenge.get("hashtagName")
                        if isinstance(tag, str) and tag:
                            tags.append(tag.lstrip("#")[:64])
            row = _tiktok_candidate(
                video_id,
                username,
                title=desc if isinstance(desc, str) else "",
                query=query,
                timestamp=item.get("createTime") or item.get("create_time"),
                stats=stats,
                tags=tags,
            )
            candidates.setdefault(video_id, row)

    # Some pages expose canonical links in HTML without a reusable embedded JSON blob.
    # Keep those IDs/URLs as metadata-only candidates, but do not invent missing fields.
    for variant in _expanded_html(body):
        for match in TIKTOK_URL_RE.finditer(variant):
            username, video_id = match.group(1), match.group(2)
            candidates.setdefault(
                video_id,
                _tiktok_candidate(video_id, username, query=query),
            )
    return list(candidates.values())


def discover_tiktok_profiles(
    *,
    profiles: tuple[str, ...] = TIKTOK_PROFILES,
    max_results: int = 25,
    timeout_seconds: int = 15,
    sleep_seconds: float = 0.35,
    max_age_days: int = 90,
    ydl_factory: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read recent public TikTok creator-feed metadata only."""
    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError:
            return [], [{"platform": "tiktok", "result": "yt_dlp_not_installed"}]
        ydl_factory = yt_dlp.YoutubeDL

    options = {
        "quiet": True, "no_warnings": True, "ignoreerrors": True,
        "skip_download": True, "extract_flat": "in_playlist",
        "playlistend": max(10, min(50, max_results * 3)),
        "socket_timeout": max(5, min(30, int(timeout_seconds))),
        "extractor_retries": 0, "retries": 0,
        "logger": _QuietYDLLogger(),
    }
    candidates: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(max_age_days)))
    relevant = re.compile(
        r"poppy[\s_-]*playtime|poppyplaytime|huggy[\s_-]*wuggy|huggywuggy|"
        r"cat\s?nap|catnap|smiling[\s_-]*critters", re.IGNORECASE
    )
    for raw_profile in profiles:
        if len(candidates) >= max(1, max_results):
            break
        handle = str(raw_profile).strip().removeprefix("@")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", handle):
            attempts.append({"platform": "tiktok", "query": str(raw_profile)[:80], "result": "invalid_public_profile"})
            continue
        try:
            with ydl_factory(options) as ydl:
                info = ydl.extract_info(f"https://www.tiktok.com/@{handle}", download=False)
        except Exception as exc:
            attempts.append({"platform": "tiktok", "query": "@" + handle, "result": "profile_metadata_failed", "error_type": type(exc).__name__})
            continue
        entries = info.get("entries") if isinstance(info, dict) else None
        if not isinstance(entries, list):
            entries = [info] if isinstance(info, dict) and info.get("id") else []
        parsed = added = stale_or_undated = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            parsed += 1
            video_id = str(entry.get("id") or "")
            page_url = str(entry.get("webpage_url") or entry.get("original_url") or "")
            match = TIKTOK_URL_RE.search(page_url)
            author = match.group(1) if match else handle
            if match:
                video_id = match.group(2)
            if not re.fullmatch(r"\d{10,25}", video_id):
                continue
            title = str(entry.get("title") or "")
            description = str(entry.get("description") or "")
            caption = (title + ("\n" + description if description else "")).strip()[:4000]
            if not relevant.search(caption):
                continue
            timestamp = entry.get("timestamp") or entry.get("release_timestamp")
            published_at = _to_iso_timestamp(timestamp)
            if not published_at:
                stale_or_undated += 1
                continue
            try:
                if datetime.fromisoformat(published_at) < cutoff:
                    stale_or_undated += 1
                    continue
            except ValueError:
                stale_or_undated += 1
                continue
            stats = {
                "playCount": entry.get("view_count"), "diggCount": entry.get("like_count"),
                "commentCount": entry.get("comment_count"), "shareCount": entry.get("repost_count"),
            }
            row = _tiktok_candidate(
                video_id, author, title=caption, query="profile:@" + handle,
                timestamp=timestamp, stats=stats,
            )
            row["url"] = f"https://www.tiktok.com/@{author}/video/{video_id}"
            row["method"] = "termux_ytdlp_public_creator_profiles"
            row["duration_seconds"] = _safe_int(entry.get("duration"))
            if video_id not in candidates:
                candidates[video_id] = row
                added += 1
            if len(candidates) >= max(1, max_results):
                break
        attempts.append({
            "platform": "tiktok", "query": "@" + handle, "parsed": parsed,
            "new_unique": added,
            "stale_or_undated_skipped": stale_or_undated,
            "result": "metadata_found" if added else "no_recent_relevant_public_video_metadata",
        })
        time.sleep(max(0.0, sleep_seconds))
    return list(candidates.values()), attempts


def discover_tiktok_hashtags(
    *,
    session: Any | None = None,
    tags: tuple[str, ...] = TIKTOK_TAGS,
    max_results: int = 25,
    timeout_seconds: int = 12,
    sleep_seconds: float = 0.35,
    profiles: tuple[str, ...] = TIKTOK_PROFILES,
    ydl_factory: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use public creator feeds by default; injected sessions retain parser tests."""
    if session is None:
        return discover_tiktok_profiles(
            profiles=profiles, max_results=max_results, timeout_seconds=timeout_seconds,
            sleep_seconds=sleep_seconds, ydl_factory=ydl_factory,
        )
    client = session
    try:
        client.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        })
    except AttributeError:
        pass

    candidates: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    for tag in tags:
        if len(candidates) >= max(1, max_results):
            break
        clean_tag = str(tag).strip().removeprefix("#").lower()
        if not re.fullmatch(r"[a-z0-9_]{2,64}", clean_tag):
            attempts.append({"platform": "tiktok", "query": clean_tag[:80], "result": "invalid_hashtag"})
            continue
        try:
            response = client.get(
                f"https://www.tiktok.com/tag/{clean_tag}",
                timeout=timeout_seconds,
                allow_redirects=True,
            )
        except requests.RequestException:
            attempts.append({"platform": "tiktok", "query": "#" + clean_tag, "result": "request_failed"})
            continue

        status = int(getattr(response, "status_code", 0))
        if status != 200:
            label = f"http_{status}" if status in (403, 429) else f"unexpected_http_{status}"
            attempts.append({"platform": "tiktok", "query": "#" + clean_tag, "http_status": status, "result": label})
            continue
        rows = extract_tiktok_candidates(getattr(response, "text", "") or "", "#" + clean_tag)
        added = 0
        for row in rows:
            if row["id"] in candidates:
                continue
            candidates[row["id"]] = row
            added += 1
            if len(candidates) >= max(1, max_results):
                break
        attempts.append({
            "platform": "tiktok",
            "query": "#" + clean_tag,
            "http_status": status,
            "parsed": len(rows),
            "new_unique": added,
            "result": "canonical_links_found" if added else "no_canonical_links",
        })
        time.sleep(max(0.0, sleep_seconds))
    return list(candidates.values()), attempts


def _date_from_youtube_entry(entry: dict[str, Any]) -> str | None:
    timestamp = entry.get("release_timestamp") or entry.get("timestamp")
    if timestamp:
        try:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            pass
    upload_date = entry.get("upload_date")
    if isinstance(upload_date, str) and re.fullmatch(r"\d{8}", upload_date):
        try:
            return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return None
    return None


def discover_youtube_metadata(
    *,
    queries: tuple[str, ...] = YOUTUBE_QUERIES,
    per_query: int = 20,
    max_results: int = 25,
    max_age_days: int = 90,
    ydl_factory: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Search recent YouTube Shorts metadata; the download flag is never enabled."""
    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError:
            return [], [{
                "platform": "youtube",
                "result": "yt_dlp_not_installed",
                "mode": "metadata_only",
            }]
        ydl_factory = yt_dlp.YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "skip_download": True,
        "extract_flat": True,
        "logger": _QuietYDLLogger(),
        "socket_timeout": 15,
    }
    candidates: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(max_age_days)))
    for query in queries:
        if len(candidates) >= max_results:
            break
        try:
            with ydl_factory(options) as ydl:
                info = ydl.extract_info(f"ytsearch{max(1, min(per_query, 20))}:{query}", download=False)
        except Exception as exc:
            # Don't expose raw extractor output or the details of network errors.
            attempts.append({
                "platform": "youtube",
                "query": query,
                "result": "metadata_search_failed",
                "error_type": type(exc).__name__,
            })
            continue
        entries = info.get("entries") if isinstance(info, dict) else None
        added = stale_or_undated = freshness_unverified = 0
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            video_id = str(entry.get("id") or "")
            if not YOUTUBE_ID_RE.fullmatch(video_id):
                continue
            duration = _safe_int(entry.get("duration"))
            # Unknown duration is not enough to classify a candidate as a Short.
            if duration is None or duration > 180 or duration <= 0:
                continue
            published_at = _date_from_youtube_entry(entry)
            if published_at:
                try:
                    parsed_date = datetime.fromisoformat(published_at)
                    if parsed_date.tzinfo is None:
                        parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                    if parsed_date.astimezone(timezone.utc) < cutoff:
                        stale_or_undated += 1
                        continue
                except ValueError:
                    published_at = None
            if not published_at:
                # Flat search results often omit upload dates. Keep them as unverified
                # candidates, but never treat them as publish-ready.
                freshness_unverified += 1
            url = f"https://www.youtube.com/shorts/{video_id}"
            title = str(entry.get("title") or "")[:1200]
            description = str(entry.get("description") or "")[:4000]
            full_text = title + "\n" + description
            hashtags = list(dict.fromkeys(HASHTAG_RE.findall(full_text)))[:40]
            candidates.setdefault(video_id, {
                "platform": "youtube",
                "id": video_id,
                "url": url,
                "title": title,
                "caption": description,
                "author": str(entry.get("uploader") or entry.get("channel") or "")[:100],
                "hashtags": hashtags,
                "published_at": published_at,
                "freshness_verified": bool(published_at),
                "freshness_status": (
                    "verified_within_window" if published_at
                    else "date_unavailable_verify_before_publish"
                ),
                "duration_seconds": duration,
                "view_count": _safe_int(entry.get("view_count")),
                "like_count": _safe_int(entry.get("like_count")),
                "media_type": "video",
                "method": "termux_ytdlp_search_metadata",
                "query": query,
            })
            added += 1
            if len(candidates) >= max_results:
                break
        attempts.append({
            "platform": "youtube",
            "query": query,
            "parsed": len(entries) if isinstance(entries, list) else 0,
            "new_unique": added,
            "stale_or_undated_skipped": stale_or_undated,
            "freshness_unverified": freshness_unverified,
            "result": "metadata_found" if added else "no_recent_eligible_short_metadata",
        })
    return list(candidates.values()), attempts