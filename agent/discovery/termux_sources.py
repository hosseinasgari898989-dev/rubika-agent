"""Metadata-only public discovery adapters intended to run on the owner's phone.

No login cookies, browser challenges, proxies, or media downloads are used here.
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from typing import Any

from agent.source_policy import known_tiktok_profiles, resolve_source_policy
from agent.brain.tiktok_urls import normalize_tiktok_url

import requests


TIKTOK_PROFILES = known_tiktok_profiles()
TIKTOK_URL_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9._-]{1,40})/video/(\d{10,25})",
    re.IGNORECASE,
)
HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_]{2,64})")


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
    timestamp: Any = None,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_id = str(video_id)
    clean_user = str(username).lstrip("@")
    stats = stats if isinstance(stats, dict) else {}
    source_policy = resolve_source_policy("tiktok", clean_user)
    return {
        "platform": "tiktok",
        "id": clean_id,
        "url": f"https://www.tiktok.com/@{clean_user}/video/{clean_id}",
        "title": str(title or "")[:1200],
        "caption": str(title or "")[:4000],
        "author": clean_user[:100],
        "hashtags": list(dict.fromkeys(HASHTAG_RE.findall(str(title or ""))))[:40],
        "source_policy": source_policy,
        "creator_trust_tier": source_policy.get("trust_tier", "unselected"),
        "published_at": _to_iso_timestamp(timestamp),
        "view_count": _safe_int(stats.get("playCount") or stats.get("view_count") or stats.get("viewCount")),
        "like_count": _safe_int(stats.get("diggCount") or stats.get("like_count") or stats.get("likeCount")),
        "comment_count": _safe_int(stats.get("commentCount") or stats.get("comment_count")),
        "share_count": _safe_int(stats.get("shareCount") or stats.get("share_count")),
        "media_type": "video",
        "method": "termux_ytdlp_selected_creator_profile",
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
    max_results: int = 48,
    timeout_seconds: int = 15,
    sleep_seconds: float = 0.35,
    max_per_profile: int = 8,
    ydl_factory: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read metadata from the six configured creator feeds only; no hashtag/search fallback."""
    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError:
            return [], [{"platform": "tiktok", "result": "yt_dlp_not_installed"}]
        ydl_factory = yt_dlp.YoutubeDL

    selected = {handle.casefold(): handle for handle in TIKTOK_PROFILES}
    options = {
        "quiet": True, "no_warnings": True, "ignoreerrors": True,
        "skip_download": True, "extract_flat": "in_playlist",
        "playlistend": max(10, min(100, max_results * 2)),
        "socket_timeout": max(5, min(30, int(timeout_seconds))),
        "extractor_retries": 0, "retries": 0, "logger": _QuietYDLLogger(),
    }
    candidates: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    for raw_profile in profiles:
        if len(candidates) >= max(1, max_results):
            break
        supplied_handle = str(raw_profile).strip().removeprefix("@")
        handle = selected.get(supplied_handle.casefold())
        if handle is None:
            attempts.append({"platform": "tiktok", "query": "@" + supplied_handle[:60], "result": "source_not_selected"})
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
        parsed = added = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            parsed += 1
            raw_id = str(entry.get("id") or "")
            page_url = str(entry.get("webpage_url") or entry.get("original_url") or "")
            identity = normalize_tiktok_url(page_url) if page_url else None
            if identity is not None:
                found_handle, video_id, canonical_url = identity
                if found_handle.casefold() != handle.casefold():
                    continue
                if raw_id and re.fullmatch(r"[0-9]{10,25}", raw_id) and raw_id != video_id:
                    continue
            else:
                video_id = raw_id
                if not re.fullmatch(r"[0-9]{10,25}", video_id):
                    continue
                canonical_url = f"https://www.tiktok.com/@{handle}/video/{video_id}"
            if video_id in candidates:
                continue
            title = str(entry.get("description") or entry.get("title") or "")
            timestamp = entry.get("timestamp") or entry.get("release_timestamp") or entry.get("upload_date")
            row = _tiktok_candidate(
                video_id, handle, title=title, timestamp=timestamp,
                stats={
                    "view_count": entry.get("view_count"),
                    "like_count": entry.get("like_count"),
                    "comment_count": entry.get("comment_count"),
                    "share_count": entry.get("share_count"),
                },
            )
            row["url"] = canonical_url
            try:
                row["duration_seconds"] = float(entry["duration"]) if entry.get("duration") is not None else None
            except (TypeError, ValueError, OverflowError):
                row["duration_seconds"] = None
            candidates[video_id] = row
            added += 1
            if added >= max(1, max_per_profile) or len(candidates) >= max(1, max_results):
                break
        attempts.append({
            "platform": "tiktok", "query": "@" + handle,
            "parsed": parsed, "new_unique": added,
            "result": "metadata_found" if added else "no_public_video_metadata",
        })
        if sleep_seconds > 0:
            time.sleep(min(2.0, sleep_seconds))
    return list(candidates.values())[:max_results], attempts

