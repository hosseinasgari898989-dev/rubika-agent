from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from relay import app as relay


TOKEN = "relay-test-token-with-at-least-thirty-two-characters"


class RelayURLTests(unittest.TestCase):
    def test_canonicalizes_watch_shorts_and_short_links(self):
        cases = [
            (
                "https://www.youtube.com/watch?v=vkVFgmgLo1s&feature=share",
                ("https://www.youtube.com/watch?v=vkVFgmgLo1s", "vkVFgmgLo1s"),
            ),
            (
                "https://www.youtube.com/shorts/vkVFgmgLo1s",
                ("https://www.youtube.com/watch?v=vkVFgmgLo1s", "vkVFgmgLo1s"),
            ),
            (
                "https://youtu.be/vkVFgmgLo1s",
                ("https://www.youtube.com/watch?v=vkVFgmgLo1s", "vkVFgmgLo1s"),
            ),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(relay.canonical_youtube_url(source), expected)

    def test_rejects_http_hosts_credentials_and_invalid_ids(self):
        invalid = [
            "http://www.youtube.com/watch?v=vkVFgmgLo1s",
            "https://youtube.com.evil.test/watch?v=vkVFgmgLo1s",
            "https://user:pass@youtube.com/watch?v=vkVFgmgLo1s",
            "https://www.youtube.com/playlist?list=PL123456789",
            "https://youtu.be/short",
            "https://www.youtube.com/shorts/vkVFgmgLo1s?si=temporary",
        ]
        for source in invalid:
            with self.subTest(source=source), self.assertRaises(relay.RelayError):
                relay.canonical_youtube_url(source)

    def test_health_does_not_require_credentials_and_never_advertises_publishing(self):
        client = TestClient(relay.app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["publishing"])
        self.assertEqual(response.json()["max_duration_seconds"], 180)


class RelayAPIProtectionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(relay.app)
        self.env_patch = patch.dict(os.environ, {"RELAY_API_TOKEN": TOKEN})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def payload(self, **changes):
        body = {
            "url": "https://www.youtube.com/watch?v=vkVFgmgLo1s",
            "platform": "youtube",
            "permission_confirmed": True,
        }
        body.update(changes)
        return body

    def test_requires_bearer_token(self):
        response = self.client.post(
            "/v1/download",
            json=self.payload(),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"]["error"], "unauthorized")

    def test_requires_explicit_permission_confirmation(self):
        response = self.client.post(
            "/v1/download",
            headers={"Authorization": "Bearer " + TOKEN},
            json=self.payload(permission_confirmed=False),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["error"], "permission_confirmation_required")

    def test_rejects_non_youtube_platform(self):
        response = self.client.post(
            "/v1/download",
            headers={"Authorization": "Bearer " + TOKEN},
            json=self.payload(platform="tiktok"),
        )
        self.assertEqual(response.status_code, 422)

    def test_public_video_download_is_validated_and_temp_file_is_removed(self):
        captured = {}
        metadata = {
            "duration_seconds": 12.5, "width": 1080, "height": 1080,
            "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
            "size_bytes": 170000, "mime_type": "video/mp4",
        }

        def fake_download(url, video_id, work_dir):
            captured["work_dir"] = work_dir
            media = work_dir / f"youtube_{video_id}.mp4"
            media.write_bytes(b"test-media-bytes" * 10000)
            metadata["size_bytes"] = media.stat().st_size
            return media, metadata

        with patch.object(relay, "_download_and_probe", side_effect=fake_download):
            response = self.client.post(
                "/v1/download",
                headers={"Authorization": "Bearer " + TOKEN},
                json=self.payload(),
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertEqual(response.headers["x-relay-publishing"], "disabled")
        self.assertEqual(response.headers["x-media-width"], "1080")
        self.assertEqual(len(response.content), metadata["size_bytes"])
        self.assertFalse(captured["work_dir"].exists())

    def test_downloader_error_does_not_expose_provider_details(self):
        with patch.object(
            relay, "_download_and_probe",
            side_effect=relay.RelayError("source_restricted_or_unavailable", 422),
        ):
            response = self.client.post(
                "/v1/download",
                headers={"Authorization": "Bearer " + TOKEN},
                json=self.payload(),
            )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["error"], "source_restricted_or_unavailable")
        self.assertNotIn("youtube", response.text.lower().replace("rubika-agent", ""))


if __name__ == "__main__":
    unittest.main()
