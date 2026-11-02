import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import subtitle_pipeline as sp  # noqa: E402


def fake_translate(prompt, *, max_tokens):
    lines = [ln for ln in prompt.splitlines() if "|" in ln and ln.split("|", 1)[0].isdigit()]
    return "\n".join(f"{ln.split('|', 1)[0]}|ترجمه {ln.split('|', 1)[0]}" for ln in lines)


def make_run(out_size=10, dur_in=5000, dur_out=5000, rc=0):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            d = dur_out if cmd[-1].endswith("subtitled.mp4") else dur_in
            return SimpleNamespace(returncode=0, stdout=json.dumps({"format": {"duration": d / 1000}}), stderr="")
        if rc == 0:
            Path(cmd[-1]).write_bytes(b"x" * out_size)
        return SimpleNamespace(returncode=rc, stdout="", stderr="boom")

    run.calls = calls
    return run


CUES = [(0, 1500, "Hello there"), (1600, 3000, "Welcome to the show"), (3100, 4800, "Goodbye")]


class CleanCues(unittest.TestCase):
    def test_dedupe_overlap_min_duration(self):
        out = sp.clean_cues([(0, 2000, "Hi"), (1000, 1100, "There"), (1200, 1300, "there"), (500, 600, "")])
        self.assertEqual([c[2] for c in out], ["Hi", "There"])
        self.assertEqual(out[0][1], 1000)
        self.assertGreaterEqual(out[1][1] - out[1][0], sp.MIN_CUE_MS)

    def test_duplicate_count_helper_matches_clean_cues_without_changing_output(self):
        raw = [(0, 2000, "Echo"), (2000, 4000, "echo"),
               (4000, 6000, " ECHO "), (7000, 9000, "Return")]
        self.assertEqual(sp.clean_cues(raw), [(0, 2000, "Echo"), (7000, 9000, "Return")])
        self.assertEqual(sp.count_dropped_duplicates(raw), 2)


class Translate(unittest.TestCase):
    def test_happy_path_and_cap(self):
        seen = []

        def fn(prompt, *, max_tokens):
            seen.append(max_tokens)
            return fake_translate(prompt, max_tokens=max_tokens)

        out, stats = sp.translate_cues(CUES, fn)
        self.assertEqual([c[2] for c in out], ["ترجمه 0", "ترجمه 1", "ترجمه 2"])
        self.assertEqual(stats, {"batches": 1, "calls": 1, "missing": 0})
        self.assertEqual(seen, [sp.MAX_OUT_TOKENS])

    def test_few_missing_keep_original(self):
        cues = [(i * 1000, i * 1000 + 800, f"line {i}") for i in range(10)]
        out, stats = sp.translate_cues(cues, lambda p, **k: "\n".join(f"{i}|فا {i}" for i in range(9)))
        self.assertEqual(stats["missing"], 1)
        self.assertEqual(out[9][2], "line 9")

    def test_incomplete_raises(self):
        with self.assertRaises(sp.SubtitleError) as cm:
            sp.translate_cues(CUES, lambda p, **k: "garbage")
        self.assertEqual(cm.exception.code, "translation_incomplete")

    def test_truncated_60_cue_batch_fails_with_one_missing_translation(self):
        cues = [(i * 1000, i * 1000 + 800, f"Line {i}") for i in range(60)]
        prompts = []

        def truncated(prompt, *, max_tokens):
            prompts.append((prompt, max_tokens))
            numbered = [
                line.split("|", 1)[0]
                for line in prompt.splitlines()
                if "|" in line and line.split("|", 1)[0].isdigit()
            ]
            return "\n".join(f"{number}|ترجمه" for number in numbered[:-1])

        with self.assertRaises(sp.SubtitleError) as caught:
            sp.translate_cues(cues, truncated, max_missing_ratio=0.0)
        self.assertEqual(sp.MAX_OUT_TOKENS, 8192)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0][1], 8192)
        self.assertEqual(caught.exception.code, "translation_incomplete")
        self.assertEqual(caught.exception.detail, "1/60 missing")

    def test_burn_in_builder_rejects_any_missing_subtitle_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "source.mp4"
            video.write_bytes(b"v")
            with self.assertRaises(sp.SubtitleError) as caught:
                sp.build_subtitled_video(
                    str(video), str(Path(tmp) / "work"),
                    lambda prompt, **kwargs: "0|ترجمه",
                    transcribe_fn=lambda _path: list(CUES),
                    run=make_run(),
                )
        self.assertEqual(caught.exception.code, "translation_incomplete")

    def test_too_long_raises_before_any_call(self):
        cues = [(i * 1000, i * 1000 + 800, "word " * 20) for i in range(200)]
        called = []
        with self.assertRaises(sp.SubtitleError) as cm:
            sp.translate_cues(cues, lambda p, **k: called.append(1) or "")
        self.assertEqual(cm.exception.code, "too_long")
        self.assertEqual(called, [])

    def test_provider_error_propagates(self):
        def boom(prompt, **k):
            raise RuntimeError("all providers failed")

        with self.assertRaises(RuntimeError):
            sp.translate_cues(CUES, boom)


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wd = str(Path(self.tmp.name) / "work")
        self.video = str(Path(self.tmp.name) / "in.mp4")
        Path(self.video).write_bytes(b"v")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, run, cues=CUES):
        return sp.build_subtitled_video(self.video, self.wd, fake_translate,
                                        transcribe_fn=lambda v: list(cues), run=run)

    def test_happy_path(self):
        run = make_run()
        res = self.build(run)
        self.assertEqual(res["cues"], 3)
        self.assertEqual(res["missing"], 0)
        self.assertTrue(Path(res["srt"]).read_text(encoding="utf-8").startswith("1\n"))
        self.assertIn("Estedad", Path(res["ass"]).read_text(encoding="utf-8"))
        burn = next(c for c in run.calls if c[0] == "ffmpeg")
        self.assertIn("-c:a", burn)
        self.assertIsNone(res["asr_quality_warning"])

    def test_sparse_real_case_reports_timeline_and_long_cue_warning(self):
        raw = [(0, 47000, "first"), (48000, 55000, "second"), (56000, 64000, "third")]
        result = sp.build_subtitled_video(
            self.video, self.wd, fake_translate, transcribe_fn=lambda _path: raw,
            run=make_run(dur_in=85000, dur_out=85000),
        )
        self.assertEqual(result["asr_raw_cues"], 3)
        self.assertEqual(result["asr_clean_cues"], 3)
        self.assertEqual(result["asr_dropped_duplicates"], 0)
        self.assertEqual(result["asr_max_cue_ms"], 47000)
        self.assertEqual(result["asr_first_start_ms"], 0)
        self.assertEqual(result["asr_last_end_ms"], 64000)
        self.assertAlmostEqual(result["asr_cue_coverage_ratio"], 62000 / 85000, places=4)
        self.assertEqual(result["asr_timeline"], [[0, 47000, 5], [48000, 55000, 6], [56000, 64000, 5]])
        self.assertLessEqual(len(result["asr_timeline"]), 200)
        self.assertNotIn("first", repr(result["asr_timeline"]))
        self.assertEqual(result["asr_quality_warning"], "long_cue_duration")

    def test_duplicate_loop_is_counted_and_low_coverage_warns_without_long_cue(self):
        raw = [(0, 2000, "Echo"), (2000, 4000, "echo"),
               (4000, 6000, " ECHO "), (7000, 9000, "Return")]
        result = sp.build_subtitled_video(
            self.video, self.wd, fake_translate, transcribe_fn=lambda _path: raw,
            run=make_run(dur_in=15000, dur_out=15000),
        )
        self.assertEqual(result["asr_raw_cues"], 4)
        self.assertEqual(result["asr_clean_cues"], 2)
        self.assertEqual(result["asr_dropped_duplicates"], 2)
        self.assertEqual(result["asr_max_cue_ms"], 2000)
        self.assertAlmostEqual(result["asr_cue_coverage_ratio"], 4000 / 15000, places=4)
        self.assertEqual(result["asr_timeline"], [[0, 2000, 4], [7000, 9000, 6]])
        self.assertEqual(result["asr_quality_warning"], "low_cue_coverage")

    def test_translation_error_keeps_numeric_asr_diagnostics_without_cue_text(self):
        raw = sp.TranscriptionResult(
            [(0, 1000, "hello"), (1000, 2000, "HELLO"), (3000, 4000, "tail")],
            {
                "asr_initial_model": "medium",
                "asr_model": "small",
                "asr_fallback_reason": "medium_runtime_exceeded_15m",
                "asr_elapsed_seconds": 19.5,
            },
        )

        def truncated(prompt, *, max_tokens):
            return "0|ترجمه"

        with self.assertRaises(sp.SubtitleError) as caught:
            sp.build_subtitled_video(
                self.video, self.wd, truncated, transcribe_fn=lambda _path: raw,
                run=make_run(dur_in=8000), max_missing_ratio=0.0,
            )

        diagnostics = caught.exception.diagnostics
        self.assertEqual(caught.exception.code, "translation_incomplete")
        self.assertEqual(caught.exception.detail, "1/2 missing")
        self.assertEqual(diagnostics["asr_initial_model"], "medium")
        self.assertEqual(diagnostics["asr_model"], "small")
        self.assertEqual(diagnostics["asr_elapsed_seconds"], 19.5)
        self.assertEqual(diagnostics["asr_raw_cues"], 3)
        self.assertEqual(diagnostics["asr_clean_cues"], 2)
        self.assertEqual(diagnostics["asr_dropped_duplicates"], 1)
        self.assertEqual(diagnostics["asr_max_cue_ms"], 1000)
        self.assertEqual(diagnostics["asr_first_start_ms"], 0)
        self.assertEqual(diagnostics["asr_last_end_ms"], 4000)
        self.assertEqual(diagnostics["asr_cue_coverage_ratio"], 0.25)
        self.assertEqual(diagnostics["asr_timeline"], [[0, 1000, 5], [3000, 4000, 4]])
        self.assertEqual(diagnostics["asr_quality_warning"], "low_cue_coverage")
        self.assertNotIn("hello", repr(diagnostics))
        self.assertNotIn("tail", repr(diagnostics))

    def test_no_cues(self):
        with self.assertRaises(sp.SubtitleError) as cm:
            self.build(make_run(), cues=[])
        self.assertEqual(cm.exception.code, "no_cues")

    def test_burn_failed(self):
        with self.assertRaises(sp.SubtitleError) as cm:
            self.build(make_run(rc=1))
        self.assertEqual(cm.exception.code, "burn_failed")

    def test_output_too_large(self):
        old = sp.MAX_OUT_BYTES
        sp.MAX_OUT_BYTES = 5
        try:
            with self.assertRaises(sp.SubtitleError) as cm:
                self.build(make_run(out_size=10))
        finally:
            sp.MAX_OUT_BYTES = old
        self.assertEqual(cm.exception.code, "output_too_large")

    def test_duration_mismatch(self):
        with self.assertRaises(sp.SubtitleError) as cm:
            self.build(make_run(dur_out=9000))
        self.assertEqual(cm.exception.code, "duration_mismatch")


class Transcribe(unittest.TestCase):
    def test_segments_to_cues_uses_beam_five_and_vad(self):
        def seg(s, e, t):
            return SimpleNamespace(
                start=s, end=e, text=t,
                words=[SimpleNamespace(start=s, end=e, word=t, probability=0.99)] if t.strip() else [],
            )
        seen = []
        def run_asr(*args, **kwargs):
            seen.append(kwargs)
            return [seg(0.0, 1.2, " Hi  you "), seg(1.3, 2.0, "  ")], None
        model = SimpleNamespace(transcribe=run_asr)
        result = sp.transcribe("x.mp4", model_factory=lambda: model)
        self.assertEqual(result, [(0, 1200, "Hi you")])
        self.assertEqual(seen[0]["beam_size"], 5)
        self.assertTrue(seen[0]["vad_filter"])
        self.assertTrue(seen[0]["word_timestamps"])
        self.assertEqual(seen[0]["hallucination_silence_threshold"], 2)
        self.assertEqual(result.report["asr_model"], "medium")
        self.assertIsNone(result.report["asr_fallback_reason"])

    def test_word_timestamps_anchor_cues_and_wordless_segments_are_dropped(self):
        seen = []

        def run_asr(*args, **kwargs):
            seen.append(kwargs)
            return [
                SimpleNamespace(
                    start=0.0, end=3.0, text=" Hello   there ",
                    words=[
                        SimpleNamespace(start=0.18, end=0.44, word=" Hello", probability=0.98),
                        SimpleNamespace(start=1.12, end=1.54, word=" there", probability=0.97),
                    ],
                ),
                SimpleNamespace(start=3.0, end=4.0, text="hallucinated without word data", words=None),
                SimpleNamespace(start=4.0, end=5.0, text="also wordless", words=[]),
            ], None

        result = sp.transcribe(
            "x.mp4", model_factory=lambda: SimpleNamespace(transcribe=run_asr),
        )

        self.assertEqual(result, [(180, 1540, "Hello there")])
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0]["word_timestamps"])
        self.assertEqual(seen[0]["hallucination_silence_threshold"], 2)

    def test_default_model_is_medium_cpu_int8(self):
        seg = lambda s, e, t: SimpleNamespace(
            start=s, end=e, text=t,
            words=[SimpleNamespace(start=s, end=e, word=t, probability=0.99)],
        )  # noqa: E731
        constructed, transcribed = [], []

        class FakeWhisperModel:
            def __init__(self, size, **kwargs):
                constructed.append((size, kwargs))

            def transcribe(self, audio, **kwargs):
                transcribed.append((audio, kwargs))
                return [seg(0.0, 1.0, "hello")], None

        with patch.object(sp, "decode_audio_16k", return_value="mocked-pcm"):
            with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=FakeWhisperModel)}):
                result = sp.transcribe("x.mp4")
        self.assertEqual(constructed, [("medium", {"device": "cpu", "compute_type": "int8"})])
        self.assertEqual(transcribed[0][0], "mocked-pcm")
        self.assertEqual(transcribed[0][1]["beam_size"], 5)
        self.assertTrue(transcribed[0][1]["vad_filter"])
        self.assertTrue(transcribed[0][1]["word_timestamps"])
        self.assertEqual(transcribed[0][1]["hallucination_silence_threshold"], 2)
        self.assertEqual(result.report["asr_model"], "medium")

    @staticmethod
    def _segments(rows):
        return [
            SimpleNamespace(
                start=start_ms / 1000, end=end_ms / 1000, text=text,
                words=[SimpleNamespace(
                    start=start_ms / 1000, end=end_ms / 1000,
                    word=text, probability=0.99,
                )],
            )
            for start_ms, end_ms, text in rows
        ]

    def test_low_density_85_second_case_runs_b_and_c_and_selects_most_characters(self):
        rounds = [
            [(180, 47480, "a" * 74), (47480, 63780, "b" * 75)],
            [(0, 10000, "B" * 100), (10000, 20000, "C" * 100),
             (20000, 30000, "D" * 100)],
            [(0, 10000, "E" * 60), (10000, 20000, "F" * 60)],
        ]
        calls = []
        def factory():
            rows = rounds[len(calls)]
            model = SimpleNamespace(transcribe=lambda audio, **kwargs: (
                calls.append({"audio": audio, **kwargs}) or iter(self._segments(rows)), None
            ))
            return model

        with patch.object(sp, "decode_audio_16k", return_value="highpass-only-pcm") as decode:
            result = sp.transcribe(
                "input.mp4", model_factory=factory, speech_seconds=63.6, duration_ms=85000,
            )

        self.assertEqual(result.report["asr_passes"][0]["raw_cues"], 2)
        self.assertEqual(result.report["asr_passes"][0]["chars"], 149)
        self.assertEqual(result.report["asr_selected_pass"], "B")
        self.assertEqual(result.report["chars_per_speech_second"], round(149 / 63.6, 3))
        self.assertEqual([row["name"] for row in result.report["asr_passes"]], ["A", "B", "C"])
        self.assertEqual(result.report["asr_passes"][1]["chars"], 300)
        self.assertEqual(result.report["asr_passes"][2]["chars"], 120)
        self.assertEqual(len(calls), 3)
        for call in calls:
            self.assertTrue(call["word_timestamps"])
            self.assertEqual(call["hallucination_silence_threshold"], 2)
        self.assertTrue(calls[0]["vad_filter"])
        self.assertFalse(calls[1]["vad_filter"])
        self.assertTrue(calls[2]["vad_filter"])
        self.assertEqual(calls[2]["vad_parameters"]["threshold"], 0.2)
        self.assertEqual(calls[0]["audio"], "input.mp4")
        self.assertEqual(calls[1]["audio"], "input.mp4")
        self.assertEqual(calls[2]["audio"], "highpass-only-pcm")
        decode.assert_called_once()
        self.assertFalse(decode.call_args.kwargs["normalize"])
        self.assertGreater(decode.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(decode.call_args.kwargs["timeout"], 600)

    def test_reports_segment_ranges_word_filter_reasons_and_long_cue_free_gaps_per_pass(self):
        def segment(start, end, text, words):
            return SimpleNamespace(start=start, end=end, text=text, words=words)

        rounds = [
            [
                segment(20.0, 22.0, "private missing words text", None),
                segment(40.0, 43.0, "private invalid words text", [
                    SimpleNamespace(start=42.5, end=41.0, word="invalid"),
                ]),
                segment(72.0, 80.0, "private invalid word time", [
                    SimpleNamespace(start=79.5, end=79.5, word=" invalid"),
                ]),
                segment(5.0, 6.0, "   ", []),
                segment(9.0, 13.0, "private valid cue text", [
                    SimpleNamespace(start=10.3, end=11.1, word=" private"),
                    SimpleNamespace(start=11.5, end=12.2, word=" words"),
                ]),
            ],
            [
                segment(20.0, 21.0, "private B one", [
                    SimpleNamespace(start=20.0, end=21.0, word=" B"),
                ]),
                segment(40.0, 41.0, "private B two", [
                    SimpleNamespace(start=40.0, end=41.0, word=" B"),
                ]),
            ],
            [
                segment(72.0, 73.0, "private C one", [
                    SimpleNamespace(start=72.0, end=73.0, word=" C"),
                ]),
            ],
        ]
        index = {"value": 0}

        def factory():
            rows = rounds[index["value"]]
            index["value"] += 1
            return SimpleNamespace(transcribe=lambda audio, **kwargs: (iter(rows), None))

        with patch.object(sp, "decode_audio_16k", return_value="highpass-only-pcm"):
            result = sp.transcribe(
                "input.mp4", model_factory=factory, speech_seconds=63.6, duration_ms=85000,
            )

        passes = result.report["asr_passes"]
        a, b, c = passes
        self.assertEqual(a["name"], "A")
        self.assertEqual(a["segments_before_word_timing_count"], 5)
        self.assertEqual(a["segments_before_word_timing_ranges_ms"], [
            [20000, 22000], [40000, 43000], [72000, 80000], [5000, 6000], [9000, 13000],
        ])
        self.assertEqual(a["segments_after_word_timing_count"], 1)
        self.assertEqual(a["segments_after_word_timing_ranges_ms"], [[9000, 13000]])
        dropped = {item["reason"]: item for item in a["segments_dropped_word_timing"]}
        self.assertEqual(dropped["missing_word_timing"]["count"], 1)
        self.assertEqual(dropped["missing_word_timing"]["ranges_ms"], [[20000, 22000]])
        self.assertEqual(dropped["invalid_word_timing"]["count"], 2)
        self.assertEqual(dropped["invalid_word_timing"]["ranges_ms"], [[40000, 43000], [72000, 80000]])
        self.assertEqual(dropped["empty_segment_text"]["count"], 1)
        self.assertEqual(dropped["empty_segment_text"]["ranges_ms"], [[5000, 6000]])
        self.assertTrue(a["cue_free_gaps_over_3s"])
        self.assertIn([0, 10300, 10300], a["cue_free_gaps_over_3s"])
        self.assertIn([12200, 85000, 72800], a["cue_free_gaps_over_3s"])
        self.assertEqual(b["segments_before_word_timing_count"], 2)
        self.assertEqual(b["segments_after_word_timing_count"], 2)
        self.assertEqual(b["segments_dropped_word_timing"], [])
        self.assertEqual(c["segments_before_word_timing_count"], 1)
        self.assertEqual(c["segments_after_word_timing_count"], 1)
        self.assertNotIn("private", repr(result.report))
        self.assertNotIn("invalid", repr(result.report))

    def test_healthy_asr_does_not_run_extra_passes(self):
        calls = []
        def factory():
            return SimpleNamespace(transcribe=lambda audio, **kwargs: (
                calls.append(kwargs) or iter(self._segments([(0, 9000, "x" * 450)])), None
            ))

        with patch.object(sp, "decode_audio_16k") as decode:
            result = sp.transcribe(
                "input.mp4", model_factory=factory, speech_seconds=10, duration_ms=10000,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result.report["asr_passes"][0]["name"], "A")
        self.assertEqual(result.report["asr_selected_pass"], "A")
        decode.assert_not_called()

    def test_duplicate_loop_pass_is_never_selected_over_a_clean_pass(self):
        rounds = [
            [(0, 1000, "A" * 50)],
            [(0, 1000, "B" * 50), (1000, 2000, "B" * 50), (2000, 3000, "B" * 50)],
            [(0, 1000, "C" * 35), (1000, 2000, "D" * 35)],
        ]
        calls = []
        def factory():
            rows = rounds[len(calls)]
            return SimpleNamespace(transcribe=lambda audio, **kwargs: (
                calls.append(kwargs) or iter(self._segments(rows)), None
            ))

        with patch.object(sp, "decode_audio_16k", return_value="highpass-only-pcm"):
            result = sp.transcribe(
                "input.mp4", model_factory=factory, speech_seconds=63.6, duration_ms=85000,
            )

        self.assertEqual(result.report["asr_passes"][1]["dropped_duplicates"], 2)
        self.assertEqual(result.report["asr_selected_pass"], "C")
        self.assertEqual(
            result,
            [(start_ms, end_ms, text) for start_ms, end_ms, text in rounds[2]],
        )

    def test_all_duplicate_loop_passes_produce_no_selected_transcript(self):
        repeated = [
            [(0, 1000, "echo"), (1000, 2000, "echo")],
            [(0, 1000, "repeat"), (1000, 2000, "repeat"), (2000, 3000, "repeat")],
            [(0, 1000, "again"), (1000, 2000, "again")],
        ]
        calls = []

        def factory():
            rows = repeated[len(calls)]
            return SimpleNamespace(transcribe=lambda audio, **kwargs: (
                calls.append(kwargs) or iter(self._segments(rows)), None
            ))

        with patch.object(sp, "decode_audio_16k", return_value="highpass-only-pcm"):
            result = sp.transcribe(
                "input.mp4", model_factory=factory, speech_seconds=63.6, duration_ms=85000,
            )

        self.assertEqual(result, [])
        self.assertIsNone(result.report["asr_selected_pass"])
        self.assertEqual([row["name"] for row in result.report["asr_passes"]], ["A", "B", "C"])
        self.assertEqual([row["dropped_duplicates"] for row in result.report["asr_passes"]], [1, 2, 1])

    def test_retry_runtime_limit_keeps_pass_a_and_does_not_start_pass_c(self):
        clock = SimpleNamespace(now=0.0)
        def monotonic():
            return clock.now

        calls = []
        def factory():
            index = len(calls)
            def transcribe(audio, **kwargs):
                calls.append(kwargs)
                if index == 0:
                    return iter(self._segments([(0, 1000, "A" * 30)])), None
                def slow_b():
                    clock.now = 6.0
                    yield SimpleNamespace(
                        start=0.0, end=5.0, text="B" * 80,
                        words=[SimpleNamespace(start=0.0, end=5.0, word="B" * 80, probability=0.99)],
                    )
                return slow_b(), None
            return SimpleNamespace(transcribe=transcribe)

        with patch.object(sp.time, "monotonic", side_effect=monotonic),              patch.object(sp, "decode_audio_16k", return_value="highpass-only-pcm") as decode:
            result = sp.transcribe(
                "input.mp4", model_factory=factory, speech_seconds=63.6,
                duration_ms=85000, runtime_limit_seconds=5,
            )

        self.assertEqual(result.report["asr_selected_pass"], "A")
        self.assertEqual([row["name"] for row in result.report["asr_passes"]], ["A", "B"])
        self.assertEqual(len(calls), 2)
        decode.assert_not_called()
        self.assertEqual(result[0][2], "A" * 30)

    def test_medium_runtime_limit_keeps_pass_a_without_starting_small_fallback(self):
        seg = lambda s, e, t: SimpleNamespace(
            start=s, end=e, text=t,
            words=[SimpleNamespace(start=s, end=e, word=t, probability=0.99)],
        )  # noqa: E731
        clock = SimpleNamespace(now=0.0)
        calls = []

        def monotonic():
            return clock.now

        def factory():
            def transcribe(audio, **kwargs):
                calls.append(kwargs)

                def slow_medium():
                    yield seg(0.0, 1.0, "medium first")
                    clock.now = 6.0
                    yield seg(1.0, 2.0, "medium second")

                return slow_medium(), None

            return SimpleNamespace(transcribe=transcribe)

        with patch.object(sp.time, "monotonic", side_effect=monotonic):
            result = sp.transcribe(
                "x.mp4", model_factory=factory, runtime_limit_seconds=5,
            )

        self.assertEqual(result, [(0, 1000, "medium first"), (1000, 2000, "medium second")])
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.report["asr_initial_model"], "medium")
        self.assertEqual(result.report["asr_model"], "medium")
        self.assertIsNone(result.report["asr_fallback_reason"])
        self.assertEqual(result.report["asr_selected_pass"], "A")
        self.assertEqual([row["name"] for row in result.report["asr_passes"]], ["A"])


if __name__ == "__main__":
    unittest.main()
