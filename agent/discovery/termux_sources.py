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

from agent.source_policy import known_tiktok_profiles, resolve_source_policy
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

import requests


TIKTOK_TAGS = ("poppyplaytime", "huggywuggy", "catnap", "smilingcritters")
TIKTOK_PROFILES = known_tiktok_profiles()
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
YOUTUBE_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
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
    query_text = str(query).strip()
    query_tag = query_text.removeprefix("#")
    query_tags = [query_tag] if query_text.startswith("#") and re.fullmatch(r"[A-Za-z0-9_]{2,64}", query_tag) else []
    all_tags = list(dict.fromkeys([*(tags or []), *text_tags, *query_tags]))[:40]
    stats = stats if isinstance(stats, dict) else {}
    source_policy = resolve_source_policy("tiktok", clean_user)
    return {
        "platform": "tiktok",
        "id": clean_id,
        "url": f"https://www.tiktok.com/@{clean_user}/video/{clean_id}",
        "title": str(title or "")[:1200],
        "caption": str(title or "")[:4000],
        "author": clean_user[:100],
        "hashtags": all_tags,
        "source_policy": source_policy,
        "creator_trust_tier": source_policy.get("trust_tier", "unknown"),
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
    max_per_profile: int = 4,
    ydl_factory: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read recent public TikTok creator-feed metadata only, capped per creator."""
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
        source_policy = resolve_source_policy("tiktok", handle)
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
            # Owner-selected profiles are trusted for broad quality. Still check
            # Poppy Playtime and edit status from the actual video downstream.
            if source_policy.get("trust_tier") != "owner_selected" and not relevant.search(caption):
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
            row["source_policy"] = source_policy
            row["creator_trust_tier"] = source_policy.get("trust_tier", "unknown")
            if video_id not in candidates:
                candidates[video_id] = row
                added += 1
            if added >= max(1, min(int(max_per_profile), int(max_results))):
                break
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


def _discover_tiktok_public_hashtag_feeds(
    *,
    tags: tuple[str, ...],
    max_results: int,
    timeout_seconds: int = 12,
    sleep_seconds: float = 0.35,
    max_age_days: int = 90,
    ydl_factory: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Metadata-only fallback for public TikTok hashtag feeds through yt-dlp.

    This route does not download video bytes, sign in, bypass challenges or retry
    access-denied responses. Unsupported/blocked feeds are recorded and skipped.
    """
    if max_results <= 0:
        return [], []
    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError:
            return [], [{
                "platform": "tiktok", "method": "yt_dlp_public_hashtag_feeds",
                "result": "yt_dlp_not_installed",
            }]
        ydl_factory = yt_dlp.YoutubeDL

    options = {
        "quiet": True, "no_warnings": True, "ignoreerrors": True,
        "skip_download": True, "extract_flat": "in_playlist",
        "playlistend": max(10, min(40, int(max_results) * 3)),
        "socket_timeout": max(5, min(30, int(timeout_seconds))),
        "extractor_retries": 0, "retries": 0,
        "logger": _QuietYDLLogger(),
    }
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(max_age_days)))
    candidates: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []

    for raw_tag in tags:
        if len(candidates) >= max_results:
            break
        tag = str(raw_tag).strip().removeprefix("#").lower()
        if not re.fullmatch(r"[a-z0-9_]{2,64}", tag):
            attempts.append({
                "platform": "tiktok", "method": "yt_dlp_public_hashtag_feeds",
                "query": str(raw_tag)[:80], "result": "invalid_hashtag",
            })
            continue
        try:
            with ydl_factory(options) as ydl:
                info = ydl.extract_info(f"https://www.tiktok.com/tag/{tag}", download=False)
        except Exception as exc:
            attempts.append({
                "platform": "tiktok", "method": "yt_dlp_public_hashtag_feeds",
                "query": "#" + tag, "result": "public_hashtag_metadata_failed",
                "error_type": type(exc).__name__,
            })
            continue

        entries = info.get("entries") if isinstance(info, dict) else None
        if not isinstance(entries, list):
            entries = [info] if isinstance(info, dict) and info.get("id") else []
        parsed = added = stale_or_undated = invalid_rows = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            parsed += 1
            page_url = str(entry.get("webpage_url") or entry.get("original_url") or "")
            url_match = TIKTOK_URL_RE.search(page_url)
            video_id = str(entry.get("id") or (url_match.group(2) if url_match else ""))
            if not re.fullmatch(r"\d{10,25}", video_id):
                invalid_rows += 1
                continue

            raw_handle = (
                url_match.group(1) if url_match else
                entry.get("uploader_id") or entry.get("channel_id") or entry.get("unique_id")
            )
            handle = str(raw_handle or "").strip().lstrip("@")
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", handle):
                invalid_rows += 1
                continue

            timestamp = entry.get("timestamp") or entry.get("release_timestamp")
            published_at = _to_iso_timestamp(timestamp)
            if not published_at:
                upload_date = entry.get("upload_date")
                if isinstance(upload_date, str) and re.fullmatch(r"\d{8}", upload_date):
                    try:
                        published_at = datetime.strptime(upload_date, "%Y%m%d").replace(
                            tzinfo=timezone.utc
                        ).isoformat()
                    except ValueError:
                        published_at = None
            if not published_at:
                stale_or_undated += 1
                continue
            try:
                published_dt = datetime.fromisoformat(published_at)
                if published_dt.tzinfo is None:
                    published_dt = published_dt.replace(tzinfo=timezone.utc)
                if published_dt.astimezone(timezone.utc) < cutoff:
                    stale_or_undated += 1
                    continue
            except ValueError:
                stale_or_undated += 1
                continue

            title = str(entry.get("title") or "")
            description = str(entry.get("description") or "")
            caption = (title + ("\n" + description if description else "")).strip()[:4000]
            row = _tiktok_candidate(
                video_id, handle, title=caption, query="#" + tag,
                timestamp=timestamp, stats={
                    "playCount": entry.get("view_count"),
                    "diggCount": entry.get("like_count"),
                    "commentCount": entry.get("comment_count"),
                    "shareCount": entry.get("repost_count"),
                }, tags=[tag],
            )
            row["published_at"] = published_at
            row["url"] = f"https://www.tiktok.com/@{handle}/video/{video_id}"
            row["source_policy"] = resolve_source_policy("tiktok", handle)
            row["creator_trust_tier"] = row["source_policy"].get("trust_tier", "unknown")
            row["method"] = "termux_ytdlp_public_hashtag_feeds"
            row["discovery_origin"] = "public_hashtag_feed"
            if video_id not in candidates:
                candidates[video_id] = row
                added += 1
            if len(candidates) >= max_results:
                break

        attempts.append({
            "platform": "tiktok", "method": "yt_dlp_public_hashtag_feeds",
            "query": "#" + tag, "parsed": parsed,
            "new_unique": added,
            "stale_or_undated_skipped": stale_or_undated,
            "invalid_rows_skipped": invalid_rows,
            "result": "metadata_found" if added else "no_recent_public_hashtag_metadata",
        })
        time.sleep(max(0.0, sleep_seconds))
    return list(candidates.values())[:max_results], attempts


def discover_tiktok_hashtags(
    *,
    session: Any | None = None,
    tags: tuple[str, ...] = TIKTOK_TAGS,
    max_results: int = 25,
    timeout_seconds: int = 12,
    sleep_seconds: float = 0.35,
    profiles: tuple[str, ...] = TIKTOK_PROFILES,
    ydl_factory: Any | None = None,
    include_unknown_sources: bool = False,
    known_source_max_results: int = 24,
    unknown_source_max_results: int = 12,
    max_per_profile: int = 4,
    max_age_days: int = 90,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect known creator feeds and optionally discover unknown creators.

    Unknown creators are discovered only via ordinary public hashtag pages. This
    adapter never logs in or bypasses a challenge page.
    """
    if session is None:
        if not include_unknown_sources:
            return discover_tiktok_profiles(
                profiles=profiles, max_results=max_results, timeout_seconds=timeout_seconds,
                sleep_seconds=sleep_seconds, max_age_days=max_age_days,
                max_per_profile=max_per_profile, ydl_factory=ydl_factory,
            )
        known, known_attempts = discover_tiktok_profiles(
            profiles=profiles,
            max_results=max(1, min(int(known_source_max_results), int(max_results))),
            timeout_seconds=timeout_seconds,
            sleep_seconds=sleep_seconds,
            max_age_days=max_age_days,
            max_per_profile=max_per_profile,
            ydl_factory=ydl_factory,
        )
        remaining = min(
            max(0, int(unknown_source_max_results)),
            max(0, int(max_results) - len(known)),
        )
        if remaining <= 0:
            return known, [*known_attempts, {
                "platform": "tiktok", "method": "public_hashtag_pages",
                "result": "unknown_source_budget_exhausted",
            }]
        unknown_feed, unknown_feed_attempts = _discover_tiktok_public_hashtag_feeds(
            tags=tags,
            max_results=remaining,
            timeout_seconds=timeout_seconds,
            sleep_seconds=sleep_seconds,
            max_age_days=max_age_days,
            ydl_factory=ydl_factory,
        )
        unknown_ids = {row.get("id") for row in unknown_feed if isinstance(row, dict)}
        html_remaining = max(0, remaining - len(unknown_feed))
        if html_remaining:
            unknown_html, unknown_html_attempts = discover_tiktok_hashtags(
                session=requests.Session(),
                tags=tags,
                max_results=html_remaining,
                timeout_seconds=timeout_seconds,
                sleep_seconds=sleep_seconds,
                max_age_days=max_age_days,
            )
        else:
            unknown_html, unknown_html_attempts = [], []
        all_unknown = list(unknown_feed)
        all_unknown.extend(
            row for row in unknown_html
            if isinstance(row, dict) and row.get("id") not in unknown_ids
        )
        known_ids = {row.get("id") for row in known if isinstance(row, dict)}
        combined = list(known)
        combined.extend(
            row for row in all_unknown
            if isinstance(row, dict) and row.get("id") not in known_ids
        )
        return combined[:max(1, int(max_results))], [
            *known_attempts, *unknown_feed_attempts, *unknown_html_attempts,
        ]
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
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(max_age_days)))
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
        publication_date_skipped = 0
        for row in rows:
            if row["id"] in candidates:
                continue
            published_at = row.get("published_at")
            if not isinstance(published_at, str) or not published_at:
                publication_date_skipped += 1
                continue
            try:
                parsed_at = datetime.fromisoformat(published_at)
                if parsed_at.tzinfo is None:
                    parsed_at = parsed_at.replace(tzinfo=timezone.utc)
                if parsed_at.astimezone(timezone.utc) < cutoff:
                    publication_date_skipped += 1
                    continue
            except ValueError:
                publication_date_skipped += 1
                continue
            policy = resolve_source_policy("tiktok", str(row.get("author") or ""))
            row["source_policy"] = policy
            row["creator_trust_tier"] = policy.get("trust_tier", "unknown")
            row["discovery_origin"] = "public_hashtag_page"
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
            "publication_date_unverified_or_old_skipped": publication_date_skipped,
            "result": "canonical_links_found" if added else "no_recent_canonical_links",
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


def _youtube_channel_feed_dates(
    channel_id: str,
    session: Any,
) -> tuple[dict[str, str], str]:
    """Read publication timestamps from one public YouTube Atom feed."""
    if not YOUTUBE_CHANNEL_ID_RE.fullmatch(channel_id):
        return {}, "invalid_channel_id"
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        response = session.get(
            url,
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; MetadataDiscovery/1.0)",
                "Accept": "application/atom+xml, application/xml;q=0.9, */*;q=0.1",
            },
        )
    except requests.RequestException:
        return {}, "request_failed"

    status = int(getattr(response, "status_code", 0))
    if status != 200:
        return {}, f"http_{status}"
    try:
        root = ET.fromstring(getattr(response, "text", "") or "")
    except ET.ParseError:
        return {}, "invalid_xml"

    atom = "{http://www.w3.org/2005/Atom}"
    yt = "{http://www.youtube.com/xml/schemas/2015}"
    dates: dict[str, str] = {}
    for feed_entry in root.findall(f"{atom}entry"):
        video_id = str(feed_entry.findtext(f"{yt}videoId") or "")
        raw_date = str(feed_entry.findtext(f"{atom}published") or "").strip()
        if not YOUTUBE_ID_RE.fullmatch(video_id) or not raw_date:
            continue
        try:
            published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        dates[video_id] = published.astimezone(timezone.utc).isoformat()
    return dates, "dates_found" if dates else "no_dates_in_feed"


def _parse_public_video_date(raw_date: str) -> str | None:
    """Normalize an ISO date/time read from the public video page."""
    value = str(raw_date or "").strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _youtube_public_page_date(video_id: str, session: Any) -> tuple[str | None, str]:
    """Read public page date metadata only; never logs in or retries challenges."""
    if not YOUTUBE_ID_RE.fullmatch(video_id):
        return None, "invalid_video_id"
    try:
        response = session.get(
            f"https://www.youtube.com/watch?v={video_id}",
            timeout=8,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
    except requests.RequestException:
        return None, "request_failed"

    status = int(getattr(response, "status_code", 0))
    if status != 200:
        return None, f"http_{status}"

    body = html.unescape(str(getattr(response, "text", "") or ""))
    meta_tags = re.findall(r"<meta\b[^>]*>", body, flags=re.IGNORECASE)
    for preferred_key in ("datePublished", "uploadDate"):
        for tag in meta_tags:
            itemprop = re.search(r"""\bitemprop\s*=\s*["']([^"']+)["']""", tag, re.IGNORECASE)
            content = re.search(r"""\bcontent\s*=\s*["']([^"']+)["']""", tag, re.IGNORECASE)
            if itemprop and content and itemprop.group(1).lower() == preferred_key.lower():
                normalized = _parse_public_video_date(content.group(1))
                if normalized:
                    return normalized, f"public_video_page_{preferred_key}"

    video_marker = re.search(
        rf"""["']videoId["']\s*:\s*["']{re.escape(video_id)}["']""", body
    )
    if video_marker:
        region = body[video_marker.start():video_marker.start() + 30000]
        for key in ("publishDate", "datePublished", "uploadDate"):
            match = re.search(
                rf"""["']{key}["']\s*:\s*["']([^"']+)["']""",
                region, flags=re.IGNORECASE,
            )
            if match:
                normalized = _parse_public_video_date(match.group(1))
                if normalized:
                    return normalized, f"public_video_page_{key}"

    if re.search(
        r"sign in to confirm|confirm you.re not a bot|captcha|unusual traffic",
        body, flags=re.IGNORECASE,
    ):
        return None, "page_challenge_or_signin"
    return None, "date_missing"


def discover_youtube_metadata(
    *,
    queries: tuple[str, ...] = YOUTUBE_QUERIES,
    per_query: int = 20,
    max_results: int = 25,
    max_age_days: int = 90,
    ydl_factory: Any | None = None,
    feed_session: Any | None = None,
    page_session: Any | None = None,
    max_feed_channels: int = 10,
    max_page_checks: int = 10,
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
    active_feed_session = feed_session if feed_session is not None else requests.Session()
    active_page_session = page_session if page_session is not None else requests.Session()
    feed_limit = max(0, min(10, int(max_feed_channels)))
    page_limit = max(0, min(10, int(max_page_checks)))
    feed_cache: dict[str, dict[str, str]] = {}
    feed_results: list[str] = []
    feed_date_enrichments = 0
    page_cache: dict[str, tuple[str | None, str]] = {}
    page_results: list[str] = []
    page_date_extractions = 0
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
            freshness_source = "yt_dlp_search_metadata" if published_at else ""
            channel_id = str(entry.get("channel_id") or "")
            if not published_at and YOUTUBE_CHANNEL_ID_RE.fullmatch(channel_id):
                if channel_id not in feed_cache and len(feed_cache) < feed_limit:
                    feed_dates, feed_result = _youtube_channel_feed_dates(
                        channel_id, active_feed_session
                    )
                    feed_cache[channel_id] = feed_dates
                    feed_results.append(feed_result)
                feed_date = feed_cache.get(channel_id, {}).get(video_id)
                if feed_date:
                    published_at = feed_date
                    freshness_source = "public_channel_atom_feed"
            if not published_at and video_id not in page_cache and len(page_cache) < page_limit:
                page_date, page_result = _youtube_public_page_date(video_id, active_page_session)
                page_cache[video_id] = (page_date, page_result)
                page_results.append(page_result)
            if not published_at:
                page_date, page_result = page_cache.get(video_id, (None, "page_check_limit_reached"))
                if page_date:
                    published_at = page_date
                    freshness_source = page_result
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
                    freshness_source = ""
            if published_at and freshness_source == "public_channel_atom_feed":
                feed_date_enrichments += 1
            if published_at and freshness_source.startswith("public_video_page_"):
                page_date_extractions += 1
            if not published_at:
                # Flat search and public channel feeds can both omit the date.
                # Keep such candidates unverified, never publish-ready.
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
                "channel_id": channel_id or None,
                "hashtags": hashtags,
                "published_at": published_at,
                "freshness_verified": bool(published_at),
                "freshness_source": freshness_source or "unavailable",
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
    if feed_results:
        attempts.append({
            "platform": "youtube",
            "method": "public_channel_atom_feed",
            "channels_checked": len(feed_results),
            "candidate_dates_enriched": feed_date_enrichments,
            "status_counts": {
                status: feed_results.count(status) for status in sorted(set(feed_results))
            },
            "result": "metadata_enriched" if feed_date_enrichments else "no_candidate_dates_matched",
        })
    if page_results:
        attempts.append({
            "platform": "youtube",
            "method": "public_video_page_metadata",
            "pages_checked": len(page_results),
            "page_dates_extracted": page_date_extractions,
            "status_counts": {
                status: page_results.count(status) for status in sorted(set(page_results))
            },
            "result": "metadata_enriched" if page_date_extractions else "no_public_page_dates_extracted",
        })
    return list(candidates.values()), attempts