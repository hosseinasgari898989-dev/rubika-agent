"""Best-effort, metadata-only discovery of Mob Entertainment's public X posts.

This uses ordinary public web-search indexes to discover post URLs, then X's public
syndication/embed response to verify the author and extract the real post text and
media URL. It does not use paid X API credits, login cookies, browser sessions,
private endpoints, downloads, or publishing. Public search indexing can be delayed.
"""
from __future__ import annotations

import html
import json
import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree as ET

import requests


OFFICIAL_HANDLE = "mobgamesstudios"
MAX_RESULTS = 12
POST_ID_RE = re.compile(r"^[0-9]{15,22}$")
POST_URL_RE = re.compile(
    r"https?://(?:(?:www|mobile)\.)?(?:x\.com|twitter\.com)/mobgamesstudios/status/([0-9]{15,22})",
    re.IGNORECASE,
)
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, str]] = []
        self._href = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            values = {key.lower(): value or "" for key, value in attrs}
            self._href = values.get("href", "")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.anchors.append({"href": self._href, "text": " ".join(self._text).strip()})
            self._href = ""
            self._text = []


def _base36_integer(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    output = ""
    while value:
        value, remainder = divmod(value, 36)
        output = digits[remainder] + output
    return output


def _syndication_token(post_id: str) -> str:
    """Build the token used by X's public embedded-post data endpoint."""
    value = (int(post_id) / 1_000_000_000_000_000) * math.pi
    whole = int(value)
    fraction = value - whole
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    fractional: list[str] = []
    for _ in range(16):
        fraction *= 36
        digit = min(35, int(fraction))
        fractional.append(digits[digit])
        fraction -= digit
        if fraction < 1e-12:
            break
    raw = _base36_integer(whole)
    if fractional:
        raw += "." + "".join(fractional).rstrip("0")
    return re.sub(r"(0+|\.)", "", raw)


def _post_id_from_url(raw_url: str) -> str | None:
    value = html.unescape(str(raw_url or "")).strip()
    if value.startswith("//"):
        value = "https:" + value
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"duckduckgo.com", "www.duckduckgo.com"}:
        value = parse_qs(parsed.query).get("uddg", [""])[0]
        if not value:
            return None
        try:
            parsed = urlparse(unquote(value))
        except ValueError:
            return None
        host = (parsed.hostname or "").lower().rstrip(".")
    if host not in {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com"}:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    match = re.fullmatch(r"/mobgamesstudios/status/([0-9]{15,22})/?", parsed.path, re.IGNORECASE)
    if not match:
        return None
    return match.group(1) if POST_ID_RE.fullmatch(match.group(1)) else None


def _rss_items(body: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(body or "")
    except ET.ParseError:
        return []
    items: list[dict[str, str]] = []
    for element in root.iter():
        if str(element.tag).rsplit("}", 1)[-1].lower() != "item":
            continue
        fields: dict[str, str] = {}
        for child in list(element):
            name = str(child.tag).rsplit("}", 1)[-1].lower()
            fields[name] = "".join(child.itertext()).strip()
        if fields:
            items.append(fields)
    return items


def _post_ids_from_search_result(url: str, description: str = "") -> list[str]:
    found: list[str] = []
    direct = _post_id_from_url(url)
    if direct:
        found.append(direct)
    parser = _AnchorParser()
    try:
        parser.feed(html.unescape(description or ""))
    except Exception:
        pass
    for anchor in parser.anchors:
        post_id = _post_id_from_url(anchor.get("href", ""))
        if post_id:
            found.append(post_id)
    for match in POST_URL_RE.finditer(html.unescape(description or "")):
        post_id = match.group(1)
        if post_id not in found:
            found.append(post_id)
    return list(dict.fromkeys(found))


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _tweet_user(payload: dict[str, Any]) -> dict[str, Any]:
    user = payload.get("user")
    if isinstance(user, dict):
        return user
    legacy = payload.get("legacy")
    if isinstance(legacy, dict) and isinstance(legacy.get("user"), dict):
        return legacy["user"]
    result = payload.get("user_results")
    if isinstance(result, dict):
        inner = result.get("result")
        if isinstance(inner, dict):
            legacy = inner.get("legacy")
            if isinstance(legacy, dict):
                return legacy
            return inner
    core = payload.get("core")
    if isinstance(core, dict):
        result = core.get("user_results")
        if isinstance(result, dict) and isinstance(result.get("result"), dict):
            inner = result["result"]
            legacy = inner.get("legacy")
            if isinstance(legacy, dict):
                return legacy
            return inner
    return {}


def _tweet_text(payload: dict[str, Any]) -> str:
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        return re.sub(r"\s+", " ", text).strip()
    legacy = payload.get("legacy")
    if isinstance(legacy, dict):
        text = legacy.get("full_text") or legacy.get("text")
        if isinstance(text, str):
            return re.sub(r"\s+", " ", text).strip()
    return ""


def _media_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("mediaDetails", "media_details"):
        rows = payload.get(key)
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, dict)]
    legacy = payload.get("legacy")
    if isinstance(legacy, dict):
        entities = legacy.get("extended_entities") or legacy.get("entities")
        if isinstance(entities, dict) and isinstance(entities.get("media"), list):
            return [row for row in entities["media"] if isinstance(row, dict)]
    entities = payload.get("extended_entities")
    if isinstance(entities, dict) and isinstance(entities.get("media"), list):
        return [row for row in entities["media"] if isinstance(row, dict)]
    return []


def _media_url(media: dict[str, Any]) -> tuple[str, str] | None:
    kind = str(media.get("type") or "").lower()
    if kind in {"photo", "image"}:
        url = str(media.get("media_url_https") or media.get("media_url") or "").strip()
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and (host == "pbs.twimg.com" or host.endswith(".pbs.twimg.com")):
            return "image", url
        return None
    if kind in {"video", "animated_gif", "gif"}:
        info = media.get("video_info") if isinstance(media.get("video_info"), dict) else {}
        variants = info.get("variants", []) if isinstance(info, dict) else []
        mp4: list[tuple[int, str]] = []
        if isinstance(variants, list):
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                url = str(variant.get("url") or "").strip()
                content_type = str(variant.get("content_type") or "").lower()
                parsed = urlparse(url)
                host = (parsed.hostname or "").lower()
                if (
                    content_type == "video/mp4"
                    and parsed.scheme == "https"
                    and (host == "video.twimg.com" or host.endswith(".video.twimg.com"))
                ):
                    try:
                        bitrate = int(variant.get("bitrate") or 0)
                    except (TypeError, ValueError):
                        bitrate = 0
                    mp4.append((bitrate, url))
        if mp4:
            return "video", max(mp4, key=lambda item: item[0])[1]
    return None


def _parse_post(payload: dict[str, Any], post_id: str) -> dict[str, Any] | None:
    returned_id = str(payload.get("id_str") or payload.get("id") or "").strip()
    legacy = payload.get("legacy") if isinstance(payload.get("legacy"), dict) else {}
    returned_id = returned_id or str(legacy.get("id_str") or legacy.get("id") or "").strip()
    if returned_id and returned_id != post_id:
        return None

    user = _tweet_user(payload)
    handle = str(user.get("screen_name") or user.get("username") or "").lstrip("@").casefold()
    if handle != OFFICIAL_HANDLE:
        return None
    caption = _tweet_text(payload)
    if not caption:
        return None

    media_rows = _media_rows(payload)
    if len(media_rows) != 1:
        # Don't publish only part of an X post's photo carousel.
        return None
    media_result = _media_url(media_rows[0])
    if not media_result:
        return None
    media_type, media_url = media_result
    created = _timestamp(str(payload.get("created_at") or legacy.get("created_at") or ""))
    canonical = f"https://x.com/{OFFICIAL_HANDLE}/status/{post_id}"
    title = caption.split("\n", 1)[0].strip()[:1200] or "Mob Entertainment official X post"
    return {
        "platform": "mob_official",
        "id": "x-" + post_id,
        "external_id": "x-" + post_id,
        "url": canonical,
        "canonical_url": canonical,
        "title": title,
        "caption": caption[:4500],
        "original_caption": caption[:4500],
        "author": "@" + OFFICIAL_HANDLE,
        "source_key": "x.com/" + OFFICIAL_HANDLE,
        "source_name": "Mob Entertainment on X",
        "source_platform": "x",
        "official_source": True,
        "published_at": created,
        "freshness_verified": created is not None,
        "freshness_source": "x_syndication_created_at" if created else "",
        "media_type": media_type,
        "media_url": media_url,
        "thumbnail_url": None,
        "method": "x_public_search_index_plus_syndication",
        "query": "site:x.com/mobgamesstudios/status",
    }


def _fetch_post(client: Any, post_id: str, timeout_seconds: int) -> tuple[dict[str, Any] | None, str]:
    endpoint = "https://cdn.syndication.twimg.com/tweet-result"
    tokens = ["0", _syndication_token(post_id)]
    for token in dict.fromkeys(tokens):
        try:
            response = client.get(
                endpoint,
                params={"id": post_id, "lang": "en", "token": token},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            return None, "request_" + type(exc).__name__
        status = int(getattr(response, "status_code", 0))
        if status != 200:
            continue
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        item = _parse_post(payload, post_id)
        if item is not None:
            return item, "ok"
        # A 200 response without usable media/canonical-author metadata may indicate
        # a different response shape; do not issue more searches or guess fields.
        if str(payload.get("id_str") or payload.get("id") or "") == post_id:
            return None, "post_has_no_supported_single_media_or_official_author"
    return None, "syndication_unavailable"


def discover_mob_x_posts(
    *,
    max_results: int = MAX_RESULTS,
    timeout_seconds: int = 12,
    sleep_seconds: float = 0.15,
    session: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover recent indexed post URLs and verify actual post details via X syndication.

    Public indexing is best-effort: it is free and needs no X API key, but can lag
    behind new posts or fail when a search engine blocks the query.
    """
    client = session or requests.Session()
    try:
        client.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.8",
            "Accept": "application/rss+xml,application/xml,text/html;q=0.9,*/*;q=0.8",
        })
    except AttributeError:
        pass
    limit = max(1, min(int(max_results), MAX_RESULTS))
    post_ids: list[str] = []
    attempts: list[dict[str, Any]] = []
    bing_status: int | None = None
    bing_result = "no_indexed_official_posts"
    queries = (
        "site:x.com/mobgamesstudios/status",
        "site:twitter.com/mobgamesstudios/status",
    )
    for query in queries:
        try:
            response = client.get(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss", "count": "20"},
                timeout=timeout_seconds,
                allow_redirects=True,
            )
            bing_status = int(getattr(response, "status_code", 0))
            if bing_status != 200:
                bing_result = f"http_{bing_status}"
                continue
            for row in _rss_items(str(getattr(response, "text", "") or "")):
                for post_id in _post_ids_from_search_result(row.get("link", ""), row.get("description", "")):
                    if post_id not in post_ids:
                        post_ids.append(post_id)
                if len(post_ids) >= limit:
                    break
            if post_ids:
                bing_result = "indexed_post_urls_found"
                break
        except requests.RequestException as exc:
            bing_result = "request_failed"
            attempts.append({
                "source": "mob_x",
                "engine": "bing_rss",
                "result": bing_result,
                "error_type": type(exc).__name__,
            })
    attempts.append({
        "source": "mob_x",
        "engine": "bing_rss",
        "http_status": bing_status,
        "result": bing_result,
        "post_urls_found": len(post_ids),
    })

    if not post_ids:
        ddg_status: int | None = None
        ddg_result = "no_indexed_official_posts"
        try:
            response = client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": "site:x.com/mobgamesstudios/status"},
                timeout=timeout_seconds,
                allow_redirects=True,
            )
            ddg_status = int(getattr(response, "status_code", 0))
            if ddg_status == 200:
                parser = _AnchorParser()
                parser.feed(str(getattr(response, "text", "") or ""))
                for anchor in parser.anchors:
                    post_id = _post_id_from_url(anchor.get("href", ""))
                    if post_id and post_id not in post_ids:
                        post_ids.append(post_id)
                    if len(post_ids) >= limit:
                        break
                ddg_result = "indexed_post_urls_found" if post_ids else "no_indexed_official_posts"
            elif ddg_status in {202, 401, 403, 429}:
                ddg_result = f"blocked_http_{ddg_status}"
            else:
                ddg_result = f"http_{ddg_status}"
        except requests.RequestException as exc:
            ddg_result = "request_failed"
            attempts.append({
                "source": "mob_x",
                "engine": "duckduckgo_html",
                "result": ddg_result,
                "error_type": type(exc).__name__,
            })
        attempts.append({
            "source": "mob_x",
            "engine": "duckduckgo_html",
            "http_status": ddg_status,
            "result": ddg_result,
            "post_urls_found": len(post_ids),
        })

    output: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for post_id in post_ids[:limit]:
        item, result = _fetch_post(client, post_id, timeout_seconds)
        if item:
            output.append(item)
        else:
            skipped[result] = skipped.get(result, 0) + 1
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    attempts.append({
        "source": "mob_x",
        "engine": "public_syndication",
        "result": "metadata_found" if output else "no_complete_media_posts",
        "candidate_count": len(output),
        "skipped": skipped,
    })
    return output, attempts
