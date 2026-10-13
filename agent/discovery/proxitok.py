"""Public ProxiTok RSS discovery adapter.

Only consumes the documented public RSS routes. It does not log in, solve challenges,
call stream/download proxy routes, or fetch media. Public instances are best-effort.
"""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


DEFAULT_INSTANCES = (
    "https://proxitok.pabloferreiro.es",
    "https://tok.artemislena.eu",
    "https://tik.hostux.net",
    "https://tok.adminforge.de",
)
DEFAULT_TAGS = ("poppyplaytime", "huggywuggy", "catnap")
CREATOR_NS = "{http://purl.org/dc/elements/1.1/}creator"


@dataclass(frozen=True)
class TikTokRSSCandidate:
    platform: str
    external_id: str
    canonical_url: str
    title: str
    source_key: str
    discovery_method: str
    discovery_tag: str


@dataclass(frozen=True)
class RSSAttempt:
    instance: str
    tag: str
    status: int | None
    parsed_count: int
    added_count: int
    result: str


@dataclass(frozen=True)
class ProxiTokDiscovery:
    candidates: tuple[TikTokRSSCandidate, ...]
    attempts: tuple[RSSAttempt, ...]

    def report(self) -> dict[str, Any]:
        return {
            "provider": "proxitok_public_rss",
            "candidate_count": len(self.candidates),
            "candidates": [asdict(item) for item in self.candidates],
            "attempts": [asdict(item) for item in self.attempts],
        }


def _plain_text(value: str, limit: int = 500) -> str:
    no_tags = re.sub(r"<[^>]*>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(no_tags)).strip()[:limit]


def extract_rss_candidates(xml_body: str, *, tag: str, instance: str) -> list[TikTokRSSCandidate]:
    """Parse valid RSS items, converting only known video links to canonical TikTok URLs."""
    try:
        root = ET.fromstring(xml_body)
    except (ET.ParseError, ValueError):
        return []

    try:
        instance_host = urlparse(instance).hostname
    except ValueError:
        return []
    if not instance_host:
        return []

    results: dict[str, TikTokRSSCandidate] = {}
    for item in root.findall(".//item"):
        title = _plain_text(item.findtext("title") or "")
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        author_raw = (item.findtext(CREATOR_NS) or "").strip()
        author_raw = author_raw.removeprefix("@").strip()

        try:
            linked = urlparse(link)
            if linked.scheme and linked.hostname:
                if linked.hostname.lower() not in {instance_host.lower(), "www.tiktok.com", "tiktok.com"}:
                    continue
                path = linked.path
            else:
                path = link
        except ValueError:
            continue

        match = re.search(r"/@([A-Za-z0-9_.-]+)/video/(\d{10,25})(?:/|$)", path)
        if match:
            author = match.group(1)
            video_id = match.group(2)
        else:
            id_match = re.fullmatch(r"\d{10,25}", guid)
            if not id_match or not re.fullmatch(r"[A-Za-z0-9_.-]{2,40}", author_raw):
                continue
            author = author_raw
            video_id = guid

        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,40}", author):
            continue
        results[video_id] = TikTokRSSCandidate(
            platform="tiktok",
            external_id=video_id,
            canonical_url=f"https://www.tiktok.com/@{author}/video/{video_id}",
            title=title,
            source_key="@" + author,
            discovery_method="proxitok_public_rss",
            discovery_tag=tag,
        )
    return list(results.values())


def discover_public_rss(
    *,
    instances: tuple[str, ...] = DEFAULT_INSTANCES,
    tags: tuple[str, ...] = DEFAULT_TAGS,
    session: requests.Session | None = None,
    timeout_seconds: int = 8,
    max_results: int = 25,
    sleep_seconds: float = 0.2,
) -> ProxiTokDiscovery:
    """Read public tag feeds, deduplicate by video ID and report each instance outcome."""
    client = session or requests.Session()
    candidates: dict[str, TikTokRSSCandidate] = {}
    attempts: list[RSSAttempt] = []

    for instance in instances:
        base = instance.rstrip("/")
        parsed_base = urlparse(base)
        if parsed_base.scheme != "https" or not parsed_base.hostname:
            attempts.append(RSSAttempt(base[:120], "", None, 0, 0, "invalid_instance_url"))
            continue
        for tag in tags:
            if len(candidates) >= max_results:
                break
            if not re.fullmatch(r"[a-z0-9_]{2,40}", tag):
                attempts.append(RSSAttempt(base[:120], tag[:40], None, 0, 0, "invalid_tag"))
                continue
            endpoint = f"{base}/tag/{tag}/rss"
            try:
                response = client.get(
                    endpoint,
                    headers={"Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.2",
                             "User-Agent": "Rubika-Agent/1.0 (+public RSS discovery)"},
                    timeout=timeout_seconds,
                    allow_redirects=True,
                )
                final_host = urlparse(response.url).hostname
                if final_host and final_host.lower() != parsed_base.hostname.lower():
                    attempts.append(RSSAttempt(base[:120], tag, int(response.status_code), 0, 0, "cross_host_redirect_rejected"))
                    continue
                status = int(response.status_code)
                if status != 200:
                    attempts.append(RSSAttempt(base[:120], tag, status, 0, 0, "rss_unavailable"))
                    continue
                parsed = extract_rss_candidates(response.text or "", tag=tag, instance=base)
                added = 0
                for item in parsed:
                    if item.external_id in candidates:
                        continue
                    candidates[item.external_id] = item
                    added += 1
                    if len(candidates) >= max_results:
                        break
                attempts.append(RSSAttempt(
                    base[:120], tag, status, len(parsed), added,
                    "rss_parsed" if parsed else "rss_empty_or_unrecognized",
                ))
                time.sleep(max(0.0, sleep_seconds))
                # A successful tag feed with enough results is sufficient; avoid excess requests.
                if len(candidates) >= 5:
                    break
            except requests.RequestException:
                attempts.append(RSSAttempt(base[:120], tag, None, 0, 0, "request_failed"))
        if len(candidates) >= 5 or len(candidates) >= max_results:
            break

    return ProxiTokDiscovery(tuple(candidates.values()), tuple(attempts))
