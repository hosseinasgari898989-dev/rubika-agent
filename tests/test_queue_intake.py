from __future__ import annotations

import unittest

from agent.brain.queue_intake import candidates_for_queue


class QueueIntakeTests(unittest.TestCase):
    def test_maps_metadata_and_deduplicates_without_media_bytes(self):
        payload = {
            "schema_version": 1,
            "source": "termux_phone",
            "mode": "metadata_only_no_download_no_publish",
            "candidates": {
                "tiktok": [
                    {
                        "id": "7616247520657771796",
                        "url": "https://www.tiktok.com/@creator/video/7616247520657771796",
                        "author": "@creator",
                        "title": "Poppy Playtime edit",
                        "caption": "Public caption",
                        "media_type": "video",
                        "freshness_verified": True,
                    },
                    {
                        "id": "7616247520657771796",
                        "url": "https://www.tiktok.com/@creator/video/7616247520657771796",
                        "author": "@creator",
                    },
                ],
                "youtube": [
                    {
                        "id": "abcdefghijk",
                        "url": "https://www.youtube.com/shorts/abcdefghijk",
                        "author": "official_creator",
                        "title": "Poppy edit",
                        "freshness_verified": True,
                    }
                ],
                "instagram": [
                    {
                        "id": "AbCdEf_1234",
                        "url": "https://www.instagram.com/p/AbCdEf_1234/",
                        "media_type": "image",
                    },
                    {
                        "id": "GhIjKl_9876",
                        "url": "https://www.instagram.com/reel/GhIjKl_9876/",
                        "media_type": "video",
                    },
                ],
            },
        }
        rows = candidates_for_queue(payload)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["platform"] for row in rows], ["tiktok", "youtube", "instagram"])
        self.assertEqual(rows[0]["external_id"], "7616247520657771796")
        self.assertEqual(rows[0]["source_key"], "@creator")
        self.assertTrue(rows[0]["freshness_verified"])
        self.assertTrue(all("media_bytes" not in row and "path" not in row for row in rows))

    def test_rejects_bad_ids_and_non_https_urls(self):
        payload = {
            "candidates": {
                "tiktok": [
                    {"id": "../bad", "url": "https://www.tiktok.com/@x/video/123"},
                    {"id": "valid", "url": "http://www.tiktok.com/@x/video/123"},
                ]
            }
        }
        self.assertEqual(candidates_for_queue(payload), [])


if __name__ == "__main__":
    unittest.main()
