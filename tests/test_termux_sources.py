from __future__ import annotations
import unittest
from datetime import timedelta, datetime, timezone
from agent.discovery.termux_sources import discover_tiktok_profiles, TIKTOK_PROFILES


class FakeYDL:
    instances=[]
    info={}
    def __init__(self,options):
        self.options=options;self.queries=[];self.__class__.instances.append(self)
    def __enter__(self): return self
    def __exit__(self,exc_type,exc,tb): return False
    def extract_info(self,query,download=False):
        self.queries.append((query,download));return self.__class__.info


class TermuxMetadataParserTests(unittest.TestCase):
    def test_tiktok_uses_only_the_six_configured_creator_feeds(self):
        FakeYDL.instances=[]
        FakeYDL.info={"entries":[{"id":"7610592632179297558","title":"unrelated clip","duration":18,"timestamp":1775000000,"view_count":12345}]}
        found,attempts=discover_tiktok_profiles(profiles=(TIKTOK_PROFILES[0],),max_results=5,sleep_seconds=0,ydl_factory=FakeYDL)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["author"],TIKTOK_PROFILES[0])
        self.assertEqual(found[0]["url"],f"https://www.tiktok.com/@{TIKTOK_PROFILES[0]}/video/7610592632179297558")
        self.assertEqual(found[0]["method"],"termux_ytdlp_selected_creator_profile")
        self.assertEqual(FakeYDL.instances[0].queries[0][1],False)
        self.assertTrue(FakeYDL.instances[0].options["skip_download"])
        self.assertEqual(attempts[0]["result"],"metadata_found")

    def test_unselected_profile_is_rejected_without_network_discovery(self):
        FakeYDL.instances=[]
        FakeYDL.info={"entries":[{"id":"7610592632179297558","title":"Poppy Playtime"}]}
        found,attempts=discover_tiktok_profiles(profiles=("unlisted_creator",),max_results=5,sleep_seconds=0,ydl_factory=FakeYDL)
        self.assertEqual(found,[])
        self.assertEqual(FakeYDL.instances,[])
        self.assertEqual(attempts[0]["result"],"source_not_selected")

    def test_old_and_undated_selected_profile_items_are_not_date_filtered_here(self):
        FakeYDL.instances=[]
        old=int((datetime.now(timezone.utc)-timedelta(days=120)).timestamp())
        FakeYDL.info={"entries":[
            {"id":"7610592632179297558","title":"old selected-source clip","timestamp":old},
            {"id":"7610592632179297559","title":"undated selected-source clip"},
        ]}
        found,_=discover_tiktok_profiles(profiles=(TIKTOK_PROFILES[0],),max_results=5,sleep_seconds=0,ydl_factory=FakeYDL)
        self.assertEqual({row["id"] for row in found},{"7610592632179297558","7610592632179297559"})
        self.assertIsNone(next(row for row in found if row["id"].endswith("7559"))["published_at"])

if __name__ == "__main__":
    unittest.main()
