#!/usr/bin/env python3
"""Offline tests for agent/brain/speech_gate.py (no ffprobe, no model, no network)."""
import importlib.util
import os
import sys
import unittest

_PATH = os.path.join(os.path.dirname(__file__), "..", "agent", "brain", "speech_gate.py")
_SPEC = importlib.util.spec_from_file_location("speech_gate", _PATH)
sg = importlib.util.module_from_spec(_SPEC)
sys.modules["speech_gate"] = sg
_SPEC.loader.exec_module(sg)


def probe(duration=60.0, audio=True):
    streams = [{"codec_type": "video"}]
    if audio:
        streams.append({"codec_type": "audio"})
    return {"streams": streams, "format": {"duration": str(duration)}}


class SpeechGateTest(unittest.TestCase):
    def test_no_audio_track(self):
        r = sg.evaluate(probe(audio=False), [], None, 0.0)
        self.assertEqual((r.decision, r.reason), ("skip", "no_audio_track"))

    def test_media_too_short(self):
        r = sg.evaluate(probe(duration=1.5), [], None, 0.0)
        self.assertEqual(r.reason, "media_too_short")

    def test_music_only_has_no_speech(self):
        r = sg.evaluate(probe(), [], "en", 0.2)
        self.assertEqual(r.reason, "no_speech")

    def test_overlapping_segments_not_double_counted(self):
        segs = [{"start": 0, "end": 10}, {"start": 5, "end": 12}]
        r = sg.evaluate(probe(duration=30), segs, "en", 0.9)
        self.assertEqual(r.speech_seconds, 12.0)
        self.assertEqual(r.decision, "subtitle")

    def test_noisy_segments_ignored(self):
        segs = [{"start": 0, "end": 20, "no_speech_prob": 0.95}]
        r = sg.evaluate(probe(), segs, "en", 0.9)
        self.assertEqual(r.reason, "no_speech")

    def test_low_ratio_and_short_speech(self):
        self.assertEqual(
            sg.evaluate(probe(), [{"start": 0, "end": 2}], "en", 0.9).reason, "speech_too_short")
        self.assertEqual(
            sg.evaluate(probe(duration=120), [{"start": 0, "end": 5}], "en", 0.9).reason,
            "low_speech_ratio")

    def test_language_checks(self):
        segs = [{"start": 0, "end": 20}]
        self.assertEqual(sg.evaluate(probe(), segs, "en", 0.3).reason, "low_language_confidence")
        cfg = sg.GateConfig(allowed_languages=("en",))
        self.assertEqual(sg.evaluate(probe(), segs, "ru", 0.9, cfg).reason, "language_not_allowed")
        self.assertEqual(sg.evaluate(probe(), segs, "en", 0.9, cfg).decision, "subtitle")

    def test_probe_args_and_duration_fallback(self):
        self.assertEqual(sg.probe_args("a.mp4")[0], "ffprobe")
        p = {"streams": [{"codec_type": "audio", "duration": "12.5"}], "format": {}}
        self.assertEqual(sg.media_duration(p), 12.5)


if __name__ == "__main__":
    unittest.main()
