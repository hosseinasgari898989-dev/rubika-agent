"""Authorized media download adapters for the staging orchestrator.

No adapter logs signed URLs or provider response bodies. Permission is checked before
network access; official/unsupported media types are rejected instead of guessed.
"""
from __future__ import annotations

import ipaddress
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .models import Candidate
from .relay_client import AuthorizedYouTubeRelay, RelayClientError


MAX_SOURCE_MEDIA_BYTES = 100 * 1024 * 1024
MAX_MEDIA_SECONDS = 180
ALLOWED_VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".ts"}


class DownloaderError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _validate_source_url(candidate: Candidate) -> None:
    domains = {
        "tiktok": {"tiktok.com"},
        "instagram": {"instagram.com"},
        "youtube": {"youtube.com", "youtu.be"},
    }
    allowed = domains.get(candidate.platform)
    if allowed is None or candidate.media_type not in {"video", "unknown"}:
        raise DownloaderError("unsupported_video_platform_or_type")
    try:
        parsed = urlparse(candidate.canonical_url)
        host = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        raise DownloaderError("invalid_source_url") from None
    if parsed.scheme != "https" or not host or not parsed.path:
        raise DownloaderError("https_source_url_required")
    if parsed.username or parsed.password or parsed.fragment:
        raise DownloaderError("noncanonical_source_url")
    if not any(host == domain or host.endswith("." + domain) for domain in allowed):
        raise DownloaderError("source_host_mismatch")
    # Block IP-literal hostnames in candidate URLs. Platform URLs should use domains.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise DownloaderError("ip_literal_source_rejected")


def _safe_generated_media_url(url: str, api_url: str) -> bool:
    """Permit Cobalt's own tunnel or HTTPS to a globally-routable public host."""
    try:
        media = urlparse(url)
        api = urlparse(api_url)
        host = (media.hostname or "").lower().rstrip(".")
        if not host or media.username or media.password or media.fragment:
            return False
        if media.scheme == api.scheme and media.netloc == api.netloc:
            return True
        if media.scheme != "https":
            return False
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return True
        return address.is_global
    except ValueError:
        return False


class CobaltDownloader:
    """Download public TikTok/Instagram URLs through a locally controlled Cobalt API."""

    def __init__(
        self,
        api_url: str = "http://127.0.0.1:9000/",
        *,
        session: Any | None = None,
        api_timeout_seconds: int = 45,
        stream_timeout_seconds: int = 90,
        max_bytes: int = MAX_SOURCE_MEDIA_BYTES,
    ) -> None:
        parsed = urlparse(api_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("cobalt_api_must_be_local_http")
        self.api_url = api_url if api_url.endswith("/") else api_url + "/"
        self.session = session or requests.Session()
        self.api_timeout_seconds = api_timeout_seconds
        self.stream_timeout_seconds = stream_timeout_seconds
        self.max_bytes = min(MAX_SOURCE_MEDIA_BYTES, max(1024, int(max_bytes)))

    def __call__(
        self,
        candidate: Candidate,
        output_dir: str | Path,
        permission_confirmed: bool,
    ) -> Path:
        if permission_confirmed is not True:
            raise DownloaderError("permission_confirmation_required")
        if candidate.platform not in {"tiktok", "instagram"}:
            raise DownloaderError("cobalt_platform_not_supported")
        _validate_source_url(candidate)
        destination_dir = Path(output_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", candidate.external_id)[:80]
        destination = destination_dir / f"{candidate.platform}_{safe_id}.mp4"
        partial = destination.with_suffix(".mp4.part")
        if destination.exists():
            raise DownloaderError("destination_already_exists")

        try:
            response = self.session.post(
                self.api_url,
                json={
                    "url": candidate.canonical_url,
                    "videoQuality": "720",
                    "filenameStyle": "basic",
                    "youtubeVideoCodec": "h264",
                },
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=self.api_timeout_seconds,
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise DownloaderError("cobalt_http_" + str(response.status_code))
            payload = response.json()
        except DownloaderError:
            raise
        except requests.Timeout:
            raise DownloaderError("cobalt_request_timeout") from None
        except requests.RequestException:
            raise DownloaderError("cobalt_unreachable") from None
        except (ValueError, TypeError):
            raise DownloaderError("cobalt_invalid_response") from None

        if not isinstance(payload, dict):
            raise DownloaderError("cobalt_invalid_response")
        if payload.get("status") == "error":
            code = str((payload.get("error") or {}).get("code") or "").lower()
            if any(word in code for word in ("login", "auth", "session", "private", "unavailable", "not_found")):
                raise DownloaderError("source_restricted_or_unavailable")
            if any(word in code for word in ("rate", "limit")):
                raise DownloaderError("cobalt_rate_limited")
            raise DownloaderError("cobalt_rejected_source")
        if payload.get("status") not in {"tunnel", "redirect"}:
            raise DownloaderError("cobalt_no_single_video")
        media_url = payload.get("url")
        if not isinstance(media_url, str) or not _safe_generated_media_url(media_url, self.api_url):
            raise DownloaderError("cobalt_unsafe_download_url")

        total = 0
        started = time.monotonic()
        try:
            with self.session.get(
                media_url,
                headers={"Accept": "video/mp4, video/*;q=0.9, */*;q=0.1"},
                stream=True,
                timeout=(15, self.stream_timeout_seconds),
                allow_redirects=True,
            ) as download_response:
                if download_response.status_code != 200:
                    raise DownloaderError("media_http_" + str(download_response.status_code))
                final_url = str(getattr(download_response, "url", media_url) or media_url)
                if not _safe_generated_media_url(final_url, self.api_url):
                    raise DownloaderError("media_redirect_rejected")
                content_type = (download_response.headers.get("Content-Type") or "").lower()
                if "application/json" in content_type or "text/html" in content_type:
                    raise DownloaderError("cobalt_returned_non_media")
                with partial.open("wb") as output:
                    for chunk in download_response.iter_content(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        if time.monotonic() - started > MAX_MEDIA_SECONDS:
                            raise DownloaderError("media_download_timeout")
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise DownloaderError("source_media_size_limit")
                        output.write(chunk)
        except DownloaderError:
            partial.unlink(missing_ok=True)
            raise
        except requests.Timeout:
            partial.unlink(missing_ok=True)
            raise DownloaderError("media_download_timeout") from None
        except requests.RequestException:
            partial.unlink(missing_ok=True)
            raise DownloaderError("media_download_failed") from None
        except OSError:
            partial.unlink(missing_ok=True)
            raise DownloaderError("local_write_failed") from None

        if total <= 50 * 1024:
            partial.unlink(missing_ok=True)
            raise DownloaderError("source_media_too_small")
        partial.replace(destination)
        return destination


class YouTubeRelayDownloader:
    """Download an authorized public YouTube video from the optional external relay."""

    def __init__(self, relay: AuthorizedYouTubeRelay):
        self.relay = relay

    def __call__(
        self,
        candidate: Candidate,
        output_dir: str | Path,
        permission_confirmed: bool,
    ) -> Path:
        if permission_confirmed is not True:
            raise DownloaderError("permission_confirmation_required")
        if candidate.platform != "youtube":
            raise DownloaderError("youtube_relay_platform_mismatch")
        try:
            path, _metadata = self.relay.download(
                candidate,
                output_dir,
                permission_confirmed=permission_confirmed,
            )
            return path
        except RelayClientError as exc:
            raise DownloaderError(exc.code) from None
