"""Best-effort Instagram public hashtag-page metadata parser for Termux.

Uses ordinary unauthenticated HTTPS requests once per configured tag. It never logs
in, uses cookies, bypasses challenges, downloads media, or calls a paid API. Instagram
may return a login wall/challenge or no canonical media links; that is reported honestly.
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urlparse

import requests


INSTAGRAM_TAGS = ("poppyplaytime", "huggywuggy", "catnap")
MAX_RESULTS = 2
MEDIA_PATH_RE = re.compile(r"^/(reel|p|tv)/([A-Za-z0-9_-]{5,20})/?$", re.IGNORECASE)
CANONICAL_URL_RE = re.compile(
    r"(?:https?://)?(?:www[.] )?instagram[.]com/(reel|p|tv)/([A-Za-z0-9_-]{5,20})".replace("www[.] ", "www[.]"),
    re.IGNORECASE,
)


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.json_scripts: list[tuple[str, str]] = []
        self._script_type = ""
        self._script_id = ""
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag.lower() == "meta":
            key = values.get("property") or values.get("name") or values.get("itemprop")
            if key and values.get("content"):
                self.meta.setdefault(key.lower(), values["content"])
        if tag.lower() == "script":
            self._script_type = values.get("type", "").lower()
            self._script_id = values.get("id", "")
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._script_type:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._script_type:
            self.json_scripts.append((self._script_type, self._script_id))
            self.json_scripts[-1] = (
                self._script_type + "|" + self._script_id,
                "".join(self._script_parts),
            )
            self._script_type = ""
            self._script_id = ""
            self._script_parts = []


def _safe_int(value: Any) -> int | None:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_date(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    try:
        numeric = float(raw)
        if numeric > 0:
            return datetime.fromtimestamp(numeric, tz=timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _canonical_media_url(raw: str) -> tuple[str, str, str] | None:
    try:
        parsed = urlparse(raw.strip())
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname not in {"instagram.com", "www.instagram.com"}:
        return None
    if parsed.username or parsed.password:
        return None
    match = MEDIA_PATH_RE.fullmatch(parsed.path)
    if not match:
        return None
    kind, code = match.group(1).lower(), match.group(2)
    return kind, code, f"https://www.instagram.com/{kind}/{code}/"


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _json_media_rows(body: str, requested_tag: str) -> list[dict[str, Any]]:
    """Extract public video metadata only when the HTML exposes canonical media data."""
    parser = _MetaParser()
    try:
        parser.feed(html.unescape(body or ""))
    except Exception:
        pass
    candidates: dict[str, dict[str, Any]] = {}

    def consume_object(obj: dict[str, Any]) -> None:
        raw_url = obj.get("permalink") or obj.get("url") or obj.get("contentUrl")
        if not isinstance(raw_url, str):
            shortcode = obj.get("shortcode") or obj.get("code")
            if isinstance(shortcode, str) and re.fullmatch(r"[A-Za-z0-9_-]{5,20}", shortcode):
                typename_value = obj.get("__typename") or obj.get("@type") or obj.get("media_type") or ""
                typename_text = str(typename_value).lower()
                kind = "reel" if "reel" in typename_text else ("p" if "video" in typename_text or "graphvideo" in typename_text else "")
                if not kind:
                    return
                raw_url = f"https://www.instagram.com/{kind}/{shortcode}/"
            else:
                return

        canonical = _canonical_media_url(raw_url)
        if not canonical:
            return
        path_kind, shortcode, permalink = canonical
        typename = str(obj.get("__typename") or obj.get("@type") or "").lower()
        media_type_value = obj.get("media_type")
        is_video = (
            path_kind == "reel"
            or "graphvideo" in typename
            or "videoobject" in typename
            or str(media_type_value).lower() in {"2", "video", "reel"}
            or obj.get("is_video") is True
            or obj.get("video_duration") is not None
            or obj.get("duration") is not None and "video" in typename
        )
        if not is_video:
            return

        caption = obj.get("caption") or obj.get("description") or obj.get("name") or ""
        if isinstance(caption, dict):
            caption = caption.get("text") or ""
        if not isinstance(caption, str):
            caption = ""
        if not caption:
            caption = parser.meta.get("og:description") or parser.meta.get("description") or ""

        owner = obj.get("owner") or obj.get("user") or obj.get("author") or {}
        if isinstance(owner, dict):
            username = owner.get("username") or owner.get("name") or owner.get("full_name") or ""
        else:
            username = owner if isinstance(owner, str) else ""

        timestamp = (
            obj.get("taken_at_timestamp") or obj.get("timestamp")
            or obj.get("datePublished") or obj.get("uploadDate")
        )
        stats = obj.get("edge_media_preview_like") or obj.get("likes") or {}
        likes = stats.get("count") or stats.get("likes_count") if isinstance(stats, dict) else stats
        comments_data = obj.get("edge_media_to_comment") or obj.get("comments") or {}
        comments = comments_data.get("count") or comments_data.get("comments_count") if isinstance(comments_data, dict) else comments_data
        tags = re.findall(r"(?:^|[^A-Za-z0-9_])#([A-Za-z0-9_]{2,64})", caption)
        tags = list(dict.fromkeys([requested_tag, *tags]))[:40]

        candidates.setdefault(shortcode, {
            "platform": "instagram",
            "id": shortcode,
            "url": permalink,
            "title": caption[:1200],
            "caption": caption[:4000],
            "author": str(username)[:100],
            "hashtags": tags,
            "published_at": _safe_date(timestamp),
            "like_count": _safe_int(likes),
            "comment_count": _safe_int(comments),
            "view_count": _safe_int(obj.get("video_view_count") or obj.get("videoViewCount")),
            "duration_seconds": None,
            "media_type": "video",
            "method": "termux_instagram_public_hashtag_page",
            "query": "#" + requested_tag,
        })

    for script_type_and_id, script_body in parser.json_scripts:
        script_type, script_id = script_type_and_id.split("|", 1)
        # Parse only known structured JSON script types; other HTML script bodies can
        # contain executable code and are not evaluated.
        if script_type == "application/ld+json" or script_id in {
            "__UNIVERSAL_DATA_FOR_REHYDRATION__", "SIGI_STATE", "additionalData"
        }:
            try:
                payload = json.loads(script_body)
            except (json.JSONDecodeError, TypeError):
                continue
            for obj in _walk_json(payload):
                if isinstance(obj, dict):
                    consume_object(obj)

    decoded = html.unescape(body or "").replace("\\/", "/")
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    for match in CANONICAL_URL_RE.finditer(decoded):
        kind, shortcode = match.group(1).lower(), match.group(2)
        url = f"https://www.instagram.com/{kind}/{shortcode}/"
        snippet = decoded[max(0, match.start() - 1200):match.end() + 3000]
        video_marker = re.search(
            r'(?i)(?:__typename|media_type)[" \t:=]+(?:GraphVideo|2|video)',
            snippet,
        )
        is_video = kind == "reel" or bool(video_marker) or re.search(
            r'(?i)"is_video" *: *true', snippet
        ) is not None
        if not is_video:
            continue
        title = parser.meta.get("og:title") or parser.meta.get("twitter:title") or ""
        caption = parser.meta.get("og:description") or parser.meta.get("description") or ""
        if shortcode not in candidates:
            tags = re.findall(r"(?:^|[^A-Za-z0-9_])#([A-Za-z0-9_]{2,64})", caption)
            candidates[shortcode] = {
                "platform": "instagram",
                "id": shortcode,
                "url": url,
                "title": title[:1200],
                "caption": caption[:4000],
                "author": "",
                "hashtags": list(dict.fromkeys([requested_tag, *tags]))[:40],
                "published_at": _safe_date(parser.meta.get("article:published_time")),
                "like_count": None,
                "comment_count": None,
                "view_count": None,
                "duration_seconds": None,
                "media_type": "video",
                "method": "termux_instagram_public_hashtag_page",
                "query": "#" + requested_tag,
            }
    return list(candidates.values())


def discover_instagram_hashtags(
    *,
    session: Any | None = None,
    tags: tuple[str, ...] = INSTAGRAM_TAGS,
    max_results: int = MAX_RESULTS,
    timeout_seconds: int = 12,
    sleep_seconds: float = 0.4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attempt a small number of public hashtag pages once each; do not retry challenges."""
    client = session or requests.Session()
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
    limit = max(1, min(int(max_results), 2))
    for raw_tag in tags:
        if len(candidates) >= limit:
            break
        tag = str(raw_tag or "").strip().removeprefix("#").lower()
        if not re.fullmatch(r"[a-z0-9_]{2,64}", tag):
            attempts.append({"platform": "instagram", "query": tag[:80], "result": "invalid_hashtag"})
            continue
        try:
            response = client.get(
                f"https://www.instagram.com/explore/tags/{tag}/",
                timeout=timeout_seconds,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            attempts.append({
                "platform": "instagram",
                "query": "#" + tag,
                "result": "request_failed",
                "error_type": type(exc).__name__,
            })
            continue

        status = int(getattr(response, "status_code", 0))
        if status != 200:
            attempts.append({
                "platform": "instagram",
                "query": "#" + tag,
                "http_status": status,
                "result": f"http_{status}",
            })
            if status in {401, 403, 429}:
                break
            continue

        body = getattr(response, "text", "") or ""
        rows = _json_media_rows(body, tag)
        added = 0
        for row in rows:
            if row["id"] in candidates:
                continue
            candidates[row["id"]] = row
            added += 1
            if len(candidates) >= limit:
                break
        low = body.lower()
        if any(phrase in low for phrase in ("login to instagram", "log in to instagram", "challenge_required", "accounts/login")) and not rows:
            outcome = "login_wall_or_challenge"
        else:
            outcome = "canonical_video_links_found" if added else "no_public_video_links"
        attempts.append({
            "platform": "instagram",
            "query": "#" + tag,
            "http_status": status,
            "parsed": len(rows),
            "new_unique": added,
            "result": outcome,
        })
        time.sleep(max(0.0, sleep_seconds))
    return list(candidates.values()), attempts
