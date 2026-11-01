from __future__ import annotations

import json
import os
from pathlib import Path
import requests
import tempfile
import unittest
from unittest.mock import Mock, patch

from agent.brain.mob_x_link_processor import (
    MobXLinkError,
    _fxtwitter,
    _vxtwitter,
    build_mob_x_caption,
    clean_caption,
    download_public_media,
    normalize_published_at,
    parse_official_link,
    process_job,
    process_one,
    run_video_speech_gate,
    probe_public_sources,
    select_media,
    WorkerClient,
)


POST_ID = "2107878755079213476"
IMAGE_URL = "https://pbs.twimg.com/media/example.jpg"
VIDEO_URL = "https://video.twimg.com/ext_tw_video/example/pu/vid/avc1/1280x720/example.mp4"


def response(status=200, payload=None, headers=None, url=None):
    item = Mock()
    item.status_code = status
    item.url = url
    item.headers = headers or {}
    item.json.return_value = payload
    item.close = Mock()
    item.iter_content.return_value = iter([])
    return item


class WorkerClientRetryTests(unittest.TestCase):

    def _client(self, session):
        return WorkerClient("https://worker.example", "t" * 40, session=session)

    def test_timeout_then_success_retries_with_twenty_second_timeout(self):
        session = Mock()
        session.request.side_effect = [
            requests.Timeout("simulated timeout"),
            response(payload={"ok": True, "rules": {}}),
        ]
        with patch("agent.brain.mob_x_link_processor.time.sleep") as sleep:
            result = self._client(session).request("GET", "/api/console/rules")
        self.assertEqual(result, {"ok": True, "rules": {}})
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual([call.kwargs["timeout"] for call in session.request.call_args_list], [20, 20])
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2])

    def test_503_retries_three_attempts_then_reports_sanitized_diagnostics(self):
        session = Mock()
        session.request.side_effect = [response(status=503) for _ in range(3)]
        with patch("agent.brain.mob_x_link_processor.time.sleep") as sleep:
            with self.assertRaises(MobXLinkError) as caught:
                self._client(session).request("GET", "/api/console/rules")
        self.assertEqual(caught.exception.code, "worker_request_failed")
        self.assertEqual(caught.exception.error_class, "HTTPStatusError")
        self.assertEqual(session.request.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 5])
        self.assertIn("path=/api/console/rules", caught.exception.error_detail)
        self.assertIn("attempts=3", caught.exception.error_detail)
        self.assertIn("status=503", caught.exception.error_detail)
        self.assertIn("elapsed_ms=", caught.exception.error_detail)
        self.assertNotIn("https://worker.example", caught.exception.error_detail)
        self.assertNotIn("t" * 40, caught.exception.error_detail)

    def test_401_does_not_retry(self):
        session = Mock()
        session.request.return_value = response(status=401, payload={"error": "unauthorized"})
        with patch("agent.brain.mob_x_link_processor.time.sleep") as sleep:
            with self.assertRaises(MobXLinkError) as caught:
                self._client(session).request("GET", "/api/console/rules")
        self.assertEqual(caught.exception.code, "unauthorized")
        self.assertEqual(session.request.call_count, 1)
        sleep.assert_not_called()

    def test_claim_scopes_worker_request_to_requested_panel_link(self):
        session = Mock()
        session.request.return_value = response(payload={"ok": True, "claimed": False})
        client = self._client(session)
        self.assertIsNone(client.claim(17))
        self.assertEqual(session.request.call_args.kwargs["json"], {"panel_link_id": 17})


class MobXLinkParserTests(unittest.TestCase):

    def test_public_api_probe_does_not_run_on_schedule(self):
        workflow_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "mob-x-link-processor.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        start = workflow.index("  public-api-probe:")
        end = workflow.index("\n  process-one-confirmed-link:", start)
        probe_job = workflow[start:end]
        self.assertIn("github.event_name == 'push'", probe_job)
        self.assertIn("github.event_name == 'pull_request'", probe_job)
        self.assertIn("github.event_name == 'workflow_dispatch'", probe_job)
        self.assertNotIn("github.event_name == 'schedule'", probe_job)

    def test_dispatch_input_scopes_to_exact_confirmed_link_and_keeps_cron_fallback(self):
        workflow_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "mob-x-link-processor.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("panel_link_id:", workflow)
        self.assertIn("MOB_X_PANEL_LINK_ID: ${{ inputs.panel_link_id }}", workflow)
        self.assertIn("--panel-link-id \"$MOB_X_PANEL_LINK_ID\"", workflow)
        self.assertIn('    - cron: "*/5 * * * *"', workflow)

    def test_link_parser_requires_https_exact_official_handle_and_numeric_post_id(self):
        expected = f"https://x.com/mobgamesstudios/status/{POST_ID}"
        for url in (
            expected,
            f"https://twitter.com/mobgamesstudios/status/{POST_ID}",
            f"https://www.x.com/mobgamesstudios/status/{POST_ID}/",
            f"https://mobile.twitter.com/mobgamesstudios/status/{POST_ID}",
        ):
            with self.subTest(url=url):
                self.assertEqual(parse_official_link(url), (expected, POST_ID))
        for url in (
            f"http://x.com/mobgamesstudios/status/{POST_ID}",
            f"https://x.com/someoneelse/status/{POST_ID}",
            f"https://x.com/mobgamesstudios/status/{POST_ID}?s=20",
            "https://example.com/mobgamesstudios/status/" + POST_ID,
            "https://x.com/mobgamesstudios/status/not-a-number",
            "some text https://x.com/mobgamesstudios/status/" + POST_ID,
        ):
            with self.subTest(url=url):
                self.assertIsNone(parse_official_link(url))

    def test_fxtwitter_checks_author_and_post_id_before_returning_media(self):
        payload = {
            "tweet": {
                "id": POST_ID,
                "author": {"screen_name": "mobgamesstudios"},
                "text": "Official Mob announcement https://example.org/info",
                "media": {"photos": [{"type": "photo", "url": IMAGE_URL}]},
            }
        }
        post = _fxtwitter(payload, POST_ID)
        self.assertEqual(post["external_id"], POST_ID)
        self.assertEqual(post["media"][0]["url"], IMAGE_URL)
        self.assertEqual(post["text"].startswith("Official Mob"), True)
        with self.assertRaisesRegex(MobXLinkError, "mob_x_author_mismatch"):
            _fxtwitter({"tweet": {**payload["tweet"], "author": {"screen_name": "imposter"}}}, POST_ID)
        with self.assertRaisesRegex(MobXLinkError, "mob_x_post_id_mismatch"):
            _fxtwitter({"tweet": {**payload["tweet"], "id": "2107878755079213477"}}, POST_ID)

    def test_vxtwitter_checks_exact_author_and_id_and_extracts_video(self):
        payload = {
            "tweetID": POST_ID,
            "user_screen_name": "@mobgamesstudios",
            "text": "Official video announcement",
            "media_extended": [{"type": "video", "url": VIDEO_URL}],
        }
        post = _vxtwitter(payload, POST_ID)
        self.assertEqual(post["media"][0]["url"], VIDEO_URL)
        self.assertEqual(post["provider"], "vxtwitter")
        with self.assertRaisesRegex(MobXLinkError, "mob_x_author_mismatch"):
            _vxtwitter({**payload, "user_screen_name": "not_mob"}, POST_ID)

    def test_probe_is_read_only_and_uses_no_api_key(self):
        session = Mock()
        session.get.side_effect = [
            response(status=503),
            response(payload={
                "tweetID": POST_ID,
                "user_screen_name": "mobgamesstudios",
                "text": "Mob update",
                "media_extended": [{"type": "photo", "url": IMAGE_URL}],
            }),
        ]
        report = probe_public_sources(POST_ID, session=session)
        self.assertTrue(report["available"])
        self.assertEqual(report["usable_sources"], ["vxtwitter"])
        self.assertFalse(report["api_keys_used"])
        self.assertFalse(report["media_downloaded"])
        self.assertFalse(report["publishing_attempted"])
        self.assertEqual(len(report["results"]), 2)
        self.assertEqual(session.get.call_count, 2)
        self.assertNotIn("Mob update", str(report))

    def test_probe_rejects_invalid_id_without_network_call(self):
        session = Mock()
        with self.assertRaisesRegex(MobXLinkError, "invalid_external_id"):
            probe_public_sources("../unexpected", session=session)
        session.get.assert_not_called()

    def test_media_selection_accepts_only_expected_cdn_host(self):
        post = {"media": [{"type": "photo", "media_url_https": IMAGE_URL}]}
        with patch("agent.brain.mob_x_link_processor.socket.getaddrinfo",
                   return_value=[(None, None, None, None, ("8.8.8.8", 443))]):
            self.assertEqual(select_media(post), ("image", IMAGE_URL))
            with self.assertRaisesRegex(MobXLinkError, "mob_x_media_host_rejected"):
                select_media({"media": [{"type": "photo", "url": "https://example.com/img.jpg"}]})

    def test_oversized_video_fails_with_explicit_error_and_no_body_download(self):
        item = response(
            headers={"Content-Type": "video/mp4", "Content-Length": "1001"},
            url=VIDEO_URL,
        )
        session = Mock()
        session.get.return_value = item
        with patch("agent.brain.mob_x_link_processor.socket.getaddrinfo",
                   return_value=[(None, None, None, None, ("8.8.8.8", 443))]):
            with self.assertRaisesRegex(MobXLinkError, "mob_x_video_too_large"):
                download_public_media(VIDEO_URL, "video", "/tmp/rubika-agent-mob-x-test", session=session, max_bytes=1000)
        item.iter_content.assert_not_called()
        item.close.assert_called()

    def test_text_only_post_is_rejected_before_translation_or_any_send(self):
        cloud = Mock()
        cloud.get_seen_index.return_value = {"ids": set()}
        client = Mock()
        job = {
            "id": "job_abcdef1234567890",
            "params": {"url": f"https://x.com/mobgamesstudios/status/{POST_ID}"},
            "external_id": POST_ID,
        }
        with (
            patch("agent.brain.mob_x_link_processor.CloudState", return_value=cloud),
            patch("agent.brain.mob_x_link_processor.fetch_public_post", return_value={"text": "Text-only announcement"}),
            patch("agent.brain.mob_x_link_processor.translate_to_persian") as translate,
            patch("agent.brain.mob_x_link_processor.MegaStorage") as mega,
            patch("agent.brain.mob_x_link_processor.upload_preview_media") as upload,
            patch("agent.brain.mob_x_link_processor.send_owner_preview") as preview,
        ):
            with self.assertRaisesRegex(MobXLinkError, "mob_x_media_missing"):
                process_job(job, client)
        client.rules.assert_not_called()
        translate.assert_not_called()
        mega.assert_not_called()
        upload.assert_not_called()
        preview.assert_not_called()

    def test_caption_template_bolds_heading_and_source_and_quotes_bold_translation(self):
        empty_caption = build_mob_x_caption("   ", None)
        self.assertEqual(
            empty_caption,
            "**ماب انترتینمنت در پیج X خود پستی منتشر کرد.**",
        )
        caption = build_mob_x_caption(
            "Official update: $literal$\n> not markup",
            "ترجمهٔ رسمی\nخط دوم",
        )
        self.assertEqual(
            caption,
            "**ماب انترتینمنت در پیج خود پستی منتشر کرد و گفت:**\n\n"
            "**Official update: ＄literal＄\n› not markup**\n\n"
            "$**ترجمه متن:**\n**ترجمهٔ رسمی\nخط دوم**$",
        )

    def test_video_speech_gate_transcribes_and_returns_gate_decision(self):
        inspection = Mock(duration_seconds=12.5, has_audio=True)
        gate_result = Mock(
            decision="subtitle", reason="ok", speech_seconds=5.0,
            speech_ratio=0.4, language="en", language_prob=0.91,
        )
        with (
            patch("agent.brain.mob_x_link_processor.speech_gate.transcribe", return_value=([{"start": 0, "end": 5, "text": "speech"}], "en", 0.91)) as transcribe,
            patch("agent.brain.mob_x_link_processor.speech_gate.evaluate", return_value=gate_result) as evaluate,
        ):
            result = run_video_speech_gate("/tmp/mock-video.mp4", inspection)
        transcribe.assert_called_once_with("/tmp/mock-video.mp4", model_size="small")
        probe = evaluate.call_args.args[0]
        self.assertEqual(probe["format"]["duration"], 12.5)
        self.assertIn({"codec_type": "audio"}, probe["streams"])
        self.assertEqual(result["decision"], "subtitle")
        self.assertEqual(result["reason"], "ok")

    def test_video_without_audio_skips_asr_but_still_runs_the_gate(self):
        inspection = Mock(duration_seconds=12.5, has_audio=False)
        gate_result = Mock(
            decision="skip", reason="no_audio_track", speech_seconds=0.0,
            speech_ratio=0.0, language=None, language_prob=0.0,
        )
        with (
            patch("agent.brain.mob_x_link_processor.speech_gate.transcribe") as transcribe,
            patch("agent.brain.mob_x_link_processor.speech_gate.evaluate", return_value=gate_result) as evaluate,
        ):
            result = run_video_speech_gate("/tmp/silent-video.mp4", inspection)
        transcribe.assert_not_called()
        evaluate.assert_called_once()
        self.assertEqual(result["decision"], "skip")
        self.assertEqual(result["reason"], "no_audio_track")

    def test_video_speech_gate_fails_closed_and_preserves_safe_exception_details(self):
        inspection = Mock(duration_seconds=12.5, has_audio=True)
        secret = "sensitive-hf-token-value"
        message = (
            "model download failed with token=" + secret
            + " at https://huggingface.co/private/model?token=hidden "
            + ("retry download " * 25)
        )
        with (
            patch.dict(os.environ, {"HF_TOKEN": secret}),
            patch(
                "agent.brain.mob_x_link_processor.speech_gate.transcribe",
                side_effect=RuntimeError(message),
            ),
            patch("builtins.print") as log,
        ):
            with self.assertRaisesRegex(MobXLinkError, "mob_x_speech_gate_failed") as caught:
                run_video_speech_gate("/tmp/broken-video.mp4", inspection)

        self.assertEqual(caught.exception.error_class, "RuntimeError")
        self.assertIsNotNone(caught.exception.error_detail)
        self.assertLessEqual(len(caught.exception.error_detail), 150)
        self.assertNotIn(secret, caught.exception.error_detail)
        self.assertNotIn("https://", caught.exception.error_detail)
        self.assertNotIn("token=hidden", caught.exception.error_detail)
        diagnostic = json.loads(log.call_args.args[0])
        self.assertEqual(diagnostic["error_code"], "mob_x_speech_gate_failed")
        self.assertEqual(diagnostic["error_class"], "RuntimeError")
        self.assertEqual(diagnostic["error_detail"], caught.exception.error_detail)

    def test_process_one_includes_speech_gate_error_detail_in_report(self):
        client = Mock()
        client.claim.return_value = {"id": "job_abcdef1234567890"}
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "report.json"
            with (
                patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}),
                patch("agent.brain.mob_x_link_processor.WorkerClient", return_value=client),
                patch(
                    "agent.brain.mob_x_link_processor.process_job",
                    side_effect=MobXLinkError(
                        "mob_x_speech_gate_failed",
                        error_class="RuntimeError",
                        error_detail="model download failed",
                    ),
                ),
                patch("builtins.print"),
            ):
                self.assertEqual(process_one(str(report_path)), 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["error_code"], "mob_x_speech_gate_failed")
        self.assertEqual(report["error_class"], "RuntimeError")
        self.assertEqual(report["error_detail"], "model download failed")

    def test_caption_is_plain_and_published_time_is_normalized(self):
        self.assertEqual(clean_caption(" **Hello**\n\nhttps://example.org/a "), "Hello")
        self.assertEqual(
            normalize_published_at("2026-10-20T12:34:56+02:00"),
            "2026-10-20T10:34:56Z",
        )
        self.assertIsNone(normalize_published_at("not-a-date"))




class MobXSubtitleWiringTests(unittest.TestCase):
    @staticmethod
    def _job():
        return {
            "id": "job_abcdef1234567890",
            "params": {"url": f"https://x.com/mobgamesstudios/status/{POST_ID}"},
            "external_id": POST_ID,
        }

    def _run_video_job(self, gate, *, pipeline_error=None):
        from contextlib import ExitStack

        events = []
        cloud = Mock()
        cloud.get_seen_index.return_value = {"ids": set()}
        cloud.upsert_item.return_value = {"item_id": "mob_official:" + POST_ID}
        client = Mock()
        client.rules.side_effect = lambda: events.append(("rules",)) or {
            "translate_enabled": False, "caption_max_chars": 900,
        }
        storage = Mock()
        storage.upload.side_effect = lambda path, tier: events.append(("mega", path)) or "storage-key"
        source_inspection = Mock(ok=True, duration_seconds=12.0, has_audio=True)
        output_inspection = Mock(ok=True, duration_seconds=12.2, has_audio=True)

        def inspect(path, *args, **kwargs):
            events.append(("inspect", Path(path).name))
            return output_inspection if Path(path).name == "subtitled.mp4" else source_inspection

        def build(video, workdir, translate_fn, **kwargs):
            events.append(("subtitle", video))
            translated = translate_fn("Rules\n\n0|Hello there", max_tokens=1200)
            self.assertEqual(translated, "0|ترجمه 0")
            kwargs["transcribe_fn"](video)
            if pipeline_error:
                raise pipeline_error
            return {"out": "/tmp/subtitled.mp4", "cues": 1, "batches": 1,
                    "missing": 0, "size": 20, "duration_ms": 12200,
                    "asr_initial_model": "medium", "asr_model": "small",
                    "asr_fallback_reason": "medium_runtime_exceeded_15m",
                    "asr_elapsed_seconds": 920, "asr_medium_elapsed_seconds": 901,
                    "asr_fallback_elapsed_seconds": 19,
                    "asr_raw_cues": 3, "asr_clean_cues": 3,
                    "asr_dropped_duplicates": 0, "asr_max_cue_ms": 47000,
                    "asr_first_start_ms": 0, "asr_last_end_ms": 64000,
                    "asr_cue_coverage_ratio": 62000 / 85000,
                    "asr_timeline": [[0, 47000, 5], [48000, 55000, 6], [56000, 64000, 5]],
                    "asr_quality_warning": "long_cue_duration",
                    "asr_unapproved_transcript": "must not enter the sanitized report"}

        with ExitStack() as stack:
            stack.enter_context(patch.dict("os.environ", {
                "RUBIKA_TOKEN": "test-rubika-token",
                "RUBIKA_OWNER_CHAT_ID": "test-owner-chat",
            }))
            cloud_class = stack.enter_context(patch("agent.brain.mob_x_link_processor.CloudState"))
            cloud_class.return_value = cloud
            cloud_class.prepare_staged_payload.return_value = {}
            # Keep the factory patch above deterministic for the class static helper and instance.
            stack.enter_context(patch("agent.brain.mob_x_link_processor.fetch_public_post",
                                      return_value={"text": "Official video update", "title": "Mob update"}))
            stack.enter_context(patch("agent.brain.mob_x_link_processor.select_media",
                                      return_value=("video", VIDEO_URL)))
            stack.enter_context(patch("agent.brain.mob_x_link_processor.download_public_media",
                                      return_value="/tmp/source.mp4"))
            stack.enter_context(patch("agent.brain.mob_x_link_processor.inspect_media", side_effect=inspect))
            stack.enter_context(patch("agent.brain.mob_x_link_processor.run_video_speech_gate",
                                      side_effect=lambda *_: events.append(("speech_gate",)) or gate))
            pipeline_mock = stack.enter_context(patch(
                "agent.brain.mob_x_link_processor.subtitle_pipeline.build_subtitled_video",
                side_effect=build,
            ))
            transcribe_mock = stack.enter_context(patch(
                "agent.brain.mob_x_link_processor.subtitle_pipeline.transcribe",
                return_value=[(0, 1000, "Hello there")],
            ))
            stack.enter_context(patch("agent.brain.mob_x_link_processor.translate_to_persian",
                                      side_effect=lambda text, **kwargs: events.append(("translate", text)) or "0|ترجمه 0"))
            stack.enter_context(patch("agent.brain.mob_x_link_processor.evaluate_official_media",
                                      return_value=Mock(decision="eligible")))
            stack.enter_context(patch("agent.brain.mob_x_link_processor.upload_preview_media",
                                      side_effect=lambda token, path, kind: events.append(("rubika_upload", path)) or "file-id"))
            stack.enter_context(patch("agent.brain.mob_x_link_processor.send_owner_preview",
                                      side_effect=lambda *args: events.append(("owner_preview", args[3]) or "preview-message")))
            result = process_job(self._job(), client, storage=storage)
        return result, events, pipeline_mock, storage, cloud, transcribe_mock

    def test_speech_video_is_subtitled_and_rechecked_before_mega_preview(self):
        result, events, pipeline, storage, cloud, transcribe_mock = self._run_video_job(
            {"decision": "subtitle", "reason": "ok", "language": "en"}
        )
        names = [event[0] for event in events]
        self.assertEqual(names.count("rules"), 1)
        self.assertLess(names.index("rules"), names.index("inspect"))
        self.assertLess(names.index("speech_gate"), names.index("subtitle"))
        output_inspection_index = next(
            i for i, event in enumerate(events) if event == ("inspect", "subtitled.mp4")
        )
        self.assertLess(names.index("subtitle"), output_inspection_index)
        self.assertLess(output_inspection_index, names.index("mega"))
        self.assertLess(names.index("mega"), names.index("rubika_upload"))
        self.assertLess(names.index("rubika_upload"), names.index("owner_preview"))
        self.assertEqual(storage.upload.call_args.args, ("/tmp/subtitled.mp4", "official"))
        self.assertEqual(result["subtitle_processing"]["status"], "burned")
        self.assertEqual(result["subtitle_processing"]["missing"], 0)
        self.assertEqual(result["subtitle_processing"]["asr_model"], "small")
        self.assertEqual(result["subtitle_processing"]["asr_fallback_reason"], "medium_runtime_exceeded_15m")
        diagnostics = result["subtitle_processing"]
        for key in ("asr_raw_cues", "asr_clean_cues", "asr_dropped_duplicates",
                    "asr_max_cue_ms", "asr_first_start_ms", "asr_last_end_ms",
                    "asr_cue_coverage_ratio", "asr_timeline", "asr_quality_warning"):
            self.assertIn(key, diagnostics)
        self.assertEqual(diagnostics["asr_timeline"], [[0, 47000, 5], [48000, 55000, 6], [56000, 64000, 5]])
        self.assertEqual(diagnostics["asr_quality_warning"], "long_cue_duration")
        self.assertNotIn("asr_unapproved_transcript", diagnostics)
        transcribe_mock.assert_called_once_with("/tmp/source.mp4", model_size="medium", language="en")
        preview_caption = next(event[1] for event in events if event[0] == "owner_preview")
        self.assertIn("زیرنویس فارسی: دارد (1 خط)", preview_caption)
        self.assertIn("مدل زیرنویس به small برگشت", preview_caption)
        self.assertNotIn("زیرنویس فارسی", cloud.upsert_item.call_args.args[0]["caption"])
        self.assertEqual(pipeline.call_args.kwargs["font"], "Estedad")
        self.assertEqual(pipeline.call_args.kwargs["max_missing_ratio"], 0.0)

    def test_video_without_recognizable_speech_skips_subtitles_and_uses_original(self):
        result, events, pipeline, storage, cloud, _transcribe_mock = self._run_video_job(
            {"decision": "skip", "reason": "no_speech", "language": None}
        )
        pipeline.assert_not_called()
        self.assertEqual(storage.upload.call_args.args, ("/tmp/source.mp4", "official"))
        self.assertEqual(result["subtitle_processing"]["status"], "skipped_by_speech_gate")
        preview_caption = next(event[1] for event in events if event[0] == "owner_preview")
        self.assertIn("بدون زیرنویس: ویدیو گفتار ندارد", preview_caption)
        self.assertNotIn("زیرنویس فارسی", cloud.upsert_item.call_args.args[0]["caption"])
        self.assertNotIn("subtitle", [event[0] for event in events])

    def test_no_cues_preview_reports_no_cues_without_changing_staged_caption(self):
        from scripts.subtitle_pipeline import SubtitleError
        result, events, pipeline, storage, cloud, _transcribe_mock = self._run_video_job(
            {"decision": "subtitle", "reason": "ok", "language": "en"},
            pipeline_error=SubtitleError("no_cues"),
        )
        self.assertEqual(result["subtitle_processing"]["status"], "skipped_no_detectable_speech")
        preview_caption = next(event[1] for event in events if event[0] == "owner_preview")
        self.assertIn("بدون زیرنویس: گفتار تشخیص داده نشد (no_cues)", preview_caption)
        self.assertEqual(storage.upload.call_args.args, ("/tmp/source.mp4", "official"))
        self.assertNotIn("no_cues", cloud.upsert_item.call_args.args[0]["caption"])

    def test_audio_decode_failed_stops_and_preserves_detail_for_owner_pv(self):
        from scripts.subtitle_pipeline import SubtitleError

        with self.assertRaises(MobXLinkError) as caught:
            self._run_video_job(
                {"decision": "subtitle", "reason": "ok", "language": "en"},
                pipeline_error=SubtitleError(
                    "audio_decode_failed", "ffmpeg could not decode source audio",
                ),
            )

        self.assertEqual(caught.exception.code, "mob_x_subtitle_audio_decode_failed")
        self.assertEqual(caught.exception.error_class, "SubtitleError")
        self.assertEqual(caught.exception.error_detail, "ffmpeg could not decode source audio")

    def test_failed_subtitle_translation_degrades_to_original_video_with_owner_label(self):
        from scripts.subtitle_pipeline import SubtitleError
        result, events, _pipeline, storage, cloud, _t = self._run_video_job(
            {"decision": "subtitle", "reason": "ok", "language": "en"},
            pipeline_error=SubtitleError("translation_incomplete"),
        )
        self.assertEqual(result["status"], "ready_for_review")
        self.assertEqual(result["subtitle_processing"],
                         {"status": "failed", "reason": "translation_incomplete"})
        # Original (unsubtitled) video is what gets stored and previewed.
        self.assertEqual(storage.upload.call_args.args, ("/tmp/source.mp4", "official"))
        preview_caption = next(event[1] for event in events if event[0] == "owner_preview")
        self.assertIn("زیرنویس ناموفق (translation_incomplete)", preview_caption)
        self.assertNotIn("زیرنویس ناموفق", cloud.upsert_item.call_args.args[0]["caption"])

    def test_unexpected_subtitle_exception_degrades_instead_of_failing_post(self):
        result, events, _pipeline, storage, _cloud, _t = self._run_video_job(
            {"decision": "subtitle", "reason": "ok", "language": "en"},
            pipeline_error=RuntimeError("boom"),
        )
        self.assertEqual(result["subtitle_processing"],
                         {"status": "failed", "reason": "processing_error"})
        self.assertEqual(storage.upload.call_args.args, ("/tmp/source.mp4", "official"))
        preview_caption = next(event[1] for event in events if event[0] == "owner_preview")
        self.assertIn("زیرنویس ناموفق (processing_error)", preview_caption)

    def test_preview_caption_failed_subtitle_label_is_sanitized(self):
        from agent.brain.mob_x_link_processor import build_owner_preview_caption
        text = build_owner_preview_caption(
            "cap", "video", {"status": "failed", "reason": "Bad Reason!<x>"},
        )
        self.assertIn("زیرنویس ناموفق (badreasonx)", text)
        self.assertTrue(text.startswith("cap\n\n"))


if __name__ == "__main__":
    unittest.main()
