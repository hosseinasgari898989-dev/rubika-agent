"""Instagram public-index discovery adapter.

Only parses canonical post/Reel links from ordinary public search responses. It
does not log into Instagram, inspect private profiles, bypass challenges, download
media, or publish anything.
"""
from __future__ import annotations

import html
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import unquote, urlparse

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


GRAPH_API_BASE = "https://graph.facebook.com"
DEFAULT_GRAPH_API_VERSION = "v26.0"
DEFAULT_GRAPH_HASHTAGS = ("poppyplaytime", "huggywuggy", "catnap", "smilingcritters")


def discover_official_hashtag_urls(
    *,
    access_token: str | None = None,
    ig_user_id: str | None = None,
    session: Any | None = None,
    api_version: str | None = None,
    hashtags: tuple[str, ...] = DEFAULT_GRAPH_HASHTAGS,
    timeout_seconds: int = 10,
    max_results: int = 25,
    sleep_seconds: float = 0.15,
) -> InstagramDiscovery:
    """Discover public hashtagged media through Meta's official Instagram Graph API.

    This path requires an eligible Professional Instagram account, the relevant app
    permissions, and credentials supplied outside source control. It never logs or
    returns the token, and it only accepts canonical public Instagram permalinks.
    """
    token = (
        access_token if access_token is not None
        else os.getenv("INSTAGRAM_GRAPH_ACCESS_TOKEN", "")
    ).strip()
    user_id = (
        ig_user_id if ig_user_id is not None
        else os.getenv("INSTAGRAM_GRAPH_USER_ID", "")
    ).strip()
    version = (
        api_version if api_version is not None
        else os.getenv("INSTAGRAM_GRAPH_API_VERSION", DEFAULT_GRAPH_API_VERSION)
    ).strip()

    if not token or not user_id:
        return InstagramDiscovery(
            candidates=(),
            attempts=(SearchAttempt(
                "Instagram Graph API", "(credentials)", None, 0, 0,
                "not_configured",
            ),),
        )
    if (
        len(token) > 4096
        or any(ch.isspace() for ch in token)
        or not re.fullmatch(r"\d{1,30}", user_id)
        or not re.fullmatch(r"v\d+\.\d+", version)
    ):
        return InstagramDiscovery(
            candidates=(),
            attempts=(SearchAttempt(
                "Instagram Graph API", "(configuration)", None, 0, 0,
                "invalid_configuration",
            ),),
        )

    limit = max(1, min(int(max_results), 50))
    client = session or requests.Session()
    candidates: dict[str, InstagramCandidate] = {}
    attempts: list[SearchAttempt] = []

    for raw_tag in hashtags:
        if len(candidates) >= limit:
            break
        tag = str(raw_tag or "").strip().removeprefix("#").lower()
        if not re.fullmatch(r"[a-z0-9_]{2,64}", tag):
            attempts.append(SearchAttempt(
                "Instagram Graph API", "#" + tag[:64], None, 0, 0,
                "invalid_hashtag",
            ))
            continue

        query = "#" + tag
        try:
            search = client.get(
                f"{GRAPH_API_BASE}/{version}/ig_hashtag_search",
                params={
                    "user_id": user_id,
                    "q": tag,
                    "fields": "id,name",
                    "access_token": token,
                },
                timeout=timeout_seconds,
            )
        except requests.RequestException:
            attempts.append(SearchAttempt(
                "Instagram Graph API", query, None, 0, 0,
                "hashtag_search_request_failed",
            ))
            continue

        status = int(search.status_code)
        if status != 200:
            attempts.append(SearchAttempt(
                "Instagram Graph API", query, status, 0, 0,
                f"hashtag_search_http_{status}",
            ))
            if status in {400, 401, 403, 429}:
                break
            continue
        try:
            search_payload = search.json()
        except (ValueError, TypeError):
            attempts.append(SearchAttempt(
                "Instagram Graph API", query, status, 0, 0,
                "hashtag_search_invalid_json",
            ))
            continue
        rows = search_payload.get("data") if isinstance(search_payload, dict) else None
        hashtag_id = next((
            str(row.get("id") or "").strip()
            for row in (rows if isinstance(rows, list) else [])
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        ), "")
        if not hashtag_id:
            attempts.append(SearchAttempt(
                "Instagram Graph API", query, status, 0, 0,
                "hashtag_not_found",
            ))
            time.sleep(max(0.0, sleep_seconds))
            continue

        try:
            media_response = client.get(
                f"{GRAPH_API_BASE}/{version}/{hashtag_id}/recent_media",
                params={
                    "user_id": user_id,
                    "fields": "id,caption,media_type,permalink,timestamp",
                    "limit": 50,
                    "access_token": token,
                },
                timeout=timeout_seconds,
            )
        except requests.RequestException:
            attempts.append(SearchAttempt(
                "Instagram Graph API", query, None, 0, 0,
                "recent_media_request_failed",
            ))
            continue

        status = int(media_response.status_code)
        if status != 200:
            attempts.append(SearchAttempt(
                "Instagram Graph API", query, status, 0, 0,
                f"recent_media_http_{status}",
            ))
            if status in {400, 401, 403, 429}:
                break
            continue
        try:
            media_payload = media_response.json()
        except (ValueError, TypeError):
            attempts.append(SearchAttempt(
                "Instagram Graph API", query, status, 0, 0,
                "recent_media_invalid_json",
            ))
            continue

        media_rows = media_payload.get("data") if isinstance(media_payload, dict) else None
        parsed_count = 0
        added = 0
        for row in media_rows if isinstance(media_rows, list) else []:
            if not isinstance(row, dict):
                continue
            media_type = str(row.get("media_type") or "").upper()
            if media_type not in {"IMAGE", "VIDEO", "CAROUSEL_ALBUM"}:
                continue
            permalink = row.get("permalink")
            if not isinstance(permalink, str):
                continue
            try:
                parsed_url = urlparse(permalink)
            except ValueError:
                continue
            if (
                parsed_url.scheme != "https"
                or parsed_url.hostname not in {"instagram.com", "www.instagram.com"}
            ):
                continue
            parsed_items = extract_instagram_urls(permalink)
            if not parsed_items:
                continue
            item = parsed_items[0]
            external_id = item["id"]
            parsed_count += 1
            if external_id in candidates:
                continue
            candidates[external_id] = InstagramCandidate(
                platform="instagram",
                external_id=external_id,
                canonical_url=item["url"],
                post_type=item["post_type"],
                query=query,
                method="instagram_graph_api_recent_media",
                media_type=media_type.lower(),
            )
            added += 1
            if len(candidates) >= limit:
                break

        attempts.append(SearchAttempt(
            "Instagram Graph API", query, status, parsed_count, added,
            "canonical_links_found" if parsed_count else "no_canonical_media",
        ))
        time.sleep(max(0.0, sleep_seconds))

    return InstagramDiscovery(
        candidates=tuple(candidates.values()),
        attempts=tuple(attempts),
    )
