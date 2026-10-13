"""Authenticated client for the optional relay; never logs tokens, URLs, or provider bodies."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .models import Candidate


class RelayClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AuthorizedYouTubeRelay:
    """Downloads one explicit, authorized public YouTube video through the relay."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: int = 240,
        max_bytes: int = 45 * 1024 * 1024,
        session: Any | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("relay_base_url_must_be_https")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("relay_base_url_must_be_origin")
        if len(token.strip()) < 32:
            raise ValueError("relay_token_too_short")
        self.base_url = base_url.rstrip("/")
        self.token = token.strip()
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max(1024, min(int(max_bytes), 45 * 1024 * 1024))
        self.session = session or requests.Session()

    @staticmethod
    def _validate_candidate(candidate: Candidate, permission_confirmed: bool) -> str:
        if permission_confirmed is not True:
            raise RelayClientError("permission_confirmation_required")
        if candidate.platform != "youtube" or candidate.media_type not in {"video", "unknown"}:
            raise RelayClientError("youtube_video_candidate_required")
        url = candidate.canonical_url
        match = re.fullmatch(
            r"https://www\.youtube\.com/(?:watch\?v=([A-Za-z0-9_-]{11})|shorts/([A-Za-z0-9_-]{11}))",
            url,
        )
        if not match:
            raise RelayClientError("noncanonical_youtube_url")
        video_id = match.group(1) or match.group(2)
        return "https://www.youtube.com/watch?v=" + video_id

    def download(
        self,
        candidate: Candidate,
        destination_dir: str | Path,
        *,
        permission_confirmed: bool,
    ) -> tuple[Path, dict[str, Any]]:
        canonical_url = self._validate_candidate(candidate, permission_confirmed)
        video_id = candidate.external_id
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            video_id = canonical_url.split("v=", 1)[-1]
        folder = Path(destination_dir)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / ("youtube_" + video_id + ".mp4")
        partial = destination.with_suffix(".mp4.part")
        if destination.exists():
            raise RelayClientError("destination_already_exists")
        if destination.resolve() == partial.resolve():
            raise RelayClientError("invalid_destination")

        try:
            with self.session.post(
                self.base_url + "/v1/download",
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Accept": "video/mp4",
                    "Content-Type": "application/json",
                },
                json={
                    "url": canonical_url,
                    "platform": "youtube",
                    "permission_confirmed": True,
                },
                stream=True,
                allow_redirects=False,
                timeout=(10, self.timeout_seconds),
            ) as response:
                if response.status_code != 200:
                    code = "relay_http_" + str(response.status_code)
                    raise RelayClientError(code)
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                if content_type != "video/mp4":
                    raise RelayClientError("relay_returned_non_mp4")
                declared = response.headers.get("Content-Length")
                if declared:
                    try:
                        if int(declared) > self.max_bytes:
                            raise RelayClientError("relay_file_size_limit")
                    except ValueError:
                        raise RelayClientError("relay_invalid_content_length") from None
                declared_hash = (response.headers.get("X-Media-SHA256") or "").lower()
                digest = hashlib.sha256()
                byte_count = 0
                try:
                    with partial.open("wb") as output:
                        for chunk in response.iter_content(chunk_size=1024 * 256):
                            if not chunk:
                                continue
                            byte_count += len(chunk)
                            if byte_count > self.max_bytes:
                                raise RelayClientError("relay_file_size_limit")
                            digest.update(chunk)
                            output.write(chunk)
                except OSError:
                    raise RelayClientError("local_write_failed") from None
        except RelayClientError:
            partial.unlink(missing_ok=True)
            raise
        except requests.Timeout:
            partial.unlink(missing_ok=True)
            raise RelayClientError("relay_timeout") from None
        except requests.RequestException:
            partial.unlink(missing_ok=True)
            raise RelayClientError("relay_unreachable") from None
        except OSError:
            partial.unlink(missing_ok=True)
            raise RelayClientError("local_write_failed") from None

        actual_hash = digest.hexdigest()
        if byte_count <= 50 * 1024:
            partial.unlink(missing_ok=True)
            raise RelayClientError("relay_file_too_small")
        if not re.fullmatch(r"[a-f0-9]{64}", declared_hash):
            partial.unlink(missing_ok=True)
            raise RelayClientError("relay_invalid_sha256_header")
        if not __import__("hmac").compare_digest(actual_hash, declared_hash):
            partial.unlink(missing_ok=True)
            raise RelayClientError("relay_sha256_mismatch")
        partial.replace(destination)

        metadata = {
            "sha256": actual_hash,
            "size_bytes": byte_count,
            "mime_type": "video/mp4",
            "width": _int_header(response.headers.get("X-Media-Width")),
            "height": _int_header(response.headers.get("X-Media-Height")),
            "duration_seconds": _float_header(response.headers.get("X-Media-Duration")),
            "has_audio": (response.headers.get("X-Media-Has-Audio") or "").lower() == "true",
            "video_codec": "h264",
            "audio_codec": "aac" if (response.headers.get("X-Media-Has-Audio") or "").lower() == "true" else None,
        }
        return destination, metadata


def _int_header(value: str | None) -> int | None:
    try:
        result = int(value) if value is not None else None
        return result if result is not None and result > 0 else None
    except (ValueError, TypeError):
        return None


def _float_header(value: str | None) -> float | None:
    try:
        result = float(value) if value is not None else None
        return result if result is not None and result > 0 else None
    except (ValueError, TypeError):
        return None
