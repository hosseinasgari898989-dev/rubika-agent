from __future__ import annotations
import json
import tempfile
import unittest
from pathlib import Path
from agent.discovery import run as runner
from agent.source_policy import known_tiktok_profiles


class DiscoveryOrchestratorTests(unittest.TestCase):
    def test_tiktok_only_self_test(self):
        self.assertEqual(runner.self_test(), 0)

    def test_phone_report_accepts_selected_tiktok_handles_and_other_metadata(self):
        selected=known_tiktok_profiles()[0]
        phone={"schema_version":1,"generated_at":"2026-10-13T12:00:00+00:00","source":"termux_phone",
               "mode":"metadata_only_no_download_no_publish","summary":{},
               "candidates":{
                   "tiktok":[
                       {"platform":"tiktok","id":"7616247520657771796",
                        "url":f"https://www.tiktok.com/@{selected}/video/7616247520657771796?share=1",
                        "author":selected,"title":"Poppy Playtime edit","caption":"Poppy Playtime edit","media_type":"video"},
                       {"platform":"tiktok","id":"7557483116605033748",
                        "url":"https://www.tiktok.com/@unlisted_creator/video/7557483116605033748",
                        "author":"unlisted_creator","title":"Poppy Playtime edit","caption":"Poppy Playtime edit","media_type":"video"}]},
               "attempts":[]}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);report_path=root/"phone.json";report_path.write_text(json.dumps(phone),encoding="utf-8")
            report=runner.run_discovery(report_path=root/"out.json",phone_report_path=report_path)
        self.assertEqual(report["summary"]["tiktok_candidates"],1)
        self.assertTrue(report["summary"]["core_search_target_met"])
        self.assertTrue(report["summary"]["termux_report_loaded"])
        self.assertFalse(report["summary"]["ready_to_publish"])
        self.assertEqual(report["candidates"]["tiktok"][0]["url"],f"https://www.tiktok.com/@{selected}/video/7616247520657771796")
        self.assertNotIn("unlisted_creator",str(report["candidates"]["tiktok"]))

    def test_unselected_or_wrong_host_phone_rows_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"phone.json"
            path.write_text(json.dumps({"schema_version":1,"source":"termux_phone","mode":"metadata_only_no_download_no_publish",
                "generated_at":"2026-10-13T12:00:00Z","candidates":{"tiktok":[
                    {"id":"12345678901","url":"https://www.tiktok.com/@unlisted_creator/video/12345678901","author":"unlisted_creator"},
                    {"id":"12345678902","url":"https://evil.example/@meloaep_/video/12345678902","author":"meloaep_"}]}}),encoding="utf-8")
            candidates,_,status=runner._load_phone_report(path)
        self.assertEqual(status,"loaded");self.assertEqual(candidates["tiktok"],[])

    def test_invalid_phone_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"bad.json";path.write_text(json.dumps({"schema_version":1,"source":"untrusted",
                "mode":"metadata_only_no_download_no_publish","candidates":{"tiktok":[]}}),encoding="utf-8")
            candidates,generated,status=runner._load_phone_report(path)
        self.assertEqual(status,"invalid_report");self.assertIsNone(generated);self.assertEqual(candidates["tiktok"],[])

    def test_live_exit_code_fails_when_no_candidates_are_found(self):
        from unittest.mock import patch
        with patch.object(runner,"run_discovery",return_value={"summary":{"core_search_target_met":False}}):
            self.assertEqual(runner.main(["--live"]),2)

    def test_live_exit_code_succeeds_when_candidates_are_found(self):
        from unittest.mock import patch
        with patch.object(runner,"run_discovery",return_value={"summary":{"core_search_target_met":True}}):
            self.assertEqual(runner.main(["--live"]),0)

if __name__=="__main__":unittest.main()
