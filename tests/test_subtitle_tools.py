#!/usr/bin/env python3
"""Offline unit tests for scripts/subtitle_tools.py (no network, no ffmpeg)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
        self.assertIn(r"{\ybord4}", ass)
        self.assertNotIn("{", ass.split("[Events]")[1].split("Text\n", 1)[1].replace(r"{\ybord4}", "", 1))

    def test_opaque_box_vertical_padding_changes_symmetrically_only(self):
        ass = st.to_ass([(0, 1800, "نمونه زیرنویس")])
        style_line = next(line for line in ass.splitlines() if line.startswith("Style: Default,"))
        fields = style_line.split(",")
        # ASS style fields: Fontname, Fontsize, ..., BorderStyle, Outline, Shadow, ..., MarginV.
        self.assertEqual(fields[1], "Estedad")
        self.assertEqual(fields[2], "60")
        self.assertEqual(fields[15], "3")
        self.assertEqual(fields[16], "6")
        self.assertEqual(fields[17], "0")
        self.assertEqual(fields[21], "40")
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        self.assertIn(r"{\ybord4}", dialogue)
        self.assertNotIn(r"{\xbord", dialogue)

    def test_ass_frame_renders_with_libass_when_available(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg is not installed in this CI runner")
        filters = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout
        if not any("ass " in line and "libass" in line.lower() for line in filters.splitlines()):
            self.skipTest("ffmpeg was built without the libass ASS filter")

        repo_root = Path(__file__).resolve().parents[1]
        fontsdir = repo_root / "assets" / "fonts"
        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "sample.ass"
            png_path = Path(tmp) / "sample.png"
            ass_path.write_text(
                st.to_ass([(0, 1800, "نمونه زیرنویس")]),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=white:s=1280x720:r=1:d=2",
                    "-vf", f"ass={ass_path}:fontsdir={fontsdir}",
                    "-frames:v", "1", str(png_path),
                ],
                capture_output=True, text=True, check=True, timeout=30,
            )
            self.assertTrue(png_path.is_file())
            self.assertGreater(png_path.stat().st_size, 100)
            self.assertEqual(png_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

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


class SubtitleBidiTest(unittest.TestCase):
    def test_each_arabic_display_line_is_wrapped_independently_without_changing_srt(self):
        cues = [(1234, 5678, "سلام!..." + chr(10) + "متن، English 42؟" + chr(10) + "عدد ۱۲۳")]
        original_srt = st.format_srt(cues)

        ass = st.to_ass(cues)
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        body = dialogue.rsplit(",,", 1)[1].removeprefix("{" + chr(92) + "ybord4}")

        self.assertEqual(
            body.split(chr(92) + "N"),
            [
                chr(0x202B) + "سلام!..." + chr(0x202C),
                chr(0x202B) + "متن، English 42؟" + chr(0x202C),
                chr(0x202B) + "عدد ۱۲۳" + chr(0x202C),
            ],
        )
        self.assertEqual(st.format_srt(cues), original_srt)
        self.assertEqual(st.parse_srt(original_srt), cues)

    def test_latin_only_display_lines_are_not_wrapped(self):
        ass = st.to_ass([(0, 1000, "English 123!")])
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        body = dialogue.rsplit(",,", 1)[1].removeprefix("{" + chr(92) + "ybord4}")
        self.assertEqual(body, "English 123!")


if __name__ == "__main__":
    unittest.main()
