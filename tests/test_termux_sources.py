from __future__ import annotations
import unittest
from datetime import timedelta, datetime, timezone
from agent.discovery.termux_sources import discover_tiktok_profiles, discover_youtube_metadata, TIKTOK_PROFILES


class FakeYDL:
    instances=[]
    info={}
    def __init__(self,options):
        self.options=options;self.queries=[];self.__class__.instances.append(self)
    def __enter__(self): return self
    def __exit__(self,exc_type,exc,tb): return False
    def extract_info(self,query,download=False):
        self.queries.append((query,download));return self.__class__.info


class FakeFeedResponse:
    def __init__(self,status_code,text): self.status_code=status_code;self.text=text


class FakeFeedSession:
    def __init__(self,responses): self.responses=list(responses);self.calls=[]
    def get(self,url,**kwargs): self.calls.append((url,kwargs));return self.responses.pop(0)


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
        self.assertIsNone(next(row for row in found if row["id"].endswith("759"))["published_at"])

    def test_youtube_search_is_metadata_only_and_keeps_only_short_duration(self):
        FakeYDL.instances = []
        FakeYDL.info = {"entries": [
            {
                "id": "vkVFgmgLo1s", "title": "Poppy Playtime edit",
                "duration": 40, "uploader": "Editor", "timestamp": 1791808200,
                "view_count": 12345, "description": "#PoppyPlaytime",
            },
            {"id": "n38wy3o0DhY", "title": "long edit", "duration": 240},
            {"id": "tooShortId", "title": "invalid identifier", "duration": 20},
            {"id": "AbcdefGhijk", "title": "unknown duration", "duration": None},
        ]}
        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime edit shorts",),
            max_results=10,
            ydl_factory=FakeYDL,
        )
        self.assertEqual([row["id"] for row in found], ["vkVFgmgLo1s"])
        self.assertEqual(found[0]["duration_seconds"], 40)
        self.assertEqual(found[0]["view_count"], 12345)
        self.assertEqual(FakeYDL.instances[0].queries[0][1], False)
        self.assertTrue(FakeYDL.instances[0].options["skip_download"])
        self.assertTrue(FakeYDL.instances[0].options["extract_flat"])
        self.assertIn("logger", FakeYDL.instances[0].options)
        self.assertTrue(found[0]["freshness_verified"])
        self.assertEqual(attempts[0]["result"], "metadata_found")

    def test_youtube_keeps_undated_flat_search_results_unverified_and_skips_old_dates(self):
        FakeYDL.instances = []
        FakeYDL.info = {"entries": [
            {"id": "vkVFgmgLo1s", "title": "old short", "duration": 40, "timestamp": 1775000000},
            {"id": "n38wy3o0DhY", "title": "undated short", "duration": 24},
        ]}
        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime edit shorts",),
            max_results=10,
            max_page_checks=0,
            ydl_factory=FakeYDL,
        )
        self.assertEqual([row["id"] for row in found], ["n38wy3o0DhY"])
        self.assertIsNone(found[0]["published_at"])
        self.assertFalse(found[0]["freshness_verified"])
        self.assertEqual(found[0]["freshness_status"], "date_unavailable_verify_before_publish")
        self.assertEqual(attempts[0]["result"], "metadata_found")
        self.assertEqual(attempts[0]["stale_or_undated_skipped"], 1)
        self.assertEqual(attempts[0]["freshness_unverified"], 1)

    def test_youtube_uses_public_channel_feed_to_verify_missing_date(self):
        FakeYDL.instances = []
        channel_id = "UC" + ("a" * 22)
        FakeYDL.info = {"entries": [{
            "id": "n38wy3o0DhY",
            "title": "Undated public short",
            "duration": 24,
            "channel_id": channel_id,
        }]}
        published = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        atom_feed = (
            '<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
            "<entry><yt:videoId>n38wy3o0DhY</yt:videoId>"
            f"<published>{published}</published></entry></feed>"
        )
        feed_session = FakeFeedSession([FakeFeedResponse(200, atom_feed)])

        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime edit shorts",),
            max_results=10,
            ydl_factory=FakeYDL,
            feed_session=feed_session,
        )

        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["freshness_verified"])
        self.assertEqual(found[0]["freshness_source"], "public_channel_atom_feed")
        self.assertEqual(found[0]["freshness_status"], "verified_within_window")
        self.assertEqual(found[0]["published_at"], datetime.fromisoformat(published).isoformat())
        self.assertTrue(FakeYDL.instances[0].options["extract_flat"])
        self.assertEqual(FakeYDL.instances[0].queries[0][1], False)
        self.assertEqual(len(feed_session.calls), 1)
        self.assertIn("feeds/videos.xml?channel_id=" + channel_id, feed_session.calls[0][0])
        self.assertEqual(attempts[-1]["method"], "public_channel_atom_feed")
        self.assertEqual(attempts[-1]["candidate_dates_enriched"], 1)
        self.assertEqual(attempts[-1]["result"], "metadata_enriched")

    def test_youtube_rejects_old_date_from_public_channel_feed(self):
        FakeYDL.instances = []
        channel_id = "UC" + ("b" * 22)
        FakeYDL.info = {"entries": [{
            "id": "n38wy3o0DhY",
            "title": "Old public short",
            "duration": 24,
            "channel_id": channel_id,
        }]}
        published = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        atom_feed = (
            '<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
            "<entry><yt:videoId>n38wy3o0DhY</yt:videoId>"
            f"<published>{published}</published></entry></feed>"
        )
        feed_session = FakeFeedSession([FakeFeedResponse(200, atom_feed)])

        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime edit shorts",),
            max_results=10,
            ydl_factory=FakeYDL,
            feed_session=feed_session,
        )

        self.assertEqual(found, [])
        self.assertEqual(attempts[0]["stale_or_undated_skipped"], 1)
        self.assertEqual(attempts[-1]["candidate_dates_enriched"], 0)
        self.assertEqual(attempts[-1]["result"], "no_candidate_dates_matched")

    def test_youtube_verifies_missing_date_from_public_video_page_metadata(self):
        FakeYDL.instances = []
        FakeYDL.info = {"entries": [{
            "id": "n38wy3o0DhY",
            "title": "Public short without search date",
            "duration": 24,
        }]}
        published = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        page_session = FakeFeedSession([
            FakeFeedResponse(200, f'<html><meta itemprop="datePublished" content="{published}"></html>')
        ])

        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime edit shorts",),
            max_results=10,
            ydl_factory=FakeYDL,
            page_session=page_session,
        )

        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["freshness_verified"])
        self.assertEqual(found[0]["freshness_source"], "public_video_page_datePublished")
        self.assertEqual(found[0]["published_at"], datetime.fromisoformat(published).replace(tzinfo=timezone.utc).isoformat())
        self.assertEqual(len(page_session.calls), 1)
        self.assertEqual(attempts[-1]["method"], "public_video_page_metadata")
        self.assertEqual(attempts[-1]["page_dates_extracted"], 1)
        self.assertEqual(attempts[-1]["result"], "metadata_enriched")

    def test_youtube_page_challenge_is_not_bypassed_and_date_stays_unverified(self):
        FakeYDL.instances = []
        FakeYDL.info = {"entries": [{
            "id": "n38wy3o0DhY",
            "title": "Public short without search date",
            "duration": 24,
        }]}
        page_session = FakeFeedSession([
            FakeFeedResponse(200, "<html>Sign in to confirm you are not a bot</html>")
        ])

        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime edit shorts",),
            max_results=10,
            ydl_factory=FakeYDL,
            page_session=page_session,
        )

        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["freshness_verified"])
        self.assertEqual(found[0]["freshness_status"], "date_unavailable_verify_before_publish")
        self.assertEqual(attempts[-1]["status_counts"], {"page_challenge_or_signin": 1})
        self.assertEqual(attempts[-1]["result"], "no_public_page_dates_extracted")
        self.assertEqual(len(page_session.calls), 1)

    def test_missing_yt_dlp_reports_a_safe_status(self):
        def missing(_options):
            raise ImportError("not installed")
        found, attempts = discover_youtube_metadata(
            queries=("Poppy Playtime",),
            ydl_factory=missing,
        )
        self.assertEqual(found, [])
        self.assertEqual(attempts[0]["result"], "metadata_search_failed")


if __name__ == "__main__":
    unittest.main()
