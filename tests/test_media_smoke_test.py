import unittest

from agent.media_smoke_test import validate_public_video_url, _safe_error


class MediaSmokeTests(unittest.TestCase):
    def test_accepts_https_domains_for_expected_platforms(self):
        self.assertTrue(validate_public_video_url("tiktok", "https://www.tiktok.com/@example/video/123")[0])
        self.assertTrue(validate_public_video_url("instagram", "https://www.instagram.com/reel/ABC123/")[0])
        self.assertTrue(validate_public_video_url("youtube", "https://youtu.be/abcdefghijk")[0])

    def test_rejects_cross_platform_and_untrusted_hosts(self):
        self.assertEqual(
            validate_public_video_url("tiktok", "https://example.com/video/123")[1],
            "platform_host_mismatch",
        )
        self.assertEqual(
            validate_public_video_url("instagram", "http://www.instagram.com/reel/ABC/")[1],
            "invalid_url",
        )
        self.assertEqual(
            validate_public_video_url("youtube", "https://youtube.com.evil.example/watch?v=1")[1],
            "platform_host_mismatch",
        )

    def test_rejects_credentials_in_url(self):
        ok, reason = validate_public_video_url("youtube", "https://user:pass@youtube.com/watch?v=1")
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_url")

    def test_errors_are_sanitized(self):
        self.assertEqual(_safe_error("Sign in to confirm you're not a bot"), "login_or_access_restricted")
        self.assertEqual(_safe_error("HTTP Error 429: Too Many Requests"), "rate_limited")
        self.assertEqual(_safe_error("unsupported URL"), "unsupported_url")
        self.assertEqual(_safe_error("", timed_out=True), "download_timeout")


if __name__ == "__main__":
    unittest.main()
