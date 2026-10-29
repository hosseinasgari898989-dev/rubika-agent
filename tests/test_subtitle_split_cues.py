import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "agent" / "brain"))
import subtitle_pipeline as sp  # noqa: E402
import speech_gate as sg  # noqa: E402


class SplitLongCues(unittest.TestCase):
    def test_short_cue_unchanged(self):
        self.assertEqual(sp.split_long_cues([(0, 2000, "short text")]), [(0, 2000, "short text")])

    def test_long_cue_split_in_order_without_gap_or_overlap(self):
        text = " ".join(["word"] * 40)
        out = sp.split_long_cues([(0, 8000, text)])
        self.assertGreaterEqual(len(out), 2)
        self.assertEqual(out[0][0], 0)
        self.assertEqual(out[-1][1], 8000)
        for (s1, e1, _t1), (s2, _e2, _t2) in zip(out, out[1:]):
            self.assertEqual(e1, s2)
        for s, e, t in out:
            self.assertLessEqual(len(t), sp.MAX_CUE_CHARS)
            self.assertGreaterEqual(e - s, sp.MIN_CUE_MS)
        self.assertEqual(" ".join(t for _s, _e, t in out), text)

    def test_too_short_to_split_is_left_alone(self):
        text = " ".join(["word"] * 40)
        self.assertEqual(sp.split_long_cues([(0, 500, text)]), [(0, 500, text)])

    def test_single_long_word_is_left_alone(self):
        word = "x" * 200
        self.assertEqual(sp.split_long_cues([(0, 5000, word)]), [(0, 5000, word)])

    def test_builder_splits_long_translation(self):
        import json
        import tempfile

        long_fa = " ".join(["کلمه"] * 40)

        def translate(prompt, *, max_tokens):
            return "0|" + long_fa

        def run(cmd, **kw):
            if cmd[0] == "ffprobe":
                return SimpleNamespace(returncode=0, stdout=json.dumps({"format": {"duration": 8.0}}), stderr="")
            Path(cmd[-1]).write_bytes(b"x")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "in.mp4"
            video.write_bytes(b"v")
            res = sp.build_subtitled_video(str(video), str(Path(tmp) / "w"), translate,
                                           transcribe_fn=lambda v: [(0, 8000, "hello world")], run=run)
        self.assertGreaterEqual(res["cues"], 2)


class AsrOptions(unittest.TestCase):
    def test_pipeline_passes_sensitive_vad_and_no_context(self):
        seen = []

        def run_asr(*args, **kwargs):
            seen.append(kwargs)
            return [SimpleNamespace(start=0.0, end=1.0, text="hi")], None

        sp.transcribe("x.mp4", model_factory=lambda: SimpleNamespace(transcribe=run_asr))
        self.assertTrue(seen[0]["vad_filter"])
        self.assertFalse(seen[0]["condition_on_previous_text"])
        self.assertEqual(seen[0]["vad_parameters"]["threshold"], 0.35)
        self.assertEqual(seen[0]["vad_parameters"]["speech_pad_ms"], 300)
        self.assertEqual(seen[0]["beam_size"], 5)

    def test_speech_gate_passes_same_options(self):
        seen = []

        class FakeModel:
            def __init__(self, size, **kwargs):
                pass

            def transcribe(self, audio, **kwargs):
                seen.append(kwargs)
                return [], SimpleNamespace(language="en", language_probability=0.9)

        with patch.object(sg, "decode_audio_16k", return_value="pcm"):
            with patch.dict(sys.modules, {"faster_whisper": SimpleNamespace(WhisperModel=FakeModel)}):
                segments, lang, prob = sg.transcribe("x.mp4")
        self.assertEqual((segments, lang, prob), ([], "en", 0.9))
        self.assertTrue(seen[0]["vad_filter"])
        self.assertFalse(seen[0]["condition_on_previous_text"])
        self.assertEqual(seen[0]["vad_parameters"], sg.ASR_VAD_PARAMETERS)
        self.assertEqual(seen[0]["beam_size"], 1)


if __name__ == "__main__":
    unittest.main()
