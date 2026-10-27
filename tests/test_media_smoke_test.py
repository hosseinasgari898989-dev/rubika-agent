import unittest

from agent.media_smoke_test import (
    _allowed_download_url,
    _classify_cobalt_error,
    validate_public_video_url,
)


class MediaSmokeTests(unittest.TestCase):
    def test_accepts_https_domains_for_expected_platforms(self):
        self.assertTrue(validate_public_video_url("tiktok", "https://www.tiktok.com/@example/video/123")[0])
        self.assertTrue(validate_public_video_url("instagram", "https://www.instagram.com/reel/ABC123/")[0])

    def test_rejects_cross_platform_and_untrusted_hosts(self):
        self.assertEqual(
            validate_public_video_url("tiktok", "https://example.com/video/123")[1],
            "platform_host_mismatch",
        )
        self.assertEqual(
            validate_public_video_url("instagram", "http://www.instagram.com/reel/ABC/")[1],
            "invalid_url",
        )

    def test_cobalt_error_codes_are_classified(self):
        self.assertEqual(
            _classify_cobalt_error({"error": {"code": "error.api.content.post.private"}}),
            "private_or_unavailable",
        )
        self.assertEqual(
            _classify_cobalt_error({"error": {"code": "error.api.rate_exceeded"}}),
            "rate_limited",
        )
        self.assertEqual(
            _classify_cobalt_error({"error": {"code": "error.api.link.unsupported"}}),
            "unsupported_url",
        )

    def test_download_url_must_be_local_cobalt_or_https(self):
        api = "http://127.0.0.1:9000/"
        self.assertTrue(_allowed_download_url("http://127.0.0.1:9000/tunnel?id=123", api))
        self.assertTrue(_allowed_download_url("https://cdn.example.com/video.mp4", api))
        self.assertFalse(_allowed_download_url("http://example.com/video.mp4", api))
        self.assertFalse(_allowed_download_url("http://127.0.0.1:9001/private", api))
        self.assertFalse(_allowed_download_url("file:///etc/passwd", api))


if __name__ == "__main__":
    unittest.main()
