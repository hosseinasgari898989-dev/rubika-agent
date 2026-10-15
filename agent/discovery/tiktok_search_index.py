"""Discover unknown TikTok creator handles from ordinary public web-search indexes.

Search-result dates are intentionally not treated as upload dates. This module only
returns canonical public TikTok video links and creator handles; the caller must
verify freshness from yt-dlp public creator-feed metadata before accepting candidates.
No login, cookies, challenge bypass, proxy, paid API, or media download is used.
"""
from __future__ import annotations

import html
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree as ET

import requests

from agent.source_policy import known_tiktok_profiles

DEFAULT_QUERIES = (
    'site:tiktok.com/@ "poppyplaytime" edit',
    'site:tiktok.com/@ "huggy wuggy" edit',
    'site:tiktok.com/@ "catnap" edit',
    'site:tiktok.com/@ "smiling critters" edit',
)
DIRECT_VIDEO_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/@([A-Za-z0-9._-]{1,40})/video/(\d{10,25})",
    re.IGNORECASE,
)


class _AnchorParser(HTMLParser):
    """Collect ordinary search-result anchors only."""
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
        if not href or not ("result__a" in classes or "tiktok.com" in href or "/l/?" in href):
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


def _rss_items(body: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(body or "")
    except ET.ParseError:
        return []
    rows: list[dict[str, str]] = []
    for item in root.iter():
        if str(item.tag).rsplit("}", 1)[-1].lower() != "item":
            continue
        fields: dict[str, str] = {}
        for child in list(item):
            fields[str(child.tag).rsplit("}", 1)[-1].lower()] = "".join(child.itertext()).strip()
        if fields:
            rows.append(fields)
    return rows


def _tiktok_video_url(raw_url: str) -> tuple[str, str, str] | None:
    """Accept only direct canonical TikTok video URLs from public search results."""
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
        targets = parse_qs(parsed.query).get("uddg", [])
        if not targets:
            return None
        value = unquote(targets[0])
        try:
            parsed = urlparse(value)
        except ValueError:
            return None
        host = (parsed.hostname or "").lower()
    if host not in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return None
    match = re.fullmatch(
        r"/@([A-Za-z0-9._-]{1,40})/video/(\d{10,25})/?",
        parsed.path,
        re.IGNORECASE,
    )
    if not match:
        return None
    handle, video_id = match.group(1), match.group(2)
    return handle, video_id, f"https://www.tiktok.com/@{handle}/video/{video_id}"


def _found_links_from_text(value: str) -> list[tuple[str, str, str]]:
    text = html.unescape(str(value or "")).replace("\\/", "/").replace("\\u002F", "/")
    found: dict[str, tuple[str, str, str]] = {}
    for match in DIRECT_VIDEO_RE.finditer(text):
        direct = _tiktok_video_url(match.group(0))
        if direct:
            found.setdefault(direct[1], direct)
    return list(found.values())


def discover_tiktok_creator_handles(
    *,
    queries: tuple[str, ...] = DEFAULT_QUERIES,
    max_handles: int = 24,
    timeout_seconds: int = 12,
    sleep_seconds: float = 0.3,
    session: Any | None = None,
    known_profiles: tuple[str, ...] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Find candidate handles via Bing RSS / DuckDuckGo HTML, without trusting index dates."""
    client = session if session is not None else requests.Session()
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

    limit = max(0, min(int(max_handles), 36))
    if limit == 0:
        return [], []
    known = {
        str(profile).strip().removeprefix("@").lower()
        for profile in (known_profiles if known_profiles is not None else known_tiktok_profiles())
    }
    handles: dict[str, str] = {}
    attempts: list[dict[str, Any]] = []
    ddg_blocked = False

    def add_url(url: str) -> bool:
        direct = _tiktok_video_url(url)
        if not direct:
            return False
        handle, _video_id, _canonical = direct
        key = handle.lower()
        if key in known or key in handles or len(handles) >= limit:
            return False
        handles[key] = handle
        return True

    for raw_query in queries:
        if len(handles) >= limit:
            break
        query = str(raw_query or "").strip()[:240]
        if not query:
            continue
        links_found = 0
        added_before = len(handles)
        bing_status: int | None = None
        bing_result = "no_canonical_public_video_links"
        bing_error: str | None = None
        try:
            response = client.get(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss", "count": 10},
                timeout=timeout_seconds,
                allow_redirects=True,
            )
            bing_status = int(getattr(response, "status_code", 0))
            if bing_status != 200:
                bing_result = f"http_{bing_status}"
            else:
                body = str(getattr(response, "text", "") or "")
                items = _rss_items(body)
                for item in items:
                    found: list[tuple[str, str, str]] = []
                    found.extend(_found_links_from_text(item.get("link", "")))
                    found.extend(_found_links_from_text(item.get("description", "")))
                    parser = _AnchorParser()
                    try:
                        parser.feed(item.get("description", ""))
                    except Exception:
                        pass
                    for anchor in parser.anchors:
                        direct = _tiktok_video_url(anchor.get("href", ""))
                        if direct:
                            found.append(direct)
                    for _handle, _video_id, canonical in found:
                        links_found += 1
                        add_url(canonical)
                        if len(handles) >= limit:
                            break
                    if len(handles) >= limit:
                        break
                if links_found:
                    bing_result = "public_video_links_found"
                elif not items:
                    bing_result = "invalid_or_empty_rss"
        except requests.RequestException as exc:
            bing_result = "request_failed"
            bing_error = type(exc).__name__
        attempts.append({
            "platform": "tiktok", "method": "bing_rss_search_index", "query": query,
            "http_status": bing_status, "video_links_found": links_found,
            "new_handles": len(handles) - added_before, "result": bing_result,
            "error_type": bing_error,
        })

        # Use a second public index when Bing did not return canonical TikTok links.
        if links_found == 0 and len(handles) < limit and not ddg_blocked:
            ddg_status: int | None = None
            ddg_result = "no_canonical_public_video_links"
            ddg_links = 0
            ddg_added_before = len(handles)
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
                    body = str(getattr(response, "text", "") or "")
                    parser = _AnchorParser()
                    try:
                        parser.feed(body)
                    except Exception:
                        pass
                    for anchor in parser.anchors:
                        direct = _tiktok_video_url(anchor.get("href", ""))
                        if direct:
                            ddg_links += 1
                            add_url(direct[2])
                        for _handle, _video_id, canonical in _found_links_from_text(anchor.get("href", "")):
                            ddg_links += 1
                            add_url(canonical)
                        if len(handles) >= limit:
                            break
                    if ddg_links:
                        ddg_result = "public_video_links_found"
            except requests.RequestException as exc:
                ddg_result = "request_failed"
                attempts.append({
                    "platform": "tiktok", "method": "duckduckgo_html_search_index",
                    "query": query, "result": ddg_result, "error_type": type(exc).__name__,
                })
            else:
                attempts.append({
                    "platform": "tiktok", "method": "duckduckgo_html_search_index",
                    "query": query, "http_status": ddg_status,
                    "video_links_found": ddg_links,
                    "new_handles": len(handles) - ddg_added_before,
                    "result": ddg_result,
                })
        time.sleep(max(0.0, float(sleep_seconds)))
    return list(handles.values()), attempts
