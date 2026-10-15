from __future__ import annotations

import unittest

from agent.discovery.instagram import extract_instagram_urls


class InstagramCanonicalURLTests(unittest.TestCase):
    def test_extracts_public_reels_posts_and_tv(self):
        sample = (
            '<a href="https://www.instagram.com/reel/AbCdEf_1234/?igsh=example">reel</a>'
            '<a href="https://www.instagram.com/p/ZyXwVu_9876/">post</a>'
            '<a href="https://www.instagram.com/tv/LmNoPq_4321/">tv</a>'
        )
        result = extract_instagram_urls(sample)
        self.assertEqual({item["id"] for item in result}, {
            "AbCdEf_1234", "ZyXwVu_9876", "LmNoPq_4321"
        })
        self.assertTrue(all(item["url"].endswith("/") for item in result))

    def test_decodes_percent_encoded_public_link(self):
        result = extract_instagram_urls(
            "https%3A%2F%2Fwww.instagram.com%2Fp%2FAbCdEf_1234%2F"
        )
        self.assertEqual(result[0]["post_type"], "p")

    def test_ignores_navigation_and_non_post_links(self):
        self.assertEqual(
            extract_instagram_urls(
                "instagram.com/explore/tags/poppyplaytime/ "
                "instagram.com/accounts/login/ "
                "instagram.com/about/"
            ),
            [],
        )

    def test_deduplicates_same_shortcode(self):
        result = extract_instagram_urls(
            "instagram.com/p/AbCdEf_1234/ "
            "https://www.instagram.com/p/AbCdEf_1234/?utm_source=search"
        )
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
