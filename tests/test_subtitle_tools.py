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

    def test_numbered_batch_respects_character_limit_including_ids(self):
        cues = [(i * 1000, i * 1000 + 800, "x" * 48) for i in range(60)]
        batches = st.make_batches(cues, max_chars=3000, max_cues=60)
        self.assertGreaterEqual(len(batches), 1)
        for start, texts in batches:
            self.assertLessEqual(len(st.numbered(texts, start)), 3000)
            self.assertLessEqual(len(texts), 60)

    def test_single_oversized_cue_is_rejected_before_translation(self):
        with self.assertRaisesRegex(ValueError, "subtitle_cue_too_long"):
            st.make_batches([(0, 1000, "x" * 3000)], max_chars=3000)

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
        self.assertIn("Estedad", ass)
        self.assertIn("Dialogue: 0,0:00:01.00,0:00:02.50", ass)
        self.assertIn("\\N", ass)
        style_line = next(line for line in ass.splitlines() if line.startswith("Style: Default,"))
        self.assertEqual(
            style_line,
            "Style: Default,Estedad,60,&H00FFFFFF,&H000000FF,&H66000000,&H80000000,"
            "-1,0,0,0,100,100,0,0,3,6,0,2,40,40,40,1",
        )
        self.assertNotIn("{", ass.split("[Events]")[1].split("Text\n", 1)[1])

    def test_ass_font_size_is_medium_large_with_floor(self):
        cue = [(1000, 2500, "سلام")]
        self.assertIn("Style: Default,Estedad,60,", st.to_ass(cue))
        # A caller that still passes the old small size must not shrink the subtitles.
        self.assertIn("Style: Default,Estedad,60,", st.to_ass(cue, size=44))
        self.assertIn("Style: Default,Estedad,72,", st.to_ass(cue, size=72))

    def test_burn_cmd_defaults_to_plain_ass_filter(self):
        cmd = st.burn_cmd("in.mp4", "subs.ass", "assets/fonts", "out.mp4")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertNotIn("-filter_complex", cmd)
        self.assertEqual(cmd[cmd.index("-vf") + 1], "ass=subs.ass:fontsdir=assets/fonts")
        self.assertEqual(cmd[-3:-1], ["-c:a", "copy"])
        with self.assertRaises(ValueError):
            st.burn_cmd("in.mp4", "a'b.ass", "assets/fonts", "out.mp4")

    def test_burn_cmd_blur_true_builds_graph(self):
        cmd = st.burn_cmd("in.mp4", "subs.ass", "assets/fonts", "out.mp4", blur=True)
        graph = cmd[cmd.index("-filter_complex") + 1]
        self.assertIn("ass=subs.ass:fontsdir=assets/fonts", graph)
        # Opt-in legacy graph: only the bottom strip is blurred.
        self.assertIn("gblur=", graph)
        self.assertIn("drawbox=", graph)
        self.assertIn("overlay=x=0:y=H-h", graph)
        self.assertLess(graph.index("overlay="), graph.index("ass="))
        self.assertIn("[out]", cmd)
        self.assertIn("0:a?", cmd)
        self.assertEqual(cmd[-3:-1], ["-c:a", "copy"])

    def test_burn_cmd_without_blur_keeps_plain_filter(self):
        cmd = st.burn_cmd("in.mp4", "subs.ass", "assets/fonts", "out.mp4", blur=False)
        self.assertIn("ass=subs.ass:fontsdir=assets/fonts", cmd)
        self.assertNotIn("-filter_complex", cmd)


if __name__ == "__main__":
    unittest.main()
