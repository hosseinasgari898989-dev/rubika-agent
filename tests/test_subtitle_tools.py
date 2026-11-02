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
            "-1,0,0,0,100,100,0,0,1,0,0,2,40,40,40,1",
        )
        self.assertNotIn(r"\ybord", ass)
        self.assertNotIn(r"\p1", ass)
        event_text = "\\n".join(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        self.assertNotIn("{", event_text)

    def test_opaque_box_vertical_padding_changes_symmetrically_only(self):
        ass = st.to_ass([(0, 1800, "نمونه زیرنویس")])
        style_line = next(line for line in ass.splitlines() if line.startswith("Style: Default,"))
        fields = style_line.split(",")
        # BorderStyle 1 removes the oversized ASS opaque box; drawing is added separately.
        self.assertEqual(fields[1], "Estedad")
        self.assertEqual(fields[2], "60")
        self.assertEqual(fields[15], "1")
        self.assertEqual(fields[16], "0")
        self.assertEqual(fields[17], "0")
        self.assertEqual(fields[21], "40")
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        self.assertNotIn(r"\ybord", dialogue)
        self.assertNotIn(r"\p1", dialogue)

    def test_ass_frame_renders_with_libass_when_available(self):
        ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(ffmpeg, "ffmpeg must be installed for real libass tests")
        filters = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout
        self.assertTrue(
            any("ass " in line and "libass" in line.lower() for line in filters.splitlines()),
            "ffmpeg must include the libass ASS filter",
        )

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

        display_lines = body.split(chr(92) + "N")
        self.assertEqual(
            display_lines[:3],
            [
                chr(0x202B) + "سلام!..." + chr(0x202C),
                chr(0x202B) + "متن، English 42؟" + chr(0x202C),
                chr(0x202B) + "عدد ۱۲۳" + chr(0x202C),
            ],
        )
        self.assertEqual(len(display_lines), 3)
        self.assertIn("Default,,0,0,40,,", dialogue)
        self.assertEqual(st.format_srt(cues), original_srt)
        self.assertEqual(st.parse_srt(original_srt), cues)

    def test_latin_only_display_lines_are_not_wrapped(self):
        ass = st.to_ass([(0, 1000, "English 123!")])
        dialogue = next(line for line in ass.splitlines() if line.startswith("Dialogue:"))
        body = dialogue.rsplit(",,", 1)[1].removeprefix("{" + chr(92) + "ybord4}")
        self.assertEqual(body, st.RLM + "English 123!")


    def test_bidi_punctuation_moves_to_left_edge_in_estedad_libass_png(self):
        ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(ffmpeg, "ffmpeg must be installed for real libass tests")
        filters = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout
        self.assertTrue(
            any("ass " in line and "libass" in line.lower() for line in filters.splitlines()),
            "ffmpeg must include the libass ASS filter",
        )

        from PIL import Image

        repo_root = Path(__file__).resolve().parents[1]
        fontsdir = repo_root / "assets" / "fonts"
        self.assertTrue((fontsdir / "Estedad-Regular.ttf").is_file())
        self.assertTrue((fontsdir / "Estedad-Bold.ttf").is_file())

        artifact_dir = os.environ.get("SUBTITLE_BIDI_ARTIFACT_DIR")
        if artifact_dir:
            output_dir = Path(artifact_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            self._render_and_check_bidi_images(ffmpeg, fontsdir, output_dir, Image)
        else:
            with tempfile.TemporaryDirectory() as tmp:
                self._render_and_check_bidi_images(ffmpeg, fontsdir, Path(tmp), Image)

    def _render_and_check_bidi_images(self, ffmpeg, fontsdir, output_dir, Image):
        cue = [(0, 1800, "سلام!")]
        after_ass = st.to_ass(cue)
        after_dialogue = next(line for line in after_ass.splitlines() if line.startswith("Dialogue:"))
        prefix = after_dialogue.rsplit(",,", 1)[0]
        before_dialogue = prefix + ",," + st.RLM + "سلام!"
        before_ass = after_ass.replace(after_dialogue, before_dialogue)

        def render(name, ass_text):
            ass_path = output_dir / (name + ".ass")
            png_path = output_dir / (name + ".png")
            ass_path.write_text(ass_text, encoding="utf-8")
            subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=1:d=2",
                    "-vf", f"ass={ass_path}:fontsdir={fontsdir}",
                    "-frames:v", "1", str(png_path),
                ],
                capture_output=True, text=True, check=True, timeout=30,
            )
            self.assertTrue(png_path.is_file())
            self.assertEqual(png_path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
            return Image.open(png_path).convert("RGB")

        before = render("before", before_ass)
        after = render("after", after_ass)

        def components(image):
            pixels = image.load()
            active = set()
            for y in range(500, 710):
                for x in range(150, 1130):
                    red, green, blue = pixels[x, y]
                    if red > 210 and green > 210 and blue > 210:
                        active.add((x, y))
            found = []
            while active:
                seed = active.pop()
                stack = [seed]
                xs, ys = [seed[0]], [seed[1]]
                while stack:
                    x, y = stack.pop()
                    for ny in range(y - 1, y + 2):
                        for nx in range(x - 1, x + 2):
                            point = (nx, ny)
                            if point in active:
                                active.remove(point)
                                stack.append(point)
                                xs.append(nx)
                                ys.append(ny)
                found.append((min(xs), min(ys), max(xs), max(ys), len(xs)))
            return found

        def exclamation_and_body(image):
            parts = components(image)
            stems = [p for p in parts if p[2] - p[0] <= 18 and p[3] - p[1] >= 14]
            dots = [p for p in parts if p[2] - p[0] <= 13 and p[3] - p[1] <= 13 and p[4] <= 180]
            marker = None
            for stem in stems:
                sx = (stem[0] + stem[2]) / 2
                for dot in dots:
                    dx = (dot[0] + dot[2]) / 2
                    if abs(sx - dx) <= 5 and -1 <= dot[1] - stem[3] <= 18:
                        marker = (min(stem[0], dot[0]), min(stem[1], dot[1]),
                                  max(stem[2], dot[2]), max(stem[3], dot[3]))
                        break
                if marker:
                    break
            self.assertIsNotNone(marker, "could not isolate the Estedad exclamation glyph")
            non_marker = [
                p for p in parts
                if not (p[0] >= marker[0] - 2 and p[2] <= marker[2] + 2
                        and p[1] >= marker[1] - 2 and p[3] <= marker[3] + 2)
            ]
            self.assertTrue(non_marker, "Persian word glyphs were not rendered")
            body_left = min(p[0] for p in non_marker)
            body_right = max(p[2] for p in non_marker)
            return marker[0], marker[2], body_left, body_right

        before_left, before_right, before_body_left, before_body_right = exclamation_and_body(before)
        after_left, after_right, after_body_left, after_body_right = exclamation_and_body(after)
        self.assertGreater(before_left, before_body_right,
                           "legacy leading RLM should leave ! on the right edge")
        self.assertLess(after_right, after_body_left,
                        "RLE/PDF should move ! to the left edge of Persian text")


class SubtitleBoxPaddingTest(unittest.TestCase):
    def test_rendered_estedad_glyph_boxes_have_12px_padding_and_fixed_bottom(self):
        ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(ffmpeg, "ffmpeg must be installed for real libass tests")
        filters = subprocess.run(
            [ffmpeg, "-hide_banner", "-filters"],
            capture_output=True, text=True, check=True, timeout=15,
        ).stdout
        self.assertTrue(
            any("ass " in line and "libass" in line.lower() for line in filters.splitlines()),
            "ffmpeg must include the libass ASS filter",
        )
        from PIL import Image

        root = Path(__file__).resolve().parents[1]
        fontsdir = root / "assets" / "fonts"
        self.assertTrue((fontsdir / "Estedad-Regular.ttf").is_file())
        self.assertTrue((fontsdir / "Estedad-Bold.ttf").is_file())
        cases = [
            ("short", "سلام!"),
            ("long", "این یک جمله فارسی نسبتاً بلند است که باید کادر آن دقیقاً دور حروف قرار بگیرد."),
            ("multiline", "خط اول زیرنویس فارسی است\nخط دوم هم باید داخل همان یک کادر قرار بگیرد."),
        ]

        def pixel_bbox(image, predicate):
            pixels = image.load()
            points = [
                (x, y)
                for y in range(image.height)
                for x in range(image.width)
                if predicate(*pixels[x, y])
            ]
            self.assertTrue(points, "expected subtitle pixels were not detected")
            return (
                min(x for x, _ in points),
                min(y for _, y in points),
                max(x for x, _ in points) + 1,
                max(y for _, y in points) + 1,
            )

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            for name, text in cases:
                with self.subTest(case=name):
                    cues = [(0, 2000, text)]
                    ass_text, measured = st.to_ass_with_glyph_boxes(
                        cues, fontsdir=str(fontsdir), ffmpeg=ffmpeg,
                    )
                    self.assertEqual(len(measured), 1)
                    drawing_lines = [
                        line for line in ass_text.splitlines()
                        if line.startswith("Dialogue: 0,") and r"\p1" in line
                    ]
                    self.assertEqual(
                        len(drawing_lines), 1,
                        "all lines in one cue must share one background drawing",
                    )
                    self.assertIn(r"\an7\pos(0,0)\p1", drawing_lines[0])

                    ass_path = output_dir / f"{name}.ass"
                    png_path = output_dir / f"{name}.png"
                    ass_path.write_text(ass_text, encoding="utf-8")
                    subprocess.run(
                        [
                            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-f", "lavfi", "-i",
                            "color=c=0x808080:s=1280x720:r=1:d=2",
                            "-vf", f"ass={ass_path}:fontsdir={fontsdir}",
                            "-frames:v", "1", str(png_path),
                        ],
                        capture_output=True, text=True, check=True, timeout=30,
                    )
                    image = Image.open(png_path).convert("RGB")
                    glyph = pixel_bbox(
                        image,
                        lambda r, g, b: r > 220 and g > 220 and b > 220,
                    )
                    box = pixel_bbox(
                        image,
                        lambda r, g, b: 5 < r < 105 and 5 < g < 105 and 5 < b < 105,
                    )
                    padding = {
                        "left": glyph[0] - box[0],
                        "top": glyph[1] - box[1],
                        "right": box[2] - glyph[2],
                        "bottom": box[3] - glyph[3],
                    }
                    for side, value in padding.items():
                        self.assertLessEqual(
                            abs(value - 12), 2,
                            f"{name} {side} padding is {value}px, expected 12px",
                        )
                    self.assertEqual(box[3] - 1, 686, "drawing lower edge must stay at row 686")




if __name__ == "__main__":
    unittest.main()
