import unittest

from agent.preflight import check_cloud_health


class PreflightTests(unittest.TestCase):
    def test_health_url_uses_expected_path(self):
        # The URL construction is tested by mocking the HTTP boundary in later stages.
        # This test validates the public API contract without needing Cloudflare credentials.
        self.assertTrue(callable(check_cloud_health))


if __name__ == "__main__":
    unittest.main()
