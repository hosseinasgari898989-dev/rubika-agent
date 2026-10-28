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
        seg = lambda s, e, t: SimpleNamespace(start=s, end=e, text=t)  # noqa: E731
        seen = []
        def run_asr(*args, **kwargs):
            seen.append(kwargs)
            return [seg(0.0, 1.2, " Hi  you "), seg(1.3, 2.0, "  ")], None
        model = SimpleNamespace(transcribe=run_asr)
        result = sp.transcribe("x.mp4", model_factory=lambda: model)
        self.assertEqual(result, [(0, 1200, "Hi you")])
        self.assertEqual(seen[0]["beam_size"], 5)
        self.assertTrue(seen[0]["vad_filter"])
        self.assertEqual(result.report["asr_model"], "medium")
        self.assertIsNone(result.report["asr_fallback_reason"])

    def test_default_model_is_medium_cpu_int8(self):
        seg = lambda s, e, t: SimpleNamespace(start=s, end=e, text=t)  # noqa: E731
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
        self.assertEqual(result.report["asr_model"], "medium")

    def test_medium_runtime_limit_falls_back_to_small_and_reports_reason(self):
        seg = lambda s, e, t: SimpleNamespace(start=s, end=e, text=t)  # noqa: E731
        factories = []

        def factory():
            factories.append(len(factories))
            text = "medium partial" if len(factories) == 1 else "small fallback"
            segment = seg(0.0, 1.0, text)
            return SimpleNamespace(transcribe=lambda *args, **kwargs: (iter([segment]), None))

        result = sp.transcribe("x.mp4", model_factory=factory, runtime_limit_seconds=0)
        self.assertEqual(result, [(0, 1000, "small fallback")])
        self.assertEqual(len(factories), 2)
        self.assertEqual(result.report["asr_initial_model"], "medium")
        self.assertEqual(result.report["asr_model"], "small")
        self.assertEqual(result.report["asr_fallback_reason"], "medium_runtime_exceeded_15m")


if __name__ == "__main__":
    unittest.main()
