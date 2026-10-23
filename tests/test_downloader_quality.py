from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from agent.brain.downloaders import CobaltDownloader, DownloaderError
from agent.brain.models import Candidate


class CobaltQualityConfigTests(unittest.TestCase):
    def test_request_uses_configured_maximum_quality(self):
        session=Mock()
        response=Mock(status_code=200)
        response.json.return_value={"status":"error","error":{"code":"unsupported_source"}}
        session.post.return_value=response
        candidate=Candidate(platform="tiktok",canonical_url="https://www.tiktok.com/@meloaep_/video/7616247520657771796",
            external_id="7616247520657771796",source_key="meloaep_",media_type="video")
        with tempfile.TemporaryDirectory() as temp:
            downloader=CobaltDownloader(session=session)
            with self.assertRaises(DownloaderError):
                downloader(candidate,Path(temp),True)
        body=session.post.call_args.kwargs["json"]
        self.assertEqual(body["videoQuality"],"1080")


if __name__=="__main__":unittest.main()
