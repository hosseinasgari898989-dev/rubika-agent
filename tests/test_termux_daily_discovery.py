from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch

from termux import daily_discovery
from agent.source_policy import resolve_source_policy, set_dynamic_tiktok_sources
from agent.brain.cloud_state import CloudStateError
from agent.discovery.termux_sources import TIKTOK_PROFILES


class TermuxDailyDiscoveryTests(unittest.TestCase):
    def test_report_combines_tiktok_edits_and_x_only_official_mob_posts(self):
        tiktok = [{
            "platform": "tiktok",
            "id": "7610592632179297558",
            "url": "https://www.tiktok.com/@meloaep_/video/7610592632179297558",
            "author": "meloaep_",
            "hashtags": ["#PoppyPlaytime"],
            "source_policy": resolve_source_policy("tiktok", "meloaep_"),
            "title": "Poppy Playtime edit",
            "caption": "A public edit",
            "published_at": "2026-10-14T11:00:00+00:00",
            "freshness_verified": True,
            "media_type": "video",
        }]
        mob_x = [{
            "platform": "mob_official",
            "id": "x-1734258130186559716",
            "external_id": "x-1734258130186559716",
            "url": "https://x.com/mobgamesstudios/status/1734258130186559716",
            "canonical_url": "https://x.com/mobgamesstudios/status/1734258130186559716",
            "title": "Official Mob update",
            "caption": "Actual official post caption",
            "original_caption": "Actual official post caption",
            "source_platform": "x",
            "source_name": "Mob Entertainment on X",
            "source_key": "x.com/mobgamesstudios",
            "author": "@mobgamesstudios",
            "official_source": True,
            "published_at": "2026-10-20T00:00:00+00:00",
            "freshness_verified": True,
            "media_type": "image",
            "media_url": "https://pbs.twimg.com/media/example.jpg",
        }]
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "termux_discovery.json"
            with patch.object(
                daily_discovery, "discover_tiktok_profiles", return_value=(tiktok, [])
            ) as tiktok_search, patch.object(
                daily_discovery, "discover_official_x_posts", return_value=(mob_x, [])
            ) as official_search:
                report = daily_discovery.collect_daily_report(output_path=output)

            tiktok_search.assert_called_once_with(
                profiles=TIKTOK_PROFILES,
                selected_profiles=TIKTOK_PROFILES,
                max_results=48,
                max_per_profile=8,
                created_after_by_profile={},
            )
            official_search.assert_called_once_with(max_results=12)
            self.assertEqual(report["source"], "github_actions_termux_docker")
            self.assertEqual(report["summary"]["tiktok_candidates"], 1)
            self.assertEqual(report["summary"]["mob_official_candidates"], 1)
            self.assertEqual(report["summary"]["mob_official_source"], "Mob Entertainment official sources")
            self.assertEqual(report["summary"]["paid_providers_used"], [])
            self.assertEqual(report["lifecycle"]["mode"], "production")
            self.assertFalse(report["summary"]["ready_to_publish"])
            self.assertEqual(set(report["candidates"]), {"tiktok", "mob_official"})
            self.assertEqual(report["candidates"]["mob_official"][0]["source_platform"], "x")
            self.assertEqual(report["limits"]["mob_official_source_lanes"], ["x"])
            self.assertEqual(report["summary"]["mob_official_method"], "official_x_syndication_and_verified_seed_urls")
            self.assertEqual(
                report["candidates"]["mob_official"][0]["media_url"],
                "https://pbs.twimg.com/media/example.jpg",
            )
            written = output.read_text(encoding="utf-8")
            self.assertNotIn("test-only-token", written)
            self.assertEqual(
                json.loads(written)["candidates"]["mob_official"][0]["id"],
                "x-1734258130186559716",
            )

    def test_live_collection_loads_console_managed_profiles_through_authenticated_api(self):
        source_rows = [{
            "id": "tiktok:newcreator", "platform": "tiktok",
            "source_key": "newcreator", "enabled": 1, "managed": True,
            "created_at": "2026-11-05T10:00:00Z",
        }, {
            "id": "tiktok:missingcutoff", "platform": "tiktok",
            "source_key": "missingcutoff", "enabled": 1, "managed": True,
            "created_at": None,
        }, {
            "id": "tiktok:invalidcutoff", "platform": "tiktok",
            "source_key": "invalidcutoff", "enabled": 1, "managed": True,
            "created_at": "not-a-valid-timestamp",
        }]
        candidate = {
            "platform": "tiktok", "id": "7610592632179297558",
            "url": "https://www.tiktok.com/@newcreator/video/7610592632179297558",
            "author": "newcreator", "hashtags": ["#PoppyPlaytime"],
            "title": "Poppy Playtime edit", "caption": "A public edit",
            "published_at": "2026-11-05T11:00:00+00:00", "media_type": "video",
        }
        class SourceCloud:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_console_tiktok_sources(self):
                return source_rows

        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"AGENT_API_TOKEN": "test-token-that-is-never-real-123456"}), patch.object(daily_discovery, "CloudState", SourceCloud) as cloud_type, patch.object(daily_discovery, "discover_tiktok_profiles") as discover, patch.object(daily_discovery, "discover_official_x_posts", return_value=([], [])):
            def collect(**kwargs):
                self.assertEqual(kwargs["profiles"], ("newcreator",))
                self.assertEqual(kwargs["created_after_by_profile"], {"newcreator": source_rows[0]["created_at"]})
                return [{**candidate, "source_policy": resolve_source_policy("tiktok", "newcreator")}], []
            discover.side_effect = collect
            report = daily_discovery.collect_daily_report(
                output_path=Path(folder) / "report.json", load_remote_sources=True
            )
        discover.assert_called_once()
        self.assertEqual(report["summary"]["tiktok_candidates"], 1)
        self.assertEqual(report["summary"]["tiktok_source_overlay_status"], "console_sources_loaded")
        self.assertTrue(report["summary"]["tiktok_account_removals_applied"])
        self.assertEqual(report["summary"]["tiktok_missing_created_at_cutoff_count"], 2)
        missing_cutoffs = [row for row in report["attempts"] if row.get("reason") == "missing_created_at_cutoff"]
        self.assertEqual({row.get("query") for row in missing_cutoffs}, {"@missingcutoff", "@invalidcutoff"})
        self.assertEqual(report["candidates"]["tiktok"][0]["author"], "newcreator")
        set_dynamic_tiktok_sources([])

    def test_live_collection_without_worker_token_falls_back_and_keeps_mob_discovery(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            "AGENT_API_TOKEN": "",
            "AGENT_API_TOKEN_FILE": "",
        }), patch.object(
            daily_discovery, "CloudState"
        ) as cloud_type, patch.object(
            daily_discovery, "discover_tiktok_profiles", return_value=([], [])
        ) as discover, patch.object(
            daily_discovery, "discover_official_x_posts", return_value=([], [])
        ) as mob_discover:
            report = daily_discovery.collect_daily_report(
                output_path=Path(folder) / "report.json", load_remote_sources=True
            )
        cloud_type.assert_not_called()
        discover.assert_called_once_with(
            profiles=TIKTOK_PROFILES, selected_profiles=TIKTOK_PROFILES,
            max_results=48, max_per_profile=8, created_after_by_profile={},
        )
        mob_discover.assert_called_once_with(max_results=12)
        self.assertEqual(report["summary"]["tiktok_source_overlay_status"],
                         "console_sources_unavailable_using_legacy_six")
        self.assertEqual(report["summary"]["tiktok_source_overlay_reason"], "token_missing_zero")
        self.assertEqual(report["summary"]["tiktok_token_length_bucket"], "zero")
        self.assertIs(report["summary"]["tiktok_account_removals_applied"], False)
        self.assertTrue(any(row.get("reason") == "token_missing_zero"
                            for row in report["attempts"]))
        self.assertNotIn("AGENT_API_TOKEN", json.dumps(report))


    def test_live_collection_reads_console_token_from_file_mount_when_env_is_empty(self):
        token = "test-token-read-from-file-with-at-least-32-characters"
        source_rows = [{
            "id": "tiktok:filecreator", "platform": "tiktok",
            "source_key": "filecreator", "enabled": True, "managed": False,
        }]
        candidate = {
            "platform": "tiktok", "id": "7610592632179297558",
            "url": "https://www.tiktok.com/@filecreator/video/7610592632179297558",
            "author": "filecreator", "hashtags": ["#PoppyPlaytime"],
            "title": "Poppy Playtime edit", "caption": "Public edit",
            "published_at": "2026-11-05T11:00:00+00:00", "media_type": "video",
        }
        with tempfile.TemporaryDirectory() as folder:
            token_file = Path(folder) / "agent-api-token"
            token_file.write_text(token, encoding="utf-8")
            with patch.dict(os.environ, {
                "AGENT_API_TOKEN": "",
                "AGENT_API_TOKEN_FILE": str(token_file),
                "AGENT_API_BASE_URL": "https://rubika-agent.h98609294.workers.dev",
            }), patch.object(daily_discovery, "CloudState") as cloud_type, patch.object(
                daily_discovery, "discover_tiktok_profiles"
            ) as discover, patch.object(
                daily_discovery, "discover_official_x_posts", return_value=([], [])
            ):
                cloud_type.return_value.get_console_tiktok_sources.return_value = source_rows
                discover.return_value = ([{
                    **candidate,
                    "source_policy": resolve_source_policy("tiktok", "filecreator"),
                }], [])
                output_path = Path(folder) / "report.json"
                report = daily_discovery.collect_daily_report(
                    output_path=output_path, load_remote_sources=True
                )

            cloud_type.assert_called_once_with(
                "https://rubika-agent.h98609294.workers.dev",
                token=token,
                timeout_seconds=25,
            )
            self.assertEqual(report["summary"]["tiktok_source_overlay_status"], "console_sources_loaded")
            self.assertEqual(report["summary"]["tiktok_token_length_bucket"], "ok")
            self.assertIsNone(report["summary"]["tiktok_source_overlay_reason"])
            self.assertNotIn(token, output_path.read_text(encoding="utf-8"))
        set_dynamic_tiktok_sources([])

    def test_console_source_failures_record_specific_reasons_and_keep_legacy_fallback(self):
        failures = (
            (CloudStateError("control_api_http_401", status_code=401,
                             endpoint_path="/api/console/sources"), "http_401"),
            (CloudStateError("control_api_http_403", status_code=403,
                             endpoint_path="/api/console/sources"), "http_403"),
            (CloudStateError("control_api_http_404", status_code=404,
                             endpoint_path="/api/console/sources"), "http_404"),
            (CloudStateError("control_api_http_503", status_code=503,
                             endpoint_path="/api/console/sources"), "http_5xx"),
            (CloudStateError("control_api_timeout",
                             endpoint_path="/api/console/sources"), "timeout"),
            (CloudStateError("control_api_invalid_json",
                             endpoint_path="/api/console/sources"), "invalid_json"),
        )
        for error, expected_reason in failures:
            with self.subTest(reason=expected_reason), tempfile.TemporaryDirectory() as folder, patch.dict(
                os.environ, {"AGENT_API_TOKEN": "test-token-that-is-never-real-123456"}
            ), patch.object(daily_discovery, "CloudState") as cloud_type, patch.object(
                daily_discovery, "discover_tiktok_profiles", return_value=([], [])
            ) as discover, patch.object(
                daily_discovery, "discover_official_x_posts", return_value=([], [])
            ) as mob_discover:
                cloud_type.return_value.get_console_tiktok_sources.side_effect = error
                output_path = Path(folder) / "report.json"
                log_capture = io.StringIO()
                with redirect_stdout(log_capture):
                    report = daily_discovery.collect_daily_report(
                        output_path=output_path, load_remote_sources=True
                    )
                discover.assert_called_once_with(
                    profiles=TIKTOK_PROFILES, selected_profiles=TIKTOK_PROFILES,
                    max_results=48, max_per_profile=8, created_after_by_profile={},
                )
                mob_discover.assert_called_once_with(max_results=12)
                self.assertEqual(report["summary"]["tiktok_source_overlay_status"],
                                 "console_sources_unavailable_using_legacy_six")
                self.assertEqual(report["summary"]["tiktok_source_overlay_reason"], expected_reason)
                self.assertIs(report["summary"]["tiktok_account_removals_applied"], False)
                attempt = next(row for row in report["attempts"]
                               if row.get("source") == "tiktok_console_sources")
                self.assertEqual(attempt["reason"], expected_reason)
                serialized = json.dumps(report)
                output_json = output_path.read_text(encoding="utf-8")
                token = "test-token-that-is-never-real-123456"
                self.assertNotIn(token, serialized)
                self.assertNotIn(token, output_json)
                self.assertNotIn(token, log_capture.getvalue())
                self.assertNotIn("Authorization", serialized)
                self.assertNotIn(error.code, serialized)
        set_dynamic_tiktok_sources([])

    def test_console_source_failure_reason_mapping_is_bounded(self):
        cases = (
            (RuntimeError("token_missing"), "token_missing"),
            (CloudStateError("control_api_http_401", status_code=401), "http_401"),
            (CloudStateError("control_api_http_403", status_code=403), "http_403"),
            (CloudStateError("control_api_http_404", status_code=404), "http_404"),
            (CloudStateError("control_api_http_502", status_code=502), "http_5xx"),
            (CloudStateError("control_api_timeout"), "timeout"),
            (CloudStateError("control_api_invalid_json"), "invalid_json"),
            (CloudStateError("control_api_unreachable"), "unavailable"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(daily_discovery._console_source_failure_reason(error), expected)


    def test_manual_cli_cannot_push_a_report_to_github(self):
        self.assertFalse(hasattr(daily_discovery, "push_report_to_main_branch"))
        with patch.object(daily_discovery.argparse.ArgumentParser, "error", side_effect=SystemExit(2)), \
             patch.object(daily_discovery, "collect_daily_report") as collect:
            with self.assertRaises(SystemExit) as result:
                daily_discovery.main(["--push"])
        self.assertEqual(result.exception.code, 2)
        collect.assert_not_called()



if __name__ == "__main__":
    unittest.main()
