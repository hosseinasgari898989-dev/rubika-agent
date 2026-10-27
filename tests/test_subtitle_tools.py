#!/usr/bin/env python3
"""Offline unit tests for scripts/subtitle_tools.py (no network, no ffmpeg)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import subtitle_tools as st  # noqa: E402

SRT = "1\n00:00:01,000 --> 00:00:02,500\nHello world\n\n2\n00:01:02,050 --> 00:01:04,000\nSecond line\nwraps\n"


class SubtitleToolsTest(unittest.TestCase):
    def test_srt_roundtrip(self):
        cues = st.parse_srt(SRT)
        self.assertEqual(cues[0], (1000, 2500, "Hello world"))
        self.assertEqual(cues[1][0], 62050)
        self.assertEqual(st.parse_srt(st.format_srt(cues)), cues)

    def test_batches_and_numbered(self):
        cues = [(i * 1000, i * 1000 + 900, f"line {i}") for i in range(130)]
        batches = st.make_batches(cues, max_chars=100000, max_cues=60)
        self.assertEqual([len(b[1]) for b in batches], [60, 60, 10])
        self.assertEqual(batches[1][0], 60)
        reply = st.numbered(["a", "b"], 5)
        self.assertEqual(st.parse_numbered(reply, 5, 3), ["a", "b", None])

    def test_apply_translation_fallback(self):
        cues = st.parse_srt(SRT)
        out, missing = st.apply_translation(cues, ["سلام", None])
        self.assertEqual(missing, 1)
        self.assertEqual(out[0][2], "سلام")
        self.assertEqual(out[1][2], cues[1][2])
        with self.assertRaises(ValueError):
            st.apply_translation(cues, ["x"])

    def test_ass_output(self):
        ass = st.to_ass([(1000, 2500, "سلام {دنیا}\nخط دوم")])
        self.assertIn("Vazirmatn", ass)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.50", ass)
        self.assertIn("\\N", ass)
        self.assertNotIn("{", ass.split("[Events]")[1].split("Text\n", 1)[1])

    def test_burn_cmd(self):
        cmd = st.burn_cmd("in.mp4", "subs.ass", "assets/fonts", "out.mp4")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("ass=subs.ass:fontsdir=assets/fonts", cmd)
        with self.assertRaises(ValueError):
            st.burn_cmd("in.mp4", "a'b.ass", "assets/fonts", "out.mp4")


if __name__ == "__main__":
    unittest.main()
