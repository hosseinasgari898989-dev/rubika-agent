"""Tavily-powered public URL discovery fallback for TikTok and Instagram.

Uses Tavily's official Search API only. This module discovers canonical public
post URLs and metadata; it never downloads, moderates, approves, or publishes media.
The free plan is optional: without TAVILY_API_KEY, it records NOT_CONFIGURED and
makes no request. Search runs are intentionally bounded to two queries/platform.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping
from urllib.parse import urlparse

import requests

from . import v7
from .instagram import extract_instagram_urls


API_ENDPOINT = "https://api.tavily.com/search"
QUERIES: dict[str, tuple[str, ...]] = {
    "tiktok": (
        "Poppy Playtime Huggy Wuggy CatNap fan edit",
        "Poppy Playtime velocity edit Smiling Critters",
    ),
    "instagram": (
        "Poppy Playtime Huggy Wuggy CatNap fan edit Reel",
        "Poppy Playtime velocity edit Smiling Critters Reel",
    ),
}
DOMAINS = {
    "tiktok": ["tiktok.com"],
    "instagram": ["instagram.com"],
}


@dataclass(frozen=True)
class TavilyCandidate:
    platform: str
    external_id: str
    canonical_url: str
    title: str
    source_key: str
    discovery_method: str
    discovery_query: str
    media_type: str = "unknown"


@dataclass(frozen=True)
class TavilyAttempt:
    platform: str
    query: str
    http_status: int | None
    parsed_count: int
    new_unique_count: int
    credits_used: int | None
    result: str


@dataclass(frozen=True)
class TavilyDiscoveryReport:
    candidates: tuple[TavilyCandidate, ...]
    attempts: tuple[TavilyAttempt, ...]

    def report(self) -> dict[str, Any]:
        return {
            "provider": "tavily_search_api",
            "candidate_count": len(self.candidates),
            "candidates": [asdict(item) for item in self.candidates],
            "attempts": [asdict(item) for item in self.attempts],
        }


def _valid_social_host(platform: str, url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    allowed = DOMAINS.get(platform, [])
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and any(host == domain or host.endswith("." + domain) for domain in allowed)
    )


def _extract_result(platform: str, url: str, title: str, query: str) -> list[TavilyCandidate]:
    """Parse only the result's own URL, not arbitrary links from its snippet."""
    if not _valid_social_host(platform, url):
        return []
    if platform == "tiktok":
        rows = v7.extract_tiktok_urls(url)
        output = []
        for row in rows:
            external_id = str(row.get("id") or "")
            canonical = str(row.get("url") or "")
            author = str(row.get("author") or "")
            if not re.fullmatch(r"\d{10,25}", external_id) or not canonical:
                continue
            output.append(TavilyCandidate(
                platform="tiktok",
                external_id=external_id,
                canonical_url=canonical,
                title=title[:500],
                source_key="@" + author if author else "",
                discovery_method="tavily_search_api",
                discovery_query=query,
                media_type="video",
            ))
        return output

    if platform == "instagram":
        rows = extract_instagram_urls(url)
        output = []
        for row in rows:
            external_id = str(row.get("id") or "")
            canonical = str(row.get("url") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{5,20}", external_id) or not canonical:
                continue
            output.append(TavilyCandidate(
                platform="instagram",
                external_id=external_id,
                canonical_url=canonical,
                title=title[:500],
                source_key="",
                discovery_method="tavily_search_api",
                discovery_query=query,
                media_type="unknown",
            ))
        return output
    return []


def discover_social_urls(
    platform: str,
    *,
    api_key: str | None = None,
    session: Any | None = None,
    timeout_seconds: int = 12,
    max_results: int = 15,
    max_queries: int = 2,
    sleep_seconds: float = 0.15,
) -> TavilyDiscoveryReport:
    """Search a bounded number of queries, enforce platform domains, and dedupe IDs."""
    if platform not in QUERIES:
        raise ValueError("unsupported_tavily_platform")
    bounded_results = max(1, min(int(max_results), 30))
    bounded_queries = max(1, min(int(max_queries), len(QUERIES[platform])))
    key = (api_key if api_key is not None else os.getenv("TAVILY_API_KEY", "")).strip()
    candidates: dict[str, TavilyCandidate] = {}
    attempts: list[TavilyAttempt] = []

    if not key:
        return TavilyDiscoveryReport(
            candidates=(),
            attempts=(TavilyAttempt(platform, "", None, 0, 0, 0, "not_configured"),),
        )
    if len(key) > 512 or any(ch.isspace() for ch in key):
        return TavilyDiscoveryReport(
            candidates=(),
            attempts=(TavilyAttempt(platform, "", None, 0, 0, 0, "invalid_api_key_format"),),
        )

    client = session or requests.Session()
    for query in QUERIES[platform][:bounded_queries]:
        if len(candidates) >= bounded_results:
            break
        try:
            response = client.post(
                API_ENDPOINT,
                headers={
                    "Authorization": "Bearer " + key,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "Rubika-Agent/1.0 (public URL discovery)",
                },
                json={
                    "query": query,
                    "search_depth": "basic",
                    "topic": "general",
                    "max_results": 10,
                    "include_domains": DOMAINS[platform],
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": False,
                    "include_usage": True,
                },
                timeout=timeout_seconds,
            )
            status = int(response.status_code)
            if status != 200:
                mapping = {
                    401: "invalid_api_key",
                    403: "provider_forbidden",
                    429: "provider_rate_limited",
                    432: "provider_quota_exhausted",
                    433: "provider_spend_limit",
                }
                attempts.append(TavilyAttempt(
                    platform, query, status, 0, 0, None,
                    mapping.get(status, "provider_http_error"),
                ))
                # A provider-wide auth/quota/rate limit will not improve on a second query.
                if status in {401, 403, 429, 432, 433}:
                    break
                continue

            try:
                payload = response.json()
            except (ValueError, TypeError):
                attempts.append(TavilyAttempt(platform, query, status, 0, 0, None, "invalid_json"))
                continue
            if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
                attempts.append(TavilyAttempt(platform, query, status, 0, 0, None, "invalid_result_shape"))
                continue

            usage = payload.get("usage")
            credits = usage.get("credits") if isinstance(usage, Mapping) else None
            if not isinstance(credits, int) or credits < 0:
                credits = None
            added = 0
            parsed_count = 0
            for row in payload["results"]:
                if not isinstance(row, Mapping):
                    continue
                url = row.get("url")
                title = row.get("title")
                if not isinstance(url, str) or not isinstance(title, str):
                    continue
                parsed = _extract_result(platform, url, title, query)
                parsed_count += len(parsed)
                for candidate in parsed:
                    if candidate.external_id in candidates:
                        continue
                    candidates[candidate.external_id] = candidate
                    added += 1
                    if len(candidates) >= bounded_results:
                        break
                if len(candidates) >= bounded_results:
                    break

            attempts.append(TavilyAttempt(
                platform, query, status, parsed_count, added, credits,
                "canonical_links_found" if parsed_count else "no_canonical_links",
            ))
            time.sleep(max(0.0, sleep_seconds))
        except requests.Timeout:
            attempts.append(TavilyAttempt(platform, query, None, 0, 0, None, "request_timeout"))
        except requests.RequestException:
            attempts.append(TavilyAttempt(platform, query, None, 0, 0, None, "request_failed"))
        except (TypeError, ValueError):
            attempts.append(TavilyAttempt(platform, query, None, 0, 0, None, "response_processing_failed"))

    return TavilyDiscoveryReport(
        candidates=tuple(candidates.values()),
        attempts=tuple(attempts),
    )
