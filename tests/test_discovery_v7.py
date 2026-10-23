from __future__ import annotations
import unittest
from agent.discovery import v7


class DiscoveryExtractorTests(unittest.TestCase):
    def test_youtube_embedded_id_extraction(self):
        self.assertEqual(v7.extract_youtube_ids('{"videoId":"vkVFgmgLo1s"} {"videoId":"n38wy3o0DhY"}'),
                         ["vkVFgmgLo1s","n38wy3o0DhY"])

    def test_add_candidates_dedupes_youtube_ids(self):
        store={"youtube":{}};seen=set()
        self.assertEqual(v7.add_candidates(store,"youtube",[{"id":"vkVFgmgLo1s"},{"id":"vkVFgmgLo1s"}],"test","Poppy",seen),1)
        self.assertEqual(len(store["youtube"]),1)

    def test_broad_tiktok_candidates_are_never_accepted_by_legacy_engine(self):
        store={"youtube":{}}
        self.assertEqual(v7.add_candidates(store,"tiktok",[{"id":"7616247520657771796"}],"test","Poppy",set()),0)
        self.assertNotIn("tiktok",store)


if __name__=="__main__":unittest.main()
