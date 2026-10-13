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
MEDIA_PATH_RE = re.compile(
    r"^/(reel|p|tv)/([A-Za-z0-9_-]{5,20})/?$",
    re.IGNORECASE,
)
CANONICAL_URL_RE = re.compile(
    r"(?:https?:\\?/\\?/)?(?:www\\.)?instagram\\.com\\/(reel|p|tv)\\/([A-Za-z0-9_-]{5,20})",
    re.IGNORECASE,
)


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.jsonld: list[str] = []
        self._in_jsonld = False
        self._jsonld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag.lower() == "meta":
            key = values.get("property") or values.get("name") or values.get("itemprop")
            if key and values.get("content"):
                self.meta.setdefault(key.lower(), values["content"])
        if tag.lower() == "script" and values.get("type", "").lower() == "application/ld+json":
            self._in_jsonld = True
            self._jsonld_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jsonld_parts))
            self._jsonld_parts = []
            self._in_jsonld = False


def _safe_int(value: Any) -> int | None:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_date(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
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


def _json_media_rows(body: str, page_meta: dict[str, str], requested_tag: str) -> list[dict[str, Any]]:
    """Extract structured public media data when Instagram includes it in the page."""
    candidates: dict[str, dict[str, Any]] = {}

    def consume_object(obj: dict[str, Any]) -> None:
        raw_url = obj.get("permalink") or obj.get("url") or obj.get("contentUrl")
        if not isinstance(raw_url, str):
            shortcode = obj.get("shortcode") or obj.get("code")
            if isinstance(shortcode, str) and re.fullmatch(r"[A-Za-z0-9_-]{5,20}", shortcode):
                typename = str(obj.get("__typename") or obj.get("media_type") or "").lower()
                kind = "reel" if "reel" in typename else ("p" if "media" in obj else "")
                if not kind:
                    return
                raw_url = f"https://www.instagram.com/{kind}/{shortcode}/"
            else:
                return

        canonical = _canonical_media_url(raw_url)
        if not canonical:
            return
        path_kind, shortcode, permalink = canonical
        typename = str(obj.get("__typename") or "").lower()
        media_type_value = obj.get("media_type")
        is_video = (
            path_kind == "reel"
            or "graphvideo" in typename
            or str(media_type_value).lower() in {"2", "video", "reel"}
            or obj.get("is_video") is True
            or obj.get("video_duration") is not None
        )
        # A /p/ URL is accepted only when the structured payload positively says video.
        if not is_video:
            return

        caption = obj.get("caption")
        if isinstance(caption, dict):
            caption = caption.get("text") or ""
        if not isinstance(caption, str):
            caption = ""
        if not caption:
            caption = page_meta.get("og:description") or ""
        owner = obj.get("owner") or obj.get("user") or {}
        if isinstance(owner, dict):
            username = owner.get("username") or owner.get("full_name") or ""
        else:
            username = ""
        timestamp = obj.get("taken_at_timestamp") or obj.get("timestamp") or obj.get("datePublished")
        stats = obj.get("edge_media_preview_like") or obj.get("likes") or {}
        if isinstance(stats, dict):
            likes = stats.get("count") or stats.get("likes_count")
        else:
            likes = stats
        comments_data = obj.get("edge_media_to_comment") or obj.get("comments") or {}
        if isinstance(comments_data, dict):
            comments = comments_data.get("count") or comments_data.get("comments_count")
        else:
            comments = comments_data

        tags = list(dict.fromkeys(re.findall(r"(?<!\\w)#([A-Za-z0-9_]{2,64})", caption)))[:40]
        tags = list(dict.fromkeys([requested_tag, *tags]))[:40]
        candidates.setdefault(shortcode, {
            "platform": "instagram",
            "id": shortcode,
            "url": permalink,
            "title": caption[:1200],
            "caption": caption[:4000],
            "author": str(username)[:100],
            "hashtags": tags,
            "published_at": _safe_date(str(timestamp)) if timestamp is not None else None,
            "like_count": _safe_int(likes),
            "comment_count": _safe_int(comments),
            "view_count": _safe_int(obj.get("video_view_count") or obj.get("videoViewCount")),
            "duration_seconds": None,
            "media_type": "video",
            "method": "termux_instagram_public_hashtag_page",
            "query": "#" + requested_tag,
        })

    # JSON-LD is easiest to read with a tolerant HTML parser; embedded script JSON is
    # additionally scanned using the shortcode/permalink fields above.
    parser = _MetaParser()
    try:
        parser.feed(html.unescape(body or ""))
    except Exception:
        pass
    for script in parser.jsonld:
        try:
            value = json.loads(script)
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in _walk_json(value):
            if isinstance(obj, dict):
                consume_object(obj)

    decoded = html.unescape(body or "")
    for _ in range(3):
        expanded = unquote(decoded)
        if expanded == decoded:
            break
        decoded = expanded
    for match in CANONICAL_URL_RE.finditer(decoded):
        kind, shortcode = match.group(1).lower(), match.group(2)
        url = f"https://www.instagram.com/{kind}/{shortcode}/"
        snippet = decoded[max(0, match.start() - 1200):match.end() + 3000]
        typename = re.search(r'(?i)(?:__typename|media_type)[\\\\\"\\s:=]+(?:GraphVideo|2|video)', snippet)
        is_video = kind == "reel" or bool(typename) or re.search(r'(?i)"is_video"\\s*:\\s*true', snippet)
        if not is_video:
            continue
        # Use JSON-LD or nearby structured data where possible, with a conservative
        # title fallback from page-level metadata if Instagram omitted per-item data.
        title = parser.meta.get("og:title") or parser.meta.get("twitter:title") or ""
        caption = parser.meta.get("og:description") or parser.meta.get("description") or ""
        if shortcode not in candidates:
            candidates[shortcode] = {
                "platform": "instagram",
                "id": shortcode,
                "url": url,
                "title": title[:1200],
                "caption": caption[:4000],
                "author": "",
                "hashtags": list(dict.fromkeys([requested_tag, *re.findall(r"(?<!\\w)#([A-Za-z0-9_]{2,64})", caption)]))[:40],
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
            # A single challenge/rate-limit response is a reason to stop this source for
            # today's run rather than hammer the site or attempt to bypass the challenge.
            if status in {401, 403, 429}:
                break
            continue

        body = getattr(response, "text", "") or ""
        candidates_in_page = _json_media_rows(body, {}, tag)
        added = 0
        for row in candidates_in_page:
            if row["id"] in candidates:
                continue
            candidates[row["id"]] = row
            added += 1
            if len(candidates) >= limit:
                break
        low = body.lower()
        if any(phrase in low for phrase in ("login to instagram", "log in to instagram", "challenge_required", "accounts/login")) and not candidates_in_page:
            result = "login_wall_or_challenge"
        else:
            result = "canonical_video_links_found" if added else "no_public_video_links"
        attempts.append({
            "platform": "instagram",
            "query": "#" + tag,
            "http_status": status,
            "parsed": len(candidates_in_page),
            "new_unique": added,
            "result": result,
        })
        time.sleep(max(0.0, sleep_seconds))
    return list(candidates.values()), attempts
