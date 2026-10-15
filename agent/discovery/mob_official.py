"""Metadata-only discovery for Mob Entertainment's publicly linked official sources.

This module does not download or publish media. It records canonical post URLs,
original caption/title, available timestamps, and direct thumbnail/article media
references for a later rights-aware posting stage. Failed sources are non-fatal.
"""
from __future__ import annotations

import argparse
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
from xml.etree import ElementTree as ET

import requests


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "mob_official_sources.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "mob-official-discovery.json"
USER_AGENT = "RubikaAgent-MobOfficialDiscovery/1.0 (+metadata-only)"
MAX_SOURCE_BODY_BYTES = 3 * 1024 * 1024
MAX_ITEMS_PER_SOURCE = 25
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
DATE_FORMATS = ("%Y%m%d", "%Y-%m-%d")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if re.fullmatch(r"\d{10}", raw):
        try:
            return datetime.fromtimestamp(int(raw), tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _plain_text(value: Any, limit: int = 5000) -> str:
    if not isinstance(value, str):
        return ""
    result = re.sub(r"<[^>]*>", " ", value)
    result = unescape(result)
    result = re.sub(r"\s+", " ", result).strip()
    return result[:limit]


def _absolute_http_url(base: str, value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    absolute = urljoin(base, value)
    try:
        parsed = urlparse(absolute)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return absolute


def _candidate(
    source: dict[str, Any],
    *,
    canonical_url: str,
    external_id: str,
    title: str = "",
    caption: str = "",
    published_at: Any = None,
    media_type: str = "unknown",
    media_url: str | None = None,
    thumbnail_url: str | None = None,
    method: str,
) -> dict[str, Any]:
    source_name = str(source.get("source_name") or source.get("id") or "Official source")
    source_platform = str(source.get("source_platform") or "website")
    stable_id = external_id.strip() or hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:24]
    return {
        "platform": "mob_official",
        "source_platform": source_platform,
        "source_id": str(source.get("id") or "")[:100],
        "source_name": source_name[:160],
        "source_key": str(source.get("url") or "")[:300],
        "id": f"{source_platform}:{stable_id}"[:160],
        "url": canonical_url,
        "canonical_url": canonical_url,
        "title": _plain_text(title, 1200),
        "caption": _plain_text(caption, 5000),
        "original_caption": _plain_text(caption, 5000),
        "published_at": _timestamp(published_at),
        "media_type": media_type if media_type in {"video", "image", "audio", "carousel", "unknown"} else "unknown",
        "media_url": media_url,
        "thumbnail_url": thumbnail_url,
        "official_source": True,
        "method": method,
    }


class _AnchorParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.anchors: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self.meta: dict[str, str] = {}
        self._in_title = False
        self.title_text = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a" and values.get("href"):
            self._current = {
                "url": urljoin(self.base_url, values["href"]),
                "text": "",
                "image_url": "",
            }
            self.anchors.append(self._current)
        elif tag.lower() == "img" and self._current and values.get("src"):
            self._current["image_url"] = urljoin(self.base_url, values["src"])
        elif tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").strip().lower()
            value = values.get("content", "").strip()
            if key and value and key not in self.meta:
                self.meta[key] = value
        elif tag.lower() == "title":
            self._in_title = True

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"] += data
        if self._in_title:
            self.title_text += data

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self._current = None
        elif tag.lower() == "title":
            self._in_title = False


def _fetch_text(session: Any, url: str, *, timeout_seconds: int = 15) -> str:
    response = session.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/xml, text/html;q=0.9, */*;q=0.5"},
        timeout=timeout_seconds,
        allow_redirects=True,
    )
    if response.status_code != 200:
        raise RuntimeError("source_http_" + str(response.status_code))
    body = response.content
    if len(body) > MAX_SOURCE_BODY_BYTES:
        raise RuntimeError("source_response_too_large")
    encoding = getattr(response, "encoding", None) or "utf-8"
    return body.decode(encoding, errors="replace") if isinstance(body, bytes) else str(response.text)


def _atom_entries(body: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    root = ET.fromstring(body)
    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }
    output: list[dict[str, Any]] = []
    for entry in root.findall(".//atom:entry", ns)[:MAX_ITEMS_PER_SOURCE]:
        title = _plain_text(entry.findtext("atom:title", default="", namespaces=ns), 1200)
        link_value = ""
        for link in entry.findall("atom:link", ns):
            if link.get("rel") in {None, "alternate"} and link.get("href"):
                link_value = link.get("href", "")
                break
        canonical = _absolute_http_url(str(source.get("url") or ""), link_value)
        if not canonical:
            continue
        video_id = entry.findtext("yt:videoId", default="", namespaces=ns)
        external_id = video_id if YOUTUBE_ID_RE.fullmatch(video_id or "") else canonical.rstrip("/").rsplit("/", 1)[-1]
        caption = _plain_text(entry.findtext("media:group/media:description", default="", namespaces=ns), 5000)
        if not caption:
            caption = _plain_text(entry.findtext("atom:summary", default="", namespaces=ns), 5000)

        media_url = None
        for link in entry.findall("atom:link", ns):
            if link.get("rel") == "enclosure" and link.get("href"):
                media_url = _absolute_http_url(canonical, link.get("href"))
                break
        if not media_url:
            media_group = entry.find("media:group", ns)
            if media_group is not None:
                media_content = media_group.find("media:content", ns)
                if media_content is not None:
                    media_url = _absolute_http_url(canonical, media_content.get("url"))
        thumbnail = None
        media_group = entry.find("media:group", ns)
        if media_group is not None:
            thumb = media_group.find("media:thumbnail", ns)
            if thumb is not None:
                thumbnail = _absolute_http_url(canonical, thumb.get("url"))

        video = bool(video_id and YOUTUBE_ID_RE.fullmatch(video_id))
        item = _candidate(
            source,
            canonical_url=canonical,
            external_id=external_id,
            title=title,
            caption=caption,
            published_at=entry.findtext("atom:published", default="", namespaces=ns)
                or entry.findtext("atom:updated", default="", namespaces=ns),
            media_type="video" if video else ("image" if media_url and media_url.lower().split("?", 1)[0].endswith((".jpg", ".jpeg", ".png", ".webp")) else "unknown"),
            media_url=media_url,
            thumbnail_url=thumbnail,
            method="official_atom_feed",
        )
        output.append(item)
    return output


def _newsroom_entries(body: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    base = str(source.get("url") or "https://mobentertainment.com/")
    parser = _AnchorParser(base)
    parser.feed(body)
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    base_host = (urlparse(base).hostname or "").lower()
    reject_names = {
        "studio", "games", "careers", "about", "view more", "learn more",
        "download press kit", "official store", "youtube", "tiktok", "instagram",
        "facebook", "x / twitter", "linkedin",
    }
    for anchor in parser.anchors:
        canonical = _absolute_http_url(base, anchor.get("url"))
        title = _plain_text(anchor.get("text", ""), 1200)
        if not canonical or canonical in seen:
            continue
        parsed = urlparse(canonical)
        host = (parsed.hostname or "").lower()
        if host != base_host and not host.endswith("." + base_host):
            continue
        if parsed.path.rstrip("/") in {"", "/studio", "/games", "/careers", "/about"}:
            continue
        if title.casefold() in reject_names or len(title) < 18:
            continue
        if not any(word in (title + " " + parsed.path).casefold() for word in (
            "news", "press", "announce", "escape", "poppy", "chapter", "update", "playtime", "patch"
        )):
            continue
        seen.add(canonical)
        image_url = _absolute_http_url(canonical, anchor.get("image_url"))
        output.append(_candidate(
            source,
            canonical_url=canonical,
            external_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
            title=title,
            # These are cards from the homepage, not fetched article pages. Do not
            # mislabel the homepage description or its page-level date as each post's caption/time.
            caption="",
            published_at=None,
            media_type="image" if image_url else "unknown",
            media_url=image_url,
            thumbnail_url=image_url,
            method="official_newsroom_html",
        ))
        if len(output) >= MAX_ITEMS_PER_SOURCE:
            break
    return output


def _ytdlp_profile_entries(source: dict[str, Any], *, max_items: int = 15) -> tuple[list[dict[str, Any]], str]:
    try:
        import yt_dlp
    except ImportError:
        return [], "yt_dlp_not_installed"

    options = {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": max(1, min(max_items, 25)),
        "socket_timeout": 15,
        "retries": 0,
        "extractor_retries": 0,
        "noplaylist": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(str(source.get("url") or ""), download=False)
    except Exception as exc:
        name = type(exc).__name__
        return [], "profile_fetch_" + re.sub(r"[^A-Za-z0-9_-]", "", name)[:50]

    rows: list[dict[str, Any]] = []
    entries = info.get("entries", []) if isinstance(info, dict) else []
    if not isinstance(entries, list):
        return [], "profile_entries_unavailable"
    source_platform = str(source.get("source_platform") or "unknown")
    for entry in entries[:max_items]:
        if not isinstance(entry, dict):
            continue
        external_id = str(entry.get("id") or "").strip()
        if not external_id:
            continue
        url = entry.get("webpage_url") or entry.get("original_url") or entry.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            if source_platform == "tiktok":
                handle = str(source.get("url", "")).rstrip("/").rsplit("@", 1)[-1]
                url = f"https://www.tiktok.com/@{handle}/video/{external_id}"
            elif source_platform == "instagram":
                url = "https://www.instagram.com/p/" + external_id.strip("/") + "/"
            else:
                continue
        caption = str(entry.get("description") or entry.get("title") or "")
        stamp = entry.get("timestamp") or entry.get("upload_date")
        if isinstance(stamp, (int, float)):
            stamp = datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
        elif isinstance(stamp, str) and re.fullmatch(r"\d{8}", stamp):
            stamp = stamp
        item = _candidate(
            source,
            canonical_url=url,
            external_id=external_id,
            title=str(entry.get("title") or ""),
            caption=caption,
            published_at=stamp,
            media_type="video" if source_platform == "tiktok" else "unknown",
            media_url=None,
            thumbnail_url=_absolute_http_url(url, entry.get("thumbnail")),
            method="official_profile_metadata",
        )
        rows.append(item)
    return rows, "ok" if rows else "profile_no_public_entries"


def _fingerprint(item: dict[str, Any]) -> str:
    raw = str(item.get("caption") or item.get("title") or "").casefold()
    raw = unicodedata.normalize("NFKC", raw)
    raw = re.sub(r"[^\w]+", " ", raw, flags=re.UNICODE)
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) < 18:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def mark_crossposts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate identical captions with the earliest verifiable source, if comparable."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        fingerprint = _fingerprint(item)
        item["caption_fingerprint"] = fingerprint or None
        if fingerprint:
            groups.setdefault(fingerprint, []).append(item)

    for fingerprint, group in groups.items():
        if len(group) < 2:
            continue
        dated = [
            (datetime.fromisoformat(item["published_at"].replace("Z", "+00:00")), item)
            for item in group
            if isinstance(item.get("published_at"), str) and _timestamp(item["published_at"])
        ]
        if len(dated) >= 2:
            dated.sort(key=lambda pair: pair[0])
            first = dated[0][1]
            for item in group:
                item["crosspost_group"] = fingerprint
                item["first_published_source"] = first.get("source_name")
                item["first_published_url"] = first.get("canonical_url")
                item["first_source_confidence"] = "timestamp_comparison"
        else:
            for item in group:
                item["crosspost_group"] = fingerprint
                item["first_published_source"] = None
                item["first_published_url"] = None
                item["first_source_confidence"] = "unknown_insufficient_timestamps"
    return items


def discover_mob_official(
    *,
    config_path: str | Path = DEFAULT_CONFIG,
    session: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect a bounded report. A failed or blocked source never stops other sources."""
    try:
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], [{"source": "config", "result": "config_unavailable"}]
    if not isinstance(config, dict) or config.get("version") != 1:
        return [], [{"source": "config", "result": "config_invalid"}]

    client = session or requests.Session()
    results: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for source in config.get("official_sources", []):
        if not isinstance(source, dict) or source.get("enabled") is not True:
            continue
        kind = str(source.get("kind") or "")
        source_id = str(source.get("id") or "unknown")
        try:
            if kind == "profile_reference":
                attempts.append({"source": source_id, "result": "configured_reference_only"})
                continue
            if kind == "yt_dlp_profile":
                found, result = _ytdlp_profile_entries(source)
            else:
                body = _fetch_text(client, str(source.get("url") or ""))
                if kind == "atom":
                    found, result = _atom_entries(body, source), "ok"
                elif kind == "youtube_atom":
                    feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=" + str(source.get("channel_id") or "")
                    feed_source = dict(source)
                    feed_source["url"] = feed_url
                    found, result = _atom_entries(_fetch_text(client, feed_url), feed_source), "ok"
                elif kind == "html_newsroom":
                    found, result = _newsroom_entries(body, source), "ok"
                else:
                    found, result = [], "unsupported_source_kind"
            results.extend(found)
            attempts.append({"source": source_id, "result": result, "candidate_count": len(found)})
        except Exception as exc:
            attempts.append({
                "source": source_id,
                "result": "source_error",
                "error_code": re.sub(r"[^A-Za-z0-9_-]", "", type(exc).__name__)[:60] or "source_error",
                "candidate_count": 0,
            })

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in results:
        key = (str(item.get("source_platform")), str(item.get("canonical_url")))
        unique.setdefault(key, item)
    candidates = mark_crossposts(list(unique.values()))
    report_attempt = {"source": "crosspost_dedupe", "result": "ok", "candidate_count": len(candidates)}
    attempts.append(report_attempt)
    return candidates, attempts


def write_report(
    output_path: str | Path = DEFAULT_OUTPUT,
    *,
    config_path: str | Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    candidates, attempts = discover_mob_official(config_path=config_path)
    report = {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "mode": "official_metadata_only_no_download_no_publish",
        "summary": {
            "candidate_count": len(candidates),
            "source_attempts": len(attempts),
            "candidate_sources": sorted({str(row.get("source_platform")) for row in candidates}),
            "ready_to_publish": False,
        },
        "candidates": candidates,
        "attempts": attempts,
    }
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    report = write_report(args.output, config_path=args.config)
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
