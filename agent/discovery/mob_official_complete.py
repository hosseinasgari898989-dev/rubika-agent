"""Complete, read-only discovery for official Mob Entertainment media.

Priority is Mob's newsroom; official YouTube and official Steam announcements are
fallback sources. This module only returns posts with their real original caption
and an actual image/video URL from that same post. It never downloads or publishes.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .mob_official import (
    DEFAULT_CONFIG,
    MAX_SOURCE_BODY_BYTES,
    USER_AGENT,
    _absolute_http_url,
    _atom_entries,
    _fetch_text,
    _newsroom_entries,
    _plain_text,
    _timestamp,
)

STEAM_APP_ID = 1721470
MAX_DISCOVERY_ITEMS = 25
MAX_CAPTION_CHARS = 4500
MAX_SOURCE_BYTES = 3 * 1024 * 1024
OFFICIAL_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "youtu.be"}
STEAM_MEDIA_HOST_SUFFIXES = ("steamstatic.com", "akamaihd.net")


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _is_host(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


class _PostPageParser(HTMLParser):
    """Extract post-level metadata and embedded media, not generic homepage text."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.meta: dict[str, str] = {}
        self.media: list[tuple[str, str]] = []
        self.article_media: list[tuple[str, str]] = []
        self.jsonld_chunks: list[str] = []
        self._script_jsonld = False
        self._script_data: list[str] = []
        self._ignored = 0
        self._article_text: list[str] = []
        self.article_paragraphs: list[str] = []
        self._in_article_paragraph = False
        self._paragraph_parts: list[str] = []
        self._capture_depth = 0
        self._tag_stack: list[tuple[str, bool, bool]] = []
        self._void_tags = {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        lower = tag.lower()
        ignored_tag = lower in {"script", "style", "noscript", "nav", "footer", "header", "aside"}
        if ignored_tag:
            self._ignored += 1
        if lower == "script":
            self._script_jsonld = values.get("type", "").lower() == "application/ld+json"
            self._script_data = []
        if lower == "meta":
            key = (values.get("property") or values.get("name") or values.get("itemprop") or "").strip().lower()
            value = values.get("content", "").strip()
            if key and value and key not in self.meta:
                self.meta[key] = value
        if lower in {"img", "video", "source", "audio", "iframe"}:
            attr = "poster" if lower == "video" and values.get("poster") else "src"
            raw = values.get(attr) or values.get("data-src") or values.get("data-lazy-src") or ""
            if raw:
                kind = "video" if lower in {"video", "source", "iframe"} else ("audio" if lower == "audio" else "image")
                media_entry = (kind, urljoin(self.base_url, raw))
                self.media.append(media_entry)
                # Keep content media separate from shared header/footer logos and icons.
                if self._capture_depth > 0:
                    self.article_media.append(media_entry)
        capture_container = (
            lower in {"article", "main"}
            or (
                lower in {"div", "section"}
                and any(
                    term in (values.get("class", "") + " " + values.get("id", "")).lower()
                    for term in ("article-body", "article-content", "news-content", "press-content", "post-content", "story-content", "rte")
                )
            )
        )
        if capture_container:
            self._capture_depth += 1
        if lower == "p" and self._capture_depth > 0:
            self._in_article_paragraph = True
            self._paragraph_parts = []
        if lower not in self._void_tags:
            self._tag_stack.append((lower, ignored_tag, capture_container))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._script_jsonld:
            self._script_data.append(data)
        if self._ignored == 0 and self._capture_depth > 0:
            text = re.sub(r"\s+", " ", data).strip()
            if text:
                self._article_text.append(text)
                if self._in_article_paragraph:
                    self._paragraph_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower == "p" and self._in_article_paragraph:
            paragraph = re.sub(r"\s+", " ", " ".join(self._paragraph_parts)).strip()
            if paragraph:
                self.article_paragraphs.append(paragraph)
            self._paragraph_parts = []
            self._in_article_paragraph = False
        if lower == "script" and self._script_jsonld:
            self.jsonld_chunks.append("".join(self._script_data))
            self._script_jsonld = False
            self._script_data = []
        # Pop the matching element and any malformed nested elements above it. Only
        # decrement capture/ignore scopes for elements that actually opened them.
        for index in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[index][0] == lower:
                popped = self._tag_stack[index:]
                del self._tag_stack[index:]
                for _tag, ignored_tag, capture_container in reversed(popped):
                    if ignored_tag:
                        self._ignored = max(0, self._ignored - 1)
                    if capture_container:
                        self._capture_depth = max(0, self._capture_depth - 1)
                break


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _detail_from_page(body: str, page_url: str) -> dict[str, Any]:
    parser = _PostPageParser(page_url)
    parser.feed(body)
    metadata = parser.meta
    json_objects: list[dict[str, Any]] = []
    for raw in parser.jsonld_chunks:
        try:
            json_objects.extend(item for item in _walk_json(json.loads(raw)) if isinstance(item, dict))
        except (ValueError, TypeError):
            continue

    def meta_value(*keys: str) -> str:
        for key in keys:
            if metadata.get(key, "").strip():
                return metadata[key].strip()
        return ""

    title = meta_value("og:title", "twitter:title", "headline")
    caption = meta_value("og:description", "twitter:description", "description")
    article_body_fallback = ""
    published = meta_value("article:published_time", "datepublished", "date")
    for row in json_objects:
        row_type = row.get("@type", "")
        kinds = row_type if isinstance(row_type, list) else [row_type]
        if any(str(kind).lower() in {"article", "newsarticle", "blogposting", "reportage"} for kind in kinds):
            title = title or str(row.get("headline") or row.get("name") or "")
            caption = caption or str(row.get("description") or "")
            article_body_fallback = article_body_fallback or str(row.get("articleBody") or "")
            published = published or str(row.get("datePublished") or row.get("dateCreated") or "")

    # If the page has no explicit caption/description, use the first genuine
    # article paragraph as the source excerpt. Do not stuff the whole press release
    # and its boilerplate into a social-media caption or invent new text.
    normalized_title = _plain_text(title, 1200).casefold()
    if not caption:
        for paragraph in parser.article_paragraphs:
            clean_paragraph = _plain_text(paragraph, MAX_CAPTION_CHARS)
            if len(clean_paragraph) < 40 or clean_paragraph.casefold() == normalized_title:
                continue
            caption = clean_paragraph
            break
    if not caption:
        caption = article_body_fallback or " ".join(parser._article_text)
    title = _plain_text(title, 1200)
    caption = _plain_text(caption, MAX_CAPTION_CHARS)

    image_url = meta_value("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")
    video_url = meta_value("og:video:secure_url", "og:video:url", "og:video", "twitter:player:stream")
    if not image_url or not video_url:
        for row in json_objects:
            row_type = row.get("@type", "")
            kinds = row_type if isinstance(row_type, list) else [row_type]
            is_article = any(
                str(kind).lower() in {"article", "newsarticle", "blogposting", "reportage"}
                for kind in kinds
            )
            if not is_article:
                continue
            for key in ("image", "thumbnailUrl"):
                value = row.get(key)
                if not image_url and isinstance(value, str):
                    image_url = value
                elif not image_url and isinstance(value, dict):
                    image_url = str(value.get("url") or "")
                elif not image_url and isinstance(value, list) and value:
                    first = value[0]
                    image_url = str(first.get("url") if isinstance(first, dict) else first or "")
            for key in ("contentUrl", "embedUrl"):
                value = row.get("video")
                if isinstance(value, dict):
                    video_url = video_url or str(value.get(key) or "")
                elif isinstance(value, str):
                    video_url = video_url or value
    image_url = _absolute_http_url(page_url, image_url) if image_url else None
    video_url = _absolute_http_url(page_url, video_url) if video_url else None

    # Use media embedded in the article, never a generic site-header image.
    for kind, raw in parser.article_media:
        candidate_url = _absolute_http_url(page_url, raw)
        if not candidate_url:
            continue
        host = _host(candidate_url)
        path = urlparse(candidate_url).path.lower()
        if kind == "video" and (
            path.endswith((".mp4", ".webm", ".mov", ".m4v", ".m3u8"))
            or any(_is_host(host, h) for h in OFFICIAL_YOUTUBE_HOSTS)
            or "vimeo.com" in host
        ):
            video_url = video_url or candidate_url
        elif kind == "image" and not image_url and path.endswith((".jpg", ".jpeg", ".png", ".webp")):
            image_url = candidate_url

    if video_url:
        media_type, media_url = "video", video_url
    elif image_url:
        media_type, media_url = "image", image_url
    else:
        media_type, media_url = "unknown", None
    return {
        "title": title,
        "original_caption": caption,
        "caption": caption,
        "published_at": _timestamp(published),
        "media_type": media_type,
        "media_url": media_url,
        "canonical_url": page_url,
    }


def _make_item(
    *,
    source_platform: str,
    source_name: str,
    source_key: str,
    canonical_url: str,
    external_id: str,
    title: str,
    caption: str,
    published_at: Any,
    media_type: str,
    media_url: str,
    source_priority: int,
) -> dict[str, Any] | None:
    if not canonical_url.startswith("https://") or not external_id or not caption.strip() or not media_url:
        return None
    if media_type not in {"image", "video"}:
        return None

    parsed = urlparse(canonical_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if source_platform == "youtube":
        # Keep only the canonical video ID, dropping tracking parameters.
        from urllib.parse import parse_qs
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            match = re.fullmatch(r"/([A-Za-z0-9_-]{11})/?", parsed.path) if host == "youtu.be" else None
            video_id = match.group(1) if match else ""
        if not video_id or not (_is_host(host, "youtube.com") or host == "youtu.be"):
            return None
        canonical_url = "https://www.youtube.com/watch?v=" + video_id
    elif source_platform == "steam":
        if not _is_host(host, "steamcommunity.com"):
            return None
        canonical_url = "https://steamcommunity.com" + parsed.path
    else:
        # Drop tracking query strings/fragments from official newsroom URLs.
        canonical_url = parsed._replace(query="", fragment="").geturl()

    clean_media = _absolute_http_url(canonical_url, media_url)
    if not clean_media:
        return None
    return {
        "platform": "mob_official",
        "source_platform": source_platform,
        "source_name": source_name,
        "source_key": source_key,
        "id": external_id[:120],
        "external_id": external_id[:120],
        "canonical_url": canonical_url,
        "url": canonical_url,
        "title": _plain_text(title, 1200),
        "caption": _plain_text(caption, MAX_CAPTION_CHARS),
        "original_caption": _plain_text(caption, MAX_CAPTION_CHARS),
        "published_at": _timestamp(published_at),
        "media_type": media_type,
        "media_url": clean_media,
        "thumbnail_url": None,
        "official_source": True,
        "method": "official_post_with_original_media",
        "source_priority": source_priority,
    }


def _discover_newsroom(session: Any, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = next((row for row in sources if row.get("id") == "mob_newsroom"), None)
    if not source or source.get("enabled") is not True:
        return []
    home = str(source.get("url") or "https://www.mobentertainment.com/")
    body = _fetch_text(session, home)
    cards = _newsroom_entries(body, source)
    output: list[dict[str, Any]] = []
    for card in cards[:MAX_DISCOVERY_ITEMS]:
        article_url = str(card.get("canonical_url") or "")
        host = _host(article_url)
        if not (_is_host(host, "mobentertainment.com") or _is_host(host, "poppyplaytime.com")):
            continue
        try:
            html = _fetch_text(session, article_url)
            detail = _detail_from_page(html, article_url)
        except Exception:
            continue
        external_id = urlparse(article_url).path.rstrip("/").rsplit("/", 1)[-1]
        external_id = re.sub(r"[^A-Za-z0-9._-]", "-", external_id)[:100]
        item = _make_item(
            source_platform="website",
            source_name="Mob Entertainment",
            source_key="mobentertainment.com",
            canonical_url=article_url,
            external_id=external_id or hashlib.sha256(article_url.encode()).hexdigest()[:24],
            title=detail["title"] or card.get("title") or "",
            caption=detail["original_caption"],
            published_at=detail["published_at"] or card.get("published_at"),
            media_type=detail["media_type"],
            media_url=detail["media_url"] or "",
            source_priority=0,
        )
        if item:
            output.append(item)
    return output


def _discover_youtube_via_ytdlp(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Fallback for the verified channel when YouTube's public Atom feed is unavailable."""
    channel_id = str(source.get("channel_id") or "").strip()
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{20,30}", channel_id):
        return []
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("youtube_profile_yt_dlp_unavailable") from None

    channel_url = "https://www.youtube.com/channel/" + channel_id + "/videos"
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": 5,
        "socket_timeout": 15,
        "retries": 1,
        "extractor_retries": 1,
        "noplaylist": False,
    }
    result: list[dict[str, Any]] = []
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            channel_info = ydl.extract_info(channel_url, download=False)
            entries = channel_info.get("entries", []) if isinstance(channel_info, dict) else []
            for entry in entries[:5] if isinstance(entries, list) else []:
                if not isinstance(entry, dict):
                    continue
                video_id = str(entry.get("id") or "").strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                    continue
                canonical_url = "https://www.youtube.com/watch?v=" + video_id
                full_info = entry
                # Flat channel entries often omit the genuine description. Fetch
                # the selected video metadata, not its thumbnail, before publishing.
                if not str(entry.get("description") or "").strip():
                    try:
                        full_info = ydl.extract_info(canonical_url, download=False) or entry
                    except Exception:
                        continue
                caption = str(full_info.get("description") or "").strip()
                if not caption:
                    # The title alone is not substituted for a missing caption.
                    continue
                timestamp = full_info.get("release_timestamp") or full_info.get("timestamp") or entry.get("timestamp")
                if isinstance(timestamp, (int, float)):
                    try:
                        timestamp = datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
                    except (ValueError, OSError, OverflowError):
                        timestamp = None
                if not timestamp:
                    upload_date = str(full_info.get("upload_date") or entry.get("upload_date") or "")
                    if re.fullmatch(r"\d{8}", upload_date):
                        timestamp = upload_date[:4] + "-" + upload_date[4:6] + "-" + upload_date[6:] + "T00:00:00+00:00"
                item = _make_item(
                    source_platform="youtube",
                    source_name="Mob Entertainment YouTube",
                    source_key="youtube.com",
                    canonical_url=canonical_url,
                    external_id="yt-" + video_id,
                    title=str(full_info.get("title") or entry.get("title") or ""),
                    caption=caption,
                    published_at=timestamp if isinstance(timestamp, str) else None,
                    media_type="video",
                    media_url=canonical_url,
                    source_priority=1,
                )
                if item:
                    result.append(item)
    except Exception as exc:
        raise RuntimeError("youtube_profile_" + type(exc).__name__) from None
    return result


def _discover_youtube(session: Any, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = next((row for row in sources if row.get("id") == "mob_youtube"), None)
    if not source or source.get("enabled") is not True:
        return []
    channel_id = str(source.get("channel_id") or "").strip()
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{20,30}", channel_id):
        return []
    feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + channel_id
    feed_source = dict(source)
    feed_source["url"] = feed_url
    try:
        rows = _atom_entries(_fetch_text(session, feed_url), feed_source)
    except Exception:
        rows = []
    result: list[dict[str, Any]] = []
    for row in rows[:MAX_DISCOVERY_ITEMS]:
        url = str(row.get("canonical_url") or "")
        external_id = str(row.get("id") or "").rsplit(":", 1)[-1]
        host = _host(url)
        if not (host == "www.youtube.com" or _is_host(host, "youtube.com")):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", external_id):
            continue
        caption = str(row.get("original_caption") or row.get("caption") or "").strip()
        item = _make_item(
            source_platform="youtube",
            source_name="Mob Entertainment YouTube",
            source_key="youtube.com",
            canonical_url=url,
            external_id="yt-" + external_id,
            title=str(row.get("title") or ""),
            caption=caption,
            published_at=row.get("published_at"),
            media_type="video",
            media_url=url,
            source_priority=1,
        )
        if item:
            result.append(item)
    if result:
        return result
    # YouTube's official channel is linked from Mob's newsroom, but the Atom
    # endpoint may return 404. Fall back to metadata from that same verified channel.
    return _discover_youtube_via_ytdlp(source)


class _SteamContentParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.text_parts: list[str] = []
        self.media: list[tuple[str, str]] = []
        self._ignored = 0
        self._tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        lower = tag.lower()
        self._tags.append(lower)
        if lower in {"script", "style", "noscript"}:
            self._ignored += 1
        if lower in {"img", "video", "source", "iframe"}:
            raw = values.get("src") or values.get("data-src") or values.get("poster") or ""
            if raw:
                self.media.append(("video" if lower in {"video", "source", "iframe"} else "image", raw))

    def handle_endtag(self, tag: str) -> None:
        if self._tags:
            popped = self._tags.pop()
            if popped in {"script", "style", "noscript"}:
                self._ignored = max(0, self._ignored - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored == 0:
            text = re.sub(r"\s+", " ", data).strip()
            if text:
                self.text_parts.append(text)


def _steam_media_url(raw: str, canonical_url: str) -> str | None:
    # Steam news HTML sometimes uses its documented clan-image placeholder.
    match = re.match(r"^\{STEAM_CLAN_IMAGE\}/([0-9]+)/(.+)$", raw.strip())
    if match:
        raw = f"https://clan.cloudflare.steamstatic.com/images/{match.group(1)}/{match.group(2)}"
    return _absolute_http_url(canonical_url, raw)


def _discover_steam(session: Any, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source = next((row for row in sources if row.get("id") == "steam_news"), None)
    if not source or source.get("enabled") is not True:
        return []
    app_id = source.get("app_id", STEAM_APP_ID)
    if str(app_id) != str(STEAM_APP_ID):
        return []
    endpoint = str(source.get("url") or "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/")
    response = session.get(
        endpoint,
        params={
            "appid": str(STEAM_APP_ID),
            "count": str(MAX_DISCOVERY_ITEMS),
            "maxlength": "0",
            "feeds": "steam_community_announcements",
        },
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=20,
    )
    if response.status_code != 200:
        raise RuntimeError("steam_news_http_" + str(response.status_code))
    payload = response.json()
    rows = ((payload.get("appnews") or {}).get("newsitems") or []) if isinstance(payload, dict) else []
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        feed = str(row.get("feedname") or "").casefold()
        # Do not label external press coverage/community chatter as developer news.
        if feed and feed not in {"steam_community_announcements", "steam_community_announcement"}:
            continue
        raw_url = str(row.get("url") or "")
        host = _host(raw_url)
        gid = str(row.get("gid") or "").strip()
        if not re.fullmatch(r"[0-9]{5,30}", gid):
            continue

        if host == "steamcommunity.com" or _is_host(host, "steamcommunity.com"):
            canonical_url = raw_url
        elif (
            _is_host(host, "akamaihd.net")
            and re.fullmatch(r"/news/externalpost/steam_community_announcements/[0-9]+/?", urlparse(raw_url).path)
            and "mob entertainment" in str(row.get("author") or "").casefold()
        ):
            # Steam's official app-news API often returns an Akamai external-post
            # URL for the Mob-authored community announcement. Rebuild the canonical
            # Steam Community URL from the verified feed item ID.
            canonical_url = (
                "https://steamcommunity.com/games/"
                + str(STEAM_APP_ID)
                + "/announcements/detail/"
                + gid
            )
        else:
            continue

        contents = str(row.get("contents") or "")
        parser = _SteamContentParser(canonical_url)
        parser.feed(contents)

        # Steam announcement bodies commonly use BBCode, including embedded
        # previewyoutube and STEAM_CLAN_IMAGE tokens rather than HTML media tags.
        caption_body = re.sub(
            r"\[previewyoutube=[\"'][^]]+\][^\[]*\[/previewyoutube\]",
            " ",
            contents,
            flags=re.IGNORECASE,
        )
        caption_body = re.sub(
            r"\[img\].*?\[/img\]",
            " ",
            caption_body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        caption_body = re.sub(
            r"\[dynamiclink\b[^\]]*\].*?\[/dynamiclink\]",
            " ",
            caption_body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        caption_body = re.sub(
            r"\[url=[\"'][^\"']+[\"']\](.*?)\[/url\]",
            r"\1",
            caption_body,
            flags=re.IGNORECASE | re.DOTALL,
        )
        caption_body = re.sub(r"\[(?:/?p|/?b|/?i|/?u|/?strike|/?h[1-6]|/?list|\*|/?hr)\]", " ", caption_body, flags=re.IGNORECASE)
        caption_body = re.sub(r"\[[^\]]{1,80}\]", " ", caption_body)
        caption = _plain_text(caption_body, MAX_CAPTION_CHARS)

        chosen_video = ""
        chosen_image = ""
        for kind, raw in parser.media:
            clean = _steam_media_url(raw, canonical_url)
            if not clean:
                continue
            host_media = _host(clean)
            media_path = urlparse(clean).path.lower()
            if kind == "video" and (
                media_path.endswith((".mp4", ".webm", ".mov", ".m3u8"))
                or host_media == "youtube.com"
                or host_media.endswith(".youtube.com")
                or host_media == "youtu.be"
            ):
                chosen_video = chosen_video or clean
            elif kind == "image" and (
                media_path.endswith((".jpg", ".jpeg", ".png", ".webp"))
                or any(_is_host(host_media, suffix) for suffix in STEAM_MEDIA_HOST_SUFFIXES)
            ):
                chosen_image = chosen_image or clean

        if not chosen_video:
            preview = re.search(
                r'\[previewyoutube=[\"\']([A-Za-z0-9_-]{11})(?:;[^\\"\']*)?[\"\']\]',
                contents,
                flags=re.IGNORECASE,
            )
            if preview:
                chosen_video = "https://www.youtube.com/watch?v=" + preview.group(1)

        if not chosen_image:
            for image_match in re.finditer(r"\[img\](.*?)\[/img\]", contents, flags=re.IGNORECASE | re.DOTALL):
                clean = _steam_media_url(unescape(image_match.group(1).strip()), canonical_url)
                if not clean:
                    continue
                host_media = _host(clean)
                media_path = urlparse(clean).path.lower()
                if (
                    media_path.endswith((".jpg", ".jpeg", ".png", ".webp"))
                    or any(_is_host(host_media, suffix) for suffix in STEAM_MEDIA_HOST_SUFFIXES)
                ):
                    chosen_image = clean
                    break
        # A Steam news card without a post-level image/video or body is not publishable.
        if not caption or not (chosen_video or chosen_image):
            continue
        published = row.get("date")
        try:
            published = datetime.fromtimestamp(int(published), tz=timezone.utc).isoformat()
        except (ValueError, TypeError, OSError, OverflowError):
            published = None
        # A direct file is preferred as video media. If the post embeds only a
        # YouTube/Vimeo page and also contains an actual Steam-hosted image, use that
        # original in-post image for preview instead of relying on a blocked video page.
        video_host = _host(chosen_video) if chosen_video else ""
        video_is_page = bool(chosen_video) and (
            _is_host(video_host, "youtube.com")
            or video_host == "youtu.be"
            or _is_host(video_host, "vimeo.com")
        )
        use_image_fallback = bool(chosen_image) and video_is_page
        selected_media_type = "image" if use_image_fallback or not chosen_video else "video"
        selected_media_url = chosen_image if use_image_fallback else (chosen_video or chosen_image)
        item = _make_item(
            source_platform="steam",
            source_name="Poppy Playtime official Steam news",
            source_key="steamcommunity.com",
            canonical_url=canonical_url,
            external_id="steam-" + gid,
            title=str(row.get("title") or ""),
            caption=caption,
            published_at=published,
            media_type=selected_media_type,
            media_url=selected_media_url,
            source_priority=2,
        )
        if item:
            output.append(item)
    return output


def _normalize_fingerprint_text(value: Any) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).casefold()
    raw = re.sub(r"[^\w]+", " ", raw, flags=re.UNICODE)
    return re.sub(r"\s+", " ", raw).strip()


def _fingerprints(item: dict[str, Any]) -> set[str]:
    """Detect the same substantial caption even when cross-post titles differ."""
    title = _normalize_fingerprint_text(item.get("title"))
    caption = _normalize_fingerprint_text(item.get("original_caption") or item.get("caption"))
    values: set[str] = set()
    if len(caption) >= 48:
        values.add("caption:" + hashlib.sha256(caption.encode("utf-8")).hexdigest())
    combined = " ".join(part for part in (title, caption) if part).strip()
    if len(combined) >= 24:
        values.add("combined:" + hashlib.sha256(combined.encode("utf-8")).hexdigest())
    return values


def _fingerprint(item: dict[str, Any]) -> str:
    """Compatibility helper used by the earlier discovery tests."""
    values = sorted(_fingerprints(item))
    return values[0] if values else ""


def discover_complete_official_posts(
    *,
    config_path: str | Path = DEFAULT_CONFIG,
    session: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read official source feeds; no downloading, publishing or persistent writes."""
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], [{"source": "config", "result": "config_unavailable"}]
    if not isinstance(config, dict) or config.get("version") != 1:
        return [], [{"source": "config", "result": "config_invalid"}]
    sources = config.get("official_sources", [])
    if not isinstance(sources, list):
        return [], [{"source": "config", "result": "sources_invalid"}]
    client = session or requests.Session()
    attempts: list[dict[str, Any]] = []
    found: list[dict[str, Any]] = []
    adapters = (
        ("mob_newsroom", _discover_newsroom),
        ("mob_youtube", _discover_youtube),
        ("steam_news", _discover_steam),
    )
    for source_id, adapter in adapters:
        try:
            rows = adapter(client, sources)
            found.extend(rows)
            attempts.append({"source": source_id, "result": "ok" if rows else "no_complete_posts", "candidate_count": len(rows)})
        except Exception as exc:
            raw_error = str(exc)
            safe_error = re.search(
                r"(?:source_http_\d{3}|source_response_too_large|steam_news_http_\d{3}|youtube_profile_[A-Za-z0-9_]+)",
                raw_error,
            )
            attempts.append({
                "source": source_id,
                "result": "source_error",
                "error_code": (
                    re.sub(r"[^A-Za-z0-9_-]", "", safe_error.group(0))[:80]
                    if safe_error else (re.sub(r"[^A-Za-z0-9_-]", "", type(exc).__name__)[:60] or "source_error")
                ),
                "candidate_count": 0,
            })

    # Prefer the primary newsroom when the exact same post is syndicated elsewhere.
    found.sort(
        key=lambda row: (
            int(row.get("source_priority", 9)),
            -(int(datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")).timestamp())
              if row.get("published_at") else 0),
        )
    )
    output: list[dict[str, Any]] = []
    seen_ids: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()
    seen_fingerprints: set[str] = set()
    for row in found:
        identity = (str(row.get("source_platform")), str(row.get("external_id")))
        canonical = str(row.get("canonical_url") or "")
        fingerprints = _fingerprints(row)
        if identity in seen_ids or (canonical and canonical in seen_urls) or fingerprints.intersection(seen_fingerprints):
            continue
        seen_ids.add(identity)
        if canonical:
            seen_urls.add(canonical)
        seen_fingerprints.update(fingerprints)
        output.append(row)
    return output, attempts


def discover_all_official_posts(
    *,
    config_path: str | Path = DEFAULT_CONFIG,
    max_results: int = 12,
    session: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Combine X with official newsroom, YouTube and Steam; never download or publish.

    Duplicate cross-posts prefer the primary newsroom record, while the final feed is
    ordered by verified publication time so new announcements are considered first.
    A blocked source remains a diagnostic result, not a reason to suppress the others.
    """
    complete_rows, complete_attempts = discover_complete_official_posts(
        config_path=config_path,
        session=session,
    )

    seed_urls: tuple[str, ...] = ()
    try:
        config_payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        configured_sources = config_payload.get("official_sources", []) if isinstance(config_payload, dict) else []
        x_source = next((
            row for row in configured_sources
            if isinstance(row, dict) and row.get("id") == "mob_x" and row.get("enabled") is True
        ), {})
        raw_seed_urls = x_source.get("seed_urls", []) if isinstance(x_source, dict) else []
        if isinstance(raw_seed_urls, list):
            seed_urls = tuple(
                value.strip() for value in raw_seed_urls
                if isinstance(value, str) and value.strip().startswith("https://")
            )
    except (OSError, json.JSONDecodeError):
        seed_urls = ()

    try:
        # Import locally to keep the official adapters independently testable.
        from .termux_mob_x import discover_mob_x_posts
        x_rows, x_attempts = discover_mob_x_posts(
            max_results=max(1, min(int(max_results), 25)),
            seed_urls=seed_urls,
        )
    except Exception as exc:
        x_rows = []
        x_attempts = [{
            "source": "mob_x",
            "result": "source_error",
            "error_code": re.sub(r"[^A-Za-z0-9_-]", "", type(exc).__name__)[:60] or "source_error",
            "candidate_count": 0,
        }]

    # X follows the primary newsroom but comes before YouTube/Steam so it cannot
    # be squeezed out of the bounded report when those feeds return many older posts.
    source_priority = {"website": 0, "x": 1, "youtube": 2, "steam": 3}
    combined = [
        row for row in [*complete_rows, *x_rows]
        if isinstance(row, dict)
        and row.get("platform") == "mob_official"
        and row.get("official_source") is True
        and str(row.get("canonical_url") or "").startswith("https://")
        and str(row.get("original_caption") or row.get("caption") or "").strip()
        and str(row.get("media_url") or "").strip()
        and row.get("media_type") in {"image", "video"}
    ]

    def published_epoch(row: dict[str, Any]) -> float:
        value = _timestamp(row.get("published_at"))
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0.0

    # Deduplicate in source-priority order, then present the newest verified posts first.
    combined.sort(
        key=lambda row: (
            source_priority.get(str(row.get("source_platform") or ""), 9),
            -published_epoch(row),
            str(row.get("external_id") or row.get("id") or ""),
        )
    )
    output: list[dict[str, Any]] = []
    seen_identity: set[tuple[str, str]] = set()
    seen_urls: set[str] = set()
    seen_fingerprints: set[str] = set()
    for row in combined:
        source_platform = str(row.get("source_platform") or "")
        external_id = str(row.get("external_id") or row.get("id") or "")
        canonical = str(row.get("canonical_url") or "")
        identity = (source_platform, external_id)
        fingerprints = _fingerprints(row)
        if identity in seen_identity or canonical in seen_urls:
            continue
        if fingerprints.intersection(seen_fingerprints):
            continue
        seen_identity.add(identity)
        seen_urls.add(canonical)
        seen_fingerprints.update(fingerprints)
        output.append(row)

    # Newest verified post first within each lane; reserve room for X before
    # applying the global limit, then backfill from healthy fallback sources.
    output.sort(
        key=lambda row: (
            int(row.get("source_priority", source_priority.get(str(row.get("source_platform") or ""), 9))),
            0 if published_epoch(row) > 0 else 1,
            -published_epoch(row),
            str(row.get("external_id") or row.get("id") or ""),
        )
    )
    limit = max(1, min(int(max_results), 25))
    lane_quotas = {"website": 1, "x": 5, "youtube": 4, "steam": 2}
    lane_counts: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    selected_keys: set[tuple[str, str]] = set()
    for row in output:
        lane = str(row.get("source_platform") or "")
        if lane_counts.get(lane, 0) >= lane_quotas.get(lane, 0):
            continue
        key = (lane, str(row.get("external_id") or row.get("id") or ""))
        selected.append(row)
        selected_keys.add(key)
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        for row in output:
            lane = str(row.get("source_platform") or "")
            key = (lane, str(row.get("external_id") or row.get("id") or ""))
            if key in selected_keys:
                continue
            selected.append(row)
            selected_keys.add(key)
            lane_counts[lane] = lane_counts.get(lane, 0) + 1
            if len(selected) >= limit:
                break

    attempts = [*x_attempts, *complete_attempts]
    attempts.append({
        "source": "official_source_merge",
        "result": "ok" if selected else "no_complete_official_posts",
        "candidate_count": len(selected),
        "lane_counts": lane_counts,
        "reserved_lane_quotas": lane_quotas,
        "enabled_lanes": ["website", "x", "youtube", "steam"],
    })
    return selected, attempts
