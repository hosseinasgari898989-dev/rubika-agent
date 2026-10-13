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
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests


INSTAGRAM_TAGS = ("poppyplaytime", "huggywuggy", "catnap")
MAX_RESULTS = 5
SEARCH_TERMS = {
    "poppyplaytime": '("Poppy Playtime" OR poppyplaytime)',
    "huggywuggy": '("Huggy Wuggy" OR huggywuggy)',
    "catnap": '("CatNap" "Poppy Playtime" OR "Smiling Critters")',
}
MEDIA_PATH_RE = re.compile(r"^/(reel|p|tv)/([A-Za-z0-9_-]{5,20})/?$", re.IGNORECASE)
CANONICAL_URL_RE = re.compile(
    r"(?:https?://)?(?:www[.])?instagram[.]com/(reel|p|tv)/([A-Za-z0-9_-]{5,20})",
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


def discover_instagram_ytdlp_tags(
    *,
    tags: tuple[str, ...] = INSTAGRAM_TAGS,
    max_results: int = MAX_RESULTS,
    timeout_seconds: int = 15,
    sleep_seconds: float = 0.4,
    ydl_factory: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Best-effort public hashtag discovery with yt-dlp; never downloads media."""
    if ydl_factory is None:
        try:
            import yt_dlp
        except ImportError:
            return [], [{"platform": "instagram", "result": "yt_dlp_not_installed"}]
        ydl_factory = yt_dlp.YoutubeDL

    limit = max(1, min(int(max_results), 5))
    options = {
        "quiet": True, "no_warnings": True, "ignoreerrors": True,
        "skip_download": True, "extract_flat": "in_playlist", "playlistend": 30,
        "socket_timeout": max(5, min(30, int(timeout_seconds))),
        "extractor_retries": 0, "retries": 0,
    }
    candidates: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    for raw_tag in tags:
        if len(candidates) >= limit:
            break
        tag = str(raw_tag or "").strip().removeprefix("#").lower()
        if not re.fullmatch(r"[a-z0-9_]{2,64}", tag):
            attempts.append({"platform": "instagram", "query": tag[:80], "result": "invalid_hashtag"})
            continue
        try:
            with ydl_factory(options) as ydl:
                info = ydl.extract_info(f"https://www.instagram.com/explore/tags/{tag}/", download=False)
        except Exception as exc:
            attempts.append({
                "platform": "instagram", "query": "#" + tag,
                "result": "metadata_search_failed", "error_type": type(exc).__name__,
            })
            continue
        entries = info.get("entries") if isinstance(info, dict) else None
        if not isinstance(entries, list):
            entries = [info] if isinstance(info, dict) and info.get("id") else []
        parsed = added = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            parsed += 1
            raw_url = entry.get("webpage_url") or entry.get("original_url") or entry.get("url") or ""
            if not isinstance(raw_url, str):
                raw_url = ""
            canonical = _canonical_media_url(raw_url)
            if canonical:
                kind, shortcode, permalink = canonical
            else:
                shortcode = str(entry.get("id") or entry.get("shortcode") or "")
                if not re.fullmatch(r"[A-Za-z0-9_-]{5,20}", shortcode):
                    continue
                kind = ""
                permalink = ""
            media_kind = str(entry.get("media_type") or entry.get("type") or "").lower()
            is_video = (
                kind == "reel" or entry.get("is_video") is True
                or media_kind in {"2", "video", "reel"}
                or entry.get("duration") is not None
                or str(entry.get("ext") or "").lower() in {"mp4", "mov", "m4v"}
            )
            if not is_video:
                continue
            if not permalink:
                permalink = f"https://www.instagram.com/{'reel' if kind == 'reel' else 'p'}/{shortcode}/"
            caption = str(entry.get("description") or entry.get("title") or "")[:4000]
            tags_found = re.findall(r"(?:^|[^A-Za-z0-9_])#([A-Za-z0-9_]{2,64})", caption)
            row = {
                "platform": "instagram",
                "id": shortcode,
                "url": permalink,
                "title": str(entry.get("title") or caption)[:1200],
                "caption": caption,
                "author": str(entry.get("uploader") or entry.get("channel") or "")[:100],
                "hashtags": list(dict.fromkeys([tag, *tags_found]))[:40],
                "published_at": _safe_date(entry.get("timestamp") or entry.get("release_timestamp")),
                "like_count": _safe_int(entry.get("like_count")),
                "comment_count": _safe_int(entry.get("comment_count")),
                "view_count": _safe_int(entry.get("view_count")),
                "duration_seconds": _safe_int(entry.get("duration")),
                "media_type": "video",
                "method": "termux_ytdlp_public_hashtag_extractor",
                "query": "#" + tag,
            }
            candidates.setdefault(shortcode, row)
            added += 1
            if len(candidates) >= limit:
                break
        attempts.append({
            "platform": "instagram", "query": "#" + tag, "parsed": parsed,
            "new_unique": added,
            "result": "metadata_found" if added else "no_eligible_public_video_metadata",
        })
        time.sleep(max(0.0, sleep_seconds))
    return list(candidates.values()), attempts


class _SearchAnchorParser(HTMLParser):
    """Collect anchors from ordinary public search-result HTML."""
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, str]] = []
        self._active: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        href = values.get("href", "")
        classes = values.get("class", "")
        if not href or not (
            "result__a" in classes or "instagram.com" in href or "/l/?" in href
        ):
            return
        self._active = {"href": href, "text": []}

    def handle_data(self, data: str) -> None:
        if self._active is not None:
            self._active["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._active is not None:
            self.anchors.append({
                "href": str(self._active["href"]),
                "text": " ".join(self._active["text"]).strip(),
            })
            self._active = None


def _search_result_instagram_reel(raw_url: str) -> tuple[str, str] | None:
    """Accept only direct canonical Instagram Reel links from public search results."""
    value = html.unescape(str(raw_url or "")).strip()
    if not value:
        return None
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host in {"duckduckgo.com", "www.duckduckgo.com"}:
        query = parse_qs(parsed.query)
        target = query.get("uddg", [""])[0]
        if not target:
            return None
        value = target
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        host = (parsed.hostname or "").lower()
    if host not in {"instagram.com", "www.instagram.com"}:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    canonical = _canonical_media_url("https://www.instagram.com" + parsed.path)
    if not canonical:
        return None
    kind, shortcode, permalink = canonical
    if kind != "reel":
        return None
    return shortcode, permalink


def _plain_search_text(value: str) -> str:
    cleaned = re.sub(r"<[^>]*>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def _rss_items(body: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(body or "")
    except ET.ParseError:
        return []
    found: list[dict[str, str]] = []
    for item in root.iter():
        if str(item.tag).rsplit("}", 1)[-1].lower() != "item":
            continue
        fields: dict[str, str] = {}
        for child in list(item):
            name = str(child.tag).rsplit("}", 1)[-1].lower()
            fields[name] = "".join(child.itertext()).strip()
        if fields:
            found.append(fields)
    return found


def _search_item_published_at(value: str) -> str | None:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _search_page_published_at(body: str, permalink: str) -> str | None:
    """Extract a date only from the relevant public search-result card."""
    raw = html.unescape(body or "")
    canonical = _canonical_media_url(permalink)
    marker = canonical[1] if canonical else ""
    position = raw.find(marker) if marker else -1
    if position < 0:
        return None
    result_pattern = r"<(?:div|li)\\b[^>]*class=[\\\"'][^\\\"']*\\bresult(?:__body)?\\b[^\\\"']*[\\\"'][^>]*>"
    prior = list(re.finditer(result_pattern, raw[:position], flags=re.IGNORECASE))
    start = prior[-1].start() if prior else max(0, position - 700)
    next_result = re.search(result_pattern, raw[position + len(marker):], flags=re.IGNORECASE)
    end = position + len(marker) + next_result.start() if next_result else min(len(raw), position + 1400)
    card = raw[start:end]

    iso_match = re.search(r"\\b(20\\d{2})[-/](\\d{1,2})[-/](\\d{1,2})\\b", card)
    if iso_match:
        try:
            return datetime(
                int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)),
                tzinfo=timezone.utc,
            ).isoformat()
        except ValueError:
            pass

    month_names = (
        "January|February|March|April|May|June|July|August|September|October|November|December|"
        "Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
    )
    month_match = re.search(
        rf"\\b({month_names})\\s+(\\d{{1,2}}),?\\s+(20\\d{{2}})\\b",
        card, flags=re.IGNORECASE,
    )
    if month_match:
        raw_date = " ".join(month_match.groups())
        for fmt in ("%B %d %Y", "%b %d %Y"):
            try:
                return datetime.strptime(raw_date, fmt).replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass

    lowered = card.lower()
    now = datetime.now(timezone.utc)
    if re.search(r"\\btoday\\b", lowered):
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if re.search(r"\\byesterday\\b", lowered):
        return (now - timedelta(days=1)).isoformat()
    relative = re.search(
        r"\\b(\\d+)\\s+(minute|hour|day|week|month|year)s?\\s+ago\\b",
        lowered,
    )
    if relative:
        quantity, unit = int(relative.group(1)), relative.group(2)
        factors = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}
        return (now - timedelta(days=quantity * factors[unit])).isoformat()
    return None


def discover_instagram_public_search(
    *,
    tags: tuple[str, ...] = INSTAGRAM_TAGS,
    max_results: int = MAX_RESULTS,
    max_age_days: int = 90,
    timeout_seconds: int = 12,
    sleep_seconds: float = 0.3,
    session: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Find public Instagram Reels through ordinary web-search indexes, not Instagram scraping.

    Bing's RSS search view is tried first. DuckDuckGo's public HTML search is a
    fallback when a Bing query yields no canonical Reel. Search pages may be
    blocked or omit indexed Reels; failures are reported rather than fabricated.
    """
    client = session or requests.Session()
    try:
        client.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.8",
            "Accept": "application/rss+xml,application/xml,text/html;q=0.9,*/*;q=0.8",
        })
    except AttributeError:
        pass

    limit = max(1, min(int(max_results), 5))
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(max_age_days)))
    candidates: dict[str, dict[str, Any]] = {}
    attempts: list[dict[str, Any]] = []
    stale_or_undated = [0]
    ddg_blocked = False

    def add_candidate(shortcode: str, permalink: str, title: str, snippet: str,
                      tag: str, method: str, published_at: str | None) -> bool:
        if shortcode in candidates:
            return False
        if not published_at:
            stale_or_undated[0] += 1
            return False
        try:
            date_value = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if date_value.tzinfo is None:
                date_value = date_value.replace(tzinfo=timezone.utc)
            if date_value.astimezone(timezone.utc) < cutoff:
                stale_or_undated[0] += 1
                return False
        except (TypeError, ValueError, OverflowError):
            stale_or_undated[0] += 1
            return False
        caption = _plain_search_text(snippet)[:4000]
        clean_title = _plain_search_text(title)[:1200] or caption[:1200]
        hashtags = re.findall(r"(?:^|[^A-Za-z0-9_])#([A-Za-z0-9_]{2,64})", caption)
        candidates[shortcode] = {
            "platform": "instagram",
            "id": shortcode,
            "url": permalink,
            "title": clean_title,
            "caption": caption,
            "author": "",
            "hashtags": list(dict.fromkeys([tag, *hashtags]))[:40],
            "published_at": published_at,
            "like_count": None,
            "comment_count": None,
            "view_count": None,
            "duration_seconds": None,
            "media_type": "video",
            "method": method,
            "query": "#" + tag,
        }
        return True

    for raw_tag in tags:
        if len(candidates) >= limit:
            break
        tag = str(raw_tag or "").strip().removeprefix("#").lower()
        if not re.fullmatch(r"[a-z0-9_]{2,64}", tag):
            attempts.append({
                "platform": "instagram", "query": tag[:80], "result": "invalid_hashtag"
            })
            continue
        terms = SEARCH_TERMS.get(tag, tag.replace("_", " "))
        query = f"site:instagram.com/reel/ {terms}"
        new_unique = 0
        bing_result = "no_indexed_public_reels"
        status: int | None = None
        bing_error_type: str | None = None
        try:
            response = client.get(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss", "count": 10},
                timeout=timeout_seconds,
                allow_redirects=True,
            )
            status = int(getattr(response, "status_code", 0))
            if status != 200:
                bing_result = f"http_{status}"
            else:
                items = _rss_items(getattr(response, "text", "") or "")
                if not items:
                    bing_result = "invalid_or_empty_rss"
                for item in items:
                    direct = _search_result_instagram_reel(item.get("link", ""))
                    if not direct:
                        parser = _SearchAnchorParser()
                        try:
                            parser.feed(item.get("description", ""))
                        except Exception:
                            pass
                        for anchor in parser.anchors:
                            direct = _search_result_instagram_reel(anchor["href"])
                            if direct:
                                break
                    if not direct:
                        continue
                    shortcode, permalink = direct
                    title = item.get("title", "")
                    snippet = item.get("description", "")
                    published = _search_item_published_at(item.get("pubdate", ""))
                    if add_candidate(shortcode, permalink, title, snippet, tag,
                                     "termux_bing_rss_public_search", published):
                        new_unique += 1
                    if len(candidates) >= limit:
                        break
                if new_unique:
                    bing_result = "metadata_found"
        except requests.RequestException as exc:
            bing_result = "request_failed"
            bing_error_type = type(exc).__name__
        attempts.append({
            "platform": "instagram", "query": "#" + tag, "engine": "bing_rss",
            "http_status": status,
            "new_unique": new_unique, "result": bing_result,
            "stale_or_undated_skipped": stale_or_undated[0],
            "error_type": bing_error_type,
        })

        # Try a second public search engine only when Bing returned no Reel URLs.
        if new_unique == 0 and len(candidates) < limit and not ddg_blocked:
            ddg_result = "no_indexed_public_reels"
            ddg_status = None
            ddg_added = 0
            try:
                response = client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    timeout=timeout_seconds,
                    allow_redirects=True,
                )
                ddg_status = int(getattr(response, "status_code", 0))
                if ddg_status != 200:
                    ddg_result = f"http_{ddg_status}"
                    if ddg_status in {202, 401, 403, 429}:
                        ddg_blocked = True
                else:
                    parser = _SearchAnchorParser()
                    try:
                        parser.feed(getattr(response, "text", "") or "")
                    except Exception:
                        pass
                    for anchor in parser.anchors:
                        direct = _search_result_instagram_reel(anchor["href"])
                        if not direct:
                            continue
                        shortcode, permalink = direct
                        if add_candidate(
                            shortcode, permalink, anchor.get("text", ""),
                            anchor.get("text", ""), tag,
                            "termux_duckduckgo_public_search",
                            _search_page_published_at(
                                getattr(response, "text", "") or "", permalink
                            ),
                        ):
                            ddg_added += 1
                        if len(candidates) >= limit:
                            break
                    if ddg_added:
                        ddg_result = "metadata_found"
            except requests.RequestException as exc:
                ddg_result = "request_failed"
                attempts.append({
                    "platform": "instagram", "query": "#" + tag, "engine": "duckduckgo_html",
                    "result": ddg_result, "error_type": type(exc).__name__,
                })
            else:
                attempts.append({
                    "platform": "instagram", "query": "#" + tag, "engine": "duckduckgo_html",
                    "http_status": ddg_status, "new_unique": ddg_added, "result": ddg_result,
                })
        if len(candidates) < limit:
            time.sleep(max(0.0, sleep_seconds))
    return list(candidates.values()), attempts


def discover_instagram_hashtags(
    *,
    session: Any | None = None,
    search_session: Any | None = None,
    tags: tuple[str, ...] = INSTAGRAM_TAGS,
    max_results: int = MAX_RESULTS,
    timeout_seconds: int = 12,
    sleep_seconds: float = 0.4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Use public search-index discovery by default; injected sessions test the HTML parser."""
    if session is None:
        return discover_instagram_public_search(
            tags=tags, max_results=max_results, timeout_seconds=timeout_seconds,
            sleep_seconds=sleep_seconds, session=search_session,
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
    limit = max(1, min(int(max_results), 5))
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
