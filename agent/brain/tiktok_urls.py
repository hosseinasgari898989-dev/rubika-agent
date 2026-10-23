"""Canonical TikTok video URL validation and one-time public short-link resolution."""
from __future__ import annotations
import re
from typing import Any
from urllib.parse import urlparse
import requests

_VIDEO_PATH = re.compile(r"^/@([A-Za-z0-9._-]{1,40})/video/([0-9]{10,25})/?$")
_CANONICAL_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}
_SHORT_PATH = re.compile(r"^/t/[A-Za-z0-9_-]+/?$")


def _canonical_from_url(value: str) -> tuple[str, str, str] | None:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return None
        if parsed.port not in (None, 443) or host not in _CANONICAL_HOSTS:
            return None
        match = _VIDEO_PATH.fullmatch(parsed.path)
    except ValueError:
        return None
    if not match:
        return None
    handle, video_id = match.groups()
    return handle, video_id, f"https://www.tiktok.com/@{handle}/video/{video_id}"


def normalize_tiktok_url(value: str, *, session: Any | None = None, timeout_seconds: int = 8) -> tuple[str, str, str] | None:
    """Return (handle, numeric video ID, tracking-free URL); resolve supported short links once."""
    raw = str(value or "").strip()
    if not raw:
        return None
    direct = _canonical_from_url(raw)
    if direct:
        return direct
    try:
        parsed = urlparse(raw)
        host = (parsed.hostname or "").casefold().rstrip(".")
        is_supported_short = parsed.scheme == "https" and (
            host in _SHORT_HOSTS or (host in _CANONICAL_HOSTS and _SHORT_PATH.fullmatch(parsed.path) is not None)
        )
        if parsed.username or parsed.password or parsed.port not in (None, 443) or not is_supported_short:
            return None
    except ValueError:
        return None
    client = session if session is not None else requests.Session()
    try:
        response = client.get(raw, timeout=timeout_seconds, allow_redirects=True)
    except requests.RequestException:
        return None
    final_url = getattr(response, "url", "")
    return _canonical_from_url(final_url) if isinstance(final_url, str) else None
