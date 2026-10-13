from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from agent.brain.models import Candidate
from agent.brain.relay_client import AuthorizedYouTubeRelay, RelayClientError


TOKEN = "relay-client-test-token-with-at-least-thirty-two-characters"
MEDIA = b"fake-mp4-content-" * 10000


class FakeResponse:
    def __init__(self, status=200, body=MEDIA, headers=None):
        self.status_code = status
        self.body = body
        self.headers = headers or {
            "Content-Type": "video/mp4",
            "Content-Length": str(len(body)),
            "X-Media-SHA256": hashlib.sha256(body).hexdigest(),
            "X-Media-Width": "1080",
            "X-Media-Height": "1080",
            "X-Media-Duration": "13.5",
            "X-Media-Has-Audio": "true",
            "X-Relay-Publishing": "disabled",
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_content(self, chunk_size=1024):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset:offset + chunk_size]


class FakeSession:
    def __init__(self, response=None):
        self.response = response or FakeResponse()
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class RelayClientTests(unittest.TestCase):
    def candidate(self, **changes):
        values = dict(
            platform="youtube",
            canonical_url="https://www.youtube.com/watch?v=vkVFgmgLo1s",
            external_id="vkVFgmgLo1s",
            title="Poppy Playtime edit",
            caption="#PoppyPlaytime #edit",
            source_key="@creator",
            media_type="video",
            source_tier="trusted",
        )
        values.update(changes)
        return Candidate(**values)

    def test_requires_https_origin_and_long_token(self):
        with self.assertRaises(ValueError):
            AuthorizedYouTubeRelay("http://relay.example", TOKEN)
        with self.assertRaises(ValueError):
            AuthorizedYouTubeRelay("https://relay.example/path", TOKEN)
        with self.assertRaises(ValueError):
            AuthorizedYouTubeRelay("https://relay.example", "short")

    def test_requires_explicit_permission_and_canonical_youtube_candidate(self):
        client = AuthorizedYouTubeRelay("https://relay.example", TOKEN, session=FakeSession())
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RelayClientError) as caught:
                client.download(self.candidate(), folder, permission_confirmed=False)
            self.assertEqual(caught.exception.code, "permission_confirmation_required")

            with self.assertRaises(RelayClientError) as caught:
                client.download(
                    self.candidate(canonical_url="https://www.youtube.com/watch?v=vkVFgmgLo1s&list=playlist"),
                    folder,
                    permission_confirmed=True,
                )
            self.assertEqual(caught.exception.code, "noncanonical_youtube_url")

    def test_download_stream_is_hashed_and_returns_validated_metadata(self):
        session = FakeSession()
        client = AuthorizedYouTubeRelay("https://relay.example", TOKEN, session=session)
        with tempfile.TemporaryDirectory() as folder:
            path, metadata = client.download(
                self.candidate(), folder, permission_confirmed=True
            )
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), MEDIA)
            self.assertEqual(metadata["sha256"], hashlib.sha256(MEDIA).hexdigest())
            self.assertEqual(metadata["size_bytes"], len(MEDIA))
            self.assertEqual(metadata["width"], 1080)
            self.assertTrue(metadata["has_audio"])
            self.assertEqual(session.calls[0][0], "https://relay.example/v1/download")
            self.assertEqual(
                session.calls[0][1]["headers"]["Authorization"],
                "Bearer " + TOKEN,
            )
            self.assertTrue(session.calls[0][1]["allow_redirects"] is False)
            self.assertEqual(
                session.calls[0][1]["json"]["url"],
                "https://www.youtube.com/watch?v=vkVFgmgLo1s",
            )

    def test_relay_hash_mismatch_deletes_partial_file(self):
        response = FakeResponse(headers={
            "Content-Type": "video/mp4",
            "Content-Length": str(len(MEDIA)),
            "X-Media-SHA256": "0" * 64,
        })
        client = AuthorizedYouTubeRelay(
            "https://relay.example", TOKEN, session=FakeSession(response)
        )
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RelayClientError) as caught:
                client.download(self.candidate(), folder, permission_confirmed=True)
            self.assertEqual(caught.exception.code, "relay_sha256_mismatch")
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_existing_destination_is_never_overwritten(self):
        client = AuthorizedYouTubeRelay("https://relay.example", TOKEN, session=FakeSession())
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "youtube_vkVFgmgLo1s.mp4"
            destination.write_bytes(b"original")
            with self.assertRaises(RelayClientError) as caught:
                client.download(self.candidate(), folder, permission_confirmed=True)
            self.assertEqual(caught.exception.code, "destination_already_exists")
            self.assertEqual(destination.read_bytes(), b"original")

    def test_missing_integrity_header_is_rejected_and_partial_deleted(self):
        response = FakeResponse(headers={"Content-Type": "video/mp4"})
        client = AuthorizedYouTubeRelay(
            "https://relay.example", TOKEN, session=FakeSession(response)
        )
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RelayClientError) as caught:
                client.download(self.candidate(), folder, permission_confirmed=True)
            self.assertEqual(caught.exception.code, "relay_invalid_sha256_header")
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_non_mp4_and_http_error_responses_are_rejected(self):
        response = FakeResponse(status=422, headers={"Content-Type": "application/json"})
        client = AuthorizedYouTubeRelay(
            "https://relay.example", TOKEN, session=FakeSession(response)
        )
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RelayClientError) as caught:
                client.download(self.candidate(), folder, permission_confirmed=True)
            self.assertEqual(caught.exception.code, "relay_http_422")
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
