"""Instagram public-index discovery adapter.

Only parses canonical post/Reel links from ordinary public search responses. It
does not log into Instagram, inspect private profiles, bypass challenges, download
media, or publish anything.
"""
from __future__ import annotations

import html
import re
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import unquote

import requests

from . import v7


@dataclass(frozen=True)
class InstagramCandidate:
    platform: str
    external_id: str
    canonical_url: str
    post_type: str
    query: str
    method: str
    media_type: str = "unknown"


@dataclass(frozen=True)
class SearchAttempt:
    engine: str
    query: str
    http_status: int | None
    canonical_count: int
    new_unique_count: int
    result: str


@dataclass(frozen=True)
class InstagramDiscovery:
    candidates: tuple[InstagramCandidate, ...]
    attempts: tuple[SearchAttempt, ...]

    def report(self) -> dict[str, Any]:
        return {
            "platform": "instagram",
            "candidate_count": len(self.candidates),
            "candidates": [asdict(candidate) for candidate in self.candidates],
            "attempts": [asdict(attempt) for attempt in self.attempts],
        }


def extract_instagram_urls(body: str) -> list[dict[str, str]]:
    """Return unique canonical Reel/Post/TV links; navigation links are excluded."""
    expanded: list[str] = []
    current = html.unescape(body or "")
    current = (
        current.replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\/", "/")
        .replace("\\x2F", "/")
        .replace("\\x2f", "/")
    )
    seen_versions: set[str] = set()
    for _ in range(5):
        if current in seen_versions:
            break
        expanded.append(current)
        seen_versions.add(current)
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded

    pattern = re.compile(
        r"(?:https?://)?(?:www\.)?instagram\.com/(reel|p|tv)/([A-Za-z0-9_-]{5,20})",
        re.IGNORECASE,
    )
    found: dict[str, dict[str, str]] = {}
    for text in expanded:
        for match in pattern.finditer(text):
            post_type = match.group(1).lower()
            shortcode = match.group(2)
            found[shortcode] = {
                "id": shortcode,
                "post_type": post_type,
                "url": f"https://www.instagram.com/{post_type}/{shortcode}/",
            }
    return list(found.values())


def discover_public_indexed_urls(
    *,
    session: requests.Session | None = None,
    timeout_seconds: int = 8,
    max_results: int = 25,
    sleep_seconds: float = 0.25,
) -> InstagramDiscovery:
    """Search public indexes once per engine, keep honest status/errors, dedupe IDs."""
    client = session or v7.session
    query = "Poppy Playtime Huggy Wuggy CatNap edit site:instagram.com"
    candidates: dict[str, InstagramCandidate] = {}
    attempts: list[SearchAttempt] = []

    for engine, endpoint in v7.ENGINES:
        if len(candidates) >= max_results:
            break
        try:
            params = {"q": query, "num": 20}
            if "duckduckgo" in endpoint:
                params.update({"kl": "us-en", "t": "h_"})
            response = client.get(
                endpoint,
                params=params,
                timeout=timeout_seconds,
                allow_redirects=True,
            )
            status = int(response.status_code)
            if status in (403, 429):
                attempts.append(SearchAttempt(engine, query, status, 0, 0, "access_denied"))
                continue
            if status not in (200, 202):
                attempts.append(SearchAttempt(engine, query, status, 0, 0, "unexpected_http_status"))
                continue

            extracted = extract_instagram_urls(response.text or "")
            added = 0
            for item in extracted:
                code = item["id"]
                if code in candidates:
                    continue
                candidates[code] = InstagramCandidate(
                    platform="instagram",
                    external_id=code,
                    canonical_url=item["url"],
                    post_type=item["post_type"],
                    query=query,
                    method=engine,
                )
                added += 1
                if len(candidates) >= max_results:
                    break
            outcome = (
                "canonical_links_parsed" if extracted
                else "no_canonical_links_in_response"
            )
            attempts.append(
                SearchAttempt(engine, query, status, len(extracted), added, outcome)
            )
            time.sleep(max(0.0, sleep_seconds))
        except requests.RequestException:
            attempts.append(SearchAttempt(engine, query, None, 0, 0, "request_failed"))

    return InstagramDiscovery(
        candidates=tuple(candidates.values()),
        attempts=tuple(attempts),
    )
