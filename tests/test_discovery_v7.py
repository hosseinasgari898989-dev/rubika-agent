from __future__ import annotations

import unittest

from agent.discovery import v7


class DiscoveryExtractorTests(unittest.TestCase):
    def test_tiktok_plain_url_is_canonicalized(self):
        result = v7.extract_tiktok_urls(
            '<a href="https://www.tiktok.com/@creator.name/video/7616247520657771796?share=1">edit</a>'
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "7616247520657771796")
        self.assertEqual(
            result[0]["url"],
            "https://www.tiktok.com/@creator.name/video/7616247520657771796",
        )

    def test_tiktok_encoded_link_is_decoded(self):
        result = v7.extract_tiktok_urls(
            "https%3A%2F%2Fwww.tiktok.com%2F%40creator.name%2Fvideo%2F7616247520657771796"
        )
        self.assertEqual(result[0]["author"], "creator.name")

    def test_tiktok_extractor_ignores_noncanonical_mentions(self):
        self.assertEqual(v7.extract_tiktok_urls("Menu: tiktok.com, #PoppyPlaytime"), [])

    def test_youtube_embedded_id_extraction(self):
        sample = '{"videoId":"vkVFgmgLo1s"} {"videoId":"n38wy3o0DhY"}'
        self.assertEqual(
            v7.extract_youtube_ids(sample),
            ["vkVFgmgLo1s", "n38wy3o0DhY"],
        )

    def test_add_candidates_dedupes_ids(self):
        store = {"tiktok": {}, "youtube": {}}
        seen = set()
        items = [
            {"id": "vkVFgmgLo1s", "url": "https://youtube.com/shorts/vkVFgmgLo1s", "title": "Poppy edit"},
            {"id": "vkVFgmgLo1s", "url": "https://youtube.com/shorts/vkVFgmgLo1s", "title": "Duplicate"},
        ]
        added = v7.add_candidates(store, "youtube", items, "test", "Poppy edit", seen)
        self.assertEqual(added, 1)
        self.assertEqual(len(store["youtube"]), 1)


if __name__ == "__main__":
    unittest.main()
